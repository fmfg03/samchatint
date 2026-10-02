"""Evidence-bound Direction home over canonical, strictly scoped read services.

No financial records, permissions, budgets or bank balances are created here.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text

from samchat.ar.service import build_ar_read_model
from samchat.budgets.executive_facts import SOURCE as FACT_SOURCE
from samchat.budgets.executive_facts import build_executive_facts
from samchat.finance_platform.service import (
    approved_unpaid_documents,
    build_finance_source_snapshot,
)

from . import service
from .report_layouts import report_layouts

SCHEMA = "samchat.direction.home.v1"
TZ = ZoneInfo("America/Mexico_City")
DEFINITIONS = {
    "budget": (
        "Presupuesto autorizado",
        "Líneas de gasto de versión aprobada o congelada; base anual.",
        "budget_amount de líneas expense",
        "Finanzas / Presupuestos",
    ),
    "actual": (
        "Ejercido documental",
        "Gastos activos atribuidos al torneo, en base fiscal canónica y por fecha de gasto; no es caja ni solo partidas presupuestadas.",
        "Σ base fiscal canónica por expense_reports.id",
        "Contabilidad / Finanzas",
    ),
    "committed": (
        "Comprometido documental",
        "Importe pagable de solicitudes creadas en el intervalo; incluye pagadas/cerradas y reembolsos. No se suma al ejercido.",
        "Σ importe pagable canónico por documentos.id",
        "Finanzas",
    ),
    "paid": (
        "Pagado documental",
        "Importe pagable con estado/registro de pago, de solicitudes creadas en el intervalo; no es flujo por fecha efectiva ni caja.",
        "Σ importe pagable de solicitudes con pago documental",
        "Contabilidad / Tesorería",
    ),
    "forecast": (
        "Cierre estimado",
        "Estimación mecánica anual, condicionada a cobertura del ejercido.",
        "forecast.projected_close_total",
        "Finanzas / Dirección",
    ),
    "deviation": (
        "Exceso estimado",
        "Positivo indica cierre por encima del presupuesto.",
        "cierre estimado - presupuesto autorizado",
        "Finanzas",
    ),
    "obligations": (
        "Obligaciones próximas · 30 días",
        "Solicitudes aprobadas o en proceso, sin pago registrado y con fecha programada.",
        "Σ importe pagable de solicitudes elegibles en próximos 30 días",
        "Tesorería",
    ),
    "receivables": (
        "Cobranza pendiente",
        "CFDI atribuibles menos cobros aceptados; las brechas de cobro impiden totalizar.",
        "ar.summary.balance_total, sujeto a coverage",
        "Cobranza / Contabilidad",
    ),
    "overdue": (
        "Cobranza vencida",
        "Requiere vencimientos contractuales validados.",
        "saldo pendiente con vencimiento < corte",
        "Cobranza",
    ),
    "liquidity": (
        "Liquidez disponible",
        "Requiere saldo bancario conciliado y atribución autorizada; flujo neto no es saldo.",
        "sin fuente de saldo disponible acreditada",
        "Tesorería",
    ),
}
BUDGET_SOURCE = "samchat.budgets.service.build_budget_snapshot"


def amount(value: Any) -> Decimal | None:
    """Reject non-finite, malformed and absent money rather than display zero."""
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
        return result.quantize(Decimal("0.01")) if result.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def format_money(value: Any) -> str:
    number = amount(value)
    return "Sin dato" if number is None else f"${number:,.2f} MXN"


def period_bounds(year: int, start: date | None, end: date | None) -> tuple[date, date]:
    first, last = date(year, 1, 1), date(year, 12, 31)
    today = datetime.now(TZ).date()
    start, end = start or first, end or min(max(today, first), last)
    if not first <= start <= end <= last:
        raise ValueError("El periodo debe pertenecer a la edición seleccionada.")
    return start, end


async def resolve_scope(
    session: Any,
    *,
    actor: str,
    superadmin: bool,
    portfolio_id: str | None,
    tournament_id: str | None,
    tournament_ids: list[str] | None = None,
) -> dict:
    """Resolve dropdowns and selection without a global or role fallback."""
    ids = await service.authorized_direction_portfolio_ids(
        session, actor, is_superadmin=superadmin
    )
    if (not ids and not superadmin) or (portfolio_id and portfolio_id not in ids):
        raise service.ClientExecutiveAccessError("Cartera fuera de tu alcance activo.")
    portfolios = (
        (
            await session.execute(
                text("""
        SELECT id::text AS id, label FROM client_executive_portfolios
        WHERE id = ANY(CAST(:ids AS uuid[])) AND active = TRUE ORDER BY label
    """),
                {"ids": ids},
            )
        )
        .mappings()
        .all()
    )
    tournaments = await service._authorized_tournaments(
        session, actor, is_superadmin=superadmin
    )
    accessible_count = len({t["id"] for t in tournaments})
    if portfolio_id:
        rows = (
            (
                await session.execute(
                    text("""
            SELECT tournament_id::text AS id FROM client_executive_portfolio_tournaments
            WHERE portfolio_id = CAST(:id AS uuid) AND active = TRUE
        """),
                    {"id": portfolio_id},
                )
            )
            .mappings()
            .all()
        )
        permitted = {str(row["id"]) for row in rows}
        tournaments = [t for t in tournaments if t["id"] in permitted]
    options = {t["id"]: t for t in tournaments}
    if tournament_id and tournament_ids:
        raise ValueError("Usa un solo selector de torneos.")
    requested = sorted(
        set(tournament_ids or ([tournament_id] if tournament_id else []))
    )
    if any(tid not in options for tid in requested):
        raise service.ClientExecutiveAccessError("Torneo fuera de tu alcance activo.")
    selected = (
        [options[tid] for tid in requested] if requested else list(options.values())
    )
    return {
        "portfolio_ids": [portfolio_id] if portfolio_id else sorted(ids),
        "portfolio_id": portfolio_id,
        "tournament_id": tournament_id,
        "tournament_ids": requested,
        "portfolios": [dict(row) for row in portfolios],
        "tournaments": list(options.values()),
        "selected": selected,
        "accessible_tournament_count": accessible_count,
        "scope_mode": (
            "installation_supervision" if superadmin else "assigned_portfolios"
        ),
    }


def budget_values(
    snapshot: dict, *, start: date, end: date, year: int
) -> tuple[dict, list[str]]:
    """Keep source quality and version approval ahead of plausible totals."""
    version = snapshot.get("version") or {}
    valid = snapshot.get("source") == "budget_db" and version.get("status") in {
        "approved",
        "frozen",
    }
    gaps = list(snapshot.get("executive_quality_gaps") or [])
    if not valid:
        gaps.append("Presupuesto aprobado y alcance no acreditados.")
    summary = snapshot.get("summary") or {}
    values = {
        key: amount(summary.get(field)) if valid else None
        for key, field in {
            "budget": "budget_total",
            "actual": "actual_total",
            "committed": "committed_total",
            "paid": "paid_total",
        }.items()
    }
    if gaps:
        values["actual"] = None
    if "mixed_or_unknown_currency" in gaps or "document_amount_missing" in gaps:
        values.update(committed=None, paid=None)
    # The existing forecast assumes an annual run rate. An arbitrary interval
    # must not silently become an annual forecast or a historical stock.
    today = datetime.now(TZ).date()
    annual_to_today = start == date(year, 1, 1) and end == today and year == today.year
    forecast = snapshot.get("forecast") or {}
    values["forecast"] = (
        amount(forecast.get("projected_close_total"))
        if valid and not gaps and annual_to_today
        else None
    )
    values["deviation"] = (
        values["forecast"] - values["budget"]
        if values["forecast"] is not None and values["budget"] is not None
        else None
    )
    if not annual_to_today:
        gaps.append(
            "El cierre mecánico no se aplica a este intervalo o edición histórica."
        )
    return values, gaps


async def _optional_read(session: Any, loader: Any, **kwargs: Any) -> dict | None:
    """A failed optional PostgreSQL source cannot poison subsequent reads."""
    try:
        async with session.begin_nested():
            return await loader(session, **kwargs)
    except Exception:
        # Do not leak query text, source contacts, connection strings or errors.
        return None


async def payment_values(session: Any, tournament_ids: list[str], today: date) -> dict:
    source = await _optional_read(
        session,
        build_finance_source_snapshot,
        year=today.year,
        month=today.month,
        limit=5000,
        tournament_ids=tournament_ids,
        documents_only=True,
    )
    projected = _project_payment_values(source, today)
    projected["by_tournament"] = {
        tid: _project_payment_values(
            (
                {
                    **source,
                    "documents": [
                        r
                        for r in source.get("documents", [])
                        if r.get("tournament_id") == tid
                    ],
                }
                if source is not None
                else None
            ),
            today,
        )
        for tid in tournament_ids
    }
    return projected


def _project_payment_values(source: dict | None, today: date) -> dict:
    """Project the current stock after one authorized set-scoped source read."""
    if source is None or (source.get("source_status") or {}).get(
        "document_scan_truncated"
    ):
        return {
            "value": None,
            "gaps": ["Fuente de pagos no disponible o lectura incompleta."],
        }
    eligible = approved_unpaid_documents(source)
    due = []
    gaps = []
    for row in eligible:
        record_valid = True
        if row.get("currency") != "MXN":
            record_valid = False
            gaps.append(
                "Hay obligaciones sin moneda MXN acreditada; falta conversión validada."
            )
        if row.get("monto_total") is None and row.get("monto_solicitado") is None:
            record_valid = False
            gaps.append("Una obligación no tiene importe acreditado.")
        scheduled = str(row.get("fecha_pago") or "")[:10]
        if not scheduled:
            gaps.append("Hay obligaciones sin fecha programada.")
            continue
        try:
            due_date = date.fromisoformat(scheduled)
        except ValueError:
            gaps.append("Hay fechas de pago no verificables.")
            continue
        if today <= due_date <= today + timedelta(days=30):
            if not record_valid:
                continue
            # Resolve with the owning domain helper, never sum request + invoice.
            from devnous.gastos.services.document_amount_service import (
                resolve_payable_document_amount,
            )

            value = amount(resolve_payable_document_amount(row))
            if value is None:
                gaps.append("Un reembolso no tiene importe pagable acreditado.")
            else:
                due.append(
                    {
                        "reference": row.get("numero_referencia"),
                        "value": str(value),
                        "date": due_date.isoformat(),
                    }
                )
    return {
        "value": None if gaps else sum((amount(r["value"]) for r in due), Decimal("0")),
        "known_subtotal": str(sum((amount(r["value"]) for r in due), Decimal("0"))),
        "status": "partial" if gaps else "available",
        "gaps": sorted(set(gaps)),
        "evidence": due,
    }


async def receivable_values(
    session: Any, tournament: str, version: str, year: int
) -> dict:
    # An unconfigured issuer allowlist produces [] in the legacy source. It is
    # absence of coverage, not proof that receivables are zero.
    from devnous.gastos.services.cfdi_income_bridge_service import (
        list_configured_rfc_allowlist,
    )

    configured = await _optional_read(session, list_configured_rfc_allowlist)
    if not configured:
        return {"value": None, "gaps": ["Emisores de CxC sin configuración validada."]}
    payload = await _optional_read(
        session,
        build_ar_read_model,
        budget_version_id=version,
        tournament_id=tournament,
        calendar_year=year,
        limit=5000,
        strict_tournament_scope=True,
        ensure_schema=False,
    )
    if payload is None:
        return {"value": None, "gaps": ["Fuente de CxC no disponible en este alcance."]}
    status = payload.get("source_status") or {}
    summary = payload.get("summary") or {}
    if (
        status.get("income_lines_truncated")
        or status.get("candidates_truncated")
        or status.get("currency_gap")
        or status.get("amount_gap")
        or summary.get("collection_gap_count")
        or summary.get("matching_gap_count")
    ):
        return {
            "value": None,
            "gaps": ["CxC conserva brechas de atribución/cobro o lectura incompleta."],
        }
    return {"value": amount(summary.get("balance_total")), "gaps": []}


def aggregate(values: list[Decimal | None]) -> tuple[str | None, str, int]:
    known = [v for v in values if v is not None]
    # Partial coverage has a covered subtotal, never a fabricated full total.
    total = str(sum(known, Decimal("0"))) if known else None
    status = (
        "available"
        if values and len(known) == len(values)
        else "partial" if known else "unavailable"
    )
    return total, status, len(known)


def monthly_execution(
    source: dict, actual: Decimal | None, start: date, end: date
) -> dict:
    """Publish a series only when it reconciles with the visible aggregate."""
    raw = source.get("executive_monthly_actuals")
    if actual is None or not isinstance(raw, list):
        return {
            "status": "unavailable",
            "rows": [],
            "gap": "Serie mensual sin fuente acreditada.",
        }
    values = {month: Decimal("0") for month in range(start.month, end.month + 1)}
    for row in raw:
        month = row.get("month")
        value = amount(row.get("actual_total"))
        if month not in values or value is None:
            return {
                "status": "unavailable",
                "rows": [],
                "gap": "Serie mensual fuera de periodo o incompleta.",
            }
        values[month] += value
    if abs(sum(values.values()) - actual) > Decimal("0.01"):
        return {
            "status": "unavailable",
            "rows": [],
            "gap": "Serie mensual no conciliada con ejercido del tablero.",
        }
    return {
        "status": "available",
        "rows": [
            {"month": month, "value": str(value)} for month, value in values.items()
        ],
        "gap": None,
    }


def concept_evidence(snapshot: dict, values: dict) -> dict:
    """Only expose a complete decomposition that reconciles to the visible facts."""
    raw = snapshot.get("executive_concepts")
    if (
        not isinstance(raw, list)
        or values["actual"] is None
        or values["budget"] is None
    ):
        return {
            "status": "unavailable",
            "rows": [],
            "gap": "Desglose de conceptos sin cobertura conciliada.",
        }
    rows = []
    for item in raw:
        budget, actual = amount(item.get("budget_total")), amount(
            item.get("actual_total")
        )
        if budget is None or actual is None:
            return {
                "status": "unavailable",
                "rows": [],
                "gap": "Desglose con importes incompletos.",
            }
        if budget == 0 and actual == 0:
            continue
        identity = str(item.get("concept_id") or "")
        if not identity or identity == "__unassigned__":
            return {
                "status": "unavailable",
                "rows": [],
                "gap": "Falta identidad canónica de conceptos; no se atribuyen excesos por nombre.",
            }
        label = str(item.get("label") or "Sin concepto")
        rows.append(
            {
                "id": identity,
                "label": label,
                "budget": str(budget),
                "actual": str(actual),
                "excess": str(actual - budget),
            }
        )
    if any(
        abs(sum((amount(r[key]) for r in rows), Decimal("0")) - values[key])
        > Decimal("0.01")
        for key in ("actual", "budget")
    ):
        return {
            "status": "unavailable",
            "rows": [],
            "gap": "El desglose no concilia con presupuesto y ejercido.",
        }
    rows.sort(key=lambda r: (-amount(r["excess"]), r["label"]))
    return {"status": "available", "rows": rows, "gap": None}


async def previous_period_values(
    session: Any, tournament: dict, start: date, end: date, year: int
) -> dict:
    """Compare equal-duration cohorts, never stocks or mismatched annual budgets."""
    previous_end = start - timedelta(days=1)
    previous_start = previous_end - (end - start)
    period = {"start": previous_start.isoformat(), "end": previous_end.isoformat()}
    if previous_start.year != year or previous_end.year != year:
        return {
            "period": period,
            "values": {},
            "gaps": [
                "El periodo anterior atraviesa otra edición; falta una base comparable validada."
            ],
        }
    source = (
        await _optional_read(
            session,
            service._build_direction_budget_snapshot,
            tournament=tournament,
            edition_year=year,
            executive_read=True,
            date_from=previous_start,
            date_to=previous_end,
        )
        or {}
    )
    previous, gaps = budget_values(
        source, start=previous_start, end=previous_end, year=year
    )
    return {
        "period": period,
        "values": {
            k: str(previous[k]) if previous[k] is not None else None
            for k in ("actual", "committed", "paid")
        },
        "gaps": [g for g in gaps if not g.startswith("El cierre mecánico")],
    }


def build_snapshot(
    scope: dict,
    rows: list[dict],
    *,
    year: int,
    start: date,
    end: date,
    observed_at: str,
) -> dict:
    indicators = []
    for key, (label, definition, formula, owner) in DEFINITIONS.items():
        value, status, covered = aggregate(
            [amount((row.get("values") or {}).get(key)) for row in rows]
        )
        gaps = sorted({g for row in rows for g in (row.get("gaps", {}).get(key) or [])})
        if key == "liquidity":
            gaps = [
                "Falta saldo bancario conciliado atribuible al alcance; no se usa flujo neto como saldo."
            ]
        if key == "overdue":
            gaps = [
                "Vencimientos contractuales sin validación; no se presume crédito de cero días."
            ]
        sources = (
            "samchat.finance_platform.service.build_finance_source_snapshot"
            if key == "obligations"
            else (
                "samchat.ar.service.build_ar_read_model"
                if key in {"receivables", "overdue"}
                else (
                    "bank_balance_source_unavailable"
                    if key == "liquidity"
                    else (
                        FACT_SOURCE
                        if key in {"actual", "committed", "paid"}
                        else BUDGET_SOURCE
                    )
                )
            )
        )
        period = (
            f"Edición {year} · anual"
            if key == "budget"
            else (
                f"Edición {year} · proyección anual al corte"
                if key in {"forecast", "deviation"}
                else (
                    "Stock actual · próximos 30 días desde la consulta; independiente del intervalo seleccionado"
                    if key == "obligations"
                    else (
                        "Saldo actual al corte de consulta"
                        if key in {"obligations", "receivables", "overdue", "liquidity"}
                        else f"{start.isoformat()} a {end.isoformat()}"
                    )
                )
            )
        )
        indicators.append(
            {
                "id": key,
                "label": label,
                "definition": definition,
                "formula": formula,
                "value": value,
                "formatted_value": format_money(value),
                "status": status,
                "unit": "MXN",
                "source": sources,
                "period": period,
                "as_of": observed_at,
                "coverage": {"covered": covered, "total": len(rows)},
                "validator": owner,
                "gaps": gaps,
                "comparison": None,
                "validation_status": "pending_business_validation",
            }
        )
    concerns = []
    cut_date = datetime.fromisoformat(observed_at).astimezone(TZ).date()
    for row in rows:
        obligation = amount(row["values"].get("obligations"))
        due_dates = [
            e["date"] for e in row.get("payment_evidence", []) if e.get("date")
        ]
        if obligation is not None and obligation > 0 and due_dates:
            nearest = min(due_dates)
            urgent = date.fromisoformat(nearest) <= cut_date + timedelta(days=7)
            concerns.append(
                {
                    "kind": "obligation",
                    "metric_id": "obligations",
                    "tournament_id": row["id"],
                    "title": f"{row['name']}: obligaciones programadas",
                    "detail": f"{format_money(obligation)} en próximos 30 días. Primera fecha: {nearest}. Validar disponibilidad de caja.",
                    "impact": str(obligation),
                    "rank": 0 if urgent else 1,
                }
            )
        excess = amount(row["values"].get("deviation"))
        if excess is not None and excess > 0:
            concerns.append(
                {
                    "kind": "deviation",
                    "metric_id": "deviation",
                    "tournament_id": row["id"],
                    "title": f"{row['name']}: cierre mecánico sobre presupuesto",
                    "detail": f"Exceso estimado {format_money(excess)}. Validar curva y compromisos.",
                    "impact": str(excess),
                    "rank": 1,
                }
            )
    for metric in indicators:
        if metric["status"] != "available" and metric["id"] in {
            "actual",
            "liquidity",
            "receivables",
        }:
            concerns.append(
                {
                    "kind": "coverage",
                    "metric_id": metric["id"],
                    "title": f"{metric['label']}: cobertura pendiente",
                    "detail": "; ".join(metric["gaps"])
                    or "Faltan fuentes para todo el alcance.",
                    "impact": None,
                    "rank": 2,
                }
            )
    concerns.sort(
        key=lambda c: (
            c["rank"],
            -(amount(c["impact"]) or Decimal("0")),
            c.get("tournament_id", ""),
            c["metric_id"],
        )
    )
    headline = (
        concerns[0]["title"]
        if concerns
        else "No hay desviaciones verificadas; revisa fuentes y cobertura."
    )
    result = {
        "schema": SCHEMA,
        "read_only": True,
        "edition_year": year,
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "as_of": observed_at,
        "scope": {
            **{k: scope[k] for k in ("portfolio_ids", "portfolio_id", "tournament_id")},
            "tournament_ids": scope.get("tournament_ids", []),
        },
        "tournament_ids": [r["id"] for r in rows],
        "indicators": indicators,
        "tournaments": rows,
        "priorities": concerns[:3],
        "headline": headline,
        "source_consistency": "sequential_reads_with_individual_cuts_not_cross_store_atomic",
        "business_acceptance": "pending",
        "reports": report_layouts({"start": start.isoformat(), "end": end.isoformat()}),
    }
    result["snapshot_id"] = hashlib.sha256(
        json.dumps(result, ensure_ascii=False, sort_keys=True, default=str).encode()
    ).hexdigest()
    return result


async def build_home(
    session: Any,
    *,
    actor: str,
    superadmin: bool,
    year: int,
    portfolio_id: str | None = None,
    tournament_id: str | None = None,
    tournament_ids: list[str] | None = None,
    start: date | None = None,
    end: date | None = None,
    source_access: dict[str, bool] | None = None,
) -> tuple[dict, dict]:
    start, end = period_bounds(year, start, end)
    source_access = source_access or {"budget": True, "finance": True}
    scope = await resolve_scope(
        session,
        actor=actor,
        superadmin=superadmin,
        portfolio_id=portfolio_id,
        tournament_id=tournament_id,
        tournament_ids=tournament_ids,
    )
    rows = []
    today = datetime.now(TZ).date()
    selected_ids = [t["id"] for t in scope["selected"]]
    facts = (
        await _optional_read(
            session,
            build_executive_facts,
            tournament_ids=selected_ids,
            start=start,
            end=end,
        )
        if source_access["finance"] and source_access["budget"]
        else None
    ) or {}
    previous_end = start - timedelta(days=1)
    previous_start = previous_end - (end - start)
    previous_facts = (
        await _optional_read(
            session,
            build_executive_facts,
            tournament_ids=selected_ids,
            start=previous_start,
            end=previous_end,
        )
        if previous_start.year == year
        and source_access["finance"]
        and source_access["budget"]
        else None
    ) or {}
    payment_batch = (
        await payment_values(session, selected_ids, today)
        if source_access["finance"]
        else {}
    )
    for tournament in scope["selected"]:
        snapshot = (
            await _optional_read(
                session,
                service._build_direction_budget_snapshot,
                tournament=tournament,
                edition_year=year,
                executive_read=True,
                date_from=start,
                date_to=end,
            )
            if source_access["budget"]
            else None
        ) or {}
        values, budget_gaps = budget_values(snapshot, start=start, end=end, year=year)
        factual = (facts.get("by_tournament") or {}).get(tournament["id"], {})
        factual_values = factual.get("values") or {}
        # A forecast calibrated on budget-classified expenses cannot describe
        # the broader documentary population unless both bases reconcile.
        if amount(factual_values.get("actual")) != values["actual"]:
            values.update(forecast=None, deviation=None)
            budget_gaps.append(
                "La base documental no concilia con la base de la proyección presupuestal."
            )
        for key in ("actual", "committed", "paid"):
            values[key] = amount(factual_values.get(key))
        concepts = concept_evidence(snapshot, values)
        prior = (previous_facts.get("by_tournament") or {}).get(tournament["id"], {})
        previous = {
            "values": prior.get("values", {}),
            "period": {
                "start": previous_start.isoformat(),
                "end": previous_end.isoformat(),
            },
            "gaps": (
                sorted({g for gs in prior.get("gaps", {}).values() for g in gs})
                if prior
                else [
                    "Periodo anterior sin fuente documental acreditada en esta edición."
                ]
            ),
        }
        payments = (payment_batch.get("by_tournament") or {}).get(
            tournament["id"],
            {
                "value": None,
                "gaps": ["Fuente financiera no disponible o no autorizada."],
            },
        )
        version = str((snapshot.get("version") or {}).get("id") or "")
        ar = (
            await receivable_values(session, tournament["id"], version, year)
            if version and source_access["finance"]
            else {
                "value": None,
                "gaps": ["Fuente de CxC no autorizada o sin versión acreditada."],
            }
        )
        values.update(
            obligations=payments["value"],
            receivables=ar["value"],
            overdue=None,
            liquidity=None,
        )
        try:
            dossier = await service._build_operational_dossier(
                tournament, edition_year=year
            )
        except Exception:
            dossier = {}
        operations = (
            dossier.get("summary")
            if dossier.get("source_status") == "available"
            else None
        )
        rows.append(
            {
                "id": tournament["id"],
                "name": tournament["name"],
                "version_id": version or None,
                "documentary_evidence": {
                    **factual,
                    "source": FACT_SOURCE,
                    "as_of": facts.get("as_of"),
                },
                "concepts": concepts,
                "previous_period": previous,
                "forecast_method": {
                    k: (snapshot.get("forecast") or {}).get(k)
                    for k in ("elapsed_days", "total_days", "as_of_date")
                },
                "values": {
                    k: str(v) if v is not None else None for k, v in values.items()
                },
                "as_of": datetime.now(timezone.utc).isoformat(),
                "gaps": {
                    **{
                        key: budget_gaps or ["Importe presupuestal no acreditado."]
                        for key in (
                            "budget",
                            "actual",
                            "committed",
                            "paid",
                            "forecast",
                            "deviation",
                        )
                        if values[key] is None
                    },
                    "obligations": payments["gaps"],
                    "receivables": ar["gaps"],
                    **{
                        key: (factual.get("gaps") or {}).get(
                            key,
                            [
                                "Fuente documental independiente no disponible o no autorizada."
                            ],
                        )
                        for key in ("actual", "committed", "paid")
                    },
                },
                "operations": {
                    "teams": (operations or {}).get("teams_count"),
                    "players": (operations or {}).get("players_count"),
                    "period": f"Edición {year}; sin filtro histórico por fecha",
                    "progress_percent": None,
                },
                "payment_evidence": payments.get("evidence", []),
                "monthly_execution": monthly_execution(
                    factual, values["actual"], start, end
                ),
            }
        )
    result = build_snapshot(
        scope,
        rows,
        year=year,
        start=start,
        end=end,
        observed_at=datetime.now(timezone.utc).isoformat(),
    )
    result["source_access"] = source_access
    result.pop("snapshot_id")
    result["snapshot_id"] = hashlib.sha256(
        json.dumps(result, ensure_ascii=False, sort_keys=True, default=str).encode()
    ).hexdigest()
    return result, scope
