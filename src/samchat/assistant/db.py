from __future__ import annotations

import os
from typing import Dict

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


_EXPENSES_SESSION_MAKER: async_sessionmaker[AsyncSession] | None = None
_TOURNAMENT_SESSION_MAKERS: Dict[str, async_sessionmaker[AsyncSession]] = {}
_LOCAL_FALLBACK_DB_URL = (
    "postgresql+asyncpg://copa_user:copa_pass_2025@localhost:5432/copa_telmex"
)
_STRICT_RUNTIME_ENVS = {"production", "prod", "staging", "stage"}


def _normalize_db_url(db_url: str) -> str:
    if db_url.startswith("postgresql://") and "+asyncpg" not in db_url:
        return db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return db_url


def _runtime_env() -> str:
    for name in ("SAMCHAT_ENV", "ENVIRONMENT", "APP_ENV", "FASTAPI_ENV"):
        value = (os.getenv(name) or "").strip().lower()
        if value:
            return value
    return "development"


def _require_db_url_or_local_fallback(*, primary_env_key: str) -> str:
    db_url = os.getenv(primary_env_key) or os.getenv("DATABASE_URL")
    if db_url:
        return db_url
    if _runtime_env() in _STRICT_RUNTIME_ENVS:
        raise RuntimeError(
            f"{primary_env_key} or DATABASE_URL must be configured for assistant DB access"
        )
    return _LOCAL_FALLBACK_DB_URL


def get_expenses_session_maker() -> async_sessionmaker[AsyncSession]:
    """DB for financial questions (gastos)."""
    global _EXPENSES_SESSION_MAKER
    if _EXPENSES_SESSION_MAKER is not None:
        return _EXPENSES_SESSION_MAKER

    db_url = _require_db_url_or_local_fallback(primary_env_key="EXPENSES_DATABASE_URL")

    engine = create_async_engine(
        _normalize_db_url(db_url),
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
    )
    _EXPENSES_SESSION_MAKER = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    return _EXPENSES_SESSION_MAKER


def get_tournament_session_maker(
    tournament_key: str,
) -> async_sessionmaker[AsyncSession]:
    """DB for operational questions (tournament registration/rosters)."""
    tournament_key = (tournament_key or "").strip().lower() or "beisbol"
    if tournament_key in _TOURNAMENT_SESSION_MAKERS:
        return _TOURNAMENT_SESSION_MAKERS[tournament_key]

    env_key = f"DATABASE_URL_{tournament_key.upper()}"
    db_url = _require_db_url_or_local_fallback(primary_env_key=env_key)

    engine = create_async_engine(
        _normalize_db_url(db_url),
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
    )
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    _TOURNAMENT_SESSION_MAKERS[tournament_key] = maker
    return maker
