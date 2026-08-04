"""
Operaciones analytics API — aggregates demo client visibility data by partida presupuestal.

Only includes expenses tagged with DEMO_ORIGIN so production stays empty unless
explicitly seeded (which the seed script blocks on non-test DBs).
Budget lines use the canonical presupuestos tables (budget_versions / budget_lines).
"""

from __future__ import annotations

import os
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from devnous.gastos.models import (
    BudgetConcept,
    Documento,
    Empleado,
    ExpenseReport,
    Tournament,
)
from devnous.gastos.routes.dependencies import get_current_empleado, get_db_session
from samchat.budgets.service import (
    DEMO_BUDGET_SOURCE,
    ensure_budget_schema,
    get_budget_version_by_source,
    list_budget_lines_with_monthly,
    month_labels_es,
    upsert_budget_line_for_concept,
)

router = APIRouter(tags=["operaciones-analytics"])

DEMO_ORIGIN = "demo_operaciones_analytics"
DEMO_SOLICITUD_NOTES = "Demo solicitudes transferencia — test-only seed"
DEMO_ANALYTICS_DOC_NOTES = "Demo analytics cliente"
DEFAULT_EDITION_YEAR = 2026
_BUDGET_SUPER_ROLES = {"superadmin", "super_admin"}
_BUDGET_VIEWER_EMAILS = {"azuniga@plataformasports.com"}
_MONEY_QUANT = Decimal("0.01")


def _can_view_budgets(empleado: Empleado) -> bool:
    role = str(getattr(empleado, "rol", "") or "").strip().lower()
    department = str(getattr(empleado, "departamento", "") or "").strip().lower()
    email = str(getattr(empleado, "correo", "") or "").strip().lower()
    return (
        role in _BUDGET_SUPER_ROLES
        or department.startswith("direcci")
        or email in _BUDGET_VIEWER_EMAILS
    )


def _require_budget_view(empleado: Empleado) -> None:
    if not _can_view_budgets(empleado):
        raise HTTPException(status_code=403, detail="No tienes acceso a presupuestos")


def _require_budget_mutation(empleado: Empleado) -> None:
    role = str(getattr(empleado, "rol", "") or "").strip().lower()
    if role not in _BUDGET_SUPER_ROLES:
        raise HTTPException(
            status_code=403,
            detail="Solo un superadmin puede modificar presupuestos",
        )


def _is_test_runtime() -> bool:
    if os.getenv("ENVIRONMENT", "").strip().lower() == "test":
        return True
    url = (os.getenv("DATABASE_URL") or "") + (os.getenv("SAMCHAT_ENV_FILE") or "")
    return "devnous_db_test" in url


def _money(value: Any) -> float:
    try:
        return float(
            Decimal(str(value or 0)).quantize(_MONEY_QUANT, rounding=ROUND_HALF_UP)
        )
    except (InvalidOperation, TypeError, ValueError):
        return 0.0


def _format_budget_line_money(line: dict[str, Any]) -> dict[str, Any]:
    formatted = dict(line)
    for key in ("budget_amount", "allocated_amount"):
        if key in formatted:
            formatted[key] = _money(formatted.get(key))
    monthly = formatted.get("monthly_allocations")
    if isinstance(monthly, list):
        formatted["monthly_allocations"] = [
            {
                **item,
                "allocated_amount": _money(item.get("allocated_amount")),
            }
            if isinstance(item, dict)
            else item
            for item in monthly
        ]
    return formatted


def _remaining_month_numbers(*, edition_year: int, as_of: Optional[date] = None) -> list[int]:
    today = as_of or date.today()
    if edition_year != today.year:
        return list(range(1, 13))
    return [month for month in range(1, 13) if month > today.month]


class BudgetLineUpsertRequest(BaseModel):
    budget_concept_id: str
    budget_amount: float = Field(ge=0)
    edition_year: int = DEFAULT_EDITION_YEAR
    monthly_allocations: Optional[dict[str, float]] = None


