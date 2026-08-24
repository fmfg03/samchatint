from __future__ import annotations

import json
import re
import unicodedata
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, List, Mapping, Optional, Sequence

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from devnous.gastos.models import (
    AssistantArtifact,
    AssistantConversation,
    AssistantMessage,
    AssistantRun,
)


CASE_MEMORY_ARTIFACT_TYPE = "case_memory_summary"

CASE_MEMORY_SAVE_COMMAND = "save"
CASE_MEMORY_RESUME_COMMAND = "resume"


@dataclass(slots=True)
class CaseMemorySummary:
    conversation_id: str
    title: Optional[str] = None
    objective: Optional[str] = None
    case_status: str = "open"
    scope: dict[str, Any] = field(default_factory=dict)
    documents: List[str] = field(default_factory=list)
    findings: List[str] = field(default_factory=list)
    decisions: List[str] = field(default_factory=list)
    open_questions: List[str] = field(default_factory=list)
    artifacts_consulted: List[str] = field(default_factory=list)
    previews: List[str] = field(default_factory=list)
    approvals: List[str] = field(default_factory=list)
    execution_receipts: List[str] = field(default_factory=list)
    limitations: List[str] = field(default_factory=list)
    last_action: Optional[str] = None
    next_step: Optional[str] = None
    resume_hint: Optional[str] = None
    non_claims: List[str] = field(default_factory=list)
    message_count: int = 0
    run_count: int = 0
    source_message_ids: List[str] = field(default_factory=list)
    source_run_ids: List[str] = field(default_factory=list)
    updated_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "conversation_id": self.conversation_id,
            "title": self.title,
            "objective": self.objective,
            "case_status": self.case_status,
            "scope": self.scope,
            "documents": self.documents,
            "findings": self.findings,
            "decisions": self.decisions,
            "open_questions": self.open_questions,
            "artifacts_consulted": self.artifacts_consulted,
            "previews": self.previews,
            "approvals": self.approvals,
            "execution_receipts": self.execution_receipts,
            "limitations": self.limitations,
            "last_action": self.last_action,
            "next_step": self.next_step,
            "resume_hint": self.resume_hint,
            "non_claims": self.non_claims,
            "message_count": self.message_count,
            "run_count": self.run_count,
            "source_message_ids": self.source_message_ids,
            "source_run_ids": self.source_run_ids,
            "updated_at": self.updated_at,
        }

    def to_markdown(self) -> str:
        lines = ["# Assistant Case Memory Summary", ""]
        lines.append(f"Conversation: `{self.conversation_id}`")
        if self.title:
            lines.append(f"Title: {self.title}")
        lines.append(f"Updated: {self.updated_at}")
        lines.append("")
        lines.append("## Case status")
        lines.append("")
        lines.append(self.case_status or "open")
        lines.append("")
        lines.append("## Objective")
        lines.append("")
        lines.append(self.objective or "No explicit objective captured yet.")
        lines.append("")
        if self.scope:
            lines.append("## Scope")
            lines.append("")
            for key, value in sorted(self.scope.items()):
                if value not in (None, "", [], {}):
                    lines.append(f"- {key}: {value}")
            lines.append("")
        sections = (
            ("Documents", self.documents),
            ("Findings", self.findings),
            ("Decisions", self.decisions),
            ("Open questions", self.open_questions),
            ("Artifacts consulted", self.artifacts_consulted),
            ("Previews", self.previews),
            ("Approvals", self.approvals),
            ("Execution receipts", self.execution_receipts),
            ("Limitations", self.limitations),
            ("Non-claims", self.non_claims),
        )
        for title, values in sections:
            lines.append(f"## {title}")
            lines.append("")
            if values:
                lines.extend(f"- {value}" for value in values)
            else:
                lines.append("- None captured.")
            lines.append("")
        lines.append("## Last action")
        lines.append("")
        lines.append(self.last_action or "No last action captured.")
        lines.append("")
        lines.append("## Next step")
        lines.append("")
        lines.append(self.next_step or "No next step captured.")
        lines.append("")
        lines.append("## Resume hint")
        lines.append("")
        lines.append(self.resume_hint or "Ask explicitly to resume this case before assuming continuity.")
        lines.append("")
        lines.append("## Source counts")
        lines.append("")
        lines.append(f"- Messages: {self.message_count}")
        lines.append(f"- Runs: {self.run_count}")
        return "\n".join(lines).strip() + "\n"


def _compact(text: Any, *, limit: int = 240) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 3)].rstrip() + "..."


