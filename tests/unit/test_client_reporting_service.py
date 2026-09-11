import pytest

from samchat.client_reporting import service


def test_report_state_transitions_are_one_way():
    assert service.advance_report_state("draft", "reviewed") == "reviewed"
    assert service.advance_report_state("reviewed", "published") == "published"
    with pytest.raises(service.ReportTransitionError):
        service.advance_report_state("draft", "published")


@pytest.mark.asyncio
async def test_draft_freezes_client_safe_snapshot(monkeypatch):
    async def dashboard(*_args, **_kwargs):
        return {"cards": [], "scope": "portfolio"}

    monkeypatch.setattr(service, "build_portfolio_dashboard", dashboard)
    draft = await service.build_report_draft(object(), portfolio_id="portfolio", edition_year=2026)
    assert draft["state"] == "draft"
    assert draft["delivery"] == "not_requested"
