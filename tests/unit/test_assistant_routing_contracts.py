from __future__ import annotations

from types import SimpleNamespace

import pytest

from samchat.assistant.conversation_service import run_message_turn_with_pending
from samchat.assistant.routing_contracts import (
    assistant_routing_contract_cases,
    evaluate_routing_contract_case,
)
from samchat.assistant.router import _maybe_append_export_prompt


class _FakeSession:
    def __init__(self):
        self.added = []
        self.commits = 0

    def add(self, item):
        self.added.append(item)

    async def commit(self):
        self.commits += 1


async def _pending_none(**_kwargs):
    return None


async def _provider_must_not_be_called(**_kwargs):  # pragma: no cover
    raise AssertionError("provider path should not be called")


async def _finance_rows(_intent):
    return [
        {"year": 2025, "concepto": "Uniformes", "amount": 1000},
        {"year": 2026, "concepto": "Uniformes", "amount": 1250},
    ]


def _case(case_id: str):
    for item in assistant_routing_contract_cases():
        if item.case_id == case_id:
            return item
    raise AssertionError(f"missing routing case {case_id}")


def _finance_payload(intent: str) -> dict:
    if intent == "cashflow.statement":
        return {
            "report_type": "cashflow_statement",
            "title": "Flujo de Efectivo",
            "subtitle": "Al Junio 2026 (cifras en miles de pesos)",
            "summary": {
                "saldo_inicial": 0,
                "origen_total": 0,
                "aplicaciones_total": 1390.78,
                "saldo_final": -1392.7,
            },
            "rows": [{"segment": "SALDO INICIAL:"}, {"segment": "Origen"}],
        }
    if intent == "budget.vs_actual":
        return {
            "report_type": "budget_vs_actual",
            "title": "Presupuesto vs Real",
            "subtitle": "Junio / Enero-Junio",
            "summary": {
                "budget_accumulated_total": 13850,
                "real_accumulated_total": 11450,
                "variance_accumulated_total": 2400,
                "variance_month_total": 2400,
            },
            "rows": [{"segment": "Ingresos"}],
        }
    if intent == "cashflow.summary":
        return {
            "summary": {
                "actual_cash_net": 100,
                "approved_obligations": 50,
                "recognized_income": 25,
                "collected_income": 10,
                "expected_uncollected_income": 15,
                "forecast_net": 75,
            }
        }
    if intent == "budget.snapshot":
        return {
            "source": "budget_db",
            "version": {"id": "version-1", "name": "Presupuesto 2026"},
            "summary": {
                "budget_total": 1000,
                "requested_total": 200,
                "committed_total": 150,
                "paid_total": 90,
                "actual_total": 80,
                "pending_to_pay_total": 60,
                "variance_vs_actual": 920,
            },
        }
    if intent == "ar.summary":
        return {
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
                {"collection_status": "matched_collected", "collected_amount": 500}
            ],
        }
    if intent == "ar.prematching":
        return {
            "summary": {
                "ar_item_count": 3,
                "candidate_match_count": 2,
                "manual_match_required_count": 1,
                "collection_unknown_count": 1,
                "payer_gap_count": 0,
                "unmatched_bank_inflow_count": 4,
            }
        }
    raise AssertionError(f"unexpected intent {intent}")


async def _run_message(raw_message, *, finance_rows_provider=None):
    return await run_message_turn_with_pending(
        raw_message=raw_message,
        conversation=SimpleNamespace(id="conv-routing", updated_at=None),
        current_empleado=SimpleNamespace(id="emp-1", rol="admin"),
        session=_FakeSession(),
        request=None,
        tournament_key=None,
        bi_year=None,
        bi_scope=None,
        bi_segment=None,
        assistant_mode=None,
        openai_api_key=None,
        latest_pending_run_for_conversation=_pending_none,
        is_explicit_approval_message=lambda _text: False,
        is_explicit_rejection_message=lambda _text: False,
        confirm_pending_run=_provider_must_not_be_called,
        deterministic_pending_builders=[],
        build_deterministic_pending_response=_provider_must_not_be_called,
        assistant_turn=_provider_must_not_be_called,
        maybe_append_export_prompt=_maybe_append_export_prompt,
        document_action_router_executor=None,
        finance_rows_provider=finance_rows_provider,
    )


