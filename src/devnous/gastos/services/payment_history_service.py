"""Read-only, filtered payment history over the canonical Payment Run source."""

from __future__ import annotations

from calendar import monthrange
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .document_amount_service import EMPLOYEE_REIMBURSEMENT_PREFIX
from .payment_run_service import (
    _PAYMENT_RUN_SOURCE_SQL,
    PaymentRunValidationError,
    _status_for_row,
)

DATE_COLUMNS = {
    "solicitud": "creado_en",
    "programacion": "fecha_pago",
    "pago": "fecha_pago_efectiva",
    "captura": "pagado_en",
}
SORT_COLUMNS = {
    "solicitud": "numero_referencia",
    "referencia_operaciones": "referencia_operaciones",
    "solicitante": "solicitante_nombre",
    "beneficiario": "beneficiario_nombre",
    "proveedor": "proveedor_nombre",
    "fecha_solicitud": "creado_en",
    "fecha_aprobacion": "aprobado_en",
    "fecha_programacion": "fecha_pago",
    "fecha_pagada": "fecha_pago_efectiva",
    "fecha_captura": "pagado_en",
    "monto": "monto",
    "estado": "status_sort",
}


def _amount(raw: str) -> Decimal | None:
    if not raw:
        return None
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        raise PaymentRunValidationError("Captura un monto válido.") from exc
    if not value.is_finite() or value < 0:
        raise PaymentRunValidationError("El monto debe ser finito y no negativo.")
    return value


def history_query(values: Mapping[str, str]) -> tuple[str, dict[str, Any], int]:
    """Validate inputs and bind values; identifiers only come from allowlists."""
    status = values.get("status", "todas")
    if status not in {"todas", "pendientes", "pagadas", "cerradas", "programadas"}:
        raise PaymentRunValidationError("Selecciona un estado válido.")
    date_kind = values.get("date_kind", "programacion")
    sort = values.get("sort", "referencia_operaciones")
    direction = values.get("direction", "desc")
    if (
        date_kind not in DATE_COLUMNS
        or sort not in SORT_COLUMNS
        or direction not in {"asc", "desc"}
    ):
        raise PaymentRunValidationError("Selecciona una fecha y un orden válidos.")
    try:
        page = int(values.get("page", "1"))
        if page < 1 or page > 1000000:
            raise ValueError
        lower = (
            date.fromisoformat(values["date_from"]) if values.get("date_from") else None
        )
        upper = date.fromisoformat(values["date_to"]) if values.get("date_to") else None
        month = values.get("month", "")
        if month:
            first = date.fromisoformat(month + "-01")
            last = first.replace(day=monthrange(first.year, first.month)[1])
            lower = max(lower, first) if lower else first
            upper = min(upper, last) if upper else last
    except (ValueError, TypeError) as exc:
        raise PaymentRunValidationError(
            "Revisa el mes, las fechas y la página."
        ) from exc
    if lower and upper and lower > upper:
        raise PaymentRunValidationError(
            "El rango de fechas no tiene coincidencias: revisa desde, hasta y mes."
        )
    minimum, maximum = _amount(values.get("amount_min", "")), _amount(
        values.get("amount_max", "")
    )
    if minimum is not None and maximum is not None and minimum > maximum:
        raise PaymentRunValidationError("El monto mínimo no puede superar al máximo.")
    paid = "(estado = 'pagado' OR pagado_en IS NOT NULL)"
    filters = [
        "(estado IN ('aprobado', 'en_proceso_pago', 'pagado') OR pagado_en IS NOT NULL)"
    ]
    params: dict[str, Any] = {
        "reimbursement_prefix": f"{EMPLOYEE_REIMBURSEMENT_PREFIX}%",
        "status_today": date.today(),
    }
    if status == "pagadas":
        filters.append(paid)
    elif status in {"pendientes", "cerradas", "programadas"}:
        filters.append("NOT " + paid)
        if status == "cerradas":
            filters.append("(estado = 'en_proceso_pago' OR closure_id IS NOT NULL)")
        elif status == "programadas":
            filters.append("estado = 'aprobado' AND closure_id IS NULL")
    column = DATE_COLUMNS[date_kind]
    if lower:
        filters.append(f"{column}::date >= :date_from")
        params["date_from"] = lower
    if upper:
        filters.append(f"{column}::date <= :date_to")
        params["date_to"] = upper
    for field, columns in {
        "employee": ["solicitante_nombre", "beneficiario_nombre"],
        "requester": ["solicitante_nombre"],
        "beneficiary": ["beneficiario_nombre"],
        "supplier": ["proveedor_nombre"],
        "q": [
            "numero_referencia",
            "referencia_operaciones::text",
            "concepto_pago",
            "solicitante_nombre",
            "beneficiario_nombre",
            "proveedor_nombre",
        ],
    }.items():
        if values.get(field):
            filters.append(
                "("
                + " OR ".join(f"{col} ILIKE :{field} ESCAPE '!'" for col in columns)
                + ")"
            )
            params[field] = (
                "%"
                + values[field].replace("!", "!!").replace("%", "!%").replace("_", "!_")
                + "%"
            )
    if minimum is not None:
        filters.append("monto >= :amount_min")
        params["amount_min"] = minimum
    if maximum is not None:
        filters.append("monto <= :amount_max")
        params["amount_max"] = maximum
    sql = f"""WITH source AS (
        {_PAYMENT_RUN_SOURCE_SQL}
        WHERE d.tipo = 'SOLICITUD'
    ), filtered AS (
        SELECT *, CASE
            WHEN estado = 'pagado' OR pagado_en IS NOT NULL THEN 'pagada'
            WHEN estado = 'en_proceso_pago' OR closure_id IS NOT NULL THEN 'en proceso de pago'
            WHEN fecha_pago < :status_today THEN 'vencida'
            ELSE 'programada'
        END AS status_sort
        FROM source WHERE {' AND '.join(filters)}
    )"""
    return sql, params, page


