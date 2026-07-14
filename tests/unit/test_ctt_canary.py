from __future__ import annotations

from typing import Optional

import pytest
from PIL import Image

from devnous.tournaments.core.ctt_canary import (
    CttCanaryMode,
    CttCanaryPolicy,
    CttCanaryRunner,
    ctt_canary_mode_from_env,
    ctt_document_sha256,
)
from devnous.tournaments.core.ctt_extraction_cache import CttCachedExtractionResult
from devnous.tournaments.core.ctt_ocr_contract import (
    CttFieldEvidence,
    CttFieldName,
    CttFieldObservation,
    CttPlayerFields,
    CttRegistrationDraft,
    CttSlotDraft,
    CttTeamDraft,
    CttTeamFields,
)

DOCUMENT_HASH = "a" * 64


def _observation(
    field_name: CttFieldName,
    value: Optional[str],
    *,
    page: int = 1,
    slot: Optional[int] = None,
    confidence: float = 0.99,
) -> CttFieldObservation:
    source_page = page
    source_slot = slot
    return CttFieldObservation(
        field_name=field_name,
        raw_text=value,
        confidence=confidence,
        candidates=[],
        evidence=CttFieldEvidence(
            page=page,
            slot=slot,
            source_page=source_page,
            source_slot=source_slot,
            crop_id=(
                f"p{page}:header:{field_name.value}"
                if slot is None
                else f"p{page}:slot-{slot}:{field_name.value}"
            ),
        ),
    )


def _draft(
    *,
    team_name: str = "Deportivo Estrellas",
    team_confidence: float = 0.99,
    occupied_count: int = 16,
    review_slot: Optional[int] = None,
) -> CttRegistrationDraft:
    team = CttTeamDraft(
        fields=CttTeamFields(
            name=_observation(
                CttFieldName.TEAM_NAME,
                team_name,
                confidence=team_confidence,
            ),
            category=_observation(CttFieldName.CATEGORY, "Libre"),
            gender=_observation(CttFieldName.GENDER, "Femenil"),
            league=_observation(CttFieldName.LEAGUE, None),
            representative_name=_observation(CttFieldName.REPRESENTATIVE_NAME, None),
            email=_observation(CttFieldName.EMAIL, None),
            state=_observation(CttFieldName.STATE, None),
            municipality=_observation(CttFieldName.MUNICIPALITY, None),
        )
    )
    slots = []
    for slot_number in range(1, 21):
        page = 1 if slot_number <= 8 else 2
        occupied = slot_number <= occupied_count
        confidence = 0.55 if slot_number == review_slot else 0.99
        slots.append(
            CttSlotDraft(
                page=page,
                slot=slot_number,
                occupied=occupied,
                fields=CttPlayerFields(
                    given_names=_observation(
                        CttFieldName.GIVEN_NAMES,
                        "Sensitive Given" if occupied else None,
                        page=page,
                        slot=slot_number,
                        confidence=confidence,
                    ),
                    paternal_surname=_observation(
                        CttFieldName.PATERNAL_SURNAME,
                        "Sensitive Surname" if occupied else None,
                        page=page,
                        slot=slot_number,
                    ),
                    maternal_surname=_observation(
                        CttFieldName.MATERNAL_SURNAME,
                        None,
                        page=page,
                        slot=slot_number,
                    ),
                    birth_date=_observation(
                        CttFieldName.BIRTH_DATE,
                        "01/01/2000" if occupied else None,
                        page=page,
                        slot=slot_number,
                    ),
                    curp=_observation(
                        CttFieldName.CURP,
                        None,
                        page=page,
                        slot=slot_number,
                    ),
                ),
            )
        )
    return CttRegistrationDraft(
        document_sha256=DOCUMENT_HASH,
        team=team,
        slots=slots,
    )


class StubCachedExtractor:
    def __init__(
        self, draft: CttRegistrationDraft, *, error: Optional[Exception] = None
    ):
        self.draft = draft
        self.error = error
        self.calls = 0

    async def extract(self, *_args, **_kwargs) -> CttCachedExtractionResult:
        if self.error:
            raise self.error
        self.calls += 1
        return CttCachedExtractionResult(
            draft=self.draft,
            model="test-model",
            response_ids=("response-id",) if self.calls == 1 else (),
            cache_hit=self.calls > 1,
            cache_key="b" * 64,
            attempts=1,
        )


def _pages():
    return [Image.new("RGB", (24, 32), "white"), Image.new("RGB", (24, 32), "white")]


def test_document_hash_preserves_page_order_and_boundaries() -> None:
    assert ctt_document_sha256([b"ab", b"c"]) != ctt_document_sha256([b"a", b"bc"])
    assert ctt_document_sha256([b"front", b"back"]) != ctt_document_sha256(
        [b"back", b"front"]
    )
    with pytest.raises(ValueError, match="two or three"):
        ctt_document_sha256([b"one"])
    with pytest.raises(ValueError, match="page 2 is empty"):
        ctt_document_sha256([b"one", b""])


