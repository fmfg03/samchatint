"""Safety and selector coverage for the dry-run/apply repair CLI."""

import importlib.util
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

PATH = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "repair_tournament_budget_reconciliation.py"
)
SPEC = importlib.util.spec_from_file_location("repair_tournament", PATH)
repair = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(repair)


def movement(ref="144", key="hotel-posting", concept="hotel", assigned=False):
    return {
        "operation_reference": ref,
        "accounting_line_id": key,
        "document_id": "report",
        "document_reference": "I-report",
        "concept_key": concept,
        "budget_concept_name": concept,
        "kind": "commitment",
        "amount": 20,
        "effective_phase": "Estatal",
        "movement_key": f"accounting:{key}",
        "budget_line_id": "existing" if assigned else None,
        "reconciliation_status": "assigned" if assigned else "unassigned",
    }


def test_plan_keeps_concepts_separate_and_excludes_already_assigned_rows():
    planned = repair.movement_plan(
        [
            movement(),
            movement(key="food-posting", concept="food"),
            movement(key="old", assigned=True),
        ],
        {"144"},
    )
    assert len(planned) == 2
    assert {row["movement_key"] for row in planned} == {
        "accounting:hotel-posting",
        "accounting:food-posting",
    }
    assert {row["budget_concept_id"] for row in planned} == {"food", "hotel"}


@pytest.mark.parametrize(
    "case",
    ["missing_ref", "missing_concept", "missing_source", "duplicate", "accounting"],
)
def test_plan_blocks_ambiguous_or_unready_inputs(case):
    rows = [movement()]
    refs = {"144"}
    if case == "missing_ref":
        refs = {"171"}
    if case == "missing_concept":
        rows[0]["concept_key"] = "__unassigned__"
    if case == "missing_source":
        rows[0].update(accounting_line_id=None, document_id=None)
    if case == "duplicate":
        rows.append(dict(rows[0]))
    if case == "accounting":
        rows[0]["kind"] = "pending_accounting"
    with pytest.raises(ValueError):
        repair.movement_plan(rows, refs)


def test_plan_hash_changes_with_financial_state():
    plan = {"amount": 20, "state": "aprobado"}
    assert repair.plan_sha(plan) == repair.plan_sha(dict(plan))
    assert repair.plan_sha(plan) != repair.plan_sha({**plan, "amount": 21})


@pytest.mark.parametrize(
    "bad_change", [None, "wrong_line", "amount", "unselected", "missing"]
)
def test_verification_rejects_unexpected_changes(bad_change):
    before = [movement(), movement(key="unselected", assigned=True)]
    after = deepcopy(before)
    after[0].update(budget_line_id="new-hotel", reconciliation_status="assigned")
    expected = {before[0]["movement_key"]: {"budget_concept_id": "hotel"}}
    if bad_change == "wrong_line":
        after[0]["budget_line_id"] = "wrong"
    if bad_change == "amount":
        after[0]["amount"] = 21
    if bad_change == "unselected":
        after[1]["budget_line_id"] = "wrong"
    if bad_change == "missing":
        after.pop(0)
    if bad_change:
        with pytest.raises(ValueError):
            repair.verify_movements(before, after, expected, {"hotel": "new-hotel"})
    else:
        repair.verify_movements(before, after, expected, {"hotel": "new-hotel"})


@pytest.mark.asyncio
async def test_route_preview_never_executes_proposed_insert():
    session = SimpleNamespace(execute=AsyncMock(return_value="read"))
    preview = repair.RoutePreview(session)
    assert await preview.execute("SELECT route", {}) == "read"
    assert (
        await preview.execute(
            "INSERT INTO documento_authorization_routes", {"positions": "configured"}
        )
        is None
    )
    assert session.execute.await_count == 1
    assert preview.proposed == {"positions": "configured"}
    with pytest.raises(ValueError):
        await preview.execute("UPDATE documentos", {})


