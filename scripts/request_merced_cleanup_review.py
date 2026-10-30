#!/usr/bin/env python3
"""Request cleanup review for the exact approved Merced batch; dry-run by default.

Run with PYTHONPATH=src:. and DATABASE_URL supplied by the operator environment.
Apply requires a permitted actor, the preview digest, and a new backup file.
No financial field is changed; the only inserts are Aprobacion review requests.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from devnous.gastos.models import Empleado
from devnous.gastos.routes.dependencies import (
    _load_effective_permissions,
    require_permission_factory,
)
from devnous.gastos.services.access_control_service import explicit_tool_decision
from devnous.gastos.services.cleanup_review_service import (
    BATCH,
    load_review_states,
    request_review,
)

MANIFEST = Path(__file__).parent / "data/merced_cleanup_review_20261030.json"
EXPECTED_REFS = {
    "299",
    "300",
    "301",
    "302",
    "303",
    "304",
    "305",
    "306",
    "307",
    "308",
    "309",
    "310",
    "311",
    "312",
    "313",
    "314",
    "319",
    "320",
    "321",
    "322",
    "323",
    "324",
    "325",
    "326",
}
SELECTION_SQL = """
SELECT t.id AS project_id,d.id AS documento_id,d.referencia_operaciones,
d.numero_referencia AS solicitud,d.estado AS estado_documento,d.pagado_en,
d.cfdi_report_id AS documento_cfdi_id,er.id AS expense_id,
er.numero_referencia AS gasto,er.fecha,er.gasto_cantidad,er.currency,
er.estado_gasto,er.cuenta_contable_id,er.contra_cuenta_contable_id,er.cfdi_report_id
FROM documentos d JOIN tournaments t ON t.id=d.torneo_id
JOIN expense_reports er ON er.id=d.gasto_generado_id
WHERE d.tipo='SOLICITUD' AND t.id=CAST(:project_id AS uuid)
AND lower(trim(t.name))='la merced' AND d.estado='pagado'
AND er.estado_gasto='activo' AND er.fecha>='2026-10-01' AND er.fecha<'2026-11-01'
ORDER BY er.id
"""


def normalized(value):
    """Serialize DB scalars in the same format as the frozen manifest."""
    return json.loads(json.dumps(value, default=str))


def validate_selection(manifest: dict, rows: list[dict]) -> str:
    """Reject identity, count, period, or financial drift before any write."""
    items = manifest.get("items", [])
    if (
        manifest.get("batch") != BATCH
        or len(items) != 24
        or len({row["expense_id"] for row in items}) != 24
        or {row["referencia_operaciones"] for row in items} != EXPECTED_REFS
        or items != rows
    ):
        raise ValueError(
            "La selección o sus valores cambiaron; no se aplicó la revisión."
        )
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


async def authorize_actor(session: AsyncSession, actor_id: UUID) -> None:
    """Reuse financial permissions and honor persisted cleanup denials."""
    actor = (
        await session.execute(select(Empleado).where(Empleado.id == actor_id))
    ).scalar_one_or_none()
    if actor is None or not actor.activo:
        raise PermissionError("Se requiere un actor activo existente.")
    proxy = SimpleNamespace(
        id=actor.id,
        rol=actor.rol,
        departamento=actor.departamento,
        permissions=await _load_effective_permissions(session, actor_id),
    )
    guard = require_permission_factory(
        ["admin.finanzas.manage", "finanzas.manage", "admin.*"],
        allowed_roles=["admin", "finanzas", "superadmin", "super_admin"],
    )
    await guard(proxy)
    for action in ("ver", "editar"):
        if (
            await explicit_tool_decision(
                session, actor, "admin.gastos.limpieza", action
            )
            is False
        ):
            raise PermissionError("Acceso a limpieza contable denegado.")


async def run(args: argparse.Namespace) -> dict:
    """Preview or activate one transaction; reruns never reopen completed reviews."""
    manifest = json.loads(MANIFEST.read_text())
    url = make_url(os.environ["DATABASE_URL"]).set(drivername="postgresql+asyncpg")
    engine = create_async_engine(url, echo=False)
    try:
        async with AsyncSession(engine) as session:
            if not args.apply:
                await session.execute(text("SET TRANSACTION READ ONLY"))
            await session.execute(text("SET LOCAL statement_timeout = '20000'"))
            await session.execute(text("SET LOCAL lock_timeout = '5000'"))
            sql = SELECTION_SQL + (" FOR UPDATE OF d, er" if args.apply else "")
            result = await session.execute(
                text(sql), {"project_id": manifest["project_id"]}
            )
            rows = normalized([dict(row) for row in result.mappings()])
            digest = validate_selection(manifest, rows)
            ids = [UUID(row["expense_id"]) for row in rows]
            states = await load_review_states(session, ids)
            receipt: dict[str, Any] = {
                "batch": BATCH,
                "count": len(rows),
                "selection_sha256": digest,
                "pending_before": sum(state.pending for state in states.values()),
                "confirmed_before": sum(not state.pending for state in states.values()),
                "mode": "apply" if args.apply else "dry-run",
                "requested": 0,
            }
            if args.apply:
                if (
                    args.confirm_sha256 != digest
                    or not args.actor_id
                    or not args.snapshot
                ):
                    raise ValueError(
                        "Apply requiere actor, snapshot y hash de la vista previa."
                    )
                actor_id = UUID(args.actor_id)
                await authorize_actor(session, actor_id)
                snapshot = {
                    "selection": manifest,
                    "receipt": receipt.copy(),
                    "actor_id": str(actor_id),
                    "previous_reviews": {
                        str(key): {"pending": state.pending, "fecha": str(state.fecha)}
                        for key, state in states.items()
                    },
                }
                # Exclusive creation prevents overwriting recovery evidence.
                fd = os.open(args.snapshot, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w") as out:
                    out.write(json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n")
                    out.flush()
                    os.fsync(out.fileno())
                for expense_id in ids:
                    receipt["requested"] += int(
                        await request_review(session, expense_id, actor_id)
                    )
                # Verify that marking did not change the original financial selection.
                after = await session.execute(
                    text(SELECTION_SQL), {"project_id": manifest["project_id"]}
                )
                validate_selection(
                    manifest, normalized([dict(r) for r in after.mappings()])
                )
                await session.commit()
            else:
                await session.rollback()
            return receipt
    finally:
        await engine.dispose()


def main() -> int:
    """Parse explicit apply controls and emit only a non-secret receipt."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--actor-id")
    parser.add_argument("--confirm-sha256")
    parser.add_argument("--snapshot")
    args = parser.parse_args()
    try:
        print(json.dumps(asyncio.run(run(args)), ensure_ascii=False))
        return 0
    except Exception as exc:
        # Never print a database exception that might contain connection secrets.
        print(json.dumps({"status": "blocked", "error_type": type(exc).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
