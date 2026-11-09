"""Approved reconciliation authority, source identity and counterfactual boundaries."""

import copy
import json
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

import pytest

import devnous.gastos  # noqa: F401
from samchat.budgets import reconciliation as owner
from samchat.budgets import service

VERSION = "10000000-0000-4000-8000-000000000001"
ACTOR = "10000000-0000-4000-8000-000000000002"
TID = "30000000-0000-4000-8000-000000000001"
CID = "40000000-0000-4000-8000-000000000001"
LINE = "50000000-0000-4000-8000-000000000001"
DOC = "60000000-0000-4000-8000-000000000001"
EXPENSE = "70000000-0000-4000-8000-000000000001"
POLICY = "80000000-0000-4000-8000-000000000001"
POSTING = "90000000-0000-4000-8000-000000000001"
OLD = "accounting:90000000-0000-4000-8000-000000000002"
KEY = "accounting:" + POSTING


class Result:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def mappings(self):
        return self

    def all(self):
        return self.rows

    def first(self):
        return self.rows[0] if self.rows else None

    def scalar_one_or_none(self):
        return self.first()

    def scalars(self):
        return self.rows

    def __iter__(self):
        return iter(self.rows)


class Session:
    def __init__(self):
        self.isolation = "serializable"
        self.readonly = True
        self.statements = []
        self.actor = SimpleNamespace(
            id=UUID(ACTOR),
            activo=True,
            rol="superadmin",
            departamento=None,
            correo=None,
        )
        self.version = {
            "id": VERSION,
            "edition_year": 2026,
            "status": "approved",
            "source": "upload",
            "updated_at": "original",
            "notes": "Borrador",
        }
        self.lines = [
            {
                "id": LINE,
                "budget_version_id": VERSION,
                "tournament_id": TID,
                "budget_concept_id": CID,
                "line_direction": "expense",
                "phase": "Fase Estatal",
                "concept_name": "Hotel",
                "budget_amount": "100.00",
            }
        ]
        self.concepts = [
            {"id": CID, "active": True, "metadata": {"scope_mode": "phase_scoped"}}
        ]
        self.tournament = {"id": TID, "name": "Copa", "etapas": ["Fase Estatal"]}
        self.movements = [
            {
                "accounting_line_id": POSTING,
                "poliza_id": POLICY,
                "document_id": DOC,
                "expense_id": EXPENSE,
                "concept_key": CID,
                "effective_phase": "Estatal",
                "kind": "ledger_expense",
                "amount": 100.25,
                "month_number": 3,
                "document_reference": "S-26000001",
            },
            {
                "accounting_line_id": OLD.split(":")[1],
                "concept_key": CID,
                "kind": "ledger_expense",
                "amount": 5.5,
                "month_number": 3,
            },
        ]
        self.bindings = [
            {
                "id": "a0000000-0000-4000-8000-000000000001",
                "movement_key": OLD,
                "budget_concept_id": CID,
                "budget_line_id": LINE,
                "assigned_by_empleado_id": ACTOR,
            }
        ]
        self.audit = []
        self.sources = {
            "documentos": {"id": DOC, "estado": "pagado"},
            "expense_reports": {"id": EXPENSE, "monto": "100.25"},
            "accounting_polizas": {"id": POLICY, "estado": "posted"},
            "accounting_poliza_lines": {
                "id": POSTING,
                "poliza_id": POLICY,
                "debe": "100.25",
            },
        }
        self.kwargs = {
            "budget_version_id": VERSION,
            "edition_year": 2026,
            "actor_empleado_id": ACTOR,
            "reason": "Conciliar referencia existente",
            "assignments": [
                {"tournament_id": TID, "movement_key": KEY, "budget_line_id": LINE}
            ],
        }

    async def scalar(self, statement):
        sql = str(statement)
        if sql == "SHOW transaction_isolation":
            return self.isolation
        if sql == "SHOW transaction_read_only":
            return "on" if self.readonly else "off"
        return self.actor

    async def execute(self, statement, params=None):
        sql = str(statement)
        p = params or {}
        self.statements.append((sql, p))
        if sql.startswith("INSERT INTO budget_movement_assignments"):
            if any(b["movement_key"] == p["movement_key"] for b in self.bindings):
                return Result()
            self.bindings.append(copy.deepcopy(p))
            return Result([p["id"]])
        if "INSERT INTO budget_version_audit_log" in sql:
            self.audit.append({"id": p["id"], "payload": json.loads(p["payload"])})
            return Result()
        if "FROM budget_version_audit_log" in sql:
            return Result(
                [
                    r
                    for r in self.audit
                    if r["payload"]["actor_empleado_id"] == p["actor"]
                    and (
                        r["payload"]["plan_sha256"] == p["hash"]
                        if p.get("hash")
                        else r["payload"]["intent"] == json.loads(p["intent"])
                    )
                ]
            )
        if "SELECT to_jsonb(v)" in sql:
            return Result([copy.deepcopy(self.version)] if self.version else [])
        if "SELECT to_jsonb(l) FROM budget_lines" in sql:
            return Result(copy.deepcopy(self.lines))
        if "SELECT to_jsonb(c)" in sql:
            return Result(copy.deepcopy(self.concepts))
        if "SELECT to_jsonb(t) FROM tournaments" in sql:
            return Result([copy.deepcopy(self.tournament)] if self.tournament else [])
        if "SELECT to_jsonb" in sql:
            for table, row in self.sources.items():
                if f"FROM {table} " in sql:
                    return Result([copy.deepcopy(row)] if row else [])
        if "FROM budget_movement_assignments" in sql:
            return Result([{**r, "assignment_id": r["id"]} for r in self.bindings])
        if "FROM budget_lines" in sql and "line_direction" in sql:
            return Result(copy.deepcopy(self.lines))
        return Result()


