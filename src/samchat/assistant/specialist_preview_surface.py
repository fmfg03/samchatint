"""Assistant-facing surface for specialist business previews.

This module exposes the deterministic specialist benchmark previews to the
assistant conversation layer. It is intentionally conservative: a preview is
shown only when the user explicitly asks for a specialist preview and names a
known seed task id. The result is read-only and carries the structured renderer
contract created in RQF-052J.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from .specialist_benchmarks import build_seed_benchmarks, run_seed_benchmark
from .specialist_preview_renderer import (
    SpecialistPreviewRender,
    render_specialist_business_preview,
    render_specialist_business_preview_markdown,
)

_TASK_ID_RE = re.compile(r"(?<![A-Z0-9])SAMCHAT-[A-Z0-9-]+(?![A-Z0-9])", re.I)
_PREVIEW_TERMS = (
    "preview",
    "vista previa",
    "superficie",
    "surface",
    "especialista",
    "specialist",
)


@dataclass(frozen=True)
class SpecialistPreviewSurface:
    task_id: str
    assistant_message: str
    preview_render: SpecialistPreviewRender
    business_preview: Dict[str, Any]
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
            },
            "tool": "assistant.specialist_preview.render",
            "result": {
                "preview_render": self.preview_render.to_dict(),
                "business_preview": self.business_preview,
            },
        }


def specialist_preview_task_ids() -> Tuple[str, ...]:
    return tuple(benchmark.task.task_id for benchmark in build_seed_benchmarks())


def detect_specialist_preview_task_id(raw_message: str) -> Optional[str]:
    text = str(raw_message or "")
    if not text.strip():
        return None
    lowered = text.casefold()
    if not any(term in lowered for term in _PREVIEW_TERMS):
        return None
    match = _TASK_ID_RE.search(text.upper())
    if not match:
        return None
    task_id = match.group(0).upper()
    if task_id not in specialist_preview_task_ids():
        return None
    return task_id


def render_specialist_preview_surface(task_id: str) -> SpecialistPreviewSurface:
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
    message = (
        "Preview especialista listo (solo lectura).\n\n"
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
    )
