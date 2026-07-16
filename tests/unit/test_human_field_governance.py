from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

from devnous.copa_telmex.human_field_governance import (
    approval_rows,
    build_gate_request,
    build_proposal,
    build_resolution_set,
    ensure_roster_entry_ids,
    proposal_id_for,
    sha256_binding,
)


def actor():
    return {
        "user_id": "employee-7",
        "role": "admin",
        "role_assignment_id": "role-assignment-7",
        "authorization_epoch": "auth-epoch-7",
        "authentication_method": "internal_session",
        "authentication_assurance_level": 1,
        "auth_context_id": "auth-context-7",
    }


def test_legacy_roster_ids_are_stable_and_existing_ids_are_preserved():
    session_id = UUID("11111111-1111-4111-8111-111111111111")
    existing_id = UUID("22222222-2222-4222-8222-222222222222")
    extraction = {
        "players": [
            {"name": "Uno"},
            {"name": "Dos", "roster_entry_id": str(existing_id)},
        ]
    }
    first = ensure_roster_entry_ids(extraction, session_id)
    second = ensure_roster_entry_ids(extraction, session_id)
    assert first["players"][0]["roster_entry_id"] == second["players"][0][
        "roster_entry_id"
    ]
    assert first["players"][1]["roster_entry_id"] == str(existing_id)


def test_resolution_set_binds_player_evidence_and_all_blocking_s03_diffs():
    session_id = UUID("11111111-1111-4111-8111-111111111111")
    base = ensure_roster_entry_ids(
        {
            "team": {"name": "Academicos"},
            "players": [
                {
                    "name": "Nombre anterior",
                    "birth_date": "2001-01-01",
                    "curp": "ABCD010101HDFXXX01",
                }
            ],
        },
        session_id,
    )
    proposed = ensure_roster_entry_ids(
        {
            **base,
            "players": [
                {
                    **base["players"][0],
                    "name": "Nombre corregido",
                }
            ],
        },
        session_id,
    )
    diff_id = UUID("33333333-3333-4333-8333-333333333333")
    run_id = UUID("44444444-4444-4444-8444-444444444444")
    decision_id = UUID("55555555-5555-4555-8555-555555555555")
    diff = SimpleNamespace(
        id=diff_id,
        field_path="players.1.name",
        previous_value_present=True,
        previous_value="Nombre anterior",
        proposed_value_present=True,
        proposed_value="Nombre OCR",
        classification="MATERIAL_CHANGE",
        ocr_run_id=run_id,
        ocr_run=SimpleNamespace(
            decision=SimpleNamespace(id=decision_id)
        ),
    )
    asset_id = UUID("66666666-6666-4666-8666-666666666666")
    resolutions, required = build_resolution_set(
        tenant_id="samchat-prod",
        session_id=session_id,
        proposal_id=proposal_id_for(session_id, uuid4()),
        base_extraction=base,
        proposed_extraction=proposed,
        assets=[
            {
                "id": asset_id,
                "page_index": 1,
                "sha256": "a" * 64,
                "image_path": "/missing/evidence.png",
                "width": 1600,
                "height": 2200,
            }
        ],
        layout_regions={
            "player_page_map": {"1": 1},
            "pages": {
                "1": [
                    {
                        "player_index": 1,
                        "field_key": "name",
                        "x": 100,
                        "y": 200,
                        "width": 500,
                        "height": 100,
                    }
                ]
            },
        },
        blocking_diffs=[diff],
        actor=actor(),
        issued_at=datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc),
    )
    assert required == [str(diff_id)]
    assert len(resolutions) == 1
    resolution = resolutions[0]
    roster_entry_id = base["players"][0]["roster_entry_id"]
    assert resolution["field_path"] == f"players.{roster_entry_id}.name"
    assert resolution["resolution_type"] == "ENTER_CORRECTED_VALUE"
    assert resolution["evidence_class"] == "S03_DIFF_RESOLUTION"
    assert resolution["source_page_artifact_id"] == str(asset_id)
    assert resolution["crop_coordinates"] == {
        "x": 100,
        "y": 200,
        "width": 500,
        "height": 100,
    }
    assert resolution["field_diff_id"] == str(diff_id)


