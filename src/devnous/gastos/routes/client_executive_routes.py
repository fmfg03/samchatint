"""Read-only internal Direction executive surfaces.

The package name is retained temporarily for database/package compatibility.
It is not a client portal: access is derived from active organizational
positions and an assigned portfolio scope.
"""

from datetime import date
from html import escape
import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from devnous.gastos.services.access_control_service import (
    AccessControlLookupError,
    explicit_tool_decision,
    is_superadmin_role,
)
from samchat.client_executive.service import (
    ClientExecutiveAccessError,
    authorized_direction_portfolio_ids,
    build_client_dashboard,
    build_client_executive_summary,
)
from samchat.client_executive.ui import render_direction_dashboard

from .dependencies import get_current_empleado, get_db_session


router = APIRouter(tags=["direction-executive"])
DIRECTION_EXECUTIVE_TOOL = "direccion.tableros_ejecutivos"


def _is_superadmin(current_empleado: object) -> bool:
    return is_superadmin_role(getattr(current_empleado, "rol", ""))


async def _assigned_direction_portfolios(
    session: AsyncSession,
    current_empleado: object,
    *,
    action_key: str = "ver",
    require_explicit_action: bool = False,
) -> list[str]:
    """Authorize an active internal identity without role-based fallback.

    Reads require position and scope. Governed writes additionally require an
    explicit positive action rule, which never substitutes the position.
    """
    if current_empleado is None or getattr(current_empleado, "activo", True) is False:
        raise HTTPException(
            status_code=403, detail="Active internal identity required."
        )

    is_superadmin = _is_superadmin(current_empleado)
    try:
        decision = await explicit_tool_decision(
            session, current_empleado, DIRECTION_EXECUTIVE_TOOL, action_key
        )
    except AccessControlLookupError:
        raise HTTPException(
            status_code=403, detail="Direction access cannot be verified."
        )
    if decision is False:
        raise HTTPException(
            status_code=403, detail="Direction access is explicitly denied."
        )
    if require_explicit_action and decision is not True:
        raise HTTPException(
            status_code=403,
            detail="Explicit Direction write authority is required.",
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
    """Compatibility wrapper for route and focused rendering tests."""
    return render_direction_dashboard(payload)


async def _dashboard_payload(
    session: AsyncSession,
    current_empleado: object,
    edition_year: Optional[int],
    tournament_id: Optional[str] = None,
    include_operational_detail: bool = False,
) -> dict:
    await _assigned_direction_portfolios(session, current_empleado)
    try:
        return await build_client_dashboard(
            session,
            empleado_id=str(current_empleado.id),
            edition_year=edition_year or date.today().year,
            tournament_id=tournament_id,
            is_superadmin=_is_superadmin(current_empleado),
            include_operational_detail=include_operational_detail,
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
            await _dashboard_payload(
                session,
                current_empleado,
                edition_year,
                include_operational_detail=True,
            )
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
        text("""SELECT d.generated_at, p.label, d.summary, d.snapshot
            FROM client_report_drafts d
            JOIN client_executive_portfolios p ON p.id = d.portfolio_id AND p.active = TRUE
            WHERE d.state = 'published' AND d.portfolio_id = ANY(:portfolio_ids)
            ORDER BY d.published_at DESC"""),
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
                session,
                current_empleado,
                edition_year,
                tournament_id,
                include_operational_detail=True,
            )
        )
    )


# Compatibility only: existing bookmarks reach the internal surface without
# retaining a client-facing semantic or a legacy write API.
def _legacy_redirect(request: Request, destination: str) -> RedirectResponse:
    """Redirect a legacy GET while preserving every query parameter."""
    query = request.url.query
    target = f"{destination}?{query}" if query else destination
    return RedirectResponse(target, status_code=307)


@router.get("/cliente/tableros", include_in_schema=False)
async def legacy_client_dashboards_redirect(request: Request):
    return _legacy_redirect(request, "/direccion/tableros")


@router.get("/cliente/tableros/asistente/resumen", include_in_schema=False)
async def legacy_client_dashboard_summary_redirect(request: Request):
    return _legacy_redirect(request, "/direccion/tableros/asistente/resumen")


@router.get("/cliente/reportes", include_in_schema=False)
async def legacy_client_reports_redirect(request: Request):
    return _legacy_redirect(request, "/direccion/reportes")


@router.get("/cliente/tableros/torneos/{tournament_id}", include_in_schema=False)
async def legacy_client_tournament_redirect(tournament_id: str, request: Request):
    return _legacy_redirect(request, f"/direccion/tableros/torneos/{tournament_id}")
