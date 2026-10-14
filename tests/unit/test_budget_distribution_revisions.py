"""Scheduling regressions use fictitious identities and no external sources."""

from datetime import date, timedelta
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from openpyxl import load_workbook

from samchat.budgets import distribution as dist, service
from devnous.gastos.routes.admin_budget_ui import render_budget_partida_matrix


def line(**overrides):
    return {
        "id": "fiction-line",
        "budget_amount": "520.00",
        "concept_name": "Material ficticio",
        "line_direction": "expense",
        **overrides,
    }


@pytest.mark.parametrize(
    "plan,status",
    [
        ({}, "missing"),
        ({1: {"budget_expense_amount": "519.99"}}, "mismatch"),
        ({1: {"budget_expense_amount": "520.00"}}, "complete"),
        ({53: {"budget_expense_amount": "520.00"}}, "invalid"),
        ({1: {"budget_expense_amount": "NaN"}}, "invalid"),
        ({1: {"budget_expense_amount": "-1"}}, "invalid"),
        ({1: {"budget_expense_amount": "520.001"}}, "invalid"),
    ],
)
def test_distribution_states(plan, status):
    assert dist.schedule_state(line(), plan)["status"] == status


def test_missing_zero_authorized_is_not_approved_zero_distribution():
    assert dist.schedule_state(line(budget_amount="0"), {})["status"] == "missing"
    assert (
        dist.schedule_state(
            line(budget_amount="0"), {1: {"budget_expense_amount": "0"}}
        )["status"]
        == "complete"
    )


@pytest.mark.parametrize("has_income", [False, True])
@pytest.mark.asyncio
async def test_ar_reads_original_links_and_matches_even_without_income_lines(
    monkeypatch, has_income
):
    from samchat.ar import service as ar

    income = {
        "id": "new-line",
        "budget_version_id": "revision",
        "concept_name": "Ingreso ficticio",
        "budget_amount": 100,
        "metadata": {
            "distribution_financial_version_id": "original",
            "distribution_financial_line_id": "old-line",
        },
    }
    monkeypatch.setattr(
        ar, "list_budget_lines", AsyncMock(return_value=[income] if has_income else [])
    )
    monkeypatch.setattr(ar, "list_monthly_plan_for_lines", AsyncMock(return_value={}))
    links = AsyncMock(return_value=[])
    candidates = AsyncMock(return_value=[])
    matches = AsyncMock(return_value=[])
    monkeypatch.setattr(ar, "list_budget_cfdi_income_links", links)
    monkeypatch.setattr(ar, "list_psp_cfdi_income_candidates", candidates)
    monkeypatch.setattr(ar, "list_ar_collection_matches", matches)
    monkeypatch.setattr(
        dist,
        "revision_context",
        AsyncMock(return_value=version("revision", "approved", source=dist.SOURCE)),
    )
    monkeypatch.setattr(
        dist,
        "financial_context",
        AsyncMock(return_value=("original", {"old-line": "new-line"})),
    )
    result = await ar.build_ar_read_model(
        object(), budget_version_id="revision", ensure_schema=False
    )
    assert all(
        call.kwargs["budget_version_id"] == "original" for call in links.await_args_list
    )
    assert candidates.await_args.kwargs["budget_version_id"] == "original"
    assert matches.await_args.kwargs["budget_version_id"] == "original"
    if has_income:
        assert result["expected_income"][0]["ar_item_id"] == "expected:old-line"


@pytest.mark.asyncio
async def test_cfdi_revision_follows_validated_root_and_rejects_foreign_tournament(
    monkeypatch,
):
    from devnous.gastos.services import cfdi_income_bridge_service as bridge

    clone = {
        "id": "new-line",
        "budget_version_id": "revision",
        "version_source": dist.SOURCE,
        "metadata": {"distribution_financial_line_id": "old-line"},
        "line_direction": "income",
        "tournament_id": "t1",
        "budget_concept_id": "c",
        "phase": "Fase",
        "edition_year": 2026,
    }
    original = {
        **clone,
        "id": "old-line",
        "budget_version_id": "original",
        "version_source": "ui_manual",
        "metadata": {},
    }

    class Result:
        def __init__(self, row):
            self.row = row

        def mappings(self):
            return self

        def first(self):
            return self.row

    async def execute(_statement, args):
        return Result(clone if args["budget_line_id"] == "new-line" else original)

    session = SimpleNamespace(execute=execute)
    monkeypatch.setattr(
        dist,
        "revision_context",
        AsyncMock(return_value=version("revision", "approved", source=dist.SOURCE)),
    )
    monkeypatch.setattr(
        dist,
        "financial_context",
        AsyncMock(return_value=("original", {"old-line": "new-line"})),
    )
    assert (await bridge._load_budget_line(session, "new-line"))["id"] == "old-line"
    original["tournament_id"] = "foreign"
    with pytest.raises(bridge.CFDIIncomeBridgeError, match="no corresponde"):
        await bridge._load_budget_line(session, "new-line")


