from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from devnous.gastos.routes.admin_routes import (
    _cleanup_document_origin,
    _cleanup_period_bounds,
)


def _expense(*, documento=None, informe=None, solicitud=None):
    return SimpleNamespace(
        documento=documento,
        informe_documento=informe,
        solicitud_documento=solicitud,
    )


def _documento(tipo: str, referencia: str):
    return SimpleNamespace(tipo=tipo, numero_referencia=referencia)


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


def test_cleanup_document_origin_marks_missing_link_explicitly() -> None:
    assert _cleanup_document_origin(_expense()) == (
        "Sin documento vinculado",
        "Sin referencia documental",
    )


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
    assert ".button, .cleanup-toggle, .btn-asignar" in cleanup
    assert "white-space:nowrap;" in cleanup
