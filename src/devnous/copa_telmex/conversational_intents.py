"""
Conversational Intents System for Copa Telmex Bot

Detects user intentions from natural language and manages conversational context.
"""

import logging
from typing import Dict, Any, Optional, List
from enum import Enum
import re


logger = logging.getLogger(__name__)


class Intent(Enum):
    """User intentions"""
    # Team management
    EDIT_TEAM_NAME = "edit_team_name"
    ADD_MORE_PLAYERS = "add_more_players"
    VIEW_TEAM_SUMMARY = "view_team_summary"
    DELETE_TEAM = "delete_team"

    # Player management
    DELETE_PLAYER = "delete_player"
    EDIT_PLAYER = "edit_player"
    LIST_PLAYERS = "list_players"

    # General
    HELP = "help"
    CANCEL = "cancel"
    START_NEW_REGISTRATION = "start_new_registration"
    VIEW_STATISTICS = "view_statistics"

    # Unknown
    UNKNOWN = "unknown"


class ConversationalIntentDetector:
    """
    Detects user intentions from natural language messages.

    Uses pattern matching and keyword detection to identify what the user wants.
    """

    # Intent patterns (Spanish)
    INTENT_PATTERNS = {
        Intent.EDIT_TEAM_NAME: [
            r'edit(ar|a)?\s+(el\s+)?nombre\s+(del\s+)?equipo',
            r'cambi(ar|a)?\s+(el\s+)?nombre\s+(del\s+)?equipo',
            r'modific(ar|a)?\s+(el\s+)?nombre\s+(del\s+)?equipo',
            r'correg(ir|i)?\s+(el\s+)?nombre\s+(del\s+)?equipo',
            r'quiero\s+cambiar\s+el\s+nombre',
            r'necesito\s+editar\s+el\s+nombre',
        ],

        Intent.ADD_MORE_PLAYERS: [
            r'agreg(ar|a)?\s+(más\s+)?jugadores',
            r'añad(ir|e)?\s+(más\s+)?jugadores',
            r'teng(o|a)\s+más\s+jugadores',
            r'imagen\s+(del\s+)?revers(o|a)',
            r'otra\s+foto',
            r'falta(n)?\s+jugadores',
            r'hay\s+más\s+jugadores',
            r'contin(uar|úa)\s+con\s+el\s+mismo\s+equipo',
            r'mismo\s+equipo',
            r'segunda\s+página',
            r'completar\s+el\s+roster',
        ],

        Intent.VIEW_TEAM_SUMMARY: [
            r've(r|a)?\s+resum(en|in)',
            r'muéstra(me)?\s+(el\s+)?resum(en|in)',
            r'cómo\s+va\s+(el\s+)?equipo',
            r'qué\s+teng(o|a)\s+registrado',
            r've(r|a)?\s+(el\s+)?equipo',
            r'inform(e|ación)\s+del\s+equipo',
            r'estatus\s+del\s+equipo',
            r'últim(o|a)\s+equipo',

            # Question patterns - Team info (cuál/cual/como/cuanto)
            r'cuá?l\s+(es|era)\s+(el\s+)?(nombre|equipo)',
            r'có?mo\s+se\s+llama\s+(el\s+)?equipo',
            r'qué\s+equipo\s+(es|tengo|registré)',
            r'dame\s+(el\s+)?(nombre|info|datos)\s+del\s+equipo',

            # Question patterns - Player count
            r'cuá?nt(os|as)\s+jugadores',
            r'cuá?nt(os|as)\s+hay',
            r'cuá?nt(os|as)\s+(se\s+)?registraron',
            r'cuá?nt(os|as)\s+(están\s+)?registrados',
            r'nú?mero\s+de\s+jugadores',

            # Question patterns - League name
            r'cuá?l\s+(es|era)\s+(el\s+)?(nombre\s+de\s+la\s+)?liga',
            r'có?mo\s+se\s+llama\s+la\s+liga',
            r'qué\s+liga\s+(es|tengo)',
            r'nombre\s+de\s+la\s+liga',

            # Question patterns - League phone
            r'cuá?l\s+(es|era)\s+(el\s+)?telé?fono\s+(de\s+la\s+)?liga',
            r'telé?fono\s+(de\s+la\s+)?liga',
            r'nú?mero\s+(de\s+)?telé?fono\s+(de\s+la\s+)?liga',

            # Question patterns - League address
            r'cuá?l\s+(es|era)\s+(el\s+)?domicilio\s+(de\s+la\s+)?liga',
            r'dó?nde\s+(está|queda)\s+la\s+liga',
            r'domicilio\s+(de\s+la\s+)?liga',
            r'direcci(ó?n|on)\s+(de\s+la\s+)?liga',
            r'ubicaci(ó?n|on)\s+(de\s+la\s+)?liga',

            # Question patterns - State
            r'cuá?l\s+(es|era)\s+(el\s+)?estado',
            r'en\s+qué\s+estado',
            r'de\s+qué\s+estado',

            # Question patterns - Municipality
            r'cuá?l\s+(es|era)\s+(el\s+)?municipio',
            r'en\s+qué\s+municipio',
            r'de\s+qué\s+municipio',

            # Question patterns - Contact phone
            r'cuá?l\s+(es|era)\s+(el\s+)?telé?fono\s+(de\s+)?contacto',
            r'telé?fono\s+(de\s+)?contacto',
            r'nú?mero\s+(de\s+)?contacto',
            r'có?mo\s+los\s+contacto',
            r'có?mo\s+te\s+contacto',
        ],

        Intent.DELETE_TEAM: [
            r'elimin(ar|a)?\s+(el\s+)?equipo',
            r'borr(ar|a)?\s+(el\s+)?equipo',
            r'cancel(ar|a)?\s+(el\s+)?equipo',
            r'deshacer\s+(el\s+)?equipo',
            r'no\s+quiero\s+(este\s+)?equipo',
        ],

        Intent.DELETE_PLAYER: [
            r'elimin(ar|a)?\s+(al\s+)?jugador',
            r'borr(ar|a)?\s+(al\s+)?jugador',
            r'quit(ar|a)?\s+(al\s+)?jugador',
            r'remov(er|e)?\s+(al\s+)?jugador',
            r'no\s+es\s+jugador',
        ],

        Intent.EDIT_PLAYER: [
            r'edit(ar|a)?\s+(al\s+)?jugador',
            r'correg(ir|i)?\s+(al\s+)?jugador',
            r'modific(ar|a)?\s+(al\s+)?jugador',
            r'cambi(ar|a)?\s+datos\s+del\s+jugador',
        ],

        Intent.LIST_PLAYERS: [
            r'list(ar|a)?\s+jugadores',
            r've(r|a)?\s+jugadores',
            r'muéstra(me)?\s+(los\s+)?jugadores',
            r'cuánt(os|as)\s+jugadores',
            r'quiénes\s+son\s+(los\s+)?jugadores',
            # Question patterns
            r'quié?n(es)?\s+(está|hay|son)',
            r'cuá?l(es)?\s+son\s+(los\s+)?jugadores',
            r'dame\s+(la\s+)?lista\s+de\s+jugadores',
            r'qué\s+jugadores\s+(hay|tengo|están)',
        ],

        Intent.HELP: [
            r'^ayuda$',
            r'ayúda(me)?',
            r'qué\s+puedo\s+hacer',
            r'cómo\s+funciona',
            r'comandos',
            r'opciones',
            r'no\s+entiendo',
            r'necesito\s+ayuda',
        ],

        Intent.CANCEL: [
            r'^cancel(ar|a)?$',
            r'^stop$',
            r'^para(r)?$',
            r'deten(er|te)',
            r'ya\s+no',
            r'olvíd(alo|ala)',
            r'mejor\s+no',
        ],

        Intent.START_NEW_REGISTRATION: [
            r'nuev(o|a)\s+registro',
            r'registrar\s+equipo',
            r'empez(ar|a)\s+de\s+nuev(o|a)',
            r'otr(o|a)\s+equipo',
            r'comenzar',
            r'^start$',
            r'^iniciar$',
        ],

        Intent.VIEW_STATISTICS: [
            r'estadísticas',
            r've(r|a)?\s+est(adísticas|ats)',
            r'cuánt(os|as)\s+equipos',
            r'total\s+de\s+equipos',
            r'resum(en|in)\s+general',
        ],
    }

    def detect_intent(self, message: str) -> Dict[str, Any]:
        """
        Detect user intention from message.

        Args:
            message: User message text

        Returns:
            {
                'intent': Intent enum,
                'confidence': float (0.0-1.0),
                'entities': Dict[str, Any],  # Extracted entities
                'original_message': str
            }
        """
        message_lower = message.lower().strip()

        # Check each intent pattern
        for intent, patterns in self.INTENT_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, message_lower):
                    logger.info(f"✅ Intent detected: {intent.value} (pattern: {pattern})")

                    # Extract entities based on intent
                    entities = self._extract_entities(intent, message)

                    return {
                        'intent': intent,
                        'confidence': 0.9,  # High confidence for pattern match
                        'entities': entities,
                        'original_message': message
                    }

        # No pattern matched
        logger.info(f"❓ No intent detected for: {message}")
        return {
            'intent': Intent.UNKNOWN,
            'confidence': 0.0,
            'entities': {},
            'original_message': message
        }

    def _extract_entities(self, intent: Intent, message: str) -> Dict[str, Any]:
        """Extract entities from message based on intent"""
        entities = {}

        if intent == Intent.DELETE_PLAYER or intent == Intent.EDIT_PLAYER:
            # Try to extract player name
            # Pattern: "eliminar a Juan García" or "eliminar al jugador Juan García"
            patterns = [
                r'(?:al\s+jugador\s+)?([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)+)',
                r'(?:jugador\s+)?([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)+)',
            ]

            for pattern in patterns:
                match = re.search(pattern, message)
                if match:
                    entities['player_name'] = match.group(1)
                    break

        elif intent == Intent.EDIT_TEAM_NAME:
            # Try to extract new team name
            # Pattern: "cambiar nombre a Los Tigres" or "nombre: Los Tigres"
            patterns = [
                r'(?:nombre\s+a\s+)([A-ZÁÉÍÓÚÑ][^\n]+)',
                r'(?:nombre:\s*)([A-ZÁÉÍÓÚÑ][^\n]+)',
                r'(?:llamar\s+)([A-ZÁÉÍÓÚÑ][^\n]+)',
            ]

            for pattern in patterns:
                match = re.search(pattern, message)
                if match:
                    entities['new_team_name'] = match.group(1).strip()
                    break

        return entities

    def get_intent_response(self, intent: Intent) -> str:
        """Get initial response for detected intent"""
        responses = {
            Intent.EDIT_TEAM_NAME: (
                "✏️ *Editar Nombre del Equipo*\n\n"
                "Escribe el nuevo nombre del equipo:"
            ),

            Intent.ADD_MORE_PLAYERS: (
                "📸 *Agregar Más Jugadores*\n\n"
                "Perfecto! Envía la foto con los jugadores adicionales.\n"
                "Los agregaré al mismo equipo."
            ),

            Intent.VIEW_TEAM_SUMMARY: (
                "📊 *Resumen del Equipo*\n\n"
                "Consultando información del último equipo registrado..."
            ),

            Intent.DELETE_TEAM: (
                "🗑️ *Eliminar Equipo*\n\n"
                "⚠️ ¿Estás seguro que deseas eliminar el equipo?\n\n"
                "Esta acción NO se puede deshacer.\n\n"
                "Responde 'SÍ' para confirmar o 'NO' para cancelar."
            ),

            Intent.DELETE_PLAYER: (
                "🗑️ *Eliminar Jugador*\n\n"
                "¿Qué jugador deseas eliminar?\n"
                "Escribe el nombre completo del jugador."
            ),

            Intent.EDIT_PLAYER: (
                "✏️ *Editar Jugador*\n\n"
                "¿Qué jugador deseas editar?\n"
                "Escribe el nombre completo del jugador."
            ),

            Intent.LIST_PLAYERS: (
                "👥 *Lista de Jugadores*\n\n"
                "Consultando jugadores registrados..."
            ),

            Intent.HELP: (
                "💡 *Ayuda - Copa Telmex OCR Bot*\n\n"
                "*Qué puedo hacer por ti:*\n\n"
                "📸 *Registrar equipo:*\n"
                "• Envía una foto del roster con jugadores\n\n"
                "✏️ *Editar:*\n"
                "• \"Cambiar nombre del equipo\"\n"
                "• \"Editar jugador [nombre]\"\n\n"
                "➕ *Agregar:*\n"
                "• \"Agregar más jugadores\" (segunda foto)\n\n"
                "📊 *Consultar:*\n"
                "• \"Ver resumen del equipo\"\n"
                "• \"Listar jugadores\"\n"
                "• \"Estadísticas\"\n\n"
                "🗑️ *Eliminar:*\n"
                "• \"Eliminar jugador [nombre]\"\n"
                "• \"Eliminar equipo\"\n\n"
                "💬 Solo dime qué necesitas en lenguaje natural!"
            ),

            Intent.CANCEL: (
                "❌ *Operación Cancelada*\n\n"
                "Entendido. ¿En qué más puedo ayudarte?"
            ),

            Intent.START_NEW_REGISTRATION: (
                "🆕 *Nuevo Registro*\n\n"
                "¡Perfecto! Envía la foto del roster del nuevo equipo."
            ),

            Intent.VIEW_STATISTICS: (
                "📊 *Estadísticas*\n\n"
                "Consultando estadísticas generales..."
            ),

            Intent.UNKNOWN: (
                "❓ *No entendí*\n\n"
                "No estoy seguro de lo que necesitas.\n\n"
                "Puedes:\n"
                "• Enviar una foto del roster\n"
                "• Escribir \"ayuda\" para ver comandos\n"
                "• Decirme qué necesitas hacer"
            ),
        }

        return responses.get(intent, responses[Intent.UNKNOWN])


