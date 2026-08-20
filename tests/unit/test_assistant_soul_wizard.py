from __future__ import annotations

from samchat.assistant.soul_wizard import (
    EXECUTION_STATUS,
    build_soul_wizard_clone_payload,
    build_soul_wizard_contract,
    build_soul_wizard_draft,
    build_soul_wizard_owner_pack_bridge,
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


def test_soul_wizard_payload_binds_contract_draft_readiness_and_preview() -> None:
    payload = build_soul_wizard_payload(_complete_payload())

    assert payload["contract"]["contract_id"] == "soul_wizard_contract_v1"
    assert payload["draft"]["tournament_name"] == "De la Calle a la Cancha"
    assert payload["readiness"]["status"] == "ready_for_review"
    assert payload["preview"]["preview_version"] == "soul_wizard_preview_v1"
    assert payload["preview"]["mode"] == "manual_draft"
    assert payload["preview"]["activation_allowed"] is False
    assert payload["preview"]["operational_writes_allowed"] is False
    assert {field["status"] for field in payload["preview"]["fields"]} == {"captured"}


def test_soul_wizard_owner_pack_bridge_summarizes_phase_plan_and_is_inert() -> None:
    payload = build_soul_wizard_payload(_complete_payload())
    bridge = build_soul_wizard_owner_pack_bridge(payload)

    assert bridge["bridge_version"] == "soul_wizard_owner_pack_bridge_v1"
    assert bridge["source"] == "assistant.soul_wizard_contract"
    assert bridge["execution_status"] == EXECUTION_STATUS
    assert bridge["operational_writes_allowed"] is False
    assert bridge["writes_attempted"] == 0
    assert bridge["side_effects_detected"] == 0
    assert bridge["status"] == "ready_for_review"
    assert bridge["tournament"]["name"] == "De la Calle a la Cancha"
    assert bridge["phase_count"] == 2
    assert bridge["activity_count"] == 2
    assert bridge["phases"][0]["name"] == "Fase estatal"
    assert bridge["phases"][0]["activities"][0]["evidence_required"] == ["acuse entrega"]
    assert "state_phase_operations" in bridge["owner_pack_support"]["supported_fields"]
    assert "real_teams" in bridge["owner_pack_support"]["unsupported_fields"]
    assert "does_not_create_owner_folder" in bridge["non_claims"]
    assert "does_not_create_tournament" in bridge["non_claims"]


def test_soul_wizard_owner_pack_bridge_surfaces_missing_phase_paths() -> None:
    bridge = build_soul_wizard_owner_pack_bridge({"tournament_name": "Copa incompleta"})

    assert bridge["status"] == "incomplete"
    assert bridge["operational_writes_allowed"] is False
    assert "edition_year" in bridge["missing_paths"]
    assert "phases" in bridge["missing_paths"]
    assert bridge["phase_count"] == 0
    assert bridge["next_action"].startswith("Completar")


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
    assert "Diff de activacion propuesta" not in html
    assert "Abrir SOUL Wizard" not in html


def _source_soul_snapshot() -> dict:
    return {
        "snapshot_hash": "sha256:abc123",
        "tournaments": [{"id": "torneo-2026", "name": "Copa Base"}],
        "summary": {"teams_count": 16},
        "breakdowns": {
            "categories": [{"category": "Sub 15"}, {"category": "Sub 17"}],
            "branches": [{"branch": "Varonil"}, {"branch": "Femenil"}],
            "entities": [
                {"entity_name": "CDMX", "teams_count": 8},
                {"entity_name": "Jalisco", "teams_count": 8},
            ],
        },
        "compliance": {
            "required_documents": ["CURP", "Acta"],
            "eligibility_rules": ["Sin duplicidad CURP"],
        },
        "finance_bridge": {"rules": ["Ayuda operador", "Uniformes"]},
        "phases": [
            {
                "phase_id": "state",
                "name": "Fase estatal",
                "start_date": "2026-03-01",
                "end_date": "2026-05-01",
                "activities": [
                    {
                        "activity_id": "rosters",
                        "name": "Validar rosters",
                        "owner": "Operaciones",
                        "due_date": "2026-04-20",
                    }
                ],
            }
        ],
    }


def test_soul_wizard_clone_from_soul_snapshot_copies_context_and_applies_overrides() -> None:
    payload = build_soul_wizard_clone_payload(
        _source_soul_snapshot(),
        overrides={"tournament_name": "Copa Nueva", "edition_year": 2027},
    )

    draft = payload["draft"]
    clone = payload["clone"]
    assert draft["tournament_name"] == "Copa Nueva"
    assert draft["edition_year"] == 2027
    assert draft["categories"] == ["Sub 15", "Sub 17"]
    assert draft["branches"] == ["Varonil", "Femenil"]
    assert draft["expected_entities"] == ["CDMX", "Jalisco"]
    assert draft["expected_teams"] == 16
    assert draft["required_documents"] == ["CURP", "Acta"]
    assert draft["eligibility_rules"] == ["Sin duplicidad CURP"]
    assert draft["finance_baseline"] == ["Ayuda operador", "Uniformes"]
    assert draft["source_tournament_id"] == "torneo-2026"
    assert draft["source_snapshot_id"] == "sha256:abc123"
    assert draft["phases"][0]["activities"][0]["name"] == "Validar rosters"
    assert payload["readiness"]["status"] == "ready_for_review"
    assert clone["source_bound"] is True
    assert clone["operational_writes_allowed"] is False
    assert clone["execution_status"] == EXECUTION_STATUS
    assert payload["preview"]["mode"] == "clone_diff"
    preview_by_path = {field["path"]: field for field in payload["preview"]["fields"]}
    assert preview_by_path["tournament_name"]["status"] == "overridden"
    assert preview_by_path["categories"]["status"] == "inherited"
    assert payload["preview"]["summary"]["inherited_count"] >= 6
    assert payload["preview"]["requires_human_authority_before_write"] is True


def test_soul_wizard_clone_preview_marks_missing_source_fields() -> None:
    payload = build_soul_wizard_clone_payload(
        {"tournament": {"id": "t-empty", "name": "Torneo vacio"}},
        overrides={"edition_year": 2027},
    )

    preview_by_path = {field["path"]: field for field in payload["preview"]["fields"]}
    assert preview_by_path["edition_year"]["status"] == "overridden"
    assert preview_by_path["phases"]["status"] == "missing"
    assert payload["preview"]["summary"]["blocker_count"] > 0
    assert payload["preview"]["activation_allowed"] is False


def test_soul_wizard_clone_from_operations_matches_builds_phase_skeleton() -> None:
    payload = build_soul_wizard_clone_payload(
        {
            "tournament": {"id": "t-1", "name": "Torneo con juegos"},
            "operations": {
                "matches": [
                    {"phase": "Estatal"},
                    {"phase": "Estatal"},
                    {"phase": "Nacional"},
                ]
            },
        },
        overrides={"edition_year": 2027},
    )

    draft = payload["draft"]
    assert [phase["name"] for phase in draft["phases"]] == ["Estatal", "Nacional"]
    assert draft["phases"][0]["activities"][0]["name"] == "Revisar plan operativo de Estatal"
    assert payload["readiness"]["status"] == "incomplete"
    assert "missing_phase_start_date" in {issue["code"] for issue in payload["readiness"]["issues"]}


def test_soul_wizard_form_clone_parses_source_json_and_overrides_name() -> None:
    import json

    payload = build_soul_wizard_payload_from_form(
        {
            "source_snapshot_json": json.dumps(_source_soul_snapshot()),
            "tournament_name": "Copa desde UI",
            "edition_year": "2028",
        }
    )

    assert payload["clone"]["source_tournament_id"] == "torneo-2026"
    assert payload["draft"]["tournament_name"] == "Copa desde UI"
    assert payload["draft"]["edition_year"] == 2028
    assert payload["draft"]["categories"] == ["Sub 15", "Sub 17"]


def test_soul_wizard_form_clone_invalid_json_is_safe_and_readonly() -> None:
    payload = build_soul_wizard_payload_from_form(
        {
            "source_snapshot_json": "{nope",
            "tournament_name": "Copa Manual",
            "edition_year": "2028",
        }
    )

    assert payload["clone"]["source_bound"] is False
    assert "Invalid source snapshot JSON" in payload["clone"]["error"]
    assert payload["clone"]["operational_writes_allowed"] is False

def test_soul_wizard_admin_renderer_shows_preview_diff_after_review() -> None:
    from types import SimpleNamespace

    from devnous.gastos.routes.admin_routes import _render_soul_wizard_admin_page

    payload = build_soul_wizard_payload(_complete_payload())
    html = _render_soul_wizard_admin_page(
        current_empleado=SimpleNamespace(nombre="Operaciones", rol="admin"),
        csrf_input='<input type="hidden" name="_csrf_token" value="token">',
        payload=payload,
        form_data={"tournament_name": "De la Calle a la Cancha"},
    )

    assert "Diff de activacion propuesta" in html
    assert "Capturado" in html
    assert "bloqueados" in html
    assert "no crea calendario" in html
