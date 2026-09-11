"""Read-model-backed, non-delivering client report drafts."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any
from uuid import uuid4

from sqlalchemy import text

from samchat.client_executive.service import (
    build_client_executive_summary,
    build_portfolio_dashboard,
)


REPORT_STATES = frozenset({"draft", "reviewed", "published"})
ALLOWED_TRANSITIONS = {"draft": {"reviewed"}, "reviewed": {"published"}, "published": set()}


class ReportTransitionError(ValueError):
    """Raised for an invalid or unauthorized report state transition."""


def advance_report_state(current: str, target: str) -> str:
    if current not in REPORT_STATES or target not in ALLOWED_TRANSITIONS.get(current, set()):
        raise ReportTransitionError("Invalid client report state transition.")
    return target


async def ensure_client_reporting_schema(session: Any) -> None:
    """Explicit provisioner-only schema for client report configuration."""
    for statement in (
        """CREATE TABLE IF NOT EXISTS client_report_schedules (
        id UUID PRIMARY KEY, portfolio_id UUID NOT NULL REFERENCES client_executive_portfolios(id),
        frequency VARCHAR(20) NOT NULL, active BOOLEAN NOT NULL DEFAULT TRUE,
        created_by_empleado_id UUID REFERENCES empleados(id), created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""",
        """CREATE TABLE IF NOT EXISTS client_report_drafts (
        id UUID PRIMARY KEY, schedule_id UUID REFERENCES client_report_schedules(id),
        portfolio_id UUID NOT NULL REFERENCES client_executive_portfolios(id),
        edition_year INTEGER NOT NULL, state VARCHAR(20) NOT NULL,
        snapshot JSONB NOT NULL, summary JSONB NOT NULL,
        generated_by_empleado_id UUID REFERENCES empleados(id), generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        reviewed_at TIMESTAMPTZ NULL, published_at TIMESTAMPTZ NULL)""",
        """CREATE TABLE IF NOT EXISTS client_report_audit_logs (
        id UUID PRIMARY KEY, draft_id UUID NULL REFERENCES client_report_drafts(id),
        actor_empleado_id UUID REFERENCES empleados(id), action VARCHAR(60) NOT NULL,
        detail JSONB NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""",
    ):
        await session.execute(text(statement))


async def create_schedule(
    session: Any, *, portfolio_id: str, frequency: str, actor_id: str
) -> str:
    if frequency not in {"weekly", "monthly"}:
        raise ValueError("Frequency must be weekly or monthly.")
    schedule_id = str(uuid4())
    await session.execute(
        text("""INSERT INTO client_report_schedules
        (id, portfolio_id, frequency, created_by_empleado_id) VALUES
        (:id, :portfolio_id, :frequency, :actor_id)"""),
        {"id": schedule_id, "portfolio_id": portfolio_id, "frequency": frequency, "actor_id": actor_id},
    )
    await session.execute(
        text("""INSERT INTO client_report_audit_logs (id, actor_empleado_id, action, detail)
        VALUES (:id, :actor_id, 'schedule_created', CAST(:detail AS JSONB))"""),
        {"id": str(uuid4()), "actor_id": actor_id,
         "detail": json.dumps({"schedule_id": schedule_id, "portfolio_id": portfolio_id, "frequency": frequency})},
    )
    return schedule_id


async def save_draft(
    session: Any, *, schedule_id: str, portfolio_id: str, draft: dict[str, Any], actor_id: str
) -> str:
    draft_id = str(uuid4())
    inserted_id = (
        await session.execute(
            text("""INSERT INTO client_report_drafts
        (id, schedule_id, portfolio_id, edition_year, state, snapshot, summary, generated_by_empleado_id)
        SELECT :id, schedule.id, schedule.portfolio_id, :edition_year, 'draft',
        CAST(:snapshot AS JSONB), CAST(:summary AS JSONB), :actor_id
        FROM client_report_schedules schedule
        WHERE schedule.id = :schedule_id AND schedule.portfolio_id = :portfolio_id
        RETURNING id"""),
            {"id": draft_id, "schedule_id": schedule_id, "portfolio_id": portfolio_id,
             "edition_year": draft["edition_year"], "snapshot": json.dumps(draft["snapshot"]),
             "summary": json.dumps(draft["summary"]), "actor_id": actor_id},
        )
    ).scalar_one_or_none()
    if inserted_id is None:
        raise ValueError("Client report schedule does not match the portfolio.")
    await session.execute(
        text("""INSERT INTO client_report_audit_logs (id, draft_id, actor_empleado_id, action)
        VALUES (:id, :draft_id, :actor_id, 'draft_generated')"""),
        {"id": str(uuid4()), "draft_id": draft_id, "actor_id": actor_id},
    )
    return draft_id


async def transition_draft(
    session: Any, *, draft_id: str, target: str, actor_id: str
) -> None:
    row = (
        await session.execute(
            text("SELECT state FROM client_report_drafts WHERE id = :id"),
            {"id": draft_id},
        )
    ).first()
    if row is None:
        raise ReportTransitionError("Client report draft not found.")
    advance_report_state(str(row.state), target)
    timestamp_column = "reviewed_at" if target == "reviewed" else "published_at"
    updated_id = (
        await session.execute(
        text(
            "UPDATE client_report_drafts SET state = :target, "
            + timestamp_column + " = NOW() WHERE id = :id AND state = :current "
            "RETURNING id"
        ),
        {"id": draft_id, "target": target, "current": str(row.state)},
        )
    ).scalar_one_or_none()
    if updated_id is None:
        raise ReportTransitionError("Client report draft changed concurrently.")
    await session.execute(
        text("""INSERT INTO client_report_audit_logs (id, draft_id, actor_empleado_id, action)
        VALUES (:id, :draft_id, :actor_id, :action)"""),
        {"id": str(uuid4()), "draft_id": draft_id, "actor_id": actor_id,
         "action": "draft_" + target},
    )


async def build_report_draft(
    session: Any, *, portfolio_id: str, edition_year: int
) -> dict[str, Any]:
    """Freeze the client-safe CEO read model; this function never delivers it."""
    dashboard = await build_portfolio_dashboard(
        session, portfolio_id=portfolio_id, edition_year=edition_year
    )
    return {
        "state": "draft",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "edition_year": edition_year,
        "snapshot": dashboard,
        "summary": build_client_executive_summary(dashboard),
        "delivery": "not_requested",
    }
