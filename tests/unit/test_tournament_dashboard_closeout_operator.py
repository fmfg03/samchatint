"""Acceptance-focused guards and exact-diff checks for the closeout operator."""

import copy
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "closeout_operator", ROOT / "scripts/repair_tournament_dashboard_closeout.py"
)
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)
service = cli.service


@pytest.mark.parametrize(
    "sql",
    [
        "CREATE TABLE surprise(id int)",
        "DELETE FROM budget_lines",
        "UPDATE documentos SET estado='pagado'",
        "INSERT INTO accounting_polizas(id) VALUES(1)",
        "SELECT 1; UPDATE budget_lines SET budget_amount=0",
        "CALL repair()",
        "DO $$ BEGIN END $$",
        "WITH changed AS (UPDATE documentos SET estado='pagado' RETURNING *) SELECT * FROM changed",
    ],
)
def test_rejects_ddl_paid_policy_or_compound_writes(sql):
    with pytest.raises(ValueError):
        cli.sql_guard(sql, True)


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE budget_lines SET account_code_final=:code",
        "INSERT INTO budget_version_audit_log(id) VALUES(:id)",
        "SELECT * FROM budget_lines FOR UPDATE",
    ],
)
def test_preview_is_readonly_even_for_allowed_owner_writes(sql):
    with pytest.raises(ValueError):
        cli.sql_guard(sql, False)


def baseline():
    state = {
        table: []
        for table in (
            *cli.PROTECTED,
            "budget_versions",
            "budget_lines",
            "budget_movement_assignments",
            "budget_tournament_weekly_revisions",
            "budget_tournament_weekly_lines",
            "budget_approvals",
            "budget_version_audit_log",
        )
    }
    state["budget_versions"] = [
        {
            "id": cli.VERSION,
            "status": "approved",
            "notes": "Borrador",
            "updated_at": "before",
        }
    ]
    state["budget_lines"] = [
        {
            "id": str(i),
            "account_code_final": None,
            "budget_amount": "100.00",
            "phase": "Fase",
            "budget_concept_id": "retired",
            "variance_amount": "100.00",
            "updated_at": "before",
        }
        for i in range(40)
    ]
    state["documentos"] = [{"id": "paid", "estado": "pagado"}]
    data = {
        "accounts": [
            {"id": str(i), "proposed_account": "5300-012-009"} for i in range(40)
        ],
        "movements": [],
    }
    after = copy.deepcopy(state)
    for row in after["budget_lines"]:
        row.update(account_code_final="5300-012-009", updated_at="after")
    return state, after, data


def test_exact_forty_account_fields_only_and_retired_identity_preserved():
    before, after, data = baseline()
    cli.preservation(before, after, data)
    assert (
        before["budget_lines"][0]["budget_concept_id"]
        == after["budget_lines"][0]["budget_concept_id"]
    )


@pytest.mark.parametrize(
    "table,key,value",
    [
        ("budget_lines", "phase", "Other"),
        ("budget_lines", "budget_amount", "0"),
        ("budget_lines", "variance_amount", "0"),
        ("documentos", "estado", "rechazado"),
        ("budget_versions", "notes", ""),
        ("budget_versions", "status", "reforecast"),
    ],
)
def test_preservation_rejects_business_changes(table, key, value):
    before, after, data = baseline()
    after[table][0][key] = value
    with pytest.raises(ValueError):
        cli.preservation(before, after, data)


def test_pins_originals_and_approval_bytes(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "ARTIFACTS", tmp_path)
    monkeypatch.setattr(cli, "PINS", {"approval.json": "0" * 64})
    (tmp_path / "approval.json").write_text("{}")
    with pytest.raises(ValueError, match="evidence changed"):
        cli.inputs()


def test_restore_has_fixed_database_and_private_socket():
    assert cli.RESTORE_DB == "samchat_dashboard_closeout_restore_20261030"
    assert (
        "host=/tmp/samchat-dashboard-closeout-restore-proxy-20261030" in cli.RESTORE_URL
    )
    assert not any(a.dest == "database_url" for a in cli.parser()._actions)


@pytest.mark.asyncio
async def test_idempotent_owner_path_performs_no_calls(monkeypatch):
    owner = AsyncMock()
    monkeypatch.setattr(cli.service, "transition_budget_version", owner)
    result = await cli.apply_owners(None, {"completed": True}, {})
    assert result == {"idempotent": True, "revisions": []}
    owner.assert_not_awaited()


