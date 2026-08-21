from samchat.artifacts import (
    build_runtime_artifact_index,
    render_runtime_artifact_index_html,
)


def test_runtime_artifact_index_separates_artifact_classes() -> None:
    payload = build_runtime_artifact_index()

    assert payload["index_id"] == "samchat_runtime_artifact_index_v1"
    assert payload["read_only"] is True
    assert payload["summary"]["runtime_saved_artifact_count"] == 1
    assert payload["summary"]["report_export_count"] >= 5
    assert payload["summary"]["evidence_closeout_count"] == 1
    assert payload["summary"]["planned_artifact_count"] >= 1

    runtime_classes = {
        item["artifact_class"] for item in payload["runtime_saved_artifacts"]
    }
    export_routes = {item["route_or_tool"] for item in payload["report_exports"]}
    planned_statuses = {item["status"] for item in payload["planned_artifacts"]}

    assert runtime_classes == {"runtime_saved_artifact"}
    assert "GET /admin/finanzas/export.xlsx" in export_routes
    assert "GET /admin/presupuestos/export.xlsx" in export_routes
    assert "POST /api/assistant/reports/export" in export_routes
    assert "planned" in planned_statuses
    assert "live" not in planned_statuses


def test_runtime_artifact_index_preserves_boundary_rules() -> None:
    payload = build_runtime_artifact_index()
    rules = " ".join(payload["boundary_rules"])
    notes = " ".join(payload["source_notes"])

    assert "conversation-scoped" in rules
    assert "generated deliveries" in rules
    assert "historical evidence" in rules
    assert "does not create, archive, delete, or execute artifacts" in rules
    assert "Does not query assistant_artifacts content" in notes
    assert "Does not execute exports" in notes


def test_runtime_artifact_admin_html_labels_non_authority_surfaces() -> None:
    html = render_runtime_artifact_index_html(build_runtime_artifact_index())

    assert "Artifact runtime index read-only" in html
    assert "No ejecuta exports" in html
    assert "no consulta contenido de assistant_artifacts" in html
    assert "Generated delivery, not a managed artifact archive." in html
    assert "Do not claim implemented." in html
    assert "Must not be treated as live feature state." in html
