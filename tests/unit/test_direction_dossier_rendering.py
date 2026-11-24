"""Render verified sources, captured facts, and pending associations by scope."""

import re
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from devnous.gastos.routes import direction_dossier_routes as routes

PATH = "/direccion/expediente-vivo"


@pytest.fixture
def dossier_client(monkeypatch):
    tournament_id = str(uuid4())
    employee = SimpleNamespace(id=str(uuid4()))

    async def guard(_session, _employee):
        return ["portfolio"]

    async def source_access(_session, _employee):
        return {"finance": True}

    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="dossier-rendering-tests")
    app.include_router(
        routes.create_dossier_router(guard, source_access, lambda _: False)
    )
    app.dependency_overrides[routes.get_db_session] = object
    app.dependency_overrides[routes.get_current_empleado] = lambda: employee
    monkeypatch.setenv("SESSION_SECRET_KEY", "dossier-rendering-previews")
    monkeypatch.setattr(
        routes,
        "_authorized_tournaments",
        AsyncMock(return_value=[{"id": tournament_id, "name": "Copa de prueba"}]),
    )
    monkeypatch.setattr(routes, "known_editions", AsyncMock(return_value=[2026]))
    monkeypatch.setattr(routes, "load_dossier_registry", AsyncMock(return_value={}))
    monkeypatch.setattr(
        routes,
        "read_expense_lines",
        AsyncMock(return_value={"status": "empty", "rows": []}),
    )
    return TestClient(app, follow_redirects=False), tournament_id


def _record(
    kind,
    domain="operations",
    *,
    phase="estatal",
    entity="Morelos",
    category="Juvenil",
    reference="ACTA-18",
    **fields,
):
    return {
        "id": str(uuid4()),
        "kind": kind,
        "domain": domain,
        "phase": phase,
        "entity": entity,
        "category": category,
        "evidence_reference": reference,
        "updated_at": "2026-11-18T10:30:00Z",
        "revision": 3,
        "fields": fields,
    }


def _articles(html):
    return re.findall(r"<article\b.*?</article>", html, flags=re.DOTALL)


def _article(html, marker):
    return next(article for article in _articles(html) if marker in article)


def _cell(article, label):
    return re.search(rf"<dt>{re.escape(label)}</dt><dd>(.*?)</dd>", article).group(1)


def _stub_page(monkeypatch, *, records, sources, permissions):
    monkeypatch.setattr(
        routes,
        "load_dossier_sources",
        AsyncMock(return_value=sources),
    )
    monkeypatch.setattr(
        routes,
        "read_records",
        AsyncMock(return_value={"status": "available", "rows": records}),
    )
    monkeypatch.setattr(
        routes, "read_domains", AsyncMock(return_value=permissions.copy())
    )
    monkeypatch.setattr(
        routes, "capture_permissions", AsyncMock(return_value=permissions.copy())
    )


