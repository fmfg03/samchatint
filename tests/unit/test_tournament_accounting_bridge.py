from unittest.mock import AsyncMock

import pytest

from samchat.client_executive import accounting_bridge as bridge
from samchat.client_executive import ui


def concept(mode="exclusive_account", kind="expense", code="5300-001"):
    return {
        "tournament_id": "t",
        "concept_name": "Hospedaje",
        "account_code": code,
        "metadata": {
            "historical_accounting_binding": {
                "approved": True,
                "evidence_ref": "review-2026",
                "fiscal_year": 2026,
                "company_code": "01",
                "attribution": mode,
                "metric_kind": kind,
                "currency": "MXN",
                "tax_basis": "includes_vat",
                "project_raw": "t-project",
            }
        },
    }


class Result:
    def __init__(self, rows=(), scalar=None):
        self.rows, self.scalar = rows, scalar

    def mappings(self):
        return self

    def all(self):
        return self.rows

    def scalar_one(self):
        return self.scalar


class Session:
    def __init__(self, concepts=None, rows=None, shared=False, ready=True):
        self.concepts = concepts if concepts is not None else [concept()]
        self.rows = (
            rows
            if rows is not None
            else [
                {
                    "fiscal_month": m,
                    "debits": 10,
                    "credits": 2,
                    "closing_balance": m * 8,
                }
                for m in [1, 2, 3]
            ]
        )
        self.shared, self.ready = shared, ready
        self.calls = []

    async def execute(self, sql, params=None):
        sql = str(sql)
        self.calls.append((sql, params or {}))
        assert sql.lstrip().startswith("SELECT")
        if "LEFT JOIN cuentas_contables" in sql:
            return Result(self.concepts)
        if "to_regclass" in sql:
            return Result(scalar=self.ready)
        if "SELECT EXISTS" in sql:
            return Result(scalar=self.shared)
        if (
            "historical_trial_balance_rows" in sql
            or "historical_policy_lines pl" in sql
        ):
            assert params["account"] in {"5300-001", "4000-001"}
            assert params["company"] == "01"
            assert params["year"] == 2026
            return Result(self.rows)
        raise AssertionError(sql)


@pytest.fixture(autouse=True)
def latest_run(monkeypatch):
    monkeypatch.setattr(
        bridge,
        "_find_latest_applied_import_run",
        AsyncMock(return_value={"id": "run", "source_sha256": "hash"}),
    )


@pytest.mark.asyncio
async def test_partial_coverage_net_expense_and_last_balance_not_sum():
    session = Session()
    result = await bridge.build_tournament_accounting(
        session, tournament_id="t", edition_year=2026
    )
    assert result["expense"] == "24"
    assert result["income"] is None
    assert result["source_status"] == "partial"
    assert result["coverage_months"] == [1, 2, 3]
    assert result["accounts"][0]["closing_balance"] == "24"
    assert result["accounts"][0]["source_sha256"] == "hash"
    assert any("is_detail = TRUE" in sql for sql, _ in session.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "session",
    [
        Session(concepts=[]),
        Session(shared=True),
        Session(ready=False),
        Session(rows=[]),
    ],
)
async def test_missing_or_shared_sources_never_become_zero(session):
    result = await bridge.build_tournament_accounting(
        session, tournament_id="t", edition_year=2026
    )
    assert result["source_status"] == "unavailable"
    assert result["expense"] is None
    assert result["income"] is None


@pytest.mark.asyncio
async def test_foreign_tournament_never_reads_historical_amounts():
    foreign = concept()
    foreign["tournament_id"] = "foreign"
    session = Session(concepts=[foreign])
    with pytest.raises(PermissionError):
        await bridge.build_tournament_accounting(
            session, tournament_id="t", edition_year=2026
        )
    assert len(session.calls) == 1


@pytest.mark.asyncio
async def test_project_binding_limits_policy_reads_and_no_balance():
    session = Session(concepts=[concept("project")])
    result = await bridge.build_tournament_accounting(
        session, tournament_id="t", edition_year=2026
    )
    assert result["accounts"][0]["closing_balance"] is None
    sql, args = session.calls[-1]
    assert "pl.project_raw = :scope" in sql
    assert "ph.is_balanced = TRUE" in sql
    assert args["scope"] == "t-project"


@pytest.mark.asyncio
async def test_unapproved_or_old_edition_never_uses_current_metrics():
    c = concept()
    c["metadata"]["historical_accounting_binding"]["fiscal_year"] = 2025
    session = Session(concepts=[c])
    result = await bridge.build_tournament_accounting(
        session, tournament_id="t", edition_year=2026
    )
    assert result["expense"] is None
    assert len(session.calls) == 1