def test_distribution_revision_ui_reports_active_state_and_preserves_permissions():
    from devnous.gastos.routes.admin_budget_routes import render_distribution_action

    active = version("revision", "approved", source=dist.SOURCE)
    html = render_distribution_action(active, {"create": True, "line_update": True})
    assert "Distribución aprobada en uso" in html and "hasta entonces" not in html
    assert 'name="confirmed"' in html
    denied = render_distribution_action(active, {"create": False, "line_update": True})
    assert "<form" not in denied


@pytest.mark.parametrize(
    "status,source,is_current,text",
    [
        ("draft", dist.SOURCE, False, "Completa las semanas"),
        ("submitted", dist.SOURCE, False, "enviada para aprobación"),
        ("closed", dist.SOURCE, False, "Revisión cerrada"),
        ("approved", dist.SOURCE, False, "Versión histórica"),
        ("draft", "ui_manual", False, "La carga de importes no distribuye semanas"),
    ],
)
def test_distribution_status_messages_do_not_claim_inactive_versions_are_current(
    status, source, is_current, text
):
    from devnous.gastos.routes.admin_budget_routes import render_distribution_action

    html = render_distribution_action(
        version("fiction", status, source=source),
        {"create": True, "line_update": True},
        is_current=is_current,
    )
    assert text in html and "<form" not in html


@pytest.mark.asyncio
async def test_distribution_revision_cannot_create_or_reimport_lines(monkeypatch):
    monkeypatch.setattr(service, "ensure_budget_schema", AsyncMock())
    monkeypatch.setattr(
        service,
        "get_budget_version",
        AsyncMock(return_value=version("revision", "draft", source=dist.SOURCE)),
    )
    session = SimpleNamespace(execute=AsyncMock())
    with pytest.raises(ValueError, match="conserva las partidas"):
        await service.create_budget_line(
            session,
            version_id="revision",
            concept_name="Nueva partida ficticia",
            budget_amount=1,
            ensure_schema=False,
        )
    with pytest.raises(ValueError, match="no admite reimportar"):
        await service.import_budget_lines_upload(
            session,
            version_id="revision",
            actor_empleado_id="fiction-actor",
            filename="fiction.csv",
            file_bytes=b"",
        )
    session.execute.assert_not_called()


@pytest.mark.asyncio
async def test_distribution_route_requires_authority_confirmation_and_actor(
    monkeypatch,
):
    from uuid import UUID
    from fastapi import HTTPException
    from devnous.gastos.routes import admin_routes

    source = UUID("10000000-0000-4000-8000-000000000001")
    actor = UUID("10000000-0000-4000-8000-000000000002")
    route = next(
        r
        for r in admin_routes.router.routes
        if getattr(r, "path", None)
        == "/admin/presupuestos/versiones/{version_id}/distribucion"
    )
    create = AsyncMock(return_value={"id": "new-fiction-version", "edition_year": 2026})
    monkeypatch.setattr(dist, "create_revision", create)
    session = SimpleNamespace(rollback=AsyncMock())
    with pytest.raises(HTTPException) as error:
        await route.endpoint(
            version_id=source,
            confirmed=True,
            session=session,
            current_empleado=SimpleNamespace(rol="empleado", id=actor),
        )
    assert error.value.status_code == 403
    create.assert_not_called()
    response = await route.endpoint(
        version_id=source,
        confirmed=False,
        session=session,
        current_empleado=SimpleNamespace(rol="superadmin", id=actor),
    )
    assert response.status_code == 303 and "error_msg" in response.headers["location"]
    create.assert_not_called()
    response = await route.endpoint(
        version_id=source,
        confirmed=True,
        session=session,
        current_empleado=SimpleNamespace(rol="superadmin", id=actor),
    )
    assert (
        response.status_code == 303
        and "new-fiction-version" in response.headers["location"]
    )
    create.assert_awaited_once_with(
        session, source_version_id=str(source), actor_empleado_id=str(actor)
    )


@pytest.mark.parametrize("year", [2024, 2025, 2026, 2029])
def test_weeks_cover_every_day_once_including_week_52(year):
    days = []
    for week in range(1, 53):
        start, end = dist.week_bounds(year, week)
        days.extend(start + timedelta(days=i) for i in range((end - start).days + 1))
    assert len(days) == len(set(days)) == (date(year + 1, 1, 1) - date(year, 1, 1)).days
    assert days[0] == date(year, 1, 1) and days[-1] == date(year, 12, 31)


def test_month_projection_conciles_and_missing_remains_unknown():
    data = dist.period_projection(
        [line(), line(id="other", budget_amount="0")],
        {
            "fiction-line": {
                1: {"budget_expense_amount": "200"},
                6: {"budget_expense_amount": "320"},
            }
        },
        2026,
        date(2026, 2, 28),
    )
    assert data["rows"][0]["budget_month"] == "320"
    assert data["rows"][0]["budget_ytd"] == "520"
    assert data["rows"][0]["monthly"]["1"] == "200"
    assert data["rows"][1]["budget_month"] is None
    assert data["status"] == "pending"


