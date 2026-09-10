#!/usr/bin/env python3
"""Create a source-backed, read-only Finance reconciliation inventory receipt."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


ROOT = Path(__file__).resolve().parents[1]


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


def _json_safe(value: Any) -> Any:
    if isinstance(value, (datetime,)):
        return value.isoformat()
    if isinstance(value, (Decimal, UUID)):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _query_plan() -> list[dict[str, str]]:
    """Return the fixed G1 plan. Every statement must remain observational."""

    return [
        {
            "name": "source_tables",
            "kind": "aggregate",
            "sql": """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_name = ANY(:tables)
                ORDER BY table_name
            """,
        },
        {
            "name": "document_states",
            "kind": "aggregate",
            "sql": """
                SELECT tipo, estado, COUNT(*) AS count,
                       COALESCE(SUM(COALESCE(monto_total, monto_solicitado)), 0) AS amount
                FROM documentos
                GROUP BY tipo, estado
                ORDER BY tipo, estado
            """,
        },
        {
            "name": "paid_request_missing_payment_evidence",
            "kind": "exception",
            "classification": "SOURCE_MISSING",
            "expected": "Solicitud pagada debe conservar fecha o referencia de pago.",
            "sql": """
                SELECT COUNT(*) OVER() AS total_candidates,
                       numero_referencia AS reference,
                       'documento' AS domain,
                       estado AS observed_state,
                       'pagado_sin_fecha_ni_referencia' AS reason
                FROM documentos
                WHERE tipo = 'SOLICITUD'
                  AND estado = 'pagado'
                  AND fecha_pago IS NULL
                  AND NULLIF(BTRIM(COALESCE(referencia_pago, '')), '') IS NULL
                ORDER BY numero_referencia
                LIMIT :limit
            """,
        },
        {
            "name": "cfdi_type_counts",
            "kind": "aggregate",
            "sql": """
                SELECT COALESCE(tipo_de_comprobante, 'UNKNOWN') AS tipo,
                       COUNT(*) AS count, COALESCE(SUM(total), 0) AS amount
                FROM cfdi_reports
                GROUP BY COALESCE(tipo_de_comprobante, 'UNKNOWN')
                ORDER BY tipo
            """,
        },
        {
            "name": "payroll_cfdi_in_cxc",
            "kind": "exception",
            "classification": "FINANCE_DECISION",
            "expected": "CFDI de Nómina se excluye de CxC y se trata en Nómina.",
            "sql": """
                SELECT COUNT(*) OVER() AS total_candidates,
                       link.id::text AS reference,
                       'cxc' AS domain,
                       COALESCE(link.status, 'UNKNOWN') AS observed_state,
                       'cfdi_nomina_vinculado_a_ingreso' AS reason
                FROM budget_cfdi_income_links link
                JOIN cfdi_reports cfdi ON cfdi.id = link.cfdi_report_id
                WHERE link.unlinked_at IS NULL
                  AND UPPER(COALESCE(cfdi.tipo_de_comprobante, '')) = 'N'
                ORDER BY link.created_at, link.id
                LIMIT :limit
            """,
        },
        {
            "name": "income_links_missing_classification",
            "kind": "exception",
            "classification": "SOURCE_MISSING",
            "expected": "Vínculo de ingreso requiere partida, proyecto y concepto presupuestal.",
            "sql": """
                SELECT COUNT(*) OVER() AS total_candidates,
                       link.id::text AS reference,
                       'cxc' AS domain,
                       COALESCE(link.status, 'UNKNOWN') AS observed_state,
                       CONCAT_WS(',',
                           CASE WHEN link.budget_line_id IS NULL THEN 'budget_line' END,
                           CASE WHEN link.tournament_id IS NULL THEN 'tournament' END,
                           CASE WHEN link.budget_concept_id IS NULL THEN 'budget_concept' END
                       ) AS reason
                FROM budget_cfdi_income_links link
                WHERE link.unlinked_at IS NULL
                  AND (link.budget_line_id IS NULL
                       OR link.tournament_id IS NULL
                       OR link.budget_concept_id IS NULL)
                ORDER BY link.created_at, link.id
                LIMIT :limit
            """,
        },
        {
            "name": "ar_match_states",
            "kind": "aggregate",
            "sql": """
                SELECT status, COUNT(*) AS count, COALESCE(SUM(accepted_amount), 0) AS amount
                FROM ar_collection_matches
                GROUP BY status
                ORDER BY status
            """,
        },
        {
            "name": "accepted_ar_match_not_inflow",
            "kind": "exception",
            "classification": "FINANCE_DECISION",
            "expected": "Cobro CxC aceptado requiere movimiento bancario de entrada.",
            "sql": """
                SELECT COUNT(*) OVER() AS total_candidates,
                       match.id::text AS reference,
                       'cxc' AS domain,
                       match.status AS observed_state,
                       'movimiento_bancario_no_es_entrada' AS reason
                FROM ar_collection_matches match
                JOIN bank_movements movement ON movement.id = match.bank_movement_id
                WHERE match.status = 'accepted_collection_match'
                  AND COALESCE(movement.signo, '') <> '+'
                ORDER BY match.accepted_at, match.id
                LIMIT :limit
            """,
        },
        {
            "name": "bank_movement_states",
            "kind": "aggregate",
            "sql": """
                SELECT COALESCE(signo, 'UNKNOWN') AS signo,
                       COALESCE(conciliacion_estado, 'UNKNOWN') AS estado,
                       COUNT(*) AS count, COALESCE(SUM(importe), 0) AS amount
                FROM bank_movements
                GROUP BY COALESCE(signo, 'UNKNOWN'), COALESCE(conciliacion_estado, 'UNKNOWN')
                ORDER BY signo, estado
            """,
        },
        {
            "name": "bank_outflows_without_paid_request_match",
            "kind": "exception",
            "classification": "FINANCE_DECISION",
            "expected": "Salida bancaria se vincula a Solicitud ya pagada o se clasifica por Finanzas.",
            "sql": """
                WITH latest_treasury AS (
                    SELECT DISTINCT ON (bank_movement_id)
                           bank_movement_id, action
                    FROM reconciliation_audit_logs
                    WHERE action IN (
                        'accept_treasury_payment_request_match',
                        'undo_treasury_payment_request_match'
                    )
                    ORDER BY bank_movement_id, created_at DESC
                )
                SELECT COUNT(*) OVER() AS total_candidates,
                       movement.id::text AS reference,
                       'tesoreria' AS domain,
                       COALESCE(movement.conciliacion_estado, 'UNKNOWN') AS observed_state,
                       'salida_sin_vinculo_a_solicitud_pagada' AS reason
                FROM bank_movements movement
                LEFT JOIN latest_treasury latest
                  ON latest.bank_movement_id = movement.id
                WHERE movement.signo = '-'
                  AND COALESCE(latest.action, '') <> 'accept_treasury_payment_request_match'
                ORDER BY movement.fecha NULLS LAST, movement.id
                LIMIT :limit
            """,
        },
        {
            "name": "payment_run_closure_states",
            "kind": "aggregate",
            "sql": """
                SELECT status, COUNT(*) AS count,
                       COALESCE(SUM(total_amount), 0) AS amount
                FROM payment_run_closures
                GROUP BY status
                ORDER BY status
            """,
        },
        {
            "name": "poliza_origins",
            "kind": "aggregate",
            "sql": """
                SELECT COALESCE(origen, 'UNKNOWN') AS origen, COUNT(*) AS count
                FROM accounting_polizas
                GROUP BY COALESCE(origen, 'UNKNOWN')
                ORDER BY origen
            """,
        },
    ]


def _plan_sha256(plan: list[dict[str, str]]) -> str:
    encoded = json.dumps(plan, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_output_path(path: str) -> Path:
    output = Path(path).expanduser().resolve()
    try:
        output.relative_to(ROOT)
    except ValueError:
        return output
    raise SystemExit("output_path_must_be_outside_repository")


async def _run_queries(
    session: Any, *, limit: int
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    aggregates: dict[str, Any] = {}
    exception_summary: dict[str, Any] = {}
    exceptions: list[dict[str, Any]] = []
    table_names = [
        "documentos", "budget_cfdi_income_links", "cfdi_reports",
        "ar_collection_matches", "bank_movements", "reconciliation_audit_logs",
        "payment_run_closures", "accounting_polizas",
    ]
    for query in _query_plan():
        params: dict[str, Any] = {"limit": limit}
        if query["name"] == "source_tables":
            params["tables"] = table_names
        result = await session.execute(text(query["sql"]), params)
        rows = [_json_safe(dict(row)) for row in result.mappings().all()]
        if query["kind"] == "aggregate":
            aggregates[query["name"]] = rows
            continue
        exception_summary[query["name"]] = {
            "total_candidates": int(rows[0]["total_candidates"]) if rows else 0,
            "returned_references": len(rows),
            "classification": query["classification"],
        }
        for row in rows:
            exceptions.append(
                {
                    "reference": row["reference"],
                    "domain": row["domain"],
                    "observed_state": row["observed_state"],
                    "reason": row["reason"],
                    "classification": query["classification"],
                    "expected_state": query["expected"],
                    "proposed_action": "Finance review; no mutation authorized.",
                    "mutation_authority": None,
                }
            )
    return aggregates, exception_summary, exceptions


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    _load_env_file(args.env_file)
    output_path = _validate_output_path(args.output)
    row_limit = max(1, min(int(args.limit), 250))
    timeout_ms = max(1_000, min(int(args.statement_timeout_ms), 120_000))
    plan = _query_plan()
    engine = create_async_engine(_database_url(), pool_pre_ping=True)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with maker() as session:
            await session.execute(text("SET TRANSACTION READ ONLY"))
            await session.execute(text(f"SET LOCAL statement_timeout = {timeout_ms}"))
            aggregates, exception_summary, exceptions = await _run_queries(
                session, limit=row_limit
            )
            await session.rollback()
    finally:
        await engine.dispose()
    receipt = {
        "schema_version": 1,
        "operation": "finance_reconciliation_g1_inventory",
        "mode": "read_only",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "release": args.release,
        "commit": args.commit,
        "plan_sha256": _plan_sha256(plan),
        "statement_timeout_ms": timeout_ms,
        "exception_limit_per_query": row_limit,
        "aggregates": aggregates,
        "exception_summary": exception_summary,
        "exceptions": exceptions,
        "exception_count": len(exceptions),
        "limitations": [
            "No bank, CFDI, payment, collection, budget, or accounting value is inferred.",
            "Exceptions are a G1 inventory, not a repair or UAT decision.",
            "Rows above the per-query limit require a subsequent bounded export.",
        ],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(receipt, indent=2, sort_keys=True), encoding="utf-8")
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--release", default="unknown")
    parser.add_argument("--commit", default="unknown")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--statement-timeout-ms", type=int, default=30_000)
    receipt = asyncio.run(_run(parser.parse_args()))
    print(json.dumps({"output": sys.argv[sys.argv.index("--output") + 1], "exception_count": receipt["exception_count"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
