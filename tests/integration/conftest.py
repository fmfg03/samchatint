"""Disposable database selection for dossier integration tests."""

import os

import pytest
from sqlalchemy.engine import make_url


@pytest.fixture
def dossier_postgres():
    socket = os.environ.get("SAMCHAT_DOSSIER_TEST_PG_SOCKET")
    if socket:
        assert socket.startswith("/tmp/samchat-dossier-pg-")
        return "postgresql+asyncpg://postgres@/postgres", {
            "host": socket,
            "port": int(os.environ.get("SAMCHAT_DOSSIER_TEST_PG_PORT", "55474")),
            "user": "postgres",
            "database": "postgres",
        }

    isolated_ci_dsn = os.environ.get("TEST_AMEX_DATABASE_URL")
    if os.environ.get("GITHUB_ACTIONS") == "true" and isolated_ci_dsn:
        url = make_url(isolated_ci_dsn)
        if (
            url.drivername == "postgresql+asyncpg"
            and url.host == "127.0.0.1"
            and url.port == 55471
            and url.database == "devnous_test"
            and url.username == "test_user"
        ):
            return url, {
                "host": url.host,
                "port": url.port,
                "user": url.username,
                "password": url.password,
                "database": url.database,
            }
    pytest.skip("Requires the disposable dossier socket or isolated CI database.")
