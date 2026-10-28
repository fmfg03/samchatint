"""Acceptance regressions for one exact, source-backed cashflow projection."""

from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from samchat.cashflow.service import (
    build_cashflow_planning_read_model,
    build_cashflow_report,
)


def sources(**changes):
    result = dict(
        bank_rows=[],
        documents=[],
        ar_payload={},
        budget_plan=[],
        source_status={
            k: dict(available=True, complete=True, reason="")
            for k in ("bank", "obligations", "ar", "budget")
        },
    )
    result.update(changes)
    return result


def bank(identity="bank-1", amount="100", currency="MXN", **changes):
    result = dict(
        id=identity,
        fecha="2026-01-05",
        signo="+",
        importe=amount,
        currency=currency,
        cuenta_bancaria="1234567890123456",
        nombre_ordenante="Cliente",
        nombre_beneficiario="Proveedor",
    )
    result.update(changes)
    return result


def ar(**changes):
    result = dict(
        ar_item_id="linked:1",
        currency="MXN",
        linked_income_amount="116",
        recognized_income_amount="100",
        recognized_income_date="2026-01-05",
        due_date="2026-01-31",
        collection_status="matched_collected",
        collected_amount="100",
        outstanding_amount="0",
        outstanding_amount_status="known",
        collection_matches=[
            dict(
                status="accepted_collection_match",
                collection_date="2026-01-05",
                accepted_amount="100",
            )
        ],
    )
    result.update(changes)
    return result


def report(data=None, **changes):
    args = dict(
        start=date(2026, 1, 1),
        end=date(2026, 2, 1),
        cutoff=date(2026, 1, 31),
        forecast_end=date(2026, 2, 1),
    )
    args.update(changes)
    return build_cashflow_report(data or sources(), **args)


def test_ac03_bank_and_accepted_collection_are_one_cash_entry():
    result = report(sources(bank_rows=[bank()], ar_payload={"issued_linked": [ar()]}))
    assert result["summary"]["actual_cash_in"] == Decimal("100")
    assert result["summary"]["collected_income"] == Decimal("100")
    assert result["summary"]["forecast_net"] == Decimal("100")


def test_ac04_budget_and_invoice_not_added_as_twice_expected_cash():
    result = report(
        sources(
            ar_payload={
                "issued_linked": [
                    ar(
                        collection_status="not_collected",
                        collected_amount="0",
                        collection_matches=[],
                        outstanding_amount="100",
                    )
                ]
            },
            budget_plan=[
                dict(
                    year=2026, month=1, currency="MXN", direction="income", amount="100"
                )
            ],
        )
    )
    assert result["summary"]["planned_budget_income"] == 100
    assert result["summary"]["recognized_income"] == 100
    assert result["summary"]["expected_uncollected_income"] == 100
    assert result["summary"]["forecast_net"] == 100


@pytest.mark.parametrize("collected,residual", [("30", "70"), ("0", "100")])
def test_partial_collection_and_reversed_match_only_reduce_outstanding(
    collected, residual
):
    result = report(
        sources(
            ar_payload={
                "issued_linked": [
                    ar(
                        collected_amount=collected,
                        outstanding_amount=residual,
                        collection_matches=[],
                    )
                ]
            }
        )
    )
    assert result["summary"]["expected_uncollected_income"] == Decimal(residual)


def test_ac05_same_month_in_other_year_never_appears_in_report():
    result = report(
        sources(
            ar_payload={
                "issued_linked": [
                    ar(
                        recognized_income_date="2025-01-05",
                        collection_matches=[
                            dict(
                                status="accepted_collection_match",
                                collection_date="2025-01-05",
                                accepted_amount="100",
                            )
                        ],
                    )
                ]
            }
        )
    )
    assert result["summary"]["recognized_income"] == 0
    assert result["summary"]["collected_income"] == 0


def test_twenty_four_months_preserve_actual_year_month():
    result = report(
        sources(bank_rows=[bank()]),
        end=date(2028, 1, 1),
        cutoff=date(2027, 12, 31),
        forecast_end=date(2028, 1, 1),
    )
    assert len(result["monthly_buckets"]) == 24
    assert result["monthly_buckets"][0]["actual_cash_in"] == 100
    assert result["monthly_buckets"][12]["actual_cash_in"] == 0
    assert result["monthly_buckets"][12]["key"] == "2027-01"


