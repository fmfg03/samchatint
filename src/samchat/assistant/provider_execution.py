from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional

from fastapi import HTTPException


RunReadToolFn = Callable[..., Awaitable[Dict[str, Any]]]
OllamaChatFn = Callable[..., Awaitable[Dict[str, Any]]]
ToolPolicyEvaluatorFn = Callable[[str, Dict[str, Any], Optional[str]], Dict[str, Any]]


def _pending_summary(tool_name: str, args: Dict[str, Any]) -> str:
    return (
        f"El asistente quiere ejecutar: {tool_name} con estos parametros:\n"
        f"{json.dumps(args, ensure_ascii=False, indent=2)}"
    )


def _evaluate_tool_policy(
    *,
    tool_policy_evaluator: Optional[ToolPolicyEvaluatorFn],
    tool_trace: List[Dict[str, Any]],
    tool_name: str,
    args: Dict[str, Any],
    current_role: Optional[str],
) -> Optional[Dict[str, Any]]:
    if tool_policy_evaluator is None:
        return None
    decision = tool_policy_evaluator(tool_name, args, current_role)
    tool_trace.append({"assistant_policy": decision})
    if decision.get("decision") == "deny":
        tool_trace.append(
            {"blocked_tool": {"tool": tool_name, "reason": decision.get("reason")}}
        )
        raise HTTPException(
            status_code=403,
            detail=f"Tool blocked by assistant policy: {decision.get('reason')}",
        )
    return decision


