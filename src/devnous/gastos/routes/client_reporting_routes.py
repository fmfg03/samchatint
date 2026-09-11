"""Internal UI for client-report schedules and immutable drafts."""
from html import escape

from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .dependencies import get_current_empleado, get_db_session
from samchat.client_reporting.service import (
    ReportTransitionError, build_report_draft, create_schedule, save_draft,
    transition_draft,
)

router = APIRouter(tags=["client-reporting"])


def _require_internal(actor: object) -> None:
    if str(getattr(actor, "rol", "")).lower() not in {"admin", "superadmin", "super_admin"}:
        raise HTTPException(status_code=403, detail="Internal report access required.")


@router.get("/admin/reportes-cliente", response_class=HTMLResponse)
async def client_report_admin(
    session: AsyncSession = Depends(get_db_session), current_empleado=Depends(get_current_empleado)
):
    _require_internal(current_empleado)
    rows = await session.execute(
        text("""SELECT d.id::text, d.state, d.generated_at, p.label
        FROM client_report_drafts d JOIN client_executive_portfolios p ON p.id = d.portfolio_id
        ORDER BY d.generated_at DESC LIMIT 50""")
    )
    def draft_row(row):
        target = "reviewed" if str(row.state) == "draft" else "published"
        action = "" if str(row.state) == "published" else (
            "<form method='post' action='/admin/reportes-cliente/borradores/{}/{}'><button>{}</button></form>"
            .format(escape(str(row.id)), target, "Revisar" if target == "reviewed" else "Publicar")
        )
        return "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
            escape(str(row.id)), escape(str(row.label)), escape(str(row.state)),
            escape(str(row.generated_at)), action
        )
    items = "".join(draft_row(row) for row in rows) or "<tr><td colspan='5'>Sin borradores.</td></tr>"
    portfolios = await session.execute(
        text("SELECT id::text, label FROM client_executive_portfolios WHERE active = TRUE ORDER BY label")
    )
    options = "".join(
        "<option value='{}'>{}</option>".format(escape(str(row.id)), escape(str(row.label)))
        for row in portfolios
    )
    schedules = await session.execute(
        text("""SELECT id::text, portfolio_id::text, frequency
        FROM client_report_schedules WHERE active = TRUE ORDER BY created_at DESC LIMIT 50""")
    )
    schedule_rows = "".join(
        "<tr><td>{}</td><td>{}</td><td>{}</td><td><form method='post' action='/admin/reportes-cliente/borradores'>"
        "<input type='hidden' name='schedule_id' value='{}'><input type='hidden' name='portfolio_id' value='{}'>"
        "<button>Generar borrador</button></form></td></tr>".format(
            escape(str(row.id)), escape(str(row.portfolio_id)), escape(str(row.frequency)),
            escape(str(row.id)), escape(str(row.portfolio_id))
        )
        for row in schedules
    ) or "<tr><td colspan='4'>Sin configuraciones.</td></tr>"
    page = """<html><head><title>Reportes cliente</title></head><body>
    <h1>Autorreporteador de cliente</h1>
    <p>Configuración y borradores internos. No hay envíos automáticos en esta fase.</p>
    <form method='post' action='/admin/reportes-cliente/configuraciones'>
    <label>Cartera <select name='portfolio_id'>{options}</select></label>
    <select name='frequency'><option value='weekly'>Semanal</option><option value='monthly'>Mensual</option></select>
    <button>Crear configuración</button></form>
    <h2>Configuraciones</h2><table><thead><tr><th>ID</th><th>Cartera</th><th>Frecuencia</th><th></th></tr></thead>
    <tbody>{schedule_rows}</tbody></table>
    <h2>Borradores</h2>
    <table><thead><tr><th>ID</th><th>Cartera</th><th>Estado</th><th>Generado</th><th></th></tr></thead>
    <tbody>{items}</tbody></table></body></html>""".format(options=options, schedule_rows=schedule_rows, items=items)
    return HTMLResponse(page)


@router.post("/admin/reportes-cliente/configuraciones")
async def client_report_schedule_create(
    portfolio_id: str = Form(...), frequency: str = Form(...),
    session: AsyncSession = Depends(get_db_session), current_empleado=Depends(get_current_empleado)
):
    _require_internal(current_empleado)
    await create_schedule(session, portfolio_id=portfolio_id, frequency=frequency, actor_id=str(current_empleado.id))
    await session.commit()
    return RedirectResponse("/admin/reportes-cliente", status_code=303)


@router.post("/admin/reportes-cliente/borradores")
async def client_report_draft_create(
    schedule_id: str = Form(...), portfolio_id: str = Form(...),
    session: AsyncSession = Depends(get_db_session), current_empleado=Depends(get_current_empleado)
):
    _require_internal(current_empleado)
    schedule = (
        await session.execute(
            text(
                "SELECT portfolio_id::text AS portfolio_id FROM client_report_schedules "
                "WHERE id = :schedule_id AND active = TRUE"
            ),
            {"schedule_id": schedule_id},
        )
    ).first()
    if schedule is None:
        raise HTTPException(status_code=404, detail="Active client report schedule not found.")
    # The form value is untrusted. The schedule is the canonical portfolio owner.
    portfolio_id = str(schedule.portfolio_id)
    draft = await build_report_draft(session, portfolio_id=portfolio_id, edition_year=date.today().year)
    await save_draft(session, schedule_id=schedule_id, portfolio_id=portfolio_id, draft=draft, actor_id=str(current_empleado.id))
    await session.commit()
    return RedirectResponse("/admin/reportes-cliente", status_code=303)


@router.post("/admin/reportes-cliente/borradores/{draft_id}/{target}")
async def client_report_draft_transition(
    draft_id: str, target: str, session: AsyncSession = Depends(get_db_session),
    current_empleado=Depends(get_current_empleado)
):
    _require_internal(current_empleado)
    try:
        await transition_draft(session, draft_id=draft_id, target=target, actor_id=str(current_empleado.id))
    except ReportTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    await session.commit()
    return RedirectResponse("/admin/reportes-cliente", status_code=303)
