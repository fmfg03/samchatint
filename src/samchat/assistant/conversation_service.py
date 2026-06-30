from __future__ import annotations

from typing import Any, Awaitable, Callable, Optional


AssistantTurnFn = Callable[..., Awaitable[Any]]
AppendExportPromptFn = Callable[[str, Any], str]
ExplicitMessageFn = Callable[[str], bool]
PendingRunLoaderFn = Callable[..., Awaitable[Any]]
ConfirmPendingRunFn = Callable[..., Awaitable[Any]]
DeterministicPendingBuilderFn = Callable[..., Any]
DeterministicResponseBuilderFn = Callable[..., Awaitable[Any]]


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
) -> Any:
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
    )