async def execute_ollama_provider(
    *,
    model: str,
    normalized_mode: str,
    route_info: Dict[str, Any],
    raw_message: str,
    conversation: Any,
    current_empleado: Any,
    session: Any,
    tool_trace: List[Dict[str, Any]],
    tool_defs: List[Dict[str, Any]],
    max_tokens: int,
    retrieval_sources: List[Dict[str, Any]],
    response_cache_enabled: bool,
    cache_key: str,
    tournament_key_default: Optional[str],
    bi_year: Optional[int],
    bi_scope: Optional[str],
    messages: List[Dict[str, Any]],
    write_tools: set[str],
    get_model: Callable[..., str],
    ollama_chat: OllamaChatFn,
    ollama_message_content: Callable[[Dict[str, Any]], str],
    ollama_tool_calls: Callable[[Dict[str, Any]], List[Dict[str, Any]]],
    ollama_assistant_message: Callable[[Dict[str, Any]], Dict[str, Any]],
    run_read_tool: RunReadToolFn,
    ensure_citations: Callable[[str, List[Dict[str, Any]]], str],
    tool_trace_has_write_intent: Callable[[List[Dict[str, Any]]], bool],
    assistant_response_cache_set: Callable[..., None],
    pending_confirmation_cls: Any,
    assistant_run_cls: Any,
    assistant_message_cls: Any,
    message_response_cls: Any,
    tool_policy_evaluator: Optional[ToolPolicyEvaluatorFn] = None,
) -> Any:
    run_id = __import__("uuid").uuid4()
    ollama_messages = list(messages)
    code_tooling_active = bool(route_info.get("code_tooling_active"))
    for _ in range(6):
        active_route_info = dict(route_info)
        if code_tooling_active:
            active_route_info["code_tooling_active"] = True
        model = get_model("ollama", normalized_mode, route_info=active_route_info)
        payload = await ollama_chat(
            model=model,
            messages=ollama_messages,
            tool_defs=tool_defs,
            mode=normalized_mode,
            route_info=active_route_info,
            max_tokens=max_tokens,
        )
        assistant_text = ollama_message_content(payload)
        tool_calls = ollama_tool_calls(payload)
        tool_trace.append(
            {
                "provider_call": {
                    "provider": "ollama",
                    "model": model,
                    "done_reason": payload.get("done_reason"),
                    "load_duration": payload.get("load_duration"),
                    "eval_count": payload.get("eval_count"),
                }
            }
        )

        if tool_calls:
            if route_info.get("route") == "code_agentic":
                code_tooling_active = True
            ollama_messages.append(ollama_assistant_message(payload))
            for call in tool_calls:
                tool_name = str(((call.get("function") or {}).get("name")) or "")
                args = ((call.get("function") or {}).get("arguments")) or {}
                if not isinstance(args, dict):
                    args = {}
                tool_trace.append({"tool": tool_name, "args": args})
                policy_decision = _evaluate_tool_policy(
                    tool_policy_evaluator=tool_policy_evaluator,
                    tool_trace=tool_trace,
                    tool_name=tool_name,
                    args=args,
                    current_role=getattr(current_empleado, "rol", None),
                )

                if tool_name in write_tools or (
                    policy_decision and policy_decision.get("decision") == "confirm"
                ):
                    pending_confirmation = pending_confirmation_cls(
                        run_id=str(run_id),
                        tool_name=tool_name,
                        tool_args=args,
                        summary=_pending_summary(tool_name, args),
                    )
                    run = assistant_run_cls(
                        id=run_id,
                        conversation_id=conversation.id,
                        empleado_id=current_empleado.id,
                        status="pending_confirmation",
                        model=f"ollama:{model}:{route_info['route']}:{normalized_mode}",
                        user_message=raw_message,
                        assistant_message=assistant_text or "",
                        tool_trace=tool_trace,
                        pending_tool_name=tool_name,
                        pending_tool_args=args,
                        created_at=datetime.utcnow(),
                    )
                    session.add(run)
                    await session.commit()

                    assistant_msg = assistant_message_cls(
                        conversation_id=conversation.id,
                        role="assistant",
                        content=assistant_text
                        or "Necesito confirmacion para continuar.",
                        tool_name=None,
                        tool_payload=None,
                    )
                    session.add(assistant_msg)
                    conversation.updated_at = datetime.utcnow()
                    await session.commit()
                    return message_response_cls(
                        assistant_message=assistant_msg.content or "",
                        run_id=str(run_id),
                        tool_trace=tool_trace,
                        pending_confirmation=pending_confirmation,
                    )

                result = await run_read_tool(
                    tool_name,
                    args,
                    gastos_session=session,
                    tournament_key_default=tournament_key_default,
                    current_role=getattr(current_empleado, "rol", None),
                    bi_year=bi_year,
                    bi_scope=bi_scope,
                )
                tool_trace.append({"tool": tool_name, "result": result})
                ollama_messages.append(
                    {
                        "role": "tool",
                        "tool_name": tool_name,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )
            continue

        if not assistant_text:
            raise HTTPException(
                status_code=502, detail="Ollama returned empty response"
            )

        answer = ensure_citations(assistant_text, retrieval_sources)
        assistant_msg = assistant_message_cls(
            conversation_id=conversation.id,
            role="assistant",
            content=answer,
            tool_name=None,
            tool_payload=None,
        )
        session.add(assistant_msg)
        run = assistant_run_cls(
            id=run_id,
            conversation_id=conversation.id,
            empleado_id=current_empleado.id,
            status="completed",
            model=f"ollama:{model}:{route_info['route']}:{normalized_mode}",
            user_message=raw_message,
            assistant_message=answer,
            tool_trace=tool_trace,
            pending_tool_name=None,
            pending_tool_args=None,
            created_at=datetime.utcnow(),
        )
        session.add(run)
        conversation.updated_at = datetime.utcnow()
        await session.commit()
        if response_cache_enabled and not tool_trace_has_write_intent(tool_trace):
            assistant_response_cache_set(
                key=cache_key,
                assistant_message=answer,
                tool_trace=tool_trace,
            )
        return message_response_cls(
            assistant_message=answer,
            run_id=str(run_id),
            tool_trace=tool_trace,
            pending_confirmation=None,
        )

    raise HTTPException(status_code=500, detail="Tool loop exceeded")


async def execute_anthropic_provider(
    *,
    model: str,
    normalized_mode: str,
    route_info: Dict[str, Any],
    raw_message: str,
    conversation: Any,
    current_empleado: Any,
    session: Any,
    tool_trace: List[Dict[str, Any]],
    tool_defs: List[Dict[str, Any]],
    max_tokens: int,
    retrieval_sources: List[Dict[str, Any]],
    response_cache_enabled: bool,
    cache_key: str,
    tournament_key_default: Optional[str],
    bi_year: Optional[int],
    bi_scope: Optional[str],
    write_tools: set[str],
    route_prompt: str,
    language_prompt: str,
    hermes_profile_prompt: Optional[str],
    workspace_context: Optional[str],
    module_key_default: Optional[str],
    module_label_default: Optional[str],
    module_context_default: Optional[str],
    retrieval_context: Optional[str],
    assistant_system_prompt: Callable[[], str],
    history_messages: Callable[..., Awaitable[List[Dict[str, Any]]]],
    get_anthropic_client: Callable[..., Any],
    tool_defs_anthropic: Callable[[List[Dict[str, Any]]], List[Dict[str, Any]]],
    anthropic_text_from_blocks: Callable[[Any], str],
    anthropic_message_from_blocks: Callable[[Any], List[Dict[str, Any]]],
    run_read_tool: RunReadToolFn,
    ensure_citations: Callable[[str, List[Dict[str, Any]]], str],
    tool_trace_has_write_intent: Callable[[List[Dict[str, Any]]], bool],
    assistant_response_cache_set: Callable[..., None],
    pending_confirmation_cls: Any,
    assistant_run_cls: Any,
    assistant_message_cls: Any,
    message_response_cls: Any,
    tool_policy_evaluator: Optional[ToolPolicyEvaluatorFn] = None,
) -> Any:
    run_id = __import__("uuid").uuid4()
    client = get_anthropic_client()
    system_prompt = assistant_system_prompt()
    system_prompt = f"{system_prompt}\n\n{route_prompt}"
    system_prompt = f"{system_prompt}\n\n{language_prompt}"
    if hermes_profile_prompt:
        system_prompt = f"{system_prompt}\n\n{hermes_profile_prompt}"
    if workspace_context:
        system_prompt = f"{system_prompt}\n\n{workspace_context}"
    if module_key_default or module_label_default or module_context_default:
        system_prompt = (
            f"{system_prompt}\n\n"
            "Contexto del modulo actual:\n"
            f"- module_key={module_key_default or 'unknown'}\n"
            "- module_label="
            f"{module_label_default or module_key_default or 'unknown'}\n"
            f"- module_context={module_context_default or 'n/a'}"
        )
    if retrieval_context:
        system_prompt = f"{system_prompt}\n\n{retrieval_context}"

    anthropic_messages: List[Dict[str, Any]] = []
    for m in await history_messages(session, conversation_id=conversation.id, limit=20):
        role = "assistant" if m["role"] == "assistant" else "user"
        anthropic_messages.append({"role": role, "content": m.get("content") or ""})
    anthropic_messages.append({"role": "user", "content": raw_message})

    for _ in range(6):
        resp = client.messages.create(
            model=model,
            system=system_prompt,
            messages=anthropic_messages,
            tools=tool_defs_anthropic(tool_defs),
            max_tokens=max_tokens,
            temperature=0.2,
        )
        blocks = getattr(resp, "content", []) or []
        assistant_text = anthropic_text_from_blocks(blocks)
        tool_uses = [b for b in blocks if getattr(b, "type", "") == "tool_use"]

        if tool_uses:
            anthropic_messages.append(
                {
                    "role": "assistant",
                    "content": anthropic_message_from_blocks(blocks),
                }
            )
            tool_result_blocks: List[Dict[str, Any]] = []
            for call in tool_uses:
                tool_name = getattr(call, "name", "")
                args = getattr(call, "input", {}) or {}
                tool_trace.append({"tool": tool_name, "args": args})
                policy_decision = _evaluate_tool_policy(
                    tool_policy_evaluator=tool_policy_evaluator,
                    tool_trace=tool_trace,
                    tool_name=tool_name,
                    args=args,
                    current_role=getattr(current_empleado, "rol", None),
                )

                if tool_name in write_tools or (
                    policy_decision and policy_decision.get("decision") == "confirm"
                ):
                    pending_confirmation = pending_confirmation_cls(
                        run_id=str(run_id),
                        tool_name=tool_name,
                        tool_args=args,
                        summary=_pending_summary(tool_name, args),
                    )
                    run = assistant_run_cls(
                        id=run_id,
                        conversation_id=conversation.id,
                        empleado_id=current_empleado.id,
                        status="pending_confirmation",
                        model=(
                            f"anthropic:{model}:{route_info['route']}:"
                            f"{normalized_mode}"
                        ),
                        user_message=raw_message,
                        assistant_message=assistant_text or "",
                        tool_trace=tool_trace,
                        pending_tool_name=tool_name,
                        pending_tool_args=args,
                        created_at=datetime.utcnow(),
                    )
                    session.add(run)
                    await session.commit()

                    assistant_msg = assistant_message_cls(
                        conversation_id=conversation.id,
                        role="assistant",
                        content=assistant_text
                        or "Necesito confirmacion para continuar.",
                        tool_name=None,
                        tool_payload=None,
                    )
                    session.add(assistant_msg)
                    conversation.updated_at = datetime.utcnow()
                    await session.commit()
                    return message_response_cls(
                        assistant_message=assistant_msg.content or "",
                        run_id=str(run_id),
                        tool_trace=tool_trace,
                        pending_confirmation=pending_confirmation,
                    )

                result = await run_read_tool(
                    tool_name,
                    args,
                    gastos_session=session,
                    tournament_key_default=tournament_key_default,
                    current_role=getattr(current_empleado, "rol", None),
                    bi_year=bi_year,
                    bi_scope=bi_scope,
                )
                tool_trace.append({"tool": tool_name, "result": result})
                tool_result_blocks.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": getattr(call, "id", ""),
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )
            if tool_result_blocks:
                anthropic_messages.append(
                    {"role": "user", "content": tool_result_blocks}
                )
            continue

        answer = ensure_citations(assistant_text or "", retrieval_sources)
        assistant_msg = assistant_message_cls(
            conversation_id=conversation.id,
            role="assistant",
            content=answer,
            tool_name=None,
            tool_payload=None,
        )
        session.add(assistant_msg)
        run = assistant_run_cls(
            id=run_id,
            conversation_id=conversation.id,
            empleado_id=current_empleado.id,
            status="completed",
            model=f"anthropic:{model}:{route_info['route']}:{normalized_mode}",
            user_message=raw_message,
            assistant_message=answer,
            tool_trace=tool_trace,
            pending_tool_name=None,
            pending_tool_args=None,
            created_at=datetime.utcnow(),
        )
        session.add(run)
        conversation.updated_at = datetime.utcnow()
        await session.commit()
        if response_cache_enabled and not tool_trace_has_write_intent(tool_trace):
            assistant_response_cache_set(
                key=cache_key,
                assistant_message=answer,
                tool_trace=tool_trace,
            )
        return message_response_cls(
            assistant_message=answer,
            run_id=str(run_id),
            tool_trace=tool_trace,
            pending_confirmation=None,
        )

    raise HTTPException(status_code=500, detail="Tool loop exceeded")


