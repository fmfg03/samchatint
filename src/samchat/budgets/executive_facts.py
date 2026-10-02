"""Read attributed documentary facts without requiring a budget version.

The caller must authorize every UUID first. Reads stay in the current gastos
installation. No name matching, global fallback, DDL, or financial writes.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from sqlalchemy import text

from devnous.gastos.services.document_amount_service import (
    resolve_payable_document_amount,
)
from devnous.gastos.services.documento_workflow_service import (
    FINANCIAL_TERMINAL_DOCUMENT_STATES,
)
from samchat.budgets.service import (
    _BUDGET_COMMITMENT_DOCUMENT_STATES,
    _budget_expense_base_amount_sql,
)

SOURCE = "samchat.budgets.executive_facts.build_executive_facts"
SCAN_LIMIT = 10000
KEYS = ("actual", "committed", "paid")
COMMITTED = _BUDGET_COMMITMENT_DOCUMENT_STATES | FINANCIAL_TERMINAL_DOCUMENT_STATES


def _money(value: Any) -> Decimal | None:
    try:
        number = Decimal(str(value))
        return number.quantize(Decimal("0.01")) if number.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def project_facts(
    tournament_ids: list[str],
    expenses: list[dict],
    documents: list[dict],
    *,
    start: date,
    end: date,
    truncated: bool | set[str] = False,
) -> dict:
    """Deduplicate identities, preserve source gaps, and never add stages together."""
    result = {}
    for tid in tournament_ids:
        result[tid] = {
            "values": dict.fromkeys(KEYS, Decimal("0")),
            "gaps": {key: [] for key in KEYS},
            "counts": dict.fromkeys(KEYS, 0),
            "monthly": {},
            "temporal_fields": {
                "actual": "expense_reports.fecha",
                "committed": "documentos.creado_en",
                "paid": "documentos.creado_en",
            },
            "scope_note": "Solo atribución por UUID del documento o su cuenta canónica; registros sin torneo quedan fuera. No es consolidación por razón social ni caja.",
        }
    seen = {}
    for kind, records in (("expense", expenses), ("document", documents)):
        for record in records:
            tid = str(record.get("tournament_id") or "")
            identity = (kind, str(record.get("id") or ""))
            if tid not in result or not identity[1]:
                raise ValueError("Fact escaped authorized scope or lacks identity")
            if identity in seen:
                if seen[identity] != record:
                    raise ValueError("Conflicting duplicate fact")
                continue
            seen[identity] = record
            bucket = result[tid]
            observed = _date(record.get("observed_date"))
            if observed and not start <= observed <= end:
                continue
            keys = ["actual"] if kind == "expense" else []
            if kind == "document":
                state = record.get("estado")
                if state in COMMITTED or record.get("pagado_en"):
                    keys.append("committed")
                if state in FINANCIAL_TERMINAL_DOCUMENT_STATES or record.get(
                    "pagado_en"
                ):
                    keys.append("paid")
            issues = []
            if observed is None:
                issues.append(
                    "Fecha de origen ausente; no puede asignarse al intervalo."
                )
            if record.get("currency") != "MXN":
                issues.append("mixed_or_unknown_currency")
            if kind == "expense":
                value = _money(record.get("base_amount"))
                if record.get("gasto_cantidad") is None or value is None:
                    issues.append("expense_amount_missing")
                if record.get("shared_cfdi"):
                    issues.append("shared_cfdi_allocation_requires_reconciliation")
            else:
                value = None
                if (
                    record.get("monto_total") is not None
                    or record.get("monto_solicitado") is not None
                ):
                    try:
                        value = _money(resolve_payable_document_amount(record))
                    except (InvalidOperation, ValueError):
                        pass
                if value is None:
                    issues.append("document_amount_missing")
            for key in keys:
                bucket["gaps"][key].extend(issues)
                bucket["counts"][key] += 1
                if not issues:
                    bucket["values"][key] += value
            if kind == "expense" and not issues:
                month = observed.month
                bucket["monthly"][month] = (
                    bucket["monthly"].get(month, Decimal("0")) + value
                )
    for bucket in result.values():
        bucket["known_subtotals"] = {k: str(v) for k, v in bucket["values"].items()}
        for key in KEYS:
            if truncated is True or (
                isinstance(truncated, set)
                and ("expense" if key == "actual" else "document") in truncated
            ):
                bucket["gaps"][key].append(
                    "Lectura documental truncada; total no acreditado."
                )
            bucket["gaps"][key] = sorted(set(bucket["gaps"][key]))
            bucket["values"][key] = (
                None if bucket["gaps"][key] else str(bucket["values"][key])
            )
        bucket["executive_monthly_actuals"] = [
            {"month": month, "actual_total": str(value)}
            for month, value in sorted(bucket.pop("monthly").items())
        ]
    return {
        "source": SOURCE,
        "by_tournament": result,
        "as_of": datetime.now(timezone.utc).isoformat(),
        "unit": "MXN",
    }


async def build_executive_facts(
    session: Any,
    *,
    tournament_ids: list[str],
    start: date,
    end: date,
    include_expenses: bool = True,
    include_documents: bool = True,
) -> dict:
    """One set-scoped read per source; base amounts reuse the budget tax owner.

    Documentary commitments and paid amounts use the canonical payable amount
    and the creation cohort. Paid dates do not become bank movement dates.
    Shared fiscal allocations remain unavailable until reconciled by the owner.
    """
    ids = sorted({str(UUID(tid)) for tid in tournament_ids})
    if start > end:
        raise ValueError("Invalid documentary period")
    if not ids:
        return project_facts([], [], [], start=start, end=end)
    params = {
        "ids": ids,
        "start": start,
        "end": end,
        "limit": SCAN_LIMIT + 1,
        "committed_states": sorted(COMMITTED),
    }
    expense_rows = (
        (
            (
                await session.execute(
                    text(f"""
        SELECT e.id::text AS id,
            COALESCE(d.torneo_id, expense_cuenta.torneo_id)::text AS tournament_id,
            e.fecha AS observed_date, e.currency, e.gasto_cantidad,
            {_budget_expense_base_amount_sql('e', 'cfdi')} AS base_amount,
            (e.cfdi_compartido_confirmado IS TRUE OR EXISTS (
                SELECT 1 FROM expense_reports sibling
                WHERE sibling.cfdi_report_id = e.cfdi_report_id
                  AND sibling.id <> e.id AND sibling.estado_gasto <> 'cancelado'
            )) AS shared_cfdi
        FROM expense_reports e
        JOIN LATERAL (
            SELECT report.* FROM documentos report
            WHERE (report.tipo = 'INFORME' AND (
                report.id = e.informe_documento_id
                OR report.id = e.documento_id
                OR (e.cuenta_gastos_id IS NOT NULL
                    AND report.cuenta_gastos_id = e.cuenta_gastos_id
                    AND 1 = (SELECT COUNT(*) FROM documentos account_report
                        WHERE account_report.tipo = 'INFORME'
                        AND account_report.cuenta_gastos_id = e.cuenta_gastos_id))
            )) OR (report.tipo = 'SOLICITUD' AND (
                report.id = e.solicitud_documento_id OR report.id = e.documento_id
                OR report.gasto_generado_id = e.id
            ))
            ORDER BY CASE WHEN report.tipo = 'SOLICITUD' THEN
                              CASE WHEN report.id = e.solicitud_documento_id THEN 3
                                   WHEN report.id = e.documento_id THEN 4 ELSE 5 END
                          WHEN report.id = e.informe_documento_id THEN 0
                          WHEN report.id = e.documento_id THEN 1 ELSE 2 END,
                     report.creado_en ASC, report.id ASC
            LIMIT 1
        ) d ON TRUE
        LEFT JOIN cuentas_de_gastos expense_cuenta
          ON expense_cuenta.id = COALESCE(e.cuenta_gastos_id, d.cuenta_gastos_id)
        LEFT JOIN cfdi_reports cfdi ON cfdi.id = e.cfdi_report_id
        WHERE COALESCE(d.torneo_id, expense_cuenta.torneo_id) = ANY(CAST(:ids AS uuid[]))
          AND e.estado_gasto <> 'cancelado'
          AND (e.fecha IS NULL OR DATE(e.fecha) BETWEEN :start AND :end)
        ORDER BY e.id LIMIT :limit
    """),
                    params,
                )
            )
            .mappings()
            .all()
        )
        if include_expenses
        else []
    )
    document_rows = (
        (
            (
                await session.execute(
                    text("""
        SELECT d.id::text AS id,
            COALESCE(d.torneo_id, document_cuenta.torneo_id)::text AS tournament_id,
            d.creado_en AS observed_date, d.currency, d.estado, d.pagado_en,
            d.monto_total, d.monto_solicitado, d.concepto_pago
        FROM documentos d
        LEFT JOIN cuentas_de_gastos document_cuenta ON document_cuenta.id = d.cuenta_gastos_id
        WHERE COALESCE(d.torneo_id, document_cuenta.torneo_id) = ANY(CAST(:ids AS uuid[])) AND d.tipo = 'SOLICITUD'
          AND (d.estado = ANY(CAST(:committed_states AS text[]))
               OR d.pagado_en IS NOT NULL)
          AND (d.creado_en IS NULL OR DATE(d.creado_en) BETWEEN :start AND :end)
        ORDER BY d.id LIMIT :limit
    """),
                    params,
                )
            )
            .mappings()
            .all()
        )
        if include_documents
        else []
    )
    result = project_facts(
        ids,
        [dict(r) for r in expense_rows],
        [dict(r) for r in document_rows],
        start=start,
        end=end,
        truncated={
            kind
            for kind, records in (
                ("expense", expense_rows),
                ("document", document_rows),
            )
            if len(records) > SCAN_LIMIT
        },
    )

    for bucket in result["by_tournament"].values():
        for key, allowed in (
            ("actual", include_expenses),
            ("committed", include_documents),
            ("paid", include_documents),
        ):
            if not allowed:
                bucket["values"][key] = None
                bucket["known_subtotals"][key] = None
                bucket["gaps"][key] = ["Fuente no autorizada para esta identidad."]
        if not include_expenses:
            bucket["executive_monthly_actuals"] = []
    return result
