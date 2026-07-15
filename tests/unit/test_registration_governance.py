import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from devnous.copa_telmex.registration_governance import (
    RegistrationGovernanceClient,
    RegistrationGovernanceDenied,
    build_preauthorization_request,
)


def test_request_derives_reproducible_geometry_without_leaking_into_bindings(tmp_path):
    image_path = tmp_path / "page.png"
    Image.new("RGB", (400, 300), "white").save(image_path)
    digest = __import__("hashlib").sha256(image_path.read_bytes()).hexdigest()
    asset = SimpleNamespace(page_index=1, image_path=str(image_path), sha256=digest)
    request = build_preauthorization_request(
        tenant_id="samchat-prod",
        draft_id="draft-1",
        draft_version=3,
        team_id="team-1",
        tournament_slug="liga-1",
        original_extraction={
            "players": [{"name": "Nombre OCR", "birth_date": "2010-01-01"}]
        },
        proposed_extraction={
            "players": [{"name": "Nombre OCR", "birth_date": "2010-01-01"}]
        },
        assets=[asset],
        layout_regions={
            "player_page_map": {"1": 1},
            "pages": {
                "1": [
                    {
                        "player_index": 1,
                        "field_key": "name",
                        "x": 10,
                        "y": 20,
                        "width": 200,
                        "height": 80,
                    },
                    {
                        "player_index": 1,
                        "field_key": "birth_date",
                        "x": 10,
                        "y": 110,
                        "width": 140,
                        "height": 40,
                    },
                ]
            },
        },
        incident_policy={"blocking": []},
    )
    assert len(request["field_candidates"]) == 2
    assert request["field_candidates"][0]["geometry"]["effective_pixels"] == 16000
    assert request["source_page_bindings"] == [f"sha256:{digest}"]


def test_missing_overlay_is_explicit_missing_geometry(tmp_path):
    image_path = tmp_path / "page.png"
    Image.new("RGB", (20, 20), "white").save(image_path)
    digest = __import__("hashlib").sha256(image_path.read_bytes()).hexdigest()
    request = build_preauthorization_request(
        tenant_id="t",
        draft_id="d",
        draft_version=1,
        team_id="team",
        tournament_slug="cup",
        original_extraction={"players": []},
        proposed_extraction={"players": [{"name": "A B"}]},
        assets=[
            SimpleNamespace(page_index=1, image_path=str(image_path), sha256=digest)
        ],
        layout_regions={"player_page_map": {"1": 1}, "pages": {}},
        incident_policy={},
    )
    assert request["field_candidates"][0]["geometry"] is None


def test_client_is_fail_closed_when_url_missing(monkeypatch):
    monkeypatch.delenv("ZAUBERN_REGISTRATION_GATE_URL", raising=False)
    with pytest.raises(RegistrationGovernanceDenied) as exc:
        RegistrationGovernanceClient.from_environment()
    assert exc.value.reason_code == "EVIDENCE_WRITE_FAILED_FAIL_CLOSED"
