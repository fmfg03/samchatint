from __future__ import annotations

import inspect
import os
import re
import uuid
from dataclasses import replace
from pathlib import Path
from datetime import datetime
from types import SimpleNamespace
from typing import Any, Awaitable, Callable, Optional

from sqlalchemy import select

from devnous.gastos.models import AssistantMessage

from .action_router import supported_actions
from .analyst_case_persistence import persist_analyst_case
from .analyst_intent import (
    AnalystIntent,
    detect_analyst_intent,
    normalize_analyst_text,
)
from .analyst_live_evidence import (
    LiveEvidenceContext,
    LiveEvidenceRowsProvider,
    acquire_live_analyst_evidence,
    live_evidence_enabled_for_employee,
    live_evidence_limit_per_source,
)
from .analyst_response import build_analyst_trace, render_analyst_result
from .analyst_workbench import (
    AnalystEvidence,
    build_analyst_evidence_pack,
    extract_analyst_evidence_from_messages,
    extract_inline_analyst_evidence,
    run_analyst_workbench,
)
from .assistant_workspace_trace import (
    build_specialist_workspace_source_panel,
    build_specialist_workspace_step_trace,
)
from .capability_negotiation import (
    capability_negotiation_enabled,
    detect_capability_goal,
    evaluate_capability,
    receipt_workflow_writes_enabled,
    render_capability_response,
)
from .case_memory import (
    CASE_MEMORY_RESUME_COMMAND,
    CASE_MEMORY_SAVE_COMMAND,
    detect_case_memory_command,
    load_latest_case_memory_summary,
    persist_case_memory_summary,
    render_case_memory_resume_markdown,
    render_case_memory_saved_markdown,
)
from .document_confirmation import AsyncActionRouterExecutor
from .document_conversation import (
    extract_document_intake_result_from_text,
    handle_document_confirmation_command_async,
    parse_document_confirmation_command,
    render_document_intake_for_conversation,
)
from .document_classifier import CFDI_INVOICE, EXPENSE_RECEIPT
from .finance_query_intent import detect_finance_comparison_intent
from .finance_query_service import (
    FinanceRowsProvider,
    render_finance_comparison_result,
    run_read_only_comparison,
)
from .finance_accounting_qa import (
    detect_finance_accounting_qa_intent,
    render_finance_accounting_qa_answer,
)
from .finance_read_adapter import run_finance_read_adapter
from .receipt_workflow_draft import (
    advance_receipt_draft,
    start_receipt_draft,
)
from .request_intent import (
    detect_request_intent,
    normalize_request_text,
    is_owner_ai_context_request,
    is_owner_ai_readiness_request,
    owner_ai_tournament_slug_hint,
)
from .request_reports import ReadOnlyActionExecutor, run_read_only_report
from .request_response import build_request_trace, render_request_report
from .owner_needs_eval import OwnerNeedsPrompt, parse_owner_needs_eval_set
from .operator_workspace_resume import (
    build_operator_workspace_resume_response,
    detect_operator_workspace_resume_intent,
    load_latest_operator_workspace_snapshot,
    render_operator_workspace_resume_markdown,
)
from .operator_workspace_snapshot import build_operator_workspace_snapshot
from .owner_entity_folder_workspace import (
    build_owner_entity_folder_workspace_from_tournament_source,
)
from .owner_pack_live_evidence import resolve_owner_pack_live_evidence
from .owner_pack_readiness import (
    build_owner_pack_readiness_from_scope,
)
from .owner_pack_readiness_answer import render_owner_pack_readiness_answer
from .owner_pack_status import build_owner_pack_status_report
from .owner_variable_answer import render_owner_variable_query_answer
from .owner_variable_query import (
    OWNER_VARIABLE_UNMAPPED,
    build_owner_variable_query_report,
)
from .tournament_goal_source import (
    TournamentSourceAmbiguousError,
    TournamentSourceNotFoundError,
    inspect_tournament_source,
)
from .specialist_preview_surface import (
    detect_specialist_preview_task_id,
    render_specialist_preview_surface,
)
from .specialist_live_context import (
    build_specialist_preview_diagnostics,
    build_specialist_preview_workspace_cards,
    render_specialist_live_context_markdown,
    render_specialist_preview_diagnostics_markdown,
    resolve_specialist_preview_live_context,
)
from .specialist_continuity_context import (
    render_specialist_continuity_context_markdown,
    resolve_specialist_preview_continuity_context,
)
from .specialist_evidence_quality import (
    build_specialist_evidence_quality_gate,
    render_specialist_evidence_quality_gate_markdown,
)
from .specialist_memory_context import (
    render_specialist_memory_context_markdown,
    resolve_specialist_preview_memory_context,
)
from .specialist_resume_guidance import (
    build_specialist_resume_guidance,
    render_specialist_resume_guidance_markdown,
)
from .request_router import route_request
from .response_quality_gate import (
    evaluate_response_quality,
    render_quality_fallback,
)
from .work_frame import WorkFrame, build_work_frame

AssistantTurnFn = Callable[..., Awaitable[Any]]
AppendExportPromptFn = Callable[[str, Any], str]
ExplicitMessageFn = Callable[[str], bool]
PendingRunLoaderFn = Callable[..., Awaitable[Any]]
ConfirmPendingRunFn = Callable[..., Awaitable[Any]]
DeterministicPendingBuilderFn = Callable[..., Any]
DeterministicResponseBuilderFn = Callable[..., Awaitable[Any]]
MaybeAppendExportPromptFn = Callable[[str, Any], str]


def _document_writes_enabled() -> bool:
    value = os.getenv("ASSISTANT_AGENT_WRITES_ENABLED", "false")
    return value.strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _live_evidence_analyst_intent(
    raw_message: str,
    current_empleado: Any,
) -> Optional[AnalystIntent]:
    if not live_evidence_enabled_for_employee(getattr(current_empleado, "id", None)):
        return None
    intent = detect_analyst_intent(raw_message)
    if intent is None:
        return None
    if not intent.requires_operational_route:
        return intent
    route_hint = str(intent.operational_route_hint or "")
    normalized = normalize_analyst_text(raw_message)
    reference_tokens = re.findall(
        r"[a-z0-9][a-z0-9._/-]{2,}",
        normalized,
    )
    has_explicit_reference = any(
        any(separator in token for separator in "-_/")
        or (
            any(char.isalpha() for char in token)
            and any(char.isdigit() for char in token)
        )
        for token in reference_tokens
    )
    has_named_tournament = bool(
        re.search(
            r"\b(?:el|este|ese|un)\s+torneo\s+"
            r"(?!activo\b|actual\b|pendiente\b|que\b|sin\b)",
            normalized,
        )
    )
    has_explicit_named_target = (
        route_hint.startswith(("cfdi.", "payments.")) and has_explicit_reference
    ) or (route_hint.startswith("tournament.") and has_named_tournament)
    if (
        route_hint.startswith(("cfdi.", "payments.", "tournament."))
        and has_explicit_named_target
        and any(
            token in normalized for token in ("explicame", "explica", "que implica")
        )
    ):
        return replace(
            intent,
            analyst_intent="explain",
            confidence=0.86,
            requires_operational_route=False,
            operational_route_hint=None,
            context_requirements=[],
            missing_context=[],
            conflict_resolution={
                "selected_route": "analyst",
                "reason": "enabled_live_evidence_explanation",
                "operational_route_hint": None,
            },
        )
    return None


def _response_object(
    *,
    assistant_message: str,
    tool_trace: list[dict[str, Any]],
    run_id: Optional[str] = None,
    preview_render: Optional[dict[str, Any]] = None,
) -> Any:
    return SimpleNamespace(
        assistant_message=assistant_message,
        run_id=run_id or str(uuid.uuid4()),
        tool_trace=tool_trace,
        pending_confirmation=None,
        preview_render=preview_render,
    )


def _with_work_frame_trace(response: Any, work_frame: WorkFrame) -> Any:
    trace = {
        "assistant_work_frame": work_frame.to_dict(),
        "tool": "assistant.work_frame",
        "result": {
            "domain": work_frame.domain,
            "task_kind": work_frame.task_kind,
            "confidence": work_frame.confidence,
            "needs_clarification": work_frame.needs_clarification,
        },
    }
    response.tool_trace = list(getattr(response, "tool_trace", []) or []) + [trace]
    return response


