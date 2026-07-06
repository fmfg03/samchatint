import pytest

import samchat.assistant.db as assistant_db


def _reset_sessionmaker_cache() -> None:
    assistant_db._EXPENSES_SESSION_MAKER = None
    assistant_db._TOURNAMENT_SESSION_MAKERS.clear()


def test_production_expenses_db_requires_configured_url(monkeypatch):
    _reset_sessionmaker_cache()
    monkeypatch.setenv("SAMCHAT_ENV", "production")
    monkeypatch.delenv("EXPENSES_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(RuntimeError) as exc_info:
        assistant_db.get_expenses_session_maker()

    assert "EXPENSES_DATABASE_URL" in str(exc_info.value)
    assert "DATABASE_URL" in str(exc_info.value)


def test_production_tournament_db_requires_configured_url(monkeypatch):
    _reset_sessionmaker_cache()
    monkeypatch.setenv("SAMCHAT_ENV", "production")
    monkeypatch.delenv("DATABASE_URL_BEISBOL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(RuntimeError) as exc_info:
        assistant_db.get_tournament_session_maker("beisbol")

    assert "DATABASE_URL_BEISBOL" in str(exc_info.value)
    assert "DATABASE_URL" in str(exc_info.value)


def test_dev_expenses_db_keeps_local_fallback(monkeypatch):
    _reset_sessionmaker_cache()
    for name in ("SAMCHAT_ENV", "ENVIRONMENT", "APP_ENV", "FASTAPI_ENV"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("EXPENSES_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    observed = {}

    def _fake_create_async_engine(url, **kwargs):
        observed["url"] = url
        observed["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(assistant_db, "create_async_engine", _fake_create_async_engine)

    assistant_db.get_expenses_session_maker()

    assert observed["url"].startswith("postgresql+asyncpg://")
