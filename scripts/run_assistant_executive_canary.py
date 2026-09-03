#!/usr/bin/env python3
"""Run the RQF-054H SamChat assistant executive canary.

The default fixture mode is deterministic and does not call providers, HTTP,
or business write paths. Live mode is opt-in and requires auth; credential
values are used only as request headers and are never printed.
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from http.cookiejar import MozillaCookieJar
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from samchat.assistant.executive_regression_suite import (  # noqa: E402
    ExecutiveRegressionCase,
    evaluate_executive_regression_case,
    executive_regression_cases,
)
from samchat.assistant.routing_contracts import (  # noqa: E402
    AssistantRoutingContractCase,
    assistant_routing_contract_cases,
    evaluate_routing_contract_case,
)

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
SCHEMA_VERSION = "samchat.assistant_executive_canary.v1"
WRITE_TERMS = (
    "approve",
    "aprobar",
    "assign",
    "cancel",
    "confirm",
    "create",
    "delete",
    "execute",
    "mark_paid",
    "mutate",
    "post",
    "publish",
    "reject",
    "send",
    "update",
    "write",
)


@dataclass(frozen=True)
class HttpResult:
    ok: bool
    status: int | None
    url: str
    payload: Any = None
    text: str = ""
    error: str | None = None
    latency_seconds: float = 0.0
    timeout: bool = False


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
    if "\t" not in raw and "=" in raw and "# Netscape" not in raw:
        return raw
    jar = MozillaCookieJar(str(cookie_path))
    jar.load(ignore_discard=True, ignore_expires=True)
    pairs = [f"{cookie.name}={cookie.value}" for cookie in jar]
    return "; ".join(pairs) if pairs else None


def _headers(*, cookie: str | None, bearer: str | None) -> dict[str, str]:
    headers = {"Accept": "application/json"}
    if cookie:
        headers["Cookie"] = cookie
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    return headers


def _request(
    *,
    method: str,
    url: str,
    headers: Mapping[str, str],
    payload: Mapping[str, Any] | None = None,
    timeout: float,
) -> HttpResult:
    body = None
    req_headers = dict(headers)
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        req_headers["Content-Type"] = "application/json"

    start = time.monotonic()
    try:
        req = urllib.request.Request(
            url, data=body, method=method, headers=req_headers
        )
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            parsed: Any = None
            content_type = response.headers.get("content-type", "").lower()
            if "json" in content_type and raw:
                parsed = json.loads(raw)
            return HttpResult(
                ok=200 <= int(response.status) < 300,
                status=int(response.status),
                url=response.geturl(),
                payload=parsed,
                text=raw if parsed is None else "",
                latency_seconds=round(time.monotonic() - start, 3),
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
            latency_seconds=round(time.monotonic() - start, 3),
        )
    except (TimeoutError, socket.timeout) as exc:
        return HttpResult(
            ok=False,
            status=None,
            url=url,
            error=str(exc),
            latency_seconds=round(time.monotonic() - start, 3),
            timeout=True,
        )
    except Exception as exc:  # pragma: no cover - exercised in live usage
        message = str(exc)
        return HttpResult(
            ok=False,
            status=None,
            url=url,
            error=message,
            latency_seconds=round(time.monotonic() - start, 3),
            timeout=(
                "timed out" in message.lower()
                or "timeout" in message.lower()
            ),
        )


def _walk_values(value: Any) -> Iterable[Any]:
    if isinstance(value, Mapping):
        for item in value.values():
            yield item
            yield from _walk_values(item)
    elif isinstance(value, list):
        for item in value:
            yield item
            yield from _walk_values(item)


def _trace_tools(tool_trace: Iterable[Mapping[str, Any]] | None) -> list[str]:
    tools: list[str] = []
    for item in tool_trace or []:
        for key in ("tool", "tool_name", "name", "canonical_action"):
            value = item.get(key)
            if value:
                tool = str(value)
                if tool not in tools:
                    tools.append(tool)
        for key in item.keys():
            if key.startswith("assistant_") and key not in tools:
                tools.append(key)
    return tools


def _trace_has_write(tool_trace: Iterable[Mapping[str, Any]] | None) -> bool:
    for item in tool_trace or []:
        if item.get("writes_attempted") or item.get("side_effects_detected"):
            return True
        for value in _walk_values(item):
            if value is True and any(
                key in item
                for key in ("writes_attempted", "side_effects_detected")
            ):
                return True
            if isinstance(value, str):
                lowered = value.lower().replace(".", "_").replace("-", "_")
                if any(term in lowered for term in WRITE_TERMS):
                    if not lowered.startswith(
                        ("read", "get", "list", "search", "assistant_owner")
                    ):
                        return True
    return False


def _extract_first(payload: Any, keys: tuple[str, ...]) -> Any:
    if isinstance(payload, Mapping):
        for key in keys:
            if payload.get(key) not in (None, ""):
                return payload.get(key)
        for value in payload.values():
            found = _extract_first(value, keys)
            if found not in (None, ""):
                return found
    elif isinstance(payload, list):
        for value in payload:
            found = _extract_first(value, keys)
            if found not in (None, ""):
                return found
    return None


def _fixture_response_for_case(
    case: ExecutiveRegressionCase,
) -> dict[str, Any]:
    fixtures: dict[str, tuple[str, str]] = {
        "OWNER-READINESS-001": (
            "assistant_owner_pack_readiness",
            "Owner Pack Readiness: faltantes visibles. "
            "Fuente: assistant.owner_pack_readiness. "
            "Frontera de autoridad: No ejecute cambios; respuesta read-only.",
        ),
        "OWNER-PAYMENT-EVIDENCE-001": (
            "assistant_owner_variable_query",
            "No hay dato soportado para pagos hechos en agosto. "
            "No hay evidencia viva suficiente. No ejecute cambios.",
        ),
        "FIN-ACCOUNTING-LOADED-001": (
            "assistant_finance_accounting_qa",
            "Si hay informacion financiera cargada. "
            "Fuente: finance.platform_snapshot. "
            "Ruta sugerida: /admin/finanzas. No ejecute cambios.",
        ),
        "FIN-PAYMENT-RUN-001": (
            "assistant_finance_accounting_qa",
            "Payment Run: hay estado financiero consultable. "
            "Fuente: finance.platform_snapshot.payment_run. "
            "Ruta sugerida: /admin/finanzas/payment-run. No ejecute cambios.",
        ),
        "FIN-CLOSE-BLOCKERS-001": (
            "assistant_finance_accounting_qa",
            "Contabilidad tiene bloqueos por revisar. "
            "Fuente: finance.closeout_diagnostics. "
            "Ruta sugerida: /admin/finanzas. No ejecute cambios.",
        ),
        "FIN-CFDI-GAPS-001": (
            "assistant_finance_accounting_qa",
            "CFDI: evidencia de faltantes por vincular. "
            "Fuente: finance.platform_snapshot.tax_readiness. "
            "Ruta sugerida: /admin/finanzas/cfdi. No ejecute cambios.",
        ),
        "OWNER-TEAMS-BY-CATEGORY-001": (
            "assistant_owner_variable_query",
            "No hay dato soportado completo de equipos reales por categoria. "
            "La evidencia disponible es parcial. No ejecute cambios.",
        ),
    }
    tool, message = fixtures.get(
        case.case_id,
        (
            (
                case.expected_tools[0]
                if case.expected_tools
                else "assistant_unknown_readonly"
            ),
            "No hay dato soportado para este caso. "
            "No hay evidencia viva suficiente. No ejecute cambios.",
        ),
    )
    return {
        "assistant_message": message,
        "tool_trace": [
            {
                "tool": tool,
                "result": {"status": "fixture", "case_id": case.case_id},
            }
        ],
        "pending_confirmation": None,
        "provider": "fixture",
        "model": "deterministic",
    }


def _fixture_response_for_routing_case(
    case: AssistantRoutingContractCase,
) -> dict[str, Any]:
    message_terms = list(case.required_terms) or ["Ruta read-only"]
    message = ". ".join(message_terms) + ". No ejecute cambios."
    result: dict[str, Any] = {
        "status": "fixture",
        "case_id": case.case_id,
        "intent": case.expected_intent,
        "read_only": True,
        "provider_called": False,
        "writes_attempted": False,
    }
    if case.must_be_exportable:
        result["payload"] = {
            "report_type": case.expected_intent or "routing_contract",
            "rows": [{"fixture": True}],
        }
    return {
        "assistant_message": message,
        "tool_trace": [{"tool": case.expected_tool, "result": result}],
        "provider": "fixture",
        "model": "deterministic",
    }


def _load_fixture_overrides(path: str | None) -> dict[str, dict[str, Any]]:
    if not path:
        return {}
    overrides: dict[str, dict[str, Any]] = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        case_id = str(payload.get("case_id") or "").strip()
        if case_id:
            overrides[case_id] = payload
    return overrides


def _evaluate_case_payload(
    *,
    case: ExecutiveRegressionCase,
    payload: Mapping[str, Any],
    http_status: int | None,
    latency_seconds: float,
    timeout: bool,
) -> dict[str, Any]:
    assistant_message = str(payload.get("assistant_message") or "")
    tool_trace = (
        payload.get("tool_trace")
        if isinstance(payload.get("tool_trace"), list)
        else []
    )
    pending_confirmation = bool(payload.get("pending_confirmation"))
    write_detected = _trace_has_write(tool_trace)
    verdict = evaluate_executive_regression_case(
        case=case,
        assistant_message=assistant_message,
        tool_trace=tool_trace,
    )
    failures = list(verdict.failures)
    if timeout:
        failures.append("provider_timeout")
    if pending_confirmation:
        failures.append("pending_confirmation")
    if write_detected and "write_or_side_effect_detected" not in failures:
        failures.append("write_or_side_effect_detected")
    ok = bool(
        verdict.ok
        and not timeout
        and not pending_confirmation
        and not write_detected
    )
    tools = _trace_tools(tool_trace)
    provider = _extract_first(payload, ("provider", "model_provider"))
    model = _extract_first(payload, ("model", "model_name"))
    return {
        "case_id": case.case_id,
        "prompt": case.question,
        "ok": ok,
        "failures": failures,
        "http_status": http_status,
        "latency_seconds": round(float(latency_seconds or 0.0), 3),
        "timeout": bool(timeout),
        "provider": provider or ("fixture" if http_status is None else None),
        "model": model or ("deterministic" if http_status is None else None),
        "tool_count": len(tools),
        "tools": tools,
        "pending_confirmation": pending_confirmation,
        "write_detected": write_detected,
        "authority_posture": (
            "failed_write_boundary"
            if (pending_confirmation or write_detected)
            else "read_only"
        ),
        "diagnostics": verdict.diagnostics,
    }


def _evaluate_routing_case_payload(
    *,
    case: AssistantRoutingContractCase,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    assistant_message = str(payload.get("assistant_message") or "")
    tool_trace = (
        payload.get("tool_trace")
        if isinstance(payload.get("tool_trace"), list)
        else []
    )
    verdict = evaluate_routing_contract_case(
        case=case,
        assistant_message=assistant_message,
        tool_trace=tool_trace,
    )
    return {
        "case_id": case.case_id,
        "prompt": case.prompt,
        "ok": verdict.ok,
        "failures": list(verdict.failures),
        "expected_tool": case.expected_tool,
        "expected_intent": case.expected_intent,
        "must_bypass_provider": case.must_bypass_provider,
        "must_be_read_only": case.must_be_read_only,
        "must_be_exportable": case.must_be_exportable,
        "diagnostics": verdict.diagnostics,
    }


def run_fixture_routing_contracts(
    *, fixture_responses: str | None = None
) -> dict[str, Any]:
    overrides = _load_fixture_overrides(fixture_responses)
    rows: list[dict[str, Any]] = []
    for case in assistant_routing_contract_cases():
        payload = dict(_fixture_response_for_routing_case(case))
        payload.update(overrides.get(case.case_id, {}))
        rows.append(_evaluate_routing_case_payload(case=case, payload=payload))

    failed = [row for row in rows if not row["ok"]]
    return {
        "ok": not failed,
        "summary": {
            "total": len(rows),
            "passed": len(rows) - len(failed),
            "failed": len(failed),
        },
        "cases": rows,
    }


def run_fixture_canary(
    *, fixture_responses: str | None = None
) -> dict[str, Any]:
    overrides = _load_fixture_overrides(fixture_responses)
    rows: list[dict[str, Any]] = []
    for case in executive_regression_cases():
        payload = dict(_fixture_response_for_case(case))
        payload.update(overrides.get(case.case_id, {}))
        rows.append(
            _evaluate_case_payload(
                case=case,
                payload=payload,
                http_status=None,
                latency_seconds=float(payload.get("latency_seconds") or 0.0),
                timeout=bool(payload.get("timeout")),
            )
        )
    routing_contracts = run_fixture_routing_contracts(
        fixture_responses=fixture_responses
    )
    return _result_payload(
        mode="fixture",
        rows=rows,
        base_url=None,
        routing_contracts=routing_contracts,
    )


def run_live_canary(
    *,
    base_url: str,
    cookie: str | None,
    bearer: str | None,
    timeout: float,
) -> dict[str, Any]:
    if not cookie and not bearer:
        return {
            "schema_version": SCHEMA_VERSION,
            "mode": "live",
            "ok": False,
            "status": "authentication_required",
            "base_url": base_url,
            "summary": {"total": 0, "passed": 0, "failed": 0, "timeouts": 0},
            "cases": [],
        }

    headers = _headers(cookie=cookie, bearer=bearer)
    marker = f"rqf-054h-executive-canary-{int(time.time())}"
    created = _request(
        method="POST",
        url=_join_url(base_url, "/api/assistant/conversations"),
        headers=headers,
        payload={
            "title": "RQF-054H Executive Canary",
            "module_key": "assistant_executive_canary",
            "module_label": "Assistant executive canary",
            "external_session_id": marker,
            "module_context": {
                "rqf": "RQF-054H",
                "side_effects": "assistant_conversation_only",
            },
        },
        timeout=timeout,
    )
    if not created.ok or not isinstance(created.payload, Mapping):
        return {
            "schema_version": SCHEMA_VERSION,
            "mode": "live",
            "ok": False,
            "status": (
                "authentication_required"
                if created.status in {401, 403}
                else "failed"
            ),
            "base_url": base_url,
            "summary": {
                "total": 0,
                "passed": 0,
                "failed": 0,
                "timeouts": int(created.timeout),
            },
            "cases": [],
            "setup_error": (
                created.error or created.text[:240] or created.payload
            ),
        }
    conversation_id = str(created.payload.get("conversation_id") or "")
    rows: list[dict[str, Any]] = []
    for case in executive_regression_cases():
        turn = _request(
            method="POST",
            url=_join_url(
                base_url,
                f"/api/assistant/conversations/{conversation_id}/messages",
            ),
            headers=headers,
            payload={
                "message": case.question,
                "module_key": "assistant_executive_canary",
                "module_label": "Assistant executive canary",
                "assistant_mode": "balanceado",
            },
            timeout=max(timeout, 30.0),
        )
        payload = turn.payload if isinstance(turn.payload, Mapping) else {}
        if not turn.ok and not payload:
            payload = {
                "assistant_message": "",
                "tool_trace": [],
                "provider": None,
                "model": None,
                "error": turn.error or turn.text[:240],
            }
        rows.append(
            _evaluate_case_payload(
                case=case,
                payload=payload,
                http_status=turn.status,
                latency_seconds=turn.latency_seconds,
                timeout=turn.timeout,
            )
        )
    result = _result_payload(mode="live", rows=rows, base_url=base_url)
    result["conversation_id"] = conversation_id
    result["external_session_id"] = marker
    return result


def _result_payload(
    *,
    mode: str,
    rows: list[dict[str, Any]],
    base_url: str | None,
    routing_contracts: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    failed = [row for row in rows if not row["ok"]]
    timeouts = [row for row in rows if row["timeout"]]
    routing_ok = True if routing_contracts is None else bool(routing_contracts.get("ok"))
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": mode,
        "ok": not failed and routing_ok,
        "status": "pass" if not failed and routing_ok else "failed",
        "base_url": base_url,
        "summary": {
            "total": len(rows),
            "passed": len(rows) - len(failed),
            "failed": len(failed),
            "timeouts": len(timeouts),
        },
        "cases": rows,
        **({"routing_contracts": routing_contracts} if routing_contracts is not None else {}),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--fixture",
        action="store_true",
        help="Run deterministic fixture canary (default)",
    )
    mode.add_argument(
        "--live",
        action="store_true",
        help="Run against authenticated /api/assistant",
    )
    parser.add_argument(
        "--fixture-responses",
        default=None,
        help="Optional JSONL case response overrides",
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument(
        "--cookie", default=None, help="Raw Cookie header; never printed"
    )
    parser.add_argument(
        "--cookie-file",
        default=None,
        help="Raw Cookie header or Netscape cookie jar file",
    )
    parser.add_argument(
        "--bearer", default=None, help="Bearer token; never printed"
    )
    parser.add_argument("--timeout", type=float, default=5.0)
    args = parser.parse_args(argv)

    if args.live:
        result = run_live_canary(
            base_url=args.base_url,
            cookie=args.cookie or _read_cookie_file(args.cookie_file),
            bearer=args.bearer,
            timeout=args.timeout,
        )
    else:
        result = run_fixture_canary(fixture_responses=args.fixture_responses)

    print(json.dumps(result, indent=2, sort_keys=True, default=_json_default))
    if result.get("ok"):
        return 0
    if result.get("status") == "authentication_required":
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