@pytest.mark.asyncio
async def test_owner_failure_keeps_approval_guard_and_order(monkeypatch):
    calls = []

    async def deactivate(*args, **kw):
        calls.append(("deactivate", kw["tournament_id"]))

    async def transition(*args, **kw):
        calls.append((kw["new_status"], kw["ensure_schema"]))
        if kw["new_status"] == "approved":
            raise ValueError("legacy schedule mismatch")

    async def update(*args, **kw):
        assert set(kw["updates"]) == {"account_code_final"}
        assert kw["ensure_schema"] is False
        calls.append(("line", kw["line_id"]))

    monkeypatch.setattr(cli.overlay, "deactivate_tournament_overlay", deactivate)
    monkeypatch.setattr(cli.service, "transition_budget_version", transition)
    monkeypatch.setattr(cli.service, "update_budget_line", update)
    monkeypatch.setattr(
        cli.service, "confirm_tournament_closeout_decisions", AsyncMock()
    )
    data = {
        "accounts": [
            {"id": "one", "proposed_account": "5300", "tournament_id": cli.LIGA}
        ],
        "movements": [],
    }
    plan = {
        "completed": False,
        "before": {
            "budget_tournament_weekly_revisions": [
                {
                    "tournament_id": tid,
                    "id": tid,
                    "plan_hash": "fixed",
                    "is_current": True,
                }
                for tid in (cli.LIGA, cli.HWC)
            ]
        },
    }
    with pytest.raises(ValueError, match="legacy schedule mismatch"):
        await cli.apply_owners(None, plan, data)
    assert calls == [
        ("deactivate", cli.LIGA),
        ("deactivate", cli.HWC),
        ("reforecast", False),
        ("line", "one"),
        ("approved", False),
    ]


@pytest.mark.asyncio
async def test_no_confirmation_no_concept_mismatch_admission():
    session = SimpleNamespace(
        execute=AsyncMock(
            return_value=SimpleNamespace(
                scalars=lambda: SimpleNamespace(all=lambda: [])
            )
        )
    )
    result = await service._closeout_assignment_proofs(
        session,
        cli.VERSION,
        [{"accounting_line_id": "source", "concept_key": "retired"}],
    )
    assert result == {}


@pytest.mark.asyncio
async def test_typed_confirmation_does_not_accept_other_actor():
    with pytest.raises(PermissionError):
        await service.confirm_tournament_closeout_decisions(
            None, actor_empleado_id="other"
        )


def test_reviewed_batch_has_exact_16_destinations_and_40_accounts():
    approved = service._closeout_actions()
    assert len(approved["accounts"]) == 40
    assert len(approved["movements"]) == 16
    assert len({d["movement_key"] for d in approved["movements"]}) == 16
    assert sum(d["tournament_id"] == cli.LIGA for d in approved["movements"]) == 3
    assert sum(d["tournament_id"] == cli.COPA for d in approved["movements"]) == 13
    assert {
        d["target_line_id"]
        for d in approved["movements"]
        if d["tournament_id"] == cli.LIGA
    } == {"a68157c6-05d5-40cc-b709-bab007f39495"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tamper", ["none", "policy", "movement", "destination", "actions", "workbook"]
)
async def test_persisted_proof_admits_only_identical_source_and_destination(
    monkeypatch, tamper
):
    approved = service._closeout_actions()
    decision = approved["movements"][0]
    movement = {
        "accounting_line_id": decision["movement_key"].split(":")[1],
        "concept_key": decision["source_concept_id"],
        "amount": float(decision["amount"]),
        "effective_phase": decision["effective_phase"],
        "document_id": decision["document_id"],
        "cuenta_codigo": decision["source_account_code"],
        "kind": "ledger_expense",
    }
    factual = {"policy": {"id": "immutable"}, "all_lines": [1, 2]}
    destination = {
        "line_id": decision["target_line_id"],
        "concept_id": decision["target_concept_id"],
        "phase": decision["effective_phase"],
    }
    proof = {
        "movement_key": decision["movement_key"],
        "movement": service._closeout_movement_identity(movement),
        "source_sha256": service._closeout_hash(factual),
        "destination": copy.deepcopy(destination),
    }
    audit = {
        "actions_sha256": service._CLOSEOUT_ACTIONS_SHA256,
        "workbook_sha256": approved["workbook_sha256"],
        "proofs": [proof],
    }
    if tamper == "policy":
        factual["all_lines"].append(3)
    elif tamper == "movement":
        movement["amount"] += 1
    elif tamper == "destination":
        destination["concept_id"] = "other"
    elif tamper == "actions":
        audit["actions_sha256"] = "0" * 64
    elif tamper == "workbook":
        audit["workbook_sha256"] = "0" * 64
    session = SimpleNamespace(
        execute=AsyncMock(
            return_value=SimpleNamespace(
                scalars=lambda: SimpleNamespace(all=lambda: [audit])
            )
        )
    )
    monkeypatch.setattr(
        service, "_closeout_destination", AsyncMock(return_value=destination)
    )
    monkeypatch.setattr(service, "_closeout_source", AsyncMock(return_value=factual))
    result = await service._closeout_assignment_proofs(session, cli.VERSION, [movement])
    assert result == (
        {decision["movement_key"]: decision["target_line_id"]}
        if tamper == "none"
        else {}
    )