async def execute_openai_provider(
    *,
    model: str,
    normalized_mode: str,
    route_info: Dict[str, Any],
    raw_message: str,
    conversation: Any,
    current_empleado: Any,
    session: Any,
    tool_trace: List[Dict[str, Any]],
    tool_defs: List[Dict[str, Any]],
    max_tokens: int,
    retrieval_sources: List[Dict[str, Any]],
    response_cache_enabled: bool,
    cache_key: str,
    tournament_key_default: Optional[str],
    bi_year: Optional[int],
    bi_scope: Optional[str],
    messages: List[Dict[str, Any]],
    openai_api_key: Optional[str],
    write_tools: set[str],
    get_openai_client: Callable[..., Any],
    run_read_tool: RunReadToolFn,
    ensure_citations: Callable[[str, List[Dict[str, Any]]], str],
    tool_trace_has_write_intent: Callable[[List[Dict[str, Any]]], bool],
    assistant_response_cache_set: Callable[..., None],
    pending_confirmation_cls: Any,
    assistant_run_cls: Any,
    assistant_message_cls: Any,
    message_response_cls: Any,
    tool_policy_evaluator: Optional[ToolPolicyEvaluatorFn] = None,
) -> Any:
    run_id = __import__("uuid").uuid4()
    client = get_openai_client(openai_api_key)
    openai_messages = list(messages)
    for _ in range(6):
        resp = client.chat.completions.create(
            model=model,
            messages=openai_messages,
            tools=tool_defs,
            tool_choice="auto",
            max_tokens=max_tokens,
            temperature=0.2,
        )
        choice = resp.choices[0].message
        if getattr(choice, "tool_calls", None):
            openai_messages.append(
                {
                    "role": "assistant",
                    "content": choice.content or "",
                    "tool_calls": choice.tool_calls,
                }
            )
            for call in choice.tool_calls:
                tool_name = call.function.name
                try:
                    args = json.loads(call.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                tool_trace.append({"tool": tool_name, "args": args})
                policy_decision = _evaluate_tool_policy(
                    tool_policy_evaluator=tool_policy_evaluator,
                    tool_trace=tool_trace,
                    tool_name=tool_name,
                    args=args,
                    current_role=getattr(current_empleado, "rol", None),
                )
                if tool_name in write_tools or (
                    policy_decision and policy_decision.get("decision") == "confirm"
                ):
                    pending_confirmation = pending_confirmation_cls(
                        run_id=str(run_id),
                        tool_name=tool_name,
                        tool_args=args,
                        summary=_pending_summary(tool_name, args),
                    )
                    run = assistant_run_cls(
                        id=run_id,
                        conversation_id=conversation.id,
                        empleado_id=current_empleado.id,
                        status="pending_confirmation",
                        model=f"openai:{model}:{route_info['route']}:{normalized_mode}",
                        user_message=raw_message,
                        assistant_message=choice.content or "",
                        tool_trace=tool_trace,
                        pending_tool_name=tool_name,
                        pending_tool_args=args,
                        created_at=datetime.utcnow(),
                    )
                    session.add(run)
                    await session.commit()
                    assistant_msg = assistant_message_cls(
                        conversation_id=conversation.id,
                        role="assistant",
                        content=choice.content
                        or "Necesito confirmacion para continuar.",
                        tool_name=None,
                        tool_payload=None,
                    )
                    session.add(assistant_msg)
                    conversation.updated_at = datetime.utcnow()
                    await session.commit()
                    return message_response_cls(
                        assistant_message=assistant_msg.content or "",
                        run_id=str(run_id),
                        tool_trace=tool_trace,
                        pending_confirmation=pending_confirmation,
                    )
                result = await run_read_tool(
                    tool_name,
                    args,
                    gastos_session=session,
                    tournament_key_default=tournament_key_default,
                    current_role=getattr(current_empleado, "rol", None),
                    bi_year=bi_year,
                    bi_scope=bi_scope,
                )
                tool_trace.append({"tool": tool_name, "result": result})
                openai_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )
            continue

        answer = ensure_citations(choice.content or "", retrieval_sources)
        assistant_msg = assistant_message_cls(
            conversation_id=conversation.id,
            role="assistant",
            content=answer,
            tool_name=None,
            tool_payload=None,
        )
        session.add(assistant_msg)
        run = assistant_run_cls(
            id=run_id,
            conversation_id=conversation.id,
            empleado_id=current_empleado.id,
            status="completed",
            model=f"openai:{model}:{route_info['route']}:{normalized_mode}",
            user_message=raw_message,
            assistant_message=answer,
            tool_trace=tool_trace,
            pending_tool_name=None,
            pending_tool_args=None,
            created_at=datetime.utcnow(),
        )
        session.add(run)
        conversation.updated_at = datetime.utcnow()
        await session.commit()
        if response_cache_enabled and not tool_trace_has_write_intent(tool_trace):
            assistant_response_cache_set(
                key=cache_key,
                assistant_message=answer,
                tool_trace=tool_trace,
            )
        return message_response_cls(
            assistant_message=answer,
            run_id=str(run_id),
            tool_trace=tool_trace,
            pending_confirmation=None,
        )

    raise HTTPException(status_code=500, detail="Tool loop exceeded")


