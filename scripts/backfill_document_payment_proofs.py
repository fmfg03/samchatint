#!/usr/bin/env python3
"""Backfill SOLICITUD payments that already have a payment proof.

Dry-run is the default. Use --execute with --actor-id to apply changes.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import traceback
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from devnous.gastos.routes.dependencies import (  # noqa: E402
    _load_empleado_proxy_by_id,
)
from devnous.gastos.services.documento_payment_service import (  # noqa: E402
    DocumentoPaymentError,
    register_document_payment,
)


def _load_env_file(path: str | None) -> None:
    if not path:
        return
    env_path = Path(path)
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


def _async_database_url() -> str:
    url = (os.getenv("DATABASE_URL") or "").strip()
    if not url:
        raise SystemExit("DATABASE_URL is required.")
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


async def _find_candidates(
    session: AsyncSession,
    *,
    refs: list[str],
    limit: int,
) -> list[dict[str, Any]]:
    filters = [
        "d.tipo = 'SOLICITUD'",
        "lower(coalesce(a.categoria, '')) = 'comprobante_pago'",
        "(d.estado IS DISTINCT FROM 'pagado' OR d.pagado_en IS NULL)",
        "d.gasto_generado_id IS NULL",
    ]
    params: dict[str, Any] = {"limit": limit}
    if refs:
        filters.append("d.numero_referencia = ANY(:refs)")
        params["refs"] = refs

    result = await session.execute(
        text(
            f"""
            SELECT
                d.id::text AS documento_id,
                d.numero_referencia,
                d.estado,
                d.aprobado_en,
                d.pagado_en,
                d.gasto_generado_id::text AS gasto_generado_id,
                d.fecha_pago,
                d.monto_solicitado,
                COUNT(a.id) AS comprobantes_pago,
                MAX(a.subido_en) AS ultimo_comprobante_pago
            FROM documentos d
            JOIN adjuntos a ON a.documento_id = d.id
            WHERE {" AND ".join(filters)}
            GROUP BY d.id, d.numero_referencia, d.estado, d.aprobado_en,
                     d.pagado_en, d.gasto_generado_id, d.fecha_pago,
                     d.monto_solicitado
            ORDER BY MAX(a.subido_en) DESC NULLS LAST, d.numero_referencia
            LIMIT :limit
            """
        ),
        params,
    )
    return [dict(row) for row in result.mappings().all()]


async def _run(args: argparse.Namespace) -> int:
    _load_env_file(args.env_file)
    engine = create_async_engine(_async_database_url())
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    refs = [item.strip() for item in args.refs if item.strip()]

    async with session_maker() as session:
        candidates = await _find_candidates(
            session,
            refs=refs,
            limit=args.limit,
        )
        print(f"candidates={len(candidates)} execute={args.execute}")
        for candidate in candidates:
            print(
                "candidate "
                f"ref={candidate['numero_referencia']} "
                f"id={candidate['documento_id']} "
                f"estado={candidate['estado']} "
                f"proofs={candidate['comprobantes_pago']} "
                f"ultimo={candidate['ultimo_comprobante_pago']}"
            )

    if not args.execute:
        await engine.dispose()
        return 0

    if not args.actor_id:
        await engine.dispose()
        raise SystemExit("--actor-id is required with --execute.")

    actor_uuid = UUID(str(args.actor_id))
    success = 0
    failed = 0
    async with session_maker() as session:
        actor = await _load_empleado_proxy_by_id(session, actor_uuid)
        if actor is None:
            await engine.dispose()
            raise SystemExit(f"actor_not_found={actor_uuid}")

    for candidate in candidates:
        async with session_maker() as session:
            actor = await _load_empleado_proxy_by_id(session, actor_uuid)
            try:
                result = await register_document_payment(
                    session,
                    documento_id=candidate["documento_id"],
                    actor_id=actor_uuid,
                    actor=actor,
                    notify=not args.suppress_notifications,
                )
                print(
                    "paid "
                    f"ref={result.documento.numero_referencia} "
                    f"id={result.documento.id} "
                    f"estado={result.documento.estado} "
                    f"pagado_en={result.documento.pagado_en}"
                )
                success += 1
            except DocumentoPaymentError as exc:
                await session.rollback()
                print(
                    "failed "
                    f"ref={candidate['numero_referencia']} "
                    f"id={candidate['documento_id']} "
                    f"code={exc.code} message={exc.message}"
                )
                failed += 1
            except Exception as exc:
                await session.rollback()
                if args.tracebacks:
                    traceback.print_exc()
                print(
                    "failed "
                    f"ref={candidate['numero_referencia']} "
                    f"id={candidate['documento_id']} "
                    f"error={type(exc).__name__}: {exc}"
                )
                failed += 1

    await engine.dispose()
    print(f"summary success={success} failed={failed}")
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file",
        default=os.getenv("SAMCHAT_ENV_FILE", "/etc/samchat/samchat.env"),
    )
    parser.add_argument("--refs", nargs="*", default=[])
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--actor-id")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--tracebacks", action="store_true")
    parser.add_argument(
        "--suppress-notifications",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
