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
from .receipt_workflow_draft import (
    advance_receipt_draft,
    start_receipt_draft,
)
from .request_intent import (
    detect_request_intent,
    is_owner_ai_context_request,
    is_owner_ai_readiness_request,
    owner_ai_tournament_slug_hint,
)
from .request_reports import ReadOnlyActionExecutor, run_read_only_report
from .request_response import build_request_trace, render_request_report
from .owner_needs_eval import OwnerNeedsPrompt, parse_owner_needs_eval_set
from .owner_operator_workflow import run_owner_operator_workflow
from .owner_pack_live_evidence import resolve_owner_pack_live_evidence
from .owner_pack_readiness import (
    OWNER_PACK_NEEDS_TARGET,
    OWNER_PACK_PARTIAL_LIVE_EVIDENCE,
    OWNER_PACK_READY_FOR_REVIEW,
    build_owner_pack_readiness_from_scope,
)
from .owner_pack_status import build_owner_pack_status_report
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
from .specialist_memory_context import (
    render_specialist_memory_context_markdown,
    resolve_specialist_preview_memory_context,
)
from .request_router import route_request

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
    if "entidad" in normalized or "operador" in normalized:
        return "entity_folder"
    return "all"


def _owner_readiness_entity_hint(raw_message: str) -> str | None:
    normalized = (raw_message or "").strip()
    markers = ("entidad", "operador")
    lowered = normalized.lower()
    for marker in markers:
        idx = lowered.find(marker)
        if idx < 0:
            continue
        tail = normalized[idx + len(marker):].strip(" :,-?")
        if tail:
            return tail[:80]
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


def _render_owner_pack_readiness(report: Any) -> str:
    lines = [
        report.headline,
        "",
        report.summary,
        "",
        f"Estado: {report.status} - readiness {report.readiness_score}%",
    ]
    target = report.target or {}
    target_bits = []
    if target.get("tournament_slug"):
        target_bits.append(f"torneo={target['tournament_slug']}")
    if target.get("entity_name"):
        target_bits.append(f"entidad={target['entity_name']}")
    if target.get("scope"):
        target_bits.append(f"scope={target['scope']}")
    if target_bits:
        lines.extend(["", "Objetivo revisado: " + " - ".join(target_bits)])

    lines.extend(["", "Superficies revisadas:"])
    for surface in report.surfaces[:6]:
        lines.append(
            f"- {surface.label}: {surface.status} "
            f"({surface.supported_field_count}/{surface.field_count} campos respaldados)"
        )

    lines.extend(["", "Evidencia encontrada:"])
    if report.evidence_found:
        lines.extend(f"- {item}" for item in report.evidence_found[:8])
    else:
        lines.append(
            "- Aun no hay evidencia viva suficiente; "
            "solo contrato/schema preparado."
        )

    lines.extend(["", "Faltantes para poder contestar sin inventar:"])
    if report.missing_evidence:
        lines.extend(f"- {item}" for item in report.missing_evidence[:12])
    elif report.status == OWNER_PACK_NEEDS_TARGET:
        lines.append("- Falta indicar la entidad/operador objetivo.")
    else:
        lines.append("- Sin faltantes detectados en el alcance solicitado.")

    if report.next_actions:
        lines.extend(["", "Siguiente paso seguro:"])
        lines.extend(f"- {item}" for item in report.next_actions[:4])
    if report.next_questions:
        lines.extend(["", "Pregunta minima para avanzar:"])
        lines.extend(f"- {item}" for item in report.next_questions[:3])

    if report.status == OWNER_PACK_READY_FOR_REVIEW:
        conclusion = "Puede presentarse como preview read-only para revision humana."
    elif report.status == OWNER_PACK_PARTIAL_LIVE_EVIDENCE:
        conclusion = "Se puede mostrar avance, pero no cerrar la respuesta como completa."
    else:
        conclusion = "Todavia no debe venderse como respuesta completa; falta contexto o evidencia."
    lines.extend(["", f"Conclusion: {conclusion}"])
    lines.extend(
        [
            "",
            "Frontera de autoridad: esto no crea carpetas, no modifica datos, "
            "no manda mensajes y no autoriza nada. Es diagnostico read-only; "
            "cualquier salida durable requiere aprobacion humana.",
        ]
    )
    return "\n".join(lines)