def test_dossier_renders_roster_age_and_unique_counts_with_phase_scoped_targets(
    dossier_client, monkeypatch
):
    client, tid = dossier_client
    matched, captured, missing = (
        _record(
            "team_target",
            reference="META-CANONICA",
            gender="femenil",
            expected_teams=10,
            actual_teams=4,
        ),
        _record(
            "team_target",
            reference="META-CAPTURADA",
            gender="varonil",
            expected_teams=5,
            actual_teams=2,
        ),
        _record(
            "team_target", reference="META-PENDIENTE", gender="mixto", expected_teams=0
        ),
    )
    national = _record(
        "national_event",
        phase="nacional",
        entity="",
        city="Puebla",
        start_date="2026-11-01",
        end_date="2026-11-02",
        duration_days=2,
    )
    result = _record("team_result", team_name="Equipo de prueba", advanced=False)
    sources = {
        "entities": ["Morelos"],
        "queried_at": "2026-11-18T12:36:00Z",
        "registration": {
            "source": "postgres_registration",
            "status": "available",
            "age_cutoff": "2026-12-31",
            "gender_basis": "Rama declarada",
            "summary": {"registrations": 29, "unique_people_identified": 23},
            "rows": [
                {
                    "entity": "Morelos",
                    "category": "Juvenil",
                    "gender": "femenil",
                    "phase": "estatal",
                    "teams": 0,
                    "registrations": 0,
                    "unique_people_identified": 0,
                    "ages": [],
                },
                {
                    "entity": "Morelos",
                    "category": "Juvenil",
                    "gender": "varonil",
                    "teams": 16,
                    "registrations": 11,
                    "ages": [
                        {"age": 15, "registrations": 9},
                        {"age": None, "registrations": 2},
                    ],
                },
            ],
        },
        "operations": {
            "source": "tournament_soul_snapshot",
            "status": "available",
            "edition_verified": True,
            "rows": [
                {
                    "label": "Partido de Puebla",
                    "entities": ["Puebla"],
                    "phase": "estatal",
                },
                {
                    "label": "Partido de Morelos",
                    "entities": ["Morelos"],
                    "phase": "estatal",
                },
            ],
        },
    }
    _stub_page(
        monkeypatch,
        records=[matched, captured, missing, national, result],
        sources=sources,
        permissions={"operations"},
    )
    page = client.get(PATH, params={"tournament_id": tid, "edition_year": 2026})
    assert page.status_code == 200
    assert "Totales del padrón para la edición" in page.text
    assert "Consultado 2026-11-18T12:36:00Z" in page.text
    assert "2026-12-31" in page.text and "Edad sin dato: 2" in page.text
    match = _article(page.text, "META-CANONICA")
    assert _cell(match, "Equipos reales en esta fase") == "0"
    assert _cell(match, "Avance de la meta") == "0.0 %"
    assert _cell(match, "Fuente del conteo") == "postgres_registration"
    attested = _article(page.text, "META-CAPTURADA")
    assert _cell(attested, "Equipos reales en esta fase") == "2"
    assert _cell(attested, "Avance de la meta") == "40.0 %"
    assert _cell(attested, "Fuente del conteo") == "Captura en SamChat"
    absent = _article(page.text, "META-PENDIENTE")
    assert _cell(absent, "Equipos reales en esta fase") == "Sin dato"
    assert _cell(absent, "Avance de la meta") == "Sin dato"
    assert (
        _cell(_article(page.text, "Calendario y sede nacional"), "Duración (días)")
        == "2"
    )
    result_view = _article(page.text, "Equipo de prueba")
    assert 'name="expected_revision" value="3"' in result_view
    assert '<option value="false" selected>No</option>' in result_view
    assert "Sin captura" in page.text and "Entrega de uniformes" in page.text
    filtered = client.get(PATH, params={"entity": "Morelos", "phase": "estatal"})
    assert filtered.status_code == 200
    assert (
        "Partido de Morelos" in filtered.text
        and "Partido de Puebla" not in filtered.text
    )
    assert "Edad sin dato: 2" not in filtered.text


