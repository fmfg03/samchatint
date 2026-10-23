#!/usr/bin/env python3
"""Preview/apply the approved provisional policy and only S-26000450's route.

Default preview is READ ONLY. No schema, folio, money, payment, budget,
financial approval or notification is changed. Existing organizational positions
are never repurposed. Apply requires an exact fresh plan and source commit.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID

from sqlalchemy import event, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

POLICY_VERSION = "p20261023"
DOCUMENT_ID = "7de8b57d-85e3-4e52-ab60-990ab5fc04b6"
DOCUMENT_REFERENCE = "S-26000450"
ACTOR_ID = "e3d13040-2360-420f-98a1-516440ef63c3"
GN = "89375388-40fb-4412-aade-56a4a807ab7c"
GV = "d21a5407-aa5f-42d5-a90e-f3293b4eb907"
ODILON = "f9aebcf3-ef28-423c-8a77-f41c9e4e885a"
OLOF = "7de0ca84-dac9-4456-ae96-0ed26e15bcbb"
LUIS_ANGEL = "723ae3e1-4329-4e48-8662-f9d9d0753cca"
GN_POSITION = "aprobador_federico_gn"
GV_POSITION = "supervision_aprobaciones_federico_gv"
FINANCE_POSITION = "aprobadores_finanzas_provisional"
EXCEPTION_POSITION = "aprobaciones_escalan_gn"
OPERATIONS_POSITION = "director_operaciones"
AUDIT_ACTION = "actualizar_ruta_autorizacion"

POSITIONS = {
    GN_POSITION: {"label": "Aprobador provisional Federico GN", "members": [GN]},
    GV_POSITION: {
        "label": "Supervisión provisional de aprobaciones Federico GV",
        "members": [GV],
    },
    FINANCE_POSITION: {
        "label": "Aprobadores provisionales de Finanzas",
        "members": [LUIS_ANGEL, ACTOR_ID],
    },
    EXCEPTION_POSITION: {
        "label": "Personas con escalamiento provisional a Federico GN",
        "members": [ODILON, OLOF, LUIS_ANGEL, GV],
    },
}
PROJECT_MATRIX = {
    "Liga Telmex Telcel de Béisbol": [OPERATIONS_POSITION],
    "Copa Telmex Telcel de Fútbol": [OPERATIONS_POSITION],
    "De la Calle a la Cancha": [OPERATIONS_POSITION],
    "Futbolito Bimbo": [OPERATIONS_POSITION],
    "La Merced": [OPERATIONS_POSITION],
    "Becarios Telmex (México Siglo XXI)": [OPERATIONS_POSITION],
    "Gastos Administrativos - Operaciones": [OPERATIONS_POSITION],
    "Otros Proyectos": [OPERATIONS_POSITION, GN_POSITION],
    "Homeless World Cup México": [OPERATIONS_POSITION, GN_POSITION],
    "Gastos Administrativos - Dirección General": [GN_POSITION],
    "Gestión de Patrocinios": [GN_POSITION],
    "Gestión RRSS y Transmisiones": [GN_POSITION],
    "Promoción de Negocios": [GN_POSITION],
    "Gastos Administrativos - Administración y Finanzas": [FINANCE_POSITION],
    "Otros ingresos": [FINANCE_POSITION],
}
EXCLUDED_FOLIO_PROJECTS = {"Gestión de Patrocinios", "Gestión RRSS y Transmisiones"}
ALLOWED_WRITE_TABLES = {
    "authorization_positions",
    "authorization_position_assignments",
    "project_authorization_rules",
    "documento_authorization_routes",
    "aprobaciones",
}


class AtomicPolicySession(AsyncSession):
    """Only the transaction context manager may commit this operation."""

    async def commit(self) -> None:
        raise ValueError("Nested commits are forbidden in the approved operation")


def normalized(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str, ensure_ascii=False))


def plan_sha(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, default=str, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


async def rows(session: Any, sql: str, params: dict | None = None) -> list[dict]:
    result = await session.execute(text(sql), params or {})
    return [dict(row) for row in result.mappings().all()]


def source_identity() -> dict[str, str]:
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    files = [
        Path(__file__),
        ROOT / "src/devnous/gastos/services/project_authorization_service.py",
    ]
    return {
        "commit": revision,
        "code_sha256": plan_sha(
            {
                str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in files
            }
        ),
    }


def canonical_api():
    # Match the application initialization order; do not start the web runtime.
    import devnous.gastos  # noqa: F401
    from devnous.gastos.services import project_authorization_service as policy

    return policy


def financial_terminal_api():
    """Use the same durable financial evidence gate as document workflows."""
    from devnous.gastos.services.documento_workflow_service import (
        documento_financial_terminal_reason,
    )

    return documento_financial_terminal_reason


async def snapshot(session: Any) -> dict:
    data = {}
    for key, query in {
        "positions": "SELECT * FROM authorization_positions ORDER BY position_key",
        "assignments": "SELECT * FROM authorization_position_assignments ORDER BY position_key,empleado_id",
        "rules": "SELECT * FROM project_authorization_rules ORDER BY tournament_id",
        "routes": "SELECT * FROM documento_authorization_routes ORDER BY documento_id",
        "employees": "SELECT id,nombre,rol,activo FROM empleados WHERE id=ANY(CAST(:employee_ids AS uuid[])) ORDER BY id",
        "projects": "SELECT id,name,active FROM tournaments WHERE name=ANY(:project_names) ORDER BY id",
        "document": "SELECT * FROM documentos WHERE id=CAST(:document_id AS uuid)",
        "approvals": "SELECT * FROM aprobaciones WHERE entidad_id=CAST(:document_id AS uuid) ORDER BY id",
    }.items():
        data[key] = await rows(
            session,
            query,
            {
                "employee_ids": [GN, GV, ODILON, OLOF, LUIS_ANGEL, ACTOR_ID],
                "project_names": list(PROJECT_MATRIX),
                "document_id": DOCUMENT_ID,
            },
        )
    if len(data["document"]) != 1:
        raise ValueError("Selected document must exist exactly once")
    return normalized(data)


def validate_identity_and_state(before: dict) -> None:
    employees = {row["id"]: row for row in before["employees"]}
    expected = {GN, GV, ODILON, OLOF, LUIS_ANGEL, ACTOR_ID}
    if set(employees) != expected or not all(
        row["activo"] for row in employees.values()
    ):
        raise ValueError("Approved employee identities must all exist and be active")
    if employees[ACTOR_ID]["rol"] not in {"superadmin", "super_admin"}:
        raise ValueError("An active configured superadmin actor is required")
    doc = before["document"][0]
    if doc["numero_referencia"] != DOCUMENT_REFERENCE or doc["estado"] != "enviado":
        raise ValueError("Selected document changed reference or is not enviado")
    if str(doc.get("referencia_operaciones") or "").strip() != "363":
        raise ValueError("Historical Operations folio 363 must be preserved")
    if doc.get("aprobado_en") or doc.get("pagado_en"):
        raise ValueError("A previously approved or paid document is not repairable")
    if doc.get("gasto_generado_id"):
        raise ValueError("Generated payment expense blocks this selected repair")
    if any(
        str(row["accion"]).strip().lower()
        in {"aprobar", "pagar", "regularizar_aprobacion_reembolso"}
        for row in before["approvals"]
    ):
        raise ValueError(
            "Existing financial approval or payment blocks this selected repair"
        )
    operations_members = {
        row["empleado_id"]
        for row in before["assignments"]
        if row["position_key"] == OPERATIONS_POSITION and row["active"]
    }
    operations = next(
        (r for r in before["positions"] if r["position_key"] == OPERATIONS_POSITION),
        None,
    )
    if operations_members != {ODILON} or not operations or not operations["active"]:
        raise ValueError("Operations holder differs from the approved identity")
    for assignment in before["assignments"]:
        key = assignment["position_key"]
        if (
            key in POSITIONS
            and assignment["empleado_id"] not in POSITIONS[key]["members"]
        ):
            raise ValueError(
                "Unexpected existing member in a new approval-only position"
            )


def configuration_plan(before: dict) -> dict:
    validate_identity_and_state(before)
    by_name: dict[str, list[dict]] = {}
    for project in before["projects"]:
        by_name.setdefault(project["name"], []).append(project)
    if set(by_name) != set(PROJECT_MATRIX) or any(
        len(v) != 1 or not v[0]["active"] for v in by_name.values()
    ):
        raise ValueError(
            "Each approved project must resolve to exactly one active UUID"
        )
    existing_rules = {row["tournament_id"]: row for row in before["rules"]}
    proposed = []
    for name, keys in PROJECT_MATRIX.items():
        project = by_name[name][0]
        previous = existing_rules.get(project["id"])
        # The new matrix does not invent folio eligibility. With no previous rule,
        # FALSE means this row imposes no folio requirement; the canonical emitter
        # retains its independent pre-existing employee/project behavior.
        requirement = bool(
            previous
            and previous["active"]
            and previous["requires_operations_reference"]
        )
        if name in EXCLUDED_FOLIO_PROJECTS:
            requirement = False
        proposed.append(
            {
                "tournament_id": project["id"],
                "name": name,
                "eligible_position_keys": keys,
                "requires_operations_reference": requirement,
                "active": True,
            }
        )
    rrss_id = by_name["Gestión RRSS y Transmisiones"][0]["id"]
    if before["document"][0].get("torneo_id") != rrss_id:
        raise ValueError(
            "The selected case no longer belongs to the approved RRSS project"
        )
    return {"positions": POSITIONS, "project_rules": proposed}


async def build_plan(session: Any, before: dict, identity: dict | None = None) -> dict:
    changes = configuration_plan(before)
    policy = canonical_api()
    document = SimpleNamespace(**before["document"][0])
    terminal_reason = await financial_terminal_api()(session, document)
    if terminal_reason:
        raise ValueError(
            f"Selected document has durable financial effects: {terminal_reason}"
        )
    context = normalized(await policy.resolve_document_route_context(session, document))
    rrss = next(
        row["tournament_id"]
        for row in changes["project_rules"]
        if row["name"] == "Gestión RRSS y Transmisiones"
    )
    if context["tournament_id"] != rrss:
        raise ValueError(
            "Canonical effective project does not match the selected RRSS case"
        )
    target = policy.ProjectAuthorizationRoute((GN_POSITION,), False, "project")
    source = policy.build_route_context_source(
        route=target,
        **context,
        eligible_employee_ids=[GN],
    )
    if not source.startswith(POLICY_VERSION + ":") or len(source) > 100:
        raise ValueError("Canonical source does not match the approved bounded format")
    plan = {
        "policy_version": POLICY_VERSION,
        "actor_id": ACTOR_ID,
        "source": identity or source_identity(),
        "before_sha256": plan_sha(before),
        "configuration": changes,
        "document_context": context,
        "selected_document": DOCUMENT_ID,
        "expected_route": {
            "eligible_position_keys": [GN_POSITION],
            "eligible_empleado_ids": [GN],
            "requires_operations_reference": False,
            "source": source,
        },
    }
    plan["sha256"] = plan_sha(plan)
    return plan


async def apply_plan(
    session: Any, before: dict, plan: dict, approved_hash: str
) -> None:
    unsigned = {k: v for k, v in plan.items() if k != "sha256"}
    if approved_hash != plan.get("sha256") or plan_sha(unsigned) != approved_hash:
        raise ValueError("The reviewed plan hash is invalid")
    if plan_sha(before) != plan["before_sha256"]:
        raise ValueError("Selected state changed after preview")
    validate_identity_and_state(before)
    for key, position in POSITIONS.items():
        await session.execute(
            text(
                """INSERT INTO authorization_positions(position_key,label,active)
            VALUES(:key,:label,TRUE) ON CONFLICT(position_key) DO UPDATE SET label=EXCLUDED.label,active=TRUE"""
            ),
            {"key": key, "label": position["label"]},
        )
        for employee in position["members"]:
            await session.execute(
                text(
                    """INSERT INTO authorization_position_assignments(position_key,empleado_id,active)
                VALUES(:key,CAST(:employee AS uuid),TRUE) ON CONFLICT(position_key,empleado_id) DO UPDATE SET active=TRUE"""
                ),
                {"key": key, "employee": employee},
            )
    for rule in plan["configuration"]["project_rules"]:
        await session.execute(
            text(
                """INSERT INTO project_authorization_rules(tournament_id,eligible_position_keys,requires_operations_reference,active)
            VALUES(CAST(:id AS uuid),CAST(:keys AS jsonb),:requires,TRUE)
            ON CONFLICT(tournament_id) DO UPDATE SET eligible_position_keys=EXCLUDED.eligible_position_keys,
            requires_operations_reference=EXCLUDED.requires_operations_reference,active=TRUE"""
            ),
            {
                "id": rule["tournament_id"],
                "keys": json.dumps(rule["eligible_position_keys"]),
                "requires": rule["requires_operations_reference"],
            },
        )
    policy = canonical_api()
    await policy.resolve_and_snapshot_document_route(
        session,
        SimpleNamespace(**before["document"][0]),
        audit_actor_id=ACTOR_ID,
        audit_context={
            "policy_version": POLICY_VERSION,
            "plan_sha256": approved_hash,
            "document_context": plan["document_context"],
            "reason": "Approved provisional policy; preserve historical Operations folio 363",
        },
    )
    await session.flush()
    current_route = await rows(
        session,
        "SELECT * FROM documento_authorization_routes WHERE documento_id=CAST(:id AS uuid)",
        {"id": DOCUMENT_ID},
    )
    if len(current_route) != 1 or any(
        normalized(current_route[0][k]) != v for k, v in plan["expected_route"].items()
    ):
        raise ValueError("Canonical route differs from the reviewed GN route")


def verify_after(before: dict, after: dict, plan: dict) -> dict:
    if (
        before["document"] != after["document"]
        or before["employees"] != after["employees"]
        or before["projects"] != after["projects"]
    ):
        raise ValueError("Document, employee or project data unexpectedly changed")
    for key in ("positions", "assignments"):
        old = [r for r in before[key] if r["position_key"] not in POSITIONS]
        new = [r for r in after[key] if r["position_key"] not in POSITIONS]
        if old != new:
            raise ValueError(
                "Organizational authority changed outside the approved positions"
            )
    expected_positions = [
        {"position_key": key, "label": p["label"], "active": True}
        for key, p in POSITIONS.items()
    ]
    if sorted(
        [r for r in after["positions"] if r["position_key"] in POSITIONS],
        key=lambda r: r["position_key"],
    ) != sorted(expected_positions, key=lambda r: r["position_key"]):
        raise ValueError(
            "Approval-only positions do not match the approved configuration"
        )
    expected_assignments = [
        {"position_key": key, "empleado_id": employee, "active": True}
        for key, p in POSITIONS.items()
        for employee in p["members"]
    ]
    order = lambda r: (r["position_key"], r["empleado_id"])
    if sorted(
        [r for r in after["assignments"] if r["position_key"] in POSITIONS], key=order
    ) != sorted(expected_assignments, key=order):
        raise ValueError(
            "Approval-only memberships differ from the reviewed identities"
        )
    selected_projects = {
        r["tournament_id"] for r in plan["configuration"]["project_rules"]
    }
    if [r for r in before["rules"] if r["tournament_id"] not in selected_projects] != [
        r for r in after["rules"] if r["tournament_id"] not in selected_projects
    ]:
        raise ValueError("Unselected project rules changed")
    rules = {r["tournament_id"]: r for r in after["rules"]}
    for proposed in plan["configuration"]["project_rules"]:
        actual = rules[proposed["tournament_id"]]
        if any(
            actual[k] != proposed[k]
            for k in (
                "eligible_position_keys",
                "requires_operations_reference",
                "active",
            )
        ):
            raise ValueError("Project matrix does not match the reviewed plan")
    if [r for r in before["routes"] if r["documento_id"] != DOCUMENT_ID] != [
        r for r in after["routes"] if r["documento_id"] != DOCUMENT_ID
    ]:
        raise ValueError("An unselected document route changed")
    selected = [r for r in after["routes"] if r["documento_id"] == DOCUMENT_ID]
    if len(selected) != 1 or any(
        selected[0][k] != value for k, value in plan["expected_route"].items()
    ):
        raise ValueError("Selected GN route does not match the preview")
    old_approvals = {r["id"]: r for r in before["approvals"]}
    new_approvals = {r["id"]: r for r in after["approvals"]}
    if any(new_approvals.get(key) != row for key, row in old_approvals.items()):
        raise ValueError("Approval history was modified")
    added = [r for key, r in new_approvals.items() if key not in old_approvals]
    if (
        len(added) != 1
        or added[0]["accion"] != AUDIT_ACTION
        or added[0]["aprobador_id"] != ACTOR_ID
    ):
        raise ValueError("Exactly one attributable technical audit is required")
    payload = json.loads(added[0]["comentario"])
    if (
        payload.get("audit_context", {}).get("plan_sha256") != plan["sha256"]
        or payload.get("audit_context", {}).get("document_context")
        != plan["document_context"]
    ):
        raise ValueError("Audit is not bound to the reviewed plan and context")
    return {
        "audit_id": added[0]["id"],
        "after_sha256": plan_sha(after),
        "document_id": DOCUMENT_ID,
        "document_reference": DOCUMENT_REFERENCE,
        "folio_preserved": "363",
        "state_preserved": "enviado",
        "configuration_positions": 4,
        "configuration_assignments": 8,
        "project_rules": 15,
        "other_routes_preserved": True,
        "organizational_assignments_preserved": True,
    }


def save_json(directory: Path, name: str, payload: Any) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    path = directory / name
    temporary = directory / (name + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as output:
        json.dump(payload, output, default=str, ensure_ascii=False, indent=2)
    os.replace(temporary, path)
    os.chmod(path, 0o600)


def database_url(args: argparse.Namespace) -> str:
    url = os.environ.get("DATABASE_URL")
    if args.env_file:
        from dotenv import dotenv_values

        url = dotenv_values(args.env_file).get("DATABASE_URL")
    if not url:
        raise ValueError(
            "DATABASE_URL or an explicitly selected --env-file is required"
        )
    parsed = make_url(url)
    if parsed.get_backend_name() != "postgresql":
        raise ValueError("This operation requires PostgreSQL")
    return str(
        parsed.set(drivername="postgresql+asyncpg").render_as_string(
            hide_password=False
        )
    )


async def run(args: argparse.Namespace) -> dict:
    if str(args.actor_id) != ACTOR_ID:
        raise ValueError("This approved operation is attributed only to Juan Pablo")
    source = source_identity()
    if args.apply and (
        not args.expected_hash or args.expected_commit != source["commit"]
    ):
        raise ValueError(
            "Apply requires the reviewed hash and exact deployed source commit"
        )
    engine = create_async_engine(database_url(args), hide_parameters=True)

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def deny_unapproved_sql(conn, cursor, statement, parameters, context, many):
        del conn, cursor, parameters, context, many
        sql = statement.strip()
        first = sql.split()[0].lower()
        if first in {"insert", "update"}:
            match = re.match(
                r"(?is)^(?:INSERT\s+INTO|UPDATE)\s+([a-z_]+)(?:\s|\()", sql
            )
            if not args.apply or not match or match[1] not in ALLOWED_WRITE_TABLES:
                raise ValueError(
                    "SQL mutation outside approved configuration and selected-route scope"
                )
        elif first not in {"select", "set", "lock", "show"}:
            raise ValueError("Schema, deletion and unapproved statements are forbidden")
        if not args.apply and (
            first == "lock" or re.search(r"\bFOR\s+(?:UPDATE|SHARE)\b", sql, re.I)
        ):
            raise ValueError("The preview is strictly read-only")

    try:
        async with AtomicPolicySession(engine) as session:
            async with session.begin():
                await session.execute(
                    text(
                        "SET TRANSACTION ISOLATION LEVEL SERIALIZABLE"
                        if args.apply
                        else "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
                    )
                )
                await session.execute(text("SET LOCAL statement_timeout='30s'"))
                await session.execute(text("SET LOCAL lock_timeout='5s'"))
                if args.apply:
                    await session.execute(
                        text(
                            "LOCK TABLE authorization_positions, authorization_position_assignments, project_authorization_rules IN SHARE ROW EXCLUSIVE MODE"
                        )
                    )
                    await session.execute(
                        text(
                            "SELECT id FROM documentos WHERE id=CAST(:id AS uuid) FOR UPDATE"
                        ),
                        {"id": DOCUMENT_ID},
                    )
                    await session.execute(
                        text(
                            "SELECT id FROM empleados WHERE id=ANY(CAST(:ids AS uuid[])) FOR SHARE"
                        ),
                        {"ids": [GN, GV, ODILON, OLOF, LUIS_ANGEL, ACTOR_ID]},
                    )
                    await session.execute(
                        text(
                            "SELECT id FROM tournaments WHERE name=ANY(:names) ORDER BY id FOR SHARE"
                        ),
                        {"names": list(PROJECT_MATRIX)},
                    )
                before = await snapshot(session)
                plan = await build_plan(session, before, source)
                if not args.apply:
                    save_json(args.output_dir, "preview.json", plan)
                    save_json(args.output_dir, "preview-before.json", before)
                    return {
                        "status": "READ_ONLY_PREVIEW",
                        "plan_sha256": plan["sha256"],
                        "source_commit": source["commit"],
                        "positions": 4,
                        "assignments": 8,
                        "projects": 15,
                        "selected_document": DOCUMENT_REFERENCE,
                    }
                if plan["sha256"] != args.expected_hash:
                    raise ValueError(
                        "Fresh plan differs from reviewed hash; regenerate the preview"
                    )
                save_json(args.output_dir, "before.json", before)
                save_json(args.output_dir, "confirmed-plan.json", plan)
                await apply_plan(session, before, plan, args.expected_hash)
                after = await snapshot(session)
                receipt = verify_after(before, after, plan)
                save_json(args.output_dir, "after-pending.json", after)
        # A new connection verifies durable state after the only commit completed.
        async with AsyncSession(engine) as verification:
            await verification.execute(
                text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            )
            committed = await snapshot(verification)
            receipt = verify_after(before, committed, plan)
        receipt.update(
            {
                "status": "COMMITTED_VERIFIED",
                "plan_sha256": plan["sha256"],
                "source": source,
                "actor_id": ACTOR_ID,
                "verified_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        save_json(args.output_dir, "after.json", committed)
        save_json(args.output_dir, "receipt.json", receipt)
        return receipt
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-hash")
    parser.add_argument("--expected-commit")
    parser.add_argument("--actor-id", type=UUID, default=UUID(ACTOR_ID))
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(run(args))
    print(json.dumps(result, default=str, ensure_ascii=False))


if __name__ == "__main__":
    main()
