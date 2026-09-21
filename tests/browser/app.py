"""Isolated FastAPI app for browser-level UX tests.

This module is test-only. It deliberately does not import or modify the production
application object. The session login route exists only inside this isolated app.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

from fastapi import FastAPI, Request
from starlette.middleware.sessions import SessionMiddleware

from devnous.gastos.routes import client_executive_routes, dependencies


EMPLOYEE_ID = UUID("10000000-0000-0000-0000-000000000354")


class _EmptySession:
    async def execute(self, *_args, **_kwargs):
        return []


async def _db_session():
    yield _EmptySession()


async def _load_employee(_session, employee_id):
    if employee_id != EMPLOYEE_ID:
        return None
    return SimpleNamespace(
        id=EMPLOYEE_ID,
        nombre="Browser UX Direction",
        correo="browser-ux@example.invalid",
        rol="coordinador",
        activo=True,
        departamento="direccion",
        permissions=set(),
    )


async def _visible_tools(_session, _employee):
    return {"direccion.tableros_ejecutivos"}


async def _can_access_path(_session, _employee, _path, _method="GET"):
    return True


async def _direction_decision(_session, _employee, _tool, _action_key):
    return None


async def _direction_portfolios(_session, _employee_id, *, is_superadmin):
    del is_superadmin
    if str(_employee_id) != str(EMPLOYEE_ID):
        return []
    return ["portfolio-browser-ux"]


async def _dashboard(*_args, **kwargs):
    edition_year = int(kwargs.get("edition_year") or 2026)
    card = {
        "tournament_id": "browser-ux-tournament",
        "tournament_name": "Torneo Browser UX",
        "as_of": "2026-09-21",
        "budget": 1_250_000,
        "actual": 630_000,
        "committed": 780_000,
        "paid": 510_000,
        "projected": 1_120_000,
        "requested": 920_000,
        "pending_to_pay": 270_000,
        "budget_source_status": "available",
        "alerts": [
            {
                "severity": "high",
                "title": "Partida con presión presupuestal",
            }
        ],
        "budget_breakdowns": {
            "by_concept": [
                {
                    "label": "Hospedaje y alimentación",
                    "budget_total": 420_000,
                    "actual_total": 265_000,
                    "committed_total": 310_000,
                },
                {
                    "label": "Transportación terrestre y aérea",
                    "budget_total": 330_000,
                    "actual_total": 190_000,
                    "committed_total": 220_000,
                },
            ],
            "by_account": [
                {
                    "label": "5300-001-001 · Operación del torneo",
                    "budget_total": 750_000,
                },
                {
                    "label": "5300-010-002 · Servicios logísticos",
                    "budget_total": 500_000,
                },
            ],
        },
        "dossier": {
            "source_status": "available",
            "source_bridge": "authorized_uuid",
            "entities": [
                {
                    "entity_name": "Asociación Deportiva Browser UX",
                    "readiness": {"status": "usable"},
                    "operations": {
                        "ps_owner": "Coordinación Plataforma Sports",
                        "summary": {"teams_count": 3, "players_count": 54},
                        "entity_contacts": [
                            {
                                "name": "Responsable de prueba",
                                "phone": "555-0100",
                                "email": "responsable@example.invalid",
                            }
                        ],
                        "real_teams_by_category_gender": [
                            {
                                "category": "Juvenil",
                                "gender_or_branch": "Varonil",
                                "teams_count": 2,
                                "players_count": 36,
                                "team_names": [
                                    "Equipo con nombre representativo A",
                                    "Equipo con nombre representativo B",
                                ],
                            }
                        ],
                        "players_by_category_age_gender": [
                            {
                                "category": "Juvenil",
                                "gender_or_branch": "Varonil",
                                "age": "15-16",
                                "players_count": 36,
                            }
                        ],
                        "pending_fields": [
                            "Confirmar documentación de dos participantes."
                        ],
                    },
                    "finance": {
                        "pending_fields": [
                            "Conciliar anticipo pendiente contra comprobación."
                        ]
                    },
                }
            ],
            "national_phase": {
                "status": "with_data",
                "matches": [
                    {
                        "phase": "Semifinal",
                        "match_date": "2026-10-12",
                        "field_number": "Cancha principal",
                        "status": "programado",
                    }
                ],
            },
            "marketing": {
                "status": "available",
                "media": {
                    "photos_count": 128,
                    "videos_count": 9,
                    "streams_count": 2,
                },
            },
        },
    }
    return {
        "edition_year": edition_year,
        "scope": "portfolio",
        "cards": [card],
        "data_boundary": {
            "operations": "browser_fixture",
            "budget": "browser_fixture",
            "entity_finance": "browser_fixture",
            "writes": False,
        },
        "unavailable_metrics": ["cashflow", "accounts_receivable", "payments"],
    }


# Patch only module references used by this isolated test app.
dependencies._load_empleado_proxy_by_id = _load_employee
dependencies.visible_tools_for = _visible_tools
dependencies.can_access_path = _can_access_path
client_executive_routes.explicit_tool_decision = _direction_decision
client_executive_routes.authorized_direction_portfolio_ids = _direction_portfolios
client_executive_routes.build_client_dashboard = _dashboard


app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key="browser-ux-test-session")
app.dependency_overrides[dependencies.get_db_session] = _db_session
app.include_router(client_executive_routes.router)


@app.get("/_test/health")
async def health():
    return {"ok": True}


@app.get("/_test/login")
async def login(request: Request):
    request.session["empleado_id"] = str(EMPLOYEE_ID)
    return {"ok": True}
