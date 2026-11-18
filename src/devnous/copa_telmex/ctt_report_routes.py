"""Authenticated CTT report and download over a shared registration projection."""

import json
from dataclasses import dataclass
from hashlib import sha256
from typing import Annotated, Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from devnous.gastos.models import Empleado
from devnous.gastos.routes.dependencies import get_current_empleado, get_db_session
from samchat.assistant.tournament_registration_reports import normalize_text

from .ctt_registration_report import build_ctt_registration_report
from .ctt_registration_xlsx import (
    ctt_registration_report_tables,
    render_ctt_registration_workbook,
)
from .ctt_report_access import CTT_ROSTER_READ_ROLES, list_ctt_report_editions

CTT_REPORT_PATH = "/registration-review/reportes-cedulas"
CTT_REPORT_DOWNLOAD_PATH = CTT_REPORT_PATH + ".xlsx"
PRIVATE_HEADERS = {"Cache-Control": "private, no-store"}


@dataclass(frozen=True)
class CttReportFilters:
    edition_year: Annotated[int | None, Query(ge=2000, le=2099)] = None
    state: Annotated[str, Query(pattern=r"^(?:[0-9]{2})?$")] = ""
    municipality: Annotated[str, Query(pattern=r"^(?:[0-9]{5})?$")] = ""
    category: Annotated[str, Query(max_length=80)] = ""


async def ctt_reports_url_for_employee(
    session: AsyncSession, employee: Empleado
) -> str | None:
    try:
        editions = await list_ctt_report_editions(
            session, employee_id=str(employee.id), role=str(employee.rol)
        )
    except SQLAlchemyError:
        return None
    return CTT_REPORT_PATH if editions else None


def _filter_label(options: list[dict], value: str) -> str:
    return next((item["label"] for item in options if item["value"] == value), value)


def _report_revision(report: dict) -> str:
    workbook_inputs = {
        field: report.get(field)
        for field in (
            "edition_year",
            "as_of_date",
            "summary",
            "reports",
            "filters",
            "catalog_source",
            "catalog_date",
        )
    }
    return sha256(json.dumps(workbook_inputs, sort_keys=True).encode()).hexdigest()


async def _report_context(
    session: AsyncSession, employee: Empleado, filters: CttReportFilters
) -> dict:
    try:
        editions = await list_ctt_report_editions(
            session, employee_id=str(employee.id), role=str(employee.rol)
        )
    except SQLAlchemyError as exc:
        raise HTTPException(
            503, "El alcance de reportes de CTT no está disponible."
        ) from exc
    context: dict[str, Any] = {"reports_url": CTT_REPORT_PATH, "download_url": None}
    if editions is None:
        return {
            **context,
            "report": {"available": False},
            "status_message": "La fuente de reportes de CTT aún no está habilitada.",
        }
    edition = next(
        (
            item
            for item in editions
            if filters.edition_year in (None, item.edition_year)
        ),
        None,
    )
    if edition is None:
        raise HTTPException(403, "La edición está fuera de tu alcance de CTT.")

    state = filters.state
    if not edition.national:
        state = state or min(edition.state_codes)
        if state not in edition.state_codes:
            raise HTTPException(403, "La entidad está fuera de tu alcance de CTT.")
    if filters.municipality and not filters.municipality.startswith(state):
        raise HTTPException(422, "El municipio debe corresponder a la entidad elegida.")
    if filters.municipality and not state:
        state = filters.municipality[:2]
    category = normalize_text(filters.category)

    context.update(
        editions=[item.edition_year for item in editions],
        selected_year=edition.edition_year,
        selected_state=state,
        selected_municipality=filters.municipality,
        selected_category=category,
        state_scope_locked=not edition.national,
    )
    if edition.age_cutoff is None:
        return {
            **context,
            "report": {"available": False},
            "status_message": "Falta configurar la fecha de corte para esta edición.",
        }
    report = await build_ctt_registration_report(
        session,
        tournament_id=edition.tournament_id,
        edition_year=edition.edition_year,
        age_cutoff=edition.age_cutoff,
        state_code=state,
        municipality_code=filters.municipality,
        category=category,
    )
    if report.get("status") == "FILTER_INVALID":
        raise HTTPException(422, "Revisa los filtros del reporte de cédulas.")
    if not report.get("available"):
        return {
            **context,
            "report": report,
            "status_message": "El padrón de esta edición no está disponible.",
        }

    options = report["options"]
    report["filters"] = {
        "state": _filter_label(options["states"], state),
        "municipality": _filter_label(options["municipalities"], filters.municipality),
        "category": _filter_label(options["categories"], category),
    }
    if not edition.national:
        options["states"] = [
            item for item in options["states"] if item["value"] in edition.state_codes
        ]
    revision = _report_revision(report)
    query = urlencode(
        [("edition_year", edition.edition_year), ("revision", revision)]
        + [
            (key, value)
            for key, value in (
                ("state", state),
                ("municipality", filters.municipality),
                ("category", category),
            )
            if value
        ]
    )
    roster_query = urlencode({"roster_slug": edition.roster_slug, "state_code": state})
    roster_url = (
        f"/teams?{roster_query}"
        if str(employee.rol or "").strip().lower() in CTT_ROSTER_READ_ROLES
        else None
    )
    return {
        **context,
        "report": report,
        "download_url": f"{CTT_REPORT_DOWNLOAD_PATH}?{query}",
        "revision": revision,
        "roster_url": roster_url,
        "report_tables": ctt_registration_report_tables(report),
    }


def create_ctt_report_router(templates: Jinja2Templates) -> APIRouter:
    router = APIRouter()

    @router.get(CTT_REPORT_PATH, include_in_schema=False)
    async def reports_view(
        request: Request,
        filters: Annotated[CttReportFilters, Depends()],
        employee: Annotated[Empleado, Depends(get_current_empleado)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
    ):
        context = await _report_context(session, employee, filters)
        return templates.TemplateResponse(
            request=request,
            name="ctt_registration_reports.html",
            context=context,
            status_code=200 if context["report"].get("available") else 503,
            headers=PRIVATE_HEADERS,
        )

    @router.get(CTT_REPORT_DOWNLOAD_PATH, include_in_schema=False)
    async def reports_download(
        filters: Annotated[CttReportFilters, Depends()],
        employee: Annotated[Empleado, Depends(get_current_empleado)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
        revision: Annotated[str | None, Query(pattern=r"^[a-f0-9]{64}$")] = None,
    ):
        context = await _report_context(session, employee, filters)
        if not context["report"].get("available"):
            raise HTTPException(503, context["status_message"], headers=PRIVATE_HEADERS)
        if revision is not None and revision != context["revision"]:
            raise HTTPException(
                409,
                "El padrón cambió. Actualiza el reporte antes de descargar el Excel.",
                headers=PRIVATE_HEADERS,
            )
        year = context["selected_year"]
        filename = f"CTT-cedulas-{year}.xlsx"
        return Response(
            render_ctt_registration_workbook(context["report"]),
            media_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
            headers={
                **PRIVATE_HEADERS,
                "Content-Disposition": f'attachment; filename="{filename}"',
            },
        )

    return router
