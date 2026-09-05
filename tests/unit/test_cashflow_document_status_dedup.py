from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from devnous.gastos.routes.user_routes import _cashflow_document_total
from samchat.finance_platform.service import build_finance_platform_snapshot


def _document(*, state: str, paid_at: str | None = None) -> dict[str, Any]:
    return {
        "id": "document-1",
        "tipo": "SOLICITUD",
        "numero_referencia": "S-26000095",
        "estado": state,
        "monto_total": 120.0,
        "monto_solicitado": 100.0,
        "pagado_en": paid_at,
    }


def test_cashflow_prefers_final_total_and_only_falls_back_when_absent() -> None:
    assert _cashflow_document_total(
        SimpleNamespace(monto_total=120, monto_solicitado=100)
    ) == 120.0
    assert _cashflow_document_total(
        SimpleNamespace(monto_total=None, monto_solicitado=100)
    ) == 100.0
    assert _cashflow_document_total(
        SimpleNamespace(monto_total=0, monto_solicitado=100)
    ) == 0.0
    assert _cashflow_document_total(
        SimpleNamespace(monto_total=-120, monto_solicitado=100)
    ) == -120.0
    assert _cashflow_document_total(
        SimpleNamespace(monto_total=None, monto_solicitado=-100)
    ) == -100.0


def test_approved_to_paid_transition_does_not_duplicate_obligation() -> None:
    approved = build_finance_platform_snapshot(
        {"documents": [_document(state="aprobado")], "expenses": [], "polizas": []}
    )
    paid = build_finance_platform_snapshot(
        {
            "documents": [
                _document(state="pagado", paid_at="2026-08-14T15:06:25+00:00")
            ],
            "expenses": [],
            "polizas": [],
        }
    )

    assert approved["payment_run"]["payable_count"] == 1
    assert approved["payment_run"]["payable_total"] == 120.0
    assert paid["payment_run"]["payable_count"] == 0
    assert paid["payment_run"]["payable_total"] == 0.0
    assert paid["cash_control_center"]["paid_documents_count"] == 1
    assert paid["cash_control_center"]["paid_total"] == 120.0


def test_legacy_cashflow_commitments_use_current_state_and_total_helper() -> None:
    source = Path("src/devnous/gastos/routes/user_routes.py").read_text()
    view = source.split(
        '@router.get("/admin/contabilidad/cash-flow"', 1
    )[1].split(
        '@router.get("/admin/contabilidad/cash-flow/export.xlsx")', 1
    )[0]

    assert 'Documento.estado.in_(["aprobado", "enviado"])' in view
    assert "_cashflow_document_total(document) for document in approved_pending" in view
    assert "monto_solicitado or d.monto_total" not in view
