from __future__ import annotations

from samchat.assistant.specialist_evidence_quality import (
    build_specialist_evidence_quality_gate,
    render_specialist_evidence_quality_gate_markdown,
)


def test_specialist_evidence_quality_gate_marks_supported_preview() -> None:
    gate = build_specialist_evidence_quality_gate(
        business_preview={
            "proposed_changes": [
                {"field": "amount", "value": 100, "evidence_id": "EV-1"}
            ],
            "found_evidence": ["EV-1"],
            "missing_evidence": [],
        },
        live_context={"matched": True, "status": "matched", "unresolved": {}},
        diagnostics={"missing": []},
    )

    assert gate["authority"] == "read_only_evidence_gate"
    assert gate["quality_status"] == "supported"
    assert gate["safe_to_continue_preview"] is True
    assert gate["safe_to_execute"] is False
    assert gate["primary_action_enabled"] is False
    assert gate["supported_change_count"] == 1
    assert gate["execution_blockers"] == []


def test_specialist_evidence_quality_gate_marks_partial_with_missing_and_precedent() -> None:
    gate = build_specialist_evidence_quality_gate(
        business_preview={
            "proposed_changes": [
                {"field": "account", "value": "1150-001-001", "evidence_id": "EV-2"},
                {"field": "date", "value": "2026-08-01", "evidence_id": None},
            ],
            "found_evidence": ["EV-2"],
            "missing_evidence": ["case-1:pending_user_note"],
        },
        live_context={
            "matched": True,
            "status": "matched",
            "unresolved": {"uuid_or_prefixes": ["NOPE0000"]},
        },
        diagnostics={"missing": ["Falta CFDI emitido"]},
        memory_context={"snippets": [{"label": "memory:abc"}]},
    )

    assert gate["quality_status"] == "partial"
    assert gate["safe_to_continue_preview"] is True
    assert gate["unbound_change_count"] == 1
    assert gate["missing_evidence_count"] == 3
    assert gate["precedent_count"] == 1
    assert "proposed_changes_without_bound_evidence" in gate["execution_blockers"]
    assert any("precedente" in caveat for caveat in gate["caveats"])


def test_specialist_evidence_quality_gate_marks_insufficient_without_current_support() -> None:
    gate = build_specialist_evidence_quality_gate(
        business_preview={
            "proposed_changes": [],
            "found_evidence": [],
            "missing_evidence": [],
        },
        live_context={"matched": False, "status": "no_matches", "unresolved": {}},
        diagnostics={"missing": []},
    )

    assert gate["quality_status"] == "insufficient"
    assert gate["safe_to_continue_preview"] is False
    assert "no_current_case_evidence" in gate["execution_blockers"]


def test_specialist_evidence_quality_gate_markdown_is_read_only() -> None:
    gate = build_specialist_evidence_quality_gate(
        business_preview={
            "proposed_changes": [
                {"field": "amount", "value": 100, "evidence_id": "EV-1"}
            ],
            "found_evidence": ["EV-1"],
            "missing_evidence": [],
        },
        live_context={"matched": True, "status": "matched", "unresolved": {}},
        diagnostics={"missing": []},
    )
    markdown = render_specialist_evidence_quality_gate_markdown(gate)

    assert "Calidad de evidencia" in markdown
    assert "Estado: supported" in markdown
    assert "compuerta read-only" in markdown
    assert "no autoriza ni ejecuta" in markdown
