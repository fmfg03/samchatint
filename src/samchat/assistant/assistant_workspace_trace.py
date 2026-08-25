"""Workspace step/source trace builders for assistant operator UI."""

from __future__ import annotations

from typing import Any, Dict, List, Mapping


def _count_items(value: Any) -> int:
    if isinstance(value, list):
        return len(value)
    return 0


def _as_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def build_specialist_workspace_step_trace(
    *,
    task_id: str,
    understood_context: Mapping[str, Any],
    live_context: Mapping[str, Any],
    diagnostics: Mapping[str, Any],
    preview_render: Mapping[str, Any],
    memory_context: Mapping[str, Any] | None = None,
    continuity_context: Mapping[str, Any] | None = None,
    evidence_quality_gate: Mapping[str, Any] | None = None,
    resume_guidance: Mapping[str, Any] | None = None,
) -> List[Dict[str, Any]]:
    """Build a deterministic visible step trace for specialist previews.

    The trace explains what the assistant did in read-only mode. It is UI-facing
    evidence of process, not an execution log and not an authority receipt.
    """

    live_lookup = bool(live_context.get("live_lookup_performed"))
    matched = bool(live_context.get("matched"))
    readiness = str(diagnostics.get("readiness") or "unknown")
    execution_status = str(preview_render.get("execution_status") or "not_executed")

    steps: List[Dict[str, Any]] = [
        {
            "step_id": "understand_request",
            "title": "Entender solicitud",
            "status": "complete",
            "kind": "context",
            "summary": "Detecte referencias, dominios y entidades mencionadas por el usuario.",
            "inputs": ["user_message"],
            "outputs": ["understood_context"],
            "authority": understood_context.get("authority", "context_hint_only"),
            "data": {
                "document_refs": understood_context.get("document_refs") or [],
                "operations_refs": understood_context.get("operations_refs") or [],
                "domains": understood_context.get("domains") or [],
                "entities": understood_context.get("entities") or [],
            },
        },
        {
            "step_id": "read_live_context",
            "title": "Consultar contexto SamChat",
            "status": "complete" if live_lookup else "skipped",
            "kind": "evidence",
            "summary": (
                "Consulte fuentes gobernadas de SamChat en modo read-only."
                if live_lookup
                else "No hubo referencias suficientes o sesion disponible para lookup live."
            ),
            "inputs": ["understood_context"],
            "outputs": ["live_context"],
            "authority": live_context.get("authority", "read_only_context"),
            "data": {
                "matched": matched,
                "documents": _count_items(live_context.get("documents")),
                "expenses": _count_items(live_context.get("expenses")),
                "cfdis": _count_items(live_context.get("cfdis")),
                "unresolved": live_context.get("unresolved") or {},
            },
        },
    ]

    if continuity_context is not None:
        steps.append(
            {
                "step_id": "identify_case_continuity",
                "title": "Identificar continuidad",
                "status": "complete",
                "kind": "continuity",
                "summary": (
                    "Detecte un caso activo en la conversacion."
                    if continuity_context.get("matched")
                    else "No hay caso activo que retomar en esta conversacion."
                ),
                "inputs": ["conversation_metadata"],
                "outputs": ["continuity_context"],
                "authority": continuity_context.get("authority", "read_only_continuity"),
                "data": {
                    "matched": bool(continuity_context.get("matched")),
                    "status": continuity_context.get("status") or "unknown",
                    "active_case": continuity_context.get("active_case"),
                },
            }
        )

    if memory_context is not None:
        steps.append(
            {
                "step_id": "recall_case_memory",
                "title": "Recordar precedentes",
                "status": "complete" if memory_context.get("lookup_performed") else "skipped",
                "kind": "memory",
                "summary": (
                    "Consulte memoria de casos en modo read-only."
                    if memory_context.get("lookup_performed")
                    else "No hubo memoria consultable para este preview."
                ),
                "inputs": ["understood_context"],
                "outputs": ["memory_context"],
                "authority": memory_context.get("authority", "read_only_memory"),
                "data": {
                    "matched": bool(memory_context.get("matched")),
                    "snippets": _count_items(memory_context.get("snippets")),
                    "status": memory_context.get("status") or "unknown",
                },
            }
        )

    if evidence_quality_gate is not None:
        steps.append(
            {
                "step_id": "gate_evidence_quality",
                "title": "Evaluar calidad de evidencia",
                "status": evidence_quality_gate.get("quality_status") or "unknown",
                "kind": "evidence_gate",
                "summary": "Clasifique soporte, faltantes, precedentes y cambios sin evidencia vinculada.",
                "inputs": ["business_preview", "live_context", "diagnostics", "memory_context", "continuity_context"],
                "outputs": ["evidence_quality_gate"],
                "authority": evidence_quality_gate.get("authority", "read_only_evidence_gate"),
                "data": dict(evidence_quality_gate),
            }
        )

    if resume_guidance is not None:
        steps.append(
            {
                "step_id": "recommend_safe_next_step",
                "title": "Recomendar siguiente paso",
                "status": resume_guidance.get("status") or "unknown",
                "kind": "guidance",
                "summary": str(resume_guidance.get("recommendation") or "Siguiente paso no determinado."),
                "inputs": ["diagnostics", "continuity_context", "memory_context"],
                "outputs": ["resume_guidance"],
                "authority": resume_guidance.get("authority", "read_only_guidance"),
                "data": dict(resume_guidance),
            }
        )

    steps.extend(
        [
            {
                "step_id": "diagnose_readiness",
                "title": "Diagnosticar preparacion",
                "status": "complete",
                "kind": "diagnostic",
                "summary": "Determine si hay contexto suficiente para seguir en preview read-only.",
                "inputs": ["understood_context", "live_context", "continuity_context", "memory_context"],
                "outputs": ["diagnostics"],
                "authority": diagnostics.get("authority", "read_only_diagnostic"),
                "data": {
                    "readiness": readiness,
                    "findings": diagnostics.get("findings") or [],
                    "missing": diagnostics.get("missing") or [],
                    "risks": diagnostics.get("risks") or [],
                    "next_steps": diagnostics.get("next_steps") or [],
                },
            },
            {
                "step_id": "prepare_preview",
                "title": "Preparar preview especialista",
                "status": "blocked" if execution_status == "not_executed" else "complete",
                "kind": "preview",
                "summary": "Prepare un diff/propuesta inertizada para revision humana.",
                "inputs": ["verified_seed_or_live_context"],
                "outputs": ["preview_render"],
                "authority": "preview_only",
                "data": {
                    "task_id": task_id,
                    "preview_type": preview_render.get("preview_type"),
                    "sections": [
                        section.get("section_id")
                        for section in _as_list(preview_render.get("sections"))
                        if isinstance(section, Mapping)
                    ],
                    "execution_status": execution_status,
                    "primary_action_enabled": bool(preview_render.get("primary_action_enabled")),
                },
            },
            {
                "step_id": "hold_authority_boundary",
                "title": "Mantener frontera de autoridad",
                "status": "blocked",
                "kind": "authority",
                "summary": "La respuesta puede proponer y explicar, pero no ejecutar cambios.",
                "inputs": ["preview_render"],
                "outputs": ["human_review_required"],
                "authority": "human_approval_required",
                "data": {
                    "execution_allowed": False,
                    "writes_attempted": False,
                    "required_before_writes": [
                        "preview exacto",
                        "aprobacion humana",
                        "idempotency key",
                        "audit trail",
                    ],
                },
            },
        ]
    )
    return steps


