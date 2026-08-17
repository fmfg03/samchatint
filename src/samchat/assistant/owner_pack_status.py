"""Owner-pack readiness status for SamChat assistant roadmap.

This module summarizes the owner's AI-needs eval set into product-facing
surfaces. It deliberately does not query live data and does not execute
writes: it answers a narrower question that became important in delivery
planning -- which owner dashboards/folders are prepared as assistant
contracts, and which evidence categories still block a truthful answer.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from typing import Dict, Iterable, List, Mapping, Sequence

from .business_diff_preview import (
    CREATE_ENTITY_FOLDER,
    CREATE_NATIONAL_PHASE_FOLDER,
    GENERATE_ACTIVATION_REPORT,
    NOT_EXECUTED,
    PLAN_FOLDER_BUILD,
    UPDATE_ENTITY_FOLDER,
    create_owner_prompt_business_diff_preview,
)
from .owner_folder_builder import build_owner_folder_proposal
from .owner_needs_eval import OwnerNeedsPrompt


OWNER_PACK_STATUS_ONLY = "owner_pack_status_only"
OWNER_PACK_PREPARED = "prepared"
OWNER_PACK_PREPARED_WITH_MISSING_EVIDENCE = "prepared_with_missing_evidence"


SURFACE_BY_OPERATION = {
    CREATE_ENTITY_FOLDER: "entity_folder",
    UPDATE_ENTITY_FOLDER: "entity_folder",
    CREATE_NATIONAL_PHASE_FOLDER: "national_phase_folder",
    GENERATE_ACTIVATION_REPORT: "marketing_activation_report",
    PLAN_FOLDER_BUILD: "work_plan_or_query",
}

SURFACE_LABELS = {
    "entity_folder": "Carpetas por entidad",
    "national_phase_folder": "Carpeta de fase nacional",
    "marketing_activation_report": "Activacion de marcas",
    "work_plan_or_query": "Plan de trabajo / consultas",
}


@dataclass(frozen=True)
class OwnerPackSurfaceStatus:
    surface_id: str
    label: str
    status: str
    prompt_count: int
    operation_types: List[str] = field(default_factory=list)
    prepared_artifacts: List[str] = field(default_factory=list)
    missing_evidence: List[str] = field(default_factory=list)
    supported_evidence: List[str] = field(default_factory=list)
    next_action: str = ""

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class OwnerPackStatusReport:
    status_id: str
    headline: str
    summary: str
    surfaces: List[OwnerPackSurfaceStatus] = field(default_factory=list)
    prompt_count: int = 0
    prepared_surface_count: int = 0
    missing_evidence: List[str] = field(default_factory=list)
    safety_summary: Dict[str, object] = field(default_factory=dict)
    execution_status: str = NOT_EXECUTED
    writes_attempted: int = 0
    side_effects_detected: int = 0
    audit_language: str = OWNER_PACK_STATUS_ONLY

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        payload["surfaces"] = [surface.to_dict() for surface in self.surfaces]
        return payload


def _surface_for_operation(operation_type: str) -> str:
    return SURFACE_BY_OPERATION.get(operation_type, "work_plan_or_query")


def _surface_status(missing_evidence: Sequence[str]) -> str:
    if missing_evidence:
        return OWNER_PACK_PREPARED_WITH_MISSING_EVIDENCE
    return OWNER_PACK_PREPARED


def _surface_next_action(surface_id: str, missing_evidence: Sequence[str]) -> str:
    if not missing_evidence:
        return "Listo para canary read-only con datos canonicos disponibles."
    if surface_id == "entity_folder":
        return (
            "Cargar o vincular evidencia de torneo, entidad, equipos, jugadores "
            "y finanzas antes de presentarlo como carpeta completa."
        )
    if surface_id == "national_phase_folder":
        return (
            "Vincular sede, hoteles, alimentos, servicios medicos, accidentes, "
            "proveedores y pagos antes de cerrar la carpeta nacional."
        )
    if surface_id == "marketing_activation_report":
        return (
            "Vincular materialidad fotografica, visitantes, proveedores presentes "
            "y resultado de actividades antes de generar informe final."
        )
    return (
        "Usar el contrato como plan/consulta read-only y separar evidencia encontrada "
        "de evidencia pendiente."
    )


def build_owner_pack_status_report(
    prompts: Iterable[OwnerNeedsPrompt],
    *,
    available_evidence_by_prompt: Mapping[str, Mapping[str, object]] | None = None,
) -> OwnerPackStatusReport:
    """Build a truthful status report for the owner's assistant surfaces.

    ``available_evidence_by_prompt`` is intentionally explicit per prompt. The
    function never treats canon requirements as live facts; if evidence is not
    supplied, the surface remains prepared but with missing evidence.
    """

    evidence_by_prompt = available_evidence_by_prompt or {}
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    prompts_list = list(prompts)

    for prompt in prompts_list:
        preview = create_owner_prompt_business_diff_preview(
            prompt,
            available_evidence=evidence_by_prompt.get(prompt.prompt_id),
        )
        proposal = build_owner_folder_proposal(preview)
        grouped[_surface_for_operation(preview.operation_type)].append(
            {
                "prompt_id": prompt.prompt_id,
                "operation_type": preview.operation_type,
                "folder_id": proposal.folder_id,
                "missing_evidence": list(proposal.missing_evidence),
                "supported_fields": list(
                    proposal.evidence_summary.get("supported_fields") or []
                ),
            }
        )

    surfaces: list[OwnerPackSurfaceStatus] = []
    all_missing: set[str] = set()
    preferred_order = (
        "entity_folder",
        "national_phase_folder",
        "marketing_activation_report",
        "work_plan_or_query",
    )
    for surface_id in preferred_order:
        items = grouped.get(surface_id, [])
        if not items:
            continue
        missing_counter: Counter[str] = Counter()
        supported_counter: Counter[str] = Counter()
        operation_types = sorted({str(item["operation_type"]) for item in items})
        artifacts = sorted({str(item["folder_id"]) for item in items})
        for item in items:
            missing_counter.update(str(value) for value in item["missing_evidence"])
            supported_counter.update(str(value) for value in item["supported_fields"])
        missing = sorted(missing_counter)
        supported = sorted(supported_counter)
        all_missing.update(missing)
        surfaces.append(
            OwnerPackSurfaceStatus(
                surface_id=surface_id,
                label=SURFACE_LABELS.get(surface_id, surface_id),
                status=_surface_status(missing),
                prompt_count=len(items),
                operation_types=operation_types,
                prepared_artifacts=artifacts,
                missing_evidence=missing,
                supported_evidence=supported,
                next_action=_surface_next_action(surface_id, missing),
            )
        )

    missing = sorted(all_missing)
    summary = (
        "Los tableros/carpetas del dueno estan preparados como contratos "
        "read-only; faltan datos vivos para llenar varias secciones sin inventar."
        if missing
        else "Los tableros/carpetas del dueno estan preparados con evidencia disponible."
    )
    return OwnerPackStatusReport(
        status_id="owner_pack_status_v1",
        headline="Owner Pack preparado en modo solo lectura",
        summary=summary,
        surfaces=surfaces,
        prompt_count=len(prompts_list),
        prepared_surface_count=len(surfaces),
        missing_evidence=missing,
        safety_summary={
            "writes_enabled": False,
            "write_handlers_invoked": 0,
            "approval_required_for_durable_outputs": True,
            "live_data_required_before_complete_claim": bool(missing),
        },
        execution_status=NOT_EXECUTED,
        writes_attempted=0,
        side_effects_detected=0,
        audit_language=OWNER_PACK_STATUS_ONLY,
    )


def owner_pack_status_contains_execution_claim(
    report: OwnerPackStatusReport,
) -> bool:
    payload = report.to_dict()
    text = str(payload).lower().replace(NOT_EXECUTED, "")
    unsafe_terms = (
        "creado",
        "actualizado",
        "ejecutado",
        "enviado",
        "publicado",
        "created",
        "updated",
        "executed",
        "published",
        "sent notification",
    )
    return any(term in text for term in unsafe_terms)


__all__ = [
    "OWNER_PACK_PREPARED",
    "OWNER_PACK_PREPARED_WITH_MISSING_EVIDENCE",
    "OWNER_PACK_STATUS_ONLY",
    "OwnerPackStatusReport",
    "OwnerPackSurfaceStatus",
    "build_owner_pack_status_report",
    "owner_pack_status_contains_execution_claim",
]
