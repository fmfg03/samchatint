"""Technical browser fixture; fake identity/data, never Chatty E2E acceptance."""

import os
from types import SimpleNamespace

from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

from devnous.gastos.routes import batisenal_pilot_routes as routes

app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key="synthetic-browser-fixture-only")
origin = os.environ["BATISENAL_BROWSER_ORIGIN"]
state = {"mode": "stored"}


class Pilot:
    enabled = True
    pilot = SimpleNamespace(
        case_id="00000000-0000-0000-0000-000000000001", source_kind="synthetic_stub"
    )
    flow = SimpleNamespace(
        config=SimpleNamespace(
            issuer=origin + routes.PREFIX, resource=origin + routes.PREFIX + "/mcp"
        )
    )

    def gate(self):
        pass


async def fixture_data(pilot, request):
    mode = state["mode"]
    if mode == "denied":
        raise routes.owner.BatisenalError("RESOURCE_UNAVAILABLE", 403)
    if mode == "error":
        raise RuntimeError("synthetic internal failure must not render")
    detail = dict(
        title="Consulta sintética de prueba",
        description='Contexto revisado <img src="https://invalid.example/secret">',
        context={"módulo": "Informe sintético"},
        can_support=False,
        private_owner_view=True,
        pilot_permission=(
            "Revocada; el agente ya no tiene acceso"
            if mode == "ended"
            else "Vigente dentro de la ventana del piloto"
        ),
    )
    drafts = (
        []
        if mode in ("pending", "ended")
        else [
            dict(
                body="Propuesta sintética <script>window.BAD=true</script>",
                source_kind="synthetic_stub",
                stale=mode == "stale",
                operation_id="operacion-sintetica-001",
            )
        ]
    )
    return detail, drafts


routes.case_data = fixture_data
routes.mount_pilot(app, Pilot())


@app.get("/_test/health")
async def health():
    return {"fixture": "technical-only"}


@app.post("/_test/state/{mode}")
async def change(mode: str):
    assert mode in {"stored", "pending", "stale", "ended", "denied", "error"}
    state["mode"] = mode
    return state
