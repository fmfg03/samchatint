from __future__ import annotations

import pytest

import samchat.assistant.router as assistant_router
from samchat.assistant.institutional_artifact_registry import (
    ARTIFACTS,
    ARTIFACT_CONNECTION_DECISIONS,
    DECISION_LABELS,
    build_institutional_artifact_connection_review,
    build_institutional_artifact_registry_report,
    get_institutional_artifact,
    get_institutional_artifact_connection_decision,
    list_institutional_artifacts,
)


def test_institutional_artifact_registry_has_unique_ids_and_required_contracts() -> None:
    ids = [item.artifact_id for item in ARTIFACTS]

    assert len(ids) == len(set(ids))
    assert "finance.platform_snapshot" in ids
    assert "sports.platform_snapshot" in ids
    assert "assistant.sports_platform_audit" in ids
    assert "assistant.sports_operations_status" in ids
    assert "accounting.historical_snapshot" in ids
    assert "assistant.historical_accounting_precedent" in ids
    assert "tournament.soul_snapshot" in ids
    assert "assistant.owner_entity_dossier_audit" in ids
    assert "assistant.owner_entity_dossier_live" in ids
    assert "assistant.soul_wizard_contract" in ids
    assert "assistant.soul_wizard_owner_pack_bridge" in ids
    assert "assistant.owner_variable_query" in ids
    assert "assistant.owner_entity_folder_workspace" in ids
    assert "assistant.owner_operator_workflow" in ids

    for item in ARTIFACTS:
        assert item.name
        assert item.purpose
        assert item.module_path
        assert item.entrypoint
        assert item.evidence_sources
        assert item.output_contract
        assert item.authority_level in {
            "read_only",
            "preview_only",
            "write_requires_approval",
        }



def test_institutional_artifact_connection_review_covers_every_artifact() -> None:
    artifact_ids = {item.artifact_id for item in ARTIFACTS}
    decision_ids = {item.artifact_id for item in ARTIFACT_CONNECTION_DECISIONS}

    assert decision_ids == artifact_ids
    assert set(DECISION_LABELS) == {
        "connect_now",
        "keep_internal",
        "merge_with_another",
        "obsolete",
        "needs_data_first",
    }

    for item in ARTIFACT_CONNECTION_DECISIONS:
        assert item.rationale
        assert item.decision in DECISION_LABELS
        if item.decision == "merge_with_another":
            assert item.merge_target in artifact_ids
        if item.decision == "needs_data_first":
            assert item.data_prerequisites


def test_institutional_artifact_connection_review_groups_actionable_verdicts() -> None:
    review = build_institutional_artifact_connection_review()

    assert review["review_id"] == "samchat_institutional_artifact_connection_review_v1"
    assert review["read_only"] is True
    assert review["artifact_count"] == len(ARTIFACTS)
    assert review["decision_labels"]["connect_now"] == "conectar ahora"
    assert "assistant.owner_entity_folder_workspace" in review["safe_to_wire_now"]
    assert "finance.closeout_diagnostics" in review["safe_to_wire_now"]
    assert "sports.platform_snapshot" in review["merge_queue"]
    assert "sports.director_general_entity_dossier" in review["merge_queue"]
    assert "assistant.owner_operator_workflow" in review["merge_queue"]
    assert "assistant.owner_entity_dossier_audit" in review["internal_only"]
    assert "tournament.soul_snapshot" in review["needs_data_first"]
    assert "accounting.historical_snapshot" in review["needs_data_first"]
    assert "sam_inbox.payload" in review["needs_data_first"]
    assert any("does not connect new runtime tools" in item for item in review["non_claims"])

    folder_item = next(
        item for item in review["items"] if item["artifact_id"] == "assistant.owner_entity_folder_workspace"
    )
    assert folder_item["connection_review"]["decision"] == "connect_now"
    assert folder_item["connection_review"]["label"] == "conectar ahora"


def test_institutional_artifact_connection_decision_lookup() -> None:
    decision = get_institutional_artifact_connection_decision("sam_inbox.payload")

    assert decision is not None
    assert decision.decision == "needs_data_first"
    assert get_institutional_artifact_connection_decision("missing") is None


