from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Optional

import pytest
from PIL import Image

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
from devnous.tournaments.instances.copa_telmex import ctt_review_handoff as handoff
from devnous.tournaments.instances.copa_telmex.ctt_review_handoff import (
    CttCanonicalReviewSink,
    build_canonical_review_payload,
    canonical_photo_box,
)


def _observation(
    field_name: CttFieldName,
    value: Optional[str],
    *,
    page: int = 1,
    slot: Optional[int] = None,
) -> CttFieldObservation:
    return CttFieldObservation(
        field_name=field_name,
        raw_text=value,
        confidence=0.95,
        evidence=CttFieldEvidence(
            page=page,
            slot=slot,
            source_page=page,
            source_slot=slot,
            crop_id=(
                f"p{page}:header:{field_name.value}"
                if slot is None
                else f"p{page}:slot-{slot}:{field_name.value}"
            ),
        ),
    )


def _draft(occupied_count: int = 2) -> CttRegistrationDraft:
    team = CttTeamDraft(
        fields=CttTeamFields(
            name=_observation(CttFieldName.TEAM_NAME, "Deportivo Estrellas"),
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
        slots.append(
            CttSlotDraft(
                page=page,
                slot=slot_number,
                occupied=occupied,
                fields=CttPlayerFields(
                    given_names=_observation(
                        CttFieldName.GIVEN_NAMES,
                        "María" if occupied else None,
                        page=page,
                        slot=slot_number,
                    ),
                    paternal_surname=_observation(
                        CttFieldName.PATERNAL_SURNAME,
                        f"Apellido {slot_number}" if occupied else None,
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
        document_sha256="a" * 64,
        team=team,
        slots=slots,
    )


class _Report:
    accepted = True
    use_canonical_result = False
    review_count = 1

    def model_dump(self, *, mode):
        assert mode == "json"
        return {
            "accepted": True,
            "use_canonical_result": False,
            "review_count": self.review_count,
        }


def _execution(occupied_count: int = 2):
    return SimpleNamespace(report=_Report(), draft=_draft(occupied_count))


def test_photo_box_uses_template_space_left_of_text_fields() -> None:
    fields = {
        "nombre": {"x": 0.24, "y": 0.43, "w": 0.25, "h": 0.03},
        "apellidos": {"x": 0.24, "y": 0.46, "w": 0.25, "h": 0.03},
        "nacimiento": {"x": 0.39, "y": 0.49, "w": 0.10, "h": 0.03},
        "curp": {"x": 0.22, "y": 0.52, "w": 0.27, "h": 0.03},
    }

    box = canonical_photo_box(
        fields,
        (2550, 3300),
        photo_extension_ratio=0.17,
    )

    assert 100 < box[0] < 300
    assert 450 < box[2] < 600
    assert box[1] < int(0.43 * 3300)
    assert box[3] > int(0.55 * 3300)

    shifted = canonical_photo_box(
        fields,
        (2550, 3300),
        photo_extension_ratio=0.17,
        vertical_offset_ratio=-0.04,
    )
    assert shifted[1] == box[1] - int(0.04 * 3300)
    assert shifted[3] == box[3] - int(0.04 * 3300)


def test_review_payload_keeps_canonical_result_non_authoritative() -> None:
    execution = _execution(occupied_count=2)

    payload = build_canonical_review_payload(
        execution,
        {1: {"relative_path": "review_sessions/x/player_01.jpg"}},
    )

    assert payload["schema_version"] == "ctt.canonical_review.v1"
    assert payload["authoritative"] is False
    assert payload["team"]["name"] == "Deportivo Estrellas"
    assert payload["team"]["field_evidence"]["team_name"]["page"] == 1
    assert [player["slot"] for player in payload["players"]] == [1, 2]
    assert payload["players"][0]["name"] == "María Apellido 1"
    assert payload["players"][0]["photo_preview"]["relative_path"].endswith(
        "player_01.jpg"
    )


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _FakeSession:
    def __init__(self, review_draft):
        self.review_draft = review_draft
        self.committed = False
        self.queries = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def execute(self, query):
        self.queries.append(query)
        return _ScalarResult(self.review_draft)

    async def commit(self):
        self.committed = True


@pytest.mark.asyncio
async def test_sink_persists_sidecar_without_replacing_legacy_draft(
    monkeypatch,
    tmp_path,
) -> None:
    review_draft = SimpleNamespace(
        ocr_raw={"provider": "legacy"},
        validation={"legacy": True},
        extraction={"team": {"name": "Legacy Team"}},
        review_edits={"team": {"name": "Operator Edit"}},
    )
    session = _FakeSession(review_draft)

    def session_maker():
        return session

    crop = Image.new("RGB", (120, 160), "red")
    monkeypatch.setattr(
        handoff,
        "build_canonical_photo_crops",
        lambda *_args: {
            1: (
                crop.copy(),
                {
                    "source_page": 1,
                    "source_slot": 1,
                    "normalized_box": {
                        "x": 1,
                        "y": 2,
                        "width": 120,
                        "height": 160,
                    },
                },
            )
        },
    )
    sink = CttCanonicalReviewSink(
        session_maker=session_maker,
        photos_base_dir=tmp_path,
    )
    session_id = "11111111-1111-1111-1111-111111111111"

    persisted = await sink.persist(
        session_id,
        _execution(occupied_count=1),
        [b"front", b"back"],
        {},
    )

    assert persisted is True
    assert session.committed is True
    assert session.queries[0]._for_update_arg is not None
    assert review_draft.extraction == {"team": {"name": "Legacy Team"}}
    assert review_draft.review_edits == {"team": {"name": "Operator Edit"}}
    assert review_draft.ocr_raw["provider"] == "legacy"
    canonical = review_draft.ocr_raw["canonical_shadow"]
    assert canonical["authoritative"] is False
    assert canonical["players"][0]["slot"] == 1
    audit = review_draft.validation["audit"]["canonical_shadow"]
    assert audit["player_count"] == 1
    assert audit["preview_count"] == 1
    preview = (
        tmp_path / "review_sessions" / session_id / "canonical_shadow" / "player_01.jpg"
    )
    assert preview.is_file()
    assert os.stat(preview).st_mode & 0o777 == 0o600
