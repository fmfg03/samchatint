"""Documentary account facts never grant exclusive financial attribution."""

from copy import deepcopy
from decimal import Decimal
import json
from unittest.mock import AsyncMock

import pytest

# Match the live application import order for the existing gastos/budgets cycle.
import devnous.gastos  # noqa: F401
from samchat.client_executive import accounting_bridge as bridge


COPA = "67c556df-f953-4f99-8cab-87ed4d111b05"
LIGA = "16793f0e-4697-4a28-8f42-b563da8cc7d8"
HWC = "c3b247f1-e3fc-4f4a-8995-56a5880f86a6"


class Result:
    def __init__(self, rows=(), scalar=None):
        self.rows, self.scalar = rows, scalar

    def mappings(self):
        return self

    def all(self):
        return self.rows

    def scalar_one(self):
        return self.scalar


@pytest.fixture
def data():
    scope = json.loads(bridge._DOCUMENTARY_SCOPE_PATH.read_text())
    identity = {
        "import_run_id": "current-run",
        "fiscal_year": 2026,
        "company_code": "01",
        "company_label": "PSP1705058S4",
    }
    validation = {
        "balance_months": [1, 2, 5, 8],
        "policy_months": [1, 2, 5, 8, 9],
        "withheld_months": [
            {"month_num": m, "reason": "Explicitly withheld"} for m in (3, 4, 6, 7)
        ],
        "material_reconciliation_issues": [],
        "rounding_incidents": [],
        "cent_reconciliation_differences": [],
        "reconciliation_status": "reconciled_with_disclosed_precision",
    }
    sources = [
        {
            **identity,
            "id": family,
            "source_family": family,
            "source_sha256": digest,
            "validation_status": "OK",
            "metadata": validation if family == "explicit_manifest" else {},
        }
        for family, digest in {
            "balanza": "a" * 64,
            "poliza_header": "b" * 64,
            "poliza_line": "c" * 64,
            "explicit_manifest": "d" * 64,
            **scope["original_source_hashes"],
        }.items()
    ]
    balances = [
        {
            **identity,
            "source_file_id": "balanza",
            "fiscal_month": month,
            "account_code": a["account_code"],
            "is_detail": True,
            "debits": Decimal("0.00"),
            "credits": Decimal("0.00"),
            "closing_balance": Decimal("0.00"),
        }
        for a in scope["accounts"]
        for month in validation["balance_months"]
    ]
    headers, lines = [], []
    # 39 account populations, with complete counterparty lines outside the display.
    for month in validation["policy_months"]:
        for number, a in enumerate(scope["accounts"][:39]):
            pid = f"2026-{month:02d}:Dr:{number}"
            headers.append(
                {
                    **identity,
                    "id": pid,
                    "source_file_id": "poliza_header",
                    "fiscal_month": month,
                    "policy_id_natural": pid,
                    "is_balanced": True,
                    "has_summary": False,
                    "quality_flags": ["missing_summary_marker"],
                    "line_count": 2,
                    "total_debits": Decimal("12.50"),
                    "total_credits": Decimal("12.50"),
                }
            )
            for side, code in enumerate((a["account_code"], "1120-001-001")):
                lines.append(
                    {
                        **identity,
                        "id": f"{pid}-{side}",
                        "source_file_id": "poliza_line",
                        "policy_header_id": pid,
                        "policy_id_natural": pid,
                        "fiscal_month": month,
                        "account_code": code,
                        "debit_amount": (
                            Decimal("12.50") if not side else Decimal("0.00")
                        ),
                        "credit_amount": Decimal("12.50") if side else Decimal("0.00"),
                    }
                )
    return {
        "sources": sources,
        "balances": balances,
        "headers": headers,
        "lines": lines,
    }