def args(tmp_path, apply=False):
    return SimpleNamespace(
        version_id=uuid4(),
        informe_ref="I-report",
        ops_refs=["144"],
        receipt=tmp_path / "receipt.json",
        apply=apply,
        actor_id=uuid4() if apply else None,
        expected_plan_sha256=None,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "blocked",
    [
        None,
        "version",
        "report",
        "route",
        "holders",
        "refs",
        "tournament",
        "phase",
        "concept",
        "existing",
    ],
)
async def test_build_plan_validates_all_selectors_and_tournament_scope(
    monkeypatch, tmp_path, blocked
):
    actor_doc = {
        "id": "r",
        "empleado_id": "employee",
        "beneficiario_empleado_id": None,
        "torneo_id": None,
        "cuenta_gastos_id": "a",
        "referencia_operaciones": None,
        "estado": "enviado",
        "numero_referencia": "I-report",
    }

    async def fake_rows(session, sql, params=None):
        if "FROM budget_versions" in sql:
            return (
                []
                if blocked == "version"
                else [{"edition_year": 2026, "status": "draft"}]
            )
        if "WHERE numero_referencia" in sql:
            return [] if blocked == "report" else [actor_doc]
        if "LEFT JOIN cuentas_de_gastos" in sql:
            return (
                []
                if blocked == "refs"
                else [
                    {
                        "id": "d",
                        "estado": "aprobado",
                        "torneo_id": None if blocked == "tournament" else "t",
                        "cuenta_gastos_id": "a",
                    }
                ]
            )
        if "FROM budget_lines" in sql:
            return (
                [{"id": "existing", "phase": "Other"}] if blocked == "existing" else []
            )
        raise AssertionError(sql)

    async def fake_route(preview, doc):
        preview.proposed = {
            "employee_ids": "[]" if blocked == "holders" else '["holder"]',
            "positions": '["director_operaciones"]',
            "requires_reference": True,
            "source": "project_rule",
        }
        return (
            None
            if blocked == "route"
            else SimpleNamespace(
                requires_operations_reference=True,
                eligible_position_keys=("director_operaciones",),
            )
        )

    movements = [movement()]
    if blocked == "phase":
        movements.append({**movement(key="second"), "effective_phase": "Other"})
    monkeypatch.setattr(repair, "rows", fake_rows)
    monkeypatch.setattr(repair, "resolve_and_snapshot_document_route", fake_route)
    monkeypatch.setattr(
        repair.budgets,
        "build_budget_actuals_snapshot",
        AsyncMock(return_value={"movements": movements}),
    )
    monkeypatch.setattr(
        repair.budgets,
        "resolve_budget_concept",
        AsyncMock(
            return_value=None if blocked == "concept" else {"concept_name": "hotel"}
        ),
    )
    if blocked:
        with pytest.raises(ValueError):
            await repair.build_plan(None, args(tmp_path))
    else:
        plan, _ = await repair.build_plan(None, args(tmp_path))
        assert plan["lines"][0]["create_budget_amount"] == 0
        assert plan["allocate_operations_reference"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "authorized,already_has_reference,rowcount",
    [(True, False, 1), (True, True, 1), (False, False, 1), (True, False, 0)],
)
async def test_apply_uses_configured_route_counter_and_authorized_actor(
    monkeypatch, tmp_path, authorized, already_has_reference, rowcount
):
    from devnous.gastos.routes import admin_routes

    monkeypatch.setattr(admin_routes, "_budget_can_mutate", lambda actor: authorized)
    session = SimpleNamespace(
        get=AsyncMock(return_value=SimpleNamespace(activo=True)),
        execute=AsyncMock(return_value=SimpleNamespace(rowcount=rowcount)),
    )
    upsert = AsyncMock(return_value={"id": "line"})
    assign, audit, route = AsyncMock(), AsyncMock(), AsyncMock()
    counter = AsyncMock(return_value="999")
    monkeypatch.setattr(
        repair, "verify_route", AsyncMock(return_value={"source": "reviewed"})
    )
    monkeypatch.setattr(repair.budgets, "upsert_budget_line_for_concept", upsert)
    monkeypatch.setattr(repair.budgets, "assign_budget_movement_to_line", assign)
    monkeypatch.setattr(repair.budgets, "_audit_budget_event", audit)
    monkeypatch.setattr(repair, "resolve_and_snapshot_document_route", route)
    monkeypatch.setattr(repair, "allocate_next_referencia_operaciones", counter)
    plan = {
        "lines": [{"budget_concept_id": "hotel", "phase": "Estatal"}],
        "assignments": [{"budget_concept_id": "hotel", "movement_key": "accounting:a"}],
        "routing_document": {
            "id": uuid4(),
            "referencia_operaciones": "old" if already_has_reference else None,
        },
        "allocate_operations_reference": not already_has_reference,
        "version": {"status": "draft"},
    }
    if not authorized or rowcount == 0:
        with pytest.raises(ValueError):
            await repair.apply_plan(session, plan, args(tmp_path, True))
        assert not audit.called
    else:
        result = await repair.apply_plan(session, plan, args(tmp_path, True))
        assert result["operations_reference"] == (
            "old" if already_has_reference else "999"
        )
        assert counter.await_count == (0 if already_has_reference else 1)
        assert upsert.call_args.kwargs["ensure_schema"] is False
        assert assign.call_args.kwargs["ensure_schema"] is False
        assert audit.await_count == 1


