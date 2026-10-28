"""Explicit schema opt-out preserves the normal transition and audit guards."""

from unittest.mock import AsyncMock, MagicMock

import pytest

import devnous.gastos  # Initialize the application model registry before budgets.

from samchat.budgets import distribution, service


@pytest.fixture
def transition(monkeypatch):
    session = AsyncMock()
    result = MagicMock()
    result.mappings.return_value.first.return_value = {
        "id": "version",
        "status": "approved",
        "source": "operational_singleton",
        "edition_year": 2026,
    }
    session.execute.return_value = result
    schema = AsyncMock()
    versions = AsyncMock(return_value=[{"id": "version", "status": "reforecast"}])
    audit = AsyncMock()
    guard = AsyncMock()
    monkeypatch.setattr(service, "ensure_budget_schema", schema)
    monkeypatch.setattr(service, "list_budget_versions", versions)
    monkeypatch.setattr(service, "_audit_budget_event", audit)
    monkeypatch.setattr(distribution, "guard_source_transition", guard)
    return session, schema, versions, audit, guard


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit", [None, True, False])
@pytest.mark.parametrize("target", ["approved", "reforecast"])
async def test_schema_mode_propagates_on_noop_and_transition(
    transition, explicit, target
):
    session, schema, versions, audit, guard = transition
    kwargs = {} if explicit is None else {"ensure_schema": explicit}
    await service.transition_budget_version(
        session,
        version_id="version",
        new_status=target,
        actor_empleado_id="actor",
        **kwargs,
    )
    enabled = explicit is not False
    assert schema.await_count == int(enabled)
    versions.assert_awaited_once_with(session, ensure_schema=enabled)
    if target == "reforecast":
        guard.assert_awaited_once_with(session, "version", target)
        audit.assert_awaited_once()
        assert audit.await_args.kwargs["actor_empleado_id"] == "actor"
        session.commit.assert_awaited_once()
    else:
        audit.assert_not_awaited()
        session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_schema_keeps_financial_history_guard(transition):
    session, schema, versions, audit, guard = transition
    guard.side_effect = ValueError("historical source")
    with pytest.raises(ValueError, match="historical source"):
        await service.transition_budget_version(
            session,
            version_id="version",
            new_status="reforecast",
            ensure_schema=False,
        )
    schema.assert_not_awaited()
    audit.assert_not_awaited()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_schema_keeps_invalid_transition_guard(transition):
    session, schema, versions, audit, guard = transition
    with pytest.raises(ValueError, match="Invalid budget status transition"):
        await service.transition_budget_version(
            session,
            version_id="version",
            new_status="draft",
            ensure_schema=False,
        )
    schema.assert_not_awaited()
    guard.assert_not_awaited()
    audit.assert_not_awaited()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["valid_overlay", "raw_mismatch", "conflict"])
async def test_approval_checks_effective_schedules_and_blocks_conflicts(
    transition, monkeypatch, mode
):
    from samchat.budgets import tournament_weekly_overlay as overlay

    session, schema, versions, audit, guard = transition
    current = MagicMock()
    current.mappings.return_value.first.return_value = {
        "id": "version",
        "status": "reforecast",
        "source": "operational_singleton",
        "edition_year": 2026,
    }
    lines = MagicMock()
    lines.mappings.return_value.all.return_value = [
        {"id": "line", "budget_amount": 100, "line_direction": "expense"}
    ]
    session.execute.side_effect = [current, lines, MagicMock(), MagicMock()]
    raw = {"line": {52: {"budget_expense_amount": 0, "expected_income_amount": 0}}}
    monkeypatch.setattr(
        service, "list_monthly_plan_for_lines", AsyncMock(return_value=raw)
    )
    effective = (
        raw
        if mode == "raw_mismatch"
        else {"line": {52: {"budget_expense_amount": 100, "expected_income_amount": 0}}}
    )
    reader = AsyncMock(
        return_value=(effective, {"torneo": "conflict"} if mode == "conflict" else {})
    )
    monkeypatch.setattr(overlay, "effective_plans_for_lines", reader)
    if mode == "valid_overlay":
        await service.transition_budget_version(
            session, version_id="version", new_status="approved", ensure_schema=False
        )
        audit.assert_awaited_once()
        session.commit.assert_awaited_once()
    else:
        with pytest.raises(ValueError):
            await service.transition_budget_version(
                session,
                version_id="version",
                new_status="approved",
                ensure_schema=False,
            )
        audit.assert_not_awaited()
        session.commit.assert_not_awaited()
    reader.assert_awaited_once_with(
        session, version_id="version", line_ids=["line"], baseline_plans=raw
    )
    assert raw["line"][52]["budget_expense_amount"] == 0
