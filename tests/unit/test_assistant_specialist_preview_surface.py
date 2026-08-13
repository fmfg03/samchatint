from __future__ import annotations

import pytest

from samchat.assistant.specialist_preview_renderer import (
    SECTION_AUTHORITY,
    SECTION_PROPOSED_CHANGES,
    SECTION_SUMMARY,
)
from samchat.assistant.specialist_preview_surface import (
    detect_specialist_preview_task_id,
    render_specialist_preview_surface,
)


def test_detect_specialist_preview_requires_explicit_preview_intent() -> None:
    assert (
        detect_specialist_preview_task_id(
            "Muestra preview especialista SAMCHAT-CXC-COLLECTION-001"
        )
        == "SAMCHAT-CXC-COLLECTION-001"
    )
    assert detect_specialist_preview_task_id("SAMCHAT-CXC-COLLECTION-001") is None
    assert detect_specialist_preview_task_id("preview SAMCHAT-UNKNOWN-001") is None


def test_specialist_preview_surface_is_structured_and_inert() -> None:
    surface = render_specialist_preview_surface("SAMCHAT-CXC-COLLECTION-001")
    payload = surface.preview_render.to_dict()

    assert surface.provider_called is False
    assert surface.writes_attempted is False
    assert payload["primary_action_enabled"] is False
    assert payload["execution_status"] == "not_executed"
    assert [section["section_id"] for section in payload["sections"]][:3] == [
        SECTION_SUMMARY,
        SECTION_PROPOSED_CHANGES,
        "evidence",
    ]
    assert payload["sections"][-1]["section_id"] == SECTION_AUTHORITY
    assert payload["sections"][-1]["status"] == "blocked"
    assert "Preview especialista listo" in surface.assistant_message
    assert "Frontera de autoridad" in surface.assistant_message


@pytest.mark.parametrize("task_id", ("SAMCHAT-CXC-COLLECTION-001", "SAMCHAT-FIN-AMEX-001"))
def test_specialist_preview_surface_trace_exposes_renderer_contract(task_id: str) -> None:
    surface = render_specialist_preview_surface(task_id)
    trace = surface.tool_trace()

    assert trace["specialist_preview_surface"]["stage"] == (
        "deterministic_read_only_preview_surface"
    )
    assert trace["specialist_preview_surface"]["primary_action_enabled"] is False
    assert trace["tool"] == "assistant.specialist_preview.render"
    assert trace["result"]["preview_render"] == surface.preview_render.to_dict()
    assert trace["result"]["business_preview"]["task_id"] == task_id
