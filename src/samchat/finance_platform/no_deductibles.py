"""Read-only No Deducibles control, derived from linked fiscal evidence."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload


def period_bounds(year: int, month: int) -> tuple[datetime, datetime]:
    """Return the half-open calendar-month interval for expense dates."""
    if month < 1 or month > 12:
        raise ValueError("El mes debe estar entre 1 y 12.")
    start = datetime(year, month, 1)
    end = datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)
    return start, end


def has_linked_fiscal_invoice(row: dict[str, Any]) -> bool:
    """A fiscal invoice counts only when the canonical CFDI record is linked.

    A typed UUID, a payment proof, or a non-deductible receipt is not fiscal
    evidence for this control.  ``cfdi_report_id`` is the established link to
    the imported/attached CFDI record.
    """
    return bool(str(row.get("cfdi_report_id") or "").strip())


def _currency(value: Any) -> str:
    """Normalize the row currency without converting or combining amounts."""
    return str(value or "MXN").strip().upper() or "MXN"


def build_no_deductibles_report(
    rows: list[dict[str, Any]], *, year: int, month: int, tournament_id: str | None
) -> dict[str, Any]:
    """Classify supplied expense rows without persisting a parallel status."""
    classified: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["currency"] = _currency(item.get("currency"))
        item["fiscal_status"] = (
            "deducible" if has_linked_fiscal_invoice(item) else "no_deducible"
        )
        item["fiscal_reason"] = (
            "CFDI fiscal vinculado"
            if item["fiscal_status"] == "deducible"
            else "Sin factura fiscal (CFDI) vinculada"
        )
        classified.append(item)

    non_deductible_rows = [
        row for row in classified if row["fiscal_status"] == "no_deducible"
    ]
    by_currency: dict[str, dict[str, Any]] = {}
    for row in classified:
        currency = row["currency"]
        currency_summary = by_currency.setdefault(
            currency,
            {
                "expense_count": 0,
                "deductible_count": 0,
                "non_deductible_count": 0,
                "total_amount": 0.0,
                "deductible_amount": 0.0,
                "non_deductible_amount": 0.0,
            },
        )
        amount = float(row.get("amount") or 0)
        currency_summary["expense_count"] += 1
        currency_summary["total_amount"] += amount
        if row["fiscal_status"] == "deducible":
            currency_summary["deductible_count"] += 1
            currency_summary["deductible_amount"] += amount
        else:
            currency_summary["non_deductible_count"] += 1
            currency_summary["non_deductible_amount"] += amount
    for currency_summary in by_currency.values():
        for field in (
            "total_amount",
            "deductible_amount",
            "non_deductible_amount",
        ):
            currency_summary[field] = round(currency_summary[field], 2)
        total_amount = currency_summary["total_amount"]
        currency_summary["non_deductible_percent"] = round(
            (
                currency_summary["non_deductible_amount"] / total_amount * 100
                if total_amount
                else 0
            ),
            2,
        )
    return {
        "period": {"year": year, "month": month},
        "tournament_id": tournament_id or "",
        "rows": classified,
        "non_deductible_rows": non_deductible_rows,
        "summary": {
            "expense_count": len(classified),
            "deductible_count": len(classified) - len(non_deductible_rows),
            "non_deductible_count": len(non_deductible_rows),
            "by_currency": dict(sorted(by_currency.items())),
        },
    }


async def list_tournaments_for_no_deductibles(session: AsyncSession) -> list[dict[str, str]]:
    from devnous.gastos.models import Tournament

    result = await session.execute(
        select(Tournament).order_by(Tournament.active.desc(), Tournament.name)
    )
    return [
        {"id": str(tournament.id), "name": str(tournament.name)}
        for tournament in result.scalars().all()
    ]


async def build_no_deductibles_source(
    session: AsyncSession, *, year: int, month: int, tournament_id: str | None = None
) -> dict[str, Any]:
    """Read all active expense lines for an expense-date period and tournament.

    Tournament provenance follows the operational document first, then the
    account and budget context.  Rows with no resolvable tournament remain in
    the unfiltered view so missing governance data cannot be hidden.
    """
    from devnous.gastos.models import BudgetConcept, CuentaDeGastos, Documento, ExpenseReport

    start, end = period_bounds(year, month)
    informe = aliased(Documento)
    solicitud = aliased(Documento)
    documento = aliased(Documento)
    cuenta = aliased(CuentaDeGastos)
    budget_concept = aliased(BudgetConcept)
    scope_tournament_id = func.coalesce(
        informe.torneo_id,
        solicitud.torneo_id,
        documento.torneo_id,
        cuenta.torneo_id,
        budget_concept.tournament_id,
    ).label("scope_tournament_id")

    statement = (
        select(ExpenseReport, scope_tournament_id)
        .outerjoin(informe, ExpenseReport.informe_documento_id == informe.id)
        .outerjoin(solicitud, ExpenseReport.solicitud_documento_id == solicitud.id)
        .outerjoin(documento, ExpenseReport.documento_id == documento.id)
        .outerjoin(cuenta, ExpenseReport.cuenta_gastos_id == cuenta.id)
        .outerjoin(budget_concept, ExpenseReport.budget_concept_id == budget_concept.id)
        .options(
            selectinload(ExpenseReport.empleado),
            selectinload(ExpenseReport.cfdi_report),
            selectinload(ExpenseReport.informe_documento).undefer(Documento.fase),
            selectinload(ExpenseReport.solicitud_documento).undefer(Documento.fase),
            selectinload(ExpenseReport.documento).undefer(Documento.fase),
        )
        .where(
            and_(
                ExpenseReport.estado_gasto != "cancelado",
                ExpenseReport.fecha >= start,
                ExpenseReport.fecha < end,
            )
        )
        .order_by(ExpenseReport.fecha.desc(), ExpenseReport.created_at.desc())
    )
    if tournament_id:
        try:
            statement = statement.where(scope_tournament_id == UUID(tournament_id))
        except ValueError as exc:
            raise ValueError("Torneo inválido.") from exc

    result = await session.execute(statement)
    records = result.all()
    tournament_ids = {str(scope_id) for _, scope_id in records if scope_id}
    tournament_names: dict[str, str] = {}
    if tournament_ids:
        from devnous.gastos.models import Tournament

        tournaments = await session.execute(
            select(Tournament).where(Tournament.id.in_([UUID(value) for value in tournament_ids]))
        )
        tournament_names = {
            str(tournament.id): str(tournament.name)
            for tournament in tournaments.scalars().all()
        }

    rows: list[dict[str, Any]] = []
    for expense, scope_id in records:
        informe_documento = expense.informe_documento
        solicitud_documento = expense.solicitud_documento
        generic_documento = expense.documento
        source_document = informe_documento or solicitud_documento or generic_documento
        source_type = (
            "Informe"
            if informe_documento is not None
            else "Solicitud"
            if solicitud_documento is not None
            else str(getattr(source_document, "tipo", "Gasto"))
        )
        scope_id_text = str(scope_id) if scope_id else ""
        rows.append(
            {
                "id": str(expense.id),
                "reference": expense.numero_referencia or str(expense.id),
                "expense_date": expense.fecha.isoformat() if expense.fecha else "",
                "concept": expense.concepto or "",
                "amount": round(float(expense.gasto_cantidad or 0), 2),
                "currency": _currency(getattr(expense, "currency", None)),
                "employee_name": getattr(expense.empleado, "nombre", None) or "-",
                "source_type": source_type,
                "source_reference": getattr(source_document, "numero_referencia", None) or "-",
                "tournament_id": scope_id_text,
                "tournament_name": tournament_names.get(scope_id_text, "Sin torneo asignado"),
                "phase": getattr(source_document, "fase", None) or expense.fase_torneo or "-",
                "cfdi_report_id": str(expense.cfdi_report_id or ""),
                "cfdi_uuid": getattr(expense.cfdi_report, "cfdi_uuid", None) or "",
                "cfdi_uuid_manual": expense.cfdi_uuid_manual or "",
            }
        )
    return build_no_deductibles_report(
        rows, year=year, month=month, tournament_id=tournament_id
    )