def version(id, status, **extra):
    return {
        "id": id,
        "status": status,
        "edition_year": 2026,
        "source": "ui_manual",
        **extra,
    }


def test_only_explicit_same_edition_authorized_successor_overrides_frozen():
    original = version("original", "frozen")
    arbitrary = version("arbitrary", "approved", updated_at="2026-10-20")
    revision = version(
        "revision",
        "draft",
        source=dist.SOURCE,
        distribution_source_version_id="original",
    )
    assert dist.select_version([arbitrary, revision, original])["id"] == "original"
    assert (
        dist.select_version([original, {**revision, "status": "approved"}])["id"]
        == "revision"
    )
    assert (
        dist.select_version(
            [original, {**revision, "status": "approved", "edition_year": 2025}]
        )["id"]
        == "original"
    )
    with pytest.raises(ValueError, match="Más de una"):
        dist.select_version(
            [
                original,
                {**revision, "status": "approved"},
                {**revision, "id": "sibling", "status": "frozen"},
            ]
        )


def test_matrix_revision_has_no_uniform_distribution_or_amount_edit():
    html = render_budget_partida_matrix(
        [line(metadata={"distribution_source_line_id": "fiction-original"})],
        plan_map={},
        actuals_map={},
        version_id="fiction-version",
        tournament_key="fiction-tournament",
        can_edit=True,
        edition_year=2026,
        matrix_mode="expenses",
    )
    assert "readonly" in html
    assert 'class="budget-spread-evenly"' not in html
    assert "Completa" in html or "Distribución pendiente" in html
    assert "Presupuesto gasto: <strong>Pendiente" in html
    assert 'name="month_1_expense" value=""' in html
    assert 'name="save_scope" value="line"' not in html


@pytest.mark.asyncio
async def test_financial_context_rejects_long_cycle(monkeypatch):
    class Result:
        def mappings(self):
            return self

        def all(self):
            return [{"id": "line", "source_line_id": "parent-line"}]

    session = SimpleNamespace(execute=AsyncMock(return_value=Result()))

    async def context(_session, id):
        return version(
            id,
            "approved",
            source=dist.SOURCE,
            distribution_source_version_id={"a": "b", "b": "a"}[id],
        )

    monkeypatch.setattr(dist, "revision_context", context)
    with pytest.raises(ValueError, match="cíclico"):
        await dist.financial_context(session, await context(session, "a"))


@pytest.mark.asyncio
async def test_revision_actuals_preserve_financial_identity_and_remap_display(
    monkeypatch,
):
    monkeypatch.setattr(
        dist,
        "financial_context",
        AsyncMock(return_value=("original", {"old-line": "new-line"})),
    )
    # Real entry point recurses once into the original projection; stub only the
    # source call and preserve its immutable posting/link identities.
    real = service.build_budget_actuals_snapshot
    source = {
        "monthly": {"old-line": {1: {"real_expense_cash": 12}}},
        "movements": [
            {
                "budget_line_id": "old-line",
                "accounting_line_id": "posting-1",
                "movement_key": "accounting:posting-1",
            }
        ],
    }

    async def wrapped(session, **kwargs):
        if kwargs["version_id"] == "original":
            return source
        return await real(session, **kwargs)

    monkeypatch.setattr(service, "build_budget_actuals_snapshot", wrapped)
    result = await wrapped(
        object(),
        edition_year=2026,
        version_id="revision",
        version_context=version("revision", "approved", source=dist.SOURCE),
    )
    assert result["financial_version_id"] == "original"
    assert result["monthly"]["new-line"][1]["real_expense_cash"] == 12
    assert result["movements"][0]["accounting_line_id"] == "posting-1"
    assert result["movements"][0]["financial_budget_line_id"] == "old-line"


def test_monthly_reading_appears_in_html_xlsx_and_pdf_without_template_mapping():
    from samchat.client_executive.home_ui import render_budget_distribution
    from samchat.executive.exporter import (
        generate_direction_report_pdf,
        generate_direction_report_xlsx,
    )
    from test_direction_home import snapshot
    from samchat.client_executive.reports import build_report

    data = snapshot()
    data["tournaments"][0]["budget_distribution"] = dist.period_projection(
        [line()], {}, 2026, date(2026, 10, 31)
    )
    assert "Completar distribución" in render_budget_distribution(data["tournaments"])
    report = build_report(data)
    book = load_workbook(BytesIO(generate_direction_report_xlsx(report)))
    sheet = book["Distribución autorizada"]
    assert sheet["E3"].value == "missing" and sheet["F3"].value == 520
    assert sheet["G3"].value is None and sheet["H3"].value is None
    import fitz

    pdf = fitz.open(stream=generate_direction_report_pdf(report), filetype="pdf")
    assert "Material ficticio" in "".join(page.get_text() for page in pdf)
