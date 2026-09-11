"""Internal Direction UI for report schedules and audited drafts.

Physical ``client_*`` names remain only as a compatibility debt; this is not
an external client portal and never authorizes from a ``cliente`` role.
"""

from datetime import date
from html import escape
import json

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from samchat.client_reporting.service import (
    ReportTransitionError,
    build_report_draft,
    create_schedule,
    save_draft,
    transition_draft,
)

from .client_executive_routes import _assigned_direction_portfolios
from .dependencies import get_current_empleado, get_db_session


router = APIRouter(tags=["direction-reporting"])


async def _require_direction_reporting_scope(
    session: AsyncSession,
    current_empleado: object,
    portfolio_id: str | None = None,
    *,
    action_key: str = "ver",
    require_explicit_action: bool = False,
) -> list[str]:
    """Require the same active, position-derived scope as the executive board."""
    portfolio_ids = await _assigned_direction_portfolios(
        session,
        current_empleado,
        action_key=action_key,
        require_explicit_action=require_explicit_action,
    )
    if portfolio_id is not None and portfolio_id not in portfolio_ids:
        raise HTTPException(
            status_code=403, detail="Portfolio is outside assigned scope."
        )
    return portfolio_ids


@router.get("/direccion/reportes/gestion", response_class=HTMLResponse)
async def direction_report_management(
    session: AsyncSession = Depends(get_db_session),
    current_empleado=Depends(get_current_empleado),
):
    portfolio_ids = await _require_direction_reporting_scope(session, current_empleado)
    rows = await session.execute(
        text(
            """SELECT d.id::text, d.state, d.generated_at, d.snapshot, d.summary, p.label
            FROM client_report_drafts d
            JOIN client_executive_portfolios p ON p.id = d.portfolio_id AND p.active = TRUE
            WHERE d.portfolio_id = ANY(:portfolio_ids)
            ORDER BY d.generated_at DESC LIMIT 50"""
        ),
        {"portfolio_ids": portfolio_ids},
    )

    def draft_row(row):
        target = "reviewed" if str(row.state) == "draft" else "published"
        action = (
            ""
            if str(row.state) == "published"
            else (
                "<form method='post' action='/direccion/reportes/gestion/borradores/{}/{}'>"
                "<button>{}</button></form>".format(
                    escape(str(row.id)),
                    target,
                    "Revisar" if target == "reviewed" else "Publicar",
                )
            )
        )
        return "<tr><td>{}</td><td>{}</td><td>{}</td><td><pre>{}</pre></td><td>{}</td><td>{}</td></tr>".format(
            escape(str(row.id)),
            escape(str(row.label)),
            escape(str(row.state)),
            escape(
                json.dumps(
                    {"summary": row.summary, "snapshot": row.snapshot},
                    ensure_ascii=False,
                    sort_keys=True,
                    indent=2,
                )
            ),
            escape(str(row.generated_at)),
            action,
        )

    items = (
        "".join(draft_row(row) for row in rows)
        or "<tr><td colspan='5'>Sin borradores.</td></tr>"
    )
    portfolios = await session.execute(
        text(
            """SELECT id::text, label FROM client_executive_portfolios
            WHERE active = TRUE AND id = ANY(:portfolio_ids) ORDER BY label"""
        ),
        {"portfolio_ids": portfolio_ids},
    )
    options = "".join(
        "<option value='{}'>{}</option>".format(
            escape(str(row.id)), escape(str(row.label))
        )
        for row in portfolios
    )
    schedules = await session.execute(
        text(
            """SELECT id::text, portfolio_id::text, frequency
            FROM client_report_schedules
            WHERE active = TRUE AND portfolio_id = ANY(:portfolio_ids)
            ORDER BY created_at DESC LIMIT 50"""
        ),
        {"portfolio_ids": portfolio_ids},
    )
    schedule_rows = (
        "".join(
            "<tr><td>{}</td><td>{}</td><td>{}</td><td><form method='post' action='/direccion/reportes/gestion/borradores'>"
            "<input type='hidden' name='schedule_id' value='{}'><input type='hidden' name='portfolio_id' value='{}'>"
            "<button>Generar borrador</button></form></td></tr>".format(
                escape(str(row.id)),
                escape(str(row.portfolio_id)),
                escape(str(row.frequency)),
                escape(str(row.id)),
                escape(str(row.portfolio_id)),
            )
            for row in schedules
        )
        or "<tr><td colspan='4'>Sin configuraciones.</td></tr>"
    )
    page = """<html><head><title>Reportes de Dirección</title></head><body>
    <h1>Reportes de Dirección</h1>
    <p>Configuración, borradores y transiciones internas para el alcance asignado. No hay envíos automáticos en esta fase.</p>
    <form method='post' action='/direccion/reportes/gestion/configuraciones'>
    <label>Cartera <select name='portfolio_id'>{options}</select></label>
    <select name='frequency'><option value='weekly'>Semanal</option><option value='monthly'>Mensual</option></select>
    <button>Crear configuración</button></form>
    <h2>Configuraciones</h2><table><thead><tr><th>ID</th><th>Cartera</th><th>Frecuencia</th><th></th></tr></thead>
    <tbody>{schedule_rows}</tbody></table>
    <h2>Borradores internos auditados</h2>
    <table><thead><tr><th>ID</th><th>Cartera</th><th>Estado</th><th>Contenido</th><th>Generado</th><th></th></tr></thead>
    <tbody>{items}</tbody></table></body></html>""".format(
        options=options,
        schedule_rows=schedule_rows,
        items=items,
    )
    return HTMLResponse(page)