def test_ac01_identity_deduplication_not_amount_matching():
    result = report(sources(bank_rows=[bank(), bank(), bank("different-id")]))
    assert result["summary"]["actual_cash_in"] == 200
    assert len(result["movements"]) == 2


def test_conflicting_same_identity_invalidates_total():
    result = report(sources(bank_rows=[bank(), bank(amount="200")]))
    assert result["summary"]["actual_cash_in"] is None


def test_decimal_arithmetic_preserves_cents():
    result = report(sources(bank_rows=[bank(amount="0.1"), bank("b2", amount="0.2")]))
    assert result["summary"]["actual_cash_in"] == Decimal("0.3")


@pytest.mark.parametrize(
    "state,amount", [("aprobado", 100), ("en_proceso_pago", 100), ("pagado", 0)]
)
def test_ac06_payment_transition_does_not_duplicate_obligations(state, amount):
    result = report(
        sources(
            bank_rows=[bank()],
            documents=[
                dict(
                    id="d1",
                    estado=state,
                    currency="MXN",
                    payable_amount="100",
                    fecha_pago="2026-01-20",
                )
            ],
        )
    )
    assert result["summary"]["approved_obligations"] == amount


def test_submitted_requests_are_separate_from_authorized_obligations():
    result = report(
        sources(
            bank_rows=[bank()],
            documents=[
                dict(
                    id="d1",
                    estado="enviado",
                    currency="MXN",
                    payable_amount="100",
                    fecha_pago="2026-01-20",
                )
            ],
        )
    )
    assert result["summary"]["approved_obligations"] == 0
    assert result["summary"]["submitted_obligations"] == 100
    assert result["summary"]["forecast_net"] == 100


def test_undated_and_overdue_obligations_remain_visible_without_invented_date():
    result = report(
        sources(
            bank_rows=[bank()],
            documents=[
                dict(id="d1", estado="aprobado", currency="MXN", payable_amount="10"),
                dict(
                    id="d2",
                    estado="aprobado",
                    currency="MXN",
                    payable_amount="20",
                    fecha_pago="2025-12-10",
                ),
            ],
        )
    )
    assert {r["window"] for r in result["obligations"]} == {"undated", "overdue"}
    assert result["summary"]["approved_obligations"] == 30
    assert result["summary"]["forecast_net"] is None


def test_ac02_no_opening_balance_is_never_called_available_cash():
    result = report(sources(bank_rows=[bank()]))
    assert result["summary"]["opening_balance"] is None
    assert result["summary"]["closing_balance"] is None
    assert result["summary"]["actual_cash_net"] == 100


def test_ac08_currency_totals_are_never_combined():
    result = report(sources(bank_rows=[bank(), bank("usd", currency="USD")]))
    assert result["summary"]["actual_cash_in"] is None
    assert result["summary_by_currency"]["MXN"]["actual_cash_in"] == 100
    assert result["summary_by_currency"]["USD"]["actual_cash_in"] == 100


def test_unknown_currency_and_null_amount_are_not_zero():
    result = report(sources(bank_rows=[bank(currency=None, amount=None)]))
    assert result["summary"]["actual_cash_in"] is None
    assert result["movements"][0]["amount"] is None


def test_project_never_reuses_unattributed_global_bank_total():
    result = report(sources(bank_rows=[bank()]), project="project-1")
    assert result["movements"] == []
    assert result["summary"]["actual_cash_in"] is None


def test_ac09_more_than_5000_movements_totals_all_details_paginated():
    result = report(
        sources(bank_rows=[bank(str(i), amount="1") for i in range(5001)]),
        page_size=50,
        page=2,
    )
    assert result["summary"]["actual_cash_in"] == 5001
    assert result["details"]["total"] == 5001
    assert len(result["details"]["rows"]) == 50
    assert result["details"]["page"] == 2


def test_bank_query_failure_keeps_total_unavailable():
    data = sources()
    data["source_status"]["bank"] = dict(
        available=False, complete=False, reason="Fuente no disponible"
    )
    result = report(data)
    assert result["summary"]["actual_cash_in"] is None
    assert any("Fuente no disponible" in n for n in result["source_notes"])