class Session:
    def __init__(self, data, ready=True):
        self.data, self.ready, self.calls = data, ready, []

    async def execute(self, statement, params=None):
        sql = str(statement)
        self.calls.append((sql, params))
        assert sql.lstrip().startswith("SELECT")
        if "to_regclass" in sql:
            return Result(scalar=self.ready)
        assert params == {"run": "current-run", "company": "01", "year": 2026}
        assert "import_run_id=:run" in sql
        assert "fiscal_year=:year" in sql and "company_code=:company" in sql
        for table, key in (
            ("historical_accounting_source_files", "sources"),
            ("historical_trial_balance_rows", "balances"),
            ("historical_policy_headers", "headers"),
            ("historical_policy_lines", "lines"),
        ):
            if table in sql:
                return Result(self.data[key])
        raise AssertionError(sql)


@pytest.fixture(autouse=True)
def selected_run(monkeypatch):
    reader = AsyncMock(return_value={"id": "current-run", "source_sha256": "e" * 64})
    monkeypatch.setattr(bridge, "_find_latest_applied_import_run", reader)
    return reader


@pytest.mark.asyncio
async def test_all85_accounts_independent_of_active_concepts_and_39_journal_populations(
    data,
):
    accounts = []
    for tournament in (COPA, LIGA, HWC):
        result = await bridge.build_tournament_documentary_accounting(
            Session(data), tournament_id=tournament, edition_year=2026
        )
        assert result["source_status"] == "partial"
        assert result["currency"] is result["tax_basis"] is None
        assert "expense" not in result and "income" not in result
        accounts += result["accounts"]
    assert len(accounts) == 85
    assert sum(bool(a["policy_periods"]) for a in accounts) == 39
    assert sum(not a["has_active_concept"] for a in accounts) == 27
    assert sum(a["classification"] == "shared_active" for a in accounts) == 6
    assert sum(a["classification"] == "other_project_retired" for a in accounts) == 1
    assert sum(a["classification"] == "prior_year" for a in accounts) == 1
    assert all(a["exclusive_attribution"] is False for a in accounts)
    assert accounts[0]["policy_periods"][0]["quality_flags"] == [
        "missing_summary_marker"
    ]
    assert data["headers"][0]["quality_flags"] == ["missing_summary_marker"]


@pytest.mark.asyncio
async def test_explicit_balance_zero_is_distinct_from_no_journal_or_missing_period(
    data,
):
    result = await bridge.build_tournament_documentary_accounting(
        Session(data), tournament_id=HWC, edition_year=2026
    )
    account = result["accounts"][0]
    assert account["policy_periods"] == []
    assert account["trial_balance_periods"][0]["debits"] == "0.00"
    assert [r["month"] for r in account["trial_balance_periods"]] == [1, 2, 5, 8]
    assert result["coverage"]["policy_months"] == [1, 2, 5, 8, 9]
    assert len(result["coverage"]["withheld_months"]) == 4
    assert "Septiembre" in result["gaps"][1]


@pytest.mark.asyncio
@pytest.mark.parametrize("year,tournament", [(2025, COPA), (2026, "foreign")])
async def test_scope_without_approval_does_not_query_database(data, year, tournament):
    session = Session(data)
    result = await bridge.build_tournament_documentary_accounting(
        session, tournament_id=tournament, edition_year=year
    )
    assert result["source_status"] == "unavailable" and result["accounts"] == []
    assert session.calls == []


