"""Plans are precise proposals, not evidence of authority or OCR precision."""

import base64
import copy
import hashlib
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4

import pytest
from PIL import Image

from devnous.copa_telmex.draft_versioning import draft_content_hash
from devnous.copa_telmex.staff_batch_authorization import parse_batch_scope
from devnous.copa_telmex.staff_batch_contracts import canonical
from devnous.copa_telmex.staff_batch_plan_builder import (
    BatchPlanBuilder,
    semantic_fields,
)
from devnous.copa_telmex.staff_photo_store import PrivateReviewPhotoStore


@pytest.fixture
def proposal(tmp_path):
    edition, actor, run = uuid4(), uuid4(), uuid4()
    policy = {
        "policy_version": "2.1.0",
        "effective_sal": "SAL-3",
        "field_order": ["full_name", "birth_date", "curp"],
        "minimum_roster_size": 16,
        "maximum_roster_size": 35,
        "minimum_effective_pixels": 12000,
        "required_staff_slots": [1, 2],
        "required_photo_kinds": [],
        "optional_curp_kinds": ["PLAYER", "STAFF"],
        "phase_binding": {
            "phase": "NACIONAL",
            "tenant_id": "synthetic",
            "edition_id": str(edition),
            "manifest_sha256": "b" * 64,
        },
    }
    policy["policy_hash"] = "sha256:" + hashlib.sha256(canonical(policy)).hexdigest()
    photo_root = tmp_path / "photos"
    photo_root.mkdir(mode=0o700)
    builder = BatchPlanBuilder(
        review_key=b"r" * 32,
        policy_artifact=policy,
        expected_policy_hash=policy["policy_hash"],
        photo_store=PrivateReviewPhotoStore(photo_root),
        gate=SimpleNamespace(
            _policy_hash=policy["policy_hash"], _max_page_bytes=4_000_000
        ),
    )
    output = BytesIO()
    Image.new("RGB", (700, 900), "white").save(output, format="PNG")
    raw = output.getvalue()
    pages = [
        {
            "page_index": i,
            "pdf_sha256": "a" * 64,
            "pdf_page": i,
            "asset_id": str(uuid4()),
            "image_sha256": hashlib.sha256(raw).hexdigest(),
            "width": 700,
            "height": 900,
        }
        for i in (1, 2)
    ]
    snapshot = SimpleNamespace(
        page_assets={__import__("uuid").UUID(p["asset_id"]): raw for p in pages}
    )

    def person(n, kind):
        page = 1 if kind == "STAFF" or n <= 8 else 2
        box = {"page_index": page, "x": 20, "y": 20, "width": 200, "height": 120}
        value = {
            "first_name": "Synthetic" + str(n),
            "last_name": kind,
            "birth_date": f"2000-01-{n:02d}",
            "curp": None,
            "presence": "PRESENT",
            "source_page_index": page,
            "boxes": {"full_name": box, "birth_date": box},
            "photo": None,
        }
        if kind == "PLAYER":
            value["slot_ref"] = f"page:{page}:jugador_{n}"
        else:
            value["role"] = "director_tecnico" if n == 1 else "auxiliar"
        return value

    analysis = {
        "auto_ready": True,
        "issues": [],
        "team": {"name": "Synthetic", "category": "UNICA", "gender": "femenil"},
        "players": [person(n, "PLAYER") for n in range(1, 17)],
        "staff": [person(n, "STAFF") for n in (1, 2)],
        "readings": [],
        "source_binding_hash": "c" * 64,
    }
    fields = semantic_fields(analysis)
    analysis["verification_input"] = {
        "expected_fields": list(fields),
        "proposed_fields": fields,
        "required_fields": [k for k, v in fields.items() if v is not None],
        "readings": [
            {"method": "first", "input_hash": "1" * 64, "fields": dict(fields)},
            {"method": "second", "input_hash": "2" * 64, "fields": dict(fields)},
        ],
        "source_binding_hash": analysis["source_binding_hash"],
    }
    context = {
        "run_id": str(run),
        "tenant_id": "synthetic",
        "edition_id": str(edition),
        "principal_id": str(actor),
        "manifest_sha256": "b" * 64,
        "policy_hash": policy["policy_hash"][7:],
        "capability": {"actor_role": "reviewer"},
    }
    source = {
        "document_id": "d" * 64,
        "source_payload_hash": "e" * 64,
        "manifest": {
            "manifest_sha256": "b" * 64,
            "edition": {"roster_slug": "ctt-synthetic"},
        },
        "source_pages": pages,
    }
    return builder, context, source, analysis, snapshot


