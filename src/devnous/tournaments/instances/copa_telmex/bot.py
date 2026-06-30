"""
Copa Telmex Bot - Tournament bot for Copa Telmex (fútbol).

Shares the same multi-module architecture as other instances (finance, operations,
marketing) and uses the production TelegramAdapter (OCR, gastos commands, assistant).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from devnous.gastos.schema_guard import apply_schema_guard, check_schema_health

from ...core.finance_module import FinanceModule
from ...core.marketing_module import MarketingModule
from ...core.operations_module import OperationsModule
from ...core.telegram_adapter import TelegramAdapter
from ...core.tournament_bot import Message, TournamentBot

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[5]


class CopaTelmexBot(TournamentBot):
    def __init__(
        self,
        config_path: Optional[str] = None,
        telegram_token: Optional[str] = None,
        anthropic_key: Optional[str] = None,
        openai_key: Optional[str] = None,
    ) -> None:
        if not config_path:
            candidate = _REPO_ROOT / "config" / "copa_telmex.yaml"
            config_path = str(candidate) if candidate.exists() else None

        super().__init__(
            tournament_id="copa_telmex",
            config_path=config_path,
        )

        telegram_token = telegram_token or os.getenv("TELEGRAM_BOT_TOKEN")
        anthropic_key = anthropic_key or os.getenv("ANTHROPIC_API_KEY")
        openai_key = openai_key or os.getenv("OPENAI_API_KEY")

        self._setup_database()

        self.finance = FinanceModule(
            tournament_id=self.tournament_id,
            config=self.config,
            db=self.db_session if hasattr(self, "db_session") else None,
        )
        self.operations = OperationsModule(
            tournament_id=self.tournament_id,
            config=self.config,
            db=self.db_session if hasattr(self, "db_session") else None,
            anthropic_key=anthropic_key,
            openai_key=openai_key,
        )
        self.marketing = MarketingModule(
            tournament_id=self.tournament_id,
            config=self.config,
            db=self.db_session if hasattr(self, "db_session") else None,
        )

        self.telegram_adapter = None
        if telegram_token:
            self.telegram_adapter = TelegramAdapter(self, telegram_token)
            logger.info("📱 Telegram adapter initialized")

        logger.info("🏆 Copa Telmex bot initialized")

    def _setup_database(self) -> None:
        db_config = self.config.get("database", {})
        db_url = (
            os.getenv("COPA_TELMEX_DATABASE_URL")
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
                pool_pre_ping=True,
            )
            self.async_session_maker = async_sessionmaker(
                self.db_engine,
                class_=AsyncSession,
                expire_on_commit=False,
            )
            self.db_session = self.async_session_maker
            logger.info("✅ Database connection initialized")
        else:
            logger.warning("⚠️  No database configuration found")
            self.db_engine = None
            self.async_session_maker = None
            self.db_session = None

    async def ensure_schema(self) -> None:
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
        logger.info("Copa Telmex: Processing message from %s", message.chat_id)
        response = await super().process_message(message)
        if isinstance(response, str) and not response.startswith("🏆"):
            response = f"🏆 Copa Telmex\n\n{response}"
        return response

    def get_help_message(self) -> str:
        base_help = super().get_help_message()
        extra = """

📋 *Copa Telmex*
  • Registro de equipos y jugadores vía OCR
  • Consulta `/status` para estado del torneo
  • Usa el menú del bot para comandos de gastos y asistente
"""
        return base_help + extra

    async def get_status(self) -> Dict[str, Any]:
        status = await super().get_status()
        status.update(
            {
                "sport": "futbol",
                "stages": self.config.get("stages", []),
            }
        )
        return status

    async def run_telegram_bot(self) -> None:
        if not self.telegram_adapter:
            raise ValueError(
                "Telegram adapter not initialized. Provide telegram_token in constructor."
            )
        await self.ensure_schema()
        logger.info("🚀 Starting Copa Telmex Telegram bot...")
        try:
            await self.telegram_adapter.run()
        finally:
            await self.cleanup()

    async def cleanup(self) -> None:
        logger.info("🔌 Cleaning up Copa Telmex bot...")
        if self.finance:
            await self.finance.cleanup()
        if self.operations:
            await self.operations.cleanup()
        if self.marketing:
            await self.marketing.cleanup()
        if hasattr(self, "db_engine") and self.db_engine:
            logger.info("🔌 Closing database connections...")
            await self.db_engine.dispose()
            logger.info("✅ Database connections closed")
        logger.info("✅ Copa Telmex bot cleanup complete")


async def create_copa_telmex_bot(
    config_path: Optional[str] = None,
    telegram_token: Optional[str] = None,
    anthropic_key: Optional[str] = None,
    openai_key: Optional[str] = None,
) -> CopaTelmexBot:
    return CopaTelmexBot(
        config_path=config_path,
        telegram_token=telegram_token,
        anthropic_key=anthropic_key,
        openai_key=openai_key,
    )
