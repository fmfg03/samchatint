"""Browser verification of the dossier routes with an isolated source fixture."""

from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from playwright.sync_api import Page
from starlette.middleware.sessions import SessionMiddleware

from devnous.gastos.routes import direction_dossier_routes as dossier

ROOT = Path(__file__).resolve().parents[2]
ORIGIN = "http://direction.test"
TOURNAMENT = "7f655ac3-4407-4292-9afc-63c3d8503218"
EMPLOYEE = "fba5e21f-71dd-4c41-8fc6-b08393209df6"


def _make_browser_app(monkeypatch):
    records = []

    async def releases(_session, _tournament_id, **_permissions):
        return [2027, 2026]

    async def assigned(_session, _employee_id, **_kwargs):
        return [{"id": TOURNAMENT, "name": "Torneo de la prueba"}]

    async def guard(_session, _employee):
        return ["cartera"]

    async def source_access(_session, _employee):
        return {"finance": True}

    async def domains(_session, _employee):
        return {"operations", "marketing", "finance"}

    async def permissions(_session, _employee, action="editar"):
        return {"operations"} if action == "editar" else set()

    async def read_records(_session, *, edition_year, allowed_domains, **_scope):
        return {
            "status": "available",
            "rows": [
                row
                for year, row in records
                if year == edition_year and row["domain"] in allowed_domains
            ],
        }

    async def save(_session, **capture):
        records.append(
            (
                capture["edition_year"],
                {
                    **capture["scope"],
                    "id": str(uuid4()),
                    "revision": 1,
                    "kind": capture["kind"],
                    "domain": "operations",
                    "fields": capture["fields"],
                    "evidence_reference": capture["evidence_reference"],
                    "updated_at": "2027-01-18T14:20:00Z",
                },
            )
        )

    async def sources(_session, *, edition_year, **_scope):
        team_row = {
            "label": "Padrón de la edición",
            "entity": "Jalisco",
            "category": "Juvenil",
            "gender": "femenil",
            "teams": 4,
            "registrations": 25,
            "unique_people_identified": 24,
            "ages": [
                {"age": 15, "registrations": 22},
                {"age": None, "registrations": 3},
            ],
        }
        national = {
            "label": "Partido nacional",
            "category": "Juvenil",
            "phase": "nacional",
            "home_score": 0,
            "away_score": 3,
        }
        return {
            "entities": ["Jalisco", "Puebla"],
            "categories": ["Juvenil"],
            "registration": {
                "source": "postgres_registration",
                "status": "available",
                "age_cutoff": f"{edition_year}-12-31",
                "rows": [team_row] if edition_year == 2026 else [],
            },
            "operations": {
                "source": "tournament_soul_snapshot",
                "status": "available",
                "rows": [national] if edition_year == 2026 else [],
            },
            "finance": {
                "source": "payment_run",
                "status": "available",
                "rows": (
                    [
                        {
                            "label": "INFORME-018",
                            "status": "aprobado",
                            "edition_year": 2026,
                            "expense_dates": ["2027-01-18"],
                            "expenses_by_currency": [
                                {"currency": "MXN", "expenses": 2, "amount": "1200.00"},
                                {"currency": "USD", "expenses": 1, "amount": "100.00"},
                            ],
                        }
                    ]
                    if edition_year == 2026
                    else []
                ),
            },
            "marketing": {
                "source": "SOUL · Galería de torneos",
                "status": "denied",
                "rows": [],
            },
        }

    class Session:
        async def commit(self):
            return None

    monkeypatch.setenv("SESSION_SECRET_KEY", "isolated-dossier-browser-fixture")
    monkeypatch.setattr(dossier, "known_editions", releases)
    monkeypatch.setattr(dossier, "_authorized_tournaments", assigned)
    monkeypatch.setattr(dossier, "read_domains", domains)
    monkeypatch.setattr(dossier, "capture_permissions", permissions)
    monkeypatch.setattr(dossier, "read_records", read_records)
    monkeypatch.setattr(dossier, "save_record", save)
    monkeypatch.setattr(dossier, "load_dossier_sources", sources)
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="isolated-dossier-session-fixture")
    app.include_router(
        dossier.create_dossier_router(guard, source_access, lambda _: False)
    )
    app.dependency_overrides[dossier.get_db_session] = Session
    app.dependency_overrides[dossier.get_current_empleado] = lambda: SimpleNamespace(
        id=EMPLOYEE
    )
    return app, records


