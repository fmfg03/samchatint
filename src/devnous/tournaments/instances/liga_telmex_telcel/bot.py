"""
Liga Telmex Telcel Bot - Tournament bot instance for Liga Telmex Telcel 2026.

This bot manages the baseball tournament with categories for 13 and 14 years old (male).
Tournament stages: Convenio, Fase Colectiva, Fase Estatal, Fase Nacional (Sep 19-26), Viaje de Campeones.
"""

import asyncio
import logging
import os
from pathlib import Path
from typing import Optional, Dict, Any
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from devnous.gastos.schema_guard import apply_schema_guard, check_schema_health

from ...core.tournament_bot import TournamentBot, Message
from ...core.finance_module import FinanceModule
from ...core.operations_module import OperationsModule
from ...core.marketing_module import MarketingModule
from ...core.telegram_adapter import TelegramAdapter

logger = logging.getLogger(__name__)


class LigaTelmexTelcelBot(TournamentBot):
    """
    Liga Telmex Telcel tournament bot for baseball.

    Categories:
    - 13 años varonil
    - 14 años varonil

    Stages:
    - Firma de Convenio (15 Ene - 28 Feb 2026)
    - Fase Colectiva/Inscripción (1 May - 2 Ago 2026)
    - Fase Estatal (17 Ago - 3 Sep 2026)
    - Fase Nacional (19-26 Sep 2026)
    - Viaje de Campeones (Ene 2027)
    """

    def __init__(
        self,
        config_path: Optional[str] = None,
        telegram_token: Optional[str] = None,
        anthropic_key: Optional[str] = None,
        openai_key: Optional[str] = None,
    ):
        """
        Initialize Liga Telmex Telcel bot.

        Args:
            config_path: Path to YAML config (optional)
            telegram_token: Telegram bot token (optional, from env if not provided)
            anthropic_key: Anthropic API key for OCR (optional, from env if not provided)
            openai_key: OpenAI API key for OCR (optional, from env if not provided)
        """
        # Default config path
        if not config_path:
            config_path = str(
                Path(__file__).parent.parent.parent / "config" / "liga_telmex_telcel.yaml"
            )

        # Initialize base tournament bot
        super().__init__(
            tournament_id="liga_telmex_telcel",
            config_path=config_path
        )

        # Get API keys from environment if not provided
        telegram_token = telegram_token or os.getenv('TELEGRAM_BOT_TOKEN')
        anthropic_key = anthropic_key or os.getenv("ANTHROPIC_API_KEY")
        openai_key = openai_key or os.getenv("OPENAI_API_KEY")

        # Initialize database connection
        self._setup_database()

        # Initialize modules
        self.finance = FinanceModule(
            tournament_id=self.tournament_id,
            config=self.config,
            db=self.db_session if hasattr(self, 'db_session') else None
        )

        self.operations = OperationsModule(
            tournament_id=self.tournament_id,
            config=self.config,
            db=self.db_session if hasattr(self, 'db_session') else None,
            anthropic_key=anthropic_key,
            openai_key=openai_key,
        )

        self.marketing = MarketingModule(
            tournament_id=self.tournament_id,
            config=self.config,
            db=self.db_session if hasattr(self, 'db_session') else None
        )

        # Initialize Telegram adapter if token provided
        self.telegram_adapter = None
        if telegram_token:
            self.telegram_adapter = TelegramAdapter(self, telegram_token)
            logger.info("📱 Telegram adapter initialized")

        logger.info("⚾ Liga Telmex Telcel bot initialized")

    def _setup_database(self):
        """Setup PostgreSQL database connection"""
        db_config = self.config.get('database', {})
        db_url = (
            os.getenv("LIGA_TELMEX_TELCEL_DATABASE_URL")
            or os.getenv("TOURNAMENT_DATABASE_URL")
            or os.getenv("EXPENSES_DATABASE_URL")
            or os.getenv("DATABASE_URL")
            or os.getenv("POSTGRESQL_URL")
        )

        if db_url and db_url.startswith("postgresql://") and "+asyncpg" not in db_url:
            db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
            logger.info("✅ Database URL loaded from environment")
        elif db_config:
            db_url = (
                f"postgresql+asyncpg://{db_config['user']}:{db_config['password']}"
                f"@{db_config['host']}:{db_config['port']}/{db_config['name']}"
            )
            logger.info("✅ Database URL built from tournament config")

        if db_url:
            self.db_engine = create_async_engine(
                db_url,
                pool_size=5,
                max_overflow=10,
                pool_pre_ping=True
            )

            self.async_session_maker = async_sessionmaker(
                self.db_engine,
                class_=AsyncSession,
                expire_on_commit=False
            )

            # Create a session property that modules can use
            self.db_session = self.async_session_maker

            logger.info("✅ Database connection initialized")
        else:
            logger.warning("⚠️  No database configuration found")
            self.db_engine = None
            self.async_session_maker = None
            self.db_session = None

    async def ensure_schema(self) -> None:
        """
        Ensure DB schema is compatible with current models (idempotent).

        This repo does not use migrations for Liga Telmex Telcel DB, so we apply minimal
        ALTER TABLE statements at startup.
        """
        if not getattr(self, "db_engine", None):
            return

        async with self.db_engine.begin() as conn:
            guard_report = await apply_schema_guard(conn, logger=logger, strict=False)
            health_report = await check_schema_health(conn)
        if guard_report.get("failed_count"):
            logger.warning("⚠️ Schema guard had failures: %s", guard_report.get("failed"))
        if not health_report.get("ok"):
            logger.warning("⚠️ Schema health still has gaps: %s", health_report)

    async def process_message(self, message: Message) -> str:
        """
        Process message with Liga Telmex Telcel-specific customizations.

        You can override this to add custom logic before/after
        standard processing.
        """
        # Log to Liga Telmex Telcel analytics
        logger.info(f"⚾ Liga Telmex Telcel: Processing message from {message.chat_id}")

        # Call parent implementation
        response = await super().process_message(message)

        # Add Liga Telmex Telcel branding to response (if it's a string)
        if isinstance(response, str) and not response.startswith("⚾"):
            response = f"⚾ Liga Telmex Telcel\n\n{response}"

        return response

    def get_help_message(self) -> str:
        """Get help message with available commands for Liga Telmex Telcel"""
        base_help = super().get_help_message()

        liga_specific = f"""

📋 *Etapas del Torneo:*
  1️⃣ Firma de Convenio (15 Ene - 28 Feb 2026)
  2️⃣ Fase Colectiva/Inscripción (1 May - 2 Ago 2026)
  3️⃣ Fase Estatal (17 Ago - 3 Sep 2026)
  4️⃣ Fase Nacional (19-26 Sep 2026)
  5️⃣ Viaje de Campeones (Enero 2027)

🏆 *Categorías:*
  • 13 años varonil
  • 14 años varonil

⚾ *Información del Torneo:*
  • Deporte: Béisbol
  • Formato: Eliminación directa
  • Duración: 7 innings
  • Regla de misericordia: 10 carreras después del 5to inning
"""

        return base_help + liga_specific

    async def get_status(self) -> Dict[str, Any]:
        """
        Get current tournament status with Liga Telmex Telcel specifics.

        Returns dictionary with metrics from all modules plus tournament stages.
        """
        status = await super().get_status()

        # Add Liga Telmex Telcel specific information
        status.update({
            'sport': 'beisbol',
            'categories': ['13 años varonil', '14 años varonil'],
            'stages': self.config.get('stages', []),
            'current_stage': self._get_current_stage()
        })

        return status

    def _get_current_stage(self) -> Optional[Dict[str, Any]]:
        """Determine current tournament stage based on date"""
        from datetime import datetime
        today = datetime.now().date()

        stages = self.config.get('stages', [])
        for stage in stages:
            start_date = datetime.strptime(stage['start_date'], '%Y-%m-%d').date()
            end_date = datetime.strptime(stage['end_date'], '%Y-%m-%d').date()

            if start_date <= today <= end_date:
                return stage

        return None

    def format_status(self, status: Dict[str, Any]) -> str:
        """Format status dict as readable message with Liga Telmex Telcel branding"""
        base_status = super().format_status(status)

        # Add Liga Telmex Telcel specific information
        lines = [base_status]

        if 'current_stage' in status and status['current_stage']:
            stage = status['current_stage']
            lines.extend([
                "",
                "📅 *Etapa Actual:*",
                f"  {stage.get('name', 'N/A')}",
                f"  {stage.get('description', '')}",
                f"  Periodo: {stage.get('start_date', '')} al {stage.get('end_date', '')}"
            ])

        if 'categories' in status:
            lines.extend([
                "",
                "🏆 *Categorías:*"
            ])
            for category in status['categories']:
                lines.append(f"  • {category}")

        return "\n".join(lines)

    async def run_telegram_bot(self):
        """Run the Telegram bot"""
        if not self.telegram_adapter:
            raise ValueError("Telegram adapter not initialized. Provide telegram_token in constructor.")

        await self.ensure_schema()
        logger.info("🚀 Starting Liga Telmex Telcel Telegram bot...")
        try:
            await self.telegram_adapter.run()
        finally:
            await self.cleanup()

    async def cleanup(self):
        """Cleanup resources"""
        logger.info("🔌 Cleaning up Liga Telmex Telcel bot...")

        # Cleanup modules
        if self.finance:
            await self.finance.cleanup()
        if self.operations:
            await self.operations.cleanup()
        if self.marketing:
            await self.marketing.cleanup()

        # Close database connections
        if hasattr(self, 'db_engine') and self.db_engine:
            logger.info("🔌 Closing database connections...")
            await self.db_engine.dispose()
            logger.info("✅ Database connections closed")

        logger.info("✅ Liga Telmex Telcel bot cleanup complete")