def _owner_prompt_sources(raw_message: str) -> list[str]:
    normalized = (raw_message or "").lower()
    sources = {"owner_needs", "product_canon"}
    if any(token in normalized for token in ("equipo", "equipos")):
        sources.add("team")
    if any(token in normalized for token in ("jugador", "jugadores", "curp")):
        sources.add("player")
    if any(
        token in normalized
        for token in (
            "cfdi",
            "factura",
            "gasto",
            "pago",
            "costo",
            "finanza",
            "presupuesto",
            "proveedor",
        )
    ):
        sources.add("finance")
        sources.add("provider")
    if any(
        token in normalized
        for token in (
            "documento",
            "documentos",
            "evidencia",
            "fotografia",
            "fotograf\u00edas",
            "foto",
            "fotos",
        )
    ):
        sources.add("document")
        sources.add("media")
    if any(
        token in normalized
        for token in (
            "torneo",
            "fase",
            "nacional",
            "estatal",
            "entidad",
            "sede",
            "uniforme",
        )
    ):
        sources.add("tournament")
    if any(
        token in normalized
        for token in (
            "accidente",
            "ambulancia",
            "medico",
            "m\u00e9dico",
            "seguro",
            "traslado",
        )
    ):
        sources.add("medical/event_incident")
        sources.add("document")
    if any(
        token in normalized
        for token in ("crea", "crear", "actualiza", "genera", "prepara", "publica")
    ):
        sources.add("authority_preview")
    return sorted(sources)


def _owner_prompt_from_message(raw_message: str) -> OwnerNeedsPrompt:
    return OwnerNeedsPrompt(
        prompt_id="AI-OWNER-LIVE",
        prompt=raw_message,
        expected_sources=_owner_prompt_sources(raw_message),
        forbidden_behaviors=[
            "no inventar hechos operativos",
            "no ejecutar escrituras",
            "no crear carpetas sin aprobacion",
        ],
    )


def _owner_readiness_scope_from_message(raw_message: str) -> str:
    normalized = (raw_message or "").lower()
    if "activacion" in normalized or "marketing" in normalized:
        return "marketing_activation_report"
    if "fase nacional" in normalized or "nacional" in normalized:
        return "national_phase_folder"
    if "entidad" in normalized or "operador" in normalized or "carpeta" in normalized:
        return "entity_folder"
    return "all"


def _owner_readiness_entity_hint(raw_message: str) -> str | None:
    normalized = (raw_message or "").strip()
    markers = ("entidad", "operador", "carpeta de", "carpeta")
    lowered = normalized.lower()
    for marker in markers:
        idx = lowered.find(marker)
        if idx < 0:
            continue
        tail = normalized[idx + len(marker):].strip(" :,-?")
        if not tail:
            continue
        lowered_tail = tail.lower()
        if lowered_tail.startswith((
            "de copa",
            "para ",
            "sobre ",
            "del torneo",
            "en copa",
            "torneo ",
        )):
            return None
        cut_points = []
        separators = (
            ".",
            "?",
            "\n",
            " para ",
            " sobre ",
            " del torneo ",
            " de copa ",
            " en copa ",
            " torneo ",
        )
        for separator in separators:
            pos = lowered_tail.find(separator)
            if pos > 0:
                cut_points.append(pos)
        if cut_points:
            tail = tail[: min(cut_points)].strip(" :,-?")
        if tail:
            return tail[:80]
    return None


def _owner_entity_folder_workspace_intent(raw_message: str) -> bool:
    if _owner_readiness_scope_from_message(raw_message) != "entity_folder":
        return False
    if not owner_ai_tournament_slug_hint(raw_message):
        return False
    normalized = (raw_message or "").lower()
    return any(
        token in normalized
        for token in (
            "carpeta",
            "expediente",
            "entidad",
            "operador",
            "director general",
            "dueno",
            "due\u00f1o",
            "owner pack",
        )
    ) and (
        is_owner_ai_readiness_request(raw_message)
        or is_owner_ai_context_request(raw_message)
    )


def _owner_workspace_tournament_candidates(raw_message: str) -> list[str]:
    hint = owner_ai_tournament_slug_hint(raw_message) or ""
    candidates = [hint]
    if hint:
        candidates.append(hint.replace("-", " ").title())
    normalized = (raw_message or "").lower()
    if hint == "copa-telmex" or "copa telmex" in normalized:
        candidates.extend(
            [
                "Copa Telmex",
                "Copa Telmex Telcel",
                "Copa Telmex Telcel de Futbol",
            ]
        )
    if hint == "liga-telmex-telcel" or "liga telmex" in normalized:
        candidates.extend(["Liga Telmex", "Liga Telmex Telcel"])

    result: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        value = str(candidate or "").strip()
        key = value.casefold()
        if value and key not in seen:
            seen.add(key)
            result.append(value)
    return result


async def _inspect_owner_workspace_tournament_source(
    session: Any, raw_message: str
) -> Any | None:
    for candidate in _owner_workspace_tournament_candidates(raw_message):
        try:
            return await inspect_tournament_source(session, tournament_name=candidate)
        except (TournamentSourceNotFoundError, TournamentSourceAmbiguousError):
            continue
    return None


def _load_owner_needs_prompts() -> list[OwnerNeedsPrompt]:
    path = Path("docs/assistant/rqf-assistant-009e-evaluation-set.md")
    try:
        markdown = path.read_text(encoding="utf-8")
    except OSError:
        markdown = ""
    prompts = parse_owner_needs_eval_set(markdown)
    if prompts:
        return prompts
    return [_owner_prompt_from_message("Necesidades del dueno para todos los torneos")]


def _render_owner_entity_folder_workspace(workspace: Any) -> str:
    lines = [
        workspace.headline or "Owner Entity Folder Workspace",
        "",
        workspace.summary,
        "",
        f"Estado: {workspace.status}",
    ]
    target = workspace.target or {}
    target_bits = []
    if target.get("tournament_name"):
        target_bits.append(f"torneo={target['tournament_name']}")
    elif target.get("tournament_slug"):
        target_bits.append(f"torneo={target['tournament_slug']}")
    if target.get("entity_name"):
        target_bits.append(f"entidad={target['entity_name']}")
    if target.get("scope"):
        target_bits.append(f"scope={target['scope']}")
    if target_bits:
        lines.extend(["", "Objetivo revisado: " + " - ".join(target_bits)])

    lines.extend(["", "Tarjetas del workspace:"])
    for card in workspace.workspace_cards[:6]:
        lines.append(f"- {card.title}: {card.status} - {card.summary}")
        for item in card.items[:3]:
            lines.append(f"  - {item}")

    section_by_id = {section.section_id: section for section in workspace.folder_sections}

    def append_folder_drawer(title: str, section_id: str) -> None:
        section = section_by_id.get(section_id)
        lines.extend(["", f"{title}:"])
        if section is None:
            lines.append("- Sin evidencia viva suficiente para esta gaveta.")
            return
        lines.append(
            f"- Estado: {section.status} "
            f"({len(section.supported)} soportados / {len(section.missing)} faltantes)"
        )
        for item in section.supported[:5]:
            lines.append(f"- Soportado: {item}")
        for item in section.missing[:5]:
            lines.append(f"- Falta: {item}")

    append_folder_drawer("Operaciones", "operations")
    append_folder_drawer("Finanzas", "finance")

    lines.extend(["", "Secciones de la carpeta (diagnosticas):"])
    diagnostic_sections = [
        section
        for section in workspace.folder_sections
        if section.section_id not in {"operations", "finance"}
    ]
    if diagnostic_sections:
        for section in diagnostic_sections[:6]:
            lines.append(
                f"- {section.title}: {section.status} "
                f"({len(section.supported)} soportados / {len(section.missing)} faltantes)"
            )
    else:
        lines.append("- Sin secciones vivas adicionales para esta entidad.")

    lines.extend(["", "Evidencia encontrada:"])
    if workspace.evidence:
        lines.extend(f"- {item}" for item in workspace.evidence[:10])
    else:
        lines.append("- No hay evidencia viva suficiente para esta entidad/torneo.")

    lines.extend(["", "Faltantes para no inventar:"])
    if workspace.missing_fields:
        lines.extend(f"- {item}" for item in workspace.missing_fields[:12])
    else:
        lines.append("- Sin faltantes detectados en el workspace read-only.")

    if workspace.next_questions:
        lines.extend(["", "Preguntas sugeridas:"])
        lines.extend(f"- {item}" for item in workspace.next_questions[:4])

    if workspace.non_claims:
        lines.extend(["", "No-claims:"])
        lines.extend(f"- {item}" for item in workspace.non_claims[:4])

    blocked = list((workspace.preview or {}).get("blocked_actions") or [])
    lines.extend(["", "Preview / frontera de autoridad:"])
    lines.append(
        "- Workspace read-only; no crea carpetas, no exporta, "
        "no notifica y no modifica datos."
    )
    if blocked:
        lines.append("- Bloqueado sin autorizacion humana: " + ", ".join(blocked[:5]))
    lines.append("- El precedente y la memoria informan; no otorgan autoridad operativa.")
    return "\n".join(lines)


