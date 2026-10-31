"""Cross-repository synthetic geometry verification; no capture authority."""

import copy
import importlib
import os
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from devnous.copa_telmex.registration_governance import _derive_geometry
from devnous.copa_telmex.staff_capture_geometry import verify_staff_geometry


@pytest.fixture
def gate(monkeypatch):
    source = os.environ.get("CTT_GATE_SOURCE")
    if not source:
        pytest.skip("Set CTT_GATE_SOURCE to isolated gate directory")
    path = Path(source).resolve(strict=True)
    if not path.is_relative_to(Path("/tmp")):
        pytest.fail("Gate source must be isolated under /tmp")
    monkeypatch.syspath_prepend(str(path))
    return importlib.import_module("registration_gate")


def test_gate_verifies_real_crop_binding_and_denies_changed_page(gate, tmp_path):
    image = Image.new("RGB", (400, 500), "white")
    image.paste("red", (10, 20, 210, 100))
    stream = BytesIO()
    image.save(stream, format="PNG")
    page = stream.getvalue()
    path = tmp_path / "page.png"
    path.write_bytes(page)
    asset = SimpleNamespace(image_path=path, sha256=sha256(page).hexdigest())
    geometry = _derive_geometry(asset, {"x": 10, "y": 20, "width": 200, "height": 80})
    assert geometry is not None
    sha, mac = "sha256:" + "a" * 64, "hmac-sha256:" + "b" * 64
    scope = {
        "tenant_id": "tenant",
        "tournament_edition_id": "edition",
        "tournament_slug": "tournament",
        "team_id": "team",
        "session_id": "session",
        "draft_id": "draft",
        "draft_version": 4,
        "staff_entry_binding": mac,
        "slot": 1,
        "role": "director_tecnico",
        "presence": "PRESENT",
        "source_page": 1,
        "source_page_hash": "sha256:" + sha256(page).hexdigest(),
        "evidence_binding": mac,
        "field_decision_hashes": [sha],
    }
    now = datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)
    candidate = {
        "scope": scope,
        "approval_id": "approval",
        "nonce": "nonce",
        "approver": {
            "principal_id": "synthetic",
            "role": "admin",
            "role_assignment_id": "assignment",
            "authorization_epoch": "epoch",
            "authentication_method": "session",
            "authentication_assurance_level": 1,
            "auth_context_id": "context",
        },
        "issued_at": (now - timedelta(minutes=1)).isoformat(),
        "not_before": (now - timedelta(minutes=1)).isoformat(),
        "expires_at": (now + timedelta(minutes=2)).isoformat(),
        "geometry": geometry,
    }
    policy = {
        "workflow_id": "samchat_registration_identity_v2",
        "policy_id": "samchat_registration_identity_policy_v2",
        "effective_sal": "SAL-3",
        "policy_version": "0.0.1",
        "minimum_effective_pixels": 12000,
    }
    policy["policy_hash"] = gate.sha256_binding(policy)

    def evaluate(raw):
        return gate.validate_staff_review_candidate_v2(
            copy.deepcopy(candidate),
            expected_scope=copy.deepcopy(scope),
            identity_key=b"v2-identity-binding-key-at-least-32-bytes",
            verify_geometry=lambda s, g: verify_staff_geometry(
                s, g, raw, max_page_bytes=1_000_000
            ),
            policy_artifact=policy,
            expected_policy_hash=policy["policy_hash"],
            now=now,
        )

    assert evaluate(page)["materialization_authorized"] is False
    with pytest.raises(gate.GateClosedError):
        evaluate(page + b"changed")
