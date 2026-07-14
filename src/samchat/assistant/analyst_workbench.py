from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional

from .analyst_intent import AnalystIntent
from .document_conversation import extract_document_intake_result_from_text


AnalystProviderFn = Callable[
    [AnalystIntent, List["AnalystEvidence"]],
    Awaitable[str],
]

MAX_ANALYST_EVIDENCE = 6
MIN_INLINE_CONTEXT_CHARS = 40
INLINE_CONTEXT_LIMIT = 500
LOW_RELEVANCE_SCORE = 55
ANSWER_CONTRACT_VERSION = "analyst_answer_contract_v1"

SOURCE_PRIORITY = {
    "inline_context": 100,
    "uploaded_file": 85,
    "document_intake": 85,
    "report_result": 70,
    "conversation": 40,
}

GENERIC_LABELS = {
    "contexto inline",
    "contexto de conversación",
    "reporte previo",
    "documento",
    "",
}

ROUTE_SIGNAL_TOKENS = {
    "cfdi.list_pending": (
        "cfdi",
        "cfdis",
        "factura",
        "facturas",
    ),
    "payments.list_pending": (
        "pago",
        "pagos",
        "reembolso",
        "reembolsos",
        "saldar",
    ),
    "finance.breakdown": (
        "presupuesto",
        "presupuestos",
        "gasto",
        "gastos",
        "finanza",
        "finanzas",
    ),
}

ROUTE_LABELS = {
    "cfdi.list_pending": "Revisar CFDI pendientes",
    "payments.list_pending": "Verificar evidencia de pagos",
    "finance.breakdown": "Conciliar presupuesto y gastos",
    "request_intelligence": "Revisar ruta operacional",
    "write_like_action": "Revisar solicitud de escritura",
    "document_confirmation": "Revisar confirmación documental",
    "evidence.collect_context": "Recolectar contexto de análisis",
}

ROUTE_REQUIRED_CONTEXT = {
    "cfdi.list_pending": ["CFDI o factura relacionada"],
    "payments.list_pending": ["Evidencia de pago o reembolso"],
    "finance.breakdown": ["Presupuesto, gasto o reporte financiero"],
    "evidence.collect_context": ["Documento, reporte o texto base"],
}

ROUTE_BLOCKED_CAPABILITIES = [
    "writes",
    "provider_calls",
    "route_execution",
    "webhooks",
    "notifications",
]


@dataclass(frozen=True)
class AnalystEvidence:
    source_type: str
    label: str
    summary: str
    rank_score: int = 0
    rank_reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AnalystWorkbenchResult:
    status: str
    title: str
    answer: str
    evidence: List[Dict[str, Any]]
    caveats: List[str]
    next_questions: List[str]
    suggested_routes: List[Dict[str, Any]]
    actions_executed: List[str]
    provider_called: bool
    coverage_level: str
    answer_contract: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ContextSufficiency:
    coverage_level: str
    coverage_reasons: List[str]


def _clip(text: str, limit: int = 500) -> str:
    compact = re.sub(r"\s+", " ", text or "").strip()
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3].rstrip() + "..."


def _stable_evidence_key(item: AnalystEvidence) -> str:
    parts = (
        item.source_type,
        item.label,
        item.summary[:240],
    )
    return "|".join(
        re.sub(r"\s+", " ", part.lower()).strip()
        for part in parts
    )


def _source_score(source_type: str) -> int:
    return SOURCE_PRIORITY.get(source_type, 10)


def context_sufficiency_for_evidence(
    evidence: Iterable[AnalystEvidence],
    intent: Optional[AnalystIntent] = None,
) -> ContextSufficiency:
    items = list(evidence)
    if not items:
        return ContextSufficiency("none", ["no_evidence"])
    best_score = max(item.rank_score for item in items)
    if any(item.summary.endswith("...") for item in items):
        return ContextSufficiency("low", ["clipped_evidence"])
    if best_score < LOW_RELEVANCE_SCORE:
        return ContextSufficiency("low", ["low_relevance"])
    if intent is not None and intent.analyst_intent == "compare" and len(
        items
    ) < 2:
        return ContextSufficiency("low", ["incomplete_comparison"])
    if best_score >= 105 and len(items) >= 2:
        return ContextSufficiency("high", ["multi_source_high_relevance"])
    return ContextSufficiency("medium", ["supported_context"])