@pytest.mark.asyncio
async def test_repeated_bindings_are_counted_once_and_overlap_blocked():
    session = Session(concepts=[concept(), concept()])
    assert (
        await bridge.build_tournament_accounting(
            session, tournament_id="t", edition_year=2026
        )
    )["expense"] == "24"
    session = Session(concepts=[concept(), concept("project")])
    assert (
        await bridge.build_tournament_accounting(
            session, tournament_id="t", edition_year=2026
        )
    )["expense"] is None


@pytest.mark.asyncio
async def test_duplicate_account_month_is_not_summed():
    rows = [{"fiscal_month": 1, "debits": 10, "credits": 0, "closing_balance": 10}] * 2
    result = await bridge.build_tournament_accounting(
        Session(rows=rows), tournament_id="t", edition_year=2026
    )
    assert result["expense"] is None


def test_ui_keeps_all_budget_lines_and_escapes_accounting():
    card = {
        "budget_breakdowns": {
            "by_concept": [
                {"label": f"line-{i}", "budget_total": 1} for i in range(65)
            ],
            "by_phase": [{"label": "Estatal", "budget_total": 1}],
        },
        "accounting": bridge.unavailable("<script>"),
    }
    assert "line-64" in ui._budget_detail(card)
    assert "Fases y subproyectos" in ui._budget_detail(card)
    assert "<script>" not in ui._accounting_detail(card)
    assert "&lt;script&gt;" in ui._accounting_detail(card)


@pytest.mark.asyncio
async def test_income_nature_and_missing_expense_stay_distinct():
    session = Session(concepts=[concept(kind="income", code="4000-001")])
    result = await bridge.build_tournament_accounting(
        session, tournament_id="t", edition_year=2026
    )
    assert result["income"] == "-24"
    assert result["expense"] is None
    assert all(p["expense"] is None for p in result["periods"])


@pytest.mark.asyncio
async def test_missing_applied_import_remains_unavailable(monkeypatch):
    monkeypatch.setattr(
        bridge, "_find_latest_applied_import_run", AsyncMock(return_value=None)
    )
    result = await bridge.build_tournament_accounting(
        Session(), tournament_id="t", edition_year=2026
    )
    assert result["expense"] is None


@pytest.mark.asyncio
async def test_conflicting_same_account_kind_is_blocked():
    session = Session(concepts=[concept(), concept(kind="income")])
    result = await bridge.build_tournament_accounting(
        session, tournament_id="t", edition_year=2026
    )
    assert result["expense"] is None


@pytest.mark.asyncio
async def test_comparison_requires_full_matching_tax_and_currency():
    class BudgetSession(Session):
        async def execute(self, sql, params=None):
            if "FROM budget_lines l JOIN budget_versions" in str(sql):
                assert params["tournament_id"] == "t" and params["version_id"] == "v"
                return Result(
                    [
                        {
                            "budget_amount": "90",
                            "metadata": {
                                "upload_source": {
                                    "currency": "MXN",
                                    "tax_basis": "includes_vat",
                                }
                            },
                        }
                    ]
                )
            return await super().execute(sql, params)

    rows = [
        {"fiscal_month": m, "debits": 10, "credits": 2, "closing_balance": m * 8}
        for m in range(1, 13)
    ]
    result = await bridge.build_tournament_accounting(
        BudgetSession(rows=rows),
        tournament_id="t",
        edition_year=2026,
        budget_version_id="v",
    )
    assert result["comparison"]["variance"] == "6"
    partial = await bridge.build_tournament_accounting(
        Session(), tournament_id="t", edition_year=2026, budget_version_id="v"
    )
    assert partial["comparison"]["variance"] is None


@pytest.mark.asyncio
async def test_mixed_tax_basis_never_aggregates():
    c = concept(code="4000-001")
    c["metadata"]["historical_accounting_binding"]["tax_basis"] = "excludes_vat"
    result = await bridge.build_tournament_accounting(
        Session(concepts=[concept(), c]), tournament_id="t", edition_year=2026
    )
    assert result["expense"] is None
    assert result["periods"] == []


def test_accounting_ui_shows_cutoff_months_sources_and_monthly_evolution():
    card = {
        "accounting": {
            "source_status": "partial",
            "fiscal_year": 2026,
            "coverage_months": [1, 2, 3],
            "currency": "MXN",
            "tax_basis": "includes_vat",
            "accounts": [
                {
                    "account_code": "<code>",
                    "company_code": "01",
                    "metric_kind": "expense",
                    "amount": "24",
                    "closing_balance": "24",
                    "closing_month": 3,
                }
            ],
            "periods": [{"month": 1, "expense": "8", "income": None}],
        }
    }
    html = ui._accounting_detail(card)
    assert "Cobertura parcial" in html and "1, 2, 3" in html
    assert "Evolución mensual" in html and "No disponible" in html
    assert "<code>" not in html