def test_future_period_is_not_a_verified_zero_bank_period():
    result = report(sources(bank_rows=[bank()]), forecast_end=date(2026, 3, 1))
    assert result["monthly_buckets"][1]["actual_cash_in"] is None
    assert result["summary"]["actual_cash_in"] == 100


def test_detail_selection_preserves_full_report_totals():
    result = report(
        sources(bank_rows=[bank(), bank("out", signo="-")]), detail_direction="out"
    )
    assert result["summary"]["actual_cash_in"] == 100
    assert result["summary"]["actual_cash_out"] == 100
    assert result["details"]["total"] == 1
    assert result["details"]["rows"][0]["direction"] == "out"


@pytest.mark.asyncio
async def test_async_builder_limits_only_detail_and_passes_real_horizon():
    with patch(
        "samchat.cashflow.service.load_cashflow_sources",
        new=AsyncMock(return_value=sources(bank_rows=[bank()])),
    ) as load:
        result = await build_cashflow_planning_read_model(
            object(),
            year=2026,
            month=1,
            horizon_months=1,
            horizon_days=90,
            limit=5,
            cutoff=date(2026, 1, 31),
        )
    assert load.await_args.kwargs["forecast_end"] > date(2026, 2, 1)
    assert result["details"]["page_size"] == 5
    assert result["summary"]["actual_cash_in"] == 100


def test_selected_account_never_combines_companywide_forecast():
    data = sources(
        bank_rows=[bank()],
        documents=[
            dict(
                id="ap",
                estado="aprobado",
                currency="MXN",
                payable_amount="60",
                fecha_pago="2026-01-31",
            )
        ],
        ar_payload={"issued_linked": [ar(outstanding_amount="40")]},
    )
    key = report(data)["account_options"][0]["key"]
    result = report(data, account=key)
    assert result["summary"]["actual_cash_in"] == 100
    assert result["summary"]["approved_obligations"] is None
    assert result["summary"]["expected_uncollected_income"] is None
    assert result["summary"]["forecast_net"] is None
    assert not result["source_status"]["obligations"]["complete"]
    assert any("ámbito empresarial" in note for note in result["source_notes"])


def test_project_selection_filters_recognized_collected_and_budget_facts():
    data = sources(
        bank_rows=[bank(project_id="project-a")],
        ar_payload={
            "issued_linked": [ar(tournament_id="project-b", linked_income_amount="99")]
        },
        budget_plan=[
            dict(
                tournament_id="project-b",
                year=2026,
                month=1,
                currency="MXN",
                direction="income",
                amount="77",
            )
        ],
    )
    result = report(data, project="project-a")
    assert result["summary"]["recognized_income"] == 0
    assert result["summary"]["collected_income"] == 0
    assert result["summary"]["planned_budget_income"] == 0
    assert result["budget_plan"] == []


@pytest.mark.parametrize("lane", ["bank", "obligations", "ar"])
def test_unknown_currency_cannot_disappear_behind_explicit_mxn_filter(lane):
    data = sources(bank_rows=[bank()])
    if lane == "bank":
        data["bank_rows"].append(bank("unknown", amount="50", currency=None))
        field = "actual_cash_in"
    elif lane == "obligations":
        data["documents"] = [
            dict(
                id="unknown-ap",
                estado="aprobado",
                currency=None,
                payable_amount="50",
                fecha_pago="2026-01-31",
            )
        ]
        field = "approved_obligations"
    else:
        data["ar_payload"] = {"issued_linked": [ar(currency=None)]}
        field = "recognized_income"
    result = report(data, currency="MXN")
    assert result["summary"][field] is None
    assert not result["source_status"][lane]["complete"]
    assert "moneda" in result["source_status"][lane]["reason"]


def test_forged_account_key_is_rejected_explicitly():
    with pytest.raises(ValueError, match="cashflow_account_selection_not_found"):
        report(sources(bank_rows=[bank()]), account="forged-key")


def test_overdue_receivable_remains_visible_but_does_not_become_future_cash():
    result = report(
        sources(
            bank_rows=[bank()],
            ar_payload={
                "issued_linked": [
                    ar(
                        due_date="2026-01-20",
                        outstanding_amount="100",
                        collection_status="not_collected",
                        collected_amount="0",
                        collection_matches=[],
                    )
                ]
            },
        )
    )
    assert result["receivables"][0]["window"] == "overdue"
    assert result["summary"]["expected_uncollected_income"] == 0
    assert result["summary"]["forecast_net"] is None


