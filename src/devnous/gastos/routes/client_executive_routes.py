"""Read-only internal Direction executive surfaces.

The package name is retained temporarily for database/package compatibility.
It is not a client portal: access is derived from active organizational
positions and an assigned portfolio scope.
"""

import secrets
from datetime import date, datetime
from decimal import Decimal
from typing import Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from devnous.gastos.services.access_control_service import (
    AccessControlLookupError,
    explicit_tool_decision,
    is_superadmin_role,
)
from samchat.client_executive.conversation import (
    ContextError,
    answer_snapshot,
    load_analysis,
    load_context,
    save_turn,
    sign_analysis,
    sign_context,
)
from samchat.client_executive.home import TZ, build_home, resolve_scope
from samchat.client_executive.home_ui import render_home
from samchat.client_executive.reports import build_report, render_published_report
from samchat.client_executive.service import (
    ClientExecutiveAccessError,
    authorized_direction_portfolio_ids,
    build_client_dashboard,
    build_client_executive_summary,
)
from samchat.client_executive.ui import render_direction_dashboard
from samchat.executive.exporter import (
    generate_direction_report_pdf,
    generate_direction_report_xlsx,
)

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


async def direction_entry_visible(
    session: AsyncSession,
    current_empleado: object,
) -> bool:
    """Return whether Direction should be discoverable for this identity.

    Discovery follows the same read authority as the route itself: active
    internal identity, no explicit deny, and assigned Direction scope.
    """
    try:
        await _assigned_direction_portfolios(session, current_empleado)
    except HTTPException as exc:
        if exc.status_code == 403:
            return False
        raise
    return True


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
            render_published_report(
                row.label, row.summary or {}, row.snapshot or {}, str(row.generated_at)
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


class DirectionScenarioRequest(BaseModel):
    kind: Literal["expense_reduction", "collection_acceleration", "payment_delay"]
    percent: Optional[Decimal] = Field(
        default=None, ge=0, le=100, max_digits=5, decimal_places=2
    )
    days: Optional[int] = Field(default=None, ge=0, le=365, strict=True)
    basis: Literal["observed_expense", "future_expense"] = "observed_expense"
    tournament_id: Optional[str] = Field(default=None, max_length=36)
    concept_id: Optional[str] = Field(default=None, max_length=36)


class DirectionReportRequest(BaseModel):
    context_token: str = Field(min_length=1, max_length=100000)
    analysis_token: Optional[str] = Field(default=None, max_length=100000)


class DirectionQueryRequest(BaseModel):
    context_token: str = Field(min_length=1, max_length=100000)
    metric_id: str = Field(min_length=1, max_length=40)
    question: str = Field(min_length=1, max_length=2000)
    conversation_id: Optional[str] = Field(default=None, max_length=36)
    scenario: Optional[DirectionScenarioRequest] = None
    analysis_token: Optional[str] = Field(default=None, max_length=100000)


def _selection(value: str) -> Optional[str]:
    if not value:
        return None
    try:
        return str(UUID(value))
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail="Selector de alcance no válido."
        ) from exc


async def _direction_source_access(session, employee) -> dict[str, bool]:
    """A specific source denial still wins over Direction cross-domain visibility."""
    result = {}
    for key, tool in (("budget", "admin.presupuestos"), ("finance", "admin.finanzas")):
        try:
            result[key] = (
                await explicit_tool_decision(session, employee, tool, "ver")
                is not False
            )
        except AccessControlLookupError:
            result[key] = False
    return result