async def _build_owner_entity_folder_workspace_response(
    *,
    raw_message: str,
    conversation: Any,
    session: Any,
    maybe_append_export_prompt: MaybeAppendExportPromptFn,
) -> Optional[Any]:
    if not _owner_entity_folder_workspace_intent(raw_message):
        return None

    source = await _inspect_owner_workspace_tournament_source(session, raw_message)
    if source is None:
        return None

    prompts = _load_owner_needs_prompts()
    status_report = build_owner_pack_status_report(prompts)
    entity_name = _owner_readiness_entity_hint(raw_message)
    workspace = build_owner_entity_folder_workspace_from_tournament_source(
        source,
        status_report=status_report,
        entity_name=entity_name,
    )
    rendered = _render_owner_entity_folder_workspace(workspace)
    tool_trace = [
        {
            "owner_entity_folder_workspace": {
                "stage": "deterministic_read_only_owner_entity_folder_workspace",
                "workspace_id": workspace.workspace_id,
                "status": workspace.status,
                "provider_called": False,
                "writes_attempted": workspace.writes_attempted,
                "side_effects_detected": workspace.side_effects_detected,
                "approval_required": True,
            },
            "tool": "assistant_owner_entity_folder_workspace",
            "result": workspace.to_dict(),
        }
    ]
    rendered = maybe_append_export_prompt(rendered, tool_trace)
    await _persist_document_conversation_messages(
        raw_message=raw_message,
        assistant_message=rendered,
        conversation=conversation,
        session=session,
    )
    return _response_object(assistant_message=rendered, tool_trace=tool_trace)


def _owner_variable_query_intent(raw_message: str) -> bool:
    normalized = normalize_request_text(raw_message)
    if is_owner_ai_readiness_request(raw_message) or is_owner_ai_context_request(raw_message):
        return False
    if any(
        token in normalized
        for token in (
            "que falta",
            "prepara",
            "preparame",
            "crea",
            "crear",
            "cosas que necesito",
            "ya estan preparados",
        )
    ):
        return False

    report = build_owner_variable_query_report(question=raw_message)
    if report.status == OWNER_VARIABLE_UNMAPPED or not report.candidates:
        return False
    owner_context = any(
        token in normalized
        for token in (
            "director general",
            "direccion general",
            "dueno",
            "owner pack",
            "tablero",
            "carpeta",
        )
    )
    direct_question = any(
        token in normalized
        for token in (
            "que",
            "cuantos",
            "cuantas",
            "cuanto",
            "cuanta",
            "cual",
            "cuales",
            "quien",
            "quienes",
            "cuando",
            "donde",
        )
    )
    specific_candidates = [
        candidate
        for candidate in report.candidates
        if candidate.field not in {"tournament", "entity_name"}
    ]
    if not direct_question or not specific_candidates:
        return False

    high_signal_direct_fields = {
        "expected_teams",
        "real_teams",
        "players_by_category_age_gender",
        "round_progression",
        "state_phase_operations",
        "operator_payments",
        "equipment_costs",
        "visit_results",
        "photographic_evidence",
        "contracted_hotels_bed_nights",
        "contracted_meals",
        "sports_venue_and_fields",
        "medical_services_description",
        "accidents_with_transfers",
        "staff_travel_costs",
        "hotel_payments",
        "provider_payments",
        "medical_and_insurance_costs",
        "brand_activation_evidence",
        "brand_activation_activities",
        "physical_supplier_attendance",
        "sponsor_visitors",
        "activation_result",
    }
    has_high_signal_variable = any(
        candidate.field in high_signal_direct_fields and candidate.score >= 1
        for candidate in specific_candidates
    )
    # Owner variable questions are intentionally narrow: they must map to a
    # canonical Owner Pack field and be phrased as a factual question. Without
    # explicit owner context, only high-signal Owner Pack fields are intercepted;
    # this keeps Analyst Workbench prompts such as contract-risk reviews from
    # being captured only because they mention a generic word like "fecha".
    return owner_context or has_high_signal_variable


async def _build_owner_variable_query_response(
    *,
    raw_message: str,
    conversation: Any,
    session: Any,
    maybe_append_export_prompt: MaybeAppendExportPromptFn,
) -> Optional[Any]:
    if not _owner_variable_query_intent(raw_message):
        return None

    scope = _owner_readiness_scope_from_message(raw_message)
    tournament_hint = owner_ai_tournament_slug_hint(raw_message) or ""
    entity_name = _owner_readiness_entity_hint(raw_message)
    live_evidence = await resolve_owner_pack_live_evidence(
        session,
        scope=scope,
        tournament_hint=tournament_hint,
        entity_name=entity_name,
    )
    report = build_owner_variable_query_report(
        question=raw_message,
        live_reports=live_evidence.reports,
    )
    if report.status == OWNER_VARIABLE_UNMAPPED:
        return None

    answer = render_owner_variable_query_answer(report)
    tool_trace = [
        {
            "owner_variable_query": {
                "stage": "deterministic_read_only_owner_variable_query",
                "query_id": report.query_id,
                "status": report.status,
                "candidate_count": len(report.candidates),
                "resolution_count": len(report.resolutions),
                "provider_called": False,
                "writes_attempted": report.writes_attempted,
                "side_effects_detected": report.side_effects_detected,
                "live_evidence_status": live_evidence.status,
                "live_evidence_source": live_evidence.source,
                "live_evidence_reports": len(live_evidence.reports),
                "live_evidence_unresolved_reason": live_evidence.unresolved_reason,
            },
            "tool": "assistant_owner_variable_query",
            "live_evidence": live_evidence.to_dict(),
            "result": {
                **report.to_dict(),
                "conversation_answer": answer.to_dict(),
            },
        }
    ]
    rendered = maybe_append_export_prompt(answer.rendered_text, tool_trace)
    await _persist_document_conversation_messages(
        raw_message=raw_message,
        assistant_message=rendered,
        conversation=conversation,
        session=session,
        assistant_tool_payload={
            "owner_variable_query": report.to_dict(),
            "conversation_answer": answer.to_dict(),
            "live_evidence": live_evidence.to_dict(),
        },
    )
    return _response_object(assistant_message=rendered, tool_trace=tool_trace)


