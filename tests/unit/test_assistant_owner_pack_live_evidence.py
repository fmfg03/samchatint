from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from samchat.assistant.owner_pack_live_evidence import (
    OWNER_PACK_LIVE_EVIDENCE_ONLY,
    OWNER_PACK_LOCAL_DB_SOURCE,
    build_owner_pack_live_report_from_tournament_source,
)
from samchat.assistant.owner_pack_live_snapshot import (
    OWNER_PACK_LIVE_MISSING,
    OWNER_PACK_LIVE_SUPPORTED,
)
from samchat.assistant.tournament_goal_source import (
    LocalTournamentOperationsAggregate,
    LocalTournamentOperationsLink,
    LocalTournamentProject,
    TournamentSourceSnapshot,
)


def _snapshot(**operation_overrides):
    operations = {
        "available": True,
        "scope_slug": "copa-telmex-2026",
        "teams_count": 12,
        "players_count": 180,
        "categories": ["Juvenil"],
        "branches": ["Varonil", "Femenil"],
        "states": ["Jalisco"],
        "municipalities": ["Zapopan"],
    }
    operations.update(operation_overrides)
    project = LocalTournamentProject(
        id=uuid4(),
        name="Copa Telmex Telcel de Futbol",
        description="Torneo nacional",
        active=True,
        display_order=1,
        cuenta_contable_relacionada="5300-010",
        etapas=["Estatal", "Nacional"],
        categorias=["Juvenil"],
        form_visibility_departments=["Operaciones"],
        created_at=datetime(2026, 1, 1),
        updated_at=datetime(2026, 1, 2),
    )
    return TournamentSourceSnapshot(
        project=project,
        operations_link=LocalTournamentOperationsLink(
            operations_tournament_id="ops-1",
            operations_tournament_slug="copa-telmex-2026",
        ),
        observed_operations=LocalTournamentOperationsAggregate(**operations),
        unavailable_components=["matches_and_schedule"],
        source_hash="sha256:test",
    )


def _field(report, field_name):
    surface = report.surfaces[0]
    for item in surface.fields:
        if item.field == field_name:
            return item
    raise AssertionError(field_name)


def test_local_tournament_source_supports_exact_owner_pack_fields():
    report = build_owner_pack_live_report_from_tournament_source(
        _snapshot(),
        surface_id="entity_folder",
        entity_name="Jalisco",
    )

    assert report.audit_language == OWNER_PACK_LIVE_EVIDENCE_ONLY
    assert report.writes_attempted == 0
    assert report.side_effects_detected == 0
    assert report.safety_summary["source"] == OWNER_PACK_LOCAL_DB_SOURCE
    assert _field(report, "tournament").status == OWNER_PACK_LIVE_SUPPORTED
    assert _field(report, "real_teams").value[0]["teams_count_total"] == 12
    assert _field(report, "players_by_category_age_gender").value[0]["players_count_total"] == 180
    assert _field(report, "state_phase_operations").value["configured_phases"] == [
        "Estatal",
        "Nacional",
    ]
    assert _field(report, "operator_payments").status == OWNER_PACK_LIVE_MISSING
    assert OWNER_PACK_LOCAL_DB_SOURCE in report.surfaces[0].workspace_files_found


def test_local_tournament_source_fail_closes_unbound_fields():
    report = build_owner_pack_live_report_from_tournament_source(
        _snapshot(teams_count=0, players_count=0, categories=[], branches=[]),
        surface_id="entity_folder",
    )

    assert _field(report, "real_teams").status == OWNER_PACK_LIVE_MISSING
    assert _field(report, "players_by_category_age_gender").status == OWNER_PACK_LIVE_MISSING
    assert _field(report, "expected_teams").status == OWNER_PACK_LIVE_MISSING
    assert _field(report, "expected_teams").value is None
