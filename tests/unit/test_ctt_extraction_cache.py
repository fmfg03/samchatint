import json
import stat
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from typing import Any, Dict, List, Optional

import pytest
from PIL import Image

import devnous.tournaments.core.ctt_extraction_cache as cache_module
from devnous.tournaments.core.ctt_extraction_cache import (
    PRESENCE_CONFLICT_CONFIDENCE,
    CttCachedResponsesExtractor,
    CttDraftCache,
    CttDraftCacheCollision,
    CttDraftCacheCorruption,
    CttExtractionFingerprint,
    CttReconciliationError,
    reconcile_ctt_drafts,
)
from devnous.tournaments.core.ctt_ocr_contract import (
    CttFieldEvidence,
    CttFieldName,
    CttFieldObservation,
    CttPlayerFields,
    CttRegistrationDraft,
    CttSlotDraft,
    CttTeamDraft,
    CttTeamFields,
    CttValidationCode,
)
from devnous.tournaments.core.ctt_responses_extractor import (
    CttResponsesExtractionResult,
)

DOCUMENT_HASH = "a" * 64
OTHER_DOCUMENT_HASH = "b" * 64
EVIDENCE_HASH = "c" * 64
OTHER_EVIDENCE_HASH = "d" * 64


def _layout(*, width: float = 0.2) -> Dict[str, Any]:
    return {
        "pages": {
            "front": {"cards": {"jugador_1": {"w": width, "x": 0.1}}},
            "back": {"cards": {"jugador_9": {"w": width, "x": 0.2}}},
        }
    }


def _observation(
    field_name: CttFieldName,
    value: Optional[str],
    *,
    confidence: float = 0.95,
    page: int = 1,
    slot: Optional[int] = None,
    evidence_hash: str = EVIDENCE_HASH,
) -> CttFieldObservation:
    location = "header" if slot is None else f"slot-{slot}"
    source_slot = None if slot is None else slot if slot <= 20 else slot - 12
    return CttFieldObservation(
        field_name=field_name,
        raw_text=value,
        confidence=confidence,
        candidates=[],
        evidence=CttFieldEvidence(
            page=page,
            slot=slot,
            source_page=page,
            source_slot=source_slot,
            crop_id=f"p{page}:{location}:{field_name.value}",
            crop_sha256=evidence_hash,
        ),
    )


def _team(
    name: Optional[str] = "Deportivo Estrellas",
    *,
    confidence: float = 0.95,
    evidence_hash: str = EVIDENCE_HASH,
) -> CttTeamDraft:
    def field(field_name: CttFieldName, value: Optional[str]) -> CttFieldObservation:
        return _observation(
            field_name,
            value,
            confidence=confidence,
            evidence_hash=evidence_hash,
        )

    return CttTeamDraft(
        fields=CttTeamFields(
            name=field(CttFieldName.TEAM_NAME, name),
            category=field(CttFieldName.CATEGORY, "Libre"),
            gender=field(CttFieldName.GENDER, "Femenil"),
            league=field(CttFieldName.LEAGUE, "Liga ejemplo"),
            representative_name=field(
                CttFieldName.REPRESENTATIVE_NAME,
                "Representante Ejemplo",
            ),
            email=field(CttFieldName.EMAIL, "equipo@example.com"),
            state=field(CttFieldName.STATE, "Michoacan"),
            municipality=field(CttFieldName.MUNICIPALITY, "Tacambaro"),
        )
    )


def _player_fields(
    slot: int,
    *,
    given_names: Optional[str] = None,
    present: bool = False,
    confidence: float = 0.95,
    evidence_hash: str = EVIDENCE_HASH,
) -> CttPlayerFields:
    page = 1 if slot <= 8 else 2 if slot <= 20 else 3
    if present and given_names is None:
        given_names = "Alma"

    def field(field_name: CttFieldName, value: Optional[str]) -> CttFieldObservation:
        return _observation(
            field_name,
            value,
            confidence=confidence,
            page=page,
            slot=slot,
            evidence_hash=evidence_hash,
        )

    return CttPlayerFields(
        given_names=field(CttFieldName.GIVEN_NAMES, given_names),
        paternal_surname=field(
            CttFieldName.PATERNAL_SURNAME,
            "Rios" if present else None,
        ),
        maternal_surname=field(
            CttFieldName.MATERNAL_SURNAME,
            "Luna" if present else None,
        ),
        birth_date=field(
            CttFieldName.BIRTH_DATE,
            "28/10/04" if present else None,
        ),
        curp=field(CttFieldName.CURP, None),
    )


