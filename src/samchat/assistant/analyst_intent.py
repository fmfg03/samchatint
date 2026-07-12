from __future__ import annotations

import re
import unicodedata
import uuid
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

from .document_conversation import parse_document_confirmation_command
from .request_intent import OperationalRequestIntent, detect_request_intent


DOCUMENT_CONTEXT_TOKENS = (
    "contrato",
    "documento",
    "texto",
    "sow",
    "propuesta",
    "balanza",
    "contexto",
    "extracto",
    "fragmento",
)

WRITE_LIKE_TOKENS = (
    "registra",
    "vincula",
    "aprueba",
    "ejecuta",
    "crea",
    "actualiza",
    "borra",
    "elimina",
)

REPORT_OPERATION_TOKENS = (
    "reporte",
    "operacion",
    "operación",
    "finanzas",
    "semana",
    "mes",
    "direccion",
    "dirección",
)


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
    conflict_resolution: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AnalystConflictResolution:
    selected_route: str
    reason: str
    operational_route_hint: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def normalize_analyst_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text or "")
    ascii_text = "".join(
        ch for ch in normalized if not unicodedata.combining(ch)
    )
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
    conflict_resolution: Optional[AnalystConflictResolution] = None,
) -> AnalystIntent:
    resolution = conflict_resolution or AnalystConflictResolution(
        selected_route="analyst",
        reason="analyst_intent_match",
        operational_route_hint=operational_route_hint,
    )
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
        conflict_resolution=resolution.to_dict(),
    )


def _has_document_context(normalized: str) -> bool:
    return any(token in normalized for token in DOCUMENT_CONTEXT_TOKENS)


def _has_write_like_action(normalized: str) -> bool:
    return any(token in normalized for token in WRITE_LIKE_TOKENS)


def _has_report_operation_context(normalized: str) -> bool:
    return any(token in normalized for token in REPORT_OPERATION_TOKENS)


def resolve_analyst_conflict(
    raw_text: str,
    operational: OperationalRequestIntent,
) -> AnalystConflictResolution:
    normalized = normalize_analyst_text(raw_text)
    if parse_document_confirmation_command(raw_text) is not None:
        return AnalystConflictResolution(
            selected_route="document_confirmation",
            reason="document_confirmation_command",
            operational_route_hint="document_confirmation",
        )
    if _has_write_like_action(normalized):
        return AnalystConflictResolution(
            selected_route="operational",
            reason="write_like_action",
            operational_route_hint="write_like_action",
        )
    if operational.domain in {"finance", "cfdi", "payments", "tournament"}:
        return AnalystConflictResolution(
            selected_route="operational",
            reason="operational_domain",
            operational_route_hint=(
                f"{operational.domain}.{operational.intent}"
            ),
        )
    if operational.domain == "executive":
        if _has_document_context(normalized):
            return AnalystConflictResolution(
                selected_route="analyst",
                reason="document_context_analysis",
                operational_route_hint=None,
            )
        return AnalystConflictResolution(
            selected_route="operational",
            reason="executive_or_report_request",
            operational_route_hint="executive.summarize",
        )
    if _has_document_context(normalized):
        return AnalystConflictResolution(
            selected_route="analyst",
            reason="document_context_analysis",
            operational_route_hint=None,
        )
    if _has_report_operation_context(normalized):
        return AnalystConflictResolution(
            selected_route="unknown",
            reason="no_supported_intent",
            operational_route_hint=None,
        )
    return AnalystConflictResolution(
        selected_route="analyst",
        reason="analyst_intent_match",
        operational_route_hint=None,
    )


def detect_analyst_intent(text: str) -> Optional[AnalystIntent]:
    raw_text = text or ""
    normalized = normalize_analyst_text(raw_text)
    if not normalized:
        return None

    operational = detect_request_intent(raw_text)
    conflict = resolve_analyst_conflict(raw_text, operational)

    if conflict.selected_route == "document_confirmation":
        return _intent(
            raw_text=raw_text,
            analyst_intent="unknown",
            confidence=0.0,
            context_requirements=[],
            requires_operational_route=True,
            operational_route_hint=conflict.operational_route_hint,
            conflict_resolution=conflict,
        )

    if conflict.selected_route == "operational":
        return _intent(
            raw_text=raw_text,
            analyst_intent="unknown",
            confidence=0.0,
            context_requirements=[],
            requires_operational_route=True,
            operational_route_hint=conflict.operational_route_hint,
            conflict_resolution=conflict,
        )

    if conflict.selected_route == "unknown":
        return None

    if _has_write_like_action(normalized):
        return _intent(
            raw_text=raw_text,
            analyst_intent="unknown",
            confidence=0.0,
            context_requirements=[],
            requires_operational_route=True,
            operational_route_hint="write_like_action",
            conflict_resolution=conflict,
        )

    if any(
        token in normalized
        for token in ("explicame", "explica", "que implica")
    ):
        return _intent(
            raw_text=raw_text,
            analyst_intent="explain",
            confidence=0.86,
            context_requirements=["uploaded_document"],
            conflict_resolution=conflict,
        )

    if any(
        token in normalized
        for token in ("riesgo", "riesgos", "red flags")
    ):
        return _intent(
            raw_text=raw_text,
            analyst_intent="risk_review",
            confidence=0.86,
            context_requirements=["uploaded_document"],
            conflict_resolution=conflict,
        )

    if any(
        token in normalized
        for token in (
            "compara estos",
            "compara esta",
            "compara este",
            "contra el sow",
            "contra esta",
            "cambio entre",
            "cambio",
        )
    ):
        return _intent(
            raw_text=raw_text,
            analyst_intent="compare",
            confidence=0.84,
            context_requirements=["uploaded_document"],
            conflict_resolution=conflict,
        )

    if any(
        token in normalized
        for token in ("resume", "resumen", "conclusiones")
    ):
        return _intent(
            raw_text=raw_text,
            analyst_intent="summarize",
            confidence=0.82,
            context_requirements=["uploaded_document"],
            conflict_resolution=conflict,
        )

    if "preguntas" in normalized and any(
        token in normalized for token in ("cliente", "hacerle", "hacer")
    ):
        return _intent(
            raw_text=raw_text,
            analyst_intent="questions",
            confidence=0.82,
            context_requirements=["conversation"],
            conflict_resolution=conflict,
        )

    if any(
        token in normalized
        for token in (
            "proximos pasos",
            "proximos",
            "siguiente",
            "cerrar este proyecto",
            "falta para cerrar",
        )
    ):
        return _intent(
            raw_text=raw_text,
            analyst_intent="next_steps",
            confidence=0.82,
            context_requirements=["conversation"],
            conflict_resolution=conflict,
        )

    return None
