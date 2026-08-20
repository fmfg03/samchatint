from __future__ import annotations

from samchat.assistant.owner_pack_live_snapshot import (
    OWNER_PACK_LIVE_SNAPSHOT_ONLY,
    OWNER_PACK_LIVE_SUPPORTED,
    OwnerPackLiveFieldSnapshot,
    OwnerPackLiveSnapshotReport,
    OwnerPackLiveSurfaceSnapshot,
)
from samchat.assistant.owner_variable_query import (
    OWNER_VARIABLE_CONFLICT,
    OWNER_VARIABLE_MISSING,
    OWNER_VARIABLE_PARTIAL,
    OWNER_VARIABLE_QUERY_ONLY,
    OWNER_VARIABLE_SUPPORTED,
    OWNER_VARIABLE_UNMAPPED,
    build_owner_variable_query_report,
)


def _live_report() -> OwnerPackLiveSnapshotReport:
    field = OwnerPackLiveFieldSnapshot(
        field="real_teams",
        label="Equipos reales participantes",
        section_id="operations",
        evidence_type="team",
        status=OWNER_PACK_LIVE_SUPPORTED,
        value=[
            {
                "category": "Sub 15",
                "gender_or_branch": "Varonil",
                "teams_count_total": 8,
                "states": ["CDMX"],
                "municipalities": ["Benito Juarez"],
            }
        ],
        source_paths=["db.copa_telmex.teams", "sha256:owner-query"],
        source_files=["samchat_local_tournament_db"],
        reason="supported_by_local_samchat_source",
    )
    surface = OwnerPackLiveSurfaceSnapshot(
        surface_id="entity_folder",
        label="Carpeta por entidad",
        target={"tournament_name": "Copa Local", "entity_name": "CDMX"},
        workspace_root="samchat_local_tournament_db",
        fields=[field],
        supported_field_count=1,
        missing_field_count=0,
        audit_language=OWNER_PACK_LIVE_SNAPSHOT_ONLY,
    )
    return OwnerPackLiveSnapshotReport(
        snapshot_id="owner_pack_live_evidence_v2_entity_folder",
        headline="Evidencia viva local para Owner Pack",
        summary="Se encontro evidencia viva en la base local de SamChat.",
        surfaces=[surface],
        supported_field_count=1,
        missing_field_count=0,
        audit_language=OWNER_PACK_LIVE_SNAPSHOT_ONLY,
    )


def _wizard_payload() -> dict:
    return {
        "draft_id": "copa-local-2027",
        "tournament_name": "Copa Local",
        "edition_year": 2027,
        "categories": ["Sub 15"],
        "branches": ["Varonil"],
        "expected_entities": ["CDMX"],
        "expected_teams": 8,
        "required_documents": ["CURP"],
        "eligibility_rules": ["Sin duplicidad CURP"],
        "phases": [
            {
                "phase_id": "state",
                "name": "Fase estatal",
                "start_date": "2027-03-01",
                "end_date": "2027-05-15",
                "activities": [
                    {
                        "activity_id": "uniforms",
                        "name": "Entrega de uniformes",
                        "owner": "Operaciones",
                        "due_date": "2027-04-01",
                    }
                ],
            }
        ],
    }


def test_owner_variable_query_resolves_supported_live_team_variable() -> None:
    live_report = _live_report()

    report = build_owner_variable_query_report(
        question="Cuantos equipos reales participantes tiene CDMX?",
        live_reports=[live_report],
    )

    assert report.audit_language == OWNER_VARIABLE_QUERY_ONLY
    assert report.execution_status == "not_executed"
    assert report.writes_attempted == 0
    assert report.side_effects_detected == 0
    assert report.status == OWNER_VARIABLE_SUPPORTED
    assert report.candidates[0].field == "real_teams"
    assert report.resolutions[0].status == OWNER_VARIABLE_SUPPORTED
    assert report.resolutions[0].value[0]["teams_count_total"] == 8
    assert report.safety_summary["fallback_to_guessing"] is False



def test_owner_variable_query_marks_conflicting_live_values() -> None:
    first = _live_report()
    conflicting_field = OwnerPackLiveFieldSnapshot(
        field="real_teams",
        label="Equipos reales participantes",
        section_id="operations",
        evidence_type="team",
        status=OWNER_PACK_LIVE_SUPPORTED,
        value=[{"category": "Sub 15", "teams_count_total": 9}],
        source_paths=["manual.conflict"],
        source_files=["samchat_local_tournament_db"],
        reason="supported_by_local_samchat_source",
    )
    conflicting_surface = OwnerPackLiveSurfaceSnapshot(
        surface_id="entity_folder",
        label="Carpeta por entidad",
        target={"tournament_name": "Copa Local", "entity_name": "CDMX"},
        workspace_root="samchat_local_tournament_db",
        fields=[conflicting_field],
        supported_field_count=1,
        missing_field_count=0,
        audit_language=OWNER_PACK_LIVE_SNAPSHOT_ONLY,
    )
    second = OwnerPackLiveSnapshotReport(
        snapshot_id="owner_pack_live_evidence_v2_conflict",
        headline="Evidencia viva local para Owner Pack",
        summary="Conflicting fixture",
        surfaces=[conflicting_surface],
        supported_field_count=1,
        missing_field_count=0,
        audit_language=OWNER_PACK_LIVE_SNAPSHOT_ONLY,
    )

    report = build_owner_variable_query_report(
        question="Cuantos equipos reales participantes tiene CDMX?",
        live_reports=[first, second],
    )

    assert report.status == OWNER_VARIABLE_CONFLICT
    assert report.resolutions[0].status == OWNER_VARIABLE_CONFLICT
    assert len(report.resolutions[0].conflict_values) == 2
    assert report.next_questions


def test_owner_variable_query_uses_soul_wizard_for_phase_dates() -> None:
    report = build_owner_variable_query_report(
        question="Cuales son las fechas de inauguracion y finales?",
        soul_wizard_payload=_wizard_payload(),
    )

    assert report.status == OWNER_VARIABLE_SUPPORTED
    assert report.resolutions[0].field == "opening_and_final_dates"
    assert report.resolutions[0].status == OWNER_VARIABLE_SUPPORTED
    assert report.resolutions[0].value["phase_count"] == 1
    assert report.resolutions[0].value["phases"][0]["start_date"] == "2027-03-01"
    assert report.resolutions[0].evidence


def test_owner_variable_query_marks_known_variable_missing_without_evidence() -> None:
    report = build_owner_variable_query_report(question="Cuanto se ha transferido de ayuda al operador?")

    assert report.status == OWNER_VARIABLE_MISSING
    assert report.candidates[0].field == "operator_payments"
    assert report.resolutions[0].status == OWNER_VARIABLE_MISSING
    assert report.resolutions[0].canonical_sources
    assert report.next_questions


def test_owner_variable_query_does_not_guess_unmapped_question() -> None:
    report = build_owner_variable_query_report(question="Le fue bien el ambiente?")

    assert report.status == OWNER_VARIABLE_UNMAPPED
    assert report.resolutions == []
    assert "no genero dato inventado" in report.answer
    assert report.safety_summary["unmapped_questions_generate_claims"] is False


def test_owner_variable_query_marks_incomplete_wizard_as_partial() -> None:
    report = build_owner_variable_query_report(
        question="Cuales son las fechas de inauguracion y finales?",
        soul_wizard_payload={"tournament_name": "Copa incompleta"},
    )

    assert report.status == OWNER_VARIABLE_PARTIAL
    assert report.resolutions[0].status == OWNER_VARIABLE_PARTIAL
    assert "edition_year" in report.resolutions[0].missing_reason