def _draft(
    *,
    document_hash: str = DOCUMENT_HASH,
    team_name: Optional[str] = "Deportivo Estrellas",
    team_confidence: float = 0.95,
    player_one_name: Optional[str] = "Alma",
    player_one_present: bool = True,
    evidence_hash: str = EVIDENCE_HASH,
    slot_count: int = 20,
) -> CttRegistrationDraft:
    slots = []
    for slot in range(1, slot_count + 1):
        present = slot == 1 and player_one_present
        if slot_count == 25 and 9 <= slot <= 21:
            present = True
        slots.append(
            CttSlotDraft(
                page=1 if slot <= 8 else 2 if slot <= 20 else 3,
                slot=slot,
                fields=_player_fields(
                    slot,
                    given_names=player_one_name if slot == 1 else None,
                    present=present,
                    confidence=team_confidence,
                    evidence_hash=evidence_hash,
                ),
            )
        )
    return CttRegistrationDraft(
        document_sha256=document_hash,
        team=_team(
            team_name,
            confidence=team_confidence,
            evidence_hash=evidence_hash,
        ),
        slots=slots,
    )


def _fingerprint(
    *,
    document_hash: str = DOCUMENT_HASH,
    model: str = "gpt-5.6-terra",
    attempts: int = 2,
    layout: Optional[Dict[str, Any]] = None,
    pipeline_version: str = "ctt.responses.v2",
) -> CttExtractionFingerprint:
    return CttExtractionFingerprint.from_inputs(
        document_sha256=document_hash,
        model=model,
        layout=layout or _layout(),
        attempts=attempts,
        pipeline_version=pipeline_version,
    )


def test_fingerprint_is_stable_and_covers_policy_inputs() -> None:
    first_layout = _layout()
    second_layout = {
        "pages": {
            "back": {"cards": {"jugador_9": {"x": 0.2, "w": 0.2}}},
            "front": {"cards": {"jugador_1": {"x": 0.1, "w": 0.2}}},
        }
    }

    first = _fingerprint(layout=first_layout)
    assert first.cache_key() == _fingerprint(layout=second_layout).cache_key()
    assert (
        first.cache_key() != _fingerprint(document_hash=OTHER_DOCUMENT_HASH).cache_key()
    )
    assert first.cache_key() != _fingerprint(model="gpt-4.1").cache_key()
    assert (
        first.cache_key()
        != _fingerprint(pipeline_version="ctt.responses.v3").cache_key()
    )
    assert first.cache_key() != _fingerprint(attempts=1).cache_key()
    assert first.cache_key() != _fingerprint(layout=_layout(width=0.3)).cache_key()


def test_fingerprint_rejects_invalid_attempt_count() -> None:
    with pytest.raises(ValueError, match="less than or equal to 3"):
        _fingerprint(attempts=4)


def test_reconciliation_is_order_independent_and_conservative() -> None:
    high = _draft(team_confidence=0.95)
    low = _draft(team_confidence=0.88)

    forward = reconcile_ctt_drafts([high, low])
    reverse = reconcile_ctt_drafts([low, high])

    assert forward.canonical_hash() == reverse.canonical_hash()
    assert forward.team.fields.name.normalized_value == "Deportivo Estrellas"
    assert forward.team.fields.name.confidence == 0.88
    assert forward.slots[0].fields.birth_date.normalized_value == "2004-10-28"


def test_reconciliation_preserves_extension_slots_and_source_evidence() -> None:
    first = _draft(slot_count=25, team_confidence=0.95)
    second = _draft(slot_count=25, team_confidence=0.88)

    result = reconcile_ctt_drafts([first, second])

    assert len(result.slots) == 25
    assert result.slots[20].slot == 21
    assert result.slots[20].page == 3
    assert result.slots[20].occupied is True
    evidence = result.slots[20].fields.given_names.evidence
    assert evidence.source_page == 3
    assert evidence.source_slot == 9


def test_reconciliation_surfaces_text_conflicts() -> None:
    result = reconcile_ctt_drafts(
        [
            _draft(team_name="Deportivo Estrellas"),
            _draft(team_name="Deportivo Estellas"),
        ]
    )

    name = result.team.fields.name
    assert name.normalized_value is None
    assert name.candidates == ["Deportivo Estellas", "Deportivo Estrellas"]
    assert CttValidationCode.FIELD_CONFLICT_REQUIRES_REVIEW in name.validation_codes
    assert name.requires_review is True
    assert result.requires_review is True


def test_reconciliation_keeps_presence_disagreement_for_review() -> None:
    present = _draft(player_one_present=True, player_one_name="Alma")
    absent = _draft(player_one_present=False, player_one_name=None)

    result = reconcile_ctt_drafts([present, absent])
    given_names = result.slots[0].fields.given_names

    assert given_names.normalized_value == "Alma"
    assert given_names.confidence == PRESENCE_CONFLICT_CONFIDENCE
    assert CttValidationCode.LOW_CONFIDENCE in given_names.validation_codes
    assert result.slots[0].requires_review is True