# Convenience function to create and run the bot
async def create_liga_telmex_telcel_bot(
    config_path: Optional[str] = None,
    telegram_token: Optional[str] = None,
    anthropic_key: Optional[str] = None,
    openai_key: Optional[str] = None,
) -> LigaTelmexTelcelBot:
    """
    Create Liga Telmex Telcel bot instance.

    Args:
        config_path: Optional path to config file
        telegram_token: Optional Telegram bot token
        anthropic_key: Optional Anthropic API key
        openai_key: Optional OpenAI API key

    Returns:
        LigaTelmexTelcelBot instance
    """
    bot = LigaTelmexTelcelBot(
        config_path=config_path,
        telegram_token=telegram_token,
        anthropic_key=anthropic_key,
        openai_key=openai_key,
    )
    return bot


# Example usage
if __name__ == "__main__":
    import asyncio

    async def main():
        # Create bot with Telegram integration
        bot = await create_liga_telmex_telcel_bot()

        if bot.telegram_adapter:
            # Run as Telegram bot
            logger.info("="*60)
            logger.info("⚾ Liga Telmex Telcel 2026 - Telegram Bot with OCR")
            logger.info("="*60)
            logger.info("")
            logger.info("🏆 Categorías: 13 y 14 años varonil")
            logger.info("📅 Fase Nacional: 19-26 de septiembre 2026")
            logger.info("✅ Validación de datos mexicanos")
            logger.info("💡 Sugerencias inteligentes")
            logger.info("👤 Verificación humana intuitiva")
            logger.info("📊 Confianza >95%")
            logger.info("")

            await bot.run_telegram_bot()
        else:
            # Test in console mode
            logger.info("Running in test mode (no Telegram)")

            test_message = Message(
                text="/status",
                chat_id=123456789,
                user_id=987654321
            )

            response = await bot.process_message(test_message)
            print(response)

            # Get status
            status = await bot.get_status()
            print("\nStatus:", status)

            await bot.cleanup()

    asyncio.run(main())