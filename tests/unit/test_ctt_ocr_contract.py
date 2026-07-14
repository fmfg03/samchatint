import json
import subprocess
import sys
from typing import Dict, List, Optional

import pytest
from pydantic import ValidationError

from devnous.tournaments.core.ctt_ocr_contract import (
    SCHEMA_VERSION,
    CttFieldEvidence,
    CttFieldName,
    CttFieldObservation,
    CttPlayerFields,
    CttRegistrationDraft,
    CttSlotDraft,
    CttSlotStatus,
    CttTeamDraft,
    CttTeamFields,
    CttValidationCode,
)

DOCUMENT_HASH = "a" * 64


def _field(
    field_name: CttFieldName,
    raw_text: Optional[str],
    *,
    page: int = 1,
    slot: Optional[int] = None,
    confidence: float = 0.95,
    candidates: Optional[List[str]] = None,
    source_page: Optional[int] = None,
    source_slot: Optional[int] = None,
) -> CttFieldObservation:
    scope = "header" if slot is None else f"slot-{slot}"
    physical_slot = source_slot
    if slot is not None and physical_slot is None:
        physical_slot = slot if slot <= 20 else slot - 12
    return CttFieldObservation(
        field_name=field_name,
        raw_text=raw_text,
        normalized_value="untrusted-model-value",
        confidence=confidence,
        evidence=CttFieldEvidence(
            page=page,
            slot=slot,
            source_page=source_page or page,
            source_slot=physical_slot,
            crop_id=f"p{page}:{scope}:{field_name.value}",
            crop_sha256="b" * 64,
        ),
        candidates=candidates or [],
    )


def _player_fields(
    *,
    page: int,
    slot: int,
    given_names: Optional[str] = "Alma",
    paternal_surname: Optional[str] = "Rios",
    maternal_surname: Optional[str] = "Luna",
    birth_date: Optional[str] = "28/10/04",
    curp: Optional[str] = None,
) -> CttPlayerFields:
    return CttPlayerFields(
        given_names=_field(CttFieldName.GIVEN_NAMES, given_names, page=page, slot=slot),
        paternal_surname=_field(
            CttFieldName.PATERNAL_SURNAME,
            paternal_surname,
            page=page,
            slot=slot,
        ),
        maternal_surname=_field(
            CttFieldName.MATERNAL_SURNAME,
            maternal_surname,
            page=page,
            slot=slot,
        ),
        birth_date=_field(CttFieldName.BIRTH_DATE, birth_date, page=page, slot=slot),
        curp=_field(CttFieldName.CURP, curp, page=page, slot=slot),
    )


def _team_fields(name: Optional[str] = "Deportivo Estrellas") -> CttTeamFields:
    values: Dict[CttFieldName, Optional[str]] = {
        CttFieldName.TEAM_NAME: name,
        CttFieldName.CATEGORY: "Libre",
        CttFieldName.GENDER: "Femenil",
        CttFieldName.LEAGUE: "Liga prueba",
        CttFieldName.REPRESENTATIVE_NAME: "Representante Ejemplo",
        CttFieldName.EMAIL: "EQUIPO@EXAMPLE.COM",
        CttFieldName.STATE: "Michoacan",
        CttFieldName.MUNICIPALITY: "Tacambaro",
    }
    return CttTeamFields(
        name=_field(CttFieldName.TEAM_NAME, values[CttFieldName.TEAM_NAME]),
        category=_field(CttFieldName.CATEGORY, values[CttFieldName.CATEGORY]),
        gender=_field(CttFieldName.GENDER, values[CttFieldName.GENDER]),
        league=_field(CttFieldName.LEAGUE, values[CttFieldName.LEAGUE]),
        representative_name=_field(
            CttFieldName.REPRESENTATIVE_NAME,
            values[CttFieldName.REPRESENTATIVE_NAME],
        ),
        email=_field(CttFieldName.EMAIL, values[CttFieldName.EMAIL]),
        state=_field(CttFieldName.STATE, values[CttFieldName.STATE]),
        municipality=_field(
            CttFieldName.MUNICIPALITY,
            values[CttFieldName.MUNICIPALITY],
        ),
    )


def _slot(slot_number: int, *, present: bool = False) -> CttSlotDraft:
    page = 1 if slot_number <= 8 else 2 if slot_number <= 20 else 3
    empty_value = None if not present else "Alma"
    return CttSlotDraft(
        page=page,
        slot=slot_number,
        fields=_player_fields(
            page=page,
            slot=slot_number,
            given_names=empty_value,
            paternal_surname=None if not present else "Rios",
            maternal_surname=None if not present else "Luna",
            birth_date=None if not present else "28/10/04",
            curp=None,
        ),
    )


def _complete_slots(count: int = 20) -> List[CttSlotDraft]:
    if count == 25:
        present = {1, 21}.union(range(9, 21))
    else:
        present = {1, 9}
    return [_slot(number, present=number in present) for number in range(1, count + 1)]


