"""
Expense Handler for Copa Telmex Telegram Bot.

Manages the conversational flow for expense reporting:
1. Receipt photo → Extract data → Generate CFDI
2. Manual expense → Collect data → Save without CFDI
"""

import base64
import hashlib
import logging
import uuid
from datetime import datetime
from typing import Dict, Any, Optional, List
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .models import (
    Empleado,
    ExpenseReport,
    InvoiceReport,
    RFCConfig,
    Tournament,
    TournamentConceptoMapping,
)
from .services.tournament_phase_service import get_tournament_etapas
from .services.tournament_project_visibility import (
    fetch_active_tournaments_for_telegram_user,
    visibility_validation_error,
)
from .services.expense_service import (
    build_tocino_payload_from_env,
    tocino_payment_fields,
)
from .services.tocino_client import TocinoClient, TocinoAPIError, get_tocino_client

logger = logging.getLogger(__name__)

# Concepto to Sub-Cuenta mapping
CONCEPTO_SUB_CUENTA_MAP = {
    "Transporte": "001",
    "Transporte a Sedes": "001",
    "Transporte a sedes": "001",
    "Hospedaje": "002",
    "Alimentos": "003",
    "Scouting": "004",
    "Supervision": "004",
    "Supervisión": "004",
    "Gastos Varios": "004",
    "Gastos Varios Fase Estatal": "012",
    "Gastos Fase Nacional": "025",
    "Gastos Administrativos": "027",
    "Gastos No Deducibles": "030",
}

# List of conceptos for inline keyboard
CONCEPTOS_LIST = list(CONCEPTO_SUB_CUENTA_MAP.keys())

# Department mapping (full name -> code)
DEPARTAMENTO_MAP = {
    "Mercadotecnia": "M",
    "Operaciones": "O",
    "Finanzas": "F",
    "Gerencia": "G",
}

# List of departments for inline keyboard
DEPARTAMENTOS_LIST = list(DEPARTAMENTO_MAP.keys())

# Payment method options
METODO_PAGO_LIST = [
    "Efectivo",
    "Tarjeta"
]


def _build_inline_keyboard(options: List[str], callback_prefix: str) -> Dict[str, Any]:
    """Build an inline keyboard with rows of two options."""
    keyboard = {"inline_keyboard": []}
    row: List[Dict[str, str]] = []
    for index, option in enumerate(options):
        row.append(
            {
                "text": option,
                "callback_data": f"{callback_prefix}:{option}",
            }
        )
        if len(row) == 2 or index == len(options) - 1:
            keyboard["inline_keyboard"].append(row)
            row = []
    return keyboard


# Helper functions for tournament concepto mappings
async def get_tournament_conceptos(tournament_id: str, session: AsyncSession) -> List[TournamentConceptoMapping]:
    """Get active concepto mappings for a tournament."""
    from uuid import UUID
    try:
        result = await session.execute(
            select(TournamentConceptoMapping)
            .where(
                TournamentConceptoMapping.tournament_id == UUID(tournament_id),
                TournamentConceptoMapping.active == True
            )
            .order_by(TournamentConceptoMapping.display_order, TournamentConceptoMapping.concepto)
        )
        return list(result.scalars().all())
    except Exception as e:
        logger.error(f"Error getting tournament conceptos: {e}", exc_info=True)
        return []


async def get_concepto_mapping(tournament_id: str, concepto: str, session: AsyncSession) -> Optional[TournamentConceptoMapping]:
    """Get a specific concepto mapping for a tournament."""
    from uuid import UUID
    try:
        result = await session.execute(
            select(TournamentConceptoMapping)
            .where(
                TournamentConceptoMapping.tournament_id == UUID(tournament_id),
                TournamentConceptoMapping.concepto == concepto,
                TournamentConceptoMapping.active == True
            )
        )
        return result.scalar_one_or_none()
    except Exception as e:
        logger.error(f"Error getting concepto mapping: {e}", exc_info=True)
        return None


class ExpenseState:
    """Tracks the state of an expense conversation for a user."""

    def __init__(self):
        self.state = "idle"  # idle, waiting_photo, collecting_name, collecting_departamento, collecting_rfc, collecting_project, collecting_fase_torneo, collecting_concepto, collecting_metodo_pago, collecting_ultimos_4_digitos, collecting_amount, collecting_has_iva, collecting_cfdi_use (ticket only), collecting_date
        self.data = {}
        self.file_data = None
        self.file_name = None
        self.expense_type = "manual"  # "manual" or "ticket"
        self.user_info = {}