def build_specialist_workspace_source_panel(
    *,
    understood_context: Mapping[str, Any],
    live_context: Mapping[str, Any],
    diagnostics: Mapping[str, Any],
    preview_render: Mapping[str, Any],
    memory_context: Mapping[str, Any] | None = None,
    continuity_context: Mapping[str, Any] | None = None,
    evidence_quality_gate: Mapping[str, Any] | None = None,
    resume_guidance: Mapping[str, Any] | None = None,
) -> List[Dict[str, Any]]:
    """Build source cards showing which sources informed the assistant."""

    sources: List[Dict[str, Any]] = [
        {
            "source_id": "user_message",
            "title": "Mensaje del usuario",
            "kind": "input",
            "status": "used",
            "summary": "Fuente primaria para referencias, entidades y dominios detectados.",
            "data": {
                "document_refs": understood_context.get("document_refs") or [],
                "operations_refs": understood_context.get("operations_refs") or [],
                "uuid_or_prefixes": understood_context.get("uuid_or_prefixes") or [],
                "account_codes": understood_context.get("account_codes") or [],
                "domains": understood_context.get("domains") or [],
            },
        },
        {
            "source_id": "samchat_db_readonly",
            "title": "SamChat DB read-only",
            "kind": "live_data",
            "status": live_context.get("status") or "unknown",
            "summary": "Consulta live limitada a referencias mencionadas; no cambia registros.",
            "data": {
                "documents": _count_items(live_context.get("documents")),
                "expenses": _count_items(live_context.get("expenses")),
                "cfdis": _count_items(live_context.get("cfdis")),
                "unresolved": live_context.get("unresolved") or {},
            },
        },
    ]

    if continuity_context is not None:
        sources.append(
            {
                "source_id": "conversation_continuity",
                "title": "Continuidad de conversacion",
                "kind": "continuity",
                "status": continuity_context.get("status") or "unknown",
                "summary": "Metadata activa de la conversacion actual; informa pero no autoriza.",
                "data": {
                    "matched": bool(continuity_context.get("matched")),
                    "active_case": continuity_context.get("active_case"),
                    "module_key": continuity_context.get("module_key"),
                    "tournament_key": continuity_context.get("tournament_key"),
                    "authority": continuity_context.get("authority", "read_only_continuity"),
                },
            }
        )

    if memory_context is not None:
        sources.append(
            {
                "source_id": "case_memory_readonly",
                "title": "Memoria de casos",
                "kind": "memory",
                "status": memory_context.get("status") or "unknown",
                "summary": "Precedentes operativos persistidos; informan pero no autorizan.",
                "data": {
                    "matched": bool(memory_context.get("matched")),
                    "snippets": _count_items(memory_context.get("snippets")),
                    "authority": memory_context.get("authority", "read_only_memory"),
                },
            }
        )

    if evidence_quality_gate is not None:
        sources.append(
            {
                "source_id": "deterministic_evidence_quality_gate",
                "title": "Calidad de evidencia",
                "kind": "evidence_gate",
                "status": evidence_quality_gate.get("quality_status") or "unknown",
                "summary": "Compuerta deterministica de soporte y faltantes; no ejecuta acciones.",
                "data": {
                    "supported_change_count": evidence_quality_gate.get("supported_change_count", 0),
                    "unbound_change_count": evidence_quality_gate.get("unbound_change_count", 0),
                    "missing_evidence_count": evidence_quality_gate.get("missing_evidence_count", 0),
                    "precedent_count": evidence_quality_gate.get("precedent_count", 0),
                    "safe_to_execute": bool(evidence_quality_gate.get("safe_to_execute")),
                    "authority": evidence_quality_gate.get("authority", "read_only_evidence_gate"),
                },
            }
        )

    if resume_guidance is not None:
        sources.append(
            {
                "source_id": "deterministic_resume_guidance",
                "title": "Guia de reanudacion",
                "kind": "guidance",
                "status": resume_guidance.get("status") or "unknown",
                "summary": "Recomendacion deterministica del siguiente paso seguro; no autoriza ejecucion.",
                "data": {
                    "recommended_mode": resume_guidance.get("recommended_mode"),
                    "uses_active_case": bool(resume_guidance.get("uses_active_case")),
                    "uses_case_memory": bool(resume_guidance.get("uses_case_memory")),
                    "primary_action_enabled": bool(resume_guidance.get("primary_action_enabled")),
                },
            }
        )

    sources.extend(
        [
            {
                "source_id": "deterministic_diagnostics",
                "title": "Diagnostico deterministico",
                "kind": "analysis",
                "status": diagnostics.get("readiness") or "unknown",
                "summary": "Reglas deterministicas para hallazgos, faltantes, riesgos y siguiente paso.",
                "data": {
                    "findings": diagnostics.get("findings") or [],
                    "missing": diagnostics.get("missing") or [],
                    "risks": diagnostics.get("risks") or [],
                },
            },
            {
                "source_id": "specialist_preview_contract",
                "title": "Contrato de preview especialista",
                "kind": "preview_contract",
                "status": preview_render.get("execution_status") or "not_executed",
                "summary": "Contrato estructurado que permite pintar proposed changes, evidencia y autoridad.",
                "data": {
                    "preview_id": preview_render.get("preview_id"),
                    "task_id": preview_render.get("task_id"),
                    "section_count": _count_items(preview_render.get("sections")),
                    "primary_action_enabled": bool(preview_render.get("primary_action_enabled")),
                },
            },
        ]
    )
    return sources



