from pathlib import Path

ARTIFACT_053A = Path(
    "artifacts/rqf-053a-assistant-workspace-cards-ui/Assistant.tsx"
)
ARTIFACT_053B = Path(
    "artifacts/rqf-053b-assistant-step-trace-sources/Assistant.tsx"
)
ARTIFACT_053H = Path("artifacts/rqf-053h-assistant-ui-revamp/Assistant.tsx")
README_053A = Path("artifacts/rqf-053a-assistant-workspace-cards-ui/README.md")
README_053B = Path("artifacts/rqf-053b-assistant-step-trace-sources/README.md")
README_053H = Path("artifacts/rqf-053h-assistant-ui-revamp/README.md")


def _source() -> str:
    return ARTIFACT_053B.read_text(encoding="utf-8")


def _source_053a() -> str:
    return ARTIFACT_053A.read_text(encoding="utf-8")


def _source_053h() -> str:
    return ARTIFACT_053H.read_text(encoding="utf-8")


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
    normalized_mode_call = (
        "normalizeAssistantMode(localStorage.getItem("
        "ASSISTANT_MODE_STORAGE_KEY))"
    )
    raw_mode_cast = (
        "(localStorage.getItem(ASSISTANT_MODE_STORAGE_KEY) as AssistantMode)"
    )
    assert "SUPPORTED_ASSISTANT_MODES" in source
    assert "function normalizeAssistantMode" in source
    assert normalized_mode_call in source
    assert raw_mode_cast not in source


def test_assistant_ui_export_intent_requires_explicit_export_verb():
    source = _source()
    assert "const explicitExport" in source
    assert "if (!explicitExport) return null" in source
    assert 'text.includes(" en pdf")' not in source
    assert 'text.includes(" en excel")' not in source
    assert (
        "Do not replay authenticated or mutating calls after non-404 failures"
        in source
    )
    export_no_replay = (
        "Export/download POSTs must not replay after non-404 failures"
    )
    assert export_no_replay in source


def test_assistant_workspace_cards_ui_artifact_renders_read_only_cards():
    source = _source_053a()

    assert "function workspaceCardsFromMessage" in source
    assert "message.tool_payload?.workspace_cards" in source
    assert "traceRecord.specialist_preview_surface" in source
    assert "workspace_cards" in source
    assert "Frontera de autoridad" in source
    assert "Autoridad:" in source


def test_assistant_step_trace_and_sources_ui_artifact_contract():
    source = _source()

    assert "function workspaceStepsFromMessage" in source
    assert "function workspaceSourcesFromMessage" in source
    assert "Pasos de trabajo" in source
    assert "Fuentes usadas" in source
    assert "specialist_preview_surface" in source
    assert "step_trace" in source
    assert "source_panel" in source


def test_assistant_ui_revamp_artifact_hydrates_persisted_history():
    source = _source_053h()
    readme = README_053H.read_text(encoding="utf-8")

    assert "type PersistedChatMessage" in source
    assert "function chatMessageFromHistoryRecord" in source
    assert "function loadConversationMessages" in source
    assert "external_session_id: assistantExternalSessionId" in source
    assert "chatMessageFromHistoryRecord(row)" in source
    assert "tool_payload: toolPayload" in source
    assert "preview_render: previewFromPayload(toolPayload)" in source
    assert "No se pudo cargar el historial" in source
    assert "Cargando historial" in source
    assert "RQF-053B-FU4 history hydration" in readme


def test_assistant_ui_artifacts_document_external_deploy_boundary():
    readme_053a = README_053A.read_text(encoding="utf-8")
    readme_053b = README_053B.read_text(encoding="utf-8")

    assert "IMPLEMENTED_DEPLOYED_STATIC_ASSETS" in readme_053a
    assert "IMPLEMENTED_DEPLOYED_STATIC_ASSETS" in readme_053b
    active_assistant_path = (
        "/srv/samchat/archive/projects/goal-fest-page/src/pages/Assistant.tsx"
    )
    assert active_assistant_path in readme_053a
    assert active_assistant_path in readme_053b
    assert "No write execution" in readme_053a
    assert "No writes" in readme_053b
