"""Canonical budgets must preserve fiscal gaps and cross-source identity."""

from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

import devnous.gastos  # noqa: F401 - initialize the production import order first
from samchat.budgets import service as budgets


class Result:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def first(self):
        return self.rows[0] if self.rows else None


class Session:
    def __init__(self, state="frozen", missing=False):
        self.writes = []
        self.state = state
        self.missing = missing

    async def execute(self, statement, params=None):
        if str(statement).lstrip().startswith("INSERT"):
            self.writes.append(params)
            return Result([])
        return Result(
            []
            if self.missing
            else [
                {
                    "tournament_id": "t",
                    "edition_year": 2026,
                    "version_status": self.state,
                }
            ]
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["approved", "frozen"])
async def test_operational_assignment_needs_direct_source_proof(monkeypatch, state):
    from devnous.gastos.services import amex_direct_budget_service as direct

    proof = AsyncMock(return_value=({"expense_id": "e", "concept_key": "c"}, True))
    monkeypatch.setattr(direct, "budget_assignment_source", proof)
    ddl = AsyncMock(side_effect=AssertionError("runtime DDL forbidden"))
    monkeypatch.setattr(budgets, "ensure_budget_schema", ddl)
    session = Session(state)
    await budgets.assign_budget_movement_to_line(
        session,
        budget_version_id="v",
        budget_concept_id="c",
        budget_line_id="l",
        movement_key="expense:e",
        actor_empleado_id="real-actor",
        ensure_schema=False,
        source_kind="amex_direct",
    )
    assert len(session.writes) == 1
    assert proof.await_args.kwargs["actor_empleado_id"] == "real-actor"
    assert proof.await_args.kwargs["version_status"] == state
    ddl.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "proof",
    [
        ({"expense_id": "e", "concept_key": "c"}, False),
        ({"expense_id": "other", "concept_key": "c"}, True),
        ({"expense_id": "e", "concept_key": "other"}, True),
    ],
)
async def test_unapproved_or_stale_source_never_assigns(monkeypatch, proof):
    from devnous.gastos.services import amex_direct_budget_service as direct

    monkeypatch.setattr(
        direct, "budget_assignment_source", AsyncMock(return_value=proof)
    )
    session = Session()
    with pytest.raises(ValueError):
        await budgets.assign_budget_movement_to_line(
            session,
            budget_version_id="v",
            budget_concept_id="c",
            budget_line_id="l",
            movement_key="expense:e",
            actor_empleado_id="actor",
            ensure_schema=False,
            source_kind="amex_direct",
        )
    assert session.writes == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source,ensure", [("unsupported", False), ("amex_direct", True)]
)
async def test_source_cannot_enable_runtime_ddl(source, ensure):
    session = Session()
    with pytest.raises(ValueError):
        await budgets.assign_budget_movement_to_line(
            session,
            budget_version_id="v",
            budget_concept_id="c",
            budget_line_id="l",
            movement_key="expense:e",
            actor_empleado_id="actor",
            ensure_schema=ensure,
            source_kind=source,
        )
    assert session.writes == []


async def project(
    monkeypatch,
    rows,
    *,
    existing=None,
    tournament_id="t",
    requested=False,
    aliases=None,
):
    from devnous.gastos.services import amex_direct_budget_service as direct

    monkeypatch.setattr(
        direct, "list_budget_direct_movements", AsyncMock(return_value=rows)
    )
    movements = list(existing or [])
    store = budgets._monthly_actual_store()
    await budgets._append_direct_budget_movements(
        Session(),
        edition_year=2026,
        version_id="v",
        tournament_id=tournament_id,
        tournament_scope_requested=requested,
        aliases=aliases or [],
        phase_filter=None,
        movements=movements,
        store=store,
    )
    return movements, store


def movement(**changes):
    return {
        "expense_id": "e",
        "concept_key": "c",
        "kind": "commitment",
        "movement_date": date(2026, 10, 15),
        "tournament_id": "t",
        "amount": Decimal("198.26"),
        **changes,
    }


@pytest.mark.asyncio
async def test_projection_counts_fiscal_base_once_before_posting(monkeypatch):
    rows, store = await project(monkeypatch, [movement(), movement()])
    assert len(rows) == 1
    assert sum(v["committed_unpaid"] for v in store["c"].values()) == 198.26
    assert sum(v["real_expense_cash"] for v in store["c"].values()) == 0
    rows, store = await project(
        monkeypatch,
        [movement()],
        existing=[
            {"expense_id": "e", "kind": "ledger_expense"},
        ],
    )
    assert len(rows) == 1
    assert not store


