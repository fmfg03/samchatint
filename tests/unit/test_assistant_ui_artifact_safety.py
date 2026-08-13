from pathlib import Path

ARTIFACT = Path("artifacts/rqf-053b-assistant-step-trace-sources/Assistant.tsx")


def _source() -> str:
    return ARTIFACT.read_text()


def test_assistant_ui_does_not_persist_provider_keys_or_accept_url_keys():
    source = _source()
    assert "OPENAI_KEY_STORAGE_KEY" not in source
    assert "localStorage.setItem(OPENAI_KEY_STORAGE_KEY" not in source
    assert "localStorage.getItem(OPENAI_KEY_STORAGE_KEY" not in source
    assert "setSessionOpenAiKey(normalized)" not in source
    assert "las API keys no se aceptan por URL" in source
    assert 'url.searchParams.delete("openai_api_key")' in source
    assert 'url.searchParams.delete("openai_key")' in source


def test_assistant_ui_validates_persisted_mode_before_use():
    source = _source()
    assert "SUPPORTED_ASSISTANT_MODES" in source
    assert "function normalizeAssistantMode" in source
    assert "normalizeAssistantMode(localStorage.getItem(ASSISTANT_MODE_STORAGE_KEY))" in source
    assert "(localStorage.getItem(ASSISTANT_MODE_STORAGE_KEY) as AssistantMode)" not in source


def test_assistant_ui_export_intent_requires_explicit_export_verb():
    source = _source()
    assert "const explicitExport" in source
    assert "if (!explicitExport) return null" in source
    assert 'text.includes(" en pdf")' not in source
    assert 'text.includes(" en excel")' not in source
    assert "Do not replay authenticated or mutating calls after non-404 failures" in source
    assert "Export/download POSTs must not replay after non-404 failures" in source
