from __future__ import annotations

from samchat.assistant.owner_pack_export_preview import (
    OWNER_PACK_EXPORT_PREVIEW_ONLY,
    build_owner_pack_export_preview,
    owner_pack_export_preview_contains_execution_claim,
    owner_pack_preview_csv_bytes,
    owner_pack_preview_excel_rows,
    render_owner_pack_preview_html,
)


def _dashboard() -> dict:
    return {
        "target": {"tournament_slug": "copa-telmex", "entity_name": "Jalisco"},
        "overall_status": "partial_live_evidence",
        "coverage_score": 42,
        "source_reports": ["owner_pack_readiness_v1"],
        "cards": [
            {
                "section_id": "entity_folder",
                "label": "Entity folder",
                "status": "partial_live_evidence",
                "coverage_score": 42,
                "available_sources": ["operations.json"],
                "missing_items": ["Telefono del contacto"],
                "next_questions": ["Quien es el contacto de Jalisco?"],
            }
        ],
        "readiness": {
            "readiness_id": "owner_pack_readiness_v1",
            "status": "partial_live_evidence",
            "readiness_score": 42,
            "evidence_found": ["Nombre de entidad: operations.json"],
            "missing_evidence": ["Fecha de entrega de uniformes"],
            "next_questions": ["Cuando entregan uniformes?"],
        },
    }


def test_owner_pack_export_preview_is_read_only_and_renderable() -> None:
    preview = build_owner_pack_export_preview(dashboard=_dashboard())
    payload = preview.to_dict()

    assert payload["schema_version"] == "samchat.owner_pack_export_preview.v1"
    assert payload["audit_language"] == OWNER_PACK_EXPORT_PREVIEW_ONLY
    assert payload["mode"] == "read_only_preview"
    assert payload["execution_status"] == "not_executed"
    assert payload["writes_attempted"] == 0
    assert payload["side_effects_detected"] == 0
    assert payload["safety_summary"]["writes_enabled"] is False
    assert payload["safety_summary"]["publishes_automatically"] is False
    assert payload["formats"]["html"]["available"] is True
    assert payload["formats"]["excel_index"]["available"] is True
    assert payload["missing_items"]
    assert payload["non_claims"]
    assert owner_pack_export_preview_contains_execution_claim(preview) is False

    html = render_owner_pack_preview_html(preview)
    assert "Read-only preview" in html
    assert "Entity folder" in html
    assert "Telefono del contacto" in html
    assert "no ejecuta acciones reales" in html

    rows = owner_pack_preview_excel_rows(preview)
    assert {row["kind"] for row in rows} >= {"supported", "missing", "evidence", "next_question"}
    csv_bytes = owner_pack_preview_csv_bytes(preview)
    assert b"preview_id,section_id,section,status,kind,value" in csv_bytes