async def _resolve_budget_version(session: AsyncSession, edition_year: int) -> Optional[dict[str, Any]]:
    await ensure_budget_schema(session)
    version = await get_budget_version_by_source(
        session,
        edition_year=edition_year,
        source=DEMO_BUDGET_SOURCE,
    )
    if version is not None:
        return version
    from samchat.budgets.service import list_budget_versions

    versions = await list_budget_versions(session, edition_year=edition_year)
    return versions[0] if versions else None


@router.get("/api/operaciones/analytics/summary")
async def operaciones_analytics_summary(
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = Depends(get_current_empleado),
    tournament_id: Optional[str] = Query(None),
    year: Optional[int] = Query(None),
    edition_year: int = Query(DEFAULT_EDITION_YEAR),
) -> JSONResponse:
    """Aggregate demo operaciones expenses and presupuesto by partida."""
    _require_budget_view(current_empleado)

    expense_year = year or edition_year
    filters = [
        ExpenseReport.origen == DEMO_ORIGIN,
        ExpenseReport.estado_gasto != "cancelado",
        func.extract("year", ExpenseReport.fecha) == expense_year,
    ]

    tournament_name: Optional[str] = None
    if tournament_id:
        tournament = await session.get(Tournament, tournament_id)
        if tournament is not None:
            tournament_name = tournament.name
            filters.append(ExpenseReport.proyecto == tournament.name)

    total_row = (
        await session.execute(
            select(
                func.coalesce(func.sum(ExpenseReport.gasto_cantidad), 0).label("total"),
                func.count(ExpenseReport.id).label("count"),
            ).where(*filters)
        )
    ).one()
    total_spent = _money(total_row.total)
    expense_count = int(total_row.count or 0)

    spent_rows = (
        await session.execute(
            select(
                BudgetConcept.id.label("concept_id"),
                BudgetConcept.concept_name,
                BudgetConcept.concept_key,
                func.coalesce(func.sum(ExpenseReport.gasto_cantidad), 0).label("amount"),
                func.count(ExpenseReport.id).label("count"),
            )
            .join(BudgetConcept, ExpenseReport.budget_concept_id == BudgetConcept.id)
            .where(*filters)
            .group_by(BudgetConcept.id, BudgetConcept.concept_name, BudgetConcept.concept_key)
        )
    ).all()
    spent_by_concept = {
        str(row.concept_id): {
            "concept_name": row.concept_name,
            "concept_key": row.concept_key,
            "amount": _money(row.amount),
            "count": int(row.count or 0),
        }
        for row in spent_rows
    }

    budget_version = await _resolve_budget_version(session, edition_year)
    budget_lines: list[dict[str, Any]] = []
    if budget_version:
        budget_lines = await list_budget_lines_with_monthly(
            session,
            version_id=budget_version["id"],
            tournament_id=tournament_id,
            limit=5000,
        )

    remaining_months = _remaining_month_numbers(edition_year=edition_year)
    remaining_month_labels = month_labels_es(remaining_months)

    partida_map: dict[str, dict[str, Any]] = {}
    for line in budget_lines:
        concept_id = str(line.get("budget_concept_id") or "")
        if not concept_id:
            continue
        monthly_lookup = {
            int(item["month_number"]): _money(item["allocated_amount"])
            for item in line.get("monthly_allocations") or []
        }
        budget_amount = _money(line.get("budget_amount"))
        spent_info = spent_by_concept.get(concept_id, {})
        spent_amount = _money(spent_info.get("amount"))
        remaining_year = _money(budget_amount - spent_amount)
        pct_budget = round((spent_amount / budget_amount) * 100) if budget_amount else None
        partida_map[concept_id] = {
            "budget_concept_id": concept_id,
            "budget_line_id": line.get("id"),
            "concept_name": line.get("concept_name") or spent_info.get("concept_name"),
            "concept_key": spent_info.get("concept_key") or "",
            "budget_amount": budget_amount,
            "spent_amount": spent_amount,
            "expense_count": int(spent_info.get("count") or 0),
            "remaining_year": remaining_year,
            "pct_budget": pct_budget,
            "pct_of_total_spent": None,
            "remaining_months": {
                str(month): _money(monthly_lookup.get(month, 0.0))
                for month in remaining_months
            },
        }

    for concept_id, spent_info in spent_by_concept.items():
        if concept_id in partida_map:
            partida_map[concept_id]["concept_key"] = spent_info.get("concept_key") or ""
            continue
        partida_map[concept_id] = {
            "budget_concept_id": concept_id,
            "budget_line_id": None,
            "concept_name": spent_info.get("concept_name"),
            "concept_key": spent_info.get("concept_key"),
            "budget_amount": 0.0,
            "spent_amount": _money(spent_info.get("amount")),
            "expense_count": int(spent_info.get("count") or 0),
            "remaining_year": _money(0 - _money(spent_info.get("amount"))),
            "pct_budget": None,
            "pct_of_total_spent": None,
            "remaining_months": {str(month): 0.0 for month in remaining_months},
        }

    by_partida = sorted(
        partida_map.values(),
        key=lambda row: (row.get("spent_amount") or 0, row.get("budget_amount") or 0),
        reverse=True,
    )
    for row in by_partida:
        if total_spent:
            row["pct_of_total_spent"] = round((row["spent_amount"] / total_spent) * 100)

    budget_total = _money(sum(_money(row.get("budget_amount")) for row in by_partida))

    fase_rows = (
        await session.execute(
            select(
                ExpenseReport.fase_torneo,
                func.coalesce(func.sum(ExpenseReport.gasto_cantidad), 0).label("amount"),
                func.count(ExpenseReport.id).label("count"),
            )
            .where(*filters)
            .group_by(ExpenseReport.fase_torneo)
            .order_by(func.sum(ExpenseReport.gasto_cantidad).desc())
        )
    ).all()

    month_expr = func.date_trunc("month", ExpenseReport.fecha).label("month")
    month_rows = (
        await session.execute(
            select(
                month_expr,
                func.coalesce(func.sum(ExpenseReport.gasto_cantidad), 0).label("amount"),
                func.count(ExpenseReport.id).label("count"),
            )
            .where(*filters)
            .group_by(month_expr)
            .order_by(month_expr.asc())
        )
    ).all()

    empleado_rows = (
        await session.execute(
            select(
                Empleado.nombre,
                func.coalesce(func.sum(ExpenseReport.gasto_cantidad), 0).label("amount"),
                func.count(ExpenseReport.id).label("count"),
            )
            .join(Empleado, ExpenseReport.empleado_id == Empleado.id)
            .where(*filters)
            .group_by(Empleado.nombre)
            .order_by(func.sum(ExpenseReport.gasto_cantidad).desc())
            .limit(8)
        )
    ).all()

    doc_rows = (
        await session.execute(
            select(
                Documento.tipo,
                func.coalesce(func.sum(Documento.monto_total), 0).label("amount"),
                func.count(Documento.id).label("count"),
            )
            .where(
                Documento.tipo.in_(("SOLICITUD", "INFORME")),
                or_(
                    Documento.notas.like(f"%{DEMO_ANALYTICS_DOC_NOTES}%"),
                    Documento.notas == DEMO_SOLICITUD_NOTES,
                ),
            )
            .group_by(Documento.tipo)
        )
    ).all()

    solicitud_doc_filters = [
        Documento.notas == DEMO_SOLICITUD_NOTES,
        Documento.tipo == "SOLICITUD",
        Documento.estado != "rechazado",
    ]
    if tournament_id:
        try:
            solicitud_doc_filters.append(Documento.torneo_id == UUID(tournament_id))
        except ValueError:
            pass
    if expense_year:
        solicitud_doc_filters.append(Documento.edicion == expense_year)
    solicitud_kpi = (
        await session.execute(
            select(
                func.coalesce(func.sum(Documento.monto_solicitado), 0).label("amount"),
                func.count(Documento.id).label("count"),
            ).where(*solicitud_doc_filters)
        )
    ).one()

    tournaments = (
        await session.execute(
            select(Tournament.id, Tournament.name)
            .where(Tournament.active == True)  # noqa: E712
            .order_by(Tournament.display_order, Tournament.name)
        )
    ).all()

    payload: dict[str, Any] = {
        "ok": True,
        "demo_only": True,
        "demo_origin": DEMO_ORIGIN,
        "test_runtime": _is_test_runtime(),
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "filters": {
            "tournament_id": tournament_id,
            "year": expense_year,
            "edition_year": edition_year,
            "tournament_name": tournament_name,
        },
        "budget_version": (
            {
                "id": budget_version["id"],
                "name": budget_version.get("version_name"),
                "status": budget_version.get("status"),
                "source": budget_version.get("source"),
            }
            if budget_version
            else None
        ),
        "remaining_months": remaining_month_labels,
        "kpis": {
            "total_spent": total_spent,
            "expense_count": expense_count,
            "budget_total": budget_total,
            "budget_remaining_year": _money(budget_total - total_spent),
            "pct_budget_consumed": round((total_spent / budget_total) * 100)
            if budget_total
            else None,
            "partida_count": len(by_partida),
            "fase_count": len([r for r in fase_rows if r.fase_torneo]),
            "cuenta_count": (
                await session.execute(
                    select(func.count(func.distinct(ExpenseReport.cuenta_gastos_id))).where(
                        *filters
                    )
                )
            ).scalar_one(),
            "solicitud_count": int(solicitud_kpi.count or 0),
            "solicitud_amount": _money(solicitud_kpi.amount),
        },
        "by_partida": by_partida,
        "by_fase": [
            {
                "fase": row.fase_torneo or "Sin fase",
                "amount": _money(row.amount),
                "count": int(row.count or 0),
            }
            for row in fase_rows
        ],
        "by_month": [
            {
                "month": (
                    row.month.date().isoformat()
                    if isinstance(row.month, datetime)
                    else str(row.month)
                ),
                "amount": _money(row.amount),
                "count": int(row.count or 0),
            }
            for row in month_rows
        ],
        "by_empleado": [
            {
                "nombre": row.nombre,
                "amount": _money(row.amount),
                "count": int(row.count or 0),
            }
            for row in empleado_rows
        ],
        "by_documento_tipo": [
            {
                "tipo": row.tipo,
                "amount": _money(row.amount),
                "count": int(row.count or 0),
            }
            for row in doc_rows
        ],
        "tournaments": [{"id": str(tid), "name": name} for tid, name in tournaments],
        "presupuestos_url": "/admin/presupuestos",
    }
    return JSONResponse(payload)


@router.post("/api/operaciones/analytics/budget-lines/upsert")
async def operaciones_analytics_budget_upsert(
    payload: BudgetLineUpsertRequest,
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = Depends(get_current_empleado),
) -> JSONResponse:
    """Upsert presupuesto anual y distribución mensual para una partida."""
    _require_budget_mutation(current_empleado)
    budget_version = await _resolve_budget_version(session, payload.edition_year)
    if budget_version is None:
        raise HTTPException(
            status_code=404,
            detail="No hay versión de presupuesto cargada para este año",
        )
    monthly: Optional[dict[int, float]] = None
    if payload.monthly_allocations:
        monthly = {
            int(month): float(amount)
            for month, amount in payload.monthly_allocations.items()
        }
    try:
        line = await upsert_budget_line_for_concept(
            session,
            version_id=budget_version["id"],
            budget_concept_id=payload.budget_concept_id,
            budget_amount=payload.budget_amount,
            actor_empleado_id=str(current_empleado.id),
            monthly_allocations=monthly,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse({"ok": True, "line": _format_budget_line_money(line)})