def test_reconciliation_rejects_document_or_evidence_mismatch() -> None:
    with pytest.raises(CttReconciliationError, match="document hashes differ"):
        reconcile_ctt_drafts([_draft(), _draft(document_hash=OTHER_DOCUMENT_HASH)])

    with pytest.raises(CttReconciliationError, match="evidence differs"):
        reconcile_ctt_drafts([_draft(), _draft(evidence_hash=OTHER_EVIDENCE_HASH)])

    with pytest.raises(CttReconciliationError, match="at least one"):
        reconcile_ctt_drafts([])

    with pytest.raises(CttReconciliationError, match="slot sets differ"):
        reconcile_ctt_drafts([_draft(), _draft(slot_count=25)])


def test_cache_round_trip_is_content_addressed_and_private(tmp_path: Path) -> None:
    cache = CttDraftCache(tmp_path / "cache")
    fingerprint = _fingerprint()
    draft = _draft()

    assert cache.load(fingerprint) is None
    stored = cache.save(fingerprint, draft)
    loaded = cache.load(fingerprint)

    assert stored.canonical_hash() == draft.canonical_hash()
    assert loaded is not None
    assert loaded.canonical_hash() == draft.canonical_hash()
    assert cache.save(fingerprint, draft).canonical_hash() == draft.canonical_hash()
    path = cache.path_for(fingerprint)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.parent.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE((tmp_path / "cache").stat().st_mode) == 0o700


def _race_cache_saves(
    cache: CttDraftCache,
    fingerprint: CttExtractionFingerprint,
    drafts: List[CttRegistrationDraft],
    monkeypatch: pytest.MonkeyPatch,
) -> List[Future]:
    original_link = cache_module.os.link
    barrier = Barrier(len(drafts))

    def synchronized_link(source: str, target: str) -> None:
        barrier.wait(timeout=5)
        original_link(source, target)

    monkeypatch.setattr(cache_module.os, "link", synchronized_link)
    with ThreadPoolExecutor(max_workers=len(drafts)) as executor:
        futures = [executor.submit(cache.save, fingerprint, draft) for draft in drafts]
    return futures


def test_concurrent_same_draft_saves_are_stable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = CttDraftCache(tmp_path / "cache")
    fingerprint = _fingerprint()
    draft = _draft()

    futures = _race_cache_saves(
        cache,
        fingerprint,
        [draft, draft],
        monkeypatch,
    )

    assert [future.result().canonical_hash() for future in futures] == [
        draft.canonical_hash(),
        draft.canonical_hash(),
    ]
    loaded = cache.load(fingerprint)
    assert loaded is not None
    assert loaded.canonical_hash() == draft.canonical_hash()


def test_concurrent_different_drafts_raise_collision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = CttDraftCache(tmp_path / "cache")
    fingerprint = _fingerprint()
    drafts = [_draft(), _draft(team_name="Otro Equipo")]

    futures = _race_cache_saves(cache, fingerprint, drafts, monkeypatch)
    successes = []
    collisions = 0
    for future in futures:
        try:
            successes.append(future.result())
        except CttDraftCacheCollision:
            collisions += 1

    assert len(successes) == 1
    assert collisions == 1
    loaded = cache.load(fingerprint)
    assert loaded is not None
    assert loaded.canonical_hash() == successes[0].canonical_hash()


def test_canonical_draft_json_round_trip_preserves_hash() -> None:
    draft = _draft()

    restored = CttRegistrationDraft.model_validate_json(draft.model_dump_json())

    assert restored.canonical_hash() == draft.canonical_hash()


def test_corrupted_cache_entry_fails_closed(tmp_path: Path) -> None:
    cache = CttDraftCache(tmp_path / "cache")
    fingerprint = _fingerprint()
    cache.save(fingerprint, _draft())
    path = cache.path_for(fingerprint)
    entry = json.loads(path.read_text(encoding="utf-8"))
    entry["canonical_hash"] = "0" * 64
    path.write_text(json.dumps(entry), encoding="utf-8")

    with pytest.raises(CttDraftCacheCorruption, match="canonical hash mismatch"):
        cache.load(fingerprint)


def test_cache_rejects_coherent_draft_for_another_document(tmp_path: Path) -> None:
    cache = CttDraftCache(tmp_path / "cache")
    fingerprint = _fingerprint()
    cache.save(fingerprint, _draft())
    path = cache.path_for(fingerprint)
    entry = json.loads(path.read_text(encoding="utf-8"))
    other_draft = _draft(document_hash=OTHER_DOCUMENT_HASH)
    entry["draft"] = other_draft.model_dump(mode="json")
    entry["canonical_hash"] = other_draft.canonical_hash()
    path.write_text(json.dumps(entry), encoding="utf-8")

    with pytest.raises(CttDraftCacheCorruption, match="document hash mismatch"):
        cache.load(fingerprint)