@pytest.mark.asyncio
async def test_apply_requires_an_explicit_actor_and_reviewed_hash(tmp_path):
    with pytest.raises(ValueError, match="requires actor"):
        await repair.run(args(tmp_path, True))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode",
    [
        "dry_run",
        "apply",
        "drift",
        "line_changed",
        "new_budget",
        "other_assignment",
        "payment_changed",
        "amount_changed",
        "route_missing",
        "route_holders_changed",
    ],
)
async def test_transaction_commits_only_the_reviewed_and_verified_plan(
    monkeypatch, tmp_path, mode
):
    options = args(tmp_path, mode != "dry_run")
    baseline = [movement()]
    plan = {
        "version": {"edition_year": 2026, "status": "draft"},
        "tournament_id": "t",
        "routing_document": {"id": "route-doc", "cuenta_gastos_id": "a"},
        "selected_reports": [{"cuenta_gastos_id": "a"}],
        "lines": [{"budget_concept_id": "hotel", "existing": []}],
        "assignments": [
            {"movement_key": "accounting:hotel-posting", "budget_concept_id": "hotel"}
        ],
    }
    options.expected_plan_sha256 = "stale" if mode == "drift" else repair.plan_sha(plan)
    after = deepcopy(baseline)
    after[0].update(budget_line_id="new", reconciliation_status="assigned")
    if mode == "amount_changed":
        after[0]["amount"] = 999
    before_docs = [
        {"id": "route-doc", "referencia_operaciones": None, "estado": "enviado"},
        {"id": "historic", "referencia_operaciones": "190", "estado": "pagado"},
    ]
    after_docs = deepcopy(before_docs)
    after_docs[0]["referencia_operaciones"] = "999"
    if mode == "payment_changed":
        after_docs[1]["estado"] = "aprobado"
    outcomes = []

    class Transaction:
        async def __aenter__(self):
            return self

        async def __aexit__(self, kind, exc, tb):
            outcomes.append("rollback" if kind else "commit")

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        def begin(self):
            return Transaction()

        async def execute(self, statement, params=None):
            return None

    engine = SimpleNamespace(sync_engine=object(), dispose=AsyncMock())
    guards = []

    def listener(*args):
        def decorator(fn):
            guards.append(fn)
            return fn

        return decorator

    counts = {"lines": 0, "assignments": 0}

    async def table_rows(session, sql, params=None):
        if "to_jsonb(l)" in sql:
            counts["lines"] += 1
            base = {"row": {"id": "old", "budget_amount": 0}}
            if counts["lines"] == 1:
                return [base]
            if mode == "line_changed":
                base["row"]["budget_amount"] = 1
            return [
                base,
                {
                    "row": {
                        "id": "new",
                        "budget_amount": 1 if mode == "new_budget" else 0,
                    }
                },
            ]
        if "to_jsonb(a)" in sql:
            counts["assignments"] += 1
            return [
                {
                    "row": {
                        "movement_key": "accounting:old",
                        "budget_line_id": (
                            "changed"
                            if counts["assignments"] > 1 and mode == "other_assignment"
                            else "old"
                        ),
                    }
                }
            ]
        if "to_jsonb(r)" in sql:
            return []
        raise AssertionError(sql)

    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")
    monkeypatch.setattr(repair, "create_async_engine", lambda *a, **k: engine)
    monkeypatch.setattr(repair.event, "listens_for", listener)
    monkeypatch.setattr(repair, "AsyncSession", lambda *a: Session())
    monkeypatch.setattr(
        repair, "build_plan", AsyncMock(return_value=(plan, {"movements": baseline}))
    )
    apply = AsyncMock(
        return_value={"line_map": {"hotel": "new"}, "operations_reference": "999"}
    )
    monkeypatch.setattr(repair, "apply_plan", apply)
    monkeypatch.setattr(repair, "rows", table_rows)
    monkeypatch.setattr(
        repair,
        "verify_route",
        (
            AsyncMock(side_effect=ValueError("route drift"))
            if mode in {"route_missing", "route_holders_changed"}
            else AsyncMock(return_value={"source": "reviewed"})
        ),
    )
    monkeypatch.setattr(
        repair, "document_state", AsyncMock(side_effect=[before_docs, after_docs])
    )
    monkeypatch.setattr(
        repair.budgets,
        "build_budget_actuals_snapshot",
        AsyncMock(return_value={"movements": after}),
    )
    if mode not in {"apply", "dry_run"}:
        with pytest.raises(ValueError):
            await repair.run(options)
        assert outcomes == ["rollback"]
    else:
        assert await repair.run(options) == 0
        assert outcomes == ["commit"]
    assert apply.await_count == (0 if mode in {"dry_run", "drift"} else 1)
    engine.dispose.assert_awaited_once()
    with pytest.raises(ValueError):
        guards[0](None, None, "ALTER TABLE financial_records", None, None, False)
    guards[0](None, None, "SELECT 1", None, None, False)