async def _build_owner_pack_readiness_response(
    *,
    raw_message: str,
    conversation: Any,
    session: Any,
    maybe_append_export_prompt: MaybeAppendExportPromptFn,
) -> Optional[Any]:
    if not (is_owner_ai_readiness_request(raw_message) or is_owner_ai_context_request(raw_message)):
        return None
    prompts = _load_owner_needs_prompts()
    status_report = build_owner_pack_status_report(prompts)
    scope = _owner_readiness_scope_from_message(raw_message)
    tournament_hint = owner_ai_tournament_slug_hint(raw_message) or ""
    entity_name = _owner_readiness_entity_hint(raw_message)
    live_evidence = await resolve_owner_pack_live_evidence(
        session,
        scope=scope,
        tournament_hint=tournament_hint,
        entity_name=entity_name,
    )
    report = build_owner_pack_readiness_from_scope(
        status_report=status_report,
        scope=scope,
        tournament_slug=tournament_hint,
        entity_name=entity_name,
        extra_live_reports=live_evidence.reports,
    )
    answer = render_owner_pack_readiness_answer(report)
    tool_trace = [
        {
            "owner_pack_readiness": {
                "stage": "deterministic_read_only_owner_pack_readiness",
                "readiness_id": report.readiness_id,
                "status": report.status,
                "readiness_score": report.readiness_score,
                "surface_count": len(report.surfaces),
                "provider_called": False,
                "writes_attempted": report.writes_attempted,
                "side_effects_detected": report.side_effects_detected,
                "approval_required": True,
                "live_evidence_status": live_evidence.status,
                "live_evidence_source": live_evidence.source,
                "live_evidence_reports": len(live_evidence.reports),
                "live_evidence_unresolved_reason": live_evidence.unresolved_reason,
            },
            "tool": "assistant_owner_pack_readiness",
            "live_evidence": live_evidence.to_dict(),
            "result": {
                **report.to_dict(),
                "conversation_answer": answer.to_dict(),
            },
        }
    ]
    rendered = maybe_append_export_prompt(answer.rendered_text, tool_trace)
    await _persist_document_conversation_messages(
        raw_message=raw_message,
        assistant_message=rendered,
        conversation=conversation,
        session=session,
        assistant_tool_payload={
            "owner_pack_readiness": report.to_dict(),
            "conversation_answer": answer.to_dict(),
            "live_evidence": live_evidence.to_dict(),
        },
    )
    return _response_object(assistant_message=rendered, tool_trace=tool_trace)


async def _build_specialist_preview_surface_response(
    *,
    raw_message: str,
    conversation: Any,
    session: Any,
) -> Optional[Any]:
    task_id = detect_specialist_preview_task_id(raw_message)
    if task_id is None:
        return None
    surface = render_specialist_preview_surface(task_id, raw_message=raw_message)
    continuity_context = resolve_specialist_preview_continuity_context(conversation)
    live_context = await resolve_specialist_preview_live_context(
        session, surface.understood_context
    )
    memory_context = await resolve_specialist_preview_memory_context(
        session=session,
        conversation=conversation,
        raw_message=raw_message,
        understood_context=surface.understood_context,
    )
    diagnostics = build_specialist_preview_diagnostics(
        task_id=task_id,
        understood_context=surface.understood_context,
        live_context=live_context,
    )
    business_preview_payload = (
        surface.business_preview.to_dict()
        if hasattr(surface.business_preview, "to_dict")
        else dict(surface.business_preview)
    )
    evidence_quality_gate = build_specialist_evidence_quality_gate(
        business_preview=business_preview_payload,
        live_context=live_context,
        diagnostics=diagnostics,
        memory_context=memory_context,
        continuity_context=continuity_context,
    )
    resume_guidance = build_specialist_resume_guidance(
        diagnostics=diagnostics,
        continuity_context=continuity_context,
        memory_context=memory_context,
    )
    workspace_cards = build_specialist_preview_workspace_cards(
        task_id=task_id,
        understood_context=surface.understood_context,
        live_context=live_context,
        diagnostics=diagnostics,
        preview_render=surface.preview_render.to_dict(),
        memory_context=memory_context,
        continuity_context=continuity_context,
        evidence_quality_gate=evidence_quality_gate,
        resume_guidance=resume_guidance,
    )
    step_trace = build_specialist_workspace_step_trace(
        task_id=task_id,
        understood_context=surface.understood_context,
        live_context=live_context,
        diagnostics=diagnostics,
        preview_render=surface.preview_render.to_dict(),
        memory_context=memory_context,
        continuity_context=continuity_context,
        evidence_quality_gate=evidence_quality_gate,
        resume_guidance=resume_guidance,
    )
    source_panel = build_specialist_workspace_source_panel(
        understood_context=surface.understood_context,
        live_context=live_context,
        diagnostics=diagnostics,
        preview_render=surface.preview_render.to_dict(),
        memory_context=memory_context,
        continuity_context=continuity_context,
        evidence_quality_gate=evidence_quality_gate,
        resume_guidance=resume_guidance,
    )
    preview_render_payload = surface.preview_render.to_dict()
    operator_workspace_snapshot = build_operator_workspace_snapshot(
        conversation_id=getattr(conversation, "id", ""),
        task_id=task_id,
        preview_render=preview_render_payload,
        business_preview=business_preview_payload,
        understood_context=surface.understood_context,
        live_context=live_context,
        continuity_context=continuity_context,
        memory_context=memory_context,
        diagnostics=diagnostics,
        evidence_quality_gate=evidence_quality_gate,
        resume_guidance=resume_guidance,
        workspace_cards=workspace_cards,
        step_trace=step_trace,
        source_panel=source_panel,
    )
    live_context_markdown = render_specialist_live_context_markdown(live_context)
    continuity_context_markdown = render_specialist_continuity_context_markdown(continuity_context)
    memory_context_markdown = render_specialist_memory_context_markdown(memory_context)
    diagnostics_markdown = render_specialist_preview_diagnostics_markdown(diagnostics)
    evidence_quality_markdown = render_specialist_evidence_quality_gate_markdown(evidence_quality_gate)
    resume_guidance_markdown = render_specialist_resume_guidance_markdown(resume_guidance)
    assistant_message = surface.assistant_message.replace(
        "\n# ",
        f"\n{live_context_markdown}\n{continuity_context_markdown}\n{memory_context_markdown}\n{diagnostics_markdown}\n{evidence_quality_markdown}\n{resume_guidance_markdown}\n# ",
        1,
    )
    tool_trace_entry = surface.tool_trace()
    tool_trace_entry["specialist_preview_surface"]["live_context"] = live_context
    tool_trace_entry["specialist_preview_surface"]["continuity_context"] = continuity_context
    tool_trace_entry["specialist_preview_surface"]["memory_context"] = memory_context
    tool_trace_entry["specialist_preview_surface"]["diagnostics"] = diagnostics
    tool_trace_entry["specialist_preview_surface"]["evidence_quality_gate"] = evidence_quality_gate
    tool_trace_entry["specialist_preview_surface"]["resume_guidance"] = resume_guidance
    tool_trace_entry["specialist_preview_surface"]["workspace_cards"] = workspace_cards
    tool_trace_entry["specialist_preview_surface"]["step_trace"] = step_trace
    tool_trace_entry["specialist_preview_surface"]["source_panel"] = source_panel
    tool_trace_entry["specialist_preview_surface"]["operator_workspace_snapshot"] = operator_workspace_snapshot
    tool_trace_entry["result"]["live_context"] = live_context
    tool_trace_entry["result"]["continuity_context"] = continuity_context
    tool_trace_entry["result"]["memory_context"] = memory_context
    tool_trace_entry["result"]["diagnostics"] = diagnostics
    tool_trace_entry["result"]["evidence_quality_gate"] = evidence_quality_gate
    tool_trace_entry["result"]["resume_guidance"] = resume_guidance
    tool_trace_entry["result"]["workspace_cards"] = workspace_cards
    tool_trace_entry["result"]["step_trace"] = step_trace
    tool_trace_entry["result"]["source_panel"] = source_panel
    tool_trace_entry["result"]["operator_workspace_snapshot"] = operator_workspace_snapshot
    tool_trace = [tool_trace_entry]
    preview_render = preview_render_payload
    business_preview = business_preview_payload
    await _persist_document_conversation_messages(
        raw_message=raw_message,
        assistant_message=assistant_message,
        conversation=conversation,
        session=session,
        assistant_tool_payload={
            "preview_render": preview_render,
            "business_preview": business_preview,
            "understood_context": surface.understood_context,
            "live_context": live_context,
            "continuity_context": continuity_context,
            "memory_context": memory_context,
            "diagnostics": diagnostics,
            "evidence_quality_gate": evidence_quality_gate,
            "resume_guidance": resume_guidance,
            "workspace_cards": workspace_cards,
            "step_trace": step_trace,
            "source_panel": source_panel,
            "operator_workspace_snapshot": operator_workspace_snapshot,
        },
    )
    return _response_object(
        assistant_message=assistant_message,
        tool_trace=tool_trace,
        preview_render=preview_render,
    )


