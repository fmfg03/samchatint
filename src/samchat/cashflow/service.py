"""Read-only cashflow planning projection over finance, budgets and AR."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import text


def _safe_str(value: Any) -> str:
    return str(value or "").strip()


def _safe_float(value: Any) -> float:
    try:
        return round(float(value or 0), 2)
    except (TypeError, ValueError):
        return 0.0


async def list_budget_lines(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
    from samchat.budgets.service import list_budget_lines as _list_budget_lines

    return await _list_budget_lines(*args, **kwargs)


async def list_monthly_plan_for_lines(
    *args: Any, **kwargs: Any
) -> dict[str, dict[int, dict[str, float]]]:
    from samchat.budgets.service import (
        list_monthly_plan_for_lines as _list_monthly_plan_for_lines,
    )

    return await _list_monthly_plan_for_lines(*args, **kwargs)


async def build_ar_read_model(*args: Any, **kwargs: Any) -> dict[str, Any]:
    from samchat.ar.service import build_ar_read_model as _build_ar_read_model

    return await _build_ar_read_model(*args, **kwargs)


async def build_finance_source_snapshot(*args: Any, **kwargs: Any) -> dict[str, Any]:
    from samchat.finance_platform import (
        build_finance_source_snapshot as _build_finance_source_snapshot,
    )

    return await _build_finance_source_snapshot(*args, **kwargs)


def build_finance_platform_snapshot(*args: Any, **kwargs: Any) -> dict[str, Any]:
    from samchat.finance_platform import (
        build_finance_platform_snapshot as _build_finance_platform_snapshot,
    )

    return _build_finance_platform_snapshot(*args, **kwargs)


async def list_bank_cash_movements(
    session: Any,
    *,
    start_year: int,
    start_month: int,
    horizon_months: int,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Load bank movements as actual cash only, not AR collection proof."""

    row_limit = max(1, min(int(limit or 500), 5000))
    start_index = start_year * 12 + start_month
    end_index = start_index + max(1, min(int(horizon_months or 1), 24))
    rows = (
        (
            await session.execute(
                text(
                    """
                    SELECT id, fecha, signo, importe, conciliacion_estado
                    FROM bank_movements
                    WHERE fecha IS NOT NULL
                      AND ((EXTRACT(YEAR FROM fecha)::int * 12)
                           + EXTRACT(MONTH FROM fecha)::int) >= :start_index
                      AND ((EXTRACT(YEAR FROM fecha)::int * 12)
                           + EXTRACT(MONTH FROM fecha)::int) < :end_index
                    ORDER BY fecha ASC
                    LIMIT :limit
                    """
                ),
                {
                    "start_index": start_index,
                    "end_index": end_index,
                    "limit": row_limit,
                },
            )
        )
        .mappings()
        .all()
    )
    return [dict(row) for row in rows]


def _period(year: Optional[int], month: Optional[int]) -> tuple[int, int]:
    now = datetime.now(timezone.utc)
    clean_year = int(year or now.year)
    clean_month = int(month or now.month)
    clean_month = min(12, max(1, clean_month))
    return clean_year, clean_month


