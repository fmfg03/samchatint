"""
Tournament Bot - Base class for all tournament management bots.

Each tournament inherits from this class and customizes the modules
for finance, operations, and marketing.
"""

import logging
from typing import Dict, Any, Optional, List
from datetime import datetime
from enum import Enum
import yaml
from pathlib import Path

logger = logging.getLogger(__name__)


class MessageIntent(Enum):
    """Message intent categories"""
    FINANCE = "finance"
    OPERATIONS = "operations"
    MARKETING = "marketing"
    GENERAL = "general"
    UNKNOWN = "unknown"


class Message:
    """Standardized message format"""

    def __init__(
        self,
        text: str,
        chat_id: int,
        user_id: int,
        intent: Optional[MessageIntent] = None,
        data: Optional[Dict[str, Any]] = None,
        photo: Optional[bytes] = None,
        timestamp: Optional[datetime] = None
    ):
        self.text = text
        self.chat_id = chat_id
        self.user_id = user_id
        self.intent = intent or MessageIntent.UNKNOWN
        self.data = data or {}
        self.photo = photo
        self.timestamp = timestamp or datetime.now()

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for logging/storage"""
        return {
            'text': self.text,
            'chat_id': self.chat_id,
            'user_id': self.user_id,
            'intent': self.intent.value,
            'data': self.data,
            'timestamp': self.timestamp.isoformat()
        }


class TournamentBot:
    """
    Base class for tournament management bots.

    Each tournament bot manages:
    - Finance: Payments, sponsorships, budgets
    - Operations: Team registration, scheduling, logistics
    - Marketing: Communications, social media, reports

    Usage:
        class CopaTelmexBot(TournamentBot):
            def __init__(self):
                super().__init__(
                    tournament_id="copa_telmex",
                    config_path="config/copa_telmex.yaml"
                )
    """

    def __init__(
        self,
        tournament_id: str,
        config_path: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None
    ):
        """
        Initialize tournament bot.

        Args:
            tournament_id: Unique identifier for tournament
            config_path: Path to YAML config file
            config: Direct config dictionary (alternative to config_path)
        """
        self.tournament_id = tournament_id
        self.config = self._load_config(config_path, config)
        self.started_at = datetime.now()

        # Will be initialized by subclass
        self.finance = None
        self.operations = None
        self.marketing = None
        self.db = None

        logger.info(f"✅ Tournament bot initialized: {tournament_id}")

    def _load_config(
        self,
        config_path: Optional[str],
        config: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Load configuration from file or dict"""
        if config:
            return config

        if config_path:
            path = Path(config_path)
            if path.exists():
                with open(path, 'r') as f:
                    return yaml.safe_load(f)

        # Default config
        return {
            'tournament_id': self.tournament_id,
            'name': self.tournament_id.replace('_', ' ').title(),
            'modules': {
                'finance': {'enabled': True},
                'operations': {'enabled': True},
                'marketing': {'enabled': True}
            }
        }

    def detect_intent(self, message: Message) -> MessageIntent:
        """
        Detect message intent using keywords.

        Override this method for custom intent detection (e.g., using LLM).
        """
        text = message.text.lower()

        # Finance keywords
        finance_keywords = ['pago', 'payment', 'dinero', 'money', 'patrocinio',
                           'sponsorship', 'presupuesto', 'budget', 'factura', 'invoice']
        if any(keyword in text for keyword in finance_keywords):
            return MessageIntent.FINANCE

        # Operations keywords
        operations_keywords = [
            'equipo', 'team',
            'jugador', 'player',
            'partido', 'match',
            'calendario', 'schedule',
            'registro', 'register',
            # Roster/admin fields
            'representante', 'representate', 'reprsentate',
            'delegado', 'entrenador', 'manager', 'responsable',
            'curp', 'nacimiento', 'fecha',
            'corregir', 'editar',
            'correo', 'email',
            'liga', 'rama', 'genero', 'categoría', 'categoria',
            'municipio', 'estado',
        ]
        if any(keyword in text for keyword in operations_keywords):
            return MessageIntent.OPERATIONS

        # Marketing keywords
        marketing_keywords = ['comunicado', 'announcement', 'redes', 'social',
                             'reporte', 'report', 'estadisticas', 'stats']
        if any(keyword in text for keyword in marketing_keywords):
            return MessageIntent.MARKETING

        return MessageIntent.GENERAL

    async def process_message(self, message: Message) -> str:
        """
        Process incoming message and route to appropriate module.

        Args:
            message: Standardized message object

        Returns:
            Response text
        """
        try:
            # Detect intent if not provided
            if message.intent == MessageIntent.UNKNOWN:
                message.intent = self.detect_intent(message)

            logger.info(f"📩 Processing message: {message.intent.value} - {message.text[:50]}...")

            # Route to appropriate module
            if message.intent == MessageIntent.FINANCE and self.finance:
                return await self.finance.handle(message)

            elif message.intent == MessageIntent.OPERATIONS and self.operations:
                return await self.operations.handle(message)

            elif message.intent == MessageIntent.MARKETING and self.marketing:
                return await self.marketing.handle(message)

            elif message.intent == MessageIntent.GENERAL:
                return await self.handle_general(message)

            else:
                return "❓ No entendí tu mensaje. Escribe /help para ver comandos disponibles."

        except Exception as e:
            logger.error(f"❌ Error processing message: {e}", exc_info=True)
            return f"❌ Error: {str(e)}"

    async def handle_general(self, message: Message) -> str:
        """Handle general messages (help, status, etc.)"""
        text = message.text.lower()

        if text in ['/start', '/help']:
            return self.get_help_message()

        elif text in ['/status', '/estado']:
            status = await self.get_status()
            return self.format_status(status)

        else:
            # Conversational fallback: let operations try natural-language handling
            # before returning unknown command.
            if self.operations and not text.strip().startswith("/"):
                return await self.operations.handle(message)
            return "❓ Comando no reconocido. Escribe /help para ver comandos disponibles."

    def get_help_message(self) -> str:
        """Get help message with available commands"""
        return f"""
🏆 *{self.config.get('name', self.tournament_id)}*

*Comandos disponibles:*

💰 *Finanzas:*
  /registrar_pago - Registrar pago de equipo
  /ver_patrocinios - Ver patrocinios
  /estado_presupuesto - Estado del presupuesto

🏃 *Operaciones:*
  /registrar_equipo - Registrar nuevo equipo
  /ver_equipos - Ver equipos registrados
  /calendario - Ver calendario de partidos

📣 *Marketing:*
  /enviar_comunicado - Enviar comunicado
  /estadisticas - Ver estadísticas
  /reporte - Generar reporte

⚙️ *General:*
  /status - Ver estado del torneo
  /help - Ver este mensaje
"""

    async def get_status(self) -> Dict[str, Any]:
        """
        Get current tournament status.

        Returns dictionary with metrics from all modules.
        Used by central bot for dashboard.
        """
        status = {
            'tournament_id': self.tournament_id,
            'name': self.config.get('name', self.tournament_id),
            'started_at': self.started_at.isoformat(),
            'uptime_hours': (datetime.now() - self.started_at).total_seconds() / 3600
        }

        # Get metrics from each module
        if self.finance:
            status['finance'] = await self.finance.get_metrics()

        if self.operations:
            status['operations'] = await self.operations.get_metrics()

        if self.marketing:
            status['marketing'] = await self.marketing.get_metrics()

        return status

    def format_status(self, status: Dict[str, Any]) -> str:
        """Format status dict as readable message"""
        lines = [
            f"🏆 *{status['name']}*",
            "",
            "📊 *Estado actual:*"
        ]

        if 'finance' in status:
            f = status['finance']
            lines.extend([
                "",
                "💰 *Finanzas:*",
                f"  Ingresos: ${f.get('total_income', 0):,.2f}",
                f"  Gastos: ${f.get('total_expenses', 0):,.2f}",
                f"  Utilidad: ${f.get('profit', 0):,.2f}",
                f"  Pagos pendientes: {f.get('pending_payments', 0)}"
            ])

        if 'operations' in status:
            o = status['operations']
            lines.extend([
                "",
                "🏃 *Operaciones:*",
                f"  Equipos: {o.get('teams_registered', 0)}",
                f"  Jugadores: {o.get('players_registered', 0)}",
                f"  Partidos programados: {o.get('matches_scheduled', 0)}"
            ])

        if 'marketing' in status:
            m = status['marketing']
            lines.extend([
                "",
                "📣 *Marketing:*",
                f"  Comunicados enviados: {m.get('announcements_sent', 0)}",
                f"  Alcance redes: {m.get('social_reach', 0):,}"
            ])

        return "\n".join(lines)

    async def shutdown(self):
        """Cleanup resources on shutdown"""
        logger.info(f"🔌 Shutting down tournament bot: {self.tournament_id}")

        if self.finance:
            await self.finance.cleanup()

        if self.operations:
            await self.operations.cleanup()

        if self.marketing:
            await self.marketing.cleanup()

        if self.db:
            await self.db.cleanup()
