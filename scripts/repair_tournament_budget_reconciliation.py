#!/usr/bin/env python3
"""Preview or apply an explicitly selected routing/budget repair.

Dry-run uses a READ ONLY transaction. Apply requires an authorized active
budget administrator, the reviewed plan SHA, an audit receipt and --apply.
No schema, document approval, payment, accounting or notification is changed.
Canon unchanged: this restores existing project-routing and budget rules.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

from sqlalchemy import event, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from devnous.gastos.models import Empleado  # noqa: E402
from devnous.gastos.services.documento_service import (  # noqa: E402
    allocate_next_referencia_operaciones,
)
from devnous.gastos.services.project_authorization_service import (  # noqa: E402
    resolve_and_snapshot_document_route,
)
from samchat.budgets import service as budgets  # noqa: E402


async def rows(session, sql, params=None):
    result = await session.execute(text(sql), params or {})
    return [dict(row) for row in result.mappings().all()]


class RoutePreview:
    """Run the canonical resolver while capturing its proposed INSERT."""

    def __init__(self, session):
        self.session = session
        self.proposed = None

    async def execute(self, statement, params=None):
        sql = str(statement).strip().lower()
        if sql.startswith("insert into documento_authorization_routes"):
            self.proposed = params
            return None
        if not sql.startswith("select"):
            raise ValueError("Unexpected mutation in route preview")
        return await self.session.execute(statement, params)


def movement_plan(movements, refs):
    """Select missing movements, keeping every concept and phase separate."""
    found = {str(row.get("operation_reference")) for row in movements}
    if not refs.issubset(found):
        raise ValueError("Selected references are absent from the budget snapshot")
    selected = [row for row in movements if str(row.get("operation_reference")) in refs]
    missing = [row for row in selected if not row.get("budget_line_id")]
    if any(row.get("kind") not in {"ledger_expense", "commitment"} for row in selected):
        raise ValueError("Selected movements need separate accounting review")
    plan = []
    for movement in missing:
        concept = movement.get("concept_key")
        key = budgets.budget_movement_key(movement)
        if not concept or concept == "__unassigned__" or not key:
            raise ValueError("Movement has no canonical concept or source identity")
        plan.append(
            {
                "movement_key": key,
                "budget_concept_id": concept,
                "concept_name": movement.get("budget_concept_name"),
                "phase": movement.get("effective_phase"),
                "operation_reference": movement["operation_reference"],
                "document_id": movement.get("document_id"),
                "document_reference": movement.get("document_reference"),
                "amount": movement["amount"],
                "kind": movement["kind"],
            }
        )
    if len({row["movement_key"] for row in plan}) != len(plan):
        raise ValueError("Ambiguous movement identity; do not assign whole reports")
    return sorted(plan, key=lambda row: row["movement_key"])


def plan_sha(plan):
    """Hash non-secret selectors and before-state, never connection settings."""
    payload = json.dumps(plan, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def verify_movements(before, after, expected, line_map):
    """Reject any unexpected change before the enclosing transaction commits."""
    actual = {row["movement_key"]: row for row in after}
    for key, item in expected.items():
        if (
            key not in actual
            or actual[key]["budget_line_id"] != line_map[item["budget_concept_id"]]
        ):
            raise ValueError("Post-repair assignment validation failed")
    for old, new in zip(before, after, strict=True):
        ignored = {"budget_line_id", "reconciliation_status"}
        if {k: v for k, v in old.items() if k not in ignored} != {
            k: v for k, v in new.items() if k not in ignored
        }:
            raise ValueError("Accounting projection changed unexpectedly")
        if old["movement_key"] not in expected and old.get("budget_line_id") != new.get(
            "budget_line_id"
        ):
            raise ValueError("Unselected movement changed")


def normalized_route(row):
    """Compare the reviewed authorization evidence independent of array order."""
    return {
        "eligible_position_keys": sorted(row["eligible_position_keys"]),
        "eligible_empleado_ids": sorted(str(x) for x in row["eligible_empleado_ids"]),
        "requires_operations_reference": row["requires_operations_reference"],
        "source": row["source"],
    }


async def route_state(session, document_id):
    result = await rows(
        session,
        """SELECT eligible_position_keys, eligible_empleado_ids,
        requires_operations_reference, source FROM documento_authorization_routes
        WHERE documento_id=:id""",
        {"id": document_id},
    )
    return normalized_route(result[0]) if len(result) == 1 else None


async def active_route_holder_ids(session, route):
    """Validate snapshot identities against active employees and Operations position."""
    if (
        route["eligible_position_keys"] != ["director_operaciones"]
        or not route["requires_operations_reference"]
    ):
        raise ValueError("Reviewed route must require Operations authorization")
    positions = await rows(
        session,
        "SELECT position_key FROM authorization_positions "
        "WHERE position_key='director_operaciones' AND active=TRUE",
    )
    expected = sorted(route["eligible_empleado_ids"])
    if not positions or not expected:
        raise ValueError("Operations position and holders must be active")
    employees = await rows(
        session,
        "SELECT id::text FROM empleados WHERE activo=TRUE "
        "AND id::text=ANY(CAST(:ids AS text[])) ORDER BY id",
        {"ids": expected},
    )
    actual = sorted(row["id"] for row in employees)
    if actual != expected:
        raise ValueError("Reviewed Operations holder is inactive or missing")
    return actual


async def lock_repair_sources(session, args):
    """Lock selected source records in stable order; SERIALIZABLE guards phantoms."""
    selectors = {
        "routing_refs": sorted(
            [args.informe_ref, *getattr(args, "also_informe_ref", [])]
        ),
        "ops_refs": sorted(set(args.ops_refs)),
    }
    selected = await rows(
        session,
        """
        SELECT id::text, cuenta_gastos_id::text, empleado_id::text,
               beneficiario_empleado_id::text, torneo_id::text
        FROM documentos
        WHERE tipo='INFORME' AND (
            numero_referencia=ANY(CAST(:routing_refs AS text[]))
            OR referencia_operaciones=ANY(CAST(:ops_refs AS text[]))
        ) ORDER BY id
    """,
        selectors,
    )
    document_ids = sorted({row["id"] for row in selected})
    account_ids = sorted(
        {row["cuenta_gastos_id"] for row in selected if row["cuenta_gastos_id"]}
    )
    await session.execute(
        text("SELECT id FROM budget_versions WHERE id=:id FOR UPDATE"),
        {"id": args.version_id},
    )
    accounts = await rows(
        session,
        """
        SELECT id::text, torneo_id::text FROM cuentas_de_gastos
        WHERE id::text=ANY(CAST(:accounts AS text[])) ORDER BY id FOR UPDATE
    """,
        {"accounts": account_ids},
    )
    documents = await rows(
        session,
        """
        SELECT id::text, empleado_id::text, beneficiario_empleado_id::text,
               cfdi_report_id::text FROM documentos
        WHERE id::text=ANY(CAST(:documents AS text[]))
           OR cuenta_gastos_id::text=ANY(CAST(:accounts AS text[]))
        ORDER BY id FOR UPDATE
    """,
        {"documents": document_ids, "accounts": account_ids},
    )
    document_ids = sorted({row["id"] for row in documents})
    scoped = {"documents": document_ids, "accounts": account_ids}
    expenses = await rows(
        session,
        """
        SELECT id::text, cfdi_report_id::text FROM expense_reports
        WHERE cuenta_gastos_id::text=ANY(CAST(:accounts AS text[]))
           OR documento_id::text=ANY(CAST(:documents AS text[]))
           OR informe_documento_id::text=ANY(CAST(:documents AS text[]))
           OR solicitud_documento_id::text=ANY(CAST(:documents AS text[]))
        ORDER BY id FOR UPDATE
    """,
        scoped,
    )
    for query in (
        "SELECT id FROM anticipos WHERE documento_id::text="
        "ANY(CAST(:documents AS text[])) ORDER BY id FOR UPDATE",
        "SELECT id FROM reembolsos WHERE documento_id::text="
        "ANY(CAST(:documents AS text[])) OR cuenta_gastos_id::text="
        "ANY(CAST(:accounts AS text[])) ORDER BY id FOR UPDATE",
        "SELECT id FROM aprobaciones WHERE tipo_entidad='documento' "
        "AND entidad_id::text=ANY(CAST(:documents AS text[])) "
        "ORDER BY id FOR UPDATE",
        "SELECT id FROM payment_run_closure_items WHERE documento_id::text="
        "ANY(CAST(:documents AS text[])) ORDER BY id FOR UPDATE",
    ):
        await session.execute(text(query), scoped)
    cfdi_ids = sorted(
        {
            row["cfdi_report_id"]
            for row in [*documents, *expenses]
            if row["cfdi_report_id"]
        }
    )
    accounting_scope = {
        "documents": document_ids,
        "expenses": sorted(row["id"] for row in expenses),
        "cfdis": cfdi_ids,
    }
    policies = await rows(
        session,
        """
        SELECT p.id::text FROM accounting_polizas p
        WHERE p.cfdi_report_id::text=ANY(CAST(:cfdis AS text[]))
           OR EXISTS (SELECT 1 FROM accounting_poliza_lines l
               WHERE l.poliza_id=p.id AND (
                   COALESCE(l.raw_row_json->>'documento_id',
                            l.raw_row_json->>'document_id')
                       =ANY(CAST(:documents AS text[]))
                   OR l.raw_row_json->>'expense_id'=ANY(CAST(:expenses AS text[]))
               )) ORDER BY p.id FOR UPDATE OF p
    """,
        accounting_scope,
    )
    await session.execute(
        text(
            "SELECT id FROM accounting_poliza_lines WHERE poliza_id::text="
            "ANY(CAST(:policies AS text[])) ORDER BY id FOR UPDATE"
        ),
        {"policies": sorted(row["id"] for row in policies)},
    )
    routes = await rows(
        session,
        """
        SELECT eligible_empleado_ids FROM documento_authorization_routes
        WHERE documento_id::text=ANY(CAST(:documents AS text[]))
        ORDER BY documento_id FOR UPDATE
    """,
        scoped,
    )
    await session.execute(
        text(
            "SELECT position_key FROM authorization_positions "
            "WHERE position_key='director_operaciones' ORDER BY position_key FOR UPDATE"
        )
    )
    employee_ids = {
        value
        for row in documents
        for value in (row["empleado_id"], row["beneficiario_empleado_id"])
        if value
    }
    employee_ids.add(str(args.actor_id))
    for route in routes:
        employee_ids.update(str(value) for value in route["eligible_empleado_ids"])
    assignments = await rows(
        session,
        """
        SELECT empleado_id::text FROM authorization_position_assignments
        WHERE position_key='director_operaciones'
           OR empleado_id::text=ANY(CAST(:employees AS text[]))
        ORDER BY position_key, empleado_id FOR UPDATE
    """,
        {"employees": sorted(employee_ids)},
    )
    employee_ids.update(row["empleado_id"] for row in assignments)
    await session.execute(
        text(
            "SELECT id FROM empleados WHERE id::text=ANY(CAST(:employees AS text[])) "
            "ORDER BY id FOR UPDATE"
        ),
        {"employees": sorted(employee_ids)},
    )
    tournaments = sorted(
        {row["torneo_id"] for row in [*selected, *accounts] if row["torneo_id"]}
    )
    await session.execute(
        text(
            "SELECT tournament_id FROM project_authorization_rules "
            "WHERE tournament_id::text=ANY(CAST(:tournaments AS text[])) "
            "ORDER BY tournament_id FOR UPDATE"
        ),
        {"tournaments": tournaments},
    )
    return {
        "account_ids": account_ids,
        "document_ids": document_ids,
        "expense_ids": accounting_scope["expenses"],
        "policy_ids": sorted(row["id"] for row in policies),
    }


async def verify_route(session, plan):
    actual = await route_state(session, plan["routing_document"]["id"])
    if actual is None or actual != plan["expected_route"]:
        raise ValueError("Authorization route changed from the reviewed plan")
    active_ids = await active_route_holder_ids(session, actual)
    if active_ids != plan["active_eligible_empleado_ids"]:
        raise ValueError("Active Operations authority changed from reviewed plan")
    return actual


async def document_state(session, account_ids):
    """Keep payments and historical references visible in the recovery receipt."""
    return await rows(
        session,
        """SELECT id::text,numero_referencia,tipo,estado,
        referencia_operaciones,monto_solicitado,monto_total,pagado_en,aprobado_en,
        torneo_id::text,budget_concept_id::text FROM documentos
        WHERE cuenta_gastos_id::text=ANY(CAST(:accounts AS text[])) ORDER BY id""",
        {"accounts": account_ids},
    )


async def build_routing_plan(session, reference):
    report = await rows(
        session,
        """SELECT id, empleado_id, beneficiario_empleado_id,
        torneo_id, cuenta_gastos_id, referencia_operaciones, estado, numero_referencia
        FROM documentos WHERE numero_referencia=:ref AND tipo='INFORME'""",
        {"ref": reference},
    )
    if len(report) != 1 or report[0]["estado"] != "enviado":
        raise ValueError("Routing repair requires exactly one submitted INFORME")
    preview = RoutePreview(session)
    route = await resolve_and_snapshot_document_route(
        preview, SimpleNamespace(**report[0])
    )
    if route is None or not route.requires_operations_reference:
        raise ValueError("No configured Operations route for selected INFORME")
    if preview.proposed:
        expected_route = normalized_route(
            {
                "eligible_position_keys": json.loads(preview.proposed["positions"]),
                "eligible_empleado_ids": json.loads(preview.proposed["employee_ids"]),
                "requires_operations_reference": preview.proposed["requires_reference"],
                "source": preview.proposed["source"],
            }
        )
    else:
        expected_route = await route_state(session, report[0]["id"])
    if expected_route is None or not expected_route["eligible_empleado_ids"]:
        raise ValueError("Configured route has no active position holder")
    active_ids = await active_route_holder_ids(session, expected_route)
    return {
        "routing_document": report[0],
        "active_eligible_empleado_ids": active_ids,
        "route_insert": preview.proposed,
        "expected_route": expected_route,
        "eligible_positions": list(route.eligible_position_keys),
        "allocate_operations_reference": not report[0]["referencia_operaciones"],
    }


async def build_plan(session, args):
    version = await rows(
        session,
        """SELECT id::text, edition_year, status
        FROM budget_versions WHERE id=CAST(:id AS uuid)""",
        {"id": str(args.version_id)},
    )
    if len(version) != 1 or version[0]["status"] not in {"draft", "reforecast"}:
        raise ValueError("Budget version is not editable")
    routing = await build_routing_plan(session, args.informe_ref)
    extra_refs = getattr(args, "also_informe_ref", [])
    if len(set([args.informe_ref, *extra_refs])) != 1 + len(extra_refs):
        raise ValueError("Routing references must be unique")
    additional_routing = [await build_routing_plan(session, ref) for ref in extra_refs]
    reports = await rows(
        session,
        """SELECT d.id::text, d.numero_referencia, d.estado,
        d.referencia_operaciones, COALESCE(d.torneo_id,c.torneo_id)::text AS torneo_id,
        d.cuenta_gastos_id::text FROM documentos d
        LEFT JOIN cuentas_de_gastos c ON c.id=d.cuenta_gastos_id
        WHERE d.tipo='INFORME' AND d.referencia_operaciones=ANY(CAST(:refs AS text[]))
        ORDER BY d.referencia_operaciones""",
        {"refs": args.ops_refs},
    )
    if len(reports) != len(set(args.ops_refs)) or any(
        row["estado"] != "aprobado" for row in reports
    ):
        raise ValueError("References must uniquely identify approved expense reports")
    tournaments = {row["torneo_id"] for row in reports}
    if len(tournaments) != 1 or None in tournaments:
        raise ValueError("Selected reports must belong to one known tournament")
    tournament_id = tournaments.pop()
    snapshot = await budgets.build_budget_actuals_snapshot(
        session,
        edition_year=version[0]["edition_year"],
        version_id=str(args.version_id),
        tournament_id=tournament_id,
    )
    movements = movement_plan(snapshot["movements"], set(args.ops_refs))
    lines = []
    for concept_id in sorted({row["budget_concept_id"] for row in movements}):
        scoped = [row for row in movements if row["budget_concept_id"] == concept_id]
        phases = {row["phase"] for row in scoped}
        if len(phases) != 1:
            raise ValueError(
                "Concept appears in multiple phases; manual review required"
            )
        concept = await budgets.resolve_budget_concept(
            session,
            budget_concept_id=concept_id,
            tournament_id=tournament_id,
            fase=scoped[0]["phase"],
            budget_direction="expense",
        )
        if concept is None:
            raise ValueError("Movement concept does not match tournament/phase")
        existing = await rows(
            session,
            """SELECT id::text,budget_amount,phase
            FROM budget_lines WHERE budget_version_id=CAST(:version AS uuid)
            AND budget_concept_id=CAST(:concept AS uuid)
            AND COALESCE(line_direction,'expense')='expense'""",
            {"version": str(args.version_id), "concept": concept_id},
        )
        if len(existing) > 1 or (
            existing and existing[0]["phase"] != scoped[0]["phase"]
        ):
            raise ValueError("Existing line is ambiguous or belongs to another phase")
        lines.append(
            {
                "budget_concept_id": concept_id,
                "concept_name": concept["concept_name"],
                "phase": scoped[0]["phase"],
                "existing": existing,
                "create_budget_amount": 0,
            }
        )
    return {
        "version": version[0],
        "tournament_id": tournament_id,
        **routing,
        "additional_routing": additional_routing,
        "selected_reports": reports,
        "lines": lines,
        "assignments": movements,
    }, snapshot


async def apply_routing_plan(session, plan):
    doc = SimpleNamespace(**plan["routing_document"])
    await resolve_and_snapshot_document_route(session, doc)
    after_route = await verify_route(session, plan)
    reference = doc.referencia_operaciones
    if plan["allocate_operations_reference"]:
        reference = await allocate_next_referencia_operaciones(session)
        result = await session.execute(
            text("""UPDATE documentos
            SET referencia_operaciones=:reference WHERE id=:id
            AND estado='enviado' AND referencia_operaciones IS NULL"""),
            {"reference": reference, "id": doc.id},
        )
        if result.rowcount != 1:
            raise ValueError("Routing document changed during repair")
    return {
        "document_id": str(doc.id),
        "operations_reference": reference,
        "after_route": after_route,
    }


async def apply_plan(session, plan, args):
    """Apply one reviewed plan atomically; leave unrelated financial state intact."""
    actor = await session.get(Empleado, args.actor_id)
    from devnous.gastos.routes.admin_routes import _budget_can_mutate

    if actor is None or not actor.activo or not _budget_can_mutate(actor):
        raise ValueError("Actor lacks canonical budget mutation authority")
    version_id = str(args.version_id)
    actor_id = str(args.actor_id)
    line_map = {}
    for item in plan["lines"]:
        line = await budgets.upsert_budget_line_for_concept(
            session,
            version_id=version_id,
            budget_concept_id=item["budget_concept_id"],
            budget_amount=0,
            phase=item["phase"],
            line_direction="expense",
            preserve_existing=True,
            actor_empleado_id=actor_id,
            commit=False,
            ensure_schema=False,
        )
        line_map[item["budget_concept_id"]] = str(line["id"])
    for movement in plan["assignments"]:
        await budgets.assign_budget_movement_to_line(
            session,
            budget_version_id=version_id,
            budget_concept_id=movement["budget_concept_id"],
            budget_line_id=line_map[movement["budget_concept_id"]],
            movement_key=movement["movement_key"],
            actor_empleado_id=actor_id,
            ensure_schema=False,
        )
    routing_change = await apply_routing_plan(session, plan)
    additional_routes = [
        await apply_routing_plan(session, item)
        for item in plan.get("additional_routing", [])
    ]
    reference = routing_change["operations_reference"]
    after_route = routing_change["after_route"]
    await budgets._audit_budget_event(
        session,
        budget_version_id=version_id,
        event_type="tournament_reconciliation_repair",
        actor_empleado_id=actor_id,
        from_status=plan["version"]["status"],
        to_status=plan["version"]["status"],
        note="Explicitly reviewed zero-budget and project-routing repair.",
        payload={
            "plan_sha256": args.expected_plan_sha256,
            "line_map": line_map,
            "movement_keys": [row["movement_key"] for row in plan["assignments"]],
            "routing_document_id": str(plan["routing_document"]["id"]),
            "additional_routes": additional_routes,
            "operations_reference": reference,
            "receipt_path": str(args.receipt),
        },
    )
    return {
        "line_map": line_map,
        "operations_reference": reference,
        "after_route": after_route,
        "additional_routes": additional_routes,
    }


async def run(args):
    if args.apply and (not args.actor_id or not args.expected_plan_sha256):
        raise ValueError("Apply requires actor and reviewed plan SHA")
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise ValueError("DATABASE_URL is required")
    engine = create_async_engine(
        make_url(database_url).set(drivername="postgresql+asyncpg"),
        hide_parameters=True,
    )

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def deny_schema_changes(conn, cursor, statement, params, context, many):
        if (
            statement.strip()
            .lower()
            .startswith(("create ", "alter ", "drop ", "truncate "))
        ):
            raise ValueError("Schema changes are outside this repair")

    receipt = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "apply": args.apply,
    }
    try:
        async with AsyncSession(engine) as session:
            async with session.begin():
                if args.apply:
                    await session.execute(
                        text("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
                    )
                else:
                    await session.execute(text("SET TRANSACTION READ ONLY"))
                await session.execute(text("SET LOCAL statement_timeout='30s'"))
                if args.apply:
                    receipt["locked_sources"] = await lock_repair_sources(session, args)
                plan, before = await build_plan(session, args)
                digest = plan_sha(plan)
                receipt.update(
                    {"plan": plan, "plan_sha256": digest, "status": "dry_run"}
                )
                args.receipt.write_text(
                    json.dumps(receipt, default=str, ensure_ascii=False, indent=2)
                )
                if args.apply:
                    if digest != args.expected_plan_sha256:
                        raise ValueError("Plan drifted; review a new dry-run")
                    receipt["before_movements"] = before["movements"]
                    receipt["before_lines"] = await rows(
                        session,
                        "SELECT to_jsonb(l) AS row FROM budget_lines l "
                        "WHERE budget_version_id=:id ORDER BY id",
                        {"id": args.version_id},
                    )
                    receipt["before_assignments"] = await rows(
                        session,
                        "SELECT to_jsonb(a) AS row FROM budget_movement_assignments a "
                        "WHERE budget_version_id=:id ORDER BY id",
                        {"id": args.version_id},
                    )
                    accounts = sorted(
                        {
                            str(row["cuenta_gastos_id"])
                            for row in plan["selected_reports"]
                        }
                        | {str(plan["routing_document"]["cuenta_gastos_id"])}
                        | {
                            str(x["routing_document"]["cuenta_gastos_id"])
                            for x in plan.get("additional_routing", [])
                        }
                    )
                    receipt["before_documents"] = await document_state(
                        session, accounts
                    )
                    receipt["before_route"] = await rows(
                        session,
                        "SELECT to_jsonb(r) AS row "
                        "FROM documento_authorization_routes r WHERE documento_id=:id",
                        {"id": plan["routing_document"]["id"]},
                    )
                    receipt["additional_before_routes"] = {
                        str(item["routing_document"]["id"]): await rows(
                            session,
                            "SELECT to_jsonb(r) AS row "
                            "FROM documento_authorization_routes r "
                            "WHERE documento_id=:id",
                            {"id": item["routing_document"]["id"]},
                        )
                        for item in plan.get("additional_routing", [])
                    }
                    args.receipt.write_text(
                        json.dumps(receipt, default=str, ensure_ascii=False, indent=2)
                    )
                    changes = await apply_plan(session, plan, args)
                    after = await budgets.build_budget_actuals_snapshot(
                        session,
                        edition_year=plan["version"]["edition_year"],
                        version_id=str(args.version_id),
                        tournament_id=plan["tournament_id"],
                    )
                    expected = {row["movement_key"]: row for row in plan["assignments"]}
                    verify_movements(
                        before["movements"],
                        after["movements"],
                        expected,
                        changes["line_map"],
                    )
                    after_lines = await rows(
                        session,
                        "SELECT to_jsonb(l) AS row FROM budget_lines l "
                        "WHERE budget_version_id=:id ORDER BY id",
                        {"id": args.version_id},
                    )
                    old_line_ids = {row["row"]["id"] for row in receipt["before_lines"]}
                    if [
                        row for row in after_lines if row["row"]["id"] in old_line_ids
                    ] != receipt["before_lines"]:
                        raise ValueError("Existing budget line changed")
                    new_lines = [
                        row
                        for row in after_lines
                        if row["row"]["id"] not in old_line_ids
                    ]
                    if {row["row"]["id"] for row in new_lines} != {
                        changes["line_map"][row["budget_concept_id"]]
                        for row in plan["lines"]
                        if not row["existing"]
                    } or any(
                        float(row["row"]["budget_amount"]) != 0 for row in new_lines
                    ):
                        raise ValueError(
                            "New line scope or zero budget validation failed"
                        )
                    after_assignments = await rows(
                        session,
                        "SELECT to_jsonb(a) AS row FROM budget_movement_assignments a "
                        "WHERE budget_version_id=:id ORDER BY id",
                        {"id": args.version_id},
                    )
                    if [
                        row
                        for row in after_assignments
                        if row["row"]["movement_key"] not in expected
                    ] != [
                        row
                        for row in receipt["before_assignments"]
                        if row["row"]["movement_key"] not in expected
                    ]:
                        raise ValueError("Unselected assignment changed")
                    after_docs = await document_state(session, accounts)
                    expected_docs = [dict(row) for row in receipt["before_documents"]]
                    extra_references = {
                        item["document_id"]: item["operations_reference"]
                        for item in changes.get("additional_routes", [])
                    }
                    for doc in expected_docs:
                        if doc["id"] in extra_references:
                            doc["referencia_operaciones"] = extra_references[doc["id"]]
                        if doc["id"] == str(plan["routing_document"]["id"]):
                            doc["referencia_operaciones"] = changes[
                                "operations_reference"
                            ]
                    if after_docs != expected_docs:
                        raise ValueError("Document payment or approval state changed")
                    receipt.update(
                        {
                            "changes": changes,
                            "after_route": await verify_route(session, plan),
                            "additional_after_routes": [
                                await verify_route(session, item)
                                for item in plan.get("additional_routing", [])
                            ],
                            "after_movements": after["movements"],
                            "status": "verified_before_commit",
                        }
                    )
                    args.receipt.write_text(
                        json.dumps(receipt, default=str, ensure_ascii=False, indent=2)
                    )
            if args.apply:
                receipt["status"] = "committed"
            args.receipt.write_text(
                json.dumps(receipt, default=str, ensure_ascii=False, indent=2)
            )
    finally:
        await engine.dispose()
    print(
        json.dumps(
            {
                "status": receipt["status"],
                "plan_sha256": digest,
                "line_count": len(plan["lines"]),
                "assignment_count": len(plan["assignments"]),
                "receipt": str(args.receipt),
            },
            ensure_ascii=False,
        )
    )
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version-id", type=UUID, required=True)
    parser.add_argument("--ops-refs", nargs="+", required=True)
    parser.add_argument("--informe-ref", required=True)
    parser.add_argument("--also-informe-ref", action="append", default=[])
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--actor-id", type=UUID)
    parser.add_argument("--expected-plan-sha256")
    try:
        return asyncio.run(run(parser.parse_args()))
    except Exception as exc:
        # Never render database exception text, SQL parameters or credentials.
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