@pytest.fixture
def case(monkeypatch):
    s = Session()

    async def governance(*args):
        return None

    async def confirmed(*args):
        return {}

    async def snapshot(session, **kwargs):
        movements = copy.deepcopy(session.movements)
        store = service._monthly_actual_store()
        service._project_explicit_movement_assignments(
            movements=movements,
            store=store,
            assignment_rows=[{**r, "assignment_id": r["id"]} for r in session.bindings],
            line_rows=session.lines,
            confirmed={},
        )
        return {
            "movements": movements,
            "monthly": {k: dict(v) for k, v in store.items()},
        }

    monkeypatch.setattr(service, "require_ungoverned_gastos_project", governance)
    monkeypatch.setattr(service, "_closeout_assignment_proofs", confirmed)
    monkeypatch.setattr(service, "build_budget_actuals_snapshot", snapshot)
    return s


@pytest.mark.asyncio
async def test_preview_apply_recover_preserve_authoritative_records(case):
    before = copy.deepcopy(
        (case.version, case.lines, case.concepts, case.sources, case.bindings)
    )
    p = await owner.preview_approved_movement_reconciliation(case, **case.kwargs)
    assert p["impact"][TID]["before_unassigned"] == 1
    assert p["impact"][TID]["after_unassigned"] == 0
    assert not case.audit and len(case.bindings) == 1
    case.readonly = False
    applied = await owner.apply_approved_movement_reconciliation(
        case, expected_plan_sha256=p["plan_sha256"], **case.kwargs
    )
    assert (
        applied["status"] == "APPLIED"
        and len(case.bindings) == 2
        and len(case.audit) == 1
    )
    assert before[:4] == (case.version, case.lines, case.concepts, case.sources)
    assert before[4][0] == case.bindings[0]
    replay = await owner.apply_approved_movement_reconciliation(
        case, expected_plan_sha256=p["plan_sha256"], **case.kwargs
    )
    assert (
        replay["status"] == "ALREADY_APPLIED"
        and replay["audit_id"] == applied["audit_id"]
    )
    case.readonly = True
    recovered = await owner.recover_approved_movement_reconciliation(
        case, **case.kwargs
    )
    assert recovered["plan_sha256"] == p["plan_sha256"] and len(case.audit) == 1
    assert all(
        not sql.lstrip().startswith(("UPDATE", "DELETE", "ALTER", "CREATE"))
        for sql, _ in case.statements
    )


@pytest.mark.parametrize(
    "change",
    [
        {"edition_year": True},
        {"edition_year": 1990},
        {"reason": ""},
        {"reason": "x" * 1001},
        {"assignments": []},
        {"budget_version_id": "bad"},
        {"actor_empleado_id": "bad"},
        {
            "assignments": [
                {
                    "tournament_id": TID,
                    "movement_key": "document:" + DOC,
                    "budget_line_id": LINE,
                }
            ]
        },
    ],
)
@pytest.mark.asyncio
async def test_invalid_intentions_never_read_or_write(case, change):
    with pytest.raises(ValueError):
        await owner.preview_approved_movement_reconciliation(
            case, **{**case.kwargs, **change}
        )
    assert not case.statements


