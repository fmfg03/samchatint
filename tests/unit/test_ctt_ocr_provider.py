import hashlib
from pathlib import Path

import pytest

from devnous.tournaments.core.ctt_ocr_provider import (
    CttFieldCrop,
    CttOcrCoordinator,
    CttOcrMode,
    CttProviderFieldCache,
    CttProviderRawField,
    CttValidationDecision,
    ctt_ocr_mode,
)


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _crop(number: int, field: str = "given_names") -> CttFieldCrop:
    payload = b"\xff\xd8" + bytes([number]) * 20
    return CttFieldCrop(
        crop_id=f"p1:slot-{number}:{field}",
        document_sha256=_digest(b"document"),
        normalized_page_sha256=_digest(b"normalized-page"),
        crop_sha256=_digest(payload),
        field_name=field,
        source_page=1,
        source_region=f"slot-{number}:{field}",
        slot=number,
        image_bytes=payload,
    )


class _Provider:
    pipeline_version = "test.pipeline.v1"
    revision_pinned = True

    def __init__(self, name, values, revision="revision-1"):
        self.name = name
        self.values = values
        self.model_revision = revision
        self.calls = []

    async def transcribe(self, crops):
        self.calls.append(tuple(crop.crop_id for crop in crops))
        return {
            crop.crop_id: CttProviderRawField(
                crop_id=crop.crop_id,
                crop_sha256=crop.crop_sha256,
                raw_text=self.values.get(crop.crop_id),
                candidates=[],
            )
            for crop in crops
        }


def _validator(crop, observation):
    value = (observation.raw_text or "").strip()
    accepted = bool(value) and value != "???"
    return CttValidationDecision(
        normalized_value=value.upper() or None,
        validation_codes=[] if accepted else ["INVALID_OR_MISSING"],
        accepted=accepted,
    )


def _coordinator(tmp_path: Path, openai, chandra):
    return CttOcrCoordinator(
        openai=openai,
        chandra=chandra,
        cache=CttProviderFieldCache(tmp_path),
        validator=_validator,
    )


def test_mode_parser_fails_closed_to_openai():
    assert ctt_ocr_mode("chandra_shadow") == CttOcrMode.CHANDRA_SHADOW
    assert ctt_ocr_mode("invented") == CttOcrMode.OPENAI


def test_crop_hash_must_bind_exact_bytes():
    crop = _crop(1)
    with pytest.raises(ValueError, match="bind"):
        CttFieldCrop(**{**crop.__dict__, "crop_sha256": _digest(b"different")})


def test_context_hash_must_bind_exact_context_bytes():
    crop = _crop(1)
    with pytest.raises(ValueError, match="context_sha256"):
        CttFieldCrop(
            **{
                **crop.__dict__,
                "context_image_bytes": b"context",
                "context_sha256": _digest(b"different"),
            }
        )


def test_context_hash_is_part_of_cache_identity(tmp_path):
    crop = _crop(1)
    provider = _Provider("chandra", {})
    coordinator = _coordinator(tmp_path, provider, provider)
    first = CttFieldCrop(
        **{
            **crop.__dict__,
            "context_image_bytes": b"context-one",
            "context_sha256": _digest(b"context-one"),
        }
    )
    second = CttFieldCrop(
        **{
            **crop.__dict__,
            "context_image_bytes": b"context-two",
            "context_sha256": _digest(b"context-two"),
        }
    )
    first_identity = coordinator._identity(first, provider, CttOcrMode.CHANDRA_SHADOW)
    second_identity = coordinator._identity(second, provider, CttOcrMode.CHANDRA_SHADOW)

    assert first_identity.cache_key() != second_identity.cache_key()


@pytest.mark.asyncio
async def test_openai_mode_returns_only_openai_fields(tmp_path):
    crop = _crop(1)
    openai = _Provider("openai", {crop.crop_id: "Sofía"})
    chandra = _Provider("chandra", {crop.crop_id: "Otra"})

    result = await _coordinator(tmp_path, openai, chandra).extract(
        [crop], mode=CttOcrMode.OPENAI
    )

    assert result.fields[crop.crop_id].normalized_value == "SOFÍA"
    assert result.fields[crop.crop_id].provider == "openai"
    assert not chandra.calls
    assert result.shadow == []