def test_institutional_artifact_registry_distinguishes_wired_from_unwired() -> None:
    wired = list_institutional_artifacts(wired_only=True)
    unwired = list_institutional_artifacts(status="available_not_wired")

    assert {item.artifact_id for item in wired} >= {
        "finance.platform_snapshot",
        "finance.closeout_diagnostics",
        "tournament.soul_snapshot",
        "budget.snapshot",
        "expense.accounting_preview",
        "assistant.sports_operations_status",
        "assistant.owner_entity_dossier_live",
        "assistant.historical_accounting_precedent",
    }
    assert {item.artifact_id for item in unwired} >= {
        "accounting.historical_snapshot",
        "sam_inbox.payload",
        "assistant.owner_entity_dossier_audit",
        "assistant.sports_platform_audit",
        "assistant.owner_operator_workflow",
    }
    assert all(item.assistant_tool or item.canonical_action for item in wired)
    assert all(item.next_wiring_step for item in unwired)
    partial = list_institutional_artifacts(status="partial")
    assert {item.artifact_id for item in partial} >= {"sports.director_general_entity_dossier", "sports.platform_snapshot"}


def test_institutional_artifact_lookup_and_domain_filtering() -> None:
    artifact = get_institutional_artifact("finance.closeout_diagnostics")

    assert artifact is not None
    assert artifact.domain == "accounting"
    assert artifact.assistant_tool == "finance_closeout_diagnostics"
    assert get_institutional_artifact("missing") is None

    accounting = list_institutional_artifacts(domain="accounting")
    assert {item.artifact_id for item in accounting} >= {
        "finance.closeout_diagnostics",
        "expense.accounting_preview",
    }


def test_institutional_artifact_registry_report_is_read_only_summary() -> None:
    report = build_institutional_artifact_registry_report()

    assert report["registry_id"] == "samchat_institutional_artifact_registry_v1"
    assert report["read_only"] is True
    assert report["artifact_count"] == len(ARTIFACTS)
    assert report["by_status"]["wired"] >= 1
    assert report["by_status"]["available_not_wired"] >= 1
    assert "finance.closeout_diagnostics" in report["wired_artifacts"]
    assert "accounting.historical_snapshot" in report["not_wired_artifacts"]


@pytest.mark.asyncio
async def test_institutional_artifacts_router_tool_filters_read_only_registry() -> None:
    result = await assistant_router._run_read_tool(
        "assistant_institutional_artifacts",
        {"domain": "accounting", "wired_only": True},
        gastos_session=object(),
        tournament_key_default=None,
        current_role="admin",
    )

    assert result["registry_id"] == "samchat_institutional_artifact_registry_v1"
    assert result["read_only"] is True
    assert result["filters"] == {
        "domain": "accounting",
        "status": None,
        "wired_only": True,
    }
    artifact_ids = {item["artifact_id"] for item in result["artifacts"]}
    assert "finance.closeout_diagnostics" in artifact_ids
    assert "expense.accounting_preview" in artifact_ids
    assert "accounting.historical_snapshot" not in artifact_ids


def test_institutional_registry_exposes_owner_pack_readiness_tool() -> None:
    from samchat.assistant.institutional_artifact_registry import list_institutional_artifacts

    artifacts = {item.artifact_id: item for item in list_institutional_artifacts(domain="owner_pack")}

    assert "assistant.owner_pack_readiness" in artifacts
    spec = artifacts["assistant.owner_pack_readiness"]
    assert spec.status == "wired"
    assert spec.authority_level == "read_only"
    assert spec.assistant_tool == "assistant_owner_pack_readiness"
    assert "missing_evidence" in spec.output_contract


def test_institutional_registry_exposes_sports_operations_status_tool() -> None:
    artifacts = {item.artifact_id: item for item in list_institutional_artifacts(domain="operations")}

    spec = artifacts["assistant.sports_operations_status"]
    assert spec.status == "wired"
    assert spec.authority_level == "read_only"
    assert spec.assistant_tool == "assistant_sports_operations_status"
    assert "wizard_alignment" in spec.output_contract


def test_institutional_registry_exposes_owner_entity_dossier_live_tool() -> None:
    artifacts = {item.artifact_id: item for item in list_institutional_artifacts(domain="owner_pack")}

    spec = artifacts["assistant.owner_entity_dossier_live"]
    assert spec.status == "wired"
    assert spec.authority_level == "read_only"
    assert spec.assistant_tool == "assistant_owner_entity_dossier_live"
    assert "non_claims" in spec.output_contract