def test_overdue_authorized_payment_has_no_invented_future_execution_date():
    result = report(
        sources(
            bank_rows=[bank()],
            documents=[
                dict(
                    id="overdue-ap",
                    estado="aprobado",
                    currency="MXN",
                    payable_amount="50",
                    fecha_pago="2026-01-20",
                )
            ],
        )
    )
    assert result["obligations"][0]["window"] == "overdue"
    assert result["summary"]["approved_obligations"] == 50
    assert result["summary"]["forecast_net"] is None


def test_declared_unknown_currency_status_overrides_nominal_currency_code():
    result = report(
        sources(ar_payload={"issued_linked": [ar(currency_status="unknown")]}),
        currency="MXN",
    )
    assert result["summary"]["recognized_income"] is None
    assert not result["source_status"]["ar"]["complete"]


def test_collection_proof_aggregate_mismatch_is_not_reported_as_collection():
    result = report(
        sources(
            bank_rows=[bank()],
            ar_payload={"issued_linked": [ar(collected_amount="999")]},
        )
    )
    assert result["summary"]["collected_income"] is None
    assert result["summary"]["actual_cash_in"] == 100


@pytest.mark.parametrize(
    "grouping,end,cutoff,keys",
    [
        ("trimestral", date(2026, 7, 1), date(2026, 6, 30), ["2026-T1", "2026-T2"]),
        ("semestral", date(2027, 1, 1), date(2026, 12, 31), ["2026-S1", "2026-S2"]),
        ("anual", date(2028, 1, 1), date(2027, 12, 31), ["2026", "2027"]),
    ],
)
def test_grouping_changes_presentation_without_moving_cash_between_years(
    grouping, end, cutoff, keys
):
    result = report(
        sources(bank_rows=[bank(fecha="2026-05-15")]),
        grouping=grouping,
        end=end,
        cutoff=cutoff,
        forecast_end=end,
    )
    assert [row["key"] for row in result["monthly_buckets"]] == keys
    assert result["summary"]["actual_cash_in"] == 100
    assert sum(row["actual_cash_in"] for row in result["monthly_buckets"]) == 100


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"start": date(2026, 2, 1)}, "intervalo"),
        ({"forecast_end": date(2026, 1, 31)}, "intervalo"),
        ({"grouping": "diario"}, "Agrupación"),
    ],
)
def test_invalid_interval_or_unsupported_grouping_is_rejected(changes, reason):
    with pytest.raises(ValueError, match=reason):
        report(sources(bank_rows=[bank()]), **changes)


@pytest.mark.parametrize("amount", [True, "NaN", "not-an-amount", "-1"])
def test_invalid_bank_amount_is_not_rendered_as_verified_zero(amount):
    result = report(sources(bank_rows=[bank(amount=amount)]))
    assert result["summary"]["actual_cash_in"] is None
    assert not result["source_status"]["bank"]["complete"]


@pytest.mark.asyncio
async def test_async_builder_preserves_separate_edition_and_calendar_year():
    data = sources(bank_rows=[bank()])
    data["budget_version_id"] = "approved-2025"
    with patch(
        "samchat.cashflow.service.load_cashflow_sources",
        new=AsyncMock(return_value=data),
    ) as loader:
        result = await build_cashflow_planning_read_model(
            object(),
            edition_year=2025,
            year=2026,
            month=1,
            horizon_months=1,
            cutoff=date(2026, 1, 31),
        )
    assert loader.await_args.kwargs["edition_year"] == 2025
    assert loader.await_args.kwargs["start"] == date(2026, 1, 1)
    assert result["edition_year"] == 2025
    assert result["period"]["year"] == 2026
    assert result["budget_version_id"] == "approved-2025"


