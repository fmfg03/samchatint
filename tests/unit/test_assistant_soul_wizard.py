from __future__ import annotations

from samchat.assistant.soul_wizard import (
    EXECUTION_STATUS,
    build_soul_wizard_contract,
    build_soul_wizard_draft,
    build_soul_wizard_payload,
    build_soul_wizard_payload_from_form,
    validate_soul_wizard_draft,
)


def _complete_payload() -> dict:
    return {
        "draft_id": "dcc-2027",
        "tournament_name": "De la Calle a la Cancha",
        "edition_year": 2027,
        "categories": ["Sub 15", "Sub 17"],
        "branches": ["Varonil", "Femenil"],
        "expected_entities": ["CDMX", "Jalisco"],
        "expected_teams": 64,
        "required_documents": ["CURP", "Acta", "Identificacion"],
        "eligibility_rules": ["Edad por categoria", "Sin duplicidad CURP"],
        "finance_baseline": ["Ayuda operador", "Uniformes"],
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
                        "evidence_required": ["acuse entrega"],
                    }
                ],
            },
            {
                "phase_id": "national",
                "name": "Fase nacional",
                "start_date": "2027-11-01",
                "end_date": "2027-11-07",
                "activities": [
                    {
                        "activity_id": "travel",
                        "name": "Viajes ida y vuelta",
                        "owner": "Logistica",
                        "due_date": "2027-10-15",
                    }
                ],
            },
        ],
    }


def test_soul_wizard_complete_draft_is_ready_and_inert() -> None:
    draft = build_soul_wizard_draft(_complete_payload())
    report = validate_soul_wizard_draft(draft)

    assert report.status == "ready_for_review"
    assert report.required_missing_count == 0
    assert report.execution_status == EXECUTION_STATUS
    assert report.operational_writes_allowed is False
    assert report.writes_attempted == 0
    assert report.side_effects_detected == 0
    assert draft.execution_status == EXECUTION_STATUS
    assert draft.operational_writes_allowed is False
    assert len(draft.phases) == 2
    assert draft.phases[0].activities[0].owner == "Operaciones"
    assert draft.to_dict()["draft_hash"]


def test_soul_wizard_missing_phases_and_identity_blocks_review() -> None:
    draft = build_soul_wizard_draft({"categories": ["Sub 17"]})
    report = validate_soul_wizard_draft(draft)
    codes = {issue.code for issue in report.issues}

    assert report.status == "incomplete"
    assert report.required_missing_count >= 3
    assert "missing_tournament_name" in codes
    assert "missing_edition_year" in codes
    assert "missing_phases" in codes


def test_soul_wizard_validates_phase_dates_and_activities() -> None:
    payload = _complete_payload()
    payload["phases"] = [
        {
            "name": "Fase estatal",
            "start_date": "2027-05-10",
            "end_date": "2027-04-01",
            "activities": [],
        }
    ]
    draft = build_soul_wizard_draft(payload)
    report = validate_soul_wizard_draft(draft)
    codes = {issue.code for issue in report.issues}

    assert report.status == "incomplete"
    assert "phase_end_before_start" in codes
    assert "missing_phase_activities" in codes


def test_soul_wizard_activity_owner_is_warning_not_write_blocker() -> None:
    payload = _complete_payload()
    payload["phases"][0]["activities"][0].pop("owner")
    draft = build_soul_wizard_draft(payload)
    report = validate_soul_wizard_draft(draft)
    codes = {issue.code for issue in report.issues}

    assert report.status == "ready_for_review"
    assert report.required_missing_count == 0
    assert report.warnings_count == 1
    assert "missing_activity_owner" in codes


def test_soul_wizard_contract_declares_steps_and_non_claims() -> None:
    contract = build_soul_wizard_contract()
    step_ids = {step["step_id"] for step in contract["steps"]}

    assert contract["read_only"] is True
    assert contract["execution_status"] == EXECUTION_STATUS
    assert contract["operational_writes_allowed"] is False
    assert "phases_dates" in step_ids
    assert "phase_activities" in step_ids
    assert "review_activation" in step_ids
    assert "does_not_create_tournament" in contract["non_claims"]


def test_soul_wizard_payload_binds_contract_draft_and_readiness() -> None:
    payload = build_soul_wizard_payload(_complete_payload())

    assert payload["contract"]["contract_id"] == "soul_wizard_contract_v1"
    assert payload["draft"]["tournament_name"] == "De la Calle a la Cancha"
    assert payload["readiness"]["status"] == "ready_for_review"



def test_soul_wizard_form_payload_parses_phase_activity_lines() -> None:
    payload = build_soul_wizard_payload_from_form(
        {
            "tournament_name": "Copa Test",
            "edition_year": "2027",
            "categories_text": "Sub 15\nSub 17",
            "branches_text": "Varonil\nFemenil",
            "expected_entities_text": "CDMX\nJalisco",
            "expected_teams": "32",
            "required_documents_text": "CURP\nActa",
            "eligibility_rules_text": "Sin duplicidad CURP",
            "phase_1_name": "Inscripcion",
            "phase_1_start_date": "2027-01-10",
            "phase_1_end_date": "2027-02-20",
            "phase_1_activities": "Abrir convocatoria | Operaciones | 2027-01-15\nValidar rosters | Mesa de control | 2027-02-15",
        }
    )

    assert payload["readiness"]["status"] == "ready_for_review"
    assert payload["draft"]["categories"] == ["Sub 15", "Sub 17"]
    assert payload["draft"]["phases"][0]["activities"][1]["owner"] == "Mesa de control"



def test_soul_wizard_admin_renderer_contains_stepper_and_readonly_boundary() -> None:
    from types import SimpleNamespace

    from devnous.gastos.routes.admin_routes import _render_soul_wizard_admin_page

    html = _render_soul_wizard_admin_page(
        current_empleado=SimpleNamespace(nombre="Operaciones", rol="admin"),
        csrf_input='<input type="hidden" name="_csrf_token" value="token">',
        form_data={"tournament_name": "Copa Test"},
    )

    assert "SOUL Wizard" in html
    assert "phase_1_activities" in html
    assert "Revisar borrador" in html
    assert "no crea torneos" in html or "no crea equipos" in html
    assert "Abrir SOUL Wizard" not in html