@pytest.mark.asyncio
async def test_shadow_output_is_openai_and_comparison_contains_no_text(tmp_path):
    crop = _crop(4)
    openai = _Provider("openai", {crop.crop_id: "Rodríguez"})
    chandra = _Provider("chandra", {crop.crop_id: "Rodriguez"})

    result = await _coordinator(tmp_path, openai, chandra).extract(
        [crop], mode=CttOcrMode.CHANDRA_SHADOW
    )

    assert result.fields[crop.crop_id].raw_text == "Rodríguez"
    assert result.fields[crop.crop_id].provider == "openai"
    assert result.shadow[0].exact_match is False
    shadow_payload = result.shadow[0].model_dump_json()
    assert "Rodríguez" not in shadow_payload
    assert "Rodriguez" not in shadow_payload


@pytest.mark.asyncio
async def test_primary_fallback_receives_only_rejected_field_crops(tmp_path):
    crops = [_crop(1), _crop(2), _crop(3)]
    chandra = _Provider(
        "chandra",
        {
            crops[0].crop_id: "Ana",
            crops[1].crop_id: None,
            crops[2].crop_id: "???",
        },
    )
    openai = _Provider(
        "openai",
        {
            crops[0].crop_id: "SHOULD NOT BE READ",
            crops[1].crop_id: "Beatriz",
            crops[2].crop_id: "Carla",
        },
    )

    result = await _coordinator(tmp_path, openai, chandra).extract(
        crops, mode=CttOcrMode.CHANDRA_PRIMARY
    )

    assert openai.calls == [tuple(crop.crop_id for crop in crops[1:])]
    assert result.fields[crops[0].crop_id].provider == "chandra"
    assert result.fields[crops[1].crop_id].normalized_value == "BEATRIZ"
    assert result.fields[crops[1].crop_id].requires_review is False
    assert result.fields[crops[2].crop_id].requires_review is True
    assert "PROVIDER_DISAGREEMENT" in result.fields[crops[2].crop_id].validation_codes


@pytest.mark.asyncio
async def test_same_cache_identity_performs_no_new_inference(tmp_path):
    crop = _crop(1)
    openai = _Provider("openai", {crop.crop_id: "Ana"})
    chandra = _Provider("chandra", {crop.crop_id: "Ana"})
    coordinator = _coordinator(tmp_path, openai, chandra)

    first = await coordinator.extract([crop], mode=CttOcrMode.CHANDRA_PRIMARY)
    second = await coordinator.extract([crop], mode=CttOcrMode.CHANDRA_PRIMARY)

    assert first.fields == second.fields
    assert chandra.calls == [(crop.crop_id,)]
    assert openai.calls == []
    assert second.provider_calls == {"chandra": 0, "openai": 0}
    assert second.cache_hits == 1


@pytest.mark.asyncio
async def test_cache_identity_changes_with_page_provider_revision_and_pipeline(
    tmp_path,
):
    crop = _crop(1)
    openai = _Provider("openai", {crop.crop_id: "Ana"})
    chandra = _Provider("chandra", {crop.crop_id: "Ana"}, revision="r1")
    await _coordinator(tmp_path, openai, chandra).extract(
        [crop], mode=CttOcrMode.CHANDRA_PRIMARY
    )
    chandra.model_revision = "r2"
    await _coordinator(tmp_path, openai, chandra).extract(
        [crop], mode=CttOcrMode.CHANDRA_PRIMARY
    )
    assert len(chandra.calls) == 2


@pytest.mark.asyncio
async def test_unpinned_provider_is_allowed_in_shadow_but_refused_in_primary(tmp_path):
    crop = _crop(1)
    openai = _Provider("openai", {crop.crop_id: "Ana"})
    chandra = _Provider("chandra_datalab", {crop.crop_id: "Ana"})
    chandra.revision_pinned = False
    coordinator = _coordinator(tmp_path, openai, chandra)

    shadow = await coordinator.extract([crop], mode=CttOcrMode.CHANDRA_SHADOW)
    assert shadow.fields[crop.crop_id].provider == "openai"
    with pytest.raises(RuntimeError, match="pinned"):
        await coordinator.extract([crop], mode=CttOcrMode.CHANDRA_PRIMARY)


@pytest.mark.asyncio
async def test_provider_cannot_change_crop_identity(tmp_path):
    crop = _crop(1)

    class _Hostile(_Provider):
        async def transcribe(self, crops):
            return {
                crop.crop_id: CttProviderRawField(
                    crop_id=crop.crop_id,
                    crop_sha256=_digest(b"other"),
                    raw_text="Ana",
                    candidates=[],
                )
                for crop in crops
            }

    openai = _Provider("openai", {crop.crop_id: "Ana"})
    hostile = _Hostile("chandra", {crop.crop_id: "Ana"})
    with pytest.raises(RuntimeError, match="immutable"):
        await _coordinator(tmp_path, openai, hostile).extract(
            [crop], mode=CttOcrMode.CHANDRA_PRIMARY
        )
