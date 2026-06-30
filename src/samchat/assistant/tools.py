from __future__ import annotations

import asyncio
import csv
import json
import logging
import os
import re
import shutil
import unicodedata
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request
from datetime import date, datetime, time, timedelta
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import and_, func, literal_column, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from devnous.copa_telmex.models import Player, Team
from devnous.gastos.models import (
    AccountingAuditLog,
    AccountingClosePeriod,
    AccountingImportRun,
    AccountingPoliza,
    AccountingPolizaLine,
    AssistantArtifact,
    AuxLedgerEntry,
    BankMovement,
    CFDIReport,
    CuentaContable,
    Documento,
    ExpenseReport,
    InvoiceReport,
    ProveedorCliente,
    Reembolso,
)
from devnous.gastos.services.cuenta_contable_suggester import get_cuenta_suggestion
from devnous.gastos.services.expense_accounting_service import (
    build_expense_accounting_preview as service_build_expense_accounting_preview,
    summarize_cfdi_tax_components,
)
from devnous.gastos.services.expense_service import trigger_cfdi_generation
from devnous.gastos.services.hospedaje_tax_service import (
    normalize_hospedaje_rate,
    normalize_hospedaje_state,
    resolve_hospedaje_local_tax,
)
from samchat.tournaments_v2 import load_tournaments_v2_config
from samchat.tournaments_v2.adapters import (
    register_team_from_roster_v2,
    registration_breakdown_v2,
    schedule_create_v2,
    schedule_regenerate_from_rules_v2,
    tournament_ops_query_v2,
)


logger = logging.getLogger(__name__)


_ACCOUNTING_BALANCE_KNOWLEDGE_DIR = Path(
    "/root/samchat/reports/accounting_knowledge/plataforma_sports_q1_2026"
)


def _format_currency(value: float) -> str:
    return f"${float(value or 0):,.2f} MXN"


def _load_static_balance_report(
    *,
    year: int,
    month: int,
    q: str = "",
    row_limit: int = 120,
) -> Optional[Dict[str, Any]]:
    month_key = f"{int(year):04d}-{int(month):02d}"
    summary_path = _ACCOUNTING_BALANCE_KNOWLEDGE_DIR / "balanzas_q1_2026_summary.json"
    csv_path = _ACCOUNTING_BALANCE_KNOWLEDGE_DIR / "balanzas_q1_2026_normalized.csv"
    if not summary_path.exists() or not csv_path.exists():
        return None

    try:
        summary_payload = json.loads(summary_path.read_text(encoding="utf-8"))
    except Exception:
        return None

    month_summary = ((summary_payload or {}).get("months") or {}).get(month_key)
    if not isinstance(month_summary, dict):
        return None

    search_tokens = [
        token for token in re.split(r"[^a-z0-9]+", (q or "").lower()) if token
    ]
    rows: List[Dict[str, Any]] = []
    source_file = None
    try:
        with csv_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for raw in reader:
                if str(raw.get("month_key") or "").strip() != month_key:
                    continue
                haystack = " ".join(
                    [
                        str(raw.get("account_code") or ""),
                        str(raw.get("account_name") or ""),
                        str(raw.get("account_class") or ""),
                        str(raw.get("account_group") or ""),
                        str(raw.get("source_file") or ""),
                    ]
                ).lower()
                if search_tokens and not all(
                    token in haystack for token in search_tokens
                ):
                    continue
                source_file = (
                    source_file or str(raw.get("source_file") or "").strip() or None
                )
                rows.append(
                    {
                        "cuenta_codigo": str(raw.get("account_code") or "").strip()
                        or None,
                        "cuenta_nombre": str(raw.get("account_name") or "").strip()
                        or None,
                        "saldo_inicial": round(
                            float(raw.get("opening_balance") or 0), 2
                        ),
                        "debe": round(float(raw.get("total_debits") or 0), 2),
                        "haber": round(float(raw.get("total_credits") or 0), 2),
                        "saldo_final": round(float(raw.get("closing_balance") or 0), 2),
                        "movimientos": 1,
                        "account_class": str(raw.get("account_class") or "").strip()
                        or None,
                        "account_group": str(raw.get("account_group") or "").strip()
                        or None,
                        "source_file": str(raw.get("source_file") or "").strip()
                        or None,
                    }
                )
                if len(rows) >= max(1, min(int(row_limit or 120), 300)):
                    break
    except Exception:
        return None

    by_class = month_summary.get("by_account_class") or {}
    totals = {
        "saldo_inicial": round(
            sum(
                float((values or {}).get("opening_balance") or 0)
                for values in by_class.values()
            ),
            2,
        ),
        "debe": round(
            sum(
                float((values or {}).get("total_debits") or 0)
                for values in by_class.values()
            ),
            2,
        ),
        "haber": round(
            sum(
                float((values or {}).get("total_credits") or 0)
                for values in by_class.values()
            ),
            2,
        ),
        "saldo_final": round(
            sum(
                float((values or {}).get("closing_balance") or 0)
                for values in by_class.values()
            ),
            2,
        ),
    }
    artifact_markdown = "\n".join(
        [
            f"# Balanza de comprobación {month_key}",
            "",
            "- Fuente: artefacto contable preindexado",
            (
                f"- Archivo origen: {source_file}"
                if source_file
                else "- Archivo origen: balanza mensual Q1 2026"
            ),
            f"- Saldo inicial: {_format_currency(totals['saldo_inicial'])}",
            f"- Debe: {_format_currency(totals['debe'])}",
            f"- Haber: {_format_currency(totals['haber'])}",
            f"- Saldo final: {_format_currency(totals['saldo_final'])}",
            "",
            _markdown_table(
                [
                    "Cuenta",
                    "Nombre",
                    "Saldo inicial",
                    "Debe",
                    "Haber",
                    "Saldo final",
                    "Clase",
                ],
                [
                    [
                        row["cuenta_codigo"],
                        row["cuenta_nombre"],
                        row["saldo_inicial"],
                        row["debe"],
                        row["haber"],
                        row["saldo_final"],
                        row["account_class"],
                    ]
                    for row in rows
                ],
            )
            or "_Sin filas coincidentes para el filtro_",
        ]
    ).strip()
    return {
        "report_type": "balanza",
        "title": f"Balanza de comprobación {month_key}",
        "period": {"year": int(year), "month": int(month)},
        "filters": {"q": q or None},
        "summary": totals,
        "rows": rows,
        "artifact_markdown": artifact_markdown,
        "source": "knowledge_artifact",
        "source_file": source_file,
    }


async def _load_historical_accounting_movements_report(
    session: AsyncSession,
    *,
    report_type: str,
    year: int,
    month: Optional[int],
    q: str = "",
    cuenta_codigo: str = "all",
    tipo_poliza: str = "all",
    row_limit: int = 120,
) -> Optional[Dict[str, Any]]:
    """Fallback over legacy accounting_movements imported from 2023-2025 COI."""

    selected_type = (report_type or "").strip().lower()
    if selected_type not in {"diario", "mayor", "balanza"}:
        return None

    safe_limit = max(10, min(int(row_limit or 120), 300))
    search = (q or "").strip().lower()
    selected_cuenta = (cuenta_codigo or "all").strip()
    selected_tipo = (tipo_poliza or "all").strip()
    month_clause = "and month_num = :month_num" if month else ""
    cuenta_clause = (
        "and account_code = :cuenta_codigo" if selected_cuenta != "all" else ""
    )
    tipo_clause = (
        "and lower(policy_type) = lower(:tipo_poliza)" if selected_tipo != "all" else ""
    )
    search_clause = ""
    if search:
        search_clause = """
          and (
            lower(coalesce(account_code, '')) like :search
            or lower(coalesce(account_name, '')) like :search
            or lower(coalesce(concept, '')) like :search
            or lower(coalesce(policy_description, '')) like :search
            or lower(coalesce(policy_id, '')) like :search
          )
        """
    params: Dict[str, Any] = {
        "year": int(year),
        "limit": safe_limit,
        "search": f"%{search}%",
        "cuenta_codigo": selected_cuenta,
        "tipo_poliza": selected_tipo,
    }
    if month:
        params["month_num"] = int(month)

    total_count = int(
        (
            await session.execute(
                text(
                    f"""
                    select count(*) from accounting_movements
                    where year = :year
                    {month_clause}
                    """
                ),
                params,
            )
        ).scalar_one()
        or 0
    )
    if total_count <= 0:
        return None

    period_label = f"{year}-{int(month):02d}" if month else str(year)
    period_payload: Dict[str, Any] = {"year": int(year)}
    if month:
        period_payload["month"] = int(month)
    else:
        period_payload["scope"] = "year"

    if selected_type == "diario":
        result = await session.execute(
            text(
                f"""
                select
                  policy_date,
                  month_num,
                  month_name,
                  policy_type,
                  policy_number,
                  policy_id,
                  account_code,
                  account_name,
                  concept,
                  policy_description,
                  debe,
                  haber,
                  file_name
                from accounting_movements
                where year = :year
                {month_clause}
                {cuenta_clause}
                {tipo_clause}
                {search_clause}
                order by policy_date nulls last, policy_type, policy_number, row_number
                limit :limit
                """
            ),
            params,
        )
        rows = [
            {
                "fecha": row.policy_date.isoformat() if row.policy_date else None,
                "mes": row.month_name,
                "tipo": row.policy_type,
                "poliza": row.policy_number,
                "policy_id": row.policy_id,
                "cuenta_codigo": row.account_code,
                "cuenta_nombre": row.account_name,
                "concepto": row.concept or row.policy_description,
                "debe": round(float(row.debe or 0), 2),
                "haber": round(float(row.haber or 0), 2),
                "source_file": row.file_name,
            }
            for row in result
        ]
        total_debe = round(sum(float(row["debe"] or 0) for row in rows), 2)
        total_haber = round(sum(float(row["haber"] or 0) for row in rows), 2)
        artifact_markdown = "\n".join(
            [
                f"# Libro diario histórico {period_label}",
                "",
                "- Fuente: accounting_movements histórico",
                f"- Partidas incluidas: {len(rows)}",
                f"- Debe: {_format_currency(total_debe)}",
                f"- Haber: {_format_currency(total_haber)}",
                "",
                _markdown_table(
                    ["Fecha", "Tipo", "Póliza", "Cuenta", "Concepto", "Debe", "Haber"],
                    [
                        [
                            row["fecha"],
                            row["tipo"],
                            row["poliza"],
                            row["cuenta_codigo"],
                            row["concepto"],
                            row["debe"],
                            row["haber"],
                        ]
                        for row in rows
                    ],
                ),
            ]
        ).strip()
        return {
            "report_type": "diario",
            "title": f"Libro diario histórico {period_label}",
            "period": period_payload,
            "filters": {
                "tipo_poliza": selected_tipo if selected_tipo != "all" else None,
                "q": search or None,
            },
            "summary": {
                "rows": len(rows),
                "debe": total_debe,
                "haber": total_haber,
                "difference": round(total_debe - total_haber, 2),
            },
            "rows": rows,
            "artifact_markdown": artifact_markdown,
            "source": "accounting_movements",
        }

    result = await session.execute(
        text(
            f"""
            select
              account_code,
              account_name,
              count(*) as movimientos,
              round(coalesce(sum(debe), 0)::numeric, 2) as debe,
              round(coalesce(sum(haber), 0)::numeric, 2) as haber,
              round((coalesce(sum(debe), 0) - coalesce(sum(haber), 0))::numeric, 2) as neto
            from accounting_movements
            where year = :year
            {month_clause}
            {cuenta_clause}
            {tipo_clause}
            {search_clause}
            group by account_code, account_name
            order by abs(coalesce(sum(debe), 0) - coalesce(sum(haber), 0)) desc,
                     coalesce(sum(debe), 0) desc,
                     account_code
            limit :limit
            """
        ),
        params,
    )
    rows = [
        {
            "cuenta_codigo": row.account_code,
            "cuenta_nombre": row.account_name,
            "movimientos": int(row.movimientos or 0),
            "debe": round(float(row.debe or 0), 2),
            "haber": round(float(row.haber or 0), 2),
            "neto": round(float(row.neto or 0), 2),
        }
        for row in result
    ]
    totals_result = await session.execute(
        text(
            f"""
            select
              round(coalesce(sum(debe), 0)::numeric, 2) as debe,
              round(coalesce(sum(haber), 0)::numeric, 2) as haber
            from accounting_movements
            where year = :year
            {month_clause}
            {cuenta_clause}
            {tipo_clause}
            {search_clause}
            """
        ),
        params,
    )
    totals_row = totals_result.first()
    total_debe = round(float((totals_row.debe if totals_row else 0) or 0), 2)
    total_haber = round(float((totals_row.haber if totals_row else 0) or 0), 2)
    summary = {
        "rows": len(rows),
        "source_rows_total": total_count,
        "debe": total_debe,
        "haber": total_haber,
        "neto": round(total_debe - total_haber, 2),
    }
    title_kind = (
        "Mayor contable histórico" if selected_type == "mayor" else "Balanza histórica"
    )
    artifact_markdown = "\n".join(
        [
            f"# {title_kind} {period_label}",
            "",
            "- Fuente: accounting_movements histórico",
            f"- Debe: {_format_currency(total_debe)}",
            f"- Haber: {_format_currency(total_haber)}",
            f"- Neto: {_format_currency(summary['neto'])}",
            "",
            _markdown_table(
                ["Cuenta", "Nombre", "Movimientos", "Debe", "Haber", "Neto"],
                [
                    [
                        row["cuenta_codigo"],
                        row["cuenta_nombre"],
                        row["movimientos"],
                        row["debe"],
                        row["haber"],
                        row["neto"],
                    ]
                    for row in rows
                ],
            )
            or "_Sin filas coincidentes para el filtro_",
        ]
    ).strip()
    return {
        "report_type": selected_type,
        "title": f"{title_kind} {period_label}",
        "period": period_payload,
        "filters": {
            "cuenta_codigo": selected_cuenta if selected_cuenta != "all" else None,
            "tipo_poliza": selected_tipo if selected_tipo != "all" else None,
            "q": search or None,
        },
        "summary": summary,
        "summary_by_account": rows if selected_type == "mayor" else None,
        "rows": rows,
        "artifact_markdown": artifact_markdown,
        "source": "accounting_movements",
    }


async def _load_historical_accounting_tables_report(
    session: AsyncSession,
    *,
    report_type: str,
    year: int,
    company_code: str = "01",
    month: Optional[int],
    q: str = "",
    cuenta_codigo: str = "all",
    tipo_poliza: str = "all",
    row_limit: int = 120,
) -> Optional[Dict[str, Any]]:
    """Read canonical historical accounting reports from persisted historical tables."""

    selected_type = (report_type or "").strip().lower()
    if selected_type not in {"diario", "mayor", "balanza"}:
        return None

    latest_run_result = await session.execute(
        text(
            """
            SELECT air.id, MAX(hs.company_label) AS company_label
            FROM accounting_import_runs air
            JOIN historical_accounting_source_files hs ON hs.import_run_id = air.id
            WHERE air.source_type = 'historical_accounting'
              AND hs.fiscal_year = :fiscal_year
              AND hs.company_code = :company_code
              AND air.mode = 'apply'
              AND air.status = 'completed'
            GROUP BY air.id, air.finished_at, air.started_at
            ORDER BY air.finished_at DESC NULLS LAST, air.started_at DESC NULLS LAST
            LIMIT 1
            """
        ),
        {"fiscal_year": int(year), "company_code": str(company_code or "01").zfill(2)},
    )
    latest_run = latest_run_result.first()
    if not latest_run:
        return None

    import_run_id = str(latest_run[0])
    company_label = str(getattr(latest_run, "company_label", "") or latest_run[1] or "")
    safe_limit = max(10, min(int(row_limit or 120), 300))
    search = (q or "").strip().lower()
    selected_cuenta = (cuenta_codigo or "all").strip()
    selected_tipo = (tipo_poliza or "all").strip()
    period_label = f"{year}-{int(month):02d}" if month else str(year)
    period_payload: Dict[str, Any] = {
        "year": int(year),
        "company_code": str(company_code or "01").zfill(2),
    }
    if company_label:
        period_payload["company_label"] = company_label
    period_payload["company_display"] = _historical_company_display(
        company_code,
        company_label,
    )
    if month:
        period_payload["month"] = int(month)
    else:
        period_payload["scope"] = "year"

    params: Dict[str, Any] = {
        "import_run_id": import_run_id,
        "fiscal_year": int(year),
        "limit": safe_limit,
        "search": f"%{search}%",
        "cuenta_codigo": selected_cuenta,
        "tipo_poliza": selected_tipo,
    }
    month_clause = "AND fiscal_month = :fiscal_month" if month else ""
    if month:
        params["fiscal_month"] = int(month)
    cuenta_clause = (
        "AND account_code_raw = :cuenta_codigo"
        if selected_cuenta != "all"
        else ""
    )
    header_tipo_clause = (
        "AND lower(policy_type) = lower(:tipo_poliza)"
        if selected_tipo != "all"
        else ""
    )
    line_search_clause = ""
    balance_search_clause = ""
    if search:
        line_search_clause = """
          AND (
            lower(coalesce(l.account_code_raw, '')) LIKE :search
            OR lower(coalesce(l.account_name_raw, '')) LIKE :search
            OR lower(coalesce(l.line_concept, '')) LIKE :search
            OR lower(coalesce(h.concept_raw, '')) LIKE :search
            OR lower(coalesce(h.policy_id_natural, '')) LIKE :search
          )
        """
        balance_search_clause = """
          AND (
            lower(coalesce(account_code_raw, '')) LIKE :search
            OR lower(coalesce(account_name_raw, '')) LIKE :search
          )
        """

    if selected_type == "diario":
        company_display = _historical_company_display(company_code, company_label)
        count_result = await session.execute(
            text(
                f"""
                SELECT COUNT(*)
                FROM historical_policy_lines l
                JOIN historical_policy_headers h ON h.id = l.policy_header_id
                WHERE l.import_run_id = :import_run_id
                  AND l.fiscal_year = :fiscal_year
                  {month_clause}
                  {cuenta_clause}
                  {header_tipo_clause}
                  {line_search_clause}
                """
            ),
            params,
        )
        total_count = int(count_result.scalar_one() or 0)
        if total_count <= 0:
            return None

        result = await session.execute(
            text(
                f"""
                SELECT
                  h.policy_date,
                  h.fiscal_month,
                  h.policy_type,
                  h.policy_number,
                  h.policy_id_natural,
                  l.account_code_raw,
                  l.account_name_raw,
                  l.line_concept,
                  h.concept_raw,
                  l.debit_amount,
                  l.credit_amount
                FROM historical_policy_lines l
                JOIN historical_policy_headers h ON h.id = l.policy_header_id
                WHERE l.import_run_id = :import_run_id
                  AND l.fiscal_year = :fiscal_year
                  {month_clause}
                  {cuenta_clause}
                  {header_tipo_clause}
                  {line_search_clause}
                ORDER BY h.policy_date NULLS LAST, h.policy_type, h.policy_number, l.line_number
                LIMIT :limit
                """
            ),
            params,
        )
        rows = [
            {
                "fecha": row.policy_date.isoformat() if row.policy_date else None,
                "mes": row.fiscal_month,
                "tipo": row.policy_type,
                "poliza": row.policy_number,
                "policy_id": row.policy_id_natural,
                "cuenta_codigo": row.account_code_raw,
                "cuenta_nombre": row.account_name_raw,
                "concepto": row.line_concept or row.concept_raw,
                "debe": round(float(row.debit_amount or 0), 2),
                "haber": round(float(row.credit_amount or 0), 2),
            }
            for row in result
        ]
        total_debe = round(sum(float(row["debe"] or 0) for row in rows), 2)
        total_haber = round(sum(float(row["haber"] or 0) for row in rows), 2)
        artifact_markdown = "\n".join(
            [
                f"# Libro diario histórico {period_label} · {company_display}",
                "",
                "- Fuente: tablas históricas canónicas",
                f"- Empresa: {company_display}",
                f"- Partidas incluidas: {len(rows)}",
                f"- Debe: {_format_currency(total_debe)}",
                f"- Haber: {_format_currency(total_haber)}",
                "",
                _markdown_table(
                    ["Fecha", "Tipo", "Póliza", "Cuenta", "Concepto", "Debe", "Haber"],
                    [
                        [
                            row["fecha"],
                            row["tipo"],
                            row["poliza"],
                            row["cuenta_codigo"],
                            row["concepto"],
                            row["debe"],
                            row["haber"],
                        ]
                        for row in rows
                    ],
                ),
            ]
        ).strip()
        return {
            "report_type": "diario",
            "title": f"Libro diario histórico {period_label} · {company_display}",
            "period": period_payload,
            "filters": {
                "company_code": str(company_code or "01").zfill(2),
                "tipo_poliza": selected_tipo if selected_tipo != "all" else None,
                "q": search or None,
            },
            "summary": {
                "rows": len(rows),
                "source_rows_total": total_count,
                "debe": total_debe,
                "haber": total_haber,
                "difference": round(total_debe - total_haber, 2),
                "company_display": company_display,
            },
            "rows": rows,
            "artifact_markdown": artifact_markdown,
            "source": "historical_tables",
            "company_code": str(company_code or "01").zfill(2),
            "company_label": company_label or None,
        }

    if selected_type == "balanza":
        company_display = _historical_company_display(company_code, company_label)
        count_result = await session.execute(
            text(
                f"""
                SELECT COUNT(*)
                FROM historical_trial_balance_rows
                WHERE import_run_id = :import_run_id
                  AND fiscal_year = :fiscal_year
                  {month_clause}
                  {cuenta_clause}
                  {balance_search_clause}
                """
            ),
            params,
        )
        total_count = int(count_result.scalar_one() or 0)
        if total_count <= 0:
            return None

        result = await session.execute(
            text(
                f"""
                SELECT
                  account_code_raw,
                  account_name_raw,
                  COUNT(*) AS movimientos,
                  ROUND(COALESCE(SUM(opening_balance), 0)::numeric, 2) AS saldo_inicial,
                  ROUND(COALESCE(SUM(debits), 0)::numeric, 2) AS debe,
                  ROUND(COALESCE(SUM(credits), 0)::numeric, 2) AS haber,
                  ROUND(COALESCE(SUM(closing_balance), 0)::numeric, 2) AS saldo_final
                FROM historical_trial_balance_rows
                WHERE import_run_id = :import_run_id
                  AND fiscal_year = :fiscal_year
                  {month_clause}
                  {cuenta_clause}
                  {balance_search_clause}
                GROUP BY account_code_raw, account_name_raw
                ORDER BY ABS(COALESCE(SUM(closing_balance), 0)) DESC, account_code_raw
                LIMIT :limit
                """
            ),
            params,
        )
        rows = [
            {
                "cuenta_codigo": row.account_code_raw,
                "cuenta_nombre": row.account_name_raw,
                "movimientos": int(row.movimientos or 0),
                "saldo_inicial": round(float(row.saldo_inicial or 0), 2),
                "debe": round(float(row.debe or 0), 2),
                "haber": round(float(row.haber or 0), 2),
                "saldo_final": round(float(row.saldo_final or 0), 2),
            }
            for row in result
        ]
        totals_result = await session.execute(
            text(
                f"""
                SELECT
                  ROUND(COALESCE(SUM(opening_balance), 0)::numeric, 2) AS saldo_inicial,
                  ROUND(COALESCE(SUM(debits), 0)::numeric, 2) AS debe,
                  ROUND(COALESCE(SUM(credits), 0)::numeric, 2) AS haber,
                  ROUND(COALESCE(SUM(closing_balance), 0)::numeric, 2) AS saldo_final
                FROM historical_trial_balance_rows
                WHERE import_run_id = :import_run_id
                  AND fiscal_year = :fiscal_year
                  {month_clause}
                  {cuenta_clause}
                  {balance_search_clause}
                """
            ),
            params,
        )
        totals_row = totals_result.first()
        summary = {
            "rows": len(rows),
            "source_rows_total": total_count,
            "saldo_inicial": round(float((totals_row.saldo_inicial if totals_row else 0) or 0), 2),
            "debe": round(float((totals_row.debe if totals_row else 0) or 0), 2),
            "haber": round(float((totals_row.haber if totals_row else 0) or 0), 2),
            "saldo_final": round(float((totals_row.saldo_final if totals_row else 0) or 0), 2),
            "company_display": company_display,
        }
        artifact_markdown = "\n".join(
            [
                f"# Balanza histórica {period_label} · {company_display}",
                "",
                "- Fuente: tablas históricas canónicas",
                f"- Empresa: {company_display}",
                f"- Saldo inicial: {_format_currency(summary['saldo_inicial'])}",
                f"- Debe: {_format_currency(summary['debe'])}",
                f"- Haber: {_format_currency(summary['haber'])}",
                f"- Saldo final: {_format_currency(summary['saldo_final'])}",
                "",
                _markdown_table(
                    ["Cuenta", "Nombre", "Movimientos", "Saldo inicial", "Debe", "Haber", "Saldo final"],
                    [
                        [
                            row["cuenta_codigo"],
                            row["cuenta_nombre"],
                            row["movimientos"],
                            row["saldo_inicial"],
                            row["debe"],
                            row["haber"],
                            row["saldo_final"],
                        ]
                        for row in rows
                    ],
                )
                or "_Sin filas coincidentes para el filtro_",
            ]
        ).strip()
        return {
            "report_type": "balanza",
            "title": f"Balanza histórica {period_label} · {company_display}",
            "period": period_payload,
            "filters": {
                "company_code": str(company_code or "01").zfill(2),
                "cuenta_codigo": selected_cuenta if selected_cuenta != "all" else None,
                "q": search or None,
            },
            "summary": summary,
            "rows": rows,
            "artifact_markdown": artifact_markdown,
            "source": "historical_tables",
            "company_code": str(company_code or "01").zfill(2),
            "company_label": company_label or None,
            "company_display": company_display,
        }

    company_display = _historical_company_display(company_code, company_label)
    count_result = await session.execute(
        text(
            f"""
            SELECT COUNT(*)
            FROM historical_policy_lines l
            JOIN historical_policy_headers h ON h.id = l.policy_header_id
            WHERE l.import_run_id = :import_run_id
              AND l.fiscal_year = :fiscal_year
              {month_clause}
              {cuenta_clause}
              {header_tipo_clause}
              {line_search_clause}
            """
        ),
        params,
    )
    total_count = int(count_result.scalar_one() or 0)
    if total_count <= 0:
        return None

    result = await session.execute(
        text(
            f"""
            SELECT
              l.account_code_raw,
              l.account_name_raw,
              COUNT(*) AS movimientos,
              ROUND(COALESCE(SUM(l.debit_amount), 0)::numeric, 2) AS debe,
              ROUND(COALESCE(SUM(l.credit_amount), 0)::numeric, 2) AS haber,
              ROUND((COALESCE(SUM(l.debit_amount), 0) - COALESCE(SUM(l.credit_amount), 0))::numeric, 2) AS neto
            FROM historical_policy_lines l
            JOIN historical_policy_headers h ON h.id = l.policy_header_id
            WHERE l.import_run_id = :import_run_id
              AND l.fiscal_year = :fiscal_year
              {month_clause}
              {cuenta_clause}
              {header_tipo_clause}
              {line_search_clause}
            GROUP BY l.account_code_raw, l.account_name_raw
            ORDER BY ABS(COALESCE(SUM(l.debit_amount), 0) - COALESCE(SUM(l.credit_amount), 0)) DESC,
                     COALESCE(SUM(l.debit_amount), 0) DESC,
                     l.account_code_raw
            LIMIT :limit
            """
        ),
        params,
    )
    rows = [
        {
            "cuenta_codigo": row.account_code_raw,
            "cuenta_nombre": row.account_name_raw,
            "movimientos": int(row.movimientos or 0),
            "debe": round(float(row.debe or 0), 2),
            "haber": round(float(row.haber or 0), 2),
            "neto": round(float(row.neto or 0), 2),
        }
        for row in result
    ]
    totals_result = await session.execute(
        text(
            f"""
            SELECT
              ROUND(COALESCE(SUM(l.debit_amount), 0)::numeric, 2) AS debe,
              ROUND(COALESCE(SUM(l.credit_amount), 0)::numeric, 2) AS haber
            FROM historical_policy_lines l
            JOIN historical_policy_headers h ON h.id = l.policy_header_id
            WHERE l.import_run_id = :import_run_id
              AND l.fiscal_year = :fiscal_year
              {month_clause}
              {cuenta_clause}
              {header_tipo_clause}
              {line_search_clause}
            """
        ),
        params,
    )
    totals_row = totals_result.first()
    total_debe = round(float((totals_row.debe if totals_row else 0) or 0), 2)
    total_haber = round(float((totals_row.haber if totals_row else 0) or 0), 2)
    summary = {
        "rows": len(rows),
        "source_rows_total": total_count,
        "debe": total_debe,
        "haber": total_haber,
        "neto": round(total_debe - total_haber, 2),
        "company_display": company_display,
    }
    artifact_markdown = "\n".join(
        [
            f"# Mayor contable histórico {period_label} · {company_display}",
            "",
            "- Fuente: tablas históricas canónicas",
            f"- Empresa: {company_display}",
            f"- Debe: {_format_currency(total_debe)}",
            f"- Haber: {_format_currency(total_haber)}",
            f"- Neto: {_format_currency(summary['neto'])}",
            "",
            _markdown_table(
                ["Cuenta", "Nombre", "Movimientos", "Debe", "Haber", "Neto"],
                [
                    [
                        row["cuenta_codigo"],
                        row["cuenta_nombre"],
                        row["movimientos"],
                        row["debe"],
                        row["haber"],
                        row["neto"],
                    ]
                    for row in rows
                ],
            )
            or "_Sin filas coincidentes para el filtro_",
        ]
    ).strip()
    return {
        "report_type": "mayor",
        "title": f"Mayor contable histórico {period_label} · {company_display}",
        "period": period_payload,
        "filters": {
            "company_code": str(company_code or "01").zfill(2),
            "cuenta_codigo": selected_cuenta if selected_cuenta != "all" else None,
            "tipo_poliza": selected_tipo if selected_tipo != "all" else None,
            "q": search or None,
        },
        "summary": summary,
        "summary_by_account": rows,
        "rows": rows,
        "artifact_markdown": artifact_markdown,
        "source": "historical_tables",
        "company_code": str(company_code or "01").zfill(2),
        "company_label": company_label or None,
        "company_display": company_display,
    }


def _extract_company_code_from_accounting_query(
    q: str,
    company_code: str = "01",
) -> tuple[str, str]:
    search = (q or "").strip()
    normalized_code = str(company_code or "01").zfill(2)
    lowered = search.lower()

    alias_map = {
        "psp1705058s4": "01",
        "pmd0608162m2": "02",
        "empresa 01": "01",
        "empresa 1": "01",
        "compañia 01": "01",
        "compania 01": "01",
        "razon social 01": "01",
        "razón social 01": "01",
        "empresa 02": "02",
        "empresa 2": "02",
        "compañia 02": "02",
        "compania 02": "02",
        "razon social 02": "02",
        "razón social 02": "02",
        "empresa 04": "04",
        "empresa 4": "04",
        "compañia 04": "04",
        "compania 04": "04",
        "razon social 04": "04",
        "razón social 04": "04",
    }
    for token, code in alias_map.items():
        if token in lowered:
            normalized_code = code
            lowered = lowered.replace(token, " ")
            search = lowered
    search = " ".join(search.split())
    return normalized_code, search


def _historical_company_display(company_code: str, company_label: Optional[str]) -> str:
    normalized_code = str(company_code or "01").zfill(2)
    label = (company_label or "").strip()
    if label:
        return f"Empresa {normalized_code} · {label}"
    return f"Empresa {normalized_code}"


