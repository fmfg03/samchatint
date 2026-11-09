"""Explicit, audited assignment to existing lines of an approved budget.

Adapters own a SERIALIZABLE transaction on the canonical runtime engine. This
owner never commits, reopens a version, changes source records or installs schema.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text

from . import service

EVENT = "approved_movement_reconciled"
SCHEMA_VERSION = "samchat.approved-budget-reconciliation.v1"


class ReconciliationConflict(ValueError):
    """A reviewed source, target or assignment no longer matches."""


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("Importe de origen no finito")
        return str(value.normalize())
    if isinstance(value, float):
        return _clean(Decimal(str(value)))
    if isinstance(value, (uuid.UUID, date, datetime)):
        return str(value)
    return value


def plan_digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            _clean(value),
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    ).hexdigest()


def _uuid(value: Any) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("Identidad de conciliación inválida") from exc


def _intent(
    *, budget_version_id, edition_year, assignments, actor_empleado_id, reason
) -> dict:
    if type(edition_year) is not int or not 2000 <= edition_year <= 2100:
        raise ValueError("Ejercicio inválido")
    reason = str(reason or "").strip()
    if not reason or len(reason) > 1000:
        raise ValueError("Indique un motivo de 1 a 1000 caracteres")
    if not isinstance(assignments, list) or not 1 <= len(assignments) <= 100:
        raise ValueError("Seleccione movimientos concretos")
    rows = []
    for item in assignments:
        key = str(item.get("movement_key") or "")
        if not re.fullmatch(
            r"(?:accounting|expense):[a-f0-9-]{36}|"
            r"document:[a-f0-9-]{36}:concept:[a-f0-9-]{36}",
            key,
        ):
            raise ValueError("Se requiere la clave canónica específica del movimiento")
        for part in key.split(":"):
            if part not in {"accounting", "expense", "document", "concept"}:
                _uuid(part)
        rows.append(
            {
                "tournament_id": _uuid(item.get("tournament_id")),
                "movement_key": key,
                "budget_line_id": _uuid(item.get("budget_line_id")),
            }
        )
    if len({r["movement_key"] for r in rows}) != len(rows):
        raise ValueError("Un movimiento no puede aparecer dos veces")
    return {
        "schema_version": SCHEMA_VERSION,
        "budget_version_id": _uuid(budget_version_id),
        "edition_year": edition_year,
        "assignments": sorted(
            rows, key=lambda r: (r["tournament_id"], r["movement_key"])
        ),
        "actor_empleado_id": _uuid(actor_empleado_id),
        "reason": reason,
    }


async def _transaction(session, *, readonly: bool) -> None:
    if await session.scalar(text("SHOW transaction_isolation")) != "serializable":
        raise ValueError("La conciliación requiere una transacción SERIALIZABLE propia")
    if readonly and await session.scalar(text("SHOW transaction_read_only")) != "on":
        raise ValueError("La vista previa requiere READ ONLY")


async def require_reconciliation_actor(
    session, actor_id: str, *, lock: bool = False
) -> dict:
    """Refresh the exact existing line_update capability; never demand approval."""
    from sqlalchemy import select
    from sqlalchemy.orm import load_only, raiseload

    from devnous.gastos.models import Empleado
    from devnous.gastos.routes.admin_routes import _budget_access_map

    statement = (
        select(Empleado)
        .options(
            load_only(
                Empleado.id,
                Empleado.activo,
                Empleado.rol,
                Empleado.departamento,
                Empleado.correo,
            ),
            raiseload("*"),
        )
        .where(Empleado.id == uuid.UUID(actor_id))
        .execution_options(populate_existing=True)
    )
    if lock:
        statement = statement.with_for_update(read=True)
    actor = await session.scalar(statement)
    if (
        not actor
        or actor.activo is not True
        or _budget_access_map(actor).get("line_update") is not True
    ):
        raise PermissionError(
            "El usuario no tiene permisos de conciliación presupuestal"
        )
    return {"id": str(actor.id), "activo": True, "rol": actor.rol}


def is_amex_direct_source(movement: dict) -> bool:
    return (
        movement.get("source_kind") == "amex_direct"
        or movement.get("origen") == "amex_direct"
        or bool(movement.get("budget_assignment_id"))
    )


def eligible_reconciliation_lines(movement, lines, *, active_concept_ids):
    """Informational candidates; the transactional owner rechecks every fact."""
    phase = service._normalize_budget_scope_key(movement.get("effective_phase"))
    if (
        movement.get("budget_line_id")
        or not phase
        or is_amex_direct_source(movement)
        or movement.get("kind")
        not in {"ledger_expense", "commitment", "pending_accounting"}
    ):
        return []
    cid = str(movement.get("concept_key") or "")
    if cid not in active_concept_ids:
        return []
    return [
        row
        for row in lines
        if str(row.get("budget_concept_id")) == cid
        and row.get("line_direction", "expense") == "expense"
        and service._normalize_budget_scope_key(row.get("phase")) == phase
    ]


async def _load_state(session, intent: dict) -> dict:
    actor = await require_reconciliation_actor(session, intent["actor_empleado_id"])
    version = (
        await session.execute(
            text(
                "SELECT to_jsonb(v) AS row FROM budget_versions v "
                "WHERE id=CAST(:id AS uuid)"
            ),
            {"id": intent["budget_version_id"]},
        )
    ).scalar_one_or_none()
    if (
        not version
        or version.get("status") != "approved"
        or version.get("source") == "distribution_revision"
        or version.get("edition_year") != intent["edition_year"]
    ):
        raise ValueError(
            "Se requiere la versión financiera raíz aprobada del ejercicio"
        )
    lines = list(
        (
            await session.execute(
                text(
                    "SELECT to_jsonb(l) FROM budget_lines l "
                    "WHERE budget_version_id=CAST(:id AS uuid)"
                ),
                {"id": intent["budget_version_id"]},
            )
        ).scalars()
    )
    concepts = list(
        (
            await session.execute(
                text(
                    "SELECT to_jsonb(c) FROM budget_concepts c WHERE id IN "
                    "(SELECT budget_concept_id FROM budget_lines "
                    "WHERE budget_version_id=CAST(:id AS uuid))"
                ),
                {"id": intent["budget_version_id"]},
            )
        ).scalars()
    )
    concepts.sort(key=lambda row: _uuid(row["id"]))
    snapshots, contexts, tournaments = {}, {}, {}
    for tid in sorted({r["tournament_id"] for r in intent["assignments"]}):
        await service.require_ungoverned_gastos_project(session, tid)
        tournament = (
            await session.execute(
                text(
                    "SELECT to_jsonb(t) FROM tournaments t WHERE id=CAST(:id AS uuid)"
                ),
                {"id": tid},
            )
        ).scalar_one_or_none()
        if not tournament:
            raise ValueError("Proyecto inexistente")
        tournaments[tid] = tournament
        snapshots[tid] = await service.build_budget_actuals_snapshot(
            session,
            edition_year=intent["edition_year"],
            version_id=intent["budget_version_id"],
            tournament_id=tid,
        )
        contexts[tid] = await service._movement_assignment_projection_context(
            session,
            budget_version_id=intent["budget_version_id"],
            movements=snapshots[tid]["movements"],
        )
    return {
        "actor": actor,
        "version": version,
        "lines": lines,
        "concepts": concepts,
        "snapshots": snapshots,
        "contexts": contexts,
        "tournaments": tournaments,
    }


def _movement_map(movements: list[dict]) -> dict:
    result = {}
    for row in movements:
        key = service.budget_movement_key(row)
        if not key or key in result:
            raise ValueError("Identidad de movimientos duplicada o ausente")
        result[key] = _clean(row)
    return result


def _totals(snapshot: dict) -> dict:
    fields = ("real_expense_cash", "real_income", "committed_unpaid")
    return {
        field: str(
            sum(
                (
                    Decimal(str(values.get(field, 0)))
                    for months in snapshot["monthly"].values()
                    for values in months.values()
                ),
                Decimal(0),
            )
        )
        for field in fields
    }


def _project(snapshot, context, additions):
    cloned = copy.deepcopy(snapshot)
    store = service._monthly_actual_store()
    for cid, months in cloned["monthly"].items():
        for period, values in months.items():
            store[cid][int(period)].update(values)
    service._project_explicit_movement_assignments(
        movements=cloned["movements"],
        store=store,
        assignment_rows=[*context["assignment_rows"], *additions],
        line_rows=context["line_rows"],
        confirmed=context["confirmed"],
    )
    cloned["monthly"] = {k: dict(v) for k, v in store.items()}
    return cloned


async def _source_rows(session, movement, *, lock=False) -> dict:
    """Fingerprint complete factual rows; lock authoritative parents first."""
    result = {}
    for table, ident in (
        ("documentos", movement.get("document_id")),
        ("expense_reports", movement.get("expense_id")),
        ("accounting_polizas", movement.get("poliza_id")),
    ):
        if not ident:
            continue
        rows = list(
            (
                await session.execute(
                    text(
                        f"SELECT to_jsonb(t) FROM {table} t WHERE id=CAST(:id AS uuid)"
                        + (" FOR SHARE OF t" if lock else "")
                    ),
                    {"id": _uuid(ident)},
                )
            ).scalars()
        )
        if len(rows) != 1:
            raise ValueError("La fuente del movimiento no está disponible")
        result[table] = [{"id": rows[0]["id"], "sha256": plan_digest(rows[0])}]
    if movement.get("poliza_id"):
        rows = list(
            (
                await session.execute(
                    text(
                        "SELECT to_jsonb(l) FROM accounting_poliza_lines l "
                        "WHERE poliza_id=CAST(:id AS uuid) ORDER BY id"
                        + (" FOR SHARE OF l" if lock else "")
                    ),
                    {"id": _uuid(movement["poliza_id"])},
                )
            ).scalars()
        )
        if not any(
            str(row["id"]) == movement.get("accounting_line_id") for row in rows
        ):
            raise ValueError("La línea de póliza no está disponible")
        result["accounting_poliza_lines"] = [
            {"id": row["id"], "sha256": plan_digest(row)} for row in rows
        ]
    if not result:
        raise ValueError("Movimiento sin fuente acreditada")
    return result


async def _build_plan(session, intent: dict, state: dict) -> dict:
    active = {str(c["id"]) for c in state["concepts"] if c.get("active") is True}
    resolved, additions = [], {}
    for wanted in intent["assignments"]:
        tid, key = wanted["tournament_id"], wanted["movement_key"]
        snapshot = state["snapshots"][tid]
        found = [
            m for m in snapshot["movements"] if service.budget_movement_key(m) == key
        ]
        if len(found) != 1:
            raise ValueError(
                "La clave no corresponde a un movimiento único del proyecto"
            )
        movement = found[0]
        if any(
            r["movement_key"] == key for r in state["contexts"][tid]["assignment_rows"]
        ):
            raise ReconciliationConflict(
                "El movimiento ya tiene una asignación explícita"
            )
        lines = [r for r in state["lines"] if r.get("tournament_id") == tid]
        candidates = eligible_reconciliation_lines(
            movement, lines, active_concept_ids=active
        )
        if len(candidates) != 1 or candidates[0]["id"] != wanted["budget_line_id"]:
            raise ValueError(
                "No existe una única partida activa de concepto y fase compatibles"
            )
        target = candidates[0]
        row = {
            **wanted,
            "budget_concept_id": target["budget_concept_id"],
            "concept_id": target["budget_concept_id"],
            "phase": target.get("phase"),
            "concept_name": target["concept_name"],
            "reference": movement.get("document_reference")
            or movement.get("operation_reference")
            or key,
            "kind": movement.get("kind"),
            "amount": _clean(movement.get("amount")),
            "source_identity": await _source_rows(session, movement),
            "target_sha256": plan_digest(target),
        }
        resolved.append(row)
        additions.setdefault(tid, []).append(
            {
                "assignment_id": "preview:" + key,
                "movement_key": key,
                "budget_concept_id": target["budget_concept_id"],
                "budget_line_id": target["id"],
            }
        )
    impact = {}
    for tid, before in state["snapshots"].items():
        virtual = _project(before, state["contexts"][tid], additions.get(tid, []))
        old, new = _movement_map(before["movements"]), _movement_map(
            virtual["movements"]
        )
        expected = {
            r["movement_key"]: r["budget_line_id"]
            for r in resolved
            if r["tournament_id"] == tid
        }
        if old.keys() != new.keys():
            raise ReconciliationConflict(
                "La proyección cambió las identidades de fuente"
            )
        for key, original in old.items():
            wanted = dict(original)
            if key in expected:
                wanted.update(
                    budget_line_id=expected[key], reconciliation_status="assigned"
                )
            if wanted != new[key]:
                raise ReconciliationConflict(
                    "La conciliación afectaría otros movimientos; revise el lote"
                )
        if _totals(before) != _totals(virtual):
            raise ReconciliationConflict(
                "La conciliación alteraría los totales de la fuente"
            )
        impact[tid] = {
            "movement_count": len(old),
            "selected_keys": sorted(expected),
            "other_movements_preserved": len(old) - len(expected),
            "before_unassigned": sum(not r.get("budget_line_id") for r in old.values()),
            "after_unassigned": sum(not r.get("budget_line_id") for r in new.values()),
            "before_fingerprint": plan_digest(old),
            "after_fingerprint": plan_digest(new),
            "totals": _totals(before),
        }
    plan = {
        **intent,
        "assignments": resolved,
        "impact": impact,
        "version_sha256": plan_digest(state["version"]),
        "actor": state["actor"],
        "catalogue_sha256": plan_digest(state["concepts"]),
        "tournaments_sha256": plan_digest(state["tournaments"]),
    }
    plan["plan_sha256"] = plan_digest(plan)
    return plan


async def preview_approved_movement_reconciliation(session, **kwargs) -> dict:
    """Prepare a READ ONLY counterfactual; a preview is not authority to write."""
    intent = _intent(**kwargs)
    await _transaction(session, readonly=True)
    state = await _load_state(session, intent)
    return await _build_plan(session, intent, state)


async def _recover(session, intent: dict, expected: str | None) -> dict | None:
    if expected is not None and not re.fullmatch(r"[a-f0-9]{64}", expected):
        raise ValueError("Huella de confirmación inválida")
    await require_reconciliation_actor(session, intent["actor_empleado_id"])
    for tid in sorted({r["tournament_id"] for r in intent["assignments"]}):
        await service.require_ungoverned_gastos_project(session, tid)
    predicate = (
        "AND payload->>'plan_sha256'=:hash"
        if expected
        else "AND payload->'intent'=CAST(:intent AS jsonb)"
    )
    rows = list(
        (
            await session.execute(
                text(
                    (
                        "SELECT id::text,payload FROM budget_version_audit_"
                        "log WHERE budget_version_id=CAST(:version AS uuid)"
                        " AND event_type=:event AND actor_empleado_id=CAST("
                        ":actor AS uuid) "
                    )
                    + predicate
                ),
                {
                    "version": intent["budget_version_id"],
                    "event": EVENT,
                    "actor": intent["actor_empleado_id"],
                    "hash": expected,
                    "intent": json.dumps(intent, sort_keys=True),
                },
            )
        ).mappings()
    )
    if not rows:
        return None
    if len(rows) != 1 or rows[0]["payload"].get("intent") != intent:
        raise ReconciliationConflict(
            "El recibo no coincide con la intención confirmada"
        )
    payload = rows[0]["payload"]
    actual_hash = payload.get("plan_sha256", "")
    if (
        payload.get("schema_version") != SCHEMA_VERSION
        or not re.fullmatch(r"[a-f0-9]{64}", actual_hash)
        or [
            {k: row.get(k) for k in ("tournament_id", "movement_key", "budget_line_id")}
            for row in payload.get("assignments", [])
        ]
        != intent["assignments"]
    ):
        raise ReconciliationConflict("Recibo de conciliación incompleto o incompatible")
    expected = actual_hash
    bindings = list(
        (
            await session.execute(
                text(
                    (
                        "SELECT id::text,budget_concept_id::text,budget_lin"
                        "e_id::text,movement_key,assigned_by_empleado_id::t"
                        "ext FROM budget_movement_assignments WHERE budget_"
                        "version_id=CAST(:id AS uuid)"
                    )
                ),
                {"id": intent["budget_version_id"]},
            )
        ).mappings()
    )
    bykey = {r["movement_key"]: r for r in bindings}
    for assignment in payload["assignments"]:
        current = bykey.get(assignment["movement_key"])
        if not current or any(
            current.get(k) != assignment.get(v)
            for k, v in (
                ("id", "assignment_id"),
                ("budget_line_id", "budget_line_id"),
                ("budget_concept_id", "budget_concept_id"),
                ("assigned_by_empleado_id", "actor_empleado_id"),
            )
        ):
            raise ReconciliationConflict("La asignación confirmada cambió")
    return {
        "status": "ALREADY_APPLIED",
        "schema_version": SCHEMA_VERSION,
        "plan_sha256": expected,
        "audit_id": rows[0]["id"],
        "actor_empleado_id": intent["actor_empleado_id"],
        "assignments": payload["assignments"],
        "impact": payload["impact"],
    }


async def recover_approved_movement_reconciliation(
    session, *, expected_plan_sha256: str | None = None, **kwargs
) -> dict | None:
    """Recover only a previous exact receipt, without locks or new authority."""
    await _transaction(session, readonly=True)
    return await _recover(session, _intent(**kwargs), expected_plan_sha256)


async def _lock_resources(session, intent, state):
    tids = sorted(state["tournaments"])
    for table, column, values in (
        ("tournaments", "id", tids),
        ("budget_versions", "id", [intent["budget_version_id"]]),
    ):
        await session.execute(
            text(
                f"SELECT id FROM {table} WHERE {column}=ANY(CAST(:ids AS uuid[])) "
                "ORDER BY id"
                + (" FOR UPDATE" if table == "budget_versions" else " FOR SHARE")
            ),
            {"ids": values},
        )
    await session.execute(
        text(
            "SELECT id FROM budget_lines WHERE budget_version_id=CAST(:id AS uuid) "
            "ORDER BY id FOR SHARE"
        ),
        {"id": intent["budget_version_id"]},
    )
    ids = sorted(str(c["id"]) for c in state["concepts"])
    await session.execute(
        text(
            "SELECT id FROM budget_concepts WHERE id=ANY(CAST(:ids AS uuid[])) "
            "ORDER BY id FOR SHARE"
        ),
        {"ids": ids},
    )
    await require_reconciliation_actor(session, intent["actor_empleado_id"], lock=True)
    movements = []
    for wanted in intent["assignments"]:
        found = [
            m
            for m in state["snapshots"][wanted["tournament_id"]]["movements"]
            if service.budget_movement_key(m) == wanted["movement_key"]
        ]
        if len(found) != 1:
            raise ValueError("Fuente de conciliación no disponible")
        movements.append(found[0])
    for table, field in (
        ("documentos", "document_id"),
        ("expense_reports", "expense_id"),
        ("accounting_polizas", "poliza_id"),
    ):
        ids = sorted({_uuid(m[field]) for m in movements if m.get(field)})
        if ids:
            await session.execute(
                text(
                    f"SELECT id FROM {table} "
                    "WHERE id=ANY(CAST(:ids AS uuid[])) ORDER BY id FOR SHARE"
                ),
                {"ids": ids},
            )
    policy_ids = sorted(
        {_uuid(m["poliza_id"]) for m in movements if m.get("poliza_id")}
    )
    if policy_ids:
        await session.execute(
            text(
                (
                    "SELECT id FROM accounting_poliza_lines WHERE poliz"
                    "a_id=ANY(CAST(:ids AS uuid[])) ORDER BY id FOR SHA"
                    "RE"
                )
            ),
            {"ids": policy_ids},
        )


async def apply_approved_movement_reconciliation(
    session, *, expected_plan_sha256: str, **kwargs
) -> dict:
    """Insert exact BMA+audit in the caller's transaction; never commit here."""
    intent = _intent(**kwargs)
    await _transaction(session, readonly=False)
    previous = await _recover(session, intent, expected_plan_sha256)
    if previous:
        return previous
    state = await _load_state(session, intent)
    await _lock_resources(session, intent, state)
    state = await _load_state(session, intent)
    plan = await _build_plan(session, intent, state)
    if plan["plan_sha256"] != expected_plan_sha256:
        raise ReconciliationConflict(
            "La fuente o destino cambió; prepare otra vista previa"
        )
    assigned = []
    for row in plan["assignments"]:
        ident = str(uuid.uuid4())
        params = {
            "id": ident,
            "budget_version_id": intent["budget_version_id"],
            "budget_concept_id": row["budget_concept_id"],
            "budget_line_id": row["budget_line_id"],
            "movement_key": row["movement_key"],
            "assigned_by_empleado_id": intent["actor_empleado_id"],
        }
        inserted = (
            await session.execute(
                text(
                    (
                        "INSERT INTO budget_movement_assignments (id,budget"
                        "_version_id,budget_concept_id,budget_line_id,movem"
                        "ent_key,assigned_by_empleado_id,created_at,updated"
                        "_at) VALUES (CAST(:id AS uuid),CAST(:budget_versio"
                        "n_id AS uuid),CAST(:budget_concept_id AS uuid),CAS"
                        "T(:budget_line_id AS uuid),:movement_key,CAST(:ass"
                        "igned_by_empleado_id AS uuid),NOW(),NOW()) ON CONF"
                        "LICT (budget_version_id,movement_key) DO NOTHING R"
                        "ETURNING id"
                    )
                ),
                params,
            )
        ).scalar_one_or_none()
        if not inserted:
            raise ReconciliationConflict(
                "Otro proceso concilió el movimiento; recargue"
            )
        assigned.append(
            {
                **row,
                "assignment_id": ident,
                "actor_empleado_id": intent["actor_empleado_id"],
            }
        )
    for tid, expected in plan["impact"].items():
        actual = await service.build_budget_actuals_snapshot(
            session,
            edition_year=intent["edition_year"],
            version_id=intent["budget_version_id"],
            tournament_id=tid,
        )
        if (
            plan_digest(_movement_map(actual["movements"]))
            != expected["after_fingerprint"]
            or _totals(actual) != expected["totals"]
        ):
            raise ReconciliationConflict(
                "La proyección real no coincide con la revisión"
            )
    await service._audit_budget_event(
        session,
        budget_version_id=intent["budget_version_id"],
        event_type=EVENT,
        actor_empleado_id=intent["actor_empleado_id"],
        from_status="approved",
        to_status="approved",
        note=intent["reason"],
        payload={
            "schema_version": SCHEMA_VERSION,
            "plan_sha256": expected_plan_sha256,
            "actor_empleado_id": intent["actor_empleado_id"],
            "reason": intent["reason"],
            "intent": intent,
            "assignments": assigned,
            "impact": plan["impact"],
        },
    )
    receipt = await _recover(session, intent, expected_plan_sha256)
    if not receipt:
        raise ReconciliationConflict("No se pudo acreditar el recibo de conciliación")
    return {**receipt, "status": "APPLIED"}
