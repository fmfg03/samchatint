"""
Shared service functions for expense creation and CFDI generation.
Reused by both Telegram bot and web routes.
"""

import base64
import logging
import os
from datetime import datetime
from typing import Dict, Any, Optional
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import ExpenseReport, RFCConfig, Tournament, TournamentConceptoMapping, InvoiceReport, Empleado
from .budget_concept_account_service import apply_budget_concept_cuenta_mapping
from ..expense_metadata import normalize_currency, normalize_edition
from .hospedaje_tax_service import normalize_hospedaje_rate, normalize_hospedaje_state
from .tocino_client import TocinoClient, TocinoAPIError, get_tocino_client

logger = logging.getLogger(__name__)

REQUIRED_TOCINO_ENV_FIELDS = (
    "TOCINO_TAX_ID",
    "TOCINO_TAXPAYER",
    "TOCINO_POSTAL_CODE",
    "TOCINO_FISCAL_REGIMEN",
)

# Department mapping (full name -> code)
DEPARTAMENTO_MAP = {
    "Mercadotecnia": "M",
    "Operaciones": "O",
    "Finanzas": "F",
    "Gerencia": "G",
}

# Concepto to Sub-Cuenta mapping (fallback)
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


async def generate_reference_number(
    session: AsyncSession,
    departamento: str
) -> str:
    """
    Generate reference number based on department, year, and sequence.
    
    Format: D-YY######
    - D: First letter of department (M, O, F, G, or U)
    - YY: Last two digits of current year
    - ######: 6-digit sequence number (starting from 000001 per department/year)
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

    # Serialize allocation per prefix to prevent concurrent duplicate references.
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


async def get_concepto_mapping(
    tournament_id: Optional[str],
    concepto: str,
    session: AsyncSession
) -> Optional[Dict[str, Any]]:
    """
    Get concepto mapping (sub_cuenta) for a given concepto and tournament.
    Returns dict with 'sub_cuenta' and 'concepto_display' keys, or None if not found.
    """
    if tournament_id:
        try:
            result = await session.execute(
                select(TournamentConceptoMapping)
                .where(
                    TournamentConceptoMapping.tournament_id == UUID(tournament_id),
                    TournamentConceptoMapping.concepto == concepto,
                    TournamentConceptoMapping.active == True
                )
            )
            mapping = result.scalar_one_or_none()
            if mapping:
                return {
                    'sub_cuenta': mapping.sub_cuenta,
                    'concepto_display': mapping.telegram_display_text or mapping.concepto
                }
        except Exception as e:
            logger.error(f"Error getting tournament concepto mapping: {e}", exc_info=True)
    
    # Fallback to global map
    if concepto in CONCEPTO_SUB_CUENTA_MAP:
        return {
            'sub_cuenta': CONCEPTO_SUB_CUENTA_MAP[concepto],
            'concepto_display': concepto
        }
    
    return None


async def create_expense_from_data(
    session: AsyncSession,
    concepto: str,
    gasto_cantidad: float,
    fecha: datetime,
    empleado_id: Optional[UUID] = None,
    proyecto: Optional[str] = None,
    tipo_gasto: str = "ticket",  # "ticket" or "manual"
    departamento: Optional[str] = None,
    fase_torneo: Optional[str] = None,
    metodo_pago: Optional[str] = None,
    ultimos_4_digitos: Optional[str] = None,
    iva: Optional[float] = None,
    hospedaje_entidad_fiscal: Optional[str] = None,
    hospedaje_tasa_impuesto: Optional[float] = None,
    hospedaje_impuesto_monto: Optional[float] = None,
    hospedaje_impuesto_confirmado: bool = False,
    cfdi_use: Optional[str] = None,
    archivo_nombre: Optional[str] = None,
    archivo_data: Optional[str] = None,  # Base64 encoded
    archivo_path: Optional[str] = None,
    tournament_id: Optional[str] = None,
    rfc_id: Optional[str] = None,
    nombre_enviador: Optional[str] = None,
    origen: Optional[str] = None,
    skip_initial_tocino: bool = False,
    categorias: Optional[list[str]] = None,
    edicion: Optional[int] = None,
    currency: str = "MXN",
    budget_concept_id: Optional[UUID] = None,
) -> ExpenseReport:
    """
    Create an ExpenseReport from structured data.
    This is the shared function used by both Telegram and web routes.
    
    Args:
        session: Database session
        concepto: Expense concept
        gasto_cantidad: Amount
        fecha: Expense date
        empleado_id: Employee ID (UUID, optional for programmatic calls)
        proyecto: Project/tournament name (optional, defaults to "Importación Masiva" if not provided)
        tipo_gasto: "ticket" or "manual"
        departamento: Department name
        fase_torneo: Tournament phase
        metodo_pago: Payment method
        ultimos_4_digitos: Last 4 digits of card (if applicable)
        iva: IVA amount (None = no IVA, numeric = IVA amount)
        hospedaje_entidad_fiscal: State/entity for lodging tax, when applicable
        hospedaje_tasa_impuesto: Lodging-tax rate; accepts decimal or percent-style value
        hospedaje_impuesto_monto: Explicit local lodging-tax amount
        hospedaje_impuesto_confirmado: Whether the lodging-tax values were explicitly confirmed
        cfdi_use: CFDI use code (for ticket expenses)
        archivo_nombre: File name (for ticket expenses)
        archivo_data: Base64 encoded file data (for ticket expenses)
        archivo_path: File path (for ticket expenses)
        tournament_id: Tournament UUID (optional)
        rfc_id: RFC config UUID (optional, for ticket expenses)
        nombre_enviador: Name of person submitting expense
        origen: Source/origin identifier (e.g., "amex_batch" for AMEX batch imports)
        skip_initial_tocino: If True and tipo_gasto is ticket, leave estado_factura None
            (comprobante guardado sin envío a Tocino en este paso).
    
    Returns:
        Created ExpenseReport instance
    """
    # Set default proyecto if not provided
    if not proyecto:
        proyecto = "Importación Masiva"
    
    # Generate reference number
    if not departamento:
        departamento = "Operaciones"  # Default
    reference_number = await generate_reference_number(session, departamento)
    
    # Get concepto mapping (sub_cuenta)
    concepto_mapping = await get_concepto_mapping(tournament_id, concepto, session)
    sub_cuenta = concepto_mapping['sub_cuenta'] if concepto_mapping else None
    
    # Get tournament cuenta_contable_base if tournament_id provided
    cuenta_contable_base = None
    if tournament_id:
        try:
            result = await session.execute(
                select(Tournament).where(Tournament.id == UUID(tournament_id))
            )
            tournament = result.scalar_one_or_none()
            if tournament and tournament.cuenta_contable_relacionada:
                cuenta_contable_base = tournament.cuenta_contable_relacionada
        except Exception as e:
            logger.error(f"Error getting tournament cuenta_contable: {e}", exc_info=True)
    
    # Set fase_torneo to source tag if origen is provided and fase_torneo is not set
    if origen and not fase_torneo:
        if origen == "amex_batch":
            fase_torneo = "AMEX_BATCH"
        else:
            # For other origins, use the origen value as tag
            fase_torneo = origen.upper()
    
    # Safe defaulting: if empleado_id is provided and nombre_enviador is None, fetch empleado and set nombre
    if empleado_id is not None and nombre_enviador is None:
        try:
            empleado = await session.get(Empleado, empleado_id)
            if empleado is not None:
                nombre_enviador = empleado.nombre
        except Exception as e:
            logger.warning(f"Could not fetch empleado {empleado_id} for nombre_enviador defaulting: {e}")
            # Leave nombre_enviador as None if empleado not found (non-breaking)
    
    # estado_factura: ticket + immediate Tocino path uses "pendiente" until trigger_cfdi_generation updates it;
    # ticket + skip_initial_tocino keeps None until user solicits CFDI later.
    if tipo_gasto == "ticket":
        initial_estado = None if skip_initial_tocino else "pendiente"
    else:
        initial_estado = None

    hospedaje_rate = normalize_hospedaje_rate(hospedaje_tasa_impuesto)
    hospedaje_state = normalize_hospedaje_state(hospedaje_entidad_fiscal)
    hospedaje_amount = None
    if hospedaje_impuesto_monto not in (None, ""):
        try:
            hospedaje_amount = round(float(hospedaje_impuesto_monto), 2)
        except (TypeError, ValueError):
            hospedaje_amount = None

    # Create expense
    expense = ExpenseReport(
        empleado_id=empleado_id,
        proyecto=proyecto,
        concepto=concepto,
        sub_cuenta=sub_cuenta,
        gasto_cantidad=gasto_cantidad,
        categorias=list(categorias or []),
        edicion=normalize_edition(edicion),
        currency=normalize_currency(currency),
        budget_concept_id=budget_concept_id,
        fecha=fecha,
        tipo_gasto=tipo_gasto,
        numero_referencia=reference_number,
        archivo_nombre=archivo_nombre,
        archivo_path=archivo_path,
        archivo_data=archivo_data,
        estado_factura=initial_estado,
        estado_reembolso="pendiente",
        cfdi_use=cfdi_use,
        cuenta_contable_base=cuenta_contable_base,
        nombre_enviador=nombre_enviador,
        departamento=departamento,
        fase_torneo=fase_torneo,
        metodo_pago=metodo_pago,
        ultimos_4_digitos=ultimos_4_digitos,
        iva=iva,
        hospedaje_entidad_fiscal=hospedaje_state,
        hospedaje_tasa_impuesto=hospedaje_rate,
        hospedaje_impuesto_monto=hospedaje_amount,
        hospedaje_impuesto_confirmado=bool(hospedaje_impuesto_confirmado),
        origen=origen,
    )
    
    session.add(expense)
    await session.flush()  # Get expense.id

    await apply_budget_concept_cuenta_mapping(
        session, expense, budget_concept_id=budget_concept_id
    )

    logger.info(f"Created expense {expense.id} with reference {reference_number} (origen={origen})")

    return expense


def tocino_payment_fields(
    *,
    metodo_pago: Optional[str] = None,
    ultimos_4_digitos: Optional[str] = None,
    expense: Optional[ExpenseReport] = None,
) -> Dict[str, str]:
    """Map expense payment fields to Tocino API payment_form / card_last_digits."""
    if expense is not None:
        if metodo_pago is None:
            metodo_pago = expense.metodo_pago
        if ultimos_4_digitos is None:
            ultimos_4_digitos = expense.ultimos_4_digitos

    fields: Dict[str, str] = {}
    if metodo_pago and str(metodo_pago).strip():
        fields["payment_form"] = str(metodo_pago).strip()
    if ultimos_4_digitos and str(ultimos_4_digitos).strip():
        digits = str(ultimos_4_digitos).strip()
        if len(digits) == 4 and digits.isdigit():
            fields["card_last_digits"] = digits
    return fields


async def trigger_cfdi_generation(
    session: AsyncSession,
    expense: ExpenseReport,
    rfc_id: Optional[str] = None,
    cfdi_use: Optional[str] = None,
) -> Optional[str]:
    """
    Trigger CFDI generation for an expense via Tocino API.
    This is the shared function used by both Telegram and web routes.
    
    Args:
        session: Database session
        expense: ExpenseReport instance (must have archivo_data for ticket expenses)
        rfc_id: RFC config UUID (optional, will use env vars if not provided)
        cfdi_use: CFDI use code (optional, defaults to expense.cfdi_use or 'G03')
    
    Returns:
        nova_request_id if successful, None if failed
    """
    # Only ticket expenses can generate CFDI
    if expense.tipo_gasto != "ticket":
        logger.warning(f"Attempted to trigger CFDI for non-ticket expense {expense.id}")
        return None
    
    # Check if expense already has a CFDI request
    if expense.nova_request_id:
        # Check if there's an existing invoice_report
        result = await session.execute(
            select(InvoiceReport).where(InvoiceReport.nova_request_id == expense.nova_request_id)
        )
        existing_invoice = result.scalar_one_or_none()
        if existing_invoice:
            estado = existing_invoice.estado_factura
            if estado in ["completada", "en_proceso"]:
                logger.info(f"Expense {expense.id} already has CFDI request {expense.nova_request_id} with estado {estado}")
                return expense.nova_request_id
            # If error state, allow retry
    
    # Validate required data
    if not expense.archivo_data:
        logger.error(f"Expense {expense.id} missing archivo_data for CFDI generation")
        expense.estado_factura = "error"
        expense.mensaje_error = "Este gasto no tiene comprobante adjunto para facturación"
        session.add(expense)
        await session.commit()
        return None
    
    # Get Tocino client
    try:
        tocino_client = get_tocino_client()
    except Exception as e:
        logger.error(f"Failed to get Tocino client: {e}", exc_info=True)
        expense.estado_factura = "error"
        expense.mensaje_error = f"Error de configuración: No se pudo conectar con el servicio de facturación. Verifique las variables de entorno TOCINO_API_KEY y TOCINO_BASE_URL."
        session.add(expense)
        await session.commit()
        return None
    
    # Prepare Tocino payload
    # Use selected RFC configuration or fallback to environment variables
    if rfc_id:
        # Fetch RFC configuration from database
        try:
            result = await session.execute(
                select(RFCConfig).where(RFCConfig.id == UUID(rfc_id))
            )
            rfc = result.scalar_one_or_none()
            
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
                    "cfdi_use_code": cfdi_use or expense.cfdi_use or "G03",
                    "csf_pdf": "",
                    "filename": expense.archivo_nombre or "receipt.jpg",
                    "file": expense.archivo_data,  # Base64 encoded file
                }
                tocino_payload.update(tocino_payment_fields(expense=expense))
            else:
                # RFC ID provided but not found, fallback to env vars
                logger.warning(f"RFC {rfc_id} not found, using environment variables")
                tocino_payload = _build_tocino_payload_from_env(
                    expense, cfdi_use or expense.cfdi_use or "G03"
                )
        except Exception as e:
            logger.error(f"Error fetching RFC config: {e}", exc_info=True)
            tocino_payload = _build_tocino_payload_from_env(
                expense, cfdi_use or expense.cfdi_use or "G03"
            )
    else:
        # No RFC ID provided, fallback to environment variables
        logger.info("No RFC ID provided, using environment variables")
        tocino_payload = _build_tocino_payload_from_env(
            expense, cfdi_use or expense.cfdi_use or "G03"
        )
    
    # Submit to Tocino
    try:
        tocino_result = tocino_client.submit_ticket(tocino_payload)
        
        # Extract ticket ID (Tocino returns ticket_id and internal_id; we store as nova_request_id)
        nova_request_id = None
        if isinstance(tocino_result, dict):
            nova_request_id = tocino_result.get("ticket_id") or tocino_result.get("internal_id") or tocino_result.get("nova_request_id")
        
        if nova_request_id:
            # Update expense with nova_request_id
            expense.nova_request_id = nova_request_id
            expense.estado_factura = "en_proceso"
            session.add(expense)
            await session.commit()
            
            logger.info(f"✅ Tocino API call successful: {nova_request_id} for expense {expense.id}")
            return nova_request_id
        else:
            logger.warning(f"Tocino API response missing nova_request_id for expense {expense.id}")
            expense.estado_factura = "error"
            expense.mensaje_error = "El servicio de facturación no devolvió un ID de solicitud. Por favor, intente más tarde."
            session.add(expense)
            await session.commit()
            return None
            
    except TocinoAPIError as e:
        logger.error(f"Tocino API error for expense {expense.id}: {e}", exc_info=True)
        # Update expense with error state
        expense.estado_factura = "error"
        expense.mensaje_error = f"Error de Tocino API: {str(e)}"
        session.add(expense)
        await session.commit()
        return None
    except Exception as e:
        logger.error(f"Unexpected error calling Tocino API for expense {expense.id}: {e}", exc_info=True)
        # Update expense with error state
        expense.estado_factura = "error"
        expense.mensaje_error = f"Error inesperado: {str(e)}"
        session.add(expense)
        await session.commit()
        return None


def _required_tocino_env(name: str) -> str:
    value = (os.getenv(name) or "").strip()
    if not value:
        raise ValueError(
            "Missing required Tocino taxpayer configuration: " + name
        )
    return value


def _optional_tocino_env(name: str) -> str:
    return (os.getenv(name) or "").strip()


def build_tocino_payload_from_env(
    expense: ExpenseReport, cfdi_use: str
) -> Dict[str, Any]:
    """Build Tocino payload from environment variables."""
    missing = [
        name
        for name in REQUIRED_TOCINO_ENV_FIELDS
        if not (os.getenv(name) or "").strip()
    ]
    if missing:
        raise ValueError(
            "Missing required Tocino taxpayer configuration: "
            + ", ".join(missing)
        )
    payload: Dict[str, Any] = {
        "tax_id": _required_tocino_env("TOCINO_TAX_ID"),
        "taxpayer": _required_tocino_env("TOCINO_TAXPAYER"),
        "taxpayer_name": _optional_tocino_env("TOCINO_TAXPAYER_NAME"),
        "taxpayer_last_name": _optional_tocino_env(
            "TOCINO_TAXPAYER_LAST_NAME"
        ),
        "taxpayer_second_last_name": _optional_tocino_env(
            "TOCINO_TAXPAYER_SECOND_LAST_NAME"
        ),
        "street_address_1": _optional_tocino_env("TOCINO_STREET_ADDRESS_1"),
        "ext_num": _optional_tocino_env("TOCINO_EXT_NUM"),
        "int_num": _optional_tocino_env("TOCINO_INT_NUM"),
        "street_address_2": _optional_tocino_env("TOCINO_STREET_ADDRESS_2"),
        "city": _optional_tocino_env("TOCINO_CITY"),
        "state": _optional_tocino_env("TOCINO_STATE"),
        "country": "México",
        "postal_code": _required_tocino_env("TOCINO_POSTAL_CODE"),
        "fiscal_regimen_code": _required_tocino_env("TOCINO_FISCAL_REGIMEN"),
        "cfdi_use_code": cfdi_use,
        "csf_pdf": "",
        "filename": expense.archivo_nombre or "receipt.jpg",
        "file": expense.archivo_data,
    }
    payload.update(tocino_payment_fields(expense=expense))
    return payload


_build_tocino_payload_from_env = build_tocino_payload_from_env
