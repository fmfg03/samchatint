"""Shared visual semantics for finance workflow statuses.

This module is presentation-only. It must not be used to infer a business
transition, payment evidence, eligibility, or authorization.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class StatusVisual:
    """Accessible visual treatment for one already-canonical status."""

    label: str
    note: str
    semantic: str
    badge_class: str
    background: str
    foreground: str


_PALETTE = {
    "neutral": ("muted", "#e2e8f0", "#334155"),
    "attention": ("warn", "#fef3c7", "#92400e"),
    "progress": ("progress", "#ede9fe", "#5b21b6"),
    "success": ("success", "#dcfce7", "#166534"),
    "danger": ("error", "#fee2e2", "#991b1b"),
}


def _visual(label: str, note: str, semantic: str) -> StatusVisual:
    badge_class, background, foreground = _PALETTE[semantic]
    return StatusVisual(
        label, note, semantic, badge_class, background, foreground
    )


_DOCUMENT_VISUALS = {
    "borrador": _visual("Borrador", "Te falta enviarlo", "neutral"),
    "control_presupuestal": _visual(
        "Control presupuestal",
        "Pendiente de asignación presupuestal",
        "attention",
    ),
    "enviado": _visual("En revisión", "Esperando aprobación", "attention"),
    "en_revision": _visual("En revisión", "Esperando aprobación", "attention"),
    "en revisión": _visual("En revisión", "Esperando aprobación", "attention"),
    # Approval authorizes the request; it is not proof that payment occurred.
    "aprobado": _visual(
        "Aprobado", "Listo para pago o siguiente paso", "attention"
    ),
    "autorizado": _visual(
        "Aprobado", "Listo para pago o siguiente paso", "attention"
    ),
    "en_proceso_pago": _visual(
        "En proceso de pago", "Pago en ejecución", "progress"
    ),
    "pagado": _visual("Pagado", "Pago registrado", "success"),
    "rechazado": _visual("Rechazado", "Requiere corrección", "danger"),
    "cancelado": _visual("Cancelado", "Sin acción pendiente", "danger"),
    "cerrado": _visual("Cerrado", "Ciclo cerrado", "success"),
    "liquidado": _visual("Liquidado", "Ciclo cerrado", "success"),
    "reembolsado": _visual("Reembolsado", "Ciclo cerrado", "success"),
}


_PAYMENT_RUN_VISUALS = {
    "programada": _visual("Programada", "Pendiente de pago", "attention"),
    "vencida": _visual("Vencida", "Requiere atención", "danger"),
    "cerrada": _visual("Cerrada", "Pago en ejecución", "progress"),
    "en proceso de pago": _visual(
        "En proceso de pago", "Pago en ejecución", "progress"
    ),
    "pagada": _visual("Pagada", "Pago registrado", "success"),
}


def document_status_visual(value: Optional[str]) -> StatusVisual:
    """Return a visual treatment for a canonical ``Documento.estado`` value."""
    normalized = (value or "").strip().lower()
    if normalized in _DOCUMENT_VISUALS:
        return _DOCUMENT_VISUALS[normalized]
    label = (value or "Sin estado").replace("_", " ").capitalize()
    return _visual(label, "Revisar estado", "neutral")


def payment_run_status_visual(value: Optional[str]) -> StatusVisual:
    """Return a visual treatment for an operational Payment Run status."""
    normalized = (value or "").strip().lower()
    if normalized in _PAYMENT_RUN_VISUALS:
        return _PAYMENT_RUN_VISUALS[normalized]
    return _visual(value or "-", "Revisar estado", "neutral")