def test_proposal_gate_request_and_approval_rows_are_exact_and_hmac_bound(
    monkeypatch,
):
    monkeypatch.setenv(
        "SAMCHAT_HUMAN_FIELD_BINDING_KEY",
        "test-human-field-binding-key-at-least-32-bytes",
    )
    session_id = UUID("11111111-1111-4111-8111-111111111111")
    edit_request_id = UUID("22222222-2222-4222-8222-222222222222")
    base_draft = SimpleNamespace(
        id=UUID("33333333-3333-4333-8333-333333333333"),
        draft_version=7,
        content_hash="sha256:" + "1" * 64,
    )
    resolution = {
        "approval_id": "44444444-4444-4444-8444-444444444444",
        "nonce": "nonce-regs05",
        "roster_entry_id": None,
        "player_slot": None,
        "field_path": "team.category",
        "resolution_type": "ENTER_CORRECTED_VALUE",
        "evidence_class": "ADMINISTRATIVE_METADATA_EDIT",
        "previous_value_present": True,
        "previous_value": "Libre",
        "previous_normalized_value": "libre",
        "proposed_value_present": True,
        "proposed_value": "Juvenil",
        "proposed_normalized_value": "juvenil",
        "ocr_candidate_value_present": False,
        "ocr_candidate_value": None,
        "source_page_artifact_id": None,
        "source_page_hash": None,
        "normalized_page_hash": None,
        "coordinate_frame_hash": None,
        "crop_coordinates": None,
        "crop_hash": None,
        "ocr_run_id": None,
        "reprocess_decision_id": None,
        "field_diff_id": None,
        "classification": None,
        "approver": {
            "principal_id": "employee-7",
            "role": "admin",
            "role_current": True,
            "role_assignment_id": "role-assignment-7",
            "authorization_epoch": "auth-epoch-7",
            "authentication_method": "internal_session",
            "authentication_assurance_level": 1,
            "auth_context_id": "auth-context-7",
        },
        "issued_at": "2026-07-15T12:00:00Z",
        "not_before": "2026-07-15T12:00:00Z",
        "expires_at": "2026-07-15T12:08:00Z",
    }
    proposed_values = {
        "review_edits": {"team": {"name": "Academicos", "category": "Juvenil"}},
        "content_hash": "sha256:" + "2" * 64,
    }
    proposal = build_proposal(
        tenant_id="samchat-prod",
        session_id=session_id,
        edit_request_id=edit_request_id,
        base_draft=base_draft,
        tournament_slug="copa_telmex",
        proposed_successor_draft_id=UUID(
            "55555555-5555-4555-8555-555555555555"
        ),
        proposed_values=proposed_values,
        resolutions=[resolution],
        required_blocking_diff_ids=[],
        actor=actor(),
    )
    rows = approval_rows(proposal)
    assert len(rows) == 1
    assert rows[0].previous_value_binding.startswith("hmac-sha256:")
    assert rows[0].proposed_value_binding.startswith("hmac-sha256:")
    request = build_gate_request(
        tenant_id="samchat-prod",
        proposal=proposal,
        current_draft=base_draft,
        consuming_principal_id="employee-7",
    )
    assert request["field_resolution_set_hash"] == sha256_binding(
        [resolution]
    )
    assert request["expected_current_draft_version"] == 7


def test_no_retired_human_edit_or_canonical_draft_bypass_remains():
    repo = Path(__file__).resolve().parents[2]
    dashboard = (repo / "copa_telmex_dashboard.py").read_text(
        encoding="utf-8"
    )
    assert 'mutation_type="operator_edit"' not in dashboard
    assert 'mutation_type="canonical_fields_adopted"' not in dashboard
    canonical_route = dashboard.split(
        '@app.post("/api/registration-review/{session_id}/canonical-adopt")',
        1,
    )[1].split(
        '@app.post("/api/registration-review/{session_id}/reject")',
        1,
    )[0]
    assert "append_draft_version(" not in canonical_route
