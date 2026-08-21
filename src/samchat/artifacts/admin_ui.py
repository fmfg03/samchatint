"""Admin HTML rendering for the runtime artifact index."""

from __future__ import annotations

from html import escape
from typing import Any


def _text(value: Any, fallback: str = "-") -> str:
    text = str(value or "").strip()
    return escape(text or fallback)


def _rows(items: list[dict[str, Any]]) -> str:
    if not items:
        return '<tr><td colspan="7" class="artifact-muted">Sin superficies.</td></tr>'
    rendered = []
    for item in items:
        rendered.append(
            "<tr>"
            f"<td>{_text(item.get('surface'))}</td>"
            f"<td>{_text(item.get('artifact_class'))}</td>"
            f"<td>{_text(item.get('owner'))}</td>"
            f"<td><code>{_text(item.get('route_or_tool'))}</code></td>"
            f"<td>{_text(item.get('status'))}</td>"
            f"<td>{_text(item.get('authority'))}</td>"
            f"<td>{_text(item.get('notes'))}</td>"
            "</tr>"
        )
    return "".join(rendered)


def _rules(items: list[Any]) -> str:
    if not items:
        return '<li class="artifact-muted">Sin reglas.</li>'
    return "".join(f"<li>{_text(item)}</li>" for item in items)


def _metric(label: str, value: Any, note: str) -> str:
    try:
        count = int(value or 0)
    except (TypeError, ValueError):
        count = 0
    return (
        "<div>"
        f"<span>{escape(label)}</span>"
        f"<strong>{count}</strong>"
        f"<small>{escape(note)}</small>"
        "</div>"
    )


def render_runtime_artifact_index_html(payload: dict[str, Any]) -> str:
    """Render the runtime artifact index as a read-only admin body fragment."""

    summary = payload.get("summary") or {}
    metrics_html = "".join(
        [
            _metric("Superficies", summary.get("surface_count"), "Indexadas"),
            _metric(
                "Assistant artifacts",
                summary.get("runtime_saved_artifact_count"),
                "Solo clase",
            ),
            _metric("Exports", summary.get("report_export_count"), "Generados"),
            _metric(
                "Closeouts",
                summary.get("evidence_closeout_count"),
                "Historicos",
            ),
            _metric(
                "Planeados",
                summary.get("planned_artifact_count"),
                "No live",
            ),
        ]
    )
    return f"""
        <section class="workspace-card artifact-warning" style="margin-bottom:18px;">
            <div class="workspace-section-title">Artifact runtime index read-only</div>
            <div class="workspace-section-subtitle">
                Este indice distingue objetos runtime, exports generados,
                evidencia historica y artefactos planeados. No ejecuta exports,
                no consulta contenido de assistant_artifacts y no crea un
                archivo gestionado.
            </div>
        </section>
        <section class="workspace-card" style="margin-bottom:18px;">
            <div class="workspace-section-title">Resumen</div>
            <div class="artifact-metrics">{metrics_html}</div>
        </section>
        <section class="workspace-card" style="margin-bottom:18px;">
            <div class="workspace-section-title">Runtime saved artifacts</div>
            <table class="artifact-table">
                <thead><tr><th>Superficie</th><th>Clase</th><th>Owner</th><th>Ruta/tool</th><th>Status</th><th>Authority</th><th>Notas</th></tr></thead>
                <tbody>{_rows(list(payload.get("runtime_saved_artifacts") or []))}</tbody>
            </table>
        </section>
        <section class="workspace-card" style="margin-bottom:18px;">
            <div class="workspace-section-title">Report exports</div>
            <table class="artifact-table">
                <thead><tr><th>Superficie</th><th>Clase</th><th>Owner</th><th>Ruta/tool</th><th>Status</th><th>Authority</th><th>Notas</th></tr></thead>
                <tbody>{_rows(list(payload.get("report_exports") or []))}</tbody>
            </table>
        </section>
        <section class="workspace-card" style="margin-bottom:18px;">
            <div class="workspace-section-title">Evidencia y planeados</div>
            <table class="artifact-table">
                <thead><tr><th>Superficie</th><th>Clase</th><th>Owner</th><th>Ruta/tool</th><th>Status</th><th>Authority</th><th>Notas</th></tr></thead>
                <tbody>{_rows(list(payload.get("evidence_closeouts") or []) + list(payload.get("planned_artifacts") or []))}</tbody>
            </table>
        </section>
        <section class="workspace-card">
            <div class="workspace-section-title">Reglas de boundary</div>
            <ul class="artifact-rules">{_rules(list(payload.get("boundary_rules") or []))}</ul>
        </section>
    """


def artifact_admin_styles() -> str:
    """CSS for the artifact admin fragment."""

    return """
        .artifact-warning {
            border-left:4px solid #0f766e;
        }
        .artifact-metrics {
            display:grid;
            grid-template-columns:repeat(auto-fit,minmax(160px,1fr));
            gap:12px;
        }
        .artifact-metrics div {
            background:#f8fafc;
            border:1px solid #e2e8f0;
            border-radius:8px;
            padding:12px;
            display:grid;
            gap:4px;
        }
        .artifact-metrics span,
        .artifact-metrics small {
            color:#64748b;
            font-size:12px;
        }
        .artifact-metrics strong {
            color:#0f172a;
            font-size:24px;
        }
        .artifact-table {
            width:100%;
            border-collapse:collapse;
            font-size:12px;
        }
        .artifact-table th,
        .artifact-table td {
            border-bottom:1px solid #e2e8f0;
            padding:8px;
            vertical-align:top;
            text-align:left;
        }
        .artifact-table th {
            color:#475569;
            background:#f8fafc;
        }
        .artifact-table code {
            white-space:normal;
            word-break:break-word;
        }
        .artifact-muted {
            color:#64748b;
        }
        .artifact-rules {
            margin:10px 0 0;
            padding-left:20px;
            color:#334155;
            line-height:1.6;
        }
    """