def test_routing_contract_matrix_covers_required_business_paths() -> None:
    cases = assistant_routing_contract_cases()
    ids = {case.case_id for case in cases}

    assert len(cases) >= 12
    assert "ROUTE-FIN-CASHFLOW-STATEMENT-001" in ids
    assert "ROUTE-FIN-CASHFLOW-SUMMARY-001" in ids
    assert "ROUTE-FIN-BUDGET-VS-ACTUAL-001" in ids
    assert "ROUTE-FIN-BUDGET-SNAPSHOT-001" in ids
    assert "ROUTE-FIN-AR-SUMMARY-001" in ids
    assert "ROUTE-FIN-AR-PREMATCHING-001" in ids
    assert "ROUTE-OWNER-READINESS-001" in ids
    assert "ROUTE-OWNER-FOLDER-WORKSPACE-001" in ids
    assert "ROUTE-OWNER-VARIABLE-001" in ids
    assert "ROUTE-FIN-ACCOUNTING-QA-001" in ids
    assert "ROUTE-FIN-CFDI-GAPS-001" in ids
    assert "ROUTE-FIN-YOY-COMPARISON-001" in ids


def test_routing_contract_evaluator_flags_wrong_route() -> None:
    case = _case("ROUTE-FIN-CASHFLOW-STATEMENT-001")

    verdict = evaluate_routing_contract_case(
        case=case,
        assistant_message="Cashflow Planning read-only",
        tool_trace=[
            {
                "tool": "assistant_finance_read",
                "result": {
                    "intent": "cashflow.summary",
                    "provider_called": True,
                    "writes_attempted": True,
                },
            }
        ],
    )

    assert verdict.ok is False
    assert "missing_intent:cashflow.statement" in verdict.failures
    assert "provider_called" in verdict.failures
    assert "write_or_side_effect_detected" in verdict.failures
    assert "not_exportable" in verdict.failures
    assert "forbidden_term:Cashflow Planning read-only" in verdict.failures


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case_id",
    [
        "ROUTE-FIN-CASHFLOW-STATEMENT-001",
        "ROUTE-FIN-CASHFLOW-SUMMARY-001",
        "ROUTE-FIN-BUDGET-VS-ACTUAL-001",
        "ROUTE-FIN-BUDGET-SNAPSHOT-001",
        "ROUTE-FIN-AR-SUMMARY-001",
        "ROUTE-FIN-AR-PREMATCHING-001",
    ],
)
async def test_known_finance_prompts_follow_contract_without_provider(
    monkeypatch,
    case_id,
) -> None:
    from samchat.assistant import conversation_service as cs

    async def adapter(*_args, **kwargs):
        intent = kwargs["intent"]
        return {
            "ok": True,
            "read_only": True,
            "intent": intent,
            "source_function": "test.adapter",
            "payload": _finance_payload(intent),
            "source_notes": [],
            "safety_labels": ["read_only", "no_financial_effects"],
        }

    monkeypatch.setattr(cs, "run_finance_read_adapter", adapter)

    case = _case(case_id)
    response = await _run_message(case.prompt)
    verdict = evaluate_routing_contract_case(
        case=case,
        assistant_message=response.assistant_message,
        tool_trace=response.tool_trace,
    )

    assert verdict.ok, verdict.to_dict()


@pytest.mark.asyncio
async def test_yoy_comparison_prompt_follows_contract_without_provider() -> None:
    case = _case("ROUTE-FIN-YOY-COMPARISON-001")

    response = await _run_message(case.prompt, finance_rows_provider=_finance_rows)
    verdict = evaluate_routing_contract_case(
        case=case,
        assistant_message=response.assistant_message,
        tool_trace=response.tool_trace,
    )

    assert verdict.ok, verdict.to_dict()
