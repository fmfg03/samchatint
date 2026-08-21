"""Read-only runtime artifact index.

This module exposes discoverability metadata only. It does not query artifact
content, execute exports, create archives, or promote planned artifacts.
"""

from __future__ import annotations

from typing import Any


def _surface(
    *,
    surface: str,
    artifact_class: str,
    owner: str,
    route_or_tool: str,
    status: str,
    authority: str,
    discoverability: str,
    notes: str,
) -> dict[str, str]:
    return {
        "surface": surface,
        "artifact_class": artifact_class,
        "owner": owner,
        "route_or_tool": route_or_tool,
        "status": status,
        "authority": authority,
        "discoverability": discoverability,
        "notes": notes,
    }


RUNTIME_SAVED_ARTIFACTS: tuple[dict[str, str], ...] = (
    _surface(
        surface="Assistant saved artifacts",
        artifact_class="runtime_saved_artifact",
        owner="Assistant runtime",
        route_or_tool="assistant_save_artifact",
        status="live",
        authority="Assistant tool contract and configured confirmation/role policy.",
        discoverability="Conversation-scoped; admin index lists the class, not content.",
        notes="Does not replace report exports, expediente snapshots, sponsor packages, or budget source artifacts.",
    ),
)

REPORT_EXPORTS: tuple[dict[str, str], ...] = (
    _surface(
        surface="Assistant report export",
        artifact_class="report_export",
        owner="Assistant report/export flow",
        route_or_tool="POST /api/assistant/reports/export",
        status="live",
        authority="Exportable assistant report traces scoped to the current user conversation.",
        discoverability="Assistant report flow.",
        notes="Generated delivery, not a managed artifact archive.",
    ),
    _surface(
        surface="Finance Platform export",
        artifact_class="report_export",
        owner="Finance Platform",
        route_or_tool="GET /admin/finanzas/export.xlsx",
        status="live",
        authority="Finance Platform read model/exporter and admin finance permissions.",
        discoverability="Finance admin view.",
        notes="Separate from Assistant artifacts and legacy accounting cash-flow.",
    ),
    _surface(
        surface="Presupuestos review export",
        artifact_class="report_export",
        owner="Presupuestos",
        route_or_tool="GET /admin/presupuestos/export.xlsx",
        status="live",
        authority="Canonical Presupuestos routes and budget services.",
        discoverability="Presupuestos admin pages.",
        notes="Budget authority remains in budget versions/services.",
    ),
    _surface(
        surface="Presupuestos concept catalog export",
        artifact_class="report_export",
        owner="Presupuestos",
        route_or_tool="GET /admin/presupuestos/conceptos/export.xlsx",
        status="live",
        authority="Canonical Presupuestos routes and catalog services.",
        discoverability="Presupuestos concepts surface.",
        notes="Not an assistant artifact.",
    ),
    _surface(
        surface="Presupuestos income mirror export",
        artifact_class="report_export/budget_source_artifact",
        owner="Budget income routes",
        route_or_tool="GET /admin/presupuestos/torneo/{tournament_key}/ingresos/export.xlsx",
        status="live",
        authority="Budget income routes and services.",
        discoverability="Tournament budget income surface.",
        notes="Supports AR expected income; raw workbook is not live authority.",
    ),
    _surface(
        surface="Legacy accounting cash-flow export",
        artifact_class="report_export",
        owner="Legacy accounting route",
        route_or_tool="GET /admin/contabilidad/cash-flow/export.xlsx",
        status="legacy/reference",
        authority="Legacy accounting route authority only.",
        discoverability="Legacy accounting cash-flow page.",
        notes="Not Finance Spine authority and not assistant finance source authority.",
    ),
)

EVIDENCE_CLOSEOUTS: tuple[dict[str, str], ...] = (
    _surface(
        surface="Closeout evidence artifacts",
        artifact_class="evidence_closeout",
        owner="Engineering/release process",
        route_or_tool="artifacts/*.md, artifacts/*.json, sprint docs",
        status="historical evidence",
        authority="Git/review evidence only; not business runtime authority.",
        discoverability="Repository/docs search.",
        notes="Must not be treated as live feature state.",
    ),
)

PLANNED_ARTIFACTS: tuple[dict[str, str], ...] = (
    _surface(
        surface="Sponsor proof packages",
        artifact_class="sponsor_marketing_proof_package",
        owner="Sponsor/marketing lane",
        route_or_tool="none approved",
        status="planned",
        authority="Requires human approval and a separate story/spec before runtime creation.",
        discoverability="None approved.",
        notes="Do not claim implemented.",
    ),
    _surface(
        surface="Expediente snapshots",
        artifact_class="expediente_snapshot",
        owner="Operations/tournament/case module",
        route_or_tool="pending route/model/export contract",
        status="pending",
        authority="Domain-specific read authority; durable snapshots need explicit contract.",
        discoverability="No single durable artifact index yet.",
        notes="Not a cross-product artifact center.",
    ),
)

BOUNDARY_RULES: tuple[str, ...] = (
    "Assistant saved artifacts are conversation-scoped runtime artifacts.",
    "Report exports are owned by their domain modules and are generated deliveries.",
    "Closeout files are historical evidence, not runtime product objects.",
    "Budget source/export files can support budget workflows, but imported budget versions and services hold authority.",
    "Planned sponsor packages require a separate approved implementation track.",
    "This index is read-only and does not create, archive, delete, or execute artifacts.",
)


def build_runtime_artifact_index() -> dict[str, Any]:
    """Return the read-only artifact discoverability contract."""

    runtime_saved = [dict(item) for item in RUNTIME_SAVED_ARTIFACTS]
    report_exports = [dict(item) for item in REPORT_EXPORTS]
    evidence_closeouts = [dict(item) for item in EVIDENCE_CLOSEOUTS]
    planned_artifacts = [dict(item) for item in PLANNED_ARTIFACTS]
    all_surfaces = (
        runtime_saved + report_exports + evidence_closeouts + planned_artifacts
    )
    by_status: dict[str, int] = {}
    by_class: dict[str, int] = {}
    for item in all_surfaces:
        by_status[item["status"]] = by_status.get(item["status"], 0) + 1
        by_class[item["artifact_class"]] = by_class.get(item["artifact_class"], 0) + 1
    return {
        "index_id": "samchat_runtime_artifact_index_v1",
        "read_only": True,
        "summary": {
            "surface_count": len(all_surfaces),
            "runtime_saved_artifact_count": len(runtime_saved),
            "report_export_count": len(report_exports),
            "evidence_closeout_count": len(evidence_closeouts),
            "planned_artifact_count": len(planned_artifacts),
            "by_status": by_status,
            "by_class": by_class,
        },
        "runtime_saved_artifacts": runtime_saved,
        "report_exports": report_exports,
        "evidence_closeouts": evidence_closeouts,
        "planned_artifacts": planned_artifacts,
        "boundary_rules": list(BOUNDARY_RULES),
        "source_notes": [
            "Derived from docs/product/runtime-artifact-index.md and live route/tool contracts.",
            "Does not query assistant_artifacts content.",
            "Does not execute exports or create a managed archive.",
        ],
    }
