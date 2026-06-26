"""Local-only registration extraction adjudication."""

from __future__ import annotations

import copy
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class RegistrationAdjudicationResult:
    extraction: Dict[str, Any]
    applied: bool
    raw: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None


def _env_flag(name: str, default: bool = False) -> bool:
    value = (os.getenv(name) or "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


def _timeout_seconds() -> float:
    try:
        return max(
            1.0,
            float(os.getenv("REGISTRATION_ADJUDICATOR_TIMEOUT_SECONDS", "45")),
        )
    except ValueError:
        return 45.0


def _ollama_base_url() -> str:
    return (
        os.getenv("REGISTRATION_ADJUDICATOR_OLLAMA_URL")
        or os.getenv("OLLAMA_BASE_URL")
        or "http://127.0.0.1:11434"
    ).rstrip("/")


def _json_from_text(value: str) -> Optional[Dict[str, Any]]:
    text = (value or "").strip()
    if not text:
        return None
    text = re.sub(r"(?is)^.*?</think>", "", text).strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < 0 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def _ollama_chat(messages: List[Dict[str, str]], *, model: str) -> Dict[str, Any]:
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "think": False,
        "options": {
            "temperature": 0.0,
            "top_p": 0.2,
            "num_predict": 1800,
        },
    }
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{_ollama_base_url()}/api/chat",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=_timeout_seconds()) as response:
        return json.loads(response.read().decode("utf-8"))


def _message_content(payload: Dict[str, Any]) -> str:
    message = payload.get("message") if isinstance(payload, dict) else None
    if isinstance(message, dict):
        return str(message.get("content") or "")
    return str(payload.get("response") or "")


def _prompt_payload(
    current_extraction: Dict[str, Any],
    mineru_extraction: Dict[str, Any],
    mineru_text: str,
) -> List[Dict[str, str]]:
    system = (
        "Eres un adjudicador local de OCR para cedulas mexicanas de registro "
        "de equipos de futbol. No inventes datos. Devuelve JSON valido y nada "
        "mas. Si dos fuentes discrepan, conserva el valor mas legible solo si "
        "hay evidencia textual; de lo contrario usa null y marca needs_review. "
        "Cada jugador dudoso debe tener needs_review=true."
    )
    user = {
        "task": "Compare current OCR and MinerU OCR, then produce one extraction.",
        "schema": {
            "extraction": {
                "team": "dict",
                "manager": "dict|null",
                "players": "list[dict]",
                "overall_confidence": "number",
                "notes": "string",
            },
            "field_notes": "dict optional",
        },
        "current_ocr": current_extraction,
        "mineru_ocr": mineru_extraction,
        "mineru_text_excerpt": (mineru_text or "")[:6000],
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user, ensure_ascii=True)},
    ]


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().upper()


def _player_key(player: Dict[str, Any]) -> Tuple[str, str]:
    return (_norm(player.get("name")), _norm(player.get("birth_date")))


def _curp_conflicts(
    current_extraction: Dict[str, Any],
    mineru_extraction: Dict[str, Any],
) -> List[Dict[str, Any]]:
    current_players = [
        item
        for item in current_extraction.get("players") or []
        if isinstance(item, dict)
    ]
    mineru_players = [
        item
        for item in mineru_extraction.get("players") or []
        if isinstance(item, dict)
    ]
    mineru_by_key = {_player_key(player): player for player in mineru_players}
    conflicts: List[Dict[str, Any]] = []
    for player in current_players:
        key = _player_key(player)
        if not key[0] or key not in mineru_by_key:
            continue
        current_curp = _norm(player.get("curp"))
        mineru_curp = _norm(mineru_by_key[key].get("curp"))
        if current_curp and mineru_curp and current_curp != mineru_curp:
            conflicts.append(
                {
                    "field": "curp",
                    "player_key": key,
                    "current": current_curp,
                    "mineru": mineru_curp,
                }
            )
    return conflicts


def _mark_conflicts(
    extraction: Dict[str, Any],
    conflicts: List[Dict[str, Any]],
) -> Dict[str, Any]:
    if not conflicts:
        return extraction
    marked = copy.deepcopy(extraction)
    conflict_keys = {tuple(item["player_key"]) for item in conflicts}
    for player in marked.get("players") or []:
        if not isinstance(player, dict):
            continue
        if _player_key(player) in conflict_keys:
            player["needs_review"] = True
            reasons = list(player.get("integrity_reasons") or [])
            if "adjudication_conflict_curp" not in reasons:
                reasons.append("adjudication_conflict_curp")
            player["integrity_reasons"] = reasons
    return marked


def adjudicate_registration_extraction(
    current_extraction: Dict[str, Any],
    mineru_extraction: Optional[Dict[str, Any]],
    *,
    mineru_text: str = "",
) -> RegistrationAdjudicationResult:
    """Adjudicate registration OCR with local Ollama when explicitly enabled."""
    base = copy.deepcopy(current_extraction or {})
    if not _env_flag("REGISTRATION_ADJUDICATOR_ENABLED", False):
        return RegistrationAdjudicationResult(
            extraction=base,
            applied=False,
            error="disabled",
        )
    if not mineru_extraction:
        return RegistrationAdjudicationResult(
            extraction=base,
            applied=False,
            error="mineru_candidate_missing",
        )

    provider = (
        os.getenv("REGISTRATION_ADJUDICATOR_PROVIDER") or "ollama"
    ).strip().lower()
    if provider != "ollama":
        return RegistrationAdjudicationResult(
            extraction=base,
            applied=False,
            error="unsupported_provider",
            raw={"provider": provider},
        )

    model = os.getenv("REGISTRATION_ADJUDICATOR_MODEL", "qwen3:4b")
    conflicts = _curp_conflicts(base, mineru_extraction)
    try:
        payload = _ollama_chat(
            _prompt_payload(base, mineru_extraction, mineru_text),
            model=model,
        )
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        return RegistrationAdjudicationResult(
            extraction=_mark_conflicts(base, conflicts),
            applied=False,
            error="ollama_unavailable",
            raw={
                "provider": provider,
                "model": model,
                "message": str(exc),
                "conflicts": conflicts,
            },
        )

    parsed = _json_from_text(_message_content(payload))
    if not parsed:
        return RegistrationAdjudicationResult(
            extraction=_mark_conflicts(base, conflicts),
            applied=False,
            error="invalid_json",
            raw={
                "provider": provider,
                "model": model,
                "payload": payload,
                "conflicts": conflicts,
            },
        )

    extraction = (
        parsed.get("extraction")
        if isinstance(parsed.get("extraction"), dict)
        else parsed
    )
    extraction = _mark_conflicts(extraction, conflicts)
    return RegistrationAdjudicationResult(
        extraction=extraction,
        applied=True,
        raw={
            "provider": provider,
            "model": model,
            "field_notes": (
                parsed.get("field_notes") if isinstance(parsed, dict) else None
            ),
            "conflicts": conflicts,
        },
    )