def _append_unique(target: List[str], value: Any, *, limit: int = 8) -> None:
    compact = _compact(value)
    if not compact or compact in target:
        return
    if len(target) < limit:
        target.append(compact)


def _extract_file_mentions(text: str) -> List[str]:
    patterns = [
        r"[\w .()\-??????????????]+\.(?:xml|pdf|jpg|jpeg|png|xlsx|xls|csv)",
        r"Documento\s+[A-Z]-\d+",
        r"I-\d+",
        r"S-\d+",
    ]
    found: List[str] = []
    for pattern in patterns:
        for match in re.findall(pattern, text, flags=re.IGNORECASE):
            _append_unique(found, match, limit=12)
    return found


def _extract_textual_signals(
    summary: CaseMemorySummary, messages: Sequence[Any]
) -> None:
    for msg in messages:
        role = str(getattr(msg, "role", "") or "")
        content = str(getattr(msg, "content", "") or "")
        if not content.strip():
            continue
        if role == "user" and summary.objective is None:
            summary.objective = _compact(content, limit=300)
        summary.last_action = f"{role}: {_compact(content, limit=260)}"
        for doc in _extract_file_mentions(content):
            _append_unique(summary.documents, doc, limit=12)
        lowered = content.lower()
        if any(token in lowered for token in ("aprobado", "apruebo", "aprobada")):
            _append_unique(summary.approvals, content, limit=8)
        if any(token in lowered for token in ("decid", "correcto", "queda", "regla")):
            _append_unique(summary.decisions, content, limit=8)
        if any(
            token in lowered for token in ("pendiente", "falta", "missing", "bloque")
        ):
            _append_unique(summary.open_questions, content, limit=8)
        if any(
            token in lowered
            for token in ("error", "inconsisten", "duplic", "no coincide")
        ):
            _append_unique(summary.findings, content, limit=8)
        if any(token in lowered for token in ("preview", "vista previa", "diff")):
            _append_unique(summary.previews, content, limit=8)
        if any(token in lowered for token in ("no ejecut", "writes", "canary")):
            _append_unique(summary.limitations, content, limit=8)
        elif role == "assistant" and "read-only" in lowered:
            _append_unique(summary.limitations, content, limit=8)


def _extract_trace_signals(summary: CaseMemorySummary, runs: Sequence[Any]) -> None:
    for run in runs:
        status = str(getattr(run, "status", "") or "")
        if status == "pending_confirmation":
            tool = getattr(run, "pending_tool_name", None)
            _append_unique(
                summary.previews, f"Pending confirmation for {tool or 'tool'}", limit=8
            )
        if status == "provider_timeout":
            _append_unique(
                summary.limitations,
                "Provider timeout occurred; no action executed.",
                limit=8,
            )
        if status == "completed" and getattr(run, "pending_tool_name", None) is None:
            trace = getattr(run, "tool_trace", None) or []
            if any(isinstance(item, Mapping) and item.get("tool") for item in trace):
                _append_unique(
                    summary.execution_receipts,
                    f"Completed run {run.id} with tool trace.",
                    limit=8,
                )
        trace = getattr(run, "tool_trace", None) or []
        for item in trace:
            if not isinstance(item, Mapping):
                continue
            if "retrieval" in item:
                sources = (item.get("retrieval") or {}).get("sources") or []
                for source in sources[:3]:
                    label = (
                        source.get("label") if isinstance(source, Mapping) else source
                    )
                    _append_unique(
                        summary.findings, f"Retrieved context: {label}", limit=8
                    )
                    _append_unique(
                        summary.artifacts_consulted, label, limit=12
                    )
            tool_name = item.get("tool")
            if tool_name:
                _append_unique(summary.artifacts_consulted, f"tool:{tool_name}", limit=12)
            surface = item.get("specialist_preview_surface")
            if isinstance(surface, Mapping):
                for source in surface.get("source_panel") or []:
                    if isinstance(source, Mapping):
                        _append_unique(
                            summary.artifacts_consulted,
                            source.get("title") or source.get("source_id"),
                            limit=12,
                        )
            if "assistant_plan" in item:
                _append_unique(
                    summary.previews,
                    f"Assistant plan: {json.dumps(item['assistant_plan'], ensure_ascii=False)[:240]}",
                    limit=8,
                )


