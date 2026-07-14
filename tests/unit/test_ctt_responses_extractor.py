from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest
from PIL import Image
from pydantic import ValidationError

import devnous.tournaments.core.ctt_responses_extractor as extractor_module
from devnous.tournaments.core.ctt_ocr_contract import CttSlotStatus
from devnous.tournaments.core.ctt_responses_extractor import (
    CttHeaderExtraction,
    CttPlayerExtraction,
    CttRawField,
    CttResponsesExtractor,
    CttResponsesProtocolError,
    CttSlotBatchExtraction,
)

DOCUMENT_HASH = "a" * 64


class FakeResponses:
    def __init__(self, outputs: List[Any]) -> None:
        self.outputs = list(outputs)
        self.calls: List[Dict[str, Any]] = []

    async def parse(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if not self.outputs:
            raise AssertionError("unexpected Responses call")
        return self.outputs.pop(0)


class FakeClient:
    def __init__(self, outputs: List[Any]) -> None:
        self.responses = FakeResponses(outputs)


def _raw(
    value: Optional[str],
    *,
    confidence: float = 0.95,
    candidates: Optional[List[str]] = None,
) -> CttRawField:
    return CttRawField(
        raw_text=value,
        confidence=confidence,
        candidates=candidates or [],
    )


def _header() -> CttHeaderExtraction:
    return CttHeaderExtraction(
        team_name=_raw("Deportivo Estrellas"),
        category=_raw("Libre"),
        gender=_raw("Femenil"),
        league=_raw("Liga ejemplo"),
        representative_name=_raw("Representante Ejemplo"),
        email=_raw("EQUIPO@EXAMPLE.COM"),
        state=_raw("Michoacan"),
        municipality=_raw("Tacambaro"),
    )


def _player(slot: int, *, present: bool = False) -> CttPlayerExtraction:
    return CttPlayerExtraction(
        slot=slot,
        given_names=_raw("Alma" if present else None),
        paternal_surname=_raw("Rios" if present else None),
        maternal_surname=_raw("Luna" if present else None),
        birth_date=_raw("28/10/04" if present else None),
        curp=_raw(None),
    )


def _slot_batches() -> List[CttSlotBatchExtraction]:
    batches = []
    for start in range(1, 21, 4):
        batches.append(
            CttSlotBatchExtraction(
                slots=[
                    _player(number, present=number in {1, 9})
                    for number in range(start, start + 4)
                ]
            )
        )
    return batches


def _responses(
    *,
    batches: Optional[List[CttSlotBatchExtraction]] = None,
) -> List[Any]:
    parsed = [_header()] + list(batches or _slot_batches())
    return [
        SimpleNamespace(id=f"resp-{index}", output_parsed=value)
        for index, value in enumerate(parsed, 1)
    ]


def _field_box(x: float, y: float) -> Dict[str, float]:
    return {"x": x, "y": y, "w": 0.18, "h": 0.04}


def _layout() -> Dict[str, Any]:
    header_names = (
        "equipo_nombre",
        "categoria",
        "rama",
        "liga",
        "representante_nombre",
        "correo",
        "estado",
        "municipio",
    )
    header_fields = {
        name: _field_box(0.10, 0.04 + (index * 0.05))
        for index, name in enumerate(header_names)
    }

    def cards(first: int, last: int) -> Dict[str, Any]:
        return {
            f"jugador_{number}": {
                "nombre": _field_box(0.25, 0.10),
                "apellidos": _field_box(0.25, 0.16),
                "nacimiento": _field_box(0.25, 0.22),
                "curp": _field_box(0.25, 0.28),
            }
            for number in range(first, last + 1)
        }

    return {
        "pages": {
            "front": {
                "header_fields": header_fields,
                "cards": cards(1, 8),
            },
            "back": {
                "cards": cards(9, 20),
            },
        }
    }


def _pages() -> List[Image.Image]:
    return [
        Image.new("RGB", (300, 420), "white"),
        Image.new("RGB", (300, 420), "white"),
    ]


@pytest.fixture(autouse=True)
def bypass_geometry_normalization(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        extractor_module,
        "normalize_ctt_template_image",
        lambda image: (image.convert("RGB"), {"test": True}),
    )


@pytest.mark.asyncio
async def test_extract_uses_bounded_structured_responses_and_builds_draft() -> None:
    client = FakeClient(_responses())
    extractor = CttResponsesExtractor(client, model="gpt-4.1-mini")

    result = await extractor.extract(
        _pages(),
        _layout(),
        document_sha256=DOCUMENT_HASH,
    )

    assert result.model == "gpt-4.1-mini"
    assert result.response_ids == tuple(f"resp-{number}" for number in range(1, 7))
    assert result.draft.team.fields.name.normalized_value == "Deportivo Estrellas"
    assert result.draft.team.fields.email.normalized_value == "equipo@example.com"
    assert len(result.draft.slots) == 20
    assert result.draft.slots[0].status == CttSlotStatus.PRESENT
    assert result.draft.slots[1].status == CttSlotStatus.EMPTY
    assert result.draft.slots[8].status == CttSlotStatus.PRESENT
    assert result.draft.slots[0].fields.birth_date.normalized_value == "2004-10-28"
    evidence = result.draft.slots[0].fields.given_names.evidence
    assert evidence.page == 1
    assert evidence.slot == 1
    assert evidence.crop_id == "p1:slot-1:given_names"
    assert evidence.crop_sha256 and len(evidence.crop_sha256) == 64

    calls = client.responses.calls
    assert len(calls) == 6
    assert calls[0]["text_format"] is CttHeaderExtraction
    assert all(call["store"] is False for call in calls)
    assert all("temperature" not in call for call in calls)
    assert all(call["metadata"]["schema_version"] for call in calls)
    for call in calls:
        content = call["input"][0]["content"]
        assert [part["type"] for part in content] == [
            "input_text",
            "input_image",
            "input_image",
        ]
        assert content[1]["detail"] == "low"
        assert content[2]["detail"] == "high"
        assert content[1]["image_url"].startswith("data:image/jpeg;base64,")
    assert calls[1]["text_format"] is CttSlotBatchExtraction
    assert "1, 2, 3, 4" in calls[1]["input"][0]["content"][0]["text"]


@pytest.mark.asyncio
async def test_same_observations_produce_same_canonical_hash() -> None:
    first = await CttResponsesExtractor(FakeClient(_responses())).extract(
        _pages(),
        _layout(),
        document_sha256=DOCUMENT_HASH,
    )
    second = await CttResponsesExtractor(FakeClient(_responses())).extract(
        _pages(),
        _layout(),
        document_sha256=DOCUMENT_HASH,
    )

    assert first.draft.canonical_hash() == second.draft.canonical_hash()


@pytest.mark.asyncio
async def test_slot_mismatch_fails_closed() -> None:
    batches = _slot_batches()
    batches[0] = CttSlotBatchExtraction(slots=[_player(1), _player(2), _player(3)])
    extractor = CttResponsesExtractor(FakeClient(_responses(batches=batches)))

    with pytest.raises(CttResponsesProtocolError, match="slot response mismatch"):
        await extractor.extract(
            _pages(),
            _layout(),
            document_sha256=DOCUMENT_HASH,
        )


@pytest.mark.asyncio
async def test_unparsed_or_unidentified_response_fails_closed() -> None:
    missing_parse = FakeClient([SimpleNamespace(id="resp-1", output_parsed=None)])
    with pytest.raises(CttResponsesProtocolError, match="parsed structured output"):
        await CttResponsesExtractor(missing_parse).extract(
            _pages(),
            _layout(),
            document_sha256=DOCUMENT_HASH,
        )

    missing_id = FakeClient([SimpleNamespace(id="", output_parsed=_header())])
    with pytest.raises(CttResponsesProtocolError, match="contain an id"):
        await CttResponsesExtractor(missing_id).extract(
            _pages(),
            _layout(),
            document_sha256=DOCUMENT_HASH,
        )


@pytest.mark.asyncio
async def test_layout_must_materialize_all_twenty_slots() -> None:
    layout = _layout()
    del layout["pages"]["back"]["cards"]["jugador_20"]
    client = FakeClient(_responses())

    with pytest.raises(ValueError, match="slots 1 through 20"):
        await CttResponsesExtractor(client).extract(
            _pages(),
            layout,
            document_sha256=DOCUMENT_HASH,
        )
    assert client.responses.calls == []


@pytest.mark.asyncio
async def test_page_count_and_header_layout_are_strict() -> None:
    extractor = CttResponsesExtractor(FakeClient(_responses()))
    with pytest.raises(ValueError, match="exactly two pages"):
        await extractor.extract(
            _pages()[:1],
            _layout(),
            document_sha256=DOCUMENT_HASH,
        )

    with pytest.raises(ValueError, match="lowercase SHA-256"):
        await extractor.extract(
            _pages(),
            _layout(),
            document_sha256="INVALID",
        )
    assert extractor.client.responses.calls == []

    layout = _layout()
    del layout["pages"]["front"]["header_fields"]["municipio"]
    with pytest.raises(ValueError, match="missing header field municipio"):
        await extractor.extract(
            _pages(),
            layout,
            document_sha256=DOCUMENT_HASH,
        )


def test_raw_schemas_reject_duplicate_slots_and_extra_fields() -> None:
    with pytest.raises(ValidationError, match="duplicate slot numbers"):
        CttSlotBatchExtraction(slots=[_player(1), _player(1)])
    with pytest.raises(ValidationError, match="Extra inputs"):
        CttRawField.model_validate(
            {
                "raw_text": "Alma",
                "confidence": 0.9,
                "candidates": [],
                "normalized_value": "not accepted",
            }
        )


def test_constructor_rejects_invalid_configuration() -> None:
    assert CttResponsesExtractor(FakeClient([])).model == "gpt-5.6-terra"
    with pytest.raises(ValueError, match="model cannot be empty"):
        CttResponsesExtractor(FakeClient([]), model=" ")
    with pytest.raises(ValueError, match="must be positive"):
        CttResponsesExtractor(FakeClient([]), timeout_seconds=0)
    with pytest.raises(ValueError, match="api_key cannot be empty"):
        CttResponsesExtractor.from_api_key(" ")
