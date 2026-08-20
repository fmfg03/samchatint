from __future__ import annotations

from samchat.assistant.owner_variable_answer import (
    OWNER_VARIABLE_ANSWER_ONLY,
    render_owner_variable_query_answer,
)
from samchat.assistant.owner_variable_query import (
    OWNER_VARIABLE_CONFLICT,
    OWNER_VARIABLE_MISSING,
    OWNER_VARIABLE_SUPPORTED,
    OWNER_VARIABLE_UNMAPPED,
    build_owner_variable_query_report,
)

from samchat.assistant.owner_pack_live_snapshot import (
    OWNER_PACK_LIVE_SNAPSHOT_ONLY,
    OWNER_PACK_LIVE_SUPPORTED,
    OwnerPackLiveFieldSnapshot,
    OwnerPackLiveSnapshotReport,
    OwnerPackLiveSurfaceSnapshot,
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


def test_owner_variable_answer_renders_supported_answer_with_evidence() -> None:
    report = build_owner_variable_query_report(
        question="Cuantos equipos reales participantes tiene CDMX?",
        live_reports=[_live_report()],
    )

    answer = render_owner_variable_query_answer(report)

    assert answer.audit_language == OWNER_VARIABLE_ANSWER_ONLY
    assert answer.execution_status == "not_executed"
    assert answer.writes_attempted == 0
    assert answer.side_effects_detected == 0
    assert answer.status == OWNER_VARIABLE_SUPPORTED
    assert "Si tengo ese dato" in answer.headline
    assert "Equipos reales participantes" in answer.rendered_text
    assert "db.copa_telmex.teams" in answer.rendered_text
    assert answer.safety_summary["renderer_infers_missing_values"] is False


def test_owner_variable_answer_keeps_missing_as_missing() -> None:
    report = build_owner_variable_query_report(
        question="Cuanto se ha transferido de ayuda al operador?"
    )

    answer = render_owner_variable_query_answer(report)

    assert answer.status == OWNER_VARIABLE_MISSING
    assert "no hay evidencia viva suficiente" in answer.short_answer
    assert answer.missing_lines
    assert "falta evidencia" in answer.rendered_text
    assert "No ejecute cambios" in answer.rendered_text


def test_owner_variable_answer_surfaces_conflicts_without_resolving_them() -> None:
    first = _live_report()
    second = _live_report()
    conflicting = second.surfaces[0].fields[0]
    object.__setattr__(conflicting, "value", [{"category": "Sub 15", "teams_count_total": 99}])
    object.__setattr__(conflicting, "source_paths", ["manual.conflict"])

    report = build_owner_variable_query_report(
        question="Cuantos equipos reales participantes tiene CDMX?",
        live_reports=[first, second],
    )

    answer = render_owner_variable_query_answer(report)

    assert report.status == OWNER_VARIABLE_CONFLICT
    assert answer.status == OWNER_VARIABLE_CONFLICT
    assert answer.conflict_lines
    assert "fuentes en conflicto" in answer.rendered_text
    assert "conciliacion humana" in answer.short_answer


def test_owner_variable_answer_does_not_create_claim_for_unmapped_question() -> None:
    report = build_owner_variable_query_report(question="Le fue bien el ambiente?")

    answer = render_owner_variable_query_answer(report)

    assert report.status == OWNER_VARIABLE_UNMAPPED
    assert answer.status == OWNER_VARIABLE_UNMAPPED
    assert answer.detail_lines == []
    assert answer.evidence_lines == []
    assert "Prefiero pedir precision" in answer.rendered_text


def test_owner_variable_answer_renders_wizard_phase_detail() -> None:
    report = build_owner_variable_query_report(
        question="Cuales son las fechas de inauguracion y finales?",
        soul_wizard_payload=_wizard_payload(),
    )

    answer = render_owner_variable_query_answer(report)

    assert answer.status == OWNER_VARIABLE_SUPPORTED
    assert "SOUL Wizard bridge" in answer.rendered_text
    assert "Copa Local" in answer.rendered_text