def _bucket_key(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def _bucket_months(year: int, month: int, horizon_months: int) -> list[dict[str, Any]]:
    buckets = []
    cursor = (year * 12) + month - 1
    for offset in range(max(1, min(int(horizon_months or 1), 24))):
        value = cursor + offset
        bucket_year = value // 12
        bucket_month = (value % 12) + 1
        buckets.append(
            {
                "year": bucket_year,
                "month": bucket_month,
                "key": _bucket_key(bucket_year, bucket_month),
                "actual_cash_in": 0.0,
                "actual_cash_out": 0.0,
                "actual_cash_net": 0.0,
                "approved_obligations": 0.0,
                "planned_budget_income": 0.0,
                "planned_budget_expense": 0.0,
                "recognized_income": 0.0,
                "collected_income": 0.0,
                "expected_uncollected_income": 0.0,
                "forecast_net": 0.0,
            }
        )
    return buckets


def _month_from_iso(value: Any) -> Optional[int]:
    if not value:
        return None
    if hasattr(value, "month"):
        return int(value.month)
    text_value = _safe_str(value)
    if len(text_value) >= 7:
        try:
            return int(text_value[5:7])
        except ValueError:
            return None
    return None


def _add(bucket: dict[str, Any], field: str, value: Any) -> None:
    bucket[field] = _safe_float(bucket.get(field)) + _safe_float(value)


def _summarize(buckets: list[dict[str, Any]]) -> dict[str, float]:
    fields = [
        "actual_cash_in",
        "actual_cash_out",
        "actual_cash_net",
        "approved_obligations",
        "planned_budget_income",
        "planned_budget_expense",
        "recognized_income",
        "collected_income",
        "expected_uncollected_income",
        "forecast_net",
    ]
    return {
        field: _safe_float(sum(_safe_float(bucket.get(field)) for bucket in buckets))
        for field in fields
    }


async def _apply_budget_plan(
    session: Any,
    *,
    budget_version_id: str,
    buckets_by_month: dict[int, dict[str, Any]],
    limit: int,
) -> list[str]:
    notes: list[str] = []
    try:
        lines = await list_budget_lines(
            session,
            version_id=budget_version_id,
            limit=limit,
        )
        line_ids = [_safe_str(line.get("id")) for line in lines if line.get("id")]
        plan = await list_monthly_plan_for_lines(session, line_ids=line_ids)
        for line in lines:
            line_id = _safe_str(line.get("id"))
            direction = _safe_str(line.get("budget_direction")).lower()
            if not direction:
                direction = _safe_str(line.get("line_direction")).lower()
            for month_number, values in (plan.get(line_id) or {}).items():
                bucket = buckets_by_month.get(int(month_number))
                if not bucket:
                    continue
                if direction == "income":
                    _add(
                        bucket,
                        "planned_budget_income",
                        values.get("expected_income_amount"),
                    )
                else:
                    _add(
                        bucket,
                        "planned_budget_expense",
                        values.get("budget_expense_amount"),
                    )
    except Exception as exc:
        notes.append(f"budget_plan_unavailable:{type(exc).__name__}")
    return notes


def _apply_ar(
    ar_payload: dict[str, Any],
    buckets_by_month: dict[int, dict[str, Any]],
) -> None:
    for item in ar_payload.get("issued_linked") or []:
        amount = _safe_float(
            item.get("linked_income_amount") or item.get("issued_amount")
        )
        month_number = _month_from_iso(item.get("recognized_income_date"))
        bucket = buckets_by_month.get(month_number)
        if bucket:
            _add(bucket, "recognized_income", amount)
        if item.get("collection_status") == "matched_collected":
            collection_month = _month_from_iso(item.get("collection_date"))
            collection_month = collection_month or month_number
            collected_bucket = buckets_by_month.get(collection_month)
            if collected_bucket:
                _add(
                    collected_bucket,
                    "collected_income",
                    item.get("collected_amount") or amount,
                )
    for item in ar_payload.get("issued_unlinked") or []:
        if item.get("collection_status") != "matched_collected":
            continue
        collection_month = _month_from_iso(
            item.get("collection_date") or item.get("issued_date")
        )
        bucket = buckets_by_month.get(collection_month)
        if bucket:
            _add(
                bucket,
                "collected_income",
                item.get("collected_amount") or item.get("issued_amount"),
            )


async def _apply_actual_cash(
    session: Any,
    *,
    year: int,
    month: int,
    horizon_months: int,
    buckets_by_key: dict[str, dict[str, Any]],
    limit: int,
) -> list[str]:
    notes: list[str] = []
    try:
        movements = await list_bank_cash_movements(
            session,
            start_year=year,
            start_month=month,
            horizon_months=horizon_months,
            limit=limit,
        )
        for movement in movements:
            fecha = movement.get("fecha")
            if not fecha:
                continue
            if hasattr(fecha, "year"):
                key = _bucket_key(int(fecha.year), int(fecha.month))
            else:
                key = _safe_str(fecha)[:7]
            bucket = buckets_by_key.get(key)
            if not bucket:
                continue
            amount = _safe_float(movement.get("importe"))
            if _safe_str(movement.get("signo")) == "+":
                _add(bucket, "actual_cash_in", amount)
            elif _safe_str(movement.get("signo")) == "-":
                _add(bucket, "actual_cash_out", amount)
    except Exception as exc:
        notes.append(f"actual_cash_unavailable:{type(exc).__name__}")
    return notes


async def _finance_platform_obligations(
    session: Any,
    *,
    year: int,
    month: int,
) -> tuple[float, list[str]]:
    notes: list[str] = []
    try:
        snapshot = await build_finance_source_snapshot(
            session,
            year=year,
            month=month,
            limit=300,
        )
        platform = build_finance_platform_snapshot(snapshot)
        payment_run = platform.get("payment_run") or {}
        cash_control = platform.get("cash_control_center") or {}
        amount = _safe_float(
            payment_run.get("payable_total")
            or cash_control.get("approved_unpaid_total")
        )
        return amount, notes
    except Exception as exc:
        notes.append(f"finance_platform_unavailable:{type(exc).__name__}")
        return 0.0, notes


async def build_cashflow_planning_read_model(
    session: Any,
    *,
    budget_version_id: Optional[str] = None,
    edition_year: Optional[int] = None,
    year: Optional[int] = None,
    month: Optional[int] = None,
    horizon_months: int = 3,
    limit: int = 500,
) -> dict[str, Any]:
    """Build a read-only cashflow planning model from canonical sources."""

    clean_budget_version_id = _safe_str(budget_version_id) or None
    clean_year, clean_month = _period(year or edition_year, month)
    clean_horizon = max(1, min(int(horizon_months or 3), 24))
    row_limit = max(1, min(int(limit or 500), 5000))
    buckets = _bucket_months(clean_year, clean_month, clean_horizon)
    buckets_by_month = {bucket["month"]: bucket for bucket in buckets}
    buckets_by_key = {bucket["key"]: bucket for bucket in buckets}
    source_notes: list[str] = []

    if not clean_budget_version_id:
        source_notes.append("missing_budget_version_id")
    else:
        source_notes.extend(
            await _apply_budget_plan(
                session,
                budget_version_id=clean_budget_version_id,
                buckets_by_month=buckets_by_month,
                limit=row_limit,
            )
        )
        try:
            ar_payload = await build_ar_read_model(
                session,
                budget_version_id=clean_budget_version_id,
                limit=row_limit,
            )
            _apply_ar(ar_payload, buckets_by_month)
        except Exception as exc:
            source_notes.append(f"ar_unavailable:{type(exc).__name__}")

    source_notes.extend(
        await _apply_actual_cash(
            session,
            year=clean_year,
            month=clean_month,
            horizon_months=clean_horizon,
            buckets_by_key=buckets_by_key,
            limit=row_limit,
        )
    )
    approved_obligations, finance_notes = await _finance_platform_obligations(
        session,
        year=clean_year,
        month=clean_month,
    )
    source_notes.extend(finance_notes)
    if buckets:
        buckets[0]["approved_obligations"] = _safe_float(approved_obligations)

    for bucket in buckets:
        bucket["actual_cash_net"] = _safe_float(
            _safe_float(bucket.get("actual_cash_in"))
            - _safe_float(bucket.get("actual_cash_out"))
        )
        bucket["expected_uncollected_income"] = _safe_float(
            max(
                _safe_float(bucket.get("planned_budget_income"))
                + _safe_float(bucket.get("recognized_income"))
                - _safe_float(bucket.get("collected_income")),
                0.0,
            )
        )
        bucket["forecast_net"] = _safe_float(
            _safe_float(bucket.get("actual_cash_net"))
            + _safe_float(bucket.get("collected_income"))
            + _safe_float(bucket.get("expected_uncollected_income"))
            - _safe_float(bucket.get("approved_obligations"))
        )

    return {
        "ok": True,
        "read_only": True,
        "budget_version_id": clean_budget_version_id,
        "edition_year": edition_year,
        "period": {
            "year": clean_year,
            "month": clean_month,
            "horizon_months": clean_horizon,
        },
        "summary": _summarize(buckets),
        "monthly_buckets": buckets,
        "source_notes": source_notes,
    }
