"""Presupuestos dashboard and tournament detail routes."""

from __future__ import annotations

import logging
from datetime import date
from html import escape as escape_html
from typing import Any, Optional
from urllib.parse import quote
from uuid import UUID as UUIDType

from fastapi import Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from samchat.budgets.service import (
    DEFAULT_BUDGET_CONCEPT_PASIVO_ACCOUNT_CODE,
    attach_cuenta_contable_to_budget_lines,
    build_budget_monthly_actuals,
    build_budget_monthly_plan_rollups,
    build_budget_snapshot,
    budget_alias_candidates,
    copy_budget_version_forward,
    create_budget_concept,
    create_budget_line,
    create_budget_version,
    ensure_budget_schema,
    import_budget_lines_upload,
    list_monthly_plan_for_lines,
    list_budget_audit_events,
    list_budget_concepts,
    list_budget_lines,
    list_budget_versions,
    replace_budget_line_monthly_plan,
    resolve_budget_tournament_context,
    resolve_definitive_budget_version,
    transition_budget_version,
    update_budget_concept,
    update_budget_line,
    update_budget_version_metadata,
    validate_active_cuenta_contable_id,
)
from samchat.budgets.exporter import (
    generate_budget_income_xlsx,
    generate_budget_review_xlsx,
)

from .admin_budget_ui import (
    budget_dashboard_url,
    budget_tournament_detail_url,
    collect_matrix_phase_filter_options,
    filter_budget_lines_by_phase,
    render_add_tournament_line_form,
    render_budget_detail_section_nav,
    render_budget_executive_dashboard,
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
from ..models import CuentaContable, Tournament

logger = logging.getLogger(__name__)


def _safe_cfdi_income_return_url(return_to: Optional[str], fallback_url: str) -> str:
    clean = str(return_to or "").strip()
    if clean.startswith("/admin/contabilidad/cuentas-por-cobrar") or clean.startswith(
        "/admin/presupuestos/"
    ):
        return clean
    return fallback_url


def _render_budget_status_message(message: Optional[str], *, is_error: bool) -> str:
    """Render a query-driven status message without allowing HTML injection."""
    if not message:
        return ""
    safe_message = escape_html(str(message), quote=True)
    if is_error:
        return (
            '<div style="background:#f8d7da;border:1px solid #f5c6cb;color:#721c24;'
            'padding:10px;border-radius:6px;margin-bottom:12px;">'
            f"<strong>⚠️ {safe_message}</strong></div>"
        )
    return (
        '<div style="background:#d4edda;border:1px solid #c3e6cb;color:#155724;'
        'padding:10px;border-radius:6px;margin-bottom:12px;">'
        f"<strong>✅ {safe_message}</strong></div>"
    )


def _select_requested_budget_version(
    versions: list[dict],
    *,
    requested_version_id: Optional[str],
    edition_year: int,
) -> Optional[dict]:
    """Return the explicitly requested version only when it belongs to the year."""
    if not requested_version_id:
        return None
    for version in versions:
        try:
            version_year = int(version.get("edition_year") or 0)
        except (TypeError, ValueError):
            continue
        if str(version.get("id") or "") == str(
            requested_version_id
        ) and version_year == int(edition_year):
            return version
    return None


def _presupuestos_redirect_url(
    *,
    edition_year: Optional[int] = None,
    version_id: Optional[str] = None,
    tournament_key: Optional[str] = None,
    budget_view: Optional[str] = None,
    phase_filter: Optional[str] = None,
    success_msg: Optional[str] = None,
    error_msg: Optional[str] = None,
    drill_dimension: Optional[str] = None,
    drill_value: Optional[str] = None,
    drill_tournament: Optional[str] = None,
    drill_document: Optional[str] = None,
    catalog_scope: Optional[str] = None,
    catalog_tournament_ids: Optional[list[str]] = None,
) -> str:
    if tournament_key:
        return budget_tournament_detail_url(
            tournament_key,
            edition_year=edition_year,
            version_id=version_id,
            budget_view=budget_view,
            phase_filter=phase_filter,
            success_msg=success_msg,
            error_msg=error_msg,
        )
    url = budget_dashboard_url(
        edition_year=edition_year,
        version_id=version_id,
        success_msg=success_msg,
        error_msg=error_msg,
    )
    extra_params: list[str] = []
    if catalog_scope not in (None, ""):
        extra_params.append(f"catalog_scope={quote(str(catalog_scope))}")
    if not isinstance(catalog_tournament_ids, (list, tuple)):
        catalog_tournament_ids = []
    for tournament_id in catalog_tournament_ids or []:
        if tournament_id not in (None, ""):
            extra_params.append(f"catalog_tournament_ids={quote(str(tournament_id))}")
    if extra_params:
        separator = "&" if "?" in url else "?"
        url = f"{url}{separator}{'&'.join(extra_params)}"
    return url


def _budget_catalog_scope_label(metadata: dict[str, Any]) -> str:
    payload = metadata if isinstance(metadata, dict) else {}
    for key in ("applicable_phase_labels", "applicable_subproject_labels"):
        labels = [
            str(label).strip()
            for label in list(payload.get(key) or [])
            if str(label).strip()
        ]
        if labels:
            return labels[0]
    return ""


def _render_presupuestos_catalog_section(
    *,
    budget_concepts: list[dict[str, Any]],
    catalog_tournaments: list[Tournament],
    catalog_cuentas: list[CuentaContable],
    access: dict[str, bool],
    selected_version: Optional[dict],
    edition_year: int,
    catalog_scope: str,
    selected_catalog_tournament_ids: list[str],
) -> str:
    all_active_concepts = sorted(
        [item for item in budget_concepts if item.get("active")],
        key=lambda row: (
            str(row.get("tournament_name") or "").lower(),
            str(row.get("concept_name") or "").lower(),
        ),
    )
    default_pasivo_cuenta_id = next(
        (
            str(cuenta.id)
            for cuenta in catalog_cuentas
            if str(cuenta.codigo or "").strip()
            == DEFAULT_BUDGET_CONCEPT_PASIVO_ACCOUNT_CODE
        ),
        "",
    )
    catalog_hidden_context = (
        f'<input type="hidden" name="version_id" '
        f'value="{escape_html(str(selected_version.get("id") or ""), quote=True)}">'
        if selected_version
        else ""
    )
    normalized_catalog_scope = str(catalog_scope or "none").strip().lower()
    if normalized_catalog_scope not in {"all", "none", "selected"}:
        normalized_catalog_scope = "selected"
    selected_catalog_tournament_set = {
        str(item).strip()
        for item in selected_catalog_tournament_ids
        if str(item).strip()
    }
    catalog_hidden_context += (
        f'<input type="hidden" name="catalog_scope" '
        f'value="{escape_html(normalized_catalog_scope, quote=True)}">'
    )
    catalog_hidden_context += "".join(
        '<input type="hidden" name="catalog_tournament_ids" '
        f'value="{escape_html(item, quote=True)}">'
        for item in sorted(selected_catalog_tournament_set)
    )

    def _resolve_catalog_tournament_id(concept: dict[str, Any]) -> str:
        concept_tid = str(concept.get("tournament_id") or "").strip()
        if concept_tid:
            return concept_tid
        concept_aliases = budget_alias_candidates(
            concept.get("tournament_code") or "",
            concept.get("tournament_name") or "",
        )
        for tournament in catalog_tournaments:
            if concept_aliases & budget_alias_candidates(tournament.name or ""):
                return str(tournament.id)
        return ""

    if normalized_catalog_scope == "all":
        active_concepts = all_active_concepts
    elif selected_catalog_tournament_set:
        active_concepts = [
            item
            for item in all_active_concepts
            if _resolve_catalog_tournament_id(item) in selected_catalog_tournament_set
        ]
    else:
        active_concepts = []

    def _render_tournament_options(selected_id: str) -> str:
        options = ['<option value="">— Proyecto —</option>']
        selected_clean = str(selected_id or "").strip()
        for tournament in catalog_tournaments:
            tournament_id = str(tournament.id)
            selected_attr = " selected" if tournament_id == selected_clean else ""
            options.append(
                f'<option value="{escape_html(tournament_id, quote=True)}"'
                f"{selected_attr}>{escape_html(tournament.name or '')}</option>"
            )
        return "".join(options)

    def _render_cuenta_select(*, selected_id: str = "", field_name: str) -> str:
        options = ['<option value="">— Sin cuenta —</option>']
        selected_clean = str(selected_id or "").strip()
        for cuenta in catalog_cuentas:
            cuenta_id = str(cuenta.id)
            selected_attr = " selected" if cuenta_id == selected_clean else ""
            label = f"{cuenta.codigo} · {cuenta.nombre}"
            options.append(
                f'<option value="{escape_html(cuenta_id, quote=True)}"'
                f"{selected_attr}>{escape_html(label)}</option>"
            )
        return (
            f'<select name="{escape_html(field_name, quote=True)}" '
            'style="width:100%;padding:8px;border:1px solid #cbd5e1;'
            f'border-radius:8px;">{"".join(options)}</select>'
        )

    def _render_direction_options(selected_direction: str) -> str:
        clean_direction = str(selected_direction or "expense").strip().lower()
        options = [
            ("expense", "egreso"),
            ("income", "ingreso"),
        ]
        return "".join(
            f'<option value="{value}" {"selected" if value == clean_direction else ""}>'
            f"{label}</option>"
            for value, label in options
        )

    def _render_edit_row(concept: Optional[dict[str, Any]] = None) -> str:
        item = concept or {}
        concept_id = str(item.get("id") or "")
        budget_direction = str(item.get("budget_direction") or "expense").strip()
        tournament_id = _resolve_catalog_tournament_id(item) if item else ""
        metadata = (
            item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        )
        sub_proyecto = _budget_catalog_scope_label(metadata)
        cuenta_id = str(item.get("cuenta_contable_id") or "")
        pasivo_id = (
            str(item.get("pasivo_cuenta_contable_id") or "") or default_pasivo_cuenta_id
        )
        hide_form = (
            f"""
            <button type="submit"
                    formaction="/admin/presupuestos/conceptos/{escape_html(concept_id, quote=True)}/hide"
                    formmethod="POST"
                    onclick="return confirm('¿Quitar esta partida del catálogo visible?');"
                    style="background:#fee2e2;color:#991b1b;border:1px solid #fecaca;border-radius:999px;padding:6px 10px;font-size:11px;font-weight:700;cursor:pointer;">
                Quitar
            </button>
            """
            if concept_id
            else '<span style="color:#94a3b8;font-size:12px;">Nueva</span>'
        )
        return f"""
        <tr>
            <td>
                <input type="hidden" name="concept_ids" value="{escape_html(concept_id, quote=True)}">
                <input type="text" name="concept_names" value="{escape_html(str(item.get("concept_name") or ""), quote=True)}" placeholder="Partida" style="width:100%;padding:8px;border:1px solid #cbd5e1;border-radius:8px;">
            </td>
            <td>
                <select name="budget_directions" style="width:100%;padding:8px;border:1px solid #cbd5e1;border-radius:8px;">
                    {_render_direction_options(budget_direction)}
                </select>
            </td>
            <td>
                <select name="tournament_ids" style="width:100%;padding:8px;border:1px solid #cbd5e1;border-radius:8px;">
                    {_render_tournament_options(tournament_id)}
                </select>
            </td>
            <td>
                <input type="text" name="sub_proyectos" value="{escape_html(sub_proyecto, quote=True)}" placeholder="Todas" style="width:100%;padding:8px;border:1px solid #cbd5e1;border-radius:8px;">
            </td>
            <td>{_render_cuenta_select(selected_id=cuenta_id, field_name="cuenta_contable_ids")}</td>
            <td>{_render_cuenta_select(selected_id=pasivo_id, field_name="pasivo_cuenta_contable_ids")}</td>
            <td>{hide_form}</td>
        </tr>
        """

    def _render_readonly_row(item: dict[str, Any]) -> str:
        tournament_id = _resolve_catalog_tournament_id(item)
        tournament_label = next(
            (
                tournament.name
                for tournament in catalog_tournaments
                if str(tournament.id) == tournament_id
            ),
            str(item.get("tournament_name") or "—"),
        )
        metadata = (
            item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        )
        cuenta_label = (
            f'{item.get("cuenta_contable_codigo")} · {item.get("cuenta_contable_nombre")}'
            if item.get("cuenta_contable_codigo")
            else "—"
        )
        pasivo_label = (
            f'{item.get("pasivo_cuenta_contable_codigo")} · '
            f'{item.get("pasivo_cuenta_contable_nombre")}'
            if item.get("pasivo_cuenta_contable_codigo")
            else "—"
        )
        tipo_label = (
            "ingreso"
            if str(item.get("budget_direction") or "expense") == "income"
            else "egreso"
        )
        return f"""
        <tr>
            <td>{escape_html(str(item.get("concept_name") or "—"))}</td>
            <td>{escape_html(tipo_label)}</td>
            <td>{escape_html(str(tournament_label or "—"))}</td>
            <td>{escape_html(_budget_catalog_scope_label(metadata) or "Todas")}</td>
            <td>{escape_html(cuenta_label)}</td>
            <td>{escape_html(pasivo_label)}</td>
        </tr>
        """

    catalog_rows = "".join(_render_edit_row(item) for item in active_concepts)
    if normalized_catalog_scope != "none" or selected_catalog_tournament_set:
        catalog_rows += _render_edit_row()
        catalog_rows += _render_edit_row()
    readonly_rows = "".join(_render_readonly_row(item) for item in active_concepts)
    empty_catalog_message = (
        '<tr><td colspan="7">Selecciona torneos para cargar partidas.</td></tr>'
        if not active_concepts
        else ""
    )
    catalog_editor_html = (
        f"""
        <form method="POST" action="/admin/presupuestos/conceptos/bulk-save" style="margin-top:14px;">
            {catalog_hidden_context}
            <div style="overflow:auto;">
                <table>
                    <thead>
                        <tr>
                            <th>Partida</th>
                            <th>Tipo</th>
                            <th>Proyecto</th>
                            <th>Sub Proyecto</th>
                            <th>Cuenta presupuestal</th>
                            <th>Contracuenta presupuestal</th>
                            <th>Acciones</th>
                        </tr>
                    </thead>
                    <tbody>{catalog_rows or empty_catalog_message}</tbody>
                </table>
            </div>
            <button type="submit" style="margin-top:12px;background:#0f766e;color:#fff;border:none;border-radius:999px;padding:10px 14px;font-weight:700;cursor:pointer;">Guardar catálogo</button>
        </form>
        """
        if access.get("line_update")
        else f"""
        <div style="margin-top:14px;color:#64748b;font-size:12px;">Sin permiso para editar el catálogo.</div>
        <div style="overflow:auto;margin-top:10px;">
            <table>
                <thead><tr><th>Partida</th><th>Tipo</th><th>Proyecto</th><th>Sub Proyecto</th><th>Cuenta presupuestal</th><th>Contracuenta presupuestal</th></tr></thead>
                <tbody>{readonly_rows if readonly_rows else '<tr><td colspan="6">Selecciona torneos para cargar partidas.</td></tr>'}</tbody>
            </table>
        </div>
        """
    )
    budget_concepts_tournaments_count = len(
        {
            str(
                item.get("tournament_name") or item.get("tournament_code") or ""
            ).strip()
            for item in all_active_concepts
            if str(
                item.get("tournament_name") or item.get("tournament_code") or ""
            ).strip()
        }
    )
    filter_base_params = [f"edition_year={int(edition_year)}"]
    if selected_version and selected_version.get("id"):
        filter_base_params.append(
            f'version_id={quote(str(selected_version.get("id") or ""))}'
        )
    filter_base_query = "&".join(filter_base_params)
    all_catalog_url = f"/admin/presupuestos?{filter_base_query}&catalog_scope=all"
    none_catalog_url = f"/admin/presupuestos?{filter_base_query}&catalog_scope=none"
    tournament_checkbox_html = "".join(
        f"""
        <label style="display:inline-flex;align-items:center;gap:6px;margin:0 8px 8px 0;font-size:12px;color:#334155;">
            <input type="checkbox" name="catalog_tournament_ids" value="{escape_html(str(tournament.id), quote=True)}"
                   {"checked" if str(tournament.id) in selected_catalog_tournament_set else ""}>
            {escape_html(tournament.name or "")}
        </label>
        """
        for tournament in catalog_tournaments
    )
    selected_tournament_detail_html = "".join(
        f"""
        <a href="{escape_html(budget_tournament_detail_url(
            str(tournament.id),
            edition_year=edition_year,
            version_id=str(selected_version.get("id") or "") if selected_version else None,
        ), quote=True)}"
           style="text-decoration:none;background:#0f766e;color:#fff;border-radius:10px;padding:8px 12px;font-size:12px;font-weight:700;">
            Capturar detalle: {escape_html(tournament.name or "Torneo")}
        </a>
        """
        for tournament in catalog_tournaments
        if str(tournament.id) in selected_catalog_tournament_set
    )
    catalog_filter_html = f"""
        <div style="margin-top:14px;padding:14px;border:1px solid #dbe2ea;border-radius:14px;background:#fff;">
            <div style="display:flex;justify-content:space-between;gap:12px;align-items:flex-start;flex-wrap:wrap;">
                <div>
                    <div style="font-weight:700;color:#0f172a;">Filtrar partidas por torneo</div>
                    <div style="margin-top:6px;font-size:12px;color:#64748b;">{len(active_concepts)} partidas cargadas de {len(all_active_concepts)} activas.</div>
                </div>
                <div style="display:flex;gap:8px;flex-wrap:wrap;">
                    <a href="{all_catalog_url}" style="text-decoration:none;background:#e2e8f0;color:#0f172a;border-radius:999px;padding:7px 10px;font-size:12px;font-weight:700;">Todos</a>
                    <a href="{none_catalog_url}" style="text-decoration:none;background:#e2e8f0;color:#0f172a;border-radius:999px;padding:7px 10px;font-size:12px;font-weight:700;">Ninguno</a>
                </div>
            </div>
            <form method="GET" action="/admin/presupuestos" style="margin-top:12px;">
                <input type="hidden" name="edition_year" value="{int(edition_year)}">
                {f'<input type="hidden" name="version_id" value="{escape_html(str(selected_version.get("id") or ""), quote=True)}">' if selected_version else ""}
                <input type="hidden" name="catalog_scope" value="selected">
                <div style="max-height:170px;overflow:auto;padding:10px;border:1px solid #e2e8f0;border-radius:10px;background:#f8fafc;">
                    {tournament_checkbox_html or '<div style="color:#64748b;font-size:12px;">Sin torneos activos.</div>'}
                </div>
                <button type="submit" style="margin-top:10px;background:#0f766e;color:#fff;border:none;border-radius:999px;padding:9px 12px;font-size:12px;font-weight:700;cursor:pointer;">Aplicar filtro</button>
            </form>
            {f'<div style="margin-top:12px;display:flex;gap:8px;flex-wrap:wrap;">{selected_tournament_detail_html}</div>' if selected_tournament_detail_html else ""}
        </div>
    """
    catalog_export_html = (
        '<a href="/admin/presupuestos/conceptos/export.xlsx" '
        'style="display:inline-flex;margin-top:10px;text-decoration:none;'
        "background:#1d4ed8;color:#fff;border-radius:999px;padding:9px 12px;"
        'font-size:12px;font-weight:700;">Exportar catálogo</a>'
        if access.get("export")
        else '<div style="margin-top:10px;color:#64748b;font-size:12px;">'
        "Sin permiso para exportar.</div>"
    )
    catalog_import_html = (
        '<form method="POST" action="/admin/presupuestos/conceptos/import" '
        'enctype="multipart/form-data" style="display:grid;gap:8px;margin-top:10px;">'
        f"{catalog_hidden_context}"
        '<input type="file" name="archivo_catalogo" accept=".csv,.xlsx,.xlsm" required>'
        '<button type="submit" style="width:max-content;background:#0f766e;color:#fff;'
        "border:none;border-radius:999px;padding:9px 12px;font-size:12px;"
        'font-weight:700;cursor:pointer;">Importar catálogo</button></form>'
        if access.get("line_update")
        else '<div style="margin-top:10px;color:#64748b;font-size:12px;">'
        "Sin permiso para importar.</div>"
    )
    catalog_tools_html = f"""
        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px;margin-top:14px;">
            <div style="padding:14px;border:1px solid #dbe2ea;border-radius:14px;background:#fff;">
                <div style="font-weight:700;color:#0f172a;">Exportar catálogo</div>
                <div style="margin-top:6px;font-size:12px;color:#64748b;">Descarga tipo, partidas, proyecto, subproyecto, cuenta presupuestal, contracuenta presupuestal y activo.</div>
                {catalog_export_html}
            </div>
            <div style="padding:14px;border:1px solid #dbe2ea;border-radius:14px;background:#fff;">
                <div style="font-weight:700;color:#0f172a;">Importar catálogo</div>
                <div style="margin-top:6px;font-size:12px;color:#64748b;">Acepta CSV/XLSX con columnas tipo, partida, proyecto, sub_proyecto, cuenta_presupuestal, contracuenta_presupuestal y activo. Tambien acepta los layouts Ingresos.xlsx y Egresos.xlsx.</div>
                {catalog_import_html}
            </div>
        </div>
    """
    return f"""
    <section class="workspace-card" style="margin-bottom:18px;">
        <div class="workspace-section-title">Catálogo presupuestal</div>
        <div class="workspace-section-subtitle">
            Administra las partidas presupuestales por proyecto y subproyecto. Estas partidas alimentan el selector de
            <a href="/documentos/nueva-solicitud-terceros" style="color:#0f766e;">Solicitud a terceros</a>.
        </div>
        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px;margin-top:14px;">
            <div style="padding:14px;border:1px solid #dbe2ea;border-radius:14px;background:#fff;">
                <div style="font-size:11px;text-transform:uppercase;letter-spacing:.14em;color:#64748b;">Catálogo activo</div>
                <div style="margin-top:6px;font-size:24px;font-weight:800;color:#0f172a;">{len(all_active_concepts)}</div>
                <div style="margin-top:6px;color:#475569;">{budget_concepts_tournaments_count} proyecto(s) con partida cargada.</div>
            </div>
            <div style="padding:14px;border:1px solid #dbe2ea;border-radius:14px;background:#fff;">
                <div style="font-size:11px;text-transform:uppercase;letter-spacing:.14em;color:#64748b;">Pasivo default</div>
                <div style="margin-top:6px;font-size:18px;font-weight:800;color:#0f172a;">{escape_html(DEFAULT_BUDGET_CONCEPT_PASIVO_ACCOUNT_CODE)}</div>
                <div style="margin-top:6px;color:#475569;">Se preselecciona para partidas nuevas mientras Contabilidad revisa el catálogo.</div>
            </div>
        </div>
        {catalog_filter_html}
        {catalog_tools_html}
        {catalog_editor_html}
    </section>
    """


def register_presupuestos_routes(router) -> None:
    from .admin_routes import (
        _admin_workspace_styles,
        _budget_access_map,
        _OPERATION_GENERIC_ERROR,
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
        catalog_scope: Optional[str] = Query("none"),
        catalog_tournament_ids: list[str] = Query([]),
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
        tournaments = (
            snapshot.get("tournaments", []) if isinstance(snapshot, dict) else []
        )
        summary = snapshot.get("summary", {}) if isinstance(snapshot, dict) else {}
        access = _budget_access_map(current_empleado)
        budget_concepts = await list_budget_concepts(
            session,
            active_only=False,
            limit=5000,
        )
        catalog_tournaments_result = await session.execute(
            select(Tournament)
            .where(Tournament.active.is_(True))
            .order_by(Tournament.display_order.asc(), Tournament.name.asc())
        )
        catalog_tournaments = catalog_tournaments_result.scalars().all()
        catalog_cuentas_result = await session.execute(
            select(CuentaContable)
            .where(CuentaContable.activo.is_(True))
            .order_by(CuentaContable.codigo.asc())
        )
        catalog_cuentas = catalog_cuentas_result.scalars().all()
        catalog_section_html = _render_presupuestos_catalog_section(
            budget_concepts=budget_concepts,
            catalog_tournaments=catalog_tournaments,
            catalog_cuentas=catalog_cuentas,
            access=access,
            selected_version=selected_version,
            edition_year=resolved_year,
            catalog_scope=catalog_scope or "none",
            selected_catalog_tournament_ids=catalog_tournament_ids,
        )
        selected_catalog_tournament_set = {
            str(item).strip()
            for item in (catalog_tournament_ids or [])
            if str(item).strip()
        }
        normalized_catalog_scope = str(catalog_scope or "none").strip().lower()
        if (
            normalized_catalog_scope == "selected"
            and selected_catalog_tournament_set
        ):
            selected_tournament_aliases: set[str] = set()
            for tournament in catalog_tournaments:
                if str(tournament.id) in selected_catalog_tournament_set:
                    selected_tournament_aliases.update(
                        budget_alias_candidates(tournament.name or "")
                    )
            tournaments = [
                item
                for item in tournaments
                if str(item.get("tournament_id") or "").strip()
                in selected_catalog_tournament_set
                or bool(
                    budget_alias_candidates(
                        item.get("tournament_code") or "",
                        item.get("tournament_name") or "",
                    )
                    & selected_tournament_aliases
                )
            ]

        tournament_rollups: dict[str, dict[str, float]] = {}
        if selected_version:
            for item in tournaments:
                rollup_key = str(
                    item.get("tournament_id") or item.get("tournament_code") or ""
                )
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
            for year in sorted(
                {int(v.get("edition_year") or 0) for v in all_versions}
                or {resolved_year}
            )
        )
        version_options = "".join(
            f'<option value="{item["id"]}" {"selected" if selected_version and item["id"] == selected_version["id"] else ""}>'
            f'{item["version_name"]} · {item["status"]} · ${float(item["budget_total"] or 0):,.2f}</option>'
            for item in versions
        )
        status_actions = {
            "draft": [("submitted", "Enviar aprobación"), ("closed", "Cerrar")],
            "submitted": [
                ("approved", "Aprobar"),
                ("draft", "Regresar a draft"),
                ("closed", "Cerrar"),
            ],
            "approved": [
                ("frozen", "Congelar"),
                ("reforecast", "Mandar a reforecast"),
                ("closed", "Cerrar"),
            ],
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
            for next_status, label in status_actions.get(
                str(row.get("status") or ""), []
            ):
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
        success_html = _render_budget_status_message(success_msg, is_error=False)
        error_html = _render_budget_status_message(error_msg, is_error=True)
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
                actions_html=(
                    '<a class="button secondary" href="/admin/gastos/sat">SAT / CFDI</a>'
                    '<a class="button secondary" href="/admin/gastos/cfdis/matching">Matching CFDI</a>'
                ),
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
                <div class="table-shell">
                <table><thead><tr><th>Año</th><th>Versión</th><th>Status</th><th>Source</th><th>Actualizado</th><th>Acciones</th></tr></thead>
                <tbody>{"".join(version_rows_parts) if version_rows_parts else '<tr><td colspan="6">Sin versiones.</td></tr>'}</tbody></table>
                </div>
                <div style="margin-top:14px;">{create_version_form}</div>
                <div style="margin-top:14px;">{selected_version_edit_form}</div>
            </section>
            {catalog_section_html}
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

    @router.get("/admin/presupuestos/export.xlsx")
    async def admin_presupuestos_export_xlsx(
        session: AsyncSession = Depends(get_db_session),
        current_empleado=Depends(get_current_empleado),
        version_id: Optional[str] = Query(None),
    ):
        _require_budget_access(current_empleado, "export")
        await ensure_budget_schema(session)
        versions = await list_budget_versions(session, edition_year=2026)
        selected_version = None
        if version_id:
            selected_version = next(
                (item for item in versions if item["id"] == version_id), None
            )
        if selected_version is None and versions:
            selected_version = versions[0]
        snapshot = await build_budget_snapshot(
            session=session,
            edition_year=2026,
            version_id=selected_version["id"] if selected_version else None,
        )
        lines = (
            await list_budget_lines(
                session,
                version_id=selected_version["id"],
                limit=500,
            )
            if selected_version
            else []
        )
        audit_events = (
            await list_budget_audit_events(
                session,
                version_id=selected_version["id"] if selected_version else None,
                limit=500,
            )
            if _budget_access_map(current_empleado).get("audit_read")
            else []
        )
        payload = generate_budget_review_xlsx(
            snapshot=snapshot,
            versions=versions,
            lines=lines,
            audit_events=audit_events,
            selected_version=selected_version,
        )
        filename = "presupuesto_2026"
        if selected_version:
            version_name = str(
                selected_version.get("version_name") or "version"
            ).lower()
            filename = f"presupuesto_2026_{version_name.replace(' ', '_')}"
        headers = {"Content-Disposition": f'attachment; filename="{filename}.xlsx"'}
        return Response(
            content=payload,
            media_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
            headers=headers,
        )

    @router.post("/admin/presupuestos/versiones/create")
    async def admin_presupuestos_create_version(
        version_name: str = Form(...),
        notes: Optional[str] = Form(None),
        edition_year: Optional[int] = Form(None),
        session: AsyncSession = Depends(get_db_session),
        current_empleado=Depends(get_current_empleado),
    ):
        _require_budget_access(current_empleado, "create")
        try:
            resolved_year = int(edition_year) if edition_year else date.today().year
            version = await create_budget_version(
                session,
                edition_year=resolved_year,
                version_name=version_name,
                notes=notes,
                created_by_empleado_id=str(current_empleado.id),
            )
            msg = f"Borrador creado: {version.get('version_name') or version_name}"
            return RedirectResponse(
                url=_presupuestos_redirect_url(
                    edition_year=resolved_year,
                    version_id=str(version.get("id") or ""),
                    success_msg=msg,
                ),
                status_code=303,
            )
        except Exception:
            await session.rollback()
            logger.exception(
                "Unexpected error creating budget version",
                extra={"actor_id": str(getattr(current_empleado, "id", ""))},
            )
            return RedirectResponse(
                url=_presupuestos_redirect_url(error_msg=_OPERATION_GENERIC_ERROR),
                status_code=303,
            )

    @router.post("/admin/presupuestos/versiones/{version_id}/transition")
    async def admin_presupuestos_transition_version(
        version_id: UUIDType,
        status: str = Form(...),
        note: Optional[str] = Form(None),
        session: AsyncSession = Depends(get_db_session),
        current_empleado=Depends(get_current_empleado),
    ):
        target_status = str(status or "").strip().lower()
        if target_status == "approved":
            _require_budget_access(current_empleado, "approve")
        elif target_status == "frozen":
            _require_budget_access(current_empleado, "freeze")
        else:
            _require_budget_access(current_empleado, "version_update")
        try:
            version = await transition_budget_version(
                session,
                version_id=str(version_id),
                new_status=status,
                actor_empleado_id=str(current_empleado.id),
                note=note,
            )
            msg = (
                f"Versión {version.get('version_name') or ''} -> "
                f"{version.get('status') or status}"
            )
            return RedirectResponse(
                url=_presupuestos_redirect_url(success_msg=msg),
                status_code=303,
            )
        except Exception:
            await session.rollback()
            logger.exception(
                "Unexpected error transitioning budget version",
                extra={
                    "version_id": str(version_id),
                    "status": status,
                    "actor_id": str(getattr(current_empleado, "id", "")),
                },
            )
            return RedirectResponse(
                url=_presupuestos_redirect_url(error_msg=_OPERATION_GENERIC_ERROR),
                status_code=303,
            )

    @router.post("/admin/presupuestos/versiones/{version_id}/update")
    async def admin_presupuestos_update_version(
        version_id: UUIDType,
        version_name: Optional[str] = Form(None),
        notes: Optional[str] = Form(None),
        session: AsyncSession = Depends(get_db_session),
        current_empleado=Depends(get_current_empleado),
    ):
        _require_budget_access(current_empleado, "version_update")
        try:
            version = await update_budget_version_metadata(
                session,
                version_id=str(version_id),
                actor_empleado_id=str(current_empleado.id),
                version_name=version_name,
                notes=notes,
            )
            msg = f"Versión actualizada: {version.get('version_name') or ''}"
            return RedirectResponse(
                url=_presupuestos_redirect_url(
                    version_id=str(version_id),
                    success_msg=msg,
                ),
                status_code=303,
            )
        except Exception:
            await session.rollback()
            logger.exception(
                "Unexpected error updating budget version metadata",
                extra={
                    "version_id": str(version_id),
                    "actor_id": str(getattr(current_empleado, "id", "")),
                },
            )
            return RedirectResponse(
                url=_presupuestos_redirect_url(
                    version_id=str(version_id),
                    error_msg=_OPERATION_GENERIC_ERROR,
                ),
                status_code=303,
            )

    @router.post("/admin/presupuestos/versiones/{version_id}/lineas/create")
    async def admin_presupuestos_create_line(
        version_id: UUIDType,
        budget_concept_id: Optional[str] = Form(None),
        tournament_id: Optional[str] = Form(None),
        tournament_code: Optional[str] = Form(None),
        tournament_name: Optional[str] = Form(None),
        concept_name: Optional[str] = Form(None),
        cuenta_contable_id: Optional[str] = Form(None),
        account_code_final: Optional[str] = Form(None),
        phase: Optional[str] = Form(None),
        owner_name: Optional[str] = Form(None),
        priority: Optional[str] = Form(None),
        budget_amount: Optional[float] = Form(0),
        reference_amount: Optional[float] = Form(0),
        criteria_note: Optional[str] = Form(None),
        observations: Optional[str] = Form(None),
        line_direction: Optional[str] = Form(None),
        budget_view: Optional[str] = Form(None),
        tournament_key: Optional[str] = Form(None),
        session: AsyncSession = Depends(get_db_session),
        current_empleado=Depends(get_current_empleado),
    ):
        _require_budget_access(current_empleado, "line_update")
        try:
            concept_id_for_line = budget_concept_id
            account_code_for_line = account_code_final
            if not concept_id_for_line and str(tournament_id or "").strip():
                created_concept = await create_budget_concept(
                    session,
                    tournament_id=str(tournament_id or "").strip(),
                    concept_name=concept_name or "",
                    scope_labels=[phase] if str(phase or "").strip() else [],
                    cuenta_contable_id=cuenta_contable_id,
                    budget_direction=line_direction,
                    actor_empleado_id=str(current_empleado.id),
                    source="admin_budget_detail_add_line",
                    commit=False,
                )
                concept_id_for_line = str(created_concept.get("id") or "")
                account_code_for_line = (
                    str(created_concept.get("cuenta_contable_codigo") or "").strip()
                    or account_code_for_line
                )
            line = await create_budget_line(
                session,
                version_id=str(version_id),
                actor_empleado_id=str(current_empleado.id),
                budget_concept_id=concept_id_for_line,
                tournament_code=tournament_code,
                tournament_name=tournament_name,
                concept_name=concept_name or "",
                line_direction=line_direction,
                account_code_final=account_code_for_line,
                phase=phase,
                owner_name=owner_name,
                priority=priority,
                budget_amount=budget_amount or 0,
                reference_amount=reference_amount or 0,
                criteria_note=criteria_note,
                observations=observations,
            )
            msg = f"Línea creada: {line.get('concept_name') or concept_name}"
            return RedirectResponse(
                url=_presupuestos_redirect_url(
                    version_id=str(version_id),
                    tournament_key=tournament_key or tournament_code,
                    budget_view=budget_view,
                    success_msg=msg,
                ),
                status_code=303,
            )
        except Exception:
            await session.rollback()
            logger.exception(
                "Unexpected error creating budget line",
                extra={
                    "version_id": str(version_id),
                    "actor_id": str(getattr(current_empleado, "id", "")),
                },
            )
            return RedirectResponse(
                url=_presupuestos_redirect_url(
                    version_id=str(version_id),
                    error_msg=_OPERATION_GENERIC_ERROR,
                ),
                status_code=303,
            )

    @router.post("/admin/presupuestos/lineas/{line_id}/update")
    async def admin_presupuestos_update_line(
        request: Request,
        line_id: UUIDType,
        version_id: str = Form(...),
        tournament_key: Optional[str] = Form(None),
        edition_year: Optional[int] = Form(None),
        budget_view: Optional[str] = Form(None),
        phase_filter: Optional[str] = Form(None),
        budget_concept_id: Optional[str] = Form(None),
        concept_name: Optional[str] = Form(None),
        account_code_final: Optional[str] = Form(None),
        cuenta_contable_id: Optional[str] = Form(None),
        phase: Optional[str] = Form(None),
        owner_name: Optional[str] = Form(None),
        priority: Optional[str] = Form(None),
        budget_amount: Optional[float] = Form(None),
        criteria_note: Optional[str] = Form(None),
        observations: Optional[str] = Form(None),
        session: AsyncSession = Depends(get_db_session),
        current_empleado=Depends(get_current_empleado),
    ):
        _require_budget_access(current_empleado, "line_update")
        try:
            form = await request.form()
            cuenta_contable_id_raw = str(
                cuenta_contable_id or form.get("cuenta_contable_id") or ""
            ).strip()
            account_code_from_form = str(
                account_code_final or form.get("account_code_final") or ""
            ).strip()
            concept_cuenta_update: Optional[str] = None
            if "cuenta_contable_id" in form or cuenta_contable_id is not None:
                if cuenta_contable_id_raw:
                    concept_cuenta_update = await validate_active_cuenta_contable_id(
                        session,
                        cuenta_contable_id_raw,
                    )
                    account_row = (
                        await session.execute(
                            text(
                                """
                                SELECT codigo
                                FROM cuentas_contables
                                WHERE CAST(id AS text) = :cuenta_contable_id
                                LIMIT 1
                                """
                            ),
                            {"cuenta_contable_id": concept_cuenta_update},
                        )
                    ).mappings().first()
                    account_code_from_form = str(
                        (account_row or {}).get("codigo") or account_code_from_form
                    ).strip()

            line_level_updates = {
                key: value
                for key, value in {
                    "budget_concept_id": budget_concept_id,
                    "concept_name": concept_name,
                    "account_code_final": account_code_from_form or None,
                    "phase": phase,
                    "owner_name": owner_name,
                    "priority": priority,
                    "budget_amount": budget_amount,
                    "criteria_note": criteria_note,
                    "observations": observations,
                }.items()
                if value is not None
            }
            if concept_cuenta_update and "account_code_final" not in line_level_updates:
                line_level_updates["account_code_final"] = (
                    account_code_from_form or None
                )
            monthly_plan: dict[int, dict[str, float]] = {}
            for key in form.keys():
                key_str = str(key)
                if key_str.startswith("month_") and (
                    key_str.endswith("_expense") or key_str.endswith("_income")
                ):
                    try:
                        parts = key_str.split("_")
                        month_number = int(parts[1])
                        field = parts[2]
                        monthly_plan.setdefault(month_number, {})
                        monthly_plan[month_number][
                            "budget_expense_amount"
                            if field == "expense"
                            else "expected_income_amount"
                        ] = float(form.get(key) or 0)
                    except (TypeError, ValueError, IndexError):
                        continue
                elif key_str.startswith("month_"):
                    try:
                        month_number = int(key_str.split("_", 1)[1])
                        monthly_plan.setdefault(month_number, {})
                        monthly_plan[month_number]["budget_expense_amount"] = float(
                            form.get(key) or 0
                        )
                    except (TypeError, ValueError):
                        continue
            if not line_level_updates and not monthly_plan:
                raise ValueError("No budget line updates were provided")

            if line_level_updates:
                line = await update_budget_line(
                    session,
                    line_id=str(line_id),
                    actor_empleado_id=str(current_empleado.id),
                    updates=line_level_updates,
                )
            else:
                current = (
                    await session.execute(
                        text(
                            """
                            SELECT l.id, l.concept_name, l.budget_concept_id, v.status AS version_status
                            FROM budget_lines l
                            JOIN budget_versions v ON v.id = l.budget_version_id
                            WHERE l.id = :line_id
                            LIMIT 1
                            """
                        ),
                        {"line_id": str(line_id)},
                    )
                ).mappings().first()
                if not current:
                    raise ValueError("Budget line not found")
                from samchat.budgets.service import _editable_version_status

                if not _editable_version_status(current.get("version_status")):
                    raise ValueError(
                        "Only draft or reforecast versions allow line edits"
                    )
                line = dict(current)

            if concept_cuenta_update:
                concept_id = str(line.get("budget_concept_id") or "").strip()
                if concept_id:
                    await update_budget_concept(
                        session,
                        concept_id=concept_id,
                        cuenta_contable_id=concept_cuenta_update,
                        cuenta_contable_provided=True,
                        actor_empleado_id=str(current_empleado.id),
                        commit=True,
                    )

            if monthly_plan:
                await replace_budget_line_monthly_plan(
                    session,
                    budget_line_id=str(line_id),
                    plan=monthly_plan,
                    actor_empleado_id=str(current_empleado.id),
                )
                await session.commit()
            msg = f"Línea actualizada: {line.get('concept_name') or ''}"
            return RedirectResponse(
                url=_presupuestos_redirect_url(
                    edition_year=edition_year,
                    version_id=version_id,
                    tournament_key=tournament_key,
                    budget_view=budget_view,
                    phase_filter=phase_filter,
                    success_msg=msg,
                ),
                status_code=303,
            )
        except Exception:
            await session.rollback()
            logger.exception(
                "Unexpected error updating budget line",
                extra={
                    "line_id": str(line_id),
                    "version_id": version_id,
                    "actor_id": str(getattr(current_empleado, "id", "")),
                },
            )
            return RedirectResponse(
                url=_presupuestos_redirect_url(
                    edition_year=edition_year,
                    version_id=version_id,
                    tournament_key=tournament_key,
                    budget_view=budget_view,
                    phase_filter=phase_filter,
                    error_msg=_OPERATION_GENERIC_ERROR,
                ),
                status_code=303,
            )

    @router.get(
        "/admin/presupuestos/torneo/{tournament_key}", response_class=HTMLResponse
    )
    async def admin_presupuestos_tournament_detail(
        tournament_key: str,
        session: AsyncSession = Depends(get_db_session),
        current_empleado=Depends(get_current_empleado),
        edition_year: Optional[int] = Query(None),
        version_id: Optional[str] = Query(None),
        budget_view: Optional[str] = Query("expenses"),
        phase_filter: Optional[str] = Query(None),
        show_committed: int = Query(1),
        show_yoy: int = Query(0),
        budget_period: str = Query("weekly"),
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
        selected_version = _select_requested_budget_version(
            all_versions,
            requested_version_id=version_id,
            edition_year=resolved_year,
        )
        if selected_version is None:
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
        expense_lines = [line for line in lines if line.get("id") in expense_line_ids]
        income_lines = [
            line for line in lines if line.get("id") not in expense_line_ids
        ]
        selected_phase_filter = str(phase_filter or "").strip()
        selected_budget_view = (
            "income"
            if str(budget_view or "").strip().lower() in {"income", "ingresos"}
            else "expenses"
        )

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
        plan_map = await list_monthly_plan_for_lines(
            session,
            line_ids=[
                line["id"] for line in (filtered_expense_lines + filtered_income_lines)
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
            (
                await session.execute(
                    select(CuentaContable)
                    .where(CuentaContable.activo.is_(True))
                    .order_by(CuentaContable.codigo)
                )
            )
            .scalars()
            .all()
        )
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
            budget_view="expenses",
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
            budget_view="income",
        )
        active_visible_count = (
            len(filtered_income_lines)
            if selected_budget_view == "income"
            else len(filtered_expense_lines)
        )
        active_total_count = (
            len(income_lines)
            if selected_budget_view == "income"
            else len(expense_lines)
        )
        matrix_filters_html = render_budget_matrix_filters(
            tournament_key=tournament_key,
            edition_year=resolved_year,
            version_id=str(selected_version["id"]),
            all_versions=all_versions,
            phase_options=phase_options,
            selected_phase_filter=selected_phase_filter,
            show_committed=bool(show_committed),
            budget_view=selected_budget_view,
            budget_period=budget_period,
            visible_count=active_visible_count,
            total_count=active_total_count,
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
                budget_view="expenses",
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
                cuentas_contables=cuentas_contables,
                budget_view="income",
            )

        cxc_income_url = (
            f"/admin/contabilidad/cuentas-por-cobrar?torneo_id={quote(str(tournament_key))}"
            f"&edition_year={int(resolved_year)}"
        )
        cfdi_income_notice = f"""
            <div style="margin:12px 0;padding:12px;border:1px solid #bfdbfe;border-radius:12px;background:#eff6ff;color:#1e3a8a;font-size:13px;">
                La vinculación de CFDI PSP a ingreso real ahora se opera desde
                <a href="{cxc_income_url}" style="color:#0f766e;font-weight:800;">Cuentas por Cobrar</a>.
                Presupuestos conserva la planeación; CxC concentra la cartera y la cobranza real.
            </div>
        """
        income_export_url = (
            f"/admin/presupuestos/torneo/{quote(str(tournament_key))}/ingresos/export.xlsx"
            f"?edition_year={int(resolved_year)}&version_id={quote(str(selected_version['id']))}"
        )
        income_import_html = ""
        if access.get("line_update"):
            income_import_html = f"""
                <form method="POST" action="/admin/presupuestos/torneo/{quote(str(tournament_key))}/ingresos/import" enctype="multipart/form-data" style="display:flex;flex-wrap:wrap;gap:10px;align-items:end;margin:12px 0;">
                    <input type="hidden" name="edition_year" value="{int(resolved_year)}">
                    <input type="hidden" name="version_id" value="{escape_html(str(selected_version['id']), quote=True)}">
                    <label style="display:grid;gap:4px;font-size:12px;font-weight:700;color:#475569;">
                        Importar ingresos
                        <input type="file" name="archivo_presupuesto" accept=".xlsx,.xlsm,.csv" required>
                    </label>
                    <button type="submit" class="button">Cargar ingresos</button>
                    <a class="button secondary" href="{income_export_url}">Descargar ingresos</a>
                </form>
            """
        else:
            income_import_html = (
                f'<div style="margin:12px 0;"><a class="button secondary" '
                f'href="{income_export_url}">Descargar ingresos</a></div>'
            )

        success_html = _render_budget_status_message(success_msg, is_error=False)
        error_html = _render_budget_status_message(error_msg, is_error=True)
        gastos_section_html = f"""
            {create_expense_line_form}
            <section class="workspace-card" id="presupuesto-gastos" style="margin-bottom:18px;">
                <div class="workspace-section-title">Gastos</div>
                <div class="workspace-section-subtitle">Captura presupuesto de gasto; gasto real se calcula automáticamente (caja).</div>
                {matrix_filters_html}
                {gastos_matrix_html}
            </section>
        """
        ingresos_section_html = f"""
            <section class="workspace-card" id="presupuesto-ingresos">
                <div class="workspace-section-title">Ingresos</div>
                <div class="workspace-section-subtitle">Captura ingreso esperado; ingreso real se alimenta con CFDI PSP vinculados.</div>
                {create_income_line_form}
                {income_import_html}
                {cfdi_income_notice}
                {matrix_filters_html}
                {ingresos_matrix_html}
            </section>
        """
        active_budget_section_html = (
            ingresos_section_html
            if selected_budget_view == "income"
            else gastos_section_html
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
                actions_html=(
                    f'<a class="button secondary" href="{back_url}">← Dashboard</a>'
                    '<a class="button secondary" href="/admin/gastos/sat">SAT / CFDI</a>'
                    '<a class="button secondary" href="/admin/gastos/cfdis/matching">Matching CFDI</a>'
                ),
                side_html=(
                    f'<div class="meta-grid">'
                    f'<div class="meta-card"><span>Presupuesto gasto</span><strong>${rollups.get("budget_expense_total", 0):,.2f}</strong></div>'
                    f'<div class="meta-card"><span>Ingreso esperado</span><strong>${rollups.get("expected_income_total", 0):,.2f}</strong></div>'
                    f'</div>'
                ),
            )}
            {render_budget_detail_section_nav(
                tournament_key=tournament_key,
                edition_year=resolved_year,
                version_id=str(selected_version["id"]),
                selected_view=selected_budget_view,
                phase_filter=selected_phase_filter or None,
                show_committed=bool(show_committed),
                budget_period=budget_period,
            )}
            {success_html}{error_html}
            {render_budget_executive_dashboard(
                filtered_income_lines if selected_budget_view == "income" else filtered_expense_lines,
                plan_map=plan_map,
                actuals_map=actuals_map,
                tournament_key=tournament_key,
                edition_year=resolved_year,
                version_id=str(selected_version["id"]),
                budget_view=selected_budget_view,
                budget_period=budget_period,
                phase_filter=selected_phase_filter or None,
                show_committed=bool(show_committed),
            )}
            {active_budget_section_html}
        </div></body></html>
        """
        return HTMLResponse(content=html)

    @router.post("/admin/presupuestos/torneo/{tournament_key}/ingresos/import")
    async def admin_presupuestos_import_income_lines(
        tournament_key: str,
        version_id: str = Form(...),
        edition_year: Optional[int] = Form(None),
        archivo_presupuesto: UploadFile = File(...),
        session: AsyncSession = Depends(get_db_session),
        current_empleado=Depends(get_current_empleado),
    ):
        _require_budget_access(current_empleado, "line_update")
        try:
            payload = await archivo_presupuesto.read()
            if not payload:
                raise ValueError("El archivo de ingresos está vacío.")
            result = await import_budget_lines_upload(
                session,
                version_id=str(version_id),
                actor_empleado_id=str(current_empleado.id),
                file_bytes=payload,
                filename=archivo_presupuesto.filename or "presupuesto_ingresos.xlsx",
                line_direction="income",
            )
            msg = (
                "Partidas de ingreso cargadas: "
                f"{int(result.get('rows_processed') or 0)} fila(s)"
            )
            return RedirectResponse(
                url=budget_tournament_detail_url(
                    tournament_key,
                    edition_year=edition_year,
                    version_id=str(version_id),
                    budget_view="income",
                    success_msg=msg,
                ),
                status_code=303,
            )
        except ValueError as exc:
            await session.rollback()
            return RedirectResponse(
                url=budget_tournament_detail_url(
                    tournament_key,
                    edition_year=edition_year,
                    version_id=str(version_id),
                    budget_view="income",
                    error_msg=str(exc)[:180],
                ),
                status_code=303,
            )
        except Exception:
            await session.rollback()
            logger.exception("Unexpected error importing budget income lines")
            return RedirectResponse(
                url=budget_tournament_detail_url(
                    tournament_key,
                    edition_year=edition_year,
                    version_id=str(version_id),
                    budget_view="income",
                    error_msg="No se pudieron importar las partidas de ingreso.",
                ),
                status_code=303,
            )

    @router.get("/admin/presupuestos/torneo/{tournament_key}/ingresos/export.xlsx")
    async def admin_presupuestos_export_income_xlsx(
        tournament_key: str,
        edition_year: Optional[int] = Query(None),
        version_id: Optional[str] = Query(None),
        session: AsyncSession = Depends(get_db_session),
        current_empleado=Depends(get_current_empleado),
    ):
        _require_budget_access(current_empleado, "export")
        await ensure_budget_schema(session)
        tournament_ctx = await resolve_budget_tournament_context(
            session, tournament_key=tournament_key
        )
        if tournament_ctx is None:
            return RedirectResponse(
                url=budget_dashboard_url(error_msg="Torneo no encontrado"),
                status_code=303,
            )
        resolved_year = edition_year or date.today().year
        all_versions = await list_budget_versions(session)
        selected_version = _select_requested_budget_version(
            all_versions,
            requested_version_id=version_id,
            edition_year=resolved_year,
        )
        if selected_version is None:
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

        income_lines = await list_budget_lines(
            session,
            version_id=selected_version["id"],
            tournament_id=tournament_ctx.get("tournament_id"),
            tournament_code=tournament_ctx.get("tournament_code"),
            line_direction="income",
            limit=5000,
        )
        income_lines = await attach_cuenta_contable_to_budget_lines(
            session,
            income_lines,
        )
        plan_map = await list_monthly_plan_for_lines(
            session,
            line_ids=[line["id"] for line in income_lines],
        )
        actuals_map = await build_budget_monthly_actuals(
            session,
            edition_year=resolved_year,
            version_id=selected_version["id"],
            tournament_id=tournament_ctx.get("tournament_id"),
            tournament_name=tournament_ctx.get("tournament_name"),
            tournament_code=tournament_ctx.get("tournament_code"),
        )
        links = await list_budget_cfdi_income_links(
            session,
            budget_version_id=str(selected_version["id"]),
            tournament_id=str(tournament_ctx.get("tournament_id") or "") or None,
        )
        candidates = await list_psp_cfdi_income_candidates(
            session,
            budget_version_id=str(selected_version["id"]),
        )
        payload = generate_budget_income_xlsx(
            lines=income_lines,
            plan_map=plan_map,
            actuals_map=actuals_map,
            links=links,
            candidates=candidates,
            selected_version=selected_version,
            tournament_context=tournament_ctx,
            edition_year=resolved_year,
        )
        safe_tournament = (
            str(tournament_ctx.get("tournament_code") or tournament_key)
            .lower()
            .replace(" ", "_")
        )
        headers = {
            "Content-Disposition": (
                f'attachment; filename="presupuesto_ingresos_{resolved_year}_{safe_tournament}.xlsx"'
            )
        }
        return Response(
            content=payload,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers=headers,
        )

    @router.get("/admin/presupuestos/torneo/{tournament_key}/cfdi-ingresos")
    async def admin_presupuestos_cfdi_income_redirect(
        tournament_key: str,
        edition_year: Optional[int] = Query(None),
    ):
        return RedirectResponse(
            url=budget_tournament_detail_url(
                tournament_key,
                edition_year=edition_year,
                budget_view="income",
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
        return_to: Optional[str] = Form(None),
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
            fallback_url = budget_tournament_detail_url(
                tournament_key,
                edition_year=edition_year,
                budget_view="income",
                success_msg=msg,
            )
            return RedirectResponse(
                url=_safe_cfdi_income_return_url(return_to, fallback_url),
                status_code=303,
            )
        except CFDIIncomeBridgeError as exc:
            await session.rollback()
            fallback_url = budget_tournament_detail_url(
                tournament_key,
                edition_year=edition_year,
                budget_view="income",
                error_msg=str(exc),
            )
            return RedirectResponse(
                url=_safe_cfdi_income_return_url(return_to, fallback_url),
                status_code=303,
            )
        except Exception:
            await session.rollback()
            logger.exception("CFDI PSP income link failed")
            fallback_url = budget_tournament_detail_url(
                tournament_key,
                edition_year=edition_year,
                budget_view="income",
                error_msg="No se pudo vincular el CFDI PSP.",
            )
            return RedirectResponse(
                url=_safe_cfdi_income_return_url(return_to, fallback_url),
                status_code=303,
            )

    @router.post(
        "/admin/presupuestos/torneo/{tournament_key}/cfdi-ingresos/upload-link"
    )
    async def admin_presupuestos_upload_link_cfdi_income(
        tournament_key: str,
        budget_line_id: str = Form(...),
        amount: str = Form(""),
        income_date: str = Form(""),
        edition_year: Optional[int] = Form(None),
        return_to: Optional[str] = Form(None),
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
            fallback_url = budget_tournament_detail_url(
                tournament_key,
                edition_year=edition_year,
                budget_view="income",
                success_msg="CFDI PSP cargado y vinculado a ingreso real.",
            )
            return RedirectResponse(
                url=_safe_cfdi_income_return_url(return_to, fallback_url),
                status_code=303,
            )
        except CFDIIncomeBridgeError as exc:
            await session.rollback()
            fallback_url = budget_tournament_detail_url(
                tournament_key,
                edition_year=edition_year,
                budget_view="income",
                error_msg=str(exc),
            )
            return RedirectResponse(
                url=_safe_cfdi_income_return_url(return_to, fallback_url),
                status_code=303,
            )
        except Exception:
            await session.rollback()
            logger.exception("CFDI PSP income upload-link failed")
            fallback_url = budget_tournament_detail_url(
                tournament_key,
                edition_year=edition_year,
                budget_view="income",
                error_msg="No se pudo cargar y vincular el CFDI PSP.",
            )
            return RedirectResponse(
                url=_safe_cfdi_income_return_url(return_to, fallback_url),
                status_code=303,
            )

    @router.post(
        "/admin/presupuestos/torneo/{tournament_key}/cfdi-ingresos/{link_id}/unlink"
    )
    async def admin_presupuestos_unlink_cfdi_income(
        tournament_key: str,
        link_id: str,
        edition_year: Optional[int] = Form(None),
        return_to: Optional[str] = Form(None),
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
            fallback_url = budget_tournament_detail_url(
                tournament_key,
                edition_year=edition_year,
                budget_view="income",
                success_msg=(
                    "El CFDI PSP dejó de contar como ingreso real."
                    if unlinked
                    else "El CFDI PSP ya estaba desvinculado."
                ),
            )
            return RedirectResponse(
                url=_safe_cfdi_income_return_url(return_to, fallback_url),
                status_code=303,
            )
        except Exception:
            await session.rollback()
            logger.exception("CFDI PSP income unlink failed")
            fallback_url = budget_tournament_detail_url(
                tournament_key,
                edition_year=edition_year,
                budget_view="income",
                error_msg="No se pudo desvincular el CFDI PSP.",
            )
            return RedirectResponse(
                url=_safe_cfdi_income_return_url(return_to, fallback_url),
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
