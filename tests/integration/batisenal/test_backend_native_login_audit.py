"""Real bcrypt/native login with PostgreSQL audit success and rollback failure."""

import bcrypt
import pytest
from backend_harness import IDS
from backend_native_fixtures import app as app_fixture
from sqlalchemy import text
from starlette.requests import Request

from devnous.gastos.routes import auth_routes
from devnous.gastos.services.customer_success_audit import (
    ensure_customer_success_audit_schema,
)

app = app_fixture


@pytest.mark.parametrize("failure", [None, "missing_table", "insert_rejected"])
async def test_native_login_audit_failure_clears_authority(app, failure):
    secret = "synthetic-only-login-proof"
    async with app.state.maker() as session:
        await session.execute(
            text(
                "UPDATE empleados SET correo='alice@synthetic.invalid',"
                " password_hash=:hash WHERE id=:actor"
            ),
            {
                "hash": bcrypt.hashpw(secret.encode(), bcrypt.gensalt()).decode(),
                "actor": IDS["alice"],
            },
        )
        correo = await session.scalar(
            text("SELECT correo FROM empleados WHERE id=:actor"),
            {"actor": IDS["alice"]},
        )
        await session.execute(
            text(
                "CREATE TABLE auth_logs (correo text, success boolean, "
                "created_at timestamp without time zone)"
            )
        )
        await ensure_customer_success_audit_schema(session)
        if failure == "missing_table":
            await session.execute(text("DROP TABLE customer_success_audit_events"))
        elif failure == "insert_rejected":
            await session.execute(
                text(
                    "ALTER TABLE customer_success_audit_events ADD CONSTRAINT"
                    " test_reject CHECK (action <> 'auth.login.success')"
                )
            )
        await session.commit()
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/login",
            "headers": [],
            "client": ("127.0.0.1", 3211),
            "session": {},
        }
    )
    async with app.state.maker() as session:
        session.info["batisenal_preprovisioned_audit"] = app.state.native_schema
        result = await auth_routes.login(
            request, correo=correo, password=secret, next="/panel", session=session
        )
        assert result.status_code == 303
        if failure:
            assert request.session == {}
            assert "invalid_credentials" in result.headers["location"]
            assert not session.in_transaction()
        else:
            assert request.session["empleado_id"] == str(IDS["alice"])
            assert result.headers["location"] == "/panel"
    if failure != "missing_table":
        async with app.state.maker() as session:
            assert await session.scalar(
                text(
                    "SELECT COUNT(*) FROM customer_success_audit_events WHERE"
                    " action='auth.login.success'"
                )
            ) == int(failure is None)
