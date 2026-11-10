import base64
import io
from types import SimpleNamespace

import pytest
from backend_harness import IDS, service
from backend_web_helpers import png
from PIL import Image


def test_image_type_dimensions_and_metadata():
    for fmt in ["GIF", "BMP"]:
        stream = io.BytesIO()
        Image.new("RGB", (10, 10)).save(stream, fmt)
        with pytest.raises(service.BatisenalError, match="IMAGE_TYPE_INVALID"):
            service.normalize_image(
                base64.b64encode(stream.getvalue()).decode(), service.Policy()
            )
    with pytest.raises(service.BatisenalError, match="IMAGE_DIMENSIONS_INVALID"):
        service.normalize_image(png(), service.Policy(max_pixels=20))
    assert b"PRIVATE" not in service.normalize_image(png(), service.Policy())
    stream = io.BytesIO()
    Image.new("RGBA", (10, 10), (255, 0, 0, 50)).save(stream, "PNG")
    assert service.normalize_image(
        base64.b64encode(stream.getvalue()).decode(), service.Policy()
    )


def test_permission_intersection_and_configuration(monkeypatch):
    employee = SimpleNamespace(id=IDS["alice"], activo=True)
    for enabled in [False, True]:
        tokens = ["batisenal:acme:demo:support"]
        if enabled:
            assert service.principal_from_tokens(
                employee, tokens, can_request=False, can_support=True
            ).permits("acme", "demo", "support")
        else:
            with pytest.raises(service.BatisenalError):
                service.principal_from_tokens(
                    employee, tokens, can_request=True, can_support=False
                )
    employee.impersonator_empleado_id = IDS["bob"]
    with pytest.raises(service.BatisenalError):
        service.principal_from_tokens(
            employee, tokens, can_request=True, can_support=True
        )
    monkeypatch.setenv("BATISENAL_RETENTION_DAYS", "0")
    with pytest.raises(service.BatisenalError):
        service.Policy.environment()
    monkeypatch.delenv("BATISENAL_RETENTION_DAYS", raising=False)
    assert service.Policy.environment().retention_days == 90


def test_image_post_decode_and_post_normalization_limits():
    import random

    source = Image.frombytes("RGB", (100, 100), random.Random(0).randbytes(30000))
    stream = io.BytesIO()
    source.save(stream, "JPEG", quality=1)
    encoded = base64.b64encode(stream.getvalue()).decode()
    with pytest.raises(service.BatisenalError, match="IMAGE_TOO_LARGE"):
        service.normalize_image(
            encoded, service.Policy(max_image_bytes=len(stream.getvalue()))
        )
    with pytest.raises(service.BatisenalError, match="IMAGE_TOO_LARGE"):
        service.normalize_image(
            base64.b64encode(b"abc").decode(), service.Policy(max_image_bytes=2)
        )
    assert service.clean_text("x\x00 token=secret") == "x token=[OCULTO]"
    assert (
        service.Principal(IDS["alice"], frozenset()).permits([], "demo", "request")
        is False
    )


def test_policy_bad_configuration_and_no_startup_schema(monkeypatch):
    from devnous.gastos.models import Base

    assert service.cases.name not in Base.metadata.tables
    assert service.events.name not in Base.metadata.tables
    monkeypatch.setenv("BATISENAL_MAX_PIXELS", "invalid")
    with pytest.raises(service.BatisenalError, match="CONFIGURATION_INVALID"):
        service.Policy.environment()
    assert "SYNTHETIC_SECRET" not in service.clean_text(
        "Authorization: Bearer SYNTHETIC_SECRET"
    )


@pytest.mark.parametrize(
    "change",
    [
        None,
        {},
        {"extra": "field"},
        {"observation": ""},
        {"source_ids": "no"},
        {"source_ids": ["x", "x"]},
        {"source_ids": [1]},
        {"source_ids": ["absent"]},
    ],
)
def test_private_preview_rejects_unbound_or_incomplete_claims(change):
    from devnous.gastos.services import batisenal_agent_service as drafts

    payload = dict(
        observation="An observation",
        inference="",
        next_step="A proposal",
        verification="Human review",
        missing_evidence="",
        source_ids=["x"],
    )
    candidate = change if change is None or change == {} else {**payload, **change}
    with pytest.raises(service.BatisenalError):
        drafts.response_preview(
            candidate, [dict(id="x", path="fixed", sha256="a" * 64)]
        )


def test_private_preview_separates_unknown_evidence_and_verification():
    from devnous.gastos.services import batisenal_agent_service as drafts

    payload = dict(
        observation="An observation",
        inference="",
        next_step="A proposal",
        verification="Human review",
        missing_evidence="",
        source_ids=["x"],
    )
    source = dict(id="x", path="fixed", sha256="a" * 64)
    assert (
        drafts.response_preview(payload, [source])["attention"]
        == "awaiting_verification"
    )
    assert (
        drafts.response_preview({**payload, "source_ids": []}, [source])["attention"]
        == "escalated"
    )
