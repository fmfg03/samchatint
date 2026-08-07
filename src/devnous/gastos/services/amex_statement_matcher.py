"""Deterministic matching for imported AMEX statement charges.

The matcher links a company AMEX charge to the most likely CFDI already known by
SamChat.  It is intentionally conservative: it only returns automatic matches
when date/amount evidence is strong enough, and it classifies restaurant deltas
as tip candidates instead of forcing fiscal equality.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from typing import Optional

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import CFDIReport, ExpenseReport

EXACT_AMOUNT_TOLERANCE = 1.00
TIP_MAX_RATIO = 0.30
DATE_WINDOW_DAYS = 7

_RESTAURANT_TERMS = (
    "alimento",
    "alimentos",
    "comida",
    "consumo",
    "restaurant",
    "restaurante",
    "cafeteria",
    "bar",
    "taqueria",
    "cafe",
    "café",
    "hotel",
)


@dataclass(frozen=True)
class AmexCFDIMatch:
    cfdi: CFDIReport
    confidence: str
    reason: str
    score: float
    amount_delta: float
    inferred_tip: float = 0.0


def normalize_match_text(value: object) -> str:
    text = unicodedata.normalize("NFD", str(value or "").lower())
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def is_food_or_hospitality_charge(description: object) -> bool:
    normalized = normalize_match_text(description)
    return any(term in normalized for term in _RESTAURANT_TERMS)


def _text_similarity(left: object, right: object) -> float:
    a = normalize_match_text(left)
    b = normalize_match_text(right)
    if not a or not b:
        return 0.0
    tokens_a = set(a.split())
    tokens_b = set(b.split())
    overlap = len(tokens_a & tokens_b) / max(len(tokens_a | tokens_b), 1)
    return max(overlap, SequenceMatcher(None, a, b).ratio())


def _date_distance_days(left: Optional[datetime], right: Optional[datetime]) -> int:
    if not left or not right:
        return DATE_WINDOW_DAYS + 1
    return abs((left.date() - right.date()).days)


def _merchant_text(cfdi: CFDIReport) -> str:
    return " ".join(
        str(part or "")
        for part in (
            cfdi.emisor_nombre,
            cfdi.emisor_rfc,
            cfdi.descripcion_concepto_principal,
        )
    )


async def find_amex_cfdi_match(
    session: AsyncSession,
    *,
    charge_description: str,
    charge_amount: float,
    charge_date: datetime,
) -> Optional[AmexCFDIMatch]:
    """Return the strongest automatic CFDI match for one AMEX charge.

    Conservative automatic rules:
    - exact/small amount difference, near date, usable text signal; or
    - food/hospitality charge where AMEX total exceeds CFDI total by a plausible
      tip delta.
    """
    amount = abs(float(charge_amount or 0.0))
    if amount <= 0 or not charge_date:
        return None

    start = charge_date - timedelta(days=DATE_WINDOW_DAYS)
    end = charge_date + timedelta(days=DATE_WINDOW_DAYS + 1)
    lower_tip_bound = max(amount * (1 - TIP_MAX_RATIO), 0.01)

    linked_expense = select(ExpenseReport.id).where(
        and_(
            ExpenseReport.cfdi_report_id == CFDIReport.id,
            ExpenseReport.estado_gasto == "activo",
            or_(
                ExpenseReport.origen.is_(None),
                ExpenseReport.origen != "amex_batch",
            ),
        )
    )

    result = await session.execute(
        select(CFDIReport)
        .where(
            and_(
                CFDIReport.total.isnot(None),
                CFDIReport.fecha >= start,
                CFDIReport.fecha < end,
                or_(
                    CFDIReport.tipo_de_comprobante.is_(None),
                    func.upper(CFDIReport.tipo_de_comprobante) == "I",
                ),
                CFDIReport.total >= lower_tip_bound,
                CFDIReport.total <= amount + EXACT_AMOUNT_TOLERANCE,
                ~linked_expense.exists(),
            )
        )
        .order_by(CFDIReport.fecha.desc())
        .limit(80)
    )
    candidates = list(result.scalars().all())
    if not candidates:
        return None

    food_charge = is_food_or_hospitality_charge(charge_description)
    best: Optional[AmexCFDIMatch] = None
    for cfdi in candidates:
        cfdi_total = float(cfdi.total or 0.0)
        delta = round(amount - cfdi_total, 2)
        abs_delta = abs(delta)
        date_days = _date_distance_days(charge_date, cfdi.fecha)
        text_score = _text_similarity(charge_description, _merchant_text(cfdi))
        date_score = max(0.0, 1.0 - (date_days / max(DATE_WINDOW_DAYS, 1)))

        exact = abs_delta <= EXACT_AMOUNT_TOLERANCE
        tip_candidate = food_charge and delta > EXACT_AMOUNT_TOLERANCE and delta <= amount * TIP_MAX_RATIO
        if not exact and not tip_candidate:
            continue

        # Amount/date carry the match; text helps break ties but is not always
        # reliable because AMEX merchant labels and CFDI legal names differ.
        amount_score = 1.0 if exact else max(0.65, 1.0 - (delta / max(amount, 1.0)))
        score = (amount_score * 0.55) + (date_score * 0.25) + (text_score * 0.20)

        if exact and (date_days <= 3 or text_score >= 0.18):
            match = AmexCFDIMatch(
                cfdi=cfdi,
                confidence="exact",
                reason="Monto AMEX coincide con CFDI y la fecha/texto son compatibles.",
                score=score,
                amount_delta=delta,
            )
        elif tip_candidate and (date_days <= 3 or text_score >= 0.12):
            match = AmexCFDIMatch(
                cfdi=cfdi,
                confidence="tip_candidate",
                reason="CFDI compatible; diferencia AMEX se interpreta como posible propina no deducible.",
                score=score,
                amount_delta=delta,
                inferred_tip=delta,
            )
        else:
            continue

        if best is None or match.score > best.score:
            best = match

    return best
