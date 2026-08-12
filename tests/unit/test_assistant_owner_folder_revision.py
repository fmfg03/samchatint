from pathlib import Path

from samchat.assistant.owner_folder_builder import (
    build_owner_prompt_folder_proposal,
)
from samchat.assistant.owner_folder_revision import (
    BLOCKED_WRITE_DISABLED,
    FOLDER_REVISION_PROPOSAL_ONLY,
    REVISION_PROPOSED,
    evaluate_owner_folder_revision_set,
    folder_revision_contains_execution_claim,
    revise_owner_folder_proposal,
)
from samchat.assistant.owner_needs_eval import parse_owner_needs_eval_set


ROOT = Path(__file__).resolve().parents[2]
EVAL_SET = ROOT / "docs/assistant/rqf-assistant-009e-evaluation-set.md"


def _prompts():
    return parse_owner_needs_eval_set(EVAL_SET.read_text(encoding="utf-8"))


def _prompt(prompt_id: str):
    for prompt in _prompts():
        if prompt.prompt_id == prompt_id:
            return prompt
    raise AssertionError(f"missing prompt {prompt_id}")


def _revision(prompt_id: str, requested_change: str):
    proposal = build_owner_prompt_folder_proposal(_prompt(prompt_id))
    return revise_owner_folder_proposal(proposal, requested_change)


def _assert_revision_safety(revision) -> None:
    assert revision.approval_required is True
    assert revision.execution_status == "not_executed"
    assert revision.writes_attempted == 0
    assert revision.side_effects_detected == 0
    assert revision.audit_language == FOLDER_REVISION_PROPOSAL_ONLY
    assert folder_revision_contains_execution_claim(revision) is False


def test_revision_can_add_finance_focus_without_execution() -> None:
    revision = _revision(
        "AI-OWNER-001",
        "agrega pagos de operador y proveedores a la vista",
    )

    assert revision.revision_id.startswith("ofr_")
    assert revision.revision_status == REVISION_PROPOSED
    assert revision.base_folder_id.startswith("ofp_")
    assert revision.base_preview_id.startswith("bdp_")
    assert "finance" in revision.changed_sections
    assert "operations" in revision.unchanged_sections
    _assert_revision_safety(revision)


def test_revision_can_separate_marketing_materiality_without_execution(
) -> None:
    revision = _revision(
        "AI-OWNER-001",
        "separa marketing y materialidad fotografica",
    )

    assert revision.revision_status == REVISION_PROPOSED
    assert "marketing_materiality" in revision.changed_sections
    _assert_revision_safety(revision)


def test_revision_preserves_missing_medical_evidence() -> None:
    revision = _revision(
        "AI-OWNER-018",
        "marca servicios medicos y accidentes como faltantes",
    )

    assert revision.revision_status == REVISION_PROPOSED
    assert "medical" in revision.changed_sections
    assert "missing" in revision.changed_sections
    assert "medical/event_incident" in revision.missing_evidence
    assert "document" in revision.missing_evidence
    _assert_revision_safety(revision)


def test_write_like_revision_request_fails_closed() -> None:
    revision = _revision(
        "AI-OWNER-028",
        "actualizala y manda el reporte al operador",
    )

    assert revision.revision_status == BLOCKED_WRITE_DISABLED
    assert revision.blocked_reason == BLOCKED_WRITE_DISABLED
    assert revision.changed_sections == []
    assert "operations" in revision.unchanged_sections
    _assert_revision_safety(revision)


def test_full_owner_eval_set_builds_safe_revisions() -> None:
    summary = evaluate_owner_folder_revision_set(_prompts())

    assert summary["total"] == 30
    assert summary["status_counts"][REVISION_PROPOSED] == 30
    assert summary["writes_attempted"] == 0
    assert summary["side_effects_detected"] == 0
    assert summary["execution_claims_detected"] == 0


def test_full_owner_eval_set_blocks_execution_requests() -> None:
    summary = evaluate_owner_folder_revision_set(
        _prompts(),
        requested_change="creala y envia la carpeta",
    )

    assert summary["total"] == 30
    assert summary["status_counts"][BLOCKED_WRITE_DISABLED] == 30
    assert summary["writes_attempted"] == 0
    assert summary["side_effects_detected"] == 0
    assert summary["execution_claims_detected"] == 0