@pytest.mark.asyncio
async def test_cli_refuses_missing_database_configuration(monkeypatch, tmp_path):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValueError, match="DATABASE_URL"):
        await repair.run(args(tmp_path))


def test_cli_redacts_database_errors(monkeypatch, tmp_path, capsys):
    import sys

    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(PATH),
            "--version-id",
            str(uuid4()),
            "--ops-refs",
            "144",
            "--informe-ref",
            "I-report",
            "--receipt",
            str(tmp_path / "receipt"),
        ],
    )
    monkeypatch.setattr(
        repair, "run", AsyncMock(side_effect=RuntimeError("sensitive database detail"))
    )
    assert repair.main() == 1
    assert "sensitive" not in capsys.readouterr().out


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation", [None, "missing", "holders", "positions", "reference", "source"]
)
async def test_route_evidence_must_match_reviewed_plan(monkeypatch, mutation):
    expected = {
        "eligible_position_keys": ["director_operaciones"],
        "eligible_empleado_ids": ["holder"],
        "requires_operations_reference": True,
        "source": "project_rule",
    }
    actual = deepcopy(expected)
    if mutation == "holders":
        actual["eligible_empleado_ids"] = ["replacement"]
    if mutation == "positions":
        actual["eligible_position_keys"] = ["other"]
    if mutation == "reference":
        actual["requires_operations_reference"] = False
    if mutation == "source":
        actual["source"] = "changed"
    monkeypatch.setattr(
        repair,
        "rows",
        AsyncMock(return_value=[] if mutation == "missing" else [actual]),
    )
    plan = {"routing_document": {"id": "doc"}, "expected_route": expected}
    if mutation:
        with pytest.raises(ValueError, match="Authorization route changed"):
            await repair.verify_route(None, plan)
    else:
        assert await repair.verify_route(None, plan) == expected


