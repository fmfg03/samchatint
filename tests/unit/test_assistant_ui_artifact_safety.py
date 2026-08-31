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
RUNTIME_DEPLOY_053H = Path(
    "artifacts/rqf-053h-assistant-ui-runtime-deploy"
)
CREDENTIAL_HARDENING_053H = Path(
    "artifacts/rqf-053h-assistant-credential-hardening"
)


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


def test_assistant_ui_revamp_artifact_distinguishes_exec_errors():
    source = _source_053h()
    readme = README_053H.read_text(encoding="utf-8")

    assert "const [execError, setExecError]" in source
    assert "setExecError(null)" in source
    assert "setExecError(String(e))" in source
    assert "No se pudo cargar el panel ejecutivo" in source
    assert "alerts?.alerts?.length ? (" in source
    assert "!execLoading ? (" in source
    assert source.index("No se pudo cargar el panel ejecutivo") < source.index(
        "Sin alertas activas para el filtro actual."
    )
    assert "RQF-053B-FU6 executive dashboard error state" in readme


def test_assistant_ui_revamp_artifact_rejects_provider_key_intake():
    source = _source_053h()
    readme = README_053H.read_text(encoding="utf-8")

    assert "OPENAI_KEY_STORAGE_KEY" not in source
    legacy_set = "localStorage.setItem(LEGACY_PROVIDER_CREDENTIAL_STORAGE"
    legacy_get = "localStorage.getItem(LEGACY_PROVIDER_CREDENTIAL_STORAGE"
    assert legacy_set not in source
    assert legacy_get not in source
    assert "setSessionOpenAiKey" not in source
    assert 'url.searchParams.get("openai_api_key")' not in source
    assert 'url.searchParams.get("openai_key")' not in source
    assert 'url.searchParams.has("openai_api_key")' in source
    assert 'url.searchParams.has("openai_key")' in source
    legacy_remove = (
        "localStorage.removeItem(LEGACY_PROVIDER_CREDENTIAL_STORAGE)"
    )
    assert legacy_remove in source
    assert 'url.searchParams.delete("openai_api_key")' in source
    assert 'url.searchParams.delete("openai_key")' in source
    assert "X-OpenAI-API-Key" not in source
    assert "Las API keys no se aceptan por URL" in source
    assert "function normalizeAssistantMode" in source
    assert "SUPPORTED_ASSISTANT_MODES" in source
    assert "RQF-053H credential-surface hardening" in readme


def test_assistant_ui_revamp_artifact_documents_static_asset_rollback():
    readme = README_053H.read_text(encoding="utf-8")

    static_dist = "/srv/samchat/current/goal-fest-page/dist"
    static_assets = f"{static_dist}/assets"

    assert "## Rollback Notes" in readme
    assert static_dist in readme
    assert static_assets in readme
    assert "find /srv/samchat/current/goal-fest-page/dist/assets" in readme
    assert "cp -a /srv/samchat/current/goal-fest-page/dist" in readme
    assert "rsync -a --delete" in readme
    assert "/healthz" in readme
    assert "/readyz" in readme
    assert "bundle markers" in readme


def test_assistant_ui_revamp_artifact_keeps_rag_admin_out_of_assistant():
    source = _source_053h()
    readme = README_053H.read_text(encoding="utf-8")

    forbidden_labels = [
        "Ingestar",
        "Auto tune",
        "Reset config",
        "Guardar configuracion",
        "Guardar configuración",
    ]

    assert 'href="/RAG"' in source
    assert "RAG movido a página dedicada" in source
    assert "RQF-053B-FU7 RAG ownership note" in readme
    for label in forbidden_labels:
        assert label not in source


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


def test_assistant_ui_runtime_deploy_receipt_captures_053h_assets():
    readme = (RUNTIME_DEPLOY_053H / "README.md").read_text(encoding="utf-8")
    predeploy = (RUNTIME_DEPLOY_053H / "predeploy-assets.txt").read_text(
        encoding="utf-8"
    )
    postdeploy = (RUNTIME_DEPLOY_053H / "postdeploy-assets.txt").read_text(
        encoding="utf-8"
    )

    assert "DEPLOYED_STATIC_ASSETS" in readme
    assert "Assistant-BxnXzIWk.js" in predeploy
    assert "Assistant-Cj-wzq_B.js" in postdeploy
    assert "Assistant-Cj-wzq_B.js" in readme
    assert "Cargando historial" in readme
    assert "No se pudo cargar el panel ejecutivo" in readme
    assert "external_session_id" in readme
    assert "/healthz" in readme
    assert "/readyz" in readme
    assert "dist.rollback-20260831-rqf053h" in readme
    assert "rsync -a --delete" in readme


def test_assistant_ui_credential_hardening_receipt_captures_runtime():
    readme = (CREDENTIAL_HARDENING_053H / "README.md").read_text(
        encoding="utf-8"
    )
    predeploy = (
        CREDENTIAL_HARDENING_053H / "predeploy-assets.txt"
    ).read_text(encoding="utf-8")
    postdeploy = (
        CREDENTIAL_HARDENING_053H / "postdeploy-assets.txt"
    ).read_text(encoding="utf-8")

    assert "DEPLOYED_STATIC_ASSETS" in readme
    assert "Assistant-Cj-wzq_B.js" in predeploy
    assert "Assistant-DM-K94_6.js" in postdeploy
    assert "Assistant-DM-K94_6.js" in readme
    assert "Las API keys no se aceptan por URL" in readme
    assert "X-OpenAI-API-Key" in readme
    assert "Verified absent from the served assistant asset" in readme
    assert "Expected remaining literals" in readme
    assert "dist.rollback-20260831-rqf053h-credentials" in readme
    assert "backend still accepts `X-OpenAI-API-Key`" in readme