@pytest.mark.asyncio
async def test_missing_fiscal_base_is_visible_and_never_zero(monkeypatch):
    rows, store = await project(
        monkeypatch,
        [
            movement(
                kind="pending_fiscal_evidence",
                amount=None,
            )
        ],
    )
    assert rows[0]["amount"] is None
    assert not store


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"movement_date": None},
        {"movement_date": date(2025, 10, 15)},
        {"kind": "ledger_expense"},
        {"amount": None},
        {"amount": Decimal("-1")},
        {"kind": "pending_fiscal_evidence", "amount": Decimal("0")},
        {"concept_key": ""},
    ],
)
async def test_invalid_projection_fails_instead_of_inventing_data(monkeypatch, changes):
    with pytest.raises(ValueError):
        await project(monkeypatch, [movement(**changes)])


@pytest.mark.asyncio
async def test_projection_cannot_escape_requested_project(monkeypatch):
    rows, store = await project(monkeypatch, [movement(tournament_id="other")])
    assert rows == [] and not store
    rows, store = await project(
        monkeypatch,
        [movement(tournament_code="CCA")],
        tournament_id=None,
        requested=True,
        aliases=["LTTB"],
    )
    assert rows == [] and not store
    rows, store = await project(
        monkeypatch,
        [movement(tournament_code="LTTB")],
        tournament_id=None,
        requested=True,
        aliases=["LTTB"],
    )
    assert len(rows) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "marker", [{"source_kind": "amex_direct"}, {"origen": "amex_direct"}]
)
async def test_legacy_budget_assignment_cannot_bypass_the_direct_owner(
    monkeypatch, marker
):
    monkeypatch.setattr(
        budgets,
        "build_budget_actuals_snapshot",
        AsyncMock(
            return_value={
                "movements": [{"expense_id": "e", "concept_key": "c", **marker}],
            }
        ),
    )
    session = Session("draft")
    with pytest.raises(ValueError, match="autoridad presupuestal"):
        await budgets.assign_budget_movement_to_line(
            session,
            budget_version_id="v",
            budget_concept_id="c",
            budget_line_id="l",
            movement_key="expense:e",
            actor_empleado_id="actor",
            ensure_schema=False,
        )
    assert not session.writes


@pytest.mark.asyncio
async def test_budget_version_change_while_source_lock_waits_never_assigns(monkeypatch):
    from devnous.gastos.services import amex_direct_budget_service as direct

    monkeypatch.setattr(
        direct,
        "budget_assignment_source",
        AsyncMock(
            return_value=(
                {"expense_id": "e", "concept_key": "c"},
                True,
            )
        ),
    )

    class ChangedSession(Session):
        async def execute(self, statement, params=None):
            if "FOR UPDATE OF l,v" in str(statement):
                return Result(
                    [
                        {
                            "tournament_id": "t",
                            "edition_year": 2026,
                            "version_status": "closed",
                        }
                    ]
                )
            return await super().execute(statement, params)

    session = ChangedSession()
    with pytest.raises(ValueError, match="cambió"):
        await budgets.assign_budget_movement_to_line(
            session,
            budget_version_id="v",
            budget_concept_id="c",
            budget_line_id="l",
            movement_key="expense:e",
            actor_empleado_id="actor",
            ensure_schema=False,
            source_kind="amex_direct",
        )
    assert not session.writes


@pytest.mark.asyncio
@pytest.mark.parametrize("current_pointer", [None, "retired", "current"])
async def test_direct_assignment_history_cannot_be_reused_as_current(current_pointer):
    class ReadSession:
        async def execute(self, statement, params=None):
            result = Result([])
            if "FROM budget_movement_assignments" in str(statement):
                result.rows = [
                    {
                        "assignment_id": "current",
                        "movement_key": "expense:e",
                        "budget_concept_id": "c",
                        "budget_line_id": "l",
                    }
                ]
            else:
                result.rows = [{"id": "l", "budget_concept_id": "c"}]
            result.all = lambda: result.rows
            return result

    row = {
        "expense_id": "e",
        "concept_key": "c",
        "source_kind": "amex_direct",
        "budget_assignment_id": current_pointer,
        "kind": "commitment",
        "month_number": 1,
        "amount": 100,
    }
    store = budgets._monthly_actual_store()
    await budgets._apply_explicit_movement_assignments(
        ReadSession(),
        budget_version_id="v",
        movements=[row],
        store=store,
    )
    assert row["budget_line_id"] == ("l" if current_pointer == "current" else None)
    assert row["reconciliation_status"] == (
        "assigned" if current_pointer == "current" else "unassigned"
    )
    key = "l" if current_pointer == "current" else "__unassigned__"
    assert store[key][1]["committed_unpaid"] == 100
