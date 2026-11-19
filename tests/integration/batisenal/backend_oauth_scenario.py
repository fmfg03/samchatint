"""Signed sessions/PKCE/JWT and real owners, synthetic SQLite, no real Chatty."""

import base64
import hashlib
import json
import sqlite3
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from backend_harness import IDS
from backend_web_helpers import create, login, payload
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import Request
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, insert, select, text

from devnous.gastos.models import Empleado
from devnous.gastos.services import batisenal_pilot_service as pilot_service
from devnous.gastos.services import batisenal_service as owner
from devnous.gastos.services.batisenal_case_service import CaseService
from samchat.batisenal_pilot import SupportAuthorizationConfig, SupportPilot
from samchat.batisenal_transport.store import Store
from samchat.private_plugin.contracts import Denied
from samchat.private_plugin.oauth_flow import JWTSigner
from samchat.private_plugin.oauth_identity import OAuthResourceConfig, VerificationKey
from samchat.private_plugin.oauth_store import (
    OAuthStore,
    create_local_fixture_schema,
)

VERIFIER = "p" * 43


@pytest.fixture
async def scenario(app, tmp_path):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as web:
        await login(web)
        data = payload()
        data.update(
            title="SIMULACION Operaciones Plataforma Sports",
            description=(
                "No puedo avanzar en la solicitud ficticia del evento; conservo"
                " borrador."
            ),
            context={
                "module": "solicitudes",
                "folio": "SIM-OPS-001",
                "error": "desconocido",
            },
        )
        case_id = await create(web, data)
        other_case = await create(web)
        async with app.state.engine.begin() as connection:
            if getattr(app.state, "native_sync_url", None):
                raw = await connection.get_raw_connection()
                for migration in (
                    "20260919_agent_action_receipts_v01.sql",
                    "20261001_private_oauth_storage_review.sql",
                    "20261024_batisenal_pilot.sql",
                ):
                    sql = (Path("database/migrations") / migration).read_text()
                    await raw.driver_connection.execute(
                        sql.replace("BEGIN;", "").replace("COMMIT;", "")
                    )
            else:
                for table in (
                    pilot_service.fixtures,
                    pilot_service.pilots,
                    pilot_service.receipts,
                    pilot_service.consents,
                ):
                    await connection.run_sync(
                        lambda conn, table=table: table.create(conn)
                    )
            await connection.execute(
                insert(pilot_service.fixtures).values(
                    ticket_id=UUID(case_id),
                    attestation_sha256="0" * 64,
                    active=True,
                )
            )
            await connection.execute(
                text("UPDATE access_profiles SET permissions=:p WHERE id='support'"),
                {
                    "p": json.dumps(
                        ["batisenal:acme:demo:support", "batisenal:acme:demo:agent"]
                    )
                },
            )
        if getattr(app.state, "native_sync_url", None):
            engine = create_engine(
                app.state.native_sync_url,
                connect_args={"options": "-c search_path=" + app.state.native_schema},
            )
        else:
            engine = create_engine("sqlite:///" + str(tmp_path / "synthetic.db"))
            create_local_fixture_schema(engine)
        clock = [1900000000]
        http_origin = getattr(app.state, "pilot_http_origin", None)
        issuer = (
            http_origin + "/soporte/batisenal/piloto"
            if http_origin
            else "https://samchat.example.test/oauth"
        )
        config = SupportAuthorizationConfig(
            issuer,
            issuer + "/mcp" if http_origin else "https://samchat.example.test/mcp",
            "synthetic-client",
            "https://chat.example.test/return",
            frozenset({"batisenal.support"}),
        )
        oauth = OAuthStore(
            engine,
            issuer=config.issuer,
            installation_id="synthetic-installation",
            organization_id="acme",
            support_only=True,
        )
        key = ec.generate_private_key(ec.SECP256R1())  # ephemeral synthetic test key
        resource = OAuthResourceConfig(
            config.issuer,
            config.resource,
            "synthetic-installation",
            config.scopes,
            frozenset({config.client_id}),
            {"synthetic-key": VerificationKey("ES256", key.public_key())},
        )
        store_path = tmp_path / "audit.sqlite"
        with sqlite3.connect(store_path) as db:
            db.executescript(
                Path(
                    "database/migrations/20261023_batisenal_transport_sqlite.sql"
                ).read_text()
            )
        audit = Store(store_path, lambda: clock[0])
        pilot = pilot_service.Pilot(
            case_id=case_id,
            organization="acme",
            project="demo",
            issuer=config.issuer,
            installation_id=resource.installation_id,
            clock=lambda: clock[0],
        )

        async def employee(request, session):
            identifier = request.session.get("empleado_id")
            if not identifier:
                raise Denied("UNAUTHENTICATED")
            row = (
                (
                    await session.execute(
                        select(
                            Empleado.id,
                            Empleado.activo,
                            Empleado.rol,
                            Empleado.nombre,
                            Empleado.correo,
                            Empleado.departamento,
                        ).where(Empleado.id == UUID(identifier))
                    )
                )
                .mappings()
                .one()
            )
            return SimpleNamespace(**dict(row, id=str(row.id)))

        composition = SupportPilot(
            sessions=app.state.maker,
            employee_dependency=employee,
            authorization=config,
            resource=resource,
            oauth_store=oauth,
            identity_records_store=OAuthStore(
                engine.execution_options(postgresql_readonly=True),
                issuer=config.issuer,
                installation_id="synthetic-installation",
                organization_id="acme",
                support_only=True,
            ),
            case_service=CaseService(
                sessions=app.state.maker,
                issuer=config.issuer,
                audience=config.resource,
                client_id=config.client_id,
                enabled=True,
            ),
            signer=JWTSigner("synthetic-key", key),
            audit=audit,
            pilot=pilot,
            clock=lambda: clock[0],
            enabled=True,
        )

        @app.get("/fixture/browser")
        async def browser(request: Request):
            request.session["empleado_id"] = str(IDS[request.session["fixture_user"]])
            return {"synthetic": True}

        @app.post("/fixture/consent/{operation}")
        async def consent(operation: str, request: Request):
            try:
                result = await composition.browser_call(request, operation)
                return asdict(result) if not isinstance(result, dict) else result
            except (Denied, owner.BatisenalError) as error:
                return JSONResponse({"error": str(error)}, status_code=403)

        @app.post("/fixture/token")
        async def token(request: Request):
            try:
                return asdict(await composition.exchange(await request.json()))
            except Denied as error:
                return JSONResponse({"error": str(error)}, status_code=401)

        app.mount("/support-agent", composition.mcp)
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Accept": "application/json"},
        ) as client:
            yield SimpleNamespace(
                app=app,
                web=web,
                client=client,
                composition=composition,
                clock=clock,
                case_id=case_id,
                other_case=other_case,
                config=config,
                engine=engine,
                audit=audit,
                key=key,
                data=data,
            )
        engine.dispose()


async def browser_login(s, who="support"):
    await login(s.web, who)
    await s.web.get("/fixture/browser")


def parameters(s):
    return list(
        dict(
            response_type="code",
            client_id=s.config.client_id,
            redirect_uri=s.config.redirect_uri,
            resource=s.config.resource,
            scope="batisenal.support",
            state="synthetic-state-123456789",
            code_challenge=base64.urlsafe_b64encode(
                hashlib.sha256(VERIFIER.encode()).digest()
            )
            .rstrip(b"=")
            .decode(),
            code_challenge_method="S256",
        ).items()
    )
