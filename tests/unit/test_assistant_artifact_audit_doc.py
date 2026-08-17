from __future__ import annotations

from pathlib import Path


DOC = Path("docs/assistant/rqf-assistant-artifact-audit-001.md")


def test_artifact_audit_doc_records_scope_and_no_runtime_wiring() -> None:
    text = DOC.read_text(encoding="utf-8")

    assert "Runtime wiring: not performed" in text
    assert "World-effect closure: not claimed" in text
    assert "No runtime behavior or production write path changes" in text


def test_artifact_audit_doc_covers_core_artifact_families() -> None:
    text = DOC.read_text(encoding="utf-8")

    required = {
        "samchat.finance_platform.service",
        "samchat.accounting_historical.service",
        "samchat.sports_platform.service",
        "samchat.sports_platform.director_general_dossier",
        "samchat.assistant.owner_pack_readiness",
        "samchat.assistant.soul_wizard",
        "samchat.assistant.specialist_agents",
        "devnous.gastos.services.*",
    }
    for item in required:
        assert item in text


def test_artifact_audit_doc_marks_supabase_as_no_new_wiring() -> None:
    text = DOC.read_text(encoding="utf-8")

    assert "Supabase-related surfaces should not receive new assistant wiring" in text
    assert "samchat.tournaments_v2.supabase_client" in text
    assert "devnous.tournaments.core.supabase_sync" in text
    assert "devnous.copa_telmex.supabase_authority" in text


def test_artifact_audit_doc_preserves_integration_queue() -> None:
    text = DOC.read_text(encoding="utf-8")

    expected_order = [
        "Artifact registry UI/assistant explanation",
        "Sports operations status wrapper",
        "Owner entity dossier live wrapper",
        "Historical accounting precedent query",
        "SOUL Wizard continuation",
        "Specialist agents after evidence contracts",
    ]
    positions = [text.index(item) for item in expected_order]

    assert positions == sorted(positions)
