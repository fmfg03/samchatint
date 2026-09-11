"""Read-only internal Direction executive surfaces.

The package name is retained temporarily for database/package compatibility.
It is not a client portal: access is derived from active organizational
positions and an assigned portfolio scope.
"""

from datetime import date
from html import escape
import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from devnous.gastos.services.access_control_service import (
    explicit_tool_decision,
    is_superadmin_role,
)
from samchat.client_executive.service import (
    ClientExecutiveAccessError,
    authorized_direction_portfolio_ids,
    build_client_dashboard,
    build_client_executive_summary,
)

from .dependencies import get_current_empleado, get_db_session


router = APIRouter(tags=["direction-executive"])
DIRECTION_EXECUTIVE_TOOL = "direccion.tableros_ejecutivos"


def _is_superadmin(current_empleado: object) -> bool:
    return is_superadmin_role(getattr(current_empleado, "rol", ""))


async def _assigned_direction_portfolios(
    session: AsyncSession, current_empleado: object
) -> list[str]:
    """Authorize an active internal identity without role-based fallback."""
    if current_empleado is None or getattr(current_empleado, "activo", True) is False:
        raise HTTPException(
            status_code=403, detail="Active internal identity required."
        )

    is_superadmin = _is_superadmin(current_empleado)
    decision = await explicit_tool_decision(
        session, current_empleado, DIRECTION_EXECUTIVE_TOOL
    )
    if decision is False and not is_superadmin:
        raise HTTPException(
            status_code=403, detail="Direction access is explicitly denied."
        )

    portfolio_ids = await authorized_direction_portfolio_ids(
        session,
        str(getattr(current_empleado, "id", "")),
        is_superadmin=is_superadmin,
    )
    if not portfolio_ids:
        raise HTTPException(
            status_code=403,
            detail="An active Direction position with assigned scope is required.",
        )
    return portfolio_ids


def _render_dashboard(payload: dict) -> str:
    cards = (
        "".join(
            "<article><h2>{}</h2><p>Presupuesto: {:,.2f} · Real: {:,.2f} · Comprometido: {:,.2f} · Proyección: {:,.2f}</p>"
            '<p>Fuente: {} · Corte: {}</p><a href="/direccion/tableros/torneos/{}?edition_year={}">Ver ficha ejecutiva</a></article>'.format(
                escape(str(card["tournament_name"])),
                card["budget"],
                card["actual"],
                card["committed"],
                card["projected"],
                escape(card["source"]),
                escape(card["as_of"]),
                escape(card["tournament_id"]),
                int(payload["edition_year"]),
            )
            for card in payload["cards"]
        )
        or "<p>No hay proyectos o torneos en tu alcance asignado.</p>"
    )
    return (
        "<html><head><title>Tablero ejecutivo de Dirección</title></head><body>"
        "<h1>Tablero ejecutivo</h1><p>Vista interna de sólo lectura para el alcance asignado. "
        "Finanzas, CxC, pagos, cashflow y detalle operativo no están disponibles en esta superficie.</p>"
        f"{cards}</body></html>"
    )


async def _dashboard_payload(
    session: AsyncSession,
    current_empleado: object,
    edition_year: Optional[int],
    tournament_id: Optional[str] = None,
) -> dict:
    await _assigned_direction_portfolios(session, current_empleado)
    try:
        return await build_client_dashboard(
            session,
            empleado_id=str(current_empleado.id),
            edition_year=edition_year or date.today().year,
            tournament_id=tournament_id,
            is_superadmin=_is_superadmin(current_empleado),
        )
    except ClientExecutiveAccessError as exc:
        raise HTTPException(status_code=403, detail=str(exc))


@router.get("/direccion/tableros", response_class=HTMLResponse)
async def direction_executive_dashboard(
    session: AsyncSession = Depends(get_db_session),
    current_empleado=Depends(get_current_empleado),
    edition_year: Optional[int] = Query(None),
):
    return HTMLResponse(
        _render_dashboard(
            await _dashboard_payload(session, current_empleado, edition_year)
        )
    )


@router.get("/direccion/tableros/asistente/resumen", response_class=JSONResponse)
async def direction_executive_assistant_summary(
    session: AsyncSession = Depends(get_db_session),
    current_empleado=Depends(get_current_empleado),
    edition_year: Optional[int] = Query(None),
):
    """Return a read-only Direction summary with no write-capable tools."""
    return JSONResponse(
        build_client_executive_summary(
            await _dashboard_payload(session, current_empleado, edition_year)
        )
    )


@router.get("/direccion/reportes", response_class=HTMLResponse)
async def direction_published_reports(
    session: AsyncSession = Depends(get_db_session),
    current_empleado=Depends(get_current_empleado),
):
    """Expose only published reports whose portfolio is in Direction scope."""
    portfolio_ids = await _assigned_direction_portfolios(session, current_empleado)
    rows = await session.execute(
        text(
            """SELECT d.generated_at, p.label, d.summary, d.snapshot
            FROM client_report_drafts d
            JOIN client_executive_portfolios p ON p.id = d.portfolio_id AND p.active = TRUE
            WHERE d.state = 'published' AND d.portfolio_id = ANY(:portfolio_ids)
            ORDER BY d.published_at DESC"""
        ),
        {"portfolio_ids": portfolio_ids},
    )
    reports = (
        "".join(
            "<article><h2>{}</h2><p>{}</p><pre>{}</pre><small>Corte: {}</small></article>".format(
                escape(str(row.label)),
                escape(
                    str((row.summary or {}).get("message") or "Reporte de Dirección")
                ),
                escape(
                    json.dumps(
                        row.snapshot or {}, ensure_ascii=False, sort_keys=True, indent=2
                    )
                ),
                escape(str(row.generated_at)),
            )
            for row in rows
        )
        or "<p>No hay reportes publicados para tu alcance asignado.</p>"
    )
    return HTMLResponse(
        "<html><body><h1>Reportes de Dirección</h1>{}</body></html>".format(reports)
    )


@router.get("/direccion/tableros/torneos/{tournament_id}", response_class=HTMLResponse)
async def direction_executive_tournament_dashboard(
    tournament_id: str,
    session: AsyncSession = Depends(get_db_session),
    current_empleado=Depends(get_current_empleado),
    edition_year: Optional[int] = Query(None),
):
    return HTMLResponse(
        _render_dashboard(
            await _dashboard_payload(
                session, current_empleado, edition_year, tournament_id
            )
        )
    )


# Compatibility only: existing bookmarks reach the internal surface without
# retaining a client-facing semantic or a legacy write API.
@router.get("/cliente/tableros", include_in_schema=False)
async def legacy_client_dashboards_redirect():
    return RedirectResponse("/direccion/tableros", status_code=307)


@router.get("/cliente/tableros/asistente/resumen", include_in_schema=False)
async def legacy_client_dashboard_summary_redirect():
    return RedirectResponse("/direccion/tableros/asistente/resumen", status_code=307)


@router.get("/cliente/reportes", include_in_schema=False)
async def legacy_client_reports_redirect():
    return RedirectResponse("/direccion/reportes", status_code=307)


@router.get("/cliente/tableros/torneos/{tournament_id}", include_in_schema=False)
async def legacy_client_tournament_redirect(tournament_id: str):
    return RedirectResponse(
        f"/direccion/tableros/torneos/{tournament_id}", status_code=307
    )