def test_rollout_mode_defaults_and_invalid_values_fail_closed(monkeypatch) -> None:
    monkeypatch.delenv("CTT_RESPONSES_ROLLOUT", raising=False)
    assert ctt_canary_mode_from_env() == CttCanaryMode.OFF
    monkeypatch.setenv("CTT_RESPONSES_ROLLOUT", "shadow")
    assert ctt_canary_mode_from_env() == CttCanaryMode.SHADOW
    monkeypatch.setenv("CTT_RESPONSES_ROLLOUT", "surprise")
    assert ctt_canary_mode_from_env() == CttCanaryMode.OFF


@pytest.mark.asyncio
async def test_shadow_canary_accepts_but_never_replaces_current_flow() -> None:
    extractor = StubCachedExtractor(_draft())
    execution = await CttCanaryRunner(
        extractor,
        mode=CttCanaryMode.SHADOW,
        policy=CttCanaryPolicy(expected_team_name="deportivo estrellas"),
    ).run(_pages(), {}, document_sha256=DOCUMENT_HASH)

    assert execution.report.accepted is True
    assert execution.report.use_canonical_result is False
    assert execution.report.no_database_write is True
    assert execution.report.occupied_count == 16
    assert execution.report.replay_cache_hit is True
    assert execution.report.replay_hash_matches is True
    assert extractor.calls == 2


@pytest.mark.asyncio
async def test_active_canary_only_uses_result_after_acceptance() -> None:
    accepted = await CttCanaryRunner(
        StubCachedExtractor(_draft()),
        mode=CttCanaryMode.ACTIVE,
        policy=CttCanaryPolicy(expected_team_name="Deportivo Estrellas"),
    ).run(_pages(), {}, document_sha256=DOCUMENT_HASH)
    blocked = await CttCanaryRunner(
        StubCachedExtractor(_draft(team_name="Deportivo Estellas")),
        mode=CttCanaryMode.ACTIVE,
        policy=CttCanaryPolicy(expected_team_name="Deportivo Estrellas"),
    ).run(_pages(), {}, document_sha256=DOCUMENT_HASH)

    assert accepted.report.use_canonical_result is True
    assert blocked.report.accepted is False
    assert blocked.report.use_canonical_result is False
    assert {item.code for item in blocked.report.incidents} == {"TEAM_NAME_MISMATCH"}


@pytest.mark.asyncio
async def test_below_minimum_and_provider_failure_are_structured_blockers() -> None:
    below = await CttCanaryRunner(
        StubCachedExtractor(_draft(occupied_count=15)),
        policy=CttCanaryPolicy(minimum_players=16),
    ).run(_pages(), {}, document_sha256=DOCUMENT_HASH)
    failed = await CttCanaryRunner(
        StubCachedExtractor(_draft(), error=RuntimeError("secret provider detail")),
    ).run(_pages(), {}, document_sha256=DOCUMENT_HASH)

    assert "ROSTER_BELOW_MINIMUM" in {item.code for item in below.report.incidents}
    assert failed.report.incidents[0].code == "EXTRACTION_FAILED"
    assert "secret provider detail" not in failed.report.model_dump_json()


@pytest.mark.asyncio
async def test_non_form_images_are_rejected_before_provider_call() -> None:
    extractor = StubCachedExtractor(_draft())
    screenshots = [
        Image.new("RGB", (720, 1600), "#172330"),
        Image.new("RGB", (720, 1600), "#172330"),
    ]

    execution = await CttCanaryRunner(extractor).run(
        screenshots,
        {},
        document_sha256=DOCUMENT_HASH,
    )

    assert execution.report.accepted is False
    assert execution.report.incidents[0].code == "INVALID_DOCUMENT_IMAGE"
    assert extractor.calls == 0

    one_page = await CttCanaryRunner(extractor).run(
        [Image.new("RGB", (24, 32), "white")],
        {},
        document_sha256=DOCUMENT_HASH,
    )
    assert one_page.report.page_count == 1
    assert one_page.report.incidents[0].code == "INVALID_DOCUMENT_IMAGE"
    assert extractor.calls == 0


@pytest.mark.asyncio
async def test_review_incident_contains_coordinates_and_codes_but_no_pii() -> None:
    execution = await CttCanaryRunner(
        StubCachedExtractor(_draft(review_slot=4, team_confidence=0.55)),
        policy=CttCanaryPolicy(expected_team_name="Deportivo Estrellas"),
    ).run(_pages(), {}, document_sha256=DOCUMENT_HASH)

    incident = next(
        item
        for item in execution.report.incidents
        if item.code == "PLAYER_SLOT_REQUIRES_REVIEW"
    )
    serialized = execution.report.model_dump_json()
    assert execution.report.accepted is True
    assert incident.page == 1 and incident.slot == 4
    assert "given_names" in incident.field_names
    assert "LOW_CONFIDENCE" in incident.validation_codes
    assert "TEAM_HEADER_REQUIRES_REVIEW" in {
        item.code for item in execution.report.incidents
    }
    assert "Sensitive" not in serialized


def test_policy_rejects_impossible_bounds() -> None:
    with pytest.raises(ValueError, match="positive"):
        CttCanaryPolicy(minimum_players=0)
    with pytest.raises(ValueError, match="between"):
        CttCanaryPolicy(minimum_players=20, maximum_players=19)
