from __future__ import annotations

import os
from typing import Dict

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


_EXPENSES_SESSION_MAKER: async_sessionmaker[AsyncSession] | None = None
_TOURNAMENT_SESSION_MAKERS: Dict[str, async_sessionmaker[AsyncSession]] = {}


def _normalize_db_url(db_url: str) -> str:
    if db_url.startswith("postgresql://") and "+asyncpg" not in db_url:
        return db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return db_url


def get_expenses_session_maker() -> async_sessionmaker[AsyncSession]:
    """DB for financial questions (gastos)."""
    global _EXPENSES_SESSION_MAKER
    if _EXPENSES_SESSION_MAKER is not None:
        return _EXPENSES_SESSION_MAKER

    db_url = os.getenv("EXPENSES_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not db_url:
        db_url = (
            "postgresql+asyncpg://copa_user:copa_pass_2025@localhost:5432/copa_telmex"
        )

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
    db_url = os.getenv(env_key) or os.getenv("DATABASE_URL")
    if not db_url:
        db_url = (
            "postgresql+asyncpg://copa_user:copa_pass_2025@localhost:5432/copa_telmex"
        )

    engine = create_async_engine(
        _normalize_db_url(db_url),
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
    )
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    _TOURNAMENT_SESSION_MAKERS[tournament_key] = maker
    return maker
