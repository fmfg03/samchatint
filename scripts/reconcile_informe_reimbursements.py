#!/usr/bin/env python3
"""Inventory or reconcile approved informe reimbursements.

Dry-run is the default. ``--apply`` only creates or promotes a reimbursement
when the approved informe has both a budget concept and an approval audit row.
It never creates an approval record.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from devnous.gastos.models import Documento  # noqa: E402
from devnous.gastos.services.reimbursement_payment_run_service import (  # noqa: E402
    ensure_approved_informe_reimbursement_for_payment_run,
    get_informe_reimbursement_payment_readiness,
    should_apply_informe_reimbursement_reconciliation,
)


def _database_url() -> str:
    value = (os.getenv("DATABASE_URL") or "").strip()
    if not value:
        raise SystemExit("DATABASE_URL is required")
    return value.replace("postgresql://", "postgresql+asyncpg://", 1)


async def _run(args: argparse.Namespace) -> int:
    refs = [str(value).strip() for value in args.refs if str(value).strip()]
    if not refs:
        raise SystemExit("at least one --refs value is required")

    engine = create_async_engine(_database_url())
    maker = async_sessionmaker(engine, expire_on_commit=False)
    rows: list[dict[str, object]] = []
    try:
        async with maker() as session:
            result = await session.execute(
                select(Documento)
                .where(
                    Documento.tipo == "INFORME",
                    Documento.estado == "aprobado",
                    Documento.referencia_operaciones.in_(refs),
                )
                .order_by(Documento.referencia_operaciones, Documento.creado_en)
            )
            informes = list(result.scalars().all())
            found_refs = {
                str(informe.referencia_operaciones or "") for informe in informes
            }
            for missing_ref in sorted(set(refs) - found_refs):
                rows.append(
                    {
                        "operation_ref": missing_ref,
                        "status": "blocked_informe_not_found",
                        "detail": "No existe un informe aprobado para la referencia.",
                        "solicitud_id": None,
                    }
                )
            for informe in informes:
                readiness = await get_informe_reimbursement_payment_readiness(
                    session,
                    informe_doc=informe,
                )
                item: dict[str, object] = {
                    "operation_ref": str(informe.referencia_operaciones or ""),
                    "informe": informe.numero_referencia,
                    "status": readiness.status,
                    "detail": readiness.detail,
                    "solicitud_id": str(readiness.solicitud_id)
                    if readiness.solicitud_id
                    else None,
                }
                if should_apply_informe_reimbursement_reconciliation(
                    apply=args.apply,
                    readiness=readiness,
                ):
                    routing = await ensure_approved_informe_reimbursement_for_payment_run(
                        session,
                        informe_doc=informe,
                        actor_id=informe.empleado_id,
                    )
                    item["reconciled"] = routing.changed
                    item["warning"] = routing.warning
                    item["solicitud_id"] = (
                        str(routing.solicitud_id) if routing.solicitud_id else None
                    )
                rows.append(item)
            if args.apply:
                await session.commit()
            else:
                await session.rollback()
    finally:
        await engine.dispose()

    print(json.dumps({"apply": args.apply, "items": rows}, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refs", nargs="+", required=True)
    parser.add_argument("--apply", action="store_true")
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