@pytest.mark.asyncio
async def test_preview_exposes_legacy_mismatch_hidden_by_current_overlay(monkeypatch):
    line = {
        "id": "line",
        "budget_version_id": cli.VERSION,
        "tournament_id": cli.LIGA,
        "line_direction": "expense",
        "budget_amount": "100.00",
        "concept_name": "Transport",
        "phase": "Fase nacional",
    }
    raw = {"line": {1: {"budget_expense_amount": 0, "expected_income_amount": 0}}}
    current = {
        "line": {40: {"budget_expense_amount": 100, "expected_income_amount": 0}}
    }
    monkeypatch.setattr(
        cli.service, "list_monthly_plan_for_lines", AsyncMock(return_value=raw)
    )
    monkeypatch.setattr(
        cli.overlay, "effective_plans_for_lines", AsyncMock(return_value=(current, {}))
    )
    blockers = await cli.approval_blockers(None, {"budget_lines": [line]})
    assert len(blockers) == 1
    assert blockers[0]["schedule"] == {
        "status": "mismatch",
        "authorized": "100.00",
        "scheduled": "0",
    }


def legacy_state():
    legacy = cli.legacy_plan()
    state = {
        "budget_lines": [],
        "budget_tournament_weekly_lines": [],
        "budget_line_monthly_plan": [],
        "budget_line_monthly_allocations": [],
    }
    for row in legacy["lines"]:
        state["budget_lines"].append(
            {
                "id": row["line_id"],
                "budget_version_id": cli.VERSION,
                "tournament_id": cli.LIGA,
                "budget_concept_id": row["concept_id"],
                "phase": row["phase"],
                "budget_amount": row["authorized_amount"],
            }
        )
        state["budget_tournament_weekly_lines"].append(
            row["effective_weekly_line_before"]
        )
        state["budget_line_monthly_plan"].extend(row["before_plan_rows"])
        state["budget_line_monthly_allocations"].extend(row["before_allocation_rows"])
    return copy.deepcopy(state), copy.deepcopy(legacy)


def test_exact_five_regularization_preserves_applied_week40_and_income():
    state, legacy = legacy_state()
    cli.validate_legacy_source(state, legacy, False)
    assert sum(row["authorized_amount"] for row in legacy["lines"]) == 248500
    assert len(state["budget_line_monthly_plan"]) == 260
    assert len(state["budget_line_monthly_allocations"]) == 260


@pytest.mark.parametrize(
    "tamper", ["sixth", "week", "income", "source", "phase", "history"]
)
def test_five_calendar_plan_rejects_scope_economic_or_source_drift(tamper):
    state, legacy = legacy_state()
    if tamper == "sixth":
        legacy["lines"].append(copy.deepcopy(legacy["lines"][0]))
    elif tamper == "week":
        legacy["lines"][0]["after_plan"]["41"]["budget_expense_amount"] = 100
    elif tamper == "income":
        legacy["lines"][0]["after_plan"]["40"]["expected_income_amount"] = 1
    elif tamper == "source":
        state["budget_line_monthly_plan"][0]["metadata"]["changed"] = True
    elif tamper == "phase":
        state["budget_lines"][0]["phase"] = "Other"
    else:
        state["budget_tournament_weekly_lines"][0]["week_number"] = 41
    with pytest.raises(ValueError):
        cli.validate_legacy_source(state, legacy, False)


@pytest.mark.parametrize(
    "line,scoped,applying",
    [("wrong", True, True), ("approved", False, True), ("approved", True, False)],
)
def test_calendar_delete_requires_exact_line_owner_scope_and_apply(
    line, scoped, applying
):
    target = next(iter(cli.LEGACY_IDS))
    with pytest.raises(ValueError):
        cli.sql_guard(
            "DELETE FROM budget_line_monthly_plan WHERE budget_line_id = $1",
            applying,
            budget_line_id=target if line == "approved" else line,
            legacy_line=target if scoped else None,
        )


def test_calendar_owner_delete_has_no_generic_table_or_uuid_permission():
    target = next(iter(cli.LEGACY_IDS))
    cli.sql_guard(
        "DELETE FROM budget_line_monthly_plan WHERE budget_line_id = $1",
        True,
        budget_line_id=target,
        legacy_line=target,
    )
    cli.sql_guard(
        "DELETE FROM budget_line_monthly_allocations WHERE budget_line_id = $1",
        True,
        budget_line_id=target,
        legacy_line=target,
    )
    for sql in (
        "DELETE FROM budget_lines WHERE budget_line_id = $1",
        "DELETE FROM budget_line_monthly_plan",
        "DELETE FROM budget_line_monthly_plan WHERE budget_line_id=$1 OR TRUE",
    ):
        with pytest.raises(ValueError):
            cli.sql_guard(sql, True, budget_line_id=target, legacy_line=target)
