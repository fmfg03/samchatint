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
    suggested_routes: List[str]
    actions_executed: List[str]
    provider_called: bool
    coverage_level: str
    answer_contract: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


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


def coverage_level_for_evidence(evidence: Iterable[AnalystEvidence]) -> str:
    items = list(evidence)
    if not items:
        return "none"
    best_score = max(item.rank_score for item in items)
    if any(item.summary.endswith("...") for item in items):
        return "low"
    if best_score < LOW_RELEVANCE_SCORE:
        return "low"
    if best_score >= 105 and len(items) >= 2:
        return "high"
    return "medium"


def _has_clipped_evidence(evidence: Iterable[AnalystEvidence]) -> bool:
    return any(item.summary.endswith("...") for item in evidence)


def build_answer_contract(
    *,
    intent: AnalystIntent,
    evidence: Iterable[AnalystEvidence],
    caveats: Iterable[str],
    coverage_level: str,
    overclaim_guard_applied: bool,
) -> Dict[str, Any]:
    evidence_items = list(evidence)
    return {
        "version": ANSWER_CONTRACT_VERSION,
        "status": "success" if coverage_level != "none" else "needs_context",
        "coverage_level": coverage_level,
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
    }


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
        next_questions=[
            "¿Qué documento, reporte o texto debo usar como base?",
            (
                "¿Quieres que el análisis sea para dirección, "
                "operación o cliente?"
            ),
        ],
        suggested_routes=[],
        actions_executed=[],
        provider_called=False,
        coverage_level="none",
        answer_contract={
            "version": ANSWER_CONTRACT_VERSION,
            "status": "needs_context",
            "coverage_level": "none",
            "analyst_intent": intent.analyst_intent,
            "evidence_count": 0,
            "evidence_types": [],
            "must_cite_evidence": True,
            "external_validation_claimed": False,
            "writes_allowed": False,
            "overclaim_guard_applied": True,
            "caveat_count": 1,
        },
    )


def _routed_to_operational(intent: AnalystIntent) -> AnalystWorkbenchResult:
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
        suggested_routes=[
            intent.operational_route_hint or "request_intelligence"
        ],
        actions_executed=[],
        provider_called=False,
        coverage_level="none",
        answer_contract={
            "version": ANSWER_CONTRACT_VERSION,
            "status": "routed_to_operational",
            "coverage_level": "none",
            "analyst_intent": intent.analyst_intent,
            "evidence_count": 0,
            "evidence_types": [],
            "must_cite_evidence": False,
            "external_validation_claimed": False,
            "writes_allowed": False,
            "overclaim_guard_applied": False,
            "caveat_count": 0,
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
    coverage_level = coverage_level_for_evidence(evidence)

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
                    suggested_routes=[],
                    actions_executed=[],
                    provider_called=True,
                    coverage_level=coverage_level,
                    answer_contract=build_answer_contract(
                        intent=intent,
                        evidence=evidence,
                        caveats=caveats,
                        coverage_level=coverage_level,
                        overclaim_guard_applied=overclaim_guard_applied,
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
                suggested_routes=[],
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
                ),
            )

    answer, caveats, next_questions = _answer_for_intent(intent, evidence)
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
        suggested_routes=[],
        actions_executed=[],
        provider_called=False,
        coverage_level=coverage_level,
        answer_contract=build_answer_contract(
            intent=intent,
            evidence=evidence,
            caveats=caveats,
            coverage_level=coverage_level,
            overclaim_guard_applied=overclaim_guard_applied,
        ),
    )