async def _build_owner_pack_readiness_response(
    *,
    raw_message: str,
    conversation: Any,
    session: Any,
    maybe_append_export_prompt: MaybeAppendExportPromptFn,
) -> Optional[Any]:
    if not is_owner_ai_readiness_request(raw_message):
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
    rendered = _render_owner_pack_readiness(report)
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
            "result": report.to_dict(),
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


def _render_owner_operator_workflow(result: Any) -> str:
    response_pack = result.response_pack
    proposal = result.folder_proposal
    sections = proposal.get("sections") or []
    section_titles = [
        str(section.get("title") or section.get("section_id")) for section in sections
    ]
    missing = list(
        response_pack.get("missing_evidence") or proposal.get("missing_evidence") or []
    )
    found = list(response_pack.get("evidence_found") or [])

    lines = [
        response_pack.get("headline") or "Propuesta de trabajo para Direccion",
        "",
        response_pack.get("summary") or "Prepare una propuesta en modo solo lectura.",
        "",
        "Estructura propuesta:",
    ]
    if sections:
        for section in sections:
            title = str(section.get("title") or section.get("section_id"))
            fields = section.get("fields") or []
            labels = [str(field.get("label") or field.get("field")) for field in fields]
            if labels:
                lines.append(f"- {title}: " + "; ".join(labels))
            else:
                lines.append(f"- {title}")
    elif section_titles:
        lines.extend(f"- {title}" for title in section_titles)
    else:
        lines.append("- Alcance")

    proposed_changes = list(response_pack.get("proposed_changes") or [])
    if proposed_changes:
        lines.extend(["", "Checklist accionable:"])
        lines.extend(f"- {change}" for change in proposed_changes[:8])

    lines.extend(
        [
            "",
            "Estado de datos:",
            "- Las superficies y contratos del pack del dueno ya estan preparados "
            "en modo read-only.",
            "- Si el torneo, entidad o evidencia real no esta cargada, el pack "
            "muestra faltantes y no inventa informacion.",
        ]
    )

    lines.extend(
        [
            "",
            "Superficies disponibles:",
            "- Expediente DG por entidad: /admin/sports/expediente-entidades "
            "(read-only; requiere torneo/datos reales para poblarse).",
        ]
    )

    lines.extend(["", "Evidencia detectada:"])
    if found:
        lines.extend(f"- {item}" for item in found[:8])
    else:
        lines.append("- Canon de necesidades del dueno / definicion de producto")

    lines.extend(["", "Evidencia faltante antes de cerrar:"])
    if missing:
        lines.extend(f"- {item}" for item in missing[:12])
    else:
        lines.append("- Sin faltantes clasificados para esta pregunta conceptual")

    plan = list(response_pack.get("plan") or [])
    if plan:
        lines.extend(["", "Siguiente paso propuesto:"])
        lines.extend(f"- {step}" for step in plan[:4])

    questions = list(response_pack.get("next_questions") or [])
    if questions:
        lines.extend(["", "Preguntas para avanzar:"])
        lines.extend(f"- {question}" for question in questions[:4])

    lines.extend(
        [
            "",
            "Frontera de autoridad: no cree ni modifique datos. "
            "Esto es una vista previa read-only; cualquier accion real "
            "requiere aprobacion explicita.",
        ]
    )
    return "\n".join(lines)