@pytest.mark.asyncio
async def test_duplicate_keys_rejected(case):
    case.kwargs["assignments"] *= 2
    with pytest.raises(ValueError, match="dos veces"):
        await owner.preview_approved_movement_reconciliation(case, **case.kwargs)


@pytest.mark.parametrize(
    "isolation,readonly", [("read committed", True), ("serializable", False)]
)
@pytest.mark.asyncio
async def test_preview_requires_real_readonly_serializable(case, isolation, readonly):
    case.isolation = isolation
    case.readonly = readonly
    with pytest.raises(ValueError):
        await owner.preview_approved_movement_reconciliation(case, **case.kwargs)


@pytest.mark.parametrize(
    "actor",
    [
        None,
        SimpleNamespace(id=UUID(ACTOR), activo=False, rol="superadmin"),
        SimpleNamespace(id=UUID(ACTOR), activo=True, rol="empleado"),
    ],
)
@pytest.mark.asyncio
async def test_actor_fails_closed(case, actor):
    case.actor = actor
    with pytest.raises(PermissionError):
        await owner.preview_approved_movement_reconciliation(case, **case.kwargs)


@pytest.mark.asyncio
async def test_only_line_update_capability_is_required(case, monkeypatch):
    from devnous.gastos.routes import admin_routes

    monkeypatch.setattr(
        admin_routes,
        "_budget_access_map",
        lambda a: {"line_update": True, "create": False, "approve": False},
    )
    assert (await owner.require_reconciliation_actor(case, ACTOR))["id"] == ACTOR


@pytest.mark.parametrize(
    "field,value",
    [
        ("status", "draft"),
        ("status", "frozen"),
        ("source", "distribution_revision"),
        ("edition_year", 2025),
    ],
)
@pytest.mark.asyncio
async def test_version_context_cannot_expand(case, field, value):
    case.version[field] = value
    with pytest.raises(ValueError, match="raíz aprobada"):
        await owner.preview_approved_movement_reconciliation(case, **case.kwargs)


@pytest.mark.parametrize(
    "change",
    [
        {"effective_phase": ""},
        {"effective_phase": "Nacional"},
        {"concept_key": "other"},
        {"source_kind": "amex_direct"},
        {"origen": "amex_direct"},
        {"budget_assignment_id": "special"},
        {"kind": "ledger_income"},
    ],
)
@pytest.mark.asyncio
async def test_invalid_source_phase_identity_and_owner_rejected(case, change):
    case.movements[0].update(change)
    with pytest.raises(ValueError):
        await owner.preview_approved_movement_reconciliation(case, **case.kwargs)


@pytest.mark.asyncio
async def test_inactive_and_ambiguous_targets_rejected(case):
    case.concepts[0]["active"] = False
    with pytest.raises(ValueError):
        await owner.preview_approved_movement_reconciliation(case, **case.kwargs)
    case.concepts[0]["active"] = True
    case.lines.append({**case.lines[0], "id": "50000000-0000-4000-8000-000000000002"})
    with pytest.raises(ValueError):
        await owner.preview_approved_movement_reconciliation(case, **case.kwargs)


@pytest.mark.parametrize(
    "table",
    ["documentos", "expense_reports", "accounting_polizas", "accounting_poliza_lines"],
)
@pytest.mark.asyncio
async def test_source_rows_must_exist(case, table):
    case.sources[table] = None
    with pytest.raises(ValueError):
        await owner.preview_approved_movement_reconciliation(case, **case.kwargs)


@pytest.mark.asyncio
async def test_source_drift_rejects_before_insert(case):
    p = await owner.preview_approved_movement_reconciliation(case, **case.kwargs)
    case.sources["expense_reports"]["monto"] = "999.00"
    case.readonly = False
    with pytest.raises(owner.ReconciliationConflict, match="cambió"):
        await owner.apply_approved_movement_reconciliation(
            case, expected_plan_sha256=p["plan_sha256"], **case.kwargs
        )
    assert len(case.bindings) == 1 and not case.audit


@pytest.mark.asyncio
async def test_pending_accounting_is_not_recognized_as_ledger(case):
    case.movements[0]["kind"] = "pending_accounting"
    p = await owner.preview_approved_movement_reconciliation(case, **case.kwargs)
    case.readonly = False
    r = await owner.apply_approved_movement_reconciliation(
        case, expected_plan_sha256=p["plan_sha256"], **case.kwargs
    )
    assert r["assignments"][0]["kind"] == "pending_accounting"


