from __future__ import annotations

import base64
import asyncio
import csv
import html
import io
import json
import logging
import math
import os
import re
import time
import uuid
import secrets
import string
import unicodedata
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request
from collections import defaultdict, deque
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Literal, Optional, Tuple

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Path as PathParam,
    Query,
    Request,
    Response,
    UploadFile,
)
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
import httpx

from devnous.gastos.models import (
    AssistantConversation,
    AssistantMessage,
    AssistantRun,
    CuentaDeGastos,
    Documento,
    Empleado,
    ExpenseReport,
    ProveedorCliente,
    Tournament,
)
from devnous.gastos.routes.dependencies import (
    get_current_empleado_or_service as get_current_empleado,
    get_db_session,
    has_permission,
)
from devnous.gastos.utils.receipt_bytes import read_upload_limited

from .db import get_tournament_session_maker
from .action_router import (
    execute_canonical_action,
    supported_read_actions,
    supported_write_actions,
)
from .context import AssistantContext
from .file_parsing import (
    dataframe_records as _dataframe_records,
    extract_document_text_from_bytes,
    spreadsheet_records_from_bytes as _spreadsheet_records_from_bytes,
)
from .upload_service import extract_text_from_media
from .conversation_service import (
    run_conversation_turn,
    run_message_turn_with_pending,
)
from .provider_service import (
    assistant_contextual_pref as _provider_assistant_contextual_pref,
    assistant_inference_tier as _provider_assistant_inference_tier,
    assistant_model as _provider_assistant_model,
    assistant_provider_order as _provider_assistant_provider_order,
    assistant_provider_order_from_pref as _provider_assistant_provider_order_from_pref,
    assistant_remote_allowed as _provider_assistant_remote_allowed,
    csv_items as _provider_csv_items,
    env_bool as _provider_env_bool,
    env_float as _provider_env_float,
    env_int as _provider_env_int,
    get_anthropic_client as _provider_get_anthropic_client,
    get_openai_client as _provider_get_openai_client,
    matches_policy_target as _provider_matches_policy_target,
    normalize_assistant_mode as _provider_normalize_assistant_mode,
)
from .turn_service import (
    build_cached_response as _build_cached_response,
    build_turn_messages as _build_turn_messages,
    prepare_turn_state as _prepare_turn_state,
)
from .provider_execution import execute_provider as _execute_provider
from .agent_runtime import (
    build_agent_runtime_trace as _build_agent_runtime_trace,
    evaluate_runtime_tool_call as _evaluate_runtime_tool_call,
    is_agent_runtime_enabled as _is_agent_runtime_enabled,
)
from .rag import get_rag_store
from .tool_registry import build_tool_registry as _build_tool_registry
from samchat.budgets.service import (
    build_budget_snapshot,
    list_budget_lines,
    list_budget_versions,
    transition_budget_version,
    update_budget_line,
    update_budget_version_metadata,
)
from samchat.tournaments_v2.services import build_tournament_soul_snapshot
from .tools import (
    finance_accounting_report,
    finance_alerts_scan,
    finance_expense_assign_accounting,
    finance_expense_create,
    finance_expense_post_accounting,
    finance_expense_request_cfdi,
    finance_expense_search,
    finance_strategy_snapshot,
    finance_expense_workflow_status,
    finance_ops_query,
    finance_realtime_report,
    finance_expense_update,
    assistant_save_artifact,
    dev_repo_search,
    dev_file_read,
    dev_file_write,
    dev_file_replace,
    dev_run_checks,
    finance_vendor_create,
    finance_vendor_payments,
    tournament_ops_query,
    tournament_registration_breakdown,
    tournament_schedule_create,
    tournament_schedule_regenerate_from_rules,
    tournament_team_register_from_roster,
)

try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas as pdf_canvas
except Exception:  # pragma: no cover
    A4 = None
    colors = None
    ImageReader = None
    pdf_canvas = None


router = APIRouter(prefix="/api/assistant", tags=["assistant"])
logger = logging.getLogger(__name__)

_RATE_LIMIT_LOCK = Lock()
_MESSAGE_BUCKETS: dict[str, deque[float]] = defaultdict(deque)
_CONFIRM_BUCKETS: dict[str, deque[float]] = defaultdict(deque)
_ASSISTANT_MEDIA_UPLOAD_MAX_BYTES = 15 * 1024 * 1024


def _assistant_request_origin(request: Optional[Request]) -> Optional[Dict[str, Any]]:
    if not request or not getattr(request.state, "auth_via_service", False):
        return None
    return {
        "type": "service",
        "provider": "hermes",
        "actor_id": getattr(request.state, "hermes_actor_id", None),
        "actor_email": getattr(request.state, "hermes_actor_email", None),
    }


_RETRIEVAL_CACHE_LOCK = Lock()
_RETRIEVAL_CACHE: dict[str, Dict[str, Any]] = {}
_RAG_METRICS_LOCK = Lock()
_RAG_METRICS: Dict[str, int] = {
    "retrieval_requests": 0,
    "cache_hits": 0,
    "cache_misses": 0,
    "doc_hits": 0,
    "sql_hits": 0,
}
_RAG_EVAL_HISTORY_LOCK = Lock()
_RAG_EVAL_HISTORY: deque[Dict[str, Any]] = deque(
    maxlen=max(10, int(os.getenv("ASSISTANT_RAG_EVAL_HISTORY_MAX", "200")))
)
_RAG_CONFIG_LOCK = Lock()
_RAG_CONFIG_PATH = Path(
    os.getenv(
        "ASSISTANT_RAG_CONFIG_PATH", "/root/samchat/data/assistant_rag_config.json"
    )
)
_RAG_CONFIG_OVERRIDES: Dict[str, float] = {}
_RAG_CONFIG_HISTORY_LOCK = Lock()
_RAG_CONFIG_HISTORY: deque[Dict[str, Any]] = deque(
    maxlen=max(10, int(os.getenv("ASSISTANT_RAG_CONFIG_HISTORY_MAX", "200")))
)
_SYNTHETIC_JOBS_LOCK = Lock()
_SYNTHETIC_JOBS: Dict[str, Dict[str, Any]] = {}
_ASSISTANT_RESPONSE_CACHE_LOCK = Lock()
_ASSISTANT_RESPONSE_CACHE: Dict[str, Dict[str, Any]] = {}


def _enforce_rate_limit(*, empleado_id: uuid.UUID, kind: str) -> None:
    now = time.time()
    if kind == "confirm":
        max_requests = int(os.getenv("ASSISTANT_CONFIRM_RATE_LIMIT_REQUESTS", "10"))
        window_seconds = int(os.getenv("ASSISTANT_CONFIRM_RATE_LIMIT_WINDOW_SEC", "60"))
        buckets = _CONFIRM_BUCKETS
    else:
        max_requests = int(os.getenv("ASSISTANT_RATE_LIMIT_REQUESTS", "30"))
        window_seconds = int(os.getenv("ASSISTANT_RATE_LIMIT_WINDOW_SEC", "60"))
        buckets = _MESSAGE_BUCKETS

    key = str(empleado_id)
    with _RATE_LIMIT_LOCK:
        bucket = buckets[key]
        cutoff = now - window_seconds
        while bucket and bucket[0] < cutoff:
            bucket.popleft()

        if len(bucket) >= max_requests:
            retry_after = int(max(1, window_seconds - (now - bucket[0])))
            raise HTTPException(
                status_code=429,
                detail=(
                    f"Rate limit exceeded for assistant {kind}. "
                    f"Try again in {retry_after}s."
                ),
                headers={"Retry-After": str(retry_after)},
            )

        bucket.append(now)


def _bump_metric(name: str, value: int = 1) -> None:
    with _RAG_METRICS_LOCK:
        _RAG_METRICS[name] = int(_RAG_METRICS.get(name, 0)) + value


def _normalize_query_for_cache(query: str) -> str:
    return re.sub(r"\s+", " ", (query or "").strip().lower())[:1000]


def _assistant_response_cache_key(
    *,
    empleado_id: uuid.UUID,
    raw_message: str,
    tournament_key: Optional[str],
    module_key: Optional[str],
    bi_year: Optional[int],
    bi_scope: Optional[str],
    bi_segment: Optional[str],
    assistant_mode: Optional[str],
) -> str:
    return (
        f"emp={str(empleado_id)}|"
        f"msg={_normalize_query_for_cache(raw_message)}|"
        f"t={str((tournament_key or '').strip().lower())}|"
        f"k={str((module_key or '').strip().lower())}|"
        f"y={str(bi_year or '')}|"
        f"s={str((bi_scope or '').strip().lower())}|"
        f"g={str((bi_segment or '').strip().lower())}|"
        f"m={_normalize_assistant_mode(assistant_mode)}"
    )


def _assistant_response_cache_get(key: str) -> Optional[Dict[str, Any]]:
    now = time.time()
    with _ASSISTANT_RESPONSE_CACHE_LOCK:
        row = _ASSISTANT_RESPONSE_CACHE.get(key)
        if not row:
            return None
        if float(row.get("expires_at") or 0) <= now:
            _ASSISTANT_RESPONSE_CACHE.pop(key, None)
            return None
        return dict(row)


def _assistant_response_cache_set(
    *,
    key: str,
    assistant_message: str,
    tool_trace: List[Dict[str, Any]],
) -> None:
    ttl = max(10, int(os.getenv("ASSISTANT_RESPONSE_CACHE_TTL_SEC", "300")))
    max_entries = max(
        50, int(os.getenv("ASSISTANT_RESPONSE_CACHE_MAX_ENTRIES", "2000"))
    )
    now = time.time()
    with _ASSISTANT_RESPONSE_CACHE_LOCK:
        _ASSISTANT_RESPONSE_CACHE[key] = {
            "assistant_message": assistant_message,
            "tool_trace": tool_trace,
            "cached_at": now,
            "expires_at": now + ttl,
        }
        if len(_ASSISTANT_RESPONSE_CACHE) > max_entries:
            stale = sorted(
                _ASSISTANT_RESPONSE_CACHE.items(),
                key=lambda kv: float(kv[1].get("cached_at") or 0),
            )[: max(1, len(_ASSISTANT_RESPONSE_CACHE) - max_entries)]
            for k, _ in stale:
                _ASSISTANT_RESPONSE_CACHE.pop(k, None)


def _tool_trace_has_write_intent(tool_trace: List[Dict[str, Any]]) -> bool:
    for step in tool_trace or []:
        if not isinstance(step, dict):
            continue
        tool_name = str(step.get("tool") or "").strip()
        if tool_name in WRITE_TOOLS:
            return True
    return False


def _default_rag_weights() -> Dict[str, float]:
    return {
        "doc_weight": float(os.getenv("ASSISTANT_RAG_DOC_WEIGHT", "1.0")),
        "sql_weight": float(os.getenv("ASSISTANT_RAG_SQL_WEIGHT", "1.15")),
        "recency_weight": float(os.getenv("ASSISTANT_RAG_RECENCY_WEIGHT", "0.8")),
    }


def _memory_weight() -> float:
    return _sanitize_weight(
        os.getenv("ASSISTANT_RAG_MEMORY_WEIGHT", "1.05"),
        fallback=1.05,
    )


def _sanitize_weight(value: Any, *, fallback: float) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return fallback
    if not math.isfinite(numeric):
        return fallback
    if numeric < 0:
        return 0.0
    if numeric > 5:
        return 5.0
    return round(numeric, 4)


def _load_rag_config_from_disk() -> None:
    with _RAG_CONFIG_LOCK:
        if _RAG_CONFIG_OVERRIDES:
            return
        if not _RAG_CONFIG_PATH.exists():
            return
        try:
            data = json.loads(_RAG_CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return
        if not isinstance(data, dict):
            return
        weights_raw = data.get("weights", data)
        if not isinstance(weights_raw, dict):
            return
        base = _default_rag_weights()
        for key in ("doc_weight", "sql_weight", "recency_weight"):
            if key in weights_raw:
                _RAG_CONFIG_OVERRIDES[key] = _sanitize_weight(
                    weights_raw.get(key),
                    fallback=base[key],
                )


def _save_rag_config_to_disk(weights: Dict[str, float]) -> None:
    _RAG_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": datetime.utcnow().isoformat(),
        "weights": {
            "doc_weight": float(weights["doc_weight"]),
            "sql_weight": float(weights["sql_weight"]),
            "recency_weight": float(weights["recency_weight"]),
        },
    }
    _RAG_CONFIG_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _rag_weights() -> Dict[str, float]:
    _load_rag_config_from_disk()
    base = _default_rag_weights()
    with _RAG_CONFIG_LOCK:
        return {
            "doc_weight": _sanitize_weight(
                _RAG_CONFIG_OVERRIDES.get("doc_weight", base["doc_weight"]),
                fallback=base["doc_weight"],
            ),
            "sql_weight": _sanitize_weight(
                _RAG_CONFIG_OVERRIDES.get("sql_weight", base["sql_weight"]),
                fallback=base["sql_weight"],
            ),
            "recency_weight": _sanitize_weight(
                _RAG_CONFIG_OVERRIDES.get("recency_weight", base["recency_weight"]),
                fallback=base["recency_weight"],
            ),
        }


def _set_rag_weights(partial: Dict[str, Any]) -> Dict[str, float]:
    current = _rag_weights()
    merged = {
        "doc_weight": _sanitize_weight(
            partial.get("doc_weight", current["doc_weight"]),
            fallback=current["doc_weight"],
        ),
        "sql_weight": _sanitize_weight(
            partial.get("sql_weight", current["sql_weight"]),
            fallback=current["sql_weight"],
        ),
        "recency_weight": _sanitize_weight(
            partial.get("recency_weight", current["recency_weight"]),
            fallback=current["recency_weight"],
        ),
    }
    with _RAG_CONFIG_LOCK:
        _RAG_CONFIG_OVERRIDES.update(merged)
    _save_rag_config_to_disk(merged)
    return merged


def _reset_rag_weights() -> Dict[str, float]:
    defaults = _default_rag_weights()
    with _RAG_CONFIG_LOCK:
        _RAG_CONFIG_OVERRIDES.update(defaults)
    _save_rag_config_to_disk(defaults)
    return _rag_weights()


def _rag_presets() -> Dict[str, Dict[str, float]]:
    return {
        "balanced": {"doc_weight": 1.0, "sql_weight": 1.15, "recency_weight": 0.8},
        "sql_heavy": {"doc_weight": 0.8, "sql_weight": 1.5, "recency_weight": 0.7},
        "doc_heavy": {"doc_weight": 1.35, "sql_weight": 0.9, "recency_weight": 0.8},
        "recency_heavy": {"doc_weight": 0.95, "sql_weight": 1.0, "recency_weight": 1.5},
    }


def _cache_key_for_query(
    query: str,
    *,
    empleado_id: Optional[uuid.UUID] = None,
    module_key: Optional[str] = None,
    tournament_key: Optional[str] = None,
) -> str:
    weights = _rag_weights()
    return (
        f"{_normalize_query_for_cache(query)}::"
        f"e={str(empleado_id or '')}|"
        f"m={str((module_key or '').strip().lower())}|"
        f"t={str((tournament_key or '').strip().lower())}|"
        f"d={weights['doc_weight']:.3f}|s={weights['sql_weight']:.3f}|r={weights['recency_weight']:.3f}"
    )


def _extract_tokens(query: str) -> List[str]:
    raw = re.split(r"[^a-zA-Z0-9_áéíóúñÁÉÍÓÚÑ]+", (query or "").lower())
    stop = {
        "de",
        "la",
        "el",
        "los",
        "las",
        "y",
        "o",
        "en",
        "por",
        "para",
        "con",
        "del",
        "al",
        "que",
        "cuanto",
        "cuantos",
        "cual",
        "quiero",
        "necesito",
        "favor",
    }
    return [t for t in raw if len(t) >= 3 and t not in stop][:12]


def _recency_boost(dt: Optional[datetime]) -> float:
    if not dt:
        return 0.0
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    days = max(0.0, (now - dt).total_seconds() / 86400.0)
    if days <= 7:
        return 0.08
    if days <= 30:
        return 0.05
    if days <= 90:
        return 0.02
    return 0.0


def _doc_recency_boost(source: Optional[str]) -> float:
    path = (source or "").strip()
    if not path:
        return 0.0
    try:
        ts = os.path.getmtime(path)
    except OSError:
        return 0.0
    return _recency_boost(datetime.utcfromtimestamp(ts))


def _record_rag_eval(payload: Dict[str, Any]) -> None:
    with _RAG_EVAL_HISTORY_LOCK:
        _RAG_EVAL_HISTORY.appendleft(payload)


def _record_rag_config_change(payload: Dict[str, Any]) -> None:
    with _RAG_CONFIG_HISTORY_LOCK:
        _RAG_CONFIG_HISTORY.appendleft(payload)


def _latest_rag_eval() -> Optional[Dict[str, Any]]:
    with _RAG_EVAL_HISTORY_LOCK:
        return dict(_RAG_EVAL_HISTORY[0]) if _RAG_EVAL_HISTORY else None


def _latest_rag_config_change() -> Optional[Dict[str, Any]]:
    with _RAG_CONFIG_HISTORY_LOCK:
        return dict(_RAG_CONFIG_HISTORY[0]) if _RAG_CONFIG_HISTORY else None


def get_assistant_rag_health_snapshot() -> Dict[str, Any]:
    with _RAG_METRICS_LOCK:
        metrics = dict(_RAG_METRICS)
    with _RETRIEVAL_CACHE_LOCK:
        cache_size = len(_RETRIEVAL_CACHE)
    return {
        "metrics": metrics,
        "cache_size": cache_size,
        "latest_eval": _latest_rag_eval(),
        "latest_config_change": _latest_rag_config_change(),
        "weights": _rag_weights(),
        "timestamp": datetime.utcnow().isoformat(),
    }


async def _rag_search_async(
    *,
    client: Any = None,
    query: str,
    top_k: int,
    min_score: float,
) -> List[Dict[str, Any]]:
    return await asyncio.to_thread(
        get_rag_store().search,
        client=client,
        query=query,
        top_k=top_k,
        min_score=min_score,
    )


async def _rag_ingest_async(
    *,
    paths: List[str],
    reset: bool,
    max_files: int,
) -> Dict[str, Any]:
    return await asyncio.to_thread(
        get_rag_store().ingest,
        paths=paths,
        reset=reset,
        max_files=max_files,
    )


def _ensure_citations(answer: str, sources: List[Dict[str, Any]]) -> str:
    text = (answer or "").strip()
    if not text:
        text = "No pude generar una respuesta con la informacion disponible."
    if "fuentes:" in text.lower():
        return text
    if not sources:
        return text + "\n\nFuentes: insuficiente evidencia verificable en RAG/SQL."
    lines = [f"- {s.get('label')} (score={s.get('score')})" for s in sources[:6]]
    return text + "\n\nFuentes:\n" + "\n".join(lines)


def _conversation_metadata_dict(conversation: AssistantConversation) -> Dict[str, Any]:
    raw = getattr(conversation, "metadata_", None)
    return dict(raw) if isinstance(raw, dict) else {}


def _conversation_module_key(conversation: AssistantConversation) -> Optional[str]:
    metadata = _conversation_metadata_dict(conversation)
    value = str(metadata.get("module_key") or "").strip().lower()
    return value or None


def _scope_from_module_key(module_key: Optional[str]) -> Optional[str]:
    value = str(module_key or "").strip().lower()
    if not value:
        return None
    if value.startswith("finance") or value.startswith("gastos"):
        return "finance"
    if (
        value.startswith("tournament")
        or value.startswith("tournaments")
        or value.startswith("torneos")
    ):
        return "tournament"
    if (
        value.startswith("code")
        or value.startswith("platform")
        or value.startswith("dev")
    ):
        return "code"
    return "generic"


def _normalize_external_session_id(value: Optional[str]) -> Optional[str]:
    cleaned = str(value or "").strip()
    return cleaned or None


def _conversation_external_session_id(
    conversation: AssistantConversation,
) -> Optional[str]:
    metadata = _conversation_metadata_dict(conversation)
    return _normalize_external_session_id(metadata.get("external_session_id"))


def _conversation_module_label(conversation: AssistantConversation) -> Optional[str]:
    metadata = _conversation_metadata_dict(conversation)
    value = str(metadata.get("module_label") or "").strip()
    return value or None


def _conversation_module_context_text(
    conversation: AssistantConversation,
) -> Optional[str]:
    metadata = _conversation_metadata_dict(conversation)
    module_context = metadata.get("module_context")
    if isinstance(module_context, dict):
        parts = []
        for key, value in module_context.items():
            key_clean = str(key or "").strip()
            value_clean = str(value or "").strip()
            if key_clean and value_clean:
                parts.append(f"{key_clean}={value_clean}")
        return "; ".join(parts) or None
    value = str(module_context or "").strip()
    return value or None


def _conversation_module_context_dict(
    conversation: AssistantConversation,
) -> Dict[str, Any]:
    metadata = _conversation_metadata_dict(conversation)
    module_context = metadata.get("module_context")
    return dict(module_context) if isinstance(module_context, dict) else {}


def _update_conversation_context(
    *,
    conversation: AssistantConversation,
    tournament_key: Optional[str] = None,
    module_key: Optional[str] = None,
    module_label: Optional[str] = None,
    module_context: Optional[Dict[str, Any]] = None,
) -> None:
    metadata = _conversation_metadata_dict(conversation)
    tournament_clean = str(tournament_key or "").strip().lower()
    if tournament_clean:
        conversation.tournament_key = tournament_clean
    module_key_clean = str(module_key or "").strip().lower()
    if module_key_clean:
        metadata["module_key"] = module_key_clean
    module_label_clean = str(module_label or "").strip()
    if module_label_clean:
        metadata["module_label"] = module_label_clean
    if isinstance(module_context, dict) and module_context:
        existing = metadata.get("module_context")
        merged = dict(existing) if isinstance(existing, dict) else {}
        for key, value in module_context.items():
            key_clean = str(key or "").strip()
            if not key_clean:
                continue
            merged[key_clean] = value
        if merged:
            metadata["module_context"] = merged
    conversation.metadata_ = metadata or None


async def _find_conversation_by_external_session_id(
    *,
    session: AsyncSession,
    empleado_id: uuid.UUID,
    external_session_id: Optional[str],
) -> Optional[AssistantConversation]:
    external_session_id_clean = _normalize_external_session_id(external_session_id)
    if not external_session_id_clean:
        return None
    return (
        (
            await session.execute(
                select(AssistantConversation)
                .where(
                    AssistantConversation.empleado_id == empleado_id,
                    AssistantConversation.archived.is_(False),
                    func.jsonb_extract_path_text(
                        AssistantConversation.metadata_,
                        "external_session_id",
                    )
                    == external_session_id_clean,
                )
                .order_by(desc(AssistantConversation.updated_at))
                .limit(1)
            )
        )
        .scalars()
        .first()
    )


def _memory_text_overlap_score(query_tokens: List[str], text: str) -> float:
    haystack = (text or "").strip().lower()
    if not haystack or not query_tokens:
        return 0.0
    hits = sum(1.0 for token in query_tokens if token in haystack)
    return round(hits / max(1, len(query_tokens)), 4)


async def _retrieve_sql_snippets(
    *,
    session: AsyncSession,
    query: str,
    top_k: int = 4,
) -> List[Dict[str, Any]]:
    tokens = _extract_tokens(query)
    if not tokens:
        return []

    expense_pred = []
    doc_pred = []
    for t in tokens:
        like = f"%{t}%"
        expense_pred.extend(
            [
                ExpenseReport.concepto.ilike(like),
                ExpenseReport.proyecto.ilike(like),
                ExpenseReport.numero_referencia.ilike(like),
                ExpenseReport.nombre_enviador.ilike(like),
            ]
        )
        doc_pred.extend(
            [
                Documento.concepto_pago.ilike(like),
                Documento.notas.ilike(like),
                Documento.numero_referencia.ilike(like),
                ProveedorCliente.nombre.ilike(like),
            ]
        )

    exp_rows = (
        await session.execute(
            select(
                ExpenseReport.id,
                ExpenseReport.numero_referencia,
                ExpenseReport.proyecto,
                ExpenseReport.concepto,
                ExpenseReport.gasto_cantidad,
                ExpenseReport.fecha,
                ExpenseReport.created_at,
            )
            .where(or_(*expense_pred))
            .order_by(
                ExpenseReport.fecha.desc().nullslast(), ExpenseReport.created_at.desc()
            )
            .limit(20)
        )
    ).all()

    doc_rows = (
        await session.execute(
            select(
                Documento.id,
                Documento.numero_referencia,
                Documento.tipo,
                Documento.estado,
                Documento.monto_total,
                Documento.fecha_pago,
                Documento.creado_en,
                ProveedorCliente.nombre.label("proveedor_nombre"),
            )
            .select_from(Documento)
            .outerjoin(
                ProveedorCliente, Documento.proveedor_cliente_id == ProveedorCliente.id
            )
            .where(or_(*doc_pred))
            .order_by(Documento.creado_en.desc())
            .limit(20)
        )
    ).all()

    scored: List[Dict[str, Any]] = []
    token_set = set(tokens)
    for r in exp_rows:
        text_blob = " ".join(
            [
                str(r.numero_referencia or ""),
                str(r.proyecto or ""),
                str(r.concepto or ""),
            ]
        ).lower()
        overlap = len([t for t in token_set if t in text_blob])
        base_score = overlap / max(1, len(token_set))
        recency_score = _recency_boost(r.fecha or r.created_at)
        score = round(base_score + recency_score, 4)
        scored.append(
            {
                "type": "sql",
                "score": score,
                "base_score": round(base_score, 4),
                "recency_score": round(recency_score, 4),
                "label": f"sql:expense_reports:{r.id}",
                "text": (
                    f"expense_reports id={r.id} ref={r.numero_referencia} "
                    f"fecha={r.fecha.isoformat() if r.fecha else None} "
                    f"proyecto={r.proyecto} concepto={r.concepto} monto={float(r.gasto_cantidad or 0):.2f}"
                ),
            }
        )

    for r in doc_rows:
        text_blob = " ".join(
            [
                str(r.numero_referencia or ""),
                str(r.tipo or ""),
                str(r.estado or ""),
                str(r.proveedor_nombre or ""),
            ]
        ).lower()
        overlap = len([t for t in token_set if t in text_blob])
        base_score = overlap / max(1, len(token_set))
        recency_score = _recency_boost(r.creado_en)
        score = round(base_score + recency_score, 4)
        scored.append(
            {
                "type": "sql",
                "score": score,
                "base_score": round(base_score, 4),
                "recency_score": round(recency_score, 4),
                "label": f"sql:documentos:{r.id}",
                "text": (
                    f"documentos id={r.id} ref={r.numero_referencia} tipo={r.tipo} estado={r.estado} "
                    f"proveedor={r.proveedor_nombre} monto_total={float(r.monto_total or 0):.2f} "
                    f"fecha_pago={r.fecha_pago.isoformat() if r.fecha_pago else None}"
                ),
            }
        )

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[: max(1, min(top_k, 10))]


async def _retrieve_memory_snippets(
    *,
    session: AsyncSession,
    empleado_id: Optional[uuid.UUID],
    conversation_id: Optional[uuid.UUID],
    module_key: Optional[str],
    tournament_key: Optional[str],
    query: str,
    top_k: int = 4,
) -> List[Dict[str, Any]]:
    if not empleado_id:
        return []
    tokens = _extract_tokens(query)
    if not tokens:
        return []

    predicates = []
    for token in tokens:
        like = f"%{token}%"
        predicates.extend(
            [
                AssistantMessage.content.ilike(like),
                AssistantConversation.title.ilike(like),
            ]
        )

    stmt = (
        select(AssistantMessage, AssistantConversation)
        .join(
            AssistantConversation,
            AssistantConversation.id == AssistantMessage.conversation_id,
        )
        .where(
            AssistantConversation.empleado_id == empleado_id,
            AssistantConversation.archived.is_(False),
            AssistantMessage.role.in_(("user", "assistant")),
            or_(*predicates),
        )
        .order_by(AssistantMessage.created_at.desc())
        .limit(max(20, min(top_k * 20, 120)))
    )
    if conversation_id:
        stmt = stmt.where(AssistantConversation.id != conversation_id)

    rows = (await session.execute(stmt)).all()
    module_key_norm = str(module_key or "").strip().lower()
    scope_norm = _scope_from_module_key(module_key_norm)
    tournament_key_norm = str(tournament_key or "").strip().lower()
    scored: List[Dict[str, Any]] = []

    for msg, conv in rows:
        metadata = _conversation_metadata_dict(conv)
        row_module_key = str(metadata.get("module_key") or "").strip().lower()
        row_scope = _scope_from_module_key(row_module_key)
        row_tournament_key = (
            str(getattr(conv, "tournament_key", "") or "").strip().lower()
        )
        if scope_norm and scope_norm != "generic":
            if row_scope != scope_norm:
                continue
        content = str(getattr(msg, "content", "") or "").strip()
        if not content:
            continue
        title = str(getattr(conv, "title", "") or "").strip()
        text_blob = " ".join([title, content])
        base_score = _memory_text_overlap_score(tokens, text_blob)
        if base_score <= 0:
            continue
        recency_score = _recency_boost(getattr(msg, "created_at", None))
        module_boost = (
            0.08 if module_key_norm and row_module_key == module_key_norm else 0.0
        )
        tournament_boost = (
            0.06
            if tournament_key_norm and row_tournament_key == tournament_key_norm
            else 0.0
        )
        weighted_score = round(
            (base_score * _memory_weight())
            + (recency_score * _rag_weights()["recency_weight"])
            + module_boost
            + tournament_boost,
            4,
        )
        scored.append(
            {
                "type": "memory",
                "score": weighted_score,
                "base_score": round(base_score, 4),
                "recency_score": round(recency_score, 4),
                "label": (f"memory:conversation:{conv.id}:message:{msg.id}"),
                "text": (
                    f"Memoria previa [{msg.role}] titulo={title or '(sin titulo)'} "
                    f"modulo={row_module_key or 'n/a'} torneo={row_tournament_key or 'n/a'} :: {content}"
                ),
                "conversation_id": str(conv.id),
                "module_key": row_module_key or None,
                "tournament_key": row_tournament_key or None,
            }
        )

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[: max(1, min(top_k, 10))]


def _source_scope(source: Optional[str]) -> str:
    value = str(source or "").strip().lower()
    if not value:
        return "generic"

    finance_markers = (
        "/reports/accounting_knowledge/",
        "/reports/finance",
        "/reports/finanzas",
        "/reports/gastos",
        "/docs/finanzas",
        "/docs/contabilidad",
        "/database/accounting",
    )
    tournament_markers = (
        "/reports/tournaments_ai/",
        "/reports/tournament",
        "/reports/torneos",
        "/docs/torneos",
        "/docs/tournaments",
        "/src/devnous/tournaments/",
        "/src/samchat/tournaments_v2/",
    )
    code_markers = (
        "/src/",
        "/tests/",
        "/docs/architecture/",
        "/architecture/",
        "/api-documentation/",
    )

    if any(marker in value for marker in finance_markers):
        return "finance"
    if any(marker in value for marker in tournament_markers):
        return "tournament"
    if any(marker in value for marker in code_markers):
        return "code"
    return "generic"


def _source_matches_scope(*, source: Optional[str], scope: Optional[str]) -> bool:
    normalized_scope = str(scope or "").strip().lower()
    if not normalized_scope or normalized_scope == "generic":
        return True
    source_scope = _source_scope(source)
    if source_scope == "generic":
        return True
    return source_scope == normalized_scope


async def _build_hybrid_retrieval(
    *,
    session: AsyncSession,
    query: str,
    empleado_id: Optional[uuid.UUID] = None,
    conversation_id: Optional[uuid.UUID] = None,
    module_key: Optional[str] = None,
    domain: Optional[str] = None,
    tournament_key: Optional[str] = None,
    client: Any = None,
) -> Dict[str, Any]:
    _bump_metric("retrieval_requests", 1)
    cache_ttl = int(os.getenv("ASSISTANT_RAG_CACHE_TTL_SEC", "300"))
    key = _cache_key_for_query(
        query,
        empleado_id=empleado_id,
        module_key=module_key,
        tournament_key=tournament_key,
    )
    now = time.time()
    with _RETRIEVAL_CACHE_LOCK:
        cached = _RETRIEVAL_CACHE.get(key)
        if cached and float(cached.get("expires_at", 0)) > now:
            _bump_metric("cache_hits", 1)
            return {
                "context": cached.get("context", ""),
                "sources": cached.get("sources", []),
                "trace": cached.get("trace", {}),
                "cache_hit": True,
            }
    _bump_metric("cache_misses", 1)

    rag_top_k = int(os.getenv("ASSISTANT_RAG_TOP_K", "5"))
    rag_min_score = float(os.getenv("ASSISTANT_RAG_MIN_SCORE", "0.15"))
    weights = _rag_weights()
    rag_results = await _rag_search_async(
        client=client,
        query=query,
        top_k=rag_top_k,
        min_score=rag_min_score,
    )
    retrieval_scope = (
        _scope_from_module_key(module_key) or str(domain or "").strip().lower()
    )
    rag_results = [
        row
        for row in rag_results
        if _source_matches_scope(
            source=row.get("source"),
            scope=retrieval_scope,
        )
    ]
    if rag_results:
        _bump_metric("doc_hits", len(rag_results))

    sql_results = await _retrieve_sql_snippets(session=session, query=query, top_k=4)
    if sql_results:
        _bump_metric("sql_hits", len(sql_results))

    memory_results = await _retrieve_memory_snippets(
        session=session,
        empleado_id=empleado_id,
        conversation_id=conversation_id,
        module_key=module_key,
        tournament_key=tournament_key,
        query=query,
        top_k=4,
    )

    combined: List[Dict[str, Any]] = []
    for r in rag_results:
        base_score = float(r.get("score") or 0)
        recency_score = _doc_recency_boost(r.get("source"))
        weighted_score = round(
            (base_score * weights["doc_weight"])
            + (recency_score * weights["recency_weight"]),
            4,
        )
        combined.append(
            {
                "type": "doc",
                "score": weighted_score,
                "base_score": round(base_score, 4),
                "recency_score": round(recency_score, 4),
                "label": f"doc:{r.get('source')}#{r.get('chunk_id')}",
                "text": (r.get("text") or "").strip(),
                "source": r.get("source"),
            }
        )
    for r in sql_results:
        base_score = float(r.get("base_score", r.get("score", 0)) or 0)
        recency_score = float(r.get("recency_score", 0) or 0)
        weighted_score = round(
            (base_score * weights["sql_weight"])
            + (recency_score * weights["recency_weight"]),
            4,
        )
        row = dict(r)
        row["score"] = weighted_score
        row["base_score"] = round(base_score, 4)
        row["recency_score"] = round(recency_score, 4)
        combined.append(row)
    combined.extend(memory_results)
    combined.sort(key=lambda x: float(x.get("score", 0)), reverse=True)

    max_ctx_items = int(os.getenv("ASSISTANT_RAG_CONTEXT_ITEMS", "6"))
    max_ctx_chars = int(os.getenv("ASSISTANT_RAG_CONTEXT_CHARS", "4000"))
    selected = combined[: max(1, min(max_ctx_items, 12))]

    lines = ["Contexto recuperado (hibrido RAG+SQL+MEMORY):"]
    used_sources: List[Dict[str, Any]] = []
    acc_chars = 0
    for i, r in enumerate(selected, start=1):
        txt = (r.get("text") or "").strip()
        if len(txt) > 900:
            txt = txt[:900] + "..."
        block = f"[{i}] {r.get('label')} score={r.get('score')}\n{txt}"
        if acc_chars + len(block) > max_ctx_chars:
            break
        acc_chars += len(block)
        lines.append(block)
        used_sources.append({"label": r.get("label"), "score": r.get("score")})
    context = "\n\n".join(lines) if used_sources else ""
    trace = {
        "weights": weights,
        "doc_results": [
            {
                "source": r.get("source"),
                "score": r.get("score"),
                "chunk_id": r.get("chunk_id"),
            }
            for r in rag_results
        ],
        "sql_results": sql_results,
        "memory_results": [
            {
                "conversation_id": r.get("conversation_id"),
                "module_key": r.get("module_key"),
                "tournament_key": r.get("tournament_key"),
                "score": r.get("score"),
                "label": r.get("label"),
            }
            for r in memory_results
        ],
    }
    with _RETRIEVAL_CACHE_LOCK:
        _RETRIEVAL_CACHE[key] = {
            "expires_at": now + cache_ttl,
            "context": context,
            "sources": used_sources,
            "trace": trace,
        }
    return {
        "context": context,
        "sources": used_sources,
        "trace": trace,
        "cache_hit": False,
    }


def _get_openai_client(api_key_override: Optional[str] = None) -> Any:
    return _provider_get_openai_client(api_key_override)


def _get_anthropic_client(api_key_override: Optional[str] = None) -> Any:
    return _provider_get_anthropic_client(api_key_override)


def _env_int(
    name: str,
    default: int,
    *,
    minimum: Optional[int] = None,
    maximum: Optional[int] = None,
) -> int:
    return _provider_env_int(
        name, default, minimum=minimum, maximum=maximum
    )


def _env_float(
    name: str,
    default: float,
    *,
    minimum: Optional[float] = None,
    maximum: Optional[float] = None,
) -> float:
    return _provider_env_float(
        name, default, minimum=minimum, maximum=maximum
    )


def _env_bool(name: str, default: bool) -> bool:
    return _provider_env_bool(name, default)


def _normalize_assistant_mode(value: Optional[str]) -> str:
    return _provider_normalize_assistant_mode(value)


def _assistant_inference_tier(
    route_info: Optional[Dict[str, Any]],
    mode: Optional[str] = None,
) -> str:
    return _provider_assistant_inference_tier(route_info, mode)


def _csv_items(raw: Optional[str]) -> List[str]:
    return _provider_csv_items(raw)


def _matches_policy_target(value: Optional[str], patterns: List[str]) -> bool:
    return _provider_matches_policy_target(value, patterns)


def _assistant_contextual_pref(
    route_info: Optional[Dict[str, Any]],
) -> Optional[str]:
    return _provider_assistant_contextual_pref(route_info)


def _assistant_remote_allowed(
    route_info: Optional[Dict[str, Any]],
    *,
    capability: str,
) -> bool:
    return _provider_assistant_remote_allowed(route_info, capability=capability)


def _assistant_provider_order_from_pref(pref: str, *, capability: str) -> List[str]:
    return _provider_assistant_provider_order_from_pref(
        pref, capability=capability
    )


def _assistant_provider_order(
    mode: Optional[str] = None,
    *,
    route_info: Optional[Dict[str, Any]] = None,
    capability: str = "chat",
) -> List[str]:
    return _provider_assistant_provider_order(
        mode, route_info=route_info, capability=capability
    )


def _assistant_model(
    provider: str,
    mode: Optional[str] = None,
    route_info: Optional[Dict[str, Any]] = None,
) -> str:
    return _provider_assistant_model(provider, mode, route_info)


def _assistant_inference_plan(
    route_info: Optional[Dict[str, Any]],
    *,
    mode: Optional[str],
) -> Dict[str, Any]:
    normalized_mode = _normalize_assistant_mode(mode)
    tier = _assistant_inference_tier(route_info, normalized_mode)
    policy_pref = _assistant_contextual_pref(route_info)
    return {
        "mode": normalized_mode,
        "tier": tier,
        "provider_order": _assistant_provider_order(
            normalized_mode,
            route_info=route_info,
            capability="chat",
        ),
        "policy_pref": policy_pref,
        "remote_allowed": _assistant_remote_allowed(route_info, capability="chat"),
        "planned_local_model": _assistant_model(
            "ollama", normalized_mode, route_info=route_info
        ),
        "route": str((route_info or {}).get("route") or "").strip().lower()
        or "lookup_sql",
        "domain": str((route_info or {}).get("domain") or "").strip().lower()
        or "generic",
        "module_key": str((route_info or {}).get("module_key") or "").strip().lower()
        or None,
    }


def _ollama_base_url() -> str:
    return (os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434") or "").rstrip("/")


def _assistant_ollama_keep_alive(mode: Optional[str]) -> Any:
    normalized_mode = _normalize_assistant_mode(mode)
    if normalized_mode == "calidad":
        return os.getenv(
            "OLLAMA_KEEP_ALIVE_HIGH", os.getenv("OLLAMA_KEEP_ALIVE", "20m")
        )
    if normalized_mode == "balanceado":
        return os.getenv(
            "OLLAMA_KEEP_ALIVE_BALANCED", os.getenv("OLLAMA_KEEP_ALIVE", "15m")
        )
    return os.getenv("OLLAMA_KEEP_ALIVE_LOW", os.getenv("OLLAMA_KEEP_ALIVE", "10m"))


def _assistant_ollama_options(
    *,
    mode: Optional[str],
    route_info: Optional[Dict[str, Any]],
    max_tokens: int,
) -> Dict[str, Any]:
    normalized_mode = _normalize_assistant_mode(mode)
    route = str((route_info or {}).get("route") or "").strip().lower()
    if normalized_mode == "calidad":
        num_ctx = _env_int(
            "OLLAMA_ASSISTANT_NUM_CTX_HIGH", 8192, minimum=1024, maximum=131072
        )
        temperature = _env_float(
            "OLLAMA_ASSISTANT_TEMPERATURE_HIGH", 0.2, minimum=0.0, maximum=2.0
        )
        top_p = _env_float("OLLAMA_ASSISTANT_TOP_P_HIGH", 0.9, minimum=0.0, maximum=1.0)
    elif normalized_mode == "balanceado":
        num_ctx = _env_int(
            "OLLAMA_ASSISTANT_NUM_CTX_BALANCED", 4096, minimum=1024, maximum=131072
        )
        temperature = _env_float(
            "OLLAMA_ASSISTANT_TEMPERATURE_BALANCED", 0.15, minimum=0.0, maximum=2.0
        )
        top_p = _env_float(
            "OLLAMA_ASSISTANT_TOP_P_BALANCED", 0.9, minimum=0.0, maximum=1.0
        )
    else:
        num_ctx = _env_int(
            "OLLAMA_ASSISTANT_NUM_CTX_LOW", 4096, minimum=1024, maximum=131072
        )
        temperature = _env_float(
            "OLLAMA_ASSISTANT_TEMPERATURE_LOW", 0.1, minimum=0.0, maximum=2.0
        )
        top_p = _env_float("OLLAMA_ASSISTANT_TOP_P_LOW", 0.85, minimum=0.0, maximum=1.0)

    if route in {"reporting", "aggregation_sql"}:
        num_ctx = max(
            num_ctx,
            _env_int(
                "OLLAMA_ASSISTANT_NUM_CTX_REPORTING",
                num_ctx,
                minimum=1024,
                maximum=131072,
            ),
        )

    return {
        "num_ctx": num_ctx,
        "num_predict": max(64, max_tokens),
        "temperature": temperature,
        "top_p": top_p,
        "top_k": _env_int("OLLAMA_ASSISTANT_TOP_K", 20, minimum=1, maximum=200),
    }


def _assistant_ollama_think(
    *, mode: Optional[str], route_info: Optional[Dict[str, Any]]
) -> Any:
    if _env_bool("OLLAMA_ASSISTANT_THINK_ENABLED", False):
        level = (
            (os.getenv("OLLAMA_ASSISTANT_THINK_LEVEL", "medium") or "").strip().lower()
        )
        if level in {"low", "medium", "high"}:
            return level
        return True
    return False


def _sanitize_ollama_content(value: Any) -> str:
    text = str(value or "").replace("\r\n", "\n").strip()
    if not text:
        return ""
    if "</think>" in text:
        text = text.split("</think>")[-1].strip()
    text = re.sub(r"(?is)<think>.*?</think>", "", text).strip()
    text = re.sub(
        r"^(?:[^\W\d_]{2,20}\s*){1,3}:\s*",
        "",
        text,
        count=1,
        flags=re.UNICODE,
    ).strip()
    return text


def _ollama_message_content(payload: Dict[str, Any]) -> str:
    message = payload.get("message") or {}
    return _sanitize_ollama_content(
        message.get("content") or payload.get("response") or ""
    )


def _ollama_tool_calls(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    message = payload.get("message") or {}
    raw_calls = message.get("tool_calls") or payload.get("tool_calls") or []
    normalized: List[Dict[str, Any]] = []
    for call in raw_calls:
        if not isinstance(call, dict):
            continue
        fn = call.get("function") or {}
        name = str(fn.get("name") or call.get("name") or "").strip()
        arguments = fn.get("arguments")
        if arguments is None:
            arguments = call.get("arguments") or {}
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = {}
        if not isinstance(arguments, dict):
            arguments = {}
        if not name:
            continue
        normalized.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": arguments,
                },
            }
        )
    return normalized


def _ollama_assistant_message(payload: Dict[str, Any]) -> Dict[str, Any]:
    message = payload.get("message") or {}
    content = _sanitize_ollama_content(message.get("content") or "")
    tool_calls = _ollama_tool_calls(payload)
    result: Dict[str, Any] = {
        "role": "assistant",
        "content": content,
    }
    if tool_calls:
        result["tool_calls"] = tool_calls
    thinking = message.get("thinking")
    if thinking:
        result["thinking"] = thinking
    return result


async def _ollama_chat(
    *,
    model: str,
    messages: List[Dict[str, Any]],
    tool_defs: Optional[List[Dict[str, Any]]],
    mode: Optional[str],
    route_info: Optional[Dict[str, Any]],
    max_tokens: int,
) -> Dict[str, Any]:
    timeout = _env_float("OLLAMA_HTTP_TIMEOUT_SEC", 90.0, minimum=5.0, maximum=600.0)
    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": False,
        "keep_alive": _assistant_ollama_keep_alive(mode),
        "think": _assistant_ollama_think(mode=mode, route_info=route_info),
        "options": _assistant_ollama_options(
            mode=mode,
            route_info=route_info,
            max_tokens=max_tokens,
        ),
    }
    if tool_defs:
        payload["tools"] = tool_defs
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(f"{_ollama_base_url()}/api/chat", json=payload)
        response.raise_for_status()
        data = response.json()
    if not isinstance(data, dict):
        raise HTTPException(status_code=502, detail="Invalid Ollama response")
    return data


async def _assistant_text_response(
    *,
    prompt_user: str,
    history_messages: List[Dict[str, Any]],
    mode: Optional[str],
    route_info: Optional[Dict[str, Any]],
    openai_api_key: Optional[str],
    max_tokens: int,
    system_prompts: Optional[List[str]] = None,
) -> Dict[str, Any]:
    normalized_mode = _normalize_assistant_mode(mode)
    provider_errors: List[str] = []
    system_messages = [
        {"role": "system", "content": item}
        for item in (system_prompts or [])
        if (item or "").strip()
    ]

    for provider in _assistant_provider_order(
        normalized_mode,
        route_info=route_info,
        capability="chat",
    ):
        model = _assistant_model(provider, normalized_mode, route_info=route_info)
        try:
            if provider == "ollama":
                messages = [
                    *system_messages,
                    *history_messages,
                    {"role": "user", "content": prompt_user},
                ]
                payload = await _ollama_chat(
                    model=model,
                    messages=messages,
                    tool_defs=None,
                    mode=normalized_mode,
                    route_info=route_info,
                    max_tokens=max_tokens,
                )
                answer = _ollama_message_content(payload)
                if not answer:
                    raise HTTPException(
                        status_code=502, detail="Ollama returned empty response"
                    )
                return {
                    "provider": provider,
                    "model": model,
                    "answer": answer,
                    "meta": {
                        "done_reason": payload.get("done_reason"),
                        "load_duration": payload.get("load_duration"),
                        "eval_count": payload.get("eval_count"),
                    },
                }

            if provider == "anthropic":
                client = _get_anthropic_client()
                system_text = "\n\n".join(
                    item["content"]
                    for item in system_messages
                    if (item.get("content") or "").strip()
                )
                anthropic_messages: List[Dict[str, Any]] = []
                for m in history_messages:
                    role = "assistant" if m.get("role") == "assistant" else "user"
                    anthropic_messages.append(
                        {"role": role, "content": m.get("content") or ""}
                    )
                anthropic_messages.append({"role": "user", "content": prompt_user})
                resp = await asyncio.to_thread(
                    client.messages.create,
                    model=model,
                    system=system_text,
                    messages=anthropic_messages,
                    max_tokens=max_tokens,
                    temperature=0.2,
                )
                answer = _anthropic_text_from_blocks(getattr(resp, "content", []) or [])
                if not answer:
                    raise HTTPException(
                        status_code=502, detail="Anthropic returned empty response"
                    )
                return {
                    "provider": provider,
                    "model": model,
                    "answer": answer,
                    "meta": {},
                }

            client = _get_openai_client(openai_api_key)
            messages = [
                *system_messages,
                *history_messages,
                {"role": "user", "content": prompt_user},
            ]
            resp = await asyncio.to_thread(
                client.chat.completions.create,
                model=model,
                messages=messages,
                temperature=0.2,
                max_tokens=max_tokens,
            )
            answer = (resp.choices[0].message.content or "").strip()
            if not answer:
                raise HTTPException(
                    status_code=502, detail="OpenAI returned empty response"
                )
            return {"provider": provider, "model": model, "answer": answer, "meta": {}}
        except Exception as exc:
            provider_errors.append(f"{provider}: {exc}")
            continue

    fallback = "No pude generar una respuesta del asistente."
    if provider_errors:
        fallback = f"{fallback} (fallback) {provider_errors[-1]}"
    return {
        "provider": "fallback",
        "model": "fallback",
        "answer": fallback,
        "meta": {"errors": provider_errors},
    }


def _assistant_hermes_profile_prompt(
    route_info: Optional[Dict[str, Any]],
) -> Optional[str]:
    profile = str((route_info or {}).get("hermes_profile") or "").strip().lower()
    if profile != "finance_strategy":
        return None
    return (
        "Delegacion interna activa: responde como Hermes profile `finance_strategy` para Plataforma Sports.\n"
        "Tu alcance es estrategia contable, fiscal y financiera.\n"
        "Objetivo:\n"
        "- sintetizar evidencia financiera disponible\n"
        "- proponer estrategia y criterios de decision\n"
        "- identificar riesgos, dependencias y tradeoffs\n"
        "- separar con claridad hechos observados vs supuestos\n"
        "Reglas:\n"
        "- NO ejecutes escrituras ni propongas writes automáticos.\n"
        "- Basate primero en evidencia recuperada y herramientas read-only.\n"
        "- Como primer paso, usa `finance_strategy_snapshot` salvo que el usuario pida un dato puntual o una alarma muy especifica.\n"
        "- Para riesgos o anomalias puntuales, complementa con `finance_alerts_scan`.\n"
        "- Si falta evidencia, dilo explicitamente y plantea supuestos mínimos.\n"
        "- Prioriza recomendaciones accionables para dirección, contabilidad, fiscal y tesorería.\n"
        "- Cuando aplique, estructura en: diagnostico, implicaciones, recomendacion, riesgos y siguientes pasos.\n"
        "- Responde en espanol de Mexico, directo y ejecutivo."
    )


def _assistant_route_mode(route: str, requested_mode: Optional[str] = None) -> str:
    if requested_mode:
        return _normalize_assistant_mode(requested_mode)
    env_key = {
        "lookup_sql": "ASSISTANT_ROUTE_MODE_LOOKUP",
        "aggregation_sql": "ASSISTANT_ROUTE_MODE_AGGREGATION",
        "reporting": "ASSISTANT_ROUTE_MODE_REPORTING",
        "agentic_write": "ASSISTANT_ROUTE_MODE_ACTION",
        "code_agentic": "ASSISTANT_ROUTE_MODE_CODE",
        "needs_clarification": "ASSISTANT_ROUTE_MODE_CLARIFICATION",
    }.get(route or "", "")
    if env_key:
        override = (os.getenv(env_key, "") or "").strip()
        if override:
            return _normalize_assistant_mode(override)
    defaults = {
        "lookup_sql": "ahorro",
        "aggregation_sql": "balanceado",
        "reporting": "calidad",
        "agentic_write": "calidad",
        "code_agentic": "calidad",
        "needs_clarification": "ahorro",
    }
    return _normalize_assistant_mode(defaults.get(route or "", "ahorro"))


def _assistant_max_tokens(*, mode: Optional[str], route: Optional[str]) -> int:
    normalized_mode = _normalize_assistant_mode(mode)
    route = (route or "").strip().lower()
    if route in {"code_agentic", "reporting", "agentic_write"}:
        return 1800 if normalized_mode == "calidad" else 1400
    if route == "aggregation_sql":
        return 1000 if normalized_mode == "ahorro" else 1200
    if route == "needs_clarification":
        return 500
    return 700 if normalized_mode == "ahorro" else 900


def _keyword_hits(text: str, keywords: Tuple[str, ...]) -> List[str]:
    hits: List[str] = []
    for keyword in keywords:
        normalized = re.sub(r"\s+", " ", (keyword or "").strip().lower())
        if not normalized:
            continue
        pattern = r"(?<!\w)" + re.escape(normalized).replace(r"\ ", r"\s+") + r"(?!\w)"
        if re.search(pattern, text, flags=re.UNICODE):
            hits.append(keyword)
    return hits


def _assistant_classify_request(raw_message: str) -> Dict[str, Any]:
    text = re.sub(r"\s+", " ", (raw_message or "").strip().lower())
    reasons: List[str] = []

    finance_keywords = (
        "gasto",
        "gastos",
        "finanzas",
        "financiero",
        "financiera",
        "fiscal",
        "impuesto",
        "impuestos",
        "contabilidad",
        "contable",
        "cuenta contable",
        "cuentas contables",
        "cfdi",
        "factura",
        "facturar",
        "amex",
        "proveedor",
        "proveedores",
        "solicitud de pago",
        "solicitud",
        "pago",
        "pagos",
        "presupuesto",
        "viatico",
        "viáticos",
        "utilera",
        "equipamiento",
        "balones",
        "uniformes",
        "tocino",
        "terceros",
        "reembolso",
        "reembolsos",
        "libro diario",
        "mayor",
        "balanza",
        "poliza",
        "póliza",
        "conciliacion",
        "conciliación",
        "tesoreria",
        "tesorería",
        "flujo de efectivo",
        "capital de trabajo",
        "estado contable",
        "cierre contable",
    )
    tournament_keywords = (
        "torneo",
        "torneos",
        "equipo",
        "equipos",
        "jugador",
        "jugadores",
        "registro",
        "registros",
        "inscrito",
        "inscritos",
        "inscripcion",
        "inscripción",
        "categoria",
        "categoría",
        "rama",
        "calendario",
        "partido",
        "partidos",
        "jornada",
        "sede",
        "sedes",
        "municipio",
        "estado",
        "fase nacional",
        "fase estatal",
        "copa telmex",
        "club america",
        "club américa",
        "beisbol",
        "béisbol",
    )
    code_keywords = (
        "codigo",
        "código",
        "repo",
        "repositorio",
        "frontend",
        "backend",
        "endpoint",
        "api",
        "ruta",
        "navbar",
        "ui",
        "ux",
        "build",
        "deploy",
        "compila",
        "error 404",
        "error 500",
        "traceback",
        "boton",
        "botón",
        "componente",
        "archivo",
        "patch",
        "fix",
        "bug",
    )
    report_keywords = (
        "reporte",
        "reporta",
        "reportar",
        "comparativo",
        "compara",
        "comparar",
        "proyeccion",
        "proyección",
        "tendencia",
        "como vamos",
        "cómo vamos",
        "hallazgo",
        "hallazgos",
        "riesgo",
        "riesgos",
        "contrato",
        "contratos",
        "resumen ejecutivo",
        "ejecutivo",
        "dashboard",
        "proyecta",
        "analiza",
        "analisis",
        "análisis",
        "consolidado",
        "consolida",
        "vs ",
        "libro diario",
        "diario contable",
        "mayor contable",
        "balanza",
        "estado contable",
        "cierre contable",
        "conciliacion",
        "conciliación",
    )
    finance_strategy_keywords = (
        "estrategia contable",
        "estrategia fiscal",
        "estrategia financiera",
        "planeacion fiscal",
        "planeación fiscal",
        "planeacion financiera",
        "planeación financiera",
        "politica contable",
        "política contable",
        "politica fiscal",
        "política fiscal",
        "flujo de efectivo",
        "cash flow",
        "tesoreria",
        "tesorería",
        "capital de trabajo",
        "estructura financiera",
        "estructura fiscal",
        "estructura contable",
        "riesgo fiscal",
        "riesgos fiscales",
        "riesgo financiero",
        "riesgos financieros",
        "optimizacion fiscal",
        "optimización fiscal",
        "optimizar impuestos",
        "cierre fiscal",
        "decision financiera",
        "decisión financiera",
        "escenario financiero",
        "escenarios financieros",
        "presupuesto anual",
        "planeacion de tesoreria",
        "planeación de tesorería",
    )
    aggregation_keywords = (
        "desglose",
        "top ",
        "ranking",
        "por municipio",
        "por estado",
        "por categoria",
        "por categoría",
        "por proyecto",
        "por proveedor",
        "mensual",
        "anual",
        "acumulado",
        "total por",
        "distribucion",
        "distribución",
        "porcentaje",
    )
    write_keywords = (
        "crea",
        "crear",
        "agrega",
        "agregar",
        "registra",
        "registrar",
        "inscribe",
        "inscribir",
        "alta",
        "dar de alta",
        "modifica",
        "modificar",
        "actualiza",
        "actualizar",
        "edita",
        "editar",
        "elimina",
        "eliminar",
        "borra",
        "borrar",
        "solicita",
        "solicitar",
        "genera el calendario",
        "genera calendario",
        "rehace el calendario",
        "regenera el calendario",
        "importa",
        "importar",
        "captura",
        "capturar",
        "sube",
        "genera póliza",
        "genera la póliza",
        "crear póliza",
        "crear poliza",
        "asiento contable",
        "genera asiento",
        "contabiliza",
        "contabilizar",
        "clasifica",
        "clasificar",
        "asigna cuenta",
        "asignar cuenta",
    )
    code_change_keywords = (
        "fix",
        "bug",
        "patch",
        "arregla",
        "corrige",
        "corrijo",
        "soluciona",
        "implementa",
        "editar",
        "edita",
        "modifica",
        "modificar",
        "cambia",
        "cambiar",
        "escribe",
        "reescribe",
        "refactor",
        "refactoriza",
    )

    finance_hits = _keyword_hits(text, finance_keywords)
    tournament_hits = _keyword_hits(text, tournament_keywords)
    code_hits = _keyword_hits(text, code_keywords)
    report_hits = _keyword_hits(text, report_keywords)
    finance_strategy_hits = _keyword_hits(text, finance_strategy_keywords)
    aggregation_hits = _keyword_hits(text, aggregation_keywords)
    write_hits = _keyword_hits(text, write_keywords)
    code_change_hits = _keyword_hits(text, code_change_keywords)

    if finance_hits:
        reasons.append(f"finance={finance_hits[:3]}")
    if tournament_hits:
        reasons.append(f"tournament={tournament_hits[:3]}")
    if code_hits:
        reasons.append(f"code={code_hits[:3]}")
    if report_hits:
        reasons.append(f"report={report_hits[:3]}")
    if finance_strategy_hits:
        reasons.append(f"finance_strategy={finance_strategy_hits[:3]}")
    if aggregation_hits:
        reasons.append(f"aggregation={aggregation_hits[:3]}")
    if write_hits:
        reasons.append(f"write={write_hits[:3]}")
    if code_change_hits:
        reasons.append(f"code_change={code_change_hits[:3]}")

    domain = "generic"
    if code_hits:
        domain = "code"
    elif finance_hits and tournament_hits:
        domain = "mixed"
    elif finance_hits or finance_strategy_hits:
        domain = "finance"
    elif tournament_hits:
        domain = "tournament"

    route = "lookup_sql"
    if code_hits:
        route = "code_agentic"
    elif write_hits and domain in {"finance", "tournament", "mixed"}:
        route = "agentic_write"
    elif finance_strategy_hits:
        route = "reporting"
    elif report_hits or domain == "mixed":
        route = "reporting"
    elif aggregation_hits:
        route = "aggregation_sql"
    elif len(text) <= 10:
        route = "needs_clarification"

    delegate_to_hermes = bool(
        domain == "finance"
        and not code_hits
        and not write_hits
        and finance_strategy_hits
    )
    hermes_profile = "finance_strategy" if delegate_to_hermes else None

    return {
        "route": route,
        "domain": domain,
        "reason": "; ".join(reasons)[:500] or "default_lookup",
        "message_chars": len(text),
        "has_write_intent": bool(write_hits or code_change_hits),
        "has_code_change_intent": bool(code_change_hits),
        "has_report_intent": bool(report_hits),
        "has_finance_strategy_intent": bool(finance_strategy_hits),
        "delegate_to_hermes": delegate_to_hermes,
        "hermes_profile": hermes_profile,
    }


def _tool_defs_anthropic(
    tool_defs: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for td in tool_defs or _tool_defs():
        fn = td.get("function") or {}
        items.append(
            {
                "name": fn.get("name"),
                "description": fn.get("description") or "",
                "input_schema": fn.get("parameters")
                or {"type": "object", "properties": {}},
            }
        )
    return items


def _anthropic_text_from_blocks(blocks: Any) -> str:
    parts: List[str] = []
    for b in blocks or []:
        if getattr(b, "type", "") == "text":
            txt = getattr(b, "text", "") or ""
            if txt:
                parts.append(txt)
    return "\n".join(parts).strip()


def _anthropic_message_from_blocks(blocks: Any) -> List[Dict[str, Any]]:
    content: List[Dict[str, Any]] = []
    for b in blocks or []:
        btype = getattr(b, "type", "")
        if btype == "text":
            content.append({"type": "text", "text": getattr(b, "text", "") or ""})
        elif btype == "tool_use":
            content.append(
                {
                    "type": "tool_use",
                    "id": getattr(b, "id", ""),
                    "name": getattr(b, "name", ""),
                    "input": getattr(b, "input", {}) or {},
                }
            )
    return content


def _assistant_system_prompt() -> str:
    return (
        "Eres un asistente agentico dentro de sam.chat.\n"
        "Tu alcance principal es corporativo para Plataforma Sports (empresa completa).\n"
        "Reglas:\n"
        "- El usuario esta autenticado.\n"
        "- Para preguntas financieras usa SOLO datos de gastos/pagos (tools de finanzas).\n"
        "- Para consultas financieras generales, prioriza finance_ops_query; usa tools especificas cuando apliquen.\n"
        "- Cuando el pedido coincida con un flujo ya cubierto por adapters canonicos, prioriza assistant_canonical_action o assistant_canonical_query sobre tools historicas separadas.\n"
        "- Flujos canonicos ya cubiertos: crear gasto desde contexto operativo, crear gasto manual, crear solicitud personal desde cuenta, crear solicitud a terceros, crear solicitud borrador desde compromiso operativo de pago, solicitar CFDI, ligar gasto a CFDI, construir preview contable de gasto, actualizar compromisos operativos y ligar movimiento bancario a gasto.\n"
        "- Usa assistant_canonical_action con action='operations.create_expense_from_context' cuando el usuario describa un gasto con torneo/fase/concepto ya conocidos.\n"
        "- Usa assistant_canonical_action con action='operations.create_solicitud_from_commitment' cuando el usuario pida preparar una solicitud de pago desde un compromiso operativo tipo payment. Requiere commitment_id, empleado_id y proveedor_cliente_id; crea SOLICITUD en borrador, no registra pago ni contabilidad.\n"
        "- Usa assistant_canonical_action con action='operations.update_commitment' cuando el usuario pida cerrar, marcar en proceso, descartar, asignar responsable o agregar notas a un compromiso operativo del expediente. Requiere commitment_id y confirmacion; no crea pagos ni contabilidad.\n"
        "- Usa assistant_canonical_action con action='expenses.create_manual_expense' cuando el usuario solo pida crear el gasto y no necesites resolver contexto operativo adicional.\n"
        "- Usa assistant_canonical_action con action='expenses.create_solicitud_personal' cuando el usuario pida una solicitud personal o anticipo sobre una cuenta de gastos ya identificada.\n"
        "- Usa assistant_canonical_action con action='expenses.create_solicitud_terceros' cuando el usuario pida una solicitud formal de pago a un proveedor o tercero con torneo y monto identificados.\n"
        "- Usa assistant_canonical_action con action='receipts.request_cfdi' para pedir factura desde un gasto identificado.\n"
        "- Usa assistant_canonical_action con action='receipts.link_expense_to_cfdi' cuando el usuario quiera ligar un gasto a un CFDI existente por UUID manual.\n"
        "- Usa assistant_canonical_action con action='receipts.send_document' cuando el usuario pida enviar un documento borrador a aprobación.\n"
        "- Usa assistant_canonical_action con action='receipts.approve_document' cuando el usuario pida aprobar un documento enviado y el actor esté identificado.\n"
        "- Usa assistant_canonical_action con action='receipts.reject_document' cuando el usuario pida rechazar un documento enviado y el actor esté identificado.\n"
        "- Usa assistant_canonical_query con action='receipts.pending_payment_overview' cuando finanzas quiera ver la cola de solicitudes aprobadas pendientes de pago.\n"
        "- Usa assistant_canonical_action con action='receipts.register_document_payment' cuando finanzas quiera registrar el pago de una SOLICITUD aprobada y generar el gasto asociado.\n"
        "- Usa assistant_canonical_action con action='receipts.register_document_reembolso' cuando el usuario quiera registrar un reembolso sobre un INFORME aprobado.\n"
        "- Usa assistant_canonical_query con action='receipts.cfdi_workflow_snapshot' cuando el usuario quiera seguimiento puntual del CFDI o comprobante de un gasto.\n"
        "- Usa assistant_canonical_query con action='receipts.cfdi_matching_overview' cuando el usuario quiera ver pendientes, vinculados o CFDI sin gasto en el matching documental.\n"
        "- Usa assistant_canonical_query con action='accounting.build_expense_preview' cuando el usuario quiera revisar como se contabilizaria un gasto antes de postear.\n"
        "- Usa assistant_canonical_action con action='accounting.assign_expense_accounting' cuando el usuario quiera clasificar o asignar cuenta contable a un gasto.\n"
        "- Usa assistant_canonical_action con action='accounting.post_expense_accounting' cuando el usuario quiera generar la póliza/asiento contable de un gasto.\n"
        "- Usa assistant_canonical_action con action='accounting.link_bank_to_expense' cuando el usuario quiera conciliar manualmente un movimiento bancario contra un gasto.\n"
        "- Usa assistant_canonical_query con action='expense.full_workflow_snapshot' cuando el usuario quiera ver el estado integral de un gasto a traves de gasto, CFDI, contabilidad y conciliacion.\n"
        "- Usa assistant_canonical_query con action='executive.realtime_report' para reportes financieros en tiempo real, presupuesto, comparativos o proyecciones.\n"
        "- Usa assistant_canonical_query con action='executive.strategy_snapshot' para diagnostico estrategico contable, fiscal o financiero.\n"
        "- Usa assistant_canonical_query con action='executive.alerts_scan' para alertas o anomalias financieras puntuales.\n"
        "- Usa assistant_canonical_query con action='executive.planner_snapshot' cuando dirección quiera un plan priorizado con cadencia, owners y playbooks a partir de estrategia y alertas.\n"
        "- Usa assistant_canonical_query con action='budgets.snapshot' cuando dirección o finanzas pida ver presupuesto por torneo/version con comparativo solicitado, comprometido, pagado, real, forecast de cierre, flujo próximo o escenarios `optimista/base/estresado`.\n"
        "- Usa assistant_canonical_action con action='budgets.update_line' cuando el usuario pida corregir un concepto, monto, owner, fase o cuenta final de una línea presupuestal. Solo aplica sobre versiones draft/reforecast y requiere confirmacion.\n"
        "- Usa assistant_canonical_action con action='budgets.update_version' cuando el usuario pida renombrar o documentar una versión presupuestal. Solo aplica sobre versiones draft/reforecast y requiere confirmacion.\n"
        "- Usa assistant_canonical_action con action='budgets.submit_for_approval' cuando el usuario pida mandar un presupuesto a revisión/aprobación.\n"
        "- Usa assistant_canonical_action con action='budgets.approve_version' cuando finanzas o dirección quiera aprobar formalmente una versión presupuestal.\n"
        "- Usa assistant_canonical_action con action='budgets.freeze_version' cuando el usuario pida congelar una versión ya aprobada.\n"
        "- Usa assistant_canonical_action con action='budgets.reforecast' cuando el usuario pida reabrir el presupuesto como reforecast sin tocar pagos ni contabilidad.\n"
        "- Usa assistant_canonical_query con action='operations.folder_planner_snapshot' cuando dirección u operaciones pregunte por compromisos operativos, alertas de carpetas, riesgos, responsables, pagos/cobros planeados no contables o vencimientos del expediente.\n"
        "- Usa assistant_canonical_query con action='operations.tournament_soul_snapshot' cuando operaciones o dirección pidan el estado canonico de un torneo: entidades, equipos, jugadores, documentos, calendario, media, email o WhatsApp. Este snapshot es read-only y respeta paginas publicas separadas por torneo sobre backend compartido.\n"
        "- Usa assistant_canonical_action con action='operations.update_team_status' cuando el usuario pida aprobar, rechazar, marcar pagado o cambiar status de un equipo. Requiere torneo, team_id o team_name, status y confirmacion.\n"
        "- Usa assistant_canonical_action con action='operations.verify_player_document' cuando el usuario pida marcar documentacion completa o verificada de un jugador. Requiere torneo/equipo/categoria e identidad del jugador; siempre confirma antes de escribir.\n"
        "- Usa assistant_canonical_action con action='operations.create_media_asset' cuando el usuario pida agregar evidencia, foto, video o stream de torneo. Requiere torneo, asset_type, titulo y URL; siempre confirma antes de escribir.\n"
        "- Usa assistant_canonical_action con action='operations.send_tournament_reminder' cuando el usuario pida enviar recordatorios operativos a equipos u operadores. Requiere torneo, destinatarios y mensaje o tipo de recordatorio; queda trazado por WhatsApp y requiere confirmacion.\n"
        "- Usa assistant_canonical_action con action='communications.send_tournament_email' cuando el usuario pida preparar, registrar o programar un email de torneo. Requiere tournament_id, recipients, subject y html_content/text_content; siempre requiere confirmacion y queda trazado en email_send_log o scheduled_emails.\n"
        "- Usa assistant_canonical_action con action='communications.send_tournament_whatsapp' cuando el usuario pida mandar o dejar listo un WhatsApp de torneo. Requiere tournament_id, recipients y message o template_type; siempre requiere confirmacion y queda trazado en whatsapp_message_log.\n"
        "- Usa assistant_canonical_query con action='executive.accounting_report' para libro diario, mayor, balanza o estado contable del mes.\n"
        "- Si el usuario pide registrar/agregar/modificar un gasto, NO expliques el proceso ni remitas al panel web: pregunta solo los datos faltantes o prepara la accion write con confirmacion.\n"
        "- Si el usuario pide exportar en Excel/PDF/CSV y hay datos tabulares recientes, responde que SI se puede exportar y ofrece exportarlo ahora.\n"
        "- Para captura de gastos: extrae proyecto, concepto, monto y fecha; si falta algo, preguntalo.\n"
        "- Si hay una foto/audio recien cargada para gasto, usa finance_expense_create con use_last_media=true.\n"
        "- Para ticket con factura, pide y confirma uso CFDI (ej. G03) antes de ejecutar.\n"
        "- Para ticket con use_last_media=true, usa request_cfdi_now=true cuando el usuario pida facturar ahora.\n"
        "- Para editar gastos, primero identifica el expense_id o numero_referencia.\n"
        "- Para solicitar factura (CFDI), identifica el gasto por expense_id o numero_referencia.\n"
        "- Para revisar en qué etapa va un ticket/gasto (registro, CFDI, cuenta contable, listo para contabilizar), usa finance_expense_workflow_status.\n"
        "- Si el usuario pide contabilizar o clasificar un gasto y ya hay cuenta sugerida o explícita, usa accounting.assign_expense_accounting por la vía canónica con confirmacion.\n"
        "- Si el usuario pide generar la póliza/asiento contable de un gasto, usa accounting.post_expense_accounting por la vía canónica con confirmacion.\n"
        "- Si el usuario pide guardar un reporte generado para reutilizarlo, usa assistant_save_artifact con el markdown del reporte y confirmacion.\n"
        "- Para preguntas operativas del torneo (equipos/jugadores/inscripciones) usa SOLO tools del torneo.\n"
        "- Para preguntas sobre expedientes, carpetas por entidad o fase nacional, usa tournament_expediente_snapshot cuando haya tournament_id en contexto; si preguntan por seguimiento/alertas/compromisos de carpetas usa operations.folder_planner_snapshot. Si no hay torneo claro, usa contexto inyectado o pregunta que torneo usar.\n"
        "- Para consultas transversales de BD fuera de tools especificas, usa db_read_universal (solo admin/superadmin).\n"
        "- Para modificaciones transversales de BD fuera de tools especificas, usa db_write_universal (requiere confirmacion superadmin).\n"
        "- Si no esta claro el torneo, pregunta cual torneo usar antes de ejecutar tools de torneo.\n"
        "- Si el usuario pide crear/programar calendario de juegos, usa tournament_schedule_create con confirmacion.\n"
        "- Si el usuario pide rehacer/regenerar calendario completo por fases, usa tournament_schedule_regenerate_from_rules con confirmacion.\n"
        "- Si el usuario sube Excel/CSV de roster y pide alta de equipo, usa tournament_team_register_from_roster con confirmacion.\n"
        "- Para calendarios, si el usuario menciona canchas/sedes u horario, mapealo a field_numbers y daily_start_time/daily_end_time.\n"
        "- Si pide ventanas distintas por categoria/rama, usa category_windows con start_time/end_time por categoria.\n"
        "- Antes de crear/regenerar calendario, valida disponibilidad: horarios (inicio/fin) y canchas; si faltan, preguntalos.\n"
        "- Si el usuario dice que canchas no restringen, usa infinite_fields=true.\n"
        "- Para consultas operativas generales, prioriza tournament_ops_query y usa tournament_registration_breakdown para casos especificos por estado.\n"
        "- Herramientas read-only: se pueden ejecutar sin confirmacion.\n"
        "- Herramientas write: SIEMPRE requieren confirmacion explicita del usuario. Finanzas/torneos requieren admin o superadmin; codigo/dev y db_write_universal requieren superadmin.\n"
        "- Si falta algun parametro (ej. estado, nombre de proveedor, rango de fechas), pide aclaracion.\n"
        "- Idioma por defecto: espanol de Mexico.\n"
        "- Si el usuario escribe en espanol, responde SOLO en espanol. No cambies a ingles, turco, vietnamita ni otro idioma salvo nombres propios, codigo o citas literales.\n"
        "- Usa numeros/tabla cuando aplique.\n"
        "- Entrega SOLO la respuesta final para el usuario o la tool call necesaria.\n"
        "- NUNCA muestres razonamiento interno, cadena de pensamiento, deliberacion, notas meta ni frases como 'veamos', 'necesito pensar', 'wait' o 'the user wants'.\n"
        "- Incluye siempre fuentes/citas cuando tengas evidencia de RAG o SQL.\n"
        "- No inventes datos: si el tool devuelve vacio, dilo explicitamente.\n"
        "- Modo codigo agentico: usa tools dev_* para leer/editar codigo solo cuando el usuario lo pida.\n"
        "- Antes de editar codigo, explica brevemente el cambio y luego solicita confirmacion admin.\n"
        "- Despues de cambios de codigo, ejecuta dev_run_checks para validar build/compile cuando aplique.\n"
    )


def _assistant_response_language_prompt(user_text: str) -> str:
    text = str(user_text or "").strip().lower()
    if not text:
        return (
            "Idioma de salida obligatorio: espanol de Mexico. "
            "Responde solo en espanol."
        )

    spanish_markers = (
        " cuanto ",
        " qué ",
        " que ",
        " gasto ",
        " gastos ",
        " hemos ",
        " este ",
        " esta ",
        " estos ",
        " estas ",
        " de ",
        " para ",
        " con ",
        " por ",
        " en ",
        " el ",
        " la ",
        " los ",
        " las ",
    )
    padded = f" {text} "
    if any(marker in padded for marker in spanish_markers) or any(
        ch in text for ch in "áéíóúñ¿¡"
    ):
        return (
            "Idioma de salida obligatorio: espanol de Mexico. "
            "Responde solo en espanol. "
            "No uses ingles, turco, vietnamita u otros idiomas salvo nombres propios o codigo."
        )
    return "Match the user's language. If the user wrote in Spanish, respond only in Spanish."


def _assistant_default_tournament_key() -> Optional[str]:
    value = (os.getenv("ASSISTANT_DEFAULT_TOURNAMENT_KEY", "") or "").strip().lower()
    return value or None


READ_TOOLS = {
    "assistant_canonical_query",
    "finance_accounting_report",
    "finance_alerts_scan",
    "finance_ops_query",
    "finance_expense_workflow_status",
    "finance_realtime_report",
    "finance_strategy_snapshot",
    "finance_vendor_payments",
    "finance_expense_search",
    "tournament_expediente_snapshot",
    "tournament_ops_query",
    "tournament_registration_breakdown",
    "dev_repo_search",
    "dev_file_read",
    "dev_run_checks",
    "db_read_universal",
}
WRITE_TOOLS = {
    "assistant_canonical_action",
    "finance_vendor_create",
    "finance_expense_assign_accounting",
    "finance_expense_create",
    "finance_expense_post_accounting",
    "finance_expense_update",
    "finance_expense_request_cfdi",
    "assistant_save_artifact",
    "tournament_schedule_create",
    "tournament_schedule_regenerate_from_rules",
    "tournament_team_register_from_roster",
    "dev_file_write",
    "dev_file_replace",
    "db_write_universal",
}

FINANCE_READ_TOOLS = {
    "assistant_canonical_query",
    "finance_accounting_report",
    "finance_alerts_scan",
    "finance_ops_query",
    "finance_expense_workflow_status",
    "finance_realtime_report",
    "finance_strategy_snapshot",
    "finance_vendor_payments",
    "finance_expense_search",
    "db_read_universal",
}
HERMES_FINANCE_STRATEGY_TOOLS = {
    "finance_strategy_snapshot",
    "finance_alerts_scan",
    "finance_realtime_report",
    "finance_accounting_report",
    "finance_ops_query",
    "finance_vendor_payments",
    "finance_expense_search",
    "finance_expense_workflow_status",
}
FINANCE_WRITE_TOOLS = {
    "assistant_canonical_action",
    "finance_vendor_create",
    "finance_expense_assign_accounting",
    "finance_expense_create",
    "finance_expense_post_accounting",
    "finance_expense_update",
    "finance_expense_request_cfdi",
    "assistant_save_artifact",
    "db_write_universal",
}
TOURNAMENT_READ_TOOLS = {
    "tournament_expediente_snapshot",
    "tournament_ops_query",
    "tournament_registration_breakdown",
    "db_read_universal",
}
TOURNAMENT_WRITE_TOOLS = {
    "assistant_canonical_action",
    "tournament_schedule_create",
    "tournament_schedule_regenerate_from_rules",
    "tournament_team_register_from_roster",
    "assistant_save_artifact",
    "db_write_universal",
}
DEV_READ_TOOLS = {
    "dev_repo_search",
    "dev_file_read",
    "dev_run_checks",
}
DEV_WRITE_TOOLS = {
    "dev_file_write",
    "dev_file_replace",
}


def _assistant_tool_registry() -> Dict[str, Any]:
    return _build_tool_registry(
        tool_defs=_tool_defs(),
        read_tools=READ_TOOLS,
        write_tools=WRITE_TOOLS,
        finance_tools=FINANCE_READ_TOOLS | FINANCE_WRITE_TOOLS,
        tournament_tools=TOURNAMENT_READ_TOOLS | TOURNAMENT_WRITE_TOOLS,
        dev_tools=DEV_READ_TOOLS | DEV_WRITE_TOOLS,
    )


def _tool_defs_filtered(allowed_names: set[str]) -> List[Dict[str, Any]]:
    return [
        tool_def
        for tool_def in _tool_defs()
        if str((tool_def.get("function") or {}).get("name") or "") in allowed_names
    ]


def _assistant_tool_defs(route_info: Dict[str, Any]) -> List[Dict[str, Any]]:
    route = str(route_info.get("route") or "").strip().lower()
    domain = str(route_info.get("domain") or "").strip().lower()
    hermes_profile = str(route_info.get("hermes_profile") or "").strip().lower()

    if hermes_profile == "finance_strategy":
        return _tool_defs_filtered(HERMES_FINANCE_STRATEGY_TOOLS)

    if route == "code_agentic":
        return _tool_defs_filtered(DEV_READ_TOOLS | DEV_WRITE_TOOLS)

    if route == "agentic_write":
        if domain == "finance":
            return _tool_defs_filtered(FINANCE_READ_TOOLS | FINANCE_WRITE_TOOLS)
        if domain == "tournament":
            return _tool_defs_filtered(TOURNAMENT_READ_TOOLS | TOURNAMENT_WRITE_TOOLS)
        return _tool_defs_filtered(
            (FINANCE_READ_TOOLS | FINANCE_WRITE_TOOLS)
            | (TOURNAMENT_READ_TOOLS | TOURNAMENT_WRITE_TOOLS)
        )

    if route in {"reporting", "aggregation_sql", "lookup_sql", "needs_clarification"}:
        if domain == "finance":
            return _tool_defs_filtered(FINANCE_READ_TOOLS)
        if domain == "tournament":
            return _tool_defs_filtered(TOURNAMENT_READ_TOOLS)
        if domain == "code":
            return _tool_defs_filtered(DEV_READ_TOOLS)
        return _tool_defs_filtered(FINANCE_READ_TOOLS | TOURNAMENT_READ_TOOLS)

    return _tool_defs()


def _assistant_route_system_prompt(route_info: Dict[str, Any]) -> str:
    route = str(route_info.get("route") or "").strip().lower()
    domain = str(route_info.get("domain") or "").strip().lower()
    route_lines: List[str] = [
        f"Modo de enrutamiento: route={route or 'lookup_sql'} domain={domain or 'generic'}."
    ]
    hermes_profile = str(route_info.get("hermes_profile") or "").strip().lower()
    if hermes_profile:
        route_lines.append(
            f"Delegacion interna transparente activa via Hermes profile `{hermes_profile}`."
        )
    if route == "lookup_sql":
        route_lines.append(
            "Consulta puntual: prioriza una sola tool read-only, responde corto y evita analisis largo."
        )
    elif route == "aggregation_sql":
        route_lines.append(
            "Consulta agregada: usa tools read-only, calcula desglose/comparativos y entrega tabla/resumen claro."
        )
    elif route == "reporting":
        route_lines.append(
            "Consulta analitica: primero junta evidencia con tools read-only y luego sintetiza hallazgos, riesgos y comparativos."
        )
    elif route == "agentic_write":
        route_lines.append(
            "Operacion de escritura: recopila parametros faltantes, valida impacto y prepara una sola accion write con confirmacion."
        )
    elif route == "code_agentic":
        route_lines.append(
            "Modo codigo: usa solo tools dev_*; lee contexto antes de proponer cambios y valida con checks cuando aplique."
        )
    else:
        route_lines.append(
            "Si el pedido es ambiguo, haz una pregunta de aclaracion minima antes de ejecutar tools."
        )
    return "\n".join(route_lines)


DEFAULT_GASTOS_DB_TABLES = {
    "adjuntos",
    "anticipos",
    "aprobaciones",
    "assistant_artifacts",
    "assistant_conversations",
    "assistant_messages",
    "assistant_runs",
    "centros_de_costo",
    "cfdi_reports",
    "cuentas_contables",
    "empleados",
    "cuentas_de_gastos",
    "expense_reports",
    "documentos",
    "invoice_reports",
    "proveedores_clientes",
    "reembolsos",
    "rfc_configs",
    "tournament_concepto_mappings",
    "tournaments",
    "copa_telmex_teams",
    "copa_telmex_players",
    "copa_telmex_ocr_registrations",
    "copa_telmex_validation_logs",
}

DEFAULT_SUPABASE_DB_TABLES = {
    "tournaments",
    "categories",
    "teams",
    "players",
    "registrations",
    "matches",
    "tournament_config",
    "profiles",
    "user_roles",
    "team_managers",
    "organizations",
    "invitations",
    "invitation_uses",
    "live_streams",
    "featured_videos",
    "gallery_photos",
    "player_stats",
    "team_standings",
    "survey_responses",
    "scheduled_emails",
    "email_inbox",
    "whatsapp_message_log",
    "whatsapp_templates",
    "whatsapp_auto_replies",
    "whatsapp_quick_replies",
    "whatsapp_conversation_assignments",
    "whatsapp_conversation_tags",
    "whatsapp_conversation_tag_assignments",
    "automation_flows",
    "automation_nodes",
    "automation_edges",
    "automation_logs",
    "admin_audit_log",
    "role_audit_log",
}

DB_FILTER_OPS = {"eq", "ilike", "gt", "gte", "lt", "lte", "in", "is_null"}
DEFAULT_DB_WRITE_DENYLIST_GASTOS = {
    "empleados",
    "assistant_conversations",
    "assistant_messages",
    "assistant_runs",
    "aprobaciones",
}
DEFAULT_DB_WRITE_DENYLIST_SUPABASE = {
    "profiles",
    "user_roles",
    "admin_audit_log",
    "role_audit_log",
}

_DB_COLUMN_ALIASES: Dict[str, Dict[str, str]] = {
    "expense_reports": {
        "fecha_gasto": "fecha",
    }
}


def _tool_defs() -> List[Dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "finance_strategy_snapshot",
                "description": (
                    "Arma un paquete read-only para estrategia contable, fiscal y financiera "
                    "combinando presupuesto, proyecciones, cierre contable, balanza y alertas."
                ),
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "question": {"type": ["string", "null"]},
                        "title": {"type": ["string", "null"]},
                        "date_from": {
                            "type": ["string", "null"],
                            "description": "YYYY-MM-DD o DD/MM/YYYY",
                        },
                        "date_to": {
                            "type": ["string", "null"],
                            "description": "YYYY-MM-DD o DD/MM/YYYY",
                        },
                        "proyecto": {"type": ["string", "null"]},
                        "concepto": {"type": ["string", "null"]},
                        "departamento": {"type": ["string", "null"]},
                        "fase_torneo": {"type": ["string", "null"]},
                        "metodo_pago": {"type": ["string", "null"]},
                        "proveedor_nombre": {"type": ["string", "null"]},
                        "budget_total": {"type": ["number", "null"]},
                        "budget_source": {
                            "type": "string",
                            "enum": ["solicitudes", "none"],
                            "default": "solicitudes",
                        },
                        "compare_years": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 5,
                            "default": 1,
                        },
                        "bi_scope": {
                            "type": ["string", "null"],
                            "enum": ["all", "beisbol", None],
                        },
                        "top_n": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 100,
                            "default": 12,
                        },
                        "z_threshold": {
                            "type": "number",
                            "minimum": 0.5,
                            "maximum": 5.0,
                            "default": 2.0,
                        },
                        "min_amount": {
                            "type": "number",
                            "minimum": 0,
                            "default": 5000.0,
                        },
                        "min_records": {
                            "type": "integer",
                            "minimum": 2,
                            "maximum": 20,
                            "default": 3,
                        },
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "finance_ops_query",
                "description": (
                    "Consulta universal financiera/contable con filtros y desgloses "
                    "de gastos, documentos y proveedores. Usa `proyecto` solo para "
                    "nombres reales de torneo/proyecto. Para rubros o insumos como "
                    "balones, uniformes, viáticos o arbitraje usa `concepto` o `query`, "
                    "nunca `proyecto`."
                ),
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "question": {"type": ["string", "null"]},
                        "query": {"type": ["string", "null"]},
                        "proyecto": {"type": ["string", "null"]},
                        "concepto": {"type": ["string", "null"]},
                        "departamento": {"type": ["string", "null"]},
                        "fase_torneo": {"type": ["string", "null"]},
                        "metodo_pago": {"type": ["string", "null"]},
                        "proveedor_nombre": {"type": ["string", "null"]},
                        "tipo_documento": {"type": ["string", "null"]},
                        "estado_documento": {"type": ["string", "null"]},
                        "date_from": {
                            "type": ["string", "null"],
                            "description": "YYYY-MM-DD o DD/MM/YYYY",
                        },
                        "date_to": {
                            "type": ["string", "null"],
                            "description": "YYYY-MM-DD o DD/MM/YYYY",
                        },
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 200,
                            "default": 50,
                        },
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "finance_alerts_scan",
                "description": "Detecta alertas/anomalias financieras por concepto usando z-score semanal.",
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "date_from": {
                            "type": ["string", "null"],
                            "description": "YYYY-MM-DD o DD/MM/YYYY",
                        },
                        "date_to": {
                            "type": ["string", "null"],
                            "description": "YYYY-MM-DD o DD/MM/YYYY",
                        },
                        "bi_scope": {
                            "type": ["string", "null"],
                            "enum": ["all", "beisbol", None],
                        },
                        "z_threshold": {
                            "type": "number",
                            "minimum": 0.5,
                            "maximum": 5.0,
                            "default": 2.0,
                        },
                        "min_amount": {
                            "type": "number",
                            "minimum": 0,
                            "default": 5000.0,
                        },
                        "min_records": {
                            "type": "integer",
                            "minimum": 2,
                            "maximum": 20,
                            "default": 3,
                        },
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "finance_realtime_report",
                "description": "Genera reporte financiero en tiempo real con presupuesto, comparativos anuales y proyecciones.",
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "question": {"type": ["string", "null"]},
                        "title": {"type": ["string", "null"]},
                        "date_from": {
                            "type": ["string", "null"],
                            "description": "YYYY-MM-DD o DD/MM/YYYY",
                        },
                        "date_to": {
                            "type": ["string", "null"],
                            "description": "YYYY-MM-DD o DD/MM/YYYY",
                        },
                        "proyecto": {"type": ["string", "null"]},
                        "concepto": {"type": ["string", "null"]},
                        "departamento": {"type": ["string", "null"]},
                        "fase_torneo": {"type": ["string", "null"]},
                        "metodo_pago": {"type": ["string", "null"]},
                        "proveedor_nombre": {"type": ["string", "null"]},
                        "budget_total": {"type": ["number", "null"]},
                        "budget_source": {
                            "type": "string",
                            "enum": ["solicitudes", "none"],
                            "default": "solicitudes",
                        },
                        "compare_years": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 5,
                            "default": 1,
                        },
                        "projection_mode": {
                            "type": "string",
                            "enum": ["run_rate", "none"],
                            "default": "run_rate",
                        },
                        "group_by": {
                            "type": "string",
                            "enum": [
                                "proyecto",
                                "concepto",
                                "departamento",
                                "fase_torneo",
                                "metodo_pago",
                                "proveedor",
                            ],
                            "default": "proyecto",
                        },
                        "top_n": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 100,
                            "default": 12,
                        },
                        "bi_scope": {
                            "type": ["string", "null"],
                            "enum": ["all", "beisbol", None],
                            "description": "Contexto BI global para filtrar por torneo/vertical.",
                        },
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "finance_vendor_payments",
                "description": "Suma pagos a un proveedor/fabricante en base a documentos tipo SOLICITUD.",
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "vendor_name": {"type": "string", "minLength": 1},
                        "date_from": {
                            "type": ["string", "null"],
                            "description": "YYYY-MM-DD o DD/MM/YYYY",
                        },
                        "date_to": {
                            "type": ["string", "null"],
                            "description": "YYYY-MM-DD o DD/MM/YYYY",
                        },
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 200,
                            "default": 50,
                        },
                    },
                    "required": ["vendor_name"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "dev_repo_search",
                "description": "Buscar texto en el codigo del repositorio (agentic code mode, solo superadmin).",
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "pattern": {"type": "string", "minLength": 1},
                        "path": {"type": "string", "default": "."},
                        "max_results": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 1000,
                            "default": 200,
                        },
                    },
                    "required": ["pattern"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "dev_file_read",
                "description": "Leer un archivo de codigo con numeracion de lineas (solo superadmin).",
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "path": {"type": "string", "minLength": 1},
                        "start_line": {"type": "integer", "minimum": 1, "default": 1},
                        "end_line": {"type": "integer", "minimum": 1, "default": 200},
                        "max_chars": {
                            "type": "integer",
                            "minimum": 100,
                            "maximum": 200000,
                            "default": 20000,
                        },
                    },
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "dev_run_checks",
                "description": "Ejecutar validaciones de codigo con lista blanca (solo superadmin).",
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "check": {
                            "type": "string",
                            "enum": [
                                "backend_compile",
                                "frontend_build_goal_fest",
                                "frontend_build_copatelmex",
                                "pytest_assistant",
                            ],
                        },
                        "path": {"type": "string", "default": "."},
                        "timeout_sec": {
                            "type": "integer",
                            "minimum": 5,
                            "maximum": 900,
                            "default": 120,
                        },
                        "max_output_chars": {
                            "type": "integer",
                            "minimum": 1000,
                            "maximum": 200000,
                            "default": 20000,
                        },
                    },
                    "required": ["check"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "db_read_universal",
                "description": "Consulta universal de BD (gastos/supabase). Solo admin o superadmin.",
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "data_source": {
                            "type": "string",
                            "enum": ["gastos", "supabase"],
                        },
                        "table": {"type": "string", "minLength": 1},
                        "columns": {
                            "type": ["array", "null"],
                            "items": {"type": "string"},
                        },
                        "filters": {
                            "type": ["array", "null"],
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": {
                                    "field": {"type": "string"},
                                    "op": {
                                        "type": "string",
                                        "enum": [
                                            "eq",
                                            "ilike",
                                            "gt",
                                            "gte",
                                            "lt",
                                            "lte",
                                            "in",
                                            "is_null",
                                        ],
                                    },
                                    "value": {},
                                },
                                "required": ["field", "op"],
                            },
                        },
                        "order_by": {"type": ["string", "null"]},
                        "order_dir": {
                            "type": "string",
                            "enum": ["asc", "desc"],
                            "default": "desc",
                        },
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 500,
                            "default": 100,
                        },
                    },
                    "required": ["data_source", "table"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "db_write_universal",
                "description": "Modificacion universal de BD (gastos/supabase). Solo superadmin y requiere confirmacion.",
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "data_source": {
                            "type": "string",
                            "enum": ["gastos", "supabase"],
                        },
                        "table": {"type": "string", "minLength": 1},
                        "action": {
                            "type": "string",
                            "enum": ["insert", "update", "delete"],
                        },
                        "values": {
                            "type": ["object", "null"],
                            "additionalProperties": True,
                        },
                        "filters": {
                            "type": ["array", "null"],
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": {
                                    "field": {"type": "string"},
                                    "op": {
                                        "type": "string",
                                        "enum": [
                                            "eq",
                                            "ilike",
                                            "gt",
                                            "gte",
                                            "lt",
                                            "lte",
                                            "in",
                                            "is_null",
                                        ],
                                    },
                                    "value": {},
                                },
                                "required": ["field", "op"],
                            },
                        },
                        "max_affected": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 1000,
                            "default": 200,
                        },
                    },
                    "required": ["data_source", "table", "action"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "dev_file_write",
                "description": "Escribir contenido en archivo de codigo (solo superadmin, requiere confirmacion).",
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "path": {"type": "string", "minLength": 1},
                        "content": {"type": "string"},
                        "mode": {
                            "type": "string",
                            "enum": ["overwrite", "append"],
                            "default": "overwrite",
                        },
                    },
                    "required": ["path", "content"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "dev_file_replace",
                "description": "Reemplazar texto en archivo de codigo (solo superadmin, requiere confirmacion).",
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "path": {"type": "string", "minLength": 1},
                        "old_text": {"type": "string", "minLength": 1},
                        "new_text": {"type": "string"},
                        "count": {"type": "integer", "minimum": 0, "default": 0},
                    },
                    "required": ["path", "old_text", "new_text"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "finance_expense_request_cfdi",
                "description": "Solicitar CFDI para un gasto ticket. Requiere confirmacion (superadmin).",
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "expense_id": {"type": ["string", "null"]},
                        "numero_referencia": {"type": ["string", "null"]},
                        "cfdi_use": {"type": ["string", "null"]},
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "tournament_expediente_snapshot",
                "description": "Snapshot integral read-only del expediente de un torneo: carpetas por entidad, fase nacional, categorias/rama, equipos, jugadores y finanzas vinculadas cuando el usuario tiene permiso.",
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "tournament_id": {
                            "type": "string",
                            "minLength": 1,
                            "description": "UUID del torneo seleccionado en la plataforma.",
                        },
                        "include_finance": {
                            "type": "boolean",
                            "default": True,
                            "description": "Incluye documentos, pagos y comprobaciones vinculados si el rol tiene permiso.",
                        },
                    },
                    "required": ["tournament_id"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "tournament_ops_query",
                "description": "Consulta universal operativa de torneo con filtros y desgloses (equipos, jugadores, categoria, rama, estado, municipio).",
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "tournament_key": {"type": "string", "minLength": 1},
                        "question": {"type": ["string", "null"]},
                        "state": {"type": ["string", "null"]},
                        "municipality": {"type": ["string", "null"]},
                        "category": {"type": ["string", "null"]},
                        "gender": {"type": ["string", "null"]},
                        "team_name": {"type": ["string", "null"]},
                        "tournament_slug": {"type": ["string", "null"]},
                        "date_from": {
                            "type": ["string", "null"],
                            "description": "YYYY-MM-DD o DD/MM/YYYY",
                        },
                        "date_to": {
                            "type": ["string", "null"],
                            "description": "YYYY-MM-DD o DD/MM/YYYY",
                        },
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 200,
                            "default": 50,
                        },
                    },
                    "required": ["tournament_key"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "tournament_registration_breakdown",
                "description": "Cuenta equipos y jugadores en un torneo por estado y desglose por municipio.",
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "tournament_key": {"type": "string", "minLength": 1},
                        "state": {"type": "string", "minLength": 1},
                        "date_from": {
                            "type": ["string", "null"],
                            "description": "YYYY-MM-DD o DD/MM/YYYY",
                        },
                        "date_to": {
                            "type": ["string", "null"],
                            "description": "YYYY-MM-DD o DD/MM/YYYY",
                        },
                    },
                    "required": ["tournament_key", "state"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "tournament_schedule_create",
                "description": "Crear calendario de juegos (round-robin) en Supabase para un torneo/categoria. Requiere confirmacion (superadmin).",
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "tournament_key": {"type": "string", "minLength": 1},
                        "tournament_slug": {"type": ["string", "null"]},
                        "tournament_name": {"type": ["string", "null"]},
                        "category_id": {"type": ["string", "null"]},
                        "phase": {"type": "string", "default": "Fase estatal"},
                        "start_date": {
                            "type": "string",
                            "description": "YYYY-MM-DD o DD/MM/YYYY",
                        },
                        "kickoff_time": {
                            "type": ["string", "null"],
                            "description": "HH:MM",
                        },
                        "games_per_day": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 20,
                            "default": 4,
                        },
                        "interval_minutes": {
                            "type": "integer",
                            "minimum": 30,
                            "maximum": 600,
                            "default": 90,
                        },
                        "field_number": {"type": ["string", "null"]},
                        "field_numbers": {
                            "type": ["array", "null"],
                            "items": {"type": "string"},
                        },
                        "infinite_fields": {"type": "boolean", "default": False},
                        "daily_start_time": {
                            "type": ["string", "null"],
                            "description": "HH:MM",
                        },
                        "daily_end_time": {
                            "type": ["string", "null"],
                            "description": "HH:MM",
                        },
                        "category_windows": {
                            "type": ["array", "null"],
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": {
                                    "category_id": {"type": ["string", "null"]},
                                    "category_name": {"type": ["string", "null"]},
                                    "gender": {"type": ["string", "null"]},
                                    "start_time": {"type": "string"},
                                    "end_time": {"type": ["string", "null"]},
                                },
                                "required": ["start_time"],
                            },
                        },
                        "status": {
                            "type": "string",
                            "enum": [
                                "scheduled",
                                "in_progress",
                                "live",
                                "finished",
                                "completed",
                            ],
                            "default": "scheduled",
                        },
                        "replace_existing_phase": {"type": "boolean", "default": False},
                        "dry_run": {"type": "boolean", "default": False},
                    },
                    "required": ["tournament_key", "start_date"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "tournament_schedule_regenerate_from_rules",
                "description": "Regenerar calendario completo por reglas (fase estatal + knockout) en Supabase. Requiere confirmacion (superadmin).",
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "tournament_key": {"type": "string", "minLength": 1},
                        "tournament_slug": {"type": ["string", "null"]},
                        "tournament_name": {"type": ["string", "null"]},
                        "category_id": {"type": ["string", "null"]},
                        "start_date": {
                            "type": "string",
                            "description": "YYYY-MM-DD o DD/MM/YYYY",
                        },
                        "kickoff_time": {
                            "type": ["string", "null"],
                            "description": "HH:MM",
                        },
                        "games_per_day": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 20,
                            "default": 4,
                        },
                        "interval_minutes": {
                            "type": "integer",
                            "minimum": 30,
                            "maximum": 600,
                            "default": 90,
                        },
                        "field_number": {"type": ["string", "null"]},
                        "field_numbers": {
                            "type": ["array", "null"],
                            "items": {"type": "string"},
                        },
                        "infinite_fields": {"type": "boolean", "default": False},
                        "daily_start_time": {
                            "type": ["string", "null"],
                            "description": "HH:MM",
                        },
                        "daily_end_time": {
                            "type": ["string", "null"],
                            "description": "HH:MM",
                        },
                        "category_windows": {
                            "type": ["array", "null"],
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": {
                                    "category_id": {"type": ["string", "null"]},
                                    "category_name": {"type": ["string", "null"]},
                                    "gender": {"type": ["string", "null"]},
                                    "start_time": {"type": "string"},
                                    "end_time": {"type": ["string", "null"]},
                                },
                                "required": ["start_time"],
                            },
                        },
                        "status": {
                            "type": "string",
                            "enum": [
                                "scheduled",
                                "in_progress",
                                "live",
                                "finished",
                                "completed",
                            ],
                            "default": "scheduled",
                        },
                        "replace_existing": {"type": "boolean", "default": True},
                        "include_group_stage": {"type": "boolean", "default": True},
                        "group_phase_name": {
                            "type": "string",
                            "default": "Fase estatal",
                        },
                        "include_knockout": {"type": "boolean", "default": True},
                        "knockout_rounds": {
                            "type": ["array", "null"],
                            "items": {"type": "string"},
                        },
                        "dry_run": {"type": "boolean", "default": False},
                    },
                    "required": ["tournament_key", "start_date"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "tournament_team_register_from_roster",
                "description": "Crear equipo + registro + jugadores desde roster estructurado (Excel/CSV). Requiere confirmacion (superadmin).",
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "tournament_key": {"type": "string", "minLength": 1},
                        "tournament_slug": {"type": ["string", "null"]},
                        "tournament_name": {"type": ["string", "null"]},
                        "category_id": {"type": ["string", "null"]},
                        "category_name": {"type": ["string", "null"]},
                        "team_name": {"type": "string", "minLength": 1},
                        "state": {"type": ["string", "null"]},
                        "country": {"type": "string", "default": "Mexico"},
                        "phone_country_code": {"type": "string", "default": "+52"},
                        "phone_number": {"type": ["string", "null"]},
                        "user_id": {"type": ["string", "null"]},
                        "payment_status": {"type": "string", "default": "pending"},
                        "notes": {"type": ["string", "null"]},
                        "representative_name": {"type": ["string", "null"]},
                        "representative_email": {"type": ["string", "null"]},
                        "representative_phone": {"type": ["string", "null"]},
                        "players": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": True,
                            },
                            "minItems": 1,
                            "maxItems": 300,
                        },
                        "dry_run": {"type": "boolean", "default": False},
                    },
                    "required": ["tournament_key", "team_name", "players"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "finance_accounting_report",
                "description": "Generar reporte contable estructurado (estado del mes, libro diario, mayor o balanza).",
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "report_type": {
                            "type": "string",
                            "enum": ["estado_mes", "diario", "mayor", "balanza"],
                        },
                        "year": {"type": ["integer", "null"]},
                        "month": {
                            "type": ["integer", "null"],
                            "minimum": 1,
                            "maximum": 12,
                        },
                        "tipo_poliza": {"type": ["string", "null"]},
                        "cuenta_codigo": {"type": ["string", "null"]},
                        "q": {"type": ["string", "null"]},
                        "limit": {
                            "type": "integer",
                            "minimum": 10,
                            "maximum": 300,
                            "default": 120,
                        },
                    },
                    "required": ["report_type"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "finance_expense_workflow_status",
                "description": "Revisar el estado end-to-end de un gasto/ticket: CFDI, links, cuenta contable y si está listo para contabilizar.",
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "expense_id": {"type": ["string", "null"]},
                        "numero_referencia": {"type": ["string", "null"]},
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "finance_expense_search",
                "description": "Buscar gastos por texto, proyecto y/o fechas.",
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "query": {"type": ["string", "null"]},
                        "proyecto": {"type": ["string", "null"]},
                        "date_from": {
                            "type": ["string", "null"],
                            "description": "YYYY-MM-DD o DD/MM/YYYY",
                        },
                        "date_to": {
                            "type": ["string", "null"],
                            "description": "YYYY-MM-DD o DD/MM/YYYY",
                        },
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 100,
                            "default": 20,
                        },
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "finance_expense_assign_accounting",
                "description": "Asignar cuenta contable a un gasto. Puede usar cuenta explícita o sugerida. Requiere confirmacion (superadmin).",
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "expense_id": {"type": ["string", "null"]},
                        "numero_referencia": {"type": ["string", "null"]},
                        "cuenta_contable_id": {"type": ["string", "null"]},
                        "cuenta_codigo": {"type": ["string", "null"]},
                        "use_suggested": {"type": "boolean", "default": True},
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "finance_expense_post_accounting",
                "description": "Generar póliza/asiento contable automático para un gasto individual. Requiere confirmacion (superadmin).",
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "expense_id": {"type": ["string", "null"]},
                        "numero_referencia": {"type": ["string", "null"]},
                        "tipo_poliza": {
                            "type": "string",
                            "enum": ["auto", "Di", "Eg", "Ig"],
                            "default": "auto",
                        },
                        "contra_cuenta_contable_id": {"type": ["string", "null"]},
                        "contra_cuenta_codigo": {"type": ["string", "null"]},
                        "iva_cuenta_contable_id": {"type": ["string", "null"]},
                        "iva_cuenta_codigo": {"type": ["string", "null"]},
                        "allow_without_cfdi": {"type": "boolean", "default": False},
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "assistant_canonical_query",
                "description": "Ejecuta una accion canonica read-only del asistente sobre capacidades ya existentes del sistema. No requiere confirmacion porque no modifica datos.",
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": supported_read_actions(),
                        },
                        "context": {
                            "type": ["object", "null"],
                            "description": "Contexto canonico compartido: torneo, fase, concepto, ids relacionados.",
                        },
                        "payload": {
                            "type": ["object", "null"],
                            "description": "Payload especifico de la accion.",
                        },
                    },
                    "required": ["action"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "assistant_canonical_action",
                "description": "Ejecuta una accion canonica del asistente sobre capacidades ya existentes del sistema. Requiere confirmacion (superadmin). Usa esta tool cuando quieras operar por adapters estables en vez de invocar tools historicas por modulo.",
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": supported_write_actions(),
                        },
                        "context": {
                            "type": ["object", "null"],
                            "description": "Contexto canonico compartido: torneo, fase, concepto, ids relacionados.",
                        },
                        "payload": {
                            "type": ["object", "null"],
                            "description": "Payload especifico de la accion.",
                        },
                    },
                    "required": ["action"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "finance_vendor_create",
                "description": "Crear un proveedor (catalogo comercial). Requiere confirmacion (superadmin).",
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "nombre": {"type": "string", "minLength": 1},
                        "rfc": {"type": ["string", "null"]},
                        "banco": {"type": ["string", "null"]},
                        "cuenta_clabe": {"type": ["string", "null"]},
                        "cuenta_bancaria": {"type": ["string", "null"]},
                    },
                    "required": ["nombre"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "finance_expense_create",
                "description": "Crear un gasto. Requiere confirmacion (superadmin).",
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "proyecto": {"type": "string", "minLength": 1},
                        "concepto": {"type": "string", "minLength": 1},
                        "gasto_cantidad": {"type": "number", "exclusiveMinimum": 0},
                        "fecha": {
                            "type": ["string", "null"],
                            "description": "YYYY-MM-DD o DD/MM/YYYY",
                        },
                        "tipo_gasto": {
                            "type": "string",
                            "enum": ["manual", "ticket"],
                            "default": "manual",
                        },
                        "metodo_pago": {"type": ["string", "null"]},
                        "departamento": {"type": ["string", "null"]},
                        "fase_torneo": {"type": ["string", "null"]},
                        "nombre_enviador": {"type": ["string", "null"]},
                        "numero_referencia": {"type": ["string", "null"]},
                        "iva": {"type": ["number", "null"]},
                        "hospedaje_entidad_fiscal": {"type": ["string", "null"]},
                        "hospedaje_tasa_impuesto": {"type": ["number", "null"]},
                        "hospedaje_impuesto_monto": {"type": ["number", "null"]},
                        "hospedaje_impuesto_confirmado": {
                            "type": "boolean",
                            "default": False,
                        },
                        "cfdi_use": {"type": ["string", "null"]},
                        "use_last_media": {
                            "type": "boolean",
                            "default": False,
                            "description": "Usa el ultimo archivo subido en la conversacion como comprobante del ticket.",
                        },
                        "request_cfdi_now": {
                            "type": "boolean",
                            "default": False,
                            "description": "Si true y es ticket con comprobante, solicita CFDI de inmediato via Tocino.",
                        },
                    },
                    "required": ["proyecto", "concepto", "gasto_cantidad"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "finance_expense_update",
                "description": "Editar un gasto existente por expense_id. Requiere confirmacion (superadmin).",
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "expense_id": {"type": ["string", "null"]},
                        "numero_referencia": {"type": ["string", "null"]},
                        "proyecto": {"type": ["string", "null"]},
                        "concepto": {"type": ["string", "null"]},
                        "gasto_cantidad": {"type": ["number", "null"]},
                        "fecha": {
                            "type": ["string", "null"],
                            "description": "YYYY-MM-DD o DD/MM/YYYY",
                        },
                        "tipo_gasto": {"type": ["string", "null"]},
                        "metodo_pago": {"type": ["string", "null"]},
                        "departamento": {"type": ["string", "null"]},
                        "fase_torneo": {"type": ["string", "null"]},
                        "nombre_enviador": {"type": ["string", "null"]},
                        "iva": {"type": ["number", "null"]},
                        "hospedaje_entidad_fiscal": {"type": ["string", "null"]},
                        "hospedaje_tasa_impuesto": {"type": ["number", "null"]},
                        "hospedaje_impuesto_monto": {"type": ["number", "null"]},
                        "hospedaje_impuesto_confirmado": {"type": ["boolean", "null"]},
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "assistant_save_artifact",
                "description": "Guardar un artefacto (ej. plantilla de reporte) asociado a la conversacion. Requiere confirmacion (superadmin).",
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "title": {"type": "string", "minLength": 1, "maxLength": 200},
                        "format": {
                            "type": "string",
                            "enum": ["markdown", "csv", "json"],
                        },
                        "content": {"type": "string", "minLength": 1},
                        "artifact_type": {
                            "type": "string",
                            "default": "report_template",
                        },
                    },
                    "required": ["title", "format", "content"],
                },
            },
        },
    ]


class ConversationCreateRequest(BaseModel):
    title: Optional[str] = Field(default=None, max_length=200)
    tournament_key: Optional[str] = Field(default=None, max_length=50)
    module_key: Optional[str] = Field(default=None, max_length=80)
    module_label: Optional[str] = Field(default=None, max_length=120)
    module_context: Optional[Dict[str, Any]] = None
    external_session_id: Optional[str] = Field(default=None, max_length=160)


class ConversationResponse(BaseModel):
    conversation_id: str
    title: Optional[str] = None
    tournament_key: Optional[str] = None
    module_key: Optional[str] = None
    external_session_id: Optional[str] = None
    created_at: str
    updated_at: str


def _conversation_response(conversation: AssistantConversation) -> ConversationResponse:
    return ConversationResponse(
        conversation_id=str(conversation.id),
        title=conversation.title,
        tournament_key=conversation.tournament_key,
        module_key=_conversation_module_key(conversation),
        external_session_id=_conversation_external_session_id(conversation),
        created_at=conversation.created_at.isoformat(),
        updated_at=conversation.updated_at.isoformat(),
    )


class MessageCreateRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=8000)
    tournament_key: Optional[str] = Field(default=None, max_length=50)
    module_key: Optional[str] = Field(default=None, max_length=80)
    module_label: Optional[str] = Field(default=None, max_length=120)
    module_context: Optional[Dict[str, Any]] = None
    assistant_mode: Optional[Literal["ahorro", "balanceado", "calidad"]] = None
    bi_year: Optional[int] = Field(default=None, ge=2000, le=2100)
    bi_scope: Optional[Literal["all", "beisbol"]] = None
    bi_segment: Optional[str] = Field(default=None, max_length=120)


class PendingConfirmation(BaseModel):
    run_id: str
    tool_name: str
    tool_args: Dict[str, Any]
    summary: str


class MessageResponse(BaseModel):
    assistant_message: str
    run_id: str
    tool_trace: List[Dict[str, Any]] = Field(default_factory=list)
    pending_confirmation: Optional[PendingConfirmation] = None


class InvitationCreateRequest(BaseModel):
    tournament_id: str = Field(..., min_length=1)
    quantity: int = Field(default=1, ge=1, le=100)
    max_uses: int = Field(default=100, ge=1, le=100000)
    notes: Optional[str] = Field(default=None, max_length=500)


class AdminTournamentSaveRequest(BaseModel):
    tournament: Dict[str, Any] = Field(default_factory=dict)
    config: Optional[Dict[str, Any]] = None


class EmailRecipientRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=320)
    name: Optional[str] = Field(default=None, max_length=200)


class EmailSendRequest(BaseModel):
    recipients: List[EmailRecipientRequest] = Field(
        default_factory=list, min_length=1, max_length=500
    )
    subject: str = Field(..., min_length=1, max_length=500)
    html_content: str = Field(..., min_length=1, max_length=200000)
    text_content: Optional[str] = Field(default=None, max_length=100000)
    tournament_id: Optional[str] = Field(default=None, min_length=1, max_length=64)


class EmailScheduleRequest(EmailSendRequest):
    scheduled_at: datetime


class ConfirmRequest(BaseModel):
    run_id: str
    approve: bool = True
    assistant_mode: Optional[Literal["ahorro", "balanceado", "calidad"]] = None


class RAGIngestRequest(BaseModel):
    paths: List[str] = Field(default_factory=lambda: ["docs", "reports", "codex.md"])
    reset: bool = False
    max_files: int = Field(default=200, ge=1, le=2000)


class RAGSearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=8000)
    top_k: int = Field(default=6, ge=1, le=20)
    min_score: float = Field(default=0.15, ge=0.0, le=1.0)


class RAGEvalRequest(BaseModel):
    questions: Optional[List[str]] = None
    top_k: int = Field(default=6, ge=1, le=20)


class RAGConfigUpdateRequest(BaseModel):
    doc_weight: Optional[float] = Field(default=None, ge=0, le=5)
    sql_weight: Optional[float] = Field(default=None, ge=0, le=5)
    recency_weight: Optional[float] = Field(default=None, ge=0, le=5)


class RAGConfigPresetRequest(BaseModel):
    preset: str = Field(..., min_length=1, max_length=50)


class RAGAutoTuneRequest(BaseModel):
    apply: bool = False
    questions: Optional[List[str]] = None
    top_k: int = Field(default=6, ge=1, le=20)


class RAGCodexUpdateRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=400_000)
    auto_ingest: bool = True
    max_files: int = Field(default=200, ge=1, le=2000)
    paths: Optional[List[str]] = None


class SyntheticDataSeedRequest(BaseModel):
    target: Literal["supabase", "gastos", "all"] = "all"
    apply: bool = False
    seed: int = Field(default=42, ge=1, le=999999)
    tournaments: int = Field(default=3, ge=1, le=20)
    teams_per_tournament: int = Field(default=8, ge=1, le=64)
    players_per_team: int = Field(default=14, ge=5, le=40)
    empleados: int = Field(default=6, ge=1, le=200)
    gastos_por_empleado: int = Field(default=12, ge=1, le=500)


class SyntheticDataCleanupRequest(BaseModel):
    target: Literal["supabase", "gastos", "all"] = "all"
    apply: bool = False


class SupabaseBridgeRequest(BaseModel):
    access_token: Optional[str] = None


class AssistantReportExportRequest(BaseModel):
    conversation_id: str
    run_id: Optional[str] = None
    format: Literal["csv", "pdf"] = "csv"
    filename: Optional[str] = None
    report_data: Optional[Dict[str, Any]] = None


class AssistantExecutiveDashboardRequest(BaseModel):
    year: int = Field(default=datetime.utcnow().year, ge=2000, le=2100)
    bi_scope: Optional[Literal["all", "beisbol"]] = None
    bi_segment: Optional[str] = Field(default=None, max_length=120)


class AssistantAlertsRequest(BaseModel):
    year: int = Field(default=datetime.utcnow().year, ge=2000, le=2100)
    bi_scope: Optional[Literal["all", "beisbol"]] = None
    bi_segment: Optional[str] = Field(default=None, max_length=120)
    spike_ratio: float = Field(default=1.35, ge=1.0, le=5.0)


class TournamentContractDraftRequest(BaseModel):
    evidence_id: Optional[str] = Field(default=None, max_length=80)
    title: Optional[str] = Field(default=None, max_length=240)
    scope: Literal["tournament", "entity", "national"] = "tournament"
    entity_name: Optional[str] = Field(default=None, max_length=200)
    additional_text: Optional[str] = Field(default=None, max_length=120_000)
    assistant_mode: Optional[Literal["ahorro", "balanceado", "calidad"]] = "calidad"


class TournamentContractDraftUpdateRequest(BaseModel):
    title: Optional[str] = Field(default=None, max_length=240)
    status: Optional[
        Literal["draft", "reviewed", "approved", "rejected", "applied"]
    ] = None
    draft_payload: Optional[Dict[str, Any]] = None


class TournamentCommitmentUpdateRequest(BaseModel):
    status: Optional[Literal["open", "in_progress", "done", "dismissed"]] = None
    owner_name: Optional[str] = Field(default=None, max_length=300)
    notes: Optional[str] = Field(default=None, max_length=4000)


class TournamentCommitmentSolicitudRequest(BaseModel):
    empleado_id: Optional[str] = Field(default=None, max_length=80)
    proveedor_cliente_id: str = Field(..., min_length=1, max_length=80)
    gastos_torneo_id: Optional[str] = Field(default=None, max_length=80)
    monto_solicitado: Optional[float] = Field(default=None, gt=0)
    concepto_pago: Optional[str] = Field(default=None, max_length=500)
    fecha_pago: Optional[str] = Field(default=None, max_length=20)
    notas: Optional[str] = Field(default=None, max_length=2000)


class BudgetVersionTransitionRequest(BaseModel):
    status: Literal["draft", "submitted", "approved", "frozen", "reforecast", "closed"]
    note: Optional[str] = Field(default=None, max_length=2000)


class BudgetVersionUpdateRequest(BaseModel):
    version_name: Optional[str] = Field(default=None, max_length=120)
    notes: Optional[str] = Field(default=None, max_length=4000)


class BudgetLineUpdateRequest(BaseModel):
    concept_name: Optional[str] = Field(default=None, max_length=200)
    account_code_final: Optional[str] = Field(default=None, max_length=80)
    budget_amount: Optional[float] = Field(default=None, ge=0)
    priority: Optional[str] = Field(default=None, max_length=40)
    owner_name: Optional[str] = Field(default=None, max_length=200)
    phase: Optional[str] = Field(default=None, max_length=80)
    criteria_note: Optional[str] = Field(default=None, max_length=2000)
    observations: Optional[str] = Field(default=None, max_length=2000)


def _normalize_role(role: Optional[str]) -> str:
    return (role or "").strip().lower()


def _is_superadmin(role: Optional[str]) -> bool:
    return _normalize_role(role) in {"super_admin", "superadmin"}


def _is_admin(role: Optional[str]) -> bool:
    return _normalize_role(role) in {"admin", "super_admin", "superadmin"}


def _can_confirm_write(tool_name: str, role: Optional[str]) -> bool:
    normalized_tool = (tool_name or "").strip()
    if normalized_tool in DEV_WRITE_TOOLS or normalized_tool == "db_write_universal":
        return _is_superadmin(role)
    return _is_admin(role)


def _can_manage_operations_console(current_empleado: Any) -> bool:
    role = getattr(current_empleado, "rol", None)
    return bool(
        _is_superadmin(role)
        or (str(role or "").strip().lower() in {"admin"})
        or has_permission(current_empleado, "admin.operaciones.manage")
        or has_permission(current_empleado, "admin.torneos.manage")
        or has_permission(current_empleado, "admin.*")
    )


def _can_view_finance_console(current_empleado: Any) -> bool:
    role = getattr(current_empleado, "rol", None)
    return bool(
        str(role or "").strip().lower() in {"finanzas"}
        or _is_admin(role)
        or has_permission(current_empleado, "admin.finanzas.manage")
        or has_permission(current_empleado, "finanzas.manage")
        or has_permission(current_empleado, "admin.operaciones.manage")
        or has_permission(current_empleado, "admin.torneos.manage")
        or has_permission(current_empleado, "admin.*")
    )


def _superadmin_emails() -> set[str]:
    raw = os.getenv("ASSISTANT_SUPERADMIN_EMAILS", "")
    return {item.strip().lower() for item in raw.split(",") if item and item.strip()}


def _is_truthy(value: Optional[str], *, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _extract_bearer_token(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    parts = authorization.strip().split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


def _supabase_base_url() -> str:
    return (os.getenv("SUPABASE_URL") or os.getenv("VITE_SUPABASE_URL") or "").rstrip(
        "/"
    )


def _supabase_api_key() -> str:
    return (
        os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        or os.getenv("SUPABASE_ANON_KEY")
        or os.getenv("VITE_SUPABASE_ANON_KEY")
        or ""
    ).strip()


def _codex_doc_path() -> Path:
    base_dir = Path(os.getenv("ASSISTANT_RAG_BASE_DIR", "/root/samchat"))
    default_path = base_dir / "codex.md"
    return Path(os.getenv("ASSISTANT_CODEX_PATH", str(default_path)))


def _sanitize_filename(value: Optional[str], *, default_stem: str, ext: str) -> str:
    stem = re.sub(r"[^a-zA-Z0-9._-]+", "_", (value or "").strip()).strip("._")
    if not stem:
        stem = default_stem
    if not stem.lower().endswith(f".{ext}"):
        stem = f"{stem}.{ext}"
    return stem


def _extract_report_payload_from_trace(tool_trace: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(tool_trace, list):
        return None
    for step in reversed(tool_trace):
        if not isinstance(step, dict):
            continue
        result = step.get("result")
        if not isinstance(result, dict):
            continue
        # Signature of finance_realtime_report output.
        if any(
            k in result
            for k in (
                "totals",
                "budget",
                "comparison_yoy",
                "projection",
                "breakdown",
                "trend_monthly",
            )
        ):
            return result
        rows = result.get("rows")
        if isinstance(rows, list) and rows and all(isinstance(r, dict) for r in rows):
            return {
                "title": f"Export {step.get('tool') or 'query'}",
                "generated_at": datetime.utcnow().isoformat(),
                "period": {},
                "totals": {"registros": len(rows)},
                "budget": {},
                "projection": {},
                "breakdown": {"items": rows},
                "trend_monthly": [],
                "comparison_yoy": [],
            }
        items = result.get("items")
        if (
            isinstance(items, list)
            and items
            and all(isinstance(r, dict) for r in items)
        ):
            return {
                "title": f"Export {step.get('tool') or 'query'}",
                "generated_at": datetime.utcnow().isoformat(),
                "period": {},
                "totals": {"registros": len(items)},
                "budget": {},
                "projection": {},
                "breakdown": {"items": items},
                "trend_monthly": [],
                "comparison_yoy": [],
            }
    return None


def _has_exportable_report_trace(tool_trace: Any) -> bool:
    return _extract_report_payload_from_trace(tool_trace) is not None


def _is_failed_or_incomplete_assistant_message(message: str) -> bool:
    text = (message or "").strip().lower()
    if not text:
        return False
    blocked_phrases = (
        "tardó demasiado",
        "tardo demasiado",
        "provider_timeout",
        "no se pudo generar",
        "no pude generar",
        "no encontré resultados",
        "no encontre resultados",
        "sin resultados",
        "intenta de nuevo",
        "unexpected processing error",
    )
    return any(phrase in text for phrase in blocked_phrases)


def _maybe_append_export_prompt(message: str, tool_trace: Any) -> str:
    text = (message or "").strip()
    if _is_failed_or_incomplete_assistant_message(text):
        return text
    if not _has_exportable_report_trace(tool_trace):
        return text
    hint = "¿Quieres que te lo exporte ahora? Responde Excel (CSV) o PDF."
    if hint.lower() in text.lower():
        return text
    if not text:
        return hint
    return f"{text}\n\n{hint}"


def _db_write_denylist(default_tables: set[str], env_var: str) -> set[str]:
    raw = (os.getenv(env_var) or "").strip()
    if not raw:
        return set(default_tables)
    tables: set[str] = set()
    for item in raw.split(","):
        name = item.strip().lower()
        if not name:
            continue
        if not re.fullmatch(r"[a-z_][a-z0-9_]*", name):
            continue
        tables.add(name)
    return tables or set(default_tables)


def _validate_db_write_target(*, data_source: str, table: str) -> None:
    src = (data_source or "").strip().lower()
    tbl = (table or "").strip().lower()
    if src == "gastos":
        deny = _db_write_denylist(
            DEFAULT_DB_WRITE_DENYLIST_GASTOS,
            "ASSISTANT_DB_WRITE_DENYLIST_GASTOS",
        )
        if tbl in deny:
            raise HTTPException(
                status_code=403,
                detail=f"Write blocked for sensitive gastos table: {tbl}",
            )
        return
    if src == "supabase":
        deny = _db_write_denylist(
            DEFAULT_DB_WRITE_DENYLIST_SUPABASE,
            "ASSISTANT_DB_WRITE_DENYLIST_SUPABASE",
        )
        if tbl in deny:
            raise HTTPException(
                status_code=403,
                detail=f"Write blocked for sensitive supabase table: {tbl}",
            )
        return


def _write_is_high_risk(tool_name: str, tool_args: Dict[str, Any]) -> bool:
    if tool_name != "db_write_universal":
        return False
    action = str(tool_args.get("action") or "").strip().lower()
    max_affected = int(tool_args.get("max_affected") or 200)
    if action == "delete":
        return True
    if action in {"insert", "update"} and max_affected >= 50:
        return True
    return False


def _write_second_confirmation_message(
    tool_name: str, tool_args: Dict[str, Any]
) -> str:
    return (
        "⚠️ Confirmación reforzada requerida por operación de alto impacto.\n"
        f"Herramienta: {tool_name}\n"
        "Esta acción puede modificar o eliminar muchos registros.\n\n"
        f"Parámetros:\n{json.dumps(tool_args, ensure_ascii=False, indent=2)}\n\n"
        "Responde /ok para confirmar definitivamente o /cancel para abortar."
    )


def _normalize_confirmation_message(message: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(message or "").strip().lower())
    return cleaned.strip(" .,!?:;")


def _is_explicit_approval_message(message: str) -> bool:
    normalized = _normalize_confirmation_message(message)
    return normalized in {
        "si",
        "sí",
        "ok",
        "/ok",
        "dale",
        "adelante",
        "procede",
        "procede",
        "confirma",
        "confirma",
        "confirmar",
        "aprueba",
        "apruébalo",
        "apruebalo",
        "ejecuta",
        "hazlo",
        "registralo",
        "regístralo",
        "si confirma",
        "sí confirma",
        "si, confirma",
        "sí, confirma",
        "si confirmo",
        "sí confirmo",
        "si, confirmo",
        "sí, confirmo",
        "si procede",
        "sí procede",
        "si, procede",
        "sí, procede",
        "si, confirma y registralo ahora",
        "sí, confirma y regístralo ahora",
        "si confirma y registralo ahora",
        "sí confirma y regístralo ahora",
    }


def _is_explicit_rejection_message(message: str) -> bool:
    normalized = _normalize_confirmation_message(message)
    return normalized in {
        "no",
        "cancela",
        "cancela",
        "/cancel",
        "aborta",
        "detenlo",
        "deténlo",
        "no confirmo",
        "rechaza",
        "recházalo",
        "rechazalo",
    }


async def _latest_pending_run_for_conversation(
    *,
    session: AsyncSession,
    conversation_id: uuid.UUID,
    empleado_id: uuid.UUID,
) -> Optional[AssistantRun]:
    return (
        await session.execute(
            select(AssistantRun)
            .where(
                AssistantRun.conversation_id == conversation_id,
                AssistantRun.empleado_id == empleado_id,
                AssistantRun.status == "pending_confirmation",
            )
            .order_by(desc(AssistantRun.created_at))
            .limit(1)
        )
    ).scalar_one_or_none()


def _build_expense_canonical_pending(
    *,
    raw_message: str,
    conversation: AssistantConversation,
    empleado_id: uuid.UUID,
) -> Optional[Tuple[str, Dict[str, Any], str]]:
    normalized_message = _normalize_confirmation_message(raw_message)
    if not any(
        token in normalized_message
        for token in ("registr", "crear", "crea", "alta", "agrega")
    ):
        return None

    context_payload = _conversation_module_context_dict(conversation)
    if not context_payload:
        return None

    canonical_context = AssistantContext.from_dict(context_payload).merge(
        responsible_user_id=str(empleado_id)
    )
    concept = context_payload.get("concepto") or canonical_context.concepto
    amount = (
        context_payload.get("gasto_cantidad")
        or context_payload.get("amount")
        or context_payload.get("monto")
    )
    expense_date = context_payload.get("fecha") or context_payload.get("expense_date")
    payment_method = context_payload.get("metodo_pago") or context_payload.get(
        "payment_method"
    )
    expense_type = (
        context_payload.get("tipo_gasto")
        or context_payload.get("expense_type")
        or "manual"
    )
    requires_cfdi = context_payload.get("requires_cfdi")
    project_name = (
        context_payload.get("proyecto")
        or context_payload.get("torneo")
        or canonical_context.tournament_name
    )
    department = context_payload.get("departamento") or canonical_context.departamento
    phase = context_payload.get("fase_torneo") or canonical_context.fase_torneo
    cfdi_use = context_payload.get("cfdi_use")

    if not concept or amount in {None, ""} or not expense_date or not project_name:
        return None
    if not payment_method:
        return None
    if bool(requires_cfdi) and not cfdi_use:
        return None

    payload: Dict[str, Any] = {
        "proyecto": project_name,
        "concepto": concept,
        "gasto_cantidad": amount,
        "fecha": expense_date,
        "tipo_gasto": expense_type,
        "departamento": department,
        "fase_torneo": phase,
        "metodo_pago": payment_method,
        "empleado_id": str(empleado_id),
    }
    if cfdi_use:
        payload["cfdi_use"] = cfdi_use

    action = "expenses.create_manual_expense"
    if canonical_context.tournament_name and phase:
        action = "operations.create_expense_from_context"

    tool_args = {
        "action": action,
        "context": canonical_context.merge(
            tournament_name=project_name,
            fase_torneo=phase,
            concepto=concept,
            departamento=department,
        ).to_dict(),
        "payload": payload,
    }
    assistant_message = (
        "Tengo los datos mínimos para registrar el gasto.\n\n"
        f"- Proyecto/Torneo: {project_name}\n"
        f"- Concepto: {concept}\n"
        f"- Monto: {amount}\n"
        f"- Fecha: {expense_date}\n"
        f"- Departamento: {department or 'N/D'}\n"
        f"- Método de pago: {payment_method}\n"
        f"- Tipo: {expense_type}\n"
        f"- Fase: {phase or 'N/D'}\n\n"
        "Confirma para ejecutar el registro."
    )
    return "assistant_canonical_action", tool_args, assistant_message


def _extract_cfdi_use_from_message(message: str) -> Optional[str]:
    match = re.search(r"\b([A-Z][0-9]{2})\b", str(message or "").upper())
    if not match:
        return None
    return match.group(1)


def _extract_uuid_candidates(message: str) -> List[str]:
    return re.findall(
        r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b",
        str(message or ""),
    )


def _build_cfdi_canonical_pending(
    *,
    raw_message: str,
    conversation: AssistantConversation,
    empleado_id: uuid.UUID,
) -> Optional[Tuple[str, Dict[str, Any], str]]:
    normalized_message = _normalize_confirmation_message(raw_message)
    if not any(
        token in normalized_message
        for token in ("cfdi", "factura", "facturar", "solicita", "solicitar")
    ):
        return None

    context_payload = _conversation_module_context_dict(conversation)
    canonical_context = AssistantContext.from_dict(context_payload).merge(
        responsible_user_id=str(empleado_id)
    )
    expense_id = context_payload.get("expense_id") or canonical_context.expense_id
    if not expense_id:
        return None

    cfdi_use = (
        context_payload.get("cfdi_use")
        or _extract_cfdi_use_from_message(raw_message)
        or "G03"
    )
    tool_args = {
        "action": "receipts.request_cfdi",
        "context": canonical_context.merge(expense_id=str(expense_id)).to_dict(),
        "payload": {
            "expense_id": str(expense_id),
            "cfdi_use": cfdi_use,
        },
    }
    assistant_message = (
        "Tengo los datos mínimos para solicitar el CFDI.\n\n"
        f"- Gasto: {expense_id}\n"
        f"- Uso CFDI: {cfdi_use}\n\n"
        "Confirma para ejecutar la solicitud de factura."
    )
    return "assistant_canonical_action", tool_args, assistant_message


def _build_link_cfdi_canonical_pending(
    *,
    raw_message: str,
    conversation: AssistantConversation,
    empleado_id: uuid.UUID,
) -> Optional[Tuple[str, Dict[str, Any], str]]:
    normalized_message = _normalize_confirmation_message(raw_message)
    if not any(
        token in normalized_message
        for token in ("liga", "ligar", "vincula", "vincular", "uuid", "cfdi")
    ):
        return None

    context_payload = _conversation_module_context_dict(conversation)
    canonical_context = AssistantContext.from_dict(context_payload).merge(
        responsible_user_id=str(empleado_id)
    )
    expense_id = context_payload.get("expense_id") or canonical_context.expense_id
    uuid_candidates = _extract_uuid_candidates(raw_message)
    cfdi_uuid_manual = context_payload.get("cfdi_uuid_manual")
    if not cfdi_uuid_manual:
        for candidate in uuid_candidates:
            if str(candidate).lower() != str(expense_id or "").lower():
                cfdi_uuid_manual = candidate
                break

    if not expense_id or not cfdi_uuid_manual:
        return None

    tool_args = {
        "action": "receipts.link_expense_to_cfdi",
        "context": canonical_context.merge(expense_id=str(expense_id)).to_dict(),
        "payload": {
            "expense_id": str(expense_id),
            "cfdi_uuid_manual": str(cfdi_uuid_manual),
        },
    }
    assistant_message = (
        "Tengo los datos mínimos para ligar el CFDI al gasto.\n\n"
        f"- Gasto: {expense_id}\n"
        f"- UUID CFDI: {cfdi_uuid_manual}\n\n"
        "Confirma para ejecutar el ligado manual."
    )
    return "assistant_canonical_action", tool_args, assistant_message


def _build_bank_link_canonical_pending(
    *,
    raw_message: str,
    conversation: AssistantConversation,
    empleado_id: uuid.UUID,
) -> Optional[Tuple[str, Dict[str, Any], str]]:
    normalized_message = _normalize_confirmation_message(raw_message)
    if not any(
        token in normalized_message
        for token in (
            "concilia",
            "conciliar",
            "conciliacion",
            "conciliación",
            "movimiento bancario",
            "bancario",
        )
    ):
        return None

    context_payload = _conversation_module_context_dict(conversation)
    canonical_context = AssistantContext.from_dict(context_payload).merge(
        responsible_user_id=str(empleado_id)
    )
    expense_id = context_payload.get("expense_id") or canonical_context.expense_id
    movement_id = context_payload.get("movement_id") or context_payload.get(
        "bank_movement_id"
    )
    uuid_candidates = _extract_uuid_candidates(raw_message)
    if not movement_id:
        for candidate in uuid_candidates:
            if str(candidate).lower() != str(expense_id or "").lower():
                movement_id = candidate
                break

    if not expense_id or not movement_id:
        return None

    tool_args = {
        "action": "accounting.link_bank_to_expense",
        "context": canonical_context.merge(expense_id=str(expense_id)).to_dict(),
        "payload": {
            "expense_id": str(expense_id),
            "movement_id": str(movement_id),
            "empleado_id": str(empleado_id),
        },
    }
    assistant_message = (
        "Tengo los datos mínimos para conciliar el movimiento bancario contra el gasto.\n\n"
        f"- Gasto: {expense_id}\n"
        f"- Movimiento bancario: {movement_id}\n\n"
        "Confirma para ejecutar la conciliación manual."
    )
    return "assistant_canonical_action", tool_args, assistant_message


def _build_accounting_assign_canonical_pending(
    *,
    raw_message: str,
    conversation: AssistantConversation,
    empleado_id: uuid.UUID,
) -> Optional[Tuple[str, Dict[str, Any], str]]:
    normalized_message = _normalize_confirmation_message(raw_message)
    if not any(
        token in normalized_message
        for token in (
            "asigna cuenta",
            "asignar cuenta",
            "clasifica",
            "clasificar",
            "contabiliza",
            "contabilizar",
            "cuenta contable",
        )
    ):
        return None
    if any(
        token in normalized_message
        for token in ("poliza", "póliza", "asiento", "postea", "postear")
    ):
        return None

    context_payload = _conversation_module_context_dict(conversation)
    canonical_context = AssistantContext.from_dict(context_payload).merge(
        responsible_user_id=str(empleado_id)
    )
    expense_id = context_payload.get("expense_id") or canonical_context.expense_id
    numero_referencia = context_payload.get("numero_referencia")
    cuenta_contable_id = context_payload.get("cuenta_contable_id")
    cuenta_codigo = (
        context_payload.get("cuenta_codigo")
        or context_payload.get("account_code")
        or context_payload.get("cuenta")
    )
    use_suggested = context_payload.get("use_suggested")
    if use_suggested is None:
        use_suggested = not bool(cuenta_contable_id or cuenta_codigo)

    if not expense_id and not numero_referencia:
        return None

    payload: Dict[str, Any] = {
        "use_suggested": bool(use_suggested),
    }
    if expense_id:
        payload["expense_id"] = str(expense_id)
    if numero_referencia:
        payload["numero_referencia"] = str(numero_referencia)
    if cuenta_contable_id:
        payload["cuenta_contable_id"] = str(cuenta_contable_id)
    if cuenta_codigo:
        payload["cuenta_codigo"] = str(cuenta_codigo)

    tool_args = {
        "action": "accounting.assign_expense_accounting",
        "context": canonical_context.merge(
            expense_id=str(expense_id) if expense_id else None
        ).to_dict(),
        "payload": payload,
    }
    assistant_message = (
        "Tengo los datos mínimos para asignar la cuenta contable del gasto.\n\n"
        f"- Gasto: {expense_id or numero_referencia}\n"
        f"- Cuenta explícita: {cuenta_codigo or cuenta_contable_id or 'usar sugerida'}\n\n"
        "Confirma para ejecutar la clasificación contable."
    )
    return "assistant_canonical_action", tool_args, assistant_message


def _build_accounting_post_canonical_pending(
    *,
    raw_message: str,
    conversation: AssistantConversation,
    empleado_id: uuid.UUID,
) -> Optional[Tuple[str, Dict[str, Any], str]]:
    normalized_message = _normalize_confirmation_message(raw_message)
    if not any(
        token in normalized_message
        for token in ("poliza", "póliza", "asiento", "postea", "postear", "contabiliza")
    ):
        return None

    context_payload = _conversation_module_context_dict(conversation)
    canonical_context = AssistantContext.from_dict(context_payload).merge(
        responsible_user_id=str(empleado_id)
    )
    expense_id = context_payload.get("expense_id") or canonical_context.expense_id
    numero_referencia = context_payload.get("numero_referencia")
    if not expense_id and not numero_referencia:
        return None

    payload: Dict[str, Any] = {
        "empleado_id": str(empleado_id),
        "tipo_poliza": context_payload.get("tipo_poliza") or "auto",
        "allow_without_cfdi": bool(context_payload.get("allow_without_cfdi", False)),
    }
    if expense_id:
        payload["expense_id"] = str(expense_id)
    if numero_referencia:
        payload["numero_referencia"] = str(numero_referencia)

    for key in (
        "contra_cuenta_contable_id",
        "contra_cuenta_codigo",
        "iva_cuenta_contable_id",
        "iva_cuenta_codigo",
    ):
        value = context_payload.get(key)
        if value:
            payload[key] = value

    tool_args = {
        "action": "accounting.post_expense_accounting",
        "context": canonical_context.merge(
            expense_id=str(expense_id) if expense_id else None
        ).to_dict(),
        "payload": payload,
    }
    assistant_message = (
        "Tengo los datos mínimos para generar la póliza del gasto.\n\n"
        f"- Gasto: {expense_id or numero_referencia}\n"
        f"- Tipo de póliza: {payload['tipo_poliza']}\n"
        f"- Permitir sin CFDI: {'sí' if payload['allow_without_cfdi'] else 'no'}\n\n"
        "Confirma para ejecutar el posteo contable."
    )
    return "assistant_canonical_action", tool_args, assistant_message


def _write_requires_verification(tool_name: str, tool_args: Dict[str, Any]) -> bool:
    normalized_tool = (tool_name or "").strip()
    if normalized_tool not in WRITE_TOOLS:
        return False
    if normalized_tool in DEV_WRITE_TOOLS:
        return True
    if normalized_tool == "db_write_universal":
        return True
    if _write_is_high_risk(normalized_tool, tool_args):
        return True
    return normalized_tool in {
        "finance_expense_assign_accounting",
        "finance_expense_post_accounting",
        "finance_expense_update",
        "tournament_schedule_create",
        "tournament_schedule_regenerate_from_rules",
        "tournament_team_register_from_roster",
    }


async def _confirm_pending_run(
    *,
    run: AssistantRun,
    conversation: AssistantConversation,
    approve: bool,
    assistant_mode: Optional[str],
    openai_api_key: Optional[str],
    current_empleado: Empleado,
    session: AsyncSession,
) -> MessageResponse:
    tool_name = run.pending_tool_name or ""
    if not _can_confirm_write(tool_name, getattr(current_empleado, "rol", None)):
        raise HTTPException(
            status_code=403,
            detail=(
                "Write confirmation requires admin or superadmin role"
                if tool_name not in DEV_WRITE_TOOLS
                and tool_name != "db_write_universal"
                else "This write confirmation requires superadmin role"
            ),
        )

    if not approve:
        run.status = "failed"
        run.assistant_message = (
            run.assistant_message or ""
        ) + "\n\nAccion cancelada por el usuario."
        await session.commit()
        return MessageResponse(
            assistant_message="Accion cancelada.",
            run_id=str(run.id),
            tool_trace=run.tool_trace or [],
            pending_confirmation=None,
        )

    tool_args = run.pending_tool_args or {}
    if tool_name not in WRITE_TOOLS:
        raise HTTPException(
            status_code=400, detail="Run does not reference a write tool"
        )

    if _write_is_high_risk(tool_name, tool_args):
        confirm_stage = int(tool_args.get("__confirm_stage") or 1)
        if confirm_stage < 2:
            tool_args = dict(tool_args)
            tool_args["__confirm_stage"] = 2
            run.pending_tool_args = tool_args
            run.assistant_message = _write_second_confirmation_message(
                tool_name, tool_args
            )
            await session.commit()
            return MessageResponse(
                assistant_message=run.assistant_message,
                run_id=str(run.id),
                tool_trace=run.tool_trace or [],
                pending_confirmation=PendingConfirmation(
                    run_id=str(run.id),
                    tool_name=tool_name,
                    tool_args=tool_args,
                    summary=_write_second_confirmation_message(tool_name, tool_args),
                ),
            )

    tool_trace = list(run.tool_trace or [])
    if _write_requires_verification(tool_name, tool_args):
        pre_write_verification = await _assistant_verify_sensitive_operation(
            phase="pre_write",
            tool_name=tool_name,
            tool_args=tool_args,
            conversation_id=conversation.id,
            session=session,
            assistant_mode=assistant_mode,
            openai_api_key=openai_api_key,
        )
        tool_trace.append({"verification_gate": pre_write_verification})
        if pre_write_verification.get("verdict") != "pass":
            blocked_message = _verification_blocking_message(
                tool_name,
                pre_write_verification,
            )
            run.status = "failed"
            run.tool_trace = tool_trace
            run.pending_tool_name = None
            run.pending_tool_args = None
            run.assistant_message = blocked_message
            conversation.updated_at = datetime.utcnow()
            await session.commit()
            return MessageResponse(
                assistant_message=blocked_message,
                run_id=str(run.id),
                tool_trace=tool_trace,
                pending_confirmation=None,
            )

    exec_tool_args = {k: v for k, v in tool_args.items() if not str(k).startswith("__")}
    result = await _execute_write_tool(
        tool_name,
        exec_tool_args,
        gastos_session=session,
        conversation_id=conversation.id,
        empleado_id=current_empleado.id,
        tournament_key_default=(
            (conversation.tournament_key or _assistant_default_tournament_key() or "")
            .strip()
            .lower()
            or None
        ),
    )
    tool_trace.append({"tool": tool_name, "result": result})

    prompt_user = (
        "La accion fue confirmada y ejecutada.\n"
        f"Herramienta: {tool_name}\n"
        f"Resultado (JSON): {json.dumps(result, ensure_ascii=False)}\n"
        "Redacta la respuesta final para el usuario."
    )
    normalized_mode = _normalize_assistant_mode(assistant_mode)
    follow_up_route = {
        "route": "code_agentic" if tool_name in DEV_WRITE_TOOLS else "agentic_write",
        "domain": (
            "code"
            if tool_name in DEV_WRITE_TOOLS
            else "tournament" if tool_name in TOURNAMENT_WRITE_TOOLS else "finance"
        ),
        "reason": "confirmed_write",
        "has_write_intent": bool(tool_name in WRITE_TOOLS),
        "has_code_change_intent": bool(tool_name in DEV_WRITE_TOOLS),
        "code_tooling_active": bool(tool_name in DEV_WRITE_TOOLS),
    }
    text_response = await _assistant_text_response(
        prompt_user=prompt_user,
        history_messages=await _history_messages(
            session, conversation_id=conversation.id, limit=20
        ),
        mode=normalized_mode,
        route_info=follow_up_route,
        openai_api_key=openai_api_key,
        max_tokens=900,
        system_prompts=[_assistant_system_prompt()],
    )
    answer = text_response.get("answer") or "Accion ejecutada."
    tool_trace.append(
        {
            "followup_llm": {
                "provider": text_response.get("provider"),
                "model": text_response.get("model"),
                "route": follow_up_route,
                "meta": text_response.get("meta") or {},
            }
        }
    )

    answer = _ensure_citations(
        answer if answer else "Accion ejecutada.",
        [{"label": f"tool:{tool_name}", "score": 1.0}],
    )
    if _write_requires_verification(tool_name, tool_args):
        post_write_verification = await _assistant_verify_sensitive_operation(
            phase="post_write_response",
            tool_name=tool_name,
            tool_args=tool_args,
            tool_result=result,
            proposed_answer=answer,
            conversation_id=conversation.id,
            session=session,
            assistant_mode=assistant_mode,
            openai_api_key=openai_api_key,
        )
        tool_trace.append({"response_verification": post_write_verification})
        if post_write_verification.get("verdict") != "pass":
            answer = _verification_safe_answer(
                tool_name=tool_name,
                tool_result=result,
                verification=post_write_verification,
            )
    answer = _maybe_append_export_prompt(answer, tool_trace)

    assistant_msg = AssistantMessage(
        conversation_id=conversation.id,
        role="assistant",
        content=answer,
        tool_name=None,
        tool_payload=None,
    )
    session.add(assistant_msg)

    run.status = "completed"
    run.tool_trace = tool_trace
    run.pending_tool_name = None
    run.pending_tool_args = None
    run.assistant_message = answer
    conversation.updated_at = datetime.utcnow()
    await session.commit()

    return MessageResponse(
        assistant_message=answer,
        run_id=str(run.id),
        tool_trace=tool_trace,
        pending_confirmation=None,
    )


def _verification_domain_for_tool(tool_name: str) -> str:
    normalized_tool = (tool_name or "").strip()
    if normalized_tool in DEV_READ_TOOLS or normalized_tool in DEV_WRITE_TOOLS:
        return "code"
    if (
        normalized_tool in TOURNAMENT_READ_TOOLS
        or normalized_tool in TOURNAMENT_WRITE_TOOLS
    ):
        return "tournament"
    return "finance"


def _verification_route_info(tool_name: str) -> Dict[str, Any]:
    domain = _verification_domain_for_tool(tool_name)
    return {
        "route": "code_agentic" if domain == "code" else "agentic_write",
        "domain": domain,
        "reason": "verification_gate",
        "allow_remote": True,
        "has_write_intent": True,
        "has_code_change_intent": bool((tool_name or "").strip() in DEV_WRITE_TOOLS),
        "code_tooling_active": bool((tool_name or "").strip() in DEV_WRITE_TOOLS),
    }


def _assistant_verification_system_prompt() -> str:
    return (
        "Eres un verificador independiente para operaciones sensibles de sam.chat.\n"
        "Objetivo: revisar exactitud, seguridad operativa y consistencia con la solicitud del usuario.\n"
        "Reglas:\n"
        "- Responde SOLO JSON valido.\n"
        '- Esquema obligatorio: {"verdict":"pass|partial|fail","summary":"...","blockers":[...],"warnings":[...]}.\n'
        "- Usa pass solo si la operación o respuesta es consistente, específica y segura.\n"
        "- Usa partial si faltan pruebas, contexto o precisión.\n"
        "- Usa fail si detectas contradicción, parámetro sospechoso, alcance excesivo o explicación engañosa.\n"
        "- No uses markdown, no uses bloques de código, no agregues texto fuera del JSON."
    )


def _json_excerpt(value: Any, *, max_chars: int = 4000) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        text = str(value)
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}…"


def _coerce_verification_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, list):
        items = value
    else:
        items = [str(value)]
    cleaned: List[str] = []
    for item in items:
        text = str(item or "").strip()
        if text:
            cleaned.append(text)
    return cleaned


def _parse_assistant_verification_response(raw_text: str) -> Dict[str, Any]:
    text = (raw_text or "").strip()
    payload: Dict[str, Any] = {}
    if text:
        candidates = [text]
        fenced = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
        candidates.extend(fenced)
        inline = re.findall(r"(\{.*\})", text, flags=re.DOTALL)
        candidates.extend(inline)
        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                payload = parsed
                break

    verdict = str(payload.get("verdict") or "").strip().lower()
    if verdict not in {"pass", "partial", "fail"}:
        match = re.search(r"\b(pass|partial|fail)\b", text.lower())
        verdict = match.group(1) if match else "partial"
    summary = str(payload.get("summary") or "").strip()
    if not summary:
        summary = (
            "Verificación sin objeciones materiales."
            if verdict == "pass"
            else "La verificación no pudo certificar la operación con suficiente confianza."
        )
    blockers = _coerce_verification_list(payload.get("blockers"))
    warnings = _coerce_verification_list(payload.get("warnings"))
    return {
        "verdict": verdict,
        "summary": summary,
        "blockers": blockers,
        "warnings": warnings,
        "raw": text,
    }


async def _assistant_verify_sensitive_operation(
    *,
    phase: Literal["pre_write", "post_write_response"],
    tool_name: str,
    tool_args: Dict[str, Any],
    conversation_id: uuid.UUID,
    session: AsyncSession,
    assistant_mode: Optional[str],
    openai_api_key: Optional[str],
    tool_result: Optional[Dict[str, Any]] = None,
    proposed_answer: Optional[str] = None,
) -> Dict[str, Any]:
    route_info = _verification_route_info(tool_name)
    history_messages = await _history_messages(
        session, conversation_id=conversation_id, limit=12
    )

    if phase == "pre_write":
        prompt_user = (
            "Verifica esta operación sensible ANTES de ejecutarla.\n"
            f"Herramienta: {tool_name}\n"
            f"Dominio: {_verification_domain_for_tool(tool_name)}\n"
            f"Argumentos JSON: {_json_excerpt(tool_args)}\n"
            "Evalúa si la acción es consistente con la intención del usuario, si los parámetros son específicos, "
            "y si el alcance parece seguro para ejecución."
        )
    else:
        prompt_user = (
            "Verifica esta respuesta final DESPUÉS de una operación sensible ya ejecutada.\n"
            f"Herramienta: {tool_name}\n"
            f"Argumentos JSON: {_json_excerpt(tool_args)}\n"
            f"Resultado JSON: {_json_excerpt(tool_result or {})}\n"
            f"Respuesta propuesta: {proposed_answer or ''}\n"
            "Evalúa si la explicación es fiel al resultado, si omite incertidumbres y si comunica el impacto real."
        )

    response = await _assistant_text_response(
        prompt_user=prompt_user,
        history_messages=history_messages,
        mode=_assistant_route_mode(route_info["route"], assistant_mode or "calidad"),
        route_info=route_info,
        openai_api_key=openai_api_key,
        max_tokens=700,
        system_prompts=[_assistant_verification_system_prompt()],
    )
    parsed = _parse_assistant_verification_response(response.get("answer") or "")
    parsed.update(
        {
            "phase": phase,
            "tool_name": tool_name,
            "provider": response.get("provider"),
            "model": response.get("model"),
            "meta": response.get("meta") or {},
        }
    )
    return parsed


def _verification_blocking_message(tool_name: str, verification: Dict[str, Any]) -> str:
    lines = [
        "La operación se bloqueó antes de ejecutarse por verificación de seguridad.",
        f"Herramienta: {tool_name}",
        f"Veredicto: {verification.get('verdict')}",
        f"Resumen: {verification.get('summary')}",
    ]
    blockers = _coerce_verification_list(verification.get("blockers"))
    warnings = _coerce_verification_list(verification.get("warnings"))
    if blockers:
        lines.append("Bloqueos:")
        lines.extend(f"- {item}" for item in blockers[:5])
    if warnings:
        lines.append("Advertencias:")
        lines.extend(f"- {item}" for item in warnings[:5])
    lines.append("Corrige los parámetros o vuelve a pedir la acción con más precisión.")
    return "\n".join(lines)


def _verification_safe_answer(
    *,
    tool_name: str,
    tool_result: Dict[str, Any],
    verification: Dict[str, Any],
) -> str:
    lines = [
        "La acción se ejecutó, pero la explicación automática quedó degradada por verificación.",
        f"Herramienta: {tool_name}",
        f"Veredicto de verificación: {verification.get('verdict')}",
        f"Resumen: {verification.get('summary')}",
        f"Resultado base (JSON): {_json_excerpt(tool_result, max_chars=2000)}",
    ]
    warnings = _coerce_verification_list(verification.get("warnings"))
    blockers = _coerce_verification_list(verification.get("blockers"))
    if blockers:
        lines.append("Riesgos detectados:")
        lines.extend(f"- {item}" for item in blockers[:5])
    elif warnings:
        lines.append("Advertencias:")
        lines.extend(f"- {item}" for item in warnings[:5])
    return "\n".join(lines)


def _report_csv_bytes(report: Dict[str, Any]) -> bytes:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["section", "metric", "value"])

    period = report.get("period") or {}
    totals = report.get("totals") or {}
    budget = report.get("budget") or {}
    projection = report.get("projection") or {}

    writer.writerow(["meta", "generated_at", report.get("generated_at") or ""])
    writer.writerow(["meta", "period_from", period.get("from") or ""])
    writer.writerow(["meta", "period_to", period.get("to") or ""])
    for key, value in totals.items():
        writer.writerow(["totals", key, value])
    for key, value in budget.items():
        writer.writerow(["budget", key, value])
    for key, value in projection.items():
        writer.writerow(["projection", key, value])

    breakdown = ((report.get("breakdown") or {}).get("items")) or []
    if isinstance(breakdown, list) and breakdown:
        headers = sorted(
            {k for row in breakdown if isinstance(row, dict) for k in row.keys()}
        )
        writer.writerow([])
        writer.writerow(["breakdown_headers", *headers])
        for row in breakdown:
            if isinstance(row, dict):
                writer.writerow(["breakdown", *[row.get(h, "") for h in headers]])

    trend = report.get("trend_monthly") or []
    if isinstance(trend, list) and trend:
        writer.writerow([])
        writer.writerow(["trend_headers", "month", "monto"])
        for row in trend:
            if isinstance(row, dict):
                writer.writerow(["trend", row.get("month", ""), row.get("monto", "")])

    yoy = report.get("comparison_yoy") or []
    if isinstance(yoy, list) and yoy:
        writer.writerow([])
        writer.writerow(
            [
                "yoy_headers",
                "year_offset",
                "period_from",
                "period_to",
                "total",
                "registros",
                "delta_vs_current_amount",
                "delta_vs_current_pct",
            ]
        )
        for row in yoy:
            if not isinstance(row, dict):
                continue
            period_row = row.get("period") or {}
            writer.writerow(
                [
                    "yoy",
                    row.get("year_offset", ""),
                    period_row.get("from", ""),
                    period_row.get("to", ""),
                    row.get("total", ""),
                    row.get("registros", ""),
                    row.get("delta_vs_current_amount", ""),
                    row.get("delta_vs_current_pct", ""),
                ]
            )

    return output.getvalue().encode("utf-8")


def _report_pdf_bytes(report: Dict[str, Any]) -> bytes:
    if pdf_canvas is None or A4 is None or colors is None:
        raise HTTPException(
            status_code=500, detail="PDF engine not available in backend"
        )
    buffer = io.BytesIO()
    c = pdf_canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    margin_x = 36
    top_margin = 36
    bottom_margin = 36
    header_h = 62
    footer_h = 24
    body_top = height - top_margin - header_h
    body_bottom = bottom_margin + footer_h
    y = body_top
    page_num = 1
    folio = (
        f"RPT-{datetime.utcnow().strftime('%Y%m%d%H%M')}-{uuid.uuid4().hex[:6].upper()}"
    )

    logo_path = Path(
        os.getenv(
            "ASSISTANT_REPORT_LOGO_PATH",
            "/root/samchat/goal-fest-page/src/assets/copa-club-america-2026-logo.png",
        )
    )
    logo_reader = None
    if ImageReader is not None and logo_path.exists():
        try:
            logo_reader = ImageReader(str(logo_path))
        except Exception:
            logo_reader = None

    def draw_header_footer() -> None:
        c.setFillColor(colors.HexColor("#0f172a"))
        c.rect(0, height - top_margin - header_h, width, header_h, fill=1, stroke=0)
        if logo_reader is not None:
            try:
                c.drawImage(
                    logo_reader,
                    margin_x,
                    height - top_margin - header_h + 10,
                    width=72,
                    height=42,
                    preserveAspectRatio=True,
                    mask="auto",
                )
            except Exception:
                pass
        c.setFillColor(colors.white)
        c.setFont("Helvetica-Bold", 14)
        c.drawString(
            margin_x + 82, height - top_margin - 26, "SAM.CHAT - REPORTE EJECUTIVO"
        )
        c.setFont("Helvetica", 9)
        c.drawString(margin_x + 82, height - top_margin - 40, f"Folio: {folio}")
        c.drawRightString(
            width - margin_x, height - top_margin - 26, f"Pagina {page_num}"
        )
        c.drawRightString(
            width - margin_x,
            height - top_margin - 40,
            f"Generado: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}",
        )

        c.setFillColor(colors.HexColor("#334155"))
        c.setFont("Helvetica", 8)
        c.drawString(
            margin_x,
            bottom_margin + 6,
            "Confidencial - Uso interno Fundacion TELMEX / sam.chat",
        )
        c.drawRightString(width - margin_x, bottom_margin + 6, folio)

    def next_page() -> None:
        nonlocal y, page_num
        c.showPage()
        page_num += 1
        draw_header_footer()
        y = body_top

    def ensure_space(lines: int = 1, line_h: int = 13) -> None:
        if y - (lines * line_h) < body_bottom:
            next_page()

    def write_text(
        text: str,
        *,
        size: int = 10,
        bold: bool = False,
        color=colors.HexColor("#111827"),
    ) -> None:
        nonlocal y
        ensure_space(1, size + 4)
        c.setFillColor(color)
        c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
        c.drawString(margin_x, y, text[:220])
        y -= size + 4

    def write_kpi_row(items: List[tuple[str, Any]]) -> None:
        nonlocal y
        card_h = 54
        gap = 10
        n = max(1, len(items))
        card_w = (width - (2 * margin_x) - (gap * (n - 1))) / n
        ensure_space(5, 12)
        for i, (label, value) in enumerate(items):
            x = margin_x + i * (card_w + gap)
            c.setFillColor(colors.HexColor("#f8fafc"))
            c.setStrokeColor(colors.HexColor("#cbd5e1"))
            c.roundRect(x, y - card_h + 6, card_w, card_h, 6, fill=1, stroke=1)
            c.setFillColor(colors.HexColor("#475569"))
            c.setFont("Helvetica", 8)
            c.drawString(x + 8, y - 10, str(label)[:40])
            c.setFillColor(colors.HexColor("#0f172a"))
            c.setFont("Helvetica-Bold", 12)
            c.drawString(x + 8, y - 28, str(value)[:36])
        y -= card_h + 8

    def write_table(
        title: str, rows: List[List[Any]], headers: List[str], max_rows: int = 18
    ) -> None:
        nonlocal y
        if not rows:
            return
        write_text(title, size=11, bold=True, color=colors.HexColor("#0f172a"))
        col_count = max(1, len(headers))
        table_w = width - (2 * margin_x)
        col_w = table_w / col_count
        header_h_local = 16
        row_h = 14

        def draw_header() -> None:
            nonlocal y
            ensure_space(2, header_h_local)
            c.setFillColor(colors.HexColor("#e2e8f0"))
            c.rect(
                margin_x,
                y - header_h_local + 3,
                table_w,
                header_h_local,
                fill=1,
                stroke=0,
            )
            c.setFillColor(colors.HexColor("#0f172a"))
            c.setFont("Helvetica-Bold", 8)
            for ci, h in enumerate(headers):
                c.drawString(margin_x + ci * col_w + 4, y - 8, str(h)[:24])
            y -= header_h_local

        draw_header()
        for ri, row in enumerate(rows[:max_rows]):
            if y - row_h < body_bottom:
                next_page()
                write_text(
                    title + " (cont.)",
                    size=11,
                    bold=True,
                    color=colors.HexColor("#0f172a"),
                )
                draw_header()
            if ri % 2 == 0:
                c.setFillColor(colors.HexColor("#f8fafc"))
                c.rect(margin_x, y - row_h + 2, table_w, row_h, fill=1, stroke=0)
            c.setFillColor(colors.HexColor("#1f2937"))
            c.setFont("Helvetica", 8)
            for ci, value in enumerate(row):
                c.drawString(margin_x + ci * col_w + 4, y - 8, str(value)[:24])
            y -= row_h
        y -= 6

    draw_header_footer()

    title = str(report.get("title") or "Reporte financiero en tiempo real")
    period = report.get("period") or {}
    write_text(title, size=14, bold=True, color=colors.HexColor("#0f172a"))
    write_text(
        f"Periodo: {period.get('from') or ''} a {period.get('to') or ''}",
        size=9,
        color=colors.HexColor("#475569"),
    )
    write_text("", size=2)

    totals = report.get("totals") if isinstance(report.get("totals"), dict) else {}
    budget = report.get("budget") if isinstance(report.get("budget"), dict) else {}
    projection = (
        report.get("projection") if isinstance(report.get("projection"), dict) else {}
    )
    kpis = [
        ("Gasto total", totals.get("gasto_total", 0)),
        ("Registros", totals.get("registros", 0)),
        ("Presupuesto", budget.get("budget_total", "N/D")),
        ("Varianza", budget.get("variance_amount", "N/D")),
    ]
    write_kpi_row(kpis)

    write_text(
        "Resumen ejecutivo", size=11, bold=True, color=colors.HexColor("#0f172a")
    )
    write_text(
        f"Proyeccion ({projection.get('mode', 'none')}): {projection.get('projected_total', 'N/D')}",
        size=9,
    )
    write_text(f"Desviacion % presupuesto: {budget.get('variance_pct', 'N/D')}", size=9)
    write_text("", size=2)

    # Keep PDF and CSV aligned: always include a base table with explicit column headers.
    summary_table_rows: List[List[Any]] = [
        ["meta", "generated_at", report.get("generated_at", "")],
        [
            "meta",
            "period_from",
            period.get("from", "") if isinstance(period, dict) else "",
        ],
        ["meta", "period_to", period.get("to", "") if isinstance(period, dict) else ""],
    ]
    for key, value in totals.items():
        summary_table_rows.append(["totals", key, value])
    for key, value in budget.items():
        summary_table_rows.append(["budget", key, value])
    for key, value in projection.items():
        summary_table_rows.append(["projection", key, value])
    write_table(
        "Resumen tabular",
        summary_table_rows,
        ["Seccion", "Metrica", "Valor"],
        max_rows=24,
    )

    breakdown = ((report.get("breakdown") or {}).get("items")) or []
    if isinstance(breakdown, list) and breakdown:
        sample = breakdown[:18]
        hdrs = sorted({k for r in sample if isinstance(r, dict) for k in r.keys()})[:5]
        table_rows = []
        for r in sample:
            if isinstance(r, dict):
                table_rows.append([r.get(h, "") for h in hdrs])
        write_table("Desglose principal", table_rows, hdrs, max_rows=18)

    yoy = report.get("comparison_yoy") or []
    if isinstance(yoy, list) and yoy:
        yoy_rows: List[List[Any]] = []
        for r in yoy[:8]:
            if not isinstance(r, dict):
                continue
            p = r.get("period") or {}
            yoy_rows.append(
                [
                    r.get("year_offset", ""),
                    f"{p.get('from', '')}..{p.get('to', '')}",
                    r.get("total", ""),
                    r.get("delta_vs_current_pct", ""),
                ]
            )
        write_table(
            "Comparativo anual",
            yoy_rows,
            ["Offset", "Periodo", "Total", "Delta %"],
            max_rows=8,
        )

    trend = report.get("trend_monthly") or []
    if isinstance(trend, list) and trend:
        trend_rows: List[List[Any]] = []
        for r in trend[:12]:
            if isinstance(r, dict):
                trend_rows.append([r.get("month", ""), r.get("monto", "")])
        write_table("Tendencia mensual", trend_rows, ["Mes", "Monto"], max_rows=12)

    c.save()
    return buffer.getvalue()


async def _build_executive_dashboard(
    *,
    session: AsyncSession,
    year: int,
    bi_scope: Optional[str],
    bi_segment: Optional[str],
) -> Dict[str, Any]:
    start_date = date(year, 1, 1)
    end_date = date(year, 12, 31)
    prev_start = date(year - 1, 1, 1)
    prev_end = date(year - 1, 12, 31)

    base_filters: List[Any] = [
        ExpenseReport.estado_gasto != "cancelado",
        func.date(ExpenseReport.fecha) >= start_date,
        func.date(ExpenseReport.fecha) <= end_date,
    ]
    scope_predicates = _expense_scope_predicates(bi_scope, bi_segment)
    if scope_predicates:
        base_filters.append(or_(*scope_predicates))

    totals_row = (
        await session.execute(
            select(
                func.coalesce(func.sum(ExpenseReport.gasto_cantidad), 0).label("total"),
                func.count(ExpenseReport.id).label("count"),
            ).where(*base_filters)
        )
    ).one()
    total = float(totals_row.total or 0)
    count = int(totals_row.count or 0)

    prev_filters: List[Any] = [
        ExpenseReport.estado_gasto != "cancelado",
        func.date(ExpenseReport.fecha) >= prev_start,
        func.date(ExpenseReport.fecha) <= prev_end,
    ]
    if scope_predicates:
        prev_filters.append(or_(*scope_predicates))
    prev_total = float(
        (
            await session.execute(
                select(func.coalesce(func.sum(ExpenseReport.gasto_cantidad), 0)).where(
                    *prev_filters
                )
            )
        ).scalar_one()
        or 0
    )
    yoy_pct = (
        round(((total - prev_total) / prev_total * 100.0), 2)
        if prev_total > 0
        else None
    )

    month_expr = func.date_trunc("month", ExpenseReport.fecha).label("month")
    month_rows = (
        await session.execute(
            select(
                month_expr,
                func.coalesce(func.sum(ExpenseReport.gasto_cantidad), 0).label("m"),
            )
            .where(*base_filters)
            .group_by(month_expr)
            .order_by(month_expr.asc())
        )
    ).all()
    monthly = [
        {
            "month": (
                row.month.date().isoformat()
                if isinstance(row.month, datetime)
                else str(row.month)
            ),
            "amount": round(float(row.m or 0), 2),
        }
        for row in month_rows
    ]
    run_rate_projection = round((total / max(1, datetime.utcnow().month)) * 12, 2)

    doc_filters: List[Any] = [
        Documento.tipo == "SOLICITUD",
        Documento.fecha_pago >= start_date,
        Documento.fecha_pago <= end_date,
    ]
    doc_scope_pred = _document_scope_predicates(bi_scope, bi_segment)
    if doc_scope_pred:
        doc_filters.append(or_(*doc_scope_pred))
    top_vendor_rows = (
        await session.execute(
            select(
                ProveedorCliente.nombre.label("vendor"),
                func.coalesce(func.sum(Documento.monto_total), 0).label("total"),
            )
            .select_from(Documento)
            .outerjoin(
                ProveedorCliente, Documento.proveedor_cliente_id == ProveedorCliente.id
            )
            .where(*doc_filters)
            .group_by(ProveedorCliente.nombre)
            .order_by(func.coalesce(func.sum(Documento.monto_total), 0).desc())
            .limit(10)
        )
    ).all()
    top_vendors = [
        {
            "vendor": str(row.vendor or "Sin proveedor"),
            "total": round(float(row.total or 0), 2),
        }
        for row in top_vendor_rows
    ]

    return {
        "generated_at": datetime.utcnow().isoformat(),
        "year": year,
        "scope": (bi_scope or "all"),
        "segment": (bi_segment or "all"),
        "kpis": {
            "expense_total": round(total, 2),
            "records": count,
            "prev_year_total": round(prev_total, 2),
            "yoy_pct": yoy_pct,
            "run_rate_projection": run_rate_projection,
        },
        "monthly_trend": monthly,
        "top_vendors": top_vendors,
    }


async def _build_automatic_alerts(
    *,
    session: AsyncSession,
    year: int,
    bi_scope: Optional[str],
    bi_segment: Optional[str],
    spike_ratio: float,
) -> Dict[str, Any]:
    dashboard = await _build_executive_dashboard(
        session=session,
        year=year,
        bi_scope=bi_scope,
        bi_segment=bi_segment,
    )
    alerts: List[Dict[str, Any]] = []
    monthly = dashboard.get("monthly_trend") or []
    monthly_amounts = [
        float(item.get("amount") or 0) for item in monthly if isinstance(item, dict)
    ]
    if len(monthly_amounts) >= 4:
        baseline = sum(monthly_amounts[-4:-1]) / 3.0
        current = monthly_amounts[-1]
        if baseline > 0 and current >= baseline * spike_ratio:
            alerts.append(
                {
                    "severity": "high",
                    "code": "monthly_spike",
                    "title": "Pico mensual de gasto",
                    "detail": (
                        f"Último mes {current:,.2f} vs promedio 3 meses previos {baseline:,.2f} "
                        f"(x{(current / baseline):.2f})."
                    ),
                }
            )

    top_vendors = dashboard.get("top_vendors") or []
    total_top = sum(
        float(v.get("total") or 0) for v in top_vendors if isinstance(v, dict)
    )
    if total_top > 0 and isinstance(top_vendors, list) and top_vendors:
        top = top_vendors[0]
        share = float(top.get("total") or 0) / total_top
        if share >= 0.45:
            alerts.append(
                {
                    "severity": "medium",
                    "code": "vendor_concentration",
                    "title": "Concentración alta en proveedor",
                    "detail": (
                        f"{top.get('vendor')}: {float(top.get('total') or 0):,.2f} "
                        f"({share * 100:.1f}% del top de pagos)."
                    ),
                }
            )

    high_expense_filters: List[Any] = [
        ExpenseReport.estado_gasto != "cancelado",
        func.date(ExpenseReport.fecha) >= date(year, 1, 1),
        func.date(ExpenseReport.fecha) <= date(year, 12, 31),
    ]
    scope_predicates = _expense_scope_predicates(bi_scope, bi_segment)
    if scope_predicates:
        high_expense_filters.append(or_(*scope_predicates))
    high_rows = (
        await session.execute(
            select(
                ExpenseReport.id,
                ExpenseReport.proyecto,
                ExpenseReport.concepto,
                ExpenseReport.gasto_cantidad,
                ExpenseReport.fecha,
            )
            .where(*high_expense_filters, ExpenseReport.gasto_cantidad >= 100000)
            .order_by(ExpenseReport.gasto_cantidad.desc())
            .limit(5)
        )
    ).all()
    for row in high_rows:
        alerts.append(
            {
                "severity": "medium",
                "code": "large_expense",
                "title": "Gasto individual alto",
                "detail": (
                    f"{float(row.gasto_cantidad or 0):,.2f} en {row.proyecto or 'N/A'} "
                    f"({row.concepto or 'sin concepto'}) "
                    f"{row.fecha.date().isoformat() if row.fecha else ''}".strip()
                ),
            }
        )

    return {
        "generated_at": datetime.utcnow().isoformat(),
        "year": year,
        "scope": (bi_scope or "all"),
        "segment": (bi_segment or "all"),
        "alerts": alerts,
    }


def _sync_fetch_json(
    url: str,
    headers: Dict[str, str],
    timeout: int = 12,
    method: str = "GET",
    payload: Optional[Dict[str, Any]] = None,
) -> Any:
    data_bytes: Optional[bytes] = None
    if payload is not None:
        data_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib_request.Request(
        url, headers=headers, method=method.upper(), data=data_bytes
    )
    try:
        with urllib_request.urlopen(req, timeout=timeout) as res:
            body = res.read().decode("utf-8", errors="replace")
    except urllib_error.HTTPError as exc:
        raise HTTPException(
            status_code=401, detail="Supabase auth rejected token"
        ) from exc
    except urllib_error.URLError as exc:
        raise HTTPException(
            status_code=502, detail="Supabase unreachable"
        ) from exc

    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=502, detail="Invalid JSON from Supabase auth endpoint"
        ) from exc


def _sync_fetch_bytes(
    url: str,
    headers: Dict[str, str],
    timeout: int = 30,
) -> bytes:
    req = urllib_request.Request(url, headers=headers, method="GET")
    try:
        with urllib_request.urlopen(req, timeout=timeout) as res:
            return res.read()
    except urllib_error.HTTPError as exc:
        raise HTTPException(
            status_code=exc.code if exc.code in {400, 401, 403, 404} else 502,
            detail="No se pudo descargar archivo privado",
        ) from exc
    except urllib_error.URLError as exc:
        raise HTTPException(
            status_code=502, detail="Supabase storage unreachable"
        ) from exc


def _sync_sendgrid_request(
    payload: Dict[str, Any],
    *,
    timeout: int = 20,
) -> Dict[str, Any]:
    api_key = (os.getenv("SENDGRID_API_KEY") or "").strip()
    if not api_key:
        raise HTTPException(
            status_code=500, detail="SENDGRID_API_KEY is not configured in backend"
        )

    req = urllib_request.Request(
        "https://api.sendgrid.com/v3/mail/send",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
    )
    try:
        with urllib_request.urlopen(req, timeout=timeout) as res:
            response_body = res.read().decode("utf-8", errors="replace")
            return {
                "status": int(getattr(res, "status", 202) or 202),
                "body": response_body,
            }
    except urllib_error.HTTPError as exc:
        raise HTTPException(
            status_code=502, detail="SendGrid rejected request"
        ) from exc
    except urllib_error.URLError as exc:
        raise HTTPException(
            status_code=502, detail="SendGrid unreachable"
        ) from exc


def _email_plain_text_from_html(html_content: str) -> str:
    raw = re.sub(r"(?is)<(script|style).*?>.*?</\\1>", " ", html_content or "")
    raw = re.sub(r"(?i)<br\\s*/?>", "\n", raw)
    raw = re.sub(r"(?i)</p\\s*>", "\n\n", raw)
    raw = re.sub(r"<[^>]+>", " ", raw)
    raw = html.unescape(raw)
    raw = re.sub(r"[ \t]+", " ", raw)
    raw = re.sub(r"\n{3,}", "\n\n", raw)
    return raw.strip()


def _email_payload_for_sendgrid(
    *,
    recipients: List[EmailRecipientRequest],
    subject: str,
    html_content: str,
    text_content: Optional[str],
) -> Dict[str, Any]:
    from_email = (os.getenv("SENDGRID_FROM_EMAIL") or "noreply@sam.chat").strip()
    from_name = (os.getenv("SENDGRID_FROM_NAME") or "Plataforma Sports").strip()
    return {
        "personalizations": [
            {
                "to": [
                    {
                        "email": recipient.email.strip(),
                        **(
                            {"name": recipient.name.strip()}
                            if (recipient.name or "").strip()
                            else {}
                        ),
                    }
                ]
            }
            for recipient in recipients
        ],
        "from": {
            "email": from_email,
            "name": from_name,
        },
        "subject": subject.strip(),
        "content": [
            {
                "type": "text/plain",
                "value": (
                    text_content or _email_plain_text_from_html(html_content)
                ).strip(),
            },
            {
                "type": "text/html",
                "value": html_content,
            },
        ],
    }


async def _send_email_campaign_now(
    *,
    recipients: List[EmailRecipientRequest],
    subject: str,
    html_content: str,
    text_content: Optional[str],
) -> Dict[str, Any]:
    if not recipients:
        raise HTTPException(
            status_code=400, detail="At least one recipient is required"
        )
    payload = _email_payload_for_sendgrid(
        recipients=recipients,
        subject=subject,
        html_content=html_content,
        text_content=text_content,
    )
    return await asyncio.to_thread(_sync_sendgrid_request, payload)


async def _load_supabase_user(access_token: str) -> Dict[str, Any]:
    base_url = _supabase_base_url()
    api_key = _supabase_api_key()
    if not base_url:
        raise HTTPException(
            status_code=500, detail="SUPABASE_URL is not configured in backend"
        )
    if not api_key:
        raise HTTPException(
            status_code=500, detail="SUPABASE API key is not configured in backend"
        )

    payload = await asyncio.to_thread(
        _sync_fetch_json,
        f"{base_url}/auth/v1/user",
        {"Authorization": f"Bearer {access_token}", "apikey": api_key},
    )
    if not isinstance(payload, dict):
        raise HTTPException(status_code=401, detail="Invalid Supabase user payload")
    return payload


async def _load_supabase_roles(user_id: str) -> List[str]:
    base_url = _supabase_base_url()
    service_key = (os.getenv("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not base_url or not service_key:
        return []

    user_id_q = urllib_parse.quote(user_id, safe="")
    url = f"{base_url}/rest/v1/user_roles" f"?select=role&user_id=eq.{user_id_q}"
    payload = await asyncio.to_thread(
        _sync_fetch_json,
        url,
        {
            "Authorization": f"Bearer {service_key}",
            "apikey": service_key,
            "Accept": "application/json",
        },
    )
    if not isinstance(payload, list):
        return []
    roles: List[str] = []
    for item in payload:
        role = str((item or {}).get("role") or "").strip().lower()
        if role:
            roles.append(role)
    return roles


async def _supabase_rest_ids(
    *,
    table: str,
    filters: Optional[Dict[str, str]] = None,
    limit: int = 5000,
) -> List[str]:
    base_url = _supabase_base_url()
    service_key = (os.getenv("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not base_url or not service_key:
        return []
    params: Dict[str, str] = {"select": "id", "limit": str(max(1, min(limit, 10000)))}
    if filters:
        params.update(filters)
    url = f"{base_url}/rest/v1/{table}?{urllib_parse.urlencode(params)}"
    payload = await asyncio.to_thread(
        _sync_fetch_json,
        url,
        {
            "Authorization": f"Bearer {service_key}",
            "apikey": service_key,
            "Accept": "application/json",
        },
    )
    if not isinstance(payload, list):
        return []
    return [
        str((r or {}).get("id") or "").strip() for r in payload if (r or {}).get("id")
    ]


async def _supabase_rest_rows(
    *,
    table: str,
    select_expr: str = "*",
    filters: Optional[Dict[str, str]] = None,
    order: Optional[str] = None,
    limit: int = 500,
) -> List[Dict[str, Any]]:
    base_url = _supabase_base_url()
    service_key = (os.getenv("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not base_url or not service_key:
        return []
    params: Dict[str, str] = {
        "select": select_expr,
        "limit": str(max(1, min(limit, 5000))),
    }
    if filters:
        params.update(filters)
    if order:
        params["order"] = order
    url = f"{base_url}/rest/v1/{table}?{urllib_parse.urlencode(params)}"
    payload = await asyncio.to_thread(
        _sync_fetch_json,
        url,
        {
            "Authorization": f"Bearer {service_key}",
            "apikey": service_key,
            "Accept": "application/json",
        },
    )
    return payload if isinstance(payload, list) else []


def _db_safe_ident(value: str) -> str:
    name = (value or "").strip().lower()
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", name):
        raise HTTPException(status_code=400, detail=f"Invalid identifier: {value}")
    return name


def _db_table_allowlist(default_tables: set[str], env_var: str) -> set[str]:
    raw = (os.getenv(env_var) or "").strip()
    if not raw:
        return set(default_tables)
    tables: set[str] = set()
    for item in raw.split(","):
        name = item.strip().lower()
        if not name:
            continue
        if not re.fullmatch(r"[a-z_][a-z0-9_]*", name):
            continue
        tables.add(name)
    return tables or set(default_tables)


def _db_validate_table(data_source: str, table: str) -> str:
    src = (data_source or "").strip().lower()
    tbl = _db_safe_ident(table)
    if src == "gastos":
        allowed = _db_table_allowlist(
            DEFAULT_GASTOS_DB_TABLES, "ASSISTANT_DB_GASTOS_ALLOWLIST"
        )
        if tbl not in allowed:
            raise HTTPException(
                status_code=400, detail=f"Table not allowed for gastos: {tbl}"
            )
        return tbl
    if src == "supabase":
        allowed = _db_table_allowlist(
            DEFAULT_SUPABASE_DB_TABLES, "ASSISTANT_DB_SUPABASE_ALLOWLIST"
        )
        if tbl not in allowed:
            raise HTTPException(
                status_code=400, detail=f"Table not allowed for supabase: {tbl}"
            )
        return tbl
    raise HTTPException(
        status_code=400, detail="data_source must be one of: gastos, supabase"
    )


def _db_json_value(value: Any) -> Any:
    if isinstance(value, (datetime,)):
        return value.isoformat()
    if isinstance(value, (date,)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [_db_json_value(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _db_json_value(v) for k, v in value.items()}
    return value


def _db_normalize_column_name(table: str, column: str) -> str:
    safe_table = _db_safe_ident(table)
    safe_column = _db_safe_ident(column)
    return _DB_COLUMN_ALIASES.get(safe_table, {}).get(safe_column, safe_column)


def _db_build_sql_where(
    *,
    table: str,
    filters: Optional[List[Dict[str, Any]]],
) -> tuple[str, Dict[str, Any]]:
    clauses: List[str] = []
    params: Dict[str, Any] = {}
    for idx, raw in enumerate(filters or []):
        if not isinstance(raw, dict):
            continue
        field = _db_normalize_column_name(table, str(raw.get("field") or ""))
        op = str(raw.get("op") or "").strip().lower()
        if op not in DB_FILTER_OPS:
            raise HTTPException(status_code=400, detail=f"Unsupported filter op: {op}")
        param_key = f"p{idx}"
        if op == "is_null":
            value = bool(raw.get("value", True))
            clauses.append(f"{field} IS {'NULL' if value else 'NOT NULL'}")
            continue
        if op == "in":
            seq = raw.get("value")
            if not isinstance(seq, list) or not seq:
                raise HTTPException(
                    status_code=400,
                    detail=f"Filter {field} with op=in requires non-empty array",
                )
            placeholder = ", ".join([f":{param_key}_{i}" for i in range(len(seq))])
            clauses.append(f"{field} IN ({placeholder})")
            for i, item in enumerate(seq):
                params[f"{param_key}_{i}"] = item
            continue
        op_map = {
            "eq": "=",
            "ilike": "ILIKE",
            "gt": ">",
            "gte": ">=",
            "lt": "<",
            "lte": "<=",
        }
        clauses.append(f"{field} {op_map[op]} :{param_key}")
        params[param_key] = raw.get("value")
    return (" AND ".join(clauses), params)


def _db_build_supabase_filters(
    *,
    table: str,
    filters: Optional[List[Dict[str, Any]]],
) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for raw in filters or []:
        if not isinstance(raw, dict):
            continue
        field = _db_normalize_column_name(table, str(raw.get("field") or ""))
        op = str(raw.get("op") or "").strip().lower()
        if op not in DB_FILTER_OPS:
            raise HTTPException(status_code=400, detail=f"Unsupported filter op: {op}")
        value = raw.get("value")
        if op == "is_null":
            out[field] = (
                "is.null"
                if bool(value if value is not None else True)
                else "not.is.null"
            )
        elif op == "in":
            if not isinstance(value, list) or not value:
                raise HTTPException(
                    status_code=400,
                    detail=f"Filter {field} with op=in requires non-empty array",
                )
            encoded = ",".join([str(v) for v in value])
            out[field] = f"in.({encoded})"
        else:
            if value is None:
                raise HTTPException(
                    status_code=400,
                    detail=f"Filter {field} with op={op} requires value",
                )
            out[field] = f"{op}.{value}"
    return out


async def _db_read_universal(
    *,
    gastos_session: AsyncSession,
    data_source: str,
    table: str,
    columns: Optional[List[str]] = None,
    filters: Optional[List[Dict[str, Any]]] = None,
    order_by: Optional[str] = None,
    order_dir: str = "desc",
    limit: int = 100,
) -> Dict[str, Any]:
    src = (data_source or "").strip().lower()
    tbl = _db_validate_table(src, table)
    safe_limit = max(1, min(int(limit or 100), 500))
    order_direction = (
        "ASC" if str(order_dir or "desc").strip().lower() == "asc" else "DESC"
    )

    if columns:
        safe_cols = [
            _db_normalize_column_name(tbl, str(c)) for c in columns if str(c).strip()
        ]
        select_cols = ", ".join(safe_cols) if safe_cols else "*"
    else:
        select_cols = "*"

    if src == "gastos":
        where_sql, params = _db_build_sql_where(table=tbl, filters=filters)
        sql = f"SELECT {select_cols} FROM {tbl}"
        if where_sql:
            sql += f" WHERE {where_sql}"
        if order_by:
            sql += (
                f" ORDER BY {_db_normalize_column_name(tbl, order_by)} "
                f"{order_direction}"
            )
        sql += " LIMIT :limit"
        params["limit"] = safe_limit
        result = await gastos_session.execute(text(sql), params)
        rows = [dict(row) for row in result.mappings().all()]
        return {
            "data_source": src,
            "table": tbl,
            "rows": [_db_json_value(r) for r in rows],
            "count": len(rows),
            "limit": safe_limit,
        }

    filter_params = _db_build_supabase_filters(table=tbl, filters=filters)
    rows = await _supabase_rest_rows(
        table=tbl,
        select_expr=select_cols,
        filters=filter_params or None,
        order=(
            f"{_db_normalize_column_name(tbl, order_by)}.{order_direction.lower()}"
            if order_by
            else None
        ),
        limit=safe_limit,
    )
    return {
        "data_source": src,
        "table": tbl,
        "rows": [_db_json_value(r) for r in rows],
        "count": len(rows),
        "limit": safe_limit,
    }


async def _supabase_rest_mutate(
    *,
    table: str,
    method: str,
    payload: Optional[Any] = None,
    filters: Optional[Dict[str, str]] = None,
) -> Any:
    base_url = _supabase_base_url()
    service_key = (os.getenv("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not base_url or not service_key:
        raise HTTPException(
            status_code=500, detail="Supabase service key is not configured"
        )
    params = urllib_parse.urlencode(filters or {})
    url = f"{base_url}/rest/v1/{table}"
    if params:
        url += f"?{params}"
    return await asyncio.to_thread(
        _sync_fetch_json,
        url,
        {
            "Authorization": f"Bearer {service_key}",
            "apikey": service_key,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        },
        18,
        method.upper(),
        payload,
    )


def _invitation_code_candidate(length: int = 6) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(max(4, length)))


async def _generate_invitation_codes(*, quantity: int) -> List[str]:
    seen: set[str] = set()
    codes: List[str] = []
    attempts = 0
    max_attempts = max(25, quantity * 20)

    while len(codes) < quantity and attempts < max_attempts:
        attempts += 1
        candidate = _invitation_code_candidate(6)
        if candidate in seen:
            continue
        rows = await _supabase_rest_rows(
            table="invitations",
            select_expr="id,code",
            filters={"code": f"eq.{candidate}"},
            limit=1,
        )
        if rows:
            continue
        seen.add(candidate)
        codes.append(candidate)

    if len(codes) != quantity:
        raise HTTPException(
            status_code=500,
            detail="No se pudieron generar codigos unicos de invitacion",
        )
    return codes


TOURNAMENT_MUTABLE_COLUMNS = {
    "name",
    "slug",
    "description",
    "logo_url",
    "start_date",
    "end_date",
    "registration_deadline",
    "registration_start_date",
    "collective_phase_date",
    "collective_phase_end_date",
    "state_phase_date",
    "state_phase_end_date",
    "national_phase_date",
    "national_phase_end_date",
    "national_draw_date",
    "classification_game_1_date",
    "classification_game_2_date",
    "classification_game_3_date",
    "classification_game_4_date",
    "quarterfinals_date",
    "quarterfinals_end_date",
    "semifinals_date",
    "semifinals_end_date",
    "final_date",
    "is_active",
    "public_site_domain",
    "public_site_repository_url",
    "public_site_repository_branch",
    "public_site_notes",
}

TOURNAMENT_CONFIG_MUTABLE_COLUMNS = {
    "registration_enabled",
    "registration_fee",
    "max_teams_per_category",
    "min_players_per_team",
    "max_players_per_team",
    "early_bird_discount",
    "early_bird_deadline",
    "payment_enabled",
    "payment_methods",
    "bank_account_name",
    "bank_account_number",
    "bank_name",
    "bank_clabe",
    "payment_instructions",
    "require_birth_certificate",
    "require_curp",
    "require_photo",
    "require_medical_certificate",
    "send_confirmation_email",
    "send_payment_reminder",
}


def _clean_tournament_payload(raw: Dict[str, Any]) -> Dict[str, Any]:
    payload = {k: raw.get(k) for k in TOURNAMENT_MUTABLE_COLUMNS if k in raw}
    for key, value in list(payload.items()):
        if isinstance(value, str):
            payload[key] = value.strip() or None
    name = str(payload.get("name") or "").strip()
    slug = str(payload.get("slug") or "").strip()
    if not name or not slug:
        raise HTTPException(status_code=400, detail="name and slug are required")
    payload["name"] = name
    payload["slug"] = slug
    payload["is_active"] = bool(payload.get("is_active", True))
    return payload


def _clean_tournament_config_payload(
    raw: Optional[Dict[str, Any]], *, tournament_id: str
) -> Optional[Dict[str, Any]]:
    if not raw:
        return None
    payload = {k: raw.get(k) for k in TOURNAMENT_CONFIG_MUTABLE_COLUMNS if k in raw}
    if not payload:
        return None
    for key, value in list(payload.items()):
        if isinstance(value, str):
            payload[key] = value.strip() or None
    payload["tournament_id"] = tournament_id
    return payload


async def _save_tournament_config(payload: Optional[Dict[str, Any]]) -> None:
    if not payload:
        return
    tournament_id = str(payload.get("tournament_id") or "")
    existing = await _supabase_rest_rows(
        table="tournament_config",
        select_expr="id",
        filters={"tournament_id": f"eq.{tournament_id}"},
        limit=1,
    )
    if existing:
        payload["updated_at"] = datetime.utcnow().isoformat()
        await _supabase_rest_mutate(
            table="tournament_config",
            method="PATCH",
            payload=payload,
            filters={"tournament_id": f"eq.{tournament_id}"},
        )
        return
    await _supabase_rest_mutate(
        table="tournament_config",
        method="POST",
        payload=payload,
    )


async def _db_write_universal(
    *,
    gastos_session: AsyncSession,
    data_source: str,
    table: str,
    action: str,
    values: Optional[Dict[str, Any]] = None,
    filters: Optional[List[Dict[str, Any]]] = None,
    max_affected: int = 200,
) -> Dict[str, Any]:
    src = (data_source or "").strip().lower()
    tbl = _db_validate_table(src, table)
    _validate_db_write_target(data_source=src, table=tbl)
    op = (action or "").strip().lower()
    if op not in {"insert", "update", "delete"}:
        raise HTTPException(
            status_code=400, detail="action must be one of: insert, update, delete"
        )
    safe_max = max(1, min(int(max_affected or 200), 1000))

    if src == "gastos":
        if op == "insert":
            data = values or {}
            if not isinstance(data, dict) or not data:
                raise HTTPException(
                    status_code=400, detail="insert requires non-empty values object"
                )
            cols = [_db_safe_ident(k) for k in data.keys()]
            placeholders = [f":v_{i}" for i in range(len(cols))]
            params = {f"v_{i}": data[k] for i, k in enumerate(data.keys())}
            sql = f"INSERT INTO {tbl} ({', '.join(cols)}) VALUES ({', '.join(placeholders)}) RETURNING *"
            result = await gastos_session.execute(text(sql), params)
            rows = [dict(r) for r in result.mappings().all()]
            return {
                "data_source": src,
                "table": tbl,
                "action": op,
                "affected": len(rows),
                "rows": [_db_json_value(r) for r in rows],
            }

        where_sql, where_params = _db_build_sql_where(table=tbl, filters=filters)
        if not where_sql:
            raise HTTPException(
                status_code=400,
                detail=f"{op} requires filters to avoid mass updates/deletes",
            )
        if op == "update":
            data = values or {}
            if not isinstance(data, dict) or not data:
                raise HTTPException(
                    status_code=400, detail="update requires non-empty values object"
                )
            sets: List[str] = []
            params = dict(where_params)
            for idx, (k, v) in enumerate(data.items()):
                col = _db_safe_ident(k)
                pkey = f"s_{idx}"
                sets.append(f"{col} = :{pkey}")
                params[pkey] = v
            params["limit"] = safe_max
            sql = (
                f"UPDATE {tbl} SET {', '.join(sets)} "
                f"WHERE ctid IN (SELECT ctid FROM {tbl} WHERE {where_sql} LIMIT :limit) "
                "RETURNING *"
            )
            result = await gastos_session.execute(text(sql), params)
            rows = [dict(r) for r in result.mappings().all()]
            return {
                "data_source": src,
                "table": tbl,
                "action": op,
                "affected": len(rows),
                "rows": [_db_json_value(r) for r in rows],
            }

        params = dict(where_params)
        params["limit"] = safe_max
        sql = (
            f"DELETE FROM {tbl} WHERE ctid IN "
            f"(SELECT ctid FROM {tbl} WHERE {where_sql} LIMIT :limit) RETURNING *"
        )
        result = await gastos_session.execute(text(sql), params)
        rows = [dict(r) for r in result.mappings().all()]
        return {
            "data_source": src,
            "table": tbl,
            "action": op,
            "affected": len(rows),
            "rows": [_db_json_value(r) for r in rows],
        }

    filter_params = _db_build_supabase_filters(table=tbl, filters=filters)
    if op in {"update", "delete"} and not filter_params:
        raise HTTPException(
            status_code=400,
            detail=f"{op} requires filters to avoid mass updates/deletes",
        )
    if op == "insert":
        payload = values or {}
        if not isinstance(payload, dict) or not payload:
            raise HTTPException(
                status_code=400, detail="insert requires non-empty values object"
            )
        rows = await _supabase_rest_mutate(table=tbl, method="POST", payload=payload)
    elif op == "update":
        payload = values or {}
        if not isinstance(payload, dict) or not payload:
            raise HTTPException(
                status_code=400, detail="update requires non-empty values object"
            )
        rows = await _supabase_rest_mutate(
            table=tbl, method="PATCH", payload=payload, filters=filter_params
        )
    else:
        rows = await _supabase_rest_mutate(
            table=tbl, method="DELETE", filters=filter_params
        )

    if isinstance(rows, list) and len(rows) > safe_max:
        rows = rows[:safe_max]
    affected = len(rows) if isinstance(rows, list) else 0
    return {
        "data_source": src,
        "table": tbl,
        "action": op,
        "affected": affected,
        "rows": _db_json_value(rows if isinstance(rows, list) else []),
    }


def _workspace_scope_terms(scope: Optional[str]) -> List[str]:
    normalized = (scope or "").strip().lower()
    if normalized == "beisbol":
        return ["beisbol", "béisbol", "liga telmex"]
    return []


def _expense_scope_predicates(
    scope: Optional[str], segment: Optional[str]
) -> List[Any]:
    terms = _workspace_scope_terms(scope)
    if segment and segment.strip():
        terms.append(segment.strip().lower())
    predicates: List[Any] = []
    for term in terms:
        like = f"%{term}%"
        predicates.extend(
            [
                ExpenseReport.proyecto.ilike(like),
                ExpenseReport.concepto.ilike(like),
                ExpenseReport.fase_torneo.ilike(like),
                ExpenseReport.departamento.ilike(like),
            ]
        )
    return predicates


def _document_scope_predicates(
    scope: Optional[str], segment: Optional[str]
) -> List[Any]:
    terms = _workspace_scope_terms(scope)
    if segment and segment.strip():
        terms.append(segment.strip().lower())
    predicates: List[Any] = []
    for term in terms:
        like = f"%{term}%"
        predicates.extend(
            [
                Documento.concepto_pago.ilike(like),
                Documento.notas.ilike(like),
                ProveedorCliente.nombre.ilike(like),
            ]
        )
    return predicates


def _workspace_matches_scope(name: str, slug: str, scope: Optional[str]) -> bool:
    normalized_scope = (scope or "").strip().lower()
    if not normalized_scope or normalized_scope == "all":
        return True
    haystack = f"{name} {slug}".strip().lower()
    terms = _workspace_scope_terms(normalized_scope)
    return any(term in haystack for term in terms)


def _workspace_matches_segment(name: str, slug: str, segment: Optional[str]) -> bool:
    normalized = (segment or "").strip().lower()
    if not normalized or normalized == "all":
        return True
    haystack = f"{name} {slug}".strip().lower()
    if normalized == "9-10":
        return bool(
            re.search(r"(^|[^0-9])9\s*[-/]\s*10([^0-9]|$)|9\s*y\s*10", haystack)
        )
    if normalized == "11-12":
        return bool(
            re.search(r"(^|[^0-9])11\s*[-/]\s*12([^0-9]|$)|11\s*y\s*12", haystack)
        )
    return normalized in haystack


def _workspace_coerce_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError, ValueError):
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


def _workspace_tournament_key_terms(tournament_key: Optional[str]) -> List[str]:
    key = (tournament_key or "").strip().lower().replace("-", "_")
    if key in {"beisbol", "baseball"}:
        return ["beisbol", "béisbol", "liga telmex"]
    return []


def _workspace_entity_matches_segment(
    entity_key: str, entity_data: Dict[str, Any], segment: str
) -> bool:
    normalized = (segment or "").strip().lower()
    if not normalized or normalized == "all":
        return True
    if normalized in {"nacional", "national"}:
        return False
    haystack = " ".join(
        [
            entity_key,
            str(entity_data.get("entity_name") or ""),
            str(entity_data.get("category_gender_expected") or ""),
        ]
    ).lower()
    return normalized in haystack


def _workspace_compact_fields(
    payload: Dict[str, Any], max_fields: int = 16
) -> List[str]:
    lines: List[str] = []
    for key, raw_value in payload.items():
        value = str(raw_value or "").strip()
        if not value:
            continue
        lines.append(f"{key}: {value}")
        if len(lines) >= max_fields:
            break
    return lines


async def _build_workspace_context(
    *,
    raw_message: str,
    tournament_key_default: Optional[str],
    bi_scope: Optional[str],
    bi_segment: Optional[str],
) -> Optional[str]:
    tournaments = await _supabase_rest_rows(
        table="tournaments",
        select_expr="id,name,slug,is_active",
        limit=300,
    )
    if not tournaments:
        return None

    candidate_rows: List[Dict[str, Any]] = []
    key_terms = _workspace_tournament_key_terms(tournament_key_default)
    normalized_scope = (bi_scope or "").strip().lower()
    normalized_segment = (bi_segment or "").strip().lower()

    for row in tournaments:
        tid = str((row or {}).get("id") or "").strip()
        if not tid:
            continue
        name = str((row or {}).get("name") or "").strip()
        slug = str((row or {}).get("slug") or "").strip()
        if not _workspace_matches_scope(name, slug, normalized_scope):
            continue
        if not _workspace_matches_segment(name, slug, normalized_segment):
            continue
        if key_terms:
            haystack = f"{name} {slug}".lower()
            if not any(term in haystack for term in key_terms):
                continue
        candidate_rows.append(row)

    if not candidate_rows and key_terms:
        for row in tournaments:
            name = str((row or {}).get("name") or "").strip()
            slug = str((row or {}).get("slug") or "").strip()
            haystack = f"{name} {slug}".lower()
            if any(term in haystack for term in key_terms):
                candidate_rows.append(row)

    if not candidate_rows:
        return None

    max_tournaments = max(
        1, min(int(os.getenv("ASSISTANT_WORKSPACE_MAX_TOURNAMENTS", "4")), 10)
    )
    selected_rows = candidate_rows[:max_tournaments]
    tournament_ids = [str((row or {}).get("id") or "").strip() for row in selected_rows]
    in_filter = f"in.({','.join(tournament_ids)})"

    config_rows = await _supabase_rest_rows(
        table="tournament_config",
        select_expr="tournament_id,payment_methods,updated_at",
        filters={"tournament_id": in_filter},
        limit=max_tournaments * 5,
    )
    config_map: Dict[str, Dict[str, Any]] = {}
    for row in config_rows:
        tid = str((row or {}).get("tournament_id") or "").strip()
        if tid and tid not in config_map:
            config_map[tid] = row

    entity_profile_rows = await _supabase_rest_rows(
        table="tournament_entity_profiles",
        select_expr=(
            "tournament_id,entity_name,ps_responsible_name,entity_responsible_name,"
            "entity_responsible_phone,entity_responsible_email,state_phase_description,"
            "uniform_delivery_date,uniform_delivery_place,national_travel_departure_date,"
            "national_travel_return_date,operations_notes,updated_at"
        ),
        filters={"tournament_id": in_filter},
        order="entity_name.asc",
        limit=max_tournaments * 200,
    )
    entity_profiles_by_tournament: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in entity_profile_rows:
        tid = str((row or {}).get("tournament_id") or "").strip()
        if tid:
            entity_profiles_by_tournament[tid].append(row)

    national_profile_rows = await _supabase_rest_rows(
        table="tournament_national_phase_profiles",
        select_expr=(
            "tournament_id,tournament_category_dates_duration_city,hotels_and_bed_nights,"
            "meals_breakdown,sports_unit_venue,courts_count_and_types,"
            "medical_services_description,accidents_with_transfers,staff_travel_costs,"
            "hotel_payments_advance_settlement,supplier_payments_for_finals,"
            "medical_services_costs,insurance_costs,on_site_brand_activation_suppliers,"
            "sponsor_related_visitors,marketing_activity_reports_with_photos,updated_at"
        ),
        filters={"tournament_id": in_filter},
        limit=max_tournaments * 5,
    )
    national_profile_by_tournament: Dict[str, Dict[str, Any]] = {}
    for row in national_profile_rows:
        tid = str((row or {}).get("tournament_id") or "").strip()
        if tid and tid not in national_profile_by_tournament:
            national_profile_by_tournament[tid] = row

    team_rows = await _supabase_rest_rows(
        table="teams",
        select_expr="id,tournament_id,team_name,academy_name,state,municipality,status",
        filters={"tournament_id": in_filter},
        order="state.asc",
        limit=max_tournaments * 1000,
    )
    category_rows_for_tournaments = await _supabase_rest_rows(
        table="categories",
        select_expr="id,tournament_id,name,branch",
        filters={"tournament_id": in_filter},
        limit=max_tournaments * 1000,
    )
    category_tournament_by_id: Dict[str, str] = {}
    for row in category_rows_for_tournaments:
        category_id = str((row or {}).get("id") or "").strip()
        tid = str((row or {}).get("tournament_id") or "").strip()
        if category_id and tid:
            category_tournament_by_id[category_id] = tid

    linked_registration_rows: List[Dict[str, Any]] = []
    if category_tournament_by_id:
        linked_registration_rows = await _supabase_rest_rows(
            table="registrations",
            select_expr="id,team_id,category_id,payment_status",
            filters={
                "category_id": f"in.({','.join(category_tournament_by_id.keys())})"
            },
            limit=5000,
        )
        linked_team_ids = sorted(
            {
                str((row or {}).get("team_id") or "").strip()
                for row in linked_registration_rows
                if (row or {}).get("team_id")
            }
        )
        if linked_team_ids:
            linked_team_rows = await _supabase_rest_rows(
                table="teams",
                select_expr="id,tournament_id,team_name,academy_name,state,municipality,status",
                filters={"id": f"in.({','.join(linked_team_ids[:1000])})"},
                order="state.asc",
                limit=1000,
            )
            existing_team_ids = {
                str((row or {}).get("id") or "").strip() for row in team_rows
            }
            for row in linked_team_rows:
                team_id = str((row or {}).get("id") or "").strip()
                if team_id and team_id not in existing_team_ids:
                    team_rows.append(row)

    teams_by_tournament: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    team_tournament_by_id: Dict[str, str] = {}
    for row in team_rows:
        tid = str((row or {}).get("tournament_id") or "").strip()
        team_id = str((row or {}).get("id") or "").strip()
        if not tid and team_id:
            linked_categories = [
                str((registration or {}).get("category_id") or "").strip()
                for registration in linked_registration_rows
                if str((registration or {}).get("team_id") or "").strip() == team_id
            ]
            for category_id in linked_categories:
                tid = category_tournament_by_id.get(category_id, "")
                if tid:
                    break
        if tid:
            teams_by_tournament[tid].append(row)
        if team_id and tid:
            team_tournament_by_id[team_id] = tid

    team_ids = list(team_tournament_by_id.keys())
    registrations_by_team: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    players_by_registration: Dict[str, int] = defaultdict(int)
    categories_by_id: Dict[str, Dict[str, Any]] = {}
    if team_ids:
        team_id_filter = f"in.({','.join(team_ids[:1000])})"
        registration_rows = await _supabase_rest_rows(
            table="registrations",
            select_expr="id,team_id,category_id,payment_status",
            filters={"team_id": team_id_filter},
            limit=5000,
        )
        if linked_registration_rows:
            seen_registration_ids = {
                str((row or {}).get("id") or "").strip() for row in registration_rows
            }
            for row in linked_registration_rows:
                registration_id = str((row or {}).get("id") or "").strip()
                team_id = str((row or {}).get("team_id") or "").strip()
                if (
                    registration_id
                    and registration_id not in seen_registration_ids
                    and team_id in team_tournament_by_id
                ):
                    registration_rows.append(row)
        registration_ids: List[str] = []
        category_ids: set[str] = set()
        for row in registration_rows:
            team_id = str((row or {}).get("team_id") or "").strip()
            registration_id = str((row or {}).get("id") or "").strip()
            category_id = str((row or {}).get("category_id") or "").strip()
            if team_id:
                registrations_by_team[team_id].append(row)
            if registration_id:
                registration_ids.append(registration_id)
            if category_id:
                category_ids.add(category_id)

        if category_ids:
            category_rows = await _supabase_rest_rows(
                table="categories",
                select_expr="id,tournament_id,name,branch",
                filters={"id": f"in.({','.join(sorted(category_ids))})"},
                limit=5000,
            )
            categories_by_id = {
                str((row or {}).get("id") or "").strip(): row
                for row in category_rows
                if (row or {}).get("id")
            }

        if registration_ids:
            player_rows = await _supabase_rest_rows(
                table="players",
                select_expr="id,registration_id",
                filters={
                    "registration_id": f"in.({','.join(registration_ids[:5000])})"
                },
                limit=5000,
            )
            for row in player_rows:
                registration_id = str((row or {}).get("registration_id") or "").strip()
                if registration_id:
                    players_by_registration[registration_id] += 1

    query_tokens = set(_extract_tokens(raw_message))
    max_entities = max(
        1, min(int(os.getenv("ASSISTANT_WORKSPACE_MAX_ENTITIES", "12")), 50)
    )
    lines: List[str] = [
        "Contexto de expedientes operativos (fuentes: equipos/registros/jugadores, tournament_entity_profiles, tournament_national_phase_profiles y ai_workspace legado):"
    ]

    for trow in selected_rows:
        tid = str((trow or {}).get("id") or "").strip()
        if not tid:
            continue
        cfg = config_map.get(tid)
        payment_methods = _workspace_coerce_dict(
            (cfg or {}).get("payment_methods") if cfg else {}
        )
        workspace = _workspace_coerce_dict(payment_methods.get("ai_workspace"))
        entity_profiles = entity_profiles_by_tournament.get(tid, [])
        national_profile = national_profile_by_tournament.get(tid, {})

        name = str((trow or {}).get("name") or "").strip()
        slug = str((trow or {}).get("slug") or "").strip()
        lines.append(f"\n[Torneo] {name} (slug={slug})")

        tournament_teams = teams_by_tournament.get(tid, [])
        if tournament_teams:
            entity_summary: Dict[str, Dict[str, Any]] = defaultdict(
                lambda: {"teams": 0, "players": 0, "categories": set()}
            )
            for team in tournament_teams:
                entity_name = str(team.get("state") or "").strip() or "Sin entidad"
                team_id = str(team.get("id") or "").strip()
                entity_summary[entity_name]["teams"] += 1
                for registration in registrations_by_team.get(team_id, []):
                    registration_id = str((registration or {}).get("id") or "").strip()
                    category_id = str(
                        (registration or {}).get("category_id") or ""
                    ).strip()
                    entity_summary[entity_name][
                        "players"
                    ] += players_by_registration.get(registration_id, 0)
                    category = categories_by_id.get(category_id) or {}
                    category_label = " / ".join(
                        str(value)
                        for value in (category.get("name"), category.get("branch"))
                        if value
                    )
                    if category_label:
                        entity_summary[entity_name]["categories"].add(category_label)

            lines.append(
                "Expediente operativo derivado de equipos/registros/jugadores:"
            )
            for entity_name, summary in sorted(entity_summary.items()):
                categories = sorted(summary["categories"])
                category_text = (
                    ", ".join(categories[:8]) if categories else "sin categoria"
                )
                lines.append(
                    f"- {entity_name}: {summary['teams']} equipos, "
                    f"{summary['players']} jugadores, categorias={category_text}"
                )
        else:
            lines.append(
                "Expediente operativo derivado de equipos/registros/jugadores: sin equipos o jugadores ligados todavia."
            )

        entities = _workspace_coerce_dict(workspace.get("entities"))
        entity_items: List[tuple[str, Dict[str, Any], float]] = []
        for key, raw_entity in entities.items():
            entity_data = _workspace_coerce_dict(raw_entity)
            if normalized_segment and normalized_segment not in {
                "all",
                "nacional",
                "national",
            }:
                if not _workspace_entity_matches_segment(
                    str(key), entity_data, normalized_segment
                ):
                    continue
            haystack = " ".join(
                [str(key), json.dumps(entity_data, ensure_ascii=False)]
            ).lower()
            score = 0.0
            if query_tokens:
                score = sum(1.0 for token in query_tokens if token in haystack)
            entity_items.append((str(key), entity_data, score))

        entity_items.sort(key=lambda item: item[2], reverse=True)
        if entity_items:
            lines.append("Entidades:")
            shown_entities = 0
            for entity_key, entity_data, _ in entity_items:
                if shown_entities >= max_entities:
                    break
                display_name = str(entity_data.get("entity_name") or entity_key)
                lines.append(f"- {display_name} [{entity_key}]")
                for field in _workspace_compact_fields(entity_data):
                    lines.append(f"  - {field}")
                shown_entities += 1
            if len(entity_items) > shown_entities:
                lines.append(
                    f"  - ...(entidades truncadas: {len(entity_items) - shown_entities})"
                )
        else:
            lines.append("Entidades: sin datos.")

        if entity_profiles:
            lines.append(
                "Expedientes por entidad (fuente: tournament_entity_profiles):"
            )
            shown_profiles = 0
            for profile in entity_profiles:
                if shown_profiles >= max_entities:
                    break
                entity_name = str(profile.get("entity_name") or "").strip()
                if normalized_segment and normalized_segment not in {
                    "all",
                    "nacional",
                    "national",
                }:
                    haystack = f"{entity_name} {json.dumps(profile, ensure_ascii=False)}".lower()
                    if normalized_segment not in haystack:
                        continue
                lines.append(f"- {entity_name or 'Entidad sin nombre'}")
                compact_profile = {
                    "responsable_ps": profile.get("ps_responsible_name"),
                    "responsable_entidad": profile.get("entity_responsible_name"),
                    "telefono": profile.get("entity_responsible_phone"),
                    "correo": profile.get("entity_responsible_email"),
                    "fase_estatal": profile.get("state_phase_description"),
                    "uniformes": " · ".join(
                        str(value)
                        for value in (
                            profile.get("uniform_delivery_date"),
                            profile.get("uniform_delivery_place"),
                        )
                        if value
                    ),
                    "viaje_nacional": " · ".join(
                        str(value)
                        for value in (
                            profile.get("national_travel_departure_date"),
                            profile.get("national_travel_return_date"),
                        )
                        if value
                    ),
                    "notas": profile.get("operations_notes"),
                }
                for field in _workspace_compact_fields(compact_profile, max_fields=10):
                    lines.append(f"  - {field}")
                shown_profiles += 1
            if len(entity_profiles) > shown_profiles:
                lines.append(
                    f"  - ...(expedientes de entidad truncados: {len(entity_profiles) - shown_profiles})"
                )

        show_national = normalized_segment in {"", "all", "nacional", "national"}
        if show_national:
            national = _workspace_coerce_dict(workspace.get("national"))
            national_lines = _workspace_compact_fields(national, max_fields=30)
            if national_lines:
                lines.append("Fase nacional:")
                for item in national_lines:
                    lines.append(f"- {item}")
            if national_profile:
                lines.append(
                    "Expediente fase nacional (fuente: tournament_national_phase_profiles):"
                )
                compact_national = {
                    "torneo_categoria_fechas_ciudad": national_profile.get(
                        "tournament_category_dates_duration_city"
                    ),
                    "hoteles_camas_noche": national_profile.get(
                        "hotels_and_bed_nights"
                    ),
                    "alimentos": national_profile.get("meals_breakdown"),
                    "sede": national_profile.get("sports_unit_venue"),
                    "canchas": national_profile.get("courts_count_and_types"),
                    "servicios_medicos": national_profile.get(
                        "medical_services_description"
                    ),
                    "accidentes_traslado": national_profile.get(
                        "accidents_with_transfers"
                    ),
                    "viajes_ps": national_profile.get("staff_travel_costs"),
                    "pagos_hoteles": national_profile.get(
                        "hotel_payments_advance_settlement"
                    ),
                    "pagos_proveedores_finales": national_profile.get(
                        "supplier_payments_for_finals"
                    ),
                    "costos_medicos": national_profile.get("medical_services_costs"),
                    "seguros": national_profile.get("insurance_costs"),
                    "proveedores_activacion": national_profile.get(
                        "on_site_brand_activation_suppliers"
                    ),
                    "visitantes_patrocinador": national_profile.get(
                        "sponsor_related_visitors"
                    ),
                    "marketing_evidencia": national_profile.get(
                        "marketing_activity_reports_with_photos"
                    ),
                }
                national_profile_lines = _workspace_compact_fields(
                    compact_national, max_fields=30
                )
                if national_profile_lines:
                    for item in national_profile_lines:
                        lines.append(f"- {item}")

    if len(lines) <= 1:
        return None

    max_chars = max(
        800, min(int(os.getenv("ASSISTANT_WORKSPACE_CONTEXT_CHARS", "9000")), 18000)
    )
    context = "\n".join(lines).strip()
    if len(context) > max_chars:
        context = context[:max_chars] + "\n...(contexto de carpetas truncado)"
    return context


async def _supabase_auth_user_map(limit: int = 500) -> Dict[str, str]:
    base_url = _supabase_base_url()
    service_key = (os.getenv("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not base_url or not service_key:
        return {}
    url = f"{base_url}/auth/v1/admin/users?page=1&per_page={max(1, min(limit, 1000))}"
    payload = await asyncio.to_thread(
        _sync_fetch_json,
        url,
        {
            "Authorization": f"Bearer {service_key}",
            "apikey": service_key,
            "Accept": "application/json",
        },
    )
    users = (payload or {}).get("users") if isinstance(payload, dict) else []
    out: Dict[str, str] = {}
    if not isinstance(users, list):
        return out
    for user in users:
        if not isinstance(user, dict):
            continue
        uid = str(user.get("id") or "").strip()
        user_email = str(user.get("email") or "").strip().lower()
        if uid:
            out[uid] = user_email
    return out


async def _supabase_synthetic_counts() -> Dict[str, int]:
    tournament_ids = await _supabase_rest_ids(
        table="tournaments",
        filters={"slug": "ilike.synth-torneo-%"},
    )
    category_ids: List[str] = []
    team_ids: List[str] = []
    if tournament_ids:
        tid_chunks = [
            tournament_ids[i : i + 200] for i in range(0, len(tournament_ids), 200)
        ]
        for chunk in tid_chunks:
            in_filter = f"in.({','.join(chunk)})"
            category_ids.extend(
                await _supabase_rest_ids(
                    table="categories",
                    filters={"tournament_id": in_filter},
                )
            )
            team_ids.extend(
                await _supabase_rest_ids(
                    table="teams",
                    filters={"tournament_id": in_filter},
                )
            )

    registration_ids: List[str] = []
    if team_ids:
        team_chunks = [team_ids[i : i + 200] for i in range(0, len(team_ids), 200)]
        for chunk in team_chunks:
            in_filter = f"in.({','.join(chunk)})"
            registration_ids.extend(
                await _supabase_rest_ids(
                    table="registrations",
                    filters={"team_id": in_filter},
                )
            )

    player_ids: List[str] = []
    if registration_ids:
        reg_chunks = [
            registration_ids[i : i + 200] for i in range(0, len(registration_ids), 200)
        ]
        for chunk in reg_chunks:
            in_filter = f"in.({','.join(chunk)})"
            player_ids.extend(
                await _supabase_rest_ids(
                    table="players",
                    filters={"registration_id": in_filter},
                )
            )

    return {
        "tournaments": len(tournament_ids),
        "categories": len(category_ids),
        "teams": len(team_ids),
        "registrations": len(registration_ids),
        "players": len(player_ids),
    }


async def _supabase_rpc_has_role(*, access_token: str, user_id: str, role: str) -> bool:
    base_url = _supabase_base_url()
    api_key = _supabase_api_key()
    if not base_url or not api_key:
        return False

    payload = await asyncio.to_thread(
        _sync_fetch_json,
        f"{base_url}/rest/v1/rpc/has_role",
        {
            "Authorization": f"Bearer {access_token}",
            "apikey": api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        12,
        "POST",
        {"_user_id": user_id, "_role": role},
    )
    return bool(payload)


def _derive_empleado_role(
    *, user_payload: Dict[str, Any], supabase_roles: List[str]
) -> str:
    role_candidates = {
        r.strip().lower() for r in (supabase_roles or []) if str(r).strip()
    }

    app_metadata = user_payload.get("app_metadata") or {}
    if isinstance(app_metadata, dict):
        app_role = str(app_metadata.get("role") or "").strip().lower()
        if app_role:
            role_candidates.add(app_role)
        app_roles = app_metadata.get("roles")
        if isinstance(app_roles, list):
            for role in app_roles:
                role_str = str(role or "").strip().lower()
                if role_str:
                    role_candidates.add(role_str)

    if "superadmin" in role_candidates or "super_admin" in role_candidates:
        return "superadmin"
    if "admin" in role_candidates:
        return "admin"
    if "customer" in role_candidates or "finanzas" in role_candidates:
        return "finanzas"
    return "empleado"


def _parse_script_counts(raw_output: str) -> Optional[Dict[str, Any]]:
    if not raw_output:
        return None
    decoder = json.JSONDecoder()
    candidates: List[Dict[str, Any]] = []
    for idx, ch in enumerate(raw_output):
        if ch != "{":
            continue
        try:
            parsed, _ = decoder.raw_decode(raw_output[idx:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            candidates.append(parsed)
    if candidates:
        return candidates[-1]
    return None


async def _run_synthetic_script(script_name: str, args: List[str]) -> Dict[str, Any]:
    script_path = Path("/root/samchat/scripts") / script_name
    if not script_path.exists():
        raise HTTPException(status_code=500, detail=f"Script not found: {script_path}")

    proc = await asyncio.create_subprocess_exec(
        "python3",
        str(script_path),
        *args,
        cwd="/root/samchat",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=600)
    except asyncio.TimeoutError as exc:
        proc.kill()
        await proc.communicate()
        raise HTTPException(
            status_code=504, detail="Synthetic data script timed out"
        ) from exc

    combined = (stdout or b"").decode("utf-8", errors="replace")
    if stderr:
        combined = f"{combined}\n{stderr.decode('utf-8', errors='replace')}".strip()
    # Drop noisy CUDA/NVML warnings unrelated to synthetic results.
    cleaned_lines = []
    for line in combined.splitlines():
        if "torch/cuda/__init__.py" in line and "Can't initialize NVML" in line:
            continue
        if "Can't initialize NVML" in line:
            continue
        cleaned_lines.append(line)
    combined = "\n".join(cleaned_lines).strip()
    counts = _parse_script_counts(combined)
    return {
        "exit_code": int(proc.returncode or 0),
        "counts": counts,
        "output": combined[-6000:],
    }


def _create_synthetic_job(*, action: str, mode: str, empleado_id: uuid.UUID) -> str:
    job_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
    with _SYNTHETIC_JOBS_LOCK:
        _SYNTHETIC_JOBS[job_id] = {
            "job_id": job_id,
            "action": action,
            "mode": mode,
            "status": "running",
            "empleado_id": str(empleado_id),
            "created_at": now,
            "updated_at": now,
            "counts": None,
            "output": "",
            "exit_code": None,
        }
    return job_id


def _update_synthetic_job(job_id: str, **patch: Any) -> None:
    with _SYNTHETIC_JOBS_LOCK:
        row = _SYNTHETIC_JOBS.get(job_id)
        if not row:
            return
        row.update(patch)
        row["updated_at"] = datetime.utcnow().isoformat()


async def _run_synthetic_job(
    *,
    job_id: str,
    script_name: str,
    args: List[str],
) -> None:
    try:
        result = await _run_synthetic_script(script_name, args)
        exit_code = int(result.get("exit_code") or 0)
        if exit_code == 0:
            _update_synthetic_job(
                job_id,
                status="completed",
                counts=result.get("counts"),
                output=result.get("output"),
                exit_code=exit_code,
            )
        else:
            _update_synthetic_job(
                job_id,
                status="failed",
                counts=result.get("counts"),
                output=result.get("output"),
                exit_code=exit_code,
            )
    except Exception as exc:
        _update_synthetic_job(
            job_id,
            status="failed",
            output=str(exc),
            exit_code=-1,
        )


async def _run_synthetic_reset_seed_job(
    *,
    job_id: str,
    seed_args: List[str],
) -> None:
    try:
        cleanup_result = await _run_synthetic_script(
            "cleanup_synthetic_data.py",
            ["--target", "all", "--apply"],
        )
        if int(cleanup_result.get("exit_code") or 0) != 0:
            _update_synthetic_job(
                job_id,
                status="failed",
                counts=cleanup_result.get("counts"),
                output=(
                    "Cleanup failed before seed.\n\n"
                    f"{cleanup_result.get('output') or ''}"
                ),
                exit_code=int(cleanup_result.get("exit_code") or 1),
            )
            return

        seed_result = await _run_synthetic_script(
            "generate_synthetic_data.py",
            seed_args,
        )
        seed_exit = int(seed_result.get("exit_code") or 0)
        combined_output = (
            "== cleanup ==\n"
            f"{cleanup_result.get('output') or ''}\n\n"
            "== seed ==\n"
            f"{seed_result.get('output') or ''}"
        ).strip()
        _update_synthetic_job(
            job_id,
            status="completed" if seed_exit == 0 else "failed",
            counts=seed_result.get("counts"),
            output=combined_output[-6000:],
            exit_code=seed_exit,
        )
    except Exception as exc:
        _update_synthetic_job(
            job_id,
            status="failed",
            output=str(exc),
            exit_code=-1,
        )


def _start_synthetic_job(
    *,
    action: str,
    mode: str,
    empleado_id: uuid.UUID,
    script_name: str,
    args: List[str],
) -> Dict[str, Any]:
    job_id = _create_synthetic_job(action=action, mode=mode, empleado_id=empleado_id)
    asyncio.create_task(
        _run_synthetic_job(job_id=job_id, script_name=script_name, args=args)
    )
    return {
        "ok": True,
        "queued": True,
        "job_id": job_id,
        "action": action,
        "mode": mode,
        "status": "running",
        "counts": {"queued": 1},
        "output": f"Job en ejecución. job_id={job_id}",
    }


def _start_synthetic_reset_seed_job(
    *,
    empleado_id: uuid.UUID,
    seed_args: List[str],
) -> Dict[str, Any]:
    job_id = _create_synthetic_job(
        action="reset_seed", mode="apply", empleado_id=empleado_id
    )
    asyncio.create_task(
        _run_synthetic_reset_seed_job(job_id=job_id, seed_args=seed_args)
    )
    return {
        "ok": True,
        "queued": True,
        "job_id": job_id,
        "action": "reset_seed",
        "mode": "apply",
        "status": "running",
        "counts": {"queued": 1},
        "output": f"Job en ejecución. job_id={job_id}",
    }


async def _load_conversation(
    session: AsyncSession, *, conversation_id: str, empleado_id: uuid.UUID
) -> AssistantConversation:
    try:
        cid = uuid.UUID(conversation_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid conversation_id") from exc

    row = (
        await session.execute(
            select(AssistantConversation).where(
                AssistantConversation.id == cid,
                AssistantConversation.empleado_id == empleado_id,
                AssistantConversation.archived.is_(False),
            )
        )
    ).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return row


async def _history_messages(
    session: AsyncSession, *, conversation_id: uuid.UUID, limit: int = 20
) -> List[Dict[str, str]]:
    rows = (
        (
            await session.execute(
                select(AssistantMessage)
                .where(AssistantMessage.conversation_id == conversation_id)
                .where(AssistantMessage.role.in_(("user", "assistant")))
                .order_by(desc(AssistantMessage.created_at))
                .limit(max(1, min(limit, 50)))
            )
        )
        .scalars()
        .all()
    )

    # We fetched DESC; reverse to chronological.
    rows = list(reversed(list(rows)))
    msgs: List[Dict[str, str]] = []
    for m in rows:
        msgs.append({"role": m.role, "content": m.content or ""})
    return msgs


async def _run_read_tool(
    tool_name: str,
    args: Dict[str, Any],
    *,
    gastos_session: AsyncSession,
    tournament_key_default: Optional[str],
    current_role: Optional[str] = None,
    bi_year: Optional[int] = None,
    bi_scope: Optional[str] = None,
) -> Dict[str, Any]:
    if tool_name == "assistant_canonical_query":
        canonical_action = str(args.get("action") or "").strip()
        if canonical_action not in supported_read_actions():
            raise HTTPException(
                status_code=403,
                detail="assistant_canonical_query only allows read-only actions",
            )
        result = await execute_canonical_action(
            canonical_action,
            session=gastos_session,
            context=args.get("context"),
            payload=args.get("payload"),
        )
        return {
            "action": result.action,
            "status": result.status,
            "data": result.data,
            "context": result.context.to_dict(),
        }

    if tool_name.startswith("dev_") and not _is_superadmin(current_role):
        raise HTTPException(
            status_code=403, detail="Developer tools require superadmin role"
        )
    if tool_name == "db_read_universal" and not _is_admin(current_role):
        raise HTTPException(
            status_code=403,
            detail="Universal DB read requires admin or superadmin role",
        )

    if tool_name == "finance_ops_query":
        with gastos_session.no_autoflush:
            return await finance_ops_query(gastos_session, **args)

    if tool_name == "finance_strategy_snapshot":
        if bi_scope and not args.get("bi_scope"):
            args["bi_scope"] = bi_scope
        if bi_year and not args.get("date_from") and not args.get("date_to"):
            args["date_from"] = f"{int(bi_year)}-01-01"
            args["date_to"] = f"{int(bi_year)}-12-31"
        with gastos_session.no_autoflush:
            return await finance_strategy_snapshot(gastos_session, **args)

    if tool_name == "finance_accounting_report":
        with gastos_session.no_autoflush:
            return await finance_accounting_report(gastos_session, **args)

    if tool_name == "finance_expense_workflow_status":
        with gastos_session.no_autoflush:
            return await finance_expense_workflow_status(gastos_session, **args)

    if tool_name == "finance_realtime_report":
        if bi_scope and not args.get("bi_scope"):
            args["bi_scope"] = bi_scope
        if bi_year and not args.get("date_from") and not args.get("date_to"):
            args["date_from"] = f"{int(bi_year)}-01-01"
            args["date_to"] = f"{int(bi_year)}-12-31"
        with gastos_session.no_autoflush:
            return await finance_realtime_report(gastos_session, **args)

    if tool_name == "finance_vendor_payments":
        with gastos_session.no_autoflush:
            return await finance_vendor_payments(gastos_session, **args)

    if tool_name == "finance_alerts_scan":
        if bi_scope and not args.get("bi_scope"):
            args["bi_scope"] = bi_scope
        if bi_year and not args.get("date_from") and not args.get("date_to"):
            args["date_from"] = f"{int(bi_year)}-01-01"
            args["date_to"] = f"{int(bi_year)}-12-31"
        with gastos_session.no_autoflush:
            return await finance_alerts_scan(gastos_session, **args)

    if tool_name == "finance_expense_search":
        with gastos_session.no_autoflush:
            return await finance_expense_search(gastos_session, **args)

    if tool_name == "tournament_expediente_snapshot":
        try:
            tournament_uuid = uuid.UUID(str(args.get("tournament_id") or ""))
        except ValueError as exc:
            raise HTTPException(
                status_code=400, detail="Invalid tournament_id"
            ) from exc
        normalized_role = _normalize_role(current_role)
        include_finance = bool(args.get("include_finance", True)) and (
            normalized_role == "finanzas" or _is_admin(current_role)
        )
        with gastos_session.no_autoflush:
            return await _build_tournament_expediente_snapshot(
                session=gastos_session,
                tournament_uuid=tournament_uuid,
                include_finance=include_finance,
            )

    if tool_name == "tournament_registration_breakdown":
        tkey = (
            (args.get("tournament_key") or tournament_key_default or "").strip().lower()
        )
        if not tkey:
            raise HTTPException(
                status_code=400,
                detail="Para consultas de torneo especifica tournament_key=beisbol.",
            )
        session_maker = get_tournament_session_maker(tkey)
        async with session_maker() as t_session:
            return await tournament_registration_breakdown(t_session, **args)

    if tool_name == "tournament_ops_query":
        tkey = (
            (args.get("tournament_key") or tournament_key_default or "").strip().lower()
        )
        if not tkey:
            raise HTTPException(
                status_code=400,
                detail="Para consultas de torneo especifica tournament_key=beisbol.",
            )
        session_maker = get_tournament_session_maker(tkey)
        async with session_maker() as t_session:
            return await tournament_ops_query(t_session, **args)

    if tool_name == "dev_repo_search":
        return await dev_repo_search(**args)

    if tool_name == "dev_file_read":
        return await dev_file_read(**args)

    if tool_name == "dev_run_checks":
        return await dev_run_checks(**args)

    if tool_name == "db_read_universal":
        with gastos_session.no_autoflush:
            return await _db_read_universal(
                gastos_session=gastos_session,
                data_source=str(args.get("data_source") or ""),
                table=str(args.get("table") or ""),
                columns=args.get("columns"),
                filters=args.get("filters"),
                order_by=args.get("order_by"),
                order_dir=str(args.get("order_dir") or "desc"),
                limit=int(args.get("limit") or 100),
            )

    raise HTTPException(status_code=400, detail=f"Unknown tool: {tool_name}")


async def _execute_write_tool(
    tool_name: str,
    args: Dict[str, Any],
    *,
    gastos_session: AsyncSession,
    conversation_id: uuid.UUID,
    empleado_id: uuid.UUID,
    tournament_key_default: Optional[str],
) -> Dict[str, Any]:
    if tool_name == "assistant_canonical_action":
        canonical_payload = dict(args.get("payload") or {})
        canonical_context = AssistantContext.from_dict(args.get("context")).merge(
            responsible_user_id=str(empleado_id)
        )
        canonical_payload.setdefault("empleado_id", str(empleado_id))
        result = await execute_canonical_action(
            str(args.get("action") or "").strip(),
            session=gastos_session,
            context=canonical_context,
            payload=canonical_payload,
        )
        return {
            "action": result.action,
            "status": result.status,
            "data": result.data,
            "context": result.context.to_dict(),
        }

    if tool_name == "finance_vendor_create":
        return await finance_vendor_create(gastos_session, **args)

    if tool_name == "finance_expense_assign_accounting":
        return await finance_expense_assign_accounting(gastos_session, **args)

    if tool_name == "finance_expense_post_accounting":
        payload = {
            "empleado_id": str(empleado_id),
            **args,
        }
        return await finance_expense_post_accounting(gastos_session, **payload)

    if tool_name == "finance_expense_create":
        use_last_media = bool(args.get("use_last_media"))
        media_draft = None
        if use_last_media:
            conversation = (
                await gastos_session.execute(
                    select(AssistantConversation).where(
                        AssistantConversation.id == conversation_id
                    )
                )
            ).scalar_one_or_none()
            if not conversation:
                raise HTTPException(
                    status_code=404, detail="Conversation not found for media draft"
                )
            metadata = _conversation_metadata_dict(conversation)
            media_draft = (
                metadata.get("assistant_last_media")
                if isinstance(metadata, dict)
                else None
            )
            if not isinstance(media_draft, dict):
                raise HTTPException(
                    status_code=400,
                    detail="No hay un comprobante reciente en esta conversacion. Sube primero la foto/audio del ticket.",
                )
            if not media_draft.get("has_inline_file") or not media_draft.get(
                "file_b64"
            ):
                raise HTTPException(
                    status_code=400,
                    detail="El comprobante cargado no esta disponible para auto-CFDI (tamano grande). Sube una imagen <=5MB.",
                )

        args_clean = dict(args)
        args_clean.pop("use_last_media", None)
        payload = {
            "empleado_id": str(empleado_id),
            **args_clean,
        }
        if media_draft is not None:
            payload["tipo_gasto"] = "ticket"
            payload["archivo_data"] = str(media_draft.get("file_b64") or "")
            payload["archivo_nombre"] = str(media_draft.get("filename") or "ticket.jpg")

        result = await finance_expense_create(gastos_session, **payload)

        # Clear used media draft after successful create to avoid accidental reuse.
        if media_draft is not None:
            conversation = (
                await gastos_session.execute(
                    select(AssistantConversation).where(
                        AssistantConversation.id == conversation_id
                    )
                )
            ).scalar_one_or_none()
            if conversation:
                metadata = _conversation_metadata_dict(conversation)
                metadata.pop("assistant_last_media", None)
                conversation.metadata_ = metadata
                await gastos_session.commit()
        return result

    if tool_name == "finance_expense_update":
        return await finance_expense_update(gastos_session, **args)

    if tool_name == "finance_expense_request_cfdi":
        return await finance_expense_request_cfdi(gastos_session, **args)

    if tool_name == "assistant_save_artifact":
        payload = {
            "conversation_id": str(conversation_id),
            "created_by_empleado_id": str(empleado_id),
            **args,
        }
        return await assistant_save_artifact(gastos_session, **payload)

    if tool_name == "tournament_schedule_create":
        tkey = (
            (args.get("tournament_key") or tournament_key_default or "").strip().lower()
        )
        if not tkey:
            raise HTTPException(
                status_code=400,
                detail="Para crear calendario especifica tournament_key=beisbol.",
            )
        payload = {**args, "tournament_key": tkey}
        return await tournament_schedule_create(**payload)

    if tool_name == "tournament_schedule_regenerate_from_rules":
        tkey = (
            (args.get("tournament_key") or tournament_key_default or "").strip().lower()
        )
        if not tkey:
            raise HTTPException(
                status_code=400,
                detail="Para regenerar calendario especifica tournament_key=beisbol.",
            )
        payload = {**args, "tournament_key": tkey}
        return await tournament_schedule_regenerate_from_rules(**payload)
    if tool_name == "tournament_team_register_from_roster":
        tkey = (
            (args.get("tournament_key") or tournament_key_default or "").strip().lower()
        )
        if not tkey:
            raise HTTPException(
                status_code=400,
                detail="Para registrar equipo desde roster especifica tournament_key=beisbol.",
            )
        payload = {**args, "tournament_key": tkey}
        return await tournament_team_register_from_roster(**payload)

    if tool_name == "dev_file_write":
        return await dev_file_write(**args)

    if tool_name == "dev_file_replace":
        return await dev_file_replace(**args)

    if tool_name == "db_write_universal":
        return await _db_write_universal(
            gastos_session=gastos_session,
            data_source=str(args.get("data_source") or ""),
            table=str(args.get("table") or ""),
            action=str(args.get("action") or ""),
            values=args.get("values"),
            filters=args.get("filters"),
            max_affected=int(args.get("max_affected") or 200),
        )

    raise HTTPException(status_code=400, detail=f"Unknown tool: {tool_name}")


async def _assistant_turn(
    *,
    raw_message: str,
    conversation: AssistantConversation,
    current_empleado: Any,
    session: AsyncSession,
    request: Optional[Request] = None,
    tournament_key: Optional[str],
    bi_year: Optional[int] = None,
    bi_scope: Optional[str] = None,
    bi_segment: Optional[str] = None,
    assistant_mode: Optional[str] = None,
    openai_api_key: Optional[str] = None,
) -> MessageResponse:
    turn_state = _prepare_turn_state(
        raw_message=raw_message,
        conversation=conversation,
        request=request,
        tournament_key=tournament_key,
        assistant_mode=assistant_mode,
        assistant_classify_request=_assistant_classify_request,
        assistant_request_origin=_assistant_request_origin,
        conversation_module_key=_conversation_module_key,
        conversation_module_label=_conversation_module_label,
        conversation_module_context_text=_conversation_module_context_text,
        assistant_route_mode=_assistant_route_mode,
        normalize_assistant_mode=_normalize_assistant_mode,
        assistant_inference_plan=_assistant_inference_plan,
        assistant_default_tournament_key=_assistant_default_tournament_key,
    )
    route_info = turn_state["route_info"]
    origin_info = turn_state["origin_info"]
    module_key_default = turn_state["module_key_default"]
    module_label_default = turn_state["module_label_default"]
    module_context_default = turn_state["module_context_default"]
    normalized_mode = turn_state["normalized_mode"]
    inference_plan = turn_state["inference_plan"]
    tournament_key_default = turn_state["tournament_key_default"]

    user_msg = AssistantMessage(
        conversation_id=conversation.id,
        role="user",
        content=raw_message,
        tool_name=None,
        tool_payload=None,
    )
    session.add(user_msg)
    await session.commit()

    response_cache_enabled = os.getenv(
        "ASSISTANT_RESPONSE_CACHE_ENABLED", "1"
    ).strip().lower() not in {
        "0",
        "false",
        "no",
    }
    cache_key = _assistant_response_cache_key(
        empleado_id=current_empleado.id,
        raw_message=raw_message,
        tournament_key=tournament_key_default,
        module_key=module_key_default,
        bi_year=bi_year,
        bi_scope=bi_scope,
        bi_segment=bi_segment,
        assistant_mode=normalized_mode,
    )
    if response_cache_enabled:
        cached = _assistant_response_cache_get(cache_key)
        if cached:
            return await _build_cached_response(
                cache_payload=cached,
                conversation=conversation,
                current_empleado=current_empleado,
                raw_message=raw_message,
                origin_info=origin_info,
                session=session,
                assistant_message_cls=AssistantMessage,
                assistant_run_cls=AssistantRun,
                message_response_cls=MessageResponse,
            )

    tool_trace: List[Dict[str, Any]] = []
    if origin_info:
        tool_trace.append({"assistant_origin": origin_info})
    tool_trace.append({"assistant_route": route_info})
    tool_trace.append({"assistant_plan": inference_plan})
    hermes_profile_prompt = _assistant_hermes_profile_prompt(route_info)
    if str(route_info.get("delegate_to_hermes") or "").strip().lower() in {
        "true",
        "1",
    } or route_info.get("delegate_to_hermes"):
        tool_trace.append(
            {
                "assistant_delegate": {
                    "provider": "hermes",
                    "profile": route_info.get("hermes_profile"),
                    "transparent": True,
                    "reason": route_info.get("reason"),
                }
            }
        )
    if module_key_default or module_label_default or module_context_default:
        tool_trace.append(
            {
                "assistant_entrypoint": {
                    "module_key": module_key_default,
                    "module_label": module_label_default,
                    "module_context": module_context_default,
                }
            }
        )
    retrieval_context: Optional[str] = None
    retrieval_sources: List[Dict[str, Any]] = []
    workspace_context: Optional[str] = None
    route_prompt = _assistant_route_system_prompt(route_info)
    language_prompt = _assistant_response_language_prompt(raw_message)
    tool_defs = _assistant_tool_defs(route_info)
    agent_runtime_enabled = _is_agent_runtime_enabled()
    tool_registry = _assistant_tool_registry() if agent_runtime_enabled else {}
    if agent_runtime_enabled:
        tool_trace.append(
            _build_agent_runtime_trace(
                route_info=route_info,
                tool_defs=tool_defs,
                registry=tool_registry,
            )
        )
    max_tokens = _assistant_max_tokens(mode=normalized_mode, route=route_info["route"])
    try:
        workspace_context = await _build_workspace_context(
            raw_message=raw_message,
            tournament_key_default=tournament_key_default,
            bi_scope=bi_scope,
            bi_segment=bi_segment,
        )
    except Exception as exc:
        tool_trace.append({"workspace_context_error": str(exc)})

    try:
        rag_enabled = os.getenv("ASSISTANT_RAG_ENABLED", "1").strip() not in {
            "0",
            "false",
            "False",
        }
        if rag_enabled:
            retrieval = await _build_hybrid_retrieval(
                session=session,
                query=raw_message,
                empleado_id=current_empleado.id,
                conversation_id=conversation.id,
                module_key=module_key_default,
                domain=route_info.get("domain"),
                tournament_key=tournament_key_default,
            )
            retrieval_context = retrieval.get("context") or None
            retrieval_sources = retrieval.get("sources") or []
            tool_trace.append(
                {
                    "retrieval": {
                        "cache_hit": retrieval.get("cache_hit", False),
                        "sources": retrieval_sources,
                        "trace": retrieval.get("trace", {}),
                    }
                }
            )
    except Exception as exc:
        tool_trace.append({"retrieval_error": str(exc)})

    messages = await _build_turn_messages(
        session=session,
        conversation_id=conversation.id,
        raw_message=raw_message,
        route_prompt=route_prompt,
        language_prompt=language_prompt,
        hermes_profile_prompt=hermes_profile_prompt,
        workspace_context=workspace_context,
        module_key_default=module_key_default,
        module_label_default=module_label_default,
        module_context_default=module_context_default,
        retrieval_context=retrieval_context,
        assistant_system_prompt=_assistant_system_prompt,
        history_messages=_history_messages,
    )

    provider_errors: List[str] = []
    tool_policy_evaluator = None
    if agent_runtime_enabled:
        def _runtime_tool_policy_evaluator(tool_name, args, role):
            return _evaluate_runtime_tool_call(
                tool_name=tool_name,
                args=args,
                role=role,
                registry=tool_registry,
            )

        tool_policy_evaluator = _runtime_tool_policy_evaluator

    for provider in _assistant_provider_order(
        normalized_mode,
        route_info=route_info,
        capability="chat",
    ):
        model = _assistant_model(provider, normalized_mode, route_info=route_info)
        try:
            return await _execute_provider(
                provider=provider,
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
                write_tools=WRITE_TOOLS,
                route_prompt=route_prompt,
                language_prompt=language_prompt,
                hermes_profile_prompt=hermes_profile_prompt,
                workspace_context=workspace_context,
                module_key_default=module_key_default,
                module_label_default=module_label_default,
                module_context_default=module_context_default,
                retrieval_context=retrieval_context,
                assistant_system_prompt=_assistant_system_prompt,
                history_messages=_history_messages,
                get_model=_assistant_model,
                get_openai_client=_get_openai_client,
                get_anthropic_client=_get_anthropic_client,
                ollama_chat=_ollama_chat,
                ollama_message_content=_ollama_message_content,
                ollama_tool_calls=_ollama_tool_calls,
                ollama_assistant_message=_ollama_assistant_message,
                tool_defs_anthropic=_tool_defs_anthropic,
                anthropic_text_from_blocks=_anthropic_text_from_blocks,
                anthropic_message_from_blocks=_anthropic_message_from_blocks,
                run_read_tool=_run_read_tool,
                ensure_citations=_ensure_citations,
                tool_trace_has_write_intent=_tool_trace_has_write_intent,
                assistant_response_cache_set=_assistant_response_cache_set,
                pending_confirmation_cls=PendingConfirmation,
                assistant_run_cls=AssistantRun,
                assistant_message_cls=AssistantMessage,
                message_response_cls=MessageResponse,
                tool_policy_evaluator=tool_policy_evaluator,
            )

        except HTTPException as exc:
            # Do not mask actionable user-facing validation errors with provider fallback.
            if 400 <= exc.status_code < 500:
                raise
            provider_errors.append(f"{provider}: {exc.detail}")
            continue
        except Exception as exc:
            provider_errors.append(f"{provider}: {exc}")
            continue

    raise HTTPException(
        status_code=500,
        detail=f"Assistant providers failed: {' | '.join(provider_errors)}",
    )


def _normalize_col_name(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", (value or "").strip().lower())
    return normalized.strip("_")


def _parse_sheet_date(value: Any) -> Optional[str]:
    raw = str(value or "").strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d-%m-%y", "%d/%m/%y"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _extract_roster_from_dataframe(df: Any) -> Dict[str, Any]:
    records = []
    if df is not None:
        records = _dataframe_records(df)
    return _extract_roster_from_records(records)


def _extract_roster_from_records(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not records:
        raise HTTPException(
            status_code=400, detail="El archivo no contiene filas de datos"
        )
    normalized_records: List[Dict[str, str]] = []
    for row in records:
        normalized_row: Dict[str, str] = {}
        for key, value in (row or {}).items():
            normalized_key = _normalize_col_name(str(key))
            if not normalized_key:
                continue
            normalized_row[normalized_key] = str(value or "").strip()
        normalized_records.append(normalized_row)
    first_non_empty = next((row for row in normalized_records if row), None)
    if not first_non_empty or len(first_non_empty.keys()) < 2:
        raise HTTPException(
            status_code=400,
            detail="El archivo no tiene columnas suficientes para mapear jugadores",
        )

    def pick(row: Any, candidates: List[str]) -> str:
        for c in candidates:
            value = str((row or {}).get(c) or "").strip()
            if value:
                return value
        return ""

    team_name = ""
    tournament_name = ""
    category_name = ""
    state = ""
    country = ""
    representative_name = ""
    representative_email = ""
    representative_phone = ""
    players: List[Dict[str, Any]] = []

    for idx, row in enumerate(normalized_records):
        team_name = team_name or pick(row, ["team_name", "equipo", "nombre_equipo"])
        tournament_name = tournament_name or pick(
            row, ["tournament_name", "torneo", "nombre_torneo"]
        )
        category_name = category_name or pick(row, ["category_name", "categoria"])
        state = state or pick(row, ["state", "estado"])
        country = country or pick(row, ["country", "pais"])
        representative_name = representative_name or pick(
            row, ["representative_name", "representante", "tutor", "nombre_tutor"]
        )
        representative_email = representative_email or pick(
            row, ["representative_email", "correo_representante", "email_representante"]
        )
        representative_phone = representative_phone or pick(
            row, ["representative_phone", "telefono_representante"]
        )

        first_name = pick(row, ["first_name", "nombre", "nombres", "name"])
        last_name = pick(row, ["last_name", "apellido", "apellidos"])
        paternal = pick(row, ["paternal_surname", "apellido_paterno"])
        maternal = pick(row, ["maternal_surname", "apellido_materno"])
        if not last_name and (paternal or maternal):
            last_name = paternal or maternal
        if not first_name and not last_name:
            continue
        birth_date = (
            _parse_sheet_date(
                pick(row, ["birth_date", "fecha_nacimiento", "nacimiento", "fecha"])
            )
            or ""
        )
        if not birth_date:
            # Keep the row but with a placeholder date to force explicit correction if needed.
            birth_date = "2012-01-01"

        jersey_raw = pick(row, ["jersey_number", "numero", "dorsal"])
        try:
            jersey_val = int(jersey_raw) if jersey_raw else (len(players) + 1)
        except ValueError:
            jersey_val = len(players) + 1

        players.append(
            {
                "first_name": first_name or "Jugador",
                "last_name": last_name or f"#{idx + 1}",
                "paternal_surname": paternal or None,
                "maternal_surname": maternal or None,
                "birth_date": birth_date,
                "curp": pick(row, ["curp"]) or None,
                "parent_name": pick(
                    row, ["parent_name", "tutor", "nombre_tutor", "representante"]
                )
                or representative_name
                or "Tutor pendiente",
                "parent_email": pick(
                    row, ["parent_email", "correo", "email", "correo_electronico"]
                )
                or representative_email
                or "pendiente@sam.chat",
                "parent_phone": pick(row, ["parent_phone", "telefono", "celular"])
                or representative_phone
                or "5500000000",
                "jersey_number": jersey_val,
                "position": pick(row, ["position", "posicion"]) or None,
            }
        )

    return {
        "team_name": team_name or None,
        "tournament_name": tournament_name or None,
        "category_name": category_name or None,
        "state": state or None,
        "country": country or "Mexico",
        "representative_name": representative_name or None,
        "representative_email": representative_email or None,
        "representative_phone": representative_phone or None,
        "players": players[:300],
        "rows_parsed": int(len(normalized_records)),
    }


def _extract_roster_from_spreadsheet_bytes(
    *, raw: bytes, filename: str, content_type: str
) -> Dict[str, Any]:
    records = _spreadsheet_records_from_bytes(
        raw=raw,
        filename=filename,
        content_type=content_type,
    )
    return _extract_roster_from_records(records)


async def _extract_text_from_image_anthropic(
    *, raw: bytes, content_type: str, note: Optional[str]
) -> str:
    client = _get_anthropic_client()
    model = os.getenv("ANTHROPIC_VISION_MODEL", "claude-sonnet-4-5-20250929")
    prompt = (
        "Extrae el texto relevante de este comprobante/imagen para captura de gastos. "
        "Incluye monto, fecha, comercio/proveedor y concepto si se ven."
    )
    if (note or "").strip():
        prompt += f"\nNota del usuario: {note.strip()}"
    b64 = base64.b64encode(raw).decode("ascii")

    def _call() -> str:
        resp = client.messages.create(
            model=model,
            max_tokens=900,
            temperature=0,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": (content_type or "image/jpeg"),
                                "data": b64,
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        )
        return _anthropic_text_from_blocks(getattr(resp, "content", []) or [])

    return (await asyncio.to_thread(_call)).strip()


def _store_last_media_draft(
    *,
    conversation: AssistantConversation,
    kind: str,
    upload: UploadFile,
    raw: bytes,
    note: Optional[str],
) -> Dict[str, Any]:
    max_bytes = int(os.getenv("ASSISTANT_MEDIA_DRAFT_MAX_BYTES", str(5 * 1024 * 1024)))
    payload: Dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "kind": kind,
        "filename": upload.filename or "upload.bin",
        "content_type": upload.content_type or "application/octet-stream",
        "size_bytes": len(raw),
        "note": (note or "").strip() or None,
        "captured_at": datetime.utcnow().isoformat(),
        "has_inline_file": False,
    }
    if len(raw) <= max_bytes:
        payload["file_b64"] = base64.b64encode(raw).decode("ascii")
        payload["has_inline_file"] = True
    else:
        payload["warning"] = (
            "Archivo grande: no se guardo inline para auto-CFDI. "
            "Sube una version de <=5MB para automatizacion completa."
        )

    metadata = _conversation_metadata_dict(conversation)
    metadata["assistant_last_media"] = payload
    conversation.metadata_ = metadata
    return payload


async def _build_deterministic_pending_response(
    *,
    deterministic_pending: Any,
    raw_message: str,
    conversation: AssistantConversation,
    current_empleado: Empleado,
    session: AsyncSession,
) -> MessageResponse:
    tool_name, tool_args, assistant_message = deterministic_pending
    run_id = uuid.uuid4()
    pending_confirmation = PendingConfirmation(
        run_id=str(run_id),
        tool_name=tool_name,
        tool_args=tool_args,
        summary=(
            f"El asistente quiere ejecutar: {tool_name} con estos parametros:\n"
            f"{json.dumps(tool_args, ensure_ascii=False, indent=2)}"
        ),
    )
    session.add(
        AssistantRun(
            id=run_id,
            conversation_id=conversation.id,
            empleado_id=current_empleado.id,
            status="pending_confirmation",
            model="deterministic:canonical-expense:agentic_write",
            user_message=raw_message,
            assistant_message=assistant_message,
            tool_trace=[
                {
                    "deterministic_pending": {
                        "tool": tool_name,
                        "args": tool_args,
                    }
                }
            ],
            pending_tool_name=tool_name,
            pending_tool_args=tool_args,
            created_at=datetime.utcnow(),
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
    return MessageResponse(
        assistant_message=assistant_message,
        run_id=str(run_id),
        tool_trace=[
            {
                "deterministic_pending": {
                    "tool": tool_name,
                    "args": tool_args,
                }
            }
        ],
        pending_confirmation=pending_confirmation,
    )


@router.post("/conversations", response_model=ConversationResponse)
async def create_conversation(
    payload: ConversationCreateRequest,
    request: Request,
    current_empleado=Depends(get_current_empleado),
    session: AsyncSession = Depends(get_db_session),
):
    try:
        metadata: Dict[str, Any] = {}
        origin_info = _assistant_request_origin(request)
        external_session_id = _normalize_external_session_id(payload.external_session_id)
        title = str(payload.title or "").strip() or None
        if external_session_id:
            existing = await _find_conversation_by_external_session_id(
                session=session,
                empleado_id=current_empleado.id,
                external_session_id=external_session_id,
            )
            if existing:
                if title:
                    existing.title = title
                _update_conversation_context(
                    conversation=existing,
                    tournament_key=payload.tournament_key,
                    module_key=payload.module_key,
                    module_label=payload.module_label,
                    module_context=payload.module_context,
                )
                metadata = _conversation_metadata_dict(existing)
                metadata["external_session_id"] = external_session_id
                if origin_info:
                    metadata["origin"] = origin_info
                existing.metadata_ = metadata or None
                await session.commit()
                await session.refresh(existing)
                return _conversation_response(existing)
        if payload.module_key:
            metadata["module_key"] = str(payload.module_key).strip().lower()
        if payload.module_label:
            metadata["module_label"] = str(payload.module_label).strip()
        if isinstance(payload.module_context, dict) and payload.module_context:
            metadata["module_context"] = payload.module_context
        if external_session_id:
            metadata["external_session_id"] = external_session_id
        if origin_info:
            metadata["origin"] = origin_info
        conversation = AssistantConversation(
            empleado_id=current_empleado.id,
            title=title,
            tournament_key=(payload.tournament_key or None),
            archived=False,
            metadata_=metadata or None,
        )
        session.add(conversation)
        await session.commit()
        await session.refresh(conversation)
        return _conversation_response(conversation)
    except HTTPException:
        raise
    except Exception:
        await session.rollback()
        logger.exception(
            "Unexpected error creating assistant conversation",
            extra={"empleado_id": str(current_empleado.id)},
        )
        raise HTTPException(status_code=500, detail="Unexpected processing error")


@router.get("/conversations", response_model=List[ConversationResponse])
async def list_conversations(
    external_session_id: Optional[str] = Query(default=None, max_length=160),
    current_empleado=Depends(get_current_empleado),
    session: AsyncSession = Depends(get_db_session),
):
    stmt = (
        select(AssistantConversation)
        .where(
            AssistantConversation.empleado_id == current_empleado.id,
            AssistantConversation.archived.is_(False),
        )
        .order_by(desc(AssistantConversation.updated_at))
        .limit(50)
    )
    external_session_id_clean = _normalize_external_session_id(external_session_id)
    if external_session_id_clean:
        stmt = stmt.where(
            func.jsonb_extract_path_text(
                AssistantConversation.metadata_,
                "external_session_id",
            )
            == external_session_id_clean
        )
    rows = (await session.execute(stmt)).scalars().all()
    return [_conversation_response(c) for c in rows]


@router.get("/me")
async def assistant_me(current_empleado=Depends(get_current_empleado)):
    role = getattr(current_empleado, "rol", None)
    permissions = sorted(
        {
            str(p).strip().lower()
            for p in (getattr(current_empleado, "permissions", set()) or set())
            if str(p).strip()
        }
    )
    can_finance_admin = (
        (str(role or "").strip().lower() in {"finanzas"})
        or _is_admin(role)
        or has_permission(current_empleado, "admin.finanzas.manage")
        or has_permission(current_empleado, "finanzas.manage")
    )
    can_operations_admin = (
        _is_superadmin(role)
        or (str(role or "").strip().lower() in {"admin"})
        or has_permission(current_empleado, "admin.operaciones.manage")
        or has_permission(current_empleado, "admin.torneos.manage")
        or has_permission(current_empleado, "admin.*")
    )
    can_user_admin = (
        _is_superadmin(role)
        or has_permission(current_empleado, "admin.perfiles.manage")
        or has_permission(current_empleado, "admin.perfiles.*")
        or has_permission(current_empleado, "admin.*")
    )
    return {
        "empleado_id": str(current_empleado.id),
        "nombre": getattr(current_empleado, "nombre", None),
        "correo": getattr(current_empleado, "correo", None),
        "rol": role,
        "permissions": permissions,
        "can_finance_admin": bool(can_finance_admin),
        "can_operations_admin": bool(can_operations_admin),
        "can_user_admin": bool(can_user_admin),
        "can_internal_console": bool(
            can_finance_admin or can_operations_admin or can_user_admin
        ),
        "can_superadmin": bool(_is_superadmin(role)),
    }


@router.post("/auth/bridge-supabase")
async def assistant_auth_bridge_supabase(
    payload: SupabaseBridgeRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
    session: AsyncSession = Depends(get_db_session),
):
    access_token = (
        payload.access_token
        or _extract_bearer_token(authorization)
        or request.session.get("supabase_access_token")
    )
    if not access_token:
        raise HTTPException(status_code=401, detail="Supabase access token is required")

    try:
        user_payload = await _load_supabase_user(access_token)
        user_id = str(user_payload.get("id") or "").strip()
        email = str(user_payload.get("email") or "").strip().lower()
        if not user_id or not email:
            raise HTTPException(
                status_code=401, detail="Supabase user payload missing id/email"
            )

        full_name = (
            str((user_payload.get("user_metadata") or {}).get("full_name") or "").strip()
            or str((user_payload.get("user_metadata") or {}).get("name") or "").strip()
            or email.split("@", 1)[0]
        )

        supabase_roles = await _load_supabase_roles(user_id)
        try:
            has_admin_role = await _supabase_rpc_has_role(
                access_token=access_token,
                user_id=user_id,
                role="admin",
            )
        except HTTPException:
            has_admin_role = False
        try:
            has_customer_role = await _supabase_rpc_has_role(
                access_token=access_token,
                user_id=user_id,
                role="customer",
            )
        except HTTPException:
            has_customer_role = False

        derived_role = _derive_empleado_role(
            user_payload=user_payload, supabase_roles=supabase_roles
        )
        if email in _superadmin_emails():
            derived_role = "superadmin"
        elif (
            _is_truthy(os.getenv("ASSISTANT_BOOTSTRAP_FIRST_SUPERADMIN"), default=True)
            and derived_role == "admin"
        ):
            existing_superadmins = (
                await session.execute(
                    select(func.count(Empleado.id)).where(
                        func.lower(Empleado.rol).in_(["superadmin", "super_admin"])
                    )
                )
            ).scalar_one()
            if int(existing_superadmins or 0) == 0:
                derived_role = "superadmin"
        elif has_admin_role and not _is_superadmin(derived_role):
            derived_role = "admin"
        elif has_customer_role and not _is_admin(derived_role):
            derived_role = "finanzas"

        existing_row = (
            await session.execute(
                select(Empleado).where(func.lower(Empleado.correo) == email)
            )
        ).scalar_one_or_none()

        if existing_row:
            existing_row.nombre = full_name or existing_row.nombre
            existing_row.activo = True
            if _is_superadmin(derived_role):
                existing_row.rol = "superadmin"
            elif _is_admin(derived_role) and not _is_admin(existing_row.rol):
                existing_row.rol = derived_role
            await session.commit()
            empleado = existing_row
        else:
            empleado = Empleado(
                nombre=full_name,
                correo=email,
                rol=derived_role,
                activo=True,
            )
            session.add(empleado)
            await session.commit()
            await session.refresh(empleado)

        request.session["empleado_id"] = str(empleado.id)
        request.session["rol"] = str(getattr(empleado, "rol", "empleado") or "empleado")
        request.session["nombre"] = str(getattr(empleado, "nombre", "") or "")
        request.session["supabase_user_id"] = user_id

        return {
            "ok": True,
            "empleado_id": str(empleado.id),
            "correo": empleado.correo,
            "rol": empleado.rol,
            "supabase_roles": sorted(set(supabase_roles)),
            "supabase_admin": bool(has_admin_role),
            "supabase_customer": bool(has_customer_role),
        }
    except HTTPException:
        raise
    except Exception:
        await session.rollback()
        logger.exception("Unexpected error bridging Supabase auth")
        raise HTTPException(status_code=500, detail="Unexpected processing error")


@router.post("/admin/synthetic-data/seed")
async def synthetic_data_seed(
    payload: SyntheticDataSeedRequest,
    current_empleado=Depends(get_current_empleado),
):
    if not _is_superadmin(getattr(current_empleado, "rol", None)):
        raise HTTPException(
            status_code=403, detail="Synthetic data actions require superadmin role"
        )

    args = [
        "--target",
        payload.target,
        "--seed",
        str(payload.seed),
        "--tournaments",
        str(payload.tournaments),
        "--teams-per-tournament",
        str(payload.teams_per_tournament),
        "--players-per-team",
        str(payload.players_per_team),
        "--empleados",
        str(payload.empleados),
        "--gastos-por-empleado",
        str(payload.gastos_por_empleado),
    ]
    if payload.apply:
        args.append("--apply")
    return _start_synthetic_job(
        action="seed",
        mode="apply" if payload.apply else "dry-run",
        empleado_id=current_empleado.id,
        script_name="generate_synthetic_data.py",
        args=args,
    )


@router.post("/admin/synthetic-data/cleanup")
async def synthetic_data_cleanup(
    payload: SyntheticDataCleanupRequest,
    current_empleado=Depends(get_current_empleado),
):
    if not _is_superadmin(getattr(current_empleado, "rol", None)):
        raise HTTPException(
            status_code=403, detail="Synthetic data actions require superadmin role"
        )

    args = ["--target", payload.target]
    if payload.apply:
        args.append("--apply")
    return _start_synthetic_job(
        action="cleanup",
        mode="apply" if payload.apply else "dry-run",
        empleado_id=current_empleado.id,
        script_name="cleanup_synthetic_data.py",
        args=args,
    )


@router.post("/admin/synthetic-data/reset-seed")
async def synthetic_data_reset_seed(
    payload: SyntheticDataSeedRequest,
    current_empleado=Depends(get_current_empleado),
):
    if not _is_superadmin(getattr(current_empleado, "rol", None)):
        raise HTTPException(
            status_code=403, detail="Synthetic data actions require superadmin role"
        )
    if not payload.apply:
        raise HTTPException(status_code=400, detail="reset-seed requires apply=true")

    seed_args = [
        "--target",
        payload.target,
        "--seed",
        str(payload.seed),
        "--tournaments",
        str(payload.tournaments),
        "--teams-per-tournament",
        str(payload.teams_per_tournament),
        "--players-per-team",
        str(payload.players_per_team),
        "--empleados",
        str(payload.empleados),
        "--gastos-por-empleado",
        str(payload.gastos_por_empleado),
        "--apply",
    ]
    return _start_synthetic_reset_seed_job(
        empleado_id=current_empleado.id,
        seed_args=seed_args,
    )


@router.get("/admin/synthetic-data/jobs/{job_id}")
async def synthetic_data_job_status(
    job_id: str = PathParam(...),
    current_empleado=Depends(get_current_empleado),
):
    with _SYNTHETIC_JOBS_LOCK:
        job = dict(_SYNTHETIC_JOBS.get(job_id) or {})
    if not job:
        raise HTTPException(status_code=404, detail="Synthetic job not found")
    if job.get("empleado_id") != str(getattr(current_empleado, "id", "")):
        raise HTTPException(
            status_code=403, detail="Synthetic job does not belong to current user"
        )
    return job


@router.get("/admin/synthetic-data/verify")
async def synthetic_data_verify(
    current_empleado=Depends(get_current_empleado),
    session: AsyncSession = Depends(get_db_session),
):
    if not _is_superadmin(getattr(current_empleado, "rol", None)):
        raise HTTPException(
            status_code=403, detail="Synthetic data actions require superadmin role"
        )

    supabase_counts = await _supabase_synthetic_counts()

    gastos_tournaments = (
        await session.execute(
            select(func.count(Tournament.id)).where(Tournament.name.ilike("SYNTH %"))
        )
    ).scalar() or 0
    gastos_empleados = (
        await session.execute(
            select(func.count(Empleado.id)).where(
                Empleado.correo.ilike("empleado.%@synthetic.sam.chat")
            )
        )
    ).scalar() or 0
    gastos_cuentas = (
        await session.execute(
            select(func.count(CuentaDeGastos.id)).where(
                CuentaDeGastos.referencia_base.ilike("SYNTH-%")
            )
        )
    ).scalar() or 0
    gastos_expenses = (
        await session.execute(
            select(func.count(ExpenseReport.id)).where(
                ExpenseReport.origen == "synthetic_seed"
            )
        )
    ).scalar() or 0
    gastos_conversations = (
        await session.execute(
            select(func.count(AssistantConversation.id)).where(
                AssistantConversation.title.ilike("Conversacion sintetica %")
            )
        )
    ).scalar() or 0

    gastos_messages = (
        await session.execute(
            select(func.count(AssistantMessage.id))
            .join(
                AssistantConversation,
                AssistantConversation.id == AssistantMessage.conversation_id,
            )
            .where(AssistantConversation.title.ilike("Conversacion sintetica %"))
        )
    ).scalar() or 0

    gastos_runs = (
        await session.execute(
            select(func.count(AssistantRun.id))
            .join(
                AssistantConversation,
                AssistantConversation.id == AssistantRun.conversation_id,
            )
            .where(AssistantConversation.title.ilike("Conversacion sintetica %"))
        )
    ).scalar() or 0

    return {
        "ok": True,
        "verified_at": datetime.utcnow().isoformat(),
        "supabase": supabase_counts,
        "gastos": {
            "tournaments": int(gastos_tournaments),
            "empleados": int(gastos_empleados),
            "cuentas": int(gastos_cuentas),
            "gastos": int(gastos_expenses),
            "conversations": int(gastos_conversations),
            "messages": int(gastos_messages),
            "runs": int(gastos_runs),
        },
    }


@router.get("/admin/audit/unified")
async def unified_audit_log(
    days: int = 30,
    limit: int = 200,
    email: Optional[str] = None,
    conversation_id: Optional[str] = None,
    current_empleado=Depends(get_current_empleado),
    session: AsyncSession = Depends(get_db_session),
):
    if not _is_superadmin(getattr(current_empleado, "rol", None)):
        raise HTTPException(
            status_code=403, detail="Unified audit requires superadmin role"
        )

    max_limit = max(1, min(int(limit), 1000))
    max_days = max(1, min(int(days), 365))
    since_dt = datetime.utcnow() - timedelta(days=max_days)
    since_iso = since_dt.isoformat()
    email_norm = (email or "").strip().lower()

    conversation_uuid: Optional[uuid.UUID] = None
    if conversation_id:
        try:
            conversation_uuid = uuid.UUID(conversation_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=400, detail="Invalid conversation_id"
            ) from exc

    assistant_stmt = (
        select(AssistantRun, Empleado)
        .join(Empleado, Empleado.id == AssistantRun.empleado_id)
        .where(AssistantRun.created_at >= since_dt)
        .order_by(desc(AssistantRun.created_at))
        .limit(max_limit)
    )
    if email_norm:
        assistant_stmt = assistant_stmt.where(func.lower(Empleado.correo) == email_norm)
    if conversation_uuid:
        assistant_stmt = assistant_stmt.where(
            AssistantRun.conversation_id == conversation_uuid
        )

    assistant_rows = (await session.execute(assistant_stmt)).all()
    items: List[Dict[str, Any]] = []
    assistant_count = 0
    for run, emp in assistant_rows:
        assistant_count += 1
        tool_count = len(run.tool_trace) if isinstance(run.tool_trace, list) else 0
        items.append(
            {
                "source": "assistant_runs",
                "timestamp": run.created_at.isoformat() if run.created_at else None,
                "action": "ASSISTANT_RUN",
                "actor": {
                    "empleado_id": str(getattr(emp, "id", "") or ""),
                    "nombre": getattr(emp, "nombre", None),
                    "email": getattr(emp, "correo", None),
                },
                "summary": (run.user_message or "")[:280],
                "details": {
                    "run_id": str(run.id),
                    "conversation_id": str(run.conversation_id),
                    "status": run.status,
                    "model": run.model,
                    "tool_count": tool_count,
                },
            }
        )

    auth_map = await _supabase_auth_user_map(limit=1000)
    sb_filters = {"created_at": f"gte.{since_iso}"}
    admin_rows = await _supabase_rest_rows(
        table="admin_audit_log",
        select_expr="id,created_at,action,table_name,record_id,user_id,user_email,old_values,new_values",
        filters=sb_filters,
        order="created_at.desc",
        limit=max_limit,
    )
    role_rows = await _supabase_rest_rows(
        table="role_audit_log",
        select_expr="id,created_at,user_id,changed_by,previous_role,new_role",
        filters=sb_filters,
        order="created_at.desc",
        limit=max_limit,
    )

    admin_count = 0
    for row in admin_rows:
        if not isinstance(row, dict):
            continue
        actor_email = str(row.get("user_email") or "").strip().lower() or auth_map.get(
            str(row.get("user_id") or "").strip(), ""
        )
        if email_norm and actor_email != email_norm:
            continue
        admin_count += 1
        items.append(
            {
                "source": "admin_audit_log",
                "timestamp": row.get("created_at"),
                "action": row.get("action"),
                "actor": {
                    "user_id": row.get("user_id"),
                    "email": actor_email or None,
                },
                "summary": f"{row.get('table_name') or ''}#{row.get('record_id') or ''}".strip(
                    "#"
                ),
                "details": {
                    "audit_id": row.get("id"),
                    "table_name": row.get("table_name"),
                    "record_id": row.get("record_id"),
                    "old_values": row.get("old_values"),
                    "new_values": row.get("new_values"),
                },
            }
        )

    role_count = 0
    for row in role_rows:
        if not isinstance(row, dict):
            continue
        changed_by = str(row.get("changed_by") or "").strip()
        changed_by_email = auth_map.get(changed_by, "")
        target_user = str(row.get("user_id") or "").strip()
        target_email = auth_map.get(target_user, "")
        if email_norm and email_norm not in {changed_by_email, target_email}:
            continue
        role_count += 1
        items.append(
            {
                "source": "role_audit_log",
                "timestamp": row.get("created_at"),
                "action": "ROLE_CHANGE",
                "actor": {
                    "user_id": changed_by,
                    "email": changed_by_email or None,
                },
                "summary": f"{target_email or target_user}: {row.get('previous_role')} -> {row.get('new_role')}",
                "details": {
                    "audit_id": row.get("id"),
                    "target_user_id": target_user,
                    "target_email": target_email or None,
                    "previous_role": row.get("previous_role"),
                    "new_role": row.get("new_role"),
                },
            }
        )

    items.sort(key=lambda x: str(x.get("timestamp") or ""), reverse=True)
    items = items[:max_limit]

    return {
        "ok": True,
        "generated_at": datetime.utcnow().isoformat(),
        "filters": {
            "days": max_days,
            "limit": max_limit,
            "email": email_norm or None,
            "conversation_id": str(conversation_uuid) if conversation_uuid else None,
        },
        "counts": {
            "assistant_runs": assistant_count,
            "admin_audit_log": admin_count,
            "role_audit_log": role_count,
            "returned": len(items),
        },
        "items": items,
    }


@router.post("/admin/tournaments")
async def admin_tournaments_create(
    payload: AdminTournamentSaveRequest,
    current_empleado=Depends(get_current_empleado),
):
    if not _can_manage_operations_console(current_empleado):
        raise HTTPException(
            status_code=403, detail="Tournament console requires operations admin role"
        )
    try:
        tournament_payload = _clean_tournament_payload(payload.tournament)
        created = await _supabase_rest_mutate(
            table="tournaments",
            method="POST",
            payload=tournament_payload,
        )
        tournament = created[0] if isinstance(created, list) and created else None
        if not isinstance(tournament, dict) or not tournament.get("id"):
            raise HTTPException(status_code=500, detail="Tournament was not created")

        config_payload = _clean_tournament_config_payload(
            payload.config, tournament_id=str(tournament["id"])
        )
        await _save_tournament_config(config_payload)
        return {"ok": True, "tournament": tournament}
    except HTTPException:
        raise
    except Exception:
        logger.exception(
            "Unexpected error creating assistant tournament",
            extra={"actor_id": str(current_empleado.id)},
        )
        raise HTTPException(status_code=500, detail="Unexpected processing error")


@router.patch("/admin/tournaments/{tournament_id}")
async def admin_tournaments_update(
    tournament_id: str,
    payload: AdminTournamentSaveRequest,
    current_empleado=Depends(get_current_empleado),
):
    if not _can_manage_operations_console(current_empleado):
        raise HTTPException(
            status_code=403, detail="Tournament console requires operations admin role"
        )
    try:
        uuid.UUID(tournament_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid tournament_id") from exc

    try:
        tournament_payload = _clean_tournament_payload(payload.tournament)
        tournament_payload["updated_at"] = datetime.utcnow().isoformat()
        updated = await _supabase_rest_mutate(
            table="tournaments",
            method="PATCH",
            payload=tournament_payload,
            filters={"id": f"eq.{tournament_id}"},
        )
        tournament = updated[0] if isinstance(updated, list) and updated else None
        if not isinstance(tournament, dict):
            raise HTTPException(status_code=404, detail="Tournament not found")

        config_payload = _clean_tournament_config_payload(
            payload.config, tournament_id=tournament_id
        )
        await _save_tournament_config(config_payload)
        return {"ok": True, "tournament": tournament}
    except HTTPException:
        raise
    except Exception:
        logger.exception(
            "Unexpected error updating assistant tournament",
            extra={"actor_id": str(current_empleado.id), "tournament_id": tournament_id},
        )
        raise HTTPException(status_code=500, detail="Unexpected processing error")


@router.get("/admin/invitations")
async def admin_invitations_list(
    current_empleado=Depends(get_current_empleado),
):
    if not _can_manage_operations_console(current_empleado):
        raise HTTPException(
            status_code=403, detail="Invitations console requires operations admin role"
        )

    tournaments = await _supabase_rest_rows(
        table="tournaments",
        select_expr="id,name,slug",
        order="name.asc",
        limit=500,
    )
    invitations = await _supabase_rest_rows(
        table="invitations",
        select_expr="id,code,tournament_id,max_uses,current_uses,is_active,created_at,expires_at,notes,tournaments(name)",
        order="created_at.desc",
        limit=1000,
    )
    return {
        "ok": True,
        "tournaments": tournaments,
        "invitations": invitations,
    }


@router.post("/admin/invitations")
async def admin_invitations_create(
    payload: InvitationCreateRequest,
    current_empleado=Depends(get_current_empleado),
):
    if not _can_manage_operations_console(current_empleado):
        raise HTTPException(
            status_code=403, detail="Invitations console requires operations admin role"
        )

    tournament_id = str(payload.tournament_id or "").strip()
    try:
        uuid.UUID(tournament_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid tournament_id") from exc

    try:
        codes = await _generate_invitation_codes(quantity=int(payload.quantity))
        rows = [
            {
                "code": code,
                "tournament_id": tournament_id,
                "max_uses": int(payload.max_uses),
                "notes": (payload.notes or "").strip() or None,
                "created_by": None,
            }
            for code in codes
        ]
        created = await _supabase_rest_mutate(
            table="invitations",
            method="POST",
            payload=rows,
        )
        return {
            "ok": True,
            "created": created if isinstance(created, list) else [],
            "count": len(rows),
        }
    except HTTPException:
        raise
    except Exception:
        logger.exception(
            "Unexpected error creating assistant invitations",
            extra={"actor_id": str(current_empleado.id), "tournament_id": tournament_id},
        )
        raise HTTPException(status_code=500, detail="Unexpected processing error")


@router.post("/admin/invitations/{invitation_id}/toggle")
async def admin_invitations_toggle(
    invitation_id: str,
    current_empleado=Depends(get_current_empleado),
):
    if not _can_manage_operations_console(current_empleado):
        raise HTTPException(
            status_code=403, detail="Invitations console requires operations admin role"
        )

    try:
        uuid.UUID(invitation_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid invitation_id") from exc

    try:
        rows = await _supabase_rest_rows(
            table="invitations",
            select_expr="id,is_active",
            filters={"id": f"eq.{invitation_id}"},
            limit=1,
        )
        if not rows:
            raise HTTPException(status_code=404, detail="Invitation not found")
        current_value = bool(rows[0].get("is_active"))
        updated = await _supabase_rest_mutate(
            table="invitations",
            method="PATCH",
            payload={"is_active": not current_value},
            filters={"id": f"eq.{invitation_id}"},
        )
        return {
            "ok": True,
            "invitation": (
                updated[0]
                if isinstance(updated, list) and updated
                else {"id": invitation_id, "is_active": (not current_value)}
            ),
        }
    except HTTPException:
        raise
    except Exception:
        logger.exception(
            "Unexpected error toggling assistant invitation",
            extra={"actor_id": str(current_empleado.id), "invitation_id": invitation_id},
        )
        raise HTTPException(status_code=500, detail="Unexpected processing error")


@router.delete("/admin/invitations/{invitation_id}")
async def admin_invitations_delete(
    invitation_id: str,
    current_empleado=Depends(get_current_empleado),
):
    if not _can_manage_operations_console(current_empleado):
        raise HTTPException(
            status_code=403, detail="Invitations console requires operations admin role"
        )

    try:
        uuid.UUID(invitation_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid invitation_id") from exc

    try:
        deleted = await _supabase_rest_mutate(
            table="invitations",
            method="DELETE",
            payload=None,
            filters={"id": f"eq.{invitation_id}"},
        )
        return {
            "ok": True,
            "deleted": deleted if isinstance(deleted, list) else [],
        }
    except HTTPException:
        raise
    except Exception:
        logger.exception(
            "Unexpected error deleting assistant invitation",
            extra={"actor_id": str(current_empleado.id), "invitation_id": invitation_id},
        )
        raise HTTPException(status_code=500, detail="Unexpected processing error")


@router.get("/admin/email/campaigns")
async def admin_email_campaigns_list(
    tournament_id: Optional[str] = Query(default=None),
    current_empleado=Depends(get_current_empleado),
):
    if not _can_manage_operations_console(current_empleado):
        raise HTTPException(
            status_code=403, detail="Email console requires operations admin role"
        )

    tournament_id_clean = str(tournament_id or "").strip()
    filters: Dict[str, str] = {}
    if tournament_id_clean:
        try:
            uuid.UUID(tournament_id_clean)
        except ValueError as exc:
            raise HTTPException(
                status_code=400, detail="Invalid tournament_id"
            ) from exc
        filters["tournament_id"] = f"eq.{tournament_id_clean}"

    scheduled = await _supabase_rest_rows(
        table="scheduled_emails",
        select_expr="id,scheduled_at,subject,recipients,status,sent_at,error_message,created_at,tournament_id",
        filters=filters or None,
        order="scheduled_at.asc",
        limit=1000,
    )
    return {
        "ok": True,
        "scheduled_emails": scheduled,
    }


@router.post("/admin/email/campaigns/send")
async def admin_email_campaigns_send(
    payload: EmailSendRequest,
    current_empleado=Depends(get_current_empleado),
):
    if not _can_manage_operations_console(current_empleado):
        raise HTTPException(
            status_code=403, detail="Email console requires operations admin role"
        )

    tournament_id_clean = str(payload.tournament_id or "").strip()
    if not tournament_id_clean:
        raise HTTPException(status_code=400, detail="tournament_id is required")
    try:
        uuid.UUID(tournament_id_clean)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid tournament_id") from exc

    try:
        result = await _send_email_campaign_now(
            recipients=payload.recipients,
            subject=payload.subject,
            html_content=payload.html_content,
            text_content=payload.text_content,
        )
        return {
            "ok": True,
            "recipient_count": len(payload.recipients),
            "provider_status": result.get("status"),
        }
    except HTTPException:
        raise
    except Exception:
        logger.exception(
            "Unexpected error sending assistant email campaign",
            extra={"actor_id": str(current_empleado.id), "tournament_id": tournament_id_clean},
        )
        raise HTTPException(status_code=500, detail="Unexpected processing error")


@router.post("/admin/email/campaigns/schedule")
async def admin_email_campaigns_schedule(
    payload: EmailScheduleRequest,
    current_empleado=Depends(get_current_empleado),
):
    if not _can_manage_operations_console(current_empleado):
        raise HTTPException(
            status_code=403, detail="Email console requires operations admin role"
        )

    tournament_id_clean = str(payload.tournament_id or "").strip()
    if not tournament_id_clean:
        raise HTTPException(status_code=400, detail="tournament_id is required")
    try:
        uuid.UUID(tournament_id_clean)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid tournament_id") from exc

    scheduled_at = payload.scheduled_at
    now_ref = (
        datetime.now(scheduled_at.tzinfo) if scheduled_at.tzinfo else datetime.utcnow()
    )
    if scheduled_at <= now_ref:
        raise HTTPException(
            status_code=400, detail="scheduled_at must be in the future"
        )

    try:
        rows = await _supabase_rest_mutate(
            table="scheduled_emails",
            method="POST",
            payload={
                "scheduled_at": scheduled_at.isoformat(),
                "subject": payload.subject.strip(),
                "html_content": payload.html_content,
                "text_content": (
                    payload.text_content
                    or _email_plain_text_from_html(payload.html_content)
                ).strip()
                or None,
                "recipients": [recipient.model_dump() for recipient in payload.recipients],
                "created_by": str(current_empleado.id),
                "tournament_id": tournament_id_clean,
            },
        )
        created = rows[0] if isinstance(rows, list) and rows else None
        return {
            "ok": True,
            "scheduled_email": created,
        }
    except HTTPException:
        raise
    except Exception:
        logger.exception(
            "Unexpected error scheduling assistant email campaign",
            extra={"actor_id": str(current_empleado.id), "tournament_id": tournament_id_clean},
        )
        raise HTTPException(status_code=500, detail="Unexpected processing error")


@router.post("/admin/email/campaigns/scheduled/{scheduled_email_id}/cancel")
async def admin_email_campaigns_cancel(
    scheduled_email_id: str,
    tournament_id: Optional[str] = Query(default=None),
    current_empleado=Depends(get_current_empleado),
):
    if not _can_manage_operations_console(current_empleado):
        raise HTTPException(
            status_code=403, detail="Email console requires operations admin role"
        )

    try:
        uuid.UUID(scheduled_email_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail="Invalid scheduled_email_id"
        ) from exc

    filters = {"id": f"eq.{scheduled_email_id}"}
    tournament_id_clean = str(tournament_id or "").strip()
    if tournament_id_clean:
        try:
            uuid.UUID(tournament_id_clean)
        except ValueError as exc:
            raise HTTPException(
                status_code=400, detail="Invalid tournament_id"
            ) from exc
        filters["tournament_id"] = f"eq.{tournament_id_clean}"

    try:
        updated = await _supabase_rest_mutate(
            table="scheduled_emails",
            method="PATCH",
            payload={"status": "cancelled"},
            filters=filters,
        )
        return {
            "ok": True,
            "scheduled_email": (
                updated[0] if isinstance(updated, list) and updated else None
            ),
        }
    except HTTPException:
        raise
    except Exception:
        logger.exception(
            "Unexpected error cancelling assistant scheduled email",
            extra={"actor_id": str(current_empleado.id), "scheduled_email_id": scheduled_email_id},
        )
        raise HTTPException(status_code=500, detail="Unexpected processing error")


@router.delete("/admin/email/campaigns/scheduled/{scheduled_email_id}")
async def admin_email_campaigns_delete(
    scheduled_email_id: str,
    tournament_id: Optional[str] = Query(default=None),
    current_empleado=Depends(get_current_empleado),
):
    if not _can_manage_operations_console(current_empleado):
        raise HTTPException(
            status_code=403, detail="Email console requires operations admin role"
        )

    try:
        uuid.UUID(scheduled_email_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail="Invalid scheduled_email_id"
        ) from exc

    filters = {"id": f"eq.{scheduled_email_id}"}
    tournament_id_clean = str(tournament_id or "").strip()
    if tournament_id_clean:
        try:
            uuid.UUID(tournament_id_clean)
        except ValueError as exc:
            raise HTTPException(
                status_code=400, detail="Invalid tournament_id"
            ) from exc
        filters["tournament_id"] = f"eq.{tournament_id_clean}"

    try:
        deleted = await _supabase_rest_mutate(
            table="scheduled_emails",
            method="DELETE",
            filters=filters,
        )
        return {
            "ok": True,
            "deleted": deleted if isinstance(deleted, list) else [],
        }
    except HTTPException:
        raise
    except Exception:
        logger.exception(
            "Unexpected error deleting assistant scheduled email",
            extra={"actor_id": str(current_empleado.id), "scheduled_email_id": scheduled_email_id},
        )
        raise HTTPException(status_code=500, detail="Unexpected processing error")


@router.get("/conversations/{conversation_id}/messages")
async def list_messages(
    conversation_id: str = PathParam(...),
    current_empleado=Depends(get_current_empleado),
    session: AsyncSession = Depends(get_db_session),
):
    conversation = await _load_conversation(
        session, conversation_id=conversation_id, empleado_id=current_empleado.id
    )
    rows = (
        (
            await session.execute(
                select(AssistantMessage)
                .where(AssistantMessage.conversation_id == conversation.id)
                .order_by(AssistantMessage.created_at.asc())
                .limit(200)
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": str(m.id),
            "role": m.role,
            "content": m.content,
            "tool_name": m.tool_name,
            "tool_payload": m.tool_payload,
            "created_at": m.created_at.isoformat(),
        }
        for m in rows
    ]


@router.post("/reports/export")
async def export_assistant_report(
    payload: AssistantReportExportRequest,
    current_empleado=Depends(get_current_empleado),
    session: AsyncSession = Depends(get_db_session),
):
    conversation = await _load_conversation(
        session,
        conversation_id=payload.conversation_id,
        empleado_id=current_empleado.id,
    )

    report_data = payload.report_data
    if report_data is None:
        run = None
        if payload.run_id:
            try:
                run_uuid = uuid.UUID(payload.run_id)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="Invalid run_id") from exc
            run = (
                await session.execute(
                    select(AssistantRun).where(
                        AssistantRun.id == run_uuid,
                        AssistantRun.conversation_id == conversation.id,
                        AssistantRun.empleado_id == current_empleado.id,
                    )
                )
            ).scalar_one_or_none()
        else:
            run = (
                await session.execute(
                    select(AssistantRun)
                    .where(
                        AssistantRun.conversation_id == conversation.id,
                        AssistantRun.empleado_id == current_empleado.id,
                        AssistantRun.status == "completed",
                    )
                    .order_by(desc(AssistantRun.created_at))
                    .limit(1)
                )
            ).scalar_one_or_none()

        if not run:
            raise HTTPException(status_code=404, detail="Run not found for export")
        report_data = _extract_report_payload_from_trace(run.tool_trace)
        if report_data is None:
            raise HTTPException(
                status_code=400, detail="Selected run has no exportable report data"
            )

    if payload.format == "csv":
        data = _report_csv_bytes(report_data)
        filename = _sanitize_filename(
            payload.filename, default_stem="assistant_report", ext="csv"
        )
        return Response(
            content=data,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    if payload.format == "pdf":
        data = _report_pdf_bytes(report_data)
        filename = _sanitize_filename(
            payload.filename, default_stem="assistant_report", ext="pdf"
        )
        return Response(
            content=data,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    raise HTTPException(status_code=400, detail="Unsupported export format")


@router.post("/reports/executive")
async def assistant_reports_executive(
    payload: AssistantExecutiveDashboardRequest,
    current_empleado=Depends(get_current_empleado),
    session: AsyncSession = Depends(get_db_session),
):
    if not _is_admin(getattr(current_empleado, "rol", None)):
        raise HTTPException(status_code=403, detail="Requires admin or superadmin role")
    return await _build_executive_dashboard(
        session=session,
        year=payload.year,
        bi_scope=payload.bi_scope,
        bi_segment=payload.bi_segment,
    )


@router.post("/reports/alerts")
async def assistant_reports_alerts(
    payload: AssistantAlertsRequest,
    current_empleado=Depends(get_current_empleado),
    session: AsyncSession = Depends(get_db_session),
):
    if not _is_admin(getattr(current_empleado, "rol", None)):
        raise HTTPException(status_code=403, detail="Requires admin or superadmin role")
    return await _build_automatic_alerts(
        session=session,
        year=payload.year,
        bi_scope=payload.bi_scope,
        bi_segment=payload.bi_segment,
        spike_ratio=payload.spike_ratio,
    )


def _finance_entity_key(value: Any) -> str:
    text_value = str(value or "").strip().lower()
    if not text_value:
        return ""
    normalized = unicodedata.normalize("NFKD", text_value)
    ascii_value = "".join(
        char for char in normalized if not unicodedata.combining(char)
    )
    return re.sub(r"\s+", " ", ascii_value).strip()


def _finance_amount(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _finance_blank_summary(entity_name: str) -> Dict[str, Any]:
    return {
        "entity_name": entity_name,
        "documents_count": 0,
        "solicitudes_count": 0,
        "paid_documents_count": 0,
        "closed_documents_count": 0,
        "requested_amount": 0.0,
        "document_total_amount": 0.0,
        "paid_amount": 0.0,
        "expenses_count": 0,
        "expense_amount": 0.0,
        "reimbursed_amount": 0.0,
        "pending_expense_amount": 0.0,
        "latest_documents": [],
        "latest_expenses": [],
        "unmatched_documents": 0,
        "unmatched_expenses": 0,
    }


def _finance_match_entity(row: Dict[str, Any], entities: List[str]) -> Optional[str]:
    candidates = [
        row.get("entidad_region"),
        row.get("proveedor_nombre"),
        row.get("notas"),
        row.get("concepto_pago"),
        row.get("numero_referencia"),
        row.get("hospedaje_entidad_fiscal"),
        row.get("proyecto"),
        row.get("fase_torneo"),
        row.get("concepto"),
        row.get("nombre_enviador"),
    ]
    searchable = " ".join(_finance_entity_key(value) for value in candidates if value)
    if not searchable:
        return None

    for entity_name in entities:
        entity_key = _finance_entity_key(entity_name)
        if not entity_key:
            continue
        if entity_key in searchable:
            return entity_name
        # Covers cases like "CDMX" stored inside a longer canonical entity field.
        for candidate in candidates:
            candidate_key = _finance_entity_key(candidate)
            if candidate_key and candidate_key in entity_key:
                return entity_name
    return None


def _finance_is_national_phase(row: Dict[str, Any]) -> bool:
    text_value = " ".join(
        _finance_entity_key(row.get(key))
        for key in (
            "fase_torneo",
            "concepto",
            "concepto_pago",
            "notas",
            "proyecto",
            "numero_referencia",
            "proveedor_nombre",
        )
        if row.get(key)
    )
    if not text_value:
        return False
    national_markers = (
        "nacional",
        "nacionales",
        "final",
        "finales",
        "viaje de campeones",
        "sede nacional",
    )
    return any(marker in text_value for marker in national_markers)


async def _build_entity_finance_summaries(
    *,
    session: AsyncSession,
    tournament_uuid: uuid.UUID,
    entities: List[str],
    tournament_name: Optional[str],
) -> Dict[str, Any]:
    summaries = {name: _finance_blank_summary(name) for name in entities}
    tournament_like = (
        f"%{str(tournament_name or '').strip()}%" if tournament_name else None
    )

    document_rows = (
        (
            await session.execute(
                text(
                    """
                SELECT
                  d.id::text,
                  d.tipo,
                  d.numero_referencia,
                  d.estado,
                  d.monto_solicitado,
                  d.monto_total,
                  d.fecha_pago,
                  d.concepto_pago,
                  d.notas,
                  d.creado_en,
                  d.pagado_en,
                  pc.nombre AS proveedor_nombre,
                  pc.entidad_region
                FROM documentos d
                LEFT JOIN proveedores_clientes pc ON pc.id = d.proveedor_cliente_id
                WHERE d.torneo_id = :tournament_id
                ORDER BY d.creado_en DESC
                LIMIT 1000
                """
                ),
                {"tournament_id": tournament_uuid},
            )
        )
        .mappings()
        .all()
    )

    for row_mapping in document_rows:
        row = dict(row_mapping)
        entity_name = _finance_match_entity(row, entities)
        if entity_name is None:
            for summary in summaries.values():
                summary["unmatched_documents"] += 1
            continue

        summary = summaries[entity_name]
        amount_requested = _finance_amount(row.get("monto_solicitado"))
        amount_total = _finance_amount(row.get("monto_total"))
        effective_amount = amount_total or amount_requested
        state = str(row.get("estado") or "").strip().lower()
        doc_type = str(row.get("tipo") or "").strip().upper()

        summary["documents_count"] += 1
        if "SOLICITUD" in doc_type:
            summary["solicitudes_count"] += 1
        if state == "pagado":
            summary["paid_documents_count"] += 1
            summary["paid_amount"] += effective_amount
        if state == "cerrado":
            summary["closed_documents_count"] += 1
            summary["paid_amount"] += effective_amount
        summary["requested_amount"] += amount_requested
        summary["document_total_amount"] += amount_total
        if len(summary["latest_documents"]) < 5:
            summary["latest_documents"].append(
                {
                    "id": row.get("id"),
                    "tipo": row.get("tipo"),
                    "referencia": row.get("numero_referencia"),
                    "estado": row.get("estado"),
                    "monto": effective_amount,
                    "proveedor": row.get("proveedor_nombre"),
                    "fecha": (
                        (
                            row.get("fecha_pago")
                            or row.get("pagado_en")
                            or row.get("creado_en")
                        ).isoformat()
                        if (
                            row.get("fecha_pago")
                            or row.get("pagado_en")
                            or row.get("creado_en")
                        )
                        else None
                    ),
                    "concepto": row.get("concepto_pago"),
                }
            )

    expense_params: Dict[str, Any] = {
        "tournament_id": tournament_uuid,
        "tournament_name": tournament_like,
    }
    expense_rows = (
        (
            await session.execute(
                text(
                    """
                SELECT
                  e.id::text,
                  e.numero_referencia,
                  e.proyecto,
                  e.concepto,
                  e.gasto_cantidad,
                  e.fecha,
                  e.fase_torneo,
                  e.estado_reembolso,
                  e.estado_factura,
                  e.nombre_enviador,
                  e.hospedaje_entidad_fiscal,
                  cg.torneo_id::text AS cuenta_torneo_id,
                  d.torneo_id::text AS documento_torneo_id,
                  sd.torneo_id::text AS solicitud_torneo_id,
                  idoc.torneo_id::text AS informe_torneo_id
                FROM expense_reports e
                LEFT JOIN cuentas_de_gastos cg ON cg.id = e.cuenta_gastos_id
                LEFT JOIN documentos d ON d.id = e.documento_id
                LEFT JOIN documentos sd ON sd.id = e.solicitud_documento_id
                LEFT JOIN documentos idoc ON idoc.id = e.informe_documento_id
                WHERE COALESCE(e.estado_gasto, 'activo') <> 'cancelado'
                  AND (
                    cg.torneo_id = :tournament_id
                    OR d.torneo_id = :tournament_id
                    OR sd.torneo_id = :tournament_id
                    OR idoc.torneo_id = :tournament_id
                    OR (
                      CAST(:tournament_name AS TEXT) IS NOT NULL
                      AND e.proyecto ILIKE CAST(:tournament_name AS TEXT)
                    )
                  )
                ORDER BY e.fecha DESC
                LIMIT 1000
                """
                ),
                expense_params,
            )
        )
        .mappings()
        .all()
    )

    for row_mapping in expense_rows:
        row = dict(row_mapping)
        entity_name = _finance_match_entity(row, entities)
        if entity_name is None:
            for summary in summaries.values():
                summary["unmatched_expenses"] += 1
            continue

        summary = summaries[entity_name]
        amount = _finance_amount(row.get("gasto_cantidad"))
        reimbursement_state = str(row.get("estado_reembolso") or "").strip().lower()
        summary["expenses_count"] += 1
        summary["expense_amount"] += amount
        if reimbursement_state == "pagado":
            summary["reimbursed_amount"] += amount
        else:
            summary["pending_expense_amount"] += amount
        if len(summary["latest_expenses"]) < 5:
            summary["latest_expenses"].append(
                {
                    "id": row.get("id"),
                    "referencia": row.get("numero_referencia"),
                    "proyecto": row.get("proyecto"),
                    "concepto": row.get("concepto"),
                    "monto": amount,
                    "fecha": row.get("fecha").isoformat() if row.get("fecha") else None,
                    "fase": row.get("fase_torneo"),
                    "estado_reembolso": row.get("estado_reembolso"),
                    "estado_factura": row.get("estado_factura"),
                }
            )

    return {
        "ok": True,
        "tournament_id": str(tournament_uuid),
        "tournament_name": tournament_name,
        "entities": list(summaries.values()),
        "matching": {
            "strategy": "torneo_id/cuenta_gastos/proyecto plus entidad_region/text match",
            "note": (
                "Los totales se calculan desde documentos, proveedores, "
                "cuentas_de_gastos y expense_reports existentes."
            ),
        },
    }


async def _build_national_finance_summary(
    *,
    session: AsyncSession,
    tournament_uuid: uuid.UUID,
    tournament_name: Optional[str],
) -> Dict[str, Any]:
    tournament_like = (
        f"%{str(tournament_name or '').strip()}%" if tournament_name else None
    )
    summary = _finance_blank_summary("Fase nacional")

    document_rows = (
        (
            await session.execute(
                text(
                    """
                SELECT
                  d.id::text,
                  d.tipo,
                  d.numero_referencia,
                  d.estado,
                  d.monto_solicitado,
                  d.monto_total,
                  d.fecha_pago,
                  d.concepto_pago,
                  d.notas,
                  d.creado_en,
                  d.pagado_en,
                  pc.nombre AS proveedor_nombre,
                  pc.entidad_region
                FROM documentos d
                LEFT JOIN proveedores_clientes pc ON pc.id = d.proveedor_cliente_id
                WHERE d.torneo_id = :tournament_id
                ORDER BY d.creado_en DESC
                LIMIT 1000
                """
                ),
                {"tournament_id": tournament_uuid},
            )
        )
        .mappings()
        .all()
    )

    for row_mapping in document_rows:
        row = dict(row_mapping)
        if not _finance_is_national_phase(row):
            summary["unmatched_documents"] += 1
            continue

        amount_requested = _finance_amount(row.get("monto_solicitado"))
        amount_total = _finance_amount(row.get("monto_total"))
        effective_amount = amount_total or amount_requested
        state = str(row.get("estado") or "").strip().lower()
        doc_type = str(row.get("tipo") or "").strip().upper()

        summary["documents_count"] += 1
        if "SOLICITUD" in doc_type:
            summary["solicitudes_count"] += 1
        if state == "pagado":
            summary["paid_documents_count"] += 1
            summary["paid_amount"] += effective_amount
        if state == "cerrado":
            summary["closed_documents_count"] += 1
            summary["paid_amount"] += effective_amount
        summary["requested_amount"] += amount_requested
        summary["document_total_amount"] += amount_total
        if len(summary["latest_documents"]) < 5:
            date_value = (
                row.get("fecha_pago") or row.get("pagado_en") or row.get("creado_en")
            )
            summary["latest_documents"].append(
                {
                    "id": row.get("id"),
                    "tipo": row.get("tipo"),
                    "referencia": row.get("numero_referencia"),
                    "estado": row.get("estado"),
                    "monto": effective_amount,
                    "proveedor": row.get("proveedor_nombre"),
                    "fecha": date_value.isoformat() if date_value else None,
                    "concepto": row.get("concepto_pago"),
                }
            )

    expense_rows = (
        (
            await session.execute(
                text(
                    """
                SELECT
                  e.id::text,
                  e.numero_referencia,
                  e.proyecto,
                  e.concepto,
                  e.gasto_cantidad,
                  e.fecha,
                  e.fase_torneo,
                  e.estado_reembolso,
                  e.estado_factura,
                  e.nombre_enviador,
                  e.hospedaje_entidad_fiscal,
                  cg.torneo_id::text AS cuenta_torneo_id,
                  d.torneo_id::text AS documento_torneo_id,
                  sd.torneo_id::text AS solicitud_torneo_id,
                  idoc.torneo_id::text AS informe_torneo_id
                FROM expense_reports e
                LEFT JOIN cuentas_de_gastos cg ON cg.id = e.cuenta_gastos_id
                LEFT JOIN documentos d ON d.id = e.documento_id
                LEFT JOIN documentos sd ON sd.id = e.solicitud_documento_id
                LEFT JOIN documentos idoc ON idoc.id = e.informe_documento_id
                WHERE COALESCE(e.estado_gasto, 'activo') <> 'cancelado'
                  AND (
                    cg.torneo_id = :tournament_id
                    OR d.torneo_id = :tournament_id
                    OR sd.torneo_id = :tournament_id
                    OR idoc.torneo_id = :tournament_id
                    OR (
                      CAST(:tournament_name AS TEXT) IS NOT NULL
                      AND e.proyecto ILIKE CAST(:tournament_name AS TEXT)
                    )
                  )
                ORDER BY e.fecha DESC
                LIMIT 1000
                """
                ),
                {"tournament_id": tournament_uuid, "tournament_name": tournament_like},
            )
        )
        .mappings()
        .all()
    )

    for row_mapping in expense_rows:
        row = dict(row_mapping)
        if not _finance_is_national_phase(row):
            summary["unmatched_expenses"] += 1
            continue

        amount = _finance_amount(row.get("gasto_cantidad"))
        reimbursement_state = str(row.get("estado_reembolso") or "").strip().lower()
        summary["expenses_count"] += 1
        summary["expense_amount"] += amount
        if reimbursement_state == "pagado":
            summary["reimbursed_amount"] += amount
        else:
            summary["pending_expense_amount"] += amount
        if len(summary["latest_expenses"]) < 5:
            summary["latest_expenses"].append(
                {
                    "id": row.get("id"),
                    "referencia": row.get("numero_referencia"),
                    "proyecto": row.get("proyecto"),
                    "concepto": row.get("concepto"),
                    "monto": amount,
                    "fecha": row.get("fecha").isoformat() if row.get("fecha") else None,
                    "fase": row.get("fase_torneo"),
                    "estado_reembolso": row.get("estado_reembolso"),
                    "estado_factura": row.get("estado_factura"),
                }
            )

    return {
        "ok": True,
        "tournament_id": str(tournament_uuid),
        "tournament_name": tournament_name,
        "summary": summary,
        "matching": {
            "strategy": "torneo_id/cuenta_gastos/proyecto plus national/final phase text markers",
            "note": (
                "La fase nacional se detecta por fase_torneo o texto operativo "
                "con marcadores como nacional, finales o viaje de campeones."
            ),
        },
    }


def _expediente_profile_completion(profile: Dict[str, Any]) -> Dict[str, Any]:
    fields = [
        "ps_responsible_name",
        "entity_responsible_name",
        "entity_responsible_phone",
        "entity_responsible_email",
        "entity_responsible_birth_date",
        "entity_responsible_partner_name",
        "entity_responsible_partner_birth_date",
        "state_phase_description",
        "uniform_delivery_date",
        "uniform_delivery_place",
        "national_travel_departure_date",
        "national_travel_return_date",
    ]
    present = [
        field for field in fields if str((profile or {}).get(field) or "").strip()
    ]
    return {
        "present": len(present),
        "total": len(fields),
        "percentage": round((len(present) / len(fields)) * 100) if fields else 0,
        "missing": [field for field in fields if field not in present],
    }


def _expediente_national_completion(profile: Dict[str, Any]) -> Dict[str, Any]:
    fields = [
        "tournament_category_dates_duration_city",
        "hotels_and_bed_nights",
        "meals_breakdown",
        "sports_unit_venue",
        "courts_count_and_types",
        "medical_services_description",
        "accidents_with_transfers",
        "staff_travel_costs",
        "hotel_payments_advance_settlement",
        "supplier_payments_for_finals",
        "medical_services_costs",
        "insurance_costs",
        "on_site_brand_activation_suppliers",
        "sponsor_related_visitors",
        "marketing_activity_reports_with_photos",
    ]
    present = [
        field for field in fields if str((profile or {}).get(field) or "").strip()
    ]
    return {
        "present": len(present),
        "total": len(fields),
        "percentage": round((len(present) / len(fields)) * 100) if fields else 0,
        "missing": [field for field in fields if field not in present],
    }


def _expediente_age_bucket(birth_date_value: Any) -> str:
    raw_value = str(birth_date_value or "").strip()
    if not raw_value:
        return "Sin edad"
    try:
        parsed = date.fromisoformat(raw_value[:10])
    except ValueError:
        return "Sin edad"
    today = date.today()
    age = (
        today.year
        - parsed.year
        - ((today.month, today.day) < (parsed.month, parsed.day))
    )
    if age < 0 or age > 120:
        return "Sin edad"
    return str(age)


async def _download_tournament_evidence_file(file_path: str) -> bytes:
    base_url = _supabase_base_url()
    service_key = (os.getenv("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not base_url or not service_key:
        raise HTTPException(
            status_code=500, detail="Supabase service key is not configured"
        )
    safe_path = urllib_parse.quote(str(file_path or "").strip(), safe="/")
    if not safe_path:
        raise HTTPException(status_code=400, detail="Evidence file_path is empty")
    url = f"{base_url}/storage/v1/object/tournament-folder-evidence/{safe_path}"
    return await asyncio.to_thread(
        _sync_fetch_bytes,
        url,
        {
            "Authorization": f"Bearer {service_key}",
            "apikey": service_key,
            "Accept": "application/octet-stream",
        },
        30,
    )


def _extract_contract_text_from_bytes(
    *,
    raw: bytes,
    filename: Optional[str],
    mime_type: Optional[str],
) -> str:
    return extract_document_text_from_bytes(
        raw=raw,
        filename=filename,
        mime_type=mime_type,
        max_bytes=25 * 1024 * 1024,
        allow_pdf=True,
        allow_spreadsheet=True,
    )


def _json_object_from_llm_text(value: str) -> Dict[str, Any]:
    text_value = str(value or "").strip()
    if not text_value:
        return {}
    if text_value.startswith("```"):
        text_value = re.sub(r"^```(?:json)?", "", text_value, flags=re.I).strip()
        text_value = re.sub(r"```$", "", text_value).strip()
    match = re.search(r"\{.*\}", text_value, flags=re.S)
    candidate = match.group(0) if match else text_value
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return {
            "summary": text_value[:4000],
            "responsibilities": [],
            "payment_dates": [],
            "collection_dates": [],
            "operational_milestones": [],
            "risks_or_assumptions": [
                "La respuesta no vino en JSON estricto; revisar manualmente."
            ],
        }
    return parsed if isinstance(parsed, dict) else {}


def _draft_date(value: Any) -> Optional[str]:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10]).isoformat()
    except ValueError:
        return None


def _draft_amount(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return round(float(value), 2)
    raw = re.sub(r"[^0-9.\-]", "", str(value))
    try:
        return round(float(raw), 2)
    except ValueError:
        return None


def _draft_str(value: Any, limit: int = 500) -> Optional[str]:
    raw = str(value or "").strip()
    return raw[:limit] if raw else None


def _draft_confidence(value: Any) -> Optional[str]:
    raw = str(value or "").strip().lower()
    return raw if raw in {"high", "medium", "low"} else None


def _commitments_from_draft(
    *,
    tournament_id: str,
    draft: Dict[str, Any],
) -> List[Dict[str, Any]]:
    payload = draft.get("draft_payload") or {}
    if not isinstance(payload, dict):
        payload = {}
    scope = str(draft.get("scope") or "tournament").strip() or "tournament"
    entity_name = _draft_str(draft.get("entity_name"), 200)
    base = {
        "tournament_id": tournament_id,
        "source_draft_id": draft.get("id"),
        "source_evidence_id": draft.get("evidence_id"),
        "scope": (
            scope if scope in {"tournament", "entity", "national"} else "tournament"
        ),
        "entity_name": entity_name,
        "status": "open",
    }
    rows: List[Dict[str, Any]] = []

    def add(item_type: str, index: int, title: str, raw: Dict[str, Any]) -> None:
        row = {
            **base,
            "source_item_index": index,
            "item_type": item_type,
            "title": title[:500],
            "owner_name": _draft_str(raw.get("owner") or raw.get("payer"), 300),
            "counterparty_name": _draft_str(
                raw.get("payee") or raw.get("counterparty"), 300
            ),
            "due_date": _draft_date(raw.get("due_date") or raw.get("date")),
            "amount": _draft_amount(raw.get("amount")),
            "currency": _draft_str(raw.get("currency"), 20),
            "confidence": _draft_confidence(raw.get("confidence")),
            "source_quote": _draft_str(raw.get("source_quote"), 1000),
            "notes": _draft_str(raw.get("notes") or raw.get("condition"), 2000),
            "payload": raw,
        }
        rows.append(row)

    for index, item in enumerate(payload.get("responsibilities") or []):
        if isinstance(item, dict):
            title = (
                _draft_str(item.get("responsibility"), 500)
                or "Responsabilidad sin titulo"
            )
            add("responsibility", index, title, item)
    for index, item in enumerate(payload.get("payment_dates") or [], start=1000):
        if isinstance(item, dict):
            title = _draft_str(item.get("concept"), 500) or "Pago planeado sin concepto"
            add("payment", index, title, item)
    for index, item in enumerate(payload.get("collection_dates") or [], start=2000):
        if isinstance(item, dict):
            title = (
                _draft_str(item.get("concept"), 500) or "Cobro planeado sin concepto"
            )
            add("collection", index, title, item)
    for index, item in enumerate(
        payload.get("operational_milestones") or [], start=3000
    ):
        if isinstance(item, dict):
            title = _draft_str(item.get("name"), 500) or "Hito operativo sin nombre"
            add("milestone", index, title, item)
    for index, item in enumerate(payload.get("risks_or_assumptions") or [], start=4000):
        title = _draft_str(item, 500)
        if title:
            add("risk", index, title, {"notes": title, "confidence": "medium"})
    for index, item in enumerate(
        payload.get("recommended_next_actions") or [], start=5000
    ):
        title = _draft_str(item, 500)
        if title:
            add("next_action", index, title, {"notes": title, "confidence": "medium"})

    return rows


async def _generate_tournament_contract_draft_payload(
    *,
    tournament: Dict[str, Any],
    evidence: Optional[Dict[str, Any]],
    source_text: str,
    additional_text: Optional[str],
    assistant_mode: Optional[str],
) -> Dict[str, Any]:
    evidence_context = evidence or {}
    prompt = (
        "Extrae un borrador operativo/financiero desde este contrato, convenio, brief o especificacion de torneo. "
        "No inventes datos. Si algo no esta claro, deja null y agrega riesgo/supuesto. "
        "Devuelve EXCLUSIVAMENTE JSON valido con esta forma:\n"
        "{\n"
        '  "summary": string,\n'
        '  "contract_parties": [{"name": string|null, "role": string|null}],\n'
        '  "responsibilities": [{"owner": string|null, "responsibility": string, "due_date": "YYYY-MM-DD"|null, "confidence": "high|medium|low", "source_quote": string|null}],\n'
        '  "payment_dates": [{"payer": string|null, "payee": string|null, "concept": string, "amount": number|null, "currency": "MXN"|string|null, "due_date": "YYYY-MM-DD"|null, "condition": string|null, "confidence": "high|medium|low", "source_quote": string|null}],\n'
        '  "collection_dates": [{"counterparty": string|null, "concept": string, "amount": number|null, "currency": "MXN"|string|null, "due_date": "YYYY-MM-DD"|null, "condition": string|null, "confidence": "high|medium|low", "source_quote": string|null}],\n'
        '  "operational_milestones": [{"name": string, "date": "YYYY-MM-DD"|null, "owner": string|null, "notes": string|null, "confidence": "high|medium|low"}],\n'
        '  "tournament_parameters": {"name": string|null, "sport": string|null, "year": number|null, "categories": array, "branches": array, "entities": array, "venues": array},\n'
        '  "risks_or_assumptions": [string],\n'
        '  "recommended_next_actions": [string]\n'
        "}\n\n"
        f"Torneo actual: {json.dumps(tournament, ensure_ascii=False)}\n"
        f"Evidencia: {json.dumps({k: evidence_context.get(k) for k in ['id', 'title', 'description', 'external_url', 'mime_type', 'file_path', 'scope', 'entity_name']}, ensure_ascii=False)}\n"
        f"Texto adicional del usuario:\n{(additional_text or '').strip()[:120000]}\n\n"
        f"Texto extraido del archivo/evidencia:\n{source_text[:120000]}"
    )
    result = await _assistant_text_response(
        prompt_user=prompt,
        history_messages=[],
        mode=assistant_mode or "calidad",
        route_info={"domain": "tournament", "intent": "contract_draft"},
        openai_api_key=None,
        max_tokens=4500,
        system_prompts=[
            "Eres un analista senior de operaciones, finanzas y contratos de torneos deportivos. "
            "Tu salida debe ser JSON estricto y auditable. No crees pagos ni obligaciones reales; solo borradores para revision."
        ],
    )
    payload = _json_object_from_llm_text(result.get("answer") or "")
    payload.setdefault("summary", "")
    payload.setdefault("responsibilities", [])
    payload.setdefault("payment_dates", [])
    payload.setdefault("collection_dates", [])
    payload.setdefault("operational_milestones", [])
    payload.setdefault("risks_or_assumptions", [])
    payload.setdefault("recommended_next_actions", [])
    payload["_model"] = {
        "provider": result.get("provider"),
        "model": result.get("model"),
    }
    return payload


async def _build_tournament_expediente_snapshot(
    *,
    session: AsyncSession,
    tournament_uuid: uuid.UUID,
    include_finance: bool,
) -> Dict[str, Any]:
    tournament_rows = await _supabase_rest_rows(
        table="tournaments",
        select_expr="id,name,slug,is_active",
        filters={"id": f"eq.{tournament_uuid}"},
        limit=1,
    )
    if not tournament_rows:
        raise HTTPException(status_code=404, detail="Tournament not found")
    tournament = tournament_rows[0]
    tournament_id = str(tournament_uuid)
    tournament_name = str(tournament.get("name") or "").strip() or None

    entity_profiles = await _supabase_rest_rows(
        table="tournament_entity_profiles",
        select_expr="*",
        filters={"tournament_id": f"eq.{tournament_id}"},
        order="entity_name.asc",
        limit=500,
    )
    profiles_by_entity = {
        str(row.get("entity_name") or "").strip(): row
        for row in entity_profiles
        if str(row.get("entity_name") or "").strip()
    }
    national_rows = await _supabase_rest_rows(
        table="tournament_national_phase_profiles",
        select_expr="*",
        filters={"tournament_id": f"eq.{tournament_id}"},
        limit=1,
    )
    national_profile = national_rows[0] if national_rows else {}
    evidence_rows = await _supabase_rest_rows(
        table="tournament_folder_evidence",
        select_expr=(
            "id,tournament_id,scope,entity_name,section,evidence_type,title,"
            "description,file_path,external_url,mime_type,file_size,created_at"
        ),
        filters={"tournament_id": f"eq.{tournament_id}"},
        order="created_at.desc",
        limit=1000,
    )
    contract_draft_rows = await _supabase_rest_rows(
        table="tournament_contract_drafts",
        select_expr=(
            "id,tournament_id,evidence_id,scope,entity_name,title,status,"
            "draft_payload,created_at"
        ),
        filters={"tournament_id": f"eq.{tournament_id}"},
        order="created_at.desc",
        limit=500,
    )
    commitment_rows = await _supabase_rest_rows(
        table="tournament_operational_commitments",
        select_expr=(
            "id,tournament_id,source_draft_id,source_evidence_id,item_type,scope,"
            "entity_name,title,owner_name,counterparty_name,due_date,amount,currency,"
            "status,confidence,source_quote,notes,payload,created_at"
        ),
        filters={"tournament_id": f"eq.{tournament_id}"},
        order="due_date.asc.nullslast",
        limit=1000,
    )
    evidence_by_entity: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    national_evidence: List[Dict[str, Any]] = []
    for row in evidence_rows:
        scope = str(row.get("scope") or "").strip().lower()
        if scope == "national":
            national_evidence.append(row)
            continue
        entity_name = str(row.get("entity_name") or "").strip()
        if entity_name:
            evidence_by_entity[entity_name].append(row)
    contract_drafts_by_entity: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    national_contract_drafts: List[Dict[str, Any]] = []
    tournament_contract_drafts: List[Dict[str, Any]] = []
    for row in contract_draft_rows:
        scope = str(row.get("scope") or "").strip().lower()
        if scope == "national":
            national_contract_drafts.append(row)
            continue
        if scope == "entity":
            entity_name = str(row.get("entity_name") or "").strip()
            if entity_name:
                contract_drafts_by_entity[entity_name].append(row)
                continue
        tournament_contract_drafts.append(row)
    commitments_by_entity: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    national_commitments: List[Dict[str, Any]] = []
    tournament_commitments: List[Dict[str, Any]] = []
    for row in commitment_rows:
        scope = str(row.get("scope") or "").strip().lower()
        if scope == "national":
            national_commitments.append(row)
            continue
        if scope == "entity":
            entity_name = str(row.get("entity_name") or "").strip()
            if entity_name:
                commitments_by_entity[entity_name].append(row)
                continue
        tournament_commitments.append(row)

    categories = await _supabase_rest_rows(
        table="categories",
        select_expr="id,tournament_id,name,branch,max_players_per_team",
        filters={"tournament_id": f"eq.{tournament_id}"},
        order="name.asc",
        limit=1000,
    )
    categories_by_id = {
        str(row.get("id") or "").strip(): row
        for row in categories
        if str(row.get("id") or "").strip()
    }
    category_ids = list(categories_by_id.keys())

    linked_registrations: List[Dict[str, Any]] = []
    if category_ids:
        linked_registrations = await _supabase_rest_rows(
            table="registrations",
            select_expr="id,team_id,category_id,payment_status",
            filters={"category_id": f"in.({','.join(category_ids[:1000])})"},
            limit=5000,
        )

    direct_teams = await _supabase_rest_rows(
        table="teams",
        select_expr=(
            "id,tournament_id,team_name,academy_name,state,municipality,"
            "phone_number,status,created_at"
        ),
        filters={"tournament_id": f"eq.{tournament_id}"},
        order="state.asc",
        limit=2000,
    )
    linked_team_ids = sorted(
        {
            str(row.get("team_id") or "").strip()
            for row in linked_registrations
            if str(row.get("team_id") or "").strip()
        }
    )
    linked_teams: List[Dict[str, Any]] = []
    if linked_team_ids:
        linked_teams = await _supabase_rest_rows(
            table="teams",
            select_expr=(
                "id,tournament_id,team_name,academy_name,state,municipality,"
                "phone_number,status,created_at"
            ),
            filters={"id": f"in.({','.join(linked_team_ids[:1000])})"},
            order="state.asc",
            limit=1000,
        )

    teams_by_id: Dict[str, Dict[str, Any]] = {}
    for row in [*direct_teams, *linked_teams]:
        team_id = str(row.get("id") or "").strip()
        if team_id:
            teams_by_id[team_id] = row

    team_ids = list(teams_by_id.keys())
    registrations: List[Dict[str, Any]] = []
    if team_ids:
        registrations = await _supabase_rest_rows(
            table="registrations",
            select_expr="id,team_id,category_id,payment_status",
            filters={"team_id": f"in.({','.join(team_ids[:1000])})"},
            limit=5000,
        )
    seen_registration_ids = {
        str(row.get("id") or "").strip() for row in registrations if row.get("id")
    }
    for row in linked_registrations:
        registration_id = str(row.get("id") or "").strip()
        if registration_id and registration_id not in seen_registration_ids:
            registrations.append(row)

    registration_ids = [
        str(row.get("id") or "").strip() for row in registrations if row.get("id")
    ]
    players: List[Dict[str, Any]] = []
    if registration_ids:
        players = await _supabase_rest_rows(
            table="players",
            select_expr="id,registration_id,birth_date,documents_complete,documents_verified",
            filters={"registration_id": f"in.({','.join(registration_ids[:5000])})"},
            limit=5000,
        )

    registrations_by_team: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in registrations:
        team_id = str(row.get("team_id") or "").strip()
        if team_id:
            registrations_by_team[team_id].append(row)
    players_by_registration: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in players:
        registration_id = str(row.get("registration_id") or "").strip()
        if registration_id:
            players_by_registration[registration_id].append(row)

    entity_map: Dict[str, Dict[str, Any]] = {}
    for team in teams_by_id.values():
        entity_name = str(team.get("state") or "").strip() or "Sin entidad"
        entity = entity_map.setdefault(
            entity_name,
            {
                "entity_name": entity_name,
                "profile": profiles_by_entity.get(entity_name),
                "profile_completion": _expediente_profile_completion(
                    profiles_by_entity.get(entity_name) or {}
                ),
                "teams_count": 0,
                "players_count": 0,
                "teams": [],
                "category_branch_summary": {},
                "players_by_category_age_branch": {},
            },
        )
        entity["teams_count"] += 1
        entity["teams"].append(
            {
                "id": team.get("id"),
                "team_name": team.get("team_name"),
                "academy_name": team.get("academy_name"),
                "state": team.get("state"),
                "municipality": team.get("municipality"),
                "status": team.get("status"),
            }
        )
        team_id = str(team.get("id") or "").strip()
        for registration in registrations_by_team.get(team_id, []):
            category = (
                categories_by_id.get(str(registration.get("category_id") or "")) or {}
            )
            category_name = str(category.get("name") or "Sin categoria").strip()
            branch = str(category.get("branch") or "Sin rama").strip()
            summary_key = f"{category_name}::{branch}"
            category_summary = entity["category_branch_summary"].setdefault(
                summary_key,
                {
                    "category": category_name,
                    "branch": branch,
                    "teams_count": 0,
                    "players_count": 0,
                    "max_players_per_team": category.get("max_players_per_team"),
                    "_team_ids": set(),
                },
            )
            category_summary["_team_ids"].add(team_id)
            registration_id = str(registration.get("id") or "").strip()
            registration_players = players_by_registration.get(registration_id, [])
            category_summary["players_count"] += len(registration_players)
            entity["players_count"] += len(registration_players)
            for player in registration_players:
                age_bucket = _expediente_age_bucket(player.get("birth_date"))
                player_key = f"{category_name}::{age_bucket}::{branch}"
                player_summary = entity["players_by_category_age_branch"].setdefault(
                    player_key,
                    {
                        "category": category_name,
                        "age": age_bucket,
                        "branch": branch,
                        "players_count": 0,
                    },
                )
                player_summary["players_count"] += 1

    for profile_entity, profile in profiles_by_entity.items():
        entity_map.setdefault(
            profile_entity,
            {
                "entity_name": profile_entity,
                "profile": profile,
                "profile_completion": _expediente_profile_completion(profile),
                "teams_count": 0,
                "players_count": 0,
                "teams": [],
                "category_branch_summary": {},
                "players_by_category_age_branch": {},
            },
        )
    for evidence_entity in evidence_by_entity.keys():
        entity_map.setdefault(
            evidence_entity,
            {
                "entity_name": evidence_entity,
                "profile": profiles_by_entity.get(evidence_entity),
                "profile_completion": _expediente_profile_completion(
                    profiles_by_entity.get(evidence_entity) or {}
                ),
                "teams_count": 0,
                "players_count": 0,
                "teams": [],
                "category_branch_summary": {},
                "players_by_category_age_branch": {},
            },
        )
    for draft_entity in contract_drafts_by_entity.keys():
        entity_map.setdefault(
            draft_entity,
            {
                "entity_name": draft_entity,
                "profile": profiles_by_entity.get(draft_entity),
                "profile_completion": _expediente_profile_completion(
                    profiles_by_entity.get(draft_entity) or {}
                ),
                "teams_count": 0,
                "players_count": 0,
                "teams": [],
                "category_branch_summary": {},
                "players_by_category_age_branch": {},
            },
        )
    for commitment_entity in commitments_by_entity.keys():
        entity_map.setdefault(
            commitment_entity,
            {
                "entity_name": commitment_entity,
                "profile": profiles_by_entity.get(commitment_entity),
                "profile_completion": _expediente_profile_completion(
                    profiles_by_entity.get(commitment_entity) or {}
                ),
                "teams_count": 0,
                "players_count": 0,
                "teams": [],
                "category_branch_summary": {},
                "players_by_category_age_branch": {},
            },
        )

    entity_names = sorted(entity_map.keys())
    finance_payload: Optional[Dict[str, Any]] = None
    national_finance_payload: Optional[Dict[str, Any]] = None
    if include_finance and entity_names:
        finance_payload = await _build_entity_finance_summaries(
            session=session,
            tournament_uuid=tournament_uuid,
            entities=entity_names,
            tournament_name=tournament_name,
        )
    if include_finance:
        national_finance_payload = await _build_national_finance_summary(
            session=session,
            tournament_uuid=tournament_uuid,
            tournament_name=tournament_name,
        )
    finance_by_entity = {
        str(row.get("entity_name") or ""): row
        for row in ((finance_payload or {}).get("entities") or [])
        if isinstance(row, dict)
    }

    entities_out: List[Dict[str, Any]] = []
    for entity_name in entity_names:
        entity = entity_map[entity_name]
        category_summaries = []
        for summary in entity["category_branch_summary"].values():
            team_count = len(summary.pop("_team_ids", set()))
            summary["teams_count"] = team_count
            category_summaries.append(summary)
        entities_out.append(
            {
                "entity_name": entity_name,
                "profile": entity["profile"],
                "profile_completion": entity["profile_completion"],
                "teams_count": entity["teams_count"],
                "players_count": entity["players_count"],
                "teams": entity["teams"],
                "category_branch_summary": sorted(
                    category_summaries,
                    key=lambda row: (
                        row.get("category") or "",
                        row.get("branch") or "",
                    ),
                ),
                "players_by_category_age_branch": sorted(
                    entity["players_by_category_age_branch"].values(),
                    key=lambda row: (
                        row.get("category") or "",
                        row.get("branch") or "",
                        row.get("age") or "",
                    ),
                ),
                "finance": finance_by_entity.get(entity_name),
                "evidence": evidence_by_entity.get(entity_name, []),
                "evidence_count": len(evidence_by_entity.get(entity_name, [])),
                "contract_drafts": contract_drafts_by_entity.get(entity_name, []),
                "contract_drafts_count": len(
                    contract_drafts_by_entity.get(entity_name, [])
                ),
                "commitments": commitments_by_entity.get(entity_name, []),
                "commitments_count": len(commitments_by_entity.get(entity_name, [])),
            }
        )

    return {
        "ok": True,
        "tournament": {
            "id": tournament_id,
            "name": tournament.get("name"),
            "slug": tournament.get("slug"),
            "is_active": tournament.get("is_active"),
        },
        "summary": {
            "entities_count": len(entities_out),
            "teams_count": len(teams_by_id),
            "registrations_count": len(registrations),
            "players_count": len(players),
            "categories_count": len(categories),
            "has_national_profile": bool(national_profile),
            "evidence_count": len(evidence_rows),
            "contract_drafts_count": len(contract_draft_rows),
            "commitments_count": len(commitment_rows),
            "finance_included": include_finance,
        },
        "entities": entities_out,
        "national_phase": {
            "profile": national_profile or None,
            "profile_completion": _expediente_national_completion(national_profile),
            "finance": (national_finance_payload or {}).get("summary"),
            "evidence": national_evidence,
            "evidence_count": len(national_evidence),
            "contract_drafts": national_contract_drafts,
            "contract_drafts_count": len(national_contract_drafts),
            "commitments": national_commitments,
            "commitments_count": len(national_commitments),
        },
        "contract_drafts": tournament_contract_drafts,
        "commitments": tournament_commitments,
        "sources": (
            [
                "tournaments",
                "categories",
                "registrations",
                "teams",
                "players",
                "tournament_entity_profiles",
                "tournament_national_phase_profiles",
                "tournament_folder_evidence",
                "tournament_contract_drafts",
                "tournament_operational_commitments",
                "documentos",
                "expense_reports",
            ]
            if include_finance
            else [
                "tournaments",
                "categories",
                "registrations",
                "teams",
                "players",
                "tournament_entity_profiles",
                "tournament_national_phase_profiles",
                "tournament_folder_evidence",
                "tournament_contract_drafts",
                "tournament_operational_commitments",
            ]
        ),
    }


@router.get("/admin/tournaments/{tournament_id}/expediente-snapshot")
async def admin_tournament_expediente_snapshot(
    tournament_id: str,
    include_finance: bool = Query(default=True),
    current_empleado=Depends(get_current_empleado),
    session: AsyncSession = Depends(get_db_session),
):
    if not _can_manage_operations_console(current_empleado):
        raise HTTPException(
            status_code=403,
            detail="Expediente snapshot requires operations, admin or superadmin role",
        )

    try:
        tournament_uuid = uuid.UUID(str(tournament_id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid tournament_id") from exc

    finance_allowed = include_finance and _can_view_finance_console(current_empleado)
    return await _build_tournament_expediente_snapshot(
        session=session,
        tournament_uuid=tournament_uuid,
        include_finance=finance_allowed,
    )


@router.get("/admin/tournaments/{tournament_id}/soul-snapshot")
async def admin_tournament_soul_snapshot(
    tournament_id: str,
    include_media: bool = Query(default=True),
    include_communications: bool = Query(default=True),
    limit: int = Query(default=250, ge=1, le=1000),
    current_empleado=Depends(get_current_empleado),
):
    if not _can_manage_operations_console(current_empleado):
        raise HTTPException(
            status_code=403,
            detail="Tournament soul snapshot requires operations, admin or superadmin role",
        )

    try:
        tournament_uuid = uuid.UUID(str(tournament_id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid tournament_id") from exc

    return await build_tournament_soul_snapshot(
        tournament_key="all",
        tournament_slug=str(tournament_uuid),
        include_media=include_media,
        include_communications=include_communications,
        limit=limit,
    )


@router.get("/admin/tournaments/{tournament_id}/budget-snapshot")
async def admin_tournament_budget_snapshot(
    tournament_id: str,
    edition_year: int = Query(default=2026, ge=2024, le=2035),
    current_empleado=Depends(get_current_empleado),
    session: AsyncSession = Depends(get_db_session),
):
    if not _can_view_finance_console(current_empleado):
        raise HTTPException(
            status_code=403,
            detail="Budget snapshot requires finance, admin or superadmin role",
        )

    try:
        tournament_uuid = uuid.UUID(str(tournament_id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid tournament_id") from exc

    tournament = await session.get(Tournament, tournament_uuid)
    if tournament is None:
        raise HTTPException(status_code=404, detail="Tournament not found")

    return await build_budget_snapshot(
        session=session,
        tournament_id=str(tournament_uuid),
        tournament_name=getattr(tournament, "name", None),
        tournament_slug=getattr(tournament, "slug", None),
        edition_year=edition_year,
    )


@router.get("/admin/budgets/versions")
async def admin_budget_versions(
    edition_year: Optional[int] = Query(default=None, ge=2024, le=2035),
    current_empleado=Depends(get_current_empleado),
    session: AsyncSession = Depends(get_db_session),
):
    if not _can_view_finance_console(current_empleado):
        raise HTTPException(
            status_code=403,
            detail="Budget versions require finance, admin or superadmin role",
        )
    versions = await list_budget_versions(session, edition_year=edition_year)
    return {"ok": True, "versions": versions}


@router.patch("/admin/budgets/versions/{version_id}/metadata")
async def admin_budget_version_update(
    version_id: str,
    payload: BudgetVersionUpdateRequest,
    current_empleado=Depends(get_current_empleado),
    session: AsyncSession = Depends(get_db_session),
):
    if not _can_view_finance_console(current_empleado):
        raise HTTPException(
            status_code=403,
            detail="Budget version updates require finance, admin or superadmin role",
        )
    try:
        version = await update_budget_version_metadata(
            session,
            version_id=version_id,
            actor_empleado_id=str(current_empleado.id),
            version_name=payload.version_name,
            notes=payload.notes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "version": version}


@router.patch("/admin/budgets/versions/{version_id}")
async def admin_budget_version_transition(
    version_id: str,
    payload: BudgetVersionTransitionRequest,
    current_empleado=Depends(get_current_empleado),
    session: AsyncSession = Depends(get_db_session),
):
    if not _can_view_finance_console(current_empleado):
        raise HTTPException(
            status_code=403,
            detail="Budget transitions require finance, admin or superadmin role",
        )
    try:
        version = await transition_budget_version(
            session,
            version_id=version_id,
            new_status=payload.status,
            actor_empleado_id=str(current_empleado.id),
            note=payload.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "version": version}


@router.get("/admin/budgets/versions/{version_id}/lines")
async def admin_budget_version_lines(
    version_id: str,
    tournament_id: Optional[str] = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
    current_empleado=Depends(get_current_empleado),
    session: AsyncSession = Depends(get_db_session),
):
    if not _can_view_finance_console(current_empleado):
        raise HTTPException(
            status_code=403,
            detail="Budget lines require finance, admin or superadmin role",
        )
    try:
        lines = await list_budget_lines(
            session,
            version_id=version_id,
            tournament_id=tournament_id,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "lines": lines}


@router.patch("/admin/budgets/lines/{line_id}")
async def admin_budget_line_update(
    line_id: str,
    payload: BudgetLineUpdateRequest,
    current_empleado=Depends(get_current_empleado),
    session: AsyncSession = Depends(get_db_session),
):
    if not _can_view_finance_console(current_empleado):
        raise HTTPException(
            status_code=403,
            detail="Budget line updates require finance, admin or superadmin role",
        )
    try:
        line = await update_budget_line(
            session,
            line_id=line_id,
            actor_empleado_id=str(current_empleado.id),
            updates=payload.model_dump(exclude_none=True),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "line": line}


@router.get("/admin/tournaments/{tournament_id}/contract-drafts")
async def admin_tournament_contract_drafts(
    tournament_id: str,
    current_empleado=Depends(get_current_empleado),
):
    if not _can_manage_operations_console(current_empleado):
        raise HTTPException(
            status_code=403,
            detail="Contract drafts require operations, admin or superadmin role",
        )
    try:
        tournament_uuid = uuid.UUID(str(tournament_id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid tournament_id") from exc
    rows = await _supabase_rest_rows(
        table="tournament_contract_drafts",
        select_expr="*",
        filters={"tournament_id": f"eq.{tournament_uuid}"},
        order="created_at.desc",
        limit=200,
    )
    return {"ok": True, "drafts": rows}


@router.post("/admin/tournaments/{tournament_id}/contract-drafts")
async def admin_tournament_contract_draft_create(
    tournament_id: str,
    payload: TournamentContractDraftRequest,
    current_empleado=Depends(get_current_empleado),
):
    if not _can_manage_operations_console(current_empleado):
        raise HTTPException(
            status_code=403,
            detail="Contract draft generation requires operations, admin or superadmin role",
        )
    try:
        tournament_uuid = uuid.UUID(str(tournament_id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid tournament_id") from exc

    tournament_rows = await _supabase_rest_rows(
        table="tournaments",
        select_expr="id,name,slug,is_active",
        filters={"id": f"eq.{tournament_uuid}"},
        limit=1,
    )
    if not tournament_rows:
        raise HTTPException(status_code=404, detail="Tournament not found")
    tournament = tournament_rows[0]

    evidence: Optional[Dict[str, Any]] = None
    extracted_text = ""
    if payload.evidence_id:
        try:
            evidence_uuid = uuid.UUID(str(payload.evidence_id))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid evidence_id") from exc
        evidence_rows = await _supabase_rest_rows(
            table="tournament_folder_evidence",
            select_expr="*",
            filters={
                "id": f"eq.{evidence_uuid}",
                "tournament_id": f"eq.{tournament_uuid}",
            },
            limit=1,
        )
        if not evidence_rows:
            raise HTTPException(status_code=404, detail="Evidence not found")
        evidence = evidence_rows[0]
        if evidence.get("file_path"):
            raw = await _download_tournament_evidence_file(
                str(evidence.get("file_path"))
            )
            extracted_text = _extract_contract_text_from_bytes(
                raw=raw,
                filename=str(evidence.get("file_path") or evidence.get("title") or ""),
                mime_type=str(evidence.get("mime_type") or ""),
            )
        elif evidence.get("description") or evidence.get("external_url"):
            extracted_text = "\n".join(
                str(item or "")
                for item in [
                    evidence.get("title"),
                    evidence.get("description"),
                    evidence.get("external_url"),
                ]
                if str(item or "").strip()
            )

    source_text = "\n\n".join(
        item
        for item in [extracted_text, payload.additional_text or ""]
        if str(item or "").strip()
    ).strip()
    if len(source_text) < 20:
        raise HTTPException(
            status_code=400,
            detail=(
                "No hay texto suficiente para generar el draft. Adjunta un PDF/DOCX/TXT/XLSX legible "
                "o agrega texto adicional."
            ),
        )

    draft_payload = await _generate_tournament_contract_draft_payload(
        tournament=tournament,
        evidence=evidence,
        source_text=source_text,
        additional_text=payload.additional_text,
        assistant_mode=payload.assistant_mode,
    )
    model_info = draft_payload.get("_model") or {}
    title = (
        payload.title
        or (evidence or {}).get("title")
        or f"Draft contrato {tournament.get('name') or tournament_uuid}"
    )
    insert_payload = {
        "tournament_id": str(tournament_uuid),
        "evidence_id": str(evidence.get("id")) if evidence else None,
        "scope": payload.scope,
        "entity_name": (
            payload.entity_name.strip()
            if payload.entity_name
            else (evidence or {}).get("entity_name")
        ),
        "title": str(title).strip()[:240],
        "status": "draft",
        "source_filename": (evidence or {}).get("file_path")
        or (evidence or {}).get("external_url"),
        "source_mime_type": (evidence or {}).get("mime_type"),
        "extracted_text_preview": source_text[:5000],
        "draft_payload": draft_payload,
        "model_provider": model_info.get("provider"),
        "model_name": model_info.get("model"),
    }
    inserted = await _supabase_rest_mutate(
        table="tournament_contract_drafts",
        method="POST",
        payload=insert_payload,
    )
    draft = inserted[0] if isinstance(inserted, list) and inserted else insert_payload
    return {
        "ok": True,
        "draft": draft,
        "extracted_chars": len(source_text),
        "note": "Draft de revision; no crea pagos, cobros, obligaciones reales ni asientos contables.",
    }


@router.patch("/admin/tournaments/{tournament_id}/contract-drafts/{draft_id}")
async def admin_tournament_contract_draft_update(
    tournament_id: str,
    draft_id: str,
    payload: TournamentContractDraftUpdateRequest,
    current_empleado=Depends(get_current_empleado),
):
    if not _can_manage_operations_console(current_empleado):
        raise HTTPException(
            status_code=403,
            detail="Contract draft review requires operations, admin or superadmin role",
        )
    try:
        tournament_uuid = uuid.UUID(str(tournament_id))
        draft_uuid = uuid.UUID(str(draft_id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid id") from exc

    update_payload: Dict[str, Any] = {}
    if payload.title is not None:
        clean_title = payload.title.strip()
        if not clean_title:
            raise HTTPException(status_code=400, detail="title cannot be blank")
        update_payload["title"] = clean_title[:240]
    if payload.status is not None:
        update_payload["status"] = payload.status
    if payload.draft_payload is not None:
        update_payload["draft_payload"] = payload.draft_payload
    if not update_payload:
        raise HTTPException(status_code=400, detail="No changes provided")

    updated = await _supabase_rest_mutate(
        table="tournament_contract_drafts",
        method="PATCH",
        payload=update_payload,
        filters={
            "id": f"eq.{draft_uuid}",
            "tournament_id": f"eq.{tournament_uuid}",
        },
    )
    if not isinstance(updated, list) or not updated:
        raise HTTPException(status_code=404, detail="Draft not found")
    return {
        "ok": True,
        "draft": updated[0],
        "note": "Draft actualizado para revisión; no se aplicó a módulos operativos ni contables.",
    }


@router.post("/admin/tournaments/{tournament_id}/contract-drafts/{draft_id}/apply")
async def admin_tournament_contract_draft_apply(
    tournament_id: str,
    draft_id: str,
    current_empleado=Depends(get_current_empleado),
):
    if not _can_manage_operations_console(current_empleado):
        raise HTTPException(
            status_code=403,
            detail="Applying contract drafts requires operations, admin or superadmin role",
        )
    try:
        tournament_uuid = uuid.UUID(str(tournament_id))
        draft_uuid = uuid.UUID(str(draft_id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid id") from exc

    drafts = await _supabase_rest_rows(
        table="tournament_contract_drafts",
        select_expr="*",
        filters={
            "id": f"eq.{draft_uuid}",
            "tournament_id": f"eq.{tournament_uuid}",
        },
        limit=1,
    )
    if not drafts:
        raise HTTPException(status_code=404, detail="Draft not found")
    draft = drafts[0]
    if str(draft.get("status") or "").strip() != "approved":
        raise HTTPException(
            status_code=400,
            detail="Only approved drafts can be applied to operational commitments",
        )

    rows = _commitments_from_draft(tournament_id=str(tournament_uuid), draft=draft)
    inserted: List[Dict[str, Any]] = []
    if rows:
        result = await _supabase_rest_mutate(
            table="tournament_operational_commitments",
            method="POST",
            payload=rows,
        )
        inserted = result if isinstance(result, list) else []
    updated = await _supabase_rest_mutate(
        table="tournament_contract_drafts",
        method="PATCH",
        payload={"status": "applied"},
        filters={
            "id": f"eq.{draft_uuid}",
            "tournament_id": f"eq.{tournament_uuid}",
        },
    )
    return {
        "ok": True,
        "inserted_count": len(inserted),
        "commitments": inserted,
        "draft": updated[0] if isinstance(updated, list) and updated else draft,
        "note": "Se aplicó al expediente operativo. No se crearon pagos reales ni asientos contables.",
    }


@router.get("/admin/tournaments/{tournament_id}/operational-commitments")
async def admin_tournament_operational_commitments(
    tournament_id: str,
    current_empleado=Depends(get_current_empleado),
):
    if not _can_manage_operations_console(current_empleado):
        raise HTTPException(
            status_code=403,
            detail="Operational commitments require operations, admin or superadmin role",
        )
    try:
        tournament_uuid = uuid.UUID(str(tournament_id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid tournament_id") from exc
    rows = await _supabase_rest_rows(
        table="tournament_operational_commitments",
        select_expr="*",
        filters={"tournament_id": f"eq.{tournament_uuid}"},
        order="due_date.asc.nullslast",
        limit=1000,
    )
    return {"ok": True, "commitments": rows}


@router.patch(
    "/admin/tournaments/{tournament_id}/operational-commitments/{commitment_id}"
)
async def admin_tournament_operational_commitment_update(
    tournament_id: str,
    commitment_id: str,
    payload: TournamentCommitmentUpdateRequest,
    current_empleado=Depends(get_current_empleado),
):
    if not _can_manage_operations_console(current_empleado):
        raise HTTPException(
            status_code=403,
            detail="Operational commitment updates require operations, admin or superadmin role",
        )
    try:
        tournament_uuid = uuid.UUID(str(tournament_id))
        commitment_uuid = uuid.UUID(str(commitment_id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid id") from exc
    update_payload: Dict[str, Any] = {}
    if payload.status is not None:
        update_payload["status"] = payload.status
    if payload.owner_name is not None:
        update_payload["owner_name"] = payload.owner_name.strip() or None
    if payload.notes is not None:
        update_payload["notes"] = payload.notes.strip() or None
    if not update_payload:
        raise HTTPException(status_code=400, detail="No changes provided")
    updated = await _supabase_rest_mutate(
        table="tournament_operational_commitments",
        method="PATCH",
        payload=update_payload,
        filters={
            "id": f"eq.{commitment_uuid}",
            "tournament_id": f"eq.{tournament_uuid}",
        },
    )
    if not isinstance(updated, list) or not updated:
        raise HTTPException(status_code=404, detail="Commitment not found")
    return {"ok": True, "commitment": updated[0]}


@router.get("/admin/operations/solicitud-catalogs")
async def admin_operations_solicitud_catalogs(
    q: Optional[str] = Query(default=None, max_length=120),
    current_empleado=Depends(get_current_empleado),
    session: AsyncSession = Depends(get_db_session),
):
    if not _can_manage_operations_console(current_empleado):
        raise HTTPException(
            status_code=403,
            detail="Solicitud catalogs require operations, admin or superadmin role",
        )
    provider_stmt = (
        select(ProveedorCliente)
        .where(ProveedorCliente.activo)
        .order_by(ProveedorCliente.nombre.asc())
        .limit(200)
    )
    if q:
        like = f"%{q.strip()}%"
        provider_stmt = provider_stmt.where(
            or_(ProveedorCliente.nombre.ilike(like), ProveedorCliente.rfc.ilike(like))
        )
    employee_stmt = (
        select(Empleado)
        .where(Empleado.activo)
        .order_by(Empleado.nombre.asc())
        .limit(200)
    )
    tournament_stmt = (
        select(Tournament)
        .where(Tournament.active)
        .order_by(Tournament.name.asc())
        .limit(100)
    )
    providers = (await session.execute(provider_stmt)).scalars().all()
    employees = (await session.execute(employee_stmt)).scalars().all()
    tournaments = (await session.execute(tournament_stmt)).scalars().all()
    return {
        "ok": True,
        "providers": [
            {
                "id": str(row.id),
                "nombre": row.nombre,
                "tipo": row.tipo,
                "rfc": row.rfc,
                "banco": row.banco,
            }
            for row in providers
        ],
        "employees": [
            {
                "id": str(row.id),
                "nombre": row.nombre,
                "correo": row.correo,
                "rol": row.rol,
            }
            for row in employees
        ],
        "tournaments": [{"id": str(row.id), "name": row.name} for row in tournaments],
    }


@router.post(
    "/admin/tournaments/{tournament_id}/operational-commitments/{commitment_id}/create-solicitud"
)
async def admin_tournament_operational_commitment_create_solicitud(
    tournament_id: str,
    commitment_id: str,
    payload: TournamentCommitmentSolicitudRequest,
    current_empleado=Depends(get_current_empleado),
    session: AsyncSession = Depends(get_db_session),
):
    if not _can_manage_operations_console(current_empleado):
        raise HTTPException(
            status_code=403,
            detail="Creating solicitud from commitment requires operations, admin or superadmin role",
        )
    try:
        tournament_uuid = uuid.UUID(str(tournament_id))
        commitment_uuid = uuid.UUID(str(commitment_id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid id") from exc

    try:
        result = await execute_canonical_action(
            "operations.create_solicitud_from_commitment",
            session=session,
            context={
                "tournament_id": str(tournament_uuid),
                "responsible_user_id": payload.empleado_id
                or str(getattr(current_empleado, "id", "") or ""),
            },
            payload={
                "commitment_id": str(commitment_uuid),
                "empleado_id": payload.empleado_id
                or str(getattr(current_empleado, "id", "") or ""),
                "proveedor_cliente_id": payload.proveedor_cliente_id,
                "gastos_torneo_id": payload.gastos_torneo_id,
                "monto_solicitado": payload.monto_solicitado,
                "concepto_pago": payload.concepto_pago,
                "fecha_pago": payload.fecha_pago,
                "notas": payload.notas,
            },
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception:
        await session.rollback()
        logger.exception(
            "Unexpected error creating solicitud from assistant commitment",
            extra={
                "actor_id": str(current_empleado.id),
                "tournament_id": str(tournament_uuid),
                "commitment_id": str(commitment_uuid),
            },
        )
        raise HTTPException(status_code=500, detail="Unexpected processing error")
    return {
        "ok": True,
        "action": result.action,
        "status": result.status,
        "data": result.data,
        "context": result.context.to_dict(),
    }


@router.get("/admin/tournaments/{tournament_id}/entity-finance")
async def admin_tournament_entity_finance(
    tournament_id: str,
    entity: List[str] = Query(default=[]),
    tournament_name: Optional[str] = Query(default=None),
    current_empleado=Depends(get_current_empleado),
    session: AsyncSession = Depends(get_db_session),
):
    if not _can_view_finance_console(current_empleado):
        raise HTTPException(
            status_code=403,
            detail="Entity finance requires finance, operations, admin or superadmin role",
        )

    try:
        tournament_uuid = uuid.UUID(str(tournament_id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid tournament_id") from exc

    entities = [str(item or "").strip() for item in entity if str(item or "").strip()]
    if not entities:
        raise HTTPException(status_code=400, detail="At least one entity is required")

    return await _build_entity_finance_summaries(
        session=session,
        tournament_uuid=tournament_uuid,
        entities=entities,
        tournament_name=tournament_name,
    )


@router.get("/admin/tournaments/{tournament_id}/national-finance")
async def admin_tournament_national_finance(
    tournament_id: str,
    tournament_name: Optional[str] = Query(default=None),
    current_empleado=Depends(get_current_empleado),
    session: AsyncSession = Depends(get_db_session),
):
    if not _can_view_finance_console(current_empleado):
        raise HTTPException(
            status_code=403,
            detail="National finance requires finance, operations, admin or superadmin role",
        )

    try:
        tournament_uuid = uuid.UUID(str(tournament_id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid tournament_id") from exc

    return await _build_national_finance_summary(
        session=session,
        tournament_uuid=tournament_uuid,
        tournament_name=tournament_name,
    )


@router.post(
    "/conversations/{conversation_id}/messages", response_model=MessageResponse
)
async def create_message(
    payload: MessageCreateRequest,
    request: Request,
    conversation_id: str = PathParam(...),
    openai_api_key: Optional[str] = Header(default=None, alias="X-OpenAI-API-Key"),
    current_empleado=Depends(get_current_empleado),
    session: AsyncSession = Depends(get_db_session),
):
    _enforce_rate_limit(empleado_id=current_empleado.id, kind="message")
    try:
        conversation = await _load_conversation(
            session, conversation_id=conversation_id, empleado_id=current_empleado.id
        )
        _update_conversation_context(
            conversation=conversation,
            tournament_key=payload.tournament_key,
            module_key=payload.module_key,
            module_label=payload.module_label,
            module_context=payload.module_context,
        )
        await session.commit()

        async def document_action_router_executor(
            canonical_action: str, payload: Dict[str, Any]
        ) -> Dict[str, Any]:
            if canonical_action not in supported_read_actions():
                raise ValueError(
                    "document confirmation live wiring only executes read actions"
                )
            result = await execute_canonical_action(
                canonical_action,
                session=session,
                context={
                    "tournament_key": conversation.tournament_key,
                    "responsible_user_id": str(
                        getattr(current_empleado, "id", "") or ""
                    ),
                },
                payload=payload,
            )
            return {
                "summary": str(result.data.get("summary") or result.status),
                "status": result.status,
                "action": result.action,
            }

        return await run_message_turn_with_pending(
            raw_message=payload.message,
            conversation=conversation,
            current_empleado=current_empleado,
            session=session,
            request=request,
            tournament_key=payload.tournament_key,
            bi_year=payload.bi_year,
            bi_scope=payload.bi_scope,
            bi_segment=payload.bi_segment,
            assistant_mode=payload.assistant_mode,
            openai_api_key=openai_api_key,
            latest_pending_run_for_conversation=_latest_pending_run_for_conversation,
            is_explicit_approval_message=_is_explicit_approval_message,
            is_explicit_rejection_message=_is_explicit_rejection_message,
            confirm_pending_run=_confirm_pending_run,
            deterministic_pending_builders=[
                _build_expense_canonical_pending,
                _build_cfdi_canonical_pending,
                _build_link_cfdi_canonical_pending,
                _build_bank_link_canonical_pending,
                _build_accounting_assign_canonical_pending,
                _build_accounting_post_canonical_pending,
            ],
            build_deterministic_pending_response=_build_deterministic_pending_response,
            assistant_turn=_assistant_turn,
            maybe_append_export_prompt=_maybe_append_export_prompt,
            document_action_router_executor=document_action_router_executor,
        )
    except HTTPException:
        raise
    except Exception:
        await session.rollback()
        logger.exception(
            "Unexpected error creating assistant message",
            extra={"empleado_id": str(current_empleado.id), "conversation_id": conversation_id},
        )
        raise HTTPException(status_code=500, detail="Unexpected processing error")


@router.post("/conversations/{conversation_id}/media", response_model=MessageResponse)
async def create_media_message(
    request: Request,
    conversation_id: str = PathParam(...),
    kind: str = Form(...),
    note: Optional[str] = Form(default=None),
    tournament_key: Optional[str] = Form(default=None),
    module_key: Optional[str] = Form(default=None),
    module_label: Optional[str] = Form(default=None),
    module_context_json: Optional[str] = Form(default=None),
    bi_year: Optional[int] = Form(default=None),
    bi_scope: Optional[str] = Form(default=None),
    bi_segment: Optional[str] = Form(default=None),
    assistant_mode: Optional[str] = Form(default=None),
    file: UploadFile = File(...),
    openai_api_key: Optional[str] = Header(default=None, alias="X-OpenAI-API-Key"),
    current_empleado=Depends(get_current_empleado),
    session: AsyncSession = Depends(get_db_session),
):
    _enforce_rate_limit(empleado_id=current_empleado.id, kind="message")
    try:
        conversation = await _load_conversation(
            session, conversation_id=conversation_id, empleado_id=current_empleado.id
        )
        module_context_payload: Optional[Dict[str, Any]] = None
        if module_context_json:
            try:
                parsed = json.loads(module_context_json)
            except json.JSONDecodeError as exc:
                raise HTTPException(
                    status_code=400, detail="module_context_json must be valid JSON"
                ) from exc
            if isinstance(parsed, dict):
                module_context_payload = parsed
        _update_conversation_context(
            conversation=conversation,
            tournament_key=tournament_key,
            module_key=module_key,
            module_label=module_label,
            module_context=module_context_payload,
        )
        await session.commit()
        try:
            raw_file = await read_upload_limited(
                file,
                max_bytes=_ASSISTANT_MEDIA_UPLOAD_MAX_BYTES,
                too_large_message="Max file size is 15MB",
                empty_message="Uploaded file is empty",
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await file.seek(0)
        if (kind or "").strip().lower() in {"image", "voice"} and raw_file:
            _store_last_media_draft(
                conversation=conversation,
                kind=(kind or "").strip().lower(),
                upload=file,
                raw=raw_file,
                note=note,
            )
            await session.commit()
        extracted_message = await extract_text_from_media(
            kind=kind,
            upload=file,
            note=note,
            raw=raw_file,
            openai_api_key=openai_api_key,
            extract_text_from_image_anthropic=_extract_text_from_image_anthropic,
            assistant_provider_order=_assistant_provider_order,
            get_openai_client=_get_openai_client,
            extract_roster_from_records=_extract_roster_from_records,
        )
        return await run_conversation_turn(
            raw_message=extracted_message,
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
            assistant_turn=_assistant_turn,
            maybe_append_export_prompt=_maybe_append_export_prompt,
        )
    except HTTPException:
        raise
    except Exception:
        await session.rollback()
        logger.exception(
            "Unexpected error creating assistant media message",
            extra={"empleado_id": str(current_empleado.id), "conversation_id": conversation_id},
        )
        raise HTTPException(status_code=500, detail="Unexpected processing error")


@router.post("/conversations/{conversation_id}/confirm", response_model=MessageResponse)
async def confirm_write(
    payload: ConfirmRequest,
    conversation_id: str = PathParam(...),
    openai_api_key: Optional[str] = Header(default=None, alias="X-OpenAI-API-Key"),
    current_empleado=Depends(get_current_empleado),
    session: AsyncSession = Depends(get_db_session),
):
    _enforce_rate_limit(empleado_id=current_empleado.id, kind="confirm")
    try:
        conversation = await _load_conversation(
            session, conversation_id=conversation_id, empleado_id=current_empleado.id
        )
        try:
            run_uuid = uuid.UUID(payload.run_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid run_id") from exc

        run = (
            await session.execute(
                select(AssistantRun).where(
                    AssistantRun.id == run_uuid,
                    AssistantRun.conversation_id == conversation.id,
                    AssistantRun.empleado_id == current_empleado.id,
                )
            )
        ).scalar_one_or_none()
        if not run or run.status != "pending_confirmation":
            raise HTTPException(status_code=404, detail="Pending run not found")

        return await _confirm_pending_run(
            run=run,
            conversation=conversation,
            approve=payload.approve,
            assistant_mode=payload.assistant_mode,
            openai_api_key=openai_api_key,
            current_empleado=current_empleado,
            session=session,
        )
    except HTTPException:
        raise
    except Exception:
        await session.rollback()
        logger.exception(
            "Unexpected error confirming assistant write",
            extra={
                "empleado_id": str(current_empleado.id),
                "conversation_id": conversation_id,
                "run_id": payload.run_id,
            },
        )
        raise HTTPException(status_code=500, detail="Unexpected processing error")


@router.get("/rag/status")
async def rag_status(
    current_empleado=Depends(get_current_empleado),
):
    return get_rag_store().status()


@router.post("/rag/search")
async def rag_search(
    payload: RAGSearchRequest,
    current_empleado=Depends(get_current_empleado),
):
    results = await _rag_search_async(
        query=payload.query,
        top_k=payload.top_k,
        min_score=payload.min_score,
    )
    return {"query": payload.query, "results": results}


@router.post("/rag/ingest")
async def rag_ingest(
    payload: RAGIngestRequest,
    current_empleado=Depends(get_current_empleado),
):
    if not _is_superadmin(getattr(current_empleado, "rol", None)):
        raise HTTPException(
            status_code=403, detail="RAG ingest requires superadmin role"
        )
    result = await _rag_ingest_async(
        paths=payload.paths,
        reset=payload.reset,
        max_files=payload.max_files,
    )
    with _RETRIEVAL_CACHE_LOCK:
        _RETRIEVAL_CACHE.clear()
    return result


@router.get("/rag/codex")
async def rag_codex_get(
    current_empleado=Depends(get_current_empleado),
):
    if not _is_superadmin(getattr(current_empleado, "rol", None)):
        raise HTTPException(
            status_code=403, detail="Codex read requires superadmin role"
        )
    path = _codex_doc_path()
    if not path.exists():
        return {
            "path": str(path),
            "exists": False,
            "content": "",
            "updated_at": None,
        }
    content = path.read_text(encoding="utf-8", errors="ignore")
    updated_at = datetime.utcfromtimestamp(path.stat().st_mtime).isoformat()
    return {
        "path": str(path),
        "exists": True,
        "content": content,
        "updated_at": updated_at,
    }


@router.put("/rag/codex")
async def rag_codex_update(
    payload: RAGCodexUpdateRequest,
    current_empleado=Depends(get_current_empleado),
):
    if not _is_superadmin(getattr(current_empleado, "rol", None)):
        raise HTTPException(
            status_code=403, detail="Codex update requires superadmin role"
        )
    path = _codex_doc_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized_content = (payload.content or "").strip()
    if not normalized_content:
        raise HTTPException(status_code=400, detail="Codex content cannot be empty")
    path.write_text(normalized_content + "\n", encoding="utf-8")

    ingest_result = None
    if payload.auto_ingest:
        ingest_paths = payload.paths or ["docs", "reports", "codex.md"]
        ingest_result = await _rag_ingest_async(
            paths=ingest_paths,
            reset=False,
            max_files=payload.max_files,
        )
        with _RETRIEVAL_CACHE_LOCK:
            _RETRIEVAL_CACHE.clear()

    return {
        "ok": True,
        "path": str(path),
        "saved": True,
        "updated_at": datetime.utcnow().isoformat(),
        "ingest": ingest_result,
    }


@router.get("/rag/metrics")
async def rag_metrics(
    current_empleado=Depends(get_current_empleado),
):
    if not _is_superadmin(getattr(current_empleado, "rol", None)):
        raise HTTPException(
            status_code=403, detail="RAG metrics requires superadmin role"
        )
    return get_assistant_rag_health_snapshot()


@router.get("/rag/config")
async def rag_config(
    current_empleado=Depends(get_current_empleado),
):
    if not _is_superadmin(getattr(current_empleado, "rol", None)):
        raise HTTPException(
            status_code=403, detail="RAG config requires superadmin role"
        )
    return {
        "weights": _rag_weights(),
        "config_path": str(_RAG_CONFIG_PATH),
        "presets": _rag_presets(),
        "latest_change": _latest_rag_config_change(),
    }


@router.put("/rag/config")
async def rag_config_update(
    payload: RAGConfigUpdateRequest,
    current_empleado=Depends(get_current_empleado),
):
    if not _is_superadmin(getattr(current_empleado, "rol", None)):
        raise HTTPException(
            status_code=403, detail="RAG config update requires superadmin role"
        )
    incoming = payload.dict(exclude_none=True)
    if not incoming:
        return {"weights": _rag_weights(), "updated": False}
    before = _rag_weights()
    weights = _set_rag_weights(incoming)
    with _RETRIEVAL_CACHE_LOCK:
        _RETRIEVAL_CACHE.clear()
    event = {
        "timestamp": datetime.utcnow().isoformat(),
        "action": "update",
        "before": before,
        "after": weights,
        "changed_by": {
            "empleado_id": str(getattr(current_empleado, "id", "")),
            "rol": getattr(current_empleado, "rol", None),
        },
    }
    _record_rag_config_change(event)
    return {
        "weights": weights,
        "updated": True,
        "config_path": str(_RAG_CONFIG_PATH),
        "latest_change": event,
    }


@router.post("/rag/config/preset")
async def rag_config_preset(
    payload: RAGConfigPresetRequest,
    current_empleado=Depends(get_current_empleado),
):
    if not _is_superadmin(getattr(current_empleado, "rol", None)):
        raise HTTPException(
            status_code=403, detail="RAG config preset requires superadmin role"
        )
    key = (payload.preset or "").strip().lower()
    presets = _rag_presets()
    if key not in presets:
        raise HTTPException(status_code=400, detail=f"Unknown preset: {key}")
    before = _rag_weights()
    weights = _set_rag_weights(presets[key])
    with _RETRIEVAL_CACHE_LOCK:
        _RETRIEVAL_CACHE.clear()
    event = {
        "timestamp": datetime.utcnow().isoformat(),
        "action": "preset",
        "preset": key,
        "before": before,
        "after": weights,
        "changed_by": {
            "empleado_id": str(getattr(current_empleado, "id", "")),
            "rol": getattr(current_empleado, "rol", None),
        },
    }
    _record_rag_config_change(event)
    return {"weights": weights, "updated": True, "preset": key, "latest_change": event}


@router.post("/rag/config/reset")
async def rag_config_reset(
    current_empleado=Depends(get_current_empleado),
):
    if not _is_superadmin(getattr(current_empleado, "rol", None)):
        raise HTTPException(
            status_code=403, detail="RAG config reset requires superadmin role"
        )
    before = _rag_weights()
    weights = _reset_rag_weights()
    with _RETRIEVAL_CACHE_LOCK:
        _RETRIEVAL_CACHE.clear()
    event = {
        "timestamp": datetime.utcnow().isoformat(),
        "action": "reset",
        "before": before,
        "after": weights,
        "changed_by": {
            "empleado_id": str(getattr(current_empleado, "id", "")),
            "rol": getattr(current_empleado, "rol", None),
        },
    }
    _record_rag_config_change(event)
    return {"weights": weights, "updated": True, "latest_change": event}


@router.get("/rag/config/history")
async def rag_config_history(
    limit: int = 20,
    current_empleado=Depends(get_current_empleado),
):
    if not _is_superadmin(getattr(current_empleado, "rol", None)):
        raise HTTPException(
            status_code=403, detail="RAG config history requires superadmin role"
        )
    max_limit = max(1, min(int(limit), 100))
    with _RAG_CONFIG_HISTORY_LOCK:
        items = list(_RAG_CONFIG_HISTORY)[:max_limit]
    return {"count": len(items), "items": items}


async def _run_rag_eval_internal(
    *,
    payload: RAGEvalRequest,
    session: AsyncSession,
    empleado_id: Optional[uuid.UUID] = None,
) -> Dict[str, Any]:
    default_questions = [
        "Cuanto se ha pagado a proveedores en solicitudes aprobadas",
        "Como funciona cuentas de gastos y solicitudes",
        "Donde se solicita CFDI para un gasto ticket",
        "Que datos requiere el asistente para registrar un gasto",
    ]
    questions = payload.questions or default_questions
    rows: List[Dict[str, Any]] = []
    hits = 0
    sql_source_hits = 0
    doc_source_hits = 0

    for q in questions:
        retrieval = await _build_hybrid_retrieval(
            session=session,
            query=q,
            empleado_id=empleado_id,
            domain="finance",
        )
        src = retrieval.get("sources") or []
        has_evidence = len(src) > 0
        if has_evidence:
            hits += 1
        for s in src:
            label = str((s or {}).get("label") or "")
            if label.startswith("sql:"):
                sql_source_hits += 1
            if label.startswith("doc:"):
                doc_source_hits += 1
        rows.append(
            {
                "question": q,
                "has_evidence": has_evidence,
                "num_sources": len(src),
                "sources": src[: payload.top_k],
                "cache_hit": retrieval.get("cache_hit", False),
            }
        )

    score = round(hits / max(1, len(questions)), 3)
    total_source_hits = sql_source_hits + doc_source_hits
    source_mix = {
        "sql_hits": sql_source_hits,
        "doc_hits": doc_source_hits,
        "sql_ratio": round(sql_source_hits / max(1, total_source_hits), 3),
        "doc_ratio": round(doc_source_hits / max(1, total_source_hits), 3),
    }
    return {
        "timestamp": datetime.utcnow().isoformat(),
        "questions_total": len(questions),
        "questions_with_evidence": hits,
        "coverage_score": score,
        "source_mix": source_mix,
        "results": rows,
    }


def _recommend_preset_from_eval(eval_payload: Dict[str, Any]) -> Dict[str, Any]:
    coverage = float(eval_payload.get("coverage_score") or 0)
    mix = eval_payload.get("source_mix") or {}
    sql_ratio = float(mix.get("sql_ratio") or 0)
    doc_ratio = float(mix.get("doc_ratio") or 0)

    preset = "balanced"
    reason = "Cobertura adecuada; mantener balance."
    if coverage < 0.55:
        if sql_ratio >= 0.6:
            preset = "sql_heavy"
            reason = "Cobertura baja y evidencia dominada por SQL."
        elif doc_ratio >= 0.6:
            preset = "doc_heavy"
            reason = "Cobertura baja y evidencia dominada por documentos."
        else:
            preset = "recency_heavy"
            reason = "Cobertura baja con señal mezclada; priorizar frescura."
    elif coverage < 0.8:
        if sql_ratio >= 0.65:
            preset = "sql_heavy"
            reason = "Cobertura media con predominio de SQL."
        elif doc_ratio >= 0.65:
            preset = "doc_heavy"
            reason = "Cobertura media con predominio de documentos."

    target = _rag_presets().get(preset) or _rag_weights()
    return {
        "preset": preset,
        "target_weights": target,
        "reason": reason,
        "coverage_score": coverage,
        "source_mix": mix,
    }


@router.post("/rag/eval")
async def rag_eval(
    payload: RAGEvalRequest,
    current_empleado=Depends(get_current_empleado),
    session: AsyncSession = Depends(get_db_session),
):
    if not _is_superadmin(getattr(current_empleado, "rol", None)):
        raise HTTPException(status_code=403, detail="RAG eval requires superadmin role")

    result = await _run_rag_eval_internal(
        payload=payload,
        session=session,
        empleado_id=getattr(current_empleado, "id", None),
    )
    _record_rag_eval(result)
    return result


@router.post("/rag/config/auto-tune")
async def rag_config_auto_tune(
    payload: RAGAutoTuneRequest,
    current_empleado=Depends(get_current_empleado),
    session: AsyncSession = Depends(get_db_session),
):
    if not _is_superadmin(getattr(current_empleado, "rol", None)):
        raise HTTPException(
            status_code=403, detail="RAG auto-tune requires superadmin role"
        )

    eval_payload = await _run_rag_eval_internal(
        payload=RAGEvalRequest(questions=payload.questions, top_k=payload.top_k),
        session=session,
        empleado_id=getattr(current_empleado, "id", None),
    )
    _record_rag_eval(eval_payload)
    recommendation = _recommend_preset_from_eval(eval_payload)
    current = _rag_weights()
    suggested = recommendation.get("target_weights") or current
    changed = any(
        abs(float(current.get(k, 0)) - float(suggested.get(k, 0))) > 1e-9
        for k in suggested
    )
    applied = False
    event = None

    if payload.apply:
        before = current
        updated = _set_rag_weights(suggested)
        with _RETRIEVAL_CACHE_LOCK:
            _RETRIEVAL_CACHE.clear()
        event = {
            "timestamp": datetime.utcnow().isoformat(),
            "action": "auto_tune",
            "preset": recommendation.get("preset"),
            "reason": recommendation.get("reason"),
            "before": before,
            "after": updated,
            "changed_by": {
                "empleado_id": str(getattr(current_empleado, "id", "")),
                "rol": getattr(current_empleado, "rol", None),
            },
            "eval": {
                "coverage_score": eval_payload.get("coverage_score"),
                "source_mix": eval_payload.get("source_mix"),
            },
        }
        _record_rag_config_change(event)
        current = updated
        applied = True

    return {
        "applied": applied,
        "would_change": changed,
        "current_weights": current,
        "recommendation": recommendation,
        "evaluation": {
            "coverage_score": eval_payload.get("coverage_score"),
            "questions_total": eval_payload.get("questions_total"),
            "questions_with_evidence": eval_payload.get("questions_with_evidence"),
            "source_mix": eval_payload.get("source_mix"),
        },
        "latest_change": event,
    }


@router.get("/rag/eval/history")
async def rag_eval_history(
    limit: int = 20,
    current_empleado=Depends(get_current_empleado),
):
    if not _is_superadmin(getattr(current_empleado, "rol", None)):
        raise HTTPException(
            status_code=403, detail="RAG eval history requires superadmin role"
        )
    max_limit = max(1, min(int(limit), 100))
    with _RAG_EVAL_HISTORY_LOCK:
        items = list(_RAG_EVAL_HISTORY)[:max_limit]
    return {"count": len(items), "items": items}