def _accounting_month_bounds(
    year: Optional[int], month: Optional[int]
) -> Tuple[int, int, datetime, datetime]:
    now = datetime.utcnow()
    selected_year = int(year or now.year)
    selected_month = int(month or now.month)
    if selected_month < 1 or selected_month > 12:
        raise ValueError("month must be between 1 and 12")
    start_dt = datetime(selected_year, selected_month, 1)
    if selected_month == 12:
        end_dt = datetime(selected_year + 1, 1, 1)
    else:
        end_dt = datetime(selected_year, selected_month + 1, 1)
    return selected_year, selected_month, start_dt, end_dt


def _markdown_table(headers: List[str], rows: List[List[Any]]) -> str:
    if not headers:
        return ""
    safe_headers = [str(item or "").strip() or "-" for item in headers]
    lines = [
        "| " + " | ".join(safe_headers) + " |",
        "| " + " | ".join(["---"] * len(safe_headers)) + " |",
    ]
    for row in rows:
        padded = list(row[: len(safe_headers)])
        if len(padded) < len(safe_headers):
            padded.extend([""] * (len(safe_headers) - len(padded)))
        safe_cells = [
            str(cell if cell is not None else "").replace("\n", " ").replace("|", "\\|")
            for cell in padded
        ]
        lines.append("| " + " | ".join(safe_cells) + " |")
    return "\n".join(lines)


def _next_poliza_number(existing_numbers: List[str], tipo_poliza: str) -> str:
    cleaned = [
        str(number or "").strip()
        for number in existing_numbers
        if str(number or "").strip()
    ]
    if not cleaned:
        return f"{tipo_poliza}-0001"

    def _score(number: str) -> Tuple[int, int]:
        match = re.search(r"(\d+)(?!.*\d)", number)
        if not match:
            return (0, 0)
        return (int(match.group(1)), len(match.group(1)))

    best = max(cleaned, key=_score)
    match = re.search(r"(\d+)(?!.*\d)", best)
    if not match:
        return f"{tipo_poliza}-0001"
    width = len(match.group(1))
    next_value = int(match.group(1)) + 1
    start, end = match.span(1)
    return f"{best[:start]}{next_value:0{width}d}{best[end:]}"


def _poliza_snapshot(poliza: AccountingPoliza) -> Dict[str, Any]:
    return {
        "id": str(poliza.id),
        "tipo_poliza": poliza.tipo_poliza,
        "numero_poliza": poliza.numero_poliza,
        "fecha_poliza": (
            poliza.fecha_poliza.isoformat() if poliza.fecha_poliza else None
        ),
        "beneficiario_nombre": poliza.beneficiario_nombre,
        "concepto": poliza.concepto,
        "concepto_resumen": poliza.concepto_resumen,
        "origen": poliza.origen,
        "line_count_actual": poliza.line_count_actual,
    }


def _poliza_line_snapshot(line: AccountingPolizaLine) -> Dict[str, Any]:
    return {
        "id": str(line.id),
        "poliza_id": str(line.poliza_id),
        "line_no": line.line_no,
        "cuenta_codigo": line.cuenta_codigo,
        "cuenta_contable_id": (
            str(line.cuenta_contable_id) if line.cuenta_contable_id else None
        ),
        "concepto": line.concepto,
        "movimiento_no": line.movimiento_no,
        "debe": round(float(line.debe or 0), 2),
        "haber": round(float(line.haber or 0), 2),
    }


async def _record_accounting_audit(
    session: AsyncSession,
    *,
    empleado_id: Optional[uuid.UUID],
    action: str,
    poliza_id: Optional[uuid.UUID],
    before_state: Optional[Dict[str, Any]],
    after_state: Optional[Dict[str, Any]],
    details: Optional[Dict[str, Any]] = None,
) -> None:
    session.add(
        AccountingAuditLog(
            id=uuid.uuid4(),
            empleado_id=empleado_id,
            poliza_id=poliza_id,
            entity_type="poliza",
            action=action,
            before_state=before_state,
            after_state=after_state,
            details=details,
        )
    )


async def _resolve_cuenta_by_id_or_code(
    session: AsyncSession,
    *,
    cuenta_contable_id: Optional[str] = None,
    cuenta_codigo: Optional[str] = None,
) -> Optional[CuentaContable]:
    if cuenta_contable_id:
        try:
            cuenta_uuid = uuid.UUID(str(cuenta_contable_id))
        except ValueError as exc:
            raise ValueError("cuenta_contable_id is invalid") from exc
        return (
            await session.execute(
                select(CuentaContable).where(
                    CuentaContable.id == cuenta_uuid,
                    CuentaContable.activo.is_(True),
                )
            )
        ).scalar_one_or_none()
    if cuenta_codigo:
        codigo = str(cuenta_codigo or "").strip()
        if not codigo:
            return None
        return (
            await session.execute(
                select(CuentaContable).where(
                    CuentaContable.codigo == codigo,
                    CuentaContable.activo.is_(True),
                )
            )
        ).scalar_one_or_none()
    return None


async def _resolve_counterpart_account(
    session: AsyncSession,
    *,
    metodo_pago: Optional[str],
    contra_cuenta_contable_id: Optional[str] = None,
    contra_cuenta_codigo: Optional[str] = None,
) -> Tuple[Optional[CuentaContable], Optional[str]]:
    explicit = await _resolve_cuenta_by_id_or_code(
        session,
        cuenta_contable_id=contra_cuenta_contable_id,
        cuenta_codigo=contra_cuenta_codigo,
    )
    if explicit:
        return explicit, "explicit"

    payment = str(metodo_pago or "").strip().lower()
    env_candidates: List[str] = []
    if "amex" in payment:
        env_candidates.append(
            os.getenv("ASSISTANT_ACCOUNTING_COUNTERPART_AMEX", "").strip()
        )
    if "empresa" in payment or "corporativa" in payment:
        env_candidates.append(
            os.getenv("ASSISTANT_ACCOUNTING_COUNTERPART_TARJETA_EMPRESA", "").strip()
        )
    if "personal" in payment:
        env_candidates.append(
            os.getenv("ASSISTANT_ACCOUNTING_COUNTERPART_TARJETA_PERSONAL", "").strip()
        )
    if "efectivo" in payment or "cash" in payment:
        env_candidates.append(
            os.getenv("ASSISTANT_ACCOUNTING_COUNTERPART_EFECTIVO", "").strip()
        )
    env_candidates.append(
        os.getenv("ASSISTANT_ACCOUNTING_COUNTERPART_DEFAULT", "").strip()
    )
    for code in env_candidates:
        account = await _resolve_cuenta_by_id_or_code(
            session, cuenta_codigo=code or None
        )
        if account:
            return account, "env"

    accounts = (
        (
            await session.execute(
                select(CuentaContable).where(CuentaContable.activo.is_(True))
            )
        )
        .scalars()
        .all()
    )
    if not accounts:
        return None, None

    keywords: List[str] = []
    preferred_types: List[str] = []
    if "amex" in payment or "empresa" in payment or "corporativa" in payment:
        keywords = ["amex", "tarjeta", "corporativa", "banco", "pasivo"]
        preferred_types = ["pasivo", "banco", "proveedor"]
    elif "personal" in payment or "efectivo" in payment or "cash" in payment:
        keywords = [
            "reembolso",
            "empleado",
            "acreedores",
            "deudores",
            "gastos por comprobar",
            "pasivo",
            "anticipo",
        ]
        preferred_types = ["pasivo", "anticipo", "proveedor"]
    else:
        keywords = ["pasivo", "banco", "acreedores", "reembolso"]
        preferred_types = ["pasivo", "banco", "anticipo", "proveedor"]

    scored: List[Tuple[int, CuentaContable]] = []
    for account in accounts:
        score = 0
        tipo = str(account.tipo or "").strip().lower()
        haystack = " ".join(
            [str(account.codigo or "").lower(), str(account.nombre or "").lower(), tipo]
        )
        for idx, item in enumerate(preferred_types):
            if tipo == item:
                score += max(1, 10 - idx)
        for keyword in keywords:
            if keyword and keyword in haystack:
                score += 3
        if score > 0:
            scored.append((score, account))
    if not scored:
        return None, None
    scored.sort(key=lambda item: (-item[0], str(item[1].codigo or "")))
    top_score = scored[0][0]
    top = [item for item in scored if item[0] == top_score]
    if len(top) != 1 and top_score < 9:
        return None, None
    return top[0][1], "heuristic"


def _parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    value = value.strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ValueError("Invalid date format; use YYYY-MM-DD or DD/MM/YYYY")


def _parse_time(value: Optional[str]) -> time:
    raw = (value or "").strip() or "09:00"
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt).time()
        except ValueError:
            continue
    raise ValueError("Invalid time format; use HH:MM")


def _normalize_field_numbers(
    *,
    field_number: Optional[str],
    field_numbers: Optional[List[str]],
) -> List[str]:
    result: List[str] = []
    if field_numbers:
        for raw in field_numbers:
            value = str(raw or "").strip()
            if value and value not in result:
                result.append(value)
    single = (field_number or "").strip()
    if single and single not in result:
        result.insert(0, single)
    if not result:
        result = ["1"]
    return result


def _matches_window_scope(
    *,
    window: Dict[str, Any],
    category_id: str,
    category_name: Optional[str],
    category_gender: Optional[str] = None,
) -> bool:
    wid = str(window.get("category_id") or "").strip()
    wname = str(window.get("category_name") or "").strip().lower()
    wgender = str(window.get("gender") or "").strip().lower()
    if wid and wid != category_id:
        return False
    if wname:
        current = (category_name or "").strip().lower()
        if wname not in current and current not in wname:
            return False
    if wgender:
        current_gender = (category_gender or "").strip().lower()
        if current_gender and wgender != current_gender:
            return False
    return True


def _normalize_windows(
    *,
    daily_start_time: time,
    daily_end_time: Optional[time],
    category_windows: Optional[List[Dict[str, Any]]],
    category_id: str,
    category_name: Optional[str],
) -> List[Tuple[time, Optional[time]]]:
    windows: List[Tuple[time, Optional[time]]] = []
    for raw in category_windows or []:
        if not isinstance(raw, dict):
            continue
        if not _matches_window_scope(
            window=raw,
            category_id=category_id,
            category_name=category_name,
        ):
            continue
        start_raw = str(raw.get("start_time") or "").strip()
        if not start_raw:
            continue
        start_t = _parse_time(start_raw)
        end_raw = str(raw.get("end_time") or "").strip()
        end_t = _parse_time(end_raw) if end_raw else None
        if end_t and end_t <= start_t:
            raise ValueError("Each category window must satisfy end_time > start_time")
        windows.append((start_t, end_t))

    if not windows:
        windows = [(daily_start_time, daily_end_time)]

    windows.sort(key=lambda w: (w[0].hour, w[0].minute, w[0].second))
    return windows


