from samchat.assistant.router import (
    _assistant_response_language_prompt,
    _assistant_classify_request,
    _assistant_hermes_profile_prompt,
    _assistant_inference_plan,
    _assistant_model,
    _assistant_provider_order,
    _assistant_tool_defs,
    _assistant_verify_sensitive_operation,
    _conversation_external_session_id,
    _conversation_module_key,
    _memory_text_overlap_score,
    _parse_assistant_verification_response,
    _sanitize_ollama_content,
    _scope_from_module_key,
    _source_matches_scope,
    _source_scope,
    _update_conversation_context,
    _verification_safe_answer,
    _write_requires_verification,
)
import asyncio
from types import SimpleNamespace
import pytest


def test_assistant_provider_order_defaults_to_ollama_for_low_cost_modes(monkeypatch):
    monkeypatch.delenv("ASSISTANT_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("ASSISTANT_LLM_PROVIDER_LOW", raising=False)
    order = _assistant_provider_order(
        "ahorro",
        route_info={"route": "lookup_sql", "domain": "finance"},
        capability="chat",
    )
    assert order[0] == "ollama"
    assert "anthropic" in order


def test_assistant_provider_order_prefers_remote_for_high_risk_quality(monkeypatch):
    monkeypatch.delenv("ASSISTANT_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("ASSISTANT_LLM_PROVIDER_HIGH", raising=False)
    order = _assistant_provider_order(
        "calidad",
        route_info={"route": "agentic_write", "domain": "finance"},
        capability="chat",
    )
    assert order[:2] == ["anthropic", "openai"]
    assert order[-1] == "ollama"


def test_assistant_provider_order_honors_explicit_ollama_override(monkeypatch):
    monkeypatch.setenv("ASSISTANT_LLM_PROVIDER_BALANCED", "ollama_only")
    order = _assistant_provider_order(
        "balanceado",
        route_info={"route": "reporting", "domain": "finance"},
        capability="chat",
    )
    assert order == ["ollama"]


def test_assistant_provider_order_can_force_local_only_by_module(monkeypatch):
    monkeypatch.setenv("ASSISTANT_LLM_PROVIDER_MODULE_LOCAL_ONLY", "assistant.general,operations.rag")
    order = _assistant_provider_order(
        "calidad",
        route_info={"route": "reporting", "domain": "generic", "module_key": "assistant.general"},
        capability="chat",
    )
    assert order == ["ollama"]


def test_assistant_provider_order_blocks_remote_without_explicit_escalation(monkeypatch):
    monkeypatch.delenv("ASSISTANT_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("ASSISTANT_LLM_PROVIDER_HIGH", raising=False)
    monkeypatch.setenv("ASSISTANT_REMOTE_ESCALATION_MODE", "explicit")
    monkeypatch.setenv("ASSISTANT_REMOTE_ESCALATION_ROUTES", "code_agentic,agentic_write")
    order = _assistant_provider_order(
        "calidad",
        route_info={"route": "reporting", "domain": "finance", "module_key": "finance.general"},
        capability="chat",
    )
    assert order == ["ollama"]


def test_assistant_provider_order_allows_remote_for_explicit_high_risk_routes(monkeypatch):
    monkeypatch.setenv("ASSISTANT_LLM_PROVIDER_HIGH", "ollama_first")
    monkeypatch.setenv("ASSISTANT_REMOTE_ESCALATION_MODE", "explicit")
    monkeypatch.setenv("ASSISTANT_REMOTE_ESCALATION_ROUTES", "code_agentic,agentic_write")
    order = _assistant_provider_order(
        "calidad",
        route_info={"route": "code_agentic", "domain": "code", "module_key": "platform.panel"},
        capability="chat",
    )
    assert order[:3] == ["ollama", "anthropic", "openai"]


def test_route_remote_first_overrides_module_local_first(monkeypatch):
    monkeypatch.setenv("ASSISTANT_LLM_PROVIDER_MODULE_LOCAL_FIRST", "finance.")
    monkeypatch.setenv("ASSISTANT_LLM_PROVIDER_ROUTE_REMOTE_FIRST", "agentic_write")
    monkeypatch.setenv("ASSISTANT_REMOTE_ESCALATION_MODE", "explicit")
    monkeypatch.setenv("ASSISTANT_REMOTE_ESCALATION_ROUTES", "agentic_write")
    order = _assistant_provider_order(
        "calidad",
        route_info={"route": "agentic_write", "domain": "finance", "module_key": "finance.general"},
        capability="chat",
    )
    assert order[:3] == ["anthropic", "openai", "ollama"]


def test_assistant_inference_plan_exposes_local_fast_tier(monkeypatch):
    monkeypatch.delenv("ASSISTANT_LLM_PROVIDER", raising=False)
    plan = _assistant_inference_plan(
        {"route": "needs_clarification", "domain": "generic"},
        mode="ahorro",
    )
    assert plan["tier"] == "local_fast"
    assert plan["provider_order"][0] == "ollama"
    assert plan["planned_local_model"] == "qwen3:4b"


def test_ollama_model_defaults_to_local_qwen():
    assert _assistant_model("ollama", "ahorro") == "qwen3:4b"
    assert _assistant_model("ollama", "balanceado") == "qwen3:4b"


def test_ollama_model_can_route_by_request_type(monkeypatch):
    monkeypatch.setenv("OLLAMA_ASSISTANT_MODEL_LOOKUP", "qwen3:1.7b")
    monkeypatch.setenv("OLLAMA_ASSISTANT_MODEL_REPORTING", "qwen3:8b")
    assert _assistant_model("ollama", "ahorro", route_info={"route": "lookup_sql"}) == "qwen3:1.7b"
    assert _assistant_model("ollama", "balanceado", route_info={"route": "reporting"}) == "qwen3:8b"


def test_ollama_model_uses_light_code_model_for_analysis(monkeypatch):
    monkeypatch.setenv("OLLAMA_ASSISTANT_MODEL_CODE_LIGHT", "qwen3:4b")
    monkeypatch.setenv("OLLAMA_ASSISTANT_MODEL_CODE", "qwen3:8b")
    assert _assistant_model("ollama", "calidad", route_info={"route": "code_agentic"}) == "qwen3:4b"


def test_ollama_model_uses_heavy_code_model_for_changes_or_tools(monkeypatch):
    monkeypatch.setenv("OLLAMA_ASSISTANT_MODEL_CODE_LIGHT", "qwen3:4b")
    monkeypatch.setenv("OLLAMA_ASSISTANT_MODEL_CODE", "qwen3:8b")
    assert (
        _assistant_model(
            "ollama",
            "calidad",
            route_info={"route": "code_agentic", "has_code_change_intent": True},
        )
        == "qwen3:8b"
    )
    assert (
        _assistant_model(
            "ollama",
            "calidad",
            route_info={"route": "code_agentic", "code_tooling_active": True},
        )
        == "qwen3:8b"
    )


def test_assistant_classifier_does_not_treat_reporte_as_repo():
    route = _assistant_classify_request(
        "Dame un reporte ejecutivo de pagos pendientes y hallazgos por proveedor."
    )
    assert route["route"] == "reporting"
    assert route["domain"] == "finance"


def test_assistant_classifier_still_detects_real_repo_queries():
    route = _assistant_classify_request(
        "Revisa el repo frontend y el endpoint del asistente."
    )
    assert route["route"] == "code_agentic"
    assert route["domain"] == "code"


def test_assistant_classifier_detects_code_change_intent():
    route = _assistant_classify_request(
        "Arregla el bug del login en el frontend y aplica el patch."
    )
    assert route["route"] == "code_agentic"
    assert route["has_code_change_intent"] is True
    assert route["has_write_intent"] is True


def test_assistant_classifier_routes_accounting_reports_to_reporting():
    route = _assistant_classify_request(
        "Genera el libro diario y la balanza de marzo 2026 para contabilidad."
    )
    assert route["route"] == "reporting"
    assert route["domain"] == "finance"


def test_assistant_classifier_delegates_finance_strategy_to_hermes():
    route = _assistant_classify_request(
        "Define una estrategia fiscal y financiera para mejorar flujo de efectivo y tesoreria."
    )
    assert route["route"] == "reporting"
    assert route["domain"] == "finance"
    assert route["delegate_to_hermes"] is True
    assert route["hermes_profile"] == "finance_strategy"


def test_assistant_classifier_detects_accounting_write_intent():
    route = _assistant_classify_request(
        "Contabiliza el gasto REF-123 y asigna la cuenta contable correcta."
    )
    assert route["route"] == "agentic_write"
    assert route["domain"] == "finance"
    assert route["has_write_intent"] is True


def test_assistant_classifier_detects_poliza_generation_as_write():
    route = _assistant_classify_request(
        "Genera la póliza contable del gasto REF-123."
    )
    assert route["route"] == "agentic_write"
    assert route["domain"] == "finance"
    assert route["has_write_intent"] is True


def test_sanitize_ollama_content_strips_thinking_traces():
    raw = (
        'Estoy razonando internamente.\n</think>\n\n'
        "Respuesta final para el usuario."
    )
    assert _sanitize_ollama_content(raw) == "Respuesta final para el usuario."


def test_sanitize_ollama_content_strips_spurious_leading_label():
    raw = "özellik: Respuesta final en espanol."
    assert _sanitize_ollama_content(raw) == "Respuesta final en espanol."


def test_assistant_response_language_prompt_pins_spanish_for_spanish_query():
    prompt = _assistant_response_language_prompt(
        "Cuanto hemos gastado en balones en 2025 y 2026?"
    )
    assert "espanol" in prompt.lower()
    assert "vietnamita" in prompt.lower()


def test_update_conversation_context_stores_module_key():
    conversation = SimpleNamespace(
        tournament_key=None,
        metadata_={"external_session_id": "hermes:finance:main"},
    )
    _update_conversation_context(
        conversation=conversation,
        tournament_key="copa_telmex",
        module_key="finance.dashboard",
        module_label="Finance Dashboard",
        module_context={"tab": "overview"},
    )
    assert conversation.tournament_key == "copa_telmex"
    assert _conversation_module_key(conversation) == "finance.dashboard"
    assert _conversation_external_session_id(conversation) == "hermes:finance:main"
    assert conversation.metadata_["module_label"] == "Finance Dashboard"
    assert conversation.metadata_["module_context"]["tab"] == "overview"


def test_scope_from_module_key_maps_known_domains():
    assert _scope_from_module_key("finance") == "finance"
    assert _scope_from_module_key("finance.dashboard") == "finance"
    assert _scope_from_module_key("tournaments") == "tournament"
    assert _scope_from_module_key("torneos.ops") == "tournament"
    assert _scope_from_module_key("platform.panel") == "code"
    assert _scope_from_module_key("assistant.general") == "generic"


def test_hermes_finance_strategy_profile_forces_anthropic():
    order = _assistant_provider_order(
        "calidad",
        route_info={
            "route": "reporting",
            "domain": "finance",
            "hermes_profile": "finance_strategy",
        },
        capability="chat",
    )
    assert order == ["anthropic"]


def test_hermes_finance_strategy_profile_prompt_mentions_strategy_scope():
    prompt = _assistant_hermes_profile_prompt(
        {"hermes_profile": "finance_strategy"}
    )
    assert prompt is not None
    assert "estrategia contable, fiscal y financiera" in prompt.lower()
    assert "no ejecutes escrituras" in prompt.lower()


def test_finance_reporting_tools_include_strategy_snapshot():
    tool_defs = _assistant_tool_defs({"route": "reporting", "domain": "finance"})
    tool_names = {tool["function"]["name"] for tool in tool_defs}
    assert "finance_strategy_snapshot" in tool_names
    assert "finance_alerts_scan" in tool_names


def test_hermes_finance_strategy_profile_uses_curated_tool_set():
    tool_defs = _assistant_tool_defs(
        {
            "route": "reporting",
            "domain": "finance",
            "hermes_profile": "finance_strategy",
        }
    )
    tool_names = {tool["function"]["name"] for tool in tool_defs}
    assert "finance_strategy_snapshot" in tool_names
    assert "finance_alerts_scan" in tool_names
    assert "db_read_universal" not in tool_names


def test_source_scope_detects_finance_and_tournament_sources():
    assert (
        _source_scope(
            "/root/samchat/reports/accounting_knowledge/plataforma_sports_q1_2026/balanzas_q1_2026_practicas_contables.md"
        )
        == "finance"
    )
    assert (
        _source_scope(
            "/root/samchat/reports/tournaments_ai/copa-telmex-2026/national/finance.json"
        )
        == "tournament"
    )
    assert _source_scope("/root/samchat/docs/integrations/hermes_samchat_assistant.md") == "generic"


def test_source_matches_scope_rejects_cross_domain_sources():
    finance_source = (
        "/root/samchat/reports/accounting_knowledge/plataforma_sports_q1_2026/"
        "balanzas_q1_2026_practicas_contables.md"
    )
    tournament_source = (
        "/root/samchat/reports/tournaments_ai/copa-telmex-2026/national/finance.json"
    )
    assert _source_matches_scope(source=finance_source, scope="finance") is True
    assert _source_matches_scope(source=finance_source, scope="tournament") is False
    assert _source_matches_scope(source=tournament_source, scope="tournament") is True
    assert _source_matches_scope(source=tournament_source, scope="finance") is False
    assert (
        _source_matches_scope(
            source="/root/samchat/docs/integrations/hermes_samchat_assistant.md",
            scope="finance",
        )
        is True
    )


def test_memory_text_overlap_score_prefers_matching_terms():
    assert _memory_text_overlap_score(["proveedor", "pagos"], "Pagos del proveedor ACME") > 0.5
    assert _memory_text_overlap_score(["nomina"], "calendario de torneos") == 0.0


def test_write_requires_verification_for_sensitive_writes():
    assert _write_requires_verification("dev_file_write", {}) is True
    assert _write_requires_verification("db_write_universal", {"action": "update", "max_affected": 5}) is True
    assert _write_requires_verification("finance_expense_post_accounting", {}) is True
    assert _write_requires_verification("finance_expense_create", {}) is False
    assert _write_requires_verification("assistant_save_artifact", {}) is False


def test_parse_assistant_verification_response_reads_json_payload():
    parsed = _parse_assistant_verification_response(
        '{"verdict":"fail","summary":"scope mismatch","blockers":["expense_id missing"],"warnings":[]}'
    )
    assert parsed["verdict"] == "fail"
    assert parsed["summary"] == "scope mismatch"
    assert parsed["blockers"] == ["expense_id missing"]


def test_parse_assistant_verification_response_defaults_to_partial_on_unstructured_text():
    parsed = _parse_assistant_verification_response(
        "No pude verificar por completo la operación."
    )
    assert parsed["verdict"] == "partial"
    assert "no pudo certificar" in parsed["summary"].lower()


def test_verification_safe_answer_degrades_explanation():
    answer = _verification_safe_answer(
        tool_name="dev_file_write",
        tool_result={"updated": True, "path": "src/app.py"},
        verification={
            "verdict": "fail",
            "summary": "La explicación omitió el archivo afectado.",
            "blockers": ["Respuesta demasiado vaga"],
            "warnings": [],
        },
    )
    assert "dev_file_write" in answer
    assert "veredicto de verificación: fail" in answer.lower()
    assert "resultado base" in answer.lower()


def test_assistant_verify_sensitive_operation_uses_structured_parser(monkeypatch):
    async def fake_history_messages(*args, **kwargs):
        return [{"role": "user", "content": "Actualiza el gasto REF-123"}]

    async def fake_text_response(**kwargs):
        return {
            "provider": "anthropic",
            "model": "test-model",
            "answer": '{"verdict":"pass","summary":"validated","blockers":[],"warnings":["audit trail ok"]}',
            "meta": {"source": "test"},
        }

    monkeypatch.setattr("samchat.assistant.router._history_messages", fake_history_messages)
    monkeypatch.setattr("samchat.assistant.router._assistant_text_response", fake_text_response)

    verification = asyncio.run(
        _assistant_verify_sensitive_operation(
            phase="pre_write",
            tool_name="finance_expense_post_accounting",
            tool_args={"expense_id": "REF-123"},
            conversation_id="00000000-0000-0000-0000-000000000000",
            session=None,
            assistant_mode="calidad",
            openai_api_key=None,
        )
    )

    assert verification["verdict"] == "pass"
    assert verification["provider"] == "anthropic"
    assert verification["model"] == "test-model"
    assert verification["warnings"] == ["audit trail ok"]