class ExpenseHandler:
    """Handles expense-related Telegram conversations."""

    def __init__(self, telegram_token: str, anthropic_key: str):
        self.telegram_token = telegram_token
        self.anthropic_key = anthropic_key

        # User state management (chat_id -> ExpenseState)
        self.user_states: Dict[int, ExpenseState] = {}

        logger.info("✅ ExpenseHandler initialized")

    def get_user_state(self, chat_id: int) -> ExpenseState:
        """Get or create user state for a chat."""
        if chat_id not in self.user_states:
            self.user_states[chat_id] = ExpenseState()
        return self.user_states[chat_id]

    def clear_user_state(self, chat_id: int):
        """Clear user state after processing."""
        if chat_id in self.user_states:
            del self.user_states[chat_id]

    async def handle_restart_command(self, chat_id: int, user_id: int) -> Dict[str, Any]:
        """Handle /reiniciar command to clear state and start over."""
        
        # Clear user state if exists
        self.clear_user_state(chat_id)
        
        message = """🔄 **Sesión Reiniciada**

¡Listo! He reiniciado tu sesión.

Tu información anterior ha sido descartada (no se guardó nada en la base de datos).

**¿Qué quieres hacer ahora?**

📸 **Registrar gasto con recibo:**
• Envía una foto del recibo

📝 **Registrar gasto sin recibo:**
• Escribe `/reportar_gasto`

📋 **Ver mis gastos:**
• Escribe `/mis_gastos`

💡 **Ayuda:**
• Escribe `/gastos` para ver todos los comandos disponibles

¡Estoy listo para ayudarte! 🚀"""

        logger.info(f"User {user_id} restarted their expense session")
        
        return {
            "status": "restarted",
            "message": message,
            "parse_mode": "Markdown"
        }

    async def generate_reference_number(self, session: AsyncSession, departamento: str) -> str:
        """Generate reference number based on department, year, and sequence.
        
        Format: D-YY######
        - D: First letter of department (M, O, F)
        - YY: Last two digits of current year
        - ######: 6-digit sequence number (starting from 000001 per department/year)
        
        Examples:
        - O-25000001 (Operations, 2025, first ticket)
        - O-25000002 (Operations, 2025, second ticket)
        - M-25000001 (Marketing, 2025, first ticket)
        """
        from sqlalchemy import func
        
        # Get department code
        dept_code = DEPARTAMENTO_MAP.get(departamento, "U")
        
        # Get current year (last 2 digits)
        current_year = datetime.now().year
        year_suffix = str(current_year)[-2:]
        
        # Find the highest sequence number for this department/year
        # Pattern: D-YY######
        prefix = f"{dept_code}-{year_suffix}"

        # Serialize allocation per prefix to avoid duplicates under concurrent requests.
        # Uses PostgreSQL advisory transaction lock; no-op fallback for other engines.
        try:
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
                {"lock_key": f"expense_ref:{prefix}"},
            )
        except Exception as e:
            logger.warning(
                "Could not acquire advisory lock for expense reference generation; "
                "falling back to best-effort allocation",
                extra={"prefix": prefix, "error": str(e)},
            )
        
        # Query existing reference numbers with this prefix
        result = await session.execute(
            select(func.max(ExpenseReport.numero_referencia))
            .where(ExpenseReport.numero_referencia.like(f"{prefix}%"))
        )
        max_ref = result.scalar_one_or_none()
        
        if max_ref:
            # Extract sequence number from max reference
            # Format: D-YY######
            try:
                sequence_str = max_ref.split('-')[1][2:]  # Get part after YY
                next_sequence = int(sequence_str) + 1
            except (IndexError, ValueError):
                next_sequence = 1
        else:
            next_sequence = 1
        
        # Format sequence as 6-digit number
        sequence_str = f"{next_sequence:06d}"
        
        # Build reference number
        reference_number = f"{prefix}{sequence_str}"
        
        logger.info(f"Generated reference number: {reference_number} for department {departamento}")
        
        return reference_number

    async def handle_expense_command(self, chat_id: int, user_id: int) -> Dict[str, Any]:
        """Handle /gastos command to start expense reporting."""

        message = """💰 **Sistema de Gastos - Copa Telmex**

¡Hola! Puedo ayudarte a registrar y gestionar gastos.

**¿Qué necesitas hacer?**

📸 **Con recibo (foto):**
• Escribe `/reportar_recibo`
• Envía la foto del recibo cuando te lo pida
• Te preguntaré proyecto, concepto y monto
• Generaré el CFDI automáticamente

📝 **Sin recibo (manual):**
• Escribe `/reportar_gasto`
• Te preguntaré los detalles
• Registraré el gasto sin CFDI

**Comandos:**
• `/gastos` - Mostrar esta ayuda
• `/reportar_recibo` - Registrar gasto con recibo
• `/reportar_gasto` - Registrar gasto sin recibo
• `/mis_gastos` - Ver mis gastos registrados
• `/reiniciar` - Reiniciar sesión (descartar información actual)
• `/estadisticas_gastos` - Ver estadísticas

¿Listo para registrar un gasto? 🚀"""

        return {
            "status": "help_sent",
            "message": message,
            "parse_mode": "Markdown"
        }

    async def handle_reportar_recibo_command(
        self,
        chat_id: int,
        user_id: int,
        session: AsyncSession
    ) -> Dict[str, Any]:
        """Handle /reportar_recibo command to start expense reporting with receipt photo."""

        state = self.get_user_state(chat_id)
        state.state = "waiting_photo"
        state.expense_type = "ticket"
        state.data = {}

        message = """📸 **Reportar Gasto Con Recibo**

¡Perfecto! Voy a ayudarte a registrar un gasto con recibo.

**Paso 1: Foto del Recibo**
📸 Por favor, envía una foto clara del recibo.

Después te preguntaré:
• Tu nombre completo
• Departamento
• RFC
• Torneo/proyecto
• Concepto del gasto
• Monto en MXN
• Uso de CFDI

💡 Si cometes un error, escribe `/reiniciar` para empezar de nuevo.

⏳ Esperando tu foto..."""

        return {
            "status": "waiting_photo",
            "message": message,
            "parse_mode": "Markdown"
        }

    async def handle_reportar_gasto_command(
        self,
        chat_id: int,
        user_id: int,
        session: AsyncSession
    ) -> Dict[str, Any]:
        """Handle /reportar_gasto command to start manual expense reporting."""

        state = self.get_user_state(chat_id)
        state.state = "collecting_name"
        state.expense_type = "manual"
        state.data = {}

        message = """📝 **Reportar Gasto Sin Recibo**

Voy a ayudarte a registrar un gasto que no tiene recibo.

**Información que necesito:**
1. Tu nombre completo
2. Departamento
3. RFC
4. Torneo/proyecto
5. Concepto del gasto
6. Monto en MXN
7. Fecha del gasto

**Pregunta 1:**
¿Cuál es tu nombre completo?

💡 Si cometes un error, escribe `/reiniciar` para empezar de nuevo."""

        return {
            "status": "manual_expense_started",
            "message": message,
            "parse_mode": "Markdown"
        }

    async def handle_photo(
        self,
        chat_id: int,
        user_id: int,
        photo_data: bytes,
        file_id: str,
        session: AsyncSession
    ) -> Dict[str, Any]:
        """Handle photo messages for expense receipts."""

        try:
            logger.info(f"Processing expense photo for user {user_id}")

            # Convert to base64
            photo_base64 = base64.b64encode(photo_data).decode('utf-8')

            # Store in user state
            state = self.get_user_state(chat_id)
            state.state = "collecting_name"
            state.expense_type = "ticket"
            state.file_data = photo_base64
            state.file_name = f"receipt_{file_id}.jpg"

            message = """📸 **¡Recibo recibido!**

Ahora necesito información adicional para procesar tu gasto:

**Pregunta 1:**
¿Cuál es tu nombre completo?

💡 Si cometes un error, escribe `/reiniciar` para empezar de nuevo."""

            return {
                "status": "collecting_name",
                "message": message,
                "parse_mode": "Markdown"
            }

        except Exception as e:
            logger.error(f"Error processing expense photo: {e}", exc_info=True)
            return {
                "status": "error",
                "message": "❌ **Error al procesar la foto**\n\nPor favor intenta de nuevo.",
                "parse_mode": "Markdown"
            }

    async def handle_text_message(
        self,
        chat_id: int,
        user_id: int,
        text: str,
        session: AsyncSession
    ) -> Dict[str, Any]:
        """Handle text messages for conversational expense data collection."""

        state = self.get_user_state(chat_id)
        current_state = state.state

        # Name collection
        if current_state == "collecting_name":
            state.data["nombre_enviador"] = text.strip()
            state.state = "collecting_departamento"
            
            # Build inline keyboard with department options
            keyboard = {"inline_keyboard": []}
            # Add departments in a single column
            for departamento in DEPARTAMENTOS_LIST:
                keyboard["inline_keyboard"].append([{
                    "text": departamento,
                    "callback_data": f"departamento:{departamento}"
                }])
            
            message = f"""✅ **Nombre:** {text.strip()}

**Pregunta 2:**
Selecciona el departamento:

💡 Si cometes un error, escribe `/reiniciar` para empezar de nuevo."""

            return {
                "status": "collecting_departamento",
                "message": message,
                "parse_mode": "Markdown",
                "reply_markup": keyboard
            }

        # Department collection (fallback for text input)
        elif current_state == "collecting_departamento":
            departamento = text.strip()
            if departamento in DEPARTAMENTO_MAP:
                state.data["departamento"] = departamento
                state.state = "collecting_rfc"
                
                # Get active RFC configurations
                result = await session.execute(
                    select(RFCConfig).where(RFCConfig.active == True)
                    .order_by(RFCConfig.display_order, RFCConfig.name)
                )
                rfc_configs = result.scalars().all()
                
                if rfc_configs:
                    # Build inline keyboard with RFC options
                    keyboard = {"inline_keyboard": []}
                    # Add RFCs in rows of 2
                    row = []
                    for i, rfc in enumerate(rfc_configs):
                        row.append({
                            "text": rfc.name,
                            "callback_data": f"rfc:{rfc.id}"
                        })
                        if len(row) == 2 or i == len(rfc_configs) - 1:
                            keyboard["inline_keyboard"].append(row)
                            row = []
                    
                    # Add "No Aplica" option only for manual expenses (reportar_gasto)
                    if state.expense_type == "manual":
                        keyboard["inline_keyboard"].append([{
                            "text": "No Aplica",
                            "callback_data": "rfc:no_aplica"
                        }])
                    
                    message = f"""✅ **Departamento:** {departamento}

**Pregunta 3:**
Selecciona el RFC a utilizar:

💡 Si cometes un error, escribe `/reiniciar` para empezar de nuevo."""

                    return {
                        "status": "collecting_rfc",
                        "message": message,
                        "parse_mode": "Markdown",
                        "reply_markup": keyboard
                    }
                else:
                    # Fallback: If no RFCs configured, skip to project (use env vars for Tocino)
                    state.state = "collecting_project"
                    message = f"""✅ **Departamento:** {departamento}

⚠️ **Nota:** No hay RFC configurados. Se usará la configuración por defecto.

**Pregunta 3:**
¿Cuál es el nombre del proyecto o torneo para este gasto?

*(Por ejemplo: Copa Telmex 2025, Torneo Regional, etc.)*

💡 Si cometes un error, escribe `/reiniciar` para empezar de nuevo."""

                    tournaments = await fetch_active_tournaments_for_telegram_user(
                        session, user_id
                    )
                    
                    if tournaments:
                        # Build inline keyboard with tournament options
                        keyboard = {"inline_keyboard": []}
                        # Add tournaments in rows of 2
                        row = []
                        for i, tournament in enumerate(tournaments):
                            row.append({
                                "text": tournament.name,
                                "callback_data": f"tournament:{tournament.id}"
                            })
                            if len(row) == 2 or i == len(tournaments) - 1:
                                keyboard["inline_keyboard"].append(row)
                                row = []
                        
                        return {
                            "status": "collecting_project",
                            "message": message,
                            "parse_mode": "Markdown",
                            "reply_markup": keyboard
                        }
                    else:
                        return {
                            "status": "collecting_project",
                            "message": message,
                            "parse_mode": "Markdown"
                        }
            else:
                return {
                    "status": "invalid_departamento",
                    "message": "❌ Departamento no válido. Por favor selecciona una opción de la lista.",
                    "parse_mode": "Markdown"
                }

        # RFC collection (fallback for text input when no RFCs configured)
        elif current_state == "collecting_rfc":
            # RFC should be selected via callback, but handle text input as fallback
            return {
                "status": "invalid_rfc",
                "message": "❌ Por favor selecciona un RFC de las opciones disponibles.",
                "parse_mode": "Markdown"
            }

        # Project name collection (fallback for text input when no tournaments)
        elif current_state == "collecting_project":
            state.data["project"] = text.strip()
            state.state = "collecting_fase_torneo"
            fase_options = get_tournament_etapas(None)
            state.data["fase_options"] = fase_options
            keyboard = _build_inline_keyboard(fase_options, "fase_torneo")

            message = f"""✅ **Proyecto:** {text.strip()}

**Pregunta 5:**
Selecciona la fase del torneo:

💡 Si cometes un error, escribe `/reiniciar` para empezar de nuevo."""

            return {
                "status": "collecting_fase_torneo",
                "message": message,
                "parse_mode": "Markdown",
                "reply_markup": keyboard
            }

        # Fase torneo collection - fallback for text input
        elif current_state == "collecting_fase_torneo":
            fase_torneo = text.strip()
            fase_options = state.data.get("fase_options") or get_tournament_etapas(None)
            if fase_torneo in fase_options:
                state.data["fase_torneo"] = fase_torneo
                # Determine next state based on expense type
                if state.expense_type == "ticket":
                    state.state = "collecting_concepto"
                else:
                    state.state = "manual_collecting_concepto"
                
                # Check if tournament_id exists in state
                tournament_id = state.data.get("tournament_id")
                conceptos_to_show = []
                
                if tournament_id:
                    # Get tournament-specific conceptos
                    tournament_conceptos = await get_tournament_conceptos(tournament_id, session)
                    if tournament_conceptos:
                        conceptos_to_show = tournament_conceptos
                    else:
                        # Fallback to global list if no tournament mappings
                        conceptos_to_show = None
                else:
                    # No tournament selected, use global list
                    conceptos_to_show = None
                
                # Build inline keyboard with concepto options
                keyboard = {"inline_keyboard": []}
                row = []
                
                if conceptos_to_show:
                    # Use tournament-specific conceptos
                    for i, mapping in enumerate(conceptos_to_show):
                        display_text = mapping.telegram_display_text or mapping.concepto
                        row.append({
                            "text": display_text,
                            "callback_data": f"concepto:{mapping.concepto}"
                        })
                        if len(row) == 2 or i == len(conceptos_to_show) - 1:
                            keyboard["inline_keyboard"].append(row)
                            row = []
                else:
                    # Fallback to global CONCEPTOS_LIST
                    for i, concepto in enumerate(CONCEPTOS_LIST):
                        row.append({
                            "text": concepto,
                            "callback_data": f"concepto:{concepto}"
                        })
                        if len(row) == 2 or i == len(CONCEPTOS_LIST) - 1:
                            keyboard["inline_keyboard"].append(row)
                            row = []
                
                message = f"""✅ **Fase del Torneo:** {fase_torneo}

**Pregunta 6:**
Selecciona el concepto del gasto:

💡 Si cometes un error, escribe `/reiniciar` para empezar de nuevo."""

                return {
                    "status": "collecting_concepto",
                    "message": message,
                    "parse_mode": "Markdown",
                    "reply_markup": keyboard
                }
            else:
                return {
                    "status": "invalid_fase_torneo",
                    "message": "❌ Fase del torneo no válida. Por favor selecciona una opción de la lista.",
                "parse_mode": "Markdown"
            }

        # Concepto collection (for ticket expenses) - fallback for text input
        elif current_state == "collecting_concepto":
            # This should not happen if using inline keyboard, but handle text fallback
            concepto = text.strip()
            tournament_id = state.data.get("tournament_id")
            
            # Try to get tournament-specific mapping first
            mapping = None
            if tournament_id:
                mapping = await get_concepto_mapping(tournament_id, concepto, session)
            
            # Validate concepto - check tournament mapping or global map
            if mapping:
                # Use tournament mapping
                sub_cuenta = mapping.sub_cuenta
                concepto_display = mapping.telegram_display_text or mapping.concepto
            elif concepto in CONCEPTO_SUB_CUENTA_MAP:
                # Fallback to global map
                sub_cuenta = CONCEPTO_SUB_CUENTA_MAP[concepto]
                concepto_display = concepto
            else:
                return {
                    "status": "invalid_concepto",
                    "message": "❌ Concepto no válido. Por favor selecciona una opción de la lista.",
                    "parse_mode": "Markdown"
                }
            
            state.data["concepto"] = concepto
            state.data["sub_cuenta"] = sub_cuenta
            state.state = "collecting_metodo_pago"
            
            # Build inline keyboard with payment method options
            keyboard = {"inline_keyboard": []}
            row = []
            for i, metodo in enumerate(METODO_PAGO_LIST):
                row.append({
                    "text": metodo,
                    "callback_data": f"metodo_pago:{metodo}"
                })
                if len(row) == 2 or i == len(METODO_PAGO_LIST) - 1:
                    keyboard["inline_keyboard"].append(row)
                    row = []
            
            message = f"""✅ **Concepto:** {concepto_display}

**Pregunta 7:**
¿Cuál fue el método de pago utilizado?

💡 Si cometes un error, escribe `/reiniciar` para empezar de nuevo."""

            return {
                "status": "collecting_metodo_pago",
                "message": message,
                "parse_mode": "Markdown",
                "reply_markup": keyboard
            }
        
        # Concepto collection (for manual expenses) - fallback for text input
        elif current_state == "manual_collecting_concepto":
            # This should not happen if using inline keyboard, but handle text fallback
            concepto = text.strip()
            tournament_id = state.data.get("tournament_id")
            
            # Try to get tournament-specific mapping first
            mapping = None
            if tournament_id:
                mapping = await get_concepto_mapping(tournament_id, concepto, session)
            
            # Validate concepto - check tournament mapping or global map
            if mapping:
                # Use tournament mapping
                sub_cuenta = mapping.sub_cuenta
                concepto_display = mapping.telegram_display_text or mapping.concepto
            elif concepto in CONCEPTO_SUB_CUENTA_MAP:
                # Fallback to global map
                sub_cuenta = CONCEPTO_SUB_CUENTA_MAP[concepto]
                concepto_display = concepto
            else:
                return {
                    "status": "invalid_concepto",
                    "message": "❌ Concepto no válido. Por favor selecciona una opción de la lista.",
                    "parse_mode": "Markdown"
                }
            
            state.data["concepto"] = concepto
            state.data["sub_cuenta"] = sub_cuenta
            state.state = "collecting_metodo_pago"
            
            # Build inline keyboard with payment method options
            keyboard = {"inline_keyboard": []}
            row = []
            for i, metodo in enumerate(METODO_PAGO_LIST):
                row.append({
                    "text": metodo,
                    "callback_data": f"metodo_pago:{metodo}"
                })
                if len(row) == 2 or i == len(METODO_PAGO_LIST) - 1:
                    keyboard["inline_keyboard"].append(row)
                    row = []
            
            message = f"""✅ **Concepto:** {concepto_display}

**Pregunta 7:**
¿Cuál fue el método de pago utilizado?

💡 Si cometes un error, escribe `/reiniciar` para empezar de nuevo."""

            return {
                "status": "collecting_metodo_pago",
                "message": message,
                "parse_mode": "Markdown",
                "reply_markup": keyboard
            }
        
        # Payment method collection - fallback for text input
        elif current_state == "collecting_metodo_pago":
            metodo_pago = text.strip()
            if metodo_pago in METODO_PAGO_LIST:
                state.data["metodo_pago"] = metodo_pago
                
                # If it's a card payment, ask for last 4 digits
                if metodo_pago == "Tarjeta":
                    state.state = "collecting_ultimos_4_digitos"
                    
                    message = f"""✅ **Método de Pago:** {metodo_pago}

**Pregunta 7.1:**
Ingresa los últimos 4 dígitos de la tarjeta utilizada:

*Por favor ingresa solo los 4 dígitos (ejemplo: 1234)*

💡 Si cometes un error, escribe `/reiniciar` para empezar de nuevo."""
                    
                    return {
                        "status": "collecting_ultimos_4_digitos",
                        "message": message,
                        "parse_mode": "Markdown"
                    }
                else:
                    # Efectivo - no need for card digits, go to amount
                    if state.expense_type == "ticket":
                        state.state = "collecting_amount"
                        message = f"""✅ **Método de Pago:** {metodo_pago}

**Pregunta 8:**
¿Cuál es el monto del gasto en MXN?

*Por favor ingresa solo el número (ejemplo: 150.50)*"""
                    else:
                        state.state = "manual_collecting_amount"
                        message = f"""✅ **Método de Pago:** {metodo_pago}

**Pregunta 8:**
¿Cuál es el monto del gasto en MXN?

*Por favor ingresa solo el número (ejemplo: 150.50)*"""
                    
                    return {
                        "status": "collecting_amount",
                        "message": message,
                        "parse_mode": "Markdown"
                    }
            else:
                return {
                    "status": "invalid_metodo_pago",
                    "message": "❌ Método de pago no válido. Por favor selecciona una opción de la lista.",
                    "parse_mode": "Markdown"
                }

        # Last 4 digits collection
        elif current_state == "collecting_ultimos_4_digitos":
            # Validate that it's exactly 4 digits
            digits = text.strip()
            if digits.isdigit() and len(digits) == 4:
                state.data["ultimos_4_digitos"] = digits
                
                # Move to amount collection
                if state.expense_type == "ticket":
                    state.state = "collecting_amount"
                    
                    message = f"""✅ **Últimos 4 dígitos:** {digits}

**Pregunta 8:**
¿Cuál es el monto del gasto en MXN?

*Por favor ingresa solo el número (ejemplo: 150.50)*"""
                else:
                    state.state = "manual_collecting_amount"
                    
                    message = f"""✅ **Últimos 4 dígitos:** {digits}

**Pregunta 8:**
¿Cuál es el monto del gasto en MXN?

*Por favor ingresa solo el número (ejemplo: 150.50)*"""
                
                return {
                    "status": "collecting_amount",
                    "message": message,
                    "parse_mode": "Markdown"
                }
            else:
                return {
                    "status": "invalid_digits",
                    "message": "❌ Por favor ingresa exactamente 4 dígitos numéricos (ejemplo: 1234).",
                    "parse_mode": "Markdown"
                }

        # Amount collection (for ticket expenses)
        elif current_state == "collecting_amount":
            try:
                amount = float(text.strip())
                state.data["amount"] = amount
                state.data["date"] = datetime.utcnow()
                state.state = "collecting_has_iva"
                
                # Build inline keyboard with Yes/No options
                keyboard = {"inline_keyboard": [[
                    {"text": "Sí", "callback_data": "has_iva:Sí"},
                    {"text": "No", "callback_data": "has_iva:No"}
                ]]}
                
                message = f"""✅ **Monto Total:** ${amount:.2f} MXN

**Pregunta 8.1:**
¿Este gasto tiene IVA?

💡 Si cometes un error, escribe `/reiniciar` para empezar de nuevo."""

                return {
                    "status": "collecting_has_iva",
                    "message": message,
                    "parse_mode": "Markdown",
                    "reply_markup": keyboard
                }

            except ValueError:
                message = "❌ **Formato inválido**\n\nPor favor ingresa un número válido (ejemplo: 150.50)"
                return {
                    "status": "invalid_amount",
                    "message": message,
                    "parse_mode": "Markdown"
                }
        
        # Amount collection (for manual expenses)
        elif current_state == "manual_collecting_amount":
            try:
                amount = float(text.strip())
                state.data["amount"] = amount
                state.state = "collecting_has_iva"
                
                # Build inline keyboard with Yes/No options
                keyboard = {"inline_keyboard": [[
                    {"text": "Sí", "callback_data": "has_iva:Sí"},
                    {"text": "No", "callback_data": "has_iva:No"}
                ]]}
                
                message = f"""✅ **Monto Total:** ${amount:.2f} MXN

**Pregunta 8.1:**
¿Este gasto tiene IVA?

💡 Si cometes un error, escribe `/reiniciar` para empezar de nuevo."""

                return {
                    "status": "collecting_has_iva",
                    "message": message,
                    "parse_mode": "Markdown",
                    "reply_markup": keyboard
                }

            except ValueError:
                message = "❌ **Formato inválido**\n\nPor favor ingresa un número válido (ejemplo: 150.50)"
                return {
                    "status": "invalid_amount",
                    "message": message,
                    "parse_mode": "Markdown"
                }

        # CFDI use collection (for ticket expenses)
        elif current_state == "collecting_cfdi_use":
            cfdi_use = text.strip().upper()
            # Validate CFDI use code format (should be like G01, G02, G03, etc.)
            if len(cfdi_use) >= 2 and cfdi_use[0] in ['G', 'P', 'D', 'I', 'S'] and cfdi_use[1:].isdigit():
                state.data["cfdi_use"] = cfdi_use
                return await self._process_expense_with_receipt(chat_id, user_id, session)
            else:
                return {
                    "status": "invalid_cfdi_use",
                    "message": "❌ **Código de Uso de CFDI inválido**\n\nPor favor ingresa un código válido (ejemplo: G03, G01, G02).\n\nEl código debe comenzar con una letra (G, P, D, I, S) seguida de números.",
                    "parse_mode": "Markdown"
                }

        # IVA collection (fallback for text input)
        elif current_state == "collecting_has_iva":
            # This should normally be handled via callback, but add fallback
            text_lower = text.strip().lower()
            if text_lower in ["sí", "si", "s", "yes", "y"]:
                has_iva = "Sí"
            elif text_lower in ["no", "n"]:
                has_iva = "No"
            else:
                return {
                    "status": "invalid_iva_response",
                    "message": "❌ **Respuesta inválida**\n\nPor favor selecciona 'Sí' o 'No' usando los botones, o escribe 'Sí' o 'No'.",
                    "parse_mode": "Markdown"
                }
            
            # Use the callback handler logic
            amount = state.data.get("amount", 0.0)
            
            if has_iva == "Sí":
                # Calculate IVA as 16% of total (extract IVA from total if it includes it)
                iva_amount = amount * (0.16 / 1.16)
                state.data["iva"] = round(iva_amount, 2)
            else:
                state.data["iva"] = None
            
            # Move to next step based on expense type
            if state.expense_type == "ticket":
                state.state = "collecting_cfdi_use"
                
                message = f"""✅ **IVA:** {"Sí" if has_iva == "Sí" else "No"}
{f"*Monto IVA calculado: ${state.data['iva']:.2f} MXN*" if has_iva == "Sí" else ""}

**Pregunta 9:**
¿Cuál es el Uso de CFDI que deseas aplicar?

*Por favor ingresa solo el código (ejemplos: G03, G01, G02)*

*Códigos comunes:*
• G01 - Adquisición de mercancías
• G02 - Devoluciones, descuentos o bonificaciones
• G03 - Gastos en general"""
                
                return {
                    "status": "collecting_cfdi_use",
                    "message": message,
                    "parse_mode": "Markdown"
                }
            else:
                state.state = "collecting_date"
                
                message = f"""✅ **IVA:** {"Sí" if has_iva == "Sí" else "No"}
{f"*Monto IVA calculado: ${state.data['iva']:.2f} MXN*" if has_iva == "Sí" else ""}

**Pregunta 9:**
¿En qué fecha fue el gasto?

*Formato: AAAA-MM-DD (ejemplo: 2025-11-10)*"""
                
                return {
                    "status": "collecting_date",
                    "message": message,
                    "parse_mode": "Markdown"
                }

        # Date collection (for manual expenses)
        elif current_state == "collecting_date":
            try:
                expense_date = datetime.strptime(text.strip(), "%Y-%m-%d")
                state.data["date"] = expense_date
                return await self._process_manual_expense(chat_id, user_id, session)

            except ValueError:
                message = "❌ **Fecha inválida**\n\nPor favor usa el formato AAAA-MM-DD (ejemplo: 2025-11-10)"
                return {
                    "status": "invalid_date",
                    "message": message,
                    "parse_mode": "Markdown"
                }

        # Unknown state
        else:
            return {
                "status": "unknown_state",
                "message": "🤔 No estoy seguro de qué hacer ahora. Usa /gastos para empezar de nuevo.",
                "parse_mode": "Markdown"
            }

    async def _process_expense_with_receipt(
        self,
        chat_id: int,
        user_id: int,
        session: AsyncSession
    ) -> Dict[str, Any]:
        """Process expense with receipt (will generate CFDI via Tocino AI)."""

        state = self.get_user_state(chat_id)
        data = state.data

        try:
            # Generate reference number based on department
            reference_number = await self.generate_reference_number(session, data['departamento'])

            # Save to database first
            expense = ExpenseReport(
                telegram_user_id=user_id,
                telegram_chat_id=chat_id,
                proyecto=data['project'],
                concepto=data['concepto'],
                sub_cuenta=data.get('sub_cuenta'),
                gasto_cantidad=data['amount'],
                fecha=data.get('date', datetime.utcnow()),
                tipo_gasto="ticket",
                numero_referencia=reference_number,
                archivo_nombre=state.file_name,
                archivo_data=state.file_data,  # Store base64 data
                estado_factura="pendiente",
                estado_reembolso="pendiente",
                cfdi_use=data.get('cfdi_use', 'G03'),  # Use collected value or default to G03
                cuenta_contable_base=data.get('cuenta_contable_base'),
                nombre_enviador=data.get('nombre_enviador'),
                departamento=data.get('departamento'),
                fase_torneo=data.get('fase_torneo'),
                metodo_pago=data.get('metodo_pago'),
                ultimos_4_digitos=data.get('ultimos_4_digitos'),
                iva=data.get('iva')
            )

            session.add(expense)
            await session.commit()
            await session.refresh(expense)

            logger.info(f"✅ Expense with receipt saved: {reference_number}")

            # Call Tocino API to generate CFDI
            nova_request_id = None
            tocino_error = None
            
            try:
                # Get Tocino client
                tocino_client = get_tocino_client()
                
                # Prepare Tocino payload
                # Use selected RFC configuration or fallback to environment variables
                rfc_id = data.get('rfc_id')
                if rfc_id:
                    # Fetch RFC configuration from database
                    from uuid import UUID
                    rfc_result = await session.execute(
                        select(RFCConfig).where(RFCConfig.id == UUID(rfc_id))
                    )
                    rfc = rfc_result.scalar_one_or_none()
                    
                    if rfc:
                        # Use RFC configuration data
                        tocino_payload = {
                            "tax_id": rfc.tax_id,
                            "taxpayer": rfc.taxpayer,
                            "taxpayer_name": rfc.taxpayer_name or "",
                            "taxpayer_last_name": rfc.taxpayer_last_name or "",
                            "taxpayer_second_last_name": rfc.taxpayer_second_last_name or "",
                            "street_address_1": rfc.street_address_1 or "",
                            "ext_num": rfc.ext_num or "",
                            "int_num": rfc.int_num or "",
                            "street_address_2": rfc.street_address_2 or "",
                            "city": rfc.city or "",
                            "state": rfc.state or "",
                            "country": rfc.country or "México",
                            "postal_code": rfc.postal_code or "",
                            "fiscal_regimen_code": rfc.invoice_fiscal_regimen or "",
                            "cfdi_use_code": data.get('cfdi_use', 'G03'),
                            "csf_pdf": "",
                            "filename": state.file_name or "receipt.jpg",
                            "file": state.file_data,  # Base64 encoded file
                        }
                        tocino_payload.update(
                            tocino_payment_fields(
                                metodo_pago=data.get("metodo_pago"),
                                ultimos_4_digitos=data.get("ultimos_4_digitos"),
                            )
                        )
                    else:
                        # RFC ID provided but not found, fallback to env vars
                        logger.warning(f"RFC {rfc_id} not found, using environment variables")
                        tocino_payload = build_tocino_payload_from_env(
                            expense,
                            data.get('cfdi_use', 'G03'),
                        )
                else:
                    # No RFC ID provided, fallback to environment variables
                    logger.warning("No RFC ID in state data, using environment variables")
                    tocino_payload = build_tocino_payload_from_env(
                        expense,
                        data.get('cfdi_use', 'G03'),
                    )
                
                # Submit to Tocino
                tocino_result = tocino_client.submit_ticket(tocino_payload)
                
                # Extract ticket ID (Tocino returns ticket_id and internal_id; we store as nova_request_id)
                if isinstance(tocino_result, dict):
                    nova_request_id = tocino_result.get("ticket_id") or tocino_result.get("internal_id") or tocino_result.get("nova_request_id")
                
                if nova_request_id:
                    # Update expense with nova_request_id
                    expense.nova_request_id = nova_request_id
                    expense.estado_factura = "en_proceso"
                    session.add(expense)
                    await session.commit()
                    
                    logger.info(f"✅ Tocino API call successful: {nova_request_id}")
                else:
                    logger.warning("Tocino API response missing nova_request_id")
                    tocino_error = "No se recibió ID de solicitud de Tocino"
                    
            except TocinoAPIError as e:
                logger.error(f"Tocino API error: {e}", exc_info=True)
                tocino_error = f"Error de Tocino API: {str(e)}"
                # Continue - expense is saved, just CFDI generation failed
            except Exception as e:
                logger.error(f"Unexpected error calling Tocino API: {e}", exc_info=True)
                tocino_error = f"Error inesperado: {str(e)}"
                # Continue - expense is saved, just CFDI generation failed

            # Clear state
            self.clear_user_state(chat_id)

            # Build response message
            if nova_request_id:
                message = f"""✅ **¡Gasto registrado exitosamente!**

**Detalles:**
• Proyecto: {data['project']}
• Concepto: {data['concepto']}
• Monto: ${data['amount']:.2f} MXN
• Tipo: Con recibo
• Referencia: `{reference_number}`

📄 Tu recibo ha sido enviado a Tocino AI para generar el CFDI automáticamente.
ID de Solicitud: `{nova_request_id}`

Te notificaremos cuando el CFDI esté listo."""
            else:
                message = f"""✅ **¡Gasto registrado exitosamente!**

**Detalles:**
• Proyecto: {data['project']}
• Concepto: {data['concepto']}
• Monto: ${data['amount']:.2f} MXN
• Tipo: Con recibo
• Referencia: `{reference_number}`

⚠️ **Nota:** El gasto fue registrado, pero hubo un problema al enviarlo a Tocino AI para generar el CFDI.
{tocino_error if tocino_error else 'Por favor contacta al soporte.'}"""

            return {
                "status": "expense_saved",
                "message": message,
                "parse_mode": "Markdown",
                "reference_number": reference_number,
                "nova_request_id": nova_request_id
            }

        except Exception as e:
            logger.error(f"Error processing expense with receipt: {e}", exc_info=True)
            await session.rollback()
            self.clear_user_state(chat_id)

            return {
                "status": "error",
                "message": f"❌ **Error al procesar el gasto**\n\n{str(e)}\n\nPor favor intenta de nuevo.",
                "parse_mode": "Markdown"
            }

    async def _process_manual_expense(
        self,
        chat_id: int,
        user_id: int,
        session: AsyncSession
    ) -> Dict[str, Any]:
        """Process manual expense (without receipt, no CFDI)."""

        state = self.get_user_state(chat_id)
        data = state.data

        try:
            # Generate reference number based on department
            reference_number = await self.generate_reference_number(session, data['departamento'])

            # Save to database
            # Note: CFDI use not collected for manual expenses (no CFDI generated)
            expense = ExpenseReport(
                telegram_user_id=user_id,
                telegram_chat_id=chat_id,
                proyecto=data['project'],
                concepto=data['concepto'],
                sub_cuenta=data.get('sub_cuenta'),
                gasto_cantidad=data['amount'],
                fecha=data.get('date', datetime.utcnow()),
                tipo_gasto="manual",
                numero_referencia=reference_number,
                estado_reembolso="pendiente",
                cuenta_contable_base=data.get('cuenta_contable_base'),
                nombre_enviador=data.get('nombre_enviador'),
                departamento=data.get('departamento'),
                fase_torneo=data.get('fase_torneo'),
                metodo_pago=data.get('metodo_pago'),
                ultimos_4_digitos=data.get('ultimos_4_digitos'),
                iva=data.get('iva')
            )

            session.add(expense)
            await session.commit()
            await session.refresh(expense)

            logger.info(f"✅ Manual expense saved: {reference_number}")

            # Clear state
            self.clear_user_state(chat_id)

            message = f"""✅ **Gasto Manual Registrado**

**Detalles:**
• Proyecto: {data['project']}
• Concepto: {data['concepto']}
• Monto: ${data['amount']:.2f} MXN
• Fecha: {data['date'].strftime('%d/%m/%Y')}
• Tipo: Registro manual (sin CFDI)
• Referencia: `{reference_number}`

Tu gasto ha sido registrado en el sistema sin generar CFDI."""

            return {
                "status": "manual_expense_saved",
                "message": message,
                "parse_mode": "Markdown",
                "reference_number": reference_number
            }

        except Exception as e:
            logger.error(f"Error processing manual expense: {e}", exc_info=True)
            await session.rollback()
            self.clear_user_state(chat_id)

            return {
                "status": "error",
                "message": f"❌ **Error al registrar el gasto**\n\n{str(e)}\n\nPor favor intenta de nuevo.",
                "parse_mode": "Markdown"
            }

    async def get_user_expenses(
        self,
        chat_id: int,
        user_id: int,
        session: AsyncSession
    ) -> Dict[str, Any]:
        """Get all expenses for a user."""

        try:
            result = await session.execute(
                select(ExpenseReport)
                .where(ExpenseReport.telegram_user_id == user_id)
                .order_by(ExpenseReport.created_at.desc())
                .limit(10)
            )
            expenses = result.scalars().all()

            if not expenses:
                return {
                    "status": "no_expenses",
                    "message": "📭 *No tienes gastos registrados*\n\nUsa /gastos para registrar tu primer gasto.",
                    "parse_mode": "Markdown"
                }

            message = f"💰 **Mis Gastos** (Últimos 10)\n\n"

            for exp in expenses:
                tipo_icon = "📸" if exp.tipo_gasto == "ticket" else "📝"
                estado_icon = "✅" if exp.estado_reembolso == "pagado" else "⏳" if exp.estado_reembolso == "aprobado" else "📋"

                message += f"{tipo_icon} *{exp.proyecto}*\n"
                message += f"   ${exp.gasto_cantidad:.2f} - {exp.concepto}\n"
                message += f"   {estado_icon} {exp.estado_reembolso.capitalize()}\n"
                message += f"   📅 {exp.fecha.strftime('%d/%m/%Y')}\n\n"

            return {
                "status": "expenses_listed",
                "message": message,
                "parse_mode": "Markdown"
            }

        except Exception as e:
            logger.error(f"Error getting user expenses: {e}", exc_info=True)
            return {
                "status": "error",
                "message": "❌ Error al obtener gastos",
                "parse_mode": "Markdown"
            }

    async def handle_departamento_callback(
        self,
        chat_id: int,
        user_id: int,
        departamento: str,
        session: AsyncSession
    ) -> Dict[str, Any]:
        """Handle departamento selection from inline keyboard."""
        try:
            # Validate departamento
            if departamento not in DEPARTAMENTO_MAP:
                return {
                    "status": "error",
                    "message": "❌ Departamento no válido. Por favor intenta de nuevo.",
                    "parse_mode": "Markdown"
                }
            
            # Store in user state
            state = self.get_user_state(chat_id)
            state.data["departamento"] = departamento
            state.state = "collecting_rfc"
            
            # Get active RFC configurations
            result = await session.execute(
                select(RFCConfig).where(RFCConfig.active == True)
                .order_by(RFCConfig.display_order, RFCConfig.name)
            )
            rfc_configs = result.scalars().all()
            
            if rfc_configs:
                # Build inline keyboard with RFC options
                keyboard = {"inline_keyboard": []}
                # Add RFCs in rows of 2
                row = []
                for i, rfc in enumerate(rfc_configs):
                    row.append({
                        "text": rfc.name,
                        "callback_data": f"rfc:{rfc.id}"
                    })
                    if len(row) == 2 or i == len(rfc_configs) - 1:
                        keyboard["inline_keyboard"].append(row)
                        row = []
                
                # Add "No Aplica" option only for manual expenses (reportar_gasto)
                if state.expense_type == "manual":
                    keyboard["inline_keyboard"].append([{
                        "text": "No Aplica",
                        "callback_data": "rfc:no_aplica"
                    }])
                
                message = f"""✅ **Departamento:** {departamento}

**Pregunta 3:**
Selecciona el RFC a utilizar:

💡 Si cometes un error, escribe `/reiniciar` para empezar de nuevo."""

                return {
                    "status": "collecting_rfc",
                    "message": message,
                    "parse_mode": "Markdown",
                    "reply_markup": keyboard
                }
            else:
                # Fallback: If no RFCs configured, skip to project (use env vars for Tocino)
                state.state = "collecting_project"
                message = f"""✅ **Departamento:** {departamento}

⚠️ **Nota:** No hay RFC configurados. Se usará la configuración por defecto.

**Pregunta 3:**
¿Cuál es el nombre del proyecto o torneo para este gasto?

*(Por ejemplo: Copa Telmex 2025, Torneo Regional, etc.)*

💡 Si cometes un error, escribe `/reiniciar` para empezar de nuevo."""

                tournaments = await fetch_active_tournaments_for_telegram_user(
                    session, user_id
                )
                
                if tournaments:
                    # Build inline keyboard with tournament options
                    keyboard = {"inline_keyboard": []}
                    # Add tournaments in rows of 2
                    row = []
                    for i, tournament in enumerate(tournaments):
                        row.append({
                            "text": tournament.name,
                            "callback_data": f"tournament:{tournament.id}"
                        })
                        if len(row) == 2 or i == len(tournaments) - 1:
                            keyboard["inline_keyboard"].append(row)
                            row = []
                    
                    return {
                        "status": "collecting_project",
                        "message": message,
                        "parse_mode": "Markdown",
                        "reply_markup": keyboard
                    }
                else:
                    return {
                        "status": "collecting_project",
                        "message": message,
                        "parse_mode": "Markdown"
                    }
        except Exception as e:
            logger.error(f"Error handling departamento callback: {e}", exc_info=True)
            return {
                "status": "error",
                "message": "❌ Error al procesar la selección. Por favor intenta de nuevo.",
                "parse_mode": "Markdown"
            }

    async def handle_concepto_callback(
        self,
        chat_id: int,
        user_id: int,
        concepto: str,
        session: AsyncSession
    ) -> Dict[str, Any]:
        """Handle concepto selection from inline keyboard."""
        try:
            # Get user state
            state = self.get_user_state(chat_id)
            tournament_id = state.data.get("tournament_id")
            
            # Try to get tournament-specific mapping first
            mapping = None
            if tournament_id:
                mapping = await get_concepto_mapping(tournament_id, concepto, session)
            
            # Validate concepto - check tournament mapping or global map
            if mapping:
                # Use tournament mapping
                sub_cuenta = mapping.sub_cuenta
                concepto_display = mapping.telegram_display_text or mapping.concepto
            elif concepto in CONCEPTO_SUB_CUENTA_MAP:
                # Fallback to global map
                sub_cuenta = CONCEPTO_SUB_CUENTA_MAP[concepto]
                concepto_display = concepto
            else:
                return {
                    "status": "error",
                    "message": "❌ Concepto no válido. Por favor intenta de nuevo.",
                    "parse_mode": "Markdown"
                }
            
            # Store in user state
            state.data["concepto"] = concepto
            state.data["sub_cuenta"] = sub_cuenta
            
            # Move to payment method collection
            state.state = "collecting_metodo_pago"
            
            # Build inline keyboard with payment method options
            keyboard = {"inline_keyboard": []}
            # Add payment methods in rows of 2
            row = []
            for i, metodo in enumerate(METODO_PAGO_LIST):
                row.append({
                    "text": metodo,
                    "callback_data": f"metodo_pago:{metodo}"
                })
                if len(row) == 2 or i == len(METODO_PAGO_LIST) - 1:
                    keyboard["inline_keyboard"].append(row)
                    row = []
            
            message = f"""✅ **Concepto:** {concepto}

**Pregunta 7:**
¿Cuál fue el método de pago utilizado?

💡 Si cometes un error, escribe `/reiniciar` para empezar de nuevo."""
            
            return {
                "status": "collecting_metodo_pago",
                "message": message,
                "parse_mode": "Markdown",
                "reply_markup": keyboard
            }
        except Exception as e:
            logger.error(f"Error handling concepto callback: {e}", exc_info=True)
            return {
                "status": "error",
                "message": "❌ Error al procesar la selección. Por favor intenta de nuevo.",
                "parse_mode": "Markdown"
            }

    async def handle_metodo_pago_callback(
        self,
        chat_id: int,
        user_id: int,
        metodo_pago: str
    ) -> Dict[str, Any]:
        """Handle payment method selection from inline keyboard."""
        try:
            # Store in user state
            state = self.get_user_state(chat_id)
            state.data["metodo_pago"] = metodo_pago
            
            # If it's a card payment, ask for last 4 digits
            if metodo_pago == "Tarjeta":
                state.state = "collecting_ultimos_4_digitos"
                
                message = f"""✅ **Método de Pago:** {metodo_pago}

**Pregunta 7.1:**
Ingresa los últimos 4 dígitos de la tarjeta utilizada:

*Por favor ingresa solo los 4 dígitos (ejemplo: 1234)*

💡 Si cometes un error, escribe `/reiniciar` para empezar de nuevo."""
                
                return {
                    "status": "collecting_ultimos_4_digitos",
                    "message": message,
                    "parse_mode": "Markdown"
                }
            else:
                # Efectivo - no need for card digits, go to amount
                if state.expense_type == "ticket":
                    state.state = "collecting_amount"
                    
                    message = f"""✅ **Método de Pago:** {metodo_pago}

**Pregunta 8:**
¿Cuál es el monto del gasto en MXN?

*Por favor ingresa solo el número (ejemplo: 150.50)*"""
                else:
                    state.state = "manual_collecting_amount"
                    
                    message = f"""✅ **Método de Pago:** {metodo_pago}

**Pregunta 8:**
¿Cuál es el monto del gasto en MXN?

*Por favor ingresa solo el número (ejemplo: 150.50)*"""
                
                return {
                    "status": "collecting_amount",
                    "message": message,
                    "parse_mode": "Markdown"
                }
        except Exception as e:
            logger.error(f"Error handling metodo_pago callback: {e}", exc_info=True)
            return {
                "status": "error",
                "message": "❌ Error al procesar la selección. Por favor intenta de nuevo.",
                "parse_mode": "Markdown"
            }

    async def handle_has_iva_callback(
        self,
        chat_id: int,
        user_id: int,
        has_iva: str
    ) -> Dict[str, Any]:
        """Handle IVA selection from inline keyboard."""
        try:
            state = self.get_user_state(chat_id)
            amount = state.data.get("amount", 0.0)
            
            if has_iva == "Sí":
                # Calculate IVA as 16% of total (extract IVA from total if it includes it)
                # Formula: IVA = total_amount × (0.16 / 1.16)
                iva_amount = amount * (0.16 / 1.16)
                state.data["iva"] = round(iva_amount, 2)
            else:
                # No IVA
                state.data["iva"] = None
            
            # Move to next step based on expense type
            if state.expense_type == "ticket":
                state.state = "collecting_cfdi_use"
                
                message = f"""✅ **IVA:** {"Sí" if has_iva == "Sí" else "No"}
{f"*Monto IVA calculado: ${state.data['iva']:.2f} MXN*" if has_iva == "Sí" else ""}

**Pregunta 9:**
¿Cuál es el Uso de CFDI que deseas aplicar?

*Por favor ingresa solo el código (ejemplos: G03, G01, G02)*

*Códigos comunes:*
• G01 - Adquisición de mercancías
• G02 - Devoluciones, descuentos o bonificaciones
• G03 - Gastos en general"""
                
                return {
                    "status": "collecting_cfdi_use",
                    "message": message,
                    "parse_mode": "Markdown"
                }
            else:
                state.state = "collecting_date"
                
                message = f"""✅ **IVA:** {"Sí" if has_iva == "Sí" else "No"}
{f"*Monto IVA calculado: ${state.data['iva']:.2f} MXN*" if has_iva == "Sí" else ""}

**Pregunta 9:**
¿En qué fecha fue el gasto?

*Formato: AAAA-MM-DD (ejemplo: 2025-11-10)*"""
                
                return {
                    "status": "collecting_date",
                    "message": message,
                    "parse_mode": "Markdown"
                }
        except Exception as e:
            logger.error(f"Error handling has_iva callback: {e}", exc_info=True)
            return {
                "status": "error",
                "message": "❌ Error al procesar la selección. Por favor intenta de nuevo.",
                "parse_mode": "Markdown"
            }

    async def handle_rfc_callback(
        self,
        chat_id: int,
        user_id: int,
        rfc_id: str,
        session: AsyncSession
    ) -> Dict[str, Any]:
        """Handle RFC selection from inline keyboard."""
        from uuid import UUID
        
        try:
            state = self.get_user_state(chat_id)
            
            # Handle "No Aplica" option (only for manual expenses)
            if rfc_id == "no_aplica":
                if state.expense_type != "manual":
                    return {
                        "status": "error",
                        "message": "❌ Esta opción solo está disponible para gastos manuales.",
                        "parse_mode": "Markdown"
                    }
                
                # Don't store rfc_id for "No Aplica"
                state.state = "collecting_project"
                
                tournaments = await fetch_active_tournaments_for_telegram_user(
                    session, user_id
                )
                
                if tournaments:
                    # Build inline keyboard with tournament options
                    keyboard = {"inline_keyboard": []}
                    # Add tournaments in rows of 2
                    row = []
                    for i, tournament in enumerate(tournaments):
                        row.append({
                            "text": tournament.name,
                            "callback_data": f"tournament:{tournament.id}"
                        })
                        if len(row) == 2 or i == len(tournaments) - 1:
                            keyboard["inline_keyboard"].append(row)
                            row = []
                    
                    message = """✅ **RFC:** No Aplica

**Pregunta 4:**
Selecciona el torneo/proyecto:

💡 Si cometes un error, escribe `/reiniciar` para empezar de nuevo."""

                    return {
                        "status": "collecting_project",
                        "message": message,
                        "parse_mode": "Markdown",
                        "reply_markup": keyboard
                    }
                else:
                    # Fallback to text input if no tournaments configured
                    message = """✅ **RFC:** No Aplica

**Pregunta 4:**
¿Cuál es el nombre del proyecto o torneo para este gasto?

*(Por ejemplo: Copa Telmex 2025, Torneo Regional, etc.)*

💡 Si cometes un error, escribe `/reiniciar` para empezar de nuevo."""

                    return {
                        "status": "collecting_project",
                        "message": message,
                        "parse_mode": "Markdown"
                    }
            
            # Get RFC configuration
            result = await session.execute(
                select(RFCConfig).where(RFCConfig.id == UUID(rfc_id))
            )
            rfc = result.scalar_one_or_none()
            
            if not rfc:
                return {
                    "status": "error",
                    "message": "❌ RFC no encontrado. Por favor intenta de nuevo.",
                    "parse_mode": "Markdown"
                }
            
            # Store in user state
            state.data["rfc_id"] = str(rfc.id)
            state.state = "collecting_project"
            
            tournaments = await fetch_active_tournaments_for_telegram_user(
                session, user_id
            )
            
            if tournaments:
                # Build inline keyboard with tournament options
                keyboard = {"inline_keyboard": []}
                # Add tournaments in rows of 2
                row = []
                for i, tournament in enumerate(tournaments):
                    row.append({
                        "text": tournament.name,
                        "callback_data": f"tournament:{tournament.id}"
                    })
                    if len(row) == 2 or i == len(tournaments) - 1:
                        keyboard["inline_keyboard"].append(row)
                        row = []
                
                message = f"""✅ **RFC:** {rfc.name}

**Pregunta 4:**
Selecciona el torneo/proyecto:

💡 Si cometes un error, escribe `/reiniciar` para empezar de nuevo."""

                return {
                    "status": "collecting_project",
                    "message": message,
                    "parse_mode": "Markdown",
                    "reply_markup": keyboard
                }
            else:
                # Fallback to text input if no tournaments configured
                message = f"""✅ **RFC:** {rfc.name}

**Pregunta 4:**
¿Cuál es el nombre del proyecto o torneo para este gasto?

*(Por ejemplo: Copa Telmex 2025, Torneo Regional, etc.)*

💡 Si cometes un error, escribe `/reiniciar` para empezar de nuevo."""

                return {
                    "status": "collecting_project",
                    "message": message,
                    "parse_mode": "Markdown"
                }
        except Exception as e:
            logger.error(f"Error handling RFC callback: {e}", exc_info=True)
            return {
                "status": "error",
                "message": "❌ Error al procesar la selección. Por favor intenta de nuevo.",
                "parse_mode": "Markdown"
            }

    async def handle_tournament_callback(
        self,
        chat_id: int,
        user_id: int,
        tournament_id: str,
        session: AsyncSession
    ) -> Dict[str, Any]:
        """Handle tournament selection from inline keyboard."""
        from uuid import UUID
        
        try:
            # Get tournament
            result = await session.execute(
                select(Tournament).where(Tournament.id == UUID(tournament_id))
            )
            tournament = result.scalar_one_or_none()
            
            if not tournament:
                return {
                    "status": "error",
                    "message": "❌ Torneo no encontrado. Por favor intenta de nuevo.",
                    "parse_mode": "Markdown"
                }

            empleado_result = await session.execute(
                select(Empleado).where(
                    Empleado.telegram_user_id == user_id,
                    Empleado.activo.is_(True),
                )
            )
            empleado = empleado_result.scalar_one_or_none()
            if empleado is not None:
                vis_err = visibility_validation_error(tournament, empleado)
                if vis_err:
                    return {
                        "status": "error",
                        "message": f"❌ {vis_err}",
                        "parse_mode": "Markdown",
                    }
            
            # Store in user state
            state = self.get_user_state(chat_id)
            state.data["project"] = tournament.name
            state.data["tournament_id"] = str(tournament.id)  # Store tournament_id for concepto mapping lookup
            # Store bank account from tournament
            if tournament.cuenta_contable_relacionada:
                state.data["cuenta_contable_base"] = tournament.cuenta_contable_relacionada
            # Move to fase_torneo collection
            state.state = "collecting_fase_torneo"
            fase_options = get_tournament_etapas(tournament)
            state.data["fase_options"] = fase_options
            keyboard = _build_inline_keyboard(fase_options, "fase_torneo")
            
            message = f"""✅ **Proyecto:** {tournament.name}

**Pregunta 5:**
Selecciona la fase del torneo:

💡 Si cometes un error, escribe `/reiniciar` para empezar de nuevo."""
            
            return {
                "status": "collecting_fase_torneo",
                "message": message,
                "parse_mode": "Markdown",
                "reply_markup": keyboard
            }
        except Exception as e:
            logger.error(f"Error handling tournament callback: {e}", exc_info=True)
            return {
                "status": "error",
                "message": "❌ Error al procesar la selección. Por favor intenta de nuevo.",
                "parse_mode": "Markdown"
            }

    async def handle_fase_torneo_callback(
        self,
        chat_id: int,
        user_id: int,
        fase_torneo: str,
        session: AsyncSession
    ) -> Dict[str, Any]:
        """Handle fase_torneo selection from inline keyboard."""
        try:
            # Store in user state
            state = self.get_user_state(chat_id)
            fase_options = state.data.get("fase_options") or get_tournament_etapas(None)
            if fase_torneo not in fase_options:
                return {
                    "status": "invalid_fase_torneo",
                    "message": "❌ Fase del torneo no válida para el torneo seleccionado. Por favor intenta de nuevo.",
                    "parse_mode": "Markdown"
                }
            state.data["fase_torneo"] = fase_torneo
            
            # Determine next state based on expense type
            if state.expense_type == "ticket":
                state.state = "collecting_concepto"
            else:
                state.state = "manual_collecting_concepto"
            
            # Check if tournament_id exists in state
            tournament_id = state.data.get("tournament_id")
            conceptos_to_show = []
            
            if tournament_id:
                # Get tournament-specific conceptos
                tournament_conceptos = await get_tournament_conceptos(tournament_id, session)
                if tournament_conceptos:
                    conceptos_to_show = tournament_conceptos
                else:
                    # Fallback to global list if no tournament mappings
                    conceptos_to_show = None
            else:
                # No tournament selected, use global list
                conceptos_to_show = None
            
            # Build inline keyboard with concepto options
            keyboard = {"inline_keyboard": []}
            row = []
            
            if conceptos_to_show:
                # Use tournament-specific conceptos
                for i, mapping in enumerate(conceptos_to_show):
                    display_text = mapping.telegram_display_text or mapping.concepto
                    row.append({
                        "text": display_text,
                        "callback_data": f"concepto:{mapping.concepto}"
                    })
                    if len(row) == 2 or i == len(conceptos_to_show) - 1:
                        keyboard["inline_keyboard"].append(row)
                        row = []
            else:
                # Fallback to global CONCEPTOS_LIST
                for i, concepto in enumerate(CONCEPTOS_LIST):
                    row.append({
                        "text": concepto,
                        "callback_data": f"concepto:{concepto}"
                    })
                    if len(row) == 2 or i == len(CONCEPTOS_LIST) - 1:
                        keyboard["inline_keyboard"].append(row)
                        row = []
            
            message = f"""✅ **Fase del Torneo:** {fase_torneo}

**Pregunta 6:**
Selecciona el concepto del gasto:

💡 Si cometes un error, escribe `/reiniciar` para empezar de nuevo."""
            
            return {
                "status": "collecting_concepto",
                "message": message,
                "parse_mode": "Markdown",
                "reply_markup": keyboard
            }
        except Exception as e:
            logger.error(f"Error handling fase_torneo callback: {e}", exc_info=True)
            return {
                "status": "error",
                "message": "❌ Error al procesar la selección. Por favor intenta de nuevo.",
                "parse_mode": "Markdown"
            }
