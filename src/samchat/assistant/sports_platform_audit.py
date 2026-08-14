"""Sports platform artifact audit wrapper.

The sports platform snapshot is broad and useful, but not all of it should be
exposed directly to the assistant. This module classifies its modules into
assistant-ready operational summaries, internal sources, commercial/demo
surfaces, and duplicate/overlapping projections before wiring.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Optional

from samchat.sports_platform import build_sports_platform_snapshot

from .business_diff_preview import NOT_EXECUTED

SPORTS_PLATFORM_AUDIT_ONLY = "sports_platform_audit_only"
DECISION_WRAP_BEFORE_WIRING = "wrap_before_wiring"
DECISION_WIRE_READ_ONLY_CANDIDATE = "wire_read_only_candidate"
DECISION_DO_NOT_WIRE_DIRECTLY = "do_not_wire_directly"

ASSISTANT_READY_MODULES = {
    "mission_control",
    "action_queue",
    "ops_brief",
    "incident_center",
    "roster_intelligence",
    "matchday_ops",
    "team_journey",
    "match_center",
}
INTERNAL_SOURCE_MODULES = {
    "command_center",
    "team_portal",
    "global_readiness",
    "sports_crm",
    "venue_ops",
    "communications",
    "public_layer",
}
COMMERCIAL_OR_DEMO_MODULES = {
    "sponsor_media",
    "public_microsite_generator",
    "mobile_field_app",
    "ai_ops_assistant",
    "ops_copilot",
    "post_tournament_report",
}


@dataclass(frozen=True)
class SportsPlatformModuleAudit:
    module_id: str
    classification: str
    title: str
    reason: str
    recommended_exposure: str
    evidence_keys: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SportsPlatformAuditReport:
    audit_id: str
    headline: str
    summary: str
    decision: str
    tournament: dict[str, Any]
    module_count: int
    assistant_ready_modules: list[str] = field(default_factory=list)
    internal_source_modules: list[str] = field(default_factory=list)
    commercial_or_demo_modules: list[str] = field(default_factory=list)
    missing_or_empty_modules: list[str] = field(default_factory=list)
    modules: list[SportsPlatformModuleAudit] = field(default_factory=list)
    redundancy_notes: list[str] = field(default_factory=list)
    improvement_notes: list[str] = field(default_factory=list)
    recommended_next_steps: list[str] = field(default_factory=list)
    safety_summary: dict[str, Any] = field(default_factory=dict)
    execution_status: str = NOT_EXECUTED
    writes_attempted: int = 0
    side_effects_detected: int = 0
    audit_language: str = SPORTS_PLATFORM_AUDIT_ONLY

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["modules"] = [item.to_dict() for item in self.modules]
        return payload


def _safe_str(value: Any) -> str:
    return str(value or "").strip()


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def _tournament_from_platform(platform: Mapping[str, Any]) -> dict[str, Any]:
    command = platform.get("command_center") or {}
    tournament = command.get("tournament") or {}
    return dict(tournament) if isinstance(tournament, Mapping) else {}


def _classification(module_id: str) -> str:
    if module_id in ASSISTANT_READY_MODULES:
        return "assistant_ready_summary"
    if module_id in INTERNAL_SOURCE_MODULES:
        return "internal_source"
    if module_id in COMMERCIAL_OR_DEMO_MODULES:
        return "commercial_or_demo_surface"
    return "unknown"


def _reason(module_id: str, classification: str) -> str:
    if classification == "assistant_ready_summary":
        return "Tiene conteos, prioridades o cola de acciones que pueden responder preguntas operativas concretas."
    if classification == "internal_source":
        return "Aporta se?ales de contexto, pero conviene usarlo como insumo de otro resumen, no como tool directa."
    if classification == "commercial_or_demo_surface":
        return "Describe experiencia/producto o salida comercial; requiere evidencia y contrato antes de prometer ejecuci?n."
    return "No hay clasificacion suficiente; requiere revision manual antes de cablearse."


def _recommended_exposure(classification: str) -> str:
    if classification == "assistant_ready_summary":
        return "Expose through a narrowed read-only operations status wrapper."
    if classification == "internal_source":
        return "Keep internal; feed a concise operations wrapper."
    if classification == "commercial_or_demo_surface":
        return "Do not expose directly; convert to explicit owner/sponsor workflow first."
    return "Do not wire until classified."


def _evidence_keys(value: Any) -> list[str]:
    if isinstance(value, Mapping):
        return sorted(str(key) for key in value.keys())[:12]
    return []


def _module_audits(platform: Mapping[str, Any]) -> list[SportsPlatformModuleAudit]:
    modules: list[SportsPlatformModuleAudit] = []
    for module_id, value in sorted(platform.items()):
        if module_id in {"ok", "read_only", "schema_version", "source", "summary"}:
            continue
        classification = _classification(module_id)
        title = ""
        if isinstance(value, Mapping):
            title = _safe_str(value.get("title")) or module_id.replace("_", " ").title()
        else:
            title = module_id.replace("_", " ").title()
        modules.append(
            SportsPlatformModuleAudit(
                module_id=module_id,
                classification=classification,
                title=title,
                reason=_reason(module_id, classification),
                recommended_exposure=_recommended_exposure(classification),
                evidence_keys=_evidence_keys(value),
            )
        )
    return modules


def build_sports_platform_audit_from_snapshot(
    snapshot: Mapping[str, Any],
    *,
    focus: Optional[str] = None,
) -> SportsPlatformAuditReport:
    platform = build_sports_platform_snapshot(dict(snapshot))
    modules = _module_audits(platform)
    if focus:
        wanted = focus.casefold().strip()
        modules = [
            item
            for item in modules
            if wanted in item.module_id.casefold() or wanted in item.classification.casefold()
        ]

    assistant_ready = sorted(
        item.module_id for item in modules if item.classification == "assistant_ready_summary"
    )
    internal = sorted(
        item.module_id for item in modules if item.classification == "internal_source"
    )
    commercial = sorted(
        item.module_id for item in modules if item.classification == "commercial_or_demo_surface"
    )
    missing = sorted(
        item.module_id
        for item in modules
        if not _has_value((platform.get(item.module_id) or {}))
    )

    if not modules:
        decision = DECISION_DO_NOT_WIRE_DIRECTLY
        headline = "No hay modulos deportivos para auditar"
        summary = "El snapshot no produjo modulos compatibles con auditoria de Sports Platform."
    elif commercial or internal:
        decision = DECISION_WRAP_BEFORE_WIRING
        headline = "Sports Platform sirve como fuente, pero no debe cablearse crudo"
        summary = (
            "El snapshot operativo contiene modulos utiles para el asistente, pero tambien "
            "superficies internas/comerciales que deben filtrarse antes de exponerlo al usuario."
        )
    else:
        decision = DECISION_WIRE_READ_ONLY_CANDIDATE
        headline = "Sports Platform es candidato read-only acotado"
        summary = "Los modulos auditados son res?menes operativos aptos para un wrapper read-only."

    return SportsPlatformAuditReport(
        audit_id="sports_platform_audit_v1",
        headline=headline,
        summary=summary,
        decision=decision,
        tournament=_tournament_from_platform(platform),
        module_count=len(modules),
        assistant_ready_modules=assistant_ready,
        internal_source_modules=internal,
        commercial_or_demo_modules=commercial,
        missing_or_empty_modules=missing,
        modules=modules,
        redundancy_notes=[
            "Se solapa con tournament.soul_snapshot: usar SOUL como caso canonico y Sports Platform como proyeccion UX.",
            "Se solapa parcialmente con Owner Pack: no presentar sponsor/media o reportes ejecutivos como entregables sin evidencia viva.",
            "Se solapa con tournament_ops_query para preguntas operativas puntuales; evitar duplicar respuestas.",
        ],
        improvement_notes=[
            "Crear wrapper pequeno: operations_status_from_sports_platform con mission_control, action_queue, incident_center y roster_intelligence.",
            "Mantener sponsor_media, public_microsite, mobile_field_app y post_tournament_report como candidatos separados, no en la tool operativa base.",
            "Agregar lenguaje de evidencia/no-claims por modulo antes de respuestas ejecutivas.",
        ],
        recommended_next_steps=[
            "No cablear build_sports_platform_snapshot directamente al router.",
            "Exponer primero un diagnostico/readiness operativo acotado a prioridades, riesgos y acciones.",
            "Usar el registry para marcar sports.platform_snapshot como partial hasta que exista wrapper vivo.",
        ],
        safety_summary={
            "read_only": True,
            "writes_enabled": False,
            "raw_snapshot_should_not_be_exposed": True,
            "approval_required_for_durable_outputs": True,
        },
        execution_status=NOT_EXECUTED,
        writes_attempted=0,
        side_effects_detected=0,
        audit_language=SPORTS_PLATFORM_AUDIT_ONLY,
    )


__all__ = [
    "SPORTS_PLATFORM_AUDIT_ONLY",
    "SportsPlatformAuditReport",
    "SportsPlatformModuleAudit",
    "build_sports_platform_audit_from_snapshot",
]