async def list_payment_history(
    session: AsyncSession, values: Mapping[str, str]
) -> dict[str, Any]:
    """Read a page and full filtered totals in one statement, without schema writes."""
    sql, params, requested_page = history_query(values)
    sort_column = SORT_COLUMNS[values.get("sort", "referencia_operaciones")]
    direction = values.get("direction", "desc").upper()
    # One snapshot for totals and page; even an empty page returns its summary.
    result = await session.execute(
        text(sql + f""",
        totals AS (
            SELECT currency,
                COUNT(*) AS count,
                COUNT(*) FILTER (WHERE monto IS NULL) AS missing_amounts,
                SUM(monto) FILTER (WHERE estado = 'pagado' OR pagado_en IS NOT NULL) ::text AS paid,
                SUM(monto) FILTER (WHERE estado <> 'pagado' AND pagado_en IS NULL) ::text AS pending,
                COUNT(*) FILTER (WHERE estado = 'pagado' OR pagado_en IS NOT NULL) AS paid_count,
                COUNT(*) FILTER (WHERE estado <> 'pagado' AND pagado_en IS NULL) AS pending_count
            FROM filtered GROUP BY currency
        ), stats AS (
            SELECT COUNT(*) AS total_count,
                LEAST(:page, GREATEST(1, (COUNT(*) + 49) / 50)) AS current_page
            FROM filtered
        ), page_rows AS (
            SELECT * FROM filtered
            ORDER BY {sort_column} {direction} NULLS LAST, id ASC
            LIMIT 50 OFFSET (SELECT (current_page - 1) * 50 FROM stats)
        )
        SELECT (SELECT COALESCE(jsonb_agg(to_jsonb(t)), '[]'::jsonb) FROM totals t) AS totals,
            (SELECT COALESCE(jsonb_agg(to_jsonb(p) || jsonb_build_object('monto', p.monto::text) ORDER BY p.{sort_column} {direction} NULLS LAST, p.id ASC), '[]'::jsonb) FROM page_rows p) AS rows,
            stats.total_count, stats.current_page
        FROM stats
    """),
        {**params, "page": requested_page},
    )
    payload = dict(result.mappings().one())
    for row in payload["rows"]:
        # JSON dates must be restored for the canonical overdue classifier.
        if row.get("fecha_pago"):
            row["fecha_pago"] = date.fromisoformat(str(row["fecha_pago"])[:10])
        row["status"] = _status_for_row(row, today=params["status_today"])
    return payload
