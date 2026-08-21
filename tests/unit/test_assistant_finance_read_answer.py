from __future__ import annotations

import inspect

from samchat.assistant import finance_read_answer
from samchat.assistant.finance_read_answer import render_finance_read_answer


def test_render_ar_summary_uses_collection_proof_only_for_matched_items() -> None:
    result = {
        "ok": True,
        "intent": "ar.summary",
        "source_notes": ["matched_collected is the only AR collection proof"],
        "payload": {
            "summary": {
                "expected_income_count": 2,
                "expected_income_total": 1500,
                "issued_linked_count": 1,
                "linked_income_total": 500,
                "issued_unlinked_count": 1,
                "issued_unlinked_total": 250,
                "collection_gap_count": 1,
                "matching_gap_count": 1,
            },
            "issued_linked": [
                {
                    "collection_status": "matched_collected",
                    "collected_amount": 500,
                }
            ],
            "issued_unlinked": [
                {
                    "collection_status": "collection_unknown",
                    "issued_amount": 250,
                }
            ],
        },
    }

    answer = render_finance_read_answer(result)

    assert "CxC AR read-only" in answer
    assert "Cobranza AR probada: $500.00 en 1 matches aceptados." in answer
    assert "Cobranza desconocida: 1 gaps de cobranza." in answer
    assert "matched_collected is the only AR collection proof" in answer


def test_render_ar_summary_does_not_call_unknown_or_candidate_items_paid() -> None:
    result = {
        "ok": True,
        "intent": "ar.summary",
        "payload": {
            "summary": {"collection_gap_count": 2},
            "issued_linked": [{"collection_status": "collection_unknown"}],
            "issued_unlinked": [{"collection_status": "candidate_match"}],
        },
    }

    answer = render_finance_read_answer(result).lower()

    assert "cobranza ar probada: $0.00" in answer
    assert "pagado" not in answer
    assert "conciliado" not in answer
    assert "cobrado" not in answer


def test_render_ar_prematching_labels_candidate_as_evidence_only() -> None:
    result = {
        "ok": True,
        "intent": "ar.prematching",
        "payload": {
            "summary": {
                "ar_item_count": 3,
                "candidate_match_count": 2,
                "manual_match_required_count": 1,
                "collection_unknown_count": 1,
                "payer_gap_count": 0,
                "unmatched_bank_inflow_count": 4,
            },
            "source_notes": ["candidate_match is not collection proof"],
        },
    }

    answer = render_finance_read_answer(result)

    assert "Pre-matching AR read-only" in answer
    assert "Evidencia candidata candidate_match: 2." in answer
    assert "candidate_match es evidencia candidata; no es cobranza AR probada." in answer
    assert "Este resultado no tiene autoridad de cobranza." in answer


def test_render_cashflow_summary_separates_actuals_and_forecast() -> None:
    result = {
        "ok": True,
        "intent": "cashflow.summary",
        "payload": {
            "summary": {
                "actual_cash_net": 700,
                "approved_obligations": 300,
                "recognized_income": 500,
                "collected_income": 200,
                "expected_uncollected_income": 800,
                "forecast_net": 1400,
            },
            "source_notes": ["AR candidates are not counted as collected cash"],
        },
    }

    answer = render_finance_read_answer(result)

    assert "Cashflow Planning read-only" in answer
    assert "Caja real neta: $700.00." in answer
    assert "Ingreso reconocido: $500.00." in answer
    assert "Cobranza AR probada: $200.00." in answer
    assert "Forecast derivado: $1,400.00." in answer
    assert "El forecast es derivado." in answer
    assert "Los candidatos AR no cuentan como cobrado." in answer


def test_render_budget_snapshot_shows_budget_totals_without_authority_language() -> None:
    result = {
        "ok": True,
        "intent": "budget.snapshot",
        "source_notes": ["budget authority stays in Presupuestos"],
        "payload": {
            "source": "budget_db",
            "version": {"id": "version-1", "name": "Presupuesto 2026"},
            "summary": {
                "budget_total": 5000,
                "requested_total": 1200,
                "committed_total": 900,
                "paid_total": 400,
                "actual_total": 700,
                "pending_to_pay_total": 500,
                "variance_vs_actual": 4300,
            },
            "forecast": {"health": "healthy"},
        },
    }

    answer = render_finance_read_answer(result)

    assert "Presupuesto read-only" in answer
    assert "Version: Presupuesto 2026." in answer
    assert "Fuente: budget_db." in answer
    assert "Presupuesto total: $5,000.00." in answer
    assert "Solicitado: $1,200.00." in answer
    assert "Comprometido: $900.00." in answer
    assert "Pagado: $400.00." in answer
    assert "Real/actual: $700.00." in answer
    assert "Pendiente por pagar: $500.00." in answer
    assert "Varianza contra actual: $4,300.00." in answer
    assert "Forecast health: healthy." in answer
    assert "Este snapshot no autoriza cambios de presupuesto." in answer
    assert "budget authority stays in Presupuestos" in answer
    assert "modificado" not in answer


