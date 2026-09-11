"""Read-only CEO dashboard routes for client portfolios."""

from datetime import date
from html import escape
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from samchat.client_executive.service import (
    ClientExecutiveAccessError,
    build_client_dashboard,
    build_client_executive_summary,
)

from .dependencies import get_current_empleado, get_db_session


router = APIRouter(tags=["client-executive"])


def _require_client(current_empleado: object) -> None:
    role = str(getattr(current_empleado, "rol", "")).strip().lower()
    if role not in {"cliente", "superadmin", "super_admin"}:
        raise HTTPException(status_code=403, detail="Client executive access required.")


def _render_dashboard(payload: dict) -> str:
    cards = "".join(
        "<article><h2>{}</h2><p>Presupuesto: {:,.2f} · Real: {:,.2f} · Comprometido: {:,.2f} · Proyección: {:,.2f}</p>"
        "<p>Fuente: {} · Corte: {}</p><a href=\"/cliente/tableros/torneos/{}?edition_year={}\">Ver ficha ejecutiva</a></article>".format(
            escape(str(card["tournament_name"])), card["budget"], card["actual"], card["committed"], card["projected"],
            escape(card["source"]), escape(card["as_of"]), escape(card["tournament_id"]), int(payload["edition_year"]),
        )
        for card in payload["cards"]
    ) or "<p>No hay proyectos o torneos asignados a esta cartera.</p>"
    return (
        "<html><head><title>Tableros ejecutivos</title></head><body>"
        "<h1>Tableros ejecutivos</h1><p>Vista CEO de sólo lectura. "
        "Flujo, cartera, pagos y detalle operativo no están disponibles para este alcance.</p>"
        f"{cards}</body></html>"
    )


@router.get("/cliente/tableros", response_class=HTMLResponse)
async def client_executive_dashboard(
    session: AsyncSession = Depends(get_db_session),
    current_empleado=Depends(get_current_empleado),
    edition_year: Optional[int] = Query(None),
):
    _require_client(current_empleado)
    payload = await build_client_dashboard(
        session,
        empleado_id=str(current_empleado.id),
        edition_year=edition_year or date.today().year,
        is_superadmin=str(getattr(current_empleado, "rol", "")).strip().lower()
        in {"superadmin", "super_admin"},
    )
    return HTMLResponse(_render_dashboard(payload))


@router.get("/cliente/tableros/asistente/resumen", response_class=JSONResponse)
async def client_executive_assistant_summary(
    session: AsyncSession = Depends(get_db_session),
    current_empleado=Depends(get_current_empleado),
    edition_year: Optional[int] = Query(None),
):
    """Return a client-safe Sam summary; it has no write-capable tools."""
    _require_client(current_empleado)
    payload = await build_client_dashboard(
        session,
        empleado_id=str(current_empleado.id),
        edition_year=edition_year or date.today().year,
        is_superadmin=str(getattr(current_empleado, "rol", "")).strip().lower()
        in {"superadmin", "super_admin"},
    )
    return JSONResponse(build_client_executive_summary(payload))


@router.get("/cliente/tableros/torneos/{tournament_id}", response_class=HTMLResponse)
async def client_executive_tournament_dashboard(
    tournament_id: str,
    session: AsyncSession = Depends(get_db_session),
    current_empleado=Depends(get_current_empleado),
    edition_year: Optional[int] = Query(None),
):
    _require_client(current_empleado)
    try:
        payload = await build_client_dashboard(
            session,
            empleado_id=str(current_empleado.id),
            edition_year=edition_year or date.today().year,
            tournament_id=tournament_id,
            is_superadmin=str(getattr(current_empleado, "rol", "")).strip().lower()
            in {"superadmin", "super_admin"},
        )
    except ClientExecutiveAccessError:
        raise HTTPException(status_code=403, detail="Client portfolio access denied.")
    return HTMLResponse(_render_dashboard(payload))