def test_field_normalization_ignores_untrusted_model_value() -> None:
    text = _field(CttFieldName.TEAM_NAME, "  Deportivo   Estrellas ")
    email = _field(CttFieldName.EMAIL, "  EQUIPO@EXAMPLE.COM ")
    birth_date = _field(CttFieldName.BIRTH_DATE, "28/10/04", slot=1)

    assert text.normalized_value == "Deportivo Estrellas"
    assert email.normalized_value == "equipo@example.com"
    assert birth_date.normalized_value == "2004-10-28"
    assert not text.requires_review


def test_invalid_date_and_curp_require_review() -> None:
    birth_date = _field(CttFieldName.BIRTH_DATE, "28/Feb/04", slot=1)
    curp = _field(CttFieldName.CURP, "NO-ES-CURP", slot=1)

    assert birth_date.normalized_value is None
    assert birth_date.validation_codes == [CttValidationCode.INVALID_DATE_FORMAT]
    assert curp.normalized_value is None
    assert curp.validation_codes == [CttValidationCode.INVALID_CURP_FORMAT]
    assert birth_date.requires_review and curp.requires_review


def test_low_confidence_and_conflicts_are_derived() -> None:
    field = _field(
        CttFieldName.GIVEN_NAMES,
        "Violeta",
        slot=1,
        confidence=0.70,
        candidates=["Violeta", "Violet"],
    )

    assert field.normalized_value is None
    assert field.candidates == ["Violeta", "Violet"]
    assert field.validation_codes == [
        CttValidationCode.LOW_CONFIDENCE,
        CttValidationCode.FIELD_CONFLICT_REQUIRES_REVIEW,
    ]
    assert field.requires_review


def test_untrusted_derived_state_is_recomputed() -> None:
    field = CttFieldObservation(
        field_name=CttFieldName.GIVEN_NAMES,
        raw_text="Violeta",
        normalized_value="Otro nombre",
        confidence=0.95,
        requires_review=True,
        evidence=CttFieldEvidence(
            page=1,
            slot=1,
            crop_id="p1:slot-1:given_names",
        ),
        validation_codes=[CttValidationCode.INVALID_CURP_FORMAT],
    )
    slot = CttSlotDraft(
        page=1,
        slot=1,
        fields=_player_fields(page=1, slot=1),
        status=CttSlotStatus.REQUIRES_REVIEW,
        requires_review=True,
        validation_codes=[CttValidationCode.SLOT_INCOMPLETE],
    )
    team = CttTeamDraft(
        fields=_team_fields(),
        requires_review=True,
        validation_codes=[CttValidationCode.TEAM_NAME_REQUIRED],
    )

    assert field.normalized_value == "Violeta"
    assert field.validation_codes == []
    assert not field.requires_review
    assert slot.status == CttSlotStatus.PRESENT
    assert slot.validation_codes == []
    assert not slot.requires_review
    assert team.validation_codes == []
    assert not team.requires_review


def test_empty_slot_stays_empty_without_false_review() -> None:
    slot = CttSlotDraft(
        page=2,
        slot=17,
        fields=_player_fields(
            page=2,
            slot=17,
            given_names=None,
            paternal_surname=None,
            maternal_surname=None,
            birth_date=None,
            curp=None,
        ),
    )

    assert slot.status == CttSlotStatus.EMPTY
    assert not slot.requires_review
    assert slot.validation_codes == []


def test_visually_occupied_slot_without_text_requires_review() -> None:
    slot = CttSlotDraft(
        page=3,
        slot=21,
        occupied=True,
        fields=_player_fields(
            page=3,
            slot=21,
            given_names=None,
            paternal_surname=None,
            maternal_surname=None,
            birth_date=None,
            curp=None,
        ),
    )

    assert slot.status == CttSlotStatus.REQUIRES_REVIEW
    assert slot.occupied is True
    assert CttValidationCode.SLOT_INCOMPLETE in slot.validation_codes


def test_incomplete_present_slot_requires_review() -> None:
    slot = CttSlotDraft(
        page=1,
        slot=1,
        fields=_player_fields(page=1, slot=1, birth_date="28/Feb/04"),
    )

    assert slot.status == CttSlotStatus.REQUIRES_REVIEW
    assert CttValidationCode.SLOT_INCOMPLETE in slot.validation_codes
    assert slot.requires_review


def test_complete_slot_is_present() -> None:
    slot = CttSlotDraft(
        page=2,
        slot=9,
        fields=_player_fields(page=2, slot=9),
    )

    assert slot.status == CttSlotStatus.PRESENT
    assert not slot.requires_review


