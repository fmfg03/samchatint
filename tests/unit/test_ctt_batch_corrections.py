import copy

import pytest

from devnous.copa_telmex.staff_batch_corrections import apply_proposals, editable_fields
from devnous.copa_telmex.staff_batch_store import BatchDenied


@pytest.fixture
def pending():
    field = "players.1.birth_date"
    analysis = {
        "auto_ready": False,
        "issues": [{"code": "READER_DISAGREEMENT"}],
        "players": [
            {
                "presence": "PRESENT",
                "birth_date": None,
                "source_page_index": 1,
                "boxes": {"birth_date": {"x": 1}},
            }
        ],
        "staff": [],
        "verification_input": {
            "expected_fields": [field],
            "required_fields": [field],
            "proposed_fields": {field: None},
            "readings": [
                {
                    "method": "first",
                    "input_hash": "a" * 64,
                    "fields": {field: "bad date"},
                },
                {"method": "second", "input_hash": "b" * 64, "fields": {field: None}},
            ],
        },
    }
    records = [
        {
            "principal_id": "actor",
            "source_pages": ["exact"],
            "edits": [{"field_id": field, "value": "2000-01-01"}],
        }
    ]
    return analysis, records


def test_partial_correction_preserves_raw_readings_and_pending_provenance(pending):
    analysis, records = pending
    original = copy.deepcopy(analysis)
    result = apply_proposals(
        analysis, records=records, principal_id="actor", source_pages=["exact"]
    )
    assert analysis == original
    assert result["players"][0]["birth_date"] == "2000-01-01"
    assert (
        result["verification_input"]["readings"]
        == original["verification_input"]["readings"]
    )
    assert result["human_edits"] == records
    assert "verified_by_human" not in result["players"][0]


@pytest.mark.parametrize("fault", ["actor", "source", "field", "photo", "geometry"])
def test_correction_cannot_widen_its_source_or_resolve_unreviewed_failures(
    pending, fault
):
    analysis, records = pending
    if fault == "actor":
        records[0]["principal_id"] = "another"
    elif fault == "source":
        records[0]["source_pages"] = ["another"]
    elif fault == "field":
        records[0]["edits"][0]["field_id"] = "players.1.photo"
    elif fault == "photo":
        analysis["issues"] = [{"code": "PHOTO_UNCERTAIN"}]
    else:
        analysis["players"][0]["boxes"] = {}
    with pytest.raises(BatchDenied):
        apply_proposals(
            analysis, records=records, principal_id="actor", source_pages=["exact"]
        )


def test_only_source_backed_semantic_fields_are_editable(pending):
    assert [f["field_id"] for f in editable_fields(pending[0])] == [
        "players.1.birth_date"
    ]