def test_dossier_finance_associations_expose_context_without_relabeling_documents(
    dossier_client, monkeypatch
):
    client, tid = dossier_client
    valid_id, stale_id, missing_id, unassigned_id = (str(uuid4()) for _ in range(4))
    visit = _record("visit", responsible="AZ", visit_date="2027-01-09")
    linked = _record(
        "financial_link",
        "finance",
        category="",
        concept="gasto_visita",
        document_id=valid_id,
        visit_id=visit["id"],
        reference="VINCULO-VISITA",
    )
    stale = _record(
        "financial_link",
        "finance",
        concept="uniformes",
        document_id=stale_id,
        reference="VINCULO-POR-CONCILIAR",
    )
    missing = _record(
        "financial_link",
        "finance",
        concept="balones",
        document_id=missing_id,
        reference="VINCULO-SIN-FUENTE",
    )
    unassigned = _record(
        "financial_link",
        "finance",
        concept="ayuda_operador",
        document_id=unassigned_id,
        reference="VINCULO-FUERA-DE-EDICION",
    )
    sources = {
        "entities": ["Morelos"],
        "categories": ["Juvenil", "Abierta"],
        "finance": {
            "source": "payment_run",
            "status": "available",
            "rows": [
                {
                    "id": valid_id,
                    "label": "Informe 19",
                    "reference": "INF-019",
                    "source": "payment_run",
                    "status": "aprobado",
                    "edition_year": 2026,
                    "phase": "estatal",
                    "categories": ["Juvenil", "Abierta"],
                    "expense_dates": ["2027-01-09"],
                    "expenses_by_currency": [
                        {"currency": "MXN", "expenses": 2, "amount": "1250.00"},
                        {"currency": "USD", "expenses": 1, "amount": "0.00"},
                        {"currency": None, "expenses": 1, "amount": None},
                    ],
                },
                {
                    "id": stale_id,
                    "label": "Solicitud en fase nacional",
                    "edition_year": 2026,
                    "phase": "nacional",
                    "category": "Juvenil",
                    "amount_requested": "88.00",
                    "amount_paid": None,
                    "proof_available": False,
                },
                {
                    "id": unassigned_id,
                    "label": "Solicitud sin edición con fase incompatible",
                    "edition_year": None,
                    "phase_label": "Final sin confirmar",
                    "amount_requested": "9999.93",
                },
            ],
        },
    }
    _stub_page(
        monkeypatch,
        records=[visit, linked, stale, missing, unassigned],
        sources=sources,
        permissions={"operations", "finance"},
    )
    page = client.get(PATH, params={"tournament_id": tid, "edition_year": 2026})
    assert page.status_code == 200
    visit_detail = _article(page.text, "VINCULO-VISITA")
    assert _cell(visit_detail, "Fechas de los gastos") == "2027-01-09"
    assert "1250.00 MXN · 2 partidas" in visit_detail
    assert "0.00 USD · 1 partida" in visit_detail
    assert "Sin importe Moneda sin dato · 1 partida" in visit_detail
    assert "Sin vínculo" in page.text and "Anticipo de hotel" in page.text
    assert "Vínculo por conciliar" in _article(page.text, "VINCULO-POR-CONCILIAR")
    assert "Fuente no disponible" in _article(page.text, "VINCULO-SIN-FUENTE")
    assert "Vínculo por conciliar" in _article(page.text, "VINCULO-FUERA-DE-EDICION")
    native = _article(page.text, "<h4>Solicitud en fase nacional</h4>")
    assert _cell(native, "Importe solicitado") == "88.00"
    assert _cell(native, "Importe pagado") == "Sin dato"
    assert _cell(native, "Comprobante de pago vigente") == "No"
    assert "9999.93" not in page.text
    assert "Solicitud sin edición con fase incompatible" not in page.text
    assert f'<option value="{valid_id}">INF-019</option>' in page.text

    category = client.get(PATH, params={"edition_year": 2026, "category": "Abierta"})
    assert category.status_code == 200
    assert "VINCULO-VISITA" in category.text
    assert "VINCULO-POR-CONCILIAR" not in category.text


@pytest.mark.parametrize("matched", [True, False])
def test_dossier_photo_preview_requires_scope_evidence_and_only_links_protected_route(
    dossier_client, monkeypatch, matched
):
    client, tid = dossier_client
    candidate_id = str(uuid4())
    activation = _record(
        "activation",
        "marketing",
        phase="nacional",
        entity="",
        activity="Clínica deportiva",
    )
    photo = _record(
        "activation_photo",
        "media",
        phase="nacional",
        entity="",
        photo_id=candidate_id,
        activation_id=activation["id"],
    )
    _stub_page(
        monkeypatch,
        records=[activation, photo],
        sources={
            "categories": ["Juvenil", "Abierta"],
            "marketing": {
                "source": "SOUL · Galería",
                "status": "needs_link",
                "candidates": [
                    {"id": candidate_id, "label": "Evento", "category": "Juvenil"}
                ],
            },
        },
        permissions={"marketing", "media"},
    )
    page = client.get(PATH, params={"edition_year": 2026})
    assert page.status_code == 200
    assert f'<img src="{PATH}/fotografias/{photo["id"]}?' in page.text
    assert "Clínica deportiva" in page.text
    form = {
        "csrf": re.search(r'name="csrf" value="([^"]+)"', page.text).group(1),
        "tournament_id": tid,
        "edition_year": "2026",
        "kind": "activation_photo",
        "intent": "preview",
        "phase": "nacional",
        "entity": "",
        "category": "Juvenil" if matched else "Abierta",
        "field_photo_id": candidate_id,
        "field_activation_id": activation["id"],
        "evidence_reference": "Evidencia de activación",
    }
    preview = client.post(f"{PATH}/captura", data=form)
    if matched:
        assert preview.status_code == 200
        assert "Evidencia de activación" in preview.text
        assert "Confirmar y guardar" in preview.text
    else:
        assert preview.status_code == 422
        assert "esta edición y categoría" in preview.json()["detail"]