async def _build_owner_operator_response(
    *,
    raw_message: str,
    conversation: Any,
    session: Any,
    maybe_append_export_prompt: MaybeAppendExportPromptFn,
) -> Optional[Any]:
    if not is_owner_ai_context_request(raw_message):
        return None
    prompt = _owner_prompt_from_message(raw_message)
    result = run_owner_operator_workflow(prompt)
    rendered = _render_owner_operator_workflow(result)
    tool_trace = [
        {
            "owner_operator_workflow": {
                "stage": "deterministic_read_only_owner_pack",
                "workflow_id": result.workflow_id,
                "prompt_id": result.prompt_id,
                "assessment_status": result.trace.get("assessment_status"),
                "preview_id": result.trace.get("preview_id"),
                "folder_id": result.trace.get("folder_id"),
                "response_id": result.trace.get("response_id"),
                "execution_status": result.execution_status,
                "writes_attempted": result.writes_attempted,
                "side_effects_detected": result.side_effects_detected,
                "provider_called": False,
                "approval_required": True,
            },
            "tool": "owner.operator_workflow.preview",
            "result": result.to_dict(),
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
    workspace_cards = build_specialist_preview_workspace_cards(
        task_id=task_id,
        understood_context=surface.understood_context,
        live_context=live_context,
        diagnostics=diagnostics,
        preview_render=surface.preview_render.to_dict(),
        memory_context=memory_context,
    )
    step_trace = build_specialist_workspace_step_trace(
        task_id=task_id,
        understood_context=surface.understood_context,
        live_context=live_context,
        diagnostics=diagnostics,
        preview_render=surface.preview_render.to_dict(),
        memory_context=memory_context,
    )
    source_panel = build_specialist_workspace_source_panel(
        understood_context=surface.understood_context,
        live_context=live_context,
        diagnostics=diagnostics,
        preview_render=surface.preview_render.to_dict(),
        memory_context=memory_context,
    )
    live_context_markdown = render_specialist_live_context_markdown(live_context)
    memory_context_markdown = render_specialist_memory_context_markdown(memory_context)
    diagnostics_markdown = render_specialist_preview_diagnostics_markdown(diagnostics)
    assistant_message = surface.assistant_message.replace(
        "\n# ",
        f"\n{live_context_markdown}\n{memory_context_markdown}\n{diagnostics_markdown}\n# ",
        1,
    )
    tool_trace_entry = surface.tool_trace()
    tool_trace_entry["specialist_preview_surface"]["live_context"] = live_context
    tool_trace_entry["specialist_preview_surface"]["memory_context"] = memory_context
    tool_trace_entry["specialist_preview_surface"]["diagnostics"] = diagnostics
    tool_trace_entry["specialist_preview_surface"]["workspace_cards"] = workspace_cards
    tool_trace_entry["specialist_preview_surface"]["step_trace"] = step_trace
    tool_trace_entry["specialist_preview_surface"]["source_panel"] = source_panel
    tool_trace_entry["result"]["live_context"] = live_context
    tool_trace_entry["result"]["memory_context"] = memory_context
    tool_trace_entry["result"]["diagnostics"] = diagnostics
    tool_trace_entry["result"]["workspace_cards"] = workspace_cards
    tool_trace_entry["result"]["step_trace"] = step_trace
    tool_trace_entry["result"]["source_panel"] = source_panel
    tool_trace = [tool_trace_entry]
    preview_render = surface.preview_render.to_dict()
    business_preview = (
        surface.business_preview.to_dict()
        if hasattr(surface.business_preview, "to_dict")
        else dict(surface.business_preview)
    )
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
            "memory_context": memory_context,
            "diagnostics": diagnostics,
            "workspace_cards": workspace_cards,
            "step_trace": step_trace,
            "source_panel": source_panel,
        },
    )
    return _response_object(
        assistant_message=assistant_message,
        tool_trace=tool_trace,
        preview_render=preview_render,
    )


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
    document_response = await _build_document_upload_response(
        raw_message=raw_message,
        conversation=conversation,
        session=session,
        maybe_append_export_prompt=maybe_append_export_prompt,
    )
    if document_response is not None:
        return document_response

    document_response = await _build_document_confirmation_response(
        raw_message=raw_message,
        conversation=conversation,
        session=session,
        maybe_append_export_prompt=maybe_append_export_prompt,
        document_action_router_executor=document_action_router_executor,
    )
    if document_response is not None:
        return document_response

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
            return analyst_response

    capability_response = await _build_capability_negotiation_response(
        raw_message=raw_message,
        conversation=conversation,
        current_empleado=current_empleado,
        session=session,
    )
    if capability_response is not None:
        return capability_response

    specialist_preview_response = await _build_specialist_preview_surface_response(
        raw_message=raw_message,
        conversation=conversation,
        session=session,
    )
    if specialist_preview_response is not None:
        return specialist_preview_response

    owner_readiness_response = await _build_owner_pack_readiness_response(
        raw_message=raw_message,
        conversation=conversation,
        session=session,
        maybe_append_export_prompt=maybe_append_export_prompt,
    )
    if owner_readiness_response is not None:
        return owner_readiness_response

    owner_response = await _build_owner_operator_response(
        raw_message=raw_message,
        conversation=conversation,
        session=session,
        maybe_append_export_prompt=maybe_append_export_prompt,
    )
    if owner_response is not None:
        return owner_response

    request_response = await _build_request_intelligence_response(
        raw_message=raw_message,
        conversation=conversation,
        session=session,
        maybe_append_export_prompt=maybe_append_export_prompt,
        action_executor=document_action_router_executor,
        finance_rows_provider=finance_rows_provider,
    )
    if request_response is not None:
        return request_response

    analyst_response = await _build_analyst_workbench_response(
        raw_message=raw_message,
        conversation=conversation,
        current_empleado=current_empleado,
        session=session,
        maybe_append_export_prompt=maybe_append_export_prompt,
        live_evidence_rows_provider=live_evidence_rows_provider,
    )
    if analyst_response is not None:
        return analyst_response

    finance_response = await _build_finance_comparison_response(
        raw_message=raw_message,
        conversation=conversation,
        session=session,
        maybe_append_export_prompt=maybe_append_export_prompt,
        finance_rows_provider=finance_rows_provider,
    )
    if finance_response is not None:
        return finance_response

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
    return response


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
            return response
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
            return response

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
            return await build_deterministic_pending_response(
                deterministic_pending=receipt_advance.pending,
                raw_message=raw_message,
                conversation=conversation,
                current_empleado=current_empleado,
                session=session,
            )
        await _persist_document_conversation_messages(
            raw_message=raw_message,
            assistant_message=receipt_advance.message,
            conversation=conversation,
            session=session,
        )
        return _response_object(
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
        )
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
        return await build_deterministic_pending_response(
            deterministic_pending=deterministic_pending,
            raw_message=raw_message,
            conversation=conversation,
            current_empleado=current_empleado,
            session=session,
        )

    document_response = await _build_document_confirmation_response(
        raw_message=raw_message,
        conversation=conversation,
        session=session,
        maybe_append_export_prompt=maybe_append_export_prompt,
        document_action_router_executor=document_action_router_executor,
    )
    if document_response is not None:
        return document_response

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
            return analyst_response

    capability_response = await _build_capability_negotiation_response(
        raw_message=raw_message,
        conversation=conversation,
        current_empleado=current_empleado,
        session=session,
    )
    if capability_response is not None:
        return capability_response

    specialist_preview_response = await _build_specialist_preview_surface_response(
        raw_message=raw_message,
        conversation=conversation,
        session=session,
    )
    if specialist_preview_response is not None:
        return specialist_preview_response

    owner_readiness_response = await _build_owner_pack_readiness_response(
        raw_message=raw_message,
        conversation=conversation,
        session=session,
        maybe_append_export_prompt=maybe_append_export_prompt,
    )
    if owner_readiness_response is not None:
        return owner_readiness_response

    owner_response = await _build_owner_operator_response(
        raw_message=raw_message,
        conversation=conversation,
        session=session,
        maybe_append_export_prompt=maybe_append_export_prompt,
    )
    if owner_response is not None:
        return owner_response

    request_response = await _build_request_intelligence_response(
        raw_message=raw_message,
        conversation=conversation,
        session=session,
        maybe_append_export_prompt=maybe_append_export_prompt,
        action_executor=document_action_router_executor,
        finance_rows_provider=finance_rows_provider,
    )
    if request_response is not None:
        return request_response

    analyst_response = await _build_analyst_workbench_response(
        raw_message=raw_message,
        conversation=conversation,
        current_empleado=current_empleado,
        session=session,
        maybe_append_export_prompt=maybe_append_export_prompt,
        live_evidence_rows_provider=live_evidence_rows_provider,
    )
    if analyst_response is not None:
        return analyst_response

    finance_response = await _build_finance_comparison_response(
        raw_message=raw_message,
        conversation=conversation,
        session=session,
        maybe_append_export_prompt=maybe_append_export_prompt,
        finance_rows_provider=finance_rows_provider,
    )
    if finance_response is not None:
        return finance_response

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
