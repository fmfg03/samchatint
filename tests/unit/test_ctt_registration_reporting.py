import re
from datetime import date
from html import unescape
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from devnous.copa_telmex import ctt_registration_report as report_service
from devnous.copa_telmex import ctt_report_access, ctt_report_routes
from devnous.copa_telmex.ctt_report_access import CttEditionAccess


def edition(*, national=False, cutoff=date(2026, 12, 31)):
    return CttEditionAccess(
        tournament_id="72482d48-f983-4c89-959a-29a64f971bbb",
        edition_year=2026,
        roster_slug="copa-telmex-2026",
        age_cutoff=cutoff,
        state_codes=frozenset({""} if national else {"09"}),
        edit_state_codes=frozenset({"09"}),
    )


def synthetic_report():
    return {
        "available": True,
        "edition_year": 2026,
        "as_of_date": "2026-12-31",
        "catalog_source": "INEGI",
        "options": {
            "states": [
                {"value": "09", "label": "Ciudad de México"},
                {"value": "23", "label": "Quintana Roo"},
            ],
            "municipalities": [{"value": "09014", "label": "Benito Juárez"}],
            "categories": [{"value": "juvenil", "label": "Juvenil"}],
        },
        "summary": {"equipos": 1, "jugadores": 2},
        "reports": {
            "femenil_edad": [],
            "juvenil_edades": [],
            "varonil_rangos": [],
            "calidad_datos": [],
            "participacion_general": [
                {
                    "nivel": "municipal",
                    "estado": "Ciudad de México",
                    "municipio": "Benito Juárez",
                    "municipality_code": "09014",
                    "categoria": "Juvenil",
                    "equipos": 1,
                    "jugadores": 2,
                    "curp": "IDENTIFICADOR-PRIVADO",
                }
            ],
            "cobertura_municipal": [
                {
                    "nivel": "estatal",
                    "estado": "Ciudad de México",
                    "municipios_participantes": 1,
                    "municipios_totales": 16,
                    "porcentaje": 6.25,
                }
            ],
        },
    }


@pytest.fixture
def report_client(monkeypatch):
    app = FastAPI()
    app.include_router(
        ctt_report_routes.create_ctt_report_router(
            Jinja2Templates(directory="templates")
        )
    )
    app.dependency_overrides[ctt_report_routes.get_current_empleado] = lambda: (
        SimpleNamespace(id="fixture-actor", rol="coordinador")
    )
    session = object()
    app.dependency_overrides[ctt_report_routes.get_db_session] = lambda: session
    lookup = AsyncMock(return_value=[edition()])
    reader = AsyncMock(side_effect=lambda *_args, **_kw: synthetic_report())
    monkeypatch.setattr(ctt_report_routes, "list_ctt_report_editions", lookup)
    monkeypatch.setattr(ctt_report_routes, "build_ctt_registration_report", reader)
    with TestClient(app) as client:
        yield client, lookup, reader


def test_html_and_xlsx_use_same_scoped_filters_and_hide_private_fields(report_client):
    client, _, reader = report_client
    query = "?edition_year=2026&municipality=09014&category=Juvenil"
    page = client.get(ctt_report_routes.CTT_REPORT_PATH + query)
    download = client.get(ctt_report_routes.CTT_REPORT_DOWNLOAD_PATH + query)

    assert page.status_code == download.status_code == 200
    assert "Benito Juárez" in page.text and "6.25 %" in page.text
    assert "Todos los estados" not in page.text
    assert "Quintana Roo" not in page.text
    assert "IDENTIFICADOR-PRIVADO" not in page.text
    assert reader.await_args_list[0] == reader.await_args_list[1]
    assert reader.await_args.kwargs["state_code"] == "09"
    assert reader.await_args.kwargs["age_cutoff"] == date(2026, 12, 31)
    assert (
        page.headers["Cache-Control"]
        == download.headers["Cache-Control"]
        == ("private, no-store")
    )

    with BytesIO(download.content) as payload:
        workbook = load_workbook(payload)
        assert workbook.sheetnames == [
            "Participación",
            "Cobertura municipal",
            "Femenil",
            "Juvenil",
            "Varonil",
            "Incidencias",
        ]
        coverage = workbook["Cobertura municipal"]
        assert any(
            cell.value == 0.0625 and "0.00%" in cell.number_format
            for row in coverage
            for cell in row
        )
        assert all(
            cell.value != "IDENTIFICADOR-PRIVADO"
            for sheet in workbook
            for row in sheet
            for cell in row
        )


