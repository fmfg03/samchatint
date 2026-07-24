import io
from types import SimpleNamespace

import pytest
from PIL import Image

from devnous.tournaments.core.operations_module import OperationsModule


def _image_bytes():
    stream = io.BytesIO()
    Image.new("RGB", (64, 96), "white").save(stream, format="JPEG")
    return stream.getvalue()


def test_ocr_provider_environment_overrides_yaml(monkeypatch):
    monkeypatch.setenv("OCR_PROVIDER", "chandra_shadow")
    module = OperationsModule(
        tournament_id="copa_telmex",
        config={"operations": {"ocr_enabled": True, "ocr_provider": "openai"}},
        db=None,
    )
    assert module.ocr_provider == "chandra_shadow"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["chandra_shadow", "chandra_primary"])
async def test_chandra_first_page_uses_openai_only_for_governed_precapture(
    monkeypatch, mode
):
    monkeypatch.delenv("OCR_PROVIDER", raising=False)
    module = OperationsModule(
        tournament_id="copa_telmex",
        config={"operations": {"ocr_enabled": True, "ocr_provider": mode}},
        db=None,
    )
    calls = []

    async def fake_single_provider(**kwargs):
        calls.append(kwargs)
        return {"text": "pre-capture only"}

    monkeypatch.setattr(module, "_ocr_single_provider", fake_single_provider)
    result = await module.process_ocr_registration(
        SimpleNamespace(chat_id=10, user_id=20, photo=_image_bytes())
    )

    assert result == {"text": "pre-capture only"}
    assert calls[0]["provider"] == "openai"
    assert module.ocr_provider == mode


def test_combined_provider_preserves_reviewed_front_page_values() -> None:
    module = OperationsModule(
        tournament_id="copa_telmex",
        config={"operations": {"ocr_enabled": True}},
        db=None,
    )
    base = {
        "team": {
            "name": "Equipo corregido",
            "state": "Michoacán",
            "municipality": "Tacámbaro",
            "confidence": 0.91,
        },
        "manager": {"name": "Representante corregido", "confidence": 0.9},
        "players": [
            {
                "name": "Jugador corregido",
                "birth_date": "01/02/2011",
                "confidence": 0.88,
                "needs_review": False,
            }
        ],
        "overall_confidence": 0.91,
    }
    provider = {
        "team": {
            "name": "Equipo OCR",
            "state": None,
            "municipality": "",
            "confidence": 0.6,
        },
        "manager": None,
        "players": [
            {"name": "Jugador OCR", "confidence": 0.7, "needs_review": True},
            {"name": "Jugador página 2", "confidence": 0.84},
        ],
        "overall_confidence": 0.7,
    }

    merged = module._merge_combined_provider_payload(base, provider)

    assert merged["team"]["name"] == "Equipo corregido"
    assert merged["team"]["state"] == "Michoacán"
    assert merged["team"]["municipality"] == "Tacámbaro"
    assert merged["manager"]["name"] == "Representante corregido"
    assert merged["players"][0]["name"] == "Jugador corregido"
    assert merged["players"][0]["birth_date"] == "01/02/2011"
    assert merged["players"][0]["needs_review"] is False
    assert merged["players"][1]["name"] == "Jugador página 2"
    assert merged["overall_confidence"] == 0.91
