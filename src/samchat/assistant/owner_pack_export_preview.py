"""Read-only Owner Pack export/print preview helpers."""
from __future__ import annotations

import csv
import hashlib
import html
import io
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from .business_diff_preview import NOT_EXECUTED

try:  # pragma: no cover
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas as pdf_canvas
except Exception:  # pragma: no cover
    A4 = None
    pdf_canvas = None

OWNER_PACK_EXPORT_PREVIEW_ONLY = "owner_pack_export_preview_only"
OWNER_PACK_EXPORT_PREVIEW_SCHEMA = "samchat.owner_pack_export_preview.v1"


@dataclass(frozen=True)
class OwnerPackExportSection:
    section_id: str
    title: str
    status: str
    coverage_score: int = 0
    supported: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    next_questions: list[str] = field(default_factory=list)
    non_claims: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OwnerPackExportPreview:
    preview_id: str
    schema_version: str
    title: str
    generated_at: str
    mode: str
    target: dict[str, Any]
    status: str
    coverage_score: int
    sections: list[OwnerPackExportSection]
    evidence_links: list[str]
    missing_items: list[str]
    non_claims: list[str]
    next_questions: list[str]
    source_artifacts: list[str]
    formats: dict[str, Any]
    safety_summary: dict[str, Any]
    execution_status: str = NOT_EXECUTED
    writes_attempted: int = 0
    side_effects_detected: int = 0
    audit_language: str = OWNER_PACK_EXPORT_PREVIEW_ONLY

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["sections"] = [section.to_dict() for section in self.sections]
        return payload


def _safe_str(value: Any) -> str:
    return str(value or "").strip()


def _as_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if hasattr(value, "to_dict"):
        raw = value.to_dict()
        return dict(raw) if isinstance(raw, Mapping) else {}
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _dedupe(values: Sequence[Any], *, limit: int = 50) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _safe_str(value)
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
        if len(result) >= limit:
            break
    return result


def _card_sections(cards: Sequence[Mapping[str, Any]]) -> list[OwnerPackExportSection]:
    sections: list[OwnerPackExportSection] = []
    for card in cards:
        section_id = _safe_str(card.get("section_id") or card.get("card_id"))
        if not section_id:
            continue
        sources = _dedupe(card.get("available_sources") or card.get("items") or [])
        sections.append(
            OwnerPackExportSection(
                section_id=section_id,
                title=_safe_str(card.get("label") or card.get("title")) or section_id,
                status=_safe_str(card.get("status")) or "unknown",
                coverage_score=int(card.get("coverage_score") or 0),
                supported=sources,
                missing=_dedupe(card.get("missing_items") or []),
                evidence=sources,
                next_questions=_dedupe(card.get("next_questions") or [], limit=8),
            )
        )
    return sections


def _workspace_sections(sections: Sequence[Mapping[str, Any]]) -> list[OwnerPackExportSection]:
    result: list[OwnerPackExportSection] = []
    for section in sections:
        section_id = _safe_str(section.get("section_id"))
        if not section_id:
            continue
        result.append(
            OwnerPackExportSection(
                section_id=section_id,
                title=_safe_str(section.get("title")) or section_id,
                status=_safe_str(section.get("status")) or "unknown",
                supported=_dedupe(section.get("supported") or []),
                missing=_dedupe(section.get("missing") or []),
                evidence=_dedupe(section.get("evidence") or []),
            )
        )
    return result