def test_dossier_filters_search_and_capture_confirmation(page: Page, monkeypatch):
    app, records = _make_browser_app(monkeypatch)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))

    with TestClient(app, base_url=ORIGIN) as client:

        def serve(route):
            req = route.request
            if req.url.endswith("/static/direction_dossier.css"):
                route.fulfill(
                    content_type="text/css",
                    body=(ROOT / "static/direction_dossier.css").read_text(),
                )
                return
            result = client.request(
                req.method,
                req.url,
                content=req.post_data_buffer,
                headers={
                    key: value
                    for key, value in req.headers.items()
                    if key == "content-type"
                },
            )
            route.fulfill(
                status=result.status_code,
                headers=dict(result.headers),
                body=result.content,
            )

        page.route(f"{ORIGIN}/**", serve)
        page.goto(f"{ORIGIN}{dossier.DOSSIER_PATH}?edition_year=2026")
        assert page.get_by_role("heading", name="Expediente Vivo").is_visible()
        assert page.get_by_text("Edad sin dato: 3", exact=False).is_visible()
        assert page.get_by_role("heading", name="Fase nacional").is_visible()
        assert page.get_by_text("Goles local").is_visible()
        assert page.get_by_text("Fechas de los gastos").is_visible()
        assert page.get_by_text("2027-01-18", exact=True).is_visible()
        assert page.get_by_text("1200.00 MXN · 2 partidas", exact=False).is_visible()
        assert page.get_by_text("100.00 USD · 1 partida", exact=False).is_visible()

        search = page.locator("#buscar")
        search.fill("jalisco")
        assert page.get_by_role("heading", name="Padrón de la edición").is_visible()
        assert not page.get_by_role("heading", name="Partido nacional").is_visible()
        search.fill("palabra ausente")
        assert page.locator("#sin-resultados").is_visible()
        search.fill("")

        filters = page.get_by_role("region", name="Filtrar expediente")
        filters.get_by_label("Fase").select_option("nacional")
        with page.expect_navigation():
            filters.get_by_role("button", name="Aplicar filtros").click()
        assert page.get_by_role("heading", name="Partido nacional").is_visible()
        assert not page.get_by_role("heading", name="Padrón de la edición").is_visible()

        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.goto(f"{ORIGIN}{dossier.DOSSIER_PATH}?edition_year=2027")
        assert not page.get_by_role("heading", name="Partido nacional").is_visible()
        capture = page.get_by_role("region", name="Capturar o vincular datos")
        capture.locator("summary").get_by_text(
            "Participación de la entidad", exact=True
        ).click()
        form = capture.locator("details[open] form")
        form.get_by_label("Entidad").fill("Zacatecas")
        form.get_by_label("Evidencia de participación").fill("Acta de operación")
        form.get_by_label("Referencia de respaldo").fill("ACTA-2027-01")
        with page.expect_navigation():
            form.get_by_role("button", name="Revisar captura").click()
        assert page.get_by_role("heading", name="Revisar captura").is_visible()
        assert page.get_by_text("ACTA-2027-01", exact=True).is_visible()
        assert not records
        with page.expect_navigation():
            page.get_by_role("button", name="Confirmar y guardar").click()
        assert (
            page.get_by_role("status").filter(has_text="Registro guardado").is_visible()
        )
        assert page.get_by_role("heading", name="Zacatecas").is_visible()
        assert len(records) == 1 and records[0][0] == 2027
        assert not errors
