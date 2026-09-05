#!/usr/bin/env python3
"""Backfill result postings for the approved budget reconciliation references.

Dry-run is the default. Pass ``--apply`` to persist idempotent postings.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from devnous.gastos.models import Documento  # noqa: E402
from devnous.gastos.services.amex_accounting_posting_service import (  # noqa: E402,E501
    ensure_amex_report_approval_posting,
)
from devnous.gastos.services.employee_debtor_accounting_service import (  # noqa: E402,E501
    ensure_debtor_comprobacion_posting_for_informe,
    ensure_provider_approval_posting,
    ensure_provider_payment_posting,
)


DEFAULT_OPERATION_REFS = [
    "1",
    "14",
    "27",
    "29",
    "32",
    "42",
    "53",
    "56",
    "58",
    "59",
    "60",
    "65",
    "66",
    "67",
    "93",
    "108",
    "109",
    "128",
    "129",
]


def _load_env_file(path: str | None) -> None:
    if not path:
        return
    env_path = Path(path)
    if not env_path.exists():
        raise SystemExit(f"env_file_not_found={env_path}")
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def _database_url() -> str:
    value = (os.getenv("DATABASE_URL") or "").strip()
    if not value:
        raise SystemExit("DATABASE_URL is required")
    if value.startswith("postgresql://"):
        return value.replace("postgresql://", "postgresql+asyncpg://", 1)
    return value


def _receipt(result: Any, *, operation: str) -> dict[str, Any]:
    status = str(getattr(result, "status", "skipped") or "skipped")
    normalized = "blocked" if status == "pending" else status
    poliza = getattr(result, "poliza", None)
    return {
        "operation": operation,
        "status": normalized,
        "reason": getattr(result, "reason", None),
        "poliza_id": str(getattr(poliza, "id", "") or "") or None,
        "numero_poliza": getattr(poliza, "numero_poliza", None),
    }


async def _post_document(
    session: Any, document: Documento
) -> list[dict[str, Any]]:
    if document.tipo == "INFORME":
        amex = await ensure_amex_report_approval_posting(
            session, informe_documento=document
        )
        employee = await ensure_debtor_comprobacion_posting_for_informe(
            session, informe_documento=document
        )
        return [
            _receipt(amex, operation="amex_report_approval"),
            _receipt(employee, operation="employee_report_approval"),
        ]

    approval = await ensure_provider_approval_posting(
        session, documento=document
    )
    receipts = [_receipt(approval, operation="provider_approval")]
    if document.estado in {"pagado", "cerrado"} or document.pagado_en:
        paid_at: date | datetime = (
            document.fecha_pago or document.pagado_en or datetime.utcnow()
        )
        payment = await ensure_provider_payment_posting(
            session,
            documento=document,
            fecha_pago=paid_at,
        )
        receipts.append(_receipt(payment, operation="provider_payment"))
    return receipts


async def _run(args: argparse.Namespace) -> int:
    _load_env_file(args.env_file)
    engine = create_async_engine(_database_url())
    maker = async_sessionmaker(engine, expire_on_commit=False)
    requested_refs = [
        str(value).strip() for value in args.refs if str(value).strip()
    ]
    totals = {"created": 0, "exists": 0, "blocked": 0, "skipped": 0}
    output: list[dict[str, Any]] = []
    try:
        async with maker() as session:
            result = await session.execute(
                select(Documento)
                .options(
                    selectinload(Documento.proveedor_cliente),
                    selectinload(Documento.beneficiario_empleado),
                    selectinload(Documento.cuenta_gastos),
                )
                .where(
                    Documento.referencia_operaciones.in_(requested_refs),
                    Documento.estado.in_(["aprobado", "pagado", "cerrado"]),
                )
                .order_by(Documento.referencia_operaciones, Documento.tipo)
            )
            documents = list(result.scalars().all())
            found_refs = {
                str(document.referencia_operaciones or "")
                for document in documents
            }
            for missing in sorted(set(requested_refs) - found_refs):
                receipt = {
                    "ref_op": missing,
                    "status": "blocked",
                    "reason": "document_not_found_or_not_approved",
                }
                output.append(receipt)
                totals["blocked"] += 1
            for document in documents:
                item = {
                    "ref_op": str(document.referencia_operaciones or ""),
                    "document": document.numero_referencia,
                    "document_id": str(document.id),
                    "type": document.tipo,
                    "state": document.estado,
                }
                try:
                    async with session.begin_nested():
                        receipts = await _post_document(session, document)
                    item["postings"] = receipts
                    for receipt in receipts:
                        totals[receipt["status"]] = (
                            totals.get(receipt["status"], 0) + 1
                        )
                except Exception as exc:
                    item["postings"] = [
                        {
                            "status": "blocked",
                            "reason": f"{type(exc).__name__}: {exc}",
                        }
                    ]
                    totals["blocked"] += 1
                output.append(item)
            if args.apply:
                await session.commit()
            else:
                await session.rollback()
    finally:
        await engine.dispose()

    print(
        json.dumps(
            {"apply": args.apply, "totals": totals, "documents": output},
            indent=2,
            default=str,
        )
    )
    return 0 if totals["blocked"] == 0 else 2


def main() -> int:
    """Parse command-line arguments and run the accounting backfill."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Persist postings")
    parser.add_argument("--env-file")
    parser.add_argument("--refs", nargs="+", default=DEFAULT_OPERATION_REFS)
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