def test_cache_collision_and_document_mismatch_fail_closed(tmp_path: Path) -> None:
    cache = CttDraftCache(tmp_path / "cache")
    fingerprint = _fingerprint()
    cache.save(fingerprint, _draft())

    with pytest.raises(CttDraftCacheCollision, match="different draft"):
        cache.save(fingerprint, _draft(team_name="Otro Equipo"))
    with pytest.raises(CttDraftCacheCollision, match="document hash"):
        cache.save(fingerprint, _draft(document_hash=OTHER_DOCUMENT_HASH))


class FakeExtractor:
    def __init__(
        self,
        results: List[CttResponsesExtractionResult],
        *,
        model: str = "gpt-5.6-terra",
        pipeline_version: str = "ctt.responses.v3",
    ) -> None:
        self.model = model
        self.pipeline_version = pipeline_version
        self.results = list(results)
        self.calls = 0

    async def extract(
        self,
        page_images: List[Image.Image],
        layout: Dict[str, Any],
        *,
        document_sha256: str,
    ) -> CttResponsesExtractionResult:
        self.calls += 1
        if not self.results:
            raise AssertionError("unexpected extractor call")
        return self.results.pop(0)


def _result(
    draft: CttRegistrationDraft,
    response_id: str,
    *,
    model: str = "gpt-5.6-terra",
) -> CttResponsesExtractionResult:
    return CttResponsesExtractionResult(
        draft=draft,
        model=model,
        response_ids=(response_id,),
    )


@pytest.mark.asyncio
async def test_cached_wrapper_reconciles_once_then_avoids_api_calls(
    tmp_path: Path,
) -> None:
    extractor = FakeExtractor(
        [
            _result(_draft(team_name="Deportivo Estrellas"), "resp-1"),
            _result(_draft(team_name="Deportivo Estellas"), "resp-2"),
        ]
    )
    wrapped = CttCachedResponsesExtractor(
        extractor,  # type: ignore[arg-type]
        CttDraftCache(tmp_path / "cache"),
        attempts=2,
    )
    pages = [Image.new("RGB", (10, 10)), Image.new("RGB", (10, 10))]

    first = await wrapped.extract(
        pages,
        _layout(),
        document_sha256=DOCUMENT_HASH,
    )
    second = await wrapped.extract(
        pages,
        _layout(),
        document_sha256=DOCUMENT_HASH,
    )

    assert first.cache_hit is False
    assert first.response_ids == ("resp-1", "resp-2")
    assert first.draft.team.fields.name.requires_review is True
    assert second.cache_hit is True
    assert second.response_ids == ()
    assert second.cache_key == first.cache_key
    assert second.draft.canonical_hash() == first.draft.canonical_hash()
    assert extractor.calls == 2


@pytest.mark.asyncio
async def test_cached_wrapper_separates_canonical_input_policy(tmp_path: Path) -> None:
    cache = CttDraftCache(tmp_path / "cache")
    raw_extractor = FakeExtractor([_result(_draft(), "resp-raw")])
    canonical_extractor = FakeExtractor(
        [_result(_draft(), "resp-canonical")],
        pipeline_version="ctt.responses.v3.canonical_input",
    )
    pages = [Image.new("RGB", (10, 10)), Image.new("RGB", (10, 10))]

    raw = await CttCachedResponsesExtractor(raw_extractor, cache).extract(
        pages,
        _layout(),
        document_sha256=DOCUMENT_HASH,
    )
    canonical = await CttCachedResponsesExtractor(canonical_extractor, cache).extract(
        pages,
        _layout(),
        document_sha256=DOCUMENT_HASH,
    )

    assert raw.cache_key != canonical.cache_key
    assert raw_extractor.calls == 1
    assert canonical_extractor.calls == 1


@pytest.mark.asyncio
async def test_cached_wrapper_rejects_model_mismatch(tmp_path: Path) -> None:
    extractor = FakeExtractor([_result(_draft(), "resp-1", model="gpt-4.1")])
    wrapped = CttCachedResponsesExtractor(
        extractor,  # type: ignore[arg-type]
        CttDraftCache(tmp_path / "cache"),
    )

    with pytest.raises(CttReconciliationError, match="result model differs"):
        await wrapped.extract(
            [Image.new("RGB", (10, 10)), Image.new("RGB", (10, 10))],
            _layout(),
            document_sha256=DOCUMENT_HASH,
        )


def test_cached_wrapper_rejects_unbounded_attempts(tmp_path: Path) -> None:
    extractor = FakeExtractor([])
    with pytest.raises(ValueError, match="between 1 and 3"):
        CttCachedResponsesExtractor(
            extractor,  # type: ignore[arg-type]
            CttDraftCache(tmp_path),
            attempts=0,
        )