@pytest.mark.asyncio
async def test_absent_source_is_not_zero_or_previous_version(data, selected_run):
    selected_run.return_value = None
    session = Session(data)
    result = await bridge.build_tournament_documentary_accounting(
        session, tournament_id=COPA, edition_year=2026
    )
    assert result["source_status"] == "unavailable"
    assert len(session.calls) == 1
    assert all(
        a["trial_balance_periods"] == a["policy_periods"] == []
        for a in result["accounts"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "damage",
    [
        "material_flag",
        "unbalanced",
        "incomplete",
        "counterparty_changed",
        "orphan",
        "wrong_company",
        "wrong_year",
        "old_run",
        "source_fk",
        "month_withheld",
        "missing_month",
        "duplicate_balance",
        "nan",
        "source_changed",
        "source_invalid",
        "metadata_json_string",
        "material_admission",
        "cent_admission",
        "rounding_admission",
        "missing_admission",
        "wrong_source_company",
        "duplicate_source",
    ],
)
async def test_fail_closed_sources_policy_integrity_and_admission(data, damage):
    d = deepcopy(data)
    h, line, source = d["headers"][0], d["lines"][0], d["sources"][0]
    validation = next(
        s["metadata"] for s in d["sources"] if s["source_family"] == "explicit_manifest"
    )
    if damage == "material_flag":
        h["quality_flags"] += ["source_rounding_difference_one_cent"]
    elif damage == "unbalanced":
        h["is_balanced"] = False
    elif damage == "incomplete":
        d["lines"].pop()
    elif damage == "counterparty_changed":
        d["lines"][1]["credit_amount"] = 13
    elif damage == "orphan":
        line["policy_header_id"] = "missing"
    elif damage == "wrong_company":
        line["company_code"] = "02"
    elif damage == "wrong_year":
        line["fiscal_year"] = 2025
    elif damage == "old_run":
        line["import_run_id"] = "previous-run"
    elif damage == "source_fk":
        line["source_file_id"] = "other"
    elif damage == "month_withheld":
        d["balances"][0]["fiscal_month"] = 3
    elif damage == "missing_month":
        d["balances"] = [r for r in d["balances"] if r["fiscal_month"] != 2]
    elif damage == "duplicate_balance":
        d["balances"].append(d["balances"][0])
    elif damage == "nan":
        d["balances"][0]["debits"] = "NaN"
    elif damage == "source_changed":
        d["sources"][-1]["source_sha256"] = "f" * 64
    elif damage == "source_invalid":
        source["validation_status"] = "REVIEW_REQUIRED"
    elif damage == "metadata_json_string":
        next(s for s in d["sources"] if s["source_family"] == "explicit_manifest")[
            "metadata"
        ] = json.dumps(validation)
    elif damage == "material_admission":
        validation["material_reconciliation_issues"] = [{"difference": "1.00"}]
    elif damage == "cent_admission":
        validation["cent_reconciliation_differences"] = [{"difference": ".01"}]
    elif damage == "rounding_admission":
        validation["rounding_incidents"] = [{"difference": ".01"}]
    elif damage == "missing_admission":
        validation.pop("material_reconciliation_issues")
    elif damage == "wrong_source_company":
        source["company_code"] = "02"
    elif damage == "duplicate_source":
        d["sources"].append(d["sources"][0])
    result = await bridge.build_tournament_documentary_accounting(
        Session(d), tournament_id=COPA, edition_year=2026
    )
    assert result["source_status"] == "unavailable"
    assert all(
        not a["policy_periods"] and not a["trial_balance_periods"]
        for a in result["accounts"]
    )


@pytest.mark.asyncio
async def test_documentary_does_not_open_strict_financial_gates(data, monkeypatch):
    strict = bridge.unavailable("No exclusive financial binding")
    monkeypatch.setattr(
        bridge, "_build_strict_tournament_accounting", AsyncMock(return_value=strict)
    )
    result = await bridge.build_tournament_accounting(
        Session(data), tournament_id=COPA, edition_year=2026
    )
    assert result["expense"] is result["income"] is result["closing_balances"] is None
    assert result["documentary"]["source_status"] == "partial"


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", [None, "plan_sha256", "row", "budget_amount"])
async def test_strict_comparison_uses_exact_committed_copa_receipt_only(
    monkeypatch, damage
):
    from samchat.budgets import source_provenance

    registry = json.loads(source_provenance.REGISTRY_PATH.read_text())
    row = deepcopy(registry["lines"][0])
    row["metadata"] = {
        "copa_20261028": deepcopy(row.pop("marker")),
        "upload_source": {"currency": "MXN", "tax_basis": "includes_vat"},
    }
    if damage == "budget_amount":
        row["budget_amount"] = "0"
    elif damage:
        row["metadata"]["copa_20261028"][damage] = "altered"

    class StrictSession:
        async def execute(self, statement, params=None):
            sql = str(statement)
            if "LEFT JOIN cuentas_contables" in sql:
                return Result(
                    [
                        {
                            "tournament_id": COPA,
                            "concept_name": "Explicit source",
                            "account_code": "5300-010-001",
                            "metadata": {
                                "historical_accounting_binding": {
                                    "approved": True,
                                    "evidence_ref": "independent financial approval",
                                    "fiscal_year": 2026,
                                    "company_code": "01",
                                    "attribution": "exclusive_account",
                                    "metric_kind": "expense",
                                    "currency": "MXN",
                                    "tax_basis": "includes_vat",
                                }
                            },
                        }
                    ]
                )
            if "to_regclass" in sql:
                return Result(scalar=True)
            if "SELECT bc.metadata" in sql:
                return Result([])
            if "FROM historical_trial_balance_rows tb" in sql:
                return Result(
                    [
                        {
                            "fiscal_month": m,
                            "debits": 10,
                            "credits": 0,
                            "closing_balance": 10,
                        }
                        for m in range(1, 13)
                    ]
                )
            if "FROM budget_lines" in sql:
                assert all(
                    f"l.{name}" in sql
                    for name in (
                        "id",
                        "budget_version_id",
                        "tournament_id",
                        "budget_concept_id",
                        "phase",
                    )
                )
                return Result([row])
            raise AssertionError(sql)

    monkeypatch.setattr(
        bridge, "build_tournament_documentary_accounting", AsyncMock(return_value={})
    )
    result = await bridge.build_tournament_accounting(
        StrictSession(),
        tournament_id=COPA,
        edition_year=2026,
        budget_version_id=row["budget_version_id"],
    )
    assert result["expense"] == "120"
    if damage is None:
        assert Decimal(result["comparison"]["budget"]) == Decimal(
            str(row["budget_amount"])
        )
    else:
        assert result["comparison"]["budget"] is None
        assert result["comparison"]["variance"] is None


@pytest.mark.parametrize("tournament", [COPA, LIGA, HWC])
def test_executive_card_preserves_facts_and_blocks_incompatible_derived_metrics(
    tournament,
):
    from samchat.client_executive.service import _executive_card

    snapshot = {
        "source": "budget_db",
        "version": {"id": "approved", "edition_year": 2026},
        "summary": {
            "budget_total": 116,
            "actual_total": 100,
            "committed_total": 50,
            "paid_total": 30,
            "requested_total": 60,
            "pending_to_pay_total": 20,
        },
        "forecast": {"projected_close_total": 150, "remaining_budget": 16},
    }
    card = _executive_card(
        {"id": tournament, "name": "Scoped tournament"}, snapshot, edition_year=2026
    )
    assert {
        k: card[k]
        for k in (
            "budget",
            "actual",
            "committed",
            "paid",
            "requested",
            "pending_to_pay",
        )
    } == {
        "budget": 116,
        "actual": 100,
        "committed": 50,
        "paid": 30,
        "requested": 60,
        "pending_to_pay": 20,
    }
    assert card["available"] is card["projected"] is card["execution_pct"] is None
    assert card["budget_actual_comparison"]["status"] == "unavailable"
    assert "bases distintas" in card["budget_actual_comparison"]["reason"]
    # No scope expansion to another edition or tournament.
    for tid, year in ((tournament, 2025), ("other", 2026)):
        other = _executive_card(
            {"id": tid, "name": "Other"}, snapshot, edition_year=year
        )
        assert other["projected"] == 150 and other["available"] == 16
        assert "budget_actual_comparison" not in other
    assert snapshot["forecast"]["remaining_budget"] == 16