async def _build_case_memory_response(
    *,
    raw_message: str,
    conversation: Any,
    current_empleado: Any,
    session: Any,
) -> Optional[Any]:
    command = detect_case_memory_command(raw_message)
    if command is None:
        return None

    if command == CASE_MEMORY_SAVE_COMMAND:
        result = await persist_case_memory_summary(
            session,
            conversation_id=str(conversation.id),
            created_by_empleado_id=str(current_empleado.id),
        )
        rendered = render_case_memory_saved_markdown(result)
        tool_trace = [
            {
                "tool": "assistant_case_memory_save",
                "case_memory": result,
                "provider_called": False,
                "writes_attempted": False,
                "operational_writes": False,
                "safe_to_execute": False,
            }
        ]
        await _persist_document_conversation_messages(
            raw_message=raw_message,
            assistant_message=rendered,
            conversation=conversation,
            session=session,
            assistant_tool_payload={"case_memory_save": result},
        )
        return _response_object(assistant_message=rendered, tool_trace=tool_trace)

    if command == CASE_MEMORY_RESUME_COMMAND:
        resolution = await load_latest_case_memory_summary(
            session,
            conversation_id=str(conversation.id),
        )
        rendered = render_case_memory_resume_markdown(resolution)
        tool_trace = [
            {
                "tool": "assistant_case_memory_resume",
                "case_memory_resume": resolution,
                "provider_called": False,
                "writes_attempted": False,
                "operational_writes": False,
                "safe_to_execute": False,
            }
        ]
        await _persist_document_conversation_messages(
            raw_message=raw_message,
            assistant_message=rendered,
            conversation=conversation,
            session=session,
            assistant_tool_payload={"case_memory_resume": resolution},
        )
        return _response_object(assistant_message=rendered, tool_trace=tool_trace)

    return None


async def _build_operator_workspace_resume_response(
    *,
    raw_message: str,
    conversation: Any,
    session: Any,
) -> Optional[Any]:
    if not detect_operator_workspace_resume_intent(raw_message):
        return None

    resolution = await load_latest_operator_workspace_snapshot(
        session=session,
        conversation_id=conversation.id,
    )
    resume = build_operator_workspace_resume_response(resolution)
    rendered = render_operator_workspace_resume_markdown(resume)
    tool_trace = [
        {
            "tool": "assistant_operator_workspace_resume",
            "operator_workspace_resume": resume,
            "provider_called": False,
            "writes_attempted": False,
            "safe_to_execute": False,
        }
    ]
    await _persist_document_conversation_messages(
        raw_message=raw_message,
        assistant_message=rendered,
        conversation=conversation,
        session=session,
        assistant_tool_payload={"operator_workspace_resume": resume},
    )
    return _response_object(assistant_message=rendered, tool_trace=tool_trace)


async def _persist_document_conversation_messages(
    *,
    raw_message: str,
    assistant_message: str,
    conversation: Any,
    session: Any,
    assistant_tool_payload: Optional[dict[str, Any]] = None,
) -> None:
    session.add(
        AssistantMessage(
            conversation_id=conversation.id,
            role="user",
            content=raw_message,
            tool_name=None,
            tool_payload=None,
        )
    )
    session.add(
        AssistantMessage(
            conversation_id=conversation.id,
            role="assistant",
            content=assistant_message,
            tool_name=None,
            tool_payload=assistant_tool_payload,
        )
    )
    conversation.updated_at = datetime.utcnow()
    await session.commit()


async def _latest_document_intake_result(
    *,
    session: Any,
    conversation_id: Any,
    limit: int = 30,
) -> Optional[dict[str, Any]]:
    rows = (
        await session.execute(
            select(AssistantMessage)
            .where(AssistantMessage.conversation_id == conversation_id)
            .order_by(AssistantMessage.created_at.desc())
            .limit(limit)
        )
    ).scalars()
    for message in rows:
        intake = extract_document_intake_result_from_text(message.content or "")
        if intake is not None:
            return intake
    return None


async def _latest_analyst_evidence(
    *,
    session: Any,
    conversation_id: Any,
    limit: int = 20,
) -> list[AnalystEvidence]:
    try:
        rows = (
            await session.execute(
                select(AssistantMessage)
                .where(AssistantMessage.conversation_id == conversation_id)
                .order_by(AssistantMessage.created_at.desc())
                .limit(limit)
            )
        ).scalars()
    except Exception:
        return []
    return extract_analyst_evidence_from_messages(rows)


async def _build_document_upload_response(
    *,
    raw_message: str,
    conversation: Any,
    session: Any,
    maybe_append_export_prompt: MaybeAppendExportPromptFn,
) -> Optional[Any]:
    intake = extract_document_intake_result_from_text(raw_message)
    if intake is None:
        return None
    if intake.get("detected_document_type") in {
        EXPENSE_RECEIPT,
        CFDI_INVOICE,
    }:
        try:
            start_receipt_draft(conversation=conversation, intake=intake)
        except ValueError:
            rendered = (
                "No pude vincular el comprobante porque la evidencia cargada cambió. "
                "Vuelve a adjuntar el archivo; no registré cambios."
            )
            tool_trace = [
                {
                    "receipt_workflow_draft": {
                        "status": "evidence_mismatch",
                        "writes_attempted": False,
                    }
                }
            ]
            await _persist_document_conversation_messages(
                raw_message=raw_message,
                assistant_message=rendered,
                conversation=conversation,
                session=session,
            )
            return _response_object(
                assistant_message=rendered,
                tool_trace=tool_trace,
            )
    rendered = render_document_intake_for_conversation(intake)
    tool_trace = [
        {
            "document_intake_live_wiring": {
                "stage": "upload_render",
                "detected_document_type": intake.get("detected_document_type"),
                "proposed_action_count": len(intake.get("proposed_actions") or []),
                "missing_field_count": len(intake.get("missing_fields") or []),
                "provider_called": False,
            }
        }
    ]
    rendered = maybe_append_export_prompt(rendered, tool_trace)
    await _persist_document_conversation_messages(
        raw_message=raw_message,
        assistant_message=rendered,
        conversation=conversation,
        session=session,
    )
    return _response_object(assistant_message=rendered, tool_trace=tool_trace)


async def _build_document_confirmation_response(
    *,
    raw_message: str,
    conversation: Any,
    session: Any,
    maybe_append_export_prompt: MaybeAppendExportPromptFn,
    document_action_router_executor: Optional[AsyncActionRouterExecutor],
) -> Optional[Any]:
    command = parse_document_confirmation_command(raw_message)
    if command is None:
        return None
    intake = await _latest_document_intake_result(
        session=session,
        conversation_id=conversation.id,
    )
    if intake is None:
        message = (
            "No encontre una accion documental propuesta en esta "
            "conversacion. "
            "Sube el documento de nuevo o confirma desde el mensaje que "
            "contiene "
            "el proposed_action_id."
        )
        tool_trace = [
            {
                "document_confirmation_live_wiring": {
                    "stage": "confirmation",
                    "status": "rejected",
                    "blocked_reason": "document_intake_context_missing",
                    "provider_called": False,
                }
            }
        ]
        message = maybe_append_export_prompt(message, tool_trace)
        await _persist_document_conversation_messages(
            raw_message=raw_message,
            assistant_message=message,
            conversation=conversation,
            session=session,
        )
        return _response_object(
            assistant_message=message,
            tool_trace=tool_trace,
        )

    result = await handle_document_confirmation_command_async(
        text=raw_message,
        intake_result=intake,
        supported_actions=supported_actions(),
        writes_enabled=_document_writes_enabled(),
        action_router_executor=document_action_router_executor,
    )
    tool_trace = [
        {
            "document_confirmation_live_wiring": {
                "stage": "confirmation",
                "status": result.status,
                "blocked_reason": result.blocked_reason,
                "executed": result.executed,
                "provider_called": False,
                "confirmation": result.confirmation,
            }
        }
    ]
    message = maybe_append_export_prompt(result.message, tool_trace)
    await _persist_document_conversation_messages(
        raw_message=raw_message,
        assistant_message=message,
        conversation=conversation,
        session=session,
    )
    return _response_object(assistant_message=message, tool_trace=tool_trace)