def build_assistant_work_turn_trace(
    *,
    work_frame: Mapping[str, Any],
    primary_tool: str,
    adjudication: Mapping[str, Any] | None,
    sufficiency: Mapping[str, Any] | None,
    rendered: bool,
) -> Dict[str, Any]:
    """Build a Claude-Code-like visible trace for a single assistant turn."""

    adjudication = adjudication or {}
    sufficiency = sufficiency or {}
    accepted = bool(adjudication.get("accepted", True))
    sufficient = bool(sufficiency.get("ok", True))
    steps = [
        {
            "step_id": "understand_work_frame",
            "title": "Entender solicitud",
            "status": "complete",
            "summary": work_frame.get("interpreted_goal") or "Solicitud interpretada.",
            "data": {
                "domain": work_frame.get("domain"),
                "task_kind": work_frame.get("task_kind"),
                "confidence": work_frame.get("confidence"),
                "required_evidence": work_frame.get("required_evidence") or [],
            },
        },
        {
            "step_id": "adjudicate_tool_candidate",
            "title": "Validar herramienta candidata",
            "status": "complete" if accepted else "blocked",
            "summary": adjudication.get("reason") or "Herramienta evaluada contra el trabajo solicitado.",
            "data": {
                "tool": primary_tool,
                "accepted": accepted,
                "rejected_interpretations": adjudication.get("rejected_interpretations") or [],
            },
        },
        {
            "step_id": "verify_answer_sufficiency",
            "title": "Verificar suficiencia",
            "status": "complete" if sufficient else "blocked",
            "summary": sufficiency.get("reason") or "Respuesta verificada contra la pregunta del usuario.",
            "data": {
                "safe_to_render": sufficient,
                "action": sufficiency.get("action"),
                "diagnostics": sufficiency.get("diagnostics") or {},
            },
        },
        {
            "step_id": "render_executive_answer",
            "title": "Redactar respuesta ejecutiva",
            "status": "complete" if rendered else "skipped",
            "summary": "Respuesta normalizada para usuario humano; sin payload crudo de herramienta.",
            "data": {"rendered": bool(rendered)},
        },
        {
            "step_id": "hold_read_only_boundary",
            "title": "Mantener frontera read-only",
            "status": "blocked",
            "summary": "No se ejecutan cambios sin preview, autorizacion, idempotencia y auditoria.",
            "data": {
                "writes_attempted": False,
                "authority_boundary": work_frame.get("authority_boundary") or "read_only",
            },
        },
    ]
    return {
        "tool": "assistant.work_turn_trace",
        "assistant_work_turn_trace": {
            "stage": "claude_code_like_work_loop",
            "steps": steps,
        },
        "result": {"steps": steps},
    }