@pytest.mark.parametrize("suffix", ["", ".xlsx"])
def test_out_of_scope_requests_cannot_read_report(report_client, suffix):
    client, _, reader = report_client
    path = ctt_report_routes.CTT_REPORT_PATH + suffix
    assert client.get(path + "?edition_year=2025").status_code == 403
    assert client.get(path + "?state=23").status_code == 403
    assert client.get(path + "?state=09&municipality=23005").status_code == 422
    reader.assert_not_awaited()


def test_report_requires_configured_source_and_cutoff(report_client):
    client, lookup, reader = report_client
    lookup.return_value = None
    page = client.get(ctt_report_routes.CTT_REPORT_PATH)
    assert page.status_code == 503
    assert "aún no está habilitada" in page.text
    lookup.return_value = [edition(cutoff=None)]
    file = client.get(ctt_report_routes.CTT_REPORT_DOWNLOAD_PATH)
    assert file.status_code == 503
    assert "fecha de corte" in file.json()["detail"]
    reader.assert_not_awaited()


def test_download_detects_corrections_after_page_loaded(report_client):
    client, _, reader = report_client
    page = client.get(ctt_report_routes.CTT_REPORT_PATH)
    href = re.search(r'href="([^\"]+\.xlsx\?[^\"]+)"', page.text)
    assert href
    url = unescape(href.group(1))
    assert client.get(url).status_code == 200

    updated = synthetic_report()
    updated["summary"]["jugadores"] += 1
    reader.side_effect = None
    reader.return_value = updated
    assert client.get(url).status_code == 409


@pytest.mark.asyncio
async def test_roster_edit_checks_origin_and_destination(monkeypatch):
    monkeypatch.setattr(
        ctt_report_access,
        "list_ctt_report_editions",
        AsyncMock(return_value=[edition(national=True)]),
    )
    actor = {
        "employee_id": "x",
        "role": "coordinador",
        "roster_slug": "copa-telmex-2026",
    }
    check = ctt_report_access.enforce_ctt_roster_access
    assert await check(None, **actor, state="Quintana Roo")
    assert await check(None, **actor, state="Ciudad de México", write=True)
    for destination in ("Quintana Roo", ""):
        with pytest.raises(HTTPException) as denial:
            await check(
                None,
                **actor,
                state="Ciudad de México",
                write=True,
                target_state=destination,
            )
        assert denial.value.status_code == 403


@pytest.mark.asyncio
async def test_report_counts_homonyms_once_per_state_and_reconciles_ages(monkeypatch):
    def rows(team, state, municipality, category, birth_dates):
        return [
            {
                "team_id": team,
                "state": state,
                "municipality": municipality,
                "category": category,
                "gender": "femenil" if category == "Femenil" else "varonil",
                "birth_date": born,
                "player_count": 1,
            }
            for born in birth_dates
        ]

    monkeypatch.setattr(
        report_service,
        "probe_registration_scope",
        AsyncMock(
            return_value={
                "status": "CONFIGURED",
                "roster_slug": "copa-telmex-2026",
                "tournament_name": "CTT",
            }
        ),
    )
    monkeypatch.setattr(
        report_service,
        "_rows",
        AsyncMock(
            return_value=[
                *rows(
                    "a",
                    "CDMX",
                    "Benito Juárez",
                    "Femenil",
                    ["2009-01-01", "2008-12-31"],
                ),
                *rows("b", "Ciudad de México", "09014", "Juvenil", ["2011-01-01"]),
                *rows(
                    "c",
                    "Quintana Roo",
                    "Benito Juárez",
                    "Varonil",
                    ["1997-01-01", "1996-12-31"],
                ),
                *rows(
                    "d",
                    "Quintana Roo",
                    "Sin identificar",
                    "Varonil",
                    [None, "2027-01-01"],
                ),
            ]
        ),
    )
    report = await report_service.build_ctt_registration_report(
        None,
        tournament_id=edition().tournament_id,
        edition_year=2026,
        age_cutoff=date(2026, 12, 31),
    )
    assert report["summary"] == {
        "equipos": 4,
        "jugadores": 7,
        "estados": 2,
        "municipios_participantes": 2,
    }
    report_rows = report["reports"]
    national_coverage = report_rows["cobertura_municipal"][0]
    assert national_coverage["municipios_participantes"] == 2
    assert national_coverage["municipios_totales"] == 2478
    national_women = report_rows["femenil_edad"][0]
    assert national_women["menores_17"] == national_women["mayores_18"] == 1
    assert report_rows["juvenil_edades"][0]["edad_15"] == 1
    national_men = report_rows["varonil_rangos"][0]
    assert national_men["edad_25_29"] == national_men["edad_30_mas"] == 1
    assert national_men["sin_fecha_nacimiento"] == 1
    assert national_men["fecha_nacimiento_invalida"] == 1
    assert report_rows["calidad_datos"][0]["equipos_sin_municipio_inegi"] == 1
