from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from devnous.gastos.routes import admin_routes
from devnous.gastos.routes.admin_routes import (
    _cleanup_beneficiary_name,
    _cleanup_document_origin,
    _cleanup_fiscal_controls_are_blockers,
    _cleanup_period_bounds,
)
from devnous.gastos.services.expense_accounting_cleanup_service import (
    cleanup_expense_matches_filters,
    cleanup_issues_match_filter,
    normalize_cleanup_document_type,
    normalize_cleanup_issue_type,
)
from devnous.gastos.services import (
    expense_accounting_cleanup_service as cleanup_service,
)


def _expense(*, documento=None, informe=None, solicitud=None):
    return SimpleNamespace(
        documento=documento,
        informe_documento=informe,
        solicitud_documento=solicitud,
    )


def _documento(tipo: str, referencia: str, **overrides):
    values = {
        "tipo": tipo,
        "numero_referencia": referencia,
        "beneficiario_empleado": None,
        "beneficiario_proveedor_cliente": None,
        "proveedor_cliente": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _searchable_expense(**overrides):
    values = {
        "id": "80000000-0000-0000-0000-000000000001",
        "numero_referencia": "O-26000001",
        "concepto": "Hospedaje Mérida",
        "proyecto": "Copa Telmex",
        "cfdi_uuid_manual": "UUID-MANUAL",
        "numero_factura": "FAC-42",
        "empleado": SimpleNamespace(nombre="Ana Pérez"),
        "cfdi_report": SimpleNamespace(
            cfdi_uuid="UUID-VINCULADO",
            emisor_nombre="Hotel Centro",
            emisor_rfc="HCE010101AA1",
        ),
        "documento": None,
        "informe_documento": _documento(
            "INFORME",
            "I-26000012",
            beneficiario_proveedor_cliente=SimpleNamespace(
                nombre="Transportes del Sureste"
            ),
        ),
        "solicitud_documento": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_cleanup_period_uses_the_requested_calendar_month() -> None:
    period, start, end = _cleanup_period_bounds("2026-09")

    assert period == "2026-09"
    assert start == datetime(2026, 9, 1)
    assert end == datetime(2026, 10, 1)


def test_cleanup_period_defaults_safely_when_period_is_invalid() -> None:
    period, start, end = _cleanup_period_bounds(
        "September", now=datetime(2026, 9, 17, 8, 30)
    )

    assert period == "2026-09"
    assert start == datetime(2026, 9, 1)
    assert end == datetime(2026, 10, 1)


def test_cleanup_period_keeps_an_inherited_bi_year_without_a_month() -> None:
    period, start, end = _cleanup_period_bounds(
        None, default_year=2025, now=datetime(2026, 9, 17, 8, 30)
    )

    assert period == "2025-09"
    assert start == datetime(2025, 9, 1)
    assert end == datetime(2025, 10, 1)


def test_cleanup_opens_fiscal_controls_only_when_they_are_blockers() -> None:
    assert _cleanup_fiscal_controls_are_blockers(
        ["Falta cuenta de cargo", "Falta cuenta de IVA"]
    )
    assert _cleanup_fiscal_controls_are_blockers(
        ["Falta cuenta de retención ISR"]
    )
    assert not _cleanup_fiscal_controls_are_blockers(
        ["Falta cuenta de cargo", "Falta CFDI vinculado"]
    )


def test_cleanup_document_origin_uses_explicit_informe_link() -> None:
    label, reference = _cleanup_document_origin(
        _expense(informe=_documento("INFORME", "I-26000012"))
    )

    assert label == "Informe de gastos"
    assert reference == "I-26000012"


def test_cleanup_document_origin_uses_explicit_solicitud_link() -> None:
    label, reference = _cleanup_document_origin(
        _expense(solicitud=_documento("SOLICITUD", "S-26000012"))
    )

    assert label == "Solicitud de transferencia"
    assert reference == "S-26000012"


def test_cleanup_document_origin_does_not_guess_when_links_conflict() -> None:
    label, reference = _cleanup_document_origin(
        _expense(
            informe=_documento("INFORME", "I-26000012"),
            solicitud=_documento("SOLICITUD", "S-26000012"),
        )
    )

    assert label == "Vínculo documental por revisar"
    assert reference == "I-26000012"


def test_cleanup_document_origin_marks_distinct_same_type_links_for_review(
) -> None:
    label, reference = _cleanup_document_origin(
        _expense(
            documento=_documento("INFORME", "I-26000011"),
            informe=_documento("INFORME", "I-26000012"),
        )
    )

    assert label == "Vínculo documental por revisar"
    assert reference == "I-26000012"


def test_cleanup_document_origin_marks_missing_link_explicitly() -> None:
    assert _cleanup_document_origin(_expense()) == (
        "Sin documento vinculado",
        "Sin referencia documental",
    )


def test_cleanup_beneficiary_uses_canonical_document_party() -> None:
    expense = _expense(
        informe=_documento(
            "INFORME",
            "I-26000012",
            beneficiario_empleado=SimpleNamespace(nombre="Ana Beneficiaria"),
        )
    )

    assert _cleanup_beneficiary_name(expense) == "Ana Beneficiaria"


def test_cleanup_beneficiary_fails_closed_when_document_links_conflict() -> None:
    expense = _expense(
        informe=_documento("INFORME", "I-26000012"),
        solicitud=_documento("SOLICITUD", "S-26000012"),
    )

    assert _cleanup_beneficiary_name(expense) == "Beneficiario por revisar"


def test_cleanup_beneficiary_falls_back_to_expense_account() -> None:
    expense = _expense()
    expense.cuenta_gastos = SimpleNamespace(
        beneficiario_empleado=None,
        beneficiario_proveedor_cliente=SimpleNamespace(nombre="Operador Regional"),
        empleado=SimpleNamespace(nombre="Solicitante"),
    )

    assert _cleanup_beneficiary_name(expense) == "Operador Regional"


def test_cleanup_search_is_accent_insensitive_and_uses_loaded_context() -> None:
    expense = _searchable_expense()

    for query in (
        "merida",
        "ana perez",
        "i-26000012",
        "uuid-vinculado",
        "hotel centro",
        "copa telmex",
        "transportes del sureste",
    ):
        assert cleanup_expense_matches_filters(
            expense, search_q=query, document_type="informe"
        )

    assert not cleanup_expense_matches_filters(
        expense, search_q="persona ausente", document_type="all"
    )


def test_cleanup_document_filter_uses_relationships_and_fails_closed() -> None:
    informe = _searchable_expense()
    solicitud = _searchable_expense(
        informe_documento=None,
        solicitud_documento=_documento("SOLICITUD", "S-26000008"),
    )
    conflict = _searchable_expense(
        solicitud_documento=_documento("SOLICITUD", "S-26000008")
    )

    assert cleanup_expense_matches_filters(
        informe, search_q="", document_type="informe"
    )
    assert cleanup_expense_matches_filters(
        solicitud, search_q="", document_type="solicitud"
    )
    assert not cleanup_expense_matches_filters(
        conflict, search_q="", document_type="informe"
    )
    assert not cleanup_expense_matches_filters(
        conflict, search_q="", document_type="solicitud"
    )
    assert cleanup_expense_matches_filters(
        conflict, search_q="", document_type="all"
    )


def test_cleanup_filter_values_and_issue_groups_fail_safe() -> None:
    assert normalize_cleanup_document_type(" INFORME ") == "informe"
    assert normalize_cleanup_document_type("desconocido") == "all"
    assert normalize_cleanup_issue_type("CFDI") == "cfdi"
    assert normalize_cleanup_issue_type("desconocido") == "all"

    issues = ["Falta cuenta de cargo", "Falta cuenta de retención ISR"]
    assert cleanup_issues_match_filter(issues, "all")
    assert cleanup_issues_match_filter(issues, "main_account")
    assert cleanup_issues_match_filter(issues, "fiscal")
    assert not cleanup_issues_match_filter(issues, "counterpart")
    assert not cleanup_issues_match_filter(issues, "cfdi")


@pytest.mark.asyncio
async def test_cleanup_route_applies_and_preserves_server_filters(monkeypatch) -> None:
    captured = {}

    async def load_expenses(_session, **kwargs):
        captured.update(kwargs)
        return []

    async def unassigned_cfdis(_session):
        return []

    class ScalarRows:
        def all(self):
            return []

    class Result:
        def scalars(self):
            return ScalarRows()

    class Session:
        async def execute(self, _statement):
            return Result()

    class Suggester:
        def __init__(self, _session):
            pass

        async def get_suggestions_batch(self, **_kwargs):
            return {}

    monkeypatch.setattr(admin_routes, "load_cleanup_expenses", load_expenses)
    monkeypatch.setattr(
        admin_routes, "list_unassigned_cfdi_options", unassigned_cfdis
    )
    monkeypatch.setattr(admin_routes, "render_admin_navigation", lambda *_a, **_k: "")
    monkeypatch.setattr(
        "devnous.gastos.services.cuenta_contable_suggester.CuentaContableSuggester",
        Suggester,
    )

    html = await admin_routes.gastos_sin_cuenta_contable(
        Request({"type": "http", "query_string": b""}),
        Session(),
        period="2026-09",
        bi_year="2026",
        bi_scope=admin_routes.ACTIVE_TOURNAMENT_SCOPE,
        q="  I-26000012  ",
        document_type="informe",
        issue="fiscal",
        focus_expense_id="no-es-uuid",
        document_id=None,
        current_empleado=SimpleNamespace(),
    )

    assert captured["search_q"] == "I-26000012"
    assert captured["document_type"] == "informe"
    assert captured["issue_type"] == "fiscal"
    assert 'name="q"' in html and 'value="I-26000012"' in html
    assert 'value="informe" selected' in html
    assert 'value="fiscal" selected' in html
    assert "No hay coincidencias con los filtros seleccionados" in html
    assert "bi_year=2026" in html
    assert f"bi_scope={admin_routes.ACTIVE_TOURNAMENT_SCOPE}" in html
    assert "cleanup-row-focused" in html

    document_id = "81000000-0000-0000-0000-000000000001"
    document_html = await admin_routes.gastos_sin_cuenta_contable(
        Request({"type": "http", "query_string": b""}),
        Session(),
        period="2026-09",
        bi_year=None,
        bi_scope=None,
        q="I-26000012",
        document_type="informe",
        issue="all",
        focus_expense_id=None,
        document_id=document_id,
        current_empleado=SimpleNamespace(),
    )
    scope_sql = " ".join(str(item) for item in captured["extra_conditions"])
    assert "expense_reports.documento_id" in scope_sql
    assert "expense_reports.informe_documento_id" in scope_sql
    assert "expense_reports.solicitud_documento_id" in scope_sql
    assert "expense_reports.fecha" not in scope_sql
    assert f'name="document_id" value="{document_id}"' in document_html
    assert f'href="/documentos/{document_id}"' in document_html
    assert "Volver al reporte" in document_html


@pytest.mark.asyncio
async def test_cleanup_loader_keeps_fiscal_only_candidates(monkeypatch) -> None:
    expense = _searchable_expense()
    expense.cuenta_contable_id = "cargo"
    expense.contra_cuenta_contable_id = "contrapartida"
    expense.cfdi_report_id = "cfdi"
    captured = {}

    class ScalarRows:
        def all(self):
            return [expense]

    class Result:
        def scalars(self):
            return ScalarRows()

    class Session:
        async def execute(self, statement):
            captured["statement"] = statement
            return Result()

    async def preview(_session, _expense):
        return {"issues": ["Falta cuenta de IVA"]}

    monkeypatch.setattr(cleanup_service, "build_cleanup_preview", preview)
    rows = await cleanup_service.load_cleanup_expenses(
        Session(), issue_type="fiscal"
    )

    where_sql = str(captured["statement"].whereclause)
    assert rows == [expense]
    assert "cuenta_contable_id IS NULL" not in where_sql
    assert "contra_cuenta_contable_id IS NULL" not in where_sql
    assert "cfdi_report_id IS NULL" not in where_sql


def test_cleanup_queue_render_contract_has_month_and_unwrapped_actions(
) -> None:
    source = Path("src/devnous/gastos/routes/admin_routes.py").read_text()
    start = source.index("async def gastos_sin_cuenta_contable")
    end = source.index(
        '@router.post("/admin/gastos/{gasto_id}/cleanup-contable")', start
    )
    cleanup = source[start:end]

    assert 'type="month" name="period"' in cleanup
    assert "ExpenseReport.fecha >= period_start" in cleanup
    assert "ExpenseReport.fecha < period_end" in cleanup
    assert "_cleanup_document_origin(gasto)" in cleanup
    assert 'name="q"' in cleanup
    assert 'name="document_type"' in cleanup
    assert 'name="issue"' in cleanup
    assert "focus_expense_id" in cleanup
    assert "cleanup-row-focused" in cleanup
    assert "_cleanup_beneficiary_name(gasto)" in cleanup
    assert "<th>Beneficiario</th>" in cleanup
    assert "cleanup-return-link" in cleanup
    assert ".button, .cleanup-toggle, .btn-asignar" in cleanup
    assert "white-space:nowrap;" in cleanup
