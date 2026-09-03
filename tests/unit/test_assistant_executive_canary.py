import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT / "scripts" / "run_assistant_executive_canary.py"
ARTIFACT_DIR = ROOT / "artifacts" / "rqf-054h-executive-canary-gate"
ARTIFACT_RESULT = ARTIFACT_DIR / "fixture-result.json"
ARTIFACT_README = ARTIFACT_DIR / "README.md"
SPEC = importlib.util.spec_from_file_location(
    "assistant_executive_canary_test", SCRIPT_PATH
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC is not None and SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_fixture_canary_passes_all_current_executive_cases():
    result = MODULE.run_fixture_canary()

    assert result["ok"] is True
    assert result["mode"] == "fixture"
    assert result["summary"]["total"] >= 7
    assert result["summary"]["failed"] == 0
    assert {row["case_id"] for row in result["cases"]} == {
        case.case_id for case in MODULE.executive_regression_cases()
    }
    assert all(
        row["authority_posture"] == "read_only" for row in result["cases"]
    )
    assert result["routing_contracts"]["ok"] is True
    assert result["routing_contracts"]["summary"]["total"] >= 12
    assert result["routing_contracts"]["summary"]["failed"] == 0


def test_fixture_canary_flags_wrong_routing_contract(tmp_path):
    overrides = tmp_path / "responses.jsonl"
    overrides.write_text(
        json.dumps(
            {
                "case_id": "ROUTE-FIN-CASHFLOW-STATEMENT-001",
                "assistant_message": "Cashflow Planning read-only. No ejecute cambios.",
                "tool_trace": [
                    {
                        "tool": "assistant_finance_read",
                        "result": {
                            "intent": "cashflow.summary",
                            "provider_called": False,
                            "writes_attempted": False,
                        },
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = MODULE.run_fixture_canary(fixture_responses=str(overrides))

    failed = [
        row
        for row in result["routing_contracts"]["cases"]
        if row["case_id"] == "ROUTE-FIN-CASHFLOW-STATEMENT-001"
    ][0]
    assert result["ok"] is False
    assert result["routing_contracts"]["ok"] is False
    assert failed["ok"] is False
    assert "missing_intent:cashflow.statement" in failed["failures"]
    assert "missing_term:Flujo de Efectivo" in failed["failures"]


def test_fixture_canary_flags_wrong_tool_from_override(tmp_path):
    overrides = tmp_path / "responses.jsonl"
    overrides.write_text(
        json.dumps(
            {
                "case_id": "OWNER-PAYMENT-EVIDENCE-001",
                "assistant_message": (
                    "Pagos pendientes\nHay 0 solicitudes pendientes por $0.00."
                ),
                "tool_trace": [
                    {
                        "tool": "receipts.pending_payment_overview",
                        "result": {"status": "success"},
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = MODULE.run_fixture_canary(fixture_responses=str(overrides))

    failed = [
        row
        for row in result["cases"]
        if row["case_id"] == "OWNER-PAYMENT-EVIDENCE-001"
    ][0]
    assert result["ok"] is False
    assert failed["ok"] is False
    assert (
        "forbidden_tool:receipts.pending_payment_overview"
        in failed["failures"]
    )


def test_fixture_canary_treats_timeout_as_quality_failure(tmp_path):
    overrides = tmp_path / "responses.jsonl"
    overrides.write_text(
        json.dumps(
            {
                "case_id": "FIN-PAYMENT-RUN-001",
                "assistant_message": "",
                "tool_trace": [],
                "timeout": True,
                "provider": "anthropic",
                "model": "claude-test",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = MODULE.run_fixture_canary(fixture_responses=str(overrides))

    failed = [
        row
        for row in result["cases"]
        if row["case_id"] == "FIN-PAYMENT-RUN-001"
    ][0]
    assert result["ok"] is False
    assert failed["timeout"] is True
    assert "provider_timeout" in failed["failures"]
    assert result["summary"]["timeouts"] == 1


def test_pending_confirmation_and_write_trace_fail_boundary(tmp_path):
    overrides = tmp_path / "responses.jsonl"
    overrides.write_text(
        json.dumps(
            {
                "case_id": "OWNER-READINESS-001",
                "assistant_message": (
                    "Owner Pack Readiness con Faltantes. "
                    "Frontera de autoridad."
                ),
                "tool_trace": [
                    {
                        "tool": "document.approve",
                        "result": {"writes_attempted": True},
                    }
                ],
                "pending_confirmation": {"tool_name": "document.approve"},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = MODULE.run_fixture_canary(fixture_responses=str(overrides))

    failed = [
        row
        for row in result["cases"]
        if row["case_id"] == "OWNER-READINESS-001"
    ][0]
    assert result["ok"] is False
    assert failed["pending_confirmation"] is True
    assert failed["write_detected"] is True
    assert "pending_confirmation" in failed["failures"]
    assert failed["authority_posture"] == "failed_write_boundary"


def test_live_canary_requires_auth_before_http_messages(monkeypatch):
    calls = []

    def fake_request(**kwargs):
        calls.append(kwargs["url"])
        raise AssertionError("should not call without auth")

    monkeypatch.setattr(MODULE, "_request", fake_request)
    result = MODULE.run_live_canary(
        base_url="http://testserver", cookie=None, bearer=None, timeout=1
    )

    assert result["ok"] is False
    assert result["status"] == "authentication_required"
    assert calls == []


def test_live_canary_never_includes_cookie_or_bearer_in_result(monkeypatch):
    def fake_request(**kwargs):
        url = kwargs["url"]
        if (
            url.endswith("/api/assistant/conversations")
            and kwargs["method"] == "POST"
        ):
            return MODULE.HttpResult(
                ok=True, status=200, url=url, payload={"conversation_id": "c1"}
            )
        if "/api/assistant/conversations/c1/messages" in url:
            case = next(
                item
                for item in MODULE.executive_regression_cases()
                if item.question == kwargs["payload"]["message"]
            )
            return MODULE.HttpResult(
                ok=True,
                status=200,
                url=url,
                payload=MODULE._fixture_response_for_case(case),
                latency_seconds=0.2,
            )
        raise AssertionError(url)

    monkeypatch.setattr(MODULE, "_request", fake_request)
    result = MODULE.run_live_canary(
        base_url="http://testserver",
        cookie="session=secret-cookie",
        bearer="secret-bearer",
        timeout=1,
    )
    serialized = json.dumps(result, sort_keys=True)

    assert result["ok"] is True
    assert "secret-cookie" not in serialized
    assert "secret-bearer" not in serialized
    assert result["summary"]["total"] >= 7


def test_rqf_054h_fixture_gate_artifact_contract():
    result = json.loads(ARTIFACT_RESULT.read_text(encoding="utf-8"))
    readme = ARTIFACT_README.read_text(encoding="utf-8")

    assert result["schema_version"] == MODULE.SCHEMA_VERSION
    assert result["mode"] == "fixture"
    assert result["ok"] is True
    assert result["summary"]["failed"] == 0
    assert result["summary"]["timeouts"] == 0
    assert result["network"]["http_called"] is False
    assert result["network"]["provider_called"] is False
    assert result["network"]["credentials_used"] is False
    assert result["authority_boundary"]["posture"] == "read_only"
    assert result["authority_boundary"]["writes_detected"] == 0
    assert result["authority_boundary"]["pending_confirmations"] == 0
    assert {row["case_id"] for row in result["cases"]} == {
        case.case_id for case in MODULE.executive_regression_cases()
    }
    assert "Status: FIXTURE_GATE_CAPTURED" in readme
    assert "## Fixture Gate" in readme
    assert "## Live Gate Boundary" in readme
    assert "--live" in readme
    assert "No live canary" in readme