async def _build_finance_comparison_response(
    *,
    raw_message: str,
    conversation: Any,
    session: Any,
    maybe_append_export_prompt: MaybeAppendExportPromptFn,
    finance_rows_provider: Optional[FinanceRowsProvider] = None,
) -> Optional[Any]:
    intent = detect_finance_comparison_intent(raw_message)
    if intent is None:
        return None

    result = await run_read_only_comparison(
        intent=intent,
        session=session,
        rows_provider=finance_rows_provider,
    )
    rendered = render_finance_comparison_result(result)
    trace_result: dict[str, Any] = {
        "status": result.status,
        "source": result.source,
        "row_count": len(result.rows),
        "exportable": result.exportable,
    }
    if result.exportable and result.rows:
        trace_result["rows"] = result.rows

    tool_trace = [
        {
            "finance_query_live_wiring": {
                "stage": "deterministic_read_only_comparison",
                "metric": intent.metric,
                "years": intent.years,
                "group_by": intent.group_by,
                "comparison": intent.comparison,
                "status": result.status,
                "source": result.source,
                "row_count": len(result.rows),
                "provider_called": False,
                "writes_attempted": False,
            },
            "tool": "finance.read_only_comparison",
            "result": trace_result,
        }
    ]
    rendered = maybe_append_export_prompt(rendered, tool_trace)
    await _persist_document_conversation_messages(
        raw_message=raw_message,
        assistant_message=rendered,
        conversation=conversation,
        session=session,
    )
    return _response_object(assistant_message=rendered, tool_trace=tool_trace)



def _finance_platform_read_intent(raw_message: str) -> bool:
    """Detect finance/accounting Q and A that can be answered from canonical reads."""

    return detect_finance_accounting_qa_intent(raw_message) is not None


async def _build_finance_platform_read_response(
    *,
    raw_message: str,
    conversation: Any,
    session: Any,
    maybe_append_export_prompt: MaybeAppendExportPromptFn,
) -> Optional[Any]:
    intent = detect_finance_accounting_qa_intent(raw_message)
    if intent is None:
        return None

    result = await run_finance_read_adapter(
        session,
        intent="finance.platform",
        year=datetime.now().year,
        month=datetime.now().month,
        limit=500,
    )
    rendered = render_finance_accounting_qa_answer(result=result, intent=intent)
    tool_trace = [
        {
            "assistant_finance_accounting_qa": {
                "stage": "deterministic_read_only_finance_accounting_qa",
                "intent": "finance.platform",
                "question_type": intent.question_type,
                "confidence": intent.confidence,
                "reason": intent.reason,
                "source_function": result.get("source_function"),
                "ok": bool(result.get("ok")),
                "read_only": True,
                "provider_called": False,
                "writes_attempted": False,
                "operational_writes": False,
            },
            "tool": "assistant_finance_accounting_qa",
            "result": {
                "intent": result.get("intent"),
                "question_type": intent.question_type,
                "ok": bool(result.get("ok")),
                "source_notes": result.get("source_notes") or [],
                "safety_labels": result.get("safety_labels") or [],
            },
        }
    ]
    rendered = maybe_append_export_prompt(rendered, tool_trace)
    await _persist_document_conversation_messages(
        raw_message=raw_message,
        assistant_message=rendered,
        conversation=conversation,
        session=session,
    )
    return _response_object(assistant_message=rendered, tool_trace=tool_trace)


async def _build_request_intelligence_response(
    *,
    raw_message: str,
    conversation: Any,
    session: Any,
    maybe_append_export_prompt: MaybeAppendExportPromptFn,
    action_executor: Optional[ReadOnlyActionExecutor] = None,
    finance_rows_provider: Optional[FinanceRowsProvider] = None,
) -> Optional[Any]:
    intent = detect_request_intent(raw_message)
    if intent.domain == "unknown":
        return None

    route = route_request(intent)
    result = await run_read_only_report(
        intent=intent,
        route=route,
        session=session,
        finance_rows_provider=finance_rows_provider,
        action_executor=action_executor,
    )
    rendered = render_request_report(intent=intent, route=route, result=result)
    tool_trace = build_request_trace(intent=intent, route=route, result=result)
    rendered = maybe_append_export_prompt(rendered, tool_trace)
    await _persist_document_conversation_messages(
        raw_message=raw_message,
        assistant_message=rendered,
        conversation=conversation,
        session=session,
    )
    return _response_object(assistant_message=rendered, tool_trace=tool_trace)


async def _build_capability_negotiation_response(
    *,
    raw_message: str,
    conversation: Any,
    current_empleado: Any,
    session: Any,
) -> Optional[Any]:
    if not capability_negotiation_enabled(getattr(current_empleado, "id", None)):
        return None
    goal = detect_capability_goal(raw_message)
    if goal is None:
        return None
    evaluation = evaluate_capability(
        goal,
        supported_actions=supported_actions(),
        role=getattr(current_empleado, "rol", None),
        flags={
            "ASSISTANT_RECEIPT_WORKFLOW_WRITES_ENABLED": receipt_workflow_writes_enabled(
                getattr(current_empleado, "id", None)
            )
        },
    )
    rendered = render_capability_response(goal, evaluation)
    tool_trace = [
        {
            "capability_negotiation": {
                "stage": "capability_inquiry",
                "goal": goal.to_dict(),
                "evaluation": evaluation.to_trace(),
                "operational_tools_called": 0,
                "writes_attempted": False,
            }
        }
    ]
    await _persist_document_conversation_messages(
        raw_message=raw_message,
        assistant_message=rendered,
        conversation=conversation,
        session=session,
    )
    return _response_object(assistant_message=rendered, tool_trace=tool_trace)


async def _build_analyst_workbench_response(
    *,
    raw_message: str,
    conversation: Any,
    current_empleado: Any,
    session: Any,
    maybe_append_export_prompt: MaybeAppendExportPromptFn,
    live_evidence_rows_provider: Optional[LiveEvidenceRowsProvider] = None,
    require_live_evidence: bool = False,
) -> Optional[Any]:
    intent = _live_evidence_analyst_intent(
        raw_message, current_empleado
    ) or detect_analyst_intent(raw_message)
    if intent is None or intent.requires_operational_route:
        return None

    inline_evidence = extract_inline_analyst_evidence(raw_message, intent)
    history_evidence = await _latest_analyst_evidence(
        session=session,
        conversation_id=conversation.id,
    )
    live_acquisition = await acquire_live_analyst_evidence(
        context=LiveEvidenceContext(
            employee_id=getattr(current_empleado, "id", None),
            role=str(getattr(current_empleado, "rol", "") or ""),
            permissions=set(getattr(current_empleado, "permissions", set()) or set()),
            question=raw_message,
            department=getattr(current_empleado, "departamento", None),
            limit_per_source=live_evidence_limit_per_source(),
        ),
        intent=intent,
        rows_provider=live_evidence_rows_provider,
    )
    if require_live_evidence and not live_acquisition.collection.evidence:
        return None
    live_evidence_signatures = {
        (
            item.source_type,
            item.source,
            item.source_id,
            item.reference,
            item.label,
            item.summary,
            item.date,
        )
        for item in live_acquisition.collection.evidence
    }
    evidence = build_analyst_evidence_pack(
        live_evidence=live_acquisition.collection.evidence,
        inline_evidence=inline_evidence,
        history_evidence=history_evidence,
        intent=intent,
    )
    result = await run_analyst_workbench(
        intent=intent,
        evidence=evidence,
        live_evidence_used=any(
            (
                item.source_type,
                item.source,
                item.source_id,
                item.reference,
                item.label,
                item.summary,
                item.date,
            )
            in live_evidence_signatures
            for item in evidence
        ),
    )
    if live_acquisition.collection.caveats:
        result = replace(
            result,
            caveats=list(
                dict.fromkeys(live_acquisition.collection.caveats + result.caveats)
            ),
        )
    rendered = render_analyst_result(result)
    tool_trace = build_analyst_trace(intent=intent, result=result)
    if live_acquisition.enabled:
        tool_trace[0]["analyst_live_evidence"] = live_acquisition.trace()
    case_persistence = await persist_analyst_case(
        session=session,
        conversation_id=str(conversation.id),
        current_empleado=current_empleado,
        question=raw_message,
        intent=intent,
        result=result,
    )
    if case_persistence.enabled:
        tool_trace[0]["analyst_case_persistence"] = case_persistence.trace()
    rendered = maybe_append_export_prompt(rendered, tool_trace)
    await _persist_document_conversation_messages(
        raw_message=raw_message,
        assistant_message=rendered,
        conversation=conversation,
        session=session,
    )
    return _response_object(assistant_message=rendered, tool_trace=tool_trace)