def test_route_arrays_are_compared_without_order_dependence():
    first = {
        "eligible_position_keys": ["a", "b"],
        "eligible_empleado_ids": ["1", "2"],
        "source": "project",
        "requires_operations_reference": True,
    }
    second = {
        **first,
        "eligible_position_keys": ["b", "a"],
        "eligible_empleado_ids": ["2", "1"],
    }
    assert repair.normalized_route(first) == repair.normalized_route(second)


@pytest.mark.asyncio
async def test_preview_includes_both_selected_routing_documents_in_hash(
    monkeypatch, tmp_path
):
    options = args(tmp_path)
    options.also_informe_ref = ["I-Mike"]
    routing = [
        {"routing_document": {"id": "sebas"}},
        {"routing_document": {"id": "mike"}},
    ]
    build_route = AsyncMock(side_effect=routing)
    monkeypatch.setattr(repair, "build_routing_plan", build_route)

    async def fake_rows(session, sql, params=None):
        if "budget_versions" in sql:
            return [{"edition_year": 2026, "status": "draft"}]
        if "LEFT JOIN cuentas_de_gastos" in sql:
            return [{"estado": "aprobado", "torneo_id": "t"}]
        raise AssertionError(sql)

    monkeypatch.setattr(repair, "rows", fake_rows)
    monkeypatch.setattr(
        repair.budgets,
        "build_budget_actuals_snapshot",
        AsyncMock(return_value={"movements": [movement(assigned=True)]}),
    )
    plan, _ = await repair.build_plan(None, options)
    assert plan["routing_document"]["id"] == "sebas"
    assert plan["additional_routing"][0]["routing_document"]["id"] == "mike"
    assert [call.args[1] for call in build_route.await_args_list] == [
        "I-report",
        "I-Mike",
    ]
    changed = deepcopy(plan)
    changed["additional_routing"][0]["routing_document"]["id"] = "different"
    assert repair.plan_sha(changed) != repair.plan_sha(plan)


@pytest.mark.asyncio
async def test_apply_audits_both_selected_routes(monkeypatch, tmp_path):
    from devnous.gastos.routes import admin_routes

    monkeypatch.setattr(admin_routes, "_budget_can_mutate", lambda actor: True)
    session = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(activo=True)))
    extra = {"routing_document": {"id": "mike"}}
    plan = {
        "routing_document": {"id": "sebas"},
        "additional_routing": [extra],
        "lines": [],
        "assignments": [],
        "version": {"status": "draft"},
    }
    routing = AsyncMock(
        side_effect=[
            {"document_id": "sebas", "operations_reference": "999", "after_route": {}},
            {"document_id": "mike", "operations_reference": "1000", "after_route": {}},
        ]
    )
    monkeypatch.setattr(repair, "apply_routing_plan", routing)
    audit = AsyncMock()
    monkeypatch.setattr(repair.budgets, "_audit_budget_event", audit)
    changes = await repair.apply_plan(session, plan, args(tmp_path, True))
    assert changes["operations_reference"] == "999"
    assert changes["additional_routes"][0]["operations_reference"] == "1000"
    assert routing.await_count == 2
    assert (
        audit.call_args.kwargs["payload"]["additional_routes"][0]["document_id"]
        == "mike"
    )
