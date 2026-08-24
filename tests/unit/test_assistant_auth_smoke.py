import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT / "scripts" / "assistant_auth_smoke.py"
SPEC = importlib.util.spec_from_file_location("assistant_auth_smoke_test", SCRIPT_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC is not None and SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_trace_write_intent_flags_mutation_verbs():
    assert MODULE._trace_has_write_intent([{"tool_name": "document.approve"}]) is True
    assert MODULE._trace_has_write_intent([{"canonical_action": "accounting_post"}]) is True
    assert MODULE._trace_has_write_intent([{"action": "read_finance_snapshot"}]) is False
    assert MODULE._trace_has_write_intent([{"tool_name": "assistant_owner_pack_readiness"}]) is False


def test_auth_failure_is_explicit_for_401_response():
    result = MODULE.HttpResult(ok=False, status=401, url="/api/assistant/me", payload={"detail": "Not authenticated"})
    assert MODULE._is_auth_failure(result) is True
    assert MODULE._short_error(result) == "Not authenticated"


def test_cookie_file_accepts_raw_cookie_header(tmp_path):
    cookie_file = tmp_path / "cookie.txt"
    cookie_file.write_text("session=abc; other=def\n")
    assert MODULE._read_cookie_file(str(cookie_file)) == "session=abc; other=def"


def test_run_smoke_stops_before_mutation_when_me_requires_auth(monkeypatch):
    calls = []

    def fake_request(**kwargs):
        calls.append(kwargs["url"])
        if kwargs["url"].endswith("/assistant"):
            return MODULE.HttpResult(ok=True, status=200, url=kwargs["url"], text="<html></html>")
        return MODULE.HttpResult(ok=False, status=401, url=kwargs["url"], payload={"detail": "Not authenticated"})

    monkeypatch.setattr(MODULE, "_request", fake_request)
    result = MODULE.run_smoke(
        base_url="http://testserver",
        cookie=None,
        bearer=None,
        openai_api_key=None,
        message="hola",
        timeout=1,
    )

    assert result["ok"] is False
    assert result["status"] == "authentication_required"
    assert calls == ["http://testserver/assistant", "http://testserver/api/assistant/me"]


def test_run_smoke_passes_readonly_turn(monkeypatch):
    def fake_request(**kwargs):
        url = kwargs["url"]
        if url.endswith("/assistant"):
            return MODULE.HttpResult(ok=True, status=200, url=url, text="<html></html>")
        if url.endswith("/api/assistant/me"):
            return MODULE.HttpResult(ok=True, status=200, url=url, payload={"empleado_id": "e1", "nombre": "Tester", "rol": "admin"})
        if url.endswith("/api/assistant/conversations") and kwargs["method"] == "POST":
            return MODULE.HttpResult(ok=True, status=200, url=url, payload={"conversation_id": "c1"})
        if "/api/assistant/conversations?" in url:
            return MODULE.HttpResult(ok=True, status=200, url=url, payload=[{"conversation_id": "c1"}])
        if url.endswith("/api/assistant/conversations/c1/messages") and kwargs["method"] == "POST":
            return MODULE.HttpResult(
                ok=True,
                status=200,
                url=url,
                payload={
                    "assistant_message": "Puedo consultar sin escribir.",
                    "run_id": "r1",
                    "tool_trace": [{"tool_name": "read_finance_snapshot"}],
                    "pending_confirmation": None,
                },
            )
        if url.endswith("/api/assistant/conversations/c1/messages") and kwargs["method"] == "GET":
            return MODULE.HttpResult(
                ok=True,
                status=200,
                url=url,
                payload=[{"role": "user"}, {"role": "assistant"}],
            )
        raise AssertionError(url)

    monkeypatch.setattr(MODULE, "_request", fake_request)
    result = MODULE.run_smoke(
        base_url="http://testserver",
        cookie="session=abc",
        bearer=None,
        openai_api_key=None,
        message="hola",
        timeout=1,
    )

    assert result["ok"] is True
    assert result["status"] == "pass"
    assert result["employee"]["nombre"] == "Tester"
    assert result["assistant_turn"]["pending_confirmation"] is False


def test_run_smoke_fails_when_turn_requests_write_confirmation(monkeypatch):
    def fake_request(**kwargs):
        url = kwargs["url"]
        if url.endswith("/assistant"):
            return MODULE.HttpResult(ok=True, status=200, url=url, text="<html></html>")
        if url.endswith("/api/assistant/me"):
            return MODULE.HttpResult(ok=True, status=200, url=url, payload={"empleado_id": "e1", "nombre": "Tester", "rol": "admin"})
        if url.endswith("/api/assistant/conversations") and kwargs["method"] == "POST":
            return MODULE.HttpResult(ok=True, status=200, url=url, payload={"conversation_id": "c1"})
        if "/api/assistant/conversations?" in url:
            return MODULE.HttpResult(ok=True, status=200, url=url, payload=[{"conversation_id": "c1"}])
        if url.endswith("/api/assistant/conversations/c1/messages") and kwargs["method"] == "POST":
            return MODULE.HttpResult(
                ok=True,
                status=200,
                url=url,
                payload={
                    "assistant_message": "Voy a aprobar.",
                    "run_id": "r1",
                    "tool_trace": [{"tool_name": "document.approve"}],
                    "pending_confirmation": {"run_id": "r1", "tool_name": "document.approve", "tool_args": {}, "summary": "aprobar"},
                },
            )
        if url.endswith("/api/assistant/conversations/c1/messages") and kwargs["method"] == "GET":
            return MODULE.HttpResult(ok=True, status=200, url=url, payload=[{"role": "user"}, {"role": "assistant"}])
        raise AssertionError(url)

    monkeypatch.setattr(MODULE, "_request", fake_request)
    result = MODULE.run_smoke(
        base_url="http://testserver",
        cookie="session=abc",
        bearer=None,
        openai_api_key=None,
        message="hola",
        timeout=1,
    )

    assert result["ok"] is False
    turn_step = next(step for step in result["steps"] if step["name"] == "create_readonly_message")
    assert turn_step["pending_confirmation"] is True
    assert turn_step["write_intent_in_trace"] is True