async def run_conversation_turn(
    *,
    raw_message: str,
    conversation: Any,
    current_empleado: Any,
    session: Any,
    request: Any,
    tournament_key: Optional[str],
    bi_year: Optional[int],
    bi_scope: Optional[str],
    bi_segment: Optional[str],
    assistant_mode: Optional[str],
    openai_api_key: Optional[str],
    assistant_turn: AssistantTurnFn,
    maybe_append_export_prompt: AppendExportPromptFn,
    document_action_router_executor: Optional[AsyncActionRouterExecutor] = None,
    finance_rows_provider: Optional[FinanceRowsProvider] = None,
    live_evidence_rows_provider: Optional[LiveEvidenceRowsProvider] = None,
) -> Any:
    work_frame = build_work_frame(raw_message)

    document_response = await _build_document_upload_response(
        raw_message=raw_message,
        conversation=conversation,
        session=session,
        maybe_append_export_prompt=maybe_append_export_prompt,
    )
    if document_response is not None:
        return _with_work_frame_trace(document_response, work_frame)

    document_response = await _build_document_confirmation_response(
        raw_message=raw_message,
        conversation=conversation,
        session=session,
        maybe_append_export_prompt=maybe_append_export_prompt,
        document_action_router_executor=document_action_router_executor,
    )
    if document_response is not None:
        return _with_work_frame_trace(document_response, work_frame)

    if _live_evidence_analyst_intent(raw_message, current_empleado) is not None:
        analyst_response = await _build_analyst_workbench_response(
            raw_message=raw_message,
            conversation=conversation,
            current_empleado=current_empleado,
            session=session,
            maybe_append_export_prompt=maybe_append_export_prompt,
            live_evidence_rows_provider=live_evidence_rows_provider,
            require_live_evidence=True,
        )
        if analyst_response is not None:
            return _with_work_frame_trace(analyst_response, work_frame)

    capability_response = await _build_capability_negotiation_response(
        raw_message=raw_message,
        conversation=conversation,
        current_empleado=current_empleado,
        session=session,
    )
    if capability_response is not None:
        return _with_work_frame_trace(capability_response, work_frame)

    case_memory_response = await _build_case_memory_response(
        raw_message=raw_message,
        conversation=conversation,
        current_empleado=current_empleado,
        session=session,
    )
    if case_memory_response is not None:
        return _with_work_frame_trace(case_memory_response, work_frame)

    workspace_resume_response = await _build_operator_workspace_resume_response(
        raw_message=raw_message,
        conversation=conversation,
        session=session,
    )
    if workspace_resume_response is not None:
        return _with_work_frame_trace(workspace_resume_response, work_frame)

    owner_entity_workspace_response = await _build_owner_entity_folder_workspace_response(
        raw_message=raw_message,
        conversation=conversation,
        session=session,
        maybe_append_export_prompt=maybe_append_export_prompt,
    )
    if owner_entity_workspace_response is not None:
        return _with_work_frame_trace(owner_entity_workspace_response, work_frame)

    specialist_preview_response = await _build_specialist_preview_surface_response(
        raw_message=raw_message,
        conversation=conversation,
        session=session,
    )
    if specialist_preview_response is not None:
        return _with_work_frame_trace(specialist_preview_response, work_frame)

    owner_variable_response = await _build_owner_variable_query_response(
        raw_message=raw_message,
        conversation=conversation,
        session=session,
        maybe_append_export_prompt=maybe_append_export_prompt,
    )
    if owner_variable_response is not None:
        return _with_work_frame_trace(owner_variable_response, work_frame)

    owner_readiness_response = await _build_owner_pack_readiness_response(
        raw_message=raw_message,
        conversation=conversation,
        session=session,
        maybe_append_export_prompt=maybe_append_export_prompt,
    )
    if owner_readiness_response is not None:
        return _with_work_frame_trace(owner_readiness_response, work_frame)


    request_response = await _build_request_intelligence_response(
        raw_message=raw_message,
        conversation=conversation,
        session=session,
        maybe_append_export_prompt=maybe_append_export_prompt,
        action_executor=document_action_router_executor,
        finance_rows_provider=finance_rows_provider,
    )
    if request_response is not None:
        return _with_work_frame_trace(request_response, work_frame)

    finance_response = await _build_finance_comparison_response(
        raw_message=raw_message,
        conversation=conversation,
        session=session,
        maybe_append_export_prompt=maybe_append_export_prompt,
        finance_rows_provider=finance_rows_provider,
    )
    if finance_response is not None:
        return _with_work_frame_trace(finance_response, work_frame)

    finance_platform_response = await _build_finance_platform_read_response(
        raw_message=raw_message,
        conversation=conversation,
        session=session,
        maybe_append_export_prompt=maybe_append_export_prompt,
    )
    if finance_platform_response is not None:
        return _with_work_frame_trace(finance_platform_response, work_frame)

    analyst_response = await _build_analyst_workbench_response(
        raw_message=raw_message,
        conversation=conversation,
        current_empleado=current_empleado,
        session=session,
        maybe_append_export_prompt=maybe_append_export_prompt,
        live_evidence_rows_provider=live_evidence_rows_provider,
    )
    if analyst_response is not None:
        return _with_work_frame_trace(analyst_response, work_frame)

    response = await assistant_turn(
        raw_message=raw_message,
        conversation=conversation,
        current_empleado=current_empleado,
        session=session,
        request=request,
        tournament_key=tournament_key,
        bi_year=bi_year,
        bi_scope=bi_scope,
        bi_segment=bi_segment,
        assistant_mode=assistant_mode,
        openai_api_key=openai_api_key,
    )
    response.assistant_message = maybe_append_export_prompt(
        response.assistant_message,
        response.tool_trace,
    )
    quality = evaluate_response_quality(response.assistant_message)
    if not quality.ok:
        fallback_message = render_quality_fallback(
            user_message=raw_message,
            reason=quality.reason,
        )
        quality_trace = {
            "assistant_response_quality_gate": {
                "stage": "post_response_display_guard",
                "status": "blocked",
                "reason": quality.reason,
                "diagnostics": quality.diagnostics or {},
                "provider_response_replaced": True,
                "writes_attempted": False,
            },
            "tool": "assistant.response_quality_gate",
            "result": {
                "ok": False,
                "reason": quality.reason,
            },
        }
        response.tool_trace = list(response.tool_trace or []) + [quality_trace]
        response.assistant_message = maybe_append_export_prompt(
            fallback_message,
            response.tool_trace,
        )
    return _with_work_frame_trace(response, work_frame)


