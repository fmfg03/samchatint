"""Read-only case memory resolver for specialist assistant previews."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping, Sequence

from sqlalchemy import or_, select

from devnous.gastos.models import AssistantArtifact, AssistantConversation

from .case_memory import CASE_MEMORY_ARTIFACT_TYPE, score_case_memory_artifacts

_MAX_MEMORY_SNIPPETS = 3
_MAX_QUERY_TOKENS = 10
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{2,}")


def _metadata(conversation: Any) -> Mapping[str, Any]:
    value = getattr(conversation, "metadata_", None) or {}
    return value if isinstance(value, Mapping) else {}


def _string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _tokens(raw_message: str, understood_context: Mapping[str, Any]) -> List[str]:
    candidates: List[str] = []
    candidates.extend(_TOKEN_PATTERN.findall(str(raw_message or "")))
    for key in (
        "document_refs",
        "operations_refs",
        "uuid_or_prefixes",
        "account_codes",
        "domains",
        "entities",
    ):
        value = understood_context.get(key)
        if isinstance(value, str):
            candidates.extend(_TOKEN_PATTERN.findall(value))
        elif isinstance(value, Sequence):
            for item in value:
                candidates.extend(_TOKEN_PATTERN.findall(str(item or "")))
    seen: set[str] = set()
    result: List[str] = []
    for token in candidates:
        normalized = token.strip().lower()
        if len(normalized) < 3 or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
        if len(result) >= _MAX_QUERY_TOKENS:
            break
    return result


def _scalars(result: Any) -> Sequence[Any]:
    scalars = result.scalars()
    all_rows = getattr(scalars, "all", None)
    if callable(all_rows):
        return all_rows()
    return list(scalars)


async def resolve_specialist_preview_memory_context(
    *,
    session: Any,
    conversation: Any,
    raw_message: str,
    understood_context: Mapping[str, Any],
    top_k: int = _MAX_MEMORY_SNIPPETS,
) -> Dict[str, Any]:
    """Resolve prior deterministic case memory for a specialist preview.

    Memory is read-only context. It can inform the preview but it never creates
    authority and never unlocks execution.
    """

    limit = max(1, min(int(top_k or _MAX_MEMORY_SNIPPETS), _MAX_MEMORY_SNIPPETS))
    base: Dict[str, Any] = {
        "source": "case_memory_artifacts",
        "lookup_performed": False,
        "authority": "read_only_memory",
        "matched": False,
        "status": "not_started",
        "snippets": [],
        "limits": {"top_k": limit},
    }
    if not hasattr(session, "execute"):
        base["status"] = "skipped_no_db_session"
        return base

    empleado_id = getattr(conversation, "empleado_id", None)
    if not empleado_id:
        base["status"] = "skipped_no_employee_scope"
        return base

    tokens = _tokens(raw_message, understood_context)
    if not tokens:
        base["status"] = "skipped_no_query_terms"
        return base

    base["lookup_performed"] = True
    metadata = _metadata(conversation)
    module_key = _string(metadata.get("module_key"))
    tournament_key = _string(getattr(conversation, "tournament_key", None))
    current_conversation_id = _string(getattr(conversation, "id", None))

    try:
        predicates = [AssistantArtifact.content.ilike(f"%{token}%") for token in tokens]
        stmt = (
            select(AssistantArtifact)
            .join(
                AssistantConversation,
                AssistantConversation.id == AssistantArtifact.conversation_id,
            )
            .where(AssistantConversation.empleado_id == empleado_id)
            .where(AssistantConversation.archived.is_(False))
            .where(AssistantArtifact.artifact_type == CASE_MEMORY_ARTIFACT_TYPE)
            .where(or_(*predicates))
            .order_by(AssistantArtifact.created_at.desc())
            .limit(max(limit * 4, 6))
        )
        if current_conversation_id:
            stmt = stmt.where(
                AssistantArtifact.conversation_id != current_conversation_id
            )
        artifacts = list(_scalars(await session.execute(stmt)))
        snippets = score_case_memory_artifacts(
            artifacts=artifacts,
            tokens=tokens,
            module_key=module_key,
            tournament_key=tournament_key,
            memory_weight=1.0,
        )[:limit]
    except Exception as exc:  # pragma: no cover - defensive read-only boundary
        base["status"] = "lookup_error"
        base["error_type"] = type(exc).__name__
        return base

    base["snippets"] = snippets
    base["matched"] = bool(snippets)
    base["status"] = "matched" if snippets else "no_matches"
    return base


def render_specialist_memory_context_markdown(memory_context: Mapping[str, Any]) -> str:
    """Render read-only case memory for specialist preview messages."""

    if not memory_context.get("matched"):
        return ""
    lines = ["## Memoria de casos", ""]
    for snippet in list(memory_context.get("snippets") or [])[:_MAX_MEMORY_SNIPPETS]:
        label = snippet.get("label") or "memory"
        score = snippet.get("score")
        text = str(snippet.get("text") or "").strip()
        if len(text) > 420:
            text = text[:417].rstrip() + "..."
        score_text = f" score {score}" if score is not None else ""
        lines.append(f"- {label}{score_text}: {text}")
    lines.append("- Alcance: precedente read-only; no autoriza ejecucion ni cambios.")
    return "\n".join(lines) + "\n"