def finalize_case_memory_summary(summary: CaseMemorySummary) -> None:
    """Fill deterministic continuity fields without inventing case state."""

    if summary.open_questions:
        summary.case_status = "waiting_context"
        summary.next_step = f"Resolver pendiente: {summary.open_questions[0]}"
    elif summary.previews:
        summary.case_status = "preview_ready"
        summary.next_step = "Revisar preview/diff guardado antes de cualquier ejecucion."
    elif summary.decisions or summary.findings:
        summary.case_status = "ready_to_resume"
        summary.next_step = "Retomar desde decisiones y hallazgos guardados; confirmar vigencia antes de actuar."
    else:
        summary.case_status = "open"
        summary.next_step = "Aportar mas contexto antes de continuar; no hay continuidad suficiente."

    if not summary.last_action:
        summary.last_action = "No user/assistant action captured."
    summary.resume_hint = (
        "Pide 'retoma este caso' en esta conversacion para reabrir la memoria guardada."
    )
    _append_unique(
        summary.non_claims,
        "La memoria es contexto read-only: no autoriza ejecuciones, pagos, mutaciones ni aprobaciones.",
        limit=8,
    )
    if not (summary.documents or summary.findings or summary.decisions or summary.open_questions):
        _append_unique(
            summary.non_claims,
            "No hay evidencia suficiente para afirmar continuidad operativa detallada.",
            limit=8,
        )


def _normalize_command_text(text: str) -> str:
    normalized = (text or "").strip().lower()
    decomposed = unicodedata.normalize("NFKD", normalized)
    without_marks = "".join(
        char for char in decomposed if not unicodedata.combining(char)
    )
    return re.sub(r"\s+", " ", without_marks)


def detect_case_memory_command(raw_message: str) -> Optional[str]:
    """Detect explicit case-memory commands. Generic 'sigamos' is not enough."""

    normalized = _normalize_command_text(raw_message)
    save_phrases = (
        "guarda este caso",
        "guardar este caso",
        "guarda la memoria",
        "guardar memoria",
        "guarda memoria",
        "deja este caso retomable",
        "dejar este caso retomable",
        "cierra memoria del caso",
    )
    resume_phrases = (
        "retoma este caso",
        "retomar este caso",
        "reanuda este caso",
        "reanudar este caso",
        "resume este asunto",
        "resumen de este caso",
        "que quedo pendiente de este caso",
        "donde ibamos con este caso",
        "abre la memoria del caso",
    )
    if any(phrase in normalized for phrase in save_phrases):
        return CASE_MEMORY_SAVE_COMMAND
    if any(phrase in normalized for phrase in resume_phrases):
        return CASE_MEMORY_RESUME_COMMAND
    return None


def _summary_dict_from_artifact(artifact: Any) -> dict[str, Any]:
    metadata = getattr(artifact, "metadata_", None) or {}
    if isinstance(metadata, Mapping):
        case_memory = metadata.get("case_memory")
        if isinstance(case_memory, Mapping):
            return dict(case_memory)
    return {
        "conversation_id": str(getattr(artifact, "conversation_id", "") or ""),
        "title": getattr(artifact, "title", None),
        "objective": _compact(getattr(artifact, "content", ""), limit=300),
        "case_status": "ready_to_resume",
        "documents": [],
        "findings": [],
        "decisions": [],
        "open_questions": [],
        "artifacts_consulted": [],
        "last_action": None,
        "next_step": "Revisar la memoria guardada antes de continuar.",
        "resume_hint": "Memoria cargada desde artefacto legacy.",
        "non_claims": [
            "La memoria legacy no autoriza ejecuciones ni cambios por si sola."
        ],
    }