def _window_slot_capacity(
    *,
    start_t: time,
    end_t: Optional[time],
    interval_minutes: int,
) -> Optional[int]:
    if end_t is None:
        return None
    start_m = start_t.hour * 60 + start_t.minute
    end_m = end_t.hour * 60 + end_t.minute
    if end_m < start_m:
        return 0
    return int((end_m - start_m) // interval_minutes) + 1


def _build_slot_generator(
    *,
    start_date: date,
    fields: List[str],
    games_per_day: int,
    interval_minutes: int,
    windows: List[Tuple[time, Optional[time]]],
):
    cursor_day = 0
    cursor_slot = 0

    def _next_slot() -> tuple[datetime, str]:
        nonlocal cursor_day, cursor_slot
        while True:
            slot_idx = cursor_slot
            field_idx = slot_idx % len(fields)
            round_idx = slot_idx // len(fields)
            remaining = round_idx
            target_dt: Optional[datetime] = None
            for start_t, end_t in windows:
                cap = _window_slot_capacity(
                    start_t=start_t,
                    end_t=end_t,
                    interval_minutes=interval_minutes,
                )
                if cap is None:
                    target_dt = datetime.combine(
                        start_date + timedelta(days=cursor_day),
                        start_t,
                    ) + timedelta(minutes=remaining * interval_minutes)
                    break
                if remaining < cap:
                    target_dt = datetime.combine(
                        start_date + timedelta(days=cursor_day),
                        start_t,
                    ) + timedelta(minutes=remaining * interval_minutes)
                    break
                remaining -= cap
            if target_dt is None:
                cursor_day += 1
                cursor_slot = 0
                continue

            field_value = fields[field_idx]
            cursor_slot += 1
            if cursor_slot >= games_per_day:
                cursor_day += 1
                cursor_slot = 0
            return target_dt, field_value

    return _next_slot


def _supabase_base_url() -> str:
    return (os.getenv("SUPABASE_URL") or os.getenv("VITE_SUPABASE_URL") or "").rstrip(
        "/"
    )


def _supabase_api_key() -> str:
    return (
        os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        or os.getenv("SUPABASE_ANON_KEY")
        or os.getenv("VITE_SUPABASE_ANON_KEY")
        or ""
    ).strip()


def _supabase_headers(
    *, include_json: bool = True, prefer_return: bool = False
) -> Dict[str, str]:
    api_key = _supabase_api_key()
    if not api_key:
        raise ValueError("Supabase API key is not configured")
    headers = {
        "apikey": api_key,
        "Authorization": f"Bearer {api_key}",
    }
    if include_json:
        headers["Content-Type"] = "application/json"
    if prefer_return:
        headers["Prefer"] = "return=representation"
    return headers


def _supabase_fetch_json_sync(
    *,
    method: str,
    path: str,
    query: Optional[Dict[str, str]] = None,
    payload: Optional[Any] = None,
) -> Any:
    base = _supabase_base_url()
    if not base:
        raise ValueError("SUPABASE_URL is not configured")
    qs = f"?{urllib_parse.urlencode(query)}" if query else ""
    url = f"{base}/rest/v1/{path.lstrip('/')}{qs}"
    data = None
    headers = _supabase_headers(
        include_json=payload is not None or method.upper() in {"POST", "PATCH", "PUT"},
        prefer_return=method.upper() in {"POST", "PATCH", "PUT"},
    )
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib_request.Request(
        url=url,
        headers=headers,
        data=data,
        method=method.upper(),
    )
    try:
        with urllib_request.urlopen(req, timeout=18) as res:
            body = res.read().decode("utf-8", errors="replace")
            if not body:
                return []
            return json.loads(body)
    except urllib_error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise ValueError(f"Supabase REST error ({exc.code}): {detail}") from exc
    except urllib_error.URLError as exc:
        raise ValueError(f"Supabase REST unreachable: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError("Supabase returned invalid JSON") from exc


async def _supabase_fetch_json(
    *,
    method: str,
    path: str,
    query: Optional[Dict[str, str]] = None,
    payload: Optional[Any] = None,
) -> Any:
    return await asyncio.to_thread(
        _supabase_fetch_json_sync,
        method=method,
        path=path,
        query=query,
        payload=payload,
    )


def _slugify(value: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", (value or "").strip().lower()).strip("-")
    return base or "torneo"


def _dev_allowed_roots() -> List[Path]:
    env_value = os.getenv(
        "ASSISTANT_DEV_ALLOWED_ROOTS",
        "/root/samchat/src,/root/samchat/goal-fest-page,/root/samchat/copatelmex",
    )
    roots: List[Path] = []
    for raw in env_value.split(","):
        p = Path(raw.strip()).resolve()
        if p.exists():
            roots.append(p)
    if not roots:
        roots = [Path("/root/samchat/src").resolve()]
    return roots


def _dev_is_allowed_path(path: Path) -> bool:
    path_resolved = path.resolve()
    for root in _dev_allowed_roots():
        try:
            path_resolved.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def _resolve_dev_path(raw_path: str) -> Path:
    candidate = Path((raw_path or "").strip())
    if not candidate.is_absolute():
        candidate = Path("/root/samchat") / candidate
    candidate = candidate.resolve()
    if not _dev_is_allowed_path(candidate):
        raise ValueError("Path is outside allowed development roots")
    blocked_parts = {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "dist",
        "build",
        "__pycache__",
    }
    if any(part in blocked_parts for part in candidate.parts):
        raise ValueError("Path points to a blocked directory")
    return candidate


def _ensure_text_extension(path: Path) -> None:
    allowed = {
        ".py",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".json",
        ".md",
        ".txt",
        ".yaml",
        ".yml",
        ".toml",
        ".css",
        ".html",
        ".sql",
        ".sh",
    }
    if path.suffix.lower() not in allowed:
        raise ValueError(f"Unsupported file extension: {path.suffix}")


async def dev_repo_search(
    *,
    pattern: str,
    path: str = ".",
    max_results: int = 200,
) -> Dict[str, Any]:
    query = (pattern or "").strip()
    if not query:
        raise ValueError("pattern is required")
    target = _resolve_dev_path(path)
    max_results = max(1, min(int(max_results or 200), 1000))
    lines: List[str] = []

    rg_bin = shutil.which("rg")
    if rg_bin:
        proc = await asyncio.create_subprocess_exec(
            rg_bin,
            "-n",
            "-S",
            "--max-count",
            str(max_results),
            query,
            str(target),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode in {0, 1}:
            lines = [
                ln
                for ln in stdout.decode("utf-8", errors="replace").splitlines()
                if ln.strip()
            ]
    else:
        regex = re.compile(query, flags=re.IGNORECASE)
        for p in target.rglob("*"):
            if len(lines) >= max_results:
                break
            if not p.is_file():
                continue
            try:
                _ensure_text_extension(p)
            except ValueError:
                continue
            try:
                content = p.read_text(encoding="utf-8", errors="ignore").splitlines()
            except Exception:
                continue
            for idx, line in enumerate(content, start=1):
                if regex.search(line):
                    lines.append(f"{p}:{idx}:{line}")
                    if len(lines) >= max_results:
                        break

    return {
        "pattern": query,
        "path": str(target),
        "count": len(lines),
        "results": lines,
    }


async def dev_file_read(
    *,
    path: str,
    start_line: int = 1,
    end_line: int = 200,
    max_chars: int = 20000,
) -> Dict[str, Any]:
    p = _resolve_dev_path(path)
    if not p.exists() or not p.is_file():
        raise ValueError("File not found")
    _ensure_text_extension(p)
    start = max(1, int(start_line or 1))
    end = max(start, min(int(end_line or 200), start + 2000))
    max_chars = max(100, min(int(max_chars or 20000), 200000))
    content = p.read_text(encoding="utf-8", errors="ignore").splitlines()
    slice_lines = content[start - 1 : end]
    numbered = [f"{start + i}: {line}" for i, line in enumerate(slice_lines)]
    text = "\n".join(numbered)
    if len(text) > max_chars:
        text = text[:max_chars]
    return {
        "path": str(p),
        "start_line": start,
        "end_line": end,
        "content": text,
    }


async def dev_file_write(
    *,
    path: str,
    content: str,
    mode: str = "overwrite",
) -> Dict[str, Any]:
    p = _resolve_dev_path(path)
    _ensure_text_extension(p)
    mode_norm = (mode or "overwrite").strip().lower()
    if mode_norm not in {"overwrite", "append"}:
        raise ValueError("mode must be one of: overwrite, append")
    p.parent.mkdir(parents=True, exist_ok=True)
    existing = p.read_text(encoding="utf-8", errors="ignore") if p.exists() else ""
    if mode_norm == "append":
        new_value = existing + str(content or "")
    else:
        new_value = str(content or "")
    p.write_text(new_value, encoding="utf-8")
    return {
        "path": str(p),
        "mode": mode_norm,
        "bytes": len(new_value.encode("utf-8")),
        "changed": existing != new_value,
    }


async def dev_file_replace(
    *,
    path: str,
    old_text: str,
    new_text: str,
    count: int = 0,
) -> Dict[str, Any]:
    p = _resolve_dev_path(path)
    if not p.exists() or not p.is_file():
        raise ValueError("File not found")
    _ensure_text_extension(p)
    if old_text is None or old_text == "":
        raise ValueError("old_text is required")
    raw = p.read_text(encoding="utf-8", errors="ignore")
    limit = int(count or 0)
    if limit < 0:
        raise ValueError("count must be >= 0")
    replaced = raw.replace(
        old_text, new_text, limit if limit > 0 else raw.count(old_text)
    )
    replacements = (
        raw.count(old_text) if limit == 0 else min(raw.count(old_text), limit)
    )
    if replacements == 0:
        return {"path": str(p), "updated": False, "replacements": 0}
    p.write_text(replaced, encoding="utf-8")
    return {
        "path": str(p),
        "updated": True,
        "replacements": replacements,
        "bytes": len(replaced.encode("utf-8")),
    }


async def dev_run_checks(
    *,
    check: str,
    path: str = ".",
    timeout_sec: int = 120,
    max_output_chars: int = 20000,
) -> Dict[str, Any]:
    """
    Run whitelisted development checks in a controlled way.
    """
    key = (check or "").strip().lower()
    timeout_sec = max(5, min(int(timeout_sec or 120), 900))
    max_output_chars = max(1000, min(int(max_output_chars or 20000), 200000))
    workdir = _resolve_dev_path(path)
    if workdir.is_file():
        workdir = workdir.parent

    allowed_commands: Dict[str, List[str]] = {
        "backend_compile": [
            "python3",
            "-m",
            "py_compile",
            "src/samchat/assistant/router.py",
            "src/samchat/assistant/tools.py",
        ],
        "frontend_build_goal_fest": [
            "npm",
            "-C",
            "/root/samchat/goal-fest-page",
            "run",
            "build",
        ],
        "frontend_build_copatelmex": [
            "npm",
            "-C",
            "/root/samchat/copatelmex",
            "run",
            "build",
        ],
        "pytest_assistant": ["pytest", "-q", "tests", "-k", "assistant"],
    }
    if key not in allowed_commands:
        raise ValueError(
            "Unknown check. Allowed: " + ", ".join(sorted(allowed_commands.keys()))
        )

    cmd = allowed_commands[key]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(workdir),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
        timed_out = False
    except asyncio.TimeoutError:
        proc.kill()
        stdout, stderr = await proc.communicate()
        timed_out = True

    out_text = (stdout or b"").decode("utf-8", errors="replace")
    err_text = (stderr or b"").decode("utf-8", errors="replace")
    output = (out_text + ("\n" + err_text if err_text else "")).strip()
    if len(output) > max_output_chars:
        output = output[:max_output_chars] + "\n...[truncated]..."

    return {
        "check": key,
        "command": cmd,
        "workdir": str(workdir),
        "timeout_sec": timeout_sec,
        "timed_out": timed_out,
        "exit_code": int(proc.returncode if proc.returncode is not None else -1),
        "ok": (not timed_out and int(proc.returncode or 0) == 0),
        "output": output,
    }


def _round_robin_pairings(team_ids: List[str]) -> List[List[str]]:
    teams = list(team_ids)
    if len(teams) < 2:
        return []
    if len(teams) % 2 == 1:
        teams.append("__BYE__")
    n = len(teams)
    rounds = n - 1
    half = n // 2
    pairings: List[List[str]] = []
    rotating = teams[:]
    for _ in range(rounds):
        left = rotating[:half]
        right = list(reversed(rotating[half:]))
        for a, b in zip(left, right):
            if a == "__BYE__" or b == "__BYE__":
                continue
            pairings.append([a, b])
        rotating = [rotating[0], rotating[-1], *rotating[1:-1]]
    return pairings


async def finance_vendor_payments(
    session: AsyncSession,
    *,
    vendor_name: str,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 50,
) -> Dict[str, Any]:
    """Sum payments to a vendor (proveedor) from SOLICITUD documents."""
    vendor_name = (vendor_name or "").strip()
    if not vendor_name:
        raise ValueError("vendor_name is required")

    df = _parse_date(date_from)
    dt = _parse_date(date_to)

    # Payment date can be fecha_pago (Date) or pagado_en (DateTime).
    # Filter on either field.
    query = (
        select(
            Documento.id,
            Documento.numero_referencia,
            Documento.estado,
            Documento.fecha_pago,
            Documento.pagado_en,
            Documento.monto_total,
            Documento.monto_solicitado,
            ProveedorCliente.nombre.label("proveedor"),
            ProveedorCliente.rfc.label("rfc"),
        )
        .select_from(Documento)
        .join(ProveedorCliente, Documento.proveedor_cliente_id == ProveedorCliente.id)
        .where(ProveedorCliente.tipo == "proveedor")
        .where(ProveedorCliente.nombre.ilike(f"%{vendor_name}%"))
        .where(Documento.tipo == "SOLICITUD")
        .where(Documento.estado.in_(("pagado", "cerrado", "aprobado")))
    )

    if df:
        query = query.where(
            or_(
                Documento.fecha_pago >= df,
                func.date(Documento.pagado_en) >= df,
            )
        )
    if dt:
        query = query.where(
            or_(
                Documento.fecha_pago <= dt,
                func.date(Documento.pagado_en) <= dt,
            )
        )

    query = query.order_by(
        Documento.pagado_en.desc().nullslast(), Documento.creado_en.desc()
    )
    query = query.limit(max(1, min(limit, 200)))

    rows = (await session.execute(query)).all()
    items: List[Dict[str, Any]] = []
    total = 0.0
    for r in rows:
        amount = float(r.monto_total or r.monto_solicitado or 0)
        total += amount
        items.append(
            {
                "documento_id": str(r.id),
                "numero_referencia": r.numero_referencia,
                "estado": r.estado,
                "fecha_pago": r.fecha_pago.isoformat() if r.fecha_pago else None,
                "pagado_en": r.pagado_en.isoformat() if r.pagado_en else None,
                "monto": amount,
                "proveedor": r.proveedor,
                "rfc": r.rfc,
            }
        )

    return {
        "vendor_name_query": vendor_name,
        "date_from": df.isoformat() if df else None,
        "date_to": dt.isoformat() if dt else None,
        "total_pagado": round(total, 2),
        "moneda": "MXN",
        "documentos": items,
        "nota": (
            "Estimado usando documentos tipo SOLICITUD y campos "
            "monto_total/monto_solicitado."
        ),
    }


def _tournaments_v2_read_flags() -> tuple[bool, bool]:
    try:
        cfg = load_tournaments_v2_config()
        return bool(cfg.reads_enabled), bool(cfg.fallback_to_legacy)
    except Exception:
        logger.exception("Failed to load tournaments_v2 config")
        return False, True


def _tournaments_v2_write_flags() -> tuple[bool, bool]:
    try:
        cfg = load_tournaments_v2_config()
        return bool(cfg.writes_enabled), bool(cfg.fallback_to_legacy)
    except Exception:
        logger.exception("Failed to load tournaments_v2 config for writes")
        return False, True


def _apply_tournaments_v2_fallback_metadata(
    payload: Dict[str, Any],
    *,
    error: Exception,
) -> Dict[str, Any]:
    result = dict(payload)
    result["source"] = result.get("source") or "legacy_copa_telmex_db"
    fallback_note = (
        "Fallback a tablas legacy locales porque tournaments_v2/Supabase "
        "no estuvo disponible. "
        f"Detalle: {type(error).__name__}: {error}"
    )
    note_value = result.get("nota")
    result["nota"] = (
        f"{note_value} {fallback_note}".strip() if note_value else fallback_note
    )
    result["adapter_fallback"] = {
        "from": "supabase_tournaments_v2",
        "to": "legacy_copa_telmex_db",
        "error": f"{type(error).__name__}: {error}",
    }
    return result


async def _tournament_registration_breakdown_legacy(
    session: AsyncSession,
    *,
    tournament_key: str,
    state: str,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> Dict[str, Any]:
    df = _parse_date(date_from)
    dt = _parse_date(date_to)

    team_filter = [Team.state.ilike(f"%{state}%")]
    if df:
        team_filter.append(func.date(Team.created_at) >= df)
    if dt:
        team_filter.append(func.date(Team.created_at) <= dt)

    total_teams = (
        await session.execute(select(func.count(Team.id)).where(*team_filter))
    ).scalar_one()
    total_players = (
        await session.execute(
            select(func.count(Player.id))
            .select_from(Player)
            .join(Team, Player.team_id == Team.id)
            .where(*team_filter)
        )
    ).scalar_one()

    muni_rows = (
        await session.execute(
            select(
                Team.municipality,
                func.count(func.distinct(Team.id)).label("teams"),
                func.count(Player.id).label("players"),
            )
            .select_from(Team)
            .outerjoin(Player, Player.team_id == Team.id)
            .where(*team_filter)
            .group_by(Team.municipality)
            .order_by(func.count(Player.id).desc())
        )
    ).all()

    municipalities: List[Dict[str, Any]] = []
    for r in muni_rows:
        municipalities.append(
            {
                "municipio": r.municipality or "(sin municipio)",
                "equipos": int(r.teams or 0),
                "jugadores": int(r.players or 0),
            }
        )

    return {
        "tournament_key": tournament_key,
        "state_query": state,
        "date_from": df.isoformat() if df else None,
        "date_to": dt.isoformat() if dt else None,
        "total_equipos": int(total_teams),
        "total_jugadores": int(total_players),
        "desglose_por_municipio": municipalities,
        "source": "legacy_copa_telmex_db",
        "nota": (
            "Conteo basado en copa_telmex_teams y copa_telmex_players "
            "en la BD del torneo seleccionado."
        ),
    }


async def _tournament_ops_query_legacy(
    session: AsyncSession,
    *,
    tournament_key: str,
    question: Optional[str] = None,
    state: Optional[str] = None,
    municipality: Optional[str] = None,
    category: Optional[str] = None,
    gender: Optional[str] = None,
    team_name: Optional[str] = None,
    tournament_slug: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 50,
) -> Dict[str, Any]:
    df = _parse_date(date_from)
    dt = _parse_date(date_to)
    max_limit = max(1, min(int(limit or 50), 200))

    normalized = {
        "state": (state or "").strip(),
        "municipality": (municipality or "").strip(),
        "category": (category or "").strip(),
        "gender": (gender or "").strip(),
        "team_name": (team_name or "").strip(),
        "tournament_slug": (tournament_slug or "").strip(),
    }

    team_filters = []
    if normalized["state"]:
        team_filters.append(Team.state.ilike(f"%{normalized['state']}%"))
    if normalized["municipality"]:
        team_filters.append(Team.municipality.ilike(f"%{normalized['municipality']}%"))
    if normalized["category"]:
        team_filters.append(Team.category.ilike(f"%{normalized['category']}%"))
    if normalized["gender"]:
        team_filters.append(Team.gender.ilike(f"%{normalized['gender']}%"))
    if normalized["team_name"]:
        team_filters.append(Team.name.ilike(f"%{normalized['team_name']}%"))
    if normalized["tournament_slug"]:
        team_filters.append(
            Team.tournament_slug.ilike(f"%{normalized['tournament_slug']}%")
        )
    if df:
        team_filters.append(func.date(Team.created_at) >= df)
    if dt:
        team_filters.append(func.date(Team.created_at) <= dt)

    total_teams = (
        await session.execute(select(func.count(Team.id)).where(*team_filters))
    ).scalar_one()
    total_players = (
        await session.execute(
            select(func.count(Player.id))
            .select_from(Player)
            .join(Team, Player.team_id == Team.id)
            .where(*team_filters)
        )
    ).scalar_one()

    muni_rows = (
        await session.execute(
            select(
                Team.municipality.label("municipality"),
                func.count(func.distinct(Team.id)).label("teams"),
                func.count(Player.id).label("players"),
            )
            .select_from(Team)
            .outerjoin(Player, Player.team_id == Team.id)
            .where(*team_filters)
            .group_by(Team.municipality)
            .order_by(
                func.count(Player.id).desc(), func.count(func.distinct(Team.id)).desc()
            )
            .limit(max_limit)
        )
    ).all()
    municipality_breakdown = [
        {
            "municipio": r.municipality or "(sin municipio)",
            "equipos": int(r.teams or 0),
            "jugadores": int(r.players or 0),
        }
        for r in muni_rows
    ]

    cat_rows = (
        await session.execute(
            select(
                Team.category.label("category"),
                func.count(func.distinct(Team.id)).label("teams"),
                func.count(Player.id).label("players"),
            )
            .select_from(Team)
            .outerjoin(Player, Player.team_id == Team.id)
            .where(*team_filters)
            .group_by(Team.category)
            .order_by(
                func.count(Player.id).desc(), func.count(func.distinct(Team.id)).desc()
            )
            .limit(max_limit)
        )
    ).all()
    category_breakdown = [
        {
            "categoria": r.category or "(sin categoria)",
            "equipos": int(r.teams or 0),
            "jugadores": int(r.players or 0),
        }
        for r in cat_rows
    ]

    gender_rows = (
        await session.execute(
            select(
                Team.gender.label("gender"),
                func.count(func.distinct(Team.id)).label("teams"),
                func.count(Player.id).label("players"),
            )
            .select_from(Team)
            .outerjoin(Player, Player.team_id == Team.id)
            .where(*team_filters)
            .group_by(Team.gender)
            .order_by(
                func.count(Player.id).desc(), func.count(func.distinct(Team.id)).desc()
            )
            .limit(max_limit)
        )
    ).all()
    gender_breakdown = [
        {
            "rama": r.gender or "(sin rama)",
            "equipos": int(r.teams or 0),
            "jugadores": int(r.players or 0),
        }
        for r in gender_rows
    ]

    team_rows = (
        await session.execute(
            select(
                Team.id,
                Team.name,
                Team.state,
                Team.municipality,
                Team.category,
                Team.gender,
                Team.tournament_slug,
                Team.created_at,
                func.count(Player.id).label("players"),
            )
            .select_from(Team)
            .outerjoin(Player, Player.team_id == Team.id)
            .where(*team_filters)
            .group_by(
                Team.id,
                Team.name,
                Team.state,
                Team.municipality,
                Team.category,
                Team.gender,
                Team.tournament_slug,
                Team.created_at,
            )
            .order_by(func.count(Player.id).desc(), Team.created_at.desc())
            .limit(max_limit)
        )
    ).all()
    teams = [
        {
            "team_id": str(r.id),
            "equipo": r.name,
            "estado": r.state,
            "municipio": r.municipality,
            "categoria": r.category,
            "rama": r.gender,
            "tournament_slug": r.tournament_slug,
            "jugadores": int(r.players or 0),
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in team_rows
    ]

    player_filters = list(team_filters)
    if normalized["team_name"]:
        player_filters.append(Team.name.ilike(f"%{normalized['team_name']}%"))
    player_rows = (
        await session.execute(
            select(
                Player.id,
                Player.first_name,
                Player.last_name,
                Player.birth_date,
                Player.curp,
                Player.roster_index,
                Team.id.label("team_id"),
                Team.name.label("team_name"),
                Team.category.label("team_category"),
                Team.gender.label("team_gender"),
                Team.state.label("team_state"),
                Team.municipality.label("team_municipality"),
            )
            .select_from(Player)
            .join(Team, Player.team_id == Team.id)
            .where(*player_filters)
            .order_by(
                Team.name.asc(),
                Player.roster_index.asc().nullslast(),
                Player.created_at.asc(),
            )
            .limit(max_limit)
        )
    ).all()
    players = [
        {
            "player_id": str(r.id),
            "nombre": (
                f"{(r.first_name or '').strip()} " f"{(r.last_name or '').strip()}"
            ).strip(),
            "birth_date": r.birth_date.isoformat() if r.birth_date else None,
            "curp": r.curp,
            "roster_index": r.roster_index,
            "team_id": str(r.team_id) if r.team_id else None,
            "equipo": r.team_name,
            "categoria": r.team_category,
            "rama": r.team_gender,
            "estado": r.team_state,
            "municipio": r.team_municipality,
        }
        for r in player_rows
    ]

    return {
        "tournament_key": tournament_key,
        "question": (question or "").strip() or None,
        "filters": {
            "state": normalized["state"] or None,
            "municipality": normalized["municipality"] or None,
            "category": normalized["category"] or None,
            "gender": normalized["gender"] or None,
            "team_name": normalized["team_name"] or None,
            "tournament_slug": normalized["tournament_slug"] or None,
            "date_from": df.isoformat() if df else None,
            "date_to": dt.isoformat() if dt else None,
        },
        "totals": {
            "equipos": int(total_teams or 0),
            "jugadores": int(total_players or 0),
        },
        "breakdowns": {
            "por_municipio": municipality_breakdown,
            "por_categoria": category_breakdown,
            "por_rama": gender_breakdown,
        },
        "teams": teams,
        "players": players,
        "limit": max_limit,
        "source": "legacy_copa_telmex_db",
        "nota": (
            "Consulta universal de operaciones sobre tablas copa_telmex_teams y "
            "copa_telmex_players en la BD del torneo seleccionado."
        ),
    }


async def tournament_registration_breakdown(
    session: AsyncSession,
    *,
    tournament_key: str,
    state: str,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> Dict[str, Any]:
    """Count teams and players for a tournament by state and municipality."""
    state = (state or "").strip()
    if not state:
        raise ValueError("state is required")

    reads_enabled, fallback_to_legacy = _tournaments_v2_read_flags()
    if reads_enabled:
        try:
            return await registration_breakdown_v2(
                tournament_key=tournament_key,
                state=state,
                date_from=date_from,
                date_to=date_to,
            )
        except Exception as exc:
            if not fallback_to_legacy:
                raise
            logger.warning(
                "tournaments_v2 registration breakdown failed; "
                "falling back to legacy: %s",
                exc,
            )
            legacy = await _tournament_registration_breakdown_legacy(
                session,
                tournament_key=tournament_key,
                state=state,
                date_from=date_from,
                date_to=date_to,
            )
            return _apply_tournaments_v2_fallback_metadata(legacy, error=exc)

    return await _tournament_registration_breakdown_legacy(
        session,
        tournament_key=tournament_key,
        state=state,
        date_from=date_from,
        date_to=date_to,
    )


async def tournament_ops_query(
    session: AsyncSession,
    *,
    tournament_key: str,
    question: Optional[str] = None,
    state: Optional[str] = None,
    municipality: Optional[str] = None,
    category: Optional[str] = None,
    gender: Optional[str] = None,
    team_name: Optional[str] = None,
    tournament_slug: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 50,
) -> Dict[str, Any]:
    """
    Universal read-only query for tournament operational data.

    Returns totals plus multiple breakdowns so the LLM can answer a wide range of
    questions without needing one tool per question pattern.
    """
    reads_enabled, fallback_to_legacy = _tournaments_v2_read_flags()
    if reads_enabled:
        try:
            return await tournament_ops_query_v2(
                tournament_key=tournament_key,
                question=question,
                state=state,
                municipality=municipality,
                category=category,
                gender=gender,
                team_name=team_name,
                tournament_slug=tournament_slug,
                date_from=date_from,
                date_to=date_to,
                limit=limit,
            )
        except Exception as exc:
            if not fallback_to_legacy:
                raise
            logger.warning(
                "tournaments_v2 ops query failed; falling back to legacy: %s",
                exc,
            )
            legacy = await _tournament_ops_query_legacy(
                session,
                tournament_key=tournament_key,
                question=question,
                state=state,
                municipality=municipality,
                category=category,
                gender=gender,
                team_name=team_name,
                tournament_slug=tournament_slug,
                date_from=date_from,
                date_to=date_to,
                limit=limit,
            )
            return _apply_tournaments_v2_fallback_metadata(legacy, error=exc)

    return await _tournament_ops_query_legacy(
        session,
        tournament_key=tournament_key,
        question=question,
        state=state,
        municipality=municipality,
        category=category,
        gender=gender,
        team_name=team_name,
        tournament_slug=tournament_slug,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
    )


async def tournament_schedule_create(
    *,
    tournament_key: str,
    tournament_slug: Optional[str] = None,
    tournament_name: Optional[str] = None,
    category_id: Optional[str] = None,
    phase: str = "Fase estatal",
    start_date: str,
    kickoff_time: str = "09:00",
    games_per_day: int = 4,
    interval_minutes: int = 90,
    field_number: Optional[str] = None,
    field_numbers: Optional[List[str]] = None,
    infinite_fields: bool = False,
    daily_start_time: Optional[str] = None,
    daily_end_time: Optional[str] = None,
    category_windows: Optional[List[Dict[str, Any]]] = None,
    status: str = "scheduled",
    replace_existing_phase: bool = False,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """
    Create a round-robin schedule in Supabase `matches` for a tournament/category.

    This powers conversational commands like:
    "Crea el calendario de juegos del torneo X".
    """
    writes_enabled, fallback_to_legacy = _tournaments_v2_write_flags()
    if writes_enabled:
        try:
            return await schedule_create_v2(
                tournament_key=tournament_key,
                tournament_slug=tournament_slug,
                tournament_name=tournament_name,
                category_id=category_id,
                phase=phase,
                start_date=start_date,
                kickoff_time=kickoff_time,
                games_per_day=games_per_day,
                interval_minutes=interval_minutes,
                field_number=field_number,
                field_numbers=field_numbers,
                infinite_fields=infinite_fields,
                daily_start_time=daily_start_time,
                daily_end_time=daily_end_time,
                category_windows=category_windows,
                status=status,
                replace_existing_phase=replace_existing_phase,
                dry_run=dry_run,
            )
        except Exception as exc:
            if not fallback_to_legacy:
                raise
            logger.warning(
                "tournaments_v2 schedule create failed; "
                "falling back to legacy writer: %s",
                exc,
            )
    if games_per_day < 1 or games_per_day > 20:
        raise ValueError("games_per_day must be between 1 and 20")
    if interval_minutes < 30 or interval_minutes > 600:
        raise ValueError("interval_minutes must be between 30 and 600")

    phase_value = (phase or "").strip() or "Fase estatal"
    status_value = (status or "scheduled").strip().lower()
    if status_value not in {
        "scheduled",
        "in_progress",
        "live",
        "finished",
        "completed",
    }:
        raise ValueError(
            "status must be one of: scheduled, in_progress, live, finished, completed"
        )

    start_d = _parse_date(start_date)
    if not start_d:
        raise ValueError("start_date is required")
    if not (daily_start_time or "").strip() or not (daily_end_time or "").strip():
        raise ValueError(
            "Para crear calendario necesito disponibilidad horaria: "
            "daily_start_time y daily_end_time (ej. 08:00 y 18:00)."
        )
    day_start_t = _parse_time(daily_start_time)
    day_end_t = _parse_time(daily_end_time)
    if day_end_t and day_end_t <= day_start_t:
        raise ValueError("daily_end_time must be later than daily_start_time")
    fields = _normalize_field_numbers(
        field_number=field_number, field_numbers=field_numbers
    )
    if infinite_fields:
        # Treat fields as non-constraining by creating virtual parallel fields.
        fields = [f"INF-{i+1}" for i in range(max(1, games_per_day))]
    elif not (field_number or (field_numbers or [])):
        raise ValueError(
            "Necesito canchas disponibles (field_numbers/field_number) "
            "o indica infinite_fields=true."
        )
    if games_per_day < len(fields):
        raise ValueError("games_per_day must be >= number of fields")

    slug_hint = (tournament_slug or "").strip()
    name_hint = (tournament_name or "").strip()
    if not slug_hint and not name_hint:
        raise ValueError("Provide tournament_slug or tournament_name")

    # 1) Resolve tournament
    query: Dict[str, str] = {"select": "id,name,slug", "limit": "1"}
    if slug_hint:
        query["slug"] = f"eq.{slug_hint}"
    else:
        query["name"] = f"ilike.*{name_hint}*"
    tournaments = await _supabase_fetch_json(
        method="GET", path="tournaments", query=query
    )
    if not tournaments:
        fallback_slug = _slugify(name_hint)
        tournaments = await _supabase_fetch_json(
            method="GET",
            path="tournaments",
            query={
                "select": "id,name,slug",
                "slug": f"eq.{fallback_slug}",
                "limit": "1",
            },
        )
    if not tournaments:
        raise ValueError("Tournament not found in Supabase")
    tournament = tournaments[0]
    tournament_id = str(tournament.get("id"))

    # 2) Resolve category
    cat_id = (category_id or "").strip()
    if not cat_id:
        cats = await _supabase_fetch_json(
            method="GET",
            path="categories",
            query={
                "select": "id,name",
                "tournament_id": f"eq.{tournament_id}",
                "order": "created_at.asc",
                "limit": "1",
            },
        )
        if not cats:
            raise ValueError("No categories found for tournament")
        cat_id = str(cats[0].get("id"))
    else:
        cats = await _supabase_fetch_json(
            method="GET",
            path="categories",
            query={"select": "id,name", "id": f"eq.{cat_id}", "limit": "1"},
        )
    category_name = (cats[0].get("name") if cats else None) or None
    windows = _normalize_windows(
        daily_start_time=day_start_t,
        daily_end_time=day_end_t,
        category_windows=category_windows,
        category_id=cat_id,
        category_name=category_name,
    )

    # 3) Resolve team list from registrations + teams
    regs = await _supabase_fetch_json(
        method="GET",
        path="registrations",
        query={"select": "team_id", "category_id": f"eq.{cat_id}", "limit": "1000"},
    )
    reg_team_ids = sorted(
        {
            str((r or {}).get("team_id") or "").strip()
            for r in regs
            if (r or {}).get("team_id")
        }
    )
    team_ids: List[str] = []
    if reg_team_ids:
        for i in range(0, len(reg_team_ids), 200):
            chunk = reg_team_ids[i : i + 200]
            teams = await _supabase_fetch_json(
                method="GET",
                path="teams",
                query={
                    "select": "id,tournament_id,team_name",
                    "id": f"in.({','.join(chunk)})",
                    "tournament_id": f"eq.{tournament_id}",
                },
            )
            team_ids.extend([str(t.get("id")) for t in teams if t.get("id")])
    else:
        teams = await _supabase_fetch_json(
            method="GET",
            path="teams",
            query={
                "select": "id,team_name",
                "tournament_id": f"eq.{tournament_id}",
                "order": "created_at.asc",
                "limit": "1000",
            },
        )
        team_ids = [str(t.get("id")) for t in teams if t.get("id")]

    team_ids = list(dict.fromkeys(team_ids))
    if len(team_ids) < 2:
        raise ValueError("Need at least 2 teams to generate schedule")

    # 4) Build round-robin pairings
    pairings = _round_robin_pairings(team_ids)
    if not pairings:
        raise ValueError("Could not generate pairings")

    # 5) Build match payload
    rows: List[Dict[str, Any]] = []
    _next_slot = _build_slot_generator(
        start_date=start_d,
        fields=fields,
        games_per_day=games_per_day,
        interval_minutes=interval_minutes,
        windows=windows,
    )

    for home_id, away_id in pairings:
        match_dt, field_value = _next_slot()
        rows.append(
            {
                "tournament_id": tournament_id,
                "category_id": cat_id,
                "home_team_id": home_id,
                "away_team_id": away_id,
                "match_date": match_dt.isoformat(),
                "field_number": field_value,
                "phase": phase_value,
                "status": status_value,
                "home_score": 0 if status_value in {"finished", "completed"} else None,
                "away_score": 0 if status_value in {"finished", "completed"} else None,
            }
        )

    existing_count = 0
    if replace_existing_phase:
        existing = await _supabase_fetch_json(
            method="GET",
            path="matches",
            query={
                "select": "id",
                "tournament_id": f"eq.{tournament_id}",
                "category_id": f"eq.{cat_id}",
                "phase": f"eq.{phase_value}",
                "limit": "2000",
            },
        )
        existing_count = len(existing or [])

    if dry_run:
        return {
            "created": False,
            "dry_run": True,
            "tournament": {
                "id": tournament_id,
                "name": tournament.get("name"),
                "slug": tournament.get("slug"),
            },
            "category": {"id": cat_id, "name": category_name},
            "phase": phase_value,
            "status": status_value,
            "replace_existing_phase": replace_existing_phase,
            "existing_matches_in_phase": existing_count,
            "teams_count": len(team_ids),
            "fields": fields,
            "infinite_fields": bool(infinite_fields),
            "daily_start_time": day_start_t.strftime("%H:%M"),
            "daily_end_time": day_end_t.strftime("%H:%M") if day_end_t else None,
            "category_windows_applied": [
                {
                    "start_time": s.strftime("%H:%M"),
                    "end_time": e.strftime("%H:%M") if e else None,
                }
                for s, e in windows
            ],
            "matches_planned": len(rows),
            "sample_matches": rows[: min(5, len(rows))],
        }

    if replace_existing_phase:
        await _supabase_fetch_json(
            method="DELETE",
            path="matches",
            query={
                "tournament_id": f"eq.{tournament_id}",
                "category_id": f"eq.{cat_id}",
                "phase": f"eq.{phase_value}",
            },
        )

    inserted = await _supabase_fetch_json(method="POST", path="matches", payload=rows)
    inserted_ids = [str(r.get("id")) for r in (inserted or []) if r.get("id")]
    return {
        "created": True,
        "dry_run": False,
        "tournament": {
            "id": tournament_id,
            "name": tournament.get("name"),
            "slug": tournament.get("slug"),
        },
        "category": {"id": cat_id, "name": category_name},
        "phase": phase_value,
        "status": status_value,
        "replace_existing_phase": replace_existing_phase,
        "existing_matches_replaced": existing_count if replace_existing_phase else 0,
        "teams_count": len(team_ids),
        "fields": fields,
        "infinite_fields": bool(infinite_fields),
        "daily_start_time": day_start_t.strftime("%H:%M"),
        "daily_end_time": day_end_t.strftime("%H:%M") if day_end_t else None,
        "category_windows_applied": [
            {
                "start_time": s.strftime("%H:%M"),
                "end_time": e.strftime("%H:%M") if e else None,
            }
            for s, e in windows
        ],
        "matches_created": len(inserted_ids),
        "match_ids": inserted_ids[:100],
    }


def _safe_str(value: Any) -> str:
    return str(value or "").strip()


def _pick_value(row: Dict[str, Any], keys: List[str]) -> Optional[Any]:
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key]
    return None


def _parse_birth_date_text(value: Any) -> Optional[str]:
    raw = _safe_str(value)
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d-%m-%y", "%d/%m/%y"):
        try:
            dt = datetime.strptime(raw, fmt).date()
            return dt.isoformat()
        except ValueError:
            continue
    return None


async def _resolve_default_user_id_for_team() -> str:
    # Prefer an admin account from role assignments.
    admin_roles = await _supabase_fetch_json(
        method="GET",
        path="user_roles",
        query={"select": "user_id,role", "role": "eq.admin", "limit": "1"},
    )
    if admin_roles:
        uid = _safe_str((admin_roles[0] or {}).get("user_id"))
        if uid:
            return uid

    any_roles = await _supabase_fetch_json(
        method="GET",
        path="user_roles",
        query={"select": "user_id", "limit": "1"},
    )
    if any_roles:
        uid = _safe_str((any_roles[0] or {}).get("user_id"))
        if uid:
            return uid

    profiles = await _supabase_fetch_json(
        method="GET",
        path="profiles",
        query={"select": "id,user_id", "limit": "1"},
    )
    if profiles:
        row = profiles[0] or {}
        uid = _safe_str(row.get("user_id")) or _safe_str(row.get("id"))
        if uid:
            return uid

    raise ValueError(
        "No se pudo resolver user_id para crear team (user_roles/profiles vacios)"
    )


async def tournament_team_register_from_roster(
    *,
    tournament_key: str,
    tournament_slug: Optional[str] = None,
    tournament_name: Optional[str] = None,
    category_id: Optional[str] = None,
    category_name: Optional[str] = None,
    team_name: str,
    state: Optional[str] = None,
    country: str = "Mexico",
    phone_country_code: str = "+52",
    phone_number: Optional[str] = None,
    user_id: Optional[str] = None,
    payment_status: str = "pending",
    notes: Optional[str] = None,
    representative_name: Optional[str] = None,
    representative_email: Optional[str] = None,
    representative_phone: Optional[str] = None,
    municipality: Optional[str] = None,
    players: Optional[List[Dict[str, Any]]] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    team_name_value = _safe_str(team_name)
    if not team_name_value:
        raise ValueError("team_name is required")
    roster = list(players or [])
    if not roster:
        raise ValueError("players is required and must contain at least one player")

    writes_enabled, fallback_to_legacy = _tournaments_v2_write_flags()
    fallback_error: Optional[Exception] = None
    if writes_enabled:
        try:
            return await register_team_from_roster_v2(
                tournament_key=tournament_key,
                tournament_slug=tournament_slug,
                tournament_name=tournament_name,
                category_id=category_id,
                category_name=category_name,
                team_name=team_name,
                state=state,
                country=country,
                phone_country_code=phone_country_code,
                phone_number=phone_number,
                user_id=user_id,
                payment_status=payment_status,
                notes=notes,
                representative_name=representative_name,
                representative_email=representative_email,
                representative_phone=representative_phone,
                municipality=municipality,
                players=players,
                dry_run=dry_run,
            )
        except Exception as exc:
            if not fallback_to_legacy:
                raise
            fallback_error = exc
            logger.warning(
                "tournaments_v2 team registration failed; "
                "falling back to legacy writer: %s",
                exc,
            )

    slug_hint = _safe_str(tournament_slug)
    name_hint = _safe_str(tournament_name)
    if not slug_hint and not name_hint:
        raise ValueError("Provide tournament_slug or tournament_name")

    # 1) Resolve tournament.
    t_query: Dict[str, str] = {"select": "id,name,slug", "limit": "1"}
    if slug_hint:
        t_query["slug"] = f"eq.{slug_hint}"
    else:
        t_query["name"] = f"ilike.*{name_hint}*"
    tournaments = await _supabase_fetch_json(
        method="GET", path="tournaments", query=t_query
    )
    if not tournaments and name_hint:
        tournaments = await _supabase_fetch_json(
            method="GET",
            path="tournaments",
            query={
                "select": "id,name,slug",
                "slug": f"eq.{_slugify(name_hint)}",
                "limit": "1",
            },
        )
    if not tournaments:
        raise ValueError("Tournament not found in Supabase")
    tournament = tournaments[0]
    tournament_id = _safe_str(tournament.get("id"))

    # 2) Resolve category.
    cat_id = _safe_str(category_id)
    cat_name_hint = _safe_str(category_name)
    if cat_id:
        cats = await _supabase_fetch_json(
            method="GET",
            path="categories",
            query={"select": "id,name", "id": f"eq.{cat_id}", "limit": "1"},
        )
    elif cat_name_hint:
        cats = await _supabase_fetch_json(
            method="GET",
            path="categories",
            query={
                "select": "id,name",
                "tournament_id": f"eq.{tournament_id}",
                "name": f"ilike.*{cat_name_hint}*",
                "limit": "1",
            },
        )
    else:
        cats = await _supabase_fetch_json(
            method="GET",
            path="categories",
            query={
                "select": "id,name",
                "tournament_id": f"eq.{tournament_id}",
                "order": "created_at.asc",
                "limit": "1",
            },
        )
    if not cats:
        raise ValueError(
            "No category found for tournament; provide category_id or category_name"
        )
    cat = cats[0]
    cat_id = _safe_str(cat.get("id"))
    cat_name = _safe_str(cat.get("name"))

    # 3) Resolve user id for teams.user_id
    team_user_id = _safe_str(user_id) or await _resolve_default_user_id_for_team()

    team_payload = {
        "user_id": team_user_id,
        "team_name": team_name_value,
        "academy_name": None,
        "state": _safe_str(state) or "Estado de Mexico",
        "country": _safe_str(country) or "Mexico",
        "phone_country_code": _safe_str(phone_country_code) or "+52",
        "phone_number": _safe_str(phone_number or representative_phone) or "5500000000",
        "status": "pending",
        "tournament_id": tournament_id,
    }

    registration_payload = {
        "category_id": cat_id,
        "payment_status": _safe_str(payment_status) or "pending",
        "notes": _safe_str(notes) or None,
    }

    player_payloads: List[Dict[str, Any]] = []
    for idx, raw in enumerate(roster, start=1):
        if not isinstance(raw, dict):
            continue
        first_name = _safe_str(
            _pick_value(raw, ["first_name", "nombre", "nombres", "name"])
        )
        last_name = _safe_str(_pick_value(raw, ["last_name", "apellido", "apellidos"]))
        paternal = _safe_str(_pick_value(raw, ["paternal_surname", "apellido_paterno"]))
        maternal = _safe_str(_pick_value(raw, ["maternal_surname", "apellido_materno"]))

        if not last_name and (paternal or maternal):
            last_name = (paternal or maternal).strip()
        if not first_name and not last_name:
            continue

        birth_date = (
            _parse_birth_date_text(
                _pick_value(
                    raw, ["birth_date", "fecha_nacimiento", "nacimiento", "fecha"]
                )
            )
            or "2012-01-01"
        )
        curp = _safe_str(_pick_value(raw, ["curp"]))
        parent_name = (
            _safe_str(
                _pick_value(
                    raw, ["parent_name", "tutor", "nombre_tutor", "representante"]
                )
            )
            or _safe_str(representative_name)
            or "Tutor pendiente"
        )
        parent_email = (
            _safe_str(
                _pick_value(
                    raw, ["parent_email", "correo", "email", "correo_electronico"]
                )
            )
            or _safe_str(representative_email)
            or "pendiente@sam.chat"
        )
        parent_phone = (
            _safe_str(_pick_value(raw, ["parent_phone", "telefono", "celular"]))
            or _safe_str(representative_phone)
            or "5500000000"
        )

        jersey_number_raw = _pick_value(raw, ["jersey_number", "numero", "dorsal"])
        try:
            jersey_number = (
                int(str(jersey_number_raw).strip())
                if jersey_number_raw not in (None, "")
                else idx
            )
        except ValueError:
            jersey_number = idx

        player_payloads.append(
            {
                "first_name": first_name or "Jugador",
                "last_name": last_name or f"#{idx}",
                "paternal_surname": paternal or None,
                "maternal_surname": maternal or None,
                "birth_date": birth_date,
                "curp": curp or None,
                "parent_name": parent_name,
                "parent_email": parent_email,
                "parent_phone": parent_phone,
                "jersey_number": jersey_number,
                "position": _safe_str(_pick_value(raw, ["position", "posicion"]))
                or None,
                "documents_complete": False,
                "documents_verified": False,
            }
        )

    if not player_payloads:
        raise ValueError("No se pudieron mapear jugadores validos desde players[]")

    if dry_run:
        result = {
            "created": False,
            "dry_run": True,
            "tournament": {
                "id": tournament_id,
                "name": tournament.get("name"),
                "slug": tournament.get("slug"),
            },
            "category": {"id": cat_id, "name": cat_name},
            "team_preview": team_payload,
            "registration_preview": registration_payload,
            "players_count": len(player_payloads),
            "players_sample": player_payloads[:5],
        }
        if fallback_error:
            return _apply_tournaments_v2_fallback_metadata(result, error=fallback_error)
        return result

    team_inserted = await _supabase_fetch_json(
        method="POST", path="teams", payload=[team_payload]
    )
    if not team_inserted:
        raise ValueError("No se pudo crear el equipo")
    team_row = (team_inserted or [None])[0] or {}
    team_id = _safe_str(team_row.get("id"))
    if not team_id:
        raise ValueError("Supabase no devolvio id de team")

    registration_insert = dict(registration_payload)
    registration_insert["team_id"] = team_id
    reg_inserted = await _supabase_fetch_json(
        method="POST", path="registrations", payload=[registration_insert]
    )
    if not reg_inserted:
        raise ValueError("No se pudo crear el registro de categoria")
    reg_row = (reg_inserted or [None])[0] or {}
    registration_id = _safe_str(reg_row.get("id"))
    if not registration_id:
        raise ValueError("Supabase no devolvio id de registration")

    for p in player_payloads:
        p["registration_id"] = registration_id

    inserted_player_ids: List[str] = []
    for i in range(0, len(player_payloads), 200):
        chunk = player_payloads[i : i + 200]
        inserted = await _supabase_fetch_json(
            method="POST", path="players", payload=chunk
        )
        inserted_player_ids.extend(
            [
                _safe_str(r.get("id"))
                for r in (inserted or [])
                if _safe_str((r or {}).get("id"))
            ]
        )

    result = {
        "created": True,
        "dry_run": False,
        "tournament": {
            "id": tournament_id,
            "name": tournament.get("name"),
            "slug": tournament.get("slug"),
        },
        "category": {"id": cat_id, "name": cat_name},
        "team": {"id": team_id, "team_name": team_row.get("team_name")},
        "registration": {"id": registration_id},
        "players_created": len(inserted_player_ids),
        "player_ids": inserted_player_ids[:100],
    }
    if fallback_error:
        return _apply_tournaments_v2_fallback_metadata(result, error=fallback_error)
    return result


async def tournament_schedule_regenerate_from_rules(
    *,
    tournament_key: str,
    tournament_slug: Optional[str] = None,
    tournament_name: Optional[str] = None,
    category_id: Optional[str] = None,
    start_date: str,
    kickoff_time: str = "09:00",
    games_per_day: int = 4,
    interval_minutes: int = 90,
    field_number: Optional[str] = None,
    field_numbers: Optional[List[str]] = None,
    infinite_fields: bool = False,
    daily_start_time: Optional[str] = None,
    daily_end_time: Optional[str] = None,
    category_windows: Optional[List[Dict[str, Any]]] = None,
    status: str = "scheduled",
    replace_existing: bool = True,
    include_group_stage: bool = True,
    group_phase_name: str = "Fase estatal",
    include_knockout: bool = True,
    knockout_rounds: Optional[List[str]] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """
    Regenerate a tournament schedule from simple rules:
    - Optional group stage (round-robin)
    - Optional knockout rounds (placeholder bracket)
    """
    writes_enabled, fallback_to_legacy = _tournaments_v2_write_flags()
    if writes_enabled:
        try:
            return await schedule_regenerate_from_rules_v2(
                tournament_key=tournament_key,
                tournament_slug=tournament_slug,
                tournament_name=tournament_name,
                category_id=category_id,
                start_date=start_date,
                kickoff_time=kickoff_time,
                games_per_day=games_per_day,
                interval_minutes=interval_minutes,
                field_number=field_number,
                field_numbers=field_numbers,
                infinite_fields=infinite_fields,
                daily_start_time=daily_start_time,
                daily_end_time=daily_end_time,
                category_windows=category_windows,
                status=status,
                replace_existing=replace_existing,
                include_group_stage=include_group_stage,
                group_phase_name=group_phase_name,
                include_knockout=include_knockout,
                knockout_rounds=knockout_rounds,
                dry_run=dry_run,
            )
        except Exception as exc:
            if not fallback_to_legacy:
                raise
            logger.warning(
                "tournaments_v2 schedule regenerate failed; "
                "falling back to legacy writer: %s",
                exc,
            )
    if games_per_day < 1 or games_per_day > 20:
        raise ValueError("games_per_day must be between 1 and 20")
    if interval_minutes < 30 or interval_minutes > 600:
        raise ValueError("interval_minutes must be between 30 and 600")
    if not include_group_stage and not include_knockout:
        raise ValueError(
            "At least one of include_group_stage/include_knockout must be true"
        )

    rounds = knockout_rounds or ["Cuartos", "Semifinal", "Final"]
    rounds = [r.strip() for r in rounds if (r or "").strip()]
    if include_knockout and not rounds:
        raise ValueError("knockout_rounds cannot be empty when include_knockout=true")

    start_d = _parse_date(start_date)
    if not start_d:
        raise ValueError("start_date is required")
    if not (daily_start_time or "").strip() or not (daily_end_time or "").strip():
        raise ValueError(
            "Para regenerar calendario necesito disponibilidad horaria: "
            "daily_start_time y daily_end_time (ej. 08:00 y 18:00)."
        )
    day_start_t = _parse_time(daily_start_time)
    day_end_t = _parse_time(daily_end_time)
    if day_end_t and day_end_t <= day_start_t:
        raise ValueError("daily_end_time must be later than daily_start_time")
    fields = _normalize_field_numbers(
        field_number=field_number, field_numbers=field_numbers
    )
    if infinite_fields:
        fields = [f"INF-{i+1}" for i in range(max(1, games_per_day))]
    elif not (field_number or (field_numbers or [])):
        raise ValueError(
            "Necesito canchas disponibles (field_numbers/field_number) "
            "o indica infinite_fields=true."
        )
    if games_per_day < len(fields):
        raise ValueError("games_per_day must be >= number of fields")
    status_value = (status or "scheduled").strip().lower()
    if status_value not in {
        "scheduled",
        "in_progress",
        "live",
        "finished",
        "completed",
    }:
        raise ValueError(
            "status must be one of: scheduled, in_progress, live, finished, completed"
        )

    slug_hint = (tournament_slug or "").strip()
    name_hint = (tournament_name or "").strip()
    if not slug_hint and not name_hint:
        raise ValueError("Provide tournament_slug or tournament_name")

    # Resolve tournament
    query: Dict[str, str] = {"select": "id,name,slug", "limit": "1"}
    if slug_hint:
        query["slug"] = f"eq.{slug_hint}"
    else:
        query["name"] = f"ilike.*{name_hint}*"
    tournaments = await _supabase_fetch_json(
        method="GET", path="tournaments", query=query
    )
    if not tournaments and name_hint:
        fallback_slug = _slugify(name_hint)
        tournaments = await _supabase_fetch_json(
            method="GET",
            path="tournaments",
            query={
                "select": "id,name,slug",
                "slug": f"eq.{fallback_slug}",
                "limit": "1",
            },
        )
    if not tournaments:
        raise ValueError("Tournament not found in Supabase")
    tournament = tournaments[0]
    tournament_id = str(tournament.get("id"))

    # Resolve category
    cat_id = (category_id or "").strip()
    if not cat_id:
        cats = await _supabase_fetch_json(
            method="GET",
            path="categories",
            query={
                "select": "id,name",
                "tournament_id": f"eq.{tournament_id}",
                "order": "created_at.asc",
                "limit": "1",
            },
        )
        if not cats:
            raise ValueError("No categories found for tournament")
        cat_id = str(cats[0].get("id"))
    else:
        cats = await _supabase_fetch_json(
            method="GET",
            path="categories",
            query={"select": "id,name", "id": f"eq.{cat_id}", "limit": "1"},
        )
    category_name = (cats[0].get("name") if cats else None) or None
    windows = _normalize_windows(
        daily_start_time=day_start_t,
        daily_end_time=day_end_t,
        category_windows=category_windows,
        category_id=cat_id,
        category_name=category_name,
    )

    # Resolve teams
    regs = await _supabase_fetch_json(
        method="GET",
        path="registrations",
        query={"select": "team_id", "category_id": f"eq.{cat_id}", "limit": "1000"},
    )
    reg_team_ids = sorted(
        {
            str((r or {}).get("team_id") or "").strip()
            for r in regs
            if (r or {}).get("team_id")
        }
    )
    team_ids: List[str] = []
    if reg_team_ids:
        for i in range(0, len(reg_team_ids), 200):
            chunk = reg_team_ids[i : i + 200]
            teams = await _supabase_fetch_json(
                method="GET",
                path="teams",
                query={
                    "select": "id,tournament_id,team_name",
                    "id": f"in.({','.join(chunk)})",
                    "tournament_id": f"eq.{tournament_id}",
                },
            )
            team_ids.extend([str(t.get("id")) for t in teams if t.get("id")])
    else:
        teams = await _supabase_fetch_json(
            method="GET",
            path="teams",
            query={
                "select": "id",
                "tournament_id": f"eq.{tournament_id}",
                "order": "created_at.asc",
                "limit": "1000",
            },
        )
        team_ids = [str(t.get("id")) for t in teams if t.get("id")]

    team_ids = list(dict.fromkeys(team_ids))
    if len(team_ids) < 2:
        raise ValueError("Need at least 2 teams to generate schedule")

    # Build rows
    all_rows: List[Dict[str, Any]] = []
    _next_slot = _build_slot_generator(
        start_date=start_d,
        fields=fields,
        games_per_day=games_per_day,
        interval_minutes=interval_minutes,
        windows=windows,
    )

    if include_group_stage:
        pairs = _round_robin_pairings(team_ids)
        for home_id, away_id in pairs:
            dt, field_value = _next_slot()
            all_rows.append(
                {
                    "tournament_id": tournament_id,
                    "category_id": cat_id,
                    "home_team_id": home_id,
                    "away_team_id": away_id,
                    "match_date": dt.isoformat(),
                    "field_number": field_value,
                    "phase": group_phase_name,
                    "status": status_value,
                    "home_score": (
                        0 if status_value in {"finished", "completed"} else None
                    ),
                    "away_score": (
                        0 if status_value in {"finished", "completed"} else None
                    ),
                }
            )

    knockout_rows_count = 0
    if include_knockout:
        bracket_size = 1
        while bracket_size * 2 <= len(team_ids):
            bracket_size *= 2
        if bracket_size < 2:
            bracket_size = 2
        seeded = team_ids[:bracket_size]
        # First knockout round with real team ids.
        first_round = rounds[0]
        seeded_pairs: List[List[Optional[str]]] = []
        for i in range(0, len(seeded), 2):
            if i + 1 < len(seeded):
                seeded_pairs.append([seeded[i], seeded[i + 1]])
        for home_id, away_id in seeded_pairs:
            dt, field_value = _next_slot()
            all_rows.append(
                {
                    "tournament_id": tournament_id,
                    "category_id": cat_id,
                    "home_team_id": home_id,
                    "away_team_id": away_id,
                    "match_date": dt.isoformat(),
                    "field_number": field_value,
                    "phase": first_round,
                    "status": status_value,
                    "home_score": (
                        0 if status_value in {"finished", "completed"} else None
                    ),
                    "away_score": (
                        0 if status_value in {"finished", "completed"} else None
                    ),
                }
            )
            knockout_rows_count += 1

        # Remaining rounds with TBD (null team ids).
        prev_count = len(seeded_pairs)
        for round_name in rounds[1:]:
            this_count = max(1, prev_count // 2)
            for _ in range(this_count):
                dt, field_value = _next_slot()
                all_rows.append(
                    {
                        "tournament_id": tournament_id,
                        "category_id": cat_id,
                        "home_team_id": None,
                        "away_team_id": None,
                        "match_date": dt.isoformat(),
                        "field_number": field_value,
                        "phase": round_name,
                        "status": status_value,
                        "home_score": (
                            0 if status_value in {"finished", "completed"} else None
                        ),
                        "away_score": (
                            0 if status_value in {"finished", "completed"} else None
                        ),
                    }
                )
                knockout_rows_count += 1
            prev_count = this_count

    existing_count = 0
    if replace_existing:
        existing = await _supabase_fetch_json(
            method="GET",
            path="matches",
            query={
                "select": "id",
                "tournament_id": f"eq.{tournament_id}",
                "category_id": f"eq.{cat_id}",
                "limit": "5000",
            },
        )
        existing_count = len(existing or [])

    if dry_run:
        return {
            "created": False,
            "dry_run": True,
            "tournament": {
                "id": tournament_id,
                "name": tournament.get("name"),
                "slug": tournament.get("slug"),
            },
            "category": {"id": cat_id, "name": category_name},
            "replace_existing": replace_existing,
            "existing_matches": existing_count,
            "teams_count": len(team_ids),
            "fields": fields,
            "infinite_fields": bool(infinite_fields),
            "daily_start_time": day_start_t.strftime("%H:%M"),
            "daily_end_time": day_end_t.strftime("%H:%M") if day_end_t else None,
            "category_windows_applied": [
                {
                    "start_time": s.strftime("%H:%M"),
                    "end_time": e.strftime("%H:%M") if e else None,
                }
                for s, e in windows
            ],
            "rules": {
                "group_stage": include_group_stage,
                "group_phase_name": group_phase_name,
                "knockout": include_knockout,
                "knockout_rounds": rounds,
            },
            "matches_planned": len(all_rows),
            "knockout_matches_planned": knockout_rows_count,
            "sample_matches": all_rows[: min(8, len(all_rows))],
        }

    if replace_existing:
        await _supabase_fetch_json(
            method="DELETE",
            path="matches",
            query={
                "tournament_id": f"eq.{tournament_id}",
                "category_id": f"eq.{cat_id}",
            },
        )

    inserted = await _supabase_fetch_json(
        method="POST", path="matches", payload=all_rows
    )
    inserted_ids = [str(r.get("id")) for r in (inserted or []) if r.get("id")]
    return {
        "created": True,
        "dry_run": False,
        "tournament": {
            "id": tournament_id,
            "name": tournament.get("name"),
            "slug": tournament.get("slug"),
        },
        "category": {"id": cat_id, "name": category_name},
        "replace_existing": replace_existing,
        "existing_matches_replaced": existing_count if replace_existing else 0,
        "teams_count": len(team_ids),
        "fields": fields,
        "infinite_fields": bool(infinite_fields),
        "daily_start_time": day_start_t.strftime("%H:%M"),
        "daily_end_time": day_end_t.strftime("%H:%M") if day_end_t else None,
        "category_windows_applied": [
            {
                "start_time": s.strftime("%H:%M"),
                "end_time": e.strftime("%H:%M") if e else None,
            }
            for s, e in windows
        ],
        "rules": {
            "group_stage": include_group_stage,
            "group_phase_name": group_phase_name,
            "knockout": include_knockout,
            "knockout_rounds": rounds,
        },
        "matches_created": len(inserted_ids),
        "knockout_matches_created": knockout_rows_count,
        "match_ids": inserted_ids[:100],
    }


async def finance_vendor_create(
    session: AsyncSession,
    *,
    nombre: str,
    rfc: Optional[str] = None,
    banco: Optional[str] = None,
    cuenta_clabe: Optional[str] = None,
    cuenta_bancaria: Optional[str] = None,
) -> Dict[str, Any]:
    nombre = (nombre or "").strip()
    if not nombre:
        raise ValueError("nombre is required")

    vendor = ProveedorCliente(
        tipo="proveedor",
        nombre=nombre,
        rfc=(rfc or None),
        banco=(banco or None),
        cuenta_clabe=(cuenta_clabe or None),
        cuenta_bancaria=(cuenta_bancaria or None),
        activo=True,
    )
    session.add(vendor)
    await session.commit()
    await session.refresh(vendor)
    return {"proveedor_id": str(vendor.id), "nombre": vendor.nombre, "rfc": vendor.rfc}


def _parse_datetime(value: Optional[str]) -> Optional[datetime]:
    d = _parse_date(value)
    if not d:
        return None
    return datetime.combine(d, time(hour=12, minute=0))


def _normalize_amount(value: Any) -> float:
    try:
        amount = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("gasto_cantidad must be a valid number") from exc
    if amount <= 0:
        raise ValueError("gasto_cantidad must be greater than 0")
    return round(amount, 2)


def _build_reference(prefix: str = "ASST") -> str:
    ts = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    return f"{prefix}-{ts}-{uuid.uuid4().hex[:6].upper()}"


async def finance_expense_search(
    session: AsyncSession,
    *,
    query: Optional[str] = None,
    proyecto: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 20,
) -> Dict[str, Any]:
    """Search expenses for conversational queries."""
    df = _parse_date(date_from)
    dt = _parse_date(date_to)
    q = (query or "").strip()
    p = (proyecto or "").strip()

    stmt = select(ExpenseReport).where(ExpenseReport.estado_gasto != "cancelado")
    if q:
        like_q = f"%{q}%"
        stmt = stmt.where(
            or_(
                ExpenseReport.concepto.ilike(like_q),
                ExpenseReport.proyecto.ilike(like_q),
                ExpenseReport.nombre_enviador.ilike(like_q),
                ExpenseReport.numero_referencia.ilike(like_q),
            )
        )
    if p:
        stmt = stmt.where(ExpenseReport.proyecto.ilike(f"%{p}%"))
    if df:
        stmt = stmt.where(func.date(ExpenseReport.fecha) >= df)
    if dt:
        stmt = stmt.where(func.date(ExpenseReport.fecha) <= dt)
    stmt = stmt.order_by(ExpenseReport.fecha.desc()).limit(max(1, min(limit, 100)))

    rows = (await session.execute(stmt)).scalars().all()
    items: List[Dict[str, Any]] = []
    total = 0.0
    for exp in rows:
        amount = float(exp.gasto_cantidad or 0)
        total += amount
        items.append(
            {
                "expense_id": str(exp.id),
                "fecha": exp.fecha.isoformat() if exp.fecha else None,
                "proyecto": exp.proyecto,
                "concepto": exp.concepto,
                "monto": round(amount, 2),
                "metodo_pago": exp.metodo_pago,
                "estado_reembolso": exp.estado_reembolso,
                "numero_referencia": exp.numero_referencia,
                "nombre_enviador": exp.nombre_enviador,
            }
        )

    return {
        "query": q or None,
        "proyecto": p or None,
        "date_from": df.isoformat() if df else None,
        "date_to": dt.isoformat() if dt else None,
        "total_registros": len(items),
        "monto_total": round(total, 2),
        "moneda": "MXN",
        "gastos": items,
    }


def _normalize_finance_ops_filters(
    *,
    question: Optional[str],
    query: str,
    filters: Dict[str, str],
) -> Tuple[str, Dict[str, str]]:
    normalized_filters = {k: str(v or "").strip() for k, v in dict(filters).items()}
    q = str(query or "").strip()
    proyecto = normalized_filters.get("proyecto", "")
    concepto = normalized_filters.get("concepto", "")

    # If the model mirrors the same keyword into query and proyecto, treat it as
    # a concept/item lookup instead of a project filter. This avoids false zeroes
    # for questions like "¿cuánto hemos gastado en balones?".
    if q and proyecto and not concepto and proyecto.lower() == q.lower():
        normalized_filters["proyecto"] = ""
        normalized_filters["concepto"] = q
        return q, normalized_filters

    question_text = str(question or "").strip().lower()
    item_hint_keywords = {
        "balones",
        "uniformes",
        "viatico",
        "viáticos",
        "viaticos",
        "utileria",
        "utilería",
        "arbitraje",
        "hospedaje",
        "transporte",
        "comidas",
    }
    if (
        proyecto
        and not concepto
        and proyecto.lower() in item_hint_keywords
        and any(
            marker in question_text
            for marker in ("gastado en ", "gasto en ", "pagado en ", "invertido en ")
        )
    ):
        normalized_filters["proyecto"] = ""
        normalized_filters["concepto"] = proyecto

    return q, normalized_filters


async def finance_ops_query(
    session: AsyncSession,
    *,
    question: Optional[str] = None,
    query: Optional[str] = None,
    proyecto: Optional[str] = None,
    concepto: Optional[str] = None,
    departamento: Optional[str] = None,
    fase_torneo: Optional[str] = None,
    metodo_pago: Optional[str] = None,
    proveedor_nombre: Optional[str] = None,
    tipo_documento: Optional[str] = None,
    estado_documento: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 50,
) -> Dict[str, Any]:
    """
    Universal read-only finance/accounting query.

    Returns expenses + accounting documents with totals and breakdowns so the LLM
    can answer broad financial questions without a dedicated tool per intent.
    """
    df = _parse_date(date_from)
    dt = _parse_date(date_to)
    max_limit = max(1, min(int(limit or 50), 200))

    q = (query or "").strip()
    filters = {
        "proyecto": (proyecto or "").strip(),
        "concepto": (concepto or "").strip(),
        "departamento": (departamento or "").strip(),
        "fase_torneo": (fase_torneo or "").strip(),
        "metodo_pago": (metodo_pago or "").strip(),
        "proveedor_nombre": (proveedor_nombre or "").strip(),
        "tipo_documento": (tipo_documento or "").strip(),
        "estado_documento": (estado_documento or "").strip(),
    }
    q, filters = _normalize_finance_ops_filters(
        question=question,
        query=q,
        filters=filters,
    )

    expense_filter = [ExpenseReport.estado_gasto != "cancelado"]
    if q:
        like_q = f"%{q}%"
        expense_filter.append(
            or_(
                ExpenseReport.concepto.ilike(like_q),
                ExpenseReport.proyecto.ilike(like_q),
                ExpenseReport.nombre_enviador.ilike(like_q),
                ExpenseReport.numero_referencia.ilike(like_q),
                ExpenseReport.departamento.ilike(like_q),
                ExpenseReport.fase_torneo.ilike(like_q),
            )
        )
    if filters["proyecto"]:
        expense_filter.append(ExpenseReport.proyecto.ilike(f"%{filters['proyecto']}%"))
    if filters["concepto"]:
        expense_filter.append(ExpenseReport.concepto.ilike(f"%{filters['concepto']}%"))
    if filters["departamento"]:
        expense_filter.append(
            ExpenseReport.departamento.ilike(f"%{filters['departamento']}%")
        )
    if filters["fase_torneo"]:
        expense_filter.append(
            ExpenseReport.fase_torneo.ilike(f"%{filters['fase_torneo']}%")
        )
    if filters["metodo_pago"]:
        expense_filter.append(
            ExpenseReport.metodo_pago.ilike(f"%{filters['metodo_pago']}%")
        )
    if df:
        expense_filter.append(func.date(ExpenseReport.fecha) >= df)
    if dt:
        expense_filter.append(func.date(ExpenseReport.fecha) <= dt)

    expense_total_row = (
        await session.execute(
            select(
                func.count(ExpenseReport.id).label("total"),
                func.coalesce(func.sum(ExpenseReport.gasto_cantidad), 0).label("monto"),
            ).where(*expense_filter)
        )
    ).one()
    expense_total_count = int(expense_total_row.total or 0)
    expense_total_amount = float(expense_total_row.monto or 0)

    expense_rows = (
        await session.execute(
            select(
                ExpenseReport.id,
                ExpenseReport.fecha,
                ExpenseReport.proyecto,
                ExpenseReport.concepto,
                ExpenseReport.gasto_cantidad,
                ExpenseReport.numero_referencia,
                ExpenseReport.departamento,
                ExpenseReport.fase_torneo,
                ExpenseReport.metodo_pago,
                ExpenseReport.nombre_enviador,
                ExpenseReport.estado_reembolso,
                ExpenseReport.estado_factura,
            )
            .where(*expense_filter)
            .order_by(ExpenseReport.fecha.desc())
            .limit(max_limit)
        )
    ).all()
    expenses = [
        {
            "expense_id": str(r.id),
            "fecha": r.fecha.isoformat() if r.fecha else None,
            "proyecto": r.proyecto,
            "concepto": r.concepto,
            "monto": float(r.gasto_cantidad or 0),
            "numero_referencia": r.numero_referencia,
            "departamento": r.departamento,
            "fase_torneo": r.fase_torneo,
            "metodo_pago": r.metodo_pago,
            "nombre_enviador": r.nombre_enviador,
            "estado_reembolso": r.estado_reembolso,
            "estado_factura": r.estado_factura,
        }
        for r in expense_rows
    ]

    # SQLAlchemy async: run each aggregation explicitly.
    by_project_rows = (
        await session.execute(
            select(
                ExpenseReport.proyecto.label("k"),
                func.count(ExpenseReport.id).label("n"),
                func.coalesce(func.sum(ExpenseReport.gasto_cantidad), 0).label("m"),
            )
            .where(*expense_filter)
            .group_by(ExpenseReport.proyecto)
            .order_by(func.coalesce(func.sum(ExpenseReport.gasto_cantidad), 0).desc())
            .limit(max_limit)
        )
    ).all()
    by_concept_rows = (
        await session.execute(
            select(
                ExpenseReport.concepto.label("k"),
                func.count(ExpenseReport.id).label("n"),
                func.coalesce(func.sum(ExpenseReport.gasto_cantidad), 0).label("m"),
            )
            .where(*expense_filter)
            .group_by(ExpenseReport.concepto)
            .order_by(func.coalesce(func.sum(ExpenseReport.gasto_cantidad), 0).desc())
            .limit(max_limit)
        )
    ).all()
    by_department_rows = (
        await session.execute(
            select(
                ExpenseReport.departamento.label("k"),
                func.count(ExpenseReport.id).label("n"),
                func.coalesce(func.sum(ExpenseReport.gasto_cantidad), 0).label("m"),
            )
            .where(*expense_filter)
            .group_by(ExpenseReport.departamento)
            .order_by(func.coalesce(func.sum(ExpenseReport.gasto_cantidad), 0).desc())
            .limit(max_limit)
        )
    ).all()
    by_phase_rows = (
        await session.execute(
            select(
                ExpenseReport.fase_torneo.label("k"),
                func.count(ExpenseReport.id).label("n"),
                func.coalesce(func.sum(ExpenseReport.gasto_cantidad), 0).label("m"),
            )
            .where(*expense_filter)
            .group_by(ExpenseReport.fase_torneo)
            .order_by(func.coalesce(func.sum(ExpenseReport.gasto_cantidad), 0).desc())
            .limit(max_limit)
        )
    ).all()
    by_method_rows = (
        await session.execute(
            select(
                ExpenseReport.metodo_pago.label("k"),
                func.count(ExpenseReport.id).label("n"),
                func.coalesce(func.sum(ExpenseReport.gasto_cantidad), 0).label("m"),
            )
            .where(*expense_filter)
            .group_by(ExpenseReport.metodo_pago)
            .order_by(func.coalesce(func.sum(ExpenseReport.gasto_cantidad), 0).desc())
            .limit(max_limit)
        )
    ).all()

    def _fmt_breakdown(rows, key_name: str) -> List[Dict[str, Any]]:
        return [
            {
                key_name: r.k or f"(sin {key_name})",
                "registros": int(r.n or 0),
                "monto": round(float(r.m or 0), 2),
            }
            for r in rows
        ]

    # Documents/accounting side.
    doc_filter = []
    if filters["proveedor_nombre"]:
        doc_filter.append(
            ProveedorCliente.nombre.ilike(f"%{filters['proveedor_nombre']}%")
        )
    if filters["tipo_documento"]:
        doc_filter.append(Documento.tipo.ilike(f"%{filters['tipo_documento']}%"))
    if filters["estado_documento"]:
        doc_filter.append(Documento.estado.ilike(f"%{filters['estado_documento']}%"))
    if q:
        like_q = f"%{q}%"
        doc_filter.append(
            or_(
                Documento.numero_referencia.ilike(like_q),
                Documento.notas.ilike(like_q),
                Documento.concepto_pago.ilike(like_q),
                ProveedorCliente.nombre.ilike(like_q),
            )
        )
    if df:
        doc_filter.append(
            or_(
                Documento.fecha_pago >= df,
                func.date(Documento.creado_en) >= df,
            )
        )
    if dt:
        doc_filter.append(
            or_(
                Documento.fecha_pago <= dt,
                func.date(Documento.creado_en) <= dt,
            )
        )

    doc_total_row = (
        await session.execute(
            select(
                func.count(Documento.id).label("total"),
                func.coalesce(func.sum(Documento.monto_total), 0).label("monto"),
            )
            .select_from(Documento)
            .outerjoin(
                ProveedorCliente, Documento.proveedor_cliente_id == ProveedorCliente.id
            )
            .where(*doc_filter)
        )
    ).one()
    doc_total_count = int(doc_total_row.total or 0)
    doc_total_amount = float(doc_total_row.monto or 0)

    doc_rows = (
        await session.execute(
            select(
                Documento.id,
                Documento.numero_referencia,
                Documento.tipo,
                Documento.estado,
                Documento.monto_total,
                Documento.monto_solicitado,
                Documento.fecha_pago,
                Documento.creado_en,
                ProveedorCliente.nombre.label("proveedor"),
            )
            .select_from(Documento)
            .outerjoin(
                ProveedorCliente, Documento.proveedor_cliente_id == ProveedorCliente.id
            )
            .where(*doc_filter)
            .order_by(Documento.creado_en.desc())
            .limit(max_limit)
        )
    ).all()
    documents = [
        {
            "documento_id": str(r.id),
            "numero_referencia": r.numero_referencia,
            "tipo": r.tipo,
            "estado": r.estado,
            "monto_total": round(float(r.monto_total or r.monto_solicitado or 0), 2),
            "fecha_pago": r.fecha_pago.isoformat() if r.fecha_pago else None,
            "creado_en": r.creado_en.isoformat() if r.creado_en else None,
            "proveedor": r.proveedor,
        }
        for r in doc_rows
    ]

    docs_by_vendor_rows = (
        await session.execute(
            select(
                ProveedorCliente.nombre.label("k"),
                func.count(Documento.id).label("n"),
                func.coalesce(func.sum(Documento.monto_total), 0).label("m"),
            )
            .select_from(Documento)
            .outerjoin(
                ProveedorCliente, Documento.proveedor_cliente_id == ProveedorCliente.id
            )
            .where(*doc_filter)
            .group_by(ProveedorCliente.nombre)
            .order_by(func.coalesce(func.sum(Documento.monto_total), 0).desc())
            .limit(max_limit)
        )
    ).all()
    docs_by_type_rows = (
        await session.execute(
            select(
                Documento.tipo.label("k"),
                func.count(Documento.id).label("n"),
                func.coalesce(func.sum(Documento.monto_total), 0).label("m"),
            )
            .select_from(Documento)
            .outerjoin(
                ProveedorCliente, Documento.proveedor_cliente_id == ProveedorCliente.id
            )
            .where(*doc_filter)
            .group_by(Documento.tipo)
            .order_by(func.coalesce(func.sum(Documento.monto_total), 0).desc())
            .limit(max_limit)
        )
    ).all()
    docs_by_status_rows = (
        await session.execute(
            select(
                Documento.estado.label("k"),
                func.count(Documento.id).label("n"),
                func.coalesce(func.sum(Documento.monto_total), 0).label("m"),
            )
            .select_from(Documento)
            .outerjoin(
                ProveedorCliente, Documento.proveedor_cliente_id == ProveedorCliente.id
            )
            .where(*doc_filter)
            .group_by(Documento.estado)
            .order_by(func.coalesce(func.sum(Documento.monto_total), 0).desc())
            .limit(max_limit)
        )
    ).all()

    return {
        "question": (question or "").strip() or None,
        "query": q or None,
        "filters": {
            **{k: (v or None) for k, v in filters.items()},
            "date_from": df.isoformat() if df else None,
            "date_to": dt.isoformat() if dt else None,
        },
        "expenses": {
            "totals": {
                "registros": expense_total_count,
                "monto_total": round(expense_total_amount, 2),
                "moneda": "MXN",
            },
            "breakdowns": {
                "por_proyecto": _fmt_breakdown(by_project_rows, "proyecto"),
                "por_concepto": _fmt_breakdown(by_concept_rows, "concepto"),
                "por_departamento": _fmt_breakdown(by_department_rows, "departamento"),
                "por_fase_torneo": _fmt_breakdown(by_phase_rows, "fase_torneo"),
                "por_metodo_pago": _fmt_breakdown(by_method_rows, "metodo_pago"),
            },
            "items": expenses,
        },
        "documents": {
            "totals": {
                "registros": doc_total_count,
                "monto_total": round(doc_total_amount, 2),
                "moneda": "MXN",
            },
            "breakdowns": {
                "por_proveedor": _fmt_breakdown(docs_by_vendor_rows, "proveedor"),
                "por_tipo": _fmt_breakdown(docs_by_type_rows, "tipo"),
                "por_estado": _fmt_breakdown(docs_by_status_rows, "estado"),
            },
            "items": documents,
        },
        "limit": max_limit,
        "nota": (
            "Consulta universal financiera/contable sobre tablas expense_reports, "
            "documentos y proveedores."
        ),
    }


def _shift_year_safe(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:
        # Handle Feb 29 -> Feb 28
        return d.replace(month=2, day=28, year=d.year + years)


def _scope_like_terms(scope_value: Optional[str]) -> List[str]:
    scope = (scope_value or "").strip().lower()
    if not scope or scope == "all":
        return []
    if scope in {"beisbol", "béisbol", "baseball"}:
        return ["beisbol", "béisbol", "beis", "liga telmex telcel de beisbol"]
    return []


def _finance_strategy_focus_areas(question: Optional[str]) -> List[str]:
    text = re.sub(r"\s+", " ", (question or "").strip().lower())
    focus: List[str] = []
    if any(
        token in text
        for token in (
            "flujo",
            "tesoreria",
            "tesorería",
            "liquidez",
            "capital de trabajo",
            "cash flow",
        )
    ):
        focus.append("cash_flow")
    if any(
        token in text
        for token in (
            "fiscal",
            "impuesto",
            "impuestos",
            "iva",
            "retencion",
            "retención",
            "retenciones",
        )
    ):
        focus.append("tax")
    if any(
        token in text
        for token in (
            "contable",
            "contabilidad",
            "balanza",
            "poliza",
            "póliza",
            "cierre",
            "mayor",
            "diario",
        )
    ):
        focus.append("accounting")
    if any(
        token in text
        for token in (
            "presupuesto",
            "variacion",
            "variación",
            "proyeccion",
            "proyección",
            "escenario",
            "rentabilidad",
            "margen",
        )
    ):
        focus.append("planning")
    return focus or ["general"]


def _finance_signal_severity(
    value: Optional[float],
    *,
    high: float,
    medium: float,
) -> str:
    if value is None:
        return "unknown"
    magnitude = abs(float(value))
    if magnitude >= high:
        return "high"
    if magnitude >= medium:
        return "medium"
    return "low"


def _finance_concentration_signal(
    items: List[Dict[str, Any]],
    *,
    key_name: str,
    total_amount: float,
) -> Dict[str, Any]:
    if not items or total_amount <= 0:
        return {
            "top_item": None,
            "top_amount": 0.0,
            "top_share_pct": 0.0,
            "severity": "low",
        }
    first = items[0] or {}
    amount = round(float(first.get("monto") or 0), 2)
    share = round((amount / float(total_amount)) * 100, 2) if total_amount else 0.0
    severity = "high" if share >= 45 else "medium" if share >= 30 else "low"
    return {
        "top_item": first.get(key_name),
        "top_amount": amount,
        "top_share_pct": share,
        "severity": severity,
    }


def _finance_money(value: Any) -> float:
    try:
        return round(float(value or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _finance_normalize_text(value: Any) -> str:
    text = str(value or "").strip().lower()
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def _finance_poliza_line_totals(
    polizas: List[AccountingPoliza],
) -> Tuple[float, float, bool]:
    debit_total = 0.0
    credit_total = 0.0
    has_retention_line = False
    for poliza in polizas:
        for line in list(getattr(poliza, "lines", None) or []):
            debit_total += _finance_money(getattr(line, "debe", None))
            credit_total += _finance_money(getattr(line, "haber", None))
            line_text = " ".join(
                [
                    _finance_normalize_text(getattr(line, "concepto", None)),
                    _finance_normalize_text(getattr(line, "cuenta_codigo", None)),
                    _finance_normalize_text(
                        getattr(getattr(line, "cuenta_contable", None), "tipo", None)
                    ),
                ]
            )
            if any(token in line_text for token in ("retencion", "retenido")):
                has_retention_line = True
    return round(debit_total, 2), round(credit_total, 2), has_retention_line


def _finance_tax_observation_for_expense(
    expense: ExpenseReport,
    polizas: List[AccountingPoliza],
    *,
    question: Optional[str] = None,
) -> Dict[str, Any]:
    cfdi_report = getattr(expense, "cfdi_report", None)
    taxes = summarize_cfdi_tax_components(
        cfdi_report,
        fallback_iva=getattr(expense, "iva", None),
    )
    total_amount = _finance_money(
        getattr(expense, "gasto_cantidad", None) or getattr(cfdi_report, "total", None)
    )
    gross_before_iva = _finance_money(
        total_amount - taxes["iva_trasladado"] + taxes["retenciones_total"]
    )
    local_tax = resolve_hospedaje_local_tax(
        expense,
        cfdi_report=cfdi_report,
        iva_amount=taxes["iva_trasladado"],
        retenciones_total=taxes["retenciones_total"],
    )
    lodging_related = bool(local_tax.get("lodging_related"))
    inferred_state = local_tax.get("entity")
    lodging_rate = local_tax.get("rate")
    estimated_lodging_tax = _finance_money(local_tax.get("amount"))
    estimated_lodging_base = (
        _finance_money(gross_before_iva - estimated_lodging_tax)
        if estimated_lodging_tax > 0
        else 0.0
    )

    poliza_debe_total, poliza_haber_total, has_retention_line = (
        _finance_poliza_line_totals(polizas)
    )
    poliza_gap_amount = _finance_money(max(0.0, poliza_haber_total - poliza_debe_total))
    lodging_tax_gap_amount = 0.0
    if (
        lodging_related
        and polizas
        and poliza_gap_amount > 0
        and estimated_lodging_tax > 0
    ):
        tolerance = max(10.0, round(estimated_lodging_tax * 0.2, 2))
        if abs(poliza_gap_amount - estimated_lodging_tax) <= tolerance:
            lodging_tax_gap_amount = poliza_gap_amount

    retention_gap_amount = 0.0
    if polizas and taxes["retenciones_total"] > 0 and not has_retention_line:
        retention_gap_amount = _finance_money(taxes["retenciones_total"])

    return {
        "expense_id": str(getattr(expense, "id", "")) or None,
        "numero_referencia": getattr(expense, "numero_referencia", None),
        "concepto": getattr(expense, "concepto", None),
        "fecha": (
            getattr(expense, "fecha", None).isoformat()
            if getattr(expense, "fecha", None)
            else None
        ),
        "emisor_nombre": getattr(cfdi_report, "emisor_nombre", None),
        "cfdi_uuid": getattr(cfdi_report, "cfdi_uuid", None),
        "cfdi_total": _finance_money(getattr(cfdi_report, "total", None))
        or total_amount,
        "gross_before_iva": gross_before_iva,
        "iva_trasladado": _finance_money(taxes["iva_trasladado"]),
        "retenciones_total": _finance_money(taxes["retenciones_total"]),
        "lodging_related": lodging_related,
        "lodging_state": inferred_state,
        "lodging_rate": lodging_rate,
        "estimated_lodging_base": estimated_lodging_base,
        "estimated_lodging_tax": estimated_lodging_tax,
        "poliza_found": bool(polizas),
        "poliza_debe_total": poliza_debe_total,
        "poliza_haber_total": poliza_haber_total,
        "poliza_gap_amount": poliza_gap_amount,
        "lodging_tax_gap_amount": lodging_tax_gap_amount,
        "retention_gap_amount": retention_gap_amount,
    }


async def _finance_strategy_tax_snapshot(
    session: AsyncSession,
    *,
    question: Optional[str],
    df: date,
    dt: date,
    proyecto: Optional[str] = None,
    concepto: Optional[str] = None,
    departamento: Optional[str] = None,
    fase_torneo: Optional[str] = None,
    metodo_pago: Optional[str] = None,
    proveedor_nombre: Optional[str] = None,
    bi_scope: Optional[str] = None,
) -> Dict[str, Any]:
    expense_filter = [
        ExpenseReport.estado_gasto != "cancelado",
        func.date(ExpenseReport.fecha) >= df,
        func.date(ExpenseReport.fecha) <= dt,
    ]
    if proyecto:
        expense_filter.append(ExpenseReport.proyecto.ilike(f"%{proyecto.strip()}%"))
    if concepto:
        expense_filter.append(ExpenseReport.concepto.ilike(f"%{concepto.strip()}%"))
    if departamento:
        expense_filter.append(
            ExpenseReport.departamento.ilike(f"%{departamento.strip()}%")
        )
    if fase_torneo:
        expense_filter.append(
            ExpenseReport.fase_torneo.ilike(f"%{fase_torneo.strip()}%")
        )
    if metodo_pago:
        expense_filter.append(
            ExpenseReport.metodo_pago.ilike(f"%{metodo_pago.strip()}%")
        )
    if proveedor_nombre:
        like_supplier = f"%{proveedor_nombre.strip()}%"
        expense_filter.append(
            ExpenseReport.cfdi_report.has(CFDIReport.emisor_nombre.ilike(like_supplier))
        )
    scope_terms = _scope_like_terms(bi_scope)
    if scope_terms:
        scope_expr = []
        for term in scope_terms:
            like = f"%{term}%"
            scope_expr.extend(
                [
                    ExpenseReport.proyecto.ilike(like),
                    ExpenseReport.concepto.ilike(like),
                    ExpenseReport.departamento.ilike(like),
                    ExpenseReport.fase_torneo.ilike(like),
                ]
            )
        expense_filter.append(or_(*scope_expr))

    expenses = (
        (
            await session.execute(
                select(ExpenseReport)
                .options(selectinload(ExpenseReport.cfdi_report))
                .where(*expense_filter)
                .order_by(ExpenseReport.fecha.desc())
            )
        )
        .scalars()
        .all()
    )

    cfdi_ids = {
        expense.cfdi_report_id for expense in expenses if expense.cfdi_report_id
    }
    source_files = {
        f"assistant_expense:{expense.id}"
        for expense in expenses
        if getattr(expense, "id", None)
    }
    poliza_lookup: Dict[str, AccountingPoliza] = {}
    polizas_by_cfdi: Dict[str, List[AccountingPoliza]] = {}
    polizas_by_source: Dict[str, List[AccountingPoliza]] = {}
    if cfdi_ids or source_files:
        poliza_filter = []
        if cfdi_ids:
            poliza_filter.append(AccountingPoliza.cfdi_report_id.in_(list(cfdi_ids)))
        if source_files:
            poliza_filter.append(AccountingPoliza.source_file.in_(list(source_files)))
        polizas = (
            (
                await session.execute(
                    select(AccountingPoliza)
                    .options(
                        selectinload(AccountingPoliza.lines).selectinload(
                            AccountingPolizaLine.cuenta_contable
                        )
                    )
                    .where(or_(*poliza_filter))
                )
            )
            .scalars()
            .unique()
            .all()
        )
        for poliza in polizas:
            poliza_lookup[str(poliza.id)] = poliza
            if poliza.cfdi_report_id:
                polizas_by_cfdi.setdefault(str(poliza.cfdi_report_id), []).append(
                    poliza
                )
            if poliza.source_file:
                polizas_by_source.setdefault(str(poliza.source_file), []).append(poliza)

    total_iva = 0.0
    total_retenciones = 0.0
    estimated_lodging_tax_total = 0.0
    lodging_tax_gap_total = 0.0
    retention_gap_total = 0.0
    lodging_imbalance_total = 0.0
    expenses_with_tax_evidence = 0
    expenses_with_poliza = 0
    expenses_with_lodging = 0
    unposted_tax_cases = 0
    observations: List[Dict[str, Any]] = []

    for expense in expenses:
        matched_polizas: List[AccountingPoliza] = []
        if expense.cfdi_report_id:
            matched_polizas.extend(polizas_by_cfdi.get(str(expense.cfdi_report_id), []))
        matched_polizas.extend(
            polizas_by_source.get(f"assistant_expense:{expense.id}", [])
        )
        deduped_polizas = list(
            {
                str(poliza.id): poliza
                for poliza in matched_polizas
                if str(poliza.id) in poliza_lookup
            }.values()
        )
        observation = _finance_tax_observation_for_expense(
            expense,
            deduped_polizas,
            question=question,
        )
        total_iva += float(observation["iva_trasladado"] or 0.0)
        total_retenciones += float(observation["retenciones_total"] or 0.0)
        estimated_lodging_tax_total += float(
            observation["estimated_lodging_tax"] or 0.0
        )
        lodging_tax_gap_total += float(observation["lodging_tax_gap_amount"] or 0.0)
        retention_gap_total += float(observation["retention_gap_amount"] or 0.0)
        if observation["lodging_related"]:
            expenses_with_lodging += 1
            if observation["poliza_gap_amount"] > 0:
                lodging_imbalance_total += float(
                    observation["poliza_gap_amount"] or 0.0
                )
        if (
            observation["iva_trasladado"] > 0
            or observation["retenciones_total"] > 0
            or observation["lodging_related"]
        ):
            expenses_with_tax_evidence += 1
            if not observation["poliza_found"]:
                unposted_tax_cases += 1
        if observation["poliza_found"]:
            expenses_with_poliza += 1
        if (
            observation["lodging_tax_gap_amount"] > 0
            or observation["retention_gap_amount"] > 0
            or (
                observation["lodging_related"]
                and observation["poliza_gap_amount"] > 0
                and observation["lodging_tax_gap_amount"] <= 0
            )
        ):
            observations.append(observation)

    observations.sort(
        key=lambda item: (
            -float(item.get("lodging_tax_gap_amount") or 0.0),
            -float(item.get("retention_gap_amount") or 0.0),
            -float(item.get("poliza_gap_amount") or 0.0),
        )
    )

    return {
        "summary": {
            "expenses_analyzed": len(expenses),
            "expenses_with_tax_evidence": expenses_with_tax_evidence,
            "expenses_with_poliza": expenses_with_poliza,
            "expenses_with_lodging": expenses_with_lodging,
            "unposted_tax_cases": unposted_tax_cases,
            "iva_trasladado_total": _finance_money(total_iva),
            "retenciones_total": _finance_money(total_retenciones),
            "estimated_lodging_tax_total": _finance_money(estimated_lodging_tax_total),
            "lodging_tax_gap_total": _finance_money(lodging_tax_gap_total),
            "lodging_imbalance_total": _finance_money(lodging_imbalance_total),
            "retention_gap_total": _finance_money(retention_gap_total),
        },
        "observations": observations[:8],
        "notes": [
            "IVA y retenciones salen de CFDI/impuestos_detalle cuando existe CFDI ligado.",
            "Impuesto sobre hospedaje no se asume fijo globalmente; se intenta inferir por entidad cuando el texto lo permite.",
            "Si la entidad no se puede inferir, una póliza descuadrada de hospedaje se marca como revisión fiscal, no como tasa confirmada.",
        ],
    }


def _finance_strategy_markdown(
    *,
    title: str,
    realtime_report: Dict[str, Any],
    accounting_month: Dict[str, Any],
    balanza_report: Dict[str, Any],
    alerts_report: Dict[str, Any],
    strategy_signals: Dict[str, Any],
    tax_snapshot: Dict[str, Any],
) -> str:
    totals = realtime_report.get("totals") or {}
    budget = realtime_report.get("budget") or {}
    projection = realtime_report.get("projection") or {}
    accounting_summary = accounting_month.get("summary") or {}
    alert_summary = alerts_report.get("summary") or {}
    tax_summary = tax_snapshot.get("summary") or {}
    risks = strategy_signals.get("detected_risks") or []
    watchlist = strategy_signals.get("watchlist") or []
    tax_observations = tax_snapshot.get("observations") or []

    risk_lines = [
        f"- [{str(item.get('severity') or 'low').upper()}] {item.get('type')}: {item.get('detail')}"
        for item in risks[:8]
    ] or ["- Sin riesgos estructurados relevantes en este corte."]
    watch_lines = [f"- {item}" for item in watchlist[:8]] or [
        "- Sin pendientes relevantes en watchlist."
    ]
    tax_lines = [
        f"- IVA identificado en CFDI: {_format_currency(float(tax_summary.get('iva_trasladado_total') or 0))}",
        f"- Retenciones identificadas: {_format_currency(float(tax_summary.get('retenciones_total') or 0))}",
        f"- Casos fiscales sin póliza ligada: {int(tax_summary.get('unposted_tax_cases') or 0)}",
    ]
    if float(tax_summary.get("lodging_tax_gap_total") or 0) > 0:
        tax_lines.append(
            "- Brecha probable por impuesto sobre hospedaje no contabilizado: "
            f"{_format_currency(float(tax_summary.get('lodging_tax_gap_total') or 0))}"
        )
    elif float(tax_summary.get("lodging_imbalance_total") or 0) > 0:
        tax_lines.append(
            "- Pólizas de hospedaje con descuadre a revisar: "
            f"{_format_currency(float(tax_summary.get('lodging_imbalance_total') or 0))}"
        )
    if float(tax_summary.get("retention_gap_total") or 0) > 0:
        tax_lines.append(
            "- Retenciones detectadas sin línea contable explícita: "
            f"{_format_currency(float(tax_summary.get('retention_gap_total') or 0))}"
        )
    for item in tax_observations[:3]:
        label = (
            item.get("numero_referencia")
            or item.get("cfdi_uuid")
            or item.get("concepto")
            or "sin referencia"
        )
        if float(item.get("lodging_tax_gap_amount") or 0) > 0:
            tax_lines.append(
                f"- {label}: brecha de hospedaje { _format_currency(float(item.get('lodging_tax_gap_amount') or 0)) }"
            )
        elif float(item.get("retention_gap_amount") or 0) > 0:
            tax_lines.append(
                f"- {label}: retenciones sin línea por { _format_currency(float(item.get('retention_gap_amount') or 0)) }"
            )
        elif float(item.get("poliza_gap_amount") or 0) > 0:
            tax_lines.append(
                f"- {label}: hospedaje con póliza descuadrada por { _format_currency(float(item.get('poliza_gap_amount') or 0)) }"
            )

    return "\n".join(
        [
            f"# {title}",
            "",
            "## Snapshot ejecutivo",
            f"- Gasto total del periodo: {_format_currency(float(totals.get('gasto_total') or 0))}",
            f"- Registros del periodo: {int(totals.get('registros') or 0)}",
            (
                f"- Presupuesto vs real: {_format_currency(float(budget.get('budget_total') or 0))} "
                f"vs {_format_currency(float(budget.get('actual_total') or 0))}"
                if budget.get("budget_total") is not None
                else "- Presupuesto base: no disponible en este corte"
            ),
            (
                f"- Proyección al cierre: {_format_currency(float(projection.get('projected_total') or 0))}"
                if projection.get("projected_total") is not None
                else "- Proyección al cierre: no calculada"
            ),
            f"- Estado de cierre contable: {accounting_summary.get('close_readiness') or 'unknown'}",
            f"- Alertas/anomalías detectadas: {int(alert_summary.get('alerts_total') or 0)}",
            "",
            "## Riesgos detectados",
            "\n".join(risk_lines),
            "",
            "## Watchlist",
            "\n".join(watch_lines),
            "",
            "## Fiscal",
            "\n".join(tax_lines),
            "",
            "## Balanza",
            (
                f"- Debe/Haber: {_format_currency(float((balanza_report.get('summary') or {}).get('debe') or 0))} / "
                f"{_format_currency(float((balanza_report.get('summary') or {}).get('haber') or 0))}"
            ),
            (
                f"- Saldo final agregado: {_format_currency(float((balanza_report.get('summary') or {}).get('saldo_final') or 0))}"
            ),
        ]
    ).strip()


async def finance_realtime_report(
    session: AsyncSession,
    *,
    question: Optional[str] = None,
    title: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    proyecto: Optional[str] = None,
    concepto: Optional[str] = None,
    departamento: Optional[str] = None,
    fase_torneo: Optional[str] = None,
    metodo_pago: Optional[str] = None,
    proveedor_nombre: Optional[str] = None,
    budget_total: Optional[float] = None,
    budget_source: str = "solicitudes",
    compare_years: int = 1,
    projection_mode: str = "run_rate",
    group_by: str = "proyecto",
    top_n: int = 12,
    bi_scope: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Realtime finance analytics report:
    - current spend
    - budget vs actual variance
    - YoY comparison (same window previous years)
    - projection (run-rate)
    """
    today = datetime.utcnow().date()
    df = _parse_date(date_from) or date(today.year, 1, 1)
    dt = _parse_date(date_to) or date(today.year, 12, 31)
    if dt < df:
        raise ValueError("date_to must be >= date_from")

    max_years = max(0, min(int(compare_years or 0), 5))
    projection_mode = (projection_mode or "run_rate").strip().lower()
    if projection_mode not in {"run_rate", "none"}:
        raise ValueError("projection_mode must be one of: run_rate, none")
    group_by = (group_by or "proyecto").strip().lower()
    allowed_group = {
        "proyecto",
        "concepto",
        "departamento",
        "fase_torneo",
        "metodo_pago",
        "proveedor",
    }
    if group_by not in allowed_group:
        raise ValueError(f"group_by must be one of: {', '.join(sorted(allowed_group))}")
    max_top = max(1, min(int(top_n or 12), 100))

    filters = {
        "proyecto": (proyecto or "").strip(),
        "concepto": (concepto or "").strip(),
        "departamento": (departamento or "").strip(),
        "fase_torneo": (fase_torneo or "").strip(),
        "metodo_pago": (metodo_pago or "").strip(),
        "proveedor_nombre": (proveedor_nombre or "").strip(),
    }

    expense_filter = [
        ExpenseReport.estado_gasto != "cancelado",
        func.date(ExpenseReport.fecha) >= df,
        func.date(ExpenseReport.fecha) <= dt,
    ]
    if filters["proyecto"]:
        expense_filter.append(ExpenseReport.proyecto.ilike(f"%{filters['proyecto']}%"))
    if filters["concepto"]:
        expense_filter.append(ExpenseReport.concepto.ilike(f"%{filters['concepto']}%"))
    if filters["departamento"]:
        expense_filter.append(
            ExpenseReport.departamento.ilike(f"%{filters['departamento']}%")
        )
    if filters["fase_torneo"]:
        expense_filter.append(
            ExpenseReport.fase_torneo.ilike(f"%{filters['fase_torneo']}%")
        )
    if filters["metodo_pago"]:
        expense_filter.append(
            ExpenseReport.metodo_pago.ilike(f"%{filters['metodo_pago']}%")
        )
    scope_terms = _scope_like_terms(bi_scope)
    if scope_terms:
        expense_scope_expr = []
        for term in scope_terms:
            like = f"%{term}%"
            expense_scope_expr.extend(
                [
                    ExpenseReport.proyecto.ilike(like),
                    ExpenseReport.concepto.ilike(like),
                    ExpenseReport.departamento.ilike(like),
                    ExpenseReport.fase_torneo.ilike(like),
                ]
            )
        expense_filter.append(or_(*expense_scope_expr))

    base_total_row = (
        await session.execute(
            select(
                func.count(ExpenseReport.id).label("n"),
                func.coalesce(func.sum(ExpenseReport.gasto_cantidad), 0).label("m"),
            ).where(*expense_filter)
        )
    ).one()
    current_count = int(base_total_row.n or 0)
    current_total = round(float(base_total_row.m or 0), 2)

    # Time series by month for trend/projection context.
    # Reuse a single SQL expression for date_trunc to avoid asyncpg GROUP BY mismatch
    # when PostgreSQL sees different bind params for "month" across SELECT/GROUP/ORDER.
    month_expr = func.date_trunc(literal_column("'month'"), ExpenseReport.fecha)
    month_rows = (
        await session.execute(
            select(
                month_expr.label("month"),
                func.coalesce(func.sum(ExpenseReport.gasto_cantidad), 0).label("m"),
            )
            .where(*expense_filter)
            .group_by(month_expr)
            .order_by(month_expr.asc())
        )
    ).all()
    monthly_series = [
        {
            "month": r.month.date().isoformat() if r.month else None,
            "monto": round(float(r.m or 0), 2),
        }
        for r in month_rows
    ]

    # Group breakdown
    if group_by == "proveedor":
        doc_filter = [
            func.date(Documento.creado_en) >= df,
            func.date(Documento.creado_en) <= dt,
        ]
        if filters["proveedor_nombre"]:
            doc_filter.append(
                ProveedorCliente.nombre.ilike(f"%{filters['proveedor_nombre']}%")
            )
        if scope_terms:
            doc_scope_expr = []
            for term in scope_terms:
                like = f"%{term}%"
                doc_scope_expr.extend(
                    [
                        Documento.notas.ilike(like),
                        Documento.concepto_pago.ilike(like),
                        Documento.estado.ilike(like),
                        ProveedorCliente.nombre.ilike(like),
                    ]
                )
            doc_filter.append(or_(*doc_scope_expr))
        grouped_rows = (
            await session.execute(
                select(
                    ProveedorCliente.nombre.label("k"),
                    func.count(Documento.id).label("n"),
                    func.coalesce(func.sum(Documento.monto_total), 0).label("m"),
                )
                .select_from(Documento)
                .outerjoin(
                    ProveedorCliente,
                    Documento.proveedor_cliente_id == ProveedorCliente.id,
                )
                .where(*doc_filter)
                .group_by(ProveedorCliente.nombre)
                .order_by(func.coalesce(func.sum(Documento.monto_total), 0).desc())
                .limit(max_top)
            )
        ).all()
    else:
        mapping = {
            "proyecto": ExpenseReport.proyecto,
            "concepto": ExpenseReport.concepto,
            "departamento": ExpenseReport.departamento,
            "fase_torneo": ExpenseReport.fase_torneo,
            "metodo_pago": ExpenseReport.metodo_pago,
        }
        col = mapping[group_by]
        grouped_rows = (
            await session.execute(
                select(
                    col.label("k"),
                    func.count(ExpenseReport.id).label("n"),
                    func.coalesce(func.sum(ExpenseReport.gasto_cantidad), 0).label("m"),
                )
                .where(*expense_filter)
                .group_by(col)
                .order_by(
                    func.coalesce(func.sum(ExpenseReport.gasto_cantidad), 0).desc()
                )
                .limit(max_top)
            )
        ).all()

    breakdown = [
        {
            group_by: (r.k or f"(sin {group_by})"),
            "registros": int(r.n or 0),
            "monto": round(float(r.m or 0), 2),
        }
        for r in grouped_rows
    ]

    # Budget baseline
    resolved_budget = None
    budget_source_value = (budget_source or "solicitudes").strip().lower()
    if budget_total is not None:
        resolved_budget = round(float(budget_total), 2)
        budget_source_value = "input"
    elif budget_source_value == "solicitudes":
        doc_budget_filter = [
            Documento.tipo == "SOLICITUD",
            func.date(Documento.creado_en) >= df,
            func.date(Documento.creado_en) <= dt,
        ]
        if filters["proveedor_nombre"]:
            doc_budget_filter.append(
                ProveedorCliente.nombre.ilike(f"%{filters['proveedor_nombre']}%")
            )
        if scope_terms:
            budget_scope_expr = []
            for term in scope_terms:
                like = f"%{term}%"
                budget_scope_expr.extend(
                    [
                        Documento.notas.ilike(like),
                        Documento.concepto_pago.ilike(like),
                        Documento.estado.ilike(like),
                        ProveedorCliente.nombre.ilike(like),
                    ]
                )
            doc_budget_filter.append(or_(*budget_scope_expr))
        budget_row = (
            await session.execute(
                select(
                    func.coalesce(func.sum(Documento.monto_solicitado), 0).label("m")
                )
                .select_from(Documento)
                .outerjoin(
                    ProveedorCliente,
                    Documento.proveedor_cliente_id == ProveedorCliente.id,
                )
                .where(*doc_budget_filter)
            )
        ).one()
        resolved_budget = round(float(budget_row.m or 0), 2)
    elif budget_source_value != "none":
        raise ValueError("budget_source must be one of: solicitudes, none")

    budget_section = {
        "budget_total": resolved_budget,
        "actual_total": current_total,
        "variance_amount": (
            round((resolved_budget - current_total), 2)
            if resolved_budget is not None
            else None
        ),
        "variance_pct": (
            round(((current_total - resolved_budget) / resolved_budget) * 100, 2)
            if resolved_budget not in (None, 0)
            else None
        ),
        "source": budget_source_value,
    }

    # YoY comparison (same date window shifted to previous years).
    yoy_items: List[Dict[str, Any]] = []
    for i in range(1, max_years + 1):
        prev_df = _shift_year_safe(df, -i)
        prev_dt = _shift_year_safe(dt, -i)
        prev_filter = [ExpenseReport.estado_gasto != "cancelado"]
        prev_filter.append(func.date(ExpenseReport.fecha) >= prev_df)
        prev_filter.append(func.date(ExpenseReport.fecha) <= prev_dt)
        if filters["proyecto"]:
            prev_filter.append(ExpenseReport.proyecto.ilike(f"%{filters['proyecto']}%"))
        if filters["concepto"]:
            prev_filter.append(ExpenseReport.concepto.ilike(f"%{filters['concepto']}%"))
        if filters["departamento"]:
            prev_filter.append(
                ExpenseReport.departamento.ilike(f"%{filters['departamento']}%")
            )
        if filters["fase_torneo"]:
            prev_filter.append(
                ExpenseReport.fase_torneo.ilike(f"%{filters['fase_torneo']}%")
            )
        if filters["metodo_pago"]:
            prev_filter.append(
                ExpenseReport.metodo_pago.ilike(f"%{filters['metodo_pago']}%")
            )
        if scope_terms:
            prev_scope_expr = []
            for term in scope_terms:
                like = f"%{term}%"
                prev_scope_expr.extend(
                    [
                        ExpenseReport.proyecto.ilike(like),
                        ExpenseReport.concepto.ilike(like),
                        ExpenseReport.departamento.ilike(like),
                        ExpenseReport.fase_torneo.ilike(like),
                    ]
                )
            prev_filter.append(or_(*prev_scope_expr))

        prev_row = (
            await session.execute(
                select(
                    func.count(ExpenseReport.id).label("n"),
                    func.coalesce(func.sum(ExpenseReport.gasto_cantidad), 0).label("m"),
                ).where(*prev_filter)
            )
        ).one()
        prev_total = round(float(prev_row.m or 0), 2)
        delta_amount = round(current_total - prev_total, 2)
        delta_pct = (
            round((delta_amount / prev_total) * 100, 2) if prev_total != 0 else None
        )
        yoy_items.append(
            {
                "year_offset": i,
                "period": {"from": prev_df.isoformat(), "to": prev_dt.isoformat()},
                "total": prev_total,
                "registros": int(prev_row.n or 0),
                "delta_vs_current_amount": delta_amount,
                "delta_vs_current_pct": delta_pct,
            }
        )

    # Projection
    projection = {
        "mode": projection_mode,
        "projected_total": None,
        "remaining_days": None,
        "elapsed_days": None,
    }
    if projection_mode == "run_rate":
        effective_end = min(dt, today) if df <= today else df
        elapsed_days = max(1, (effective_end - df).days + 1)
        total_days = max(1, (dt - df).days + 1)
        run_rate_daily = current_total / elapsed_days
        projected_total = round(run_rate_daily * total_days, 2)
        projection = {
            "mode": projection_mode,
            "elapsed_days": elapsed_days,
            "total_days": total_days,
            "remaining_days": max(0, total_days - elapsed_days),
            "run_rate_daily": round(run_rate_daily, 2),
            "projected_total": projected_total,
            "projected_vs_budget_amount": (
                round(projected_total - float(resolved_budget), 2)
                if resolved_budget is not None
                else None
            ),
            "projected_vs_budget_pct": (
                round(
                    (
                        (projected_total - float(resolved_budget))
                        / float(resolved_budget)
                    )
                    * 100,
                    2,
                )
                if resolved_budget not in (None, 0)
                else None
            ),
        }

    report_title = (title or "").strip() or "Reporte financiero en tiempo real"
    return {
        "title": report_title,
        "question": (question or "").strip() or None,
        "generated_at": datetime.utcnow().isoformat(),
        "period": {"from": df.isoformat(), "to": dt.isoformat()},
        "filters": {
            **{k: (v or None) for k, v in filters.items()},
            "bi_scope": (bi_scope or "").strip().lower() or None,
        },
        "totals": {
            "gasto_total": current_total,
            "registros": current_count,
            "moneda": "MXN",
        },
        "budget": budget_section,
        "comparison_yoy": yoy_items,
        "projection": projection,
        "breakdown": {
            "group_by": group_by,
            "items": breakdown,
        },
        "trend_monthly": monthly_series,
        "notes": [
            "Tiempo real sobre expense_reports/documentos en la BD de gastos.",
            "Presupuesto por defecto usa suma de monto_solicitado "
            "en SOLICITUDES (si existe).",
            "Proyeccion run-rate extrapola gasto acumulado en el periodo.",
        ],
    }


async def finance_strategy_snapshot(
    session: AsyncSession,
    *,
    question: Optional[str] = None,
    title: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    proyecto: Optional[str] = None,
    concepto: Optional[str] = None,
    departamento: Optional[str] = None,
    fase_torneo: Optional[str] = None,
    metodo_pago: Optional[str] = None,
    proveedor_nombre: Optional[str] = None,
    budget_total: Optional[float] = None,
    budget_source: str = "solicitudes",
    compare_years: int = 1,
    bi_scope: Optional[str] = None,
    top_n: int = 12,
    z_threshold: float = 2.0,
    min_amount: float = 5000.0,
    min_records: int = 3,
) -> Dict[str, Any]:
    """
    Build a finance strategy package for advisory prompts.

    This is intentionally read-only and composes the existing finance reporting
    primitives into one evidence bundle that Hermes can reason over.
    """
    today = datetime.utcnow().date()
    df = _parse_date(date_from) or date(today.year, 1, 1)
    dt = _parse_date(date_to) or today
    if dt < df:
        raise ValueError("date_to must be >= date_from")

    focus_areas = _finance_strategy_focus_areas(question)
    group_by = "concepto"
    if "cash_flow" in focus_areas:
        group_by = "metodo_pago"
    elif "planning" in focus_areas:
        group_by = "proyecto"

    realtime_report = await finance_realtime_report(
        session,
        question=question,
        title=title or "Paquete estratégico financiero",
        date_from=df.isoformat(),
        date_to=dt.isoformat(),
        proyecto=proyecto,
        concepto=concepto,
        departamento=departamento,
        fase_torneo=fase_torneo,
        metodo_pago=metodo_pago,
        proveedor_nombre=proveedor_nombre,
        budget_total=budget_total,
        budget_source=budget_source,
        compare_years=compare_years,
        projection_mode="run_rate",
        group_by=group_by,
        top_n=top_n,
        bi_scope=bi_scope,
    )
    ops_report = await finance_ops_query(
        session,
        question=question,
        query=None,
        proyecto=proyecto,
        concepto=concepto,
        departamento=departamento,
        fase_torneo=fase_torneo,
        metodo_pago=metodo_pago,
        proveedor_nombre=proveedor_nombre,
        date_from=df.isoformat(),
        date_to=dt.isoformat(),
        limit=max(20, min(int(top_n or 12) * 2, 80)),
    )
    accounting_year = dt.year
    accounting_month = dt.month
    accounting_state = await finance_accounting_report(
        session,
        report_type="estado_mes",
        year=accounting_year,
        month=accounting_month,
        limit=120,
    )
    balanza_report = await finance_accounting_report(
        session,
        report_type="balanza",
        year=accounting_year,
        month=accounting_month,
        limit=120,
    )
    alerts_report = await finance_alerts_scan(
        session,
        date_from=df.isoformat(),
        date_to=dt.isoformat(),
        bi_scope=bi_scope,
        z_threshold=z_threshold,
        min_amount=min_amount,
        min_records=min_records,
    )
    tax_snapshot = await _finance_strategy_tax_snapshot(
        session,
        question=question,
        df=df,
        dt=dt,
        proyecto=proyecto,
        concepto=concepto,
        departamento=departamento,
        fase_torneo=fase_torneo,
        metodo_pago=metodo_pago,
        proveedor_nombre=proveedor_nombre,
        bi_scope=bi_scope,
    )

    realtime_budget = realtime_report.get("budget") or {}
    projection = realtime_report.get("projection") or {}
    accounting_summary = accounting_state.get("summary") or {}
    alerts_summary = alerts_report.get("summary") or {}
    tax_summary = tax_snapshot.get("summary") or {}
    expense_breakdowns = (ops_report.get("expenses") or {}).get("breakdowns") or {}
    document_breakdowns = (ops_report.get("documents") or {}).get("breakdowns") or {}
    expense_total = float(
        ((ops_report.get("expenses") or {}).get("totals") or {}).get("monto_total") or 0
    )
    document_total = float(
        ((ops_report.get("documents") or {}).get("totals") or {}).get("monto_total")
        or 0
    )

    top_concept_signal = _finance_concentration_signal(
        list(expense_breakdowns.get("por_concepto") or []),
        key_name="concepto",
        total_amount=expense_total,
    )
    top_vendor_signal = _finance_concentration_signal(
        list(document_breakdowns.get("por_proveedor") or []),
        key_name="proveedor",
        total_amount=document_total,
    )

    detected_risks: List[Dict[str, Any]] = []
    projected_vs_budget_pct = projection.get("projected_vs_budget_pct")
    projected_vs_budget_amount = projection.get("projected_vs_budget_amount")
    if projected_vs_budget_pct is not None:
        detected_risks.append(
            {
                "type": "budget_projection",
                "severity": _finance_signal_severity(
                    projected_vs_budget_pct,
                    high=12.0,
                    medium=5.0,
                ),
                "detail": (
                    "La proyección run-rate contra presupuesto es "
                    f"{round(float(projected_vs_budget_pct), 2)}% "
                    f"({ _format_currency(float(projected_vs_budget_amount or 0)) })."
                ),
            }
        )
    blockers = list(accounting_summary.get("close_blockers") or [])
    if blockers:
        detected_risks.append(
            {
                "type": "accounting_close",
                "severity": "high" if len(blockers) >= 2 else "medium",
                "detail": " | ".join(blockers[:3]),
            }
        )
    if int(alerts_summary.get("alerts_total") or 0) > 0:
        detected_risks.append(
            {
                "type": "anomalies",
                "severity": (
                    "high"
                    if int(alerts_summary.get("alerts_high") or 0) > 0
                    else "medium"
                ),
                "detail": (
                    f"{int(alerts_summary.get('alerts_total') or 0)} alertas "
                    "estadísticas por concepto en el periodo."
                ),
            }
        )
    if top_vendor_signal.get("top_item"):
        detected_risks.append(
            {
                "type": "vendor_concentration",
                "severity": top_vendor_signal.get("severity"),
                "detail": (
                    f"Proveedor principal {top_vendor_signal.get('top_item')} concentra "
                    f"{top_vendor_signal.get('top_share_pct')}% del monto documental."
                ),
            }
        )
    if top_concept_signal.get("top_item"):
        detected_risks.append(
            {
                "type": "expense_concentration",
                "severity": top_concept_signal.get("severity"),
                "detail": (
                    f"Concepto principal {top_concept_signal.get('top_item')} concentra "
                    f"{top_concept_signal.get('top_share_pct')}% del gasto."
                ),
            }
        )
    if float(tax_summary.get("lodging_tax_gap_total") or 0) > 0:
        detected_risks.append(
            {
                "type": "lodging_tax_gap",
                "severity": _finance_signal_severity(
                    float(tax_summary.get("lodging_tax_gap_total") or 0),
                    high=5000.0,
                    medium=1000.0,
                ),
                "detail": (
                    "Se detectó una brecha probable de impuesto sobre hospedaje no "
                    f"contabilizado por {_format_currency(float(tax_summary.get('lodging_tax_gap_total') or 0))}."
                ),
            }
        )
    elif float(tax_summary.get("lodging_imbalance_total") or 0) > 0:
        detected_risks.append(
            {
                "type": "lodging_poliza_imbalance",
                "severity": _finance_signal_severity(
                    float(tax_summary.get("lodging_imbalance_total") or 0),
                    high=5000.0,
                    medium=1000.0,
                ),
                "detail": (
                    "Hay pólizas de hospedaje descuadradas por "
                    f"{_format_currency(float(tax_summary.get('lodging_imbalance_total') or 0))}; "
                    "conviene revisar si falta impuesto local o una línea fiscal."
                ),
            }
        )
    if float(tax_summary.get("retention_gap_total") or 0) > 0:
        detected_risks.append(
            {
                "type": "retention_lines_missing",
                "severity": _finance_signal_severity(
                    float(tax_summary.get("retention_gap_total") or 0),
                    high=5000.0,
                    medium=1000.0,
                ),
                "detail": (
                    "Se identificaron retenciones en CFDI sin línea contable explícita "
                    f"por {_format_currency(float(tax_summary.get('retention_gap_total') or 0))}."
                ),
            }
        )
    if int(tax_summary.get("unposted_tax_cases") or 0) > 0:
        detected_risks.append(
            {
                "type": "tax_documents_pending_posting",
                "severity": "medium",
                "detail": (
                    f"{int(tax_summary.get('unposted_tax_cases') or 0)} gastos con "
                    "evidencia fiscal siguen sin póliza ligada."
                ),
            }
        )

    watchlist: List[str] = []
    if accounting_summary.get("close_readiness") != "ready":
        watchlist.append(
            "Cierre contable del mes no está listo; revisar bloqueadores antes de tomar decisiones de cierre."
        )
    if projected_vs_budget_pct is not None and float(projected_vs_budget_pct) > 0:
        watchlist.append(
            "La proyección supera presupuesto; conviene revisar recortes o reasignaciones antes del cierre del periodo."
        )
    if top_vendor_signal.get("severity") in {"high", "medium"}:
        watchlist.append(
            "Hay concentración relevante en proveedor; validar dependencia operativa y condiciones de pago."
        )
    if top_concept_signal.get("severity") in {"high", "medium"}:
        watchlist.append(
            "Hay concentración relevante por concepto de gasto; revisar política de aprobación y control presupuestal."
        )
    if int(alerts_summary.get("alerts_total") or 0) > 0:
        watchlist.append(
            "Existen alertas estadísticas; validar si responden a estacionalidad o a desvíos reales."
        )
    if float(tax_summary.get("lodging_tax_gap_total") or 0) > 0:
        watchlist.append(
            "Cruzar CFDI y pólizas de hospedaje; parece faltar impuesto local en el asiento contable."
        )
    elif float(tax_summary.get("lodging_imbalance_total") or 0) > 0:
        watchlist.append(
            "Hay pólizas de hospedaje descuadradas; validar si el descuadre corresponde a un impuesto estatal/local."
        )
    if float(tax_summary.get("retention_gap_total") or 0) > 0:
        watchlist.append(
            "Las retenciones detectadas en CFDI necesitan contrapartida contable explícita para no subestimar pasivos fiscales."
        )
    if int(tax_summary.get("unposted_tax_cases") or 0) > 0:
        watchlist.append(
            "Existen gastos con señal fiscal sin póliza ligada; no conviene cerrar periodo sin depurar esos expedientes."
        )

    strategy_signals = {
        "focus_areas": focus_areas,
        "cash_flow": {
            "projection_mode": projection.get("mode"),
            "projected_total": projection.get("projected_total"),
            "run_rate_daily": projection.get("run_rate_daily"),
            "remaining_days": projection.get("remaining_days"),
            "budget_total": realtime_budget.get("budget_total"),
            "projected_vs_budget_amount": projected_vs_budget_amount,
            "projected_vs_budget_pct": projected_vs_budget_pct,
        },
        "accounting": {
            "close_readiness": accounting_summary.get("close_readiness"),
            "close_blockers": blockers,
            "unmapped_lines": accounting_summary.get("unmapped_lines"),
            "reconciliation": accounting_summary.get("reconciliation"),
        },
        "tax": tax_summary,
        "concentration": {
            "top_vendor": top_vendor_signal,
            "top_concept": top_concept_signal,
        },
        "detected_risks": detected_risks,
        "watchlist": watchlist,
    }

    report_title = (title or "").strip() or "Paquete estratégico financiero"
    artifact_markdown = _finance_strategy_markdown(
        title=report_title,
        realtime_report=realtime_report,
        accounting_month=accounting_state,
        balanza_report=balanza_report,
        alerts_report=alerts_report,
        strategy_signals=strategy_signals,
        tax_snapshot=tax_snapshot,
    )

    return {
        "title": report_title,
        "question": (question or "").strip() or None,
        "generated_at": datetime.utcnow().isoformat(),
        "period": {"from": df.isoformat(), "to": dt.isoformat()},
        "filters": {
            "proyecto": (proyecto or "").strip() or None,
            "concepto": (concepto or "").strip() or None,
            "departamento": (departamento or "").strip() or None,
            "fase_torneo": (fase_torneo or "").strip() or None,
            "metodo_pago": (metodo_pago or "").strip() or None,
            "proveedor_nombre": (proveedor_nombre or "").strip() or None,
            "bi_scope": (bi_scope or "").strip().lower() or None,
        },
        "totals": realtime_report.get("totals") or {},
        "budget": realtime_report.get("budget") or {},
        "comparison_yoy": realtime_report.get("comparison_yoy") or [],
        "projection": realtime_report.get("projection") or {},
        "breakdown": realtime_report.get("breakdown") or {},
        "trend_monthly": realtime_report.get("trend_monthly") or [],
        "strategy_signals": strategy_signals,
        "tax_snapshot": tax_snapshot,
        "ops_snapshot": {
            "expenses": (ops_report.get("expenses") or {}).get("totals") or {},
            "documents": (ops_report.get("documents") or {}).get("totals") or {},
            "expense_breakdowns": expense_breakdowns,
            "document_breakdowns": document_breakdowns,
        },
        "accounting_month": {
            "title": accounting_state.get("title"),
            "period": accounting_state.get("period") or {},
            "summary": accounting_summary,
            "imports": accounting_state.get("imports") or [],
        },
        "balanza": {
            "title": balanza_report.get("title"),
            "period": balanza_report.get("period") or {},
            "summary": balanza_report.get("summary") or {},
            "rows": balanza_report.get("rows") or [],
        },
        "alerts": alerts_report,
        "artifact_markdown": artifact_markdown,
        "notes": [
            "Paquete read-only para estrategia contable, fiscal y financiera.",
            "Combina realtime report, estado contable del mes, balanza y alertas estadísticas.",
            "Diseñado para que Hermes sintetice recomendaciones sin ejecutar writes.",
        ],
    }


def _planner_priority_from_risks(
    risks: List[Dict[str, Any]],
    *,
    tax_summary: Dict[str, Any],
    accounting_summary: Dict[str, Any],
    projection: Dict[str, Any],
    alerts_summary: Dict[str, Any],
) -> List[Dict[str, Any]]:
    priorities: List[Dict[str, Any]] = []

    close_blockers = list(accounting_summary.get("close_blockers") or [])
    if close_blockers:
        priorities.append(
            {
                "priority": "P1",
                "owner": "contabilidad",
                "theme": "cierre_contable",
                "severity": "high",
                "why": close_blockers[0],
                "action": "Destrabar bloqueadores del cierre antes de comprometer cifras o exportes ejecutivos.",
            }
        )

    projected_vs_budget_pct = float(projection.get("projected_vs_budget_pct") or 0)
    if projected_vs_budget_pct > 0:
        priorities.append(
            {
                "priority": "P1" if projected_vs_budget_pct >= 12 else "P2",
                "owner": "direccion_finanzas",
                "theme": "presupuesto_run_rate",
                "severity": "high" if projected_vs_budget_pct >= 12 else "medium",
                "why": (
                    f"La proyección run-rate supera presupuesto por "
                    f"{round(projected_vs_budget_pct, 2)}%."
                ),
                "action": "Definir recorte, reasignación o cobertura presupuestal antes del cierre del periodo.",
            }
        )

    lodging_gap = float(tax_summary.get("lodging_tax_gap_total") or 0)
    if lodging_gap > 0:
        priorities.append(
            {
                "priority": "P1" if lodging_gap >= 5000 else "P2",
                "owner": "fiscal_contabilidad",
                "theme": "impuesto_hospedaje",
                "severity": "high" if lodging_gap >= 5000 else "medium",
                "why": (
                    "Existe una brecha probable de impuesto local de hospedaje por "
                    f"{_format_currency(lodging_gap)}."
                ),
                "action": "Cruzar CFDI y pólizas de hospedaje y registrar el impuesto local faltante.",
            }
        )

    retention_gap = float(tax_summary.get("retention_gap_total") or 0)
    if retention_gap > 0:
        priorities.append(
            {
                "priority": "P2",
                "owner": "fiscal_contabilidad",
                "theme": "retenciones_sin_linea",
                "severity": "medium",
                "why": (
                    "Se detectaron retenciones sin línea contable explícita por "
                    f"{_format_currency(retention_gap)}."
                ),
                "action": "Agregar contrapartidas de retención para no subestimar pasivos fiscales.",
            }
        )

    alerts_high = int(alerts_summary.get("alerts_high") or 0)
    alerts_total = int(alerts_summary.get("alerts_total") or 0)
    if alerts_total > 0:
        priorities.append(
            {
                "priority": "P1" if alerts_high > 0 else "P2",
                "owner": "operaciones_finanzas",
                "theme": "anomalias_estadisticas",
                "severity": "high" if alerts_high > 0 else "medium",
                "why": (
                    f"Hay {alerts_total} alertas estadísticas, "
                    f"{alerts_high} de severidad alta."
                ),
                "action": "Validar si los incrementos responden a estacionalidad, torneo o desvíos reales.",
            }
        )

    high_risks = [
        risk for risk in risks if str(risk.get("severity") or "").lower() == "high"
    ]
    if not priorities and high_risks:
        top = high_risks[0]
        priorities.append(
            {
                "priority": "P2",
                "owner": "direccion_finanzas",
                "theme": str(top.get("type") or "riesgo"),
                "severity": "high",
                "why": str(top.get("detail") or "Riesgo material detectado."),
                "action": "Escalar revisión ejecutiva y definir contención para la semana en curso.",
            }
        )

    return priorities[:6]


def _planner_cadence(
    *,
    priorities: List[Dict[str, Any]],
    alerts_summary: Dict[str, Any],
    accounting_summary: Dict[str, Any],
) -> Dict[str, Any]:
    alerts_total = int(alerts_summary.get("alerts_total") or 0)
    close_readiness = (
        str(accounting_summary.get("close_readiness") or "").strip().lower()
    )
    if priorities and any(item.get("priority") == "P1" for item in priorities):
        rhythm = "semanal"
    elif alerts_total > 0 or close_readiness not in {"", "ready"}:
        rhythm = "quincenal"
    else:
        rhythm = "mensual"

    return {
        "rhythm": rhythm,
        "next_review": (
            "Revisión semanal de caja, cierre y desvíos"
            if rhythm == "semanal"
            else (
                "Revisión quincenal de alertas y desviaciones"
                if rhythm == "quincenal"
                else "Revisión mensual de presupuesto, cierre y planeación"
            )
        ),
        "meeting_inputs": [
            "Projection vs budget",
            "Close blockers",
            "High-severity alerts",
            "Tax gaps and unposted tax cases",
        ],
    }


def _planner_playbooks(
    *,
    priorities: List[Dict[str, Any]],
    strategy_signals: Dict[str, Any],
) -> List[Dict[str, Any]]:
    playbooks: List[Dict[str, Any]] = []
    themes = {str(item.get("theme") or "") for item in priorities}

    if "presupuesto_run_rate" in themes:
        playbooks.append(
            {
                "name": "control_presupuestal",
                "objective": "Contener el sobreconsumo antes del cierre del periodo.",
                "steps": [
                    "Congelar gastos discrecionales no comprometidos.",
                    "Reasignar presupuesto entre torneos/proyectos con menor presión.",
                    "Definir umbrales temporales de autorización para conceptos concentrados.",
                ],
            }
        )
    if "cierre_contable" in themes:
        playbooks.append(
            {
                "name": "cierre_contable",
                "objective": "Cerrar el periodo con cifras auditables y sin bloqueadores abiertos.",
                "steps": [
                    "Resolver pólizas descuadradas y líneas no mapeadas.",
                    "Depurar conciliación bancaria high/medium antes de exportar.",
                    "Validar expedientes fiscales sin póliza ligada.",
                ],
            }
        )
    if {"impuesto_hospedaje", "retenciones_sin_linea"} & themes:
        playbooks.append(
            {
                "name": "higiene_fiscal",
                "objective": "Reducir brechas fiscales recurrentes en CFDI y pólizas.",
                "steps": [
                    "Cruzar CFDI, pólizas y hospedaje local por estado fiscal.",
                    "Registrar impuestos locales y retenciones faltantes.",
                    "Crear checklist previo a cierre para expedientes con señal fiscal.",
                ],
            }
        )
    if "anomalias_estadisticas" in themes:
        playbooks.append(
            {
                "name": "desvios_operativos",
                "objective": "Separar variación esperada de anomalía material.",
                "steps": [
                    "Revisar proveedor, concepto y fase del torneo por cada alerta alta.",
                    "Confirmar si el pico responde a calendario o a ejecución irregular.",
                    "Escalar a dirección sólo alertas confirmadas con impacto económico.",
                ],
            }
        )

    if not playbooks:
        watchlist = list(strategy_signals.get("watchlist") or [])
        playbooks.append(
            {
                "name": "seguimiento_ejecutivo",
                "objective": "Mantener disciplina de revisión corporativa sobre señales activas.",
                "steps": watchlist[:3]
                or [
                    "Revisar run-rate, alertas y readiness contable en la siguiente junta.",
                    "Mantener seguimiento del scope financiero por torneo y concepto.",
                ],
            }
        )
    return playbooks


async def finance_planner_snapshot(
    session: AsyncSession,
    *,
    question: Optional[str] = None,
    title: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    proyecto: Optional[str] = None,
    concepto: Optional[str] = None,
    departamento: Optional[str] = None,
    fase_torneo: Optional[str] = None,
    metodo_pago: Optional[str] = None,
    proveedor_nombre: Optional[str] = None,
    budget_total: Optional[float] = None,
    budget_source: str = "solicitudes",
    compare_years: int = 1,
    bi_scope: Optional[str] = None,
    top_n: int = 12,
    z_threshold: float = 2.0,
    min_amount: float = 5000.0,
    min_records: int = 3,
) -> Dict[str, Any]:
    strategy = await finance_strategy_snapshot(
        session,
        question=question,
        title=title or "Planeador corporativo financiero",
        date_from=date_from,
        date_to=date_to,
        proyecto=proyecto,
        concepto=concepto,
        departamento=departamento,
        fase_torneo=fase_torneo,
        metodo_pago=metodo_pago,
        proveedor_nombre=proveedor_nombre,
        budget_total=budget_total,
        budget_source=budget_source,
        compare_years=compare_years,
        bi_scope=bi_scope,
        top_n=top_n,
        z_threshold=z_threshold,
        min_amount=min_amount,
        min_records=min_records,
    )

    signals = strategy.get("strategy_signals") or {}
    accounting = signals.get("accounting") or {}
    tax = signals.get("tax") or {}
    cash_flow = signals.get("cash_flow") or {}
    risks = list(signals.get("detected_risks") or [])
    alerts_summary = (strategy.get("alerts") or {}).get("summary") or {}

    priorities = _planner_priority_from_risks(
        risks,
        tax_summary=tax,
        accounting_summary=accounting,
        projection=cash_flow,
        alerts_summary=alerts_summary,
    )
    cadence = _planner_cadence(
        priorities=priorities,
        alerts_summary=alerts_summary,
        accounting_summary=accounting,
    )
    playbooks = _planner_playbooks(priorities=priorities, strategy_signals=signals)

    owner_queue: Dict[str, List[Dict[str, Any]]] = {}
    for item in priorities:
        owner_queue.setdefault(
            str(item.get("owner") or "direccion_finanzas"), []
        ).append(item)

    planning_horizon = {
        "window": (
            "0-30 dias"
            if any(item.get("priority") == "P1" for item in priorities)
            else ("30-60 dias" if priorities else "60-90 dias")
        ),
        "focus": (
            "contencion y cierre"
            if any(
                item.get("theme") in {"cierre_contable", "presupuesto_run_rate"}
                for item in priorities
            )
            else (
                "depuracion y disciplina operativa"
                if priorities
                else "monitoreo preventivo"
            )
        ),
    }

    notes = [
        "Planeador corporativo read-only construido sobre strategy snapshot, alerts y accounting state.",
        "Priorizacion sugerida para direccion, contabilidad, fiscal y tesoreria; no ejecuta writes.",
    ]

    return {
        "title": strategy.get("title") or "Planeador corporativo financiero",
        "question": strategy.get("question"),
        "generated_at": strategy.get("generated_at"),
        "period": strategy.get("period") or {},
        "filters": strategy.get("filters") or {},
        "planning_horizon": planning_horizon,
        "cadence": cadence,
        "priorities": priorities,
        "owner_queue": owner_queue,
        "playbooks": playbooks,
        "watchlist": list(signals.get("watchlist") or []),
        "signals": {
            "projection": cash_flow,
            "accounting": accounting,
            "tax": tax,
            "alerts_summary": alerts_summary,
            "detected_risks": risks,
        },
        "source_strategy": strategy,
        "notes": notes,
    }


async def finance_expense_workflow_status(
    session: AsyncSession,
    *,
    expense_id: Optional[str] = None,
    numero_referencia: Optional[str] = None,
) -> Dict[str, Any]:
    """Return end-to-end status for an expense ticket workflow."""
    expense: Optional[ExpenseReport] = None
    if expense_id:
        try:
            expense_uuid = uuid.UUID(expense_id)
        except ValueError as exc:
            raise ValueError("expense_id is invalid") from exc
        expense = (
            await session.execute(
                select(ExpenseReport)
                .options(
                    selectinload(ExpenseReport.cuenta_contable),
                    selectinload(ExpenseReport.cfdi_report),
                )
                .where(ExpenseReport.id == expense_uuid)
            )
        ).scalar_one_or_none()

    if expense is None and numero_referencia:
        reference = (numero_referencia or "").strip()
        expense = (
            await session.execute(
                select(ExpenseReport)
                .options(
                    selectinload(ExpenseReport.cuenta_contable),
                    selectinload(ExpenseReport.cfdi_report),
                )
                .where(ExpenseReport.numero_referencia == reference)
            )
        ).scalar_one_or_none()

    if expense is None:
        raise ValueError("Provide expense_id or numero_referencia")
    if not expense:
        raise ValueError("Expense not found")

    linked_document_id = (
        expense.solicitud_documento_id
        or expense.documento_id
        or expense.informe_documento_id
    )
    document_workflow: Optional[Dict[str, Any]] = None
    if linked_document_id:
        linked_document = (
            await session.execute(
                select(Documento)
                .options(
                    selectinload(Documento.gasto_generado),
                    selectinload(Documento.reembolsos),
                    selectinload(Documento.proveedor_cliente),
                    selectinload(Documento.beneficiario_empleado),
                )
                .where(Documento.id == linked_document_id)
            )
        ).scalar_one_or_none()
        if linked_document is not None:
            reembolsos = sorted(
                list(linked_document.reembolsos or []),
                key=lambda item: (item.fecha_pago or item.creado_en or datetime.min),
            )
            total_reembolsado = round(
                sum(float(getattr(item, "monto", 0) or 0) for item in reembolsos),
                2,
            )
            reembolsos_pagados = [
                item
                for item in reembolsos
                if (getattr(item, "estado", "") or "").lower() == "pagado"
            ]
            latest_reembolso = (
                reembolsos_pagados[-1]
                if reembolsos_pagados
                else (reembolsos[-1] if reembolsos else None)
            )
            gasto_generado = linked_document.gasto_generado
            document_estado = (linked_document.estado or "").strip().lower()
            final_status = document_estado or "borrador"
            if final_status not in {"pagado", "cerrado"}:
                if linked_document.tipo == "SOLICITUD" and gasto_generado is not None:
                    final_status = "pagado"
                elif linked_document.tipo == "INFORME" and reembolsos_pagados:
                    final_status = "pagado"

            closure_detail = "Documento todavía no liquidado."
            next_document_actions: List[str] = []
            if final_status == "cerrado":
                closure_detail = "Documento cerrado operativamente."
                next_document_actions = ["El documento ya está cerrado."]
            elif final_status == "pagado":
                if linked_document.tipo == "SOLICITUD" and gasto_generado is not None:
                    closure_detail = "Solicitud pagada y convertida en gasto operativo."
                elif linked_document.tipo == "INFORME" and latest_reembolso is not None:
                    closure_detail = "Informe pagado vía reembolso registrado."
                else:
                    closure_detail = "Documento marcado como pagado."
                next_document_actions = [
                    "Validar si el documento debe cerrarse formalmente."
                ]
            elif document_estado == "aprobado":
                if linked_document.tipo == "SOLICITUD":
                    next_document_actions = [
                        "Registrar el pago para liquidar la solicitud."
                    ]
                elif linked_document.tipo == "INFORME":
                    next_document_actions = [
                        "Registrar el reembolso para liquidar el informe."
                    ]
            elif document_estado == "enviado":
                next_document_actions = [
                    "Resolver la aprobación o rechazo del documento."
                ]
            elif document_estado == "borrador":
                next_document_actions = ["Enviar el documento para aprobación."]

            document_workflow = {
                "documento_id": str(linked_document.id),
                "numero_referencia": linked_document.numero_referencia,
                "tipo": linked_document.tipo,
                "estado": linked_document.estado,
                "final_status": final_status,
                "is_terminal": final_status in {"pagado", "cerrado"},
                "enviado_en": (
                    linked_document.enviado_en.isoformat()
                    if linked_document.enviado_en
                    else None
                ),
                "aprobado_en": (
                    linked_document.aprobado_en.isoformat()
                    if linked_document.aprobado_en
                    else None
                ),
                "pagado_en": (
                    linked_document.pagado_en.isoformat()
                    if linked_document.pagado_en
                    else None
                ),
                "closure_detail": closure_detail,
                "beneficiario": (
                    linked_document.proveedor_cliente.nombre
                    if linked_document.proveedor_cliente is not None
                    else (
                        linked_document.beneficiario_empleado.nombre
                        if linked_document.beneficiario_empleado is not None
                        else None
                    )
                ),
                "gasto_generado": (
                    {
                        "expense_id": str(gasto_generado.id),
                        "numero_referencia": getattr(
                            gasto_generado, "numero_referencia", None
                        ),
                        "monto": round(
                            float(getattr(gasto_generado, "gasto_cantidad", 0) or 0), 2
                        ),
                    }
                    if gasto_generado is not None
                    else None
                ),
                "reembolsos": {
                    "count": len(reembolsos),
                    "total": total_reembolsado,
                    "latest": (
                        {
                            "reembolso_id": str(latest_reembolso.id),
                            "estado": getattr(latest_reembolso, "estado", None),
                            "monto": round(
                                float(getattr(latest_reembolso, "monto", 0) or 0), 2
                            ),
                            "moneda": getattr(latest_reembolso, "moneda", None),
                            "fecha_pago": (
                                latest_reembolso.fecha_pago.isoformat()
                                if getattr(latest_reembolso, "fecha_pago", None)
                                else None
                            ),
                        }
                        if latest_reembolso is not None
                        else None
                    ),
                },
                "next_actions": next_document_actions,
            }

    invoice_report: Optional[InvoiceReport] = None
    if expense.nova_request_id:
        invoice_report = (
            await session.execute(
                select(InvoiceReport).where(
                    InvoiceReport.nova_request_id == expense.nova_request_id
                )
            )
        ).scalar_one_or_none()
    assistant_poliza = (
        await session.execute(
            select(AccountingPoliza).where(
                AccountingPoliza.source_file == f"assistant_expense:{expense.id}"
            )
        )
    ).scalar_one_or_none()

    cfdi_report = expense.cfdi_report
    ticket_has_receipt = bool(
        expense.archivo_data or expense.archivo_path or expense.archivo_nombre
    )
    cfdi_status = (
        (invoice_report.estado_factura if invoice_report else None)
        or expense.estado_factura
        or ("completada" if cfdi_report else None)
        or ("pendiente" if (expense.tipo_gasto or "").lower() == "ticket" else None)
    )
    account = expense.cuenta_contable
    auto_suggestion: Optional[Dict[str, Any]] = None
    if (
        expense.cuenta_contable_id is None
        and (expense.estado_gasto or "").lower() != "cancelado"
    ):
        try:
            suggestion = await get_cuenta_suggestion(
                session=session,
                expense_id=expense.id,
                concepto=expense.concepto or "",
                metodo_pago=expense.metodo_pago,
                proyecto=expense.proyecto,
                gasto_cantidad=float(expense.gasto_cantidad or 0),
                use_llm=os.getenv("ASSISTANT_CUENTA_SUGGESTER_USE_LLM", "0")
                .strip()
                .lower()
                in {"1", "true", "yes"},
            )
        except Exception:
            suggestion = None
        if suggestion:
            auto_suggestion = {
                "cuenta_contable_id": str(suggestion.cuenta_contable_id),
                "cuenta_codigo": suggestion.cuenta_codigo,
                "cuenta_nombre": suggestion.cuenta_nombre,
                "confidence": round(float(suggestion.confidence_score), 3),
                "reason": suggestion.reason,
                "tier": suggestion.tier,
            }

    ready_for_accounting = bool(
        (expense.estado_gasto or "").lower() != "cancelado"
        and expense.cuenta_contable_id
        and (
            (expense.tipo_gasto or "").lower() != "ticket"
            or cfdi_report is not None
            or cfdi_status == "completada"
        )
    )

    stages = [
        {
            "stage": "expense_registered",
            "status": "done",
            "detail": f"Gasto {expense.numero_referencia or expense.id} registrado",
        },
        {
            "stage": "receipt_attached",
            "status": "done" if ticket_has_receipt else "missing",
            "detail": (
                "Comprobante disponible"
                if ticket_has_receipt
                else "No hay comprobante adjunto"
            ),
        },
        {
            "stage": "cfdi_requested",
            "status": (
                "done"
                if expense.nova_request_id
                else (
                    "not_required"
                    if (expense.tipo_gasto or "").lower() != "ticket"
                    else "pending"
                )
            ),
            "detail": (
                f"Solicitud Tocino {expense.nova_request_id}"
                if expense.nova_request_id
                else "Todavía no se solicita CFDI"
            ),
        },
        {
            "stage": "cfdi_generated",
            "status": (
                "done"
                if (cfdi_report or cfdi_status == "completada")
                else (cfdi_status or "pending")
            ),
            "detail": (
                f"CFDI vinculado UUID {cfdi_report.cfdi_uuid}"
                if cfdi_report and cfdi_report.cfdi_uuid
                else (
                    invoice_report.mensaje_error
                    if invoice_report and invoice_report.mensaje_error
                    else (expense.mensaje_error or "Esperando resultado de CFDI")
                )
            ),
        },
        {
            "stage": "accounting_classified",
            "status": "done" if account else "pending",
            "detail": (
                f"{account.codigo} · {account.nombre}"
                if account
                else "Sin cuenta contable asignada"
            ),
        },
        {
            "stage": "ready_for_accounting",
            "status": "done" if ready_for_accounting else "pending",
            "detail": (
                "Listo para contabilización operativa"
                if ready_for_accounting
                else "Aún faltan CFDI o clasificación contable"
            ),
        },
        {
            "stage": "document_settlement",
            "status": (
                "done"
                if document_workflow
                and document_workflow["final_status"] in {"pagado", "cerrado"}
                else ("pending" if document_workflow else "not_applicable")
            ),
            "detail": (
                document_workflow["closure_detail"]
                if document_workflow
                else "Este gasto no depende de un documento de solicitud/informe."
            ),
        },
        {
            "stage": "posted_to_ledger",
            "status": "done" if assistant_poliza else "pending",
            "detail": (
                "Póliza "
                f"{assistant_poliza.tipo_poliza}-{assistant_poliza.numero_poliza}"
                if assistant_poliza
                else "Aún no existe póliza/asiento automático para este gasto"
            ),
        },
    ]

    next_actions: List[str] = []
    if not ticket_has_receipt and (expense.tipo_gasto or "").lower() == "ticket":
        next_actions.append("Adjuntar un comprobante legible del ticket.")
    if (expense.tipo_gasto or "").lower() == "ticket" and not expense.nova_request_id:
        next_actions.append("Solicitar CFDI a Tocino para iniciar la factura.")
    elif cfdi_status in {"pendiente", "en_proceso"} and not cfdi_report:
        next_actions.append(
            "Esperar el callback/sync de Tocino hasta que llegue el CFDI."
        )
    elif cfdi_status == "error":
        next_actions.append(
            "Revisar el error de facturación y reintentar la solicitud CFDI."
        )
    if expense.cuenta_contable_id is None:
        if auto_suggestion:
            next_actions.append(
                "Asignar cuenta contable sugerida "
                f"{auto_suggestion['cuenta_codigo']} "
                f"({auto_suggestion['confidence']:.3f})."
            )
        else:
            next_actions.append(
                "Asignar manualmente una cuenta contable antes de contabilizar."
            )
    if ready_for_accounting:
        next_actions.append("Proceder con contabilización/revisión contable del gasto.")
    if assistant_poliza:
        next_actions = ["El gasto ya tiene póliza contable generada."]
    if document_workflow and document_workflow.get("next_actions"):
        for action in document_workflow["next_actions"]:
            if action not in next_actions:
                next_actions.insert(0, action)

    overall_status = (
        "posted_to_ledger"
        if assistant_poliza
        else ("ready_for_accounting" if ready_for_accounting else "in_progress")
    )
    if cfdi_status == "error":
        overall_status = "needs_attention"
    elif (expense.estado_gasto or "").lower() == "cancelado":
        overall_status = "cancelled"
    elif document_workflow and document_workflow["final_status"] == "cerrado":
        overall_status = "document_closed"
    elif document_workflow and document_workflow["final_status"] == "pagado":
        overall_status = "document_paid"

    return {
        "expense_id": str(expense.id),
        "numero_referencia": expense.numero_referencia,
        "overall_status": overall_status,
        "stages": stages,
        "expense": {
            "proyecto": expense.proyecto,
            "concepto": expense.concepto,
            "monto": round(float(expense.gasto_cantidad or 0), 2),
            "fecha": expense.fecha.isoformat() if expense.fecha else None,
            "tipo_gasto": expense.tipo_gasto,
            "estado_gasto": expense.estado_gasto,
            "estado_reembolso": expense.estado_reembolso,
            "metodo_pago": expense.metodo_pago,
            "departamento": expense.departamento,
            "cfdi_use": expense.cfdi_use,
        },
        "cfdi": {
            "nova_request_id": expense.nova_request_id,
            "estado_factura": cfdi_status,
            "mensaje_error": (invoice_report.mensaje_error if invoice_report else None)
            or expense.mensaje_error,
            "link_pdf": (invoice_report.link_pdf if invoice_report else None)
            or expense.link_pdf,
            "link_xml": (invoice_report.link_xml if invoice_report else None)
            or expense.link_xml,
            "cfdi_report_id": (
                str(expense.cfdi_report_id) if expense.cfdi_report_id else None
            ),
            "cfdi_uuid": cfdi_report.cfdi_uuid if cfdi_report else None,
            "emisor": cfdi_report.emisor_nombre if cfdi_report else None,
            "total": (
                round(float(cfdi_report.total or 0), 2)
                if cfdi_report and cfdi_report.total is not None
                else None
            ),
        },
        "accounting": {
            "cuenta_contable_id": (
                str(expense.cuenta_contable_id) if expense.cuenta_contable_id else None
            ),
            "cuenta_codigo": account.codigo if account else None,
            "cuenta_nombre": account.nombre if account else None,
            "auto_suggestion": auto_suggestion,
            "ready_for_accounting": ready_for_accounting,
            "posted_poliza_id": str(assistant_poliza.id) if assistant_poliza else None,
            "posted_poliza_numero": (
                assistant_poliza.numero_poliza if assistant_poliza else None
            ),
            "posted_poliza_tipo": (
                assistant_poliza.tipo_poliza if assistant_poliza else None
            ),
        },
        "document_workflow": document_workflow,
        "next_actions": next_actions,
    }


async def finance_accounting_report(
    session: AsyncSession,
    *,
    report_type: str,
    year: Optional[int] = None,
    month: Optional[int] = None,
    company_code: str = "01",
    tipo_poliza: str = "all",
    cuenta_codigo: str = "all",
    q: str = "",
    limit: int = 120,
) -> Dict[str, Any]:
    """Generate accounting-oriented reports for assistant consumption."""
    requested_full_year = year is not None and month is None
    selected_year, selected_month, start_dt, end_dt = _accounting_month_bounds(
        year, month
    )
    if requested_full_year:
        start_dt = datetime(selected_year, 1, 1)
        end_dt = datetime(selected_year + 1, 1, 1)
    selected_type = (report_type or "").strip().lower()
    if selected_type not in {"estado_mes", "diario", "mayor", "balanza"}:
        raise ValueError(
            "report_type must be one of: estado_mes, diario, mayor, balanza"
        )
    selected_tipo_poliza = (tipo_poliza or "all").strip()
    selected_cuenta = (cuenta_codigo or "all").strip()
    selected_company_code, search = _extract_company_code_from_accounting_query(
        q,
        company_code=company_code,
    )
    row_limit = max(10, min(int(limit or 120), 300))

    if selected_type == "estado_mes":
        polizas = (
            (
                await session.execute(
                    select(AccountingPoliza)
                    .options(selectinload(AccountingPoliza.lines))
                    .where(
                        AccountingPoliza.fecha_poliza.isnot(None),
                        AccountingPoliza.fecha_poliza >= start_dt,
                        AccountingPoliza.fecha_poliza < end_dt,
                    )
                )
            )
            .scalars()
            .all()
        )
        aux_entries = (
            (
                await session.execute(
                    select(AuxLedgerEntry).where(
                        AuxLedgerEntry.fecha.isnot(None),
                        AuxLedgerEntry.fecha >= start_dt,
                        AuxLedgerEntry.fecha < end_dt,
                    )
                )
            )
            .scalars()
            .all()
        )
        bank_movements = (
            (
                await session.execute(
                    select(BankMovement).where(
                        BankMovement.fecha >= start_dt,
                        BankMovement.fecha < end_dt,
                    )
                )
            )
            .scalars()
            .all()
        )
        imports = (
            (
                await session.execute(
                    select(AccountingImportRun)
                    .where(
                        AccountingImportRun.started_at >= start_dt,
                        AccountingImportRun.started_at < end_dt,
                    )
                    .order_by(AccountingImportRun.started_at.desc())
                )
            )
            .scalars()
            .all()
        )
        unmapped_lines = int(
            (
                await session.execute(
                    select(func.count(AccountingPolizaLine.id))
                    .join(
                        AccountingPoliza,
                        AccountingPoliza.id == AccountingPolizaLine.poliza_id,
                    )
                    .where(
                        AccountingPoliza.fecha_poliza.isnot(None),
                        AccountingPoliza.fecha_poliza >= start_dt,
                        AccountingPoliza.fecha_poliza < end_dt,
                        AccountingPolizaLine.cuenta_contable_id.is_(None),
                    )
                )
            ).scalar_one()
            or 0
        )

        poliza_debe = sum(
            sum(float(line.debe or 0) for line in poliza.lines) for poliza in polizas
        )
        poliza_haber = sum(
            sum(float(line.haber or 0) for line in poliza.lines) for poliza in polizas
        )
        aux_debe = sum(float(entry.debe or 0) for entry in aux_entries)
        aux_haber = sum(float(entry.haber or 0) for entry in aux_entries)
        bank_amount = sum(float(movement.importe or 0) for movement in bank_movements)
        reco = {"high": 0, "medium": 0, "unmatched": 0}
        for movement in bank_movements:
            state = str(movement.conciliacion_estado or "unmatched").strip().lower()
            if state not in reco:
                state = "unmatched"
            reco[state] += 1

        import_rows: List[Dict[str, Any]] = []
        grouped_imports: Dict[str, Dict[str, Any]] = {}
        for run in imports:
            bucket = grouped_imports.setdefault(
                str(run.source_type or "otro"),
                {
                    "source_type": run.source_type or "otro",
                    "runs": 0,
                    "filename": run.filename,
                    "mode": run.mode,
                    "status": run.status,
                },
            )
            bucket["runs"] += 1
        import_rows = list(grouped_imports.values())

        blockers: List[str] = []
        diff = round(poliza_debe - poliza_haber, 2)
        if abs(diff) > 0.01:
            blockers.append(f"Pólizas descuadradas por {_format_currency(diff)}.")
        if reco["unmatched"] > 0:
            blockers.append(
                f"Hay {reco['unmatched']} movimientos bancarios sin conciliar."
            )
        if unmapped_lines > 0:
            blockers.append(f"Hay {unmapped_lines} partidas sin cuenta contable.")

        summary = {
            "polizas_total": len(polizas),
            "poliza_debe": round(poliza_debe, 2),
            "poliza_haber": round(poliza_haber, 2),
            "aux_debe": round(aux_debe, 2),
            "aux_haber": round(aux_haber, 2),
            "bank_movements": len(bank_movements),
            "bank_amount": round(bank_amount, 2),
            "reconciliation": reco,
            "unmapped_lines": unmapped_lines,
            "close_readiness": "ready" if not blockers else "blocked",
            "close_blockers": blockers,
        }
        artifact_markdown = "\n".join(
            [
                f"# Estado contable {selected_year}-{selected_month:02d}",
                "",
                f"- Pólizas del período: {summary['polizas_total']}",
                (
                    f"- COI debe/haber: "
                    f"{_format_currency(summary['poliza_debe'])} / "
                    f"{_format_currency(summary['poliza_haber'])}"
                ),
                (
                    f"- Auxiliar debe/haber: "
                    f"{_format_currency(summary['aux_debe'])} / "
                    f"{_format_currency(summary['aux_haber'])}"
                ),
                (
                    f"- Banco movimientos: {summary['bank_movements']} por "
                    f"{_format_currency(summary['bank_amount'])}"
                ),
                (
                    f"- Conciliación high/medium/unmatched: "
                    f"{reco['high']} / {reco['medium']} / {reco['unmatched']}"
                ),
                f"- Partidas sin cuenta contable: {unmapped_lines}",
                f"- Estado de cierre: {summary['close_readiness']}",
                "",
                "## Imports",
                _markdown_table(
                    ["Origen", "Corridas", "Archivo ejemplo", "Modo", "Status"],
                    [
                        [
                            row.get("source_type"),
                            row.get("runs"),
                            row.get("filename"),
                            row.get("mode"),
                            row.get("status"),
                        ]
                        for row in import_rows[:20]
                    ],
                )
                or "_Sin imports del período_",
                "",
                "## Bloqueadores",
                (
                    "\n".join([f"- {item}" for item in blockers])
                    if blockers
                    else "- Sin bloqueadores detectados"
                ),
            ]
        ).strip()
        return {
            "report_type": selected_type,
            "title": f"Estado contable del mes {selected_year}-{selected_month:02d}",
            "period": {"year": selected_year, "month": selected_month},
            "summary": summary,
            "imports": import_rows,
            "artifact_markdown": artifact_markdown,
        }

    if selected_type == "diario":
        conditions = [
            AccountingPoliza.fecha_poliza.isnot(None),
            AccountingPoliza.fecha_poliza >= start_dt,
            AccountingPoliza.fecha_poliza < end_dt,
        ]
        if selected_tipo_poliza != "all":
            conditions.append(AccountingPoliza.tipo_poliza == selected_tipo_poliza)
        if search:
            token = f"%{search}%"
            conditions.append(
                or_(
                    AccountingPoliza.numero_poliza.ilike(token),
                    AccountingPoliza.beneficiario_nombre.ilike(token),
                    AccountingPoliza.concepto.ilike(token),
                    AccountingPolizaLine.concepto.ilike(token),
                    AccountingPolizaLine.cuenta_codigo.ilike(token),
                )
            )
        lines = (
            (
                await session.execute(
                    select(AccountingPolizaLine)
                    .join(
                        AccountingPoliza,
                        AccountingPoliza.id == AccountingPolizaLine.poliza_id,
                    )
                    .options(
                        selectinload(AccountingPolizaLine.poliza),
                        selectinload(AccountingPolizaLine.cuenta_contable),
                    )
                    .where(and_(*conditions))
                    .order_by(
                        AccountingPoliza.fecha_poliza.asc(),
                        AccountingPoliza.tipo_poliza.asc(),
                        AccountingPoliza.numero_poliza.asc(),
                        AccountingPolizaLine.line_no.asc(),
                    )
                    .limit(row_limit)
        )
        )
        .scalars()
        .all()
        )
        if not lines:
            historical_tables_report = await _load_historical_accounting_tables_report(
                session,
                report_type=selected_type,
                year=selected_year,
                company_code=selected_company_code,
                month=None if requested_full_year else selected_month,
                q=search,
                cuenta_codigo=selected_cuenta,
                tipo_poliza=selected_tipo_poliza,
                row_limit=row_limit,
            )
            if historical_tables_report is not None:
                return historical_tables_report
            historical_report = await _load_historical_accounting_movements_report(
                session,
                report_type=selected_type,
                year=selected_year,
                month=None if requested_full_year else selected_month,
                q=search,
                cuenta_codigo=selected_cuenta,
                tipo_poliza=selected_tipo_poliza,
                row_limit=row_limit,
            )
            if historical_report is not None:
                return historical_report
        total_debe = round(sum(float(line.debe or 0) for line in lines), 2)
        total_haber = round(sum(float(line.haber or 0) for line in lines), 2)
        rows = [
            {
                "fecha": (
                    line.poliza.fecha_poliza.date().isoformat()
                    if line.poliza and line.poliza.fecha_poliza
                    else None
                ),
                "tipo": line.poliza.tipo_poliza if line.poliza else None,
                "poliza": line.poliza.numero_poliza if line.poliza else None,
                "beneficiario": (
                    line.poliza.beneficiario_nombre if line.poliza else None
                ),
                "cuenta_codigo": line.cuenta_codigo,
                "cuenta_nombre": (
                    line.cuenta_contable.nombre if line.cuenta_contable else None
                ),
                "concepto": line.concepto
                or (line.poliza.concepto_resumen if line.poliza else None),
                "debe": round(float(line.debe or 0), 2),
                "haber": round(float(line.haber or 0), 2),
                "origen": line.poliza.origen if line.poliza else None,
            }
            for line in lines
        ]
        artifact_markdown = "\n".join(
            [
                f"# Libro diario {selected_year}-{selected_month:02d}",
                "",
                f"- Partidas incluidas: {len(rows)}",
                f"- Debe: {_format_currency(total_debe)}",
                f"- Haber: {_format_currency(total_haber)}",
                f"- Diferencia: {_format_currency(total_debe - total_haber)}",
                "",
                _markdown_table(
                    ["Fecha", "Tipo", "Póliza", "Cuenta", "Concepto", "Debe", "Haber"],
                    [
                        [
                            row["fecha"],
                            row["tipo"],
                            row["poliza"],
                            row["cuenta_codigo"],
                            row["concepto"],
                            row["debe"],
                            row["haber"],
                        ]
                        for row in rows
                    ],
                ),
            ]
        ).strip()
        return {
            "report_type": selected_type,
            "title": f"Libro diario {selected_year}-{selected_month:02d}",
            "period": {"year": selected_year, "month": selected_month},
            "filters": {
                "tipo_poliza": (
                    selected_tipo_poliza if selected_tipo_poliza != "all" else None
                ),
                "q": search or None,
            },
            "summary": {
                "rows": len(rows),
                "debe": total_debe,
                "haber": total_haber,
                "difference": round(total_debe - total_haber, 2),
            },
            "rows": rows,
            "artifact_markdown": artifact_markdown,
        }

    if selected_type == "mayor":
        conditions = [
            AccountingPoliza.fecha_poliza.isnot(None),
            AccountingPoliza.fecha_poliza >= start_dt,
            AccountingPoliza.fecha_poliza < end_dt,
        ]
        if search:
            token = f"%{search}%"
            conditions.append(
                or_(
                    AccountingPolizaLine.concepto.ilike(token),
                    AccountingPoliza.numero_poliza.ilike(token),
                    AccountingPoliza.beneficiario_nombre.ilike(token),
                )
            )
        if selected_cuenta != "all":
            conditions.append(AccountingPolizaLine.cuenta_codigo == selected_cuenta)
        lines = (
            (
                await session.execute(
                    select(AccountingPolizaLine)
                    .join(
                        AccountingPoliza,
                        AccountingPoliza.id == AccountingPolizaLine.poliza_id,
                    )
                    .options(
                        selectinload(AccountingPolizaLine.poliza),
                        selectinload(AccountingPolizaLine.cuenta_contable),
                    )
                    .where(and_(*conditions))
                    .order_by(
                        AccountingPolizaLine.cuenta_codigo.asc(),
                        AccountingPoliza.fecha_poliza.asc(),
                        AccountingPoliza.numero_poliza.asc(),
                        AccountingPolizaLine.line_no.asc(),
                    )
                    .limit(row_limit)
        )
        )
        .scalars()
        .all()
        )
        if not lines:
            historical_tables_report = await _load_historical_accounting_tables_report(
                session,
                report_type=selected_type,
                year=selected_year,
                company_code=selected_company_code,
                month=None if requested_full_year else selected_month,
                q=search,
                cuenta_codigo=selected_cuenta,
                tipo_poliza=selected_tipo_poliza,
                row_limit=row_limit,
            )
            if historical_tables_report is not None:
                return historical_tables_report
            historical_report = await _load_historical_accounting_movements_report(
                session,
                report_type=selected_type,
                year=selected_year,
                month=None if requested_full_year else selected_month,
                q=search,
                cuenta_codigo=selected_cuenta,
                tipo_poliza=selected_tipo_poliza,
                row_limit=row_limit,
            )
            if historical_report is not None:
                return historical_report
        by_account: Dict[str, Dict[str, Any]] = {}
        movement_rows: List[Dict[str, Any]] = []
        running_balance = 0.0
        running_code = None
        for line in lines:
            code = line.cuenta_codigo or "Sin cuenta"
            bucket = by_account.setdefault(
                code,
                {
                    "cuenta_codigo": code,
                    "cuenta_nombre": (
                        line.cuenta_contable.nombre if line.cuenta_contable else None
                    ),
                    "movimientos": 0,
                    "debe": 0.0,
                    "haber": 0.0,
                },
            )
            bucket["movimientos"] += 1
            bucket["debe"] += float(line.debe or 0)
            bucket["haber"] += float(line.haber or 0)
            if running_code != code:
                running_code = code
                running_balance = 0.0
            running_balance += float(line.debe or 0) - float(line.haber or 0)
            movement_rows.append(
                {
                    "fecha": (
                        line.poliza.fecha_poliza.date().isoformat()
                        if line.poliza and line.poliza.fecha_poliza
                        else None
                    ),
                    "tipo": line.poliza.tipo_poliza if line.poliza else None,
                    "poliza": line.poliza.numero_poliza if line.poliza else None,
                    "cuenta_codigo": code,
                    "concepto": line.concepto
                    or (line.poliza.concepto_resumen if line.poliza else None),
                    "debe": round(float(line.debe or 0), 2),
                    "haber": round(float(line.haber or 0), 2),
                    "saldo_corrida": round(running_balance, 2),
                }
            )
        summary_rows = [
            {
                **item,
                "debe": round(float(item["debe"] or 0), 2),
                "haber": round(float(item["haber"] or 0), 2),
                "neto": round(float(item["debe"] or 0) - float(item["haber"] or 0), 2),
            }
            for item in by_account.values()
        ]
        summary_rows.sort(key=lambda item: str(item.get("cuenta_codigo") or ""))
        artifact_markdown = "\n".join(
            [
                f"# Mayor contable {selected_year}-{selected_month:02d}",
                "",
                "## Resumen por cuenta",
                _markdown_table(
                    ["Cuenta", "Nombre", "Movimientos", "Debe", "Haber", "Neto"],
                    [
                        [
                            row["cuenta_codigo"],
                            row["cuenta_nombre"],
                            row["movimientos"],
                            row["debe"],
                            row["haber"],
                            row["neto"],
                        ]
                        for row in summary_rows[:80]
                    ],
                ),
                "",
                "## Movimientos",
                _markdown_table(
                    [
                        "Fecha",
                        "Tipo",
                        "Póliza",
                        "Cuenta",
                        "Concepto",
                        "Debe",
                        "Haber",
                        "Saldo",
                    ],
                    [
                        [
                            row["fecha"],
                            row["tipo"],
                            row["poliza"],
                            row["cuenta_codigo"],
                            row["concepto"],
                            row["debe"],
                            row["haber"],
                            row["saldo_corrida"],
                        ]
                        for row in movement_rows
                    ],
                ),
            ]
        ).strip()
        return {
            "report_type": selected_type,
            "title": f"Mayor contable {selected_year}-{selected_month:02d}",
            "period": {"year": selected_year, "month": selected_month},
            "filters": {
                "cuenta_codigo": selected_cuenta if selected_cuenta != "all" else None,
                "q": search or None,
            },
            "summary_by_account": summary_rows,
            "movements": movement_rows,
            "artifact_markdown": artifact_markdown,
        }

    aux_conditions = [
        AuxLedgerEntry.fecha.isnot(None),
        AuxLedgerEntry.fecha >= start_dt,
        AuxLedgerEntry.fecha < end_dt,
    ]
    if search:
        token = f"%{search}%"
        aux_conditions.append(
            or_(
                AuxLedgerEntry.cuenta_codigo.ilike(token),
                AuxLedgerEntry.cuenta_nombre.ilike(token),
                AuxLedgerEntry.concepto.ilike(token),
            )
        )
    aux_entries = (
        (
            await session.execute(
                select(AuxLedgerEntry)
                .options(selectinload(AuxLedgerEntry.cuenta_contable))
                .where(and_(*aux_conditions))
                .order_by(
                    AuxLedgerEntry.cuenta_codigo.asc(),
                    AuxLedgerEntry.fecha.asc(),
                    AuxLedgerEntry.source_row_number.asc(),
                )
            )
    )
    .scalars()
    .all()
    )
    if not aux_entries:
        historical_tables_report = await _load_historical_accounting_tables_report(
            session,
            report_type=selected_type,
            year=selected_year,
            company_code=selected_company_code,
            month=None if requested_full_year else selected_month,
            q=search,
            cuenta_codigo=selected_cuenta,
            tipo_poliza=selected_tipo_poliza,
            row_limit=row_limit,
        )
        if historical_tables_report is not None:
            return historical_tables_report
        historical_report = await _load_historical_accounting_movements_report(
            session,
            report_type=selected_type,
            year=selected_year,
            month=None if requested_full_year else selected_month,
            q=search,
            cuenta_codigo=selected_cuenta,
            tipo_poliza=selected_tipo_poliza,
            row_limit=row_limit,
        )
        if historical_report is not None:
            return historical_report
        static_report = _load_static_balance_report(
            year=selected_year,
            month=selected_month,
            q=search,
            row_limit=row_limit,
        )
        if static_report is not None:
            return static_report
    opening_entries = (
        (
            await session.execute(
                select(AuxLedgerEntry)
                .where(
                    AuxLedgerEntry.fecha.isnot(None), AuxLedgerEntry.fecha < start_dt
                )
                .order_by(
                    AuxLedgerEntry.cuenta_codigo.asc(),
                    AuxLedgerEntry.fecha.desc(),
                    AuxLedgerEntry.source_row_number.desc(),
                )
            )
        )
        .scalars()
        .all()
    )
    opening_by_code: Dict[str, float] = {}
    for entry in opening_entries:
        if entry.cuenta_codigo and entry.cuenta_codigo not in opening_by_code:
            opening_by_code[entry.cuenta_codigo] = float(
                entry.saldo or entry.saldo_inicial or 0
            )
    balances: Dict[str, Dict[str, Any]] = {}
    for entry in aux_entries:
        code = entry.cuenta_codigo or "Sin cuenta"
        bucket = balances.setdefault(
            code,
            {
                "cuenta_codigo": code,
                "cuenta_nombre": entry.cuenta_nombre
                or (entry.cuenta_contable.nombre if entry.cuenta_contable else None),
                "saldo_inicial": opening_by_code.get(
                    code, float(entry.saldo_inicial or 0)
                ),
                "debe": 0.0,
                "haber": 0.0,
                "saldo_final": 0.0,
                "movimientos": 0,
            },
        )
        bucket["debe"] += float(entry.debe or 0)
        bucket["haber"] += float(entry.haber or 0)
        bucket["movimientos"] += 1
        bucket["saldo_final"] = float(entry.saldo or 0)
    rows = [
        {
            **stats,
            "saldo_inicial": round(float(stats["saldo_inicial"] or 0), 2),
            "debe": round(float(stats["debe"] or 0), 2),
            "haber": round(float(stats["haber"] or 0), 2),
            "saldo_final": round(
                float(
                    stats["saldo_final"]
                    if stats["movimientos"]
                    else stats["saldo_inicial"] or 0
                ),
                2,
            ),
        }
        for _, stats in sorted(balances.items())
    ]
    totals = {
        "saldo_inicial": round(
            sum(float(row["saldo_inicial"] or 0) for row in rows), 2
        ),
        "debe": round(sum(float(row["debe"] or 0) for row in rows), 2),
        "haber": round(sum(float(row["haber"] or 0) for row in rows), 2),
        "saldo_final": round(sum(float(row["saldo_final"] or 0) for row in rows), 2),
    }
    artifact_markdown = "\n".join(
        [
            f"# Balanza de comprobación {selected_year}-{selected_month:02d}",
            "",
            f"- Saldo inicial: {_format_currency(totals['saldo_inicial'])}",
            f"- Debe: {_format_currency(totals['debe'])}",
            f"- Haber: {_format_currency(totals['haber'])}",
            f"- Saldo final: {_format_currency(totals['saldo_final'])}",
            "",
            _markdown_table(
                [
                    "Cuenta",
                    "Nombre",
                    "Saldo inicial",
                    "Debe",
                    "Haber",
                    "Saldo final",
                    "Movs",
                ],
                [
                    [
                        row["cuenta_codigo"],
                        row["cuenta_nombre"],
                        row["saldo_inicial"],
                        row["debe"],
                        row["haber"],
                        row["saldo_final"],
                        row["movimientos"],
                    ]
                    for row in rows[: max(1, min(len(rows), row_limit))]
                ],
            ),
        ]
    ).strip()
    return {
        "report_type": selected_type,
        "title": f"Balanza de comprobación {selected_year}-{selected_month:02d}",
        "period": {"year": selected_year, "month": selected_month},
        "filters": {"q": search or None},
        "summary": totals,
        "rows": rows[: max(1, min(len(rows), row_limit))],
        "artifact_markdown": artifact_markdown,
    }


async def finance_alerts_scan(
    session: AsyncSession,
    *,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    bi_scope: Optional[str] = None,
    z_threshold: float = 2.0,
    min_amount: float = 5000.0,
    min_records: int = 3,
) -> Dict[str, Any]:
    """
    Detect financial anomalies for the selected window.

    Strategy:
    - Build weekly totals per concepto (current window).
    - Compare each week vs concepto mean/std-dev in same window.
    - Raise alert when z-score >= threshold and amount >= min_amount.
    """
    today = datetime.utcnow().date()
    df = _parse_date(date_from) or (today - timedelta(days=35))
    dt = _parse_date(date_to) or today
    if dt < df:
        raise ValueError("date_to must be >= date_from")

    z_threshold = max(0.5, min(float(z_threshold or 2.0), 5.0))
    min_amount = max(0.0, float(min_amount or 0))
    min_records = max(2, min(int(min_records or 3), 20))

    filters = [
        ExpenseReport.estado_gasto != "cancelado",
        func.date(ExpenseReport.fecha) >= df,
        func.date(ExpenseReport.fecha) <= dt,
    ]
    scope_terms = _scope_like_terms(bi_scope)
    if scope_terms:
        scope_expr = []
        for term in scope_terms:
            like = f"%{term}%"
            scope_expr.extend(
                [
                    ExpenseReport.proyecto.ilike(like),
                    ExpenseReport.concepto.ilike(like),
                    ExpenseReport.departamento.ilike(like),
                    ExpenseReport.fase_torneo.ilike(like),
                ]
            )
        filters.append(or_(*scope_expr))

    week_expr = func.date_trunc(literal_column("'week'"), ExpenseReport.fecha)
    weekly_rows = (
        await session.execute(
            select(
                ExpenseReport.concepto.label("concepto"),
                week_expr.label("week"),
                func.count(ExpenseReport.id).label("n"),
                func.coalesce(func.sum(ExpenseReport.gasto_cantidad), 0).label("m"),
            )
            .where(*filters)
            .group_by(ExpenseReport.concepto, week_expr)
            .order_by(ExpenseReport.concepto.asc(), week_expr.asc())
        )
    ).all()

    by_concept: Dict[str, List[Dict[str, Any]]] = {}
    for r in weekly_rows:
        key = str(r.concepto or "(sin concepto)")
        by_concept.setdefault(key, []).append(
            {
                "week": r.week.date().isoformat() if r.week else None,
                "records": int(r.n or 0),
                "amount": float(r.m or 0),
            }
        )

    alerts: List[Dict[str, Any]] = []
    for concepto, rows in by_concept.items():
        if len(rows) < min_records:
            continue
        values = [float(x["amount"]) for x in rows]
        mean = sum(values) / len(values)
        variance = sum((v - mean) ** 2 for v in values) / len(values)
        std = variance**0.5
        if std <= 0:
            continue
        for item in rows:
            amount = float(item["amount"])
            if amount < min_amount:
                continue
            z_score = (amount - mean) / std
            if z_score >= z_threshold:
                severity = "high" if z_score >= (z_threshold + 1.5) else "medium"
                alerts.append(
                    {
                        "severity": severity,
                        "concepto": concepto,
                        "week": item["week"],
                        "amount": round(amount, 2),
                        "mean": round(mean, 2),
                        "stddev": round(std, 2),
                        "z_score": round(z_score, 2),
                        "records": int(item["records"]),
                        "hint": (
                            "Revisar comprobantes, proveedor y fase del torneo "
                            "para confirmar si el incremento es esperado."
                        ),
                    }
                )

    alerts.sort(
        key=lambda a: (a.get("severity") != "high", -(float(a.get("z_score") or 0)))
    )

    return {
        "generated_at": datetime.utcnow().isoformat(),
        "period": {"from": df.isoformat(), "to": dt.isoformat()},
        "filters": {
            "bi_scope": (bi_scope or "").strip().lower() or None,
            "z_threshold": z_threshold,
            "min_amount": min_amount,
            "min_records": min_records,
        },
        "summary": {
            "concepts_analyzed": len(by_concept),
            "weeks_analyzed": int(len(weekly_rows)),
            "alerts_total": len(alerts),
            "alerts_high": len([a for a in alerts if a.get("severity") == "high"]),
        },
        "alerts": alerts[:80],
        "notes": [
            "Modelo estadístico simple (z-score semanal por concepto).",
            "Úsalo como alerta temprana; validar con contexto operativo "
            "antes de accionar.",
        ],
    }


async def finance_expense_create(
    session: AsyncSession,
    *,
    empleado_id: str,
    proyecto: str,
    concepto: str,
    gasto_cantidad: float,
    fecha: Optional[str] = None,
    tipo_gasto: str = "manual",
    metodo_pago: Optional[str] = None,
    departamento: Optional[str] = None,
    fase_torneo: Optional[str] = None,
    nombre_enviador: Optional[str] = None,
    numero_referencia: Optional[str] = None,
    iva: Optional[float] = None,
    hospedaje_entidad_fiscal: Optional[str] = None,
    hospedaje_tasa_impuesto: Optional[float] = None,
    hospedaje_impuesto_monto: Optional[float] = None,
    hospedaje_impuesto_confirmado: bool = False,
    cfdi_use: Optional[str] = None,
    archivo_data: Optional[str] = None,
    archivo_nombre: Optional[str] = None,
    request_cfdi_now: bool = False,
) -> Dict[str, Any]:
    """Create an expense from conversational payload."""
    proyecto = (proyecto or "").strip()
    concepto = (concepto or "").strip()
    if not proyecto:
        raise ValueError("proyecto is required")
    if not concepto:
        raise ValueError("concepto is required")

    try:
        emp_uuid = uuid.UUID(empleado_id)
    except ValueError as exc:
        raise ValueError("empleado_id is invalid") from exc

    amount = _normalize_amount(gasto_cantidad)
    fecha_dt = _parse_datetime(fecha) or datetime.utcnow()
    tipo = (tipo_gasto or "manual").strip().lower()
    if tipo not in {"manual", "ticket"}:
        raise ValueError("tipo_gasto must be one of: manual, ticket")
    if request_cfdi_now and tipo != "ticket":
        raise ValueError("request_cfdi_now requires tipo_gasto=ticket")

    reference = (numero_referencia or "").strip() or _build_reference()
    if iva is not None:
        try:
            iva = round(float(iva), 2)
        except (TypeError, ValueError) as exc:
            raise ValueError("iva must be numeric") from exc
    hospedaje_rate = normalize_hospedaje_rate(hospedaje_tasa_impuesto)
    if hospedaje_tasa_impuesto is not None and hospedaje_rate is None:
        raise ValueError("hospedaje_tasa_impuesto must be numeric")
    hospedaje_amount = None
    if hospedaje_impuesto_monto is not None:
        try:
            hospedaje_amount = round(float(hospedaje_impuesto_monto), 2)
        except (TypeError, ValueError) as exc:
            raise ValueError("hospedaje_impuesto_monto must be numeric") from exc

    exp = ExpenseReport(
        empleado_id=emp_uuid,
        proyecto=proyecto,
        concepto=concepto,
        gasto_cantidad=amount,
        fecha=fecha_dt,
        tipo_gasto=tipo,
        metodo_pago=(metodo_pago or None),
        departamento=(departamento or None),
        fase_torneo=(fase_torneo or None),
        nombre_enviador=(nombre_enviador or None),
        numero_referencia=reference,
        iva=iva,
        hospedaje_entidad_fiscal=normalize_hospedaje_state(hospedaje_entidad_fiscal),
        hospedaje_tasa_impuesto=hospedaje_rate,
        hospedaje_impuesto_monto=hospedaje_amount,
        hospedaje_impuesto_confirmado=bool(hospedaje_impuesto_confirmado),
        estado_gasto="activo",
        estado_reembolso="pendiente",
        archivo_data=(archivo_data or None),
        archivo_nombre=(archivo_nombre or None),
        estado_factura="pendiente" if tipo == "ticket" else None,
        cfdi_use=(cfdi_use or None),
        origen="assistant",
    )
    session.add(exp)
    await session.commit()
    await session.refresh(exp)

    # Best-effort automatic accounting classification.
    auto_assign_summary: Dict[str, Any] = {"applied": False}
    auto_assign_enabled = os.getenv(
        "ASSISTANT_AUTO_ASSIGN_CUENTA", "1"
    ).strip().lower() not in {"0", "false", "no"}
    if auto_assign_enabled and exp.cuenta_contable_id is None:
        min_confidence = float(
            os.getenv("ASSISTANT_AUTO_ASSIGN_MIN_CONFIDENCE", "0.80")
        )
        use_llm_for_suggester = os.getenv(
            "ASSISTANT_CUENTA_SUGGESTER_USE_LLM", "0"
        ).strip().lower() in {"1", "true", "yes"}
        suggestion = await get_cuenta_suggestion(
            session=session,
            expense_id=exp.id,
            concepto=exp.concepto,
            metodo_pago=exp.metodo_pago,
            proyecto=exp.proyecto,
            gasto_cantidad=float(exp.gasto_cantidad or 0),
            use_llm=use_llm_for_suggester,
        )
        if suggestion:
            auto_assign_summary = {
                "suggested": True,
                "confidence": round(float(suggestion.confidence_score), 3),
                "reason": suggestion.reason,
                "cuenta_codigo": suggestion.cuenta_codigo,
                "cuenta_nombre": suggestion.cuenta_nombre,
                "tier": suggestion.tier,
                "applied": False,
            }
            if float(suggestion.confidence_score) >= min_confidence:
                exp.cuenta_contable_id = suggestion.cuenta_contable_id
                session.add(exp)
                await session.commit()
                await session.refresh(exp)
                auto_assign_summary["applied"] = True
                auto_assign_summary["cuenta_contable_id"] = str(
                    suggestion.cuenta_contable_id
                )

    nova_request_id = None
    if request_cfdi_now:
        if not exp.archivo_data:
            raise ValueError("Ticket expense needs archivo_data to request CFDI")
        nova_request_id = await trigger_cfdi_generation(
            session=session,
            expense=exp,
            rfc_id=None,
            cfdi_use=cfdi_use or exp.cfdi_use,
        )
        await session.refresh(exp)

    return {
        "expense_id": str(exp.id),
        "numero_referencia": exp.numero_referencia,
        "proyecto": exp.proyecto,
        "concepto": exp.concepto,
        "monto": round(float(exp.gasto_cantidad or 0), 2),
        "fecha": exp.fecha.isoformat() if exp.fecha else None,
        "tipo_gasto": exp.tipo_gasto,
        "estado_factura": exp.estado_factura,
        "cfdi_use": exp.cfdi_use,
        "hospedaje_entidad_fiscal": exp.hospedaje_entidad_fiscal,
        "hospedaje_tasa_impuesto": exp.hospedaje_tasa_impuesto,
        "hospedaje_impuesto_monto": exp.hospedaje_impuesto_monto,
        "hospedaje_impuesto_confirmado": exp.hospedaje_impuesto_confirmado,
        "nova_request_id": nova_request_id,
        "cuenta_contable_id": (
            str(exp.cuenta_contable_id) if exp.cuenta_contable_id else None
        ),
        "auto_accounting": auto_assign_summary,
    }


async def finance_expense_update(
    session: AsyncSession,
    *,
    expense_id: Optional[str] = None,
    numero_referencia: Optional[str] = None,
    proyecto: Optional[str] = None,
    concepto: Optional[str] = None,
    gasto_cantidad: Optional[float] = None,
    fecha: Optional[str] = None,
    tipo_gasto: Optional[str] = None,
    metodo_pago: Optional[str] = None,
    departamento: Optional[str] = None,
    fase_torneo: Optional[str] = None,
    nombre_enviador: Optional[str] = None,
    iva: Optional[float] = None,
    hospedaje_entidad_fiscal: Optional[str] = None,
    hospedaje_tasa_impuesto: Optional[float] = None,
    hospedaje_impuesto_monto: Optional[float] = None,
    hospedaje_impuesto_confirmado: Optional[bool] = None,
) -> Dict[str, Any]:
    """Update editable fields on an expense."""
    exp = None
    if expense_id:
        try:
            exp_uuid = uuid.UUID(expense_id)
        except ValueError as exc:
            raise ValueError("expense_id is invalid") from exc
        exp = (
            await session.execute(
                select(ExpenseReport).where(ExpenseReport.id == exp_uuid)
            )
        ).scalar_one_or_none()

    if exp is None and numero_referencia:
        ref = numero_referencia.strip()
        exp = (
            await session.execute(
                select(ExpenseReport).where(ExpenseReport.numero_referencia == ref)
            )
        ).scalar_one_or_none()

    if exp is None:
        raise ValueError("Provide expense_id or numero_referencia")

    if not exp:
        raise ValueError("Expense not found")
    if (exp.estado_gasto or "").lower() == "cancelado":
        raise ValueError("Cannot edit a canceled expense")

    changed_fields: List[str] = []
    if (
        proyecto is not None
        and proyecto.strip()
        and proyecto.strip() != (exp.proyecto or "")
    ):
        exp.proyecto = proyecto.strip()
        changed_fields.append("proyecto")
    if (
        concepto is not None
        and concepto.strip()
        and concepto.strip() != (exp.concepto or "")
    ):
        exp.concepto = concepto.strip()
        changed_fields.append("concepto")
    if gasto_cantidad is not None:
        amount = _normalize_amount(gasto_cantidad)
        if float(exp.gasto_cantidad or 0) != amount:
            exp.gasto_cantidad = amount
            changed_fields.append("gasto_cantidad")
    if fecha is not None:
        dt = _parse_datetime(fecha)
        if dt and exp.fecha != dt:
            exp.fecha = dt
            changed_fields.append("fecha")
    if tipo_gasto is not None:
        tipo = tipo_gasto.strip().lower()
        if tipo not in {"manual", "ticket"}:
            raise ValueError("tipo_gasto must be one of: manual, ticket")
        if tipo != (exp.tipo_gasto or ""):
            exp.tipo_gasto = tipo
            changed_fields.append("tipo_gasto")
    if metodo_pago is not None and (metodo_pago or "") != (exp.metodo_pago or ""):
        exp.metodo_pago = metodo_pago or None
        changed_fields.append("metodo_pago")
    if departamento is not None and (departamento or "") != (exp.departamento or ""):
        exp.departamento = departamento or None
        changed_fields.append("departamento")
    if fase_torneo is not None and (fase_torneo or "") != (exp.fase_torneo or ""):
        exp.fase_torneo = fase_torneo or None
        changed_fields.append("fase_torneo")
    if nombre_enviador is not None and (nombre_enviador or "") != (
        exp.nombre_enviador or ""
    ):
        exp.nombre_enviador = nombre_enviador or None
        changed_fields.append("nombre_enviador")
    if iva is not None:
        try:
            parsed_iva = round(float(iva), 2)
        except (TypeError, ValueError) as exc:
            raise ValueError("iva must be numeric") from exc
        if float(exp.iva or 0) != parsed_iva:
            exp.iva = parsed_iva
            changed_fields.append("iva")
    if hospedaje_entidad_fiscal is not None:
        normalized_state = normalize_hospedaje_state(hospedaje_entidad_fiscal)
        if (exp.hospedaje_entidad_fiscal or None) != normalized_state:
            exp.hospedaje_entidad_fiscal = normalized_state
            changed_fields.append("hospedaje_entidad_fiscal")
    if hospedaje_tasa_impuesto is not None:
        normalized_rate = normalize_hospedaje_rate(hospedaje_tasa_impuesto)
        if normalized_rate is None:
            raise ValueError("hospedaje_tasa_impuesto must be numeric")
        if round(float(exp.hospedaje_tasa_impuesto or 0), 6) != round(
            normalized_rate, 6
        ):
            exp.hospedaje_tasa_impuesto = normalized_rate
            changed_fields.append("hospedaje_tasa_impuesto")
    if hospedaje_impuesto_monto is not None:
        try:
            parsed_hospedaje = round(float(hospedaje_impuesto_monto), 2)
        except (TypeError, ValueError) as exc:
            raise ValueError("hospedaje_impuesto_monto must be numeric") from exc
        if float(exp.hospedaje_impuesto_monto or 0) != parsed_hospedaje:
            exp.hospedaje_impuesto_monto = parsed_hospedaje
            changed_fields.append("hospedaje_impuesto_monto")
    if hospedaje_impuesto_confirmado is not None:
        confirmed = bool(hospedaje_impuesto_confirmado)
        if bool(exp.hospedaje_impuesto_confirmado) != confirmed:
            exp.hospedaje_impuesto_confirmado = confirmed
            changed_fields.append("hospedaje_impuesto_confirmado")

    if not changed_fields:
        return {
            "expense_id": str(exp.id),
            "updated": False,
            "message": "No se detectaron cambios.",
        }

    exp.updated_at = datetime.utcnow()
    await session.commit()
    await session.refresh(exp)
    return {
        "expense_id": str(exp.id),
        "updated": True,
        "changed_fields": changed_fields,
        "proyecto": exp.proyecto,
        "concepto": exp.concepto,
        "monto": round(float(exp.gasto_cantidad or 0), 2),
        "fecha": exp.fecha.isoformat() if exp.fecha else None,
        "hospedaje_entidad_fiscal": exp.hospedaje_entidad_fiscal,
        "hospedaje_tasa_impuesto": exp.hospedaje_tasa_impuesto,
        "hospedaje_impuesto_monto": exp.hospedaje_impuesto_monto,
        "hospedaje_impuesto_confirmado": exp.hospedaje_impuesto_confirmado,
    }


async def finance_expense_assign_accounting(
    session: AsyncSession,
    *,
    expense_id: Optional[str] = None,
    numero_referencia: Optional[str] = None,
    cuenta_contable_id: Optional[str] = None,
    cuenta_codigo: Optional[str] = None,
    use_suggested: bool = True,
) -> Dict[str, Any]:
    """Assign an accounting account to an expense, optionally using auto-suggestion."""
    exp: Optional[ExpenseReport] = None
    if expense_id:
        try:
            exp_uuid = uuid.UUID(expense_id)
        except ValueError as exc:
            raise ValueError("expense_id is invalid") from exc
        exp = (
            await session.execute(
                select(ExpenseReport)
                .options(selectinload(ExpenseReport.cuenta_contable))
                .where(ExpenseReport.id == exp_uuid)
            )
        ).scalar_one_or_none()

    if exp is None and numero_referencia:
        reference = (numero_referencia or "").strip()
        exp = (
            await session.execute(
                select(ExpenseReport)
                .options(selectinload(ExpenseReport.cuenta_contable))
                .where(ExpenseReport.numero_referencia == reference)
            )
        ).scalar_one_or_none()

    if exp is None:
        raise ValueError("Provide expense_id or numero_referencia")
    if not exp:
        raise ValueError("Expense not found")
    if (exp.estado_gasto or "").lower() == "cancelado":
        raise ValueError("Cannot assign accounting to a canceled expense")

    selected_account: Optional[CuentaContable] = None
    source = "explicit"
    suggestion_payload: Optional[Dict[str, Any]] = None

    if cuenta_contable_id:
        try:
            cuenta_uuid = uuid.UUID(str(cuenta_contable_id))
        except ValueError as exc:
            raise ValueError("cuenta_contable_id is invalid") from exc
        selected_account = (
            await session.execute(
                select(CuentaContable).where(
                    CuentaContable.id == cuenta_uuid,
                    CuentaContable.activo.is_(True),
                )
            )
        ).scalar_one_or_none()
    elif cuenta_codigo:
        code = (cuenta_codigo or "").strip()
        selected_account = (
            await session.execute(
                select(CuentaContable).where(
                    CuentaContable.codigo == code,
                    CuentaContable.activo.is_(True),
                )
            )
        ).scalar_one_or_none()
    elif use_suggested:
        suggestion = await get_cuenta_suggestion(
            session=session,
            expense_id=exp.id,
            concepto=exp.concepto or "",
            metodo_pago=exp.metodo_pago,
            proyecto=exp.proyecto,
            gasto_cantidad=float(exp.gasto_cantidad or 0),
            use_llm=os.getenv("ASSISTANT_CUENTA_SUGGESTER_USE_LLM", "0").strip().lower()
            in {"1", "true", "yes"},
        )
        if suggestion:
            selected_account = (
                await session.execute(
                    select(CuentaContable).where(
                        CuentaContable.id == suggestion.cuenta_contable_id,
                        CuentaContable.activo.is_(True),
                    )
                )
            ).scalar_one_or_none()
            source = "suggested"
            suggestion_payload = {
                "cuenta_contable_id": str(suggestion.cuenta_contable_id),
                "cuenta_codigo": suggestion.cuenta_codigo,
                "cuenta_nombre": suggestion.cuenta_nombre,
                "confidence": round(float(suggestion.confidence_score), 3),
                "reason": suggestion.reason,
                "tier": suggestion.tier,
            }

    if not selected_account:
        raise ValueError("No active cuenta contable found or suggested")

    exp.cuenta_contable_id = selected_account.id
    exp.updated_at = datetime.utcnow()
    session.add(exp)
    await session.commit()
    await session.refresh(exp)

    return {
        "expense_id": str(exp.id),
        "numero_referencia": exp.numero_referencia,
        "assigned": True,
        "assignment_source": source,
        "cuenta_contable_id": str(selected_account.id),
        "cuenta_codigo": selected_account.codigo,
        "cuenta_nombre": selected_account.nombre,
        "suggestion": suggestion_payload,
    }


async def finance_expense_post_accounting(
    session: AsyncSession,
    *,
    empleado_id: str,
    expense_id: Optional[str] = None,
    numero_referencia: Optional[str] = None,
    tipo_poliza: str = "auto",
    contra_cuenta_contable_id: Optional[str] = None,
    contra_cuenta_codigo: Optional[str] = None,
    iva_cuenta_contable_id: Optional[str] = None,
    iva_cuenta_codigo: Optional[str] = None,
    allow_without_cfdi: bool = False,
) -> Dict[str, Any]:
    """Create a journal entry/poliza for a single expense."""
    try:
        empleado_uuid = uuid.UUID(str(empleado_id))
    except ValueError as exc:
        raise ValueError("empleado_id is invalid") from exc

    exp: Optional[ExpenseReport] = None
    if expense_id:
        try:
            exp_uuid = uuid.UUID(expense_id)
        except ValueError as exc:
            raise ValueError("expense_id is invalid") from exc
        exp = (
            await session.execute(
                select(ExpenseReport)
                .options(
                    selectinload(ExpenseReport.cuenta_contable),
                    selectinload(ExpenseReport.cfdi_report),
                )
                .where(ExpenseReport.id == exp_uuid)
            )
        ).scalar_one_or_none()

    if exp is None and numero_referencia:
        reference = (numero_referencia or "").strip()
        exp = (
            await session.execute(
                select(ExpenseReport)
                .options(
                    selectinload(ExpenseReport.cuenta_contable),
                    selectinload(ExpenseReport.cfdi_report),
                )
                .where(ExpenseReport.numero_referencia == reference)
            )
        ).scalar_one_or_none()

    if exp is None:
        raise ValueError("Provide expense_id or numero_referencia")
    if not exp:
        raise ValueError("Expense not found")
    if (exp.estado_gasto or "").lower() == "cancelado":
        raise ValueError("Cannot post a canceled expense")
    if exp.cuenta_contable_id is None or exp.cuenta_contable is None:
        raise ValueError("Expense needs cuenta contable before posting")

    existing_poliza = (
        await session.execute(
            select(AccountingPoliza)
            .where(AccountingPoliza.source_file == f"assistant_expense:{exp.id}")
            .options(selectinload(AccountingPoliza.lines))
        )
    ).scalar_one_or_none()
    if existing_poliza:
        return {
            "posted": False,
            "reason": "already_posted",
            "expense_id": str(exp.id),
            "numero_referencia": exp.numero_referencia,
            "poliza_id": str(existing_poliza.id),
            "numero_poliza": existing_poliza.numero_poliza,
            "tipo_poliza": existing_poliza.tipo_poliza,
        }

    if (exp.tipo_gasto or "").lower() == "ticket" and not allow_without_cfdi:
        if not (
            exp.cfdi_report_id or (exp.estado_factura or "").lower() == "completada"
        ):
            raise ValueError(
                "Ticket expense requires completed CFDI before posting "
                "(or allow_without_cfdi=true)"
            )

    fecha_poliza = exp.fecha or datetime.utcnow()
    fiscal_year = fecha_poliza.year
    fiscal_month = fecha_poliza.month
    close_period = await session.scalar(
        select(AccountingClosePeriod).where(
            AccountingClosePeriod.fiscal_year == fiscal_year,
            AccountingClosePeriod.fiscal_month == fiscal_month,
        )
    )
    if close_period and str(close_period.status or "").strip().lower() == "closed":
        raise ValueError(
            f"Accounting period {fiscal_year}-{fiscal_month:02d} is closed"
        )

    selected_tipo_poliza = (tipo_poliza or "auto").strip()
    if selected_tipo_poliza == "auto":
        payment = str(exp.metodo_pago or "").strip().lower()
        selected_tipo_poliza = (
            "Eg"
            if any(item in payment for item in ("empresa", "amex", "corporativa"))
            else "Di"
        )
    if selected_tipo_poliza not in {"Di", "Eg", "Ig"}:
        raise ValueError("tipo_poliza must be one of: auto, Di, Eg, Ig")

    contra_account, contra_source = await _resolve_counterpart_account(
        session,
        metodo_pago=exp.metodo_pago,
        contra_cuenta_contable_id=contra_cuenta_contable_id,
        contra_cuenta_codigo=contra_cuenta_codigo,
    )
    if not contra_account:
        raise ValueError(
            "No contra account could be resolved; provide "
            "contra_cuenta_codigo or configure defaults"
        )

    iva_account = await _resolve_cuenta_by_id_or_code(
        session,
        cuenta_contable_id=iva_cuenta_contable_id,
        cuenta_codigo=iva_cuenta_codigo
        or os.getenv("ASSISTANT_ACCOUNTING_IVA_CUENTA", "").strip()
        or None,
    )
    accounting_preview = await service_build_expense_accounting_preview(session, exp)
    preview_taxes = accounting_preview.get("taxes") or {}
    local_tax_lines = list(preview_taxes.get("impuestos_locales") or [])
    local_tax_total = round(
        float(preview_taxes.get("impuestos_locales_total") or 0.0), 2
    )

    total_amount = round(float(exp.gasto_cantidad or 0), 2)
    iva_amount = round(float(exp.iva or 0), 2)
    if iva_amount < 0 or iva_amount > total_amount:
        iva_amount = 0.0
    subtotal_amount = round(
        float(
            preview_taxes.get("base_gasto")
            or max(0.0, total_amount - iva_amount - local_tax_total)
        ),
        2,
    )

    source_file = f"assistant_expense:{exp.id}"
    existing_numbers_same_period = (
        (
            await session.execute(
                select(AccountingPoliza.numero_poliza).where(
                    AccountingPoliza.fecha_poliza.isnot(None),
                    AccountingPoliza.fecha_poliza
                    >= datetime(fiscal_year, fiscal_month, 1),
                    AccountingPoliza.fecha_poliza
                    < (
                        datetime(fiscal_year + 1, 1, 1)
                        if fiscal_month == 12
                        else datetime(fiscal_year, fiscal_month + 1, 1)
                    ),
                    AccountingPoliza.tipo_poliza == selected_tipo_poliza,
                    AccountingPoliza.origen.in_(["manual_ui", "assistant_expense"]),
                )
            )
        )
        .scalars()
        .all()
    )
    numero_poliza = _next_poliza_number(
        list(existing_numbers_same_period), selected_tipo_poliza
    )

    line_specs: List[Dict[str, Any]] = []
    expense_concept = exp.concepto or "Gasto operativo"
    if iva_amount > 0 and iva_account:
        line_specs.append(
            {
                "cuenta_codigo": exp.cuenta_contable.codigo,
                "cuenta_contable_id": exp.cuenta_contable.id,
                "concepto": f"{expense_concept} subtotal",
                "debe": subtotal_amount,
                "haber": 0.0,
            }
        )
        line_specs.append(
            {
                "cuenta_codigo": iva_account.codigo,
                "cuenta_contable_id": iva_account.id,
                "concepto": f"IVA acreditable {exp.numero_referencia or exp.id}",
                "debe": iva_amount,
                "haber": 0.0,
            }
        )
        for local_tax in local_tax_lines:
            local_tax_amount = round(float(local_tax.get("importe") or 0.0), 2)
            if local_tax_amount <= 0:
                continue
            account = local_tax.get("account") or {}
            line_specs.append(
                {
                    "cuenta_codigo": account.get("codigo")
                    or exp.cuenta_contable.codigo,
                    "cuenta_contable_id": (
                        uuid.UUID(str(account.get("cuenta_contable_id")))
                        if account.get("cuenta_contable_id")
                        else exp.cuenta_contable.id
                    ),
                    "concepto": (
                        f"{local_tax.get('label') or 'Impuesto local'} "
                        f"{exp.numero_referencia or exp.id}"
                    ),
                    "debe": local_tax_amount,
                    "haber": 0.0,
                }
            )
    else:
        line_specs.append(
            {
                "cuenta_codigo": exp.cuenta_contable.codigo,
                "cuenta_contable_id": exp.cuenta_contable.id,
                "concepto": expense_concept,
                "debe": subtotal_amount,
                "haber": 0.0,
            }
        )
        for local_tax in local_tax_lines:
            local_tax_amount = round(float(local_tax.get("importe") or 0.0), 2)
            if local_tax_amount <= 0:
                continue
            account = local_tax.get("account") or {}
            line_specs.append(
                {
                    "cuenta_codigo": account.get("codigo")
                    or exp.cuenta_contable.codigo,
                    "cuenta_contable_id": (
                        uuid.UUID(str(account.get("cuenta_contable_id")))
                        if account.get("cuenta_contable_id")
                        else exp.cuenta_contable.id
                    ),
                    "concepto": (
                        f"{local_tax.get('label') or 'Impuesto local'} "
                        f"{exp.numero_referencia or exp.id}"
                    ),
                    "debe": local_tax_amount,
                    "haber": 0.0,
                }
            )
    line_specs.append(
        {
            "cuenta_codigo": contra_account.codigo,
            "cuenta_contable_id": contra_account.id,
            "concepto": f"Contra cuenta {exp.metodo_pago or 'gasto'}",
            "debe": 0.0,
            "haber": total_amount,
        }
    )

    poliza = AccountingPoliza(
        id=uuid.uuid4(),
        source_file=source_file,
        source_sheet="assistant",
        source_row_start=None,
        tipo_poliza=selected_tipo_poliza,
        numero_poliza=numero_poliza,
        fecha_poliza=fecha_poliza,
        beneficiario_nombre=exp.nombre_enviador or exp.usuario_nombre or "",
        concepto=f"Gasto {exp.numero_referencia or exp.id}",
        concepto_resumen=f"{expense_concept} ({exp.numero_referencia or exp.id})",
        line_count_declared=len(line_specs),
        line_count_actual=len(line_specs),
        cfdi_uuid=(exp.cfdi_report.cfdi_uuid if exp.cfdi_report else None),
        cfdi_report_id=exp.cfdi_report_id,
        origen="assistant_expense",
    )
    session.add(poliza)
    await session.flush()

    created_lines: List[AccountingPolizaLine] = []
    for idx, spec in enumerate(line_specs, start=1):
        line = AccountingPolizaLine(
            id=uuid.uuid4(),
            poliza_id=poliza.id,
            line_no=idx,
            cuenta_codigo=str(spec["cuenta_codigo"] or ""),
            cuenta_contable_id=spec.get("cuenta_contable_id"),
            concepto=str(spec.get("concepto") or ""),
            movimiento_no=None,
            debe=float(spec.get("debe") or 0),
            haber=float(spec.get("haber") or 0),
            raw_row_json={
                "origin": "assistant_expense",
                "expense_id": str(exp.id),
                "numero_referencia": exp.numero_referencia,
                "contra_source": contra_source,
            },
        )
        created_lines.append(line)
        session.add(line)

    await _record_accounting_audit(
        session,
        empleado_id=empleado_uuid,
        action="create_assistant_expense_poliza",
        poliza_id=poliza.id,
        before_state=None,
        after_state={
            **_poliza_snapshot(poliza),
            "lines": [_poliza_line_snapshot(line) for line in created_lines],
            "expense_id": str(exp.id),
            "numero_referencia": exp.numero_referencia,
        },
        details={
            "source": "assistant_expense",
            "expense_id": str(exp.id),
            "numero_referencia": exp.numero_referencia,
            "contra_account": contra_account.codigo,
            "contra_source": contra_source,
        },
    )

    await session.commit()

    return {
        "posted": True,
        "expense_id": str(exp.id),
        "numero_referencia": exp.numero_referencia,
        "poliza_id": str(poliza.id),
        "numero_poliza": poliza.numero_poliza,
        "tipo_poliza": poliza.tipo_poliza,
        "contra_account": {
            "cuenta_contable_id": str(contra_account.id),
            "codigo": contra_account.codigo,
            "nombre": contra_account.nombre,
            "source": contra_source,
        },
        "iva_account": (
            {
                "cuenta_contable_id": str(iva_account.id),
                "codigo": iva_account.codigo,
                "nombre": iva_account.nombre,
            }
            if iva_account and iva_amount > 0
            else None
        ),
        "impuestos_locales": local_tax_lines,
        "lines": [_poliza_line_snapshot(line) for line in created_lines],
    }


async def finance_expense_request_cfdi(
    session: AsyncSession,
    *,
    expense_id: Optional[str] = None,
    numero_referencia: Optional[str] = None,
    cfdi_use: Optional[str] = None,
) -> Dict[str, Any]:
    """Request CFDI generation for an existing ticket expense."""
    exp = None
    if expense_id:
        try:
            exp_uuid = uuid.UUID(expense_id)
        except ValueError as exc:
            raise ValueError("expense_id is invalid") from exc
        exp = (
            await session.execute(
                select(ExpenseReport).where(ExpenseReport.id == exp_uuid)
            )
        ).scalar_one_or_none()

    if exp is None and numero_referencia:
        ref = numero_referencia.strip()
        exp = (
            await session.execute(
                select(ExpenseReport).where(ExpenseReport.numero_referencia == ref)
            )
        ).scalar_one_or_none()

    if exp is None:
        raise ValueError("Provide expense_id or numero_referencia")
    if (exp.tipo_gasto or "").lower() != "ticket":
        raise ValueError("Only ticket expenses can request CFDI")
    if not exp.archivo_data:
        raise ValueError("Expense has no receipt file for CFDI")

    nova_request_id = await trigger_cfdi_generation(
        session=session,
        expense=exp,
        rfc_id=None,
        cfdi_use=cfdi_use or exp.cfdi_use,
    )
    await session.refresh(exp)

    return {
        "expense_id": str(exp.id),
        "numero_referencia": exp.numero_referencia,
        "nova_request_id": nova_request_id,
        "estado_factura": exp.estado_factura,
        "mensaje_error": exp.mensaje_error,
        "requested": bool(nova_request_id),
    }


async def assistant_save_artifact(
    session: AsyncSession,
    *,
    conversation_id: str,
    created_by_empleado_id: str,
    title: str,
    format: str,
    content: str,
    artifact_type: str = "report_template",
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    import uuid

    artifact = AssistantArtifact(
        conversation_id=uuid.UUID(conversation_id),
        created_by_empleado_id=uuid.UUID(created_by_empleado_id),
        title=title,
        artifact_type=artifact_type,
        format=format,
        content=content,
        metadata_=metadata,
    )
    session.add(artifact)
    await session.commit()
    await session.refresh(artifact)
    return {
        "artifact_id": str(artifact.id),
        "title": artifact.title,
        "format": artifact.format,
    }