@router.post("/direccion/reportes/gestion/configuraciones")
async def direction_report_schedule_create(
    portfolio_id: str = Form(...),
    frequency: str = Form(...),
    session: AsyncSession = Depends(get_db_session),
    current_empleado=Depends(get_current_empleado),
):
    """Create a schedule only with explicit Direction write authority."""
    await _require_direction_reporting_scope(
        session,
        current_empleado,
        portfolio_id,
        action_key="editar",
        require_explicit_action=True,
    )
    await create_schedule(
        session,
        portfolio_id=portfolio_id,
        frequency=frequency,
        actor_id=str(current_empleado.id),
    )
    await session.commit()
    return RedirectResponse("/direccion/reportes/gestion", status_code=303)


@router.post("/direccion/reportes/gestion/borradores")
async def direction_report_draft_create(
    schedule_id: str = Form(...),
    portfolio_id: str = Form(...),
    session: AsyncSession = Depends(get_db_session),
    current_empleado=Depends(get_current_empleado),
):
    """Create an audited draft only with explicit Direction write authority."""
    portfolio_ids = await _require_direction_reporting_scope(
        session,
        current_empleado,
        action_key="editar",
        require_explicit_action=True,
    )
    schedule = (
        await session.execute(
            text(
                """SELECT portfolio_id::text AS portfolio_id FROM client_report_schedules
                WHERE id = :schedule_id AND active = TRUE
                AND portfolio_id = ANY(:portfolio_ids)"""
            ),
            {"schedule_id": schedule_id, "portfolio_ids": portfolio_ids},
        )
    ).first()
    if schedule is None:
        raise HTTPException(
            status_code=404, detail="Active Direction report schedule not found."
        )
    # The form value is untrusted. The in-scope schedule is the canonical owner.
    portfolio_id = str(schedule.portfolio_id)
    draft = await build_report_draft(
        session, portfolio_id=portfolio_id, edition_year=date.today().year
    )
    await save_draft(
        session,
        schedule_id=schedule_id,
        portfolio_id=portfolio_id,
        draft=draft,
        actor_id=str(current_empleado.id),
    )
    await session.commit()
    return RedirectResponse("/direccion/reportes/gestion", status_code=303)


@router.post("/direccion/reportes/gestion/borradores/{draft_id}/{target}")
async def direction_report_draft_transition(
    draft_id: str,
    target: str,
    session: AsyncSession = Depends(get_db_session),
    current_empleado=Depends(get_current_empleado),
):
    """Transition an in-scope draft only with explicit Direction write authority."""
    portfolio_ids = await _require_direction_reporting_scope(
        session,
        current_empleado,
        action_key="editar",
        require_explicit_action=True,
    )
    draft = (
        await session.execute(
            text(
                """SELECT portfolio_id::text AS portfolio_id FROM client_report_drafts
                WHERE id = :draft_id AND portfolio_id = ANY(:portfolio_ids)"""
            ),
            {"draft_id": draft_id, "portfolio_ids": portfolio_ids},
        )
    ).first()
    if draft is None:
        raise HTTPException(status_code=404, detail="Direction report draft not found.")
    try:
        await transition_draft(
            session,
            draft_id=draft_id,
            target=target,
            actor_id=str(current_empleado.id),
        )
    except ReportTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    await session.commit()
    return RedirectResponse("/direccion/reportes/gestion", status_code=303)


# Compatibility only. Legacy write endpoints are intentionally not retained.
@router.get("/admin/reportes-cliente", include_in_schema=False)
async def legacy_client_report_management_redirect(request: Request):
    query = request.url.query
    target = "/direccion/reportes/gestion"
    return RedirectResponse(f"{target}?{query}" if query else target, status_code=307)
