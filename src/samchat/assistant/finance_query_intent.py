from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class FinanceComparisonIntent:
    metric: str
    years: List[int]
    group_by: str
    comparison: str
    raw_text: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text or "")
    ascii_text = "".join(
        ch for ch in normalized if not unicodedata.combining(ch)
    )
    return re.sub(r"\s+", " ", ascii_text.lower()).strip()


def _extract_years(text: str) -> List[int]:
    seen: List[int] = []
    for match in re.finditer(r"\b(20[0-9]{2})\b", text):
        year = int(match.group(1))
        if year not in seen:
            seen.append(year)
    return seen[:2]


def _detect_group_by(text: str) -> str:
    if any(
        token in text for token in ("cuenta contable", "account", "cuenta")
    ):
        return "account"
    if any(token in text for token in ("categoria", "category")):
        return "category"
    if any(token in text for token in ("concepto", "concept")):
        return "concepto"
    return "concepto"


def detect_finance_comparison_intent(
    text: str,
) -> Optional[FinanceComparisonIntent]:
    normalized = _normalize_text(text)
    if not normalized:
        return None

    has_finance_metric = any(
        token in normalized
        for token in ("gasto", "gastos", "expense", "expenses")
    )
    has_comparison = any(
        token in normalized
        for token in ("compara", "comparar", "contra", " vs ", "variacion")
    )
    years = _extract_years(normalized)
    if not (has_finance_metric and has_comparison and len(years) == 2):
        return None

    return FinanceComparisonIntent(
        metric="gasto",
        years=years,
        group_by=_detect_group_by(normalized),
        comparison="year_over_year",
        raw_text=text or "",
    )
