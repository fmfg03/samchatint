from devnous.tournaments.core.ctt_ocr_provider import CttOcrMode
from devnous.tournaments.core.ctt_provider_runtime import configured_ctt_ocr_mode


def test_runtime_mode_uses_only_supported_provider_states(monkeypatch):
    monkeypatch.setenv("OCR_PROVIDER", "chandra_shadow")
    assert configured_ctt_ocr_mode() == CttOcrMode.CHANDRA_SHADOW
    monkeypatch.setenv("OCR_PROVIDER", "local_first")
    assert configured_ctt_ocr_mode() == CttOcrMode.OPENAI


def test_explicit_runtime_mode_precedes_environment(monkeypatch):
    monkeypatch.setenv("OCR_PROVIDER", "chandra_primary")
    assert configured_ctt_ocr_mode("openai") == CttOcrMode.OPENAI