def test_render_budget_snapshot_labels_artifact_fallback() -> None:
    result = {
        "ok": True,
        "intent": "budget.snapshot",
        "payload": {
            "source": "budget_artifact",
            "summary": {"budget_total": 100},
        },
    }

    answer = render_finance_read_answer(result)

    assert "Fuente: budget_artifact." in answer
    assert "fallback o referencia" in answer
    assert "verificar la version runtime" in answer


def test_render_finance_platform_shows_operating_metrics_without_ar_language() -> None:
    result = {
        "ok": True,
        "intent": "finance.platform",
        "source_notes": ["payment_run is AP/payment-run, not AR collection"],
        "payload": {
            "period": {"year": 2026, "month": 4},
            "summary": {"documents": 8, "expenses": 5, "polizas": 3},
            "action_queue": {"open_count": 4, "high_count": 1},
            "cash_control_center": {"payment_pressure": "high"},
            "payment_run": {"payable_count": 2, "payable_total": 1500},
            "accounting_close_center": {
                "coi_ready_expenses_count": 3,
                "pending_coi_expenses_count": 2,
                "unbalanced_count": 1,
            },
            "tax_readiness": {"diot_blockers_count": 6, "status": "blocked"},
            "finance_brief": {"plain_text": "Brief financiero 4/2026"},
        },
    }

    answer = render_finance_read_answer(result)

    assert "Finance Platform read-only" in answer
    assert "Periodo: 4 / 2026." in answer
    assert "Documentos: 8." in answer
    assert "Gastos: 5." in answer
    assert "Polizas: 3." in answer
    assert "Acciones abiertas: 4 (1 alta prioridad)." in answer
    assert "Presion de pago: high. Pagos AP pendientes: 2 por $1,500.00." in answer
    assert "COI listo: 3; COI pendiente: 2." in answer
    assert "Polizas descuadradas: 1." in answer
    assert "DIOT/CFDI blockers: 6; tax status: blocked." in answer
    assert "Payment run es AP; no es cobranza AR." in answer
    assert "Brief financiero 4/2026" in answer
    assert "payment_run is AP/payment-run, not AR collection" in answer
    assert "cobranza AR probada" not in answer
    assert "pagado ejecutado" not in answer
    assert "cierre ejecutado" not in answer
    assert "autorizado" not in answer


def test_render_finance_exports_lists_owners_routes_and_caveats() -> None:
    result = {
        "ok": True,
        "intent": "finance.exports",
        "source_notes": ["exports are executed by owning modules"],
        "payload": {
            "exports": [
                {
                    "id": "finance_platform_xlsx",
                    "owner": "Finance Platform",
                    "route": "/admin/finanzas/export.xlsx",
                    "artifact_class": "report_export",
                    "status": "live",
                    "caveat": "Generated by the Finance Platform export route.",
                },
                {
                    "id": "budget_review_xlsx",
                    "owner": "Presupuestos",
                    "route": "/admin/presupuestos/export.xlsx",
                    "artifact_class": "report_export",
                    "status": "live",
                    "caveat": "Budget authority stays in budget versions and services.",
                },
                {
                    "id": "assistant_report_export",
                    "owner": "Assistant report flow",
                    "route": "POST /api/assistant/reports/export",
                    "artifact_class": "report_export",
                    "status": "live",
                    "caveat": "Only for exportable assistant report traces.",
                },
                {
                    "id": "legacy_cashflow_export",
                    "owner": "Legacy accounting route",
                    "route": "/admin/contabilidad/cash-flow/export.xlsx",
                    "artifact_class": "report_export",
                    "status": "legacy/reference",
                    "caveat": "Not Finance Spine authority.",
                },
            ]
        },
    }

    answer = render_finance_read_answer(result)

    assert "Finance exports guidance read-only" in answer
    assert "El asistente no genero archivo" in answer
    assert "finance_platform_xlsx: owner=Finance Platform; status=live" in answer
    assert "route=/admin/finanzas/export.xlsx" in answer
    assert "budget_review_xlsx: owner=Presupuestos; status=live" in answer
    assert "assistant_report_export: owner=Assistant report flow; status=live" in answer
    assert "legacy_cashflow_export: owner=Legacy accounting route" in answer
    assert "status=legacy/reference" in answer
    assert "Not Finance Spine authority." in answer
    assert "exports are executed by owning modules" in answer
    assert "archivo generado" not in answer


def test_render_error_does_not_suggest_sql_or_legacy_route() -> None:
    answer = render_finance_read_answer(
        {
            "ok": False,
            "intent": "finance.sql",
            "error": {"code": "unsupported_finance_intent"},
            "allowed_intents": ["ar.summary", "cashflow.summary"],
        }
    )

    assert "fuente canónica read-only" in answer
    assert "unsupported_finance_intent" in answer
    assert "db_read_universal" not in answer
    assert "/admin/contabilidad/cash-flow" not in answer
    assert "SQL" not in answer


def test_renderer_source_has_no_legacy_sql_or_write_surfaces() -> None:
    source = inspect.getsource(finance_read_answer)

    assert "db_read_universal" not in source
    assert "/admin/contabilidad/cash-flow" not in source
    assert "session.execute" not in source
    assert "text(" not in source
    assert "pending_confirmation" not in source
    assert "db_write_universal" not in source