@pytest.mark.asyncio
async def test_nonfinite_accounting_is_missing_not_a_number():
    rows = [{"fiscal_month": 1, "debits": "NaN", "credits": 0, "closing_balance": 1}]
    result = await bridge.build_tournament_accounting(
        Session(rows=rows), tournament_id="t", edition_year=2026
    )
    assert result["expense"] is None
    assert result["source_status"] == "unavailable"


def test_previous_year_is_visible_even_when_current_attribution_is_missing():
    html = ui._accounting_detail(
        {
            "accounting": bridge.unavailable("Sin fuente actual"),
            "accounting_previous_year": {
                "source_status": "partial",
                "fiscal_year": 2025,
                "coverage_months": [1],
                "accounts": [],
                "periods": [],
            },
        }
    )
    assert "Sin fuente actual" in html and "2025" in html


@pytest.mark.asyncio
async def test_months_missing_one_indicator_do_not_become_zero():
    class DifferentMonths(Session):
        async def execute(self, sql, params=None):
            result = await super().execute(sql, params)
            if "FROM historical_trial_balance_rows" in str(sql):
                month = 1 if params["account"] == "4000-001" else 2
                return Result(
                    [
                        {
                            "fiscal_month": month,
                            "debits": 10,
                            "credits": 2,
                            "closing_balance": 8,
                        }
                    ]
                )
            return result

    session = DifferentMonths(
        concepts=[concept(kind="income", code="4000-001"), concept()]
    )
    result = await bridge.build_tournament_accounting(
        session, tournament_id="t", edition_year=2026
    )
    assert result["periods"][0] == {"month": 1, "income": "-8", "expense": None}
    assert result["periods"][1] == {"month": 2, "income": None, "expense": "8"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode,ignored_field",
    [
        ("exclusive_account", "project_raw"),
        ("exclusive_account", "cost_center_raw"),
        ("project", "cost_center_raw"),
    ],
)
async def test_unused_binding_metadata_cannot_duplicate_account_amounts(
    mode, ignored_field
):
    first, second = concept(mode), concept(mode)
    first["metadata"]["historical_accounting_binding"][ignored_field] = "a"
    second["metadata"]["historical_accounting_binding"][ignored_field] = "b"
    result = await bridge.build_tournament_accounting(
        Session(concepts=[first, second]), tournament_id="t", edition_year=2026
    )
    assert result["expense"] == "24"
    assert len(result["accounts"]) == 1


def test_current_and_previous_year_both_show_their_coverage():
    html = ui._accounting_detail(
        {
            "accounting": {
                "source_status": "partial",
                "fiscal_year": 2026,
                "coverage_months": [1, 2, 3],
                "accounts": [],
                "periods": [],
            },
            "accounting_previous_year": {
                "source_status": "partial",
                "fiscal_year": 2025,
                "coverage_months": [7, 8],
                "accounts": [],
                "periods": [],
            },
        }
    )
    assert "2026" in html and "2025" in html
    assert "1, 2, 3" in html and "7, 8" in html
    assert html.count("Cobertura parcial") == 2


@pytest.mark.asyncio
async def test_dashboard_wires_exact_authorized_tournament_and_historical_edition(
    monkeypatch,
):
    from types import SimpleNamespace

    from samchat.client_executive import service

    allowed = {"id": "allowed", "name": "Torneo", "slug": "torneo"}
    monkeypatch.setattr(
        service, "_authorized_tournaments", AsyncMock(return_value=[allowed])
    )
    monkeypatch.setattr(
        service,
        "_build_direction_budget_snapshot",
        AsyncMock(
            return_value={"source": "budget_db", "version": {"id": "v"}, "summary": {}}
        ),
    )
    reader = AsyncMock(return_value=bridge.unavailable("No binding"))
    monkeypatch.setattr(service, "build_tournament_accounting", reader)
    await service.build_client_dashboard(
        object(), empleado_id="actor", edition_year=2026
    )
    assert reader.call_args_list[0].kwargs == {
        "tournament_id": "allowed",
        "edition_year": 2026,
        "budget_version_id": "v",
    }
    assert reader.call_args_list[1].kwargs == {
        "tournament_id": "allowed",
        "edition_year": 2025,
    }
    reader.reset_mock()
    with pytest.raises(service.ClientExecutiveAccessError):
        await service.build_client_dashboard(
            object(), empleado_id="actor", edition_year=2026, tournament_id="foreign"
        )
    reader.assert_not_awaited()
    session = SimpleNamespace(
        execute=AsyncMock(return_value=[SimpleNamespace(**allowed)])
    )
    await service.build_portfolio_dashboard(
        session, portfolio_id="p", edition_year=2026
    )
    assert reader.call_args_list[0].kwargs["tournament_id"] == "allowed"
    assert reader.call_args_list[1].kwargs["edition_year"] == 2025