@pytest.mark.asyncio
async def test_audited_receipt_cannot_adopt_changed_bma(case):
    p = await owner.preview_approved_movement_reconciliation(case, **case.kwargs)
    case.readonly = False
    await owner.apply_approved_movement_reconciliation(
        case, expected_plan_sha256=p["plan_sha256"], **case.kwargs
    )
    case.bindings[-1]["id"] = "a0000000-0000-4000-8000-000000000099"
    case.readonly = True
    with pytest.raises(owner.ReconciliationConflict, match="cambió"):
        await owner.recover_approved_movement_reconciliation(
            case, expected_plan_sha256=p["plan_sha256"], **case.kwargs
        )


@pytest.mark.asyncio
async def test_recovery_does_not_create_a_missing_operation(case):
    assert (
        await owner.recover_approved_movement_reconciliation(case, **case.kwargs)
        is None
    )
    with pytest.raises(ValueError):
        await owner.recover_approved_movement_reconciliation(
            case, expected_plan_sha256="bad", **case.kwargs
        )
    assert len(case.bindings) == 1 and not case.audit


def test_hash_stability_decimal_and_unsafe_numbers():
    assert owner.plan_digest({"x": Decimal("1.00")}) == owner.plan_digest({"x": 1.0})
    with pytest.raises(ValueError):
        owner.plan_digest({"x": Decimal("NaN")})


@pytest.mark.asyncio
async def test_counterfactual_rejects_any_collateral_assignment_change(
    case, monkeypatch
):
    project = owner._project

    def damaged(snapshot, context, additions):
        result = project(snapshot, context, additions)
        result["movements"][1]["budget_line_id"] = None
        result["movements"][1]["reconciliation_status"] = "unassigned"
        return result

    monkeypatch.setattr(owner, "_project", damaged)
    with pytest.raises(owner.ReconciliationConflict, match="otros movimientos"):
        await owner.preview_approved_movement_reconciliation(case, **case.kwargs)
    assert len(case.bindings) == 1 and not case.audit


@pytest.mark.asyncio
async def test_hidden_derived_total_change_is_not_approved(case, monkeypatch):
    project = owner._project

    def damaged(snapshot, context, additions):
        result = project(snapshot, context, additions)
        result["monthly"][LINE][3]["real_expense_cash"] += 0.01
        return result

    monkeypatch.setattr(owner, "_project", damaged)
    with pytest.raises(owner.ReconciliationConflict, match="totales"):
        await owner.preview_approved_movement_reconciliation(case, **case.kwargs)


@pytest.mark.asyncio
async def test_already_assigned_sources_cannot_be_used_to_activate_fallback(case):
    case.bindings = []
    # Both source rows now use the legacy fallback. Creating a first explicit
    # assignment would remove it from the other row, so neither is a pending
    # source that can be adopted by this new owner.
    with pytest.raises(ValueError):
        await owner.preview_approved_movement_reconciliation(case, **case.kwargs)
    assert case.bindings == [] and not case.audit


@pytest.mark.asyncio
async def test_recovery_requires_complete_exact_audit_coverage(case):
    plan = await owner.preview_approved_movement_reconciliation(case, **case.kwargs)
    case.readonly = False
    await owner.apply_approved_movement_reconciliation(
        case, expected_plan_sha256=plan["plan_sha256"], **case.kwargs
    )
    case.audit[0]["payload"]["assignments"] = []
    case.readonly = True
    with pytest.raises(owner.ReconciliationConflict, match="incompleto"):
        await owner.recover_approved_movement_reconciliation(case, **case.kwargs)


@pytest.mark.asyncio
async def test_catalogue_row_order_does_not_invalidate_confirmed_plan(case):
    other_cid = "40000000-0000-4000-8000-000000000002"
    case.concepts.append({"id": other_cid, "active": True, "metadata": {}})
    case.lines.append(
        {
            **case.lines[0],
            "id": "50000000-0000-4000-8000-000000000002",
            "budget_concept_id": other_cid,
            "concept_name": "Otra partida",
        }
    )
    original = await owner.preview_approved_movement_reconciliation(case, **case.kwargs)
    case.concepts.reverse()
    reordered = await owner.preview_approved_movement_reconciliation(
        case, **case.kwargs
    )
    assert reordered["plan_sha256"] == original["plan_sha256"]
    case.readonly = False
    receipt = await owner.apply_approved_movement_reconciliation(
        case, expected_plan_sha256=original["plan_sha256"], **case.kwargs
    )
    assert receipt["status"] == "APPLIED"
    assert len(case.audit) == 1
