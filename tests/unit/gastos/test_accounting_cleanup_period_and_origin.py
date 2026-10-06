from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from devnous.gastos.routes.admin_routes import (
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
    assert ".button, .cleanup-toggle, .btn-asignar" in cleanup
    assert "white-space:nowrap;" in cleanup
