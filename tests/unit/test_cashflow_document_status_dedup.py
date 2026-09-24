from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from devnous.gastos.routes import user_routes
from devnous.gastos.routes.user_routes import (
    _cashflow_document_total,
    contabilidad_cash_flow_view,
)
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


def test_legacy_cashflow_period_filter_bounds_projection_sources() -> None:
    source = Path("src/devnous/gastos/routes/user_routes.py").read_text()
    view = source.split(
        '@router.get("/admin/contabilidad/cash-flow"', 1
    )[1].split(
        '@router.get("/admin/contabilidad/cash-flow/export.xlsx")', 1
    )[0]

    assert "cashflow_window_start = start_dt.date()" in view
    assert "cashflow_window_end = min(" in view
    assert "Documento.fecha_pago >= cashflow_window_start" in view
    assert "Documento.fecha_pago < cashflow_window_end" in view
    assert "CFDIReport.fecha < datetime.combine(cashflow_window_end" in view
    assert "target_date - cashflow_window_start" in view
    assert "CFDIReport.fecha >= datetime(today.year, 1, 1)" not in view
    assert "El banco se filtra por cuenta y período, no por proyecto" in view

    export = source.split(
        '@router.get("/admin/contabilidad/cash-flow/export.xlsx")', 1
    )[1].split('@router.get("/admin/contabilidad/tesoreria-matches")', 1)[0]
    assert "cashflow_window_start = start_dt.date()" in export
    assert "cashflow_window_end = min(" in export
    assert "Documento.fecha_pago.is_(None)" in export
    assert "CFDIReport.fecha < datetime.combine(cashflow_window_end" in export


class _CashflowResult:
    def __init__(self, rows: list[Any] | None = None) -> None:
        self.rows = rows or []

    def all(self) -> list[Any]:
        return self.rows

    def scalars(self) -> "_CashflowResult":
        return self

    def __iter__(self):
        return iter(self.rows)


class _CashflowSession:
    def __init__(self, responses: list[_CashflowResult]) -> None:
        self.responses = responses

    async def execute(self, *_args: Any, **_kwargs: Any) -> _CashflowResult:
        return self.responses.pop(0) if self.responses else _CashflowResult()


@pytest.mark.asyncio
async def test_legacy_cashflow_uses_selected_period_for_projected_rows() -> None:
    cfdi = SimpleNamespace(
        id="cfdi-1",
        total=100.0,
        cfdi_uuid="UUID-1",
        fecha=datetime(2026, 1, 5),
        receptor_nombre="Cliente",
        receptor_rfc="CLI010101AAA",
        emisor_nombre="Proveedor",
        emisor_rfc="RFC010101AAA",
        moneda="MXN",
        serie="A",
        folio="1",
        descripcion_concepto_principal="Servicio",
    )
    session = _CashflowSession(
        [
            _CashflowResult([("RFC010101AAA",)]),
            _CashflowResult(),
            _CashflowResult(),
            _CashflowResult(),
            _CashflowResult(),
            _CashflowResult(),
            _CashflowResult(),
            _CashflowResult(),
            _CashflowResult(),
            _CashflowResult(),
            _CashflowResult([cfdi]),
            _CashflowResult(),
            _CashflowResult([cfdi]),
            _CashflowResult(),
        ]
    )
    empty_matches = {"total": 0.0, "count": 0, "by_uuid": {}, "by_id": {}}
    with (
        patch.object(
            user_routes,
            "_ensure_cfdi_project_assignment_schema",
            new=AsyncMock(),
        ),
        patch.object(
            user_routes,
            "_active_cfdi_project_assignment_ids",
            new=AsyncMock(return_value=[]),
        ),
        patch.object(
            user_routes,
            "_load_active_treasury_cfdi_match_amounts",
            new=AsyncMock(return_value=empty_matches),
        ),
        patch.object(user_routes, "render_top_navigation", return_value=""),
        patch.object(user_routes, "_contabilidad_subnav", return_value=""),
    ):
        response = await contabilidad_cash_flow_view(
            request=SimpleNamespace(),
            session=session,
            current_empleado=SimpleNamespace(),
            year=2026,
            month=1,
            horizon_days=15,
            dias_credito=0,
            periodo="semanal",
            cuenta_bancaria="all",
            proyecto_scope="all",
        )

    assert response.status_code == 200
    assert "Cobros esperados 15 días" in response.body.decode()
