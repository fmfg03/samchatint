#!/usr/bin/env python3
"""Build a read-only Finance G2 decision packet for unresolved bank outflows."""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


ROOT = Path(__file__).resolve().parents[1]
READ_ONLY_TRANSACTION_SQL = "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
DECISIONS = (
    "PAID_REQUEST",
    "CXP_CFDI",
    "AMEX",
    "OTHER_FINANCE_FLOW",
    "SOURCE_MISSING",
    "OUT_OF_SCOPE",
    "PENDING_FINANCE_DECISION",
)


def _load_env_file(path: str) -> None:
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def _database_url() -> str:
    value = (os.getenv("DATABASE_URL") or "").strip()
    if not value:
        raise SystemExit("DATABASE_URL is required")
    return value.replace("postgresql://", "postgresql+asyncpg://", 1)


def _safe(value: Any) -> Any:
    if isinstance(value, (datetime, Decimal, UUID)):
        return str(value.isoformat() if isinstance(value, datetime) else value)
    if isinstance(value, dict):
        return {str(k): _safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe(v) for v in value]
    return value


def _outside_repo(path: str) -> Path:
    output = Path(path).expanduser().resolve()
    try:
        output.relative_to(ROOT)
    except ValueError:
        return output
    raise SystemExit("output_directory_must_be_outside_repository")


def _plan_sql() -> str:
    return """
        WITH latest_treasury AS (
            SELECT DISTINCT ON (bank_movement_id) bank_movement_id, action
            FROM reconciliation_audit_logs
            WHERE action IN (
                'accept_treasury_cfdi_match', 'accept_treasury_payment_request_match',
                'undo_treasury_cfdi_match', 'undo_treasury_payment_request_match'
            )
            ORDER BY bank_movement_id, created_at DESC
        ), platform_rfcs AS (
            SELECT UPPER(BTRIM(tax_id)) AS rfc FROM rfc_configs WHERE active = true
        )
        SELECT movement.id::text AS movement_id, movement.fecha, movement.importe,
               movement.conciliacion_estado, movement.referencia_bancaria,
               movement.clave_rastreo, movement.descripcion, movement.concepto_banco,
               movement.nombre_beneficiario,
               COALESCE(request_candidates.items, '[]'::json) AS paid_request_candidates,
               COALESCE(cfdi_candidates.items, '[]'::json) AS cxp_cfdi_candidates
        FROM bank_movements movement
        LEFT JOIN latest_treasury latest ON latest.bank_movement_id = movement.id
        LEFT JOIN LATERAL (
            SELECT json_agg(row_to_json(candidate)) AS items FROM (
                SELECT document.numero_referencia AS reference,
                       COALESCE(document.monto_total, document.monto_solicitado) AS amount,
                       COALESCE(document.pagado_en, document.fecha_pago::timestamp) AS paid_at
                FROM documentos document
                WHERE document.tipo = 'SOLICITUD' AND document.estado = 'pagado'
                  AND ABS(COALESCE(document.monto_total, document.monto_solicitado, 0)
                          - COALESCE(movement.importe, 0)) <= :tolerance
                ORDER BY ABS(COALESCE(document.monto_total, document.monto_solicitado, 0)
                             - COALESCE(movement.importe, 0)), document.pagado_en DESC NULLS LAST
                LIMIT 3
            ) candidate
        ) request_candidates ON true
        LEFT JOIN LATERAL (
            SELECT json_agg(row_to_json(candidate)) AS items FROM (
                SELECT cfdi.cfdi_uuid AS uuid, cfdi.id::text AS cfdi_report_id,
                       cfdi.total AS amount, cfdi.fecha, cfdi.emisor_rfc
                FROM cfdi_reports cfdi
                WHERE UPPER(COALESCE(cfdi.tipo_de_comprobante, '')) LIKE 'I%'
                  AND EXISTS (SELECT 1 FROM platform_rfcs p
                              WHERE p.rfc = UPPER(COALESCE(cfdi.receptor_rfc, '')))
                  AND ABS(COALESCE(cfdi.total, 0) - COALESCE(movement.importe, 0)) <= :tolerance
                ORDER BY ABS(COALESCE(cfdi.total, 0) - COALESCE(movement.importe, 0)), cfdi.fecha DESC NULLS LAST
                LIMIT 3
            ) candidate
        ) cfdi_candidates ON true
        WHERE movement.signo = '-'
          AND COALESCE(latest.action, '') NOT IN (
              'accept_treasury_cfdi_match', 'accept_treasury_payment_request_match'
          )
        ORDER BY movement.fecha NULLS LAST, movement.id
    """


def _plan_sha256() -> str:
    return hashlib.sha256(_plan_sql().encode("utf-8")).hexdigest()


def _decision_item(row: dict[str, Any]) -> dict[str, Any]:
    """Keep candidate evidence separate from the Finance decision authority."""
    item = _safe(row)
    item.update({
        "decision_status": "PENDING_FINANCE_DECISION",
        "decision": None,
        "decision_owner": None,
        "decision_evidence": None,
        "decision_at": None,
        "warning": (
            "Candidates are factual leads only; no match is accepted by this packet."
        ),
    })
    return item


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    _load_env_file(args.env_file)
    g1_path = Path(args.g1_receipt).resolve()
    g1_bytes = g1_path.read_bytes()
    g1 = json.loads(g1_bytes)
    output_dir = _outside_repo(args.output_dir)
    tolerance = max(0.0, min(float(args.tolerance), 5000.0))
    engine = create_async_engine(_database_url(), pool_pre_ping=True)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with maker() as session:
            await session.execute(text(READ_ONLY_TRANSACTION_SQL))
            await session.execute(text("SET LOCAL statement_timeout = 30000"))
            rows = (
                await session.execute(text(_plan_sql()), {"tolerance": tolerance})
            ).mappings().all()
            await session.rollback()
    finally:
        await engine.dispose()
    items = []
    for row in rows:
        items.append(_decision_item(dict(row)))
    packet = {
        "schema_version": 1, "operation": "finance_g2_decision_packet",
        "mode": "read_only",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "g1_receipt_path": str(g1_path),
        "g1_receipt_sha256": hashlib.sha256(g1_bytes).hexdigest(),
        "g1_plan_sha256": g1.get("plan_sha256"),
        "g2_plan_sha256": _plan_sha256(),
        "allowed_decisions": list(DECISIONS),
        "candidate_tolerance": tolerance,
        "items": items,
        "item_count": len(items),
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "finance-g2-decision-packet.json"
    csv_path = output_dir / "finance-g2-decision-template.csv"
    json_path.write_text(json.dumps(packet, indent=2, sort_keys=True), encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        fields = [
            "movement_id",
            "fecha",
            "importe",
            "conciliacion_estado",
            "referencia_bancaria",
            "clave_rastreo",
            "decision_status",
            "decision",
            "decision_owner",
            "decision_evidence",
            "decision_at",
            "paid_request_candidates",
            "cxp_cfdi_candidates",
        ]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in items:
            writer.writerow(
                {
                    key: (
                        json.dumps(item.get(key), ensure_ascii=False)
                        if key.endswith("candidates")
                        else item.get(key)
                    )
                    for key in fields
                }
            )
    return {"json": str(json_path), "csv": str(csv_path), "item_count": len(items)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--g1-receipt", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--tolerance", type=float, default=1.0)
    print(json.dumps(asyncio.run(_run(parser.parse_args()))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
