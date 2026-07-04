from __future__ import annotations

import re
import unicodedata
import uuid
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

from .document_conversation import parse_document_confirmation_command
from .request_intent import detect_request_intent


@dataclass(frozen=True)
class AnalystIntent:
    request_id: str
    mode: str
    analyst_intent: str
    confidence: float
    requires_operational_route: bool
    operational_route_hint: Optional[str]
    requires_provider: bool
    context_requirements: List[str]
    missing_context: List[str]
    safety: Dict[str, Any]
    raw_text: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def normalize_analyst_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text or "")
    ascii_text = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", ascii_text.lower()).strip()


def _request_id(text: str) -> str:
    return f"analyst_{uuid.uuid5(uuid.NAMESPACE_URL, text or '').hex[:16]}"


def _intent(
    *,
    raw_text: str,
    analyst_intent: str,
    confidence: float,
    context_requirements: List[str],
    requires_provider: bool = False,
    requires_operational_route: bool = False,
    operational_route_hint: Optional[str] = None,
) -> AnalystIntent:
    return AnalystIntent(
        request_id=_request_id(raw_text),
        mode="analyst",
        analyst_intent=analyst_intent,
        confidence=confidence,
        requires_operational_route=requires_operational_route,
        operational_route_hint=operational_route_hint,
        requires_provider=requires_provider,
        context_requirements=context_requirements,
        missing_context=list(context_requirements),
        safety={
            "read_only": True,
            "writes_allowed": False,
            "provider_allowed": requires_provider,
            "must_cite_context": True,
            "fail_closed": True,
        },
        raw_text=raw_text or "",
    )


def detect_analyst_intent(text: str) -> Optional[AnalystIntent]:
    raw_text = text or ""
    normalized = normalize_analyst_text(raw_text)
    if not normalized:
        return None

    if parse_document_confirmation_command(raw_text) is not None:
        return _intent(
            raw_text=raw_text,
            analyst_intent="unknown",
            confidence=0.0,
            context_requirements=[],
            requires_operational_route=True,
            operational_route_hint="document_confirmation",
        )

    operational = detect_request_intent(raw_text)
    if operational.domain != "unknown":
        return _intent(
            raw_text=raw_text,
            analyst_intent="unknown",
            confidence=0.0,
            context_requirements=[],
            requires_operational_route=True,
            operational_route_hint=f"{operational.domain}.{operational.intent}",
        )

    if any(token in normalized for token in ("registra", "vincula", "aprueba", "ejecuta")):
        return _intent(
            raw_text=raw_text,
            analyst_intent="unknown",
            confidence=0.0,
            context_requirements=[],
            requires_operational_route=True,
            operational_route_hint="write_like_action",
        )

    if any(token in normalized for token in ("explicame", "explica", "que implica")):
        return _intent(
            raw_text=raw_text,
            analyst_intent="explain",
            confidence=0.86,
            context_requirements=["uploaded_document"],
        )

    if any(token in normalized for token in ("riesgo", "riesgos", "red flags")):
        return _intent(
            raw_text=raw_text,
            analyst_intent="risk_review",
            confidence=0.86,
            context_requirements=["uploaded_document"],
        )

    if any(token in normalized for token in ("compara estos", "compara esta", "contra el sow", "cambio entre", "cambio")):
        return _intent(
            raw_text=raw_text,
            analyst_intent="compare",
            confidence=0.84,
            context_requirements=["uploaded_document"],
        )

    if any(token in normalized for token in ("resume", "resumen", "conclusiones")):
        return _intent(
            raw_text=raw_text,
            analyst_intent="summarize",
            confidence=0.82,
            context_requirements=["uploaded_document"],
        )

    if "preguntas" in normalized and any(token in normalized for token in ("cliente", "hacerle", "hacer")):
        return _intent(
            raw_text=raw_text,
            analyst_intent="questions",
            confidence=0.82,
            context_requirements=["conversation"],
        )

    if any(token in normalized for token in ("proximos pasos", "proximos", "siguiente", "cerrar este proyecto", "falta para cerrar")):
        return _intent(
            raw_text=raw_text,
            analyst_intent="next_steps",
            confidence=0.82,
            context_requirements=["conversation"],
        )

    return None
