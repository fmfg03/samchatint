#!/usr/bin/env python3
"""Authenticated smoke test for the live SamChat assistant runtime.

This script is intentionally small and production-oriented. It verifies the
Claude-Code-like assistant surface with a real authenticated browser/API session
while keeping the test read-only from the business-domain perspective:

1. /assistant UI is reachable.
2. /api/assistant/me resolves the authenticated employee.
3. A marked smoke conversation can be created/listed.
4. A read-only prompt can run through the message endpoint.
5. Conversation history persists the turn.
6. The turn does not request a pending write confirmation or expose obvious
   write/tool mutation intent.

Pass a real session cookie (or bearer token if your environment uses one). The
script never prints credential values.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from http.cookiejar import MozillaCookieJar
from pathlib import Path
from typing import Any

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_MESSAGE = (
    "Smoke test read-only del asistente: identifica qu? puedes consultar "
    "sin ejecutar acciones ni modificar datos. No solicites confirmaci?n de escritura."
)
WRITE_VERBS = (
    "approve",
    "aprobar",
    "assign",
    "cancel",
    "cerrar",
    "confirm",
    "create",
    "delete",
    "edit",
    "execute",
    "import",
    "link",
    "mark_paid",
    "mutate",
    "pay",
    "post",
    "publish",
    "reject",
    "reenviar",
    "reversar",
    "send",
    "unlink",
    "update",
    "write",
)
READ_PREFIXES = ("read", "get", "list", "search", "consult", "inspect", "assistant_owner")


@dataclass(frozen=True)
class HttpResult:
    ok: bool
    status: int | None
    url: str
    payload: Any = None
    text: str = ""
    error: str | None = None


def _json_default(value: Any) -> str:
    return str(value)


def _join_url(base_url: str, path: str) -> str:
    base = base_url.rstrip("/")
    return f"{base}{path if path.startswith('/') else '/' + path}"


def _read_cookie_file(path: str | None) -> str | None:
    if not path:
        return None
    cookie_path = Path(path)
    raw = cookie_path.read_text(encoding="utf-8").strip()
    if not raw:
        return None
    # Accept either a raw Cookie header or a Netscape cookie file exported by a browser.
    if "\t" not in raw and "=" in raw and "# Netscape" not in raw:
        return raw
    jar = MozillaCookieJar(str(cookie_path))
    jar.load(ignore_discard=True, ignore_expires=True)
    pairs = [f"{cookie.name}={cookie.value}" for cookie in jar]
    return "; ".join(pairs) if pairs else None


def _headers(*, cookie: str | None, bearer: str | None, openai_api_key: str | None) -> dict[str, str]:
    headers = {"Accept": "application/json"}
    if cookie:
        headers["Cookie"] = cookie
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    if openai_api_key:
        headers["X-OpenAI-API-Key"] = openai_api_key
    return headers


def _request(
    *,
    method: str,
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any] | None = None,
    timeout: float,
) -> HttpResult:
    body = None
    req_headers = dict(headers)
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        req_headers["Content-Type"] = "application/json"
    try:
        req = urllib.request.Request(url, data=body, method=method, headers=req_headers)
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            content_type = response.headers.get("content-type", "")
            parsed: Any = None
            if "json" in content_type.lower() and raw:
                parsed = json.loads(raw)
            return HttpResult(
                ok=200 <= int(response.status) < 300,
                status=int(response.status),
                url=response.geturl(),
                payload=parsed,
                text=raw if parsed is None else "",
            )
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        parsed = None
        try:
            parsed = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            parsed = None
        return HttpResult(
            ok=False,
            status=int(exc.code),
            url=getattr(exc, "url", url),
            payload=parsed,
            text="" if parsed is not None else raw,
            error=str(exc),
        )
    except Exception as exc:  # pragma: no cover - exercised in live usage
        return HttpResult(ok=False, status=None, url=url, error=str(exc))


def _short_error(result: HttpResult) -> str:
    if isinstance(result.payload, dict):
        detail = result.payload.get("detail") or result.payload.get("error")
        if detail:
            return str(detail)
    return result.error or result.text[:240] or "request_failed"


def _status_step(name: str, result: HttpResult, *, required: bool = True) -> dict[str, Any]:
    step = {
        "name": name,
        "ok": bool(result.ok),
        "status": result.status,
        "url": result.url,
        "required": required,
    }
    if not result.ok:
        step["error"] = _short_error(result)
    return step


def _is_auth_failure(result: HttpResult) -> bool:
    if result.status in {401, 403}:
        return True
    if isinstance(result.payload, dict):
        detail = str(result.payload.get("detail") or "").lower()
        return "auth" in detail or "session" in detail or "login" in detail
    return False


def _trace_has_write_intent(trace: list[dict[str, Any]]) -> bool:
    for item in trace:
        values: list[str] = []
        for key in ("tool_name", "action", "canonical_action", "name", "kind"):
            value = item.get(key)
            if value is not None:
                values.append(str(value).lower())
        joined = " ".join(values).strip()
        if not joined:
            continue
        if joined.startswith(READ_PREFIXES):
            continue
        tokens = joined.replace(".", "_").replace("-", "_")
        if any(verb in tokens for verb in WRITE_VERBS):
            return True
    return False


def run_smoke(
    *,
    base_url: str,
    cookie: str | None,
    bearer: str | None,
    openai_api_key: str | None,
    message: str,
    timeout: float,
    skip_turn: bool = False,
) -> dict[str, Any]:
    headers = _headers(cookie=cookie, bearer=bearer, openai_api_key=openai_api_key)
    marker = f"rqf-053b-auth-smoke-{int(time.time())}"
    steps: list[dict[str, Any]] = []

    ui = _request(method="GET", url=_join_url(base_url, "/assistant"), headers=headers, timeout=timeout)
    ui_step = _status_step("assistant_ui", ui, required=False)
    final_path = urllib.parse.urlparse(ui.url).path
    ui_step["ok"] = bool(ui.ok and final_path.rstrip("/") == "/assistant")
    if ui.ok and not ui_step["ok"]:
        ui_step["error"] = f"assistant UI redirected to {final_path or '/'}"
    steps.append(ui_step)

    me = _request(method="GET", url=_join_url(base_url, "/api/assistant/me"), headers=headers, timeout=timeout)
    steps.append(_status_step("assistant_me", me))
    if not me.ok:
        return {
            "schema_version": "samchat.assistant_auth_smoke.v1",
            "ok": False,
            "status": "authentication_required" if _is_auth_failure(me) else "failed",
            "steps": steps,
        }

    conversation_payload = {
        "title": "RQF-053B Assistant Auth Smoke",
        "module_key": "assistant_smoke",
        "module_label": "Assistant authenticated smoke",
        "external_session_id": marker,
        "module_context": {"rqf": "RQF-ASSISTANT-053B", "side_effects": "assistant_conversation_only"},
    }
    created = _request(
        method="POST",
        url=_join_url(base_url, "/api/assistant/conversations"),
        headers=headers,
        payload=conversation_payload,
        timeout=timeout,
    )
    steps.append(_status_step("create_conversation", created))
    if not created.ok or not isinstance(created.payload, dict):
        return {"schema_version": "samchat.assistant_auth_smoke.v1", "ok": False, "status": "failed", "steps": steps}
    conversation_id = str(created.payload.get("conversation_id") or "")

    qs = urllib.parse.urlencode({"external_session_id": marker})
    listed = _request(
        method="GET",
        url=_join_url(base_url, f"/api/assistant/conversations?{qs}"),
        headers=headers,
        timeout=timeout,
    )
    listed_ok = bool(
        listed.ok
        and isinstance(listed.payload, list)
        and any(str(row.get("conversation_id")) == conversation_id for row in listed.payload if isinstance(row, dict))
    )
    list_step = _status_step("list_conversation_by_external_session", listed)
    list_step["ok"] = listed_ok
    if listed.ok and not listed_ok:
        list_step["error"] = "created conversation not returned by external_session_id filter"
    steps.append(list_step)

    assistant_turn: dict[str, Any] | None = None
    if not skip_turn:
        message_payload = {
            "message": message,
            "module_key": "assistant_smoke",
            "module_label": "Assistant authenticated smoke",
            "assistant_mode": "balanceado",
        }
        turn = _request(
            method="POST",
            url=_join_url(base_url, f"/api/assistant/conversations/{conversation_id}/messages"),
            headers=headers,
            payload=message_payload,
            timeout=max(timeout, 30.0),
        )
        turn_step = _status_step("create_readonly_message", turn)
        if turn.ok and isinstance(turn.payload, dict):
            trace = turn.payload.get("tool_trace") or []
            pending = turn.payload.get("pending_confirmation")
            write_intent = _trace_has_write_intent(trace if isinstance(trace, list) else [])
            assistant_text = str(turn.payload.get("assistant_message") or "")
            turn_step.update(
                {
                    "has_run_id": bool(turn.payload.get("run_id")),
                    "has_assistant_message": bool(assistant_text.strip()),
                    "pending_confirmation": bool(pending),
                    "write_intent_in_trace": bool(write_intent),
                    "tool_trace_count": len(trace) if isinstance(trace, list) else None,
                }
            )
            turn_step["ok"] = bool(
                turn_step["has_run_id"]
                and turn_step["has_assistant_message"]
                and not pending
                and not write_intent
            )
            if not turn_step["ok"]:
                turn_step["error"] = "assistant turn did not satisfy read-only smoke invariants"
            assistant_turn = turn.payload
        steps.append(turn_step)

    history = _request(
        method="GET",
        url=_join_url(base_url, f"/api/assistant/conversations/{conversation_id}/messages"),
        headers=headers,
        timeout=timeout,
    )
    history_step = _status_step("list_messages", history)
    if history.ok and isinstance(history.payload, list) and not skip_turn:
        roles = [row.get("role") for row in history.payload if isinstance(row, dict)]
        history_step.update({"message_count": len(history.payload), "roles": roles})
        history_step["ok"] = "user" in roles and "assistant" in roles
        if not history_step["ok"]:
            history_step["error"] = "expected persisted user and assistant messages"
    steps.append(history_step)

    ok = all(bool(step.get("ok")) for step in steps if step.get("required", True))
    result: dict[str, Any] = {
        "schema_version": "samchat.assistant_auth_smoke.v1",
        "ok": ok,
        "status": "pass" if ok else "failed",
        "base_url": base_url,
        "conversation_id": conversation_id,
        "external_session_id": marker,
        "employee": {
            "empleado_id": me.payload.get("empleado_id") if isinstance(me.payload, dict) else None,
            "nombre": me.payload.get("nombre") if isinstance(me.payload, dict) else None,
            "rol": me.payload.get("rol") if isinstance(me.payload, dict) else None,
        },
        "steps": steps,
    }
    if assistant_turn:
        result["assistant_turn"] = {
            "run_id": assistant_turn.get("run_id"),
            "assistant_message_preview": str(assistant_turn.get("assistant_message") or "")[:500],
            "tool_trace_count": len(assistant_turn.get("tool_trace") or []),
            "pending_confirmation": bool(assistant_turn.get("pending_confirmation")),
        }
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--cookie", default=None, help="Raw Cookie header for an authenticated browser session")
    parser.add_argument("--cookie-file", default=None, help="Raw Cookie header or Netscape cookie jar file")
    parser.add_argument("--bearer", default=None, help="Bearer token, if enabled by the deployment")
    parser.add_argument("--openai-api-key", default=None, help="Optional provider key header; never printed")
    parser.add_argument("--message", default=DEFAULT_MESSAGE)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--skip-turn", action="store_true", help="Only verify auth and conversation persistence")
    args = parser.parse_args(argv)

    cookie = args.cookie or _read_cookie_file(args.cookie_file)
    result = run_smoke(
        base_url=args.base_url,
        cookie=cookie,
        bearer=args.bearer,
        openai_api_key=args.openai_api_key,
        message=args.message,
        timeout=args.timeout,
        skip_turn=args.skip_turn,
    )
    print(json.dumps(result, indent=2, sort_keys=True, default=_json_default))
    if result.get("ok"):
        return 0
    if result.get("status") == "authentication_required":
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
