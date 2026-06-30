from __future__ import annotations

from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional


HistoryMessagesFn = Callable[..., Awaitable[List[Dict[str, Any]]]]


def prepare_turn_state(
    *,
    raw_message: str,
    conversation: Any,
    request: Any,
    tournament_key: Optional[str],
    assistant_mode: Optional[str],
    assistant_classify_request: Callable[[str], Dict[str, Any]],
    assistant_request_origin: Callable[[Any], Optional[Dict[str, Any]]],
    conversation_module_key: Callable[[Any], Optional[str]],
    conversation_module_label: Callable[[Any], Optional[str]],
    conversation_module_context_text: Callable[[Any], Optional[str]],
    assistant_route_mode: Callable[[str, Optional[str]], str],
    normalize_assistant_mode: Callable[[Optional[str]], str],
    assistant_inference_plan: Callable[..., Dict[str, Any]],
    assistant_default_tournament_key: Callable[[], Optional[str]],
) -> Dict[str, Any]:
    route_info = assistant_classify_request(raw_message)
    origin_info = assistant_request_origin(request)
    module_key_default = conversation_module_key(conversation)
    module_label_default = conversation_module_label(conversation)
    module_context_default = conversation_module_context_text(conversation)
    if module_key_default:
        route_info["module_key"] = module_key_default
    normalized_mode = assistant_route_mode(route_info["route"], assistant_mode)
    route_info["requested_mode"] = (
        normalize_assistant_mode(assistant_mode) if assistant_mode else None
    )
    route_info["effective_mode"] = normalized_mode
    inference_plan = assistant_inference_plan(route_info, mode=normalized_mode)
    tournament_key_default = (
        tournament_key
        or conversation.tournament_key
        or assistant_default_tournament_key()
        or ""
    ).strip().lower() or None
    return {
        "route_info": route_info,
        "origin_info": origin_info,
        "module_key_default": module_key_default,
        "module_label_default": module_label_default,
        "module_context_default": module_context_default,
        "normalized_mode": normalized_mode,
        "inference_plan": inference_plan,
        "tournament_key_default": tournament_key_default,
    }


async def build_turn_messages(
    *,
    session: Any,
    conversation_id: Any,
    raw_message: str,
    route_prompt: str,
    language_prompt: str,
    hermes_profile_prompt: Optional[str],
    workspace_context: Optional[str],
    module_key_default: Optional[str],
    module_label_default: Optional[str],
    module_context_default: Optional[str],
    retrieval_context: Optional[str],
    assistant_system_prompt: Callable[[], str],
    history_messages: HistoryMessagesFn,
) -> List[Dict[str, Any]]:
    return [
        {"role": "system", "content": assistant_system_prompt()},
        {"role": "system", "content": route_prompt},
        {"role": "system", "content": language_prompt},
        *(
            [{"role": "system", "content": hermes_profile_prompt}]
            if hermes_profile_prompt
            else []
        ),
        *(
            [{"role": "system", "content": workspace_context}]
            if workspace_context
            else []
        ),
        *(
            [
                {
                    "role": "system",
                    "content": (
                        "Contexto del modulo actual:\n"
                        f"- module_key={module_key_default or 'unknown'}\n"
                        "- module_label="
                        f"{module_label_default or module_key_default or 'unknown'}\n"
                        f"- module_context={module_context_default or 'n/a'}"
                    ),
                }
            ]
            if (module_key_default or module_label_default or module_context_default)
            else []
        ),
        *(
            [{"role": "system", "content": retrieval_context}]
            if retrieval_context
            else []
        ),
        *await history_messages(session, conversation_id=conversation_id, limit=20),
        {"role": "user", "content": raw_message},
    ]


async def build_cached_response(
    *,
    cache_payload: Dict[str, Any],
    conversation: Any,
    current_empleado: Any,
    raw_message: str,
    origin_info: Optional[Dict[str, Any]],
    session: Any,
    assistant_message_cls: Any,
    assistant_run_cls: Any,
    message_response_cls: Any,
) -> Any:
    run_id = __import__("uuid").uuid4()
    base_trace = list(cache_payload.get("tool_trace") or [])
    cache_trace = (
        base_trace
        + ([{"assistant_origin": origin_info}] if origin_info else [])
        + [
            {
                "assistant_cache": {
                    "hit": True,
                    "cached_at": datetime.utcfromtimestamp(
                        float(
                            cache_payload.get("cached_at") or __import__("time").time()
                        )
                    ).isoformat(),
                }
            }
        ]
    )
    answer = str(cache_payload.get("assistant_message") or "")
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
        model="cache:assistant_response",
        user_message=raw_message,
        assistant_message=answer,
        tool_trace=cache_trace,
        pending_tool_name=None,
        pending_tool_args=None,
        created_at=datetime.utcnow(),
    )
    session.add(run)
    conversation.updated_at = datetime.utcnow()
    await session.commit()
    return message_response_cls(
        assistant_message=answer,
        run_id=str(run_id),
        tool_trace=cache_trace,
        pending_confirmation=None,
    )
