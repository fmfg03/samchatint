from __future__ import annotations

import pytest

import samchat.assistant.router as assistant_router
from samchat.assistant.institutional_artifact_registry import (
    ARTIFACTS,
    build_institutional_artifact_registry_report,
    get_institutional_artifact,
    list_institutional_artifacts,
)


def test_institutional_artifact_registry_has_unique_ids_and_required_contracts() -> None:
    ids = [item.artifact_id for item in ARTIFACTS]

    assert len(ids) == len(set(ids))
    assert "finance.platform_snapshot" in ids
    assert "sports.platform_snapshot" in ids
    assert "assistant.sports_platform_audit" in ids
    assert "accounting.historical_snapshot" in ids
    assert "tournament.soul_snapshot" in ids
    assert "assistant.owner_entity_dossier_audit" in ids

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


def test_institutional_artifact_registry_distinguishes_wired_from_unwired() -> None:
    wired = list_institutional_artifacts(wired_only=True)
    unwired = list_institutional_artifacts(status="available_not_wired")

    assert {item.artifact_id for item in wired} >= {
        "finance.platform_snapshot",
        "finance.closeout_diagnostics",
        "tournament.soul_snapshot",
        "budget.snapshot",
        "expense.accounting_preview",
    }
    assert {item.artifact_id for item in unwired} >= {
        "accounting.historical_snapshot",
        "sam_inbox.payload",
        "assistant.owner_entity_dossier_audit",
        "assistant.sports_platform_audit",
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
