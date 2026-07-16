from __future__ import annotations

import os
import re
import uuid
from dataclasses import replace
from datetime import datetime
from types import SimpleNamespace
from typing import Any, Awaitable, Callable, Optional

from sqlalchemy import select

from devnous.gastos.models import AssistantMessage

from .action_router import supported_actions
from .analyst_intent import (
    AnalystIntent,
    detect_analyst_intent,
    normalize_analyst_text,
)
from .analyst_live_evidence import (
    LiveEvidenceContext,
    LiveEvidenceRowsProvider,
    acquire_live_analyst_evidence,
    live_evidence_enabled,
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
from .document_confirmation import AsyncActionRouterExecutor
from .document_conversation import (
    extract_document_intake_result_from_text,
    handle_document_confirmation_command_async,
    parse_document_confirmation_command,
    render_document_intake_for_conversation,
)
from .finance_query_intent import detect_finance_comparison_intent
from .finance_query_service import (
    FinanceRowsProvider,
    render_finance_comparison_result,
    run_read_only_comparison,
)
from .request_intent import detect_request_intent
from .request_reports import ReadOnlyActionExecutor, run_read_only_report
from .request_response import build_request_trace, render_request_report
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
) -> Optional[AnalystIntent]:
    if not live_evidence_enabled():
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
        route_hint.startswith(("cfdi.", "payments."))
        and has_explicit_reference
    ) or (
        route_hint.startswith("tournament.")
        and has_named_tournament
    )
    if (
        route_hint.startswith(("cfdi.", "payments.", "tournament."))
        and has_explicit_named_target
        and any(
            token in normalized
            for token in ("explicame", "explica", "que implica")
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
) -> Any:
    return SimpleNamespace(
        assistant_message=assistant_message,
        run_id=run_id or str(uuid.uuid4()),
        tool_trace=tool_trace,
        pending_confirmation=None,
    )


async def _persist_document_conversation_messages(
    *,
    raw_message: str,
    assistant_message: str,
    conversation: Any,
    session: Any,
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
            tool_payload=None,
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
        intake = extract_document_intake_result_from_text(
            message.content or ""
        )
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
    rendered = render_document_intake_for_conversation(intake)
    tool_trace = [
        {
            "document_intake_live_wiring": {
                "stage": "upload_render",
                "detected_document_type": intake.get("detected_document_type"),
                "proposed_action_count": len(
                    intake.get("proposed_actions") or []
                ),
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
    if _live_evidence_analyst_intent(raw_message) is not None:
        return None
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


async def _build_analyst_workbench_response(
    *,
    raw_message: str,
    conversation: Any,
    current_empleado: Any,
    session: Any,
    maybe_append_export_prompt: MaybeAppendExportPromptFn,
    live_evidence_rows_provider: Optional[
        LiveEvidenceRowsProvider
    ] = None,
) -> Optional[Any]:
    intent = (
        _live_evidence_analyst_intent(raw_message)
        or detect_analyst_intent(raw_message)
    )
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
            permissions=set(
                getattr(current_empleado, "permissions", set()) or set()
            ),
            question=raw_message,
            department=getattr(current_empleado, "departamento", None),
            limit_per_source=live_evidence_limit_per_source(),
        ),
        intent=intent,
        rows_provider=live_evidence_rows_provider,
    )
    evidence = build_analyst_evidence_pack(
        live_evidence=live_acquisition.collection.evidence,
        inline_evidence=inline_evidence,
        history_evidence=history_evidence,
        intent=intent,
    )
    result = await run_analyst_workbench(
        intent=intent,
        evidence=evidence,
        live_evidence_used=bool(live_acquisition.collection.evidence),
    )
    if live_acquisition.collection.caveats:
        result = replace(
            result,
            caveats=list(
                dict.fromkeys(
                    live_acquisition.collection.caveats + result.caveats
                )
            ),
        )
    rendered = render_analyst_result(result)
    tool_trace = build_analyst_trace(intent=intent, result=result)
    if live_acquisition.enabled:
        tool_trace[0]["analyst_live_evidence"] = live_acquisition.trace()
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
    document_action_router_executor: Optional[
        AsyncActionRouterExecutor
    ] = None,
    finance_rows_provider: Optional[FinanceRowsProvider] = None,
    live_evidence_rows_provider: Optional[
        LiveEvidenceRowsProvider
    ] = None,
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
    document_action_router_executor: Optional[
        AsyncActionRouterExecutor
    ] = None,
    finance_rows_provider: Optional[FinanceRowsProvider] = None,
    live_evidence_rows_provider: Optional[
        LiveEvidenceRowsProvider
    ] = None,
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

    deterministic_pending = None
    for builder in deterministic_pending_builders:
        deterministic_pending = builder(
            raw_message=raw_message,
            conversation=conversation,
            empleado_id=current_empleado.id,
        )
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