def test_page_slot_and_evidence_mismatches_fail_closed() -> None:
    with pytest.raises(ValidationError, match="must use page 1"):
        CttFieldEvidence(page=2, slot=1, crop_id="bad:page")
    with pytest.raises(ValidationError, match="belongs to page 1"):
        CttSlotDraft(
            page=2,
            slot=1,
            fields=_player_fields(page=1, slot=1),
        )
    with pytest.raises(ValidationError, match="field evidence slot"):
        CttSlotDraft(
            page=1,
            slot=1,
            fields=_player_fields(page=1, slot=2),
        )

    remapped = CttFieldEvidence(
        page=3,
        slot=21,
        source_page=2,
        source_slot=9,
        crop_id="p3:slot-21:source-p2-slot-9:given_names",
    )
    assert remapped.page == 3
    assert remapped.source_page == 2
    assert remapped.source_slot == 9

    with pytest.raises(ValidationError, match="source page 1"):
        CttFieldEvidence(
            page=1,
            source_page=2,
            crop_id="bad:header-source",
        )
    with pytest.raises(ValidationError, match="back-page evidence"):
        CttFieldEvidence(
            page=3,
            slot=21,
            source_page=1,
            source_slot=1,
            crop_id="bad:extension-source",
        )


def test_team_name_is_required_and_header_evidence_is_strict() -> None:
    team = CttTeamDraft(fields=_team_fields(name=None))

    assert team.requires_review
    assert team.validation_codes == [CttValidationCode.TEAM_NAME_REQUIRED]

    wrong_name = _field(CttFieldName.CATEGORY, "Estrellas")
    fields = _team_fields().model_copy(update={"name": wrong_name})
    with pytest.raises(ValidationError, match="name must contain team_name"):
        CttTeamFields.model_validate(fields.model_dump())


def test_draft_sorts_slots_and_has_stable_canonical_hash() -> None:
    team = CttTeamDraft(fields=_team_fields())
    slots = _complete_slots()

    first = CttRegistrationDraft(
        document_sha256=DOCUMENT_HASH,
        team=team,
        slots=list(reversed(slots)),
    )
    second = CttRegistrationDraft(
        document_sha256=DOCUMENT_HASH,
        team=team,
        slots=slots,
    )

    assert first.schema_version == SCHEMA_VERSION
    assert [slot.slot for slot in first.slots] == list(range(1, 21))
    assert first.canonical_payload() == second.canonical_payload()
    assert first.canonical_hash() == second.canonical_hash()


def test_extension_page_requires_full_primary_back_and_one_to_five_players() -> None:
    team = CttTeamDraft(fields=_team_fields())
    valid = CttRegistrationDraft(
        document_sha256=DOCUMENT_HASH,
        team=team,
        slots=_complete_slots(25),
    )

    assert len(valid.slots) == 25
    assert valid.slots[20].page == 3
    assert valid.slots[20].occupied is True
    assert all(slot.occupied for slot in valid.slots[8:20])

    primary_not_full = _complete_slots(25)
    primary_not_full[8] = _slot(9, present=False)
    with pytest.raises(ValidationError, match="primary page 2 must be full"):
        CttRegistrationDraft(
            document_sha256=DOCUMENT_HASH,
            team=team,
            slots=primary_not_full,
        )

    empty_extension = [
        _slot(number, present=(number == 1 or 9 <= number <= 20))
        for number in range(1, 26)
    ]
    with pytest.raises(ValidationError, match="at least one player"):
        CttRegistrationDraft(
            document_sha256=DOCUMENT_HASH,
            team=team,
            slots=empty_extension,
        )


def test_duplicate_slots_are_rejected() -> None:
    slot = CttSlotDraft(page=1, slot=1, fields=_player_fields(page=1, slot=1))
    with pytest.raises(ValidationError, match="duplicate page/slot"):
        CttRegistrationDraft(
            document_sha256=DOCUMENT_HASH,
            team=CttTeamDraft(fields=_team_fields()),
            slots=[slot, slot],
        )


def test_missing_template_slots_are_rejected() -> None:
    with pytest.raises(ValidationError, match="materialize every slot"):
        CttRegistrationDraft(
            document_sha256=DOCUMENT_HASH,
            team=CttTeamDraft(fields=_team_fields()),
            slots=[_slot(1, present=True)],
        )


def test_unknown_fields_are_rejected() -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        CttRegistrationDraft.model_validate(
            {
                "document_sha256": DOCUMENT_HASH,
                "team": CttTeamDraft(fields=_team_fields()).model_dump(),
                "slots": [],
                "unexpected": True,
            }
        )


def test_json_schema_exposes_versioned_structured_contract() -> None:
    # Exercise the same plugin-free process used by the application runtime.
    script = """
import json
from devnous.tournaments.core.ctt_ocr_contract import CttRegistrationDraft

schema = CttRegistrationDraft.model_json_schema()
print(json.dumps({
    "version": schema["properties"]["schema_version"]["const"],
    "definitions": sorted(schema["$defs"]),
}))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
    )
    schema_summary = json.loads(completed.stdout)

    assert schema_summary["version"] == SCHEMA_VERSION
    assert "CttFieldObservation" in schema_summary["definitions"]