@pytest.mark.asyncio
async def test_current_document_states_do_not_invent_historical_forecast():
    data = sources(
        bank_rows=[bank()],
        state_as_of="2026-10-28",
        documents=[
            dict(
                id="current-ap",
                estado="aprobado",
                currency="MXN",
                payable_amount="50",
                fecha_pago="2026-02-01",
            )
        ],
        ar_payload={"issued_linked": [ar()]},
    )
    with patch(
        "samchat.cashflow.service.load_cashflow_sources",
        new=AsyncMock(return_value=data),
    ):
        result = await build_cashflow_planning_read_model(
            object(),
            year=2026,
            month=1,
            horizon_months=1,
            horizon_days=90,
            cutoff=date(2026, 1, 31),
        )
    assert result["summary"]["actual_cash_in"] == 100
    assert result["summary"]["recognized_income"] == 100
    assert result["summary"]["collected_income"] == 100
    for field in (
        "approved_obligations",
        "submitted_obligations",
        "expected_uncollected_income",
        "forecast_net",
    ):
        assert result["summary"][field] is None
        assert all(row[field] is None for row in result["monthly_buckets"])
    assert result["state_as_of"] == "2026-10-28"
    assert not result["source_status"]["forecast"]["complete"]
    assert "corte histórico" in result["source_status"]["forecast"]["reason"]
    assert result["obligations"][0]["reference"] == "current-ap"


def test_future_real_window_does_not_drop_current_forecast_obligations():
    data = sources(
        documents=[
            dict(
                id="november-ap",
                estado="aprobado",
                currency="MXN",
                payable_amount="100",
                fecha_pago="2026-11-15",
            )
        ]
    )
    result = report(
        data,
        start=date(2027, 1, 1),
        end=date(2027, 2, 1),
        cutoff=date(2026, 10, 28),
        forecast_end=date(2027, 2, 1),
    )
    assert result["summary"]["actual_cash_in"] is None
    assert result["summary"]["approved_obligations"] == 100
    assert result["summary"]["forecast_net"] == -100
    november = next(r for r in result["monthly_buckets"] if r["key"] == "2026-11")
    assert november["actual_cash_in"] is None
    assert november["forecast_net"] == -100


def test_noncontiguous_real_and_forecast_intervals_do_not_invent_gap_periods():
    result = report(
        sources(bank_rows=[bank()]),
        cutoff=date(2026, 10, 28),
        forecast_end=date(2026, 11, 1),
    )
    assert [r["key"] for r in result["monthly_buckets"]] == ["2026-01", "2026-10"]
    assert result["summary"]["actual_cash_in"] == 100
    assert result["monthly_buckets"][1]["actual_cash_in"] is None


@pytest.mark.parametrize(
    "grouping", ["semanal", "mensual", "trimestral", "semestral", "anual"]
)
def test_grouping_does_not_change_real_or_projected_total(grouping):
    result = report(sources(bank_rows=[bank()]), grouping=grouping)
    assert result["summary"]["actual_cash_net"] == 100
    assert result["summary"]["forecast_net"] == 100


@pytest.mark.parametrize(
    "grouping", ["semanal", "mensual", "trimestral", "semestral", "anual"]
)
def test_grouping_preserves_real_baseline_and_future_obligation_once(grouping):
    result = report(
        sources(
            bank_rows=[bank()],
            documents=[
                dict(
                    id="future-ap",
                    estado="aprobado",
                    currency="MXN",
                    payable_amount="40",
                    fecha_pago="2026-02-15",
                )
            ],
        ),
        grouping=grouping,
        forecast_end=date(2026, 3, 1),
    )
    assert result["summary"]["actual_cash_net"] == 100
    assert result["summary"]["forecast_net"] == 60


@pytest.mark.parametrize(
    "state", ["matched_collected", "partially_collected", "over_collected_review"]
)
def test_current_ar_receipts_keep_each_collection_in_its_year_month(state):
    item = ar(
        recognized_income_amount="1000",
        linked_income_amount="1160",
        collection_status=state,
        collection_source="accepted_match",
        collected_amount="400",
        outstanding_amount="760",
        due_date=None,
        collection_matches=[],
        collection_receipts=[
            dict(id="receipt-1", accepted_amount="150", collection_date="2026-01-20"),
            dict(id="receipt-2", accepted_amount="250", collection_date="2027-01-20"),
        ],
    )
    result = report(
        sources(ar_payload={"issued_linked": [item]}),
        end=date(2027, 2, 1),
        cutoff=date(2027, 1, 31),
        forecast_end=date(2027, 2, 1),
    )
    january = {r["key"]: r for r in result["monthly_buckets"]}
    assert january["2026-01"]["recognized_income"] == 1000
    assert january["2026-01"]["collected_income"] == 150
    assert january["2027-01"]["collected_income"] == 250
    assert result["summary"]["collected_income"] == 400
    assert result["summary"]["forecast_net"] is None
    assert item["outstanding_amount"] == "760"


