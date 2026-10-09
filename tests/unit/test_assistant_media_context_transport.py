"""Real Hermes multipart -> FastAPI endpoint, without network or LLM calls."""

import copy
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from samchat.assistant import router
from samchat.assistant.conversation_context import update_context
from samchat.assistant.hermes_client import HermesSamchatAssistantClient


@pytest.mark.parametrize("change", ["omitted", "screen", "clear"])
def test_hermes_media_omission_and_explicit_snapshot(monkeypatch, tmp_path, change):
    conv = SimpleNamespace(id=uuid.uuid4(), metadata_={}, tournament_key=None)
    update_context(
        conv,
        module_key="finance",
        tournament_key="cup",
        module_context={"company": "Sports"},
        bi_year=2026,
        bi_scope="national",
        bi_segment="CDMX",
    )
    before = copy.deepcopy(conv.metadata_)
    session = AsyncMock()
    employee = SimpleNamespace(id=uuid.uuid4(), rol="admin")
    monkeypatch.setattr(router, "_enforce_rate_limit", lambda **kw: None)
    monkeypatch.setattr(router, "_load_conversation", AsyncMock(return_value=conv))
    monkeypatch.setattr(
        router, "extract_text_from_media", AsyncMock(return_value="¿y agosto?")
    )
    turn = AsyncMock(
        return_value={
            "assistant_message": "Consulta recibida",
            "run_id": "local",
            "tool_trace": [],
        }
    )
    monkeypatch.setattr(router, "run_conversation_turn", turn)
    app = FastAPI()
    app.include_router(router.router)
    app.dependency_overrides[router.get_db_session] = lambda: session
    app.dependency_overrides[router.get_current_empleado] = lambda: employee
    upload = tmp_path / "query.txt"
    upload.write_text("consulta")
    client = HermesSamchatAssistantClient(
        base_url="http://local", service_token="local-test", actor_id="local-test"
    )
    with TestClient(app) as http:

        def transport(method, path, *, data_payload, files):
            result = http.request(
                method, "/api/assistant" + path, data=data_payload, files=files
            )
            assert result.status_code == 200, result.text
            return result.json()

        monkeypatch.setattr(client, "_request", transport)
        kwargs = {}
        if change == "screen":
            kwargs = {
                "module_key": "tournament",
                "module_context": {"selection": "new"},
            }
        elif change == "clear":
            kwargs = {"module_context": {}}
        client.send_media(
            conversation_id=str(conv.id), file_path=str(upload), kind="text", **kwargs
        )
    if change == "omitted":
        assert conv.metadata_["module_key"] == "finance"
        assert conv.metadata_["module_context"] == before["module_context"]
        assert conv.metadata_["bi_filters"] == before["bi_filters"]
        assert conv.tournament_key == "cup"
    elif change == "screen":
        assert conv.metadata_["module_key"] == "tournament"
        assert conv.metadata_["module_context"] == {"selection": "new"}
        assert conv.metadata_["bi_filters"] == {}
        assert conv.tournament_key is None
    else:
        assert conv.metadata_["module_context"] == {}
        assert conv.metadata_["bi_filters"] == {}
    assert turn.call_args.kwargs["contextual"] is True
