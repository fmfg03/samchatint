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
) -> List[Dict[str, Any]]:
    """Build a deterministic visible step trace for specialist previews.

    The trace explains what the assistant did in read-only mode. It is UI-facing
    evidence of process, not an execution log and not an authority receipt.
    """

    live_lookup = bool(live_context.get("live_lookup_performed"))
    matched = bool(live_context.get("matched"))
    readiness = str(diagnostics.get("readiness") or "unknown")
    execution_status = str(preview_render.get("execution_status") or "not_executed")

    return [
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
        {
            "step_id": "diagnose_readiness",
            "title": "Diagnosticar preparacion",
            "status": "complete",
            "kind": "diagnostic",
            "summary": "Determine si hay contexto suficiente para seguir en preview read-only.",
            "inputs": ["understood_context", "live_context"],
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


def build_specialist_workspace_source_panel(
    *,
    understood_context: Mapping[str, Any],
    live_context: Mapping[str, Any],
    diagnostics: Mapping[str, Any],
    preview_render: Mapping[str, Any],
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
    return sources
