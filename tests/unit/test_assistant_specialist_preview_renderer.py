from __future__ import annotations

from samchat.assistant.specialist_benchmarks import build_seed_benchmarks, run_seed_benchmark
from samchat.assistant.specialist_preview_renderer import (
    SECTION_AUTHORITY,
    SECTION_CHECKS,
    SECTION_EVIDENCE,
    SECTION_MISSING_EVIDENCE,
    SECTION_PROPOSED_CHANGES,
    SECTION_STEPS,
    SECTION_SUMMARY,
    render_specialist_business_preview,
    render_specialist_business_preview_markdown,
)


def _preview(task_id: str):
    benchmark = next(
        item for item in build_seed_benchmarks() if item.task.task_id == task_id
    )
    return run_seed_benchmark(benchmark).business_preview


def test_specialist_preview_renderer_contract_has_stable_sections() -> None:
    rendered = render_specialist_business_preview(_preview("SAMCHAT-FIN-AMEX-001"))

    assert [section.section_id for section in rendered.sections] == [
        SECTION_SUMMARY,
        SECTION_PROPOSED_CHANGES,
        SECTION_EVIDENCE,
        SECTION_MISSING_EVIDENCE,
        SECTION_STEPS,
        SECTION_CHECKS,
        SECTION_AUTHORITY,
    ]
    assert rendered.primary_action_label == "Aprobar y ejecutar"
    assert rendered.primary_action_enabled is False
    assert rendered.execution_status == "not_executed"
    assert rendered.audit_language == "preview_only"


def test_specialist_preview_renderer_surfaces_evidence_and_missing_evidence() -> None:
    rendered = render_specialist_business_preview(_preview("SAMCHAT-FIN-AMEX-001"))
    sections = {section.section_id: section for section in rendered.sections}

    evidence_items = sections[SECTION_EVIDENCE].items
    missing_items = sections[SECTION_MISSING_EVIDENCE].items

    assert {item["evidence_id"] for item in evidence_items} >= {
        "EV-AMEX-STATEMENT",
        "EV-AMEX-REPORT",
    }
    assert {item["missing"] for item in missing_items} == {
        "expense-amex-ref-28:pending_user_note"
    }
    assert sections[SECTION_MISSING_EVIDENCE].status == "warning"


def test_specialist_preview_renderer_surfaces_supported_changes() -> None:
    rendered = render_specialist_business_preview(
        _preview("SAMCHAT-CXC-COLLECTION-001")
    )
    sections = {section.section_id: section for section in rendered.sections}
    changes = {item["field"]: item for item in sections[SECTION_PROPOSED_CHANGES].items}

    assert changes["amount"]["value"] == 1972903.00
    assert changes["account"]["value"] == "1150-001-001"
    assert changes["account"]["evidence_id"] == "EV-CXC-POLICY"
    assert sections[SECTION_AUTHORITY].status == "blocked"


def test_specialist_preview_renderer_markdown_is_human_readable_and_inert() -> None:
    markdown = render_specialist_business_preview_markdown(
        _preview("SAMCHAT-CXC-COLLECTION-001")
    )

    assert "# CxC collection preview" in markdown
    assert "Tipo: accounts_receivable_collection" in markdown
    assert "Accion principal habilitada: False" in markdown
    assert "## Cambios propuestos" in markdown
    assert "account: 1150-001-001" in markdown
    assert "## Autoridad" in markdown
    assert "Ejecucion: not_executed" in markdown