async def run_message_turn_with_pending(
    *,
    raw_message: str,
    conversation: Any,
    current_empleado: Any,
    session: Any,
    request: Any,
    tournament_key: Optional[str],
    bi_year: Optional[int],
    bi_scope: Optional[str],
    bi_segment: Optional[str],
    assistant_mode: Optional[str],
    openai_api_key: Optional[str],
    latest_pending_run_for_conversation: PendingRunLoaderFn,
    is_explicit_approval_message: ExplicitMessageFn,
    is_explicit_rejection_message: ExplicitMessageFn,
    confirm_pending_run: ConfirmPendingRunFn,
    deterministic_pending_builders: list[DeterministicPendingBuilderFn],
    build_deterministic_pending_response: DeterministicResponseBuilderFn,
    assistant_turn: AssistantTurnFn,
    maybe_append_export_prompt: AppendExportPromptFn,
    document_action_router_executor: Optional[AsyncActionRouterExecutor] = None,
    finance_rows_provider: Optional[FinanceRowsProvider] = None,
    live_evidence_rows_provider: Optional[LiveEvidenceRowsProvider] = None,
) -> Any:
    work_frame = build_work_frame(raw_message)

    pending_run = await latest_pending_run_for_conversation(
        session=session,
        conversation_id=conversation.id,
        empleado_id=current_empleado.id,
    )
    if pending_run is not None:
        if is_explicit_approval_message(raw_message):
            response = await confirm_pending_run(
                run=pending_run,
                conversation=conversation,
                approve=True,
                assistant_mode=assistant_mode,
                openai_api_key=openai_api_key,
                current_empleado=current_empleado,
                session=session,
            )
            response.assistant_message = maybe_append_export_prompt(
                response.assistant_message,
                response.tool_trace,
            )
            return _with_work_frame_trace(response, work_frame)
        if is_explicit_rejection_message(raw_message):
            response = await confirm_pending_run(
                run=pending_run,
                conversation=conversation,
                approve=False,
                assistant_mode=assistant_mode,
                openai_api_key=openai_api_key,
                current_empleado=current_empleado,
                session=session,
            )
            response.assistant_message = maybe_append_export_prompt(
                response.assistant_message,
                response.tool_trace,
            )
            return _with_work_frame_trace(response, work_frame)

    receipt_advance = await advance_receipt_draft(
        raw_message=raw_message,
        conversation=conversation,
        employee_id=current_empleado.id,
        session=session,
        writes_enabled=receipt_workflow_writes_enabled(current_empleado.id),
        bi_year=bi_year,
        bi_scope=bi_scope,
    )
    if receipt_advance is not None:
        if receipt_advance.pending is not None:
            response = await build_deterministic_pending_response(
                deterministic_pending=receipt_advance.pending,
                raw_message=raw_message,
                conversation=conversation,
                current_empleado=current_empleado,
                session=session,
            )
            return _with_work_frame_trace(response, work_frame)
        await _persist_document_conversation_messages(
            raw_message=raw_message,
            assistant_message=receipt_advance.message,
            conversation=conversation,
            session=session,
        )
        return _with_work_frame_trace(_response_object(
            assistant_message=receipt_advance.message,
            tool_trace=[
                {
                    "receipt_workflow_draft": {
                        "status": (
                            "canceled"
                            if receipt_advance.canceled
                            else "collecting_inputs"
                        ),
                        "writes_attempted": False,
                    }
                }
            ],
        ), work_frame)
    deterministic_pending = None
    for builder in deterministic_pending_builders:
        builder_kwargs = {
            "raw_message": raw_message,
            "conversation": conversation,
            "empleado_id": current_empleado.id,
        }
        parameters = inspect.signature(builder).parameters
        if "session" in parameters or any(
            item.kind == inspect.Parameter.VAR_KEYWORD for item in parameters.values()
        ):
            builder_kwargs["session"] = session
        deterministic_pending = builder(
            **builder_kwargs,
        )
        if inspect.isawaitable(deterministic_pending):
            deterministic_pending = await deterministic_pending
        if deterministic_pending is not None:
            break

    if deterministic_pending is not None:
        response = await build_deterministic_pending_response(
            deterministic_pending=deterministic_pending,
            raw_message=raw_message,
            conversation=conversation,
            current_empleado=current_empleado,
            session=session,
        )
        return _with_work_frame_trace(response, work_frame)

    document_response = await _build_document_confirmation_response(
        raw_message=raw_message,
        conversation=conversation,
        session=session,
        maybe_append_export_prompt=maybe_append_export_prompt,
        document_action_router_executor=document_action_router_executor,
    )
    if document_response is not None:
        return _with_work_frame_trace(document_response, work_frame)

    if _live_evidence_analyst_intent(raw_message, current_empleado) is not None:
        analyst_response = await _build_analyst_workbench_response(
            raw_message=raw_message,
            conversation=conversation,
            current_empleado=current_empleado,
            session=session,
            maybe_append_export_prompt=maybe_append_export_prompt,
            live_evidence_rows_provider=live_evidence_rows_provider,
            require_live_evidence=True,
        )
        if analyst_response is not None:
            return _with_work_frame_trace(analyst_response, work_frame)

    capability_response = await _build_capability_negotiation_response(
        raw_message=raw_message,
        conversation=conversation,
        current_empleado=current_empleado,
        session=session,
    )
    if capability_response is not None:
        return _with_work_frame_trace(capability_response, work_frame)

    case_memory_response = await _build_case_memory_response(
        raw_message=raw_message,
        conversation=conversation,
        current_empleado=current_empleado,
        session=session,
    )
    if case_memory_response is not None:
        return _with_work_frame_trace(case_memory_response, work_frame)

    workspace_resume_response = await _build_operator_workspace_resume_response(
        raw_message=raw_message,
        conversation=conversation,
        session=session,
    )
    if workspace_resume_response is not None:
        return _with_work_frame_trace(workspace_resume_response, work_frame)

    owner_entity_workspace_response = await _build_owner_entity_folder_workspace_response(
        raw_message=raw_message,
        conversation=conversation,
        session=session,
        maybe_append_export_prompt=maybe_append_export_prompt,
    )
    if owner_entity_workspace_response is not None:
        return _with_work_frame_trace(owner_entity_workspace_response, work_frame)

    specialist_preview_response = await _build_specialist_preview_surface_response(
        raw_message=raw_message,
        conversation=conversation,
        session=session,
    )
    if specialist_preview_response is not None:
        return _with_work_frame_trace(specialist_preview_response, work_frame)

    owner_variable_response = await _build_owner_variable_query_response(
        raw_message=raw_message,
        conversation=conversation,
        session=session,
        maybe_append_export_prompt=maybe_append_export_prompt,
    )
    if owner_variable_response is not None:
        return _with_work_frame_trace(owner_variable_response, work_frame)

    owner_readiness_response = await _build_owner_pack_readiness_response(
        raw_message=raw_message,
        conversation=conversation,
        session=session,
        maybe_append_export_prompt=maybe_append_export_prompt,
    )
    if owner_readiness_response is not None:
        return _with_work_frame_trace(owner_readiness_response, work_frame)


    request_response = await _build_request_intelligence_response(
        raw_message=raw_message,
        conversation=conversation,
        session=session,
        maybe_append_export_prompt=maybe_append_export_prompt,
        action_executor=document_action_router_executor,
        finance_rows_provider=finance_rows_provider,
    )
    if request_response is not None:
        return _with_work_frame_trace(request_response, work_frame)

    analyst_response = await _build_analyst_workbench_response(
        raw_message=raw_message,
        conversation=conversation,
        current_empleado=current_empleado,
        session=session,
        maybe_append_export_prompt=maybe_append_export_prompt,
        live_evidence_rows_provider=live_evidence_rows_provider,
    )
    if analyst_response is not None:
        return _with_work_frame_trace(analyst_response, work_frame)

    finance_response = await _build_finance_comparison_response(
        raw_message=raw_message,
        conversation=conversation,
        session=session,
        maybe_append_export_prompt=maybe_append_export_prompt,
        finance_rows_provider=finance_rows_provider,
    )
    if finance_response is not None:
        return _with_work_frame_trace(finance_response, work_frame)

    return await run_conversation_turn(
        raw_message=raw_message,
        conversation=conversation,
        current_empleado=current_empleado,
        session=session,
        request=request,
        tournament_key=tournament_key,
        bi_year=bi_year,
        bi_scope=bi_scope,
        bi_segment=bi_segment,
        assistant_mode=assistant_mode,
        openai_api_key=openai_api_key,
        assistant_turn=assistant_turn,
        maybe_append_export_prompt=maybe_append_export_prompt,
        document_action_router_executor=document_action_router_executor,
        finance_rows_provider=finance_rows_provider,
        live_evidence_rows_provider=live_evidence_rows_provider,
    )
