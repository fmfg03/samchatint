"""Presupuestos dashboard and tournament detail routes."""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from fastapi import Depends, File, Form, Query, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from samchat.budgets.service import (
    attach_cuenta_contable_to_budget_lines,
    build_budget_monthly_actuals,
    build_budget_monthly_plan_rollups,
    build_budget_snapshot,
    copy_budget_version_forward,
    ensure_budget_schema,
    list_budget_lines,
    list_budget_versions,
    resolve_budget_tournament_context,
    resolve_definitive_budget_version,
)

from .admin_budget_ui import (
    budget_dashboard_url,
    budget_tournament_detail_url,
    collect_matrix_phase_filter_options,
    filter_budget_lines_by_phase,
    render_cfdi_income_bridge_panel,
    render_add_tournament_line_form,
    render_budget_detail_section_nav,
    render_budget_matrix_filters,
    render_budget_partida_matrix,
    render_tournament_dashboard_cards,
)
from ..services.cfdi_income_bridge_service import (
    CFDIIncomeBridgeError,
    create_cfdi_income_link,
    ingest_and_link_cfdi_income,
    list_budget_cfdi_income_links,
    list_psp_cfdi_income_candidates,
    soft_unlink_cfdi_income,
)

logger = logging.getLogger(__name__)


def register_presupuestos_routes(router) -> None:
    from .admin_routes import (
        _admin_workspace_styles,
        _budget_access_map,
        _render_admin_workspace_hero,
        _require_budget_access,
        format_value,
        get_current_empleado,
        get_db_session,
        render_admin_navigation,
    )

    @router.get("/admin/presupuestos", response_class=HTMLResponse)
    async def admin_presupuestos_dashboard(
        session: AsyncSession = Depends(get_db_session),
        current_empleado=Depends(get_current_empleado),
        edition_year: Optional[int] = Query(None),
        version_id: Optional[str] = Query(None),
        success_msg: Optional[str] = Query(None),
        error_msg: Optional[str] = Query(None),
    ):
        _require_budget_access(current_empleado, "read")
        await ensure_budget_schema(session)

        all_versions = await list_budget_versions(session)
        resolved_year = edition_year
        if resolved_year is None:
            resolved_year = (
                int(all_versions[0]["edition_year"])
                if all_versions
                else date.today().year
            )
        versions = await list_budget_versions(session, edition_year=resolved_year)
        selected_version = None
        if version_id:
            selected_version = next(
                (item for item in versions if item["id"] == version_id), None
            )
        if selected_version is None and versions:
            selected_version = versions[0]

        snapshot = await build_budget_snapshot(
            session=session,
            edition_year=resolved_year,
            version_id=selected_version["id"] if selected_version else None,
        )
        tournaments = snapshot.get("tournaments", []) if isinstance(snapshot, dict) else []
        summary = snapshot.get("summary", {}) if isinstance(snapshot, dict) else {}
        access = _budget_access_map(current_empleado)

        tournament_rollups: dict[str, dict[str, float]] = {}
        if selected_version:
            for item in tournaments:
                rollup_key = str(item.get("tournament_id") or item.get("tournament_code") or "")
                rollups = await build_budget_monthly_plan_rollups(
                    session,
                    version_id=selected_version["id"],
                    tournament_id=str(item.get("tournament_id") or "") or None,
                    tournament_code=str(item.get("tournament_code") or "") or None,
                )
                actuals = await build_budget_monthly_actuals(
                    session,
                    edition_year=resolved_year,
                    version_id=selected_version["id"],
                    tournament_id=str(item.get("tournament_id") or "") or None,
                    tournament_name=str(item.get("tournament_name") or "") or None,
                    tournament_code=str(item.get("tournament_code") or "") or None,
                )
                real_income = sum(
                    float(month.get("real_income") or 0)
                    for concept in actuals.values()
                    for month in concept.values()
                )
                tournament_rollups[rollup_key] = {
                    **rollups,
                    "real_income_total": round(real_income, 2),
                }

        year_options = "".join(
            f'<option value="{year}" {"selected" if year == resolved_year else ""}>{year}</option>'
            for year in sorted({int(v.get("edition_year") or 0) for v in all_versions} or {resolved_year})
        )
        version_options = "".join(
            f'<option value="{item["id"]}" {"selected" if selected_version and item["id"] == selected_version["id"] else ""}>'
            f'{item["version_name"]} · {item["status"]} · ${float(item["budget_total"] or 0):,.2f}</option>'
            for item in versions
        )
        status_actions = {
            "draft": [("submitted", "Enviar aprobación"), ("closed", "Cerrar")],
            "submitted": [("approved", "Aprobar"), ("draft", "Regresar a draft"), ("closed", "Cerrar")],
            "approved": [("frozen", "Congelar"), ("reforecast", "Mandar a reforecast"), ("closed", "Cerrar")],
            "frozen": [("reforecast", "Reforecast"), ("closed", "Cerrar")],
            "reforecast": [
                ("submitted", "Reenviar"),
                ("approved", "Aprobar"),
                ("frozen", "Congelar"),
                ("closed", "Cerrar"),
            ],
            "closed": [],
        }
        version_rows_parts: list[str] = []
        for row in versions:
            actions_html = [
                f'<a href="{budget_dashboard_url(edition_year=resolved_year, version_id=str(row.get("id") or ""))}" '
                f'style="text-decoration:none;background:#e2e8f0;color:#0f172a;border-radius:999px;padding:6px 10px;font-size:11px;font-weight:700;">Abrir</a>'
            ]
            for next_status, label in status_actions.get(str(row.get("status") or ""), []):
                can_transition = False
                if next_status == "approved":
                    can_transition = access.get("approve", False)
                elif next_status == "frozen":
                    can_transition = access.get("freeze", False)
                else:
                    can_transition = access.get("version_update", False)
                if can_transition:
                    actions_html.append(
                        f'<form method="POST" action="/admin/presupuestos/versiones/{row.get("id")}/transition">'
                        f'<input type="hidden" name="status" value="{next_status}">'
                        f'<input type="hidden" name="edition_year" value="{resolved_year}">'
                        f'<button type="submit" style="background:#0f766e;color:#fff;border:none;border-radius:999px;padding:6px 10px;font-size:11px;font-weight:700;cursor:pointer;">{label}</button>'
                        f"</form>"
                    )
            version_rows_parts.append(
                f"""
                <tr>
                    <td>{int(row.get("edition_year") or 0)}</td>
                    <td><div style="font-weight:700;">{row.get("version_name")}</div></td>
                    <td>{row.get("status")}</td>
                    <td>{row.get("source")}</td>
                    <td>{format_value(row.get("updated_at") or row.get("created_at"))}</td>
                    <td><div style="display:flex;flex-wrap:wrap;gap:6px;">{"".join(actions_html)}</div></td>
                </tr>
                """
            )

        tournament_cards = render_tournament_dashboard_cards(
            tournaments,
            edition_year=resolved_year,
            version_id=selected_version["id"] if selected_version else None,
            tournament_rollups=tournament_rollups,
        )
        success_html = (
            f'<div style="background:#d4edda;border:1px solid #c3e6cb;color:#155724;padding:10px;border-radius:6px;margin-bottom:12px;"><strong>✅ {success_msg}</strong></div>'
            if success_msg
            else ""
        )
        error_html = (
            f'<div style="background:#f8d7da;border:1px solid #f5c6cb;color:#721c24;padding:10px;border-radius:6px;margin-bottom:12px;"><strong>⚠️ {error_msg}</strong></div>'
            if error_msg
            else ""
        )
        create_version_form = (
            f"""
            <form method="POST" action="/admin/presupuestos/versiones/create" style="display:grid;gap:8px;">
                <input type="hidden" name="edition_year" value="{resolved_year}">
                <label style="font-weight:700;">Nuevo presupuesto {resolved_year}</label>
                <input type="text" name="version_name" placeholder="Ej. Presupuesto {resolved_year} Dirección" required>
                <textarea name="notes" rows="3" placeholder="Notas de alcance"></textarea>
                <button type="submit" style="width:max-content;background:#0f766e;color:#fff;border:none;border-radius:999px;padding:10px 14px;font-weight:700;cursor:pointer;">Crear borrador vacío</button>
            </form>
            """
            if access.get("create")
            else '<div style="color:#64748b;">Sin permiso para crear presupuestos.</div>'
        )
        selected_version_edit_form = (
            f"""
            <form method="POST" action="/admin/presupuestos/versiones/{selected_version["id"]}/update" style="display:grid;gap:8px;">
                <input type="hidden" name="edition_year" value="{resolved_year}">
                <label style="font-weight:700;">Nombre versión</label>
                <input type="text" name="version_name" value="{selected_version.get("version_name") or ""}" {'disabled' if not access.get("version_update") else ''}>
                <label style="font-weight:700;">Notas</label>
                <textarea name="notes" rows="4" {'disabled' if not access.get("version_update") else ''}>{selected_version.get("notes") or ""}</textarea>
                {f'<button type="submit" style="width:max-content;background:#1d4ed8;color:#fff;border:none;border-radius:999px;padding:10px 14px;font-weight:700;cursor:pointer;">Guardar metadatos</button>' if access.get("version_update") else ""}
            </form>
            """
            if selected_version
            else ""
        )

        html = f"""
        <!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
        <title>Presupuestos - Administración</title>
        <style>{_admin_workspace_styles("1380px")}</style></head><body>
        <div class="workspace-shell">
            {render_admin_navigation(current_empleado, "presupuestos", subtitle="Dashboard por torneo con acceso al detalle de partidas y plan mensual.")}
            {_render_admin_workspace_hero(
                eyebrow="C-suite",
                title=f"Presupuestos {resolved_year}",
                description="Selecciona un torneo para capturar presupuesto mensual, ingreso esperado y revisar gasto real en caja.",
                actions_html="",
                side_html=(
                    f'<div class="meta-grid">'
                    f'<div class="meta-card"><span>Torneos</span><strong>{int(summary.get("tournaments_count") or 0)}</strong></div>'
                    f'<div class="meta-card"><span>Presupuesto</span><strong>${float(summary.get("budget_total") or 0):,.2f}</strong></div>'
                    f'<div class="meta-card"><span>Pagado</span><strong>${float(summary.get("paid_total") or 0):,.2f}</strong></div>'
                    f'</div>'
                ),
            )}
            {success_html}{error_html}
            <section class="workspace-card" style="margin-bottom:18px;">
                <div class="workspace-section-title">Contexto</div>
                <form method="GET" action="/admin/presupuestos" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;align-items:end;">
                    <div><label>Año</label><select name="edition_year">{year_options}</select></div>
                    <div><label>Versión</label><select name="version_id">{version_options or '<option value="">Sin versiones</option>'}</select></div>
                    <div><button type="submit" class="button">Actualizar</button></div>
                </form>
            </section>
            <section class="workspace-card" style="margin-bottom:18px;">
                <div class="workspace-section-title">Versiones</div>
                <table><thead><tr><th>Año</th><th>Versión</th><th>Status</th><th>Source</th><th>Actualizado</th><th>Acciones</th></tr></thead>
                <tbody>{"".join(version_rows_parts) if version_rows_parts else '<tr><td colspan="6">Sin versiones.</td></tr>'}</tbody></table>
                <div style="margin-top:14px;">{create_version_form}</div>
                <div style="margin-top:14px;">{selected_version_edit_form}</div>
            </section>
            <section class="workspace-card">
                <div class="workspace-section-title">Torneos / proyectos</div>
                <div class="workspace-section-subtitle">Abre el detalle para capturar plan mensual por partida.</div>
                <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:14px;margin-top:14px;">
                    {tournament_cards}
                </div>
            </section>
        </div></body></html>
        """
        return HTMLResponse(content=html)

    @router.get("/admin/presupuestos/torneo/{tournament_key}", response_class=HTMLResponse)
    async def admin_presupuestos_tournament_detail(
        tournament_key: str,
        session: AsyncSession = Depends(get_db_session),
        current_empleado=Depends(get_current_empleado),
        edition_year: Optional[int] = Query(None),
        phase_filter: Optional[str] = Query(None),
        show_committed: int = Query(1),
        show_yoy: int = Query(0),
        success_msg: Optional[str] = Query(None),
        error_msg: Optional[str] = Query(None),
    ):
        _require_budget_access(current_empleado, "read")
        await ensure_budget_schema(session)
        tournament_ctx = await resolve_budget_tournament_context(
            session, tournament_key=tournament_key
        )
        if tournament_ctx is None:
            return RedirectResponse(
                url=budget_dashboard_url(error_msg="Torneo no encontrado"),
                status_code=303,
            )

        all_versions = await list_budget_versions(session)
        resolved_year = edition_year or date.today().year
        selected_version = await resolve_definitive_budget_version(
            session,
            edition_year=resolved_year,
        )
        if selected_version is None:
            return RedirectResponse(
                url=budget_dashboard_url(
                    edition_year=resolved_year,
                    error_msg="No hay versión presupuestal para este año.",
                ),
                status_code=303,
            )

        expense_lines = await list_budget_lines(
            session,
            version_id=selected_version["id"],
            tournament_id=tournament_ctx.get("tournament_id"),
            tournament_code=tournament_ctx.get("tournament_code"),
            line_direction="expense",
            limit=5000,
        )
        income_lines = await list_budget_lines(
            session,
            version_id=selected_version["id"],
            tournament_id=tournament_ctx.get("tournament_id"),
            tournament_code=tournament_ctx.get("tournament_code"),
            line_direction="income",
            limit=5000,
        )
        lines = await attach_cuenta_contable_to_budget_lines(
            session, expense_lines + income_lines
        )
        expense_line_ids = {line["id"] for line in expense_lines}
        expense_lines = [
            line for line in lines if line.get("id") in expense_line_ids
        ]
        income_lines = [
            line for line in lines if line.get("id") not in expense_line_ids
        ]
        selected_phase_filter = str(phase_filter or "").strip()

        from sqlalchemy import select

        from ..models import Tournament
        from ..services.tournament_phase_service import (
            get_tournament_etapas,
            get_tournament_scope_labels,
        )

        phase_labels: list[str] = []
        tournament_id = tournament_ctx.get("tournament_id")
        if tournament_id:
            tournament_row = (
                await session.execute(
                    select(Tournament).where(Tournament.id == tournament_id)
                )
            ).scalar_one_or_none()
            if tournament_row is not None:
                phase_labels = get_tournament_scope_labels(tournament_row)
                if not phase_labels:
                    phase_labels = get_tournament_etapas(tournament_row)

        phase_options = collect_matrix_phase_filter_options(lines, phase_labels)
        filtered_expense_lines = filter_budget_lines_by_phase(
            expense_lines, selected_phase_filter
        )
        filtered_income_lines = filter_budget_lines_by_phase(
            income_lines, selected_phase_filter
        )
        from samchat.budgets.service import list_monthly_plan_for_lines

        plan_map = await list_monthly_plan_for_lines(
            session,
            line_ids=[
                line["id"]
                for line in (filtered_expense_lines + filtered_income_lines)
            ],
        )
        actuals_map = await build_budget_monthly_actuals(
            session,
            edition_year=resolved_year,
            version_id=selected_version["id"],
            tournament_id=tournament_ctx.get("tournament_id"),
            tournament_name=tournament_ctx.get("tournament_name"),
            tournament_code=tournament_ctx.get("tournament_code"),
        )
        access = _budget_access_map(current_empleado)
        rollups = await build_budget_monthly_plan_rollups(
            session,
            version_id=selected_version["id"],
            tournament_id=tournament_ctx.get("tournament_id"),
            tournament_code=tournament_ctx.get("tournament_code"),
        )

        from sqlalchemy import select

        from ..models import CuentaContable

        cuentas_rows = (
            await session.execute(
                select(CuentaContable)
                .where(CuentaContable.activo.is_(True))
                .order_by(CuentaContable.codigo)
            )
        ).scalars().all()
        cuentas_contables = [
            {
                "id": str(cuenta.id),
                "codigo": str(cuenta.codigo or ""),
                "nombre": str(cuenta.nombre or ""),
                "tipo": str(cuenta.tipo or ""),
            }
            for cuenta in cuentas_rows
        ]

        gastos_matrix_html = render_budget_partida_matrix(
            filtered_expense_lines,
            plan_map=plan_map,
            actuals_map=actuals_map,
            version_id=selected_version["id"],
            tournament_key=tournament_key,
            can_edit=bool(access.get("line_update")),
            show_committed=bool(show_committed),
            edition_year=resolved_year,
            phase_filter=selected_phase_filter or None,
            filtered_empty=bool(selected_phase_filter and not filtered_expense_lines),
            cuentas_contables=cuentas_contables,
            matrix_mode="expenses",
        )
        ingresos_matrix_html = render_budget_partida_matrix(
            filtered_income_lines,
            plan_map=plan_map,
            actuals_map=actuals_map,
            version_id=selected_version["id"],
            tournament_key=tournament_key,
            can_edit=bool(access.get("line_update")),
            show_committed=False,
            edition_year=resolved_year,
            phase_filter=selected_phase_filter or None,
            filtered_empty=bool(selected_phase_filter and not filtered_income_lines),
            cuentas_contables=cuentas_contables,
            matrix_mode="income",
        )
        matrix_filters_html = render_budget_matrix_filters(
            tournament_key=tournament_key,
            edition_year=resolved_year,
            all_versions=all_versions,
            phase_options=phase_options,
            selected_phase_filter=selected_phase_filter,
            show_committed=bool(show_committed),
            visible_count=len(filtered_expense_lines) + len(filtered_income_lines),
            total_count=len(lines),
        )
        back_url = budget_dashboard_url(
            edition_year=resolved_year,
            version_id=selected_version["id"],
        )
        create_expense_line_form = ""
        create_income_line_form = ""
        if access.get("line_update"):
            create_expense_line_form = render_add_tournament_line_form(
                version_id=str(selected_version["id"]),
                tournament_key=tournament_key,
                tournament_id=str(tournament_id or "") or None,
                tournament_code=str(tournament_ctx.get("tournament_code") or ""),
                tournament_name=str(tournament_ctx.get("tournament_name") or ""),
                phase_labels=phase_labels,
                line_direction="expense",
                cuentas_contables=cuentas_contables,
            )
            create_income_line_form = render_add_tournament_line_form(
                version_id=str(selected_version["id"]),
                tournament_key=tournament_key,
                tournament_id=str(tournament_id or "") or None,
                tournament_code=str(tournament_ctx.get("tournament_code") or ""),
                tournament_name=str(tournament_ctx.get("tournament_name") or ""),
                phase_labels=[],
                line_direction="income",
                show_phase_field=False,
                section_id="presupuesto-ingresos",
                cuentas_contables=cuentas_contables,
            )

        cfdi_income_panel = render_cfdi_income_bridge_panel(
            tournament_key=tournament_key,
            edition_year=resolved_year,
            lines=income_lines,
            candidates=await list_psp_cfdi_income_candidates(
                session,
                budget_version_id=str(selected_version["id"]),
            ),
            links=await list_budget_cfdi_income_links(
                session,
                budget_version_id=str(selected_version["id"]),
                tournament_id=str(tournament_id or "") or None,
            ),
            can_edit=bool(access.get("line_update")),
        )

        success_html = (
            f'<div style="background:#d4edda;border:1px solid #c3e6cb;color:#155724;padding:10px;border-radius:6px;margin-bottom:12px;"><strong>✅ {success_msg}</strong></div>'
            if success_msg
            else ""
        )
        error_html = (
            f'<div style="background:#f8d7da;border:1px solid #f5c6cb;color:#721c24;padding:10px;border-radius:6px;margin-bottom:12px;"><strong>⚠️ {error_msg}</strong></div>'
            if error_msg
            else ""
        )

        html = f"""
        <!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
        <title>{tournament_ctx.get("tournament_name")} - Presupuestos</title>
        <style>{_admin_workspace_styles("1400px")}</style></head><body>
        <div class="workspace-shell">
            {render_admin_navigation(current_empleado, "presupuestos", subtitle="Detalle de partidas con plan mensual y actuals en caja.")}
            {_render_admin_workspace_hero(
                eyebrow="Detalle torneo",
                title=str(tournament_ctx.get("tournament_name") or tournament_key),
                description=f"Presupuesto operativo {resolved_year}",
                actions_html=f'<a class="button secondary" href="{back_url}">← Dashboard</a>',
                side_html=(
                    f'<div class="meta-grid">'
                    f'<div class="meta-card"><span>Presupuesto gasto</span><strong>${rollups.get("budget_expense_total", 0):,.2f}</strong></div>'
                    f'<div class="meta-card"><span>Ingreso esperado</span><strong>${rollups.get("expected_income_total", 0):,.2f}</strong></div>'
                    f'</div>'
                ),
            )}
            {render_budget_detail_section_nav()}
            {success_html}{error_html}
            {create_expense_line_form}
            <section class="workspace-card" id="presupuesto-gastos" style="margin-bottom:18px;">
                <div class="workspace-section-title">Gastos</div>
                <div class="workspace-section-subtitle">Captura presupuesto de gasto; gasto real se calcula automáticamente (caja).</div>
                {matrix_filters_html}
                {gastos_matrix_html}
            </section>
            {create_income_line_form}
            <section class="workspace-card">
                <div class="workspace-section-title">Ingresos</div>
                <div class="workspace-section-subtitle">Captura ingreso esperado; ingreso real se alimenta con CFDI PSP vinculados.</div>
                {cfdi_income_panel}
                {matrix_filters_html}
                {ingresos_matrix_html}
            </section>
        </div></body></html>
        """
        return HTMLResponse(content=html)

    @router.get("/admin/presupuestos/torneo/{tournament_key}/cfdi-ingresos")
    async def admin_presupuestos_cfdi_income_redirect(
        tournament_key: str,
        edition_year: Optional[int] = Query(None),
    ):
        return RedirectResponse(
            url=budget_tournament_detail_url(
                tournament_key,
                edition_year=edition_year,
            ),
            status_code=303,
        )

    @router.post("/admin/presupuestos/torneo/{tournament_key}/cfdi-ingresos/link")
    async def admin_presupuestos_link_cfdi_income(
        tournament_key: str,
        cfdi_report_id: str = Form(...),
        budget_line_id: str = Form(...),
        amount: str = Form(""),
        income_date: str = Form(""),
        edition_year: Optional[int] = Form(None),
        session: AsyncSession = Depends(get_db_session),
        current_empleado=Depends(get_current_empleado),
    ):
        _require_budget_access(current_empleado, "line_update")
        try:
            result = await create_cfdi_income_link(
                session,
                cfdi_report_id=cfdi_report_id,
                budget_line_id=budget_line_id,
                actor_empleado_id=str(current_empleado.id),
                amount=amount,
                income_date=income_date,
                source="admin_ui",
            )
            msg = (
                "CFDI PSP actualizado con el monto enviado."
                if result.get("status") == "updated"
                else "CFDI PSP vinculado a ingreso real."
            )
            return RedirectResponse(
                url=budget_tournament_detail_url(
                    tournament_key,
                    edition_year=edition_year,
                    success_msg=msg,
                ),
                status_code=303,
            )
        except CFDIIncomeBridgeError as exc:
            await session.rollback()
            return RedirectResponse(
                url=budget_tournament_detail_url(
                    tournament_key,
                    edition_year=edition_year,
                    error_msg=str(exc),
                ),
                status_code=303,
            )
        except Exception:
            await session.rollback()
            logger.exception("CFDI PSP income link failed")
            return RedirectResponse(
                url=budget_tournament_detail_url(
                    tournament_key,
                    edition_year=edition_year,
                    error_msg="No se pudo vincular el CFDI PSP.",
                ),
                status_code=303,
            )

    @router.post("/admin/presupuestos/torneo/{tournament_key}/cfdi-ingresos/upload-link")
    async def admin_presupuestos_upload_link_cfdi_income(
        tournament_key: str,
        budget_line_id: str = Form(...),
        amount: str = Form(""),
        income_date: str = Form(""),
        edition_year: Optional[int] = Form(None),
        cfdi_xml: Optional[UploadFile] = File(None),
        cfdi_pdf: Optional[UploadFile] = File(None),
        session: AsyncSession = Depends(get_db_session),
        current_empleado=Depends(get_current_empleado),
    ):
        _require_budget_access(current_empleado, "line_update")
        try:
            xml_bytes = (
                await cfdi_xml.read()
                if cfdi_xml is not None and (cfdi_xml.filename or "").strip()
                else None
            )
            pdf_bytes = (
                await cfdi_pdf.read()
                if cfdi_pdf is not None and (cfdi_pdf.filename or "").strip()
                else None
            )
            await ingest_and_link_cfdi_income(
                session,
                budget_line_id=budget_line_id,
                actor_empleado_id=str(current_empleado.id),
                xml_bytes=xml_bytes,
                pdf_bytes=pdf_bytes,
                amount=amount,
                income_date=income_date,
            )
            return RedirectResponse(
                url=budget_tournament_detail_url(
                    tournament_key,
                    edition_year=edition_year,
                    success_msg="CFDI PSP cargado y vinculado a ingreso real.",
                ),
                status_code=303,
            )
        except CFDIIncomeBridgeError as exc:
            await session.rollback()
            return RedirectResponse(
                url=budget_tournament_detail_url(
                    tournament_key,
                    edition_year=edition_year,
                    error_msg=str(exc),
                ),
                status_code=303,
            )
        except Exception:
            await session.rollback()
            logger.exception("CFDI PSP income upload-link failed")
            return RedirectResponse(
                url=budget_tournament_detail_url(
                    tournament_key,
                    edition_year=edition_year,
                    error_msg="No se pudo cargar y vincular el CFDI PSP.",
                ),
                status_code=303,
            )

    @router.post("/admin/presupuestos/torneo/{tournament_key}/cfdi-ingresos/{link_id}/unlink")
    async def admin_presupuestos_unlink_cfdi_income(
        tournament_key: str,
        link_id: str,
        edition_year: Optional[int] = Form(None),
        session: AsyncSession = Depends(get_db_session),
        current_empleado=Depends(get_current_empleado),
    ):
        _require_budget_access(current_empleado, "line_update")
        try:
            unlinked = await soft_unlink_cfdi_income(
                session,
                link_id=link_id,
                actor_empleado_id=str(current_empleado.id),
            )
            return RedirectResponse(
                url=budget_tournament_detail_url(
                    tournament_key,
                    edition_year=edition_year,
                    success_msg=(
                        "El CFDI PSP dejó de contar como ingreso real."
                        if unlinked
                        else "El CFDI PSP ya estaba desvinculado."
                    ),
                ),
                status_code=303,
            )
        except Exception:
            await session.rollback()
            logger.exception("CFDI PSP income unlink failed")
            return RedirectResponse(
                url=budget_tournament_detail_url(
                    tournament_key,
                    edition_year=edition_year,
                    error_msg="No se pudo desvincular el CFDI PSP.",
                ),
                status_code=303,
            )

    @router.post("/admin/presupuestos/versiones/copy-forward")
    async def admin_presupuestos_copy_forward(
        source_version_id: str = Form(...),
        target_edition_year: int = Form(...),
        version_name: str = Form(...),
        tournament_key: Optional[str] = Form(None),
        session: AsyncSession = Depends(get_db_session),
        current_empleado=Depends(get_current_empleado),
    ):
        _require_budget_access(current_empleado, "create")
        try:
            result = await copy_budget_version_forward(
                session,
                source_version_id=source_version_id,
                target_edition_year=int(target_edition_year),
                version_name=version_name.strip(),
                actor_empleado_id=str(current_empleado.id),
            )
            msg = f"Versión copiada: {result['copied_lines']} líneas."
            if tournament_key:
                return RedirectResponse(
                    url=budget_tournament_detail_url(
                        tournament_key,
                        edition_year=int(target_edition_year),
                        version_id=result["version"]["id"],
                        success_msg=msg,
                    ),
                    status_code=303,
                )
            return RedirectResponse(
                url=budget_dashboard_url(
                    edition_year=int(target_edition_year),
                    version_id=result["version"]["id"],
                    success_msg=msg,
                ),
                status_code=303,
            )
        except Exception:
            await session.rollback()
            logger.exception("copy-forward failed")
            target = tournament_key or ""
            if target:
                return RedirectResponse(
                    url=budget_tournament_detail_url(
                        target,
                        error_msg="No se pudo copiar la versión.",
                    ),
                    status_code=303,
                )
            return RedirectResponse(
                url=budget_dashboard_url(error_msg="No se pudo copiar la versión."),
                status_code=303,
            )
