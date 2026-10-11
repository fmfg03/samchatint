"""Governed tournament workbook preview/apply, using existing budget authority."""

from __future__ import annotations

from html import escape
from typing import Any, Callable
from urllib.parse import urlencode
from uuid import UUID

from fastapi import Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import HTMLResponse

from samchat.budgets.tournament_upload import (
    MAX_UPLOAD_BYTES,
    apply_tournament_upload,
    preview_tournament_upload,
)


def _page(
    tournament_id: str,
    version_id: str,
    edition_year: int,
    plan: dict[str, Any] | None = None,
) -> str:
    def safe(value: Any) -> str:
        return escape(str(value or ""), quote=True)

    plan = plan or {}
    rows = "".join(
        "<tr>"
        + "".join(
            "<td>" + safe(row.get(k)) + "</td>"
            for k in (
                "row",
                "phase",
                "concept_name",
                "original_amount",
                "budget_amount",
                "before_amount",
                "before_phase",
                "action",
            )
        )
        + "</tr>"
        for row in plan.get("operations", [])
    )
    blockers = "".join("<li>" + safe(b) + "</li>" for b in plan.get("blockers", []))
    summary = ""
    if plan:
        summary = (
            f'<h2>Vista previa: {safe(plan.get("filename"))}</h2>'
            f'<p>Hoja: {safe(plan.get("sheet"))} · Total original: '
            f'{safe(plan.get("original_total"))} · Total por partidas: '
            f'{safe(plan.get("total"))} · Diferencia por redondeo: '
            f'{safe(plan.get("rounding_delta"))}</p><ul>{blockers}</ul>'
            "<table><thead><tr><th>Fila</th><th>Fase / subproyecto</th>"
            "<th>Partida</th><th>Original</th><th>A dos decimales</th>"
            "<th>Importe previo</th><th>Fase previa</th><th>Acción</th></tr></thead>"
            f"<tbody>{rows}</tbody></table>"
        )
    apply_button = ""
    if plan and not plan.get("blockers"):
        apply_button = (
            '<label><input type="checkbox" name="confirmed" value="true">'
            "Confirmo aplicar las partidas de esta vista previa</label>"
            '<button name="action" value="apply">Aplicar presupuesto</button>'
        )
    params = urlencode({"version_id": version_id, "edition_year": edition_year})
    includes_selected = "selected" if plan.get("tax_basis") == "includes_vat" else ""
    excludes_selected = "selected" if plan.get("tax_basis") == "excludes_vat" else ""
    rounding_checked = "checked" if plan.get("confirmed_rounding") else ""
    updates_checked = "checked" if plan.get("confirmed_updates") else ""
    back_url = f"/admin/presupuestos/torneo/{tournament_id}?{params}"
    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <title>Revisar presupuesto</title><style>
    body{{font:16px system-ui;margin:2rem;max-width:1200px;color:#172033}}
    label{{display:block;margin:1rem 0}} table{{border-collapse:collapse;width:100%}}
    td,th{{padding:.6rem;text-align:left;border-bottom:1px solid #ddd}}
    .scroll{{overflow:auto}} button{{padding:.6rem;margin:.5rem}}
    </style></head><body><a href="{safe(back_url)}">Volver al presupuesto</a>
    <h1>Revisar presupuesto</h1><p>Revisa partidas y totales antes de aplicar.
    Para aplicar, vuelve a seleccionar el mismo archivo que revisaste.</p>
    <div class="scroll">{summary}</div>
    <form method="post" enctype="multipart/form-data">
    <input type="hidden" name="version_id" value="{safe(version_id)}">
    <input type="hidden" name="edition_year" value="{edition_year}">
    <input type="hidden" name="approved_plan_sha256"
    value="{safe(plan.get('plan_sha256'))}">
    <label>Presupuesto XLSX <input type="file" name="archivo_presupuesto"
    accept=".xlsx" required></label>
    <label>Moneda <input name="currency" maxlength="3" placeholder="MXN"
    value="{safe(plan.get('currency'))}"></label>
    <label>Tratamiento de IVA <select name="tax_basis">
    <option value="">Selecciona</option>
    <option value="includes_vat" {includes_selected}>Incluye IVA</option>
    <option value="excludes_vat" {excludes_selected}>No incluye IVA</option>
    </select></label>
    <label><input type="checkbox" name="confirmed_rounding"
    value="true" {rounding_checked}>
    Confirmo el redondeo por partida a dos decimales mostrado en la vista previa</label>
    <label><input type="checkbox" name="confirmed_updates"
    value="true" {updates_checked}>
    Confirmo las actualizaciones de importes existentes
    mostradas en esta vista previa</label>
    <button name="action" value="preview">Revisar archivo</button>{apply_button}
    </form></body></html>"""


def register_tournament_budget_upload_routes(
    router: Any,
    *,
    get_db_session: Callable,
    get_current_empleado: Callable,
    require_budget_access: Callable,
) -> None:
    """Register routes using the budget module's existing session and permissions."""

    @router.get(
        "/admin/presupuestos/torneo/{tournament_id}/archivo",
        response_class=HTMLResponse,
    )
    async def upload_form(
        tournament_id: UUID,
        version_id: UUID = Query(...),
        edition_year: int = Query(...),
        current_empleado=Depends(get_current_empleado),
    ):
        require_budget_access(current_empleado, "read")
        return HTMLResponse(_page(str(tournament_id), str(version_id), edition_year))

    @router.post(
        "/admin/presupuestos/torneo/{tournament_id}/archivo",
        response_class=HTMLResponse,
    )
    async def upload_preview_or_apply(
        tournament_id: UUID,
        version_id: UUID = Form(...),
        edition_year: int = Form(...),
        archivo_presupuesto: UploadFile = File(...),
        action: str = Form("preview"),
        currency: str = Form(""),
        tax_basis: str = Form(""),
        confirmed_rounding: bool = Form(False),
        confirmed_updates: bool = Form(False),
        confirmed: bool = Form(False),
        approved_plan_sha256: str = Form(""),
        session=Depends(get_db_session),
        current_empleado=Depends(get_current_empleado),
    ):
        require_budget_access(current_empleado, "read")
        if action not in {"preview", "apply"}:
            raise HTTPException(400, "Acción inválida.")
        if action == "apply":
            require_budget_access(current_empleado, "line_update")
            require_budget_access(current_empleado, "create")
        payload = await archivo_presupuesto.read(MAX_UPLOAD_BYTES + 1)
        args = {
            "tournament_id": str(tournament_id),
            "version_id": str(version_id),
            "edition_year": edition_year,
            "filename": archivo_presupuesto.filename or "",
            "file_bytes": payload,
            "currency": currency.strip().upper() or None,
            "tax_basis": tax_basis or None,
            "confirmed_rounding": confirmed_rounding,
            "confirmed_updates": confirmed_updates,
        }
        try:
            if action == "apply":
                await apply_tournament_upload(
                    session,
                    actor_empleado_id=str(current_empleado.id),
                    approved_plan_sha256=approved_plan_sha256,
                    confirmed=confirmed,
                    **args,
                )
                return HTMLResponse(
                    "<p>Presupuesto aplicado y auditado.</p>"
                    + _page(str(tournament_id), str(version_id), edition_year)
                )
            plan = await preview_tournament_upload(session, **args)
            return HTMLResponse(
                _page(str(tournament_id), str(version_id), edition_year, plan)
            )
        except ValueError as exc:
            await session.rollback()
            return HTMLResponse(
                "<p>"
                + escape(str(exc))
                + "</p>"
                + _page(str(tournament_id), str(version_id), edition_year),
                status_code=422,
            )
