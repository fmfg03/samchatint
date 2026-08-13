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

_TASK_ID_RE = re.compile(r"(?<![A-Z0-9])SAMCHAT-[A-Z0-9-]+(?![A-Z0-9])", re.I)
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
class _NaturalPreviewRule:
    task_id: str
    required_any: Tuple[str, ...]
    signals: Tuple[str, ...]
    min_signal_count: int = 1


_NATURAL_PREVIEW_RULES: Tuple[_NaturalPreviewRule, ...] = (
    _NaturalPreviewRule(
        task_id="SAMCHAT-CXC-COLLECTION-001",
        required_any=(
            "cxc",
            "cuentas por cobrar",
            "cobro",
            "cobranza",
            "factura emitida",
            "669dbf39",
        ),
        signals=(
            "bimbo",
            "dcc",
            "dcc nacional",
            "factura",
            "cfdi",
            "1150-001-001",
            "4100-001-004",
            "cobrar",
        ),
    ),
    _NaturalPreviewRule(
        task_id="SAMCHAT-FIN-AMEX-001",
        required_any=(
            "amex",
            "american express",
            "tarjeta corporativa",
            "referencia 28",
            "ref 28",
        ),
        signals=(
            "odilon",
            "fgv",
            "45007",
            "comprobacion",
            "comprobar",
            "estado de cuenta",
            "tarifa aerea",
        ),
    ),
    _NaturalPreviewRule(
        task_id="SAMCHAT-SUPPLIER-HOTEL-001",
        required_any=("hospedaje", "ish", "impuesto sobre hospedaje", "hotel"),
        signals=("leon", "128", "impuesto local", "cfdi", "cuenta contable"),
    ),
    _NaturalPreviewRule(
        task_id="SAMCHAT-TEAM-REG-001",
        required_any=("equipo", "cedula", "registro", "roster", "aguiluchos"),
        signals=(
            "jugadores",
            "juvenil",
            "tercera pagina",
            "pagina tres",
            "copa telmex",
        ),
    ),
    _NaturalPreviewRule(
        task_id="SAMCHAT-PLAYER-ELIG-001",
        required_any=("jugador", "elegibilidad", "axel"),
        signals=(
            "curp",
            "documentos",
            "fecha de nacimiento",
            "soto ramirez",
            "validacion",
        ),
    ),
    _NaturalPreviewRule(
        task_id="SAMCHAT-DOC-INCIDENT-001",
        required_any=("incidente", "curp duplicada", "duplicidad", "documental"),
        signals=("curp", "documento", "revision humana", "excepcion"),
    ),
    _NaturalPreviewRule(
        task_id="SAMCHAT-MONEY-REQ-001",
        required_any=("solicitud", "s-2600071", "reembolso", "referencia 9", "ref 9"),
        signals=(
            "bibiana",
            "628",
            "deudores",
            "transferencia",
            "referencia operaciones",
        ),
    ),
    _NaturalPreviewRule(
        task_id="SAMCHAT-BUDGET-2027-001",
        required_any=("presupuesto", "budget", "reforecast"),
        signals=("2027", "historico", "anual", "dcc", "4100-001-004"),
    ),
    _NaturalPreviewRule(
        task_id="SAMCHAT-TOURNAMENT-2027-001",
        required_any=(
            "crear torneo",
            "torneo 2027",
            "copa telmex 2027",
            "sub-17",
            "sub 17",
        ),
        signals=("torneo", "categoria", "copa telmex", "2026", "nuevo"),
    ),
    _NaturalPreviewRule(
        task_id="SAMCHAT-OWNER-DCC-001",
        required_any=("dueno pack", "owner pack", "carpeta", "entidad"),
        signals=("dcc", "bimbo", "torneo", "direccion", "finanzas"),
    ),
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


def _normalize_text(raw_message: str) -> str:
    text = unicodedata.normalize("NFKD", str(raw_message or "").casefold())
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def _contains_any(text: str, terms: Sequence[str]) -> bool:
    return any(term in text for term in terms)


def _natural_preview_task_id(normalized_text: str) -> Optional[str]:
    if not _contains_any(normalized_text, _NATURAL_PREVIEW_ACTION_TERMS):
        return None
    for rule in _NATURAL_PREVIEW_RULES:
        if not _contains_any(normalized_text, rule.required_any):
            continue
        signal_count = sum(1 for signal in rule.signals if signal in normalized_text)
        if signal_count >= rule.min_signal_count:
            return rule.task_id
    return None


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
