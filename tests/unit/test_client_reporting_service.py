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


@pytest.mark.asyncio
async def test_save_draft_rejects_schedule_from_another_portfolio():
    class Result:
        def scalar_one_or_none(self):
            return None

    class Session:
        def __init__(self):
            self.statements = []

        async def execute(self, statement, _params=None):
            self.statements.append(str(statement))
            return Result()

    session = Session()
    with pytest.raises(ValueError, match="does not match"):
        await service.save_draft(
            session,
            schedule_id="schedule",
            portfolio_id="other-portfolio",
            draft={"edition_year": 2026, "snapshot": {}, "summary": {}},
            actor_id="actor",
        )
    assert "schedule.portfolio_id = :portfolio_id" in session.statements[0]


@pytest.mark.asyncio
async def test_transition_draft_does_not_audit_lost_concurrent_update():
    class StateResult:
        def first(self):
            return type("Draft", (), {"state": "draft"})()

    class EmptyUpdateResult:
        def scalar_one_or_none(self):
            return None

    class Session:
        def __init__(self):
            self.statements = []

        async def execute(self, statement, _params=None):
            self.statements.append(str(statement))
            return StateResult() if len(self.statements) == 1 else EmptyUpdateResult()

    session = Session()
    with pytest.raises(service.ReportTransitionError, match="concurrently"):
        await service.transition_draft(
            session, draft_id="draft", target="reviewed", actor_id="actor"
        )
    assert "state = :current" in session.statements[1]
    assert len(session.statements) == 2
