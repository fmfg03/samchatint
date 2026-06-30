from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover
    OpenAI = None

try:
    import anthropic
except Exception:  # pragma: no cover
    anthropic = None


def get_openai_client(api_key_override: Optional[str] = None) -> Any:
    if OpenAI is None:
        raise HTTPException(status_code=500, detail="OpenAI package not installed")
    api_key = (api_key_override or os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY not configured")
    return OpenAI(api_key=api_key)


def get_anthropic_client(api_key_override: Optional[str] = None) -> Any:
    if anthropic is None:
        raise HTTPException(status_code=500, detail="anthropic package not installed")
    api_key = (api_key_override or os.getenv("ANTHROPIC_API_KEY") or "").strip()
    if not api_key:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not configured")
    return anthropic.Anthropic(api_key=api_key)


def env_int(
    name: str,
    default: int,
    *,
    minimum: Optional[int] = None,
    maximum: Optional[int] = None,
) -> int:
    try:
        value = int((os.getenv(name) or "").strip() or default)
    except (TypeError, ValueError):
        value = default
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def env_float(
    name: str,
    default: float,
    *,
    minimum: Optional[float] = None,
    maximum: Optional[float] = None,
) -> float:
    try:
        value = float((os.getenv(name) or "").strip() or default)
    except (TypeError, ValueError):
        value = default
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def env_bool(name: str, default: bool) -> bool:
    value = (os.getenv(name) or "").strip().lower()
    if not value:
        return default
    return value not in {"0", "false", "no", "off"}


def normalize_assistant_mode(value: Optional[str]) -> str:
    mode = (
        (value or os.getenv("ASSISTANT_MODE_DEFAULT", "ahorro") or "").strip().lower()
    )
    if mode in {"low", "cheap", "ahorro"}:
        return "ahorro"
    if mode in {"balanced", "balanceado"}:
        return "balanceado"
    if mode in {"high", "quality", "calidad"}:
        return "calidad"
    return "ahorro"


def assistant_inference_tier(
    route_info: Optional[Dict[str, Any]],
    mode: Optional[str] = None,
) -> str:
    normalized_mode = normalize_assistant_mode(mode)
    if str((route_info or {}).get("hermes_profile") or "").strip().lower():
        return "remote_high_risk"
    route = str((route_info or {}).get("route") or "").strip().lower()
    if normalized_mode == "calidad" and route in {
        "code_agentic",
        "reporting",
        "agentic_write",
    }:
        return "remote_high_risk"
    if normalized_mode == "balanceado" and route == "code_agentic":
        return "remote_assisted"
    if route in {"needs_clarification", "lookup_sql"}:
        return "local_fast"
    return "local_general"


def csv_items(raw: Optional[str]) -> List[str]:
    return [
        item.strip().lower()
        for item in str(raw or "").split(",")
        if item and item.strip()
    ]


def matches_policy_target(value: Optional[str], patterns: List[str]) -> bool:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return False
    for pattern in patterns:
        token = str(pattern or "").strip().lower()
        if not token:
            continue
        if token.endswith("*"):
            if normalized.startswith(token[:-1]):
                return True
            continue
        if token.endswith("."):
            if normalized.startswith(token):
                return True
            continue
        if normalized == token or normalized.startswith(f"{token}."):
            return True
    return False


def assistant_contextual_pref(route_info: Optional[Dict[str, Any]]) -> Optional[str]:
    route = str((route_info or {}).get("route") or "").strip().lower()
    module_key = str((route_info or {}).get("module_key") or "").strip().lower()

    route_local_only = csv_items(os.getenv("ASSISTANT_LLM_PROVIDER_ROUTE_LOCAL_ONLY"))
    module_local_only = csv_items(os.getenv("ASSISTANT_LLM_PROVIDER_MODULE_LOCAL_ONLY"))
    route_remote_only = csv_items(os.getenv("ASSISTANT_LLM_PROVIDER_ROUTE_REMOTE_ONLY"))
    module_remote_only = csv_items(
        os.getenv("ASSISTANT_LLM_PROVIDER_MODULE_REMOTE_ONLY")
    )
    route_local_first = csv_items(os.getenv("ASSISTANT_LLM_PROVIDER_ROUTE_LOCAL_FIRST"))
    module_local_first = csv_items(
        os.getenv("ASSISTANT_LLM_PROVIDER_MODULE_LOCAL_FIRST")
    )
    route_remote_first = csv_items(
        os.getenv("ASSISTANT_LLM_PROVIDER_ROUTE_REMOTE_FIRST")
    )
    module_remote_first = csv_items(
        os.getenv("ASSISTANT_LLM_PROVIDER_MODULE_REMOTE_FIRST")
    )

    if route and route in route_local_only:
        return "ollama_only"
    if matches_policy_target(module_key, module_local_only):
        return "ollama_only"
    if route and route in route_remote_only:
        return "anthropic_first"
    if matches_policy_target(module_key, module_remote_only):
        return "anthropic_first"
    if route and route in route_remote_first:
        return "anthropic_first"
    if matches_policy_target(module_key, module_remote_first):
        return "anthropic_first"
    if route and route in route_local_first:
        return "ollama_first"
    if matches_policy_target(module_key, module_local_first):
        return "ollama_first"
    return None


def assistant_remote_allowed(
    route_info: Optional[Dict[str, Any]],
    *,
    capability: str,
) -> bool:
    if capability != "chat":
        return True
    if bool((route_info or {}).get("allow_remote")):
        return True
    mode = (
        (os.getenv("ASSISTANT_REMOTE_ESCALATION_MODE", "automatic") or "")
        .strip()
        .lower()
    )
    if mode not in {"explicit", "manual"}:
        return True
    route = str((route_info or {}).get("route") or "").strip().lower()
    module_key = str((route_info or {}).get("module_key") or "").strip().lower()
    if route and route in set(
        csv_items(os.getenv("ASSISTANT_REMOTE_ESCALATION_ROUTES"))
    ):
        return True
    if matches_policy_target(
        module_key,
        csv_items(os.getenv("ASSISTANT_REMOTE_ESCALATION_MODULES")),
    ):
        return True
    return False


def assistant_provider_order_from_pref(pref: str, *, capability: str) -> List[str]:
    normalized = (pref or "").strip().lower()
    if capability != "chat":
        if normalized in {"openai", "openai_only"}:
            return ["openai"]
        if normalized in {"anthropic", "anthropic_only", "claude", "claude_only"}:
            return ["anthropic"]
        if normalized in {"openai_first"}:
            return ["openai", "anthropic"]
        return ["anthropic", "openai"]

    if normalized in {"ollama", "ollama_only", "local", "local_only"}:
        return ["ollama"]
    if normalized in {"ollama_first", "local_first", "hybrid_local_remote"}:
        return ["ollama", "anthropic", "openai"]
    if normalized in {"remote_only"}:
        return ["anthropic", "openai"]
    if normalized in {"remote_first"}:
        return ["anthropic", "openai", "ollama"]
    if normalized in {"openai", "openai_only"}:
        return ["openai"]
    if normalized in {"anthropic", "anthropic_only", "claude", "claude_only"}:
        return ["anthropic"]
    if normalized in {"openai_first"}:
        return ["openai", "anthropic", "ollama"]
    if normalized in {"anthropic_first", "claude_first"}:
        return ["anthropic", "openai", "ollama"]
    return ["ollama", "anthropic", "openai"]


def assistant_provider_order(
    mode: Optional[str] = None,
    *,
    route_info: Optional[Dict[str, Any]] = None,
    capability: str = "chat",
) -> List[str]:
    if (
        capability == "chat"
        and str((route_info or {}).get("hermes_profile") or "").strip()
    ):
        return ["anthropic"]
    normalized_mode = normalize_assistant_mode(mode)
    pref = assistant_contextual_pref(route_info)
    if not pref:
        if normalized_mode == "calidad":
            pref = (os.getenv("ASSISTANT_LLM_PROVIDER_HIGH", "") or "").strip().lower()
        elif normalized_mode == "balanceado":
            pref = (
                (os.getenv("ASSISTANT_LLM_PROVIDER_BALANCED", "") or "").strip().lower()
            )
        else:
            pref = (os.getenv("ASSISTANT_LLM_PROVIDER_LOW", "") or "").strip().lower()
    if not pref:
        pref = (os.getenv("ASSISTANT_LLM_PROVIDER", "") or "").strip().lower()
    if not pref:
        tier = assistant_inference_tier(route_info, normalized_mode)
        if capability != "chat":
            pref = "anthropic_first"
        elif tier == "remote_high_risk":
            pref = "anthropic_first"
        elif tier == "remote_assisted":
            pref = "ollama_first"
        else:
            pref = "ollama_first"

    remote_allowed = assistant_remote_allowed(route_info, capability=capability)
    order = assistant_provider_order_from_pref(pref, capability=capability)
    if capability == "chat" and not remote_allowed:
        order = [provider for provider in order if provider == "ollama"] or ["ollama"]
    deduped: List[str] = []
    for provider in order:
        if provider not in deduped:
            deduped.append(provider)
    return deduped


def assistant_model(
    provider: str,
    mode: Optional[str] = None,
    route_info: Optional[Dict[str, Any]] = None,
) -> str:
    normalized_mode = normalize_assistant_mode(mode)
    route = str((route_info or {}).get("route") or "").strip().lower()
    hermes_profile = str((route_info or {}).get("hermes_profile") or "").strip().lower()
    if provider == "ollama":
        if route == "code_agentic":
            code_heavy = (
                bool((route_info or {}).get("code_tooling_active"))
                or bool((route_info or {}).get("has_code_change_intent"))
                or bool((route_info or {}).get("has_write_intent"))
            )
            if code_heavy:
                return os.getenv(
                    "OLLAMA_ASSISTANT_MODEL_CODE",
                    os.getenv(
                        "OLLAMA_ASSISTANT_MODEL_HIGH",
                        os.getenv("OLLAMA_ASSISTANT_MODEL", "qwen3:8b"),
                    ),
                )
            return os.getenv(
                "OLLAMA_ASSISTANT_MODEL_CODE_LIGHT",
                os.getenv(
                    "OLLAMA_ASSISTANT_MODEL_BALANCED",
                    os.getenv("OLLAMA_ASSISTANT_MODEL", "qwen3:4b"),
                ),
            )
        route_env = {
            "lookup_sql": "OLLAMA_ASSISTANT_MODEL_LOOKUP",
            "aggregation_sql": "OLLAMA_ASSISTANT_MODEL_AGGREGATION",
            "reporting": "OLLAMA_ASSISTANT_MODEL_REPORTING",
            "agentic_write": "OLLAMA_ASSISTANT_MODEL_ACTION",
            "code_agentic": "OLLAMA_ASSISTANT_MODEL_CODE",
            "needs_clarification": "OLLAMA_ASSISTANT_MODEL_CLARIFICATION",
        }.get(route, "")
        if route_env:
            route_model = (os.getenv(route_env, "") or "").strip()
            if route_model:
                return route_model
        if normalized_mode == "calidad":
            return os.getenv(
                "OLLAMA_ASSISTANT_MODEL_HIGH",
                os.getenv("OLLAMA_ASSISTANT_MODEL", "qwen3:4b"),
            )
        if normalized_mode == "balanceado":
            return os.getenv(
                "OLLAMA_ASSISTANT_MODEL_BALANCED",
                os.getenv("OLLAMA_ASSISTANT_MODEL", "qwen3:4b"),
            )
        return os.getenv(
            "OLLAMA_ASSISTANT_MODEL_LOW",
            os.getenv("OLLAMA_ASSISTANT_MODEL", "qwen3:4b"),
        )
    if provider == "anthropic":
        if hermes_profile == "finance_strategy":
            return os.getenv(
                "ANTHROPIC_ASSISTANT_MODEL_HERMES_FINANCE_STRATEGY",
                os.getenv(
                    "ANTHROPIC_ASSISTANT_MODEL_HERMES",
                    os.getenv(
                        "ANTHROPIC_ASSISTANT_MODEL_HIGH",
                        os.getenv(
                            "ANTHROPIC_ASSISTANT_MODEL",
                            "claude-sonnet-4-5-20250929",
                        ),
                    ),
                ),
            )
        if normalized_mode == "calidad":
            return os.getenv(
                "ANTHROPIC_ASSISTANT_MODEL_HIGH",
                os.getenv("ANTHROPIC_ASSISTANT_MODEL", "claude-sonnet-4-5-20250929"),
            )
        if normalized_mode == "balanceado":
            return os.getenv(
                "ANTHROPIC_ASSISTANT_MODEL_BALANCED",
                os.getenv("ANTHROPIC_ASSISTANT_MODEL", "claude-sonnet-4-5-20250929"),
            )
        return os.getenv(
            "ANTHROPIC_ASSISTANT_MODEL_LOW",
            os.getenv("ANTHROPIC_ASSISTANT_MODEL", "claude-sonnet-4-5-20250929"),
        )
    if normalized_mode == "calidad":
        return os.getenv(
            "OPENAI_ASSISTANT_MODEL_HIGH",
            os.getenv("OPENAI_ASSISTANT_MODEL", "gpt-4.1"),
        )
    if normalized_mode == "balanceado":
        return os.getenv(
            "OPENAI_ASSISTANT_MODEL_BALANCED",
            os.getenv("OPENAI_ASSISTANT_MODEL", "gpt-4o-mini"),
        )
    return os.getenv(
        "OPENAI_ASSISTANT_MODEL_LOW",
        os.getenv("OPENAI_ASSISTANT_MODEL", "gpt-4o-mini"),
    )