def _digest(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def build_owner_pack_export_preview(
    *,
    dashboard: Mapping[str, Any] | None = None,
    readiness: Mapping[str, Any] | None = None,
    workspace: Mapping[str, Any] | None = None,
    variable_answers: Sequence[Mapping[str, Any]] | None = None,
    soul_bridge: Mapping[str, Any] | None = None,
    target: Mapping[str, Any] | None = None,
) -> OwnerPackExportPreview:
    """Build an inert Owner Pack package preview from existing artifacts."""
    dashboard = _as_dict(dashboard)
    readiness = _as_dict(readiness or dashboard.get("readiness"))
    workspace = _as_dict(workspace)
    soul_bridge = _as_dict(soul_bridge)
    variable_answers = [dict(item) for item in (variable_answers or []) if isinstance(item, Mapping)]

    merged_target: dict[str, Any] = {}
    for source in (dashboard.get("target"), readiness.get("target"), workspace.get("target"), target):
        if isinstance(source, Mapping):
            merged_target.update({k: v for k, v in source.items() if v not in (None, "")})

    sections: list[OwnerPackExportSection] = []
    sections += _card_sections([item for item in _as_list(dashboard.get("cards")) if isinstance(item, Mapping)])
    sections += _workspace_sections([item for item in _as_list(workspace.get("folder_sections")) if isinstance(item, Mapping)])

    if variable_answers:
        supported: list[str] = []
        missing: list[str] = []
        evidence: list[str] = []
        questions: list[str] = []
        for answer in variable_answers:
            question = _safe_str(answer.get("question") or answer.get("canonical_variable") or answer.get("variable"))
            status = _safe_str(answer.get("status"))
            if question:
                questions.append(question)
            if status in {"supported", "answer_supported", "ready_for_readonly_review"}:
                supported.append(question or status)
            else:
                missing.append(question or status or "Variable sin dato soportado")
            evidence += _as_list(answer.get("evidence") or answer.get("sources"))
        sections.append(OwnerPackExportSection("owner_variable_answers", "Variables respondidas", "partial" if missing else "supported", supported=_dedupe(supported), missing=_dedupe(missing), evidence=_dedupe(evidence), next_questions=_dedupe(questions)))

    if soul_bridge:
        sections.append(OwnerPackExportSection("soul_wizard_bridge", "SOUL Wizard bridge", _safe_str(soul_bridge.get("status")) or "available", supported=_dedupe(soul_bridge.get("supported") or soul_bridge.get("available_fields") or []), missing=_dedupe(soul_bridge.get("missing") or soul_bridge.get("missing_fields") or []), evidence=_dedupe(soul_bridge.get("evidence") or [])))

    if not sections:
        sections.append(OwnerPackExportSection("owner_pack_preview_empty", "Owner Pack", "needs_source_artifact", missing=["No hay dashboard/workspace/variables para renderizar el paquete."], non_claims=["No se afirma que el paquete este completo sin evidencia fuente."]))

    evidence_links = _dedupe([item for section in sections for item in section.evidence] + _as_list(readiness.get("evidence_found")) + _as_list(workspace.get("evidence")))
    missing_items = _dedupe([item for section in sections for item in section.missing] + _as_list(readiness.get("missing_evidence")) + _as_list(workspace.get("missing_fields")))
    non_claims = _dedupe([item for section in sections for item in section.non_claims] + _as_list(workspace.get("non_claims")) + ["Preview read-only: no publica, no manda al dueno, no crea carpetas y no ejecuta acciones reales.", "Los campos sin evidencia quedan como faltantes; no se inventan personas, fechas, montos ni telefonos."])
    next_questions = _dedupe([item for section in sections for item in section.next_questions] + _as_list(dashboard.get("next_questions")) + _as_list(readiness.get("next_questions")) + _as_list(workspace.get("next_questions")), limit=12)
    supported_count = sum(len(section.supported) for section in sections)
    missing_count = len(missing_items)
    computed_coverage = round((supported_count / (supported_count + missing_count)) * 100) if supported_count + missing_count else 0
    coverage_score = int(dashboard.get("coverage_score") or readiness.get("readiness_score") or computed_coverage or 0)
    status = _safe_str(dashboard.get("overall_status") or readiness.get("status") or workspace.get("status")) or "preview_ready"
    source_artifacts = _dedupe(_as_list(dashboard.get("source_reports")) + _as_list(readiness.get("source_reports")) + _as_list(workspace.get("source_reports")) + ["assistant.owner_pack_readiness", "assistant.owner_entity_folder_workspace"])
    digest = _digest({"target": merged_target, "status": status, "coverage": coverage_score, "missing": missing_items, "evidence": evidence_links})
    return OwnerPackExportPreview(
        preview_id=f"owner-pack-preview-{digest}", schema_version=OWNER_PACK_EXPORT_PREVIEW_SCHEMA, title="Owner Pack - preview revisable", generated_at=datetime.now(timezone.utc).isoformat(), mode="read_only_preview", target=merged_target, status=status, coverage_score=coverage_score, sections=sections, evidence_links=evidence_links, missing_items=missing_items, non_claims=non_claims, next_questions=next_questions, source_artifacts=source_artifacts,
        formats={"html": {"available": True, "route": "/api/assistant/owner-pack/export-preview.html"}, "print": {"available": True, "route": "/api/assistant/owner-pack/export-preview.html?print=1"}, "pdf": {"available": pdf_canvas is not None and A4 is not None, "route": "/api/assistant/owner-pack/export-preview.pdf"}, "excel_index": {"available": True, "route": "/api/assistant/owner-pack/export-preview.csv"}},
        safety_summary={"read_only_preview": True, "writes_enabled": False, "approval_required_for_publication": True, "publishes_automatically": False, "source_artifacts_required": True},
    )


def _list_html(items: Sequence[str], empty: str = "-") -> str:
    clean = _dedupe(items, limit=20)
    if not clean:
        return f"<p class='muted'>{html.escape(empty)}</p>"
    return "<ul>" + "".join(f"<li>{html.escape(item)}</li>" for item in clean) + "</ul>"


def render_owner_pack_preview_html(preview: OwnerPackExportPreview | Mapping[str, Any], *, print_mode: bool = False) -> str:
    payload = preview.to_dict() if hasattr(preview, "to_dict") else dict(preview)
    sections = [item for item in _as_list(payload.get("sections")) if isinstance(item, Mapping)]
    target = _as_dict(payload.get("target"))
    css = """body{font-family:Inter,Arial,sans-serif;color:#0f172a;margin:32px;background:#f8fafc}.page{max-width:1120px;margin:auto;background:#fff;border:1px solid #e2e8f0;border-radius:18px;padding:28px}.badge{display:inline-block;background:#dbeafe;color:#1e40af;border-radius:999px;padding:6px 12px;font-weight:700;font-size:12px;text-transform:uppercase}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:14px;margin:18px 0}.card{border:1px solid #e2e8f0;border-radius:14px;padding:16px;background:#fff}.muted{color:#64748b}.status{font-weight:800}.section{break-inside:avoid;margin-top:18px}h1{margin-bottom:6px}h2{font-size:18px;margin:0 0 8px}.score{font-size:36px;font-weight:900;color:#0f766e}@media print{body{background:#fff;margin:0}.page{border:0;border-radius:0}.no-print{display:none}}"""
    target_html = "".join(f"<div class='card'><div class='muted'>{html.escape(str(k))}</div><strong>{html.escape(str(v))}</strong></div>" for k, v in target.items()) or "<div class='card muted'>Sin torneo/entidad especificada</div>"
    rendered_sections = []
    for section in sections:
        rendered_sections.append("<div class='card section'>" + f"<h2>{html.escape(_safe_str(section.get('title')))}</h2><p class='status'>Estado: {html.escape(_safe_str(section.get('status')))} · Cobertura: {int(section.get('coverage_score') or 0)}%</p>" + "<div class='grid'>" + f"<div><strong>Soportado</strong>{_list_html(section.get('supported') or [])}</div>" + f"<div><strong>Faltantes</strong>{_list_html(section.get('missing') or [])}</div>" + f"<div><strong>Evidencia</strong>{_list_html(section.get('evidence') or [])}</div>" + "</div></div>")
    return "<!doctype html><html lang='es'><head><meta charset='utf-8'><title>Owner Pack preview</title><style>" + css + "</style></head><body><main class='page'><span class='badge'>Read-only preview</span><h1>Owner Pack - preview revisable</h1><p class='muted'>No publica, no escribe datos, no notifica y no ejecuta acciones reales.</p><div class='grid'><div class='card'><div class='muted'>Estado</div><strong>" + html.escape(_safe_str(payload.get('status'))) + "</strong></div><div class='card'><div class='muted'>Cobertura</div><div class='score'>" + str(int(payload.get('coverage_score') or 0)) + "%</div></div>" + target_html + "</div><h2>Secciones</h2>" + "".join(rendered_sections) + "<div class='grid'><div class='card'><h2>Faltantes explicitos</h2>" + _list_html(payload.get('missing_items') or []) + "</div><div class='card'><h2>Non-claims</h2>" + _list_html(payload.get('non_claims') or []) + "</div><div class='card'><h2>Preguntas siguientes</h2>" + _list_html(payload.get('next_questions') or []) + "</div></div></main></body></html>"


def owner_pack_preview_excel_rows(preview: OwnerPackExportPreview | Mapping[str, Any]) -> list[dict[str, Any]]:
    payload = preview.to_dict() if hasattr(preview, "to_dict") else dict(preview)
    rows: list[dict[str, Any]] = []
    for section in [item for item in _as_list(payload.get("sections")) if isinstance(item, Mapping)]:
        for kind, values in (("supported", section.get("supported") or []), ("missing", section.get("missing") or []), ("evidence", section.get("evidence") or []), ("non_claim", section.get("non_claims") or []), ("next_question", section.get("next_questions") or [])):
            for value in _dedupe(values, limit=100):
                rows.append({"preview_id": payload.get("preview_id"), "section_id": section.get("section_id"), "section": section.get("title"), "status": section.get("status"), "kind": kind, "value": value})
    return rows or [{"preview_id": payload.get("preview_id"), "section_id": "owner_pack", "section": payload.get("title"), "status": payload.get("status"), "kind": "empty", "value": "Sin filas para exportar."}]


def owner_pack_preview_csv_bytes(preview: OwnerPackExportPreview | Mapping[str, Any]) -> bytes:
    output = io.StringIO()
    fieldnames = ["preview_id", "section_id", "section", "status", "kind", "value"]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for row in owner_pack_preview_excel_rows(preview):
        writer.writerow({key: row.get(key, "") for key in fieldnames})
    return output.getvalue().encode("utf-8-sig")


def owner_pack_preview_pdf_bytes(preview: OwnerPackExportPreview | Mapping[str, Any]) -> bytes:
    if pdf_canvas is None or A4 is None:
        raise RuntimeError("PDF engine not available")
    payload = preview.to_dict() if hasattr(preview, "to_dict") else dict(preview)
    buffer = io.BytesIO()
    canvas = pdf_canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    y = height - 48
    def line(text: str, size: int = 10, bold: bool = False) -> None:
        nonlocal y
        if y < 64:
            canvas.showPage(); y = height - 48
        canvas.setFont("Helvetica-Bold" if bold else "Helvetica", size)
        canvas.drawString(42, y, text[:110]); y -= size + 7
    line("Owner Pack - preview revisable", 16, True)
    line("Read-only: no publica, no escribe datos y no ejecuta acciones reales.", 10, True)
    line(f"Estado: {_safe_str(payload.get('status'))} | Cobertura: {int(payload.get('coverage_score') or 0)}%")
    for key, value in _as_dict(payload.get("target")).items():
        line(f"{key}: {value}")
    for section in [item for item in _as_list(payload.get("sections")) if isinstance(item, Mapping)]:
        line(_safe_str(section.get("title")) or _safe_str(section.get("section_id")), 12, True)
        line(f"Estado: {_safe_str(section.get('status'))} | Cobertura: {int(section.get('coverage_score') or 0)}%")
        for label, key in (("Soportado", "supported"), ("Faltantes", "missing"), ("Evidencia", "evidence")):
            values = _dedupe(section.get(key) or [], limit=8)
            if values:
                line(label, 10, True)
                for value in values:
                    line(f"- {value}")
    canvas.save()
    return buffer.getvalue()


def owner_pack_export_preview_contains_execution_claim(preview: OwnerPackExportPreview | Mapping[str, Any]) -> bool:
    payload = preview.to_dict() if hasattr(preview, "to_dict") else dict(preview)
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).casefold()
    return any(item in blob for item in ("writes_attempted\": 1", "side_effects_detected\": 1", "published\": true", "sent_to_owner\": true"))
