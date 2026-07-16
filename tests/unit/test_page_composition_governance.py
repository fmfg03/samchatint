from types import SimpleNamespace
from uuid import uuid4

from devnous.copa_telmex.draft_versioning import build_successor_values
from devnous.copa_telmex.models import RegistrationReviewDraft
from devnous.copa_telmex.page_composition_governance import (
    build_gate_request,
    build_page_append_attempt,
    existing_page_manifest,
    proposed_page_manifest,
    sha256_binding,
    staged_page_manifest,
)


def extraction(team, players):
    return {
        "team": {"name": team, "category": "Libre"},
        "manager": {},
        "players": [
            {"name": name, "birth_date": birth, "curp": curp}
            for name, birth, curp in players
        ],
        "overall_confidence": 0.9,
    }


def draft():
    base_extraction = extraction(
        "Academicos", [("Ana Uno", "2000-01-01", "CURP0001")]
    )
    values = build_successor_values(
        None,
        ocr_raw={"pages": [{"page_index": 1}]},
        extraction=base_extraction,
        review_edits=base_extraction,
        validation={},
        layout_regions={"pages": {"1": []}, "player_page_map": {"1": 1}},
        overall_confidence=0.9,
        needs_review=False,
    )
    return RegistrationReviewDraft(
        id=uuid4(),
        session_id=uuid4(),
        draft_version=1,
        content_hash=values.pop("content_hash"),
        mutation_type="ocr_initial",
        mutation_operation_id="initial-operation",
        mutation_decision_id="sha256:" + "1" * 64,
        mutation_receipt_id="initial-receipt",
        **values,
    )


def test_manifest_preserves_existing_page_and_binds_new_page_to_one_base():
    base = draft()
    asset = SimpleNamespace(
        id=uuid4(),
        session_id=base.session_id,
        page_index=1,
        image_path="/tmp/page-1.jpg",
        sha256="a" * 64,
        width=1600,
        height=2200,
        source_base_draft_id=base.id,
        admitted_draft_id=base.id,
        source_base_content_hash=base.content_hash,
        source_ocr_run_ref=f"initial:{base.id}",
        admission_operation_id="initial-operation",
    )
    existing = existing_page_manifest(
        session_id=base.session_id, base_draft=base, assets=[asset]
    )
    request_id = uuid4()
    run_id, operation_id, staged, appended = staged_page_manifest(
        session_id=base.session_id,
        base_draft=base,
        page_append_request_id=request_id,
        stored_assets=[
            {
                "page_index": 2,
                "image_path": "/tmp/page-2.jpg",
                "sha256": "b" * 64,
                "width": 1600,
                "height": 2200,
            }
        ],
    )
    proposed = proposed_page_manifest(existing, appended)

    assert proposed[0] == existing[0]
    assert proposed[1]["source_base_draft_id"] == str(base.id)
    assert proposed[1]["source_base_content_hash"] == base.content_hash
    assert proposed[1]["source_ocr_run_ref"] == str(run_id)
    assert proposed[1]["admission_operation_id"] == operation_id
    assert staged[0]["id"] == proposed[1]["asset_id"]


def test_attempt_and_gate_request_bind_exact_roster_and_page_manifest():
    base = draft()
    incoming = extraction(
        "Académicos", [("Beto Dos", "2001-02-02", "CURP0002")]
    )
    proposed_extraction = extraction(
        "Academicos",
        [
            ("Ana Uno", "2000-01-01", "CURP0001"),
            ("Beto Dos", "2001-02-02", "CURP0002"),
        ],
    )
    existing = [
        {
            "asset_id": "asset-1",
            "session_id": str(base.session_id),
            "page_index": 1,
            "image_hash": "sha256:" + "a" * 64,
            "width": 1600,
            "height": 2200,
            "source_base_draft_id": str(base.id),
            "source_base_content_hash": base.content_hash,
            "source_ocr_run_ref": "initial-run",
            "admission_operation_id": "initial-operation",
        }
    ]
    run_id, operation_id, staged, appended = staged_page_manifest(
        session_id=base.session_id,
        base_draft=base,
        page_append_request_id=uuid4(),
        stored_assets=[
            {
                "page_index": 2,
                "image_path": "/tmp/page-2.jpg",
                "sha256": "b" * 64,
                "width": 1600,
                "height": 2200,
            }
        ],
    )
    manifest = existing + appended
    proposed_values = build_successor_values(
        base,
        ocr_raw={"pages": [{"page_index": 1}, {"page_index": 2}]},
        extraction=proposed_extraction,
        review_edits=proposed_extraction,
        validation={},
        layout_regions={
            "pages": {"1": [], "2": []},
            "player_page_map": {"1": 1, "2": 2},
        },
        page_manifest_hash=sha256_binding(manifest),
        overall_confidence=0.9,
        needs_review=False,
    )
    attempt = build_page_append_attempt(
        session_id=base.session_id,
        page_append_request_id=uuid4(),
        base_draft=base,
        provider="openai",
        prompt_config_hash="sha256:" + "c" * 64,
        append_ocr_run_id=run_id,
        operation_id=operation_id,
        existing_manifest=existing,
        appended_manifest=appended,
        staged_assets=staged,
        incoming_extraction=incoming,
        incoming_ocr_raw={},
        incoming_layout_regions={},
        proposed_values=proposed_values,
    )
    gate_request = build_gate_request(
        tenant_id="samchat-prod",
        tournament_slug="copa_telmex",
        attempt=attempt,
        current_draft=base,
        base_extraction=base.extraction,
        successor_draft_id=uuid4(),
    )

    assert gate_request["proposed_page_manifest"] == manifest
    assert gate_request["base_player_page_assignments"] == [1]
    assert gate_request["proposed_player_page_assignments"] == [1, 2]
    assert gate_request["incoming_team_identity"]["name"] == "academicos"
    assert gate_request["incoming_player_identities"][0]["curp"] == "CURP0002"


def test_page_manifest_changes_draft_content_hash():
    base = draft()
    left = build_successor_values(
        base, page_manifest_hash="sha256:" + "a" * 64
    )
    right = build_successor_values(
        base, page_manifest_hash="sha256:" + "b" * 64
    )
    assert left["content_hash"] != right["content_hash"]