async def load_latest_case_memory_summary(
    session: AsyncSession,
    *,
    conversation_id: str,
) -> dict[str, Any]:
    """Load the latest explicit case memory for this conversation, fail-closed."""

    try:
        cid = uuid.UUID(str(conversation_id))
        artifact = (
            (
                await session.execute(
                    select(AssistantArtifact)
                    .where(AssistantArtifact.conversation_id == cid)
                    .where(AssistantArtifact.artifact_type == CASE_MEMORY_ARTIFACT_TYPE)
                    .order_by(desc(AssistantArtifact.created_at))
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
    except Exception as exc:  # pragma: no cover - defensive read-only boundary
        return {
            "status": "lookup_error",
            "matched": False,
            "error_type": type(exc).__name__,
            "writes_attempted": False,
        }
    if artifact is None:
        return {
            "status": "no_case_memory",
            "matched": False,
            "conversation_id": str(conversation_id),
            "writes_attempted": False,
        }
    return {
        "status": "matched",
        "matched": True,
        "artifact_id": str(getattr(artifact, "id", "") or ""),
        "conversation_id": str(conversation_id),
        "summary": _summary_dict_from_artifact(artifact),
        "writes_attempted": False,
    }


def _render_list(lines: List[str], title: str, values: Any, *, limit: int = 5) -> None:
    items = list(values or []) if isinstance(values, list) else []
    if not items:
        return
    lines.append(f"- {title}:")
    for item in items[:limit]:
        lines.append(f"  - {_compact(item, limit=220)}")


def render_case_memory_saved_markdown(result: Mapping[str, Any]) -> str:
    summary = result.get("summary") if isinstance(result.get("summary"), Mapping) else {}
    lines = ["## Memoria de caso guardada", ""]
    lines.append(f"- Artefacto: `{result.get('artifact_id') or '-'}`")
    lines.append(f"- Estado del caso: {summary.get('case_status') or 'open'}")
    lines.append(f"- Objetivo: {summary.get('objective') or 'Sin objetivo explicito capturado.'}")
    if summary.get("last_action"):
        lines.append(f"- Ultima accion: {summary.get('last_action')}")
    if summary.get("next_step"):
        lines.append(f"- Siguiente paso: {summary.get('next_step')}")
    _render_list(lines, "Documentos", summary.get("documents"))
    _render_list(lines, "Decisiones", summary.get("decisions"))
    _render_list(lines, "Pendientes", summary.get("open_questions"))
    _render_list(lines, "Artefactos consultados", summary.get("artifacts_consulted"))
    lines.append("")
    lines.append("Frontera de autoridad: memoria read-only; no ejecuta cambios ni autoriza acciones.")
    return "\n".join(lines) + "\n"


def render_case_memory_resume_markdown(resolution: Mapping[str, Any]) -> str:
    lines = ["## Memoria de caso", ""]
    if not resolution.get("matched"):
        lines.append("No encontre memoria de caso guardada en esta conversacion.")
        lines.append(
            "Para no inventar continuidad, primero guarda un caso o dime la referencia concreta que quieres revisar."
        )
        lines.append("")
        lines.append("Frontera de autoridad: no ejecute acciones ni asumi contexto historico.")
        return "\n".join(lines) + "\n"

    summary = resolution.get("summary") if isinstance(resolution.get("summary"), Mapping) else {}
    lines.append(f"- Artefacto: `{resolution.get('artifact_id') or '-'}`")
    lines.append(f"- Estado del caso: {summary.get('case_status') or 'open'}")
    lines.append(f"- Objetivo: {summary.get('objective') or 'Sin objetivo explicito capturado.'}")
    if summary.get("last_action"):
        lines.append(f"- Ultima accion: {summary.get('last_action')}")
    if summary.get("next_step"):
        lines.append(f"- Siguiente paso: {summary.get('next_step')}")
    _render_list(lines, "Documentos", summary.get("documents"))
    _render_list(lines, "Hallazgos", summary.get("findings"))
    _render_list(lines, "Decisiones", summary.get("decisions"))
    _render_list(lines, "Pendientes", summary.get("open_questions"))
    _render_list(lines, "Artefactos consultados", summary.get("artifacts_consulted"))
    _render_list(lines, "Non-claims", summary.get("non_claims"))
    lines.append("")
    lines.append("Frontera de autoridad: continuidad read-only; no ejecuta acciones ni desbloquea writes.")
    return "\n".join(lines) + "\n"


def build_case_memory_summary(
    *,
    conversation: Any,
    messages: Sequence[Any],
    runs: Sequence[Any],
) -> CaseMemorySummary:
    metadata = getattr(conversation, "metadata_", None) or {}
    scope = {}
    if isinstance(metadata, Mapping):
        for key in (
            "module_key",
            "module_label",
            "external_session_id",
            "active_tournament_goal_case",
        ):
            if metadata.get(key) not in (None, "", [], {}):
                scope[key] = metadata.get(key)
    tournament_key = getattr(conversation, "tournament_key", None)
    if tournament_key:
        scope["tournament_key"] = tournament_key
    summary = CaseMemorySummary(
        conversation_id=str(getattr(conversation, "id", "")),
        title=getattr(conversation, "title", None),
        scope=scope,
        message_count=len(messages),
        run_count=len(runs),
        source_message_ids=[
            str(getattr(msg, "id", "")) for msg in messages if getattr(msg, "id", None)
        ],
        source_run_ids=[
            str(getattr(run, "id", "")) for run in runs if getattr(run, "id", None)
        ],
    )
    _extract_textual_signals(summary, messages)
    _extract_trace_signals(summary, runs)
    finalize_case_memory_summary(summary)
    return summary


def score_case_memory_artifacts(
    *,
    artifacts: Sequence[Any],
    tokens: Sequence[str],
    module_key: Optional[str] = None,
    tournament_key: Optional[str] = None,
    memory_weight: float = 1.0,
) -> List[dict[str, Any]]:
    """Rank persisted case summaries as memory snippets.

    Case summaries are intentionally preferred over raw chat when they match the
    same query because they are compact, source-scoped, and less noisy.
    """
    token_list = [str(token).strip().lower() for token in tokens if str(token).strip()]
    if not token_list:
        return []
    module_key_norm = str(module_key or "").strip().lower()
    tournament_key_norm = str(tournament_key or "").strip().lower()
    scored: List[dict[str, Any]] = []
    for artifact in artifacts:
        content = str(getattr(artifact, "content", "") or "").strip()
        if not content:
            continue
        haystack = content.lower()
        hits = sum(1.0 for token in token_list if token in haystack)
        base_score = hits / max(1, len(token_list))
        if base_score <= 0:
            continue
        metadata = getattr(artifact, "metadata_", None) or {}
        case_memory = (
            metadata.get("case_memory") if isinstance(metadata, Mapping) else None
        )
        scope = case_memory.get("scope") if isinstance(case_memory, Mapping) else None
        if not isinstance(scope, Mapping):
            scope = {}
        artifact_module_key = str(scope.get("module_key") or "").strip().lower()
        artifact_tournament_key = str(scope.get("tournament_key") or "").strip().lower()
        module_boost = (
            0.12 if module_key_norm and module_key_norm == artifact_module_key else 0.0
        )
        tournament_boost = (
            0.08
            if tournament_key_norm and tournament_key_norm == artifact_tournament_key
            else 0.0
        )
        score = round(
            (base_score * memory_weight) + 0.18 + module_boost + tournament_boost, 4
        )
        scored.append(
            {
                "type": "memory",
                "score": score,
                "base_score": round(base_score, 4),
                "recency_score": 0.0,
                "label": f"memory:case_summary:{artifact.id}",
                "text": f"Memoria de caso resumida :: {_compact(content, limit=1200)}",
                "conversation_id": str(getattr(artifact, "conversation_id", "")),
                "module_key": artifact_module_key or None,
                "tournament_key": artifact_tournament_key or None,
                "artifact_id": str(getattr(artifact, "id", "")),
            }
        )
    scored.sort(key=lambda x: float(x.get("score", 0)), reverse=True)
    return scored


async def load_case_memory_inputs(
    session: AsyncSession,
    *,
    conversation_id: str,
    message_limit: int = 80,
    run_limit: int = 30,
) -> tuple[Any, List[Any], List[Any]]:
    cid = uuid.UUID(str(conversation_id))
    conversation = await session.get(AssistantConversation, cid)
    messages = list(
        reversed(
            (
                await session.execute(
                    select(AssistantMessage)
                    .where(AssistantMessage.conversation_id == cid)
                    .where(AssistantMessage.role.in_(("user", "assistant")))
                    .order_by(desc(AssistantMessage.created_at))
                    .limit(max(1, min(message_limit, 200)))
                )
            )
            .scalars()
            .all()
        )
    )
    runs = list(
        reversed(
            (
                await session.execute(
                    select(AssistantRun)
                    .where(AssistantRun.conversation_id == cid)
                    .order_by(desc(AssistantRun.created_at))
                    .limit(max(1, min(run_limit, 100)))
                )
            )
            .scalars()
            .all()
        )
    )
    return conversation, messages, runs


async def persist_case_memory_summary(
    session: AsyncSession,
    *,
    conversation_id: str,
    created_by_empleado_id: str,
    message_limit: int = 80,
    run_limit: int = 30,
) -> dict[str, Any]:
    conversation, messages, runs = await load_case_memory_inputs(
        session,
        conversation_id=conversation_id,
        message_limit=message_limit,
        run_limit=run_limit,
    )
    summary = build_case_memory_summary(
        conversation=conversation,
        messages=messages,
        runs=runs,
    )
    artifact = AssistantArtifact(
        conversation_id=uuid.UUID(str(conversation_id)),
        created_by_empleado_id=uuid.UUID(str(created_by_empleado_id)),
        title=f"Case memory ? {summary.title or str(conversation_id)[:8]}",
        artifact_type=CASE_MEMORY_ARTIFACT_TYPE,
        format="markdown",
        content=summary.to_markdown(),
        metadata_={"case_memory": summary.to_dict(), "source": "deterministic"},
    )
    session.add(artifact)
    await session.commit()
    await session.refresh(artifact)
    return {
        "artifact_id": str(artifact.id),
        "artifact_type": artifact.artifact_type,
        "conversation_id": str(conversation_id),
        "summary": summary.to_dict(),
    }