def test_attributed_expense_appears_once_without_whole_invoice_amount(
    dossier_client, monkeypatch
):
    client, tid = dossier_client
    parent, expense_id = str(uuid4()), str(uuid4())
    capture = _record(
        "expense_line_link",
        "finance",
        phase="nacional",
        entity="",
        expense_id=expense_id,
        source_document_id=parent,
        concept="hotel_liquidacion",
        service_type="Hospedaje",
    )
    _stub_page(
        monkeypatch,
        records=[capture],
        sources={
            "categories": ["Juvenil"],
            "finance": {
                "source": "Gastos",
                "status": "available",
                "rows": [
                    {
                        "id": parent,
                        "label": "Informe de hotel",
                        "kind": "INFORME",
                        "amount_document": "22450.00",
                        "edition_year": 2026,
                    }
                ],
            },
        },
        permissions={"operations", "finance"},
    )
    reader = AsyncMock(
        return_value={
            "status": "available",
            "rows": [
                {
                    "id": expense_id,
                    "document_id": parent,
                    "expense_reference": "PARTIDA-045",
                    "reference": "INF-425",
                    "amount": "100.25",
                    "currency": "MXN",
                    "expense_date": "2027-01-09",
                    "updated_at": "2027-01-10T10:30:00Z",
                    "source": "Gastos · Partidas de informes",
                    "expense_phase": "nacional",
                    "document_phase": "nacional",
                    "expense_categories": ["Juvenil"],
                    "document_categories": ["Juvenil"],
                },
            ],
        }
    )
    monkeypatch.setattr(routes, "read_expense_lines", reader)
    response = client.get(PATH, params={"tournament_id": tid, "edition_year": 2026})
    assert response.status_code == 200
    assert "Liquidación de hotel" in response.text
    assert "Hospedaje" in response.text
    assert response.text.count("<dd>100.25</dd>") == 1
    assert "22450.00" not in response.text
    assert "<dd>2027-01-09</dd>" in response.text
    assert "<dd>PARTIDA-045</dd>" in response.text
    assert reader.await_args.kwargs["expense_ids"] == (expense_id,)
    # The title is not painted as a second record with the invoice's total.
    assert "<h4>Informe de hotel</h4>" not in response.text


def test_visit_report_link_and_evidenced_operator_transfer_stage(
    dossier_client, monkeypatch
):
    client, tid = dossier_client
    report_id, first_id, later_id = (str(uuid4()) for _ in range(3))
    visit = _record(
        "visit", responsible="AZ", visit_date="2026-11-18", report_document_id=report_id
    )
    first = _record(
        "financial_link", "finance", document_id=first_id, concept="ayuda_operador"
    )
    later = _record(
        "financial_link", "finance", document_id=later_id, concept="ayuda_operador"
    )

    def transfer(document_id, reference, effective_payment_date):
        return {
            "id": document_id,
            "label": reference,
            "reference": reference,
            "kind": "SOLICITUD",
            "phase": "estatal",
            "edition_year": 2026,
            "category": "Juvenil",
            "payment_method": "Transferencia",
            "proof_available": True,
            "effective_payment_date": effective_payment_date,
        }

    _stub_page(
        monkeypatch,
        records=[visit, first, later],
        sources={
            "finance": {
                "source": "Gastos",
                "status": "available",
                "rows": [
                    {
                        "id": report_id,
                        "label": "Informe de la visita",
                        "kind": "INFORME",
                        "phase": "estatal",
                        "reference": "INF-VISITA",
                        "edition_year": 2026,
                    },
                    transfer(later_id, "AYUDA-2", "2027-01-01"),
                    transfer(first_id, "AYUDA-1", "2026-10-01"),
                ],
            },
        },
        permissions={"operations", "finance"},
    )
    result = client.get(PATH, params={"tournament_id": tid, "edition_year": 2026})
    assert result.status_code == 200
    assert f'<a href="/documentos/{report_id}">Ver informe en Gastos</a>' in result.text
    assert (
        _cell(
            _article(result.text, "Ver informe en Gastos"),
            "Referencia del informe en Gastos",
        )
        == "INF-VISITA"
    )
    assert "Primera fecha vinculada con transferencia acreditada" in _article(
        result.text, "<dd>AYUDA-1</dd>"
    )
    assert "Transferencia posterior acreditada" in _article(
        result.text, "<dd>AYUDA-2</dd>"
    )

    monkeypatch.setattr(routes, "read_domains", AsyncMock(return_value={"operations"}))
    monkeypatch.setattr(
        routes,
        "read_records",
        AsyncMock(return_value={"status": "available", "rows": [visit]}),
    )
    page = client.get(PATH, params={"tournament_id": tid, "edition_year": 2026})
    assert page.status_code == 200
    assert "Ver informe en Gastos" not in page.text
    assert f"/documentos/{report_id}" not in page.text
