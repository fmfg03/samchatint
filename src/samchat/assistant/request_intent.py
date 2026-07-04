from __future__ import annotations

import re
import unicodedata
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from .finance_query_intent import detect_finance_comparison_intent


@dataclass(frozen=True)
class OperationalRequestIntent:
    request_id: str
    domain: str
    intent: str
    confidence: float
    slots: Dict[str, Any]
    missing_fields: List[str]
    raw_text: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def normalize_request_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text or "")
    ascii_text = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", ascii_text.lower()).strip()


def _request_id(text: str) -> str:
    return f"req_{uuid.uuid5(uuid.NAMESPACE_URL, text or '').hex[:16]}"


def _years(text: str) -> List[int]:
    found: List[int] = []
    for match in re.finditer(r"\b(20[0-9]{2})\b", text):
        year = int(match.group(1))
        if year not in found:
            found.append(year)
    return found[:2]


def _period(text: str) -> Optional[str]:
    if "esta semana" in text:
        return "this_week"
    if "este mes" in text:
        return "this_month"
    if "este trimestre" in text:
        return "this_quarter"
    if "este ano" in text or "este año" in text:
        return "this_year"
    return None


def _group_by(text: str) -> Optional[str]:
    if any(token in text for token in ("proveedor", "vendor")):
        return "proveedor"
    if any(token in text for token in ("proyecto", "project")):
        return "proyecto"
    if any(token in text for token in ("torneo", "tournament")):
        return "torneo"
    if any(token in text for token in ("cuenta contable", "cuenta", "account")):
        return "cuenta_contable"
    if any(token in text for token in ("categoria", "category")):
        return "categoria"
    if any(token in text for token in ("concepto", "concept")):
        return "concepto"
    return None


def _base_slots(text: str) -> Dict[str, Any]:
    return {
        "metric": None,
        "years": _years(text),
        "period": _period(text),
        "group_by": _group_by(text),
        "filters": {},
        "output": "table",
    }


def _intent(
    *,
    raw_text: str,
    domain: str,
    intent: str,
    confidence: float,
    slots: Dict[str, Any],
    missing_fields: Optional[List[str]] = None,
) -> OperationalRequestIntent:
    return OperationalRequestIntent(
        request_id=_request_id(raw_text),
        domain=domain,
        intent=intent,
        confidence=confidence,
        slots=slots,
        missing_fields=missing_fields or [],
        raw_text=raw_text or "",
    )


def detect_request_intent(text: str) -> OperationalRequestIntent:
    raw_text = text or ""
    normalized = normalize_request_text(raw_text)
    slots = _base_slots(normalized)

    finance_compare = detect_finance_comparison_intent(raw_text)
    if finance_compare is not None:
        slots.update(
            {
                "metric": finance_compare.metric,
                "years": finance_compare.years,
                "group_by": finance_compare.group_by,
                "comparison": finance_compare.comparison,
            }
        )
        return _intent(
            raw_text=raw_text,
            domain="finance",
            intent="compare",
            confidence=0.95,
            slots=slots,
        )

    has_finance = any(token in normalized for token in ("gasto", "gastos", "finanza"))
    if has_finance:
        slots["metric"] = "gasto"
        if any(token in normalized for token in ("pendiente", "comprobar", "comprobacion")):
            return _intent(
                raw_text=raw_text,
                domain="finance",
                intent="list_pending",
                confidence=0.86,
                slots=slots,
            )
        if any(token in normalized for token in ("reporte", "dame", "cuanto", "gastamos")):
            slots["group_by"] = slots.get("group_by") or "proyecto"
            return _intent(
                raw_text=raw_text,
                domain="finance",
                intent="breakdown",
                confidence=0.84,
                slots=slots,
            )

    if any(token in normalized for token in ("cfdi", "cfdis", "factura", "facturas")):
        intent_name = "list_pending"
        if any(token in normalized for token in ("sin vincular", "sin gasto")):
            intent_name = "list_unlinked"
        if any(token in normalized for token in ("pagar", "pagado", "pago")):
            intent_name = "payment_status"
        return _intent(
            raw_text=raw_text,
            domain="cfdi",
            intent=intent_name,
            confidence=0.9,
            slots={**slots, "metric": "cfdi", "output": "table"},
        )

    if any(
        token in normalized
        for token in ("pago", "pagos", "comprobacion", "comprobaciones", "reembolso", "reembolsos", "saldar")
    ):
        intent_name = "due_soon" if "vence" in normalized or "vencen" in normalized else "list_pending"
        return _intent(
            raw_text=raw_text,
            domain="payments",
            intent=intent_name,
            confidence=0.88,
            slots={**slots, "metric": "payment", "output": "table"},
        )

    if any(
        token in normalized
        for token in ("torneo", "equipos", "equipo", "jugadores", "jugador", "registro")
    ):
        missing: List[str] = []
        if "torneo" not in normalized and "copa telmex" not in normalized:
            missing.append("tournament")
        entity = "team_documents" if "document" in normalized or "equip" in normalized else "tournament"
        return _intent(
            raw_text=raw_text,
            domain="tournament",
            intent="list_pending" if any(token in normalized for token in ("pendiente", "incompleto", "faltan")) else "status",
            confidence=0.86,
            slots={**slots, "metric": entity, "output": "table"},
            missing_fields=missing,
        )

    if any(token in normalized for token in ("direccion", "directivo", "ejecutivo", "atorando")) or (
        "riesgos" in normalized
        and any(token in normalized for token in ("semana", "operacion", "finanzas", "reporte"))
    ):
        return _intent(
            raw_text=raw_text,
            domain="executive",
            intent="summarize",
            confidence=0.84,
            slots={**slots, "metric": "operations_summary", "output": "summary"},
        )

    return _intent(
        raw_text=raw_text,
        domain="unknown",
        intent="unknown",
        confidence=0.0,
        slots=slots,
        missing_fields=["intent"],
    )
