"""Isolated FastAPI app for browser-level UX tests.

This module is test-only. It deliberately does not import or modify the production
application object. The session login route exists only inside this isolated app.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from starlette.middleware.sessions import SessionMiddleware

from devnous.gastos.routes import admin_routes, client_executive_routes, dependencies, user_routes


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




async def _panel_visible_tools(_session, employee):
    return set(getattr(employee, "visible_tool_keys", set()) or set())


async def _panel_can_review_pending_approvals(_session, employee):
    return str(getattr(employee, "correo", "") or "").startswith(
        "approver-browser-ux@"
    )


def _panel_is_budget_control_user(employee):
    return str(getattr(employee, "correo", "") or "").startswith(
        "budget-control-browser-ux@"
    )

# Patch only module references used by this isolated test app.
dependencies._load_empleado_proxy_by_id = _load_employee
dependencies.visible_tools_for = _visible_tools
dependencies.can_access_path = _can_access_path
client_executive_routes.explicit_tool_decision = _direction_decision
client_executive_routes.authorized_direction_portfolio_ids = _direction_portfolios
client_executive_routes.build_client_dashboard = _dashboard
user_routes.visible_tools_for = _panel_visible_tools
user_routes._can_review_pending_approvals = _panel_can_review_pending_approvals
user_routes._is_budget_control_user = _panel_is_budget_control_user


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


PROFILE_FIXTURES = {
    "employee": {
        "employee": SimpleNamespace(
            id=UUID("10000000-0000-0000-0000-000000000401"),
            nombre="Empleado Browser UX",
            correo="employee-browser-ux@example.invalid",
            rol="empleado",
            activo=True,
            departamento="Operaciones",
            permissions=set(),
            visible_tool_keys={
                "panel.home",
                "panel.telegram",
                "panel.entrenamiento",
                "gastos.informes",
                "gastos.solicitudes",
                "gastos.mis_gastos",
                "soporte.self",
            },
        ),
        "active": "gastos",
        "workspace": "solicitudes",
        "task": "Solicitar una transferencia a un proveedor.",
    },
    "approver": {
        "employee": SimpleNamespace(
            id=UUID("10000000-0000-0000-0000-000000000402"),
            nombre="Aprobador Browser UX",
            correo="approver-browser-ux@example.invalid",
            rol="coordinador",
            activo=True,
            departamento="Operaciones",
            permissions=set(),
            visible_tool_keys={
                "panel.home",
                "gastos.informes",
                "gastos.solicitudes",
                "soporte.self",
            },
        ),
        "active": "gastos",
        "workspace": "documentos",
        "task": "Atender una solicitud que requiere aprobación.",
    },
    "budget_control": {
        "employee": SimpleNamespace(
            id=UUID("10000000-0000-0000-0000-000000000403"),
            nombre="Control Presupuestal Browser UX",
            correo="budget-control-browser-ux@example.invalid",
            rol="finanzas",
            activo=True,
            departamento="Finanzas",
            permissions=set(),
            visible_tool_keys={
                "panel.home",
                "gastos.solicitudes",
                "admin.finanzas",
                "admin.contabilidad",
                "admin.gastos.dashboard",
                "admin.gastos.limpieza",
                "presupuestos.ingresos",
                "soporte.self",
            },
        ),
        "active": "finanzas",
        "workspace": "documentos",
        "task": "Resolver un documento detenido por asignación presupuestal.",
    },
    "finance": {
        "employee": SimpleNamespace(
            id=UUID("10000000-0000-0000-0000-000000000404"),
            nombre="Finanzas Browser UX",
            correo="finance-browser-ux@example.invalid",
            rol="finanzas",
            activo=True,
            departamento="Finanzas",
            permissions=set(),
            visible_tool_keys={
                "panel.home",
                "admin.gastos.dashboard",
                "admin.gastos.expenses",
                "admin.gastos.invoices",
                "admin.gastos.cfdi_carga",
                "admin.gastos.cfdi_matching",
                "admin.gastos.limpieza",
                "admin.finanzas",
                "admin.contabilidad",
                "presupuestos.ingresos",
                "soporte.self",
            },
        ),
        "active": "finanzas",
        "workspace": None,
        "task": "Preparar el siguiente corte de pagos.",
    },
    "accounting": {
        "employee": SimpleNamespace(
            id=UUID("10000000-0000-0000-0000-000000000405"),
            nombre="Contabilidad Browser UX",
            correo="accounting-browser-ux@example.invalid",
            rol="finanzas",
            activo=True,
            departamento="Contabilidad",
            permissions=set(),
            visible_tool_keys={
                "panel.home",
                "admin.gastos.dashboard",
                "admin.gastos.limpieza",
                "admin.finanzas",
                "admin.contabilidad",
                "presupuestos.ingresos",
                "soporte.self",
            },
        ),
        "active": "contabilidad",
        "workspace": None,
        "task": "Resolver un gasto que no está listo para COI.",
    },
    "direction": {
        "employee": SimpleNamespace(
            id=EMPLOYEE_ID,
            nombre="Dirección Browser UX",
            correo="direction-browser-ux@example.invalid",
            rol="coordinador",
            activo=True,
            departamento="Dirección",
            permissions=set(),
            visible_tool_keys={
                "panel.home",
                "direccion.tableros_ejecutivos",
                "soporte.self",
            },
        ),
        "active": "direccion",
        "workspace": None,
        "task": "Identificar qué requiere atención en Dirección.",
    },
}


@app.get("/_test/profile/{profile_name}", response_class=HTMLResponse)
async def profile_navigation(profile_name: str):
    fixture = PROFILE_FIXTURES.get(profile_name)
    if fixture is None:
        return HTMLResponse("<h1>Perfil no encontrado</h1>", status_code=404)

    employee = fixture["employee"]
    top = user_routes.render_top_navigation(employee, fixture["active"])
    workspace = ""
    if fixture["workspace"] is not None:
        workspace = user_routes._gastos_workspace_nav_html(
            employee, fixture["workspace"]
        )

    admin = ""
    if profile_name in {"budget_control", "finance", "accounting"}:
        admin = admin_routes.render_admin_navigation(employee)

    accounting = ""
    if profile_name == "accounting":
        accounting = user_routes._contabilidad_subnav("estado")

    html = f"""
    <html>
      <head><title>Perfil UX · {profile_name}</title><style>html,body{{margin:0;max-width:100%;}}</style></head>
      <body>
        <main>
          <h1>Perfil simulado: {profile_name}</h1>
          <p data-testid="task-prompt">{fixture["task"]}</p>
          <section aria-label="Navegación global">{top}</section>
          <section aria-label="Navegación de Gastos">{workspace}</section>
          <section aria-label="Navegación administrativa">{admin}</section>
          <section aria-label="Navegación contable">{accounting}</section>
        </main>
      </body>
    </html>
    """
    return HTMLResponse(html)


@app.get("/_test/panel/{profile_name}", response_class=HTMLResponse)
async def profile_panel(profile_name: str, request: Request):
    fixture = PROFILE_FIXTURES.get(profile_name)
    if fixture is None:
        return HTMLResponse("<h1>Perfil no encontrado</h1>", status_code=404)

    html = await user_routes.panel(
        request,
        _EmptySession(),
        fixture["employee"],
    )
    return HTMLResponse(html)