def coverage_level_for_evidence(evidence: Iterable[AnalystEvidence]) -> str:
    return context_sufficiency_for_evidence(evidence).coverage_level


def _has_clipped_evidence(evidence: Iterable[AnalystEvidence]) -> bool:
    return any(item.summary.endswith("...") for item in evidence)


def _route_contract(
    route_id: str,
    *,
    reason: str,
    required_context: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    context = list(
        required_context
        if required_context is not None
        else ROUTE_REQUIRED_CONTEXT.get(route_id, [])
    )
    return {
        "route_id": route_id,
        "label": ROUTE_LABELS.get(route_id, route_id),
        "reason": reason,
        "required_context": context,
        "blocked_capabilities": list(ROUTE_BLOCKED_CAPABILITIES),
        "execution_status": "not_executed",
        "writes_enabled": False,
    }


def _dedupe_route_contracts(
    routes: Iterable[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    deduped: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for route in routes:
        route_id = re.sub(
            r"\s+",
            " ",
            str(route.get("route_id") or "").strip(),
        )
        if not route_id:
            continue
        key = route_id.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(dict(route))
    return deduped


def suggested_routes_for_context(
    *,
    intent: AnalystIntent,
    coverage_level: str,
    coverage_reasons: Iterable[str],
    evidence: Iterable[AnalystEvidence],
) -> List[Dict[str, Any]]:
    reasons = set(coverage_reasons)
    routes: List[Dict[str, Any]] = []
    if intent.requires_operational_route:
        route_id = intent.operational_route_hint or "request_intelligence"
        routes.append(
            _route_contract(
                route_id,
                reason="operational_route_detected_not_executed",
            )
        )
    if (
        not intent.requires_operational_route
        and (coverage_level == "none" or "no_evidence" in reasons)
    ):
        routes.append(
            _route_contract(
                "evidence.collect_context",
                reason="insufficient_evidence",
                required_context=(
                    intent.context_requirements
                    or ROUTE_REQUIRED_CONTEXT["evidence.collect_context"]
                ),
            )
        )

    evidence_text = " ".join(
        f"{item.label} {item.summary}" for item in evidence
    ).lower()
    for route, tokens in ROUTE_SIGNAL_TOKENS.items():
        if _contains_any(evidence_text, tokens):
            routes.append(
                _route_contract(
                    route,
                    reason=f"evidence_signal:{route}",
                )
            )
    return _dedupe_route_contracts(routes)


def _coverage_contribution(
    *,
    index: int,
    item: AnalystEvidence,
    coverage_level: str,
    coverage_reasons: Iterable[str],
) -> str:
    reasons = set(coverage_reasons)
    if coverage_level == "none" or "no_evidence" in reasons:
        return "missing"
    if item.summary.endswith("...") or "clipped_summary" in item.rank_reasons:
        return "clipped"
    if item.rank_score < LOW_RELEVANCE_SCORE or "low_relevance" in reasons:
        return "limited"
    if index == 0:
        return "primary"
    return "supporting"


def _missing_evidence_reason(
    *,
    coverage_level: str,
    coverage_reasons: Iterable[str],
) -> Optional[str]:
    reasons = set(coverage_reasons)
    if "no_evidence" in reasons or coverage_level == "none":
        return "no_evidence"
    if "clipped_evidence" in reasons:
        return "clipped_evidence"
    if "low_relevance" in reasons:
        return "low_relevance"
    if "incomplete_comparison" in reasons:
        return "incomplete_comparison"
    return None


def evidence_diagnostics_for_context(
    *,
    evidence: Iterable[AnalystEvidence],
    coverage_level: str,
    coverage_reasons: Iterable[str],
) -> List[Dict[str, Any]]:
    reasons = set(coverage_reasons)
    diagnostics: List[Dict[str, Any]] = []
    evidence_items = list(evidence)
    if not evidence_items:
        return [
            {
                "source_type": "missing_context",
                "label": "contexto requerido",
                "rank_score": 0,
                "rank_reasons": list(reasons or ["no_evidence"]),
                "coverage_contribution": "missing",
                "clipped": False,
                "low_relevance": True,
                "missing_evidence_reason": _missing_evidence_reason(
                    coverage_level=coverage_level,
                    coverage_reasons=reasons,
                ) or "no_evidence",
                "trace_safe_summary": (
                    "No hay evidencia disponible para sostener el análisis."
                ),
            }
        ]
    for index, item in enumerate(evidence_items):
        rank_reasons = list(item.rank_reasons or [])
        clipped = item.summary.endswith("...") or (
            "clipped_summary" in rank_reasons
        )
        low_relevance = (
            item.rank_score < LOW_RELEVANCE_SCORE
            or (
                coverage_level == "low"
                and "low_relevance" in reasons
            )
        )
        diagnostics.append(
            {
                "source_type": item.source_type,
                "label": item.label,
                "rank_score": item.rank_score,
                "rank_reasons": rank_reasons,
                "coverage_contribution": _coverage_contribution(
                    index=index,
                    item=item,
                    coverage_level=coverage_level,
                    coverage_reasons=reasons,
                ),
                "clipped": clipped,
                "low_relevance": low_relevance,
                "missing_evidence_reason": _missing_evidence_reason(
                    coverage_level=coverage_level,
                    coverage_reasons=reasons,
                ),
                "trace_safe_summary": (
                    f"{item.source_type} evidence ranked with score "
                    f"{item.rank_score}."
                ),
            }
        )
    return diagnostics


def build_answer_contract(
    *,
    intent: AnalystIntent,
    evidence: Iterable[AnalystEvidence],
    caveats: Iterable[str],
    coverage_level: str,
    overclaim_guard_applied: bool,
    coverage_reasons: Optional[Iterable[str]] = None,
    next_questions: Optional[Iterable[str]] = None,
    suggested_routes: Optional[Iterable[Dict[str, Any]]] = None,
    evidence_diagnostics: Optional[Iterable[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    evidence_items = list(evidence)
    reasons = list(coverage_reasons or [])
    questions = list(next_questions or [])
    routes = list(suggested_routes or [])
    diagnostics = list(evidence_diagnostics or [])
    return {
        "version": ANSWER_CONTRACT_VERSION,
        "status": "success" if coverage_level != "none" else "needs_context",
        "coverage_level": coverage_level,
        "coverage_reasons": reasons,
        "analyst_intent": intent.analyst_intent,
        "evidence_count": len(evidence_items),
        "evidence_types": [
            item.source_type for item in evidence_items
        ],
        "must_cite_evidence": True,
        "external_validation_claimed": False,
        "writes_allowed": False,
        "overclaim_guard_applied": overclaim_guard_applied,
        "caveat_count": len(list(caveats)),
        "next_question_count": len(questions),
        "suggested_route_count": len(routes),
        "suggested_routes": routes,
        "evidence_diagnostic_count": len(diagnostics),
        "evidence_diagnostics": diagnostics,
    }


def _dedupe_questions(questions: Iterable[str]) -> List[str]:
    deduped: List[str] = []
    seen: set[str] = set()
    for question in questions:
        compact = re.sub(r"\s+", " ", question or "").strip()
        if not compact:
            continue
        key = compact.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(compact)
    return deduped


def next_questions_for_context(
    *,
    intent: AnalystIntent,
    coverage_level: str,
    coverage_reasons: Iterable[str],
    evidence: Iterable[AnalystEvidence],
) -> List[str]:
    reasons = set(coverage_reasons)
    questions: List[str] = []
    if intent.requires_operational_route:
        questions.append(
            "¿Confirmas que solo debo sugerir la ruta y no ejecutarla?"
        )
        return _dedupe_questions(questions)
    if intent.analyst_intent == "unknown":
        questions.append(
            "¿Quieres que analice riesgos, resumen, comparación o próximos "
            "pasos?"
        )
        return _dedupe_questions(questions)
    if coverage_level == "none" or "no_evidence" in reasons:
        questions.extend(
            [
                "¿Qué documento, reporte o texto debo usar como base?",
                (
                    "¿Quieres que el análisis sea para dirección, "
                    "operación o cliente?"
                ),
            ]
        )
        return _dedupe_questions(questions)
    if coverage_level == "high":
        return []
    if coverage_level == "low":
        questions.append(
            "¿Puedes compartir la fuente completa o confirmar estos hallazgos?"
        )
    if "incomplete_comparison" in reasons:
        questions.extend(
            [
                "¿Cuál es el documento base?",
                "¿Cuál es el documento contraparte a comparar?",
            ]
        )
    if intent.analyst_intent == "risk_review":
        questions.extend(
            [
                (
                    "¿Existe anexo, SOW o contrato completo para validar "
                    "obligaciones?"
                ),
                "¿Qué decisión debe tomar dirección con este análisis?",
            ]
        )
    elif (
        intent.analyst_intent == "compare"
        and "incomplete_comparison" not in reasons
    ):
        questions.extend(
            [
                "¿Cuál es el documento base?",
                "¿Cuál es la versión o propuesta a comparar?",
            ]
        )
    elif intent.analyst_intent == "summarize":
        questions.append(
            "¿Quieres enfoque ejecutivo, operativo o para cliente?"
        )
    elif intent.analyst_intent == "explain":
        questions.append("¿Qué parte quieres que explique con más detalle?")
    elif intent.analyst_intent == "questions":
        questions.append("¿Quieres que las convierta en correo o minuta?")
    elif intent.analyst_intent == "next_steps":
        questions.extend(
            [
                "¿Cuál es la fecha objetivo de cierre?",
                "¿Quién aprueba el siguiente entregable?",
            ]
        )
    if not list(evidence) and coverage_level != "none":
        questions.append("¿Qué evidencia debo priorizar?")
    return _dedupe_questions(questions)


def apply_no_overclaim_guard(
    *,
    answer: str,
    caveats: List[str],
    intent: AnalystIntent,
    coverage_level: str,
    evidence: List[AnalystEvidence],
) -> tuple[str, List[str], bool]:
    guard_applied = False
    guarded_caveats = list(caveats)
    guarded_answer = answer
    if (
        coverage_level == "low"
        or _has_clipped_evidence(evidence)
        or (
            intent.analyst_intent == "compare"
            and len(evidence) < 2
        )
    ):
        guard_applied = True
        if "preliminar" not in guarded_answer.lower():
            guarded_answer = guarded_answer.replace(
                "con el contexto disponible",
                "preliminar con el contexto disponible",
                1,
            )
        for caveat in (
            "La respuesta requiere confirmación humana antes de usarse "
            "como conclusión final.",
        ):
            if caveat not in guarded_caveats:
                guarded_caveats.append(caveat)
    return guarded_answer, guarded_caveats, guard_applied


def _contains_any(text: str, tokens: Iterable[str]) -> bool:
    normalized = re.sub(r"\s+", " ", text.lower()).strip()
    return any(token in normalized for token in tokens)


def _intent_boost(
    intent: AnalystIntent,
    item: AnalystEvidence,
) -> tuple[int, List[str]]:
    text = f"{item.label} {item.summary}".lower()
    if intent.analyst_intent == "risk_review":
        if _contains_any(
            text,
            (
                "riesgo",
                "obligacion",
                "obligación",
                "responsable",
                "fecha",
                "monto",
                "penalizacion",
                "penalización",
                "anexo",
                "contrato",
            ),
        ):
            return 20, ["risk_review_terms"]
    if intent.analyst_intent == "compare":
        if _contains_any(
            text,
            (
                "version",
                "versión",
                "propuesta",
                "contrato",
                "sow",
                "alcance",
                "costo",
                "fecha",
            ),
        ):
            return 20, ["comparison_terms"]
    if intent.analyst_intent in {"summarize", "explain"}:
        if item.source_type in {"document_intake", "report_result"}:
            return 15, ["direct_document_or_report"]
        if len(item.summary) >= 120:
            return 15, ["substantial_summary"]
    if intent.analyst_intent in {"questions", "next_steps"}:
        if item.source_type in {"conversation", "inline_context"}:
            return 15, ["planning_context"]
    return 0, []


def rank_analyst_evidence(
    intent: AnalystIntent,
    evidence: Iterable[AnalystEvidence],
) -> List[AnalystEvidence]:
    ranked: List[tuple[AnalystEvidence, int, str]] = []
    for index, item in enumerate(evidence):
        score = _source_score(item.source_type)
        reasons = [f"source:{item.source_type}"]
        boost, boost_reasons = _intent_boost(intent, item)
        score += boost
        reasons.extend(boost_reasons)
        if item.label.strip().lower() not in GENERIC_LABELS:
            score += 5
            reasons.append("specific_label")
        if len(item.summary.strip()) < MIN_INLINE_CONTEXT_CHARS:
            score -= 10
            reasons.append("short_summary")
        if item.summary.endswith("..."):
            score -= 10
            reasons.append("clipped_summary")
        ranked.append(
            (
                replace(
                    item,
                    rank_score=score,
                    rank_reasons=reasons,
                ),
                index,
                _stable_evidence_key(item),
            )
        )
    ranked.sort(
        key=lambda value: (
            -value[0].rank_score,
            -_source_score(value[0].source_type),
            value[1],
            value[2],
        )
    )
    return [item for item, _index, _key in ranked]


def _inline_context_candidate(text: str) -> str:
    raw = text or ""
    for pattern in (
        r"(?:contexto|texto|documento|contrato|balanza)\s*:\s*(?P<body>.+)$",
        r"(?:contenido|extracto|fragmento)\s*:\s*(?P<body>.+)$",
    ):
        match = re.search(pattern, raw, flags=re.IGNORECASE | re.DOTALL)
        if match:
            return str(match.group("body") or "")

    if "\n\n" in raw:
        parts = [part.strip() for part in raw.split("\n\n") if part.strip()]
        if len(parts) > 1:
            return "\n\n".join(parts[1:])

    if ":" in raw:
        _prefix, suffix = raw.split(":", 1)
        return suffix
    return ""


def extract_inline_analyst_evidence(
    text: str,
    intent: AnalystIntent,
) -> List[AnalystEvidence]:
    if intent.requires_operational_route:
        return []

    candidate = _inline_context_candidate(text)
    compact = re.sub(r"\s+", " ", candidate or "").strip()
    if len(compact) < MIN_INLINE_CONTEXT_CHARS:
        return []

    return [
        AnalystEvidence(
            source_type="inline_context",
            label="contexto inline",
            summary=_clip(compact, INLINE_CONTEXT_LIMIT),
        )
    ]


def build_analyst_evidence_pack(
    *,
    inline_evidence: Iterable[AnalystEvidence],
    history_evidence: Iterable[AnalystEvidence],
    intent: Optional[AnalystIntent] = None,
    limit: int = MAX_ANALYST_EVIDENCE,
) -> List[AnalystEvidence]:
    packed: List[AnalystEvidence] = []
    seen: set[str] = set()
    for item in list(inline_evidence) + list(history_evidence):
        key = _stable_evidence_key(item)
        if key in seen:
            continue
        seen.add(key)
        packed.append(item)
    if intent is not None:
        packed = rank_analyst_evidence(intent, packed)
    return packed[:limit]


def _document_intake_evidence(content: str) -> Optional[AnalystEvidence]:
    intake = extract_document_intake_result_from_text(content)
    if intake is None:
        return None
    doc_type = str(intake.get("detected_document_type") or "documento")
    summary = str(intake.get("summary") or "Documento subido sin resumen.")
    missing = [
        str(item)
        for item in (intake.get("missing_fields") or [])
        if item
    ]
    if missing:
        summary = f"{summary} Faltantes: {', '.join(missing)}."
    return AnalystEvidence(
        source_type="document_intake",
        label=doc_type,
        summary=_clip(summary),
    )


def extract_analyst_evidence_from_messages(
    messages: Iterable[Any],
) -> List[AnalystEvidence]:
    evidence: List[AnalystEvidence] = []
    for item in messages:
        content = str(getattr(item, "content", item) or "")
        if not content.strip():
            continue
        intake_evidence = _document_intake_evidence(content)
        if intake_evidence is not None:
            evidence.append(intake_evidence)
            continue
        if "|" in content and any(
            token in content.lower()
            for token in ("comparacion", "reporte", "cfdi", "pagos")
        ):
            evidence.append(
                AnalystEvidence(
                    source_type="report_result",
                    label="reporte previo",
                    summary=_clip(content),
                )
            )
            continue
        if len(content.strip()) > 80:
            evidence.append(
                AnalystEvidence(
                    source_type="conversation",
                    label="contexto de conversación",
                    summary=_clip(content),
                )
            )
    return evidence[:6]


def _needs_context(intent: AnalystIntent) -> AnalystWorkbenchResult:
    needed = ", ".join(intent.context_requirements or ["contexto"])
    next_questions = next_questions_for_context(
        intent=intent,
        coverage_level="none",
        coverage_reasons=["no_evidence"],
        evidence=[],
    )
    suggested_routes = suggested_routes_for_context(
        intent=intent,
        coverage_level="none",
        coverage_reasons=["no_evidence"],
        evidence=[],
    )
    evidence_diagnostics = evidence_diagnostics_for_context(
        evidence=[],
        coverage_level="none",
        coverage_reasons=["no_evidence"],
    )
    return AnalystWorkbenchResult(
        status="needs_context",
        title="Necesito contexto para analizar",
        answer=(
            f"Necesito que subas, pegues o selecciones el contexto ({needed}) "
            "antes de responder. No ejecuté acciones ni cambios."
        ),
        evidence=[],
        caveats=[
            "Sin contexto no puedo sostener una conclusión sin inventar datos."
        ],
        next_questions=next_questions,
        suggested_routes=suggested_routes,
        actions_executed=[],
        provider_called=False,
        coverage_level="none",
        answer_contract={
            "version": ANSWER_CONTRACT_VERSION,
            "status": "needs_context",
            "coverage_level": "none",
            "coverage_reasons": ["no_evidence"],
            "analyst_intent": intent.analyst_intent,
            "evidence_count": 0,
            "evidence_types": [],
            "must_cite_evidence": True,
            "external_validation_claimed": False,
            "writes_allowed": False,
            "overclaim_guard_applied": True,
            "caveat_count": 1,
            "next_question_count": len(next_questions),
            "suggested_route_count": len(suggested_routes),
            "suggested_routes": suggested_routes,
            "evidence_diagnostic_count": len(evidence_diagnostics),
            "evidence_diagnostics": evidence_diagnostics,
        },
    )


def _routed_to_operational(intent: AnalystIntent) -> AnalystWorkbenchResult:
    suggested_routes = suggested_routes_for_context(
        intent=intent,
        coverage_level="none",
        coverage_reasons=["operational_route"],
        evidence=[],
    )
    return AnalystWorkbenchResult(
        status="routed_to_operational",
        title="Ruta operacional detectada",
        answer=(
            "Esta solicitud corresponde a una ruta operacional "
            "determinística, "
            "no al Analyst Workbench."
        ),
        evidence=[],
        caveats=[],
        next_questions=[],
        suggested_routes=suggested_routes,
        actions_executed=[],
        provider_called=False,
        coverage_level="none",
        answer_contract={
            "version": ANSWER_CONTRACT_VERSION,
            "status": "routed_to_operational",
            "coverage_level": "none",
            "coverage_reasons": ["operational_route"],
            "analyst_intent": intent.analyst_intent,
            "evidence_count": 0,
            "evidence_types": [],
            "must_cite_evidence": False,
            "external_validation_claimed": False,
            "writes_allowed": False,
            "overclaim_guard_applied": False,
            "caveat_count": 0,
            "next_question_count": 0,
            "suggested_route_count": len(suggested_routes),
            "suggested_routes": suggested_routes,
            "evidence_diagnostic_count": 0,
            "evidence_diagnostics": [],
        },
    )


def _answer_for_intent(
    intent: AnalystIntent,
    evidence: List[AnalystEvidence],
) -> tuple[str, List[str], List[str]]:
    primary = evidence[0].summary if evidence else ""
    clipping_caveats = [
        "Alguna evidencia fue recortada para mantener el analisis acotado."
    ] if any(item.summary.endswith("...") for item in evidence) else []
    relevance_caveats = [
        "La evidencia disponible es limitada o indirecta."
    ] if evidence and max(item.rank_score for item in evidence) < (
        LOW_RELEVANCE_SCORE
    ) else []
    if intent.analyst_intent == "risk_review":
        return (
            "Riesgos visibles con el contexto disponible:\n"
            f"- Base revisada: {primary}\n"
            "- Riesgo de evidencia incompleta si faltan anexos, importes, "
            "fechas o responsables.\n"
            "- Riesgo operativo si las obligaciones no tienen dueño, fecha "
            "límite o criterio de aceptación.\n"
            "- Riesgo financiero si los montos no están reconciliados contra "
            "CFDI, pago o presupuesto.",
            [
                "El análisis se limita al contexto disponible; "
                "no revisé datos vivos adicionales."
            ] + clipping_caveats + relevance_caveats,
            [
                (
                    "¿Existe anexo, SOW o contrato completo para validar "
                    "obligaciones?"
                ),
                "¿Qué decisión debe tomar dirección con este análisis?",
            ],
        )
    if intent.analyst_intent == "compare":
        return (
            "Comparación preliminar con el contexto disponible:\n"
            f"- Evidencia principal: {primary}\n"
            "- Puntos a contrastar: alcance, fechas, entregables, costos, "
            "penalizaciones y responsables.\n"
            "- Para una comparación completa necesito ambos documentos o sus "
            "extractos relevantes.",
            [
                "Si solo hay un documento en contexto, "
                "la comparación queda incompleta."
            ] + clipping_caveats + relevance_caveats,
            [
                "¿Cuál es el documento base y cuál es la versión/propuesta "
                "a comparar?"
            ],
        )
    if intent.analyst_intent == "questions":
        return (
            "Preguntas útiles para el cliente:\n"
            f"- Sobre contexto: {primary}\n"
            "- ¿Qué resultado espera y con qué criterio se acepta?\n"
            "- ¿Qué fechas, responsables y dependencias son obligatorias?\n"
            "- ¿Qué riesgos o excepciones ya conoce el cliente?",
            [
                "Estas preguntas salen del contexto disponible, "
                "no de datos externos."
            ] + clipping_caveats + relevance_caveats,
            ["¿Quieres que las convierta en correo o minuta?"],
        )
    if intent.analyst_intent == "next_steps":
        return (
            "Próximos pasos sugeridos:\n"
            f"- Confirmar el contexto base: {primary}\n"
            "- Separar pendientes por dueño, fecha límite y evidencia "
            "requerida.\n"
            "- Identificar bloqueos que requieren decisión de dirección "
            "o cliente.\n"
            "- Definir el siguiente entregable verificable.",
            [
                "No ejecuté acciones; son pasos sugeridos para "
                "revisión humana."
            ] + clipping_caveats + relevance_caveats,
            [
                "¿Cuál es la fecha objetivo de cierre?",
                "¿Quién aprueba el siguiente entregable?",
            ],
        )
    if intent.analyst_intent == "summarize":
        return (
            "Resumen con el contexto disponible:\n"
            f"- {primary}\n"
            "- No hay evidencia suficiente para afirmar hechos fuera "
            "de ese contexto.\n"
            "- Conviene validar faltantes antes de usarlo como "
            "conclusión ejecutiva.",
            ["Resumen limitado a la evidencia disponible."]
            + clipping_caveats
            + relevance_caveats,
            ["¿Quieres enfoque ejecutivo, operativo o para cliente?"],
        )
    return (
        "Explicación con el contexto disponible:\n"
        f"- {primary}\n"
        "- Lo anterior describe la evidencia disponible, no una validación "
        "contra datos vivos.\n"
        "- Si necesitas una conclusión formal, faltaría confirmar fuente, "
        "fecha y alcance.",
        ["No revisé datos vivos ni ejecuté acciones."]
        + clipping_caveats
        + relevance_caveats,
        ["¿Qué parte quieres que explique con más detalle?"],
    )


async def run_analyst_workbench(
    *,
    intent: AnalystIntent,
    evidence: Optional[List[AnalystEvidence]] = None,
    provider_allowed: bool = False,
    provider_fn: Optional[AnalystProviderFn] = None,
) -> AnalystWorkbenchResult:
    if intent.requires_operational_route:
        return _routed_to_operational(intent)

    evidence = list(evidence or [])
    if not evidence:
        return _needs_context(intent)
    if all(item.rank_score == 0 for item in evidence):
        evidence = rank_analyst_evidence(intent, evidence)
    sufficiency = context_sufficiency_for_evidence(evidence, intent)
    coverage_level = sufficiency.coverage_level
    coverage_reasons = sufficiency.coverage_reasons
    suggested_routes = suggested_routes_for_context(
        intent=intent,
        coverage_level=coverage_level,
        coverage_reasons=coverage_reasons,
        evidence=evidence,
    )
    evidence_diagnostics = evidence_diagnostics_for_context(
        evidence=evidence,
        coverage_level=coverage_level,
        coverage_reasons=coverage_reasons,
    )

    provider_called = False
    if provider_allowed and provider_fn is not None:
        try:
            provider_called = True
            answer = await provider_fn(intent, evidence)
            if answer.strip():
                caveats = ["Respuesta basada en contexto autorizado."]
                guarded_answer, caveats, overclaim_guard_applied = (
                    apply_no_overclaim_guard(
                        answer=answer.strip(),
                        caveats=caveats,
                        intent=intent,
                        coverage_level=coverage_level,
                        evidence=evidence,
                    )
                )
                return AnalystWorkbenchResult(
                    status="success",
                    title="Analyst Workbench",
                    answer=guarded_answer,
                    evidence=[item.to_dict() for item in evidence],
                    caveats=caveats,
                    next_questions=[],
                    suggested_routes=suggested_routes,
                    actions_executed=[],
                    provider_called=True,
                    coverage_level=coverage_level,
                    answer_contract=build_answer_contract(
                        intent=intent,
                        evidence=evidence,
                        caveats=caveats,
                        coverage_level=coverage_level,
                        overclaim_guard_applied=overclaim_guard_applied,
                        coverage_reasons=coverage_reasons,
                        next_questions=[],
                        suggested_routes=suggested_routes,
                        evidence_diagnostics=evidence_diagnostics,
                    ),
                )
        except Exception:
            return AnalystWorkbenchResult(
                status="provider_unavailable",
                title="Provider no disponible",
                answer=(
                    "No pude usar provider para redactar el análisis. "
                    "No ejecuté acciones ni cambios."
                ),
                evidence=[item.to_dict() for item in evidence],
                caveats=["Provider no disponible; no se inventó respuesta."],
                next_questions=[
                    "¿Quieres que responda solo con síntesis determinística "
                    "del contexto?"
                ],
                suggested_routes=suggested_routes,
                actions_executed=[],
                provider_called=provider_called,
                coverage_level=coverage_level,
                answer_contract=build_answer_contract(
                    intent=intent,
                    evidence=evidence,
                    caveats=[
                        "Provider no disponible; no se inventó respuesta."
                    ],
                    coverage_level=coverage_level,
                    overclaim_guard_applied=True,
                    coverage_reasons=coverage_reasons,
                    next_questions=[
                        "¿Quieres que responda solo con síntesis "
                        "determinística "
                        "del contexto?"
                    ],
                    suggested_routes=suggested_routes,
                    evidence_diagnostics=evidence_diagnostics,
                ),
            )

    answer, caveats, _legacy_next_questions = _answer_for_intent(
        intent,
        evidence,
    )
    next_questions = next_questions_for_context(
        intent=intent,
        coverage_level=coverage_level,
        coverage_reasons=coverage_reasons,
        evidence=evidence,
    )
    answer, caveats, overclaim_guard_applied = apply_no_overclaim_guard(
        answer=answer,
        caveats=caveats,
        intent=intent,
        coverage_level=coverage_level,
        evidence=evidence,
    )
    return AnalystWorkbenchResult(
        status="success",
        title="Analyst Workbench",
        answer=answer,
        evidence=[item.to_dict() for item in evidence],
        caveats=caveats,
        next_questions=next_questions,
        suggested_routes=suggested_routes,
        actions_executed=[],
        provider_called=False,
        coverage_level=coverage_level,
        answer_contract=build_answer_contract(
            intent=intent,
            evidence=evidence,
            caveats=caveats,
            coverage_level=coverage_level,
            overclaim_guard_applied=overclaim_guard_applied,
            coverage_reasons=coverage_reasons,
            next_questions=next_questions,
            suggested_routes=suggested_routes,
            evidence_diagnostics=evidence_diagnostics,
        ),
    )
