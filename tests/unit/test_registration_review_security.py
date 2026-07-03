import os

os.environ.setdefault("SESSION_SECRET_KEY", "test-session-secret")

import pytest

import copa_telmex_dashboard as dashboard


def test_session_secret_missing_fails_fast(monkeypatch):
    monkeypatch.delenv("SESSION_SECRET_KEY", raising=False)

    with pytest.raises(RuntimeError) as exc_info:
        dashboard._require_session_secret_key()

    assert "SESSION_SECRET_KEY" in str(exc_info.value)


def test_production_missing_database_url_fails_fast(monkeypatch):
    monkeypatch.setenv("SAMCHAT_ENV", "production")
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(RuntimeError) as exc_info:
        dashboard._require_database_url_for_runtime()

    assert "DATABASE_URL" in str(exc_info.value)


def test_dev_missing_database_url_keeps_local_fallback(monkeypatch):
    for name in ("SAMCHAT_ENV", "ENVIRONMENT", "APP_ENV", "FASTAPI_ENV"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    db_url = dashboard._require_database_url_for_runtime()

    assert db_url.startswith("postgresql+asyncpg://")
