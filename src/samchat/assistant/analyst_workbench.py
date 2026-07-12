from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional

from .analyst_intent import AnalystIntent
from .document_conversation import extract_document_intake_result_from_text


AnalystProviderFn = Callable[
    [AnalystIntent, List["AnalystEvidence"]],
    Awaitable[str],
]


@dataclass(frozen=True)
class AnalystEvidence:
    source_type: str
    label: str
    summary: str

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

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _clip(text: str, limit: int = 500) -> str:
    compact = re.sub(r"\s+", " ", text or "").strip()
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3].rstrip() + "..."


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
    )


def _answer_for_intent(
    intent: AnalystIntent,
    evidence: List[AnalystEvidence],
) -> tuple[str, List[str], List[str]]:
    primary = evidence[0].summary if evidence else ""
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
            ],
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
            ],
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
            ],
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
            ["No ejecuté acciones; son pasos sugeridos para revisión humana."],
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
            ["Resumen limitado a la evidencia disponible."],
            ["¿Quieres enfoque ejecutivo, operativo o para cliente?"],
        )
    return (
        "Explicación con el contexto disponible:\n"
        f"- {primary}\n"
        "- Lo anterior describe la evidencia disponible, no una validación "
        "contra datos vivos.\n"
        "- Si necesitas una conclusión formal, faltaría confirmar fuente, "
        "fecha y alcance.",
        ["No revisé datos vivos ni ejecuté acciones."],
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

    provider_called = False
    if provider_allowed and provider_fn is not None:
        try:
            provider_called = True
            answer = await provider_fn(intent, evidence)
            if answer.strip():
                return AnalystWorkbenchResult(
                    status="success",
                    title="Analyst Workbench",
                    answer=answer.strip(),
                    evidence=[item.to_dict() for item in evidence],
                    caveats=["Respuesta basada en contexto autorizado."],
                    next_questions=[],
                    suggested_routes=[],
                    actions_executed=[],
                    provider_called=True,
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
            )

    answer, caveats, next_questions = _answer_for_intent(intent, evidence)
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
    )