def test_batch_plan_has_no_invented_human_review_or_probability(proposal):
    result = proposal[0].build_batch(*proposal[1:])
    snapshot = result["snapshot"]
    review = __import__("json").loads(base64.b64decode(snapshot["review_bytes"]))
    validation = __import__("json").loads(
        base64.b64decode(snapshot["validation_bytes"])
    )
    assert len(review["players"]) == 16
    assert all(
        p["verified_by_human"] is False and p["ocr_confidence"] is None
        for p in review["players"]
    )
    assert all(p["curp"] is None for p in review["players"])
    assert any(
        i["incident_type"] == "LOW_CONFIDENCE_FIELD"
        for p in validation["incident_policy"]["player_results"]
        for i in p["incidents"]
    )
    scope = parse_batch_scope(base64.b64decode(snapshot["scope_bytes"]))
    assert "sha256:" + scope.draft_content_hash == draft_content_hash(
        snapshot["raw_snapshot"]
    )
    assert (
        result["payload_hash"]
        == hashlib.sha256(base64.b64decode(snapshot["review_bytes"])).hexdigest()
    )


def test_observation_receipt_cannot_authorize_different_materialized_fields(proposal):
    builder, context, source, analysis, snapshot = proposal
    analysis = copy.deepcopy(analysis)
    analysis["players"][0]["first_name"] = "Unobserved replacement"
    with pytest.raises(ValueError, match="BATCH_FIELD_PLAN_MISMATCH"):
        builder.build_batch(context, source, analysis, snapshot)


def test_empty_physical_placeholder_is_retained_without_a_canonical_player(proposal):
    builder, context, source, analysis, snapshot = proposal
    analysis = copy.deepcopy(analysis)
    extra = copy.deepcopy(analysis["players"][-1])
    extra.update(
        first_name="Synthetic17", birth_date="2000-01-17", slot_ref="page:2:jugador_17"
    )
    analysis["players"].append(extra)
    absent = analysis["players"][2]
    absent.update(
        presence="ABSENT", first_name=None, last_name=None, birth_date=None, curp=None
    )
    fields = semantic_fields(analysis)
    analysis["verification_input"].update(
        expected_fields=list(fields), proposed_fields=fields
    )
    result = builder.build_batch(context, source, analysis, snapshot)
    review = __import__("json").loads(
        base64.b64decode(result["snapshot"]["review_bytes"])
    )
    evidence = __import__("json").loads(
        base64.b64decode(result["snapshot"]["evidence_bytes"])
    )
    assert len(result["snapshot"]["raw_snapshot"]["extraction"]["players"]) == 17
    assert len(review["players"]) == 16
    assert [p["roster_index"] for p in review["players"]] == list(range(1, 17))
    assert [p["source_slot"] for p in review["player_sources"]] == [1, 2] + list(
        range(4, 18)
    )
    assert len(evidence["members"]) == 18
    assert all(p["verified_by_human"] is False for p in review["players"])


def test_absent_placeholder_cannot_discard_known_identity(proposal):
    builder, context, source, analysis, snapshot = proposal
    analysis = copy.deepcopy(analysis)
    extra = copy.deepcopy(analysis["players"][-1])
    extra.update(
        first_name="Synthetic17", birth_date="2000-01-17", slot_ref="page:2:jugador_17"
    )
    analysis["players"].append(extra)
    analysis["players"][2]["presence"] = "ABSENT"
    fields = semantic_fields(analysis)
    analysis["verification_input"].update(
        expected_fields=list(fields), proposed_fields=fields
    )
    with pytest.raises(ValueError, match="BATCH_ABSENCE_HAS_DATA"):
        builder.build_batch(context, source, analysis, snapshot)


@pytest.mark.parametrize("field", ["auto_ready", "source", "birth_date", "geometry"])
def test_unresolved_or_changed_inputs_never_make_an_executable_plan(proposal, field):
    builder, context, source, analysis, snapshot = proposal
    analysis = copy.deepcopy(analysis)
    if field == "auto_ready":
        analysis["auto_ready"] = False
    elif field == "source":
        context = dict(context, manifest_sha256="0" * 64)
    elif field == "birth_date":
        analysis["players"][0]["birth_date"] = "bad"
    else:
        analysis["players"][0]["boxes"]["full_name"]["x"] = 9000
    with pytest.raises((ValueError, KeyError)):
        builder.build_batch(context, source, analysis, snapshot)
