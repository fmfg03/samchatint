"""Automatic AMEX charge ↔ CFDI matching suggestions.

The service is intentionally conservative: it produces explainable candidates and
lets Finanzas apply a specific suggestion. It does not create accounting entries.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from difflib import SequenceMatcher
from typing import Iterable, Optional

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import CFDIReport, ExpenseReport


@dataclass(frozen=True)
class AmexCFDIMatchSuggestion:
    expense_id: str
    cfdi_report_id: str
    cfdi_uuid: str
    emisor_nombre: str
    emisor_rfc: str
    cfdi_fecha: str
    cfdi_total: float
    amount_delta: float
    date_delta_days: int
    score: int
    confidence: str
    reason: str


@dataclass(frozen=True)
class PaseMonthlyCFDIMatchSuggestion:
    expense_ids: tuple[str, ...]
    cfdi_report_id: str
    cfdi_uuid: str
    emisor_nombre: str
    emisor_rfc: str
    cfdi_fecha: str
    cfdi_total: float
    amex_total: float
    amount_delta: float
    charge_count: int
    score: int
    confidence: str
    reason: str


def _to_float(value: object) -> float:
    try:
        return round(float(value or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _norm(value: object) -> str:
    return " ".join(str(value or "").upper().split())


def _is_restaurant_concept(concepto: Optional[str]) -> bool:
    text = _norm(concepto)
    return any(
        token in text
        for token in (
            "RESTAURANTE",
            "CAFETERIA",
            "CAFÉ",
            "CAFE",
            "ALIMENTO",
            "COMIDA",
            "CONSUMO",
        )
    )


PASE_EXPENSE_TOKENS = ("PASE", "CASETA", "CASETAS", "PEAJE", "TELEPEAJE", "TAG")
PASE_CFDI_TOKENS = ("PASE", "CASETA", "CASETAS", "PEAJE", "TELEPEAJE", "TAG")


def is_pase_expense(expense: ExpenseReport) -> bool:
    text = _norm(
        " ".join(
            str(v or "")
            for v in (
                getattr(expense, "concepto", ""),
                getattr(expense, "numero_factura", ""),
                getattr(expense, "metodo_pago", ""),
            )
        )
    )
    return any(token in text for token in PASE_EXPENSE_TOKENS)


def is_pase_cfdi(cfdi: CFDIReport) -> bool:
    text = _norm(
        " ".join(
            str(v or "")
            for v in (
                getattr(cfdi, "emisor_nombre", ""),
                getattr(cfdi, "emisor_rfc", ""),
                getattr(cfdi, "descripcion_concepto_principal", ""),
            )
        )
    )
    return any(token in text for token in PASE_CFDI_TOKENS)


def _tip_tolerance(expense: ExpenseReport) -> float:
    amount = _to_float(getattr(expense, "gasto_cantidad", 0))
    if _is_restaurant_concept(getattr(expense, "concepto", None)):
        return max(30.0, round(amount * 0.18, 2))
    return 1.0


def _text_similarity(expense: ExpenseReport, cfdi: CFDIReport) -> float:
    left = _norm(getattr(expense, "concepto", ""))
    right = _norm(
        " ".join(
            str(v or "")
            for v in (
                getattr(cfdi, "emisor_nombre", ""),
                getattr(cfdi, "emisor_rfc", ""),
                getattr(cfdi, "descripcion_concepto_principal", ""),
            )
        )
    )
    if not left or not right:
        return 0.0
    best = SequenceMatcher(None, left, right).ratio()
    for token in left.split():
        if len(token) >= 4 and token in right:
            best = max(best, 0.72)
    return best


def score_amex_cfdi_candidate(
    expense: ExpenseReport, cfdi: CFDIReport
) -> Optional[AmexCFDIMatchSuggestion]:
    """Return a candidate only when the amount/date evidence is plausible."""
    amount = _to_float(getattr(expense, "gasto_cantidad", 0))
    total = _to_float(getattr(cfdi, "total", 0))
    if amount <= 0 or total <= 0:
        return None

    delta = round(amount - total, 2)
    abs_delta = abs(delta)
    tolerance = _tip_tolerance(expense)
    amount_points = 0
    amount_reason = ""
    if abs_delta <= 1.0:
        amount_points = 60
        amount_reason = "monto exacto"
    elif 0 < delta <= tolerance:
        amount_points = 45
        amount_reason = "CFDI menor; diferencia compatible con propina"
    else:
        return None

    exp_date = getattr(expense, "fecha", None)
    cfdi_date = getattr(cfdi, "fecha", None) or getattr(cfdi, "fecha_timbrado", None)
    if exp_date and cfdi_date:
        days = abs((exp_date.date() - cfdi_date.date()).days)
    else:
        days = 99

    if days == 0:
        date_points = 25
        date_reason = "misma fecha"
    elif days <= 3:
        date_points = 18
        date_reason = f"fecha cercana ({days} días)"
    elif days <= 10:
        date_points = 8
        date_reason = f"fecha dentro de ventana ({days} días)"
    else:
        return None

    sim = _text_similarity(expense, cfdi)
    text_points = 15 if sim >= 0.70 else 8 if sim >= 0.45 else 0
    score = min(100, amount_points + date_points + text_points)
    confidence = "Alta" if score >= 85 else "Media" if score >= 70 else "Baja"
    if score < 70:
        return None

    cfdi_uuid = str(getattr(cfdi, "cfdi_uuid", "") or "")
    return AmexCFDIMatchSuggestion(
        expense_id=str(getattr(expense, "id", "")),
        cfdi_report_id=str(getattr(cfdi, "id", "")),
        cfdi_uuid=cfdi_uuid,
        emisor_nombre=str(getattr(cfdi, "emisor_nombre", "") or "—"),
        emisor_rfc=str(getattr(cfdi, "emisor_rfc", "") or "—"),
        cfdi_fecha=(cfdi_date.date().isoformat() if cfdi_date else "—"),
        cfdi_total=total,
        amount_delta=delta,
        date_delta_days=days,
        score=score,
        confidence=confidence,
        reason="; ".join(
            [amount_reason, date_reason] + (["texto compatible"] if text_points else [])
        ),
    )


def rank_amex_cfdi_candidates(
    expense: ExpenseReport,
    cfdis: Iterable[CFDIReport],
    *,
    limit: int = 3,
) -> list[AmexCFDIMatchSuggestion]:
    suggestions = [
        s for cfdi in cfdis if (s := score_amex_cfdi_candidate(expense, cfdi))
    ]
    suggestions.sort(
        key=lambda s: (-s.score, abs(s.amount_delta), s.date_delta_days, s.cfdi_fecha)
    )
    return suggestions[:limit]


async def suggest_amex_cfdi_matches(
    session: AsyncSession,
    expense: ExpenseReport,
    *,
    day_window: int = 10,
    limit: int = 3,
) -> list[AmexCFDIMatchSuggestion]:
    if getattr(expense, "cfdi_report_id", None):
        return []
    if getattr(expense, "origen", None) != "amex_batch":
        return []
    amount = _to_float(getattr(expense, "gasto_cantidad", 0))
    exp_date = getattr(expense, "fecha", None)
    if amount <= 0 or not exp_date:
        return []

    lower_total = max(0.01, round(amount - _tip_tolerance(expense), 2))
    upper_total = round(amount + 1.0, 2)
    start_dt = exp_date - timedelta(days=day_window)
    end_dt = exp_date + timedelta(days=day_window + 1)

    result = await session.execute(
        select(CFDIReport)
        .where(
            and_(
                CFDIReport.cfdi_uuid.isnot(None),
                CFDIReport.fecha >= start_dt,
                CFDIReport.fecha < end_dt,
                CFDIReport.total >= lower_total,
                CFDIReport.total <= upper_total,
                CFDIReport.tipo_de_comprobante == "I",
            )
        )
        .order_by(CFDIReport.fecha.asc())
        .limit(50)
    )
    return rank_amex_cfdi_candidates(expense, result.scalars().all(), limit=limit)


def score_pase_monthly_cfdi_candidate(
    expenses: Iterable[ExpenseReport], cfdi: CFDIReport
) -> Optional[PaseMonthlyCFDIMatchSuggestion]:
    pase_expenses = [exp for exp in expenses if is_pase_expense(exp)]
    if not pase_expenses or not is_pase_cfdi(cfdi):
        return None

    amex_total = round(
        sum(_to_float(getattr(exp, "gasto_cantidad", 0)) for exp in pase_expenses), 2
    )
    cfdi_total = _to_float(getattr(cfdi, "total", 0))
    if amex_total <= 0 or cfdi_total <= 0:
        return None

    delta = round(amex_total - cfdi_total, 2)
    if abs(delta) <= 1.0:
        amount_points = 70
        amount_reason = "suma mensual AMEX cuadra con CFDI PASE"
    else:
        return None

    cfdi_date = getattr(cfdi, "fecha", None) or getattr(cfdi, "fecha_timbrado", None)
    expense_months = {
        (exp.fecha.year, exp.fecha.month)
        for exp in pase_expenses
        if getattr(exp, "fecha", None)
    }
    date_points = 15
    date_reason = "CFDI dentro del mes de cargos"
    if cfdi_date and (cfdi_date.year, cfdi_date.month) in expense_months:
        date_points = 25
    elif cfdi_date:
        date_reason = "CFDI cercano al cierre mensual"

    score = min(100, amount_points + date_points)
    confidence = "Alta" if score >= 90 else "Media"
    return PaseMonthlyCFDIMatchSuggestion(
        expense_ids=tuple(str(getattr(exp, "id", "")) for exp in pase_expenses),
        cfdi_report_id=str(getattr(cfdi, "id", "")),
        cfdi_uuid=str(getattr(cfdi, "cfdi_uuid", "") or ""),
        emisor_nombre=str(getattr(cfdi, "emisor_nombre", "") or "—"),
        emisor_rfc=str(getattr(cfdi, "emisor_rfc", "") or "—"),
        cfdi_fecha=(cfdi_date.date().isoformat() if cfdi_date else "—"),
        cfdi_total=cfdi_total,
        amex_total=amex_total,
        amount_delta=delta,
        charge_count=len(pase_expenses),
        score=score,
        confidence=confidence,
        reason="; ".join([amount_reason, date_reason]),
    )


def rank_pase_monthly_cfdi_candidates(
    expenses: Iterable[ExpenseReport],
    cfdis: Iterable[CFDIReport],
    *,
    limit: int = 3,
) -> list[PaseMonthlyCFDIMatchSuggestion]:
    suggestions = [
        s for cfdi in cfdis if (s := score_pase_monthly_cfdi_candidate(expenses, cfdi))
    ]
    suggestions.sort(key=lambda s: (-s.score, abs(s.amount_delta), s.cfdi_fecha))
    return suggestions[:limit]


async def suggest_pase_monthly_cfdi_matches(
    session: AsyncSession,
    expenses: Iterable[ExpenseReport],
    *,
    start_dt,
    end_dt,
    limit: int = 3,
) -> list[PaseMonthlyCFDIMatchSuggestion]:
    pase_expenses = [
        exp
        for exp in expenses
        if not getattr(exp, "cfdi_report_id", None)
        and getattr(exp, "origen", None) == "amex_batch"
        and is_pase_expense(exp)
    ]
    if not pase_expenses:
        return []
    amex_total = round(
        sum(_to_float(getattr(exp, "gasto_cantidad", 0)) for exp in pase_expenses), 2
    )
    lower_total = max(0.01, round(amex_total - 1.0, 2))
    upper_total = round(amex_total + 1.0, 2)

    result = await session.execute(
        select(CFDIReport)
        .where(
            and_(
                CFDIReport.cfdi_uuid.isnot(None),
                CFDIReport.fecha >= start_dt - timedelta(days=3),
                CFDIReport.fecha < end_dt + timedelta(days=10),
                CFDIReport.total >= lower_total,
                CFDIReport.total <= upper_total,
                CFDIReport.tipo_de_comprobante == "I",
            )
        )
        .order_by(CFDIReport.fecha.asc())
        .limit(50)
    )
    return rank_pase_monthly_cfdi_candidates(
        pase_expenses, result.scalars().all(), limit=limit
    )


async def validate_pase_monthly_cfdi_suggestion(
    session: AsyncSession,
    expenses: Iterable[ExpenseReport],
    cfdi_report_id: object,
) -> Optional[PaseMonthlyCFDIMatchSuggestion]:
    result = await session.execute(
        select(CFDIReport).where(CFDIReport.id == cfdi_report_id)
    )
    cfdi = result.scalar_one_or_none()
    if not cfdi:
        return None
    suggestion = score_pase_monthly_cfdi_candidate(expenses, cfdi)
    if not suggestion or suggestion.score < 90:
        return None
    return suggestion


async def validate_amex_cfdi_suggestion(
    session: AsyncSession,
    expense: ExpenseReport,
    cfdi_report_id: object,
) -> Optional[AmexCFDIMatchSuggestion]:
    result = await session.execute(
        select(CFDIReport).where(CFDIReport.id == cfdi_report_id)
    )
    cfdi = result.scalar_one_or_none()
    if not cfdi:
        return None
    suggestion = score_amex_cfdi_candidate(expense, cfdi)
    if not suggestion or suggestion.score < 70:
        return None
    return suggestion
