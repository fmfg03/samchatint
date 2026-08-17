"""Assistant-facing surface for specialist business previews.

This module exposes the deterministic specialist benchmark previews to the
assistant conversation layer. It is intentionally conservative: a preview is
shown only when the user explicitly asks for a preview by task id or when a
business-language request contains a clear prepare/review intent plus enough
domain signals to route to a known seed task. The result is read-only and
carries the structured renderer contract created in RQF-052J.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Dict, Optional, Sequence, Tuple

from .specialist_benchmarks import build_seed_benchmarks, run_seed_benchmark
from .specialist_preview_renderer import (
    SpecialistPreviewRender,
    render_specialist_business_preview,
    render_specialist_business_preview_markdown,
)
from .specialist_task_registry import (
    route_specialist_task_from_text,
    specialist_task_ids,
)

_TASK_ID_RE = re.compile(r"(?<![A-Z0-9])SAMCHAT-[A-Z0-9-]+(?![A-Z0-9])", re.I)
_DOCUMENT_REF_RE = re.compile(r"\b[ISO]-\d{6,}\b", re.I)
_OPERATIONS_REF_RE = re.compile(
    r"\b(?:ref(?:erencia)?\s*(?:operaciones)?|referencia)\s*#?\s*(\d{1,6})\b", re.I
)
_UUID_PREFIX_RE = re.compile(r"\b[A-F0-9]{8}(?:-[A-F0-9]{4}){0,4}\b", re.I)
_ACCOUNT_CODE_RE = re.compile(r"\b\d{4}-\d{3}-\d{3}\b")
_EXPLICIT_PREVIEW_TERMS = (
    "preview",
    "vista previa",
    "superficie",
    "surface",
    "especialista",
    "specialist",
)

_NATURAL_PREVIEW_ACTION_TERMS = (
    "prepara",
    "preparar",
    "preparame",
    "arma",
    "armar",
    "armame",
    "genera",
    "generar",
    "muestra",
    "mostrar",
    "revisa",
    "revisar",
    "propuesta",
    "borrador",
    "preview",
    "vista previa",
)


@dataclass(frozen=True)
class SpecialistPreviewSurface:
    task_id: str
    assistant_message: str
    preview_render: SpecialistPreviewRender
    business_preview: Dict[str, Any]
    understood_context: Dict[str, Any]
    status: str = "preview_ready"
    provider_called: bool = False
    writes_attempted: bool = False

    def tool_trace(self) -> Dict[str, Any]:
        return {
            "specialist_preview_surface": {
                "stage": "deterministic_read_only_preview_surface",
                "task_id": self.task_id,
                "status": self.status,
                "provider_called": self.provider_called,
                "writes_attempted": self.writes_attempted,
                "primary_action_enabled": self.preview_render.primary_action_enabled,
                "execution_status": self.preview_render.execution_status,
                "audit_language": self.preview_render.audit_language,
                "understood_context": self.understood_context,
            },
            "tool": "assistant.specialist_preview.render",
            "result": {
                "preview_render": self.preview_render.to_dict(),
                "business_preview": self.business_preview,
                "understood_context": self.understood_context,
            },
        }


def specialist_preview_task_ids() -> Tuple[str, ...]:
    return specialist_task_ids()


def _normalize_text(raw_message: str) -> str:
    text = unicodedata.normalize("NFKD", str(raw_message or "").casefold())
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def _contains_any(text: str, terms: Sequence[str]) -> bool:
    return any(term in text for term in terms)


def _natural_preview_task_id(normalized_text: str) -> Optional[str]:
    if not _contains_any(normalized_text, _NATURAL_PREVIEW_ACTION_TERMS):
        return None
    return route_specialist_task_from_text(normalized_text)


def extract_specialist_preview_understood_context(raw_message: str) -> Dict[str, Any]:
    """Extract deterministic, user-visible context hints from a preview request.

    This is not live retrieval and it never creates authority. It only records
    what the assistant understood from the user's own wording so the preview can
    be audited before any future live lookup/execution path is added.
    """

    original = str(raw_message or "")
    normalized = _normalize_text(original)
    document_refs = tuple(
        dict.fromkeys(ref.upper() for ref in _DOCUMENT_REF_RE.findall(original))
    )
    operations_refs = tuple(
        dict.fromkeys(match.group(1) for match in _OPERATIONS_REF_RE.finditer(original))
    )
    uuid_like = tuple(
        dict.fromkeys(
            match.group(0).upper() for match in _UUID_PREFIX_RE.finditer(original)
        )
    )
    account_codes = tuple(dict.fromkeys(_ACCOUNT_CODE_RE.findall(original)))

    domains = []
    if any(token in normalized for token in ("amex", "american express")):
        domains.append("amex")
    if any(
        token in normalized
        for token in ("cxc", "cuentas por cobrar", "cobranza", "cobro")
    ):
        domains.append("cxc")
    if any(token in normalized for token in ("cfdi", "factura")):
        domains.append("cfdi")
    if any(token in normalized for token in ("torneo", "dcc", "copa telmex")):
        domains.append("torneo")
    if any(token in normalized for token in ("presupuesto", "budget", "reforecast")):
        domains.append("presupuesto")

    entities = []
    for label, tokens in (
        ("DCC Nacional", ("dcc nacional",)),
        ("De la Calle a la Cancha", ("de la calle a la cancha", "dcc")),
        ("Bimbo", ("bimbo",)),
        ("Odilon", ("odilon",)),
        ("Bibiana", ("bibiana",)),
        ("Alicia", ("alicia",)),
        ("Benjamin", ("benjamin",)),
        ("FGV", ("fgv",)),
        ("FGN", ("fgn",)),
        ("Luis Angel", ("luis angel", "lao")),
    ):
        if any(token in normalized for token in tokens):
            entities.append(label)

    context: Dict[str, Any] = {
        "source": "user_message",
        "live_lookup_performed": False,
        "authority": "context_hint_only",
    }
    if document_refs:
        context["document_refs"] = list(document_refs)
    if operations_refs:
        context["operations_refs"] = list(operations_refs)
    if uuid_like:
        context["uuid_or_prefixes"] = list(uuid_like)
    if account_codes:
        context["account_codes"] = list(account_codes)
    if domains:
        context["domains"] = sorted(set(domains))
    if entities:
        context["entities"] = list(dict.fromkeys(entities))
    return context


def _understood_context_markdown(context: Dict[str, Any]) -> str:
    visible = [
        ("Referencias documento", context.get("document_refs")),
        ("Referencias operaciones", context.get("operations_refs")),
        ("UUID / prefijos", context.get("uuid_or_prefixes")),
        ("Cuentas", context.get("account_codes")),
        ("Dominios", context.get("domains")),
        ("Entidades", context.get("entities")),
    ]
    lines = ["## Contexto entendido", ""]
    emitted = False
    for label, values in visible:
        if values:
            emitted = True
            lines.append(f"- {label}: {', '.join(str(value) for value in values)}")
    if not emitted:
        lines.append(
            "- No se detectaron referencias operativas explicitas en el mensaje."
        )
    lines.append(
        "- Alcance: contexto leido del mensaje; sin consulta live y sin autoridad de ejecucion."
    )
    return "\n".join(lines) + "\n"


def detect_specialist_preview_task_id(raw_message: str) -> Optional[str]:
    text = str(raw_message or "")
    if not text.strip():
        return None
    normalized = _normalize_text(text)
    match = _TASK_ID_RE.search(text.upper())
    if match and _contains_any(normalized, _EXPLICIT_PREVIEW_TERMS):
        task_id = match.group(0).upper()
        if task_id in specialist_preview_task_ids():
            return task_id
    return _natural_preview_task_id(normalized)


def render_specialist_preview_surface(
    task_id: str, raw_message: str = ""
) -> SpecialistPreviewSurface:
    selected = None
    for benchmark in build_seed_benchmarks():
        if benchmark.task.task_id == task_id:
            selected = benchmark
            break
    if selected is None:
        raise ValueError(f"unsupported_specialist_preview_task:{task_id}")

    result = run_seed_benchmark(selected)
    preview = result.business_preview
    rendered = render_specialist_business_preview(preview)
    markdown = render_specialist_business_preview_markdown(preview)
    understood_context = extract_specialist_preview_understood_context(raw_message)
    context_markdown = _understood_context_markdown(understood_context)
    message = (
        "Preview especialista listo (solo lectura).\n\n"
        f"{context_markdown}\n"
        f"{markdown}\n"
        "Frontera de autoridad: esto no ejecuta acciones, no crea registros y "
        "mantiene el boton principal deshabilitado hasta que exista una "
        "aprobacion humana gobernada."
    )
    return SpecialistPreviewSurface(
        task_id=task_id,
        assistant_message=message,
        preview_render=rendered,
        business_preview=preview.to_dict(),
        understood_context=understood_context,
    )