async def execute_provider(
    *,
    provider: str,
    model: str,
    normalized_mode: str,
    route_info: Dict[str, Any],
    raw_message: str,
    conversation: Any,
    current_empleado: Any,
    session: Any,
    tool_trace: List[Dict[str, Any]],
    tool_defs: List[Dict[str, Any]],
    max_tokens: int,
    retrieval_sources: List[Dict[str, Any]],
    response_cache_enabled: bool,
    cache_key: str,
    tournament_key_default: Optional[str],
    bi_year: Optional[int],
    bi_scope: Optional[str],
    messages: List[Dict[str, Any]],
    openai_api_key: Optional[str],
    write_tools: set[str],
    route_prompt: str,
    language_prompt: str,
    hermes_profile_prompt: Optional[str],
    workspace_context: Optional[str],
    module_key_default: Optional[str],
    module_label_default: Optional[str],
    module_context_default: Optional[str],
    retrieval_context: Optional[str],
    assistant_system_prompt: Callable[[], str],
    history_messages: Callable[..., Awaitable[List[Dict[str, Any]]]],
    get_model: Callable[..., str],
    get_openai_client: Callable[..., Any],
    get_anthropic_client: Callable[..., Any],
    ollama_chat: OllamaChatFn,
    ollama_message_content: Callable[[Dict[str, Any]], str],
    ollama_tool_calls: Callable[[Dict[str, Any]], List[Dict[str, Any]]],
    ollama_assistant_message: Callable[[Dict[str, Any]], Dict[str, Any]],
    tool_defs_anthropic: Callable[[List[Dict[str, Any]]], List[Dict[str, Any]]],
    anthropic_text_from_blocks: Callable[[Any], str],
    anthropic_message_from_blocks: Callable[[Any], List[Dict[str, Any]]],
    run_read_tool: RunReadToolFn,
    ensure_citations: Callable[[str, List[Dict[str, Any]]], str],
    tool_trace_has_write_intent: Callable[[List[Dict[str, Any]]], bool],
    assistant_response_cache_set: Callable[..., None],
    pending_confirmation_cls: Any,
    assistant_run_cls: Any,
    assistant_message_cls: Any,
    message_response_cls: Any,
    tool_policy_evaluator: Optional[ToolPolicyEvaluatorFn] = None,
) -> Any:
    if provider == "ollama":
        return await execute_ollama_provider(
            model=model,
            normalized_mode=normalized_mode,
            route_info=route_info,
            raw_message=raw_message,
            conversation=conversation,
            current_empleado=current_empleado,
            session=session,
            tool_trace=tool_trace,
            tool_defs=tool_defs,
            max_tokens=max_tokens,
            retrieval_sources=retrieval_sources,
            response_cache_enabled=response_cache_enabled,
            cache_key=cache_key,
            tournament_key_default=tournament_key_default,
            bi_year=bi_year,
            bi_scope=bi_scope,
            messages=messages,
            write_tools=write_tools,
            get_model=get_model,
            ollama_chat=ollama_chat,
            ollama_message_content=ollama_message_content,
            ollama_tool_calls=ollama_tool_calls,
            ollama_assistant_message=ollama_assistant_message,
            run_read_tool=run_read_tool,
            ensure_citations=ensure_citations,
            tool_trace_has_write_intent=tool_trace_has_write_intent,
            assistant_response_cache_set=assistant_response_cache_set,
            pending_confirmation_cls=pending_confirmation_cls,
            assistant_run_cls=assistant_run_cls,
            assistant_message_cls=assistant_message_cls,
            message_response_cls=message_response_cls,
            tool_policy_evaluator=tool_policy_evaluator,
        )
    if provider == "anthropic":
        return await execute_anthropic_provider(
            model=model,
            normalized_mode=normalized_mode,
            route_info=route_info,
            raw_message=raw_message,
            conversation=conversation,
            current_empleado=current_empleado,
            session=session,
            tool_trace=tool_trace,
            tool_defs=tool_defs,
            max_tokens=max_tokens,
            retrieval_sources=retrieval_sources,
            response_cache_enabled=response_cache_enabled,
            cache_key=cache_key,
            tournament_key_default=tournament_key_default,
            bi_year=bi_year,
            bi_scope=bi_scope,
            write_tools=write_tools,
            route_prompt=route_prompt,
            language_prompt=language_prompt,
            hermes_profile_prompt=hermes_profile_prompt,
            workspace_context=workspace_context,
            module_key_default=module_key_default,
            module_label_default=module_label_default,
            module_context_default=module_context_default,
            retrieval_context=retrieval_context,
            assistant_system_prompt=assistant_system_prompt,
            history_messages=history_messages,
            get_anthropic_client=get_anthropic_client,
            tool_defs_anthropic=tool_defs_anthropic,
            anthropic_text_from_blocks=anthropic_text_from_blocks,
            anthropic_message_from_blocks=anthropic_message_from_blocks,
            run_read_tool=run_read_tool,
            ensure_citations=ensure_citations,
            tool_trace_has_write_intent=tool_trace_has_write_intent,
            assistant_response_cache_set=assistant_response_cache_set,
            pending_confirmation_cls=pending_confirmation_cls,
            assistant_run_cls=assistant_run_cls,
            assistant_message_cls=assistant_message_cls,
            message_response_cls=message_response_cls,
            tool_policy_evaluator=tool_policy_evaluator,
        )
    return await execute_openai_provider(
        model=model,
        normalized_mode=normalized_mode,
        route_info=route_info,
        raw_message=raw_message,
        conversation=conversation,
        current_empleado=current_empleado,
        session=session,
        tool_trace=tool_trace,
        tool_defs=tool_defs,
        max_tokens=max_tokens,
        retrieval_sources=retrieval_sources,
        response_cache_enabled=response_cache_enabled,
        cache_key=cache_key,
        tournament_key_default=tournament_key_default,
        bi_year=bi_year,
        bi_scope=bi_scope,
        messages=messages,
        openai_api_key=openai_api_key,
        write_tools=write_tools,
        get_openai_client=get_openai_client,
        run_read_tool=run_read_tool,
        ensure_citations=ensure_citations,
        tool_trace_has_write_intent=tool_trace_has_write_intent,
        assistant_response_cache_set=assistant_response_cache_set,
        pending_confirmation_cls=pending_confirmation_cls,
        assistant_run_cls=assistant_run_cls,
        assistant_message_cls=assistant_message_cls,
        message_response_cls=message_response_cls,
        tool_policy_evaluator=tool_policy_evaluator,
    )
