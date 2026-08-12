from pathlib import Path

from samchat.assistant.owner_folder_builder import (
    build_owner_prompt_folder_proposal,
)
from samchat.assistant.owner_folder_revision import (
    BLOCKED_WRITE_DISABLED,
    revise_owner_folder_proposal,
)
from samchat.assistant.owner_needs_eval import parse_owner_needs_eval_set
from samchat.assistant.owner_response_pack import (
    OPERATOR_RESPONSE_PACK_ONLY,
    SAFETY_BLOCKED_WRITE_DISABLED,
    SOURCE_FOLDER_PROPOSAL,
    SOURCE_FOLDER_REVISION,
    build_response_pack_from_proposal,
    build_response_pack_from_revision,
    evaluate_response_pack_set,
    response_pack_contains_execution_claim,
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


def _proposal(prompt_id: str):
    return build_owner_prompt_folder_proposal(_prompt(prompt_id))


def _assert_pack_safety(pack) -> None:
    assert pack.execution_status == "not_executed"
    assert pack.writes_attempted == 0
    assert pack.side_effects_detected == 0
    assert pack.audit_language == OPERATOR_RESPONSE_PACK_ONLY
    assert response_pack_contains_execution_claim(pack) is False


def test_proposal_response_pack_summarizes_owner_folder() -> None:
    proposal = _proposal("AI-OWNER-001")
    pack = build_response_pack_from_proposal(proposal)

    assert pack.response_id.startswith("orp_")
    assert pack.source_type == SOURCE_FOLDER_PROPOSAL
    assert pack.source_id == proposal.folder_id
    assert "Propuesta de carpeta" in pack.headline
    assert "solo lectura" in pack.summary
    assert "no se ejecuto" in pack.summary
    assert any("Revisar secciones" in step for step in pack.plan)
    assert "team" in pack.missing_evidence
    assert pack.proposed_changes
    assert "approval_required=true" in pack.approval_boundary
    assert pack.next_questions
    _assert_pack_safety(pack)


def test_revision_response_pack_includes_changed_and_unchanged_sections(
) -> None:
    proposal = _proposal("AI-OWNER-001")
    revision = revise_owner_folder_proposal(
        proposal,
        "agrega pagos de operador y proveedores a la vista",
    )
    pack = build_response_pack_from_revision(revision)

    assert pack.source_type == SOURCE_FOLDER_REVISION
    assert pack.source_id == revision.revision_id
    assert "Revision pendiente" in pack.headline
    assert any("finance" in change for change in pack.proposed_changes)
    assert any("operations" in step for step in pack.plan)
    _assert_pack_safety(pack)


def test_medical_response_pack_declares_missing_concrete_evidence() -> None:
    proposal = _proposal("AI-OWNER-018")
    pack = build_response_pack_from_proposal(proposal)

    assert "medical/event_incident" in pack.missing_evidence
    assert "No tengo evidencia concreta cargada" in pack.summary
    assert "servicios medicos" in pack.summary
    assert "accidentes" in pack.summary
    assert "seguros" in pack.summary
    assert "traslados" in pack.summary
    _assert_pack_safety(pack)


def test_blocked_write_revision_response_pack_explains_block() -> None:
    proposal = _proposal("AI-OWNER-028")
    revision = revise_owner_folder_proposal(
        proposal,
        "actualizala y manda el reporte al operador",
    )
    pack = build_response_pack_from_revision(revision)

    assert revision.revision_status == BLOCKED_WRITE_DISABLED
    assert pack.safety_status == SAFETY_BLOCKED_WRITE_DISABLED
    assert "bloqueada" in pack.headline.lower()
    assert "escrituras estan deshabilitadas" in pack.summary
    assert BLOCKED_WRITE_DISABLED in pack.approval_boundary
    _assert_pack_safety(pack)


def test_full_owner_eval_set_builds_safe_response_packs() -> None:
    summary = evaluate_response_pack_set(_prompts())

    assert summary["proposal_pack_count"] == 30
    assert summary["revision_pack_count"] == 30
    assert summary["total"] == 60
    assert summary["writes_attempted"] == 0
    assert summary["side_effects_detected"] == 0
    assert summary["execution_claims_detected"] == 0


def test_full_owner_eval_set_blocks_execution_wording_in_packs() -> None:
    summary = evaluate_response_pack_set(
        _prompts(),
        requested_change="creala y envia la carpeta",
    )

    assert summary["proposal_pack_count"] == 30
    assert summary["revision_pack_count"] == 30
    assert summary["writes_attempted"] == 0
    assert summary["side_effects_detected"] == 0
    assert summary["execution_claims_detected"] == 0
    blocked = [
        pack
        for pack in summary["packs"]
        if pack["source_type"] == SOURCE_FOLDER_REVISION
    ]
    assert all(
        pack["safety_status"] == SAFETY_BLOCKED_WRITE_DISABLED
        for pack in blocked
    )
