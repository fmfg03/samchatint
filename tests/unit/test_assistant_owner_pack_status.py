from pathlib import Path

from samchat.assistant.owner_needs_eval import parse_owner_needs_eval_set
from samchat.assistant.owner_pack_status import (
    OWNER_PACK_PREPARED,
    OWNER_PACK_PREPARED_WITH_MISSING_EVIDENCE,
    OWNER_PACK_STATUS_ONLY,
    build_owner_pack_status_report,
    owner_pack_status_contains_execution_claim,
)


ROOT = Path(__file__).resolve().parents[2]
EVAL_SET = ROOT / "docs/assistant/rqf-assistant-009e-evaluation-set.md"


def _prompts():
    return parse_owner_needs_eval_set(EVAL_SET.read_text(encoding="utf-8"))


def _surface(report, surface_id: str):
    for surface in report.surfaces:
        if surface.surface_id == surface_id:
            return surface
    raise AssertionError(f"missing surface {surface_id}")


def test_owner_pack_status_groups_all_owner_surfaces() -> None:
    report = build_owner_pack_status_report(_prompts())

    assert report.status_id == "owner_pack_status_v1"
    assert report.prompt_count == 30
    assert report.prepared_surface_count == 4
    assert {surface.surface_id for surface in report.surfaces} == {
        "entity_folder",
        "national_phase_folder",
        "marketing_activation_report",
        "work_plan_or_query",
    }
    assert _surface(report, "entity_folder").label == "Carpetas por entidad"
    assert _surface(report, "national_phase_folder").label == (
        "Carpeta de fase nacional"
    )
    assert _surface(report, "marketing_activation_report").label == (
        "Activacion de marcas"
    )


def test_owner_pack_status_is_prepared_but_honest_about_missing_data() -> None:
    report = build_owner_pack_status_report(_prompts())

    assert "preparados como contratos read-only" in report.summary
    assert report.writes_attempted == 0
    assert report.side_effects_detected == 0
    assert report.execution_status == "not_executed"
    assert report.audit_language == OWNER_PACK_STATUS_ONLY
    assert report.safety_summary["writes_enabled"] is False
    assert report.safety_summary["live_data_required_before_complete_claim"] is True
    assert report.missing_evidence
    assert all(
        surface.status == OWNER_PACK_PREPARED_WITH_MISSING_EVIDENCE
        for surface in report.surfaces
    )
    assert owner_pack_status_contains_execution_claim(report) is False


def test_owner_pack_status_lists_concrete_next_actions_by_surface() -> None:
    report = build_owner_pack_status_report(_prompts())

    assert "torneo" in _surface(report, "entity_folder").next_action
    assert "hoteles" in _surface(report, "national_phase_folder").next_action
    assert "fotografica" in _surface(
        report, "marketing_activation_report"
    ).next_action
    assert "plan/consulta" in _surface(report, "work_plan_or_query").next_action


def test_owner_pack_status_can_show_supported_surface_without_execution() -> None:
    prompts = [prompt for prompt in _prompts() if prompt.prompt_id == "AI-OWNER-002"]
    report = build_owner_pack_status_report(
        prompts,
        available_evidence_by_prompt={
            "AI-OWNER-002": {
                "fields": {
                    "entity_name": "Plantilla canonica",
                    "tournament": "Cualquier torneo",
                    "expected_teams": "Por categoria/genero",
                    "real_teams": "Parcial diario",
                    "players_by_category_age_gender": "Recuento requerido",
                    "round_progression": "Rondas estatales/nacional",
                    "state_phase_operations": "Descripcion requerida",
                    "operator_payments": "Primer apoyo y sucesivos",
                    "equipment_costs": "Uniformes/balones/utileria",
                    "visit_results": "AZ/CL y gastos de visita",
                    "photographic_evidence": "Materialidad",
                }
            }
        },
    )

    assert report.prepared_surface_count == 1
    surface = _surface(report, "entity_folder")
    assert surface.status == OWNER_PACK_PREPARED
    assert surface.missing_evidence == []
    assert "entity_name" in surface.supported_evidence
    assert report.safety_summary["writes_enabled"] is False
    assert owner_pack_status_contains_execution_claim(report) is False
