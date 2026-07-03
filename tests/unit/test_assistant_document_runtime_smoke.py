from samchat.assistant.conversation_service import run_message_turn_with_pending


async def test_deterministic_pending_bypasses_provider_path():
    async def pending_loader(**_kwargs):
        return None

    async def provider_must_not_run(**_kwargs):  # pragma: no cover - sentinel
        raise AssertionError("provider path was called")

    async def build_response(**_kwargs):
        class Response:
            assistant_message = "ok"
            tool_trace = [{"provider_called": False}]

        return Response()

    class Obj:
        id = "id-1"

    response = await run_message_turn_with_pending(
        raw_message="registrar deterministico",
        conversation=Obj(),
        current_empleado=Obj(),
        session=object(),
        request=None,
        tournament_key=None,
        bi_year=None,
        bi_scope=None,
        bi_segment=None,
        assistant_mode=None,
        openai_api_key=None,
        latest_pending_run_for_conversation=pending_loader,
        is_explicit_approval_message=lambda _text: False,
        is_explicit_rejection_message=lambda _text: False,
        confirm_pending_run=provider_must_not_run,
        deterministic_pending_builders=[lambda **_kwargs: ("tool", {}, "message")],
        build_deterministic_pending_response=build_response,
        assistant_turn=provider_must_not_run,
        maybe_append_export_prompt=lambda message, _trace: message,
    )

    assert response.tool_trace[0]["provider_called"] is False
