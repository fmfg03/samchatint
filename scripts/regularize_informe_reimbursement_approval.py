#!/usr/bin/env python3
"""Regularize one approved informe reimbursement with a verified superadmin.

The command defaults to dry-run.  ``--apply`` records a new current-time
regularization audit; it never recreates or backdates a missing approval.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from uuid import UUID

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from devnous.gastos.models import Documento  # noqa: E402
from devnous.gastos.services import (  # noqa: E402
    reimbursement_payment_run_service as rprs,
)


def _database_url() -> str:
    value = (os.getenv("DATABASE_URL") or "").strip()
    if not value:
        raise SystemExit("DATABASE_URL is required")
    return value.replace("postgresql://", "postgresql+asyncpg://", 1)


async def _run(args: argparse.Namespace) -> int:
    informe_id = UUID(args.informe_id)
    solicitud_id = UUID(args.solicitud_id)
    actor_id = UUID(args.actor_id)
    get_readiness = rprs.get_informe_reimbursement_payment_readiness
    regularize_reimbursement = rprs.regularize_approved_informe_reimbursement
    engine = create_async_engine(_database_url())
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with maker() as session:
            informe = await session.get(Documento, informe_id)
            solicitud = await session.get(Documento, solicitud_id)
            if informe is None or solicitud is None:
                raise SystemExit("informe and solicitud must exist")
            if str(informe.referencia_operaciones or "") != args.expected_ref:
                raise SystemExit("informe does not match --expected-ref")

            before = await get_readiness(session, informe_doc=informe)
            receipt: dict[str, object] = {
                "apply": args.apply,
                "expected_ref": args.expected_ref,
                "informe": informe.numero_referencia,
                "informe_id": str(informe.id),
                "solicitud": solicitud.numero_referencia,
                "solicitud_id": str(solicitud.id),
                "before": {"status": before.status, "detail": before.detail},
            }
            if args.apply:
                result = await regularize_reimbursement(
                    session,
                    informe_id=informe_id,
                    solicitud_id=solicitud_id,
                    actor_id=actor_id,
                    motivo=args.motivo,
                )
                await session.commit()
                refreshed = await get_readiness(session, informe_doc=informe)
                receipt["result"] = {
                    "changed": result.changed,
                    "status": result.status,
                    "detail": result.detail,
                }
                receipt["after"] = {
                    "status": refreshed.status,
                    "detail": refreshed.detail,
                }
            else:
                await session.rollback()
            print(json.dumps(receipt, ensure_ascii=False, indent=2))
    finally:
        await engine.dispose()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--informe-id", required=True)
    parser.add_argument("--solicitud-id", required=True)
    parser.add_argument("--actor-id", required=True)
    parser.add_argument("--expected-ref", required=True)
    parser.add_argument("--motivo", required=True)
    parser.add_argument("--apply", action="store_true")
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
