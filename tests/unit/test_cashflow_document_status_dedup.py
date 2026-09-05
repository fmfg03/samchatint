from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from devnous.gastos.routes.user_routes import _cashflow_document_total
from samchat.finance_platform.service import build_finance_platform_snapshot


def _document(
    *,
    state: str,
    paid_at: str | None = None,
    reference: str = "S-26000095",
    total: float | None = 120.0,
    requested: float = 100.0,
    concept: str = "Pago a tercero",
) -> dict[str, Any]:
    return {
        "id": reference,
        "tipo": "SOLICITUD",
        "numero_referencia": reference,
        "estado": state,
        "monto_total": total,
        "monto_solicitado": requested,
        "concepto_pago": concept,
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


def test_reimbursement_without_final_total_fails_closed() -> None:
    document = SimpleNamespace(
        monto_total=None,
        monto_solicitado=56020,
        concepto_pago="Reembolso de saldo a favor - informe",
    )

    assert _cashflow_document_total(document) == 0.0


def test_only_active_reimbursement_contributes_to_payment_run() -> None:
    documents = [
        _document(
            state="rechazado", reference="S-rejected-1", total=4582.32,
            requested=4582.32,
            concept="Reembolso de saldo a favor - I-764369",
        ),
        _document(
            state="cancelado", reference="S-rejected-2", total=4582.32,
            requested=4582.32,
            concept="Reembolso de saldo a favor - I-764369",
        ),
        _document(
            state="aprobado", reference="S-active", total=4582.32,
            requested=4582.32,
            concept="Reembolso de saldo a favor - I-764369",
        ),
    ]
    result = build_finance_platform_snapshot(
        {"documents": documents, "expenses": [], "polizas": []}
    )

    assert result["payment_run"]["payable_count"] == 1
    assert result["payment_run"]["payable_total"] == 4582.32


def test_reimbursement_without_total_is_reported_as_inconsistency() -> None:
    result = build_finance_platform_snapshot(
        {
            "documents": [
                _document(
                    state="aprobado", total=None, requested=56020,
                    concept="Reembolso de saldo a favor - I-235650",
                )
            ],
            "expenses": [],
            "polizas": [],
        }
    )

    assert result["payment_run"]["payable_count"] == 0
    assert result["payment_run"]["payable_total"] == 0.0
    assert result["payment_run"]["amount_inconsistency_count"] == 1


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


def test_in_process_payment_remains_one_obligation() -> None:
    in_process = build_finance_platform_snapshot(
        {
            "documents": [_document(state="en_proceso_pago")],
            "expenses": [],
            "polizas": [],
        }
    )

    assert in_process["payment_run"]["payable_count"] == 1
    assert in_process["payment_run"]["payable_total"] == 120.0


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
