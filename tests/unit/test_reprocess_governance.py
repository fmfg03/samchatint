from uuid import uuid4

from devnous.copa_telmex.draft_versioning import build_successor_values
from devnous.copa_telmex.models import RegistrationReviewDraft
from devnous.copa_telmex.reprocess_governance import (
    build_field_diffs,
    build_ocr_run,
)


def extraction(names):
    return {
        "team": {"name": "Academicos"},
        "manager": {},
        "players": [
            {"name": name, "birth_date": None, "curp": None}
            for name in names
        ],
        "overall_confidence": 0.99,
    }


def assets():
    return [
        {
            "page_index": 1,
            "sha256": "1" * 64,
            "width": 1600,
            "height": 2200,
        },
        {
            "page_index": 2,
            "sha256": "2" * 64,
            "width": 1600,
            "height": 2200,
        },
    ]


def layout():
    return {
        "player_page_map": {
            str(slot): 1 if slot <= 4 else 2 for slot in range(1, 9)
        },
        "pages": {},
    }


def base_draft(names):
    current_extraction = extraction(names)
    values = build_successor_values(
        None,
        ocr_raw={"pages": []},
        extraction=current_extraction,
        review_edits=current_extraction,
        validation={},
        layout_regions=layout(),
        overall_confidence=0.70,
        needs_review=True,
    )
    return RegistrationReviewDraft(
        id=uuid4(),
        session_id=uuid4(),
        draft_version=1,
        content_hash=values.pop("content_hash"),
        mutation_type="ocr_initial",
        mutation_operation_id="initial",
        mutation_decision_id="sha256:" + "a" * 64,
        mutation_receipt_id="receipt-initial",
        **values,
    )


def test_normalization_only_is_not_material_but_name_replacement_is():
    diffs = build_field_diffs(
        extraction(["Ramón García", "Nombre Correcto"]),
        extraction(["RAMON GARCIA", "Academicos"]),
        assets=assets(),
        previous_layout=layout(),
        new_layout=layout(),
    )
    by_path = {item["field_path"]: item for item in diffs}
    assert (
        by_path["players.1.name"]["classification"]
        == "NORMALIZATION_ONLY_CHANGE"
    )
    assert by_path["players.2.name"]["classification"] == "MATERIAL_CHANGE"


def test_academicos_preserves_both_candidates_and_has_stable_run_identity():
    previous_names = [f"Cedula {slot}" for slot in range(1, 9)]
    proposed_names = [
        "Nombre equivocado 1",
        "Nombre equivocado 2",
        "Nombre equivocado 3",
        "Nombre equivocado 4",
        "Nombre equivocado 5",
        "Cedula 6",
        "Cedula 7",
        "Cedula 8",
    ]
    draft = base_draft(previous_names)
    proposed_extraction = extraction(proposed_names)
    proposed = build_successor_values(
        draft,
        ocr_raw={
            "pages": [
                {
                    "raw": {
                        "provider": "openai",
                        "model": "vision-test",
                        "model_version": "2026-07-16",
                    }
                }
            ]
        },
        extraction=proposed_extraction,
        review_edits=proposed_extraction,
        validation={"needs_review": False},
        layout_regions=layout(),
        overall_confidence=0.99,
        needs_review=False,
    )
    first_run, first_rows, first_public = build_ocr_run(
        tenant_id="samchat-prod",
        session_id=draft.session_id,
        reprocess_request_id=uuid4(),
        base_draft=draft,
        assets=assets(),
        proposed_values=proposed,
        provider="openai",
        prompt_config_hash="sha256:" + "b" * 64,
    )
    retry_request_id = first_run.reprocess_request_id
    second_run, _, _ = build_ocr_run(
        tenant_id="samchat-prod",
        session_id=draft.session_id,
        reprocess_request_id=retry_request_id,
        base_draft=draft,
        assets=assets(),
        proposed_values=proposed,
        provider="openai",
        prompt_config_hash="sha256:" + "b" * 64,
    )

    material_rows = [
        row for row in first_rows if row.classification == "MATERIAL_CHANGE"
    ]
    assert len(material_rows) == 5
    assert material_rows[0].previous_value == "Cedula 1"
    assert material_rows[0].proposed_value == "Nombre equivocado 1"
    assert all("previous_value" not in item for item in first_public)
    assert all("proposed_value" not in item for item in first_public)
    assert first_run.material_change_count == 5
    assert first_run.operation_id == second_run.operation_id
    assert first_run.run_fingerprint == second_run.run_fingerprint
    assert first_run.id != second_run.id
    assert first_run.model_identity["pages"][0]["model"] == "vision-test"

    separate_run, _, _ = build_ocr_run(
        tenant_id="samchat-prod",
        session_id=draft.session_id,
        reprocess_request_id=uuid4(),
        base_draft=draft,
        assets=assets(),
        proposed_values=proposed,
        provider="openai",
        prompt_config_hash="sha256:" + "b" * 64,
    )
    assert separate_run.run_fingerprint == first_run.run_fingerprint
    assert separate_run.operation_id != first_run.operation_id
