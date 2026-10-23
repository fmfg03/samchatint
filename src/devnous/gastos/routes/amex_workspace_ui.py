"""Presentation-only primitives for the direct AMEX workspace."""

from collections import Counter
from functools import lru_cache
from hashlib import sha256
from html import escape
from pathlib import Path
from typing import Any

_ASSETS = Path(__file__).resolve().parents[4] / "static"


@lru_cache(maxsize=2)
def _asset_version(name: str) -> str:
    return sha256((_ASSETS / name).read_bytes()).hexdigest()[:12]


def workspace_assets() -> str:
    """Version existing-root static assets without adding a runtime mount."""
    css = _asset_version("amex_workspace.css")
    js = _asset_version("amex_workspace.js")
    return (
        f'<link rel="stylesheet" href="/static/amex_workspace.css?v={css}">'
        f'<script src="/static/amex_workspace.js?v={js}" defer></script>'
    )


def workspace_view(
    title: str,
    body: str,
    *,
    kind: str = "detail",
    statement_url: str = "",
    source_version: int | None = None,
    pending_budget: int = 0,
) -> str:
    """Mark controlled server content; no financial state is inferred here."""
    root = kind in {"intake", "statement"}
    return (
        '<div id="amex-workspace-view" class="amex-workspace-view" '
        f'data-amex-view="{escape(kind, quote=True)}" '
        f'data-amex-root="{str(root).lower()}" '
        f'data-amex-title="{escape(title, quote=True)}" '
        f'data-statement-url="{escape(statement_url, quote=True)}" '
        f'data-source-version="{source_version or ""}" '
        f'data-pending-budget="{pending_budget}">' + body + "</div>"
    )


def workspace_steps(*, intake: bool = False, finalized: bool = False) -> str:
    current = 1 if intake else 3 if finalized else 2
    entries = []
    for number, label in enumerate(
        ("Cargar estado", "Resolver cargos", "Obtener póliza"), start=1
    ):
        entries.append(
            '<li class="amex-workspace-step"'
            + (' aria-current="step"' if current == number else "")
            + f"><span>{number}</span> {label}</li>"
        )
    return (
        '<nav class="amex-workspace-steps" aria-label="Etapas del estado AMEX">'
        '<ol class="amex-workspace-stages">' + "".join(entries) + "</ol></nav>"
    )


def row_bucket(item: Any, preview: Any) -> str:
    """Use owner diagnostics, never absence of issues as posting authority."""
    issues = {
        str(issue.get("item_id"))
        for issue in (getattr(preview, "progress", None) or {}).get("issues", [])
    }
    identity = str(item.id)
    if item.accounting_status in {"recognized", "bound"} or getattr(
        item, "recognized_poliza_ids", None
    ):
        return "done"
    if item.movement_kind != "debit":
        return "pending" if identity in issues else "credit"
    kind = getattr(item, "treatment_kind", None) or "project_expense"
    if item.budget_status == "sent" and kind in {
        "project_expense",
        "benefit_expense",
    }:
        return "budget"
    if identity in issues:
        return "pending"
    progress = getattr(preview, "progress", None) or {}
    if (
        preview.status == "ready"
        and getattr(preview, "cut", None) is None
        and not progress.get("completion_receipt")
    ):
        return "ready"
    return "review"


def workspace_summary(items: list[Any], preview: Any) -> tuple[str, dict[str, int]]:
    counts = Counter(row_bucket(item, preview) for item in items)
    ready = (
        getattr(preview, "status", None) == "ready"
        and getattr(preview, "cut", None) is None
        and not (getattr(preview, "progress", None) or {}).get("completion_receipt")
    )
    third_label = "Listos para póliza" if ready else "Sin bloqueos individuales"
    third_count = counts["ready"] if ready else counts["review"]
    cards = (
        ("Movimientos", len(items)),
        ("Por resolver", counts["pending"]),
        ("Pendientes de Juan Pablo", counts["budget"]),
        (third_label, third_count),
    )
    html = '<div class="amex-workspace-counters" aria-label="Avance del estado">'
    for label, count in cards:
        html += f"<div><span>{label}</span><strong>{count}</strong></div>"
    html += "</div>"
    if not ready and getattr(preview, "cut", None) is None:
        html += (
            '<p class="amex-workspace-note">La póliza se confirma cuando el estado '
            "completo esté listo. Las filas sin bloqueos individuales conservan "
            "la revisión del estado completo.</p>"
        )
    return html, dict(counts)


def workspace_filters() -> str:
    choices = (
        ("all", "Todos los movimientos"),
        ("pending", "Por resolver"),
        ("budget", "Pendientes de Juan Pablo"),
        ("review", "Sin bloqueos individuales"),
        ("ready", "Listos para póliza"),
        ("done", "Reconocidos o cubiertos"),
        ("credit", "Créditos y otras disposiciones"),
    )
    options = "".join(
        f'<option value="{key}">{label}</option>' for key, label in choices
    )
    return (
        '<div class="amex-workspace-filters">'
        '<label>Buscar movimiento<input type="search" data-amex-search-input '
        'placeholder="Comercio, importe o fecha" autocomplete="off"></label>'
        "<label>Mostrar<select data-amex-filter>" + options + "</select></label>"
        '<button type="button" class="button secondary" data-amex-refresh>'
        "Actualizar estado</button></div>"
    )


def workspace_toolbar(
    *, classification: bool, treatment: bool, finance: bool, frozen: bool
) -> str:
    if frozen:
        return ""
    actions = []
    if treatment:
        actions.append(("treatment", "Elegir tratamiento", True))
    if classification:
        actions.extend(
            (
                ("project", "Clasificar gasto de proyecto", True),
                ("budget", "Enviar a Juan Pablo", True),
            )
        )
    if finance:
        actions.append(("no-cfdi", "Declarar o revisar Sin CFDI", True))
    actions.append(("review", "Otras revisiones", False))
    html = '<div class="amex-workspace-toolbar" aria-label="Acciones de los cargos">'
    for key, label, selection in actions:
        html += (
            '<button type="button" class="button secondary" '
            f'data-amex-open="{key}" data-amex-title="{label}"'
            + (" data-amex-selection-required disabled" if selection else "")
            + f">{label}</button>"
        )
    return html + "</div>"


def dialog_source(name: str, content: str, *, disabled: bool = False) -> str:
    """Keep permission-disabled fieldsets inside sources moved to a dialog."""
    if disabled:
        content = "<fieldset disabled>" + content + "</fieldset>"
    return f'<div data-amex-dialog-source="{escape(name, quote=True)}">{content}</div>'