@router.get("/direccion/inicio", response_class=HTMLResponse)
async def direction_home(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_empleado=Depends(get_current_empleado),
    edition_year: Optional[int] = Query(None, ge=2000, le=2100),
    portfolio_id: str = Query("", max_length=36),
    tournament_id: str = Query("", max_length=36),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
):
    await _assigned_direction_portfolios(session, current_empleado)
    access = await _direction_source_access(session, current_empleado)
    try:
        snapshot, scope = await build_home(
            session,
            actor=str(current_empleado.id),
            superadmin=_is_superadmin(current_empleado),
            year=edition_year or datetime.now(TZ).year,
            portfolio_id=_selection(portfolio_id),
            tournament_id=_selection(tournament_id),
            start=date_from,
            end=date_to,
            source_access=access,
        )
        token = sign_context(snapshot, str(current_empleado.id))
    except ClientExecutiveAccessError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ContextError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    csrf = request.session.setdefault(
        "direction_context_csrf", secrets.token_urlsafe(32)
    )
    return HTMLResponse(
        render_home(snapshot, scope, token=token, csrf=csrf),
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


async def _verified_direction_context(request, payload, session, employee) -> dict:
    await _assigned_direction_portfolios(session, employee)
    expected = str(request.session.get("direction_context_csrf") or "")
    submitted = request.headers.get("X-Direction-CSRF", "")
    if not expected or not secrets.compare_digest(expected, submitted):
        raise HTTPException(
            status_code=403, detail="La consulta no pertenece a esta sesión."
        )
    try:
        snapshot = load_context(payload.context_token, str(employee.id))
        selected_scope = snapshot["scope"]
        current = await resolve_scope(
            session,
            actor=str(employee.id),
            superadmin=_is_superadmin(employee),
            portfolio_id=selected_scope["portfolio_id"],
            tournament_id=selected_scope["tournament_id"],
        )
        if (
            sorted(snapshot["tournament_ids"])
            != sorted(t["id"] for t in current["selected"])
            or sorted(selected_scope["portfolio_ids"])
            != sorted(current["portfolio_ids"])
            or snapshot["source_access"]
            != await _direction_source_access(session, employee)
        ):
            raise ContextError(
                "El alcance o los permisos cambiaron; actualiza el tablero."
            )
        return snapshot
    except ClientExecutiveAccessError as exc:
        raise HTTPException(
            status_code=403, detail="El alcance ya no está autorizado."
        ) from exc
    except ContextError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/direccion/tableros/asistente/consulta", response_class=JSONResponse)
async def direction_context_query(
    request: Request,
    payload: DirectionQueryRequest,
    session: AsyncSession = Depends(get_db_session),
    current_empleado=Depends(get_current_empleado),
):
    snapshot = await _verified_direction_context(
        request, payload, session, current_empleado
    )
    try:
        previous = (
            load_analysis(
                payload.analysis_token,
                snapshot["snapshot_id"],
                str(current_empleado.id),
            )
            if payload.analysis_token
            else None
        )
        answer = answer_snapshot(
            snapshot,
            payload.metric_id,
            payload.question,
            scenario=(
                payload.scenario.model_dump(mode="json", exclude_none=True)
                if payload.scenario
                else None
            ),
            previous=previous,
        )
        answer["analysis_token"] = sign_analysis(
            answer, snapshot["snapshot_id"], str(current_empleado.id)
        )
        answer["report_ready"] = True
        answer["conversation_id"] = await save_turn(
            session,
            actor=str(current_empleado.id),
            snapshot=snapshot,
            metric_id=payload.metric_id,
            question=payload.question,
            answer=answer,
            conversation_id=payload.conversation_id,
        )
    except ClientExecutiveAccessError as exc:
        raise HTTPException(
            status_code=403, detail="El alcance de consulta ya no está autorizado."
        ) from exc
    except ContextError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return JSONResponse(answer, headers={"Cache-Control": "no-store"})


@router.post("/direccion/reportes/exportar/{output_format}")
async def direction_export_report(
    output_format: Literal["pdf", "xlsx"],
    request: Request,
    payload: DirectionReportRequest,
    session: AsyncSession = Depends(get_db_session),
    current_empleado=Depends(get_current_empleado),
):
    snapshot = await _verified_direction_context(
        request, payload, session, current_empleado
    )
    try:
        analysis = (
            load_analysis(
                payload.analysis_token,
                snapshot["snapshot_id"],
                str(current_empleado.id),
            )
            if payload.analysis_token
            else None
        )
        report = build_report(snapshot, analysis)
        from starlette.concurrency import run_in_threadpool

        generator = (
            generate_direction_report_pdf
            if output_format == "pdf"
            else generate_direction_report_xlsx
        )
        content = await run_in_threadpool(generator, report)
    except ContextError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (ImportError, RuntimeError) as exc:
        raise HTTPException(
            status_code=503, detail="El generador de reportes no está disponible."
        ) from exc
    media = (
        "application/pdf"
        if output_format == "pdf"
        else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    filename = f"consejo-{snapshot['snapshot_id'][:12]}.{output_format}"
    return Response(
        content,
        media_type=media,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "X-Direction-Snapshot": snapshot["snapshot_id"],
            "X-Direction-Report": report["report_id"],
        },
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
