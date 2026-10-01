"""Context transport and authority regression tests; no live LLM or production UAT."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from samchat.assistant.conversation_context import (
    context_digest,
    interpretation_text,
    update_context,
)
from samchat.assistant.turn_service import build_turn_messages

QUESTION = (
    "Con base en las solicitudes de pago e informes de gastos ingresados y ya "
    "aprobados durante septiembre 2026, dime cuánto IVA ha pagado Plataforma "
    "Sports a sus proveedores?"
)


def conversation():
    return SimpleNamespace(id="one", metadata_={}, tournament_key=None)


def test_context_digest_isolates_identity_history_and_ui():
    base = dict(
        conversation_id="one",
        history=[{"role": "user", "content": QUESTION}],
        metadata={"module_context": {"company": "Plataforma Sports"}},
    )
    digest = context_digest(**base)
    for key, value in (("conversation_id", "two"), ("history", []), ("metadata", {})):
        assert context_digest(**{**base, key: value}) != digest
    assert QUESTION not in digest


def test_ui_snapshot_replaces_filters_and_preserves_unrelated_case():
    conv = conversation()
    conv.metadata_ = {"active_case": "case-1"}
    update_context(
        conv,
        module_key="finance",
        tournament_key="one",
        module_context={"supplier": "A", "month": 9},
        bi_year=2026,
        bi_scope="all",
        bi_segment="national",
    )
    update_context(conv)
    assert conv.metadata_["bi_filters"]["bi_year"] == 2026
    update_context(conv, module_context={"supplier": "B"}, bi_year=2025)
    assert conv.metadata_["module_context"] == {"supplier": "B"}
    assert conv.metadata_["bi_filters"] == {"bi_year": 2025}
    update_context(conv, module_key="registrations")
    assert "module_context" not in conv.metadata_
    assert conv.metadata_["bi_filters"] == {}
    assert conv.tournament_key is None
    assert conv.metadata_["active_case"] == "case-1"


def test_empty_ui_snapshot_clears_and_explicit_all_is_preserved():
    conv = conversation()
    update_context(conv, module_context={"month": 9}, bi_scope="national")
    update_context(conv, module_context={}, bi_scope="all", tournament_key="")
    assert conv.metadata_["module_context"] == {}
    assert conv.metadata_["bi_filters"] == {"bi_scope": "all"}
    assert conv.tournament_key is None


@pytest.mark.asyncio
async def test_followup_sequence_gets_full_history_once_and_untrusted_ui():
    history = []
    for question in (QUESTION, "¿y agosto?", "por proveedor", "¿por qué aumentó?"):
        loader = AsyncMock(side_effect=AssertionError("must use pre-write snapshot"))
        messages = await build_turn_messages(
            session=None,
            conversation_id="one",
            raw_message=question,
            route_prompt="route",
            language_prompt="es",
            hermes_profile_prompt=None,
            workspace_context=None,
            module_key_default="finance",
            module_label_default="Finanzas",
            module_context_default='{"company": "Plataforma Sports"}',
            retrieval_context=None,
            assistant_system_prompt=lambda: "system",
            history_messages=loader,
            history_snapshot=list(history),
            filter_context={"bi_year": 2026},
        )
        assert messages[-1] == {"role": "user", "content": question}
        assert sum(m["content"] == question for m in messages) == 1
        assert all(m in messages for m in history)
        assert QUESTION in interpretation_text(question, history)
        assert any(m["role"] == "user" and "company" in m["content"] for m in messages)
        assert not any(
            m["role"] == "system" and '"company"' in m["content"] for m in messages
        )
        history.extend(
            [
                {"role": "user", "content": question},
                {"role": "assistant", "content": "Necesito evidencia de pago."},
            ]
        )


@pytest.mark.asyncio
async def test_vat_capability_is_not_zero_or_sample_aggregate():
    from samchat.assistant.finance_read_adapter import run_finance_read_adapter

    session = AsyncMock()
    result = await run_finance_read_adapter(
        session, intent="finance.vat_paid", year=2026, month=8
    )
    assert result["status"] == "capability_unavailable"
    assert result["amount"] is None
    assert result["coverage"] == "not_queried"
    assert result["period"] == {"year": 2026, "month": 8}
    assert (
        "shared_cfdi_deduplication_and_partial_payment_allocation"
        in result["missing_capabilities"]
    )
    assert not session.mock_calls


@pytest.mark.asyncio
@pytest.mark.parametrize("pending_variant", [False, True])
@pytest.mark.parametrize(
    "question", [QUESTION, "¿y agosto?", "por proveedor", "¿por qué aumentó?"]
)
async def test_general_questions_reach_contextual_loop(
    monkeypatch, pending_variant, question
):
    from samchat.assistant import conversation_service as service

    turn = AsyncMock(
        return_value=SimpleNamespace(
            assistant_message="Necesito distinguir documentos aprobados de pagos efectivamente realizados.",
            tool_trace=[],
            pending_confirmation=None,
        )
    )
    # Guards run first, but these fixtures have neither uploads nor pending actions.
    for name in (
        "_build_document_upload_response",
        "_build_document_confirmation_response",
        "_build_case_memory_response",
        "advance_receipt_draft",
    ):
        monkeypatch.setattr(service, name, AsyncMock(return_value=None))
    forbidden = AsyncMock(
        side_effect=AssertionError("keyword interceptor bypassed LLM")
    )
    monkeypatch.setattr(service, "_build_owner_variable_query_response", forbidden)
    monkeypatch.setattr(service, "_build_finance_direct_read_response", forbidden)
    kwargs = dict(
        raw_message=question,
        conversation=conversation(),
        current_empleado=SimpleNamespace(id="u"),
        session=AsyncMock(),
        request=None,
        tournament_key=None,
        bi_year=2026,
        bi_scope=None,
        bi_segment=None,
        assistant_mode=None,
        openai_api_key=None,
        assistant_turn=turn,
        maybe_append_export_prompt=lambda message, trace: message,
        contextual=True,
    )
    if pending_variant:
        kwargs.update(
            latest_pending_run_for_conversation=AsyncMock(return_value=None),
            is_explicit_approval_message=lambda text: False,
            is_explicit_rejection_message=lambda text: False,
            confirm_pending_run=AsyncMock(),
            deterministic_pending_builders=[],
            build_deterministic_pending_response=AsyncMock(),
        )
        await service.run_message_turn_with_pending(**kwargs)
    else:
        await service.run_conversation_turn(**kwargs)
    turn.assert_awaited_once()
    assert turn.call_args.kwargs["raw_message"] == question
    forbidden.assert_not_called()


def test_historical_write_cannot_upgrade_current_route():
    from samchat.assistant.conversation_context import contextual_route

    def classify(text):
        if "paga" in text:
            return {"route": "agentic_write", "domain": "finance"}
        return {"route": "lookup_sql", "domain": "generic"}

    route = contextual_route(
        "¿y agosto?", [{"role": "user", "content": "paga proveedor"}], classify
    )
    assert route == {"route": "lookup_sql", "domain": "finance"}


@pytest.mark.asyncio
async def test_pending_confirmation_still_precedes_contextual_interpretation(
    monkeypatch,
):
    from samchat.assistant.conversation_service import run_message_turn_with_pending

    turn = AsyncMock(
        side_effect=AssertionError("LLM must not authorize a pending write")
    )
    confirmed = SimpleNamespace(
        assistant_message="Confirmación gobernada registrada.", tool_trace=[]
    )
    confirm = AsyncMock(return_value=confirmed)
    await run_message_turn_with_pending(
        raw_message="/ok",
        conversation=conversation(),
        current_empleado=SimpleNamespace(id="u"),
        session=AsyncMock(),
        request=None,
        tournament_key=None,
        bi_year=None,
        bi_scope=None,
        bi_segment=None,
        assistant_mode=None,
        openai_api_key=None,
        latest_pending_run_for_conversation=AsyncMock(
            return_value=SimpleNamespace(id="pending")
        ),
        is_explicit_approval_message=lambda text: text == "/ok",
        is_explicit_rejection_message=lambda text: False,
        confirm_pending_run=confirm,
        deterministic_pending_builders=[],
        build_deterministic_pending_response=AsyncMock(),
        assistant_turn=turn,
        maybe_append_export_prompt=lambda message, trace: message,
        contextual=True,
    )
    confirm.assert_awaited_once()
    turn.assert_not_called()


@pytest.mark.asyncio
async def test_runtime_transports_snapshot_filters_and_isolates_cache(monkeypatch):
    import uuid
    from unittest.mock import MagicMock

    from samchat.assistant import router

    monkeypatch.setenv("ASSISTANT_RAG_ENABLED", "1")
    retrieval = AsyncMock(return_value={"context": None, "sources": []})
    monkeypatch.setattr(router, "_build_hybrid_retrieval", retrieval)
    monkeypatch.setenv("ASSISTANT_RESPONSE_CACHE_ENABLED", "1")
    monkeypatch.setattr(
        router, "_build_workspace_context", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        router, "_assistant_provider_order", lambda *a, **kw: ["openai"]
    )
    history = [
        {"role": "user", "content": QUESTION},
        {"role": "assistant", "content": "Necesito evidencia de pago."},
    ]
    loader = AsyncMock(return_value=history)
    monkeypatch.setattr(router, "_history_messages", loader)
    monkeypatch.setattr(
        router,
        "_assistant_response_cache_get",
        lambda key: (_ for _ in ()).throw(
            AssertionError("cache bypassed live permissions")
        ),
    )
    provider = AsyncMock(
        return_value=SimpleNamespace(assistant_message="Aclaración", tool_trace=[])
    )
    monkeypatch.setattr(router, "_execute_provider", provider)
    session = AsyncMock()
    session.add = MagicMock()
    conv = conversation()
    conv.id = uuid.uuid4()
    update_context(
        conv,
        module_key="finance",
        module_context={"company": "Plataforma Sports"},
        bi_year=2026,
        bi_scope="all",
        bi_segment="all",
    )
    kwargs = dict(
        raw_message="¿y agosto?",
        conversation=conv,
        current_empleado=SimpleNamespace(id=uuid.uuid4(), rol="admin"),
        session=session,
        tournament_key=None,
    )
    await router._assistant_turn(**kwargs)
    call = provider.call_args.kwargs
    assert call["bi_year"] == 2026
    assert call["route_info"]["domain"] == "finance"
    assert call["deterministic_tool_answer"] is None
    safe, trace = call["finalize_response"](
        "En agosto se pagaron 42.000 MXN de IVA.", []
    )
    assert "42.000" not in safe
    assert any(
        step.get("assistant_work_frame", {}).get("domain") == "finance"
        for step in trace
    )
    assert call["response_cache_enabled"] is False
    assert retrieval.call_args.kwargs["use_cache"] is False
    assert await call["history_messages"]() == history
    assert sum(m["content"] == "¿y agosto?" for m in call["messages"]) == 1
    assert all(m in call["messages"] for m in history)
    key_one = call["cache_key"]
    conv.id = uuid.uuid4()
    await router._assistant_turn(**kwargs)
    assert provider.call_args.kwargs["cache_key"] != key_one
    # A new writing task uses the real router and its service finalizer without
    # inheriting the financial evidence contract from history or current screen.
    await router._assistant_turn(**{**kwargs, "raw_message": "Redacta un correo de agradecimiento"})
    writing = provider.call_args.kwargs
    assert writing["route_info"]["domain"] == "generic"
    draft = "Hola, equipo. Les invito a nuestra reunión de trabajo. Saludos."
    safe, _ = writing["finalize_response"](draft, [])
    assert draft in safe
    loader.assert_awaited()
    # There are no business writes; only the user message is persisted by this layer.
    assert all(
        isinstance(c.args[0], router.AssistantMessage)
        for c in session.add.call_args_list
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("media", [False, True])
async def test_http_handlers_forward_contextual_mode_and_ui_snapshot(
    monkeypatch, media
):
    import io
    import uuid

    from starlette.datastructures import UploadFile

    from samchat.assistant import router

    conv = conversation()
    conv.id = uuid.uuid4()
    session = AsyncMock()
    employee = SimpleNamespace(id=uuid.uuid4(), rol="admin")
    monkeypatch.setattr(router, "_enforce_rate_limit", lambda **kw: None)
    monkeypatch.setattr(router, "_load_conversation", AsyncMock(return_value=conv))
    turn = AsyncMock(
        return_value=SimpleNamespace(assistant_message="ok", tool_trace=[])
    )
    common = dict(
        request=SimpleNamespace(headers={}),
        conversation_id=str(conv.id),
        current_empleado=employee,
        session=session,
    )
    if media:
        monkeypatch.setattr(router, "run_conversation_turn", turn)
        monkeypatch.setattr(
            router, "extract_text_from_media", AsyncMock(return_value=QUESTION)
        )
        await router.create_media_message(
            **common,
            kind="document",
            note=None,
            tournament_key=None,
            module_key="finance",
            module_label="Finanzas",
            module_context_json='{"company":"Plataforma Sports"}',
            bi_year=2026,
            bi_scope="all",
            bi_segment="all",
            assistant_mode=None,
            file=UploadFile(filename="question.txt", file=io.BytesIO(b"question")),
        )
    else:
        monkeypatch.setattr(router, "run_message_turn_with_pending", turn)
        await router.create_message(
            **common,
            payload=router.MessageCreateRequest(
                message=QUESTION,
                module_key="finance",
                module_context={"company": "Plataforma Sports"},
                bi_year=2026,
                bi_scope="all",
                bi_segment="all",
            ),
        )
    assert turn.call_args.kwargs["contextual"] is True
    assert conv.metadata_["bi_filters"]["bi_year"] == 2026
    assert conv.metadata_["module_context"]["company"] == "Plataforma Sports"


def test_unchanged_ui_snapshot_preserves_omitted_filters():
    conv = conversation()
    update_context(
        conv,
        module_key="finance",
        tournament_key="cup",
        module_context={"tab": "tax"},
        bi_year=2026,
        bi_scope="national",
    )
    update_context(
        conv,
        module_key=" Finance ",
        tournament_key=" CUP ",
        module_context={"tab": "tax"},
    )
    assert conv.metadata_["bi_filters"] == {"bi_year": 2026, "bi_scope": "national"}


@pytest.mark.parametrize("domain", ["tournament", "generic"])
def test_current_screen_domain_does_not_inherit_previous_finance(domain):
    from samchat.assistant.conversation_context import contextual_route

    def classify(text):
        return {
            "route": "lookup_sql",
            "domain": "finance" if "IVA" in text else "generic",
        }

    route = contextual_route(
        "¿y aquí?",
        [{"role": "user", "content": "IVA"}],
        classify,
        current_domain=domain,
    )
    assert route["domain"] == domain
    assert route["route"] == "lookup_sql"


@pytest.mark.asyncio
async def test_contextual_path_preserves_sufficiency_gate(monkeypatch):
    from samchat.assistant import conversation_service as service

    for name in (
        "_build_document_upload_response",
        "_build_document_confirmation_response",
        "_build_case_memory_response",
    ):
        monkeypatch.setattr(service, name, AsyncMock(return_value=None))
    result = await service.run_conversation_turn(
        raw_message=QUESTION,
        conversation=conversation(),
        current_empleado=SimpleNamespace(id="u"),
        session=AsyncMock(),
        request=None,
        tournament_key=None,
        bi_year=None,
        bi_scope=None,
        bi_segment=None,
        assistant_mode=None,
        openai_api_key=None,
        assistant_turn=AsyncMock(
            return_value=SimpleNamespace(
                assistant_message="Sin evidencia suficiente.", tool_trace=[]
            )
        ),
        maybe_append_export_prompt=lambda message, trace: message,
        contextual=True,
    )
    assert any(
        step.get("tool") == "assistant.response_sufficiency_gate"
        for step in result.tool_trace
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai", "anthropic", "ollama"])
@pytest.mark.parametrize("outcome", ["read", "confirm", "deny"])
async def test_provider_evidence_loop_and_authority(provider, outcome):
    import copy
    import inspect
    import json
    from unittest.mock import MagicMock

    from fastapi import HTTPException

    from samchat.assistant import provider_execution as execution

    tool = (
        "finance_expense_create" if outcome == "confirm" else "assistant_finance_read"
    )
    evidence = {
        "ok": True,
        "payload": {"amount": 42, "folio": "TEST-42", "coverage": "fixture"},
    }
    reader = AsyncMock(return_value=evidence)
    calls = []

    def sync_call(**kwargs):
        calls.append(copy.deepcopy(kwargs))
        first = len(calls) == 1
        if provider == "openai":
            choice = SimpleNamespace(
                content="" if first else "Síntesis con folio TEST-42.",
                tool_calls=(
                    [
                        SimpleNamespace(
                            id="call1",
                            function=SimpleNamespace(name=tool, arguments="{}"),
                        )
                    ]
                    if first
                    else []
                ),
            )
            return SimpleNamespace(choices=[SimpleNamespace(message=choice)])
        return SimpleNamespace(
            content=(
                [SimpleNamespace(type="tool_use", id="call1", name=tool, input={})]
                if first
                else [SimpleNamespace(type="text", text="Síntesis con folio TEST-42.")]
            )
        )

    async def ollama_call(**kwargs):
        calls.append(copy.deepcopy(kwargs))
        first = len(calls) == 1
        return {
            "content": "" if first else "Síntesis con folio TEST-42.",
            "tool_calls": (
                [{"function": {"name": tool, "arguments": {}}}] if first else []
            ),
        }

    session = AsyncMock()
    session.add = MagicMock()
    common = dict(
        model="mock",
        normalized_mode="balanceado",
        route_info={"route": "lookup_sql"},
        raw_message="consulta",
        conversation=conversation(),
        current_empleado=SimpleNamespace(id="u", rol="admin"),
        session=session,
        tool_trace=[],
        tool_defs=[],
        max_tokens=100,
        retrieval_sources=[],
        response_cache_enabled=False,
        cache_key="context",
        tournament_key_default=None,
        bi_year=2026,
        bi_scope="all",
        messages=[{"role": "user", "content": "consulta"}],
        write_tools={"finance_expense_create"},
        run_read_tool=reader,
        ensure_citations=lambda answer, sources: answer,
        tool_trace_has_write_intent=lambda trace: False,
        assistant_response_cache_set=lambda **kw: None,
        pending_confirmation_cls=SimpleNamespace,
        assistant_run_cls=SimpleNamespace,
        assistant_message_cls=SimpleNamespace,
        message_response_cls=SimpleNamespace,
        tool_policy_evaluator=lambda *args: {
            "decision": "deny" if outcome == "deny" else "allow",
            "reason": "fixture",
        },
        deterministic_tool_answer=None,
        openai_api_key=None,
        get_openai_client=lambda key: SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=sync_call))
        ),
        get_anthropic_client=lambda: SimpleNamespace(
            messages=SimpleNamespace(create=sync_call)
        ),
        route_prompt="read",
        language_prompt="es",
        hermes_profile_prompt=None,
        workspace_context=None,
        module_key_default="finance",
        module_label_default=None,
        module_context_default=None,
        retrieval_context=None,
        assistant_system_prompt=lambda: "system",
        history_messages=AsyncMock(return_value=[]),
        tool_defs_anthropic=lambda defs: defs,
        anthropic_text_from_blocks=lambda blocks: (
            "" if blocks[0].type == "tool_use" else blocks[0].text
        ),
        anthropic_message_from_blocks=lambda blocks: [vars(b) for b in blocks],
        get_model=lambda *a, **kw: "mock",
        ollama_chat=ollama_call,
        ollama_message_content=lambda p: p["content"],
        ollama_tool_calls=lambda p: p["tool_calls"],
        ollama_assistant_message=lambda p: {
            "role": "assistant",
            "content": p["content"],
        },
    )
    execute = getattr(execution, f"execute_{provider}_provider")
    kwargs = {
        k: v for k, v in common.items() if k in inspect.signature(execute).parameters
    }
    if outcome == "deny":
        with pytest.raises(HTTPException) as exc:
            await execute(**kwargs)
        assert exc.value.status_code == 403
        reader.assert_not_awaited()
        assert not session.add.call_args_list
    else:
        result = await execute(**kwargs)
        if outcome == "confirm":
            assert result.pending_confirmation.tool_name == tool
            reader.assert_not_awaited()
            assert len(calls) == 1
        else:
            reader.assert_awaited_once()
            assert len(calls) == 2
            assert "TEST-42" in json.dumps(calls[1]["messages"], default=vars)
            assert result.assistant_message == "Síntesis con folio TEST-42."


def test_scope_change_drops_previous_segment_without_dropping_year():
    conv = conversation()
    update_context(conv, bi_year=2026, bi_scope="national", bi_segment="finals")
    update_context(conv, bi_scope="all")
    assert conv.metadata_["bi_filters"] == {"bi_year": 2026, "bi_scope": "all"}


@pytest.mark.asyncio
async def test_retrieval_cache_cannot_bypass_fresh_reads(monkeypatch, tmp_path):
    from unittest.mock import MagicMock

    from samchat.assistant import router

    monkeypatch.setattr(router, "_RAG_CONFIG_PATH", tmp_path / "rag.json")
    monkeypatch.setattr(router, "_cache_key_for_query", lambda *a, **kw: "poison")
    poisoned = {"expires_at": 10**20, "context": "stale privileged answer"}
    monkeypatch.setattr(router, "_RETRIEVAL_CACHE", {"poison": poisoned})
    monkeypatch.setattr(
        router,
        "get_rag_store",
        lambda: SimpleNamespace(search=MagicMock(return_value=[])),
    )
    sql = AsyncMock(return_value=[])
    memory = AsyncMock(return_value=[])
    monkeypatch.setattr(router, "_retrieve_sql_snippets", sql)
    monkeypatch.setattr(router, "_retrieve_memory_snippets", memory)
    result = await router._build_hybrid_retrieval(
        session=None, query="facturas septiembre", use_cache=False
    )
    assert result["cache_hit"] is False
    assert "stale privileged" not in result["context"]
    sql.assert_awaited_once()
    memory.assert_awaited_once()
    assert router._RETRIEVAL_CACHE == {"poison": poisoned}


def test_retrieval_cache_identity_includes_conversation_domain_and_full_query(
    monkeypatch, tmp_path
):
    from samchat.assistant import router
    from samchat.assistant.router import _cache_key_for_query

    monkeypatch.setattr(router, "_RAG_CONFIG_PATH", tmp_path / "rag.json")

    key = _cache_key_for_query("x" * 1200, conversation_id="one", domain="finance")
    assert key != _cache_key_for_query(
        "x" * 1200, conversation_id="two", domain="finance"
    )
    assert key != _cache_key_for_query(
        "x" * 1200, conversation_id="one", domain="tournament"
    )
    assert key != _cache_key_for_query(
        "x" * 1200 + "other", conversation_id="one", domain="finance"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai", "anthropic", "ollama"])
@pytest.mark.parametrize(
    "outcome",
    [
        "invented",
        "causal",
        "clarification",
        "unavailable",
        "evidence",
        "confirm",
        "denied",
        "reader_reference_valid", "reader_reference_multiple", "reader_reference_invented", "reader_reference_other_row", "reader_reference_other_result",
        "reader_mismatch", "reader_valid", "reader_year", "reader_folio", "reader_derived",
        "clarification_year", "clarification_folio", "clarification_assertion",
    ],
)
@pytest.mark.parametrize("raw_message", [QUESTION, "¿y agosto?", "¿por qué aumentó?"])
async def test_contextual_provider_gate_before_persistence(
    monkeypatch, provider, outcome, raw_message
):
    import copy
    import inspect
    from unittest.mock import MagicMock

    from fastapi import HTTPException

    from samchat.assistant import provider_execution as execution

    from samchat.assistant import conversation_service as service, router
    from samchat.assistant.conversation_context import contextual_read_frame

    tool = (
        "finance_expense_create" if outcome == "confirm" else "assistant_finance_read"
    )
    if outcome.startswith("reader_"):
        tool = "finance_realtime_report"
    if outcome.startswith("reader_reference_"):
        tool = "finance_expense_search"
    answer = {
        "reader_reference_multiple": "Estimado de los registros consultados: importe 42 MXN. Fuente: folio SOL-999.\nImporte 17 MXN. Fuente: folio SOL-123.",
        "reader_reference_valid": "Estimado de los registros consultados: importe 42 MXN. Fuente: folio SOL-999.",
        "reader_reference_invented": "Estimado de los registros consultados: importe 42 MXN. Fuente: folio SOL-555.",
        "reader_reference_other_row": "Estimado de los registros consultados: importe 42 MXN. Fuente: folio SOL-123.",
        "reader_reference_other_result": "Estimado de los registros consultados: importe 42 MXN. Fuente: folio SOL-123.",
        "reader_mismatch": "El total es 99 MXN. Fuente: reporte de gastos.",
        "reader_valid": "El total es 42 MXN. Fuente: reporte de gastos.",
        "reader_year": "El total es 2026 MXN. Fuente: reporte de gastos.",
        "reader_folio": "El total es 999 MXN. Fuente: folio SOL-999.",
        "reader_derived": "El total es 84 MXN. Fuente: reporte de gastos.",
        "clarification_year": "¿Septiembre 2025 o 2026?",
        "clarification_folio": "¿Te refieres al folio SOL-999 o SOL-998?",
        "clarification_assertion": "¿Confirmas que se pagaron 99 MXN?",
        "confirm": "En agosto se pagaron 42.000 MXN de IVA.",
        "denied": "En agosto se pagaron 42.000 MXN de IVA.",
        "invented": "En agosto se pagaron 42.000 MXN de IVA.",
        "causal": "El IVA aumentó porque se contrataron más proveedores.",
        "clarification": "¿Buscas documentos aprobados o pagos efectivamente realizados?",
        "unavailable": "IVA pagado: 0 MXN. Fuente: solicitudes.",
        "evidence": "El importe consultado es 42 MXN. Fuente: folio TEST-42.",
    }[outcome]
    evidence = {
        "ok": True,
        "read_only": True,
        "intent": "budget.snapshot",
        "source_function": "samchat.budgets.service.build_budget_snapshot",
        "source_notes": ["budget authority stays in Presupuestos"],
        "payload": {
            "ok": True,
            "source": "budget_db",
            "version": {"id": "TEST-42", "edition_year": 2026},
            "summary": {"budget_total": 42, "line_count": 1},
        },
    }
    if outcome == "unavailable":
        from samchat.assistant.finance_read_adapter import run_finance_read_adapter

        evidence = await run_finance_read_adapter(
            AsyncMock(), intent="finance.vat_paid"
        )
    has_tool = outcome.startswith("reader_") or outcome in {"unavailable", "evidence", "confirm", "denied"}
    history = [{"role": "user", "content": QUESTION}]
    if outcome == "clarification_folio":
        history[0]["content"] += " Folios SOL-999 y SOL-998."
    frame = contextual_read_frame(raw_message, history)

    def finalize(answer, trace, *, pending=False):
        return service.finalize_contextual_response(
            answer,
            trace,
            work_frame=frame,
            maybe_append_export_prompt=lambda text, trace: text,
            pending=pending,
        )

    reader = AsyncMock(return_value=evidence)
    if outcome.startswith("reader_"):
        # Execute the actual reader and router envelope, replacing only SQL IO.
        async def actual_read(name, args, **unused):
            sql = AsyncMock()
            if outcome.startswith("reader_reference_"):
                def expense(ref, amount):
                    return SimpleNamespace(
                        id=ref, numero_referencia=ref,
                        gasto_cantidad=amount, fecha=None, proyecto="Local",
                        concepto="Hotel", metodo_pago="transferencia",
                        estado_reembolso="pendiente", nombre_enviador="Local")
                rows = [expense("SOL-999", 42)]
                if outcome in {"reader_reference_other_row", "reader_reference_multiple"}:
                    rows.append(expense("SOL-123", 17))
                if args.get("query") == "other":
                    rows = [expense("SOL-123", 17)]
                sql.execute.return_value = SimpleNamespace(
                    scalars=lambda: SimpleNamespace(all=lambda: rows))
                return await router._run_read_tool(
                    name, args, gastos_session=sql,
                    current_role="admin", tournament_key_default=None)
            sql.execute.side_effect = [
                SimpleNamespace(one=lambda: SimpleNamespace(n=999, m=42)),
                SimpleNamespace(all=lambda: []),
                SimpleNamespace(all=lambda: [SimpleNamespace(k="SOL-999", n=999, m=42)]),
            ]
            return await router._run_read_tool(
                name,
                {"date_from": "2026-09-01", "date_to": "2026-09-30", "budget_source": "none", "compare_years": 0},
                gastos_session=sql, current_role="admin", tournament_key_default=None)
        reader = AsyncMock(side_effect=actual_read)
    calls = []

    def sync_call(**kwargs):
        calls.append(copy.deepcopy(kwargs))
        first = len(calls) == 1 and has_tool
        if provider == "openai":
            choice = SimpleNamespace(
                content="" if first else answer,
                tool_calls=(
                    [
                        SimpleNamespace(
                            id="call1",
                            function=SimpleNamespace(name=tool, arguments="{}"),
                        )
                    ]
                    if first
                    else []
                ),
            )
            if first and outcome == "reader_reference_other_result":
                choice.tool_calls.append(SimpleNamespace(id="call2", function=SimpleNamespace(
                    name=tool, arguments='{"query":"other"}')))
            return SimpleNamespace(choices=[SimpleNamespace(message=choice)])
        response = SimpleNamespace(
            content=(
                [SimpleNamespace(type="tool_use", id="call1", name=tool, input={})]
                if first
                else [SimpleNamespace(type="text", text=answer)]
            )
        )
        if first and outcome == "reader_reference_other_result":
            response.content.append(SimpleNamespace(type="tool_use", id="call2", name=tool, input={"query": "other"}))
        return response

    async def ollama_call(**kwargs):
        calls.append(copy.deepcopy(kwargs))
        first = len(calls) == 1 and has_tool
        response = {
            "content": "" if first else answer,
            "tool_calls": (
                [{"function": {"name": tool, "arguments": {}}}] if first else []
            ),
        }
        if first and outcome == "reader_reference_other_result":
            response["tool_calls"].append({"function": {"name": tool, "arguments": {"query": "other"}}})
        return response

    session = AsyncMock()
    session.add = MagicMock()
    common = dict(
        model="mock",
        normalized_mode="balanceado",
        route_info={"route": "lookup_sql"},
        raw_message=raw_message,
        conversation=conversation(),
        current_empleado=SimpleNamespace(id="u", rol="admin"),
        session=session,
        tool_trace=[],
        tool_defs=[],
        max_tokens=100,
        retrieval_sources=[],
        response_cache_enabled=False,
        cache_key="context",
        tournament_key_default=None,
        bi_year=2026,
        bi_scope="all",
        messages=[{"role": "user", "content": "consulta"}],
        write_tools={"finance_expense_create"},
        run_read_tool=reader,
        ensure_citations=lambda answer, sources: answer,
        tool_trace_has_write_intent=lambda trace: False,
        assistant_response_cache_set=lambda **kw: None,
        pending_confirmation_cls=SimpleNamespace,
        assistant_run_cls=SimpleNamespace,
        assistant_message_cls=SimpleNamespace,
        message_response_cls=SimpleNamespace,
        tool_policy_evaluator=lambda *args: {
            "decision": "deny" if outcome == "denied" else "allow",
            "reason": "fixture",
        },
        deterministic_tool_answer=None,
        openai_api_key=None,
        get_openai_client=lambda key: SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=sync_call))
        ),
        get_anthropic_client=lambda: SimpleNamespace(
            messages=SimpleNamespace(create=sync_call)
        ),
        route_prompt="read",
        language_prompt="es",
        hermes_profile_prompt=None,
        workspace_context=None,
        module_key_default="finance",
        module_label_default=None,
        module_context_default=None,
        retrieval_context=None,
        assistant_system_prompt=lambda: "system",
        history_messages=AsyncMock(return_value=history),
        finalize_response=finalize,
        tool_defs_anthropic=lambda defs: defs,
        anthropic_text_from_blocks=lambda blocks: (
            "" if blocks[0].type == "tool_use" else blocks[0].text
        ),
        anthropic_message_from_blocks=lambda blocks: [vars(b) for b in blocks],
        get_model=lambda *a, **kw: "mock",
        ollama_chat=ollama_call,
        ollama_message_content=lambda p: p["content"],
        ollama_tool_calls=lambda p: p["tool_calls"],
        ollama_assistant_message=lambda p: {
            "role": "assistant",
            "content": p["content"],
        },
    )
    execute = getattr(execution, f"execute_{provider}_provider")
    kwargs = {
        k: v for k, v in common.items() if k in inspect.signature(execute).parameters
    }
    saved = []
    session.add.side_effect = saved.append
    commits = []

    async def commit():
        commits.append(copy.deepcopy(saved))

    session.commit.side_effect = commit

    async def turn(**unused):
        return await execute(**kwargs)

    for name in (
        "_build_document_upload_response",
        "_build_document_confirmation_response",
        "_build_case_memory_response",
    ):
        monkeypatch.setattr(service, name, AsyncMock(return_value=None))
    if outcome == "denied":
        with pytest.raises(HTTPException) as exc:
            await execute(**kwargs)
        assert exc.value.status_code == 403
        assert not saved and not commits
        reader.assert_not_awaited()
        return
    result = await service.run_conversation_turn(
        raw_message=raw_message,
        conversation=common["conversation"],
        current_empleado=common["current_empleado"],
        session=session,
        request=None,
        tournament_key=None,
        bi_year=None,
        bi_scope=None,
        bi_segment=None,
        assistant_mode=None,
        openai_api_key=None,
        assistant_turn=turn,
        maybe_append_export_prompt=lambda text, trace: text,
        contextual=True,
    )
    if outcome == "confirm":
        assert result.pending_confirmation.tool_name == tool
        assert (
            result.assistant_message
            == "Necesito confirmación explícita para continuar."
        )
    elif outcome in {"clarification", "evidence", "reader_valid", "clarification_year", "clarification_folio", "reader_reference_valid", "reader_reference_multiple"}:
        assert answer in result.assistant_message
    else:
        assert answer not in result.assistant_message
        assert "42.000" not in result.assistant_message
        assert "0 MXN" not in result.assistant_message
        assert "porque se contrataron" not in result.assistant_message
        assert any(
            step.get("assistant_response_sufficiency_gate", {}).get("ok") is False
            for step in result.tool_trace
        )
    if outcome == "unavailable":
        assert "no significa que el importe sea cero" in result.assistant_message
    messages = [obj for obj in saved if getattr(obj, "role", None) == "assistant"]
    runs = [obj for obj in saved if hasattr(obj, "assistant_message")]
    assert len(messages) == len(runs) == 1
    assert messages[0].content == runs[0].assistant_message == result.assistant_message
    assert runs[0].tool_trace == result.tool_trace
    assert commits
    for snapshot in commits:
        for row in snapshot:
            if getattr(row, "role", None) == "assistant":
                assert row.content == result.assistant_message
            if hasattr(row, "assistant_message"):
                assert row.assistant_message == result.assistant_message
    # Reload through the real history reader; rejected text must never return.
    session.execute.return_value = SimpleNamespace(
        scalars=lambda: SimpleNamespace(all=lambda: list(reversed(messages)))
    )
    reloaded = await router._history_messages(session, conversation_id="one")
    assert reloaded == [{"role": "assistant", "content": result.assistant_message}]
    if not has_tool or outcome == "confirm":
        reader.assert_not_awaited()
    elif outcome == "reader_reference_other_result":
        assert reader.await_count == 2
    else:
        reader.assert_awaited_once()


def test_contextual_read_frame_never_inherits_write_authority():
    from samchat.assistant.conversation_context import contextual_read_frame

    frame = contextual_read_frame(
        "¿y agosto?",
        [
            {"role": "user", "content": "Paga el IVA al proveedor y confirma el pago"},
        ],
    )
    assert frame.domain == "finance"
    assert frame.authority_boundary == "read_only"
    assert frame.task_kind == "evidence"
    assert frame.user_message == "¿y agosto?"
    assert frame.temporal_scope["month"] == "08"
    assert frame.answer_contract["require_current_read_evidence"] is True


def test_contextual_read_frame_retains_period_across_grouping_followup():
    from samchat.assistant.conversation_context import contextual_read_frame

    history = [
        {"role": "user", "content": QUESTION},
        {"role": "user", "content": "¿y agosto?"},
    ]
    frame = contextual_read_frame("por proveedor", history)
    assert frame.temporal_scope == {
        "year": 2026,
        "month": "08",
        "month_label": "agosto",
    }
    assert frame.authority_boundary == "read_only"