def test_institutional_registry_exposes_historical_accounting_precedent_tool() -> None:
    artifacts = {item.artifact_id: item for item in list_institutional_artifacts(domain="institutional_memory")}

    spec = artifacts["assistant.historical_accounting_precedent"]
    assert spec.status == "wired"
    assert spec.authority_level == "read_only"
    assert spec.assistant_tool == "assistant_historical_accounting_precedent"
    assert "candidates" in spec.output_contract


def test_institutional_registry_reflects_soul_wizard_reality_sync() -> None:
    artifacts = {item.artifact_id: item for item in list_institutional_artifacts(domain="tournament")}

    spec = artifacts["assistant.soul_wizard_contract"]
    assert spec.status == "wired"
    assert spec.authority_level == "read_only"
    assert spec.canonical_action == "assistant.soul_wizard_review"
    assert "source_tournament_snapshot?" in spec.input_contract
    assert "activation_diff" in spec.output_contract
    assert "owner_pack_bridge" in spec.output_contract
    assert "clone_metadata" in spec.output_contract
    assert "explicit review/approval" in (spec.next_wiring_step or "")


def test_institutional_registry_exposes_soul_wizard_owner_pack_bridge() -> None:
    artifacts = {item.artifact_id: item for item in list_institutional_artifacts(domain="owner_pack")}

    spec = artifacts["assistant.soul_wizard_owner_pack_bridge"]
    assert spec.status == "wired"
    assert spec.authority_level == "read_only"
    assert spec.entrypoint == "build_soul_wizard_owner_pack_bridge"
    assert "phases" in spec.output_contract
    assert "non_claims" in spec.output_contract
    assert "folder export" in (spec.next_wiring_step or "")



def test_institutional_registry_exposes_owner_variable_query_tool() -> None:
    artifacts = {item.artifact_id: item for item in list_institutional_artifacts(domain="owner_pack")}

    spec = artifacts["assistant.owner_variable_query"]
    assert spec.status == "wired"
    assert spec.authority_level == "read_only"
    assert spec.assistant_tool == "assistant_owner_variable_query"
    assert "question" in spec.input_contract
    assert "resolutions" in spec.output_contract
    assert "conflict_values" in spec.output_contract



def test_institutional_registry_exposes_owner_variable_answer_renderer() -> None:
    artifacts = {item.artifact_id: item for item in list_institutional_artifacts(domain="owner_pack")}

    spec = artifacts["assistant.owner_variable_answer"]
    assert spec.status == "wired"
    assert spec.authority_level == "read_only"
    assert spec.assistant_tool == "assistant_owner_variable_query"
    assert "assistant.owner_variable_query" in spec.evidence_sources
    assert "rendered_text" in spec.output_contract
    assert "safety_summary" in spec.output_contract

def test_institutional_registry_exposes_owner_entity_folder_workspace_tool() -> None:
    artifacts = {item.artifact_id: item for item in list_institutional_artifacts(domain="owner_pack")}

    spec = artifacts["assistant.owner_entity_folder_workspace"]
    assert spec.status == "wired"
    assert spec.authority_level == "preview_only"
    assert spec.assistant_tool == "assistant_owner_entity_folder_workspace"
    assert "assistant.owner_entity_dossier_live" in spec.evidence_sources
    assert "assistant.owner_pack_readiness" in spec.evidence_sources
    assert "tournament.soul_snapshot" in spec.evidence_sources
    assert "assistant.sports_operations_status" in spec.evidence_sources
    assert "finance.platform_snapshot" in spec.evidence_sources
    assert "folder_sections" in spec.output_contract
    assert "soul_wizard_payload?" in spec.input_contract
    assert "soul_wizard_plan" in spec.output_contract
    assert "non_claims" in spec.output_contract
    assert "no folder write" in (spec.next_wiring_step or "")


def test_institutional_registry_keeps_owner_operator_workflow_internal_after_slice7() -> None:
    artifacts = {item.artifact_id: item for item in list_institutional_artifacts(domain="owner_pack")}

    spec = artifacts["assistant.owner_operator_workflow"]
    assert spec.status == "available_not_wired"
    assert spec.authority_level == "preview_only"
    assert spec.assistant_tool is None
    assert spec.entrypoint == "run_owner_operator_workflow"
    assert "response_pack" in spec.output_contract
    assert "writes_attempted" in spec.output_contract
    assert "side_effects_detected" in spec.output_contract
    assert "assistant.owner_pack_readiness" in (spec.next_wiring_step or "")
    assert "assistant.owner_entity_folder_workspace" in (spec.next_wiring_step or "")
    assert "do not expose" in (spec.next_wiring_step or "")