class ConversationState(Enum):
    """Conversation states"""
    IDLE = "idle"
    WAITING_PHOTO = "waiting_photo"
    WAITING_TEAM_NAME = "waiting_team_name"
    WAITING_PLAYER_NAME = "waiting_player_name"
    WAITING_CONFIRMATION = "waiting_confirmation"
    WAITING_FIELD_EDIT_CHOICE = "waiting_field_edit_choice"
    WAITING_FIELD_VALUE = "waiting_field_value"
    WAITING_MORE_PLAYERS_CONFIRMATION = "waiting_more_players_confirmation"
    WAITING_BACK_PHOTO = "waiting_back_photo"
    PROCESSING = "processing"


class ConversationContext:
    """Manages conversation context for a chat"""

    def __init__(self):
        self.state = ConversationState.IDLE
        self.current_intent: Optional[Intent] = None
        self.last_team_id: Optional[str] = None
        self.last_team_name: Optional[str] = None
        self.pending_entities: Dict[str, Any] = {}
        self.conversation_history: List[str] = []

    def set_state(self, state: ConversationState, intent: Optional[Intent] = None):
        """Set conversation state"""
        self.state = state
        self.current_intent = intent
        logger.info(f"🔄 State changed: {state.value} (intent: {intent})")

    def add_to_history(self, message: str):
        """Add message to conversation history"""
        self.conversation_history.append(message)
        # Keep only last 10 messages
        if len(self.conversation_history) > 10:
            self.conversation_history = self.conversation_history[-10:]

    def clear(self):
        """Clear conversation context"""
        self.state = ConversationState.IDLE
        self.current_intent = None
        self.pending_entities = {}
        logger.info("🧹 Conversation context cleared")


def get_intent_detector() -> ConversationalIntentDetector:
    """Get intent detector singleton"""
    return ConversationalIntentDetector()