def test_missing_recognized_income_does_not_fall_back_to_gross_invoice():
    result = report(
        sources(ar_payload={"issued_linked": [ar(recognized_income_amount=None)]})
    )
    assert result["summary"]["recognized_income"] is None
    assert result["summary"]["collected_income"] == 100


@pytest.mark.parametrize("state", ["candidate_match", "collection_unknown"])
def test_current_ar_candidates_do_not_supply_collection_facts(state):
    result = report(
        sources(
            ar_payload={
                "issued_linked": [
                    ar(
                        collection_status=state,
                        collection_matches=[],
                        collected_amount=None,
                        outstanding_amount=None,
                        outstanding_amount_status="unknown",
                    )
                ]
            }
        )
    )
    assert result["summary"]["collected_income"] == 0
    assert result["summary"]["forecast_net"] is None


def test_current_ar_collection_conflict_does_not_show_zero_collection():
    result = report(
        sources(
            ar_payload={
                "issued_linked": [
                    ar(collection_status="collection_conflict", collected_amount=None)
                ]
            }
        )
    )
    assert result["summary"]["collected_income"] is None
    assert result["summary"]["forecast_net"] is None
    assert "ar_collection_conflict" in result["source_notes"]


@pytest.mark.parametrize(
    "gap",
    [
        "candidates_year_missing",
        "income_lines_truncated",
        "candidates_truncated",
        "unlinked_accounting_count",
        "unknown_balance_count",
        "gross_amount_gap",
        "recognized_income_gap",
        "collection_conflict_count",
    ],
)
def test_current_ar_coverage_gap_keeps_forecast_unknown(gap):
    result = report(
        sources(ar_payload={"source_coverage": {gap: True}, "issued_linked": [ar()]})
    )
    assert result["summary"]["expected_uncollected_income"] is None
    assert result["summary"]["forecast_net"] is None
    assert "ar_forecast_basis_and_schedule_unproven" in result["source_notes"]


def test_current_ar_currency_gap_does_not_show_zero_income_or_collection():
    result = report(
        sources(ar_payload={"source_coverage": {"currency_gap": True}}), currency="MXN"
    )
    assert result["summary"]["recognized_income"] is None
    assert result["summary"]["collected_income"] is None
    assert result["summary"]["forecast_net"] is None
    assert "ar_currency_coverage_missing" in result["source_notes"]


def test_current_ar_unlinked_accounting_keeps_forecast_unknown():
    result = report(sources(ar_payload={"unlinked_income_links": [{"id": "old-link"}]}))
    assert result["summary"]["forecast_net"] is None
    assert "ar_forecast_basis_and_schedule_unproven" in result["source_notes"]


def test_direction_coverage_never_overrides_selected_account_scope():
    from samchat.cashflow.service import _account_key

    data = sources(
        bank_rows=[bank(bank_account_id="bank-account-a")],
        budget_plan=[
            dict(year=2026, month=1, currency="MXN", direction="income", amount="1000")
        ],
    )
    data["source_status"]["budget"]["direction_complete"] = {
        "income": True,
        "expense": True,
    }
    result = report(data, account=_account_key("1234567890123456"))
    assert result["summary"]["actual_cash_in"] == 100
    assert result["summary"]["planned_budget_income"] is None
    assert result["summary"]["planned_budget_expense"] is None
    assert not any(result["source_status"]["budget"]["direction_complete"].values())


def test_direction_coverage_never_overrides_missing_budget_currency():
    data = sources(
        budget_plan=[
            dict(year=2026, month=1, currency=None, direction="income", amount="1000")
        ]
    )
    data["source_status"]["budget"]["direction_complete"] = {
        "income": True,
        "expense": True,
    }
    result = report(data, currency="MXN")
    assert result["summary"]["planned_budget_income"] is None
    assert result["summary"]["planned_budget_expense"] is None
