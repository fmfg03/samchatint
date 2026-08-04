from pathlib import Path

from samchat.assistant.owner_folder_revision import BLOCKED_WRITE_DISABLED
from samchat.assistant.owner_needs_eval import parse_owner_needs_eval_set
from samchat.assistant.owner_operator_workflow import (
    OWNER_OPERATOR_WORKFLOW_ONLY,
    evaluate_owner_operator_workflow_set,
    run_owner_operator_workflow,
    workflow_contains_execution_claim,
)
from samchat.assistant.owner_response_pack import (
    SOURCE_FOLDER_PROPOSAL,
    SOURCE_FOLDER_REVISION,
)


ROOT = Path(__file__).resolve().parents[2]
EVAL_SET = ROOT / "docs/assistant/rqf-assistant-009e-evaluation-set.md"


def _prompts():
    return parse_owner_needs_eval_set(EVAL_SET.read_text(encoding="utf-8"))


def _prompt(prompt_id: str):
    for prompt in _prompts():
        if prompt.prompt_id == prompt_id:
            return prompt
    raise AssertionError(f"missing prompt {prompt_id}")


def _assert_workflow_safety(result) -> None:
    assert result.execution_status == "not_executed"
    assert result.writes_attempted == 0
    assert result.side_effects_detected == 0
    assert result.audit_language == OWNER_OPERATOR_WORKFLOW_ONLY
    assert result.safety_summary["writes_enabled"] is False
    assert result.safety_summary["write_handlers_invoked"] == 0
    assert result.safety_summary["runtime_general_enabled"] is False
    assert workflow_contains_execution_claim(result) is False


def test_workflow_without_revision_responds_from_proposal() -> None:
    result = run_owner_operator_workflow(_prompt("AI-OWNER-001"))

    assert result.workflow_id.startswith("oow_")
    assert result.prompt_id == "AI-OWNER-001"
    assert result.revision is None
    assert result.response_pack["source_type"] == SOURCE_FOLDER_PROPOSAL
    assert result.trace["assessment_status"]
    assert result.trace["preview_id"].startswith("bdp_")
    assert result.trace["folder_id"].startswith("ofp_")
    assert result.trace["response_id"].startswith("orp_")
    assert "team" in result.response_pack["missing_evidence"]
    _assert_workflow_safety(result)


def test_workflow_with_revision_responds_from_revision() -> None:
    result = run_owner_operator_workflow(
        _prompt("AI-OWNER-001"),
        requested_revision="agrega pagos de operador y proveedores",
    )

    assert result.revision is not None
    assert result.revision["revision_id"].startswith("ofr_")
    assert result.response_pack["source_type"] == SOURCE_FOLDER_REVISION
    assert result.trace["revision_id"] == result.revision["revision_id"]
    assert "finance" in result.revision["changed_sections"]
    _assert_workflow_safety(result)


def test_workflow_with_write_like_revision_fails_closed() -> None:
    result = run_owner_operator_workflow(
        _prompt("AI-OWNER-028"),
        requested_revision="actualizala y manda el reporte al operador",
    )

    assert result.revision is not None
    assert result.revision["revision_status"] == BLOCKED_WRITE_DISABLED
    assert result.trace["revision_status"] == BLOCKED_WRITE_DISABLED
    assert result.response_pack["safety_status"] == BLOCKED_WRITE_DISABLED
    assert result.safety_summary["write_handlers_invoked"] == 0
    _assert_workflow_safety(result)


def test_workflow_preserves_medical_missing_evidence() -> None:
    result = run_owner_operator_workflow(_prompt("AI-OWNER-018"))

    assert "medical/event_incident" in result.preview["missing_evidence"]
    assert "medical/event_incident" in result.folder_proposal[
        "missing_evidence"
    ]
    assert "medical/event_incident" in result.response_pack[
        "missing_evidence"
    ]
    assert "No tengo evidencia concreta cargada" in result.response_pack[
        "summary"
    ]
    _assert_workflow_safety(result)


def test_full_set_without_revision_builds_safe_workflows() -> None:
    summary = evaluate_owner_operator_workflow_set(_prompts())

    assert summary["total"] == 30
    assert summary["blocked_write_disabled"] == 0
    assert summary["writes_attempted"] == 0
    assert summary["side_effects_detected"] == 0
    assert summary["execution_claims_detected"] == 0


def test_full_set_with_normal_revision_builds_safe_workflows() -> None:
    summary = evaluate_owner_operator_workflow_set(
        _prompts(),
        requested_revision="marca evidencia faltante y separa secciones",
    )

    assert summary["total"] == 30
    assert summary["blocked_write_disabled"] == 0
    assert summary["writes_attempted"] == 0
    assert summary["side_effects_detected"] == 0
    assert summary["execution_claims_detected"] == 0


def test_full_set_with_write_like_revision_blocks_all_workflows() -> None:
    summary = evaluate_owner_operator_workflow_set(
        _prompts(),
        requested_revision="creala y envia la carpeta",
    )

    assert summary["total"] == 30
    assert summary["blocked_write_disabled"] == 30
    assert summary["writes_attempted"] == 0
    assert summary["side_effects_detected"] == 0
    assert summary["execution_claims_detected"] == 0
