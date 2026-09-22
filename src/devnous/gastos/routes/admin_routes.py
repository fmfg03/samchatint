Warning: truncated output (original token count: 50110)
Total output lines: 5000

"""
Admin routes for expense and invoice management.

Provides HTML interfaces for viewing and exporting expense and invoice data.
"""

import csv
import io
import json
import logging
import os
import sys
import time
import asyncio
import secrets
import unicodedata
from collections import deque
from datetime import date, datetime
from decimal import Decimal
from html import escape
from pathlib import Path
from threading import Lock
from typing import Optional, List, Any, Union, Dict, Mapping, Tuple
from urllib.parse import quote, urlencode
from uuid import UUID as UUIDType, uuid4
from base64 import urlsafe_b64decode, urlsafe_b64encode
from dotenv import dotenv_values, load_dotenv
from fastapi import (
    APIRouter,
    Request,
    Query,
    Depends,
    Form,
    HTTPException,
    File,
    UploadFile,
)
from fastapi.responses import (
    HTMLResponse,
    Response,
    JSONResponse,
    RedirectResponse,
    FileResponse,
)
from sqlalchemy import select, and_, or_, func, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..models import (
    Aprobacion,
    Documento,
    ExpenseReport,
    InvoiceReport,
    CFDIReport,
    Tournament,
    TournamentOperationsLink,
    RFCConfig,
    Empleado,
    CuentaContable,
    CentroDeCosto,
    ProveedorCliente,
    SolicitudPrestamo,
)
from ..status_semantics import payment_run_status_visual
from ..services.import_balanza_service import parse_cuentas_contables_upload
from ..services.import_aux_service import import_aux_workbook
from ..services.import_bank_movements_service import import_bank_movements_csv
from ..services.import_coi_admin_service import COIUploadSummary
from ..services.coi_poliza_exporter import (
    ExpenseCFDI,
    generate_coi_poliza_xlsx,
    generate_coi_poliza_zip,
)
from ..services.import_coi_service import import_coi_workbook
from ..services.import_proveedores_service import (
    parse_proveedores_clientes_upload,
    proveedor_match_key,
)
from ..services.import_runa_payroll_service import import_runa_payroll_workbook
from ..services.tournament_phase_service import (
    get_tournament_etapas,
    get_tournament_scope_labels,
    get_tournament_scope_options,
    tournament_scope_config_changed,
)
from ..services.tournament_project_visibility import (
    DEFAULT_OPERATIONS_ONLY_VISIBILITY,
    format_form_visibility_areas_label,
    parse_form_visibility_areas_from_form,
    render_form_visibility_areas_checkboxes,
)
from ..services.tournament_authority_service import (
    GovernedGastosProjectError,
    TournamentAuthorityUnavailableError,
    require_ungoverned_gastos_project,
)
from ..services.tocino_client import TocinoAPIError, get_tocino_client
from ..services.cfdi_expense_link_service import (
    bulk_link_pending_documentos_to_cfdi_reports,
    bulk_link_pending_expenses_to_cfdi_reports,
    link_documento_to_cfdi_if_manual_uuid_set,
    link_expense_to_cfdi_if_manual_uuid_set,
    normalize_cfdi_uuid_to_canonical,
)
from ..services.cfdi_batch1_status_service import (
    evaluate_ar_status,
    evaluate_three_way_match,
)
from ..services.empleado_onboarding_email import send_initial_password_email
from ..services.finance_training_seed_service import (
    cleanup_finance_training_dataset,
    generate_finance_training_dataset,
    manifest_path,
    cfdi_csv_path as training_cfdi_csv_path,
    reset_finance_training_dataset,
)
from ..services.expense_accounting_service import (
    build_expense_accounting_preview,
    resolve_counterpart_account,
)
from ..services.employee_debtor_accounting_service import (
    DEBTOR_ACCOUNT_PREFIX,
    build_debtors_admin_snapshot,
)
from ..services.expense_accounting_cleanup_service import (
    safe_build_cleanup_preview,
    list_unassigned_cfdi_options,
    load_cleanup_expenses,
    resolve_default_cleanup_contra_cuenta,
    save_expense_cleanup,
)
from ..services.budget_concept_account_service import (
    build_cleanup_accounting_display,
    resolve_effective_budget_concept,
)
from ..services.customer_success_usage import (
    build_customer_success_usage_report,
    customer_success_usage_csv_rows,
    render_customer_success_usage_tracker_script,
)
from ..services.customer_success_audit import (
    build_customer_success_audit_report,
    is_superadmin_role,
)
from ..services.access_control_service import is_catalog_admin_user
from ..services.payment_run_service import (
    PaymentRunPermissionError,
    PaymentRunValidationError,
    can_access_payment_run,
    can_manage_payment_run,
    close_payment_run,
    get_payment_run_closure,
    list_payment_run_closures,
    list_payment_run_items,
    parse_payment_run_date,
    can_confirm_payment_run_payment,
    require_payment_run_access,
    require_payment_run_manager,
    require_payment_run_payment_confirmation,
    update_payment_run_fecha_pago,
)
from ..services.payment_run_exporter import generate_payment_run_order_xlsx
from ..services.loan_request_service import (
    PRESTAMO_STATUS_APROBADA,
    PRESTAMO_STATUS_EN_PROCESO_PAGO,
)
from ..services.documento_service import (
    SolicitudTercerosAttachment,
    SolicitudValidationError,
    add_solicitud_documento_adjuntos,
)
from ..utils.receipt_bytes import resolve_media_type
from ..services.access_control_service import filter_cards_by_tools, visible_tools_for
from ..services.telegram_console import TELEGRAM_APPROVER_ROLES
from ..utils.receipt_bytes import (
    fetch_expense_ids_with_archivo_data,
    fetch_gasto_adjuntos_meta_batch,
    html_expense_archivos_cell,
)
from ..empleado_rol_normalize import normalize_empleado_rol_from_form
from .dependencies import (
    get_current_empleado,
    has_permission,
    require_admin_finanzas,
    require_permission_factory,
)
from .auth_routes import get_password_hash
from devnous.tournaments.config import ACTIVE_TOURNAMENT_SCOPE
from samchat.budgets.service import (
    build_budget_commitment_expense_preview,
    DEFAULT_BUDGET_ARTIFACT,
    DEFAULT_BUDGET_CONCEPT_PASIVO_ACCOUNT_CODE,
    budget_alias_candidates,
    build_budget_executive_alerts,
    build_budget_executive_comparison,
    build_budget_scenario_player,
    build_budget_snapshot,
    bulk_save_budget_concepts,
    clear_budget_concept_scope_for_tournament,
    create_budget_line,
    ensure_budget_schema,
    hide_budget_concept,
    import_budget_artifact,
    generate_budget_concepts_catalog_xlsx,
    import_budget_concepts_upload,
    import_budget_lines_upload,
    list_budget_audit_events,
    list_budget_concepts,
    list_budget_lines,
    list_budget_tournament_commitments,
    list_budget_versions,
    list_monthly_allocations_for_lines,
    replace_budget_line_monthly_allocations,
    resolve_definitive_budget_version,
    resolve_definitive_budget_version_from_versions,
    update_budget_concept,
    update_budget_line,
)
from samchat.sam_inbox import build_sam_inbox_payload
from samchat.assistant.soul_wizard import (
    build_soul_wizard_contract,
    build_soul_wizard_payload_from_form,
)
from devnous.sat.sat_handler import SATExpenseHandler
from devnous.sat.sat_sync_service import SATSyncService
from devnous.gastos.services.sat_catalog_service import (
    list_sat_catalogs,
    render_catalog_preview_rows,
)

logger = logging.getLogger(__name__)


async def _ensure_cfdi_project_assignment_schema(session: AsyncSession) -> None:
    """Manual operational classification for loose CFDI; does not alter fiscal XML truth."""
    await session.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS cfdi_project_assignments (
                id UUID PRIMARY KEY,
                cfdi_report_id UUID NOT NULL REFERENCES cfdi_reports(id) ON UPDATE CASCADE ON DELETE CASCADE,
                tournament_id UUID NULL REFERENCES tournaments(id) ON UPDATE CASCADE ON DELETE SET NULL,
                proyecto_otro TEXT NULL,
                phase TEXT NULL,
                note TEXT NULL,
                assigned_by_empleado_id UUID NULL REFERENCES empleados(id) ON UPDATE CASCADE ON DELETE SET NULL,
                removed_by_empleado_id UUID NULL REFERENCES empleados(id) ON UPDATE CASCADE ON DELETE SET NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                removed_at TIMESTAMPTZ NULL
            )
            """
        )
    )
    await session.execute(text("CREATE INDEX IF NOT EXISTS ix_cfdi_project_assignments_cfdi ON cfdi_project_assignments(cfdi_report_id)"))
    await session.execute(text("CREATE INDEX IF NOT EXISTS ix_cfdi_project_assignments_tournament ON cfdi_project_assignments(tournament_id) WHERE removed_at IS NULL"))
    await session.execute(text("CREATE INDEX IF NOT EXISTS ix_cfdi_project_assignments_otro ON cfdi_project_assignments(proyecto_otro) WHERE removed_at IS NULL"))
    await session.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_cfdi_project_assignments_active_cfdi ON cfdi_project_assignments(cfdi_report_id) WHERE removed_at IS NULL"))
    await session.commit()


_PROFILE_ACTION_LABELS: list[tuple[str, str]] = [
    ("read", "Ver"),
    ("create", "Crear"),
    ("update", "Editar"),
    ("approve", "Aprobar"),
    ("execute", "Ejecutar"),
    ("export", "Exportar"),
    ("manage", "Admin"),
]

_PROFILE_PERMISSION_MATRIX: list[dict[str, Any]] = [
    {
        "module": "Torneos",
        "capability": "Catálogo",
        "token_prefix": "tournaments.catalog",
    },
    {
        "module": "Torneos",
        "capability": "Publicación",
        "token_prefix": "tournaments.publication",
    },
    {
        "module": "Operaciones",
        "capability": "Carpetas",
        "token_prefix": "operations.folders",
    },
    {
        "module": "Operaciones",
        "capability": "Equipos",
        "token_prefix": "operations.teams",
    },
    {
        "module": "Operaciones",
        "capability": "Jugadores",
        "token_prefix": "operations.players",
    },
    {
        "module": "Operaciones",
        "capability": "OCR / cédulas",
        "token_prefix": "operations.ocr",
    },
    {
        "module": "Operaciones",
        "capability": "Calendario",
        "token_prefix": "operations.schedule",
    },
    {
        "module": "Operaciones",
        "capability": "Resultados",
        "token_prefix": "operations.standings",
    },
    {
        "module": "Documentos",
        "capability": "Checklist",
        "token_prefix": "documents.checklist",
    },
    {
        "module": "Documentos",
        "capability": "Verificación",
        "token_prefix": "documents.players",
    },
    {
        "module": "Finanzas",
        "capability": "Solicitudes",
        "token_prefix": "finance.solicitudes",
    },
    {"module": "Finanzas", "capability": "Pagos", "token_prefix": "finance.payments"},
    {
        "module": "Finanzas",
        "capability": "Comprobaciones",
        "token_prefix": "finance.reimbursements",
    },
    {
        "module": "Contabilidad",
        "capability": "Cuentas",
        "token_prefix": "accounting.accounts",
    },
    {
        "module": "Contabilidad",
        "capability": "Pólizas",
        "token_prefix": "accounting.entries",
    },
    {
        "module": "Contabilidad",
        "capability": "Conciliación",
        "token_prefix": "accounting.reconciliation",
    },
    {
        "module": "BI / C-suite",
        "capability": "Reportes",
        "token_prefix": "executive.reports",
    },
    {"module": "BI / C-suite", "capability": "Presupuestos", "token_prefix": "budgets"},
    {
        "module": "BI / C-suite",
        "capability": "Versiones presupuesto",
        "token_prefix": "budgets.version",
    },
    {
        "module": "BI / C-suite",
        "capability": "Líneas presupuesto",
        "token_prefix": "budgets.line",
    },
    {
        "module": "BI / C-suite",
        "capability": "Auditoría presupuesto",
        "token_prefix": "budgets.audit",
    },
    {
        "module": "BI / C-suite",
        "capability": "Planeador",
        "token_prefix": "executive.planner",
    },
    {
        "module": "BI / C-suite",
        "capability": "Alertas",
        "token_prefix": "executive.alerts",
    },
    {
        "module": "Comunicaciones",
        "capability": "Email",
        "token_prefix": "communications.email",
    },
    {
        "module": "Comunicaciones",
        "capability": "WhatsApp",
        "token_prefix": "communications.whatsapp",
    },
    {"module": "Marketing", "capability": "Media", "token_prefix": "marketing.media"},
    {
        "module": "Marketing",
        "capability": "Encuestas",
        "token_prefix": "marketing.surveys",
    },
    {"module": "Seguridad", "capability": "Usuarios", "token_prefix": "admin.users"},
    {"module": "Seguridad", "capability": "Perfiles", "token_prefix": "admin.profiles"},
    {"module": "Auditoría", "capability": "Logs", "token_prefix": "audit.logs"},
]

_PROFILE_SCOPE_OPTIONS: list[tuple[str, str]] = [
    ("scope:tournament:lttb-2026", "Liga Telmex Telcel 2026"),
    ("scope:sport:beisbol", "Béisbol"),
    ("scope:sport:futbol", "Fútbol"),
    ("scope:area:operations", "Operaciones"),
    ("scope:area:finance", "Finanzas"),
    ("scope:area:executive", "C-suite"),
    ("scope:period:2026", "Edición 2026"),
]

_PROFILE_PRESETS: dict[str, dict[str, Any]] = {
    "superadmin": {
        "label": "Superadmin",
        "base_role": "superadmin",
        "description": "Control total sobre operación, finanzas, contabilidad, seguridad y presupuestos.",
        "permissions": [
            "admin.*",
            "budgets.*",
            "finance.*",
            "accounting.*",
            "operations.*",
            "communications.*",
            "marketing.*",
            "executive.*",
            "audit.logs.read",
        ],
    },
    "admin_operaciones": {
        "label": "Admin operaciones",
        "base_role": "admin",
        "description": "Opera torneos, carpetas, documentos y comunicaciones sin tocar contabilidad.",
        "permissions": [
            "admin.operaciones.manage",
            "admin.torneos.manage",
            "operations.*",
            "documents.*",
            "communications.email.execute",
            "communications.whatsapp.execute",
            "marketing.media.read",
            "marketing.media.create",
            "executive.reports.read",
        ],
    },
    "operador_torneo": {
        "label": "Operador torneo",
        "base_role": "coordinador",
        "description": "Trabajo diario por torneo/entidad con operación y documentos.",
        "permissions": [
            "operations.folders.read",
            "operations.folders.create",
            "operations.folders.update",
            "operations.teams.read",
            "operations.teams.create",
            "operations.teams.update",
            "operations.players.read",
            "operations.players.create",
            "operations.players.update",
            "operations.ocr.read",
            "operations.ocr.execute",
            "documents.checklist.read",
            "documents.checklist.update",
            "documents.players.read",
            "documents.players.update",
            "communications.email.read",
            "communications.whatsapp.read",
        ],
    },
    "operaciones_presupuesto": {
        "label": "Lectura de presupuestos - Alicia",
        "base_role": "empleado",
        "description": "Acceso de solo lectura reservado a Alicia; las modificaciones requieren superadmin.",
        "permissions": [
            "budgets.read",
            "budgets.version.read",
            "budgets.line.read",
            "budgets.audit.read",
        ],
    },
    "solicitudes_beneficiario_empleado": {
        "label": "Solicitudes para empleados terceros",
        "base_role": "empleado",
        "description": "Permite crear anticipos e informes a nombre de otro empleado activo sin cambiar la bandeja de autorizaci?n del solicitante.",
        "permissions": [
            "finance.employee_beneficiary.request",
        ],
    },
    "finanzas": {
        "label": "Finanzas",
        "base_role": "finanzas",
        "description": "Solicitudes, pagos, comprobaciones y reportes financieros.",
        "permissions": [
            "admin.finanzas.manage",
            "finance.solicitudes.*",
            "finance.payments.*",
            "finance.reimbursements.*",
            "finance.employee_beneficiary.request",
            "executive.reports.read",
        ],
    },
    "contabilidad": {
        "label": "Contabilidad",
        "base_role": "finanzas",
        "description": "Clasificación contable, pólizas y conciliación.",
        "permissions": [
            "accounting.accounts.*",
            "accounting.entries.*",
            "accounting.reconciliation.*",
            "finance.solicitudes.read",
            "finance.payments.read",
            "contabilidad.pagos.marcar_pagado",
            "finance.employee_beneficiary.request",
            "executive.reports.read",
        ],
    },
    "c_suite": {
        "label": "C-suite",
        "base_role": "admin",
        "description": "Reportes ejecutivos, presupuestos, flujo y alertas corporativas.",
        "permissions": [
            "executive.reports.*",
            "executive.planner.*",
            "executive.alerts.*",
            "budgets.read",
            "budgets.export",
            "budgets.version.read",
            "budgets.line.read",
            "budgets.audit.read",
            "finance.solicitudes.read",
            "finance.payments.read",
        ],
    },
    "marketing": {
        "label": "Marketing",
        "base_role": "coordinador",
        "description": "Media, activaciones, encuestas y comunicaciones de torneo.",
        "permissions": [
            "marketing.media.*",
            "marketing.surveys.*",
            "communications.email.read",
            "communications.email.execute",
            "communications.whatsapp.read",
            "communications.whatsapp.execute",
            "operations.folders.read",
        ],
    },
    "solo_lectura": {
        "label": "Solo lectura",
        "base_role": "empleado",
        "description": "Consulta transversal sin escrituras.",
        "permissions": [
            "operations.*.read",
            "documents.*.read",
            "finance.solicitudes.read",
            "finance.payments.read",
            "accounting.accounts.read",
            "accounting.entries.read",
            "executive.reports.read",
            "marketing.media.read",
            "communications.email.read",
            "communications.whatsapp.read",
        ],
    },
}

_BUDGET_SUPER_ROLES = {"superadmin", "super_admin"}
_BUDGET_VIEWER_EMAILS = {"azuniga@plataformasports.com"}


def _budget_can_view(current_empleado: Empleado) -> bool:
    role = str(getattr(current_empleado, "rol", "") or "").strip().lower()
    if role in _BUDGET_SUPER_ROLES:
        return True
    department = str(
        getattr(current_empleado, "departamento", "") or ""
    ).strip().lower()
    email = str(getattr(current_empleado, "correo", "") or "").strip().lower()
    return department.startswith("direcci") or email in _BUDGET_VIEWER_EMAILS


def _budget_can_mutate(current_empleado: Empleado) -> bool:
    role = str(getattr(current_empleado, "rol", "") or "").strip().lower()
    return role in _BUDGET_SUPER_ROLES


def _budget_access_map(current_empleado: Empleado) -> dict[str, bool]:
    can_view = _budget_can_view(current_empleado)
    can_mutate = _budget_can_mutate(current_empleado)
    return {
        "read": can_view,
        "create": can_mutate,
        "version_update": can_mutate,
        "line_update": can_mutate,
        "approve": can_mutate,
        "freeze": can_mutate,
        "audit_read": can_view,
        "export": can_view,
    }


def _require_budget_access(current_empleado: Empleado, capability: str) -> None:
    access = _budget_access_map(current_empleado)
    if access.get(capability):
        return
    raise HTTPException(
        status_code=403, detail="Access denied. Missing required budget permission."
    )


def _budget_executive_alerts(
    summary: dict[str, Any],
    forecast: dict[str, Any],
    scenarios: dict[str, Any],
) -> list[dict[str, str]]:
    return build_budget_executive_alerts(summary, forecast, scenarios)


def _normalize_tokens(values: list[str]) -> list[str]:
    return sorted(
        {
            str(value or "").strip().lower()
            for value in values
            if str(value or "").strip()
        }
    )


def _collect_profile_tokens(value: Any) -> set[str]:
    tokens: set[str] = set()
    if value is None:
        return tokens
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized:
            tokens.add(normalized)
        return tokens
    if isinstance(value, list):
        for item in value:
            tokens.update(_collect_profile_tokens(item))
        return tokens
    if isinstance(value, dict):
        for key, item in value.items():
            key_norm = str(key or "").strip().lower()
            if key_norm == "permissions":
                tokens.update(_collect_profile_tokens(item))
                continue
            if key_norm == "scopes":
                continue
            if key_norm and isinstance(item, bool) and item:
                tokens.add(key_norm)
                continue
            for nested in _collect_profile_tokens(item):
                if key_norm and "." not in nested and ":" not in nested:
                    tokens.add(f"{key_norm}.{nested}")
                tokens.add(nested)
        return tokens
    return tokens


def _profile_scopes(value: Any) -> list[str]:
    if isinstance(value, dict):
        scopes = value.get("scopes")
        if isinstance(scopes, list):
            return _normalize_tokens([str(item) for item in scopes])
    return []


def _build_profile_permissions_payload(
    *,
    preset_key: Optional[str],
    permission_tokens: list[str],
    scope_tokens: list[str],
    permissions_json: str,
) -> Any:
    tokens = set(_normalize_tokens(permission_tokens))
    scopes = set(_normalize_tokens(scope_tokens))
    selected_preset = _PROFILE_PRESETS.get((preset_key or "").strip().lower())
    if selected_preset:
        tokens.update(_normalize_tokens(selected_preset.get("permissions", [])))
    if tokens or scopes or selected_preset:
        return {
            "preset_key": (preset_key or "").strip().lower() or None,
            "permissions": sorted(tokens),
            "scopes": sorted(scopes),
        }
    return json.loads((permissions_json or "{}").strip() or "{}")


def _render_profile_matrix(
    *,
    form_prefix: str,
    selected_tokens: set[str],
) -> str:
    header = "".join(
        f'<th style="text-align:center;">{label}</th>'
        for _, label in _PROFILE_ACTION_LABELS
    )
    rows: list[str] = []
    for entry in _PROFILE_PERMISSION_MATRIX:
        cells = []
        prefix = str(entry["token_prefix"])
        for action_key, _action_label in _PROFILE_ACTION_LABELS:
            token = f"{prefix}.{action_key}"
            checked = (
                "checked"
                if token in selected_tokens or f"{prefix}.*" in selected_tokens
                else ""
            )
            cells.append(
                f'<td style="text-align:center;"><input type="checkbox" name="{form_prefix}permission_token" value="{escape(token)}" {checked}></td>'
            )
        rows.append(
            f"""
            <tr>
                <td>{escape(str(entry["module"]))}</td>
                <td>{escape(str(entry["capability"]))}<br><small style="color:#64748b;"><code>{escape(prefix)}</code></small></td>
                {''.join(cells)}
            </tr>
            """
        )
    return f"""
    <table>
        <thead>
            <tr>
                <th>Módulo</th>
                <th>Capacidad</th>
                {header}
            </tr>
        </thead>
        <tbody>{''.join(rows)}</tbody>
    </table>
    """


def _render_profile_scope_inputs(
    *,
    form_prefix: str,
    selected_scopes: list[str],
) -> str:
    chips = []
    selected = set(selected_scopes)
    for value, label in _PROFILE_SCOPE_OPTIONS:
        checked = "checked" if value in selected else ""
        chips.append(
            f'<label style="display:inline-flex;align-items:center;gap:6px;margin:0 8px 8px 0;font-weight:500;"><input type="checkbox" name="{form_prefix}scope_token" value="{escape(value)}" {checked}> {escape(label)}</label>'
        )
    custom_value = ", ".join(
        scope
        for scope in selected_scopes
        if scope not in {item[0] for item in _PROFILE_SCOPE_OPTIONS}
    )
    return (
        f'<div style="margin-bottom:8px;">{"".join(chips)}</div>'
        f"<label>Scopes adicionales</label>"
        f'<input type="text" name="{form_prefix}scope_custom" value="{escape(custom_value)}" placeholder="scope:entity:morelos, scope:module:documents">'
    )


def _render_preset_options(selected_key: Optional[str]) -> str:
    options = ['<option value="">Sin preset</option>']
    for preset_key, preset in _PROFILE_PRESETS.items():
        selected_attr = (
            " selected" if preset_key == (selected_key or "").strip().lower() else ""
        )
        options.append(
            f'<option value="{escape(preset_key)}"{selected_attr}>{escape(str(preset["label"]))}</option>'
        )
    return "".join(options)


def _build_effective_profile_preview(
    *,
    empleado_role: Optional[str],
    permission_payloads: list[Any],
) -> dict[str, Any]:
    role_norm = str(empleado_role or "empleado").strip().lower() or "empleado"
    tokens: set[str] = set()
    scopes: set[str] = set()
    for payload in permission_payloads:
        tokens.update(_collect_profile_tokens(payload))
        scopes.update(_profile_scopes(payload))

    def _has_prefix(*prefixes: str) -> bool:
        return any(
            any(
                token == prefix or token.startswith(f"{prefix}.") for prefix in prefixes
            )
            for token in tokens
        )

    highlights = {
        "telegram": role_norm in TELEGRAM_APPROVER_ROLES
        or _has_prefix("finance.solicitudes", "communications", "documents"),
        "operations": role_norm in {"coordinador", "admin", "superadmin", "super_admin"}
        or _has_prefix("operations"),
        "budgets": role_norm in _BUDGET_SUPER_ROLES
        or _has_prefix("budgets", "executive"),
        "employee_beneficiary": _has_prefix("finance.employee_beneficiary"),
    }
    enabled_surfaces = [
        label
        for label, is_enabled in (
            ("Telegram", highlights["telegram"]),
            ("Operaciones", highlights["operations"]),
            ("Presupuestos", highlights["budgets"]),
            ("Beneficiarios empleado", highlights["employee_beneficiary"]),
        )
        if is_enabled
    ]

    return {
        "role": role_norm,
        "token_count": len(tokens),
        "scope_count": len(scopes),
        "tokens": sorted(tokens),
        "scopes": sorted(scopes),
        "enabled_surfaces": enabled_surfaces,
        "highlights": highlights,
    }


def _render_employee_beneficiary_access_summary(
    effective_preview_map: dict[str, dict[str, Any]]
) -> str:
    """Render assigned users who can request informes/anticipos for another employee."""

    rows: list[str] = []
    for empleado_id, preview in sorted(
        effective_preview_map.items(),
        key=lambda item: (
            str(item[1].get("empleado_nombre") or "").lower(),
            str(item[0]),
        ),
    ):
        effective = preview.get("effective") or _build_effective_profile_preview(
            empleado_role=preview.get("empleado_rol"),
            permission_payloads=list(preview.get("permission_payloads") or []),
        )
        if not effective.get("highlights", {}).get("employee_beneficiary"):
            continue
        profile_names = ", ".join(preview.get("profile_names") or []) or "?"
        rows.append(
            "<tr>"
            f"<td>{escape(str(preview.get('empleado_nombre') or ''))}"
            f'<br><small style="color:#64748b;">{escape(str(preview.get("empleado_correo") or ""))}</small></td>'
            f"<td><code>{escape(str(empleado_id))}</code></td>"
            f"<td>{escape(profile_names)}</td>"
            '<td><span class="pill">finance.employee_beneficiary.request</span></td>'
            "</tr>"
        )
    if not rows:
        return (
            '<div style="color:#64748b;">Ningun perfil activo asignado otorga '
            '<code>finance.employee_beneficiary.request</code>. Si un usuario autorizado no ve el selector, '
            'asigna el preset <code>Solicitudes para empleados terceros</code>.</div>'
        )
    return (
        '<table><thead><tr><th>Empleado</th><th>ID</th><th>Perfiles</th><th>Capacidad</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table>'
    )


def _build_effective_profile_comparison(
    *,
    left_label: str,
    left_preview: dict[str, Any],
    right_label: str,
    right_preview: dict[str, Any],
) -> dict[str, Any]:
    left_tokens = set(left_preview.get("tokens") or [])
    right_tokens = set(right_preview.get("tokens") or [])
    left_scopes = set(left_preview.get("scopes") or [])
    right_scopes = set(right_preview.get("scopes") or [])
    left_surfaces = set(left_preview.get("enabled_surfaces") or [])
    right_surfaces = set(right_preview.get("enabled_surfaces") or [])

    return {
        "left_label": left_label,
        "right_label": right_label,
        "shared_tokens": sorted(left_tokens & right_tokens),
        "left_only_tokens": sorted(left_tokens - right_tokens),
        "right_only_tokens": sorted(right_tokens - left_tokens),
        "shared_scopes": sorted(left_scopes & right_scopes),
        "left_only_scopes": sorted(left_scopes - right_scopes),
        "right_only_scopes": sorted(right_scopes - left_scopes),
        "shared_surfaces": sorted(left_surfaces & right_surfaces),
        "left_only_surfaces": sorted(left_surfaces - right_surfaces),
        "right_only_surfaces": sorted(right_surfaces - left_surfaces),
    }


def _build_budget_line_drilldown(
    lines: list[dict[str, Any]],
    *,
    dimension: Optional[str],
    value: Optional[str],
) -> dict[str, Any]:
    dimension_norm = str(dimension or "").strip().lower()
    value_norm = str(value or "").strip()
    if not dimension_norm or not value_norm:
        return {
            "active": False,
            "dimension": None,
            "value": None,
            "line_count": len(lines),
            "budget_total": round(
                sum(float(item.get("budget_amount") or 0) for item in lines), 2
            ),
            "reference_total": round(
                sum(float(item.get("reference_amount") or 0) for item in lines), 2
            ),
            "variance_total": round(
                sum(float(item.get("variance_amount") or 0) for item in lines), 2
            ),
            "rows": list(lines),
        }

    field_map = {
        "concept": "concept_name",
        "provider": "provider_name",
        "phase": "phase",
        "entity": "entity_name",
        "owner": "owner_name",
        "account": "account_code_final",
    }
    field_name = field_map.get(dimension_norm)
    if not field_name:
        return {
            "active": False,
            "dimension": None,
            "value": None,
            "line_count": len(lines),
            "budget_total": round(
                sum(float(item.get("budget_amount") or 0) for item in lines), 2
            ),
            "reference_total": round(
                sum(float(item.get("reference_amount") or 0) for item in lines), 2
            ),
            "variance_total": round(
                sum(float(item.get("variance_amount") or 0) for item in lines), 2
            ),
            "rows": list(lines),
        }

    target_norm = value_norm.lower()
    filtered_rows = [
        item
        for item in lines
        if str(item.get(field_name) or "").strip().lower() == target_norm
    ]
    return {
        "active": True,
        "dimension": dimension_norm,
        "value": value_norm,
        "line_count": len(filtered_rows),
        "budget_total": round(
            sum(float(item.get("budget_amount") or 0) for item in filtered_rows), 2
        ),
        "reference_total": round(
            sum(float(item.get("reference_amount") or 0) for item in filtered_rows), 2
        ),
        "variance_total": round(
            sum(float(item.get("variance_amount") or 0) for item in filtered_rows), 2
        ),
        "rows": filtered_rows,
    }


async def _audit_access_profile_event(
    session: AsyncSession,
    *,
    event_type: str,
    actor_empleado_id: Optional[str],
    profile_id: Optional[str] = None,
    empleado_id: Optional[str] = None,
    payload: Optional[dict[str, Any]] = None,
) -> None:
    await session.execute(
        text(
            """
            INSERT INTO access_profile_audit_log (
                id, event_type, actor_empleado_id, profile_id, empleado_id, payload, created_at
            ) VALUES (
                :id, :event_type, :actor_empleado_id, :profile_id, :empleado_id, CAST(:payload AS jsonb), NOW()
            )
            """
        ),
        {
            "id": str(uuid4()),
            "event_type": event_type,
            "actor_empleado_id": actor_empleado_id,
            "profile_id": profile_id,
            "empleado_id": empleado_id,
            "payload": json.dumps(payload or {}, ensure_ascii=False),
        },
    )


router = APIRouter()
require_tournament_admin = require_permission_factory(
    ["admin.torneos.manage"],
    allowed_roles=["admin", "superadmin", "super_admin"],
)
_HEALTH_HISTORY_LOCK = Lock()
_HEALTH_HISTORY: deque[dict[str, Any]] = deque(maxlen=100)

# Shared back link for configuración / catálogo admin pages (panel entry at /panel).
_CONFIG_PANEL_BACK_LINK_HTML = """
            <div class="config-panel-back" style="margin-bottom:16px;">
                <a href="/panel" style="color:#667eea;text-decoration:none;font-weight:600;">⬅️ Volver a Panel</a>
            </div>
"""


def _render_admin_nav_link(
    href: str,
    label: str,
    key: str,
    active_area: Optional[str],
) -> str:
    active = key == active_area
    return f"""
        <a
            href="{href}"
            style="
                text-decoration:none;
                padding:10px 14px;
                border-radius:14px;
                font-size:13px;
                font-weight:700;
                letter-spacing:.01em;
                border:1px solid {'rgba(16,185,129,.34)' if active else '#dbe2ea'};
                background:{'#0f766e' if active else '#ffffff'};
                color:{'#f8fafc' if active else '#334155'};
                box-shadow:{'0 10px 24px rgba(15,118,110,.18)' if active else 'none'};
            "
        >{escape(label)}</a>
    """


def _render_admin_nav_group(
    group_label: str,
    items: list[tuple[str, str, str]],
    active_area: Optional[str],
) -> str:
    if not items:
        return ""
    links = "".join(
        _render_admin_nav_link(href, label, key, active_area)
        for href, label, key in items
    )
    return f"""
        <div style="margin-bottom:12px;">
            <div style="font-size:10px;text-transform:uppercase;letter-spacing:.14em;color:#64748b;margin-bottom:8px;font-weight:800;">
                {escape(group_label)}
            </div>
            <div style="display:flex;gap:8px;flex-wrap:wrap;">{links}</div>
        </div>
    """


def render_admin_navigation(
    current_empleado: Empleado,
    active_area: Optional[str] = None,
    *,
    subtitle: str = "Administración financiera y operativa",
) -> str:
    role_norm = (getattr(current_empleado, "rol", "") or "").strip().lower()
    visible_tool_keys = set(getattr(current_empleado, "visible_tool_keys", set()) or set())
    is_superadmin = role_norm in {"superadmin", "super_admin"}

    def can_nav(tool_key: str) -> bool:
        if is_superadmin:
            return True
        if visible_tool_keys:
            return tool_key in visible_tool_keys
        return True

    inicio_items = [
        ("admin.gastos.dashboard", "/admin/gastos", "Resumen", "dashboard"),
        ("executive.reports.read", "/admin/ejecutivo", "Ejecutivo", "ejecutivo"),
        ("admin.finanzas", "/admin/sam-inbox", "Sam Inbox", "sam_inbox"),
    ]
    finanzas_items = [
        ("admin.finanzas", "/admin/finanzas", "Finanzas", "finanzas"),
        (
            "admin.finanzas",
            "/admin/finanzas/cashflow",
            "Cashflow",
            "cashflow",
        ),
        (
            "admin.finanzas",
            "/admin/finanzas/cuentas-por-cobrar",
            "Cuentas por Cobrar",
            "ar_cxc",
        ),
        ("admin.contabilidad", "/admin/contabilidad/deudores", "Deudores", "deudores"),
        ("admin.finanzas", "/admin/artifacts", "Artifacts", "artifacts"),
        ("admin.gastos.cfdi_matching", "/admin/gastos/cfdis/matching", "Matching CFDI", "matching"),
        ("admin.gastos.sat", "/admin/gastos/sat", "e.firma SAT", "sat"),
        ("admin.gastos.limpieza", "/admin/gastos/sin-cuenta-contable", "Limpieza contable", "limpieza"),
    ]
    payment_run_item = None
    if can_access_payment_run(current_empleado):
        payment_run_item = (
            "/admin/finanzas/payment-run",
            "Payment Run",
            "payment_run",
        )
    if can_manage_payment_run(current_empleado):
        finanzas_items.extend(
            [
                (
                    "admin.finanzas",
                    "/admin/finanzas/payment-history",
                    "Historial de pagos",
                    "payment_history",
                ),
            ]
        )
    catalogos_items = [
        ("admin.empleados", "/admin/empleados", "Empleados", "empleados"),
        ("admin.perfiles", "/admin/perfiles", "Perfiles", "perfiles"),
        ("admin.rfc", "/admin/rfc", "RFC", "rfc"),
        ("admin.cuentas_contables", "/admin/cuentas-contables", "Cuentas", "cuentas"),
        ("admin.centros_costo", "/admin/centros-costo", "Centros", "centros"),
        ("admin.proveedores", "/admin/proveedores-clientes", "Proveedores", "proveedores"),
        ("admin.torneos", "/admin/torneos", "Torneos y proyectos", "torneos"),
    ]
    if not is_catalog_admin_user(current_empleado):
        catalogos_items = []
    avanzado_items = [
        ("admin.torneos", "/admin/sports", "Sports", "sports"),
        ("admin.torneos", "/admin/torneos/domain-alignment", "Alineación", "alineacion"),
        ("presupuestos.ingresos", "/admin/presupuestos", "Presupuestos", "presupuestos"),
    ]
    if not _budget_can_view(current_empleado):
        avanzado_items = [
            item for item in avanzado_items if item[3] != "presupuestos"
        ]
    elif role_norm not in {"admin", "superadmin", "super_admin"}:
        avanzado_items = [
            item for item in avanzado_items if item[3] == "presupuestos"
        ]
    if avanzado_items and (
        role_norm in {"admin", "superadmin", "super_admin"}
        or _budget_can_view(current_empleado)
    ):
        avanzado_items.append(
            ("admin.customer_success", "/admin/customer-success/uso", "Customer Success", "customer_success")
        )
    if role_norm in {"superadmin", "super_admin"}:
        avanzado_items.append(
            ("admin.customer_success", "/admin/customer-success/bitacora", "Bitácora", "customer_success_audit")
        )
    inicio_items = [(href, label, key) for tool_key, href, label, key in inicio_items if can_nav(tool_key)]
    finanzas_items = [(href, label, key) for tool_key, href, label, key in finanzas_items if can_nav(tool_key)]
    if payment_run_item is not None:
        finanzas_items.append(payment_run_item)
    catalogos_items = [(href, label, key) for tool_key, href, label, key in catalogos_items if can_nav(tool_key)]
    avanzado_items = [(href, label, key) for tool_key, href, label, key in avanzado_items if can_nav(tool_key)]
    avanzado_keys = {key for _, _, key in avanzado_items}
    avanzado_open = active_area in avanzado_keys
    impersonator_id = getattr(current_empleado, "impersonator_empleado_id", None)
    identity_link_html = ""
    if is_superadmin or impersonator_id:
        identity_link_html = """
                <a
                    href="/admin/identidad"
                    style="text-decoration:none;padding:10px 14px;border-radius:14px;border:1px solid #f59e0b;background:#fffbeb;color:#78350f;font-size:13px;font-weight:700;"
                >Cambiar identidad</a>
        """
    impersonation_html = ""
    if impersonator_id:
        impersonator_name = escape(
            str(getattr(current_empleado, "impersonator_nombre", "") or "superadmin")
        )
        impersonation_html = f"""
        <div style="margin:0 0 12px;padding:10px 12px;border-radius:14px;background:#7f1d1d;color:#fee2e2;border:1px solid rgba(254,202,202,.24);font-size:12px;font-weight:700;">
            Viendo como {escape(current_empleado.nombre or '')}. Superadmin real: {impersonator_name}.
            <form method="POST" action="/admin/identidad/restaurar" style="display:inline;margin-left:10px;">
                <input type="hidden" name="next" value="/panel">
                <button type="submit" style="border:0;border-radius:999px;padding:6px 10px;background:#fee2e2;color:#7f1d1d;font-weight:800;cursor:pointer;">Volver</button>
            </form>
        </div>
        """
    grouped_nav_html = "".join(
        [
            _render_admin_nav_group("Inicio", inicio_items, active_area),
            _render_admin_nav_group("Finanzas", finanzas_items, active_area),
            _render_admin_nav_group("Catálogos", catalogos_items, active_area),
        ]
    )
    if role_norm in {"admin", "superadmin", "super_admin"}:
        avanzado_links = "".join(
            _render_admin_nav_link(href, label, key, active_area)
            for href, label, key in avanzado_items
        )
        grouped_nav_html += f"""
            <details
                style="margin-top:4px;border:1px solid #e2e8f0;border-radius:16px;background:#fff;padding:10px 12px;"
                {'open' if avanzado_open else ''}
            >
                <summary
                    style="
                        cursor:pointer;
                        list-style:none;
                        font-size:10px;
                        text-transform:uppercase;
                        letter-spacing:.14em;
                        color:#64748b;
                        font-weight:800;
                    "
                >Más herramientas</summary>
                <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:10px;">{avanzado_links}</div>
            </details>
        """
    tracker_script = render_customer_success_usage_tracker_script()
    return f"""
    <section
        style="
            margin:0 0 22px 0;
            padding:18px 20px;
            border-radius:20px;
            background:
                radial-gradient(circle at top right, rgba(16,185,129,.12), transparent 28%),
                linear-gradient(180deg,#ffffff 0%,#f8fafc 100%);
            border:1px solid #dbe2ea;
            box-shadow:0 18px 45px rgba(15,23,42,.08);
        "
    >
        {impersonation_html}
        <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:16px;flex-wrap:wrap;margin-bottom:14px;">
            <div>
                <div style="font-size:11px;text-transform:uppercase;letter-spacing:.12em;color:#64748b;margin-bottom:6px;">Consola</div>
                <div style="font-size:22px;font-weight:800;letter-spacing:-.03em;color:#0f172a;">Administración</div>
                <div style="margin-top:4px;color:#475569;font-size:13px;line-height:1.5;">{escape(subtitle)}</div>
            </div>
            <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;">
                <div style="padding:10px 12px;border-radius:14px;background:#0f172a;color:#f8fafc;">
                    <div style="font-size:13px;font-weight:700;">{escape(current_empleado.nombre or '')}</div>
                    <div style="font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:#bfdbfe;">{escape(current_empleado.rol or 'empleado')}</div>
                </div>
                <a
                    href="/panel"
                    style="text-decoration:none;padding:10px 14px;border-radius:14px;border:1px solid #0f766e;background:#ecfdf5;color:#0f172a;font-size:13px;font-weight:700;"
                >Volver a Panel</a>
                {identity_link_html}
                <a
                    href="/logout"
                    style="text-decoration:none;padding:10px 14px;border-radius:14px;border:1px solid #fecaca;background:#7f1d1d;color:#fff;font-size:13px;font-weight:700;"
                >Salir</a>
            </div>
        </div>
        <div>{grouped_nav_html}</div>
    </section>
    {tracker_script}
    """


def _admin_workspace_styles(
    max_width: str = "1240px", *, layout: str = "reading"
) -> str:
    """Return a readable or full-width admin workspace shell."""
    if layout not in {"reading", "data"}:
        raise ValueError("layout must be 'reading' or 'data'")
    data_layout = layout == "data"
    container_max_width = "none" if data_layout else max_width
    container_margin = "0" if data_layout else "0 auto"
    table_shell_style = (
        "overflow-x:auto;overflow-y:visible;-webkit-overflow-scrolling:touch;"
        if data_layout
        else "overflow:auto;max-block-size:min(68vh, 46rem);-webkit-overflow-scrolling:touch;scrollbar-gutter:stable both-edges;"
    )
    table_shell_table_style = "width:100%;" if data_layout else "min-width:max-content;"
    return f"""
        :root {{
            --shell-bg:#edf3f8;
            --shell-ink:#0f172a;
            --shell-muted:#475569;
            --shell-line:#dbe2ea;
            --shell-card:#ffffff;
            --shell-accent:#0f766e;
        }}
        * {{ box-sizing:border-box; }}
        html {{ max-width:100%; overflow-x:hidden; }}
        body {{
            font-family:"Segoe UI","Helvetica Neue",sans-serif;
            margin:0;
            padding:26px 16px;
            color:var(--shell-ink);
            background:
                radial-gradient(circle at top left, rgba(15,118,110,.10), transparent 26%),
                radial-gradient(circle at top right, rgba(29,78,216,.08), transparent 22%),
                linear-gradient(180deg, #eaf1f6 0%, #dfe9f1 100%);
            min-height:100dvh;
            max-width:100%;
            overflow-x:hidden;
        }}
        body .container,
        body .workspace-shell {{
            max-width:{container_max_width}{' !important' if data_layout else ''};
            width:100%;
            margin:{container_margin}{' !important' if data_layout else ''};
        }}
        .workspace-card {{
            background:#ffffff;
            border:1px solid var(--shell-line);
            border-radius:22px;
            padding:20px;
            box-shadow:0 12px 30px rgba(15,23,42,.06);
            color:var(--shell-ink);
        }}
        .workspace-section-title {{
            font-size:1.1rem;
            font-weight:800;
            letter-spacing:-.02em;
            color:var(--shell-ink);
        }}
        .workspace-section-subtitle {{
            margin-top:6px;
            color:var(--shell-muted);
            font-size:13px;
            line-height:1.55;
        }}
        .workspace-hero {{
            display:grid;
            grid-template-columns:minmax(0,1.2fr) minmax(280px,.8fr);
            gap:16px;
            margin-bottom:18px;
        }}
        .workspace-hero-main,
        .workspace-hero-side,
        .surface {{
            min-width:0;
            border:1px solid var(--shell-line);
            border-radius:22px;
            background:linear-gradient(180deg,#ffffff 0%,#f8fafc 100%);
            box-shadow:0 12px 30px rgba(15,23,42,.06);
        }}
        .workspace-hero-main {{ padding:22px; }}
        .workspace-hero-side, .surface {{ padding:18px; }}
        .eyebrow {{
            font-size:11px;
            text-transform:uppercase;
            letter-spacing:.14em;
            color:#64748b;
            margin-bottom:8px;
        }}
        h1 {{ margin:0; font-size:2.05rem; line-height:1; letter-spacing:-.04em; }}
        .lead {{ margin:10px 0 0; color:var(--shell-muted); font-size:14px; line-height:1.65; max-width:72ch; }}
        .meta-grid {{
            display:grid;
            grid-template-columns:repeat(auto-fit,minmax(min(180px,100%),1fr));
            gap:12px;
        }}
        .meta-card {{
            min-width:0;
            border:1px solid var(--shell-line);
            border-radius:18px;
            padding:16px;
            background:#fff;
        }}
        .meta-card span {{
            display:block;
            margin-bottom:6px;
            color:#64748b;
            font-size:11px;
            text-transform:uppercase;
            letter-spacing:.1em;
        }}
        .meta-card strong {{
            display:block;
            font-size:1.45rem;
            letter-spacing:-.03em;
            color:var(--shell-ink);
            overflow-wrap:anywhere;
        }}
        .meta-card small {{
            display:block;
            margin-top:6px;
            color:var(--shell-muted);
            line-height:1.45;
        }}
        .stack {{ display:grid; gap:16px; }}
        .section-head {{
            display:flex;
            justify-content:space-between;
            align-items:flex-end;
            gap:12px;
            flex-wrap:wrap;
            margin-bottom:14px;
        }}
        .section-head h2 {{ margin:0; font-size:1.1rem; letter-spacing:-.02em; }}
        .section-note {{ color:var(--shell-muted); font-size:13px; line-height:1.55; }}
        .action-grid {{
            display:grid;
            grid-template-columns:repeat(auto-fit,minmax(min(220px,100%),1fr));
            gap:14px;
        }}
        .action-card {{
            min-width:0;
            display:block;
            text-decoration:none;
            padding:16px;
            border-radius:18px;
            border:1px solid var(--shell-line);
            background:#fff;
            color:var(--shell-ink);
            box-shadow:0 10px 24px rgba(15,23,42,.04);
        }}
        .action-card strong {{ display:block; margin-bottom:6px; font-size:15px; }}
        .action-card p {{ margin:0; color:var(--shell-muted); font-size:13px; line-height:1.55; }}
        .hero-actions {{ display:flex; gap:10px; flex-wrap:wrap; margin-top:16px; }}
        .admin-breadcrumbs {{
            display:flex;
            gap:8px;
            align-items:center;
            flex-wrap:wrap;
            margin:0 0 14px 0;
            color:#64748b;
            font-size:13px;
            font-weight:800;
        }}
        .admin-breadcrumbs a {{
            color:#0f766e;
            text-decoration:none;
        }}
        .admin-breadcrumbs .sep {{
            color:#94a3b8;
        }}
        .button {{
            text-decoration:none;
            border:none;
            cursor:pointer;
            border-radius:14px;
            padding:10px 14px;
            font-size:13px;
            font-weight:700;
            display:inline-flex;
            align-items:center;
            justify-content:center;
            background:var(--shell-accent);
            color:#f8fafc;
            box-shadow:0 12px 26px rgba(15,118,110,.18);
            white-space:nowrap;
            min-inline-size:max-content;
        }}
        .button.secondary {{
            background:#fff;
            color:#334155;
            border:1px solid var(--shell-line);
            box-shadow:none;
        }}
        .table-shell {{
            max-width:100%;
            {table_shell_style}
            border:1px solid var(--shell-line);
            border-radius:18px;
            background:#fff;
        }}
        .table-shell table {{
            {table_shell_table_style}
        }}
        .table-shell thead th {{
            position:sticky;
            top:0;
            z-index:2;
            background:#0f172a;
            color:#f8fafc;
        }}
        .table-actions {{
            display:flex;
            flex-wrap:wrap;
            align-items:center;
            gap:8px;
            min-inline-size:max-content;
        }}
        .table-actions > form {{ margin:0; }}
        .table-actions .button {{ min-block-size:44px; }}
        .table-status,
        .table-actions-cell,
        .table-value-nowrap {{
            white-space:nowrap;
            overflow-wrap:normal;
            min-inline-size:max-content;
        }}
        .table-actions-cell a,
        .table-actions-cell button,
        .table-status .badge,
        .table-status .status-chip {{ white-space:nowrap; }}
        table {{
            max-width:100%;
            border-collapse:collapse;
        }}
        th, td {{
            overflow-wrap:anywhere;
        }}
        input,
        select,
        textarea {{
            max-width:100%;
        }}
        .form-grid {{
            display:grid;
            gap:12px;
        }}
        @media (max-width: 980px) {{
            .workspace-hero {{ grid-template-columns:1fr; }}
        }}
        @media (max-width: 720px) {{
            body {{ padding:12px; }}
            .container,
            .workspace-shell {{ width:100%; }}
            .workspace-card,
            .workspace-hero-main,
            .workspace-hero-side,
            .surface {{
                border-radius:16px;
                padding:14px;
            }}
            h1 {{ font-size:1.55rem; line-height:1.08; }}
            .lead {{ font-size:13px; line-height:1.55; }}
            .button {{
                width:100%;
                min-inline-size:0;
                min-height:42px;
                white-space:normal;
                text-align:center;
            }}
            .table-actions-cell .button {{
                width:auto;
                min-inline-size:max-content;
                white-space:nowrap;
            }}
            .form-grid,
            .filter-grid,
            .review-toolbar,
            .toolbar-actions {{
                grid-template-columns:1fr !important;
                width:100%;
            }}
            .workspace-card form[style*="grid-template-columns"],
            .workspace-card div[style*="grid-template-columns"],
            .surface form[style*="grid-template-columns"],
            .surface div[style*="grid-template-columns"] {{
                grid-template-columns:1fr !important;
            }}
            .filter-actions {{
                flex-direction:column;
                width:100%;
            }}
            .filter-actions button,
            .toolbar-actions > * {{
                width:100%;
            }}
            .hero-actions,
            .inline-actions,
            .section-head {{ align-items:stretch; }}
        }}
    """


def _admin_money(value: Any) -> str:
    try:
        return f"${float(value or 0):,.2f}"
    except (TypeError, ValueError):
        return "$0.00"


def _admin_text_key(value: Any) -> str:
    text_value = str(value or "").strip().lower()
    normalized = unicodedata.normalize("NFKD", text_value)
    ascii_text = "".join(char for char in normalized if not unicodedata.combining(char))
    return " ".join(ascii_text.split())


def _can_operate_ar_cxc(current_empleado: Empleado) -> bool:
    role = _admin_text_key(getattr(current_empleado, "rol", ""))
    department = _admin_text_key(getattr(current_empleado, "departamento", ""))
    name = _admin_text_key(getattr(current_empleado, "nombre", ""))
    if role in {"superadmin", "super_admin", "contabilidad"}:
        return True
    if department == "contabilidad":
        return True
    return "juan pablo" in name or "luis angel" in name


def _admin_breadcrumb_html(items: list[tuple[str, Optional[str]]]) -> str:
    parts: list[str] = []
    for label, href in items:
        clean_label = escape(str(label or ""))
        if href:
            parts.append(f'<a href="{escape(href)}">{clean_label}</a>')
        else:
            parts.append(f"<span>{clean_label}</span>")
    return (
        '<nav class="admin-breadcrumbs" aria-label="Breadcrumbs">'
        + '<span class="sep">/</span>'.join(parts)
        + "</nav>"
    )


def _admin_query_url(path: str, params: Mapping[str, Any]) -> str:
    clean_params = {
        key: value
        for key, value in params.items()
        if value not in (None, "", [])
    }
    query = urlencode(clean_params)
    return f"{path}?{query}" if query else path


def _safe_admin_cxc_return_url(return_to: Optional[str]) -> str:
    clean = str(return_to or "").strip()
    if clean.startswith("/admin/finanzas/cuentas-por-cobrar"):
        return clean
    return "/admin/finanzas/cuentas-por-cobrar"


def _executive_alert_severity(value: Any) -> str:
    severity = str(value or "").strip().lower()
    if severity in {"critical", "high", "alta"}:
        return "high"
    if severity in {"warning", "medium", "media"}:
        return "medium"
    return "low"


def _executive_alert_priority_key(item: dict[str, Any]) -> tuple[int, str, str]:
    rank = {"high": 0, "medium": 1, "low": 2}
    return (
        rank.get(str(item.get("severity") or "low"), 9),
        str(item.get("module") or "").lower(),
        str(item.get("title") or "").lower(),
    )


def _executive_alert_card(
    *,
    severity: Any,
    module: Any,
    title: Any,
    detail: Any,
    owner: Any = "Dirección",
    href: Any = "/admin/ejecutivo/alertas",
    source: Any = "SamChat",
) -> dict[str, Any]:
    clean_href = str(href or "").strip()
    if not (
        clean_href.startswith("/admin/")
        or clean_href.startswith("/assistant")
    ):
        clean_href = "/admin/ejecutivo/alertas"
    return {
        "severity": _executive_alert_severity(severity),
        "module": str(module or "Dirección").strip() or "Dirección",
        "title": str(title or "Alerta ejecutiva").strip() or "Alerta ejecutiva",
        "detail": str(detail or "Revisar señal ejecutiva.").strip()
        or "Revisar señal ejecutiva.",
        "owner": str(owner or "Dirección").strip() or "Dirección",
        "href": clean_href,
        "source": str(source or "SamChat").strip() or "SamChat",
    }


def _build_consolidated_executive_alerts(
    platform: dict[str, Any],
    inbox_payload: dict[str, Any],
) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    for action in list((platform.get("action_queue") or {}).get("actions") or []):
        alerts.append(
            _executive_alert_card(
                severity=action.get("severity"),
                module=action.get("module") or "Finanzas",
                title=action.get("title"),
                detail=action.get("detail"),
                owner=action.get("owner") or "Finanzas",
                href=action.get("href") or "/admin/finanzas",
                source="Finance Action Queue",
            )
        )

    payment_run = platform.get("payment_run") or {}
    if int(payment_run.get("payable_count") or 0) > 0:
        alerts.append(
            _executive_alert_card(
                severity="high",
                module="Pagos",
                title="Pagos aprobados pendientes",
                detail=(
                    f"{int(payment_run.get('payable_count') or 0)} documento…20110 tokens truncated…      f"Sam Inbox Dirección no disponible: {type(exc).__name__}."
        )
        inbox_payload = {"direction": {"executive_alerts": {"alerts": []}}}

    alerts = _build_consolidated_executive_alerts(platform, inbox_payload)
    action_queue = platform.get("action_queue") or {}
    payment_run = platform.get("payment_run") or {}
    tax_readiness = platform.get("tax_readiness") or {}
    accounting_close = platform.get("accounting_close_center") or {}
    severity_counts = {
        "high": sum(1 for item in alerts if item.get("severity") == "high"),
        "medium": sum(1 for item in alerts if item.get("severity") == "medium"),
        "low": sum(1 for item in alerts if item.get("severity") == "low"),
    }

    errors_html = "".join(
        f"""
        <div style="margin-bottom:12px;padding:12px 14px;border:1px solid #fcd34d;border-radius:14px;background:#fffbeb;color:#92400e;">
            {escape(message)}
        </div>
        """
        for message in source_errors
    )
    alert_rows = "".join(
        f"""
        <tr>
            <td><span class="finance-pill finance-{escape(str(item.get("severity") or "low"))}">{escape(str(item.get("severity") or "-"))}</span></td>
            <td>{escape(str(item.get("module") or "-"))}</td>
            <td>{escape(str(item.get("title") or "-"))}</td>
            <td>{escape(str(item.get("detail") or "-"))}</td>
            <td>{escape(str(item.get("owner") or "-"))}</td>
            <td>{escape(str(item.get("source") or "-"))}</td>
            <td><a class="button secondary compact" href="{escape(str(item.get("href") or "/admin/ejecutivo/alertas"))}">Abrir</a></td>
        </tr>
        """
        for item in alerts[:80]
    ) or '<tr><td colspan="7">Sin alertas ejecutivas activas con las fuentes disponibles.</td></tr>'
    quick_actions_html = "".join(
        f"""
        <a class="action-card" href="{escape(str(item.get("href") or "/admin/ejecutivo/alertas"))}">
            <strong>{escape(str(item.get("title") or "Alerta"))}</strong>
            <p>{escape(str(item.get("module") or "Dirección"))} · {escape(str(item.get("owner") or "Dirección"))}</p>
        </a>
        """
        for item in alerts[:6]
    ) or '<div class="section-note">Sin acciones prioritarias para este corte.</div>'
    period_form_html = (
        '<form method="GET" action="/admin/ejecutivo/alertas" '
        'style="display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;align-items:end;">'
        f'<div><label>Año</label><input name="year" type="number" min="2020" max="2100" value="{current_year}"></div>'
        f'<div><label>Mes</label><input name="month" type="number" min="1" max="12" value="{current_month}"></div>'
        '<button class="button" type="submit">Actualizar alertas</button>'
        "</form>"
    )

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>Alertas ejecutivas - SamChat</title>
        <style>
            {_admin_workspace_styles("1380px")}
            .finance-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:14px; }}
            .finance-table {{ width:100%; border-collapse:separate; border-spacing:0; }}
            .finance-table th, .finance-table td {{ text-align:left; padding:12px; border-bottom:1px solid #e2e8f0; vertical-align:top; }}
            .finance-table th {{ color:#64748b; font-size:11px; text-transform:uppercase; letter-spacing:.11em; background:#f8fafc; }}
            .finance-pill {{ display:inline-flex; padding:5px 9px; border-radius:999px; font-size:11px; font-weight:900; text-transform:uppercase; }}
            .finance-high {{ background:#fee2e2; color:#991b1b; }}
            .finance-medium {{ background:#fef3c7; color:#92400e; }}
            .finance-low {{ background:#dcfce7; color:#166534; }}
        </style>
    </head>
    <body>
        <div class="workspace-shell">
            {render_admin_navigation(current_empleado, "ejecutivo", subtitle="Alertas consolidadas para dirección y finanzas.")}
            {_admin_breadcrumb_html([("Centro Ejecutivo", "/admin/ejecutivo"), ("Alertas ejecutivas", None)])}
            {_render_admin_workspace_hero(
                eyebrow="Dirección",
                title="Alertas ejecutivas consolidadas",
                description="Una sola vista read-only para riesgos de pagos, COI, DIOT/CFDI, pólizas y señales directivas desde snapshots existentes.",
                actions_html=period_form_html,
                side_html=(
                    '<div class="eyebrow">Periodo</div>'
                    f'<div style="font-size:1.3rem;font-weight:900;color:#0f172a;">{current_month}/{current_year}</div>'
                    '<div style="margin-top:8px;color:#64748b;">Sin mutaciones ni tablas nuevas.</div>'
                ),
            )}
            {errors_html}
            <section class="workspace-card" style="margin-bottom:18px;">
                <div class="workspace-section-title">Semáforo ejecutivo</div>
                <div class="workspace-section-subtitle">Resumen priorizado desde Finance Action Queue, Payment Run, Tax Readiness, Accounting Close y Sam Inbox Dirección.</div>
                <div class="finance-grid" style="margin-top:14px;">
                    {_sports_card("Alertas totales", len(alerts), "Consolidadas y deduplicadas")}
                    {_sports_card("Alta prioridad", severity_counts["high"], "Atención inmediata")}
                    {_sports_card("Media prioridad", severity_counts["medium"], "Seguimiento operativo")}
                    {_sports_card("Pagos pendientes", payment_run.get("payable_count", 0), f"${_safe_money(payment_run.get('payable_total'))}")}
                    {_sports_card("DIOT/CFDI bloqueado", tax_readiness.get("diot_blockers_count", 0), str(tax_readiness.get("status") or "sin fuente"))}
                    {_sports_card("Pólizas descuadradas", accounting_close.get("unbalanced_count", 0), "Debe/haber")}
                </div>
            </section>
            <section class="workspace-card" style="margin-bottom:18px;">
                <div class="workspace-section-title">Acciones prioritarias</div>
                <div class="workspace-section-subtitle">Atajos a los módulos canónicos. Esta pantalla no ejecuta acciones.</div>
                <div class="action-grid" style="margin-top:14px;">{quick_actions_html}</div>
            </section>
            <section class="workspace-card">
                <div class="workspace-section-title">Lista priorizada</div>
                <div class="workspace-section-subtitle">Ordenada por severidad, módulo y título para revisión ejecutiva.</div>
                <div class="table-shell" style="margin-top:14px;">
                    <table class="finance-table">
                        <thead><tr><th>Sev</th><th>Módulo</th><th>Alerta</th><th>Detalle</th><th>Responsable</th><th>Fuente</th><th>Acción</th></tr></thead>
                        <tbody>{alert_rows}</tbody>
                    </table>
                </div>
            </section>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html)


@router.get("/admin/gastos/finance-training", response_class=HTMLResponse)
async def finance_training_page(
    request: Request,
    current_empleado: Empleado = require_admin_finanzas(),
):
    """Admin UI: one-click finance training dataset generate / cleanup."""
    success_msg = request.query_params.get("success_msg", "")
    error_msg = request.query_params.get("error_msg", "")
    batch_key = request.query_params.get("batch_key", "").strip()

    manifest_tip = ""
    csv_link = ""
    login_tip = (
        "Empleados sintéticos: correos <code>fintrain.&lt;batch&gt;.0x@finance-training.sam.chat</code> "
        "— contraseña por defecto <code>FinTrain2026!</code> o variable <code>FINANCE_TRAINING_DEFAULT_PASSWORD</code>."
    )
    if batch_key:
        mp = manifest_path(_repo_root(), batch_key)
        if mp.exists():
            try:
                raw = json.loads(mp.read_text(encoding="utf-8"))
                manifest_tip = (
                    f"Lote <strong>{escape(batch_key)}</strong>: "
                    f"{len(raw.get('expense_ids') or [])} gastos, "
                    f"{len(raw.get('cfdi_uuids') or [])} UUID CFDI en CSV."
                )
                csv_link = (
                    f'<a class="btn btn-primary" href="/admin/gastos/finance-training/cfdi-csv?batch_key={quote(batch_key)}">'
                    f"Descargar CSV CFDI</a> "
                    f'<a class="btn btn-secondary" href="/admin/gastos/cfdis/carga-masiva">Ir a carga masiva CFDI</a>'
                )
            except Exception:
                manifest_tip = "Manifiesto presente pero no legible."

    alerts = ""
    if success_msg:
        alerts += f'<div class="alert alert-success">{escape(success_msg)}</div>'
    if error_msg:
        alerts += f'<div class="alert alert-error">{escape(error_msg)}</div>'

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <title>Dataset capacitación finanzas</title>
        <style>
            body {{ font-family: system-ui, sans-serif; max-width: 920px; margin: 24px auto; padding: 0 16px; }}
            .alert-success {{ background: #d4edda; color: #155724; padding: 12px 16px; border-radius: 8px; margin-bottom: 12px; }}
            .alert-error {{ background: #f8d7da; color: #721c24; padding: 12px 16px; border-radius: 8px; margin-bottom: 12px; }}
            .card {{ border: 1px solid #e5e7eb; border-radius: 10px; padding: 20px; margin-bottom: 20px; }}
            label {{ display: block; font-weight: 600; margin-top: 12px; }}
            input, select {{ width: 100%; max-width: 480px; padding: 8px; margin-top: 4px; }}
            .btn {{ display: inline-block; padding: 10px 18px; border-radius: 8px; text-decoration: none; margin-right: 8px; margin-top: 12px; font-weight: 600; border: none; cursor: pointer; }}
            .btn-primary {{ background: #4f46e5; color: white; }}
            .btn-secondary {{ background: #6b7280; color: white; }}
            .btn-danger {{ background: #b91c1c; color: white; }}
            code {{ background: #f3f4f6; padding: 2px 6px; border-radius: 4px; }}
        </style>
    </head>
    <body>
        {render_admin_navigation(current_empleado, "dashboard", subtitle="Dataset aislado para cursos de finanzas y matching CFDI.")}
        <h1>Dataset de capacitación (finanzas)</h1>
        <p>Crea ~50 gastos coherentes (empleado + terceros), documentos, anticipo/reembolso de demo y un CSV listo para
        <a href="/admin/gastos/cfdis/carga-masiva">Carga masiva de CFDIs</a>. La limpieza usa solo el manifiesto del lote.</p>
        {alerts}
        <div class="card">
            <p>{login_tip}</p>
            {f"<p>{manifest_tip}</p>{csv_link}" if batch_key else ""}
        </div>
        <div class="card">
            <h2>Generar lote</h2>
            <form method="POST" action="/admin/gastos/finance-training/generate">
                <label for="batch_key_gen">Batch key (opcional, alfanumérico 4-64)</label>
                <input id="batch_key_gen" name="batch_key" type="text" placeholder="vacío = auto">
                <label for="seed_gen">Semilla PRNG</label>
                <input id="seed_gen" name="seed" type="number" value="42">
                <label for="modo_gen">Modo</label>
                <select id="modo_gen" name="modo">
                    <option value="apply">Aplicar (escribe BD)</option>
                    <option value="dry_run">Solo simular (sin BD)</option>
                </select>
                <label><input type="checkbox" name="force" value="1"> Forzar si ya existe manifiesto (sobrescribe tras error previo)</label>
                <div><button type="submit" class="btn btn-primary">Generar dataset</button></div>
            </form>
        </div>
        <div class="card">
            <h2>Reset (eliminar lote si existe y generar de nuevo)</h2>
            <form method="POST" action="/admin/gastos/finance-training/reset" onsubmit="return confirm('¿Borrar lote existente y regenerar?');">
                <label for="batch_key_reset">Batch key (mismo que el manifiesto a reemplazar; vacío = nuevo auto)</label>
                <input id="batch_key_reset" name="batch_key" type="text" placeholder="vacío = auto nuevo">
                <label for="seed_reset">Semilla</label>
                <input id="seed_reset" name="seed" type="number" value="42">
                <div><button type="submit" class="btn btn-primary">Reset + generar</button></div>
            </form>
        </div>
        <div class="card">
            <h2>Eliminar lote</h2>
            <form method="POST" action="/admin/gastos/finance-training/cleanup" onsubmit="return confirm('¿Eliminar todos los datos del manifiesto?');">
                <label for="batch_key_del">Batch key (requerido)</label>
                <input id="batch_key_del" name="batch_key" type="text" required placeholder="ej. abc123">
                <label for="modo_del">Modo</label>
                <select id="modo_del" name="modo">
                    <option value="apply">Aplicar borrado</option>
                    <option value="dry_run">Solo contar qué se borraría</option>
                </select>
                <div><button type="submit" class="btn btn-danger">Eliminar dataset</button></div>
            </form>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html)


@router.post("/admin/gastos/finance-training/generate")
async def finance_training_generate(
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = require_admin_finanzas(),
    batch_key: Optional[str] = Form(None),
    seed: int = Form(42),
    modo: str = Form("apply"),
    force: Optional[str] = Form(None),
):
    repo = _repo_root()
    apply = (modo or "apply").strip().lower() != "dry_run"
    try:
        result = await generate_finance_training_dataset(
            session,
            repo_root=repo,
            batch_key=(batch_key or "").strip() or None,
            apply=apply,
            force=bool(force),
            seed=int(seed),
        )
        if not result.get("ok"):
            msg = result.get("error") or "Error en generación"
            return RedirectResponse(
                url=f"/admin/gastos/finance-training?error_msg={quote(str(msg))}",
                status_code=303,
            )
        bkey = result.get("batch_key", "")
        if apply:
            summary = (
                f"Lote {bkey}: {result.get('counts', {}).get('expenses', 0)} gastos, "
                f"{result.get('counts', {}).get('cfdi_rows_csv', 0)} filas CSV. "
                f"Contraseña demo: FinTrain2026!"
            )
            return RedirectResponse(
                url=f"/admin/gastos/finance-training?success_msg={quote(summary)}&batch_key={quote(bkey)}",
                status_code=303,
            )
        summary = f"dry_run OK plan batch={bkey}"
        return RedirectResponse(
            url=f"/admin/gastos/finance-training?success_msg={quote(summary)}&batch_key={quote(bkey)}",
            status_code=303,
        )
    except Exception as exc:
        await session.rollback()
        logger.exception(
            "Unexpected error generating finance training dataset",
            extra={
                "batch_key": (batch_key or "").strip(),
                "modo": modo,
                "actor_id": str(getattr(current_empleado, "id", "")),
            },
        )
        return _redirect_with_error_message(
            "/admin/gastos/finance-training",
            _OPERATION_GENERIC_ERROR,
        )


@router.post("/admin/gastos/finance-training/cleanup")
async def finance_training_cleanup(
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = require_admin_finanzas(),
    batch_key: str = Form(...),
    modo: str = Form("apply"),
):
    repo = _repo_root()
    apply = (modo or "apply").strip().lower() != "dry_run"
    bkey = (batch_key or "").strip()
    try:
        result = await cleanup_finance_training_dataset(
            session, repo_root=repo, batch_key=bkey, apply=apply
        )
        if not result.get("ok"):
            return RedirectResponse(
                url=f"/admin/gastos/finance-training?error_msg={quote(str(result.get('error', 'Error')))}",
                status_code=303,
            )
        if apply:
            msg = f"Lote {bkey} eliminado. " + json.dumps(
                result.get("deleted", {}), ensure_ascii=False
            )
        else:
            msg = "dry_run cleanup: " + json.dumps(
                result.get("would_delete", {}), ensure_ascii=False
            )
        return RedirectResponse(
            url=f"/admin/gastos/finance-training?success_msg={quote(msg)}",
            status_code=303,
        )
    except Exception as exc:
        await session.rollback()
        logger.exception(
            "Unexpected error cleaning finance training dataset",
            extra={
                "batch_key": bkey,
                "modo": modo,
                "actor_id": str(getattr(current_empleado, "id", "")),
            },
        )
        return _redirect_with_error_message(
            "/admin/gastos/finance-training",
            _OPERATION_GENERIC_ERROR,
        )


@router.post("/admin/gastos/finance-training/reset")
async def finance_training_reset(
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = require_admin_finanzas(),
    batch_key: Optional[str] = Form(None),
    seed: int = Form(42),
):
    repo = _repo_root()
    try:
        result = await reset_finance_training_dataset(
            session,
            repo_root=repo,
            batch_key=(batch_key or "").strip() or None,
            apply=True,
            seed=int(seed),
        )
        if not result.get("ok"):
            return RedirectResponse(
                url=f"/admin/gastos/finance-training?error_msg={quote(str(result.get('error', 'Error')))}",
                status_code=303,
            )
        bkey = result.get("batch_key", "")
        summary = (
            f"Reset generado lote {bkey}: {result.get('counts', {}).get('expenses', 0)} gastos. "
            f"Descarga CSV y súbelo en carga masiva CFDI."
        )
        return RedirectResponse(
            url=f"/admin/gastos/finance-training?success_msg={quote(summary)}&batch_key={quote(bkey)}",
            status_code=303,
        )
    except Exception as exc:
        await session.rollback()
        logger.exception(
            "Unexpected error resetting finance training dataset",
            extra={
                "batch_key": (batch_key or "").strip(),
                "actor_id": str(getattr(current_empleado, "id", "")),
            },
        )
        return _redirect_with_error_message(
            "/admin/gastos/finance-training",
            _OPERATION_GENERIC_ERROR,
        )


@router.get("/admin/gastos/finance-training/cfdi-csv")
async def finance_training_cfdi_csv_download(
    batch_key: str = Query(...),
    current_empleado: Empleado = require_admin_finanzas(),
):
    bkey = (batch_key or "").strip()
    path = training_cfdi_csv_path(_repo_root(), bkey)
    if not path.is_file():
        raise HTTPException(
            status_code=404, detail="CSV no encontrado para ese batch_key"
        )
    return FileResponse(
        path,
        filename=f"finance-training-{bkey}-cfdi.csv",
        media_type="text/csv; charset=utf-8",
    )


@router.get("/admin/gastos/expenses", response_class=HTMLResponse)
async def admin_expenses(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    numero_referencia: Optional[str] = Query(None),
    proyecto: Optional[str] = Query(None),
    cantidad_min: Optional[float] = Query(None),
    cantidad_max: Optional[float] = Query(None),
    concepto: Optional[str] = Query(None),
    tipo_gasto: Optional[str] = Query(None),
    estado_factura: Optional[str] = Query(None),
    estado_reembolso: Optional[str] = Query(None),
    created_from: Optional[str] = Query(None),
    created_to: Optional[str] = Query(None),
    cfdi_status: Optional[str] = Query(None),  # 'vinculado', 'pendiente', 'sin_cfdi'
    bi_year: Optional[str] = Query(None),
    bi_scope: Optional[str] = Query(None),
    current_empleado: Empleado = require_admin_finanzas(),
) -> str:
    """
    View all expenses in a table with filters.

    HARDENED VERSION per LEAP_SPEC_01:
    - Uses session.no_autoflush to prevent implicit flush errors
    - Shows CFDI status column (vinculado, pendiente, sin cfdi)
    - Shows CFDI linked via cfdi_report_id (UUID-based matching) AND nova_request_id
    - NEVER throws 500/Internal Server Errors
    """
    try:
        # Build filters
        conditions = build_expense_filters(
            numero_referencia=numero_referencia,
            proyecto=proyecto,
            cantidad_min=cantidad_min,
            cantidad_max=cantidad_max,
            concepto=concepto,
            tipo_gasto=tipo_gasto,
            estado_factura=estado_factura,
            estado_reembolso=estado_reembolso,
            created_from=created_from,
            created_to=created_to,
        )
        _append_bi_expense_filters(
            conditions=conditions, bi_year=bi_year, bi_scope=bi_scope
        )

        # Add CFDI status filter if specified
        if cfdi_status == "vinculado":
            conditions.append(ExpenseReport.cfdi_report_id.isnot(None))
        elif cfdi_status == "pendiente":
            conditions.append(
                and_(
                    ExpenseReport.cfdi_report_id.is_(None),
                    ExpenseReport.cfdi_uuid_manual.isnot(None),
                )
            )
        elif cfdi_status == "sin_cfdi":
            conditions.append(
                and_(
                    ExpenseReport.cfdi_report_id.is_(None),
                    ExpenseReport.cfdi_uuid_manual.is_(None),
                )
            )

        # Build query - use no_autoflush to prevent implicit flush during rendering
        query = select(ExpenseReport)
        if conditions:
            query = query.where(and_(*conditions))
        query = query.order_by(ExpenseReport.created_at.desc()).limit(
            1000
        )  # Safety limit

        # Execute within no_autoflush context
        with session.no_autoflush:
            result = await session.execute(query)
            expenses = result.scalars().all()

        # Fetch all tournaments for project name resolution
        tournament_map = {}
        with session.no_autoflush:
            tournaments_result = await session.execute(select(Tournament))
            tournaments = tournaments_result.scalars().all()
            tournament_map = {str(t.id).lower(): t.name for t in tournaments}

        # Get CFDI data for all expenses via TWO paths:
        # 1. nova_request_id (Tocino integration)
        # 2. cfdi_report_id (UUID-based matching)
        cfdi_map = {}  # keyed by nova_request_id
        cfdi_linked_map = {}  # keyed by cfdi_report_id (as string)

        expense_ids_admin = [e.id for e in expenses]
        ids_with_archivo_admin: set = set()
        gasto_adj_meta_admin: dict = {}
        if expense_ids_admin:
            with session.no_autoflush:
                ids_with_archivo_admin = await fetch_expense_ids_with_archivo_data(
                    session, expense_ids_admin
                )
                gasto_adj_meta_admin = await fetch_gasto_adjuntos_meta_batch(
                    session, expense_ids_admin
                )

        if expenses:
            with session.no_autoflush:
                # Path 1: Get CFDIs by nova_request_id
                nova_ids = [e.nova_request_id for e in expenses if e.nova_request_id]
                if nova_ids:
                    cfdi_result = await session.execute(
                        select(CFDIReport).where(
                            CFDIReport.nova_request_id.in_(nova_ids)
                        )
                    )
                    cfdi_records = cfdi_result.scalars().all()
                    cfdi_map = {c.nova_request_id: c for c in cfdi_records}

                # Path 2: Get CFDIs by cfdi_report_id (UUID-based matching)
                cfdi_report_ids = [
                    e.cfdi_report_id for e in expenses if e.cfdi_report_id
                ]
                if cfdi_report_ids:
                    cfdi_linked_result = await session.execute(
                        select(CFDIReport).where(CFDIReport.id.in_(cfdi_report_ids))
                    )
                    cfdi_linked_records = cfdi_linked_result.scalars().all()
                    cfdi_linked_map = {str(c.id): c for c in cfdi_linked_records}

        # Helper for export CSV impuestos
        def _format_impuestos(impuestos_detalle):
            if not impuestos_detalle or not isinstance(impuestos_detalle, dict):
                return "", ""
            traslados = impuestos_detalle.get("traslados", [])
            retenciones = impuestos_detalle.get("retenciones", [])
            traslados_str = "; ".join(
                [
                    f"{t.get('impuesto', '')} {t.get('tasa_o_cuota', 0) * 100:.2f}% (Base: {t.get('base', 0):.2f}, Importe: {t.get('importe', 0):.2f})"
                    for t in traslados
                ]
            )
            retenciones_str = "; ".join(
                [
                    f"{r.get('impuesto', '')}: {r.get('importe', 0):.2f}"
                    for r in retenciones
                ]
            )
            return traslados_str, retenciones_str

        from html import escape as html_escape

        export_rows = []
        rows_html = ""
        for idx, expense in enumerate(expenses):
            # Get CFDI from either path (UUID-based takes priority)
            cfdi = None
            if (
                expense.cfdi_report_id
                and str(expense.cfdi_report_id) in cfdi_linked_map
            ):
                cfdi = cfdi_linked_map[str(expense.cfdi_report_id)]
            elif expense.nova_request_id and expense.nova_request_id in cfdi_map:
                cfdi = cfdi_map[expense.nova_request_id]

            # Determine CFDI status (plain text for data attribute and filter)
            if expense.cfdi_report_id:
                cfdi_status_plain = "Vinculado"
                cfdi_status_display = (
                    '<span style="color: green; font-weight: bold;">✅ Vinculado</span>'
                )
            elif expense.cfdi_uuid_manual:
                cfdi_status_plain = "Pendiente"
                cfdi_status_display = '<span style="color: orange; font-weight: bold;">⏳ Pendiente</span>'
            else:
                cfdi_status_plain = "Sin CFDI"
                cfdi_status_display = '<span style="color: gray;">— Sin CFDI</span>'

            # Get CFDI UUID from linked record or manual entry
            cfdi_uuid_display = "-"
            if cfdi and cfdi.cfdi_uuid:
                cfdi_uuid_display = cfdi.cfdi_uuid
            elif expense.cfdi_uuid_manual:
                cfdi_uuid_display = f'<span style="color: orange;" title="UUID capturado, CFDI pendiente">{expense.cfdi_uuid_manual}</span>'

            # Resolve project name from UUID if applicable
            proyecto_display = resolve_project_name(expense.proyecto, tournament_map)

            # Data attributes for client-side filtering (escaped)
            nombre_enviador = html_escape((expense.nombre_enviador or "").strip())
            departamento = html_escape((expense.departamento or "").strip())
            proyecto_attr = html_escape((proyecto_display or "").strip())
            fase_torneo = html_escape((expense.fase_torneo or "").strip())
            concepto_attr = html_escape((expense.concepto or "").strip())
            est_reembolso = html_escape((expense.estado_reembolso or "").strip())
            fecha_val = (
                expense.created_at.strftime("%Y-%m-%d") if expense.created_at else ""
            )

            archivos_cell = html_expense_archivos_cell(
                expense.id,
                expense.id in ids_with_archivo_admin,
                gasto_adj_meta_admin.get(expense.id, []),
                expense.link_pdf,
                expense.link_xml,
            )

            # Export row for client-side CSV (same structure as server CSV)
            traslados_str, retenciones_str = (
                _format_impuestos(cfdi.impuestos_detalle)
                if cfdi and getattr(cfdi, "impuestos_detalle", None)
                else ("", "")
            )
            cuenta = getattr(expense, "cuenta_contable", None)
            cuenta_codigo = cuenta.codigo if cuenta else ""
            cuenta_nombre = cuenta.nombre if cuenta else ""
            export_rows.append(
                {
                    "id": str(expense.id),
                    "numero_referencia": expense.numero_referencia or "",
                    "nombre_enviador": expense.nombre_enviador or "",
                    "departamento": expense.departamento or "",
                    "proyecto": proyecto_display or "",
                    "fase_torneo": expense.fase_torneo or "",
                    "metodo_pago": expense.metodo_pago or "",
                    "ultimos_4_digitos": expense.ultimos_4_digitos or "",
                    "gasto_cantidad": expense.gasto_cantidad,
                    "concepto": expense.concepto or "",
                    "sub_cuenta": expense.sub_cuenta or "",
                    "tipo_gasto": expense.tipo_gasto or "",
                    "cfdi_use": expense.cfdi_use or "",
                    "cuenta_contable_base": expense.cuenta_contable_base or "",
                    "cuenta_codigo": cuenta_codigo,
                    "cuenta_nombre": cuenta_nombre,
                    "telegram_user_id": expense.telegram_user_id or "",
                    "estado_factura": expense.estado_factura or "",
                    "estado_reembolso": expense.estado_reembolso or "",
                    "nova_request_id": expense.nova_request_id or "",
                    "link_pdf": expense.link_pdf or "",
                    "link_xml": expense.link_xml or "",
                    "mensaje_error": expense.mensaje_error or "",
                    "cfdi_fecha": (
                        format_datetime(cfdi.fecha) if cfdi and cfdi.fecha else ""
                    ),
                    "cfdi_emisor_rfc": cfdi.emisor_rfc or "" if cfdi else "",
                    "cfdi_receptor_rfc": cfdi.receptor_rfc or "" if cfdi else "",
                    "cfdi_total": cfdi.total if cfdi else "",
                    "cfdi_uuid": cfdi.cfdi_uuid or "" if cfdi else "",
                    "cfdi_tipo_cambio": cfdi.tipo_cambio if cfdi else "",
                    "cfdi_emisor_nombre": cfdi.emisor_nombre or "" if cfdi else "",
                    "cfdi_descripcion_concepto": (
                        cfdi.descripcion_concepto_principal or "" if cfdi else ""
                    ),
                    "cfdi_serie": cfdi.serie or "" if cfdi else "",
                    "cfdi_folio": cfdi.folio or "" if cfdi else "",
                    "cfdi_subtotal": cfdi.subtotal if cfdi else "",
                    "cfdi_descuento": cfdi.descuento if cfdi else "",
                    "cfdi_moneda": cfdi.moneda or "" if cfdi else "",
                    "cfdi_traslados": traslados_str,
                    "cfdi_retenciones": retenciones_str,
                    "cfdi_fecha_timbrado": (
                        format_datetime(cfdi.fecha_timbrado)
                        if cfdi and getattr(cfdi, "fecha_timbrado", None)
                        else ""
                    ),
                    "cfdi_total_impuestos": (
                        cfdi.total_impuestos_trasladados
                        if cfdi and getattr(cfdi, "total_impuestos_trasladados", None)
                        else ""
                    ),
                    "cfdi_uuid_manual": expense.cfdi_uuid_manual or "",
                    "cfdi_vinculado": "Sí" if expense.cfdi_report_id else "No",
                    "created_at": format_datetime(expense.created_at),
                    "updated_at": format_datetime(expense.updated_at),
                }
            )

            rows_html += f"""
            <tr data-row-index="{idx}" data-nombre-enviador="{nombre_enviador}" data-departamento="{departamento}" data-proyecto="{proyecto_attr}" data-fase-torneo="{fase_torneo}" data-concepto="{concepto_attr}" data-estado-cfdi="{cfdi_status_plain}" data-est-reembolso="{est_reembolso}" data-fecha="{fecha_val}">
                <td>{format_value(expense.numero_referencia)}</td>
                <td>{format_value(expense.nombre_enviador)}</td>
                <td>{format_value(expense.departamento)}</td>
                <td>{format_value(proyecto_display)}</td>
                <td>{format_value(expense.fase_torneo)}</td>
                <td>{format_value(expense.metodo_pago)}</td>
                <td>{format_value(expense.ultimos_4_digitos)}</td>
                <td>${format_value(expense.gasto_cantidad)}</td>
                <td>{format_value(expense.concepto)}</td>
                <td>{format_value(expense.sub_cuenta)}</td>
                <td>{format_value(expense.tipo_gasto)}</td>
                <td>{cfdi_status_display}</td>
                <td>{cfdi_uuid_display}</td>
                <td>{format_value(expense.cfdi_use)}</td>
                <td>{format_value(expense.cuenta_contable_base)}</td>
                <td>{format_value(expense.estado_factura)}</td>
                <td>{format_value(expense.estado_reembolso)}</td>
                <td>{format_value(expense.nova_request_id)}</td>
                <td>{archivos_cell}</td>
                <td>{format_value(cfdi.serie) if cfdi else format_value(None)}</td>
                <td>{format_value(cfdi.folio) if cfdi else format_value(None)}</td>
                <td>{format_value(cfdi.total) if cfdi else format_value(None)}</td>
                <td>{format_value(cfdi.tipo_cambio) if cfdi else format_value(None)}</td>
                <td>{format_value(cfdi.emisor_nombre) if cfdi else format_value(None)}</td>
                <td>{format_value(cfdi.descripcion_concepto_principal) if cfdi else format_value(None)}</td>
                <td>{format_value(expense.created_at)}</td>
            </tr>
            """

        export_json = json.dumps(export_rows, ensure_ascii=False).replace("</", "<\\/")

        # Count CFDI statuses for summary
        vinculado_count = sum(1 for e in expenses if e.cfdi_report_id)
        pendiente_count = sum(
            1 for e in expenses if not e.cfdi_report_id and e.cfdi_uuid_manual
        )
        sin_cfdi_count = sum(
            1 for e in expenses if not e.cfdi_report_id and not e.cfdi_uuid_manual
        )

        bi_year_safe = (bi_year or "").strip()
        bi_year_safe = (
            bi_year_safe if (bi_year_safe.isdigit() and len(bi_year_safe) == 4) else ""
        )
        bi_scope_safe = (bi_scope or "").strip().lower()
        if bi_scope_safe not in {"all", ACTIVE_TOURNAMENT_SCOPE}:
            bi_scope_safe = ""
        bi_suffix = ""
        if bi_year_safe or bi_scope_safe:
            parts = []
            if bi_year_safe:
                parts.append(f"bi_year={bi_year_safe}")
            if bi_scope_safe:
                parts.append(f"bi_scope={bi_scope_safe}")
            bi_suffix = "&" + "&".join(parts)
        bi_context_label = (
            f"año={bi_year_safe or 'n/a'} · ámbito={bi_scope_safe or 'all'}"
        )
        hero_actions_html = f"""
            <a href="#" class="button" onclick="downloadCSV(); return false;">Exportar CSV</a>
            <a href="/admin/gastos/cfdis/carga-masiva{('?'+bi_suffix[1:]) if bi_suffix else ''}" class="button secondary">Carga CFDIs</a>
            <a href="/admin/gastos/cfdis/matching" class="button secondary">Matching CFDI</a>
        """
        hero_side_html = f"""
            <div class="eyebrow">Cobertura</div>
            <div class="meta-grid">
                <div class="meta-card">
                    <span>Resultados</span>
                    <strong>{len(expenses)}</strong>
                    <small>Filas visibles antes de filtro cliente-side.</small>
                </div>
                <div class="meta-card">
                    <span>Contexto BI</span>
                    <strong style="font-size:1rem;">{escape(bi_context_label)}</strong>
                    <small>Se mantiene al navegar entre vistas financieras.</small>
                </div>
            </div>
        """

        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Gastos - Admin</title>
            <style>
                {_admin_workspace_styles("1820px", layout="data")}
                .filter-grid {{
                    display: grid;
                    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
                    gap: 15px;
                    margin-bottom: 15px;
                }}
                .filter-group label {{
                    font-size: 12px;
                    font-weight: 700;
                    color: #475569;
                    margin-bottom: 5px;
                    display: block;
                }}
                .filter-group input, .filter-group select {{
                    padding: 10px 12px;
                    border: 1px solid #cbd5e1;
                    border-radius: 12px;
                    font-size: 14px;
                    width: 100%;
                }}
                .filter-actions {{
                    display: flex;
                    gap: 10px;
                }}
                button.apply {{ background-color: #0f766e; color: white; padding: 10px 20px; border: none; border-radius: 12px; cursor: pointer; font-weight: 700; }}
                button.clear {{ background-color: #fff; color: #334155; padding: 10px 20px; border: 1px solid #dbe2ea; border-radius: 12px; cursor: pointer; font-weight: 700; }}
                .summary-links {{ display:flex; gap:12px; flex-wrap:wrap; }}
                .summary-links a {{ text-decoration:none; color:#0f172a; }}
                .summary-links a:hover {{ text-decoration:underline; }}
            </style>
            <script type="application/json" id="expenses-export-data">__EXPORT_JSON__</script>
            <script>
            function applyFilters() {{
                var nombreEnviador = (document.getElementById('filter_nombre_enviador').value || '').trim().toLowerCase();
                var departamento = (document.getElementById('filter_departamento').value || '').trim().toLowerCase();
                var proyecto = (document.getElementById('filter_proyecto').value || '').trim().toLowerCase();
                var faseTorneo = (document.getElementById('filter_fase_torneo').value || '').trim().toLowerCase();
                var concepto = (document.getElementById('filter_concepto').value || '').trim().toLowerCase();
                var estadoCfdi = (document.getElementById('filter_estado_cfdi').value || '').trim();
                var estReembolso = (document.getElementById('filter_est_reembolso').value || '').trim().toLowerCase();
                var fechaDesde = (document.getElementById('filter_fecha_desde').value || '').trim();
                var fechaHasta = (document.getElementById('filter_fecha_hasta').value || '').trim();
                var rows = document.querySelectorAll('#expensesTable tbody tr[data-row-index]');
                var visible = 0;
                for (var i = 0; i < rows.length; i++) {{
                    var r = rows[i];
                    var nom = (r.getAttribute('data-nombre-enviador') || '').toLowerCase();
                    var dep = (r.getAttribute('data-departamento') || '').toLowerCase();
                    var proy = (r.getAttribute('data-proyecto') || '').toLowerCase();
                    var fase = (r.getAttribute('data-fase-torneo') || '').toLowerCase();
                    var conc = (r.getAttribute('data-concepto') || '').toLowerCase();
                    var estCfdi = (r.getAttribute('data-estado-cfdi') || '');
                    var estRem = (r.getAttribute('data-est-reembolso') || '').toLowerCase();
                    var fecha = r.getAttribute('data-fecha') || '';
                    var show = true;
                    if (nombreEnviador && nom.indexOf(nombreEnviador) === -1) show = false;
                    if (show && departamento && dep.indexOf(departamento) === -1) show = false;
                    if (show && proyecto && proy.indexOf(proyecto) === -1) show = false;
                    if (show && faseTorneo && fase.indexOf(faseTorneo) === -1) show = false;
                    if (show && concepto && conc.indexOf(concepto) === -1) show = false;
                    if (show && estadoCfdi && estCfdi !== estadoCfdi) show = false;
                    if (show && estReembolso && estRem.indexOf(estReembolso) === -1) show = false;
                    if (show && fechaDesde && (fecha < fechaDesde || !fecha)) show = false;
                    if (show && fechaHasta && (fecha > fechaHasta || !fecha)) show = false;
                    r.style.display = show ? '' : 'none';
                    if (show) visible++;
                }}
                var countEl = document.getElementById('resultsCount');
                if (countEl) countEl.textContent = 'Mostrando ' + visible + ' resultados (máx. 1000)';
            }}
            function clearFilters() {{
                document.getElementById('filter_nombre_enviador').value = '';
                document.getElementById('filter_departamento').value = '';
                document.getElementById('filter_proyecto').value = '';
                document.getElementById('filter_fase_torneo').value = '';
                document.getElementById('filter_concepto').value = '';
                document.getElementById('filter_estado_cfdi').value = '';
                document.getElementById('filter_est_reembolso').value = '';
                document.getElementById('filter_fecha_desde').value = '';
                document.getElementById('filter_fecha_hasta').value = '';
                var rows = document.querySelectorAll('#expensesTable tbody tr[data-row-index]');
                for (var i = 0; i < rows.length; i++) rows[i].style.display = '';
                var countEl = document.getElementById('resultsCount');
                if (countEl) countEl.textContent = 'Mostrando ' + rows.length + ' resultados (máx. 1000)';
            }}
            function escapeCsv(val) {{
                if (val === null || val === undefined) return '';
                var s = String(val);
                if (/[,"\\n\\r]/.test(s)) return '"' + s.replace(/"/g, '""') + '"';
                return s;
            }}
            function downloadCSV() {{
                var visibleRows = document.querySelectorAll('#expensesTable tbody tr[data-row-index]:not([style*="display: none"])');
                var dataEl = document.getElementById('expenses-export-data');
                if (!dataEl) {{ window.location.href = '/admin/gastos/expenses/export'; return; }}
                var allData = JSON.parse(dataEl.textContent);
                var indices = [];
                for (var i = 0; i < visibleRows.length; i++) {{
                    var idx = parseInt(visibleRows[i].getAttribute('data-row-index'), 10);
                    if (!isNaN(idx) && idx >= 0 && idx < allData.length) indices.push(idx);
                }}
                var rowsToExport = indices.length ? indices.map(function(i) {{ return allData[i]; }}) : allData;
                var headers = ["ID","Numero Referencia","Nombre Enviador","Departamento","Proyecto","Fase Torneo","Método Pago","Últimos 4 Dígitos","Gasto Cantidad","Concepto","Sub-Cuenta","Tipo Gasto","Uso CFDI","Cuenta Bancaria Base","Cuenta Contable Codigo","Cuenta Contable Nombre","Telegram User ID","Estado Factura","Estado Reembolso","Nova Request ID","Link PDF","Link XML","Mensaje Error","CFDI Fecha","CFDI Emisor RFC","CFDI Receptor RFC","CFDI Total","CFDI UUID","CFDI Tipo Cambio","CFDI Emisor Nombre","CFDI Descripcion Concepto","CFDI Serie","CFDI Folio","CFDI Subtotal","CFDI Descuento","CFDI Moneda","CFDI Traslados (IVA)","CFDI Retenciones","CFDI Fecha Timbrado","CFDI Total Impuestos","CFDI UUID (Manual)","CFDI Vinculado","Created At","Updated At"];
                var csvLines = [headers.join(',')];
                for (var i = 0; i < rowsToExport.length; i++) {{
                    var row = rowsToExport[i];
                    var cells = [escapeCsv(row.id), escapeCsv(row.numero_referencia), escapeCsv(row.nombre_enviador), escapeCsv(row.departamento), escapeCsv(row.proyecto), escapeCsv(row.fase_torneo), escapeCsv(row.metodo_pago), escapeCsv(row.ultimos_4_digitos), row.gasto_cantidad, escapeCsv(row.concepto), escapeCsv(row.sub_cuenta), escapeCsv(row.tipo_gasto), escapeCsv(row.cfdi_use), escapeCsv(row.cuenta_contable_base), escapeCsv(row.cuenta_codigo), escapeCsv(row.cuenta_nombre), row.telegram_user_id, escapeCsv(row.estado_factura), escapeCsv(row.estado_reembolso), escapeCsv(row.nova_request_id), escapeCsv(row.link_pdf), escapeCsv(row.link_xml), escapeCsv(row.mensaje_error), escapeCsv(row.cfdi_fecha), escapeCsv(row.cfdi_emisor_rfc), escapeCsv(row.cfdi_receptor_rfc), row.cfdi_total, escapeCsv(row.cfdi_uuid), row.cfdi_tipo_cambio, escapeCsv(row.cfdi_emisor_nombre), escapeCsv(row.cfdi_descripcion_concepto), escapeCsv(row.cfdi_serie), escapeCsv(row.cfdi_folio), row.cfdi_subtotal, row.cfdi_descuento, escapeCsv(row.cfdi_moneda), escapeCsv(row.cfdi_traslados), escapeCsv(row.cfdi_retenciones), escapeCsv(row.cfdi_fecha_timbrado), row.cfdi_total_impuestos, escapeCsv(row.cfdi_uuid_manual), escapeCsv(row.cfdi_vinculado), escapeCsv(row.created_at), escapeCsv(row.updated_at)];
                    csvLines.push(cells.join(','));
                }}
                var csvContent = '\\uFEFF' + csvLines.join('\\r\\n');
                var blob = new Blob([csvContent], {{ type: 'text/csv;charset=utf-8;' }});
                var a = document.createElement('a');
                a.href = URL.createObjectURL(blob);
                a.download = 'expenses.csv';
                a.click();
                URL.revokeObjectURL(a.href);
            }}
            </script>
        </head>
        <body>
            <div class="container">
                {render_admin_navigation(current_empleado, "dashboard", subtitle="Explora gasto operativo, estado fiscal y exportes sin salir del mismo workspace financiero.")}
                {_render_admin_workspace_hero(
                    eyebrow="Finanzas",
                    title="Gastos operativos",
                    description="Vista global de gasto con estado CFDI, exportes y filtros rápidos para revisión operativa antes de conciliación o cierre.",
                    actions_html=hero_actions_html,
                    side_html=hero_side_html,
                )}
                <div class="stack">
                    <section class="meta-grid">
                        <div class="meta-card">
                            <span>Vinculados</span>
                            <strong>{vinculado_count}</strong>
                            <small>Gastos ya enlazados a un CFDI.</small>
                        </div>
                        <div class="meta-card">
                            <span>Pendientes</span>
                            <strong>{pendiente_count}</strong>
                            <small>UUID capturado, pero aún sin CFDI enlazado.</small>
                        </div>
                        <div class="meta-card">
                            <span>Sin CFDI</span>
                            <strong>{sin_cfdi_count}</strong>
                            <small>Sin evidencia fiscal vinculada.</small>
                        </div>
                    </section>
                    <section class="surface">
                        <div class="section-head">
                            <div>
                                <div class="eyebrow">Cobertura fiscal</div>
                                <h2>Estado CFDI</h2>
                                <div class="section-note">Usa estos accesos para ir directo a los subconjuntos fiscales más sensibles.</div>
                            </div>
                        </div>
                        <div class="summary-links">
                            <a href="?cfdi_status=vinculado{bi_suffix}">Vinculados: {vinculado_count}</a>
                            <a href="?cfdi_status=pendiente{bi_suffix}">Pendientes: {pendiente_count}</a>
                            <a href="?cfdi_status=sin_cfdi{bi_suffix}">Sin CFDI: {sin_cfdi_count}</a>
                            <a href="/admin/gastos/expenses{('?'+bi_suffix[1:]) if bi_suffix else ''}">Ver todos</a>
                        </div>
                    </section>
                    <section class="surface">
                        <div class="section-head">
                            <div>
                                <div class="eyebrow">Filtros</div>
                                <h2>Refinar la bandeja</h2>
                                <div class="section-note">Los filtros corren en cliente sobre el dataset visible para no reconsultar la base en cada ajuste.</div>
                            </div>
                        </div>
                        <form id="filterForm" onsubmit="applyFilters(); return false;">
                    <div class="filter-grid">
                        <div class="filter-group">
                            <label>Nombre enviador</label>
                            <input type="text" id="filter_nombre_enviador" placeholder="">
                        </div>
                        <div class="filter-group">
                            <label>Departamento</label>
                            <input type="text" id="filter_departamento" placeholder="">
                        </div>
                        <div class="filter-group">
                            <label>Proyecto</label>
                            <input type="text" id="filter_proyecto" placeholder="">
                        </div>
                        <div class="filter-group">
                            <label>Fase torneo</label>
                            <input type="text" id="filter_fase_torneo" placeholder="">
                        </div>
                        <div class="filter-group">
                            <label>Concepto</label>
                            <input type="text" id="filter_concepto" placeholder="">
                        </div>
                        <div class="filter-group">
                            <label>Estado CFDI</label>
                            <select id="filter_estado_cfdi">
                                <option value="">Todos</option>
                                <option value="Vinculado">Vinculado</option>
                                <option value="Pendiente">Pendiente</option>
                                <option value="Sin CFDI">Sin CFDI</option>
                            </select>
                        </div>
                        <div class="filter-group">
                            <label>Est. reembolso</label>
                            <input type="text" id="filter_est_reembolso" placeholder="">
                        </div>
                        <div class="filter-group">
                            <label>Fecha desde</label>
                            <input type="date" id="filter_fecha_desde">
                        </div>
                        <div class="filter-group">
                            <label>Fecha hasta</label>
                            <input type="date" id="filter_fecha_hasta">
                        </div>
                    </div>
                    <div class="filter-actions">
                        <button type="button" class="apply" onclick="applyFilters(); return false;">Aplicar filtros</button>
                        <button type="button" class="clear" onclick="clearFilters(); return false;">Limpiar Filtros</button>
                    </div>
                        </form>
                    </section>
                    <section class="surface">
                        <div class="section-head">
                            <div>
                                <div class="eyebrow">Resultados</div>
                                <h2>Bandeja de gastos</h2>
                                <div class="section-note" id="resultsCount">Mostrando {len(expenses)} resultados (máx. 1000)</div>
                            </div>
                        </div>
                        <div class="table-shell">
            <table id="expensesTable">
                <thead>
                    <tr>
                        <th>Referencia</th>
                        <th>Nombre Enviador</th>
                        <th>Departamento</th>
                        <th>Proyecto</th>
                        <th>Fase Torneo</th>
                        <th>Método Pago</th>
                        <th>Últimos 4</th>
                        <th>Cantidad</th>
                        <th>Concepto</th>
                        <th>Sub-Cuenta</th>
                        <th>Tipo</th>
                        <th>Estado CFDI</th>
                        <th>UUID CFDI</th>
                        <th>Uso CFDI</th>
                        <th>Cuenta Base</th>
                        <th>Est. Factura</th>
                        <th>Est. Reembolso</th>
                        <th>Nova ID</th>
                        <th>Archivos</th>
                        <th>Serie</th>
                        <th>Folio</th>
                        <th>Total</th>
                        <th>T. Cambio</th>
                        <th>Emisor</th>
                        <th>Concepto CFDI</th>
                        <th>Fecha</th>
                    </tr>
                </thead>
                <tbody>
                    {rows_html}
                </tbody>
            </table>
                        </div>
                    </section>
                </div>
            </div>
        </body>
        </html>
        """
        html = html.replace("__EXPORT_JSON__", export_json)
        return html

    except Exception as e:
        logger.error(f"Error in admin_expenses: {e}", exc_info=True)
        return _render_admin_error_page(
            title="Error al cargar gastos",
            message="La bandeja de gastos no pudo renderizarse en esta solicitud. Puedes volver a finanzas o abrir otra consola administrativa.",
            detail=str(e),
            current_empleado=current_empleado,
            return_href="/admin/gastos",
            return_label="Volver a finanzas",
        )


@router.get("/admin/gastos/invoices", response_class=HTMLResponse)
async def admin_invoices(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    nova_request_id: Optional[str] = Query(None),
    estado_factura: Optional[str] = Query(None),
    has_error: Optional[bool] = Query(None),
    created_from: Optional[str] = Query(None),
    created_to: Optional[str] = Query(None),
    origen: Optional[str] = Query(None),  # 'tocino', 'csv', 'sat'
    current_empleado: Empleado = require_admin_finanzas(),
) -> str:
    """
    View all invoices/CFDIs in a table with filters.

    HARDENED VERSION per LEAP_SPEC_01:
    - Uses session.no_autoflush to prevent implicit flush errors
    - Shows both Tocino-generated invoices AND CSV-imported CFDIs
    - NEVER throws 500/Internal Server Errors
    """
    try:
        # Build filters for invoice_reports
        conditions = build_invoice_filters(
            nova_request_id=nova_request_id,
            estado_factura=estado_factura,
            has_error=has_error,
            created_from=created_from,
            created_to=created_to,
        )

        # Build query for invoices - use no_autoflush
        with session.no_autoflush:
            query = select(InvoiceReport)
            if conditions:
                query = query.where(and_(*conditions))
            query = query.order_by(InvoiceReport.created_at.desc()).limit(1000)

            result = await session.execute(query)
            invoices = result.scalars().all()

            # Get CFDI data for all invoices (join by nova_request_id)
            cfdi_map = {}
            if invoices:
                nova_ids = [i.nova_request_id for i in invoices if i.nova_request_id]
                if nova_ids:
                    cfdi_result = await session.execute(
                        select(CFDIReport).where(
                            CFDIReport.nova_request_id.in_(nova_ids)
                        )
                    )
                    cfdi_records = cfdi_result.scalars().all()
                    cfdi_map = {c.nova_request_id: c for c in cfdi_records}

            # Also get standalone CFDIs (CSV-imported, not linked to invoice_reports)
            cfdi_only_query = select(CFDIReport)
            cfdi_conditions = []
            if origen == "csv":
                cfdi_conditions.append(CFDIReport.origen == "csv")
            elif origen == "sat":
                cfdi_conditions.append(CFDIReport.origen == "sat")
            elif origen == "tocino":
                cfdi_conditions.append(CFDIReport.origen == "tocino")

            if cfdi_conditions:
                cfdi_only_query = cfdi_only_query.where(and_(*cfdi_conditions))

            cfdi_only_query = cfdi_only_query.order_by(
                CFDIReport.created_at.desc()
            ).limit(500)
            cfdi_only_result = await session.execute(cfdi_only_query)
            standalone_cfdis = cfdi_only_result.scalars().all()

        # Build rows HTML for invoices
        rows_html = ""
        for invoice in invoices:
            cfdi = (
                cfdi_map.get(invoice.nova_request_id)
                if invoice.nova_request_id
                else None
            )
            if origen and not _cfdi_matches_origen_filter(
                cfdi, origen, invoice_pending=True
            ):
                continue
            if cfdi:
                origen_display = _cfdi_origen_badge(cfdi.origen)
            else:
                origen_display = "-"

            rows_html += f"""
            <tr>
                <td>{format_value(invoice.nova_request_id)}</td>
                <td>{format_value(invoice.estado_factura)}</td>
                <td>{origen_display}</td>
                <td>{f'<a href="{invoice.link_pdf}" target="_blank">PDF</a>' if invoice.link_pdf else '-'}</td>
                <td>{f'<a href="{invoice.link_xml}" target="_blank">XML</a>' if invoice.link_xml else '-'}</td>
                <td>{format_value(invoice.mensaje_error)[:50] if invoice.mensaje_error else '-'}</td>
                <td>{format_value(cfdi.cfdi_uuid) if cfdi else format_value(None)}</td>
                <td>{format_value(cfdi.serie) if cfdi else format_value(None)}</td>
                <td>{format_value(cfdi.folio) if cfdi else format_value(None)}</td>
                <td>{format_value(cfdi.total) if cfdi else format_value(None)}</td>
                <td>{format_value(cfdi.tipo_cambio) if cfdi else format_value(None)}</td>
                <td>{format_value(cfdi.emisor_nombre)[:30] if cfdi and cfdi.emisor_nombre else format_value(None)}</td>
                <td>{format_value(cfdi.descripcion_concepto_principal)[:50] if cfdi and cfdi.descripcion_concepto_principal else format_value(None)}</td>
Warning: truncated output (original token count: 60256)
Total output lines: 5000

                <td>{format_value(invoice.created_at)}</td>
            </tr>
            """

        # Add standalone CFDIs (CSV-imported) that aren't linked to invoice_reports
        invoice_nova_ids = {i.nova_request_id for i in invoices if i.nova_request_id}
        for cfdi in standalone_cfdis:
            if cfdi.nova_request_id and cfdi.nova_request_id in invoice_nova_ids:
                continue  # Already shown via invoice_reports

            origen_display = _cfdi_origen_badge(cfdi.origen)

            rows_html += f"""
            <tr style="background-color: #f0f8ff;">
                <td>{format_value(cfdi.nova_request_id) or '<em>CSV Import</em>'}</td>
                <td>-</td>
                <td>{origen_display}</td>
                <td>-</td>
                <td>-</td>
                <td>-</td>
                <td>{format_value(cfdi.cfdi_uuid)}</td>
                <td>{format_value(cfdi.serie)}</td>
                <td>{format_value(cfdi.folio)}</td>
                <td>{format_value(cfdi.total)}</td>
                <td>{format_value(cfdi.tipo_cambio)}</td>
                <td>{format_value(cfdi.emisor_nombre)[:30] if cfdi.emisor_nombre else '-'}</td>
                <td>{format_value(cfdi.descripcion_concepto_principal)[:50] if cfdi.descripcion_concepto_principal else '-'}</td>
                <td>{format_value(cfdi.created_at)}</td>
            </tr>
            """

        # Count CFDIs by origen
        tocino_count = sum(
            1 for c in standalone_cfdis if c.origen == "tocino" or c.origen is None
        )
        csv_count = sum(1 for c in standalone_cfdis if c.origen == "csv")
        sat_count = sum(1 for c in standalone_cfdis if c.origen == "sat")

        hero_actions_html = """
            <a href="/admin/gastos/invoices/export" class="button">Exportar CSV</a>
            <a href="/admin/gastos/cfdis/carga-masiva" class="button secondary">Carga CFDIs</a>
            <a href="/admin/gastos/cfdis/matching" class="button secondary">Matching CFDI</a>
        """
        hero_side_html = f"""
            <div class="eyebrow">Cobertura</div>
            <div class="meta-grid">
                <div class="meta-card">
                    <span>Invoice reports</span>
                    <strong>{len(invoices)}</strong>
                    <small>Registros provenientes del flujo Tocino.</small>
                </div>
                <div class="meta-card">
                    <span>CFDI standalone</span>
                    <strong>{len(standalone_cfdis)}</strong>
                    <small>CFDI cargados o detectados sin invoice report asociado.</small>
                </div>
            </div>
        """
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Facturas/CFDIs - Admin</title>
            <style>
                {_admin_workspace_styles("1820px", layout="data")}
                .summary-links {{ display:flex; gap:12px; flex-wrap:wrap; }}
                .summary-links a {{ text-decoration:none; color:#0f172a; }}
                .summary-links a:hover {{ text-decoration:underline; }}
                .csv-row td {{ background:#eff6ff; }}
            </style>
        </head>
        <body>
            <div class="container">
                {render_admin_navigation(current_empleado, "dashboard", subtitle="Sigue el estado del flujo fiscal desde la misma consola financiera, sin separar facturas, CFDI y matching.")}
                {_render_admin_workspace_hero(
                    eyebrow="Finanzas",
                    title="Facturas y CFDI",
                    description="Vista consolidada de invoice reports y CFDI disponibles, incluyendo registros CSV que todavía no están ligados al flujo Tocino.",
                    actions_html=hero_actions_html,
                    side_html=hero_side_html,
                )}
                <div class="stack">
                    <section class="meta-grid">
                        <div class="meta-card"><span>Tocino</span><strong>{tocino_count}</strong><small>CFDI originados por flujo integrado.</small></div>
                        <div class="meta-card"><span>CSV</span><strong>{csv_count}</strong><small>CFDI importados manualmente.</small></div>
                        <div class="meta-card"><span>SAT</span><strong>{sat_count}</strong><small>CFDI descargados vía e.firma SAT.</small></div>
                        <div class="meta-card"><span>Total visible</span><strong>{len(invoices) + len(standalone_cfdis)}</strong><small>Suma de invoice reports y CFDI standalone.</small></div>
                    </section>
                    <section class="surface">
                        <div class="section-head">
                            <div>
                                <div class="eyebrow">Origen</div>
                                <h2>Lectura rápida</h2>
                                <div class="section-note">Salta entre origen Tocino, CSV, SAT o la vista completa sin abandonar la consola.</div>
                            </div>
                        </div>
                        <div class="summary-links">
                            <a href="?origen=tocino">Tocino: {tocino_count}</a>
                            <a href="?origen=csv">CSV import: {csv_count}</a>
                            <a href="?origen=sat">SAT: {sat_count}</a>
                            <a href="/admin/gastos/invoices">Ver todos</a>
                        </div>
                    </section>
                    <section class="surface">
                        <div class="section-head">
                            <div>
                                <div class="eyebrow">Resultados</div>
                                <h2>Bandeja fiscal</h2>
                                <div class="section-note">Mostrando {len(invoices)} facturas + {len(standalone_cfdis)} CFDI (máx. 1000 + 500).</div>
                            </div>
                        </div>
                        <div class="table-shell">
            <table>
                <thead>
                    <tr>
                        <th>Nova Request ID</th>
                        <th>Estado</th>
                        <th>Origen</th>
                        <th>PDF</th>
                        <th>XML</th>
                        <th>Error</th>
                        <th>CFDI UUID</th>
                        <th>Serie</th>
                        <th>Folio</th>
                        <th>Total</th>
                        <th>T. Cambio</th>
                        <th>Emisor</th>
                        <th>Concepto</th>
                        <th>Fecha</th>
                    </tr>
                </thead>
                <tbody>
                    {rows_html}
                </tbody>
            </table>
                        </div>
                    </section>
                </div>
            </div>
        </body>
        </html>
        """
        return html

    except Exception as e:
        logger.error(f"Error in admin_invoices: {e}", exc_info=True)
        return _render_admin_error_page(
            title="Error al cargar facturas y CFDI",
            message="La vista fiscal no pudo completarse. El resto del workspace sigue disponible para continuar la operación.",
            detail=str(e),
            current_empleado=current_empleado,
            return_href="/admin/gastos",
            return_label="Volver a finanzas",
        )


@router.get("/admin/gastos/expenses/export")
async def export_expenses(
    session: AsyncSession = Depends(get_db_session),
    numero_referencia: Optional[str] = Query(None),
    proyecto: Optional[str] = Query(None),
    cantidad_min: Optional[float] = Query(None),
    cantidad_max: Optional[float] = Query(None),
    concepto: Optional[str] = Query(None),
    tipo_gasto: Optional[str] = Query(None),
    estado_factura: Optional[str] = Query(None),
    estado_reembolso: Optional[str] = Query(None),
    created_from: Optional[str] = Query(None),
    created_to: Optional[str] = Query(None),
) -> Response:
    """Export expenses to CSV."""

    conditions = build_expense_filters(
        numero_referencia=numero_referencia,
        proyecto=proyecto,
        cantidad_min=cantidad_min,
        cantidad_max=cantidad_max,
        concepto=concepto,
        tipo_gasto=tipo_gasto,
        estado_factura=estado_factura,
        estado_reembolso=estado_reembolso,
        created_from=created_from,
        created_to=created_to,
    )

    query = select(ExpenseReport)
    if conditions:
        query = query.where(and_(*conditions))
    query = query.order_by(ExpenseReport.created_at.desc())

    result = await session.execute(query)
    expenses = result.scalars().all()

    # Fetch all tournaments for project name resolution
    tournaments_result = await session.execute(select(Tournament))
    tournaments = tournaments_result.scalars().all()
    tournament_map = {str(t.id).lower(): t.name for t in tournaments}

    # Get CFDI data for all expenses (join by nova_request_id)
    cfdi_map = {}
    if expenses:
        nova_ids = [e.nova_request_id for e in expenses if e.nova_request_id]
        if nova_ids:
            cfdi_result = await session.execute(
                select(CFDIReport).where(CFDIReport.nova_request_id.in_(nova_ids))
            )
            cfdi_records = cfdi_result.scalars().all()
            cfdi_map = {c.nova_request_id: c for c in cfdi_records}

    # Get CFDI data for expenses linked via cfdi_report_id (UUID-based matching)
    cfdi_linked_map = {}
    if expenses:
        cfdi_report_ids = [e.cfdi_report_id for e in expenses if e.cfdi_report_id]
        if cfdi_report_ids:
            cfdi_linked_result = await session.execute(
                select(CFDIReport).where(CFDIReport.id.in_(cfdi_report_ids))
            )
            cfdi_linked_records = cfdi_linked_result.scalars().all()
            cfdi_linked_map = {str(c.id): c for c in cfdi_linked_records}

    # Helper function to format impuestos
    def format_impuestos(impuestos_detalle):
        """Format impuestos for CSV display."""
        if not impuestos_detalle or not isinstance(impuestos_detalle, dict):
            return "", ""
        traslados = impuestos_detalle.get("traslados", [])
        retenciones = impuestos_detalle.get("retenciones", [])

        # Format traslados
        traslados_str = "; ".join(
            [
                f"{t.get('impuesto', '')} {t.get('tasa_o_cuota', 0)*100:.2f}% (Base: {t.get('base', 0):.2f}, Importe: {t.get('importe', 0):.2f})"
                for t in traslados
            ]
        )

        # Format retenciones
        retenciones_str = "; ".join(
            [f"{r.get('impuesto', '')}: {r.get('importe', 0):.2f}" for r in retenciones]
        )

        return traslados_str, retenciones_str

    # Create CSV
    output = io.StringIO()
    writer = csv.writer(output)

    # Write header (expanded with CFDI fields in Excel order)
    writer.writerow(
        [
            "ID",
            "Numero Referencia",
            "Nombre Enviador",
            "Departamento",
            "Proyecto",
            "Fase Torneo",
            "Método Pago",
            "Últimos 4 Dígitos",
            "Gasto Cantidad",
            "Concepto",
            "Sub-Cuenta",
            "Tipo Gasto",
            "Uso CFDI",
            "Cuenta Bancaria Base",
            "Cuenta Contable Codigo",
            "Cuenta Contable Nombre",
            "Telegram User ID",
            "Estado Factura",
            "Estado Reembolso",
            "Nova Request ID",
            "Link PDF",
            "Link XML",
            "Mensaje Error",
            # CFDI fields (Excel order)
            "CFDI Fecha",
            "CFDI Emisor RFC",
            "CFDI Receptor RFC",
            "CFDI Total",
            "CFDI UUID",
            "CFDI Tipo Cambio",
            "CFDI Emisor Nombre",
            "CFDI Descripcion Concepto",
            "CFDI Serie",
            "CFDI Folio",
            "CFDI Subtotal",
            "CFDI Descuento",
            "CFDI Moneda",
            "CFDI Traslados (IVA)",
            "CFDI Retenciones",
            "CFDI Fecha Timbrado",
            "CFDI Total Impuestos",
            "CFDI UUID (Manual)",
            "CFDI Vinculado",
            "Created At",
            "Updated At",
        ]
    )

    # Write data
    for expense in expenses:
        cfdi = (
            cfdi_map.get(expense.nova_request_id) if expense.nova_request_id else None
        )
        # Also check for CFDI linked via cfdi_report_id
        cfdi_linked = (
            cfdi_linked_map.get(str(expense.cfdi_report_id))
            if expense.cfdi_report_id
            else None
        )
        cfdi = cfdi or cfdi_linked
        traslados_str, retenciones_str = (
            format_impuestos(cfdi.impuestos_detalle)
            if cfdi and cfdi.impuestos_detalle
            else ("", "")
        )

        # Get cuenta contable info
        cuenta_codigo = (
            expense.cuenta_contable.codigo if expense.cuenta_contable else ""
        )
        cuenta_nombre = (
            expense.cuenta_contable.nombre if expense.cuenta_contable else ""
        )

        # Determine CFDI UUID and vinculado status
        cfdi_uuid_manual = expense.cfdi_uuid_manual or ""
        cfdi_vinculado = "Sí" if expense.cfdi_report_id else "No"

        # Resolve project name from UUID if applicable
        proyecto_display = resolve_project_name(expense.proyecto, tournament_map)

        writer.writerow(
            [
                str(expense.id),
                expense.numero_referencia,
                expense.nombre_enviador or "",
                expense.departamento or "",
                proyecto_display,
                expense.fase_torneo or "",
                expense.metodo_pago or "",
                expense.ultimos_4_digitos or "",
                expense.gasto_cantidad,
                expense.concepto,
                expense.sub_cuenta or "",
                expense.tipo_gasto,
                expense.cfdi_use or "",
                expense.cuenta_contable_base or "",
                cuenta_codigo,
                cuenta_nombre,
                expense.telegram_user_id,
                expense.estado_factura,
                expense.estado_reembolso,
                expense.nova_request_id or "",
                expense.link_pdf or "",
                expense.link_xml or "",
                expense.mensaje_error or "",
                # CFDI fields (Excel order)
                format_datetime(cfdi.fecha) if cfdi and cfdi.fecha else "",
                cfdi.emisor_rfc or "" if cfdi else "",
                cfdi.receptor_rfc or "" if cfdi else "",
                cfdi.total or "" if cfdi else "",
                cfdi.cfdi_uuid or "" if cfdi else "",
                cfdi.tipo_cambio or "" if cfdi else "",
                cfdi.emisor_nombre or "" if cfdi else "",
                cfdi.descripcion_concepto_principal or "" if cfdi else "",
                cfdi.serie or "" if cfdi else "",
                cfdi.folio or "" if cfdi else "",
                cfdi.subtotal or "" if cfdi else "",
                cfdi.descuento or "" if cfdi else "",
                cfdi.moneda or "" if cfdi else "",
                traslados_str,
                retenciones_str,
                (
                    format_datetime(cfdi.fecha_timbrado)
                    if cfdi and cfdi.fecha_timbrado
                    else ""
                ),
                cfdi.total_impuestos_trasladados or "" if cfdi else "",
                cfdi_uuid_manual,
                cfdi_vinculado,
                format_datetime(expense.created_at),
                format_datetime(expense.updated_at),
            ]
        )

    output.seek(0)
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=expenses.csv"},
    )


@router.get("/admin/gastos/invoices/export")
async def export_invoices(
    session: AsyncSession = Depends(get_db_session),
    nova_request_id: Optional[str] = Query(None),
    estado_factura: Optional[str] = Query(None),
    has_error: Optional[bool] = Query(None),
    created_from: Optional[str] = Query(None),
    created_to: Optional[str] = Query(None),
) -> Response:
    """Export invoices to CSV."""

    conditions = build_invoice_filters(
        nova_request_id=nova_request_id,
        estado_factura=estado_factura,
        has_error=has_error,
        created_from=created_from,
        created_to=created_to,
    )

    query = select(InvoiceReport)
    if conditions:
        query = query.where(and_(*conditions))
    query = query.order_by(InvoiceReport.created_at.desc())

    result = await session.execute(query)
    invoices = result.scalars().all()

    # Get CFDI data for all invoices (join by nova_request_id)
    cfdi_map = {}
    if invoices:
        nova_ids = [i.nova_request_id for i in invoices if i.nova_request_id]
        if nova_ids:
            cfdi_result = await session.execute(
                select(CFDIReport).where(CFDIReport.nova_request_id.in_(nova_ids))
            )
            cfdi_records = cfdi_result.scalars().all()
            cfdi_map = {c.nova_request_id: c for c in cfdi_records}

    # Helper function to format impuestos
    def format_impuestos(impuestos_detalle):
        """Format impuestos for CSV display."""
        if not impuestos_detalle or not isinstance(impuestos_detalle, dict):
            return "", ""
        traslados = impuestos_detalle.get("traslados", [])
        retenciones = impuestos_detalle.get("retenciones", [])

        # Format traslados
        traslados_str = "; ".join(
            [
                f"{t.get('impuesto', '')} {t.get('tasa_o_cuota', 0)*100:.2f}% (Base: {t.get('base', 0):.2f}, Importe: {t.get('importe', 0):.2f})"
                for t in traslados
            ]
        )

        # Format retenciones
        retenciones_str = "; ".join(
            [f"{r.get('impuesto', '')}: {r.get('importe', 0):.2f}" for r in retenciones]
        )

        return traslados_str, retenciones_str

    # Create CSV
    output = io.StringIO()
    writer = csv.writer(output)

    # Write header (expanded with CFDI fields in Excel order)
    writer.writerow(
        [
            "ID",
            "Expense ID",
            "Nova Request ID",
            "Estado Factura",
            "Link PDF",
            "Link XML",
            "Mensaje Error",
            # CFDI fields (Excel order)
            "CFDI Fecha",
            "CFDI Emisor RFC",
            "CFDI Receptor RFC",
            "CFDI Total",
            "CFDI UUID",
            "CFDI Tipo Cambio",
            "CFDI Emisor Nombre",
            "CFDI Descripcion Concepto",
            "CFDI Serie",
            "CFDI Folio",
            "CFDI Subtotal",
            "CFDI Descuento",
            "CFDI Moneda",
            "CFDI Traslados (IVA)",
            "CFDI Retenciones",
            "CFDI Fecha Timbrado",
            "CFDI Total Impuestos",
            "Created At",
            "Updated At",
        ]
    )

    # Write data
    for invoice in invoices:
        cfdi = (
            cfdi_map.get(invoice.nova_request_id) if invoice.nova_request_id else None
        )
        traslados_str, retenciones_str = (
            format_impuestos(cfdi.impuestos_detalle)
            if cfdi and cfdi.impuestos_detalle
            else ("", "")
        )

        writer.writerow(
            [
                str(invoice.id),
                str(invoice.expense_id) if invoice.expense_id else "",
                invoice.nova_request_id or "",
                invoice.estado_factura or "",
                invoice.link_pdf or "",
                invoice.link_xml or "",
                invoice.mensaje_error or "",
                # CFDI fields (Excel order)
                format_datetime(cfdi.fecha) if cfdi and cfdi.fecha else "",
                cfdi.emisor_rfc or "" if cfdi else "",
                cfdi.receptor_rfc or "" if cfdi else "",
                cfdi.total or "" if cfdi else "",
                cfdi.cfdi_uuid or "" if cfdi else "",
                cfdi.tipo_cambio or "" if cfdi else "",
                cfdi.emisor_nombre or "" if cfdi else "",
                cfdi.descripcion_concepto_principal or "" if cfdi else "",
                cfdi.serie or "" if cfdi else "",
                cfdi.folio or "" if cfdi else "",
                cfdi.subtotal or "" if cfdi else "",
                cfdi.descuento or "" if cfdi else "",
                cfdi.moneda or "" if cfdi else "",
                traslados_str,
                retenciones_str,
                (
                    format_datetime(cfdi.fecha_timbrado)
                    if cfdi and cfdi.fecha_timbrado
                    else ""
                ),
                cfdi.total_impuestos_trasladados or "" if cfdi else "",
                format_datetime(invoice.created_at),
                format_datetime(invoice.updated_at),
            ]
        )

    output.seek(0)
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=invoices.csv"},
    )


# ============================================================================
# Tournament Management Routes
# ============================================================================


def _sports_card(label: str, value: Any, note: str = "") -> str:
    return f"""
        <article style="border:1px solid #dbe2ea;border-radius:18px;background:#fff;padding:16px;box-shadow:0 10px 24px rgba(15,23,42,.04);">
            <div style="font-size:11px;text-transform:uppercase;letter-spacing:.12em;color:#64748b;">{escape(label)}</div>
            <div style="margin-top:8px;font-size:28px;font-weight:900;color:#0f172a;">{escape(str(value))}</div>
            <div style="margin-top:6px;color:#64748b;font-size:12px;line-height:1.45;">{escape(note)}</div>
        </article>
    """


def _retention_account_selector_html(
    *,
    gasto_id: UUIDType,
    retention: Dict[str, Any],
    current_retention_accounts: Dict[str, Any],
) -> str:
    impuesto_code = str(retention.get("impuesto") or "").strip()
    account = retention.get("account") or {}
    current_account_id = str(
        current_retention_accounts.get(impuesto_code)
        or account.get("cuenta_contable_id")
        or ""
    )
    current_account_code = str(account.get("codigo") or "").strip()
    current_account_name = str(account.get("nombre") or "").strip()
    current_label = (
        f"{current_account_code} - {current_account_name}".strip(" -")
        if current_account_code or current_account_name
        else ""
    )
    impuesto_label = str(
        retention.get("label") or f"Retención {impuesto_code or 'impuesto'}"
    ).strip()
    return f"""
        <div class="cuenta-selector">
            <div style="font-size:11px; font-weight:700; color:#0f172a; margin-bottom:4px;">Cuenta {escape(impuesto_label)}</div>
            <input type="text"
                   class="account-search"
                   data-gasto-id="{gasto_id}"
                   data-target="retention"
                   data-existing-id="{escape(current_account_id)}"
                   placeholder="Buscar cuenta de retención..."
                   value="{escape(current_label)}"
                   autocomplete="off"
                   style="width: 100%; padding: 8px; border: 1px solid #ddd; border-radius: 4px;">
            <div class="account-results" style="display: none; position: absolute; background: white; border: 1px solid #ddd; max-height: 200px; overflow-y: auto; z-index: 1000; width: 100%; box-shadow: 0 2px 8px rgba(0,0,0,0.15);"></div>
            <input type="hidden" class="retention-cuenta-id" data-gasto-id="{gasto_id}" data-impuesto="{escape(impuesto_code)}" name="retention_account_{escape(impuesto_code)}" value="{escape(current_account_id)}">
        </div>
    """


def _sports_list(items: list[Any], empty: str) -> str:
    if not items:
        return f'<div style="color:#64748b;">{escape(empty)}</div>'
    return "".join(
        f'<div style="padding:10px 0;border-bottom:1px solid #eef2f7;color:#334155;">{escape(str(item))}</div>'
        for item in items[:8]
    )


def _encode_sat_result_payload(payload: Dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=True, default=str).encode("utf-8")
    return urlsafe_b64encode(raw).decode("ascii")


def _decode_sat_result_payload(raw: str) -> Optional[Dict[str, Any]]:
    value = (raw or "").strip()
    if not value:
        return None
    try:
        padded = value + "=" * (-len(value) % 4)
        decoded = urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        parsed = json.loads(decoded)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


def _sat_redirect_url(
    *,
    success_msg: Optional[str] = None,
    error_msg: Optional[str] = None,
    current_result: Optional[Dict[str, Any]] = None,
    cfdi_status_result: Optional[Dict[str, Any]] = None,
    sync_result: Optional[str] = None,
) -> str:
    params: List[str] = []
    if success_msg:
        params.append(f"success_msg={quote(success_msg)}")
    if error_msg:
        params.append(f"error_msg={quote(error_msg)}")
    if current_result:
        params.append(
            f"sat_result={quote(_encode_sat_result_payload(current_result))}"
        )
    if cfdi_status_result:
        params.append(
            "cfdi_status_result="
            f"{quote(_encode_sat_result_payload(cfdi_status_result))}"
        )
    if sync_result:
        params.append(f"sync_result={quote(sync_result)}")
    suffix = f"?{'&'.join(params)}" if params else ""
    return f"/admin/gastos/sat{suffix}"


def _sat_sync_run_trigger_label(trigger_source: Optional[str]) -> str:
    mapping = {
        "cron": "Cron programado",
        "admin": "Admin UI",
        "api": "API",
    }
    return mapping.get((trigger_source or "").strip().lower(), trigger_source or "—")


def _sat_sync_run_status_style(status: Optional[str]) -> str:
    normalized = (status or "").strip().lower()
    if normalized == "success":
        return "background:#dcfce7;color:#166534;"
    if normalized in {"error", "quota_blocked"}:
        return "background:#fee2e2;color:#991b1b;"
    if normalized in {"processing", "skipped", "running"}:
        return "background:#fef3c7;color:#92400e;"
    return "background:#e2e8f0;color:#334155;"


def _sat_sync_run_ingested_total(results: Optional[Any]) -> int:
    if isinstance(results, dict):
        return int(results.get("ingested_cfdis") or 0)
    if not isinstance(results, list):
        return 0
    total = 0
    for row in results:
        if isinstance(row, dict):
            total += int(row.get("ingested_cfdis") or 0)
    return total


def _sat_job_detail_url(run_id: Any) -> str:
    return f"/admin/gastos/sat/jobs/{run_id}"


def _sat_job_is_active(status: Optional[str]) -> bool:
    return (status or "").strip().lower() == "running"


def _format_sat_daily_sla_rows(rows: List[Any]) -> str:
    if not rows:
        return (
            '<tr><td colspan="5" style="text-align:center;padding:24px;">'
            "Sin datos de cobertura SAT en el rango seleccionado."
            "</td></tr>"
        )
    html = ""
    for row in rows:
        day_label = escape(row.day.strftime("%Y-%m-%d"))
        rec = "✓" if row.received_covered else "—"
        issued = "✓" if row.issued_covered else "—"
        status = escape(str(row.status or "—"))
        html += f"""
        <tr>
            <td><strong>{day_label}</strong></td>
            <td>{int(row.cfdis_in_db)}</td>
            <td>{rec}</td>
            <td>{issued}</td>
            <td><span style="display:inline-flex;padding:4px 8px;border-radius:999px;font-size:11px;font-weight:800;{row.status_style}">{status}</span></td>
        </tr>
        """
    return html


def _format_sat_open_solicitud_rows(rows: List[Any]) -> str:
    if not rows:
        return (
            '<tr><td colspan="9" style="text-align:center;padding:24px;">'
            "No hay solicitudes SAT abiertas. Las descargas completadas desaparecen de esta bandeja."
            "</td></tr>"
        )
    html = ""
    for row in rows:
        quota_note = " (cuota)" if row.quota_blocked else ""
        verified = (
            row.last_verified_at.strftime("%Y-%m-%d %H:%M UTC")
            if row.last_verified_at
            else "—"
        )
        sat_num = "—" if row.sat_num_cfdis is None else str(int(row.sat_num_cfdis))
        html += f"""
        <tr>
            <td><code>{escape(row.rfc)}</code></td>
            <td>{escape(row.direction)}{quota_note}</td>
            <td><code>{escape(row.solicitud_id)}</code></td>
            <td>{escape(row.estado_sat)}</td>
            <td>{int(row.age_hours)}h</td>
            <td><code>{escape(row.fecha_inicial.strftime('%Y-%m-%d'))}</code> → <code>{escape(row.fecha_final.strftime('%Y-%m-%d'))}</code></td>
            <td>{sat_num}</td>
            <td>{int(row.ingested_cfdis)}</td>
            <td title="{escape(row.last_error_message)}">{escape(verified)}</td>
        </tr>
        """
    return html


def _format_sat_direction_health_rows(summaries: List[Any]) -> str:
    if not summaries:
        return (
            '<tr><td colspan="6" style="text-align:center;padding:24px;">'
            "Sin estado de sync persistido todavía."
            "</td></tr>"
        )
    html = ""
    for item in summaries:
        quota = "—"
        if item.quota_blocked and item.quota_blocked_until:
            quota = item.quota_blocked_until.strftime("%Y-%m-%d %H:%M UTC")
        elif item.quota_blocked:
            quota = "Activa"
        cursor = (
            item.cursor_fecha_emision.strftime("%Y-%m-%d %H:%M")
            if item.cursor_fecha_emision
            else "—"
        )
        last_ok = (
            item.last_successful_sync_at.strftime("%Y-%m-%d %H:%M UTC")
            if item.last_successful_sync_at
            else "—"
        )
        html += f"""
        <tr>
            <td><code>{escape(item.rfc)}</code></td>
            <td>{escape(item.direction)}</td>
            <td>{int(item.open_jobs)}</td>
            <td>{escape(quota)}</td>
            <td>{escape(cursor)}</td>
            <td>{escape(last_ok)}</td>
        </tr>
        """
    return html


def _format_sat_daily_coverage_rows(rows: List[Any]) -> str:
    if not rows:
        return (
            '<tr><td colspan="4" style="text-align:center;padding:24px;">'
            "Sin CFDIs SAT con fecha de emisión en el rango seleccionado. "
            "Ejecuta Backfill o Actualizar para iniciar la descarga."
            "</td></tr>"
        )
    html = ""
    for row in rows:
        day_label = escape(row.day.strftime("%Y-%m-%d"))
        status = escape(str(row.status or "—"))
        status_style = row.status_style
        html += f"""
        <tr>
            <td><strong>{day_label}</strong></td>
            <td>{int(row.sat_emission_count)}</td>
            <td>{int(row.ingested_emission_count)}</td>
            <td><span style="display:inline-flex;padding:4px 8px;border-radius:999px;font-size:11px;font-weight:800;{status_style}">{status}</span></td>
        </tr>
        """
    return html


def _format_sat_sync_run_history_rows(runs: List[Any]) -> str:
    if not runs:
        return (
            '<tr><td colspan="9" style="text-align:center;padding:24px;">'
            "Sin ejecuciones registradas todavía. Las corridas del cron y los botones "
            "Backfill/Actualizar aparecerán aquí."
            "</td></tr>"
        )
    rows_html = ""
    for run in runs:
        started = (
            run.started_at.strftime("%Y-%m-%d %H:%M UTC")
            if getattr(run, "started_at", None)
            else "—"
        )
        finished = (
            run.finished_at.strftime("%Y-%m-%d %H:%M UTC")
            if getattr(run, "finished_at", None)
            else "—"
        )
        duration = "—"
        if getattr(run, "started_at", None) and getattr(run, "finished_at", None):
            seconds = int((run.finished_at - run.started_at).total_seconds())
            if seconds >= 0:
                duration = f"{seconds}s"
        ingested = _sat_sync_run_ingested_total(getattr(run, "results", None))
        status = escape(str(getattr(run, "status", "") or "—"))
        status_style = _sat_sync_run_status_style(getattr(run, "status", None))
        summary = escape(str(getattr(run, "summary_message", "") or ""))
        run_id = getattr(run, "id", None)
        detail_link = (
            f'<a href="{escape(_sat_job_detail_url(run_id))}">Ver job</a>'
            if run_id
            else "—"
        )
        rows_html += f"""
        <tr>
            <td>{escape(started)}</td>
            <td>{escape(finished)}</td>
            <td>{escape(duration)}</td>
            <td>{escape(str(getattr(run, "mode", "") or "—"))}</td>
            <td>{escape(_sat_sync_run_trigger_label(getattr(run, "trigger_source", None)))}</td>
            <td><span style="display:inline-flex;padding:4px 8px;border-radius:999px;font-size:11px;font-weight:800;{status_style}">{status}</span></td>
            <td>{ingested}</td>
            <td title="{summary}">{summary[:120] + ("…" if len(summary) > 120 else "")}</td>
            <td>{detail_link}</td>
        </tr>
        """
    return rows_html


def _format_sat_status_result(result: Optional[Dict[str, Any]]) -> str:
    if not result:
        return ""
    request_data = result.get("request") or {}
    status = escape(str(result.get("status") or "error"))
    estado = escape(str(result.get("estado") or ""))
    codigo = escape(str(result.get("codigo_estatus") or ""))
    cancelable = escape(str(result.get("es_cancelable") or ""))
    cancelacion = escape(str(result.get("estatus_cancelacion") or ""))
    uuid = escape(str(request_data.get("uuid") or ""))
    rfc_emisor = escape(str(request_data.get("rfc_emisor") or ""))
    rfc_receptor = escape(str(request_data.get("rfc_receptor") or ""))
    total = escape(str(request_data.get("total") or ""))
    return f"""
        <section class="surface">
            <div class="section-head">
                <div>
                    <div class="eyebrow">Resultado SAT</div>
                    <h2>Consulta CFDI</h2>
                    <div class="section-note">
                        Resultado normalizado de la consulta de estatus CFDI.
                    </div>
                </div>
            </div>
            <div class="meta-grid">
                <div class="meta-card"><span>Estatus</span><strong>{status}</strong><small>{estado or codigo}</small></div>
                <div class="meta-card"><span>UUID</span><strong style="font-size:1rem;">{uuid or "—"}</strong><small>Folio fiscal consultado.</small></div>
                <div class="meta-card"><span>Emisor</span><strong>{rfc_emisor or "—"}</strong><small>RFC emisor.</small></div>
                <div class="meta-card"><span>Receptor</span><strong>{rfc_receptor or "—"}</strong><small>RFC receptor.</small></div>
                <div class="meta-card"><span>Total</span><strong>{total or "—"}</strong><small>Total consultado.</small></div>
                <div class="meta-card"><span>Cancelable</span><strong>{cancelable or "—"}</strong><small>{cancelacion or "Sin dato de cancelación."}</small></div>
            </div>
        </section>
    """


def _sat_catalogs_html() -> str:
    catalogs = list_sat_catalogs()
    sections = []
    labels = {
        "uso_cfdi": "Uso CFDI",
        "regimen_fiscal": "Régimen fiscal",
        "forma_pago": "Forma de pago",
        "metodo_pago": "Método de pago",
    }
    for key, items in catalogs.items():
        rows = render_catalog_preview_rows(items)
        sections.append(
            f"""
            <div class="meta-card" style="overflow:auto;">
                <span>{escape(labels.get(key, key))}</span>
                <table style="margin-top:10px;">
                    <tbody>{rows}</tbody>
                </table>
            </div>
            """
        )
    return "".join(sections)


def _parse_sat_form_date(raw: str, *, end_of_day: bool = False) -> datetime:
    value = (raw or "").strip()
    if not value:
        raise ValueError("Fecha requerida")
    parsed = datetime.strptime(value, "%Y-%m-%d")
    if end_of_day:
        return parsed.replace(hour=23, minute=59, second=59)
    return parsed


_SAT_EFIRMA_MAX_FILE_BYTES = 1024 * 1024
_SAT_CERTIFICATE_EXTENSIONS = {".cer", ".cert"}
_SAT_PRIVATE_KEY_EXTENSIONS = {".key"}
_SAT_EFIRMA_VALIDATION_ERROR_PREFIX = (
    "No se pudieron guardar las credenciales SAT"
)


def _validate_sat_upload_filename(
    upload: UploadFile,
    *,
    allowed_extensions: set[str],
    label: str,
) -> None:
    filename = (getattr(upload, "filename", "") or "").strip()
    suffix = Path(filename).suffix.lower()
    if not filename or suffix not in allowed_extensions:
        expected = ", ".join(sorted(allowed_extensions))
        raise ValueError(f"{label} debe tener extensión {expected}.")


async def _read_sat_efirma_upload(
    upload: UploadFile,
    *,
    allowed_extensions: set[str],
    label: str,
) -> bytes:
    _validate_sat_upload_filename(
        upload,
        allowed_extensions=allowed_extensions,
        label=label,
    )
    contents = await upload.read()
    if not contents:
        raise ValueError(f"{label} no puede estar vacío.")
    if len(contents) > _SAT_EFIRMA_MAX_FILE_BYTES:
        raise ValueError(f"{label} excede el tamaño máximo de 1 MB.")
    return contents


@router.get("/admin/sports", response_class=HTMLResponse)
async def admin_sports_platform(
    request: Request,
    current_empleado: Empleado = Depends(get_current_empleado),
    tournament_key: str = Query("all"),
    tournament_slug: Optional[str] = Query(None),
    team_id: Optional[str] = Query(None),
    match_id: Optional[str] = Query(None),
):
    """Sports platform command center over the canonical tournament snapshot."""
    from samchat.sports_platform import build_sports_platform_snapshot
    from samchat.tournaments_v2.services import build_tournament_soul_snapshot

    error_html = ""
    snapshot: dict[str, Any] = {}
    platform: dict[str, Any] = {"ok": False}
    try:
        snapshot = await build_tournament_soul_snapshot(
            tournament_key=tournament_key,
            tournament_slug=tournament_slug,
            include_communications=True,
            include_media=True,
            limit=250,
        )
        platform = build_sports_platform_snapshot(snapshot)
    except Exception as exc:
        error_html = f"""
            <div style="margin:16px 0;padding:14px;border:1px solid #fecaca;border-radius:14px;background:#fef2f2;color:#991b1b;">
                No se pudo construir Sports Platform: {escape(str(exc)[:220])}
            </div>
        """

    command_center = platform.get("command_center") or {}
    mission_control = platform.get("mission_control") or {}
    team_journey = platform.get("team_journey") or {}
    match_center = platform.get("match_center") or {}
    team_portal = platform.get("team_portal") or {}
    roster = platform.get("roster_intelligence") or {}
    matchday = platform.get("matchday_ops") or {}
    communications = platform.get("communications") or {}
    risk_radar = platform.get("risk_radar") or {}
    sports_crm = platform.get("sports_crm") or {}
    public_layer = platform.get("public_layer") or {}
    mobile_app = platform.get("mobile_field_app") or {}
    assistant = platform.get("ai_ops_assistant") or {}
    action_queue = platform.get("action_queue") or {}
    ops_brief = platform.get("ops_brief") or {}
    global_readiness = platform.get("global_readiness") or {}
    ops_copilot = platform.get("ops_copilot") or {}
    public_microsite = platform.get("public_microsite") or {}
    sponsor_media = platform.get("sponsor_media") or {}
    incident_center = platform.get("incident_center") or {}
    venue_ops = platform.get("venue_ops") or {}
    post_tournament_report = platform.get("post_tournament_report") or {}
    summary = platform.get("summary") or {}
    tournament = command_center.get("tournament") or {}
    teams = list(team_portal.get("teams") or [])
    next_matches = list(
        matchday.get("next_matches") or command_center.get("next_matches") or []
    )
    risks = list(risk_radar.get("risks") or [])
    crm_entities = list(sports_crm.get("entities") or [])
    prompts = list(assistant.get("suggested_prompts") or [])
    team_journeys = list(team_journey.get("teams") or [])
    match_center_rows = list(match_center.get("matches") or [])
    active_team = next(
        (
            item
            for item in team_journeys
            if str(item.get("team_id") or "") == str(team_id or "")
        ),
        team_journeys[0] if team_journeys else {},
    )
    active_match = next(
        (
            item
            for item in match_center_rows
            if str(item.get("id") or "") == str(match_id or "")
        ),
        match_center_rows[0] if match_center_rows else {},
    )

    team_rows = "".join(
        f"""
        <tr>
            <td>{escape(str(team.get("team_name") or "-"))}</td>
            <td>{escape(str(team.get("entity_name") or "-"))}</td>
            <td>{escape(str(team.get("category") or "-"))}</td>
            <td>{int(team.get("players_count") or 0)}</td>
            <td>{float(team.get("document_completion_rate") or 0) * 100:.1f}%</td>
            <td>{escape(str(team.get("status") or "-"))}</td>
        </tr>
        """
        for team in teams[:12]
    )
    match_rows = "".join(
        f"""
        <tr>
            <td>{escape(str(match.get("match_date") or "-"))}</td>
            <td>{escape(str(match.get("phase") or "-"))}</td>
            <td>{escape(str(match.get("field_number") or "-"))}</td>
            <td>{escape(str(match.get("status") or "-"))}</td>
            <td>{escape(str(match.get("cedula_status") or "-"))}</td>
        </tr>
        """
        for match in next_matches[:12]
    )
    risk_rows = "".join(
        f"""
        <div style="padding:12px;border:1px solid #fed7aa;border-radius:14px;background:#fff7ed;">
            <div style="font-size:11px;text-transform:uppercase;letter-spacing:.12em;color:#9a3412;">{escape(str(risk.get("severity") or "risk"))}</div>
            <div style="margin-top:6px;font-weight:800;color:#7c2d12;">{escape(str(risk.get("code") or "risk"))}</div>
            <div style="margin-top:4px;color:#475569;">{escape(str(risk.get("message") or ""))}</div>
        </div>
        """
        for risk in risks[:8]
    )
    crm_rows = "".join(
        f"""
        <tr>
            <td>{escape(str(entity.get("entity_name") or "-"))}</td>
            <td>{int(entity.get("teams_count") or 0)}</td>
            <td>{int(entity.get("players_count") or 0)}</td>
        </tr>
        """
        for entity in crm_entities[:12]
    )
    mission_items = "".join(
        f"""
        <div style="padding:12px;border:1px solid #dbe2ea;border-radius:14px;background:#fff;">
            <div style="font-weight:800;color:#0f172a;">{escape(str(item))}</div>
        </div>
        """
        for item in list(mission_control.get("today_plan") or [])[:8]
    )
    team_journey_rows = "".join(
        f"""
        <tr>
            <td><a href="/admin/sports?tournament_key={quote(str(tournament_key or 'all'))}&tournament_slug={quote(str(tournament_slug or ''))}&team_id={quote(str(journey.get('team_id') or ''))}" style="color:#0f172a;font-weight:800;text-decoration:none;">{escape(str(journey.get("team_name") or "-"))}</a></td>
            <td>{escape(str(journey.get("entity_name") or "-"))}</td>
            <td>{int((journey.get("readiness") or {}).get("score") or 0)}</td>
            <td>{escape(str((journey.get("readiness") or {}).get("status") or "-"))}</td>
            <td>{len(journey.get("next_actions") or [])}</td>
        </tr>
        """
        for journey in team_journeys[:12]
    )
    active_team_actions = _sports_list(
        list(active_team.get("next_actions") or []),
        "Sin acciones para el equipo.",
    )
    match_center_table_rows = "".join(
        f"""
        <tr>
            <td><a href="/admin/sports?tournament_key={quote(str(tournament_key or 'all'))}&tournament_slug={quote(str(tournament_slug or ''))}&match_id={quote(str(match.get('id') or ''))}" style="color:#0f172a;font-weight:800;text-decoration:none;">{escape(str(match.get("match_date") or "-"))}</a></td>
            <td>{escape(str(match.get("home_team_name") or "-"))}</td>
            <td>{escape(str(match.get("away_team_name") or "-"))}</td>
            <td>{escape(str(match.get("field_number") or "-"))}</td>
            <td>{escape(str(match.get("match_status") or "-"))}</td>
            <td>{escape(str(match.get("cedula_status") or "-"))}</td>
        </tr>
        """
        for match in match_center_rows[:12]
    )
    active_match_checklist = _sports_list(
        list(active_match.get("field_checklist") or []),
        "Sin checklist para el partido.",
    )
    ops_draft_rows = "".join(
        f"""
        <div style="padding:12px;border:1px solid #dbe2ea;border-radius:14px;background:#fff;">
            <div style="font-size:11px;text-transform:uppercase;letter-spacing:.12em;color:#64748b;">{escape(str(item.get("label") or "Draft"))}</div>
            <div style="margin-top:6px;color:#334155;line-height:1.5;">{escape(str(item.get("copy") or ""))}</div>
        </div>
        """
        for item in list(ops_copilot.get("drafts") or [])[:4]
    )
    microsite_sections = "".join(
        f'<span class="sports-chip" style="background:{"#dcfce7" if section.get("ready") else "#fef3c7"};color:{"#166534" if section.get("ready") else "#92400e"};">{escape(str(section.get("label") or ""))}</span>'
        for section in list(public_microsite.get("sections") or [])
    )
    sponsor_points = "".join(
        _sports_card(
            str(point.get("label") or "-"),
            point.get("value", 0),
            "Sponsor proof point",
        )
        for point in list(sponsor_media.get("proof_points") or [])[:6]
    )
    incident_rows = "".join(
        f"""
        <tr>
            <td>{escape(str(item.get("type") or "-"))}</td>
            <td>{escape(str(item.get("severity") or "-"))}</td>
            <td>{escape(str(item.get("message") or "-"))}</td>
            <td>{escape(str(item.get("source") or "-"))}</td>
        </tr>
        """
        for item in list(incident_center.get("incidents") or [])[:10]
    )
    venue_rows = "".join(
        f"""
        <tr>
            <td>{escape(str(item.get("venue") or "-"))}</td>
            <td>{int(item.get("matches_count") or 0)}</td>
            <td>{int(item.get("open_matches") or 0)}</td>
            <td>{escape(", ".join(item.get("phases") or []))}</td>
        </tr>
        """
        for item in list(venue_ops.get("venues") or [])[:10]
    )
    action_rows = "".join(
        f"""
        <tr>
            <td>{escape(str(item.get("severity") or "-"))}</td>
            <td>{escape(str(item.get("title") or "-"))}</td>
            <td>{escape(str(item.get("module") or "-"))}</td>
            <td>{escape(str(item.get("owner") or "-"))}</td>
            <td>{escape(str(item.get("due") or "-"))}</td>
            <td>{escape(str(item.get("detail") or "-"))}</td>
        </tr>
        """
        for item in list(action_queue.get("actions") or [])[:15]
    )
    brief_text = escape(str(ops_brief.get("plain_text") or "Sin briefing disponible."))
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>Sports Platform - Samchat</title>
        <style>
            {_admin_workspace_styles("1380px")}
            .sports-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:14px; }}
            .sports-section-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(320px,1fr)); gap:16px; }}
            .sports-table {{ width:100%; border-collapse:separate; border-spacing:0; background:#fff; }}
            .sports-table th, .sports-table td {{ text-align:left; padding:12px; border-bottom:1px solid #e2e8f0; }}
            .sports-table th {{ color:#475569; font-size:11px; text-transform:uppercase; letter-spacing:.11em; background:#f1f5f9; font-weight:800; }}
            .sports-table td {{ color:#334155; background:#fff; font-size:14px; line-height:1.45; }}
            .sports-table tbody tr:nth-child(even) td {{ background:#f8fafc; }}
            .sports-table tbody tr:hover td {{ background:#eef2f7; }}
            .sports-chip {{ display:inline-flex; padding:6px 10px; border-radius:999px; background:#ecfeff; color:#155e75; font-size:12px; font-weight:800; }}
            .sports-brief {{
                white-space:pre-wrap;
                margin-top:14px;
                padding:16px;
                border:1px solid var(--shell-line);
                border-radius:16px;
                background:#f8fafc;
                color:#334155;
                font-size:13px;
                line-height:1.55;
                font-family:inherit;
            }}
            input {{ width:100%; padding:10px 12px; border-radius:12px; border:1px solid #cbd5e1; background:#fff; color:var(--shell-ink); }}
        </style>
    </head>
    <body>
        <div class="workspace-shell">
            {render_admin_navigation(current_empleado, "sports", subtitle="Sports Platform: operación deportiva viva sobre el snapshot canónico.")}
            {_render_admin_workspace_hero(
                eyebrow="Sports Platform",
                title="Command Center deportivo",
                description="Una capa operativa para equipos, roster, matchday, comunicación, riesgos, CRM, capa pública, móvil y AI Ops sin duplicar la fuente de verdad.",
                actions_html=(
                    '<form method="GET" action="/admin/sports" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px;align-items:end;">'
                    f'<div><label style="font-size:12px;font-weight:800;color:#475569;">Scope</label><input name="tournament_key" value="{escape(str(tournament_key or "all"))}" placeholder="all, beisbol"></div>'
                    f'<div><label style="font-size:12px;font-weight:800;color:#475569;">Slug</label><input name="tournament_slug" value="{escape(str(tournament_slug or ""))}" placeholder="liga-telmex..."></div>'
                    f'<div><label style="font-size:12px;font-weight:800;color:#475569;">Team</label><input name="team_id" value="{escape(str(team_id or ""))}" placeholder="team-id"></div>'
                    f'<div><label style="font-size:12px;font-weight:800;color:#475569;">Match</label><input name="match_id" value="{escape(str(match_id or ""))}" placeholder="match-id"></div>'
                    '<button class="button" type="submit">Actualizar Sports</button>'
                    '<a class="button secondary" href="/admin/sports/expediente-entidades">Expediente DG</a>'
                    '</form>'
                ),
                side_html=(
                    f'<div class="eyebrow">Torneo activo</div>'
                    f'<div style="font-size:1.3rem;font-weight:900;color:#0f172a;">{escape(str(tournament.get("name") or "Sin torneo cargado"))}</div>'
                    f'<div style="margin-top:8px;color:#64748b;">{escape(str(tournament.get("slug") or tournament_slug or tournament_key))}</div>'
                    '<div style="margin-top:12px;"><span class="sports-chip">read-only</span></div>'
                ),
            )}
            {error_html}
            <section class="workspace-card" style="margin-bottom:18px;">
                <div class="workspace-section-title">Action Queue</div>
                <div class="workspace-section-subtitle">Cola única de trabajo: documentos, cédulas, comunicación, incidentes y sedes con responsable, vencimiento y severidad.</div>
                <div class="sports-grid" style="margin-top:14px;">
                    {_sports_card("Abiertas", action_queue.get("open_count", 0), "Acciones por cerrar")}
                    {_sports_card("Alta", action_queue.get("high_count", 0), "Severidad high")}
                    {_sports_card("Media", action_queue.get("medium_count", 0), "Severidad medium")}
                    {_sports_card("Baja", action_queue.get("low_count", 0), "Severidad low")}
                </div>
                <table class="sports-table" style="margin-top:16px;">
                    <thead><tr><th>Sev</th><th>Acción</th><th>Módulo</th><th>Responsable</th><th>Vence</th><th>Detalle</th></tr></thead>
                    <tbody>{action_rows or '<tr><td colspan="6">Sin acciones abiertas.</td></tr>'}</tbody>
                </table>
            </section>
            <section class="workspace-card" style="margin-bottom:18px;">
                <div class="workspace-section-title">One-click Ops Brief</div>
                <div class="workspace-section-subtitle">Briefing operativo listo para WhatsApp, email o PDF.</div>
                <div class="sports-grid" style="margin-top:14px;">
                    {_sports_card("WhatsApp", "listo", str(ops_brief.get("whatsapp_text") or "")[:90])}
                    {_sports_card("Email", "listo", str(ops_brief.get("email_subject") or "-"))}
                    {_sports_card("PDF", "ready" if ops_brief.get("pdf_ready") else "draft", ", ".join(ops_brief.get("export_targets") or []))}
                </div>
                <pre class="sports-brief">{brief_text}</pre>
            </section>
            <section class="workspace-card" style="margin-bottom:18px;">
                <div class="workspace-section-title">Sports Mission Control</div>
                <div class="workspace-section-subtitle">Qué resolver hoy: documentos, cédulas, comunicación, riesgos y briefing operativo.</div>
                <div class="sports-grid" style="margin-top:14px;">
                    {_sports_card("Equipos bloqueados", len(mission_control.get("blocked_teams") or []), "No deberían llegar a cancha sin acción")}
                    {_sports_card("Equipos en riesgo", len(mission_control.get("risk_teams") or []), "Requieren seguimiento")}
                    {_sports_card("Partidos abiertos", len(mission_control.get("open_matches") or []), "Cédula/preparación pendiente")}
                    {_sports_card("Briefing", "listo", str(mission_control.get("ops_brief") or "")[:120])}
                </div>
                <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px;margin-top:14px;">
                    {mission_items or '<div style="color:#64748b;">Sin acciones críticas para hoy.</div>'}
                </div>
            </section>
            <section class="workspace-card" style="margin-bottom:18px;">
                <div class="workspace-section-title">Tournament Command Center</div>
                <div class="workspace-section-subtitle">Pulso operativo: equipos, jugadores, calendario, documentos, comunicación y riesgos.</div>
                <div class="sports-grid" style="margin-top:14px;">
                    {_sports_card("Equipos", summary.get("teams", 0), "Equipos registrados")}
                    {_sports_card("Jugadores", summary.get("players", 0), "Roster total")}
                    {_sports_card("Partidos", summary.get("matches", 0), "Matchday activo")}
                    {_sports_card("Riesgos", summary.get("risk_count", 0), "Risk Radar")}
                    {_sports_card("Equipos con acción", summary.get("team_actions", 0), "Portal de equipos")}
                    {_sports_card("WhatsApp", communications.get("whatsapp_unread", 0), "Mensajes sin leer")}
                </div>
            </section>
            <section class="sports-section-grid" style="margin-bottom:18px;">
                <div class="workspace-card">
                    <div class="workspace-section-title">Risk Radar</div>
                    <div class="workspace-section-subtitle">Alertas accionables antes de que exploten en cancha.</div>
                    <div style="display:grid;gap:10px;margin-top:12px;">{risk_rows or '<div style="color:#64748b;">Sin riesgos activos.</div>'}</div>
                </div>
                <div class="workspace-card">
                    <div class="workspace-section-title">AI Ops Assistant</div>
                    <div class="workspace-section-subtitle">Preguntas rápidas para operar el torneo.</div>
                    <div style="margin-top:12px;">{_sports_list(prompts, "Sin prompts sugeridos.")}</div>
                </div>
            </section>
            <section class="workspace-card" style="margin-bottom:18px;">
                <div class="workspace-section-title">Portal para equipos + Roster inteligente</div>
                <div class="workspace-section-subtitle">Estatus de equipos, responsables, documentos y readiness de roster.</div>
                <div class="sports-grid" style="margin-top:14px;">
                    {_sports_card("Completion roster", f"{float(roster.get("completion_rate") or 0) * 100:.1f}%", "Jugadores con documentos completos")}
                    {_sports_card("Verification", f"{float(roster.get("verification_rate") or 0) * 100:.1f}%", "Jugadores verificados")}
                    {_sports_card("Sin contacto", team_portal.get("missing_contact_count", 0), "Equipos sin responsable primario")}
                    {_sports_card("Acción requerida", team_portal.get("action_needed_count", 0), "Equipos no listos")}
                </div>
                <table class="sports-table" style="margin-top:16px;">
                    <thead><tr><th>Equipo</th><th>Entidad</th><th>Categoría</th><th>Jugadores</th><th>Docs</th><th>Status</th></tr></thead>
                    <tbody>{team_rows or '<tr><td colspan="6">Sin equipos visibles.</td></tr>'}</tbody>
                </table>
            </section>
            <section class="workspace-card" style="margin-bottom:18px;">
                <div class="workspace-section-title">Team Journey</div>
                <div class="workspace-section-subtitle">Ficha 360 por equipo: readiness, roster, contacto, calendario y siguientes acciones.</div>
                <div class="sports-grid" style="margin-top:14px;">
                    {_sports_card("Ready", team_journey.get("ready_count", 0), "Equipos listos")}
                    {_sports_card("Riesgo", team_journey.get("risk_count", 0), "Equipos con pendientes")}
                    {_sports_card("Bloqueado", team_journey.get("blocked_count", 0), "No listos para jugar")}
                    {_sports_card("Equipo activo", active_team.get("team_name") or "-", f'Score {int((active_team.get("readiness") or {}).get("score") or 0)}')}
                </div>
                <div class="sports-section-grid" style="margin-top:16px;">
                    <div>
                        <table class="sports-table">
                            <thead><tr><th>Equipo</th><th>Entidad</th><th>Score</th><th>Status</th><th>Acciones</th></tr></thead>
                            <tbody>{team_journey_rows or '<tr><td colspan="5">Sin journeys visibles.</td></tr>'}</tbody>
                        </table>
                    </div>
                    <div style="border:1px solid #dbe2ea;border-radius:16px;background:#fff;padding:16px;">
                        <div style="font-size:11px;text-transform:uppercase;letter-spacing:.12em;color:#64748b;">Equipo selec…30256 tokens truncated…repolizas_coi_xlsx(
    request: Request,
    current_empleado: Empleado = require_admin_finanzas(),
    session: AsyncSession = Depends(get_db_session),
    edition_year: Optional[int] = Query(None),
    budget_version_id: Optional[str] = Query(None),
    tournament_id: Optional[str] = Query(None),
    tournament_code: Optional[str] = Query(None),
    limit: int = Query(500),
    estado: str = Query("todos"),
    cliente: Optional[str] = Query(None),
    dias_credito: int = Query(0),
    sort_by: str = Query("issued_date"),
    sort_dir: str = Query("desc"),
) -> Response:
    """Download read-only COI workbook for ready CxC policy previews."""
    from samchat.ar import (
        build_ar_operational_rows,
        build_ar_read_model,
        generate_ar_coi_ready_xlsx,
    )

    all_versions = await list_budget_versions(session, ensure_schema=False)
    resolved_year = edition_year
    if resolved_year is None:
        resolved_year = (
            int(all_versions[0]["edition_year"])
            if all_versions
            else date.today().year
        )
    versions = await list_budget_versions(
        session,
        edition_year=resolved_year,
        ensure_schema=False,
    )
    selected_version = None
    if budget_version_id:
        selected_version = next(
            (item for item in versions if item["id"] == budget_version_id),
            None,
        )
    if selected_version is None:
        selected_version = resolve_definitive_budget_version_from_versions(versions)
    if selected_version is None:
        raise HTTPException(
            status_code=404,
            detail="No hay versión presupuestal para exportar prepólizas CxC.",
        )

    safe_limit = max(1, min(int(limit or 500), 5000))
    safe_credit_days = max(0, min(int(dias_credito or 0), 365))
    payload = await build_ar_read_model(
        session,
        budget_version_id=str(selected_version["id"]),
        tournament_id=tournament_id,
        tournament_code=tournament_code,
        limit=safe_limit,
        credit_days_default=safe_credit_days,
        ensure_schema=False,
    )
    rows = build_ar_operational_rows(
        payload,
        status_filter=estado,
        search=cliente or "",
        sort_by=sort_by,
        sort_dir=sort_dir,
    )
    content = generate_ar_coi_ready_xlsx(rows, payload)
    filename = f"prepolizas_cxc_coi_{int(resolved_year)}.xlsx"
    return Response(
        content=content,
        media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/admin/finanzas/export.xlsx", response_class=Response)
async def admin_finance_platform_export_xlsx(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = require_admin_finanzas(),
    year: Optional[int] = Query(None),
    month: Optional[int] = Query(None),
) -> Response:
    """Download the Finance Platform operational workbook."""
    from samchat.finance_platform import (
        build_finance_platform_snapshot,
        build_finance_source_snapshot,
    )
    from samchat.finance_platform.exporter import generate_finance_platform_xlsx

    snapshot = await build_finance_source_snapshot(
        session,
        year=year,
        month=month,
        limit=300,
    )
    platform = build_finance_platform_snapshot(snapshot)
    payload = generate_finance_platform_xlsx(platform=platform)
    period = platform.get("period") or {}
    filename = (
        f"finanzas_{int(period.get('year') or year or datetime.utcnow().year)}_"
        f"{int(period.get('month') or month or datetime.utcnow().month):02d}.xlsx"
    )
    return Response(
        content=payload,
        media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _finance_period_bounds(
    year: Optional[int],
    month: Optional[int],
) -> tuple[int, int, datetime, datetime]:
    now = datetime.utcnow()
    period_year = int(year or now.year)
    period_month = max(1, min(int(month or now.month), 12))
    start = datetime(period_year, period_month, 1)
    end = (
        datetime(period_year + 1, 1, 1)
        if period_month == 12
        else datetime(period_year, period_month + 1, 1)
    )
    return period_year, period_month, start, end


_NON_FISCAL_ACCOUNT_NAMES = {
    "sin requisitos fiscales",
    "no deducible",
    "gastos no deducibles",
}


def _normalize_account_name(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _allows_coi_without_cfdi(account: Any) -> bool:
    name = _normalize_account_name(getattr(account, "nombre", None))
    return name in _NON_FISCAL_ACCOUNT_NAMES


def _allows_coi_without_cfdi_name(name: Any) -> bool:
    return _normalize_account_name(name) in _NON_FISCAL_ACCOUNT_NAMES


async def _build_finance_coi_batch_expenses(
    session: AsyncSession,
    *,
    year: Optional[int],
    month: Optional[int],
    limit: int = 500,
) -> tuple[int, int, list[ExpenseCFDI]]:
    """Build COI-ready ExpenseCFDI rows for the Finance Platform period."""
    period_year, period_month, start, end = _finance_period_bounds(year, month)
    result = await session.execute(
        select(ExpenseReport)
        .options(
            selectinload(ExpenseReport.cfdi_report),
            selectinload(ExpenseReport.cuenta_contable),
            selectinload(ExpenseReport.contra_cuenta_contable),
        )
        .where(
            and_(
                ExpenseReport.estado_gasto != "cancelado",
                ExpenseReport.fecha >= start,
                ExpenseReport.fecha < end,
                ExpenseReport.cuenta_contable_id.isnot(None),
                ExpenseReport.contra_cuenta_contable_id.isnot(None),
            )
        )
        .order_by(ExpenseReport.fecha.asc(), ExpenseReport.numero_referencia.asc())
        .limit(limit)
    )
    expenses = result.scalars().all()
    output: list[ExpenseCFDI] = []
    for expense in expenses:
        cuenta_contable = getattr(expense, "cuenta_contable", None)
        allows_missing_cfdi = bool(
            _allows_coi_without_cfdi(cuenta_contable)
            and not getattr(expense, "cfdi_report_id", None)
        )
        if not getattr(expense, "cfdi_report_id", None) and not allows_missing_cfdi:
            continue
        preview = await build_expense_accounting_preview(session, expense)
        taxes = preview.get("taxes") or {}
        contra_account = preview.get("contra_account") or {}
        cfdi = getattr(expense, "cfdi_report", None)
        iva_amount = round(float(taxes.get("iva_trasladado") or 0), 2)
        total_amount = round(float(expense.gasto_cantidad or 0), 2)
        subtotal_amount = round(total_amount - iva_amount, 2)
        retenciones = [
            {
                "label": item.get("label"),
                "importe": float(item.get("importe") or 0.0),
                "cuenta_contable": (item.get("account", {}) or {}).get("codigo"),
            }
            for item in list(taxes.get("retenciones") or [])
        ]
        impuestos_locales = [
            {
                "kind": item.get("kind") or "tax",
                "label": item.get("label") or "Impuesto local",
                "importe": float(item.get("importe") or 0.0),
                "cuenta_contable": (item.get("account", {}) or {}).get("codigo"),
                "entidad": item.get("entidad"),
                "tasa_pct": item.get("tasa_pct"),
                "confirmado": bool(item.get("confirmado")),
            }
            for item in list(taxes.get("impuestos_locales") or [])
        ]
        gastos_no_deducibles = [
            {
                "kind": item.get("kind") or "gasto",
                "label": item.get("label") or "No deducible",
                "importe": float(item.get("importe") or 0.0),
                "cuenta_contable": (item.get("account", {}) or {}).get("codigo"),
            }
            for item in list(taxes.get("gastos_no_deducibles") or [])
        ]
        output.append(
            ExpenseCFDI(
                fecha=expense.fecha,
                total=total_amount,
                iva_amount=iva_amount,
                subtotal_amount=subtotal_amount,
                concepto=expense.concepto or "Gasto",
                cuenta_contable=str(cuenta_contable.codigo),
                cuenta_contrapartida=str(
                    contra_account.get("codigo")
                    or expense.contra_cuenta_contable.codigo
                ),
                cfdi_uuid=getattr(cfdi, "cfdi_uuid", None),
                cfdi_date=getattr(cfdi, "fecha", None),
                rfc_emisor=getattr(cfdi, "emisor_rfc", None),
                rfc_receptor=getattr(cfdi, "receptor_rfc", None),
                folio=getattr(cfdi, "folio", None),
                nombre_emisor=getattr(cfdi, "emisor_nombre", None),
                receptor_uso_cfdi=getattr(cfdi, "receptor_uso_cfdi", None),
                cuenta_iva=str((taxes.get("iva_account") or {}).get("codigo") or ""),
                retenciones=retenciones,
                impuestos_locales=impuestos_locales,
                gastos_no_deducibles=gastos_no_deducibles,
                neto_contrapartida=float(
                    taxes.get("neto_contrapartida") or total_amount
                ),
                base_amount=float(taxes.get("base_gasto") or subtotal_amount),
                export_reference=expense.numero_referencia or expense.concepto or "",
                proyecto=expense.proyecto,
                cuenta_contable_nombre=str(
                    getattr(cuenta_contable, "nombre", "") or ""
                ),
                allows_missing_cfdi=allows_missing_cfdi,
                missing_cfdi_warning=(
                    "No deducible sin CFDI. Verifica que la cuenta contable sea "
                    "'Sin requisitos fiscales' o 'No deducible'."
                    if allows_missing_cfdi
                    else None
                ),
            )
        )
    return period_year, period_month, output


async def _build_finance_coi_batch_blocker_summary(
    session: AsyncSession,
    *,
    year: Optional[int],
    month: Optional[int],
    limit: int = 500,
) -> tuple[int, int, dict[str, int]]:
    """Summarize why period expenses are not yet exportable to COI batch."""
    period_year, period_month, start, end = _finance_period_bounds(year, month)
    result = await session.execute(
        select(ExpenseReport)
        .options(selectinload(ExpenseReport.cuenta_contable))
        .where(
            and_(
                ExpenseReport.estado_gasto != "cancelado",
                ExpenseReport.fecha >= start,
                ExpenseReport.fecha < end,
            )
        )
        .order_by(ExpenseReport.fecha.asc(), ExpenseReport.numero_referencia.asc())
        .limit(limit)
    )
    summary = {
        "period_expenses": 0,
        "missing_cuenta": 0,
        "missing_contra": 0,
        "non_fiscal_without_cfdi": 0,
        "manual_uuid_unlinked": 0,
        "missing_cfdi": 0,
        "ready_count": 0,
    }
    for expense in result.scalars().all():
        summary["period_expenses"] += 1
        has_cuenta = bool(getattr(expense, "cuenta_contable_id", None))
        has_contra = bool(getattr(expense, "contra_cuenta_contable_id", None))
        has_cfdi_link = bool(getattr(expense, "cfdi_report_id", None))
        has_manual_uuid = bool(getattr(expense, "cfdi_uuid_manual", None))
        allows_missing_cfdi = _allows_coi_without_cfdi(
            getattr(expense, "cuenta_contable", None)
        )
        if not has_cuenta:
            summary["missing_cuenta"] += 1
        if not has_contra:
            summary["missing_contra"] += 1
        if allows_missing_cfdi and not has_cfdi_link:
            summary["non_fiscal_without_cfdi"] += 1
        elif has_manual_uuid and not has_cfdi_link:
            summary["manual_uuid_unlinked"] += 1
        elif not has_cfdi_link:
            summary["missing_cfdi"] += 1
        if has_cuenta and has_contra and (has_cfdi_link or allows_missing_cfdi):
            summary["ready_count"] += 1
    return period_year, period_month, summary


def _format_finance_coi_batch_blocker_message(
    *,
    period_year: int,
    period_month: int,
    summary: dict[str, int],
) -> str:
    period_label = f"{period_month:02d}/{period_year}"
    if not summary.get("period_expenses"):
        return f"No hay gastos activos en el periodo {period_label}."
    parts = [f"Periodo {period_label}: 0 gastos listos para COI."]
    if summary.get("missing_cuenta"):
        parts.append(f"Sin cuenta contable: {summary['missing_cuenta']}.")
    if summary.get("missing_contra"):
        parts.append(f"Sin contracuenta: {summary['missing_contra']}.")
    if summary.get("non_fiscal_without_cfdi"):
        parts.append(
            "No deducibles permitidos sin CFDI: "
            f"{summary['non_fiscal_without_cfdi']}."
        )
    if summary.get("manual_uuid_unlinked"):
        parts.append(
            "Con UUID manual pero sin CFDI ligado: "
            f"{summary['manual_uuid_unlinked']}."
        )
    if summary.get("missing_cfdi"):
        parts.append(f"Sin CFDI ligado: {summary['missing_cfdi']}.")
    return " ".join(parts)


@router.get(
    "/admin/finanzas/coi-lote-consolidado.xlsx",
    response_class=Response,
    response_model=None,
)
async def admin_finance_coi_batch_consolidated_xlsx(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = require_admin_finanzas(),
    year: Optional[int] = Query(None),
    month: Optional[int] = Query(None),
) -> Union[Response, RedirectResponse]:
    """Download one consolidated COI workbook for all COI-ready period expenses."""
    period_year, period_month, expenses = await _build_finance_coi_batch_expenses(
        session,
        year=year,
        month=month,
    )
    if not expenses:
        _, _, blocker_summary = await _build_finance_coi_batch_blocker_summary(
            session,
            year=period_year,
            month=period_month,
        )
        return RedirectResponse(
            url=(
                f"/admin/finanzas?year={period_year}&month={period_month}"
                "&error_msg="
                + quote(
                    _format_finance_coi_batch_blocker_message(
                        period_year=period_year,
                        period_month=period_month,
                        summary=blocker_summary,
                    )
                )
            ),
            status_code=303,
        )

    payload = generate_coi_poliza_xlsx(expenses)
    filename = f"COI_finanzas_consolidado_{period_year}_{period_month:02d}.xlsx"
    return Response(
        content=payload,
        media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get(
    "/admin/finanzas/coi-lote.zip",
    response_class=Response,
    response_model=None,
)
@router.get(
    "/admin/finanzas/coi-lote.xlsx",
    response_class=Response,
    response_model=None,
)
async def admin_finance_coi_batch_xlsx(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = require_admin_finanzas(),
    year: Optional[int] = Query(None),
    month: Optional[int] = Query(None),
) -> Union[Response, RedirectResponse]:
    """Download a ZIP with one COI workbook per COI-ready period expense."""
    period_year, period_month, expenses = await _build_finance_coi_batch_expenses(
        session,
        year=year,
        month=month,
    )
    if not expenses:
        _, _, blocker_summary = await _build_finance_coi_batch_blocker_summary(
            session,
            year=period_year,
            month=period_month,
        )
        return RedirectResponse(
            url=(
                f"/admin/finanzas?year={period_year}&month={period_month}"
                "&error_msg="
                + quote(
                    _format_finance_coi_batch_blocker_message(
                        period_year=period_year,
                        period_month=period_month,
                        summary=blocker_summary,
                    )
                )
            ),
            status_code=303,
        )

    payload = generate_coi_poliza_zip(
        expenses,
        filename_prefix=f"COI_finanzas_{period_year}_{period_month:02d}",
    )
    filename = f"COI_finanzas_{period_year}_{period_month:02d}.zip"
    return Response(
        content=payload,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _payment_run_redirect(
    *,
    success_msg: Optional[str] = None,
    error_msg: Optional[str] = None,
) -> RedirectResponse:
    params = []
    if success_msg:
        params.append(f"success_msg={quote(success_msg)}")
    if error_msg:
        params.append(f"error_msg={quote(error_msg)}")
    suffix = ("?" + "&".join(params)) if params else ""
    return RedirectResponse(url=f"/admin/finanzas/payment-run{suffix}", status_code=303)


def _payment_run_money(value: Any, currency: str = "MXN") -> str:
    try:
        amount = float(value or 0)
    except Exception:
        amount = 0.0
    return f"{currency or 'MXN'} ${amount:,.2f}"


def _payment_run_ref_number(value: Any) -> Optional[int]:
    raw = str(value or "").strip()
    if not raw:
        return None
    digits = "".join(ch for ch in raw if ch.isdigit())
    if not digits:
        return None
    try:
        return int(digits)
    except ValueError:
        return None


def _payment_run_sort_key(row: dict[str, Any]) -> tuple[int, int, str]:
    ref_number = _payment_run_ref_number(row.get("referencia_operaciones"))
    fallback = str(row.get("fecha_pago") or row.get("aprobado_en") or "")
    if ref_number is None:
        return (1, 0, fallback)
    return (0, -ref_number, fallback)


def _payment_run_sort_value(value: Any, *, kind: str = "text") -> str:
    if kind == "referencia_operaciones":
        ref_number = _payment_run_ref_number(value)
        return "" if ref_number is None else str(ref_number)
    if kind == "money":
        try:
            return str(float(value or 0))
        except Exception:
            return "0"
    if kind == "date":
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        return str(value or "")
    return str(value or "")


def _admin_sortable_table_assets() -> str:
    return """
        <style>
            table[data-sortable-table] th[data-sort-key] {
                cursor:pointer;
                user-select:none;
                white-space:nowrap;
            }
            table[data-sortable-table] th[data-sort-dir="asc"]::after {
                content:"↑";
                margin-left:6px;
            }
            table[data-sortable-table] th[data-sort-dir="desc"]::after {
                content:"↓";
                margin-left:6px;
            }
        </style>
        <script>
        (function() {
            function normalizeText(value) {
                return String(value || '').normalize('NFD').replace(/[\\u0300-\\u036f]/g, '').toLowerCase().trim();
            }
            function numericValue(value) {
                var parsed = Number.parseFloat(String(value || '').replace(/[^0-9.-]+/g, ''));
                return Number.isFinite(parsed) ? parsed : null;
            }
            function cellValue(row, index) {
                var cell = row.children[index];
                return cell ? (cell.getAttribute('data-sort-value') || cell.textContent || '') : '';
            }
            function compareValues(a, b, type, direction) {
                var aEmpty = normalizeText(a) === '' || normalizeText(a) === '—' || normalizeText(a) === '-';
                var bEmpty = normalizeText(b) === '' || normalizeText(b) === '—' || normalizeText(b) === '-';
                if (aEmpty && bEmpty) return 0;
                if (aEmpty) return 1;
                if (bEmpty) return -1;
                var result = 0;
                if (type === 'number' || type === 'money' || type === 'date') {
                    var aNum = type === 'date' ? Date.parse(a) : numericValue(a);
                    var bNum = type === 'date' ? Date.parse(b) : numericValue(b);
                    if (!Number.isFinite(aNum)) aNum = numericValue(a);
                    if (!Number.isFinite(bNum)) bNum = numericValue(b);
                    if (aNum !== null && bNum !== null) result = aNum - bNum;
                    else result = normalizeText(a).localeCompare(normalizeText(b), 'es');
                } else {
                    result = normalizeText(a).localeCompare(normalizeText(b), 'es');
                }
                return direction === 'desc' ? -result : result;
            }
            function sortTable(table, columnIndex, direction, markHeader) {
                var tbody = table.tBodies && table.tBodies[0];
                if (!tbody) return;
                var header = table.tHead ? table.tHead.rows[0].children[columnIndex] : null;
                var type = header ? (header.getAttribute('data-sort-type') || 'text') : 'text';
                var rows = Array.prototype.slice.call(tbody.rows).filter(function(row) {
                    return !row.hasAttribute('data-sort-ignore');
                });
                rows.sort(function(a, b) {
                    return compareValues(cellValue(a, columnIndex), cellValue(b, columnIndex), type, direction);
                });
                rows.forEach(function(row) { tbody.appendChild(row); });
                if (!markHeader) return;
                table.querySelectorAll('th[data-sort-key]').forEach(function(th) {
                    th.removeAttribute('data-sort-dir');
                });
                if (header) header.setAttribute('data-sort-dir', direction);
            }
            document.querySelectorAll('table[data-sortable-table]').forEach(function(table) {
                var defaultIndex = table.getAttribute('data-default-sort-index');
                var defaultDir = table.getAttribute('data-default-sort-dir') || 'desc';
                if (defaultIndex !== null && defaultIndex !== '') {
                    sortTable(table, Number.parseInt(defaultIndex, 10), defaultDir, false);
                }
                table.querySelectorAll('th[data-sort-key]').forEach(function(header) {
                    header.addEventListener('click', function() {
                        var nextDir = header.getAttribute('data-sort-dir') === 'asc' ? 'desc' : 'asc';
                        sortTable(table, header.cellIndex, nextDir, true);
                    });
                });
            });
        })();
        </script>
    """


def _payment_run_badge(status: str) -> str:
    visual = payment_run_status_visual(status)
    return (
        f'<span style="display:inline-flex;padding:5px 9px;border-radius:999px;'
        f'font-size:11px;font-weight:900;text-transform:uppercase;'
        f'background:{visual.background};color:{visual.foreground};">'
        f'{escape(visual.label)}</span>'
    )


def _prestamo_payment_run_beneficiary(prestamo: SolicitudPrestamo) -> str:
    snapshot = str(getattr(prestamo, "beneficiario_nombre_snapshot", "") or "").strip()
    if snapshot:
        return snapshot
    empleado = getattr(prestamo, "beneficiario_empleado", None)
    if empleado is not None and getattr(empleado, "nombre", None):
        return str(empleado.nombre)
    proveedor = getattr(prestamo, "beneficiario_proveedor_cliente", None)
    if proveedor is not None and getattr(proveedor, "nombre", None):
        return str(proveedor.nombre)
    solicitante = getattr(prestamo, "solicitante", None)
    if solicitante is not None and getattr(solicitante, "nombre", None):
        return str(solicitante.nombre)
    return "-"


def _loan_payment_run_row(prestamo: SolicitudPrestamo) -> dict[str, Any]:
    status = "en proceso de pago"
    if prestamo.estado == PRESTAMO_STATUS_APROBADA:
        status = "programada"
    return {
        "id": prestamo.id,
        "entity_type": "prestamo",
        "numero_referencia": prestamo.numero_referencia,
        "referencia_operaciones": None,
        "fecha_pago": None,
        "aprobado_en": prestamo.aprobado_en,
        "pagado_en": prestamo.pagado_en,
        "concepto_pago": prestamo.motivo or "Prestamo",
        "currency": prestamo.currency or "MXN",
        "monto": Decimal(str(prestamo.monto_solicitado or 0)),
        "solicitante_nombre": getattr(prestamo.solicitante, "nombre", "-"),
        "beneficiario_nombre": _prestamo_payment_run_beneficiary(prestamo),
        "proveedor_nombre": None,
        "closure_id": None,
        "status": status,
        "can_edit_fecha_pago": False,
        "can_close": prestamo.estado == PRESTAMO_STATUS_APROBADA,
        "can_upload_payment_proof": (
            prestamo.estado == PRESTAMO_STATUS_EN_PROCESO_PAGO
        ),
    }


async def list_prestamo_payment_run_items(
    session: AsyncSession,
    *,
    status_filter: str = "pendientes",
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    query: Optional[str] = None,
    limit: int = 250,
) -> list[dict[str, Any]]:
    normalized_status = (status_filter or "pendientes").strip().lower()
    estados = [PRESTAMO_STATUS_APROBADA]
    if normalized_status in {"cerradas", "en_proceso", "en_proceso_pago"}:
        estados = [PRESTAMO_STATUS_EN_PROCESO_PAGO]
    elif normalized_status == "todas":
        estados = [PRESTAMO_STATUS_APROBADA, PRESTAMO_STATUS_EN_PROCESO_PAGO]
    elif normalized_status not in {"pendientes", "abiertas"}:
        estados = [PRESTAMO_STATUS_APROBADA]
    stmt = (
        select(SolicitudPrestamo)
        .options(
            selectinload(SolicitudPrestamo.solicitante),
            selectinload(SolicitudPrestamo.beneficiario_empleado),
            selectinload(SolicitudPrestamo.beneficiario_proveedor_cliente),
        )
        .where(SolicitudPrestamo.estado.in_(estados))
        .order_by(SolicitudPrestamo.aprobado_en.desc().nullslast())
        .limit(max(1, min(int(limit or 250), 500)))
    )
    if date_from:
        stmt = stmt.where(SolicitudPrestamo.aprobado_en >= datetime.combine(date_from, datetime.min.time()))
    if date_to:
        stmt = stmt.where(SolicitudPrestamo.aprobado_en <= datetime.combine(date_to, datetime.max.time()))
    result = await session.execute(stmt)
    rows = [_loan_payment_run_row(prestamo) for prestamo in result.scalars().all()]
    if query:
        needle = str(query or "").strip().lower()
        rows = [
            row
            for row in rows
            if needle in str(row.get("numero_referencia") or "").lower()
            or needle in str(row.get("concepto_pago") or "").lower()
            or needle in str(row.get("solicitante_nombre") or "").lower()
            or needle in str(row.get("beneficiario_nombre") or "").lower()
        ]
    return rows


def _render_payment_run_items(
    rows: list[dict[str, Any]],
    *,
    can_close_run: bool = True,
    can_confirm_payment: bool = False,
    can_edit_payment_date: bool = False,
) -> str:
    rendered_rows = []
    for row in sorted(rows, key=_payment_run_sort_key):
        documento_id = escape(str(row.get("id") or ""))
        entity_type = str(row.get("entity_type") or "documento")
        referencia = escape(str(row.get("numero_referencia") or documento_id))
        referencia_operaciones = escape(str(row.get("referencia_operaciones") or "—"))
        referencia_operaciones_sort = _payment_run_sort_value(
            row.get("referencia_operaciones"),
            kind="referencia_operaciones",
        )
        fecha_pago = row.get("fecha_pago")
        fecha_value = fecha_pago.isoformat() if hasattr(fecha_pago, "isoformat") else ""
        fecha_sort = _payment_run_sort_value(fecha_pago, kind="date")
        can_edit = bool(row.get("can_edit_fecha_pago"))
        can_close = bool(row.get("can_close"))
        checkbox = ""
        if entity_type == "prestamo" and can_close and can_close_run:
            checkbox = f"""
                <form method="POST" action="/prestamos/{documento_id}/programar-pago" style="display:inline;">
                    <button class="button secondary" type="submit" style="padding:8px 10px;" onclick="return confirm('Pasar este préstamo a En Proceso de Pago?');">Pasar a pago</button>
                </form>
            """
        else:
            checkbox = (
            f'<input type="checkbox" form="payment-run-close-form" '
            f'name="document_ids" value="{documento_id}">'
            if can_close and can_close_run
            else '<span style="color:#94a3b8;">-</span>'
            )
        if can_edit and can_edit_payment_date:
            fecha_html = f"""
                <form method="POST" action="/admin/finanzas/payment-run/documentos/{documento_id}/fecha-pago" style="display:flex;gap:8px;align-items:center;">
                    <input type="date" name="fecha_pago" value="{escape(fecha_value)}" style="min-width:150px;">
                    <button class="button secondary" type="submit" style="padding:8px 10px;">Guardar</button>
                </form>
            """
        else:
            fecha_html = escape(fecha_value or "-")
        closure_id = row.get("closure_id")
        closure_html = (
            f'<a href="/admin/finanzas/payment-run/closures/{escape(str(closure_id))}">Ver corte</a>'
            if closure_id
            else "-"
        )
        proof_html = "-"
        if row.get("can_upload_payment_proof") and can_confirm_payment:
            proof_action = (
                f"/prestamos/{documento_id}/comprobante-pago"
                if entity_type == "prestamo"
                else f"/admin/finanzas/payment-run/documentos/{documento_id}/comprobante-pago"
            )
            proof_html = f"""
                <form method="POST" enctype="multipart/form-data" action="{proof_action}" style="display:grid;gap:8px;min-width:220px;">
                    <input type="file" name="comprobante_pago" required>
                    <button class="button secondary" type="submit" style="padding:8px 10px;" onclick="return confirm('Subir testigo y marcar la solicitud como pagada?');">Subir testigo y pagar</button>
                </form>
            """
        detail_href = (
            f"/prestamos/{documento_id}"
            if entity_type == "prestamo"
            else f"/documentos/{documento_id}"
        )
        type_label = "Préstamo" if entity_type == "prestamo" else "Solicitud"
        amount_issue = str(row.get("amount_issue") or "").strip()
        amount_html = _payment_run_money(
            row.get("monto"), str(row.get("currency") or "MXN")
        )
        if amount_issue:
            amount_html += (
                '<div style="color:#991b1b;font-size:12px;font-weight:700;">'
                f"{escape(amount_issue)}</div>"
            )
        rendered_rows.append(
            f"""
            <tr>
                <td>{checkbox}</td>
                <td><a href="{detail_href}"><strong>{referencia}</strong></a><div style="color:#64748b;font-size:12px;">{escape(type_label)} · {escape(str(row.get("concepto_pago") or ""))[:120]}</div></td>
                <td data-sort-value="{escape(referencia_operaciones_sort)}">{referencia_operaciones}</td>
                <td>{escape(str(row.get("solicitante_nombre") or "-"))}</td>
                <td>{escape(str(row.get("beneficiario_nombre") or row.get("proveedor_nombre") or "-"))}</td>
                <td data-sort-value="{escape(fecha_sort)}">{fecha_html}</td>
                <td data-sort-value="{escape(_payment_run_sort_value(row.get('monto'), kind='money'))}">{amount_html}</td>
                <td>{_payment_run_badge(str(row.get("status") or ""))}</td>
                <td>{proof_html}</td>
                <td>{closure_html}</td>
            </tr>
            """
        )
    return "".join(rendered_rows) or '<tr><td colspan="10">Sin solicitudes para este filtro.</td></tr>'


def _render_payment_run_closures(rows: list[dict[str, Any]]) -> str:
    rendered_rows = []
    for row in rows:
        closure_id = escape(str(row.get("id") or ""))
        rendered_rows.append(
            f"""
            <tr>
                <td><a href="/admin/finanzas/payment-run/closures/{closure_id}">{closure_id[:8]}</a></td>
                <td>{escape(str(row.get("closed_at") or "-"))[:19]}</td>
                <td>{escape(str(row.get("run_date") or "-"))}</td>
                <td>{int(row.get("item_count") or 0)}</td>
                <td>{_payment_run_money(row.get("total_amount"))}</td>
                <td>{escape(str(row.get("closed_by_nombre") or "-"))}</td>
            </tr>
            """
        )
    return "".join(rendered_rows) or '<tr><td colspan="6">Sin cortes cerrados.</td></tr>'


@router.get("/admin/finanzas/payment-run", response_class=HTMLResponse)
async def admin_finance_payment_run(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = Depends(get_current_empleado),
    status: str = Query("pendientes"),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
) -> HTMLResponse:
    """Consult, date-edit and operationally close approved SOLICITUD payment runs."""
    try:
        require_payment_run_access(current_empleado)
    except PaymentRunPermissionError as exc:
        raise HTTPException(status_code=403, detail=exc.message)

    error_msg = (request.query_params.get("error_msg") or "").strip()
    success_msg = (request.query_params.get("success_msg") or "").strip()
    date_from_value = date_from if isinstance(date_from, str) else None
    date_to_value = date_to if isinstance(date_to, str) else None
    q_value = q if isinstance(q, str) else ""
    try:
        parsed_from = parse_payment_run_date(date_from_value) if date_from_value else None
        parsed_to = parse_payment_run_date(date_to_value) if date_to_value else None
    except PaymentRunValidationError as exc:
        parsed_from = None
        parsed_to = None
        error_msg = error_msg or exc.message

    approved_rows = await list_payment_run_items(
        session,
        status_filter="pendientes",
        date_from=parsed_from,
        date_to=parsed_to,
        query=(q_value or "").strip() or None,
    )
    approved_rows.extend(
        await list_prestamo_payment_run_items(
            session,
            status_filter="pendientes",
            date_from=parsed_from,
            date_to=parsed_to,
            query=(q_value or "").strip() or None,
        )
    )
    proof_rows = await list_payment_run_items(
        session,
        status_filter="cerradas",
        date_from=parsed_from,
        date_to=parsed_to,
        query=(q_value or "").strip() or None,
    )
    proof_rows.extend(
        await list_prestamo_payment_run_items(
            session,
            status_filter="cerradas",
            date_from=parsed_from,
            date_to=parsed_to,
            query=(q_value or "").strip() or None,
        )
    )
    closures = await list_payment_run_closures(session, limit=20)
    can_close_run = can_manage_payment_run(current_empleado)
    can_confirm_payment = can_confirm_payment_run_payment(current_empleado)
    close_form_html = ""
    if can_close_run:
        close_form_html = """
                <form id="payment-run-close-form" method="POST" action="/admin/finanzas/payment-run/closures" style="margin-top:16px;display:grid;grid-template-columns:minmax(160px,.5fr) minmax(260px,1fr) auto;gap:12px;align-items:end;">
                    <div><label style="font-size:12px;font-weight:800;color:#475569;">Fecha corte</label><input type="date" name="run_date"></div>
                    <div><label style="font-size:12px;font-weight:800;color:#475569;">Notas</label><input name="notes" placeholder="Referencia interna opcional"></div>
                    <button class="button" type="submit" onclick="return confirm('Cerrar el corte operativo seleccionado? Las solicitudes pasaran a En Proceso de Pago.');">Cerrar corte</button>
                </form>
        """
    total_open = sum(float(row.get("monto") or 0) for row in approved_rows if row.get("can_close"))
    total_proof = sum(float(row.get("monto") or 0) for row in proof_rows)
    selected_status = escape(status or "pendientes")
    total_rows = len(approved_rows) + len(proof_rows)
    alerts = ""
    if success_msg:
        alerts += f'<div class="alert alert-success">{escape(success_msg)}</div>'
    if error_msg:
        alerts += f'<div class="alert alert-error">{escape(error_msg)}</div>'
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>Payment Run - Samchat</title>
        <style>
            {_admin_workspace_styles("1380px", layout="data")}
            .payment-table {{ width:100%; border-collapse:separate; border-spacing:0; }}
            .payment-table th, .payment-table td {{ text-align:left; padding:12px; border-bottom:1px solid #e2e8f0; vertical-align:top; }}
            .payment-table th {{ color:#64748b; font-size:11px; text-transform:uppercase; letter-spacing:.11em; background:#f8fafc; }}
            input, select, textarea {{ width:100%; padding:10px 12px; border-radius:12px; border:1px solid #cbd5e1; }}
            input[type="checkbox"] {{ width:auto; }}
            .alert {{ border-radius:14px; padding:12px 14px; margin-bottom:14px; font-weight:700; }}
            .alert-success {{ background:#dcfce7; color:#166534; border:1px solid #86efac; }}
            .alert-error {{ background:#fee2e2; color:#991b1b; border:1px solid #fecaca; }}
        </style>
    </head>
    <body>
        <div class="workspace-shell">
            {render_admin_navigation(current_empleado, "payment_run", subtitle="Payment Run: consulta, fecha de pago y cierre operativo.")}
            {_render_admin_workspace_hero(
                eyebrow="Payment Run",
                title="Corte operativo de solicitudes aprobadas",
                description="Consulta solicitudes aprobadas, ajusta fecha_pago y cierra el corte operativo. Al cerrar, las solicitudes pasan a En Proceso de Pago para que Contabilidad adjunte el comprobante.",
                actions_html=(
                    '<form method="GET" action="/admin/finanzas/payment-run" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;align-items:end;">'
                    f'<input type="hidden" name="status" value="{selected_status}">'
                    f'<div><label style="font-size:12px;font-weight:800;color:#475569;">Desde</label><input name="date_from" type="date" value="{escape(date_from_value or "")}"></div>'
                    f'<div><label style="font-size:12px;font-weight:800;color:#475569;">Hasta</label><input name="date_to" type="date" value="{escape(date_to_value or "")}"></div>'
                    f'<div><label style="font-size:12px;font-weight:800;color:#475569;">Buscar</label><input name="q" value="{escape(q_value or "")}" placeholder="Referencia, solicitante, beneficiario"></div>'
                    '<button class="button" type="submit">Filtrar</button>'
                    '</form>'
                ),
                side_html=(
                    '<div class="eyebrow">Vista actual</div>'
                    f'<div style="font-size:1.3rem;font-weight:900;color:#0f172a;">{total_rows} solicitudes</div>'
                    f'<div style="margin-top:8px;color:#64748b;">Cerrables en filtro: {_payment_run_money(total_open)}</div>'
                    f'<div style="margin-top:8px;color:#64748b;">Comprobantes pendientes: {_payment_run_money(total_proof)}</div>'
                ),
            )}
            {alerts}
            <section class="workspace-card" style="margin-bottom:18px;">
                <div class="workspace-section-title">Solicitudes aprobadas para corte</div>
                <div class="workspace-section-subtitle">Finanzas ajusta la fecha de pago y cierra el corte operativo. Al cerrar, estas solicitudes pasan a En Proceso de Pago.</div>
                <div style="overflow-x:auto;overflow-y:visible;margin-top:14px;">
	                    <table class="payment-table" data-sortable-table data-default-sort-index="2" data-default-sort-dir="desc">
	                        <thead><tr><th>Cerrar</th><th data-sort-key="solicitud" data-sort-type="text">Solicitud</th><th data-sort-key="referencia_operaciones" data-sort-type="number">Referencia Operaciones</th><th data-sort-key="solicitante" data-sort-type="text">Solicitante</th><th data-sort-key="beneficiario" data-sort-type="text">Beneficiario</th><th data-sort-key="fecha_pago" data-sort-type="date">Fecha pago</th><th data-sort-key="monto" data-sort-type="money">Monto</th><th data-sort-key="estado" data-sort-type="text">Estado</th><th>Testigo de pago</th><th data-sort-key="corte" data-sort-type="text">Corte</th></tr></thead>
                        <tbody>{_render_payment_run_items(approved_rows, can_close_run=can_close_run, can_confirm_payment=False, can_edit_payment_date=can_close_run)}</tbody>
	                    </table>
                </div>
                {close_form_html}
            </section>
            <section class="workspace-card" style="margin-bottom:18px;">
                <div class="workspace-section-title">Comprobantes pendientes - En Proceso de Pago</div>
                <div class="workspace-section-subtitle">Contabilidad o un usuario autorizado adjunta el comprobante; al guardarlo, la solicitud se marca Pagada automáticamente.</div>
                <div style="overflow-x:auto;overflow-y:visible;margin-top:14px;">
	                    <table class="payment-table" data-sortable-table data-default-sort-index="2" data-default-sort-dir="desc">
	                        <thead><tr><th>Cerrar</th><th data-sort-key="solicitud" data-sort-type="text">Solicitud</th><th data-sort-key="referencia_operaciones" data-sort-type="number">Referencia Operaciones</th><th data-sort-key="solicitante" data-sort-type="text">Solicitante</th><th data-sort-key="beneficiario" data-sort-type="text">Beneficiario</th><th data-sort-key="fecha_pago" data-sort-type="date">Fecha pago</th><th data-sort-key="monto" data-sort-type="money">Monto</th><th data-sort-key="estado" data-sort-type="text">Estado</th><th>Testigo de pago</th><th data-sort-key="corte" data-sort-type="text">Corte</th></tr></thead>
	                        <tbody>{_render_payment_run_items(proof_rows, can_close_run=False, can_confirm_payment=can_confirm_payment)}</tbody>
	                    </table>
                </div>
            </section>
            <section class="workspace-card">
                <div class="workspace-section-title">Cortes recientes</div>
                <div style="overflow-x:auto;overflow-y:visible;margin-top:14px;">
                    <table class="payment-table">
                        <thead><tr><th>Corte</th><th>Cerrado</th><th>Fecha corte</th><th>Items</th><th>Total</th><th>Cerro</th></tr></thead>
                        <tbody>{_render_payment_run_closures(closures)}</tbody>
                    </table>
                </div>
            </section>
	        </div>
	        {_admin_sortable_table_assets()}
	    </body>
    </html>
    """
    return HTMLResponse(html)



def _render_payment_history_rows(rows: list[dict[str, Any]]) -> str:
    rendered_rows = []
    for row in sorted(rows, key=_payment_run_sort_key):
        documento_id = escape(str(row.get("id") or ""))
        referencia = escape(str(row.get("numero_referencia") or documento_id))
        referencia_operaciones = escape(str(row.get("referencia_operaciones") or "—"))
        referencia_operaciones_sort = _payment_run_sort_value(
            row.get("referencia_operaciones"),
            kind="referencia_operaciones",
        )
        solicitante = escape(str(row.get("solicitante_nombre") or "-"))
        beneficiario = escape(str(row.get("beneficiario_nombre") or row.get("proveedor_nombre") or "-"))
        fecha_aprobacion = escape(str(row.get("aprobado_en") or "-")[:10])
        fecha_programacion = escape(str(row.get("fecha_pago") or "-")[:10])
        fecha_pagada = escape(str(row.get("pagado_en") or "-")[:10])
        concepto = escape(str(row.get("concepto_pago") or ""))[:180]
        rendered_rows.append(
            f"""
            <tr>
                <td><a href="/documentos/{documento_id}">{referencia}</a><div style="color:#64748b;font-size:12px;">{concepto}</div></td>
                <td data-sort-value="{escape(referencia_operaciones_sort)}">{referencia_operaciones}</td>
                <td>{solicitante}</td>
                <td>{beneficiario}</td>
                <td data-sort-value="{escape(_payment_run_sort_value(row.get('aprobado_en'), kind='date'))}">{fecha_aprobacion}</td>
                <td data-sort-value="{escape(_payment_run_sort_value(row.get('fecha_pago'), kind='date'))}">{fecha_programacion}</td>
                <td data-sort-value="{escape(_payment_run_sort_value(row.get('pagado_en'), kind='date'))}">{fecha_pagada}</td>
                <td data-sort-value="{escape(_payment_run_sort_value(row.get('monto'), kind='money'))}">{_payment_run_money(row.get("monto"), str(row.get("currency") or "MXN"))}</td>
                <td>{_payment_run_badge(str(row.get("status") or ""))}</td>
            </tr>
            """
        )
    return "".join(rendered_rows) or '<tr><td colspan="9">Sin pagos para este filtro.</td></tr>'


@router.get("/admin/finanzas/payment-history", response_class=HTMLResponse)
async def admin_finance_payment_history(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = Depends(get_current_empleado),
    status: str = Query("todas"),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
) -> HTMLResponse:
    """Payment Run history for vencidas, programadas, en proceso and paid requests."""
    try:
        require_payment_run_access(current_empleado)
    except PaymentRunPermissionError as exc:
        raise HTTPException(status_code=403, detail=exc.message)

    error_msg = (request.query_params.get("error_msg") or "").strip()
    try:
        parsed_from = parse_payment_run_date(date_from) if date_from else None
        parsed_to = parse_payment_run_date(date_to) if date_to else None
    except PaymentRunValidationError as exc:
        parsed_from = None
        parsed_to = None
        error_msg = error_msg or exc.message
    rows = await list_payment_run_items(
        session,
        status_filter=status or "todas",
        date_from=parsed_from,
        date_to=parsed_to,
        query=(q or "").strip() or None,
        limit=500,
    )
    total_amount = sum(float(row.get("monto") or 0) for row in rows)
    selected_status = escape(status or "todas")
    alerts = f'<div class="alert alert-error">{escape(error_msg)}</div>' if error_msg else ""
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>Historial de pagos - Samchat</title>
        <style>
            {_admin_workspace_styles("1480px", layout="data")}
            .payment-table {{ width:100%; border-collapse:separate; border-spacing:0; }}
            .payment-table th, .payment-table td {{ text-align:left; padding:12px; border-bottom:1px solid #e2e8f0; vertical-align:top; }}
            .payment-table th {{ color:#64748b; font-size:11px; text-transform:uppercase; letter-spacing:.11em; background:#f8fafc; }}
            input, select {{ width:100%; padding:10px 12px; border-radius:12px; border:1px solid #cbd5e1; }}
            .alert {{ border-radius:14px; padding:12px 14px; margin-bottom:14px; font-weight:700; }}
            .alert-error {{ background:#fee2e2; color:#991b1b; border:1px solid #fecaca; }}
        </style>
    </head>
    <body>
        <div class="workspace-shell">
            {render_admin_navigation(current_empleado, "payment_history", subtitle="Historial de pagos: vencidas, programadas, en proceso y pagadas.")}
            {_render_admin_workspace_hero(
                eyebrow="Payment Run",
                title="Historial de pagos",
                description="Consulta solicitudes vencidas, programadas, en proceso de pago y pagadas con fechas clave y trazabilidad operativa.",
                actions_html=(
                    '<form method="GET" action="/admin/finanzas/payment-history" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;align-items:end;">'
                    f'<div><label style="font-size:12px;font-weight:800;color:#475569;">Estado</label><select name="status"><option value="todas" {"selected" if selected_status == "todas" else ""}>Todas</option><option value="pendientes" {"selected" if selected_status == "pendientes" else ""}>Programadas/vencidas</option><option value="cerradas" {"selected" if selected_status == "cerradas" else ""}>En proceso de pago</option><option value="pagadas" {"selected" if selected_status == "pagadas" else ""}>Pagadas</option></select></div>'
                    f'<div><label style="font-size:12px;font-weight:800;color:#475569;">Desde</label><input name="date_from" type="date" value="{escape(date_from or "")}"></div>'
                    f'<div><label style="font-size:12px;font-weight:800;color:#475569;">Hasta</label><input name="date_to" type="date" value="{escape(date_to or "")}"></div>'
                    f'<div><label style="font-size:12px;font-weight:800;color:#475569;">Buscar</label><input name="q" value="{escape(q or "")}" placeholder="Referencia, op., solicitante, beneficiario"></div>'
                    '<button class="button" type="submit">Filtrar</button>'
                    '<a class="button secondary" href="/admin/finanzas/payment-history">Limpiar</a>'
                    '</form>'
                ),
                side_html=(
                    '<div class="eyebrow">Vista actual</div>'
                    f'<div style="font-size:1.3rem;font-weight:900;color:#0f172a;">{len(rows)} solicitudes</div>'
                    f'<div style="margin-top:8px;color:#64748b;">Monto filtrado: {_payment_run_money(total_amount)}</div>'
                ),
            )}
            {alerts}
            <section class="workspace-card">
                <div class="workspace-section-title">Solicitudes por estado de pago</div>
                <div class="workspace-section-subtitle">Incluye fecha de aprobación, fecha programada, fecha pagada, solicitante y beneficiario.</div>
                <div style="overflow-x:auto;overflow-y:visible;margin-top:14px;">
	                    <table class="payment-table" data-sortable-table data-default-sort-index="1" data-default-sort-dir="desc">
	                        <thead><tr><th data-sort-key="solicitud" data-sort-type="text">Solicitud</th><th data-sort-key="referencia_operaciones" data-sort-type="number">Referencia Operaciones</th><th data-sort-key="solicitante" data-sort-type="text">Solicitante</th><th data-sort-key="beneficiario" data-sort-type="text">Beneficiario</th><th data-sort-key="fecha_aprobacion" data-sort-type="date">Fecha Aprobación</th><th data-sort-key="fecha_programacion" data-sort-type="date">Fecha Programación</th><th data-sort-key="fecha_pagada" data-sort-type="date">Fecha Pagada</th><th data-sort-key="monto" data-sort-type="money">Monto</th><th data-sort-key="estado" data-sort-type="text">Estado</th></tr></thead>
	                        <tbody>{_render_payment_history_rows(rows)}</tbody>
	                    </table>
                </div>
            </section>
	        </div>
	        {_admin_sortable_table_assets()}
	    </body>
    </html>
    """
    return HTMLResponse(html)


@router.post("/admin/finanzas/payment-run/documentos/{documento_id}/fecha-pago")
async def admin_finance_payment_run_update_fecha_pago(
    request: Request,
    documento_id: str,
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = Depends(get_current_empleado),
    fecha_pago: str = Form(...),
) -> RedirectResponse:
    try:
        require_payment_run_manager(current_empleado)
        documento = await update_payment_run_fecha_pago(
            session,
            documento_id=documento_id,
            fecha_pago=fecha_pago,
            actor_id=current_empleado.id,
            request=request,
        )
        ref = documento.numero_referencia or str(documento.id)
        return _payment_run_redirect(success_msg=f"fecha_pago actualizada para {ref}.")
    except PaymentRunPermissionError as exc:
        raise HTTPException(status_code=403, detail=exc.message)
    except (PaymentRunValidationError, ValueError) as exc:
        await session.rollback()
        return _payment_run_redirect(error_msg=getattr(exc, "message", str(exc)))


@router.post("/admin/finanzas/payment-run/closures")
async def admin_finance_payment_run_close(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = Depends(get_current_empleado),
    document_ids: Optional[List[str]] = Form(None),
    notes: Optional[str] = Form(None),
    run_date: Optional[str] = Form(None),
) -> RedirectResponse:
    try:
        require_payment_run_manager(current_empleado)
        result = await close_payment_run(
            session,
            document_ids=document_ids or [],
            actor_id=current_empleado.id,
            notes=notes,
            run_date=run_date,
            request=request,
        )
        return _payment_run_redirect(
            success_msg=(
                f"Corte cerrado con {result.item_count} solicitudes "
                f"por {_payment_run_money(result.total_amount)}."
            )
        )
    except PaymentRunPermissionError as exc:
        raise HTTPException(status_code=403, detail=exc.message)
    except (PaymentRunValidationError, ValueError) as exc:
        await session.rollback()
        return _payment_run_redirect(error_msg=getattr(exc, "message", str(exc)))


@router.post("/admin/finanzas/payment-run/documentos/{documento_id}/comprobante-pago")
async def admin_finance_payment_run_upload_payment_proof(
    documento_id: UUIDType,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = Depends(get_current_empleado),
    comprobante_pago: UploadFile = File(...),
) -> RedirectResponse:
    """Attach payment proof for an in-process Payment Run item and mark it paid."""
    from devnous.gastos.services.documento_payment_service import (
        DocumentoPaymentPermissionError,
        DocumentoPaymentValidationError,
        register_document_payment,
    )

    try:
        require_payment_run_access(current_empleado)
        require_payment_run_payment_confirmation(current_empleado)
    except PaymentRunPermissionError as exc:
        raise HTTPException(status_code=403, detail=exc.message)

    documento = await session.get(Documento, documento_id)
    if documento is None:
        return _payment_run_redirect(error_msg="Solicitud no encontrada.")
    if (documento.estado or "").strip().lower() != "en_proceso_pago":
        return _payment_run_redirect(
            error_msg="Solo se puede subir testigo desde En Proceso de Pago."
        )
    if not comprobante_pago or not comprobante_pago.filename:
        return _payment_run_redirect(error_msg="Selecciona el testigo de pago.")

    try:
        raw = await comprobante_pago.read()
        content_type = (comprobante_pago.content_type or "").split(";", 1)[0].strip().lower()
        await add_solicitud_documento_adjuntos(
            session,
            documento=documento,
            attachments=[
                SolicitudTercerosAttachment(
                    raw_bytes=raw,
                    filename=comprobante_pago.filename or "comprobante_pago",
                    mime_type=content_type or resolve_media_type(comprobante_pago.filename, raw),
                    categoria="comprobante_pago",
                )
            ],
            commit=False,
        )
        result = await register_document_payment(
            session,
            documento_id=documento_id,
            actor_id=current_empleado.id,
            actor=current_empleado,
        )
        ref = result.documento.numero_referencia or str(result.documento.id)
        return _payment_run_redirect(
            success_msg=f"Testigo cargado y solicitud {ref} marcada como pagada."
        )
    except SolicitudValidationError as exc:
        await session.rollback()
        return _payment_run_redirect(error_msg=str(exc))
    except DocumentoPaymentPermissionError as exc:
        await session.rollback()
        raise HTTPException(status_code=403, detail=exc.message)
    except DocumentoPaymentValidationError as exc:
        await session.rollback()
        return _payment_run_redirect(error_msg=exc.message)
    except Exception:
        await session.rollback()
        logger.exception(
            "Unexpected error uploading payment proof from payment run",
            extra={
                "documento_id": str(documento_id),
                "actor_id": str(current_empleado.id),
            },
        )
        return _payment_run_redirect(
            error_msg="No se pudo cargar el testigo ni marcar el pago."
        )


@router.get(
    "/admin/finanzas/payment-run/closures/{closure_id}/orden-pago.xlsx",
    response_class=Response,
)
async def admin_finance_payment_run_closure_order_export(
    closure_id: str,
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = Depends(get_current_empleado),
) -> Response:
    """Download the controlled payment instructions for one closed cutoff."""
    try:
        require_payment_run_manager(current_empleado)
    except PaymentRunPermissionError as exc:
        raise HTTPException(status_code=403, detail=exc.message)
    closure = await get_payment_run_closure(session, closure_id=closure_id)
    if not closure:
        raise HTTPException(status_code=404, detail="Corte no encontrado.")
    payload = generate_payment_run_order_xlsx(closure=closure)
    short_id = str(closure.get("id") or closure_id)[:8]
    return Response(
        content=payload,
        media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        headers={
            "Content-Disposition": (
                f'attachment; filename="orden_pago_corte_{short_id}.xlsx"'
            )
        },
    )


@router.get("/admin/finanzas/payment-run/closures/{closure_id}", response_class=HTMLResponse)
async def admin_finance_payment_run_closure_detail(
    closure_id: str,
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = Depends(get_current_empleado),
) -> HTMLResponse:
    try:
        require_payment_run_access(current_empleado)
    except PaymentRunPermissionError as exc:
        raise HTTPException(status_code=403, detail=exc.message)
    closure = await get_payment_run_closure(session, closure_id=closure_id)
    if not closure:
        raise HTTPException(status_code=404, detail="Corte no encontrado.")
    can_export_order = can_manage_payment_run(current_empleado)
    order_export_html = (
Warning: truncated output (original token count: 54308)
Total output lines: 5000

        '<a class="button" href="/admin/finanzas/payment-run/closures/'
        f'{escape(str(closure.get("id")))}/orden-pago.xlsx">'
        "Descargar orden de pago</a>"
        if can_export_order
        else ""
    )
    rows = "".join(
        f"""
        <tr>
            <td>{escape(str(item.get("numero_referencia") or item.get("documento_id") or "-"))}</td>
            <td>{escape(str(item.get("fecha_pago") or "-"))}</td>
            <td>{_payment_run_money(item.get("monto"), str(item.get("currency") or "MXN"))}</td>
            <td>{escape(str(item.get("estado_documento") or "-"))}</td>
            <td>{escape("pagada" if item.get("pagado_en") else "sin pago registrado")}</td>
        </tr>
        """
        for item in closure.get("items", [])
    )
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>Corte Payment Run - Samchat</title>
        <style>
            {_admin_workspace_styles("1180px")}
            .payment-table {{ width:100%; border-collapse:separate; border-spacing:0; }}
            .payment-table th, .payment-table td {{ text-align:left; padding:12px; border-bottom:1px solid #e2e8f0; vertical-align:top; }}
            .payment-table th {{ color:#64748b; font-size:11px; text-transform:uppercase; letter-spacing:.11em; background:#f8fafc; }}
        </style>
    </head>
    <body>
        <div class="workspace-shell">
            {render_admin_navigation(current_empleado, "payment_run", subtitle="Detalle de corte Payment Run.")}
            {_render_admin_workspace_hero(
                eyebrow="Corte Payment Run",
                title=f"Corte {escape(str(closure.get('id') or ''))[:8]}",
                description="Snapshot operativo de solicitudes incluidas en el corte. Los pagos se completan subiendo el testigo desde Payment Run.",
                actions_html=(
                    f'{order_export_html}'
                    '<a class="button secondary" href="/admin/finanzas/payment-run">Volver a Payment Run</a>'
                ),
                side_html=(
                    '<div class="eyebrow">Resumen</div>'
                    f'<div style="font-size:1.3rem;font-weight:900;color:#0f172a;">{int(closure.get("item_count") or 0)} solicitudes</div>'
                    f'<div style="margin-top:8px;color:#64748b;">Total {_payment_run_money(closure.get("total_amount"))}</div>'
                    f'<div style="margin-top:8px;color:#64748b;">Cerrado {escape(str(closure.get("closed_at") or "-"))[:19]}</div>'
                    f'<div style="margin-top:8px;color:#991b1b;">Datos de pago incompletos: {int(closure.get("missing_payment_data_count") or 0)}</div>'
                ),
            )}
            <section class="workspace-card">
                <div class="workspace-section-title">Solicitudes incluidas</div>
                <div style="overflow-x:auto;overflow-y:visible;margin-top:14px;">
                    <table class="payment-table">
                        <thead><tr><th>Solicitud</th><th>Fecha pago</th><th>Monto</th><th>Estado al corte</th><th>Pago actual</th></tr></thead>
                        <tbody>{rows or '<tr><td colspan="5">Sin items.</td></tr>'}</tbody>
                    </table>
                </div>
            </section>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(html)


@router.post("/admin/finanzas/payment-run/pay")
async def admin_finance_payment_run_pay(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = Depends(get_current_empleado),
    document_ids: Optional[List[str]] = Form(None),
    year: Optional[int] = Form(None),
    month: Optional[int] = Form(None),
) -> RedirectResponse:
    """Legacy endpoint: payment confirmation now requires proof upload."""
    base_url = "/admin/finanzas/payment-run"
    return RedirectResponse(
        url=(
            f"{base_url}?error_msg="
            + quote(
                "El pago se confirma adjuntando comprobante desde Programación de Pagos."
            )
        ),
        status_code=303,
    )


@router.post("/admin/finanzas/coi-pendientes/clasificar")
async def admin_finance_classify_coi_pending(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = require_admin_finanzas(),
) -> RedirectResponse:
    """Assign account and counterpart account to selected expenses for COI readiness."""
    form = await request.form()
    expense_ids = [
        str(item).strip() for item in form.getlist("expense_ids") if str(item).strip()
    ]
    year = (form.get("year") or "").strip()
    month = (form.get("month") or "").strip()
    query_parts = []
    if year:
        query_parts.append(f"year={quote(year)}")
    if month:
        query_parts.append(f"month={quote(month)}")
    redirect_url = "/admin/finanzas" + (
        ("?" + "&".join(query_parts)) if query_parts else ""
    )
    separator = "&" if "?" in redirect_url else "?"

    if not expense_ids:
        return RedirectResponse(
            url=(
                f"{redirect_url}{separator}"
                f"error_msg={quote('Selecciona al menos un gasto pendiente COI.')}"
            ),
            status_code=303,
        )

    classified_refs: list[str] = []
    failures: list[str] = []
    for expense_id_raw in expense_ids:
        try:
            expense_id = UUIDType(expense_id_raw)
        except (TypeError, ValueError):
            failures.append(f"{expense_id_raw}: ID inválido")
            continue

        cuenta_raw = (form.get(f"cuenta_contable_id_{expense_id_raw}") or "").strip()
        contra_raw = (
            form.get(f"contra_cuenta_contable_id_{expense_id_raw}") or ""
        ).strip()
        cuenta_iva_raw = (form.get(f"cuenta_iva_id_{expense_id_raw}") or "").strip()
        if not cuenta_raw or not contra_raw:
            failures.append(f"{expense_id_raw}: cuenta y contracuenta son obligatorias")
            continue

        try:
            cuenta_id = UUIDType(cuenta_raw)
            contra_id = UUIDType(contra_raw)
        except (TypeError, ValueError):
            failures.append(f"{expense_id_raw}: cuenta o contracuenta inválida")
            continue

        cuenta_iva_id = None
        if cuenta_iva_raw:
            try:
                cuenta_iva_id = UUIDType(cuenta_iva_raw)
            except (TypeError, ValueError):
                failures.append(f"{expense_id_raw}: cuenta IVA inválida")
                continue

        expense = await session.get(ExpenseReport, expense_id)
        cuenta = await session.get(CuentaContable, cuenta_id)
        contra = await session.get(CuentaContable, contra_id)
        cuenta_iva = (
            await session.get(CuentaContable, cuenta_iva_id) if cuenta_iva_id else None
        )
        if expense is None:
            failures.append(f"{expense_id_raw}: gasto no encontrado")
            continue
        if (expense.estado_gasto or "").lower() == "cancelado":
            failures.append(f"{expense.numero_referencia or expense_id_raw}: cancelado")
            continue
        if cuenta is None or not cuenta.activo:
            failures.append(
                f"{expense.numero_referencia or expense_id_raw}: cuenta inactiva"
            )
            continue
        if contra is None or not contra.activo:
            failures.append(
                f"{expense.numero_referencia or expense_id_raw}: contracuenta inactiva"
            )
            continue
        if cuenta_iva_id and (cuenta_iva is None or not cuenta_iva.activo):
            failures.append(
                f"{expense.numero_referencia or expense_id_raw}: cuenta IVA inactiva"
            )
            continue

        old_cuenta = str(expense.cuenta_contable_id or "")
        old_contra = str(expense.contra_cuenta_contable_id or "")
        old_iva = str(getattr(expense, "cuenta_iva_id", "") or "")
        expense.cuenta_contable_id = cuenta.id
        expense.contra_cuenta_contable_id = contra.id
        expense.cuenta_contable_budget_concept_id = None
        expense.contra_cuenta_contable_budget_concept_id = None
        if cuenta_iva_id:
            expense.cuenta_iva_id = cuenta_iva.id
        expense.updated_at = datetime.utcnow()
        session.add(expense)
        session.add(
            Aprobacion(
                tipo_entidad="gasto",
                entidad_id=expense.id,
                aprobador_id=current_empleado.id,
                accion="editar",
                comentario=(
                    "Clasificación COI desde Finanzas. "
                    f"Cuenta: {old_cuenta or '(sin asignar)'} -> {cuenta.codigo}; "
                    f"Contracuenta: {old_contra or '(sin asignar)'} -> {contra.codigo}; "
                    f"IVA: {old_iva or '(auto)'} -> "
                    f"{getattr(cuenta_iva, 'codigo', None) or '(auto)'}"
                ),
                fecha=datetime.utcnow(),
            )
        )
        classified_refs.append(str(expense.numero_referencia or expense.id))

    if classified_refs:
        await session.commit()
        message = f"Clasificación COI guardada: {', '.join(classified_refs)}."
        if failures:
            message += f" Pendientes: {'; '.join(failures[:3])}"
        return RedirectResponse(
            url=f"{redirect_url}{separator}success_msg={quote(message)}",
            status_code=303,
        )

    await session.rollback()
    return RedirectResponse(
        url=(
            f"{redirect_url}{separator}"
            f"error_msg={quote('No se clasificó ningún gasto. ' + '; '.join(failures[:3]))}"
        ),
        status_code=303,
    )


@router.post("/admin/finanzas/diot-blockers/link-cfdi")
async def admin_finance_link_diot_blockers_cfdi(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = require_admin_finanzas(),
) -> RedirectResponse:
    """Capture and link CFDI UUIDs for selected DIOT blockers."""
    form = await request.form()
    target_keys = [
        str(item).strip() for item in form.getlist("target_keys") if str(item).strip()
    ]
    year = (form.get("year") or "").strip()
    month = (form.get("month") or "").strip()
    query_parts = []
    if year:
        query_parts.append(f"year={quote(year)}")
    if month:
        query_parts.append(f"month={quote(month)}")
    redirect_url = "/admin/finanzas" + (
        ("?" + "&".join(query_parts)) if query_parts else ""
    )
    separator = "&" if "?" in redirect_url else "?"

    if not target_keys:
        return RedirectResponse(
            url=(
                f"{redirect_url}{separator}"
                f"error_msg={quote('Selecciona al menos un bloqueo DIOT.')}"
            ),
            status_code=303,
        )

    linked_refs: list[str] = []
    captured_refs: list[str] = []
    failures: list[str] = []
    for target_key in target_keys:
        try:
            entity_type, entity_id_raw = target_key.split(":", 1)
            entity_id = UUIDType(entity_id_raw)
        except (TypeError, ValueError):
            failures.append(f"{target_key}: objetivo inválido")
            continue

        uuid_field = f"cfdi_uuid_{entity_type}_{entity_id_raw}"
        raw_uuid = (form.get(uuid_field) or "").strip()
        if not raw_uuid:
            failures.append(f"{target_key}: falta UUID CFDI")
            continue
        try:
            canonical_uuid = normalize_cfdi_uuid_to_canonical(raw_uuid)
        except ValueError as exc:
            failures.append(f"{target_key}: {exc}")
            continue

        if entity_type == "expense":
            entity = await session.get(ExpenseReport, entity_id)
            tipo_entidad = "gasto"
            link_fn = link_expense_to_cfdi_if_manual_uuid_set
            ref = getattr(entity, "numero_referencia", None) if entity else None
        elif entity_type == "documento":
            entity = await session.get(Documento, entity_id)
            tipo_entidad = "documento"
            link_fn = link_documento_to_cfdi_if_manual_uuid_set
            ref = getattr(entity, "numero_referencia", None) if entity else None
        else:
            failures.append(f"{target_key}: tipo inválido")
            continue

        if entity is None:
            failures.append(f"{target_key}: no encontrado")
            continue

        previous_uuid = getattr(entity, "cfdi_uuid_manual", None)
        previous_report = getattr(entity, "cfdi_report_id", None)
        entity.cfdi_uuid_manual = canonical_uuid
        linked = await link_fn(session, entity, clear_report_if_no_match=False)
        session.add(entity)
        session.add(
            Aprobacion(
                tipo_entidad=tipo_entidad,
                entidad_id=entity.id,
                aprobador_id=current_empleado.id,
                accion="editar",
                comentario=(
                    "CFDI capturado desde Finanzas para DIOT. "
                    f"UUID: {previous_uuid or '(sin asignar)'} -> {canonical_uuid}. "
                    f"CFDI vinculado: {previous_report or '(sin vincular)'} -> "
                    f"{getattr(entity, 'cfdi_report_id', None) or '(sin vincular)'}"
                ),
                fecha=datetime.utcnow(),
            )
        )
        if linked:
            linked_refs.append(str(ref or entity.id))
        else:
            captured_refs.append(str(ref or entity.id))

    if linked_refs or captured_refs:
        await session.commit()
        parts = []
        if linked_refs:
            parts.append(f"CFDI vinculado: {', '.join(linked_refs)}")
        if captured_refs:
            parts.append(
                f"UUID capturado sin CFDI importado todavía: {', '.join(captured_refs)}"
            )
        if failures:
            parts.append(f"Pendientes: {'; '.join(failures[:3])}")
        return RedirectResponse(
            url=f"{redirect_url}{separator}success_msg={quote('. '.join(parts))}",
            status_code=303,
        )

    await session.rollback()
    return RedirectResponse(
        url=(
            f"{redirect_url}{separator}"
            f"error_msg={quote('No se amarró ningún CFDI. ' + '; '.join(failures[:3]))}"
        ),
        status_code=303,
    )


def _soul_form_value(form_data: Mapping[str, Any], key: str) -> str:
    return escape(str(form_data.get(key) or ""), quote=True)


def _soul_text_value(form_data: Mapping[str, Any], key: str) -> str:
    return escape(str(form_data.get(key) or ""))


def _render_soul_wizard_issues(readiness: Optional[dict[str, Any]]) -> str:
    if not readiness:
        return ""
    issues = list(readiness.get("issues") or [])
    if not issues:
        return """
            <div class="ok-box">
                Borrador listo para revision humana. Todavia no crea el torneo ni escribe en operacion.
            </div>
        """
    rows = "".join(
        f"""
        <tr>
            <td><span class="pill {'pill-error' if item.get('severity') == 'error' else 'pill-warning'}">{escape(str(item.get('severity') or '-'))}</span></td>
            <td>{escape(str(item.get('code') or '-'))}</td>
            <td>{escape(str(item.get('path') or '-'))}</td>
            <td>{escape(str(item.get('message') or '-'))}</td>
        </tr>
        """
        for item in issues
    )
    return f"""
        <div class="surface">
            <div class="section-title">Hallazgos del borrador</div>
            <table>
                <thead><tr><th>Tipo</th><th>Codigo</th><th>Campo</th><th>Mensaje</th></tr></thead>
                <tbody>{rows}</tbody>
            </table>
        </div>
    """


def _soul_preview_status_label(status: str) -> tuple[str, str]:
    labels = {
        "captured": ("Capturado", "pill-info"),
        "missing": ("Falta", "pill-error"),
        "inherited": ("Heredado", "pill-ok"),
        "overridden": ("Cambiado", "pill-warning"),
        "override_same_value": ("Confirmado", "pill-info"),
        "added": ("Nuevo", "pill-info"),
        "removed_or_missing": ("Falta contra fuente", "pill-error"),
        "changed": ("Diferente", "pill-warning"),
    }
    return labels.get(str(status or ""), (str(status or "-"), "pill-info"))


def _render_soul_wizard_diff(payload: dict[str, Any]) -> str:
    preview = payload.get("preview") or {}
    fields = list(preview.get("fields") or [])
    if not fields:
        return ""
    rows = ""
    for field in fields:
        label, css_class = _soul_preview_status_label(str(field.get("status") or ""))
        rows += f"""
            <tr>
                <td><strong>{escape(str(field.get('label') or field.get('path') or '-'))}</strong><br><small>{escape(str(field.get('path') or '-'))}</small></td>
                <td><span class="pill {css_class}">{escape(label)}</span></td>
                <td>{escape(str(field.get('source_summary') or '-'))}</td>
                <td>{escape(str(field.get('draft_summary') or '-'))}</td>
            </tr>
        """
    summary = preview.get("summary") or {}
    return f"""
        <div class="surface">
            <div class="section-title">Diff de activacion propuesta</div>
            <div class="summary-grid compact-grid">
                <div class="metric"><span>Modo</span><strong>{escape(str(preview.get('mode') or '-'))}</strong><small>Manual o clone.</small></div>
                <div class="metric"><span>Heredado</span><strong>{int(summary.get('inherited_count') or 0)}</strong><small>Viene de la fuente.</small></div>
                <div class="metric"><span>Cambios</span><strong>{int(summary.get('overridden_count') or 0)}</strong><small>Overrides del formulario.</small></div>
                <div class="metric"><span>Faltantes</span><strong>{int(summary.get('missing_count') or 0)}</strong><small>Antes de activar.</small></div>
            </div>
            <table class="diff-table">
                <thead><tr><th>Campo</th><th>Estado</th><th>Fuente</th><th>Borrador</th></tr></thead>
                <tbody>{rows}</tbody>
            </table>
            <div class="readonly-note">Este diff es solo una propuesta. No activa torneo, no crea calendario, no crea equipos y no notifica a nadie.</div>
        </div>
    """


def _render_soul_wizard_preview(payload: Optional[dict[str, Any]]) -> str:
    if not payload:
        return """
            <div class="muted-card">
                Captura el borrador y presiona <strong>Revisar borrador</strong>. El sistema validara fases, fechas y actividades antes de cualquier activacion.
            </div>
        """
    draft = payload.get("draft") or {}
    readiness = payload.get("readiness") or {}
    clone = payload.get("clone") or {}
    phases = list(draft.get("phases") or [])
    phase_cards = "".join(
        f"""
        <div class="phase-preview">
            <strong>{escape(str(phase.get('name') or '-'))}</strong>
            <span>{escape(str(phase.get('start_date') or '-'))} a {escape(str(phase.get('end_date') or '-'))}</span>
            <small>{len(phase.get('activities') or [])} actividades</small>
        </div>
        """
        for phase in phases
    ) or '<div class="muted-card">Sin fases capturadas.</div>'
    clone_html = ""
    if clone:
        clone_error = clone.get("error")
        clone_html = f"""
            <div class="{'readonly-note' if not clone_error else 'muted-card'}">
                <strong>{'Error de clone' if clone_error else 'Clone source'}</strong>: {escape(str(clone_error or clone.get('source_tournament_name') or clone.get('source_tournament_id') or clone.get('source_snapshot_id') or 'source bound'))}
            </div>
        """
    return f"""
        <div class="surface">
            <div class="section-title">Preview del SOUL</div>
            {clone_html}
            <div class="summary-grid">
                <div class="metric"><span>Estado</span><strong>{escape(str(readiness.get('status') or '-'))}</strong><small>Validacion del contrato.</small></div>
                <div class="metric"><span>Score</span><strong>{int(readiness.get('readiness_score') or 0)}</strong><small>Penaliza faltantes y advertencias.</small></div>
                <div class="metric"><span>Errores</span><strong>{int(readiness.get('required_missing_count') or 0)}</strong><small>Bloquean revision.</small></div>
                <div class="metric"><span>Warnings</span><strong>{int(readiness.get('warnings_count') or 0)}</strong><small>No bloquean, pero hay que cerrar.</small></div>
            </div>
            <div class="draft-line"><strong>Torneo:</strong> {escape(str(draft.get('tournament_name') or '-'))} / {escape(str(draft.get('edition_year') or '-'))}</div>
            <div class="draft-line"><strong>Categorias:</strong> {escape(', '.join(draft.get('categories') or []) or '-')}</div>
            <div class="draft-line"><strong>Ramas:</strong> {escape(', '.join(draft.get('branches') or []) or '-')}</div>
            <div class="phase-grid">{phase_cards}</div>
            <div class="readonly-note">Execution status: <strong>{escape(str(draft.get('execution_status') or 'not_executed'))}</strong>. Operational writes: <strong>{'permitidos' if draft.get('operational_writes_allowed') else 'bloqueados'}</strong>.</div>
        </div>
        {_render_soul_wizard_diff(payload)}
        {_render_soul_wizard_issues(readiness)}
    """


def _render_soul_wizard_admin_page(
    *,
    current_empleado: Empleado,
    csrf_input: str,
    payload: Optional[dict[str, Any]] = None,
    form_data: Optional[Mapping[str, Any]] = None,
) -> str:
    form_data = form_data or {}
    contract = build_soul_wizard_contract()
    step_cards = "".join(
        f"""
        <div class="step-card">
            <span>{index}</span>
            <strong>{escape(str(step.get('title') or '-'))}</strong>
            <small>{escape(', '.join(step.get('required_fields') or []))}</small>
        </div>
        """
        for index, step in enumerate(contract.get("steps") or [], start=1)
    )
    phase_inputs = "".join(
        f"""
        <div class="phase-card">
            <h3>Fase {index}</h3>
            <label>Nombre de fase</label>
            <input name="phase_{index}_name" value="{_soul_form_value(form_data, f'phase_{index}_name')}" placeholder="Ej. Inscripcion estatal">
            <div class="two-cols">
                <div><label>Fecha inicio</label><input type="date" name="phase_{index}_start_date" value="{_soul_form_value(form_data, f'phase_{index}_start_date')}"></div>
                <div><label>Fecha fin</label><input type="date" name="phase_{index}_end_date" value="{_soul_form_value(form_data, f'phase_{index}_end_date')}"></div>
            </div>
            <label>Actividades de la fase</label>
            <textarea name="phase_{index}_activities" rows="4" placeholder="Una por linea: actividad | responsable | YYYY-MM-DD">{_soul_text_value(form_data, f'phase_{index}_activities')}</textarea>
        </div>
        """
        for index in range(1, 7)
    )
    preview_html = _render_soul_wizard_preview(payload)
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>SOUL Wizard - SamChat</title>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            * {{ box-sizing:border-box; }}
            body {{ margin:0; padding:24px; min-height:100vh; background:#0f172a; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; color:#0f172a; }}
            .page {{ max-width:1280px; margin:0 auto; background:#f8fafc; border-radius:24px; padding:24px; box-shadow:0 24px 80px rgba(0,0,0,.28); }}
            h1 {{ margin:0; font-size:34px; letter-spacing:-.04em; }}
            .subtitle {{ color:#475569; margin-top:8px; line-height:1.55; }}
            .topbar {{ display:flex; justify-content:space-between; gap:16px; flex-wrap:wrap; align-items:flex-start; margin-bottom:20px; }}
            .actions {{ display:flex; gap:10px; flex-wrap:wrap; }}
            a.btn, button.btn {{ border:0; border-radius:14px; padding:12px 16px; font-weight:800; text-decoration:none; cursor:pointer; }}
            .btn-primary {{ background:#0f766e; color:white; }}
            .btn-light {{ background:white; color:#0f172a; border:1px solid #dbe2ea; }}
            .readonly-note {{ margin-top:14px; padding:12px 14px; border-radius:14px; background:#fffbeb; border:1px solid #fde68a; color:#78350f; font-size:13px; }}
            .wizard-grid {{ display:grid; grid-template-columns:minmax(0,1.35fr) minmax(360px,.65fr); gap:18px; align-items:start; }}
            .surface {{ background:white; border:1px solid #dbe2ea; border-radius:20px; padding:18px; margin-bottom:16px; box-shadow:0 14px 35px rgba(15,23,42,.06); }}
            .section-title {{ font-size:18px; font-weight:900; margin-bottom:12px; }}
            label {{ display:block; margin:12px 0 6px; font-weight:800; color:#334155; font-size:13px; }}
            input, textarea {{ width:100%; border:1px solid #cbd5e1; border-radius:12px; padding:11px 12px; font-size:14px; background:white; }}
            textarea {{ resize:vertical; }}
            .two-cols {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; }}
            .three-cols {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:12px; }}
            .phase-list {{ display:grid; gap:12px; }}
            .phase-card {{ border:1px solid #dbe2ea; border-radius:18px; padding:14px; background:#f8fafc; }}
            .phase-card h3 {{ margin:0 0 6px; }}
            .steps {{ display:grid; gap:8px; }}
            .step-card {{ display:grid; grid-template-columns:34px 1fr; gap:10px; align-items:start; padding:12px; border-radius:14px; background:#f1f5f9; border:1px solid #e2e8f0; }}
            .step-card span {{ width:28px; height:28px; border-radius:999px; display:grid; place-items:center; background:#0f766e; color:white; font-weight:900; }}
            .step-card small {{ grid-column:2; color:#64748b; }}
            .summary-grid {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:10px; }}
            .metric {{ padding:12px; border:1px solid #dbe2ea; border-radius:16px; background:#f8fafc; }}
            .metric span {{ display:block; font-size:11px; text-transform:uppercase; letter-spacing:.12em; color:#64748b; font-weight:800; }}
            .metric strong {{ display:block; font-size:24px; margin-top:4px; }}
            .metric small {{ color:#64748b; }}
            .draft-line {{ margin-top:10px; color:#334155; }}
            .phase-grid {{ display:grid; gap:8px; margin-top:14px; }}
            .phase-preview {{ padding:12px; border:1px solid #dbe2ea; border-radius:14px; background:#f8fafc; display:flex; justify-content:space-between; gap:10px; flex-wrap:wrap; }}
            .muted-card {{ padding:18px; border:1px dashed #94a3b8; border-radius:18px; color:#475569; background:#f8fafc; }}
            .ok-box {{ padding:14px; border-radius:16px; background:#ecfdf5; border:1px solid #bbf7d0; color:#065f46; font-weight:800; }}
            table {{ width:100%; border-collapse:collapse; }}
            th, td {{ text-align:left; border-bottom:1px solid #e2e8f0; padding:10px; vertical-align:top; }}
            th {{ background:#0f766e; color:white; }}
            .pill {{ padding:4px 8px; border-radius:999px; font-size:12px; font-weight:900; }}
            .pill-error {{ background:#fee2e2; color:#991b1b; }}
            .pill-warning {{ background:#fef3c7; color:#92400e; }}
            .pill-ok {{ background:#dcfce7; color:#166534; }}
            .pill-info {{ background:#dbeafe; color:#1e40af; }}
            .compact-grid {{ grid-template-columns:repeat(4,minmax(0,1fr)); margin-bottom:12px; }}
            .diff-table td:nth-child(3), .diff-table td:nth-child(4) {{ max-width:220px; color:#334155; }}
            @media (max-width: 960px) {{ .wizard-grid, .three-cols, .two-cols, .summary-grid {{ grid-template-columns:1fr; }} }}
        </style>
    </head>
    <body>
        <div class="page">
            {render_admin_navigation(current_empleado, "torneos", subtitle="Crea el borrador SOUL de un torneo paso a paso antes de activarlo.")}
            <div class="topbar">
                <div>
                    <h1>SOUL Wizard</h1>
                    <div class="subtitle">Borrador institucional para torneos: fases, fechas, actividades, responsables, documentos y reglas. Este corte solo revisa; no crea torneos.</div>
                </div>
                <div class="actions">
                    <a class="btn btn-light" href="/admin/torneos">Volver a Torneos</a>
                    <a class="btn btn-light" href="/admin/sports">Sports Platform</a>
                </div>
            </div>
            <div class="readonly-note">Contrato read-only: no crea equipos, calendario, comunicaciones ni movimientos. La activacion futura requerira preview y autoridad explicita.</div>
            <div class="wizard-grid" style="margin-top:18px;">
                <form method="POST" action="/admin/sports/soul-wizard" class="surface">
                    {csrf_input}
                    <div class="section-title">1. Identidad del torneo</div>
                    <div class="two-cols">
                        <div><label>Nombre del torneo</label><input name="tournament_name" required value="{_soul_form_value(form_data, 'tournament_name')}" placeholder="Ej. Copa Telmex Telcel de Futbol"></div>
                        <div><label>Edicion</label><input name="edition_year" type="number" min="2000" max="2100" value="{_soul_form_value(form_data, 'edition_year')}" placeholder="2027"></div>
                    </div>
                    <div class="three-cols">
                        <div><label>Categorias</label><textarea name="categories_text" rows="4" placeholder="Una por linea">{_soul_text_value(form_data, 'categories_text')}</textarea></div>
                        <div><label>Ramas / generos</label><textarea name="branches_text" rows="4" placeholder="Una por linea">{_soul_text_value(form_data, 'branches_text')}</textarea></div>
                        <div><label>Entidades esperadas</label><textarea name="expected_entities_text" rows="4" placeholder="Una por linea">{_soul_text_value(form_data, 'expected_entities_text')}</textarea></div>
                    </div>
                    <div class="two-cols">
                        <div><label>Equipos esperados</label><input name="expected_teams" type="number" min="0" value="{_soul_form_value(form_data, 'expected_teams')}" placeholder="64"></div>
                        <div><label>Torneo origen opcional</label><input name="source_tournament_id" value="{_soul_form_value(form_data, 'source_tournament_id')}" placeholder="ID o slug de torneo base"></div>
                    </div>
                    <label>Clone desde SOUL / torneo existente (JSON opcional)</label>
                    <textarea name="source_snapshot_json" rows="5" placeholder='Pega aqui un snapshot SOUL o un objeto compatible. Los campos capturados arriba reemplazan la fuente.'>{_soul_text_value(form_data, 'source_snapshot_json')}</textarea>
                    <div class="readonly-note">Si pegas una fuente, el wizard copia categorias, ramas, entidades, documentos, reglas, baseline financiera y fases disponibles; despues aplica los campos que captures como overrides.</div>
                    <div class="section-title" style="margin-top:20px;">2. Fases, fechas y actividades</div>
                    <div class="phase-list">{phase_inputs}</div>
                    <div class="section-title" style="margin-top:20px;">3. Documentos, reglas y finanzas</div>
                    <div class="three-cols">
                        <div><label>Documentos requeridos</label><textarea name="required_documents_text" rows="5" placeholder="CURP\nActa\nIdentificacion">{_soul_text_value(form_data, 'required_documents_text')}</textarea></div>
                        <div><label>Reglas de elegibilidad</label><textarea name="eligibility_rules_text" rows="5" placeholder="Edad por categoria\nSin duplicidad CURP">{_soul_text_value(form_data, 'eligibility_rules_text')}</textarea></div>
                        <div><label>Baseline financiera</label><textarea name="finance_baseline_text" rows="5" placeholder="Ayuda operador\nUniformes\nViajes nacional">{_soul_text_value(form_data, 'finance_baseline_text')}</textarea></div>
                    </div>
                    <div style="margin-top:18px;display:flex;gap:10px;flex-wrap:wrap;">
                        <button class="btn btn-primary" type="submit">Revisar borrador</button>
                        <a class="btn btn-light" href="/admin/sports/soul-wizard">Limpiar</a>
                    </div>
                </form>
                <aside>
                    <div class="surface">
                        <div class="section-title">Pasos del wizard</div>
                        <div class="steps">{step_cards}</div>
                    </div>
                    {preview_html}
                </aside>
            </div>
        </div>
    </body>
    </html>
    """


@router.get("/admin/sports/soul-wizard", response_class=HTMLResponse)
async def admin_soul_wizard(
    request: Request,
    current_empleado: Empleado = Depends(require_tournament_admin),
):
    csrf_input = _tournament_admin_csrf_input(request)
    return HTMLResponse(
        content=_render_soul_wizard_admin_page(
            current_empleado=current_empleado,
            csrf_input=csrf_input,
        )
    )


@router.post("/admin/sports/soul-wizard", response_class=HTMLResponse)
async def admin_soul_wizard_review(
    request: Request,
    current_empleado: Empleado = Depends(require_tournament_admin),
    _csrf: None = Depends(require_tournament_admin_csrf),
):
    form = await request.form()
    form_data = {str(key): str(value) for key, value in form.items()}
    payload = build_soul_wizard_payload_from_form(form_data)
    return HTMLResponse(
        content=_render_soul_wizard_admin_page(
            current_empleado=current_empleado,
            csrf_input=_tournament_admin_csrf_input(request),
            payload=payload,
            form_data=form_data,
        )
    )


@router.get("/admin/torneos", response_class=HTMLResponse)
async def admin_tournaments(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = Depends(require_tournament_admin),
):
    """Admin interface for managing gastos projects and linked tournaments."""
    csrf_input = _tournament_admin_csrf_input(request)
    try:
        await _ensure_tournaments_admin_schema(session)
    except Exception as exc:
        await session.rollback()
        logger.warning("Tournament admin schema ensure skipped: %s", exc)
    result = await session.execute(
        select(Tournament).order_by(
            Tournament.active.desc(),
            Tournament.display_order,
            Tournament.name,
        )
    )
    tournaments = result.scalars().all()
    links_by_tournament_id: dict[str, TournamentOperationsLink] = {}
    if tournaments:
        link_result = await session.execute(
            select(TournamentOperationsLink).where(
                TournamentOperationsLink.tournament_id.in_([t.id for t in tournaments])
            )
        )
        links_by_tournament_id = {
            str(link.tournament_id): link for link in link_result.scalars().all()
        }
    operations_tournaments, operations_error = await _load_operations_tournaments()
    success_msg = (request.query_params.get("success_msg") or "").strip()
    error_msg = (request.query_params.get("error_msg") or "").strip()

    feedback_html = ""
    if success_msg:
        feedback_html += f"""
            <div style="background:#d4edda;color:#155724;border-radius:8px;padding:12px 14px;margin-bottom:16px;">
                {escape(success_msg)}
            </div>
        """
    if error_msg:
        feedback_html += f"""
            <div style="background:#f8d7da;color:#721c24;border-radius:8px;padding:12px 14px;margin-bottom:16px;">
                {escape(error_msg)}
            </div>
        """
    if operations_error:
        feedback_html += f"""
            <div style="background:#fff3cd;color:#856404;border-radius:8px;padding:12px 14px;margin-bottom:16px;">
                No se pudo cargar el catálogo de la app Torneos: {escape(operations_error)}
            </div>
        """

    create_from_torneo_options = _render_operations_tournament_options(
        operations_tournaments,
        blank_label="Selecciona un torneo de la app Torneos",
    )

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Torneos y proyectos - Copa Telmex</title>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                padding: 20px;
                min-height: 100dvh;
                max-width: 100%;
                overflow-x: hidden;
            }}
            .container {{
                max-width: 1200px;
                width: 100%;
                margin: 0 auto;
                background: white;
                border-radius: 12px;
                box-shadow: 0 10px 40px rgba(0,0,0,0.2);
                padding: 30px;
            }}
            h1 {{
                color: #333;
                margin-bottom: 10px;
                border-bottom: 3px solid #667eea;
                padding-bottom: 10px;
            }}
            .subtitle {{
                color: #666;
                margin-bottom: 30px;
            }}
            .form-section {{
                background: #f8f9fa;
                padding: 20px;
                border-radius: 8px;
                margin-bottom: 30px;
                min-width: 0;
            }}
            .form-group {{
                margin-bottom: 15px;
            }}
            label {{
                display: block;
                margin-bottom: 5px;
                font-weight: 600;
                color: #333;
            }}
            input[type="text"], input[type="number"], textarea {{
                width: 100%;
                padding: 10px;
                border: 2px solid #ddd;
                border-radius: 6px;
                font-size: 14px;
            }}
            select {{
                width: 100%;
                padding: 10px;
                border: 2px solid #ddd;
                border-radius: 6px;
                font-size: 14px;
                background: white;
            }}
            input[type="text"]:focus, input[type="number"]:focus, textarea:focus, select:focus {{
                outline: none;
                border-color: #667eea;
            }}
            textarea {{
                resize: vertical;
                min-height: 80px;
            }}
            .creation-grid {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
                gap: 20px;
            }}
            .creation-card {{
                background: white;
                border: 2px solid #e9ecef;
                border-radius: 10px;
                padding: 18px;
            }}
            .creation-card h3 {{
                margin-bottom: 8px;
            }}
            .creation-card p {{
                color: #666;
                font-size: 14px;
                margin-bottom: 16px;
            }}
            .checkbox-group {{
                display: flex;
                align-items: center;
                gap: 10px;
            }}
            input[type="checkbox"] {{
                width: 20px;
                height: 20px;
            }}
            .btn {{
                padding: 12px 24px;
                border: none;
                border-radius: 6px;
                font-size: 16px;
                font-weight: 600;
                cursor: pointer;
                transition: all 0.3s;
                text-decoration: none;
                display: inline-block;
            }}
            .btn-primary {{
                background: #667eea;
                color: white;
            }}
            .btn-primary:hover {{
                background: #5568d3;
                transform: translateY(-2px);
                box-shadow: 0 4px 12px rgba(102, 126, 234, 0.4);
            }}
            .btn-secondary {{
                background: #6c757d;
                color: white;
            }}
            .btn-secondary:hover {{
                background: #5a6268;
            }}
            .btn-danger {{
                background: #dc3545;
                color: white;
            }}
            .btn-danger:hover {{
                background: #c82333;
            }}
            .tournaments-list {{
                margin-top: 30px;
            }}
            .tournament-item {{
                background: white;
                border: 2px solid #e9ecef;
                border-radius: 8px;
                padding: 15px;
                margin-bottom: 15px;
                transition: all 0.3s;
            }}
            .tournament-item:hover {{
                border-color: #667eea;
                box-shadow: 0 4px 12px rgba(0,0,0,0.1);
            }}
            .tournament-top {{
                display: flex;
                justify-content: space-between;
                align-items: flex-start;
                gap: 16px;
            }}
            .tournament-info {{
                flex: 1;
            }}
            .tournament-name {{
                font-weight: 600;
                font-size: 18px;
                color: #333;
                margin-bottom: 5px;
            }}
            .tournament-description {{
                color: #666;
                font-size: 14px;
            }}
            .tournament-badges {{
                margin-top: 8px;
                display: flex;
                gap: 10px;
            }}
            .badge {{
                padding: 4px 12px;
                border-radius: 12px;
                font-size: 12px;
                font-weight: 600;
            }}
            .badge-active {{
                background: #d4edda;
                color: #155724;
            }}
            .badge-inactive {{
                background: #f8d7da;
                color: #721c24;
            }}
            .tournament-actions {{
                display: flex;
                gap: 10px;
                flex-wrap: wrap;
            }}
            .btn-small {{
                padding: 8px 16px;
                font-size: 14px;
            }}
            .link-panel {{
                margin-top: 16px;
                padding-top: 16px;
                border-top: 1px solid #e9ecef;
            }}
            .link-form {{
                display: flex;
                gap: 10px;
                align-items: center;
                flex-wrap: wrap;
            }}
            .link-form select {{
                flex: 1 1 280px;
            }}
            .helper-text {{
                color: #666;
                font-size: 13px;
                margin-top: 8px;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            {render_admin_navigation(current_empleado, "torneos", subtitle="Administra proyectos del app de gastos y lígalos con torneos de la app operativa.")}
            <h1>🏆 Torneos y proyectos</h1>
            <p class="subtitle">Administra proyectos del app de gastos y, si hace falta, lígalos con torneos de la app operativa.</p>
            {feedback_html}

            <div class="form-section">
                <h2 style="margin-bottom: 15px;">➕ Crear proyecto en gastos</h2>
                <div class="creation-grid">
                    <div class="creation-card">
                        <h3>Crear proyecto manualmente</h3>
                        <p>Crea un proyecto local en el app de gastos sin depender del catálogo de Torneos.</p>
                        <form method="POST" action="/admin/torneos/create">
                            {csrf_input}
                            <div class="form-group">
                                <label for="manual_name">Nombre del proyecto</label>
                                <input type="text" id="manual_name" name="name" required>
                            </div>
                            <div class="form-group">
                                <label for="manual_description">Descripción</label>
                                <textarea id="manual_description" name="description" placeholder="Describe el proyecto o torneo..."></textarea>
                            </div>
                            <div class="form-group">
                                <label for="manual_display_order">Orden de visualización</label>
                                <input type="number" id="manual_display_order" name="display_order" value="0" min="0">
                            </div>
                            <div class="form-group">
                                <label for="manual_cuenta">Cuenta Contable</label>
                                <input type="text" id="manual_cuenta" name="cuenta_contable_relacionada" placeholder="Pendiente de asignar">
                            </div>
                            <div class="form-group">
                                <label for="manual_etapas">Etapas del proyecto</label>
                                <textarea id="manual_etapas" name="etapas" rows="4" placeholder="Una por línea"></textarea>
                            </div>
                            <div class="form-group">
                                <label for="manual_categorias">Categoría</label>
                                <textarea id="manual_categorias" name="categorias" rows="4" placeholder="Una por línea"></textarea>
                                <small style="color: #666; display: block; margin-top: 5px;">Opciones de categoría para este proyecto.</small>
                            </div>
                            <div class="form-group">
                                <label>Visible en formularios para</label>
                                {render_form_visibility_areas_checkboxes(None, html_id_prefix="manual_vis")}
                            </div>
                            <div class="form-group">
                                <div class="checkbox-group">
                                    <input type="checkbox" id="manual_active" name="active" checked>
                                    <label for="manual_active" style="margin: 0;">Activo y visible</label>
                                </div>
                            </div>
                            <a href="/assistant" class="btn btn-primary" title="Abre un caso gobernado en el asistente">Crear mediante el asistente</a>
                        </form>
                    </div>
                    <div class="creation-card">
                        <h3>Crear proyecto desde torneo existente</h3>
                        <p>Crea un proyecto local usando un torneo ya existente en la app de Torneos y deja la liga guardada desde el inicio.</p>
                        <form method="POST" action="/admin/torneos/create/from-torneo">
                            {csrf_input}
                            <div class="form-group">
                                <label for="source_tournament_id">Torneo origen</label>
                                <select id="source_tournament_id" name="linked_operations_tournament_id" {'disabled' if not operations_tournaments else ''} required>
                                    {create_from_torneo_options}
                                </select>
                            </div>
                            <div class="form-group">
                                <label for="source_display_order">Orden de visualización</label>
                                <input type="number" id="source_display_order" name="display_order" value="-1" min="-1">
                                <small style="color: #666; display: block; margin-top: 5px;">Usa <code>-1</code> para colocarlo al final automáticamente.</small>
                            </div>
                            <div class="form-group">
                                <label>Visible en formularios para</label>
                                {render_form_visibility_areas_checkboxes(DEFAULT_OPERATIONS_ONLY_VISIBILITY, html_id_prefix="source_vis")}
                            </div>
                            <div class="form-group">
                                <div class="checkbox-group">
                                    <input type="checkbox" id="source_active" name="active" checked>
                                    <label for="source_active" style="margin: 0;">Activo y visible</label>
                                </div>
                            </div>
                            <a href="/assistant" class="btn btn-primary" title="Abre un caso gobernado en el asistente">Crear mediante el asistente</a>
                        </form>
                    </div>
                </div>
            </div>

            <div class="tournaments-list">
                <h2 style="margin-bottom: 20px;">📋 Proyectos ({len(tournaments)})</h2>
                {"<p style='color: #666;'>No hay proyectos aún. Usa una de las rutas de creación arriba.</p>" if not tournaments else ""}
    """

    for tournament in tournaments:
        status_class = "badge-active" if tournament.active else "badge-inactive"
        status_text = "Activo" if tournament.active else "Inactivo"
        link_record = links_by_tournament_id.get(str(tournament.id))
        linked_item = _find_operations_tournament(
            operations_tournaments,
            getattr(link_record, "operations_tournament_id", None),
        )
        linked_label = ""
        if linked_item:
            linked_label = _operations_tournament_label(linked_item)
        elif getattr(link_record, "operations_tournament_slug", None):
            linked_label = str(link_record.operations_tournament_slug)
        elif getattr(link_record, "operations_tournament_id", None):
            linked_label = str(link_record.operations_tournament_id)
        link_options_html = _render_operations_tournament_options(
            operations_tournaments,
            selected_id=getattr(link_record, "operations_tournament_id", None),
            blank_label="Sin ligar",
        )
        html_content += f"""
                <div class="tournament-item">
                    <div class="tournament-top">
                        <div class="tournament-info">
                            <div class="tournament-name">{escape(tournament.name)}</div>
                            {f'<div class="tournament-description">{escape(tournament.description)}</div>' if tournament.description else ''}
                            {f'<div style="color: #666; font-size: 14px; margin-top: 5px;">📒 Cuenta contable: {escape(tournament.cuenta_contable_relacionada)}</div>' if tournament.cuenta_contable_relacionada else ''}
                            {f'<div style="color: #666; font-size: 14px; margin-top: 5px;">🔗 Ligado a Torneos: {escape(linked_label)}</div>' if linked_label else '<div style="color: #999; font-size: 14px; margin-top: 5px;">🔗 Sin liga con la app Torneos</div>'}
                            <div class="tournament-badges">
                                <span class="badge {status_class}">{status_text}</span>
                                <span class="badge" style="background: #e9ecef; color: #495057;">Orden: {tournament.display_order}</span>
                                <span class="badge" style="background: #e7f1ff; color: #1e3a5f;" title="Áreas que ven este proyecto al capturar gastos, informes o solicitudes">Formularios: {escape(format_form_visibility_areas_label(tournament))}</span>
                            </div>
                        </div>
                        <div class="tournament-actions">
                            <a href="/admin/torneos/edit/{tournament.id}" class="btn btn-secondary btn-small">✏️ Editar</a>
                            <form method="POST" action="/admin/torneos/toggle/{tournament.id}" style="display: inline;">
                                {csrf_input}
                                <button type="submit" class="btn btn-secondary btn-small">{'Desactivar' if tournament.active else 'Activar'}</button>
                            </form>
                        </div>
                    </div>
                    <div class="link-panel">
                        <div style="font-weight: 600; margin-bottom: 8px; color: #333;">Ligar proyecto</div>
                        {"<div class='helper-text'>No se pudo cargar el catálogo remoto de Torneos en este momento.</div>" if operations_error else f'''
                        <form method="POST" action="/admin/torneos/link/{tournament.id}" class="link-form">
                            {csrf_input}
                            <select name="linked_operations_tournament_id">
                                {link_options_html}
                            </select>
                            <button type="submit" class="btn btn-primary btn-small">Guardar liga</button>
                        </form>
                        <div class="helper-text">Selecciona un torneo de la app Torneos para ligar este proyecto. Elige <strong>Sin ligar</strong> para quitar la relación.</div>
                        '''}
                    </div>
                </div>
        """

    html_content += """
            </div>
        </div>
    </body>
    </html>
    """

    return HTMLResponse(content=html_content)


@router.get("/admin/torneos/domain-alignment", response_class=HTMLResponse)
async def admin_tournaments_domain_alignment(
    request: Request,
    current_empleado: Empleado = Depends(require_tournament_admin),
):
    schema_probe = await _probe_tournaments_v2_domain_schema()
    return _render_tournaments_domain_alignment_page(
        request=request,
        current_empleado=current_empleado,
        schema_probe=schema_probe,
    )


@router.post("/admin/torneos/domain-alignment/run", response_class=HTMLResponse)
async def admin_tournaments_domain_alignment_run(
    request: Request,
    mode: str = Form("dry_run"),
    scope: str = Form(ACTIVE_TOURNAMENT_SCOPE),
    tournament_slug: str = Form(""),
    team_limit: int = Form(0),
    current_empleado: Empleado = Depends(require_tournament_admin),
    _csrf: None = Depends(require_tournament_admin_csrf),
):
    requested_mode = (mode or "dry_run").strip().lower()
    normalized_mode = "apply" if requested_mode == "apply" else "dry_run"
    scope_value = (
        scope or ACTIVE_TOURNAMENT_SCOPE
    ).strip().lower() or ACTIVE_TOURNAMENT_SCOPE
    slug_value = (tournament_slug or "").strip()
    limit_value = max(0, int(team_limit or 0))
    schema_probe = await _probe_tournaments_v2_domain_schema()

    if normalized_mode == "apply" and not _is_superadmin_role(current_empleado):
        return _render_tournaments_domain_alignment_page(
            request=request,
            current_empleado=current_empleado,
            schema_probe=schema_probe,
            mode=normalized_mode,
            scope=scope_value,
            tournament_slug=slug_value,
            team_limit=limit_value,
            error_msg="Solo un superadmin puede ejecutar apply.",
        )

    repo_root = _repo_root()
    script_path = repo_root / "scripts" / "backfill_tournaments_v2_domain_alignment.py"
    cmd = [sys.executable, str(script_path), "--scope", scope_value]
    if slug_value:
        cmd.extend(["--tournament-slug", slug_value])
    if limit_value > 0:
        cmd.extend(["--team-limit", str(limit_value)])
    if normalized_mode == "apply":
        cmd.append("--apply")

    stdout_text = ""
    stderr_text = ""
    run_summary: Optional[dict[str, Any]] = None
    return_code = None

    try:
        subprocess_env = _domain_alignment_subprocess_env(scope_value)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(repo_root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=subprocess_env,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=240
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return _render_tournaments_domain_alignment_page(
                request=request,
                current_empleado=current_empleado,
                schema_probe=schema_probe,
                mode=normalized_mode,
                scope=scope_value,
                tournament_slug=slug_value,
                team_limit=limit_value,
                error_msg="La ejecución excedió el timeout de 240s.",
            )

        return_code = proc.returncode
        stdout_text = _clean_domain_alignment_output(
            (stdout_bytes or b"").decode("utf-8", errors="replace").strip()
        )
        stderr_text = _clean_domain_alignment_output(
            (stderr_bytes or b"").decode("utf-8", errors="replace").strip()
        )
        if stdout_text:
            try:
                run_summary = json.loads(stdout_text)
            except Exception:
                run_summary = None
    except Exception as exc:
        return _render_tournaments_domain_alignment_page(
            request=request,
            current_empleado=current_empleado,
            schema_probe=schema_probe,
            mode=normalized_mode,
            scope=scope_value,
            tournament_slug=slug_value,
            team_limit=limit_value,
            error_msg=f"No se pudo ejecutar el backfill: {exc}",
        )

    output_parts = []
    if stdout_text:
        output_parts.append(stdout_text)
    if stderr_text:
        output_parts.append(f"STDERR:\n{stderr_text}")
    if not output_parts:
        output_parts.append("(sin salida)")

    return _render_tournaments_domain_alignment_page(
        request=request,
        current_empleado=current_empleado,
        schema_probe=schema_probe,
        mode=normalized_mode,
        scope=scope_value,
        tournament_slug=slug_value,
        team_limit=limit_value,
        run_output="\n\n".join(output_parts),
        run_summary=run_summary,
        run_returncode=return_code,
        error_msg=(
            None
            if (return_code == 0 or run_summary is not None)
            else "El proceso devolvió error."
        ),
    )


@router.post("/admin/torneos/domain-alignment/audit", response_class=HTMLResponse)
async def admin_tournaments_domain_alignment_audit(
    request: Request,
    scope: str = Form(ACTIVE_TOURNAMENT_SCOPE),
    tournament_slug: str = Form(""),
    team_limit: int = Form(0),
    current_empleado: Empleado = Depends(require_tournament_admin),
    _csrf: None = Depends(require_tournament_admin_csrf),
):
    scope_value = (
        scope or ACTIVE_TOURNAMENT_SCOPE
    ).strip().lower() or ACTIVE_TOURNAMENT_SCOPE
    slug_value = (tournament_slug or "").strip()
    limit_value = max(0, int(team_limit or 0))
    schema_probe = await _probe_tournaments_v2_domain_schema()

    repo_root = _repo_root()
    script_path = repo_root / "scripts" / "audit_tournaments_v2_alignment.py"
    cmd = [sys.executable, str(script_path),…24308 tokens truncated…             </div>
            </section>
            <section class="workspace-card" style="margin-bottom:18px;">
                <div class="workspace-section-title">Spotlight</div>
                <div class="workspace-section-subtitle">Los cuatro focos que más rápido explican el periodo: área, torneo, pantalla y cohorte.</div>
                <div class="cs-spotlight-grid" style="margin-top:14px;">
                    {spotlight_cards}
                </div>
            </section>
            <section class="workspace-card cs-table-card" style="margin-bottom:18px;">
                <div class="workspace-section-title">Usuarios</div>
                <div class="workspace-section-subtitle">Quién está usando la plataforma, cuánto tiempo estimado y cuánta variedad de áreas/páginas toca.</div>
                <table>
                    <thead>
                        <tr>
                            <th>Usuario</th>
                            <th>Rol</th>
                            <th>Minutos</th>
                            <th>Sesiones</th>
                            <th>Áreas</th>
                            <th>Páginas</th>
                            <th>Cliente</th>
                            <th>Última actividad</th>
                        </tr>
                    </thead>
                    <tbody>
                        {user_rows if user_rows else '<tr><td colspan="8">Sin actividad registrada todavía para este filtro.</td></tr>'}
                    </tbody>
                </table>
            </section>
            <section class="workspace-card cs-table-card" style="margin-bottom:18px;">
                <div class="workspace-section-title">Áreas</div>
                <div class="workspace-section-subtitle">Qué módulos están teniendo más uso real.</div>
                <table>
                    <thead>
                        <tr>
                            <th>Área</th>
                            <th>Usuarios</th>
                            <th>Minutos</th>
                            <th>Páginas</th>
                            <th>Última actividad</th>
                        </tr>
                    </thead>
                    <tbody>
                        {area_rows if area_rows else '<tr><td colspan="5">Sin áreas registradas para este filtro.</td></tr>'}
                    </tbody>
                </table>
            </section>
            <section class="workspace-card cs-table-card" style="margin-bottom:18px;">
                <div class="workspace-section-title">Torneos / clientes</div>
                <div class="workspace-section-subtitle">Segmentación por torneo rastreado y etiqueta de cliente capturada en el heartbeat.</div>
                <table>
                    <thead>
                        <tr>
                            <th>Torneo</th>
                            <th>Cliente</th>
                            <th>Usuarios</th>
                            <th>Minutos</th>
                            <th>Páginas</th>
                            <th>Última actividad</th>
                        </tr>
                    </thead>
                    <tbody>
                        {tournament_rows if tournament_rows else '<tr><td colspan="6">Sin torneos/clientes registrados para este filtro.</td></tr>'}
                    </tbody>
                </table>
            </section>
            <section class="workspace-card cs-table-card" style="margin-bottom:18px;">
                <div class="workspace-section-title">Cohortes</div>
                <div class="workspace-section-subtitle">Primer día de uso por usuario dentro del filtro actual, útil para ver adopción por oleadas.</div>
                <table>
                    <thead>
                        <tr>
                            <th>Cohorte</th>
                            <th>Usuarios</th>
                            <th>Promedio minutos</th>
                            <th>Última actividad</th>
                        </tr>
                    </thead>
                    <tbody>
                        {cohort_rows if cohort_rows else '<tr><td colspan="4">Sin cohortes registradas para este filtro.</td></tr>'}
                    </tbody>
                </table>
            </section>
            <section class="workspace-card cs-table-card">
                <div class="workspace-section-title">Páginas</div>
                <div class="workspace-section-subtitle">Qué pantallas específicas están concentrando el uso.</div>
                <table>
                    <thead>
                        <tr>
                            <th>Página</th>
                            <th>Área</th>
                            <th>Usuarios</th>
                            <th>Minutos</th>
                            <th>Última actividad</th>
                        </tr>
                    </thead>
                    <tbody>
                        {page_rows if page_rows else '<tr><td colspan="5">Sin páginas registradas para este filtro.</td></tr>'}
                    </tbody>
                </table>
            </section>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html)


@router.get("/admin/customer-success/uso/export")
async def admin_customer_success_usage_export(
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = Depends(
        require_permission_factory(
            ["admin.customer_success.read", "admin.*"],
            allowed_roles=["admin", "superadmin", "super_admin"],
        )
    ),
    days: int = Query(14),
    area: Optional[str] = Query(None),
    tournament_id: Optional[str] = Query(None),
    customer_label: Optional[str] = Query(None),
    limit: int = Query(200),
    view: str = Query("users"),
):
    report = await build_customer_success_usage_report(
        session,
        days=days,
        area=area,
        tournament_id=tournament_id,
        customer_label=customer_label,
        limit=limit,
    )
    header, rows = customer_success_usage_csv_rows(report, view=view)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    writer.writerows(rows)
    csv_body = buffer.getvalue()
    filename = f"customer-success-{str(view or 'users').strip().lower()}-{max(1, min(int(days or 14), 180))}d.csv"
    return Response(
        content=csv_body,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/admin/customer-success/bitacora", response_class=HTMLResponse)
async def admin_customer_success_audit_log(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = Depends(
        require_permission_factory(
            ["admin.*"],
            allowed_roles=["superadmin", "super_admin"],
        )
    ),
    actor_empleado_id: Optional[str] = Query(None),
    target_empleado_id: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    documento_referencia: Optional[str] = Query(None),
    ip_address: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    limit: int = Query(100),
):
    if not is_superadmin_role(getattr(current_empleado, "rol", None)):
        raise HTTPException(status_code=403, detail="Sólo superadmin puede consultar la bitácora.")

    empleados_result = await session.execute(
        text(
            """
            SELECT id, nombre, correo, rol
            FROM empleados
            WHERE activo = TRUE
            ORDER BY lower(nombre), lower(correo)
            """
        )
    )
    empleados = empleados_result.mappings().all()
    report = await build_customer_success_audit_report(
        session,
        actor_empleado_id=actor_empleado_id,
        target_empleado_id=target_empleado_id,
        action=action,
        documento_referencia=documento_referencia,
        ip_address=ip_address,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
    )
    events = list(report.get("events") or [])
    telegram_by_documento = report.get("telegram_by_documento") or {}
    selected_limit = max(10, min(int(limit or 100), 500))

    def _employee_options(selected: Optional[str]) -> str:
        options = ['<option value="">Todos</option>']
        selected_raw = str(selected or "")
        for empleado in empleados:
            empleado_id = str(empleado.get("id") or "")
            label = (
                f"{empleado.get('nombre') or 'Sin nombre'} · "
                f"{empleado.get('correo') or 'sin correo'} · "
                f"{empleado.get('rol') or 'empleado'}"
            )
            selected_attr = " selected" if empleado_id == selected_raw else ""
            options.append(
                f'<option value="{escape(empleado_id)}"{selected_attr}>{escape(label)}</option>'
            )
        return "".join(options)

    def _telegram_html(documento_id: Any) -> str:
        if not documento_id:
            return '<span class="audit-muted">Sin documento</span>'
        rows = telegram_by_documento.get(str(documento_id), [])
        if not rows:
            return '<span class="audit-muted">Sin alertas registradas</span>'
        chips = []
        for row in rows[:4]:
            status = str(row.get("status") or "unknown")
            status_class = "ok" if status == "sent" else ("warn" if status == "skipped" else "bad")
            label = row.get("notification_type") or "telegram"
            recipient = row.get("recipient_nombre") or row.get("telegram_chat_id") or "—"
            sent_at = row.get("sent_at") or row.get("created_at") or "—"
            error = row.get("error_message") or ""
            title = f' title="{escape(str(error))}"' if error else ""
            chips.append(
                f'<span class="audit-chip {status_class}"{title}>{escape(str(label))} · {escape(status)} · {escape(str(recipient))} · {escape(str(sent_at))}</span>'
            )
        if len(rows) > 4:
            chips.append(f'<span class="audit-chip">+{len(rows) - 4}</span>')
        return "".join(chips)

    event_rows = ""
    for event in events:
        actor_label = event.get("actor_nombre") or event.get("actor_correo") or "no registrado"
        target_label = event.get("target_nombre") or event.get("target_correo") or "no registrado"
        doc_label = event.get("documento_referencia") or "—"
        doc_meta = " · ".join(
            part
            for part in [
                str(event.get("documento_tipo") or "").strip(),
                str(event.get("documento_estado") or "").strip(),
            ]
            if part
        )
        event_rows += f"""
        <tr>
            <td>
                <div style="font-weight:700;">{escape(str(event.get("created_at") or "—"))}</div>
                <div class="audit-muted">{escape(str(event.get("surface") or "web"))}</div>
            </td>
            <td><span class="audit-action">{escape(str(event.get("action") or "—"))}</span></td>
            <td>
                <div>{escape(str(actor_label))}</div>
                <div class="audit-muted">{escape(str(event.get("actor_correo") or "—"))}</div>
            </td>
            <td>
                <div>{escape(str(target_label))}</div>
                <div class="audit-muted">{escape(str(event.get("target_correo") or "—"))}</div>
            </td>
            <td>
                <div style="font-weight:700;">{escape(str(doc_label))}</div>
                <div class="audit-muted">{escape(doc_meta or "—")}</div>
            </td>
            <td>
                <div>{escape(str(event.get("ip_address") or "no registrado"))}</div>
                <div class="audit-muted">{escape(str(event.get("request_method") or ""))} {escape(str(event.get("request_path") or ""))}</div>
            </td>
            <td>{escape(str(event.get("summary") or "—"))}</td>
            <td>{_telegram_html(event.get("documento_id"))}</td>
        </tr>
        """

    page_styles = """
        .audit-filters {
            display:grid;
            grid-template-columns:repeat(auto-fit,minmax(190px,1fr));
            gap:12px;
            align-items:end;
        }
        .audit-filters label {
            display:block;
            margin-bottom:6px;
            color:#475569;
            font-size:12px;
            font-weight:800;
        }
        .audit-input, .audit-select {
            width:100%;
            padding:10px 11px;
            border:1px solid #cbd5e1;
            border-radius:12px;
            background:#fff;
            color:#0f172a;
        }
        .audit-table {
            width:100%;
            border-collapse:separate;
            border-spacing:0;
        }
        .audit-table th, .audit-table td {
            padding:12px 10px;
            border-bottom:1px solid #e2e8f0;
            text-align:left;
            vertical-align:top;
            font-size:13px;
        }
        .audit-table th {
            color:#64748b;
            font-size:11px;
            text-transform:uppercase;
            letter-spacing:.10em;
            background:#f8fafc;
        }
        .audit-muted {
            margin-top:3px;
            color:#64748b;
            font-size:12px;
            line-height:1.35;
        }
        .audit-action {
            display:inline-flex;
            padding:5px 8px;
            border-radius:999px;
            background:#eef2ff;
            color:#3730a3;
            border:1px solid rgba(55,48,163,.12);
            font-size:11px;
            font-weight:800;
        }
        .audit-chip {
            display:inline-flex;
            margin:0 4px 4px 0;
            padding:5px 8px;
            border-radius:999px;
            background:#f8fafc;
            color:#334155;
            border:1px solid #dbe2ea;
            font-size:11px;
            font-weight:700;
        }
        .audit-chip.ok { background:#ecfdf5;color:#047857;border-color:#a7f3d0; }
        .audit-chip.warn { background:#fffbeb;color:#92400e;border-color:#fde68a; }
        .audit-chip.bad { background:#fef2f2;color:#991b1b;border-color:#fecaca; }
    """
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>Bitácora - Customer Success</title>
        <style>{_admin_workspace_styles("1680px")}{page_styles}</style>
    </head>
    <body>
        <div class="workspace-shell">
            {render_admin_navigation(current_empleado, "customer_success_audit", subtitle="Bitácora operativa de pruebas y acciones críticas. Sólo superadmin.")}
            {_render_admin_workspace_hero(
                eyebrow="Customer Success",
                title="Bitácora operativa",
                description="Consulta quién hizo qué, cuándo, desde qué IP y qué alertas de Telegram se generaron para sustentar pruebas y operación.",
                actions_html=(
                    '<form method="GET" action="/admin/customer-success/bitacora" class="audit-filters">'
                    f'<div><label>Actor</label><select class="audit-select" name="actor_empleado_id">{_employee_options(actor_empleado_id)}</select></div>'
                    f'<div><label>Empleado afectado</label><select class="audit-select" name="target_empleado_id">{_employee_options(target_empleado_id)}</select></div>'
                    f'<div><label>Acción</label><input class="audit-input" name="action" value="{escape(str(action or ""))}" placeholder="documento.approved"></div>'
                    f'<div><label>Referencia</label><input class="audit-input" name="documento_referencia" value="{escape(str(documento_referencia or ""))}" placeholder="S-26000013"></div>'
                    f'<div><label>IP</label><input class="audit-input" name="ip_address" value="{escape(str(ip_address or ""))}" placeholder="187.207..."></div>'
                    f'<div><label>Desde</label><input class="audit-input" type="date" name="date_from" value="{escape(str(date_from or ""))}"></div>'
                    f'<div><label>Hasta</label><input class="audit-input" type="date" name="date_to" value="{escape(str(date_to or ""))}"></div>'
                    f'<div><label>Límite</label><input class="audit-input" type="number" min="10" max="500" name="limit" value="{selected_limit}"></div>'
                    '<div style="display:flex;gap:8px;flex-wrap:wrap;"><button class="button" type="submit">Filtrar</button><a class="button secondary" href="/admin/customer-success/bitacora">Limpiar</a></div>'
                    '</form>'
                ),
                side_html=(
                    f'<div class="meta-grid">'
                    f'<div class="meta-card"><span>Eventos</span><strong>{len(events)}</strong><small>Resultado actual</small></div>'
                    f'<div class="meta-card"><span>Acceso</span><strong>Superadmin</strong><small>Auditoría restringida</small></div>'
                    f'<div class="meta-card"><span>IP</span><strong>Incluida</strong><small>XFF/X-Real-IP/client</small></div>'
                    f"</div>"
                ),
            )}
            <section class="workspace-card">
                <div class="workspace-section-title">Eventos</div>
                <div class="workspace-section-subtitle">Las filas nuevas guardan IP y origen cuando la acción viene por web. La bitácora empieza a registrar eventos desde esta versión.</div>
                <div style="overflow-x:auto;overflow-y:visible;margin-top:14px;">
                    <table class="audit-table">
                        <thead>
                            <tr>
                                <th>Fecha/hora</th>
                                <th>Acción</th>
                                <th>Actor</th>
                                <th>Empleado</th>
                                <th>Documento</th>
                                <th>IP / Ruta</th>
                                <th>Resumen</th>
                                <th>Telegram</th>
                            </tr>
                        </thead>
                        <tbody>{event_rows if event_rows else '<tr><td colspan="8">Sin eventos para el filtro actual.</td></tr>'}</tbody>
                    </table>
                </div>
            </section>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html)


@router.get("/admin/perfiles", response_class=HTMLResponse)
async def admin_perfiles(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = Depends(
        require_permission_factory(
            ["admin.perfiles.manage", "admin.perfiles.*"],
            allowed_roles=["admin", "superadmin", "super_admin"],
        )
    ),
    success_msg: Optional[str] = Query(None),
    error_msg: Optional[str] = Query(None),
    compare_left: Optional[str] = Query(None),
    compare_right: Optional[str] = Query(None),
):
    """Admin UI for access profiles, presets, scopes and assignments."""
    await _ensure_access_profiles_schema(session)

    profiles_result = await session.execute(
        text(
            """
            SELECT
                p.id, p.profile_key, p.name, p.description, p.base_role, p.permissions, p.active,
                COUNT(a.id) AS assignments
            FROM access_profiles p
            LEFT JOIN empleado_access_profiles a ON a.profile_id = p.id
            GROUP BY p.id, p.profile_key, p.name, p.description, p.base_role, p.permissions, p.active
            ORDER BY p.active DESC, p.name ASC
            """
        )
    )
    profiles = profiles_result.fetchall()

    empleados_result = await session.execute(
        text(
            "SELECT id, nombre, correo, rol FROM empleados WHERE activo = TRUE ORDER BY nombre"
        )
    )
    empleados = empleados_result.fetchall()

    assignments_result = await session.execute(
        text(
            """
            SELECT
                a.id,
                a.empleado_id,
                e.nombre AS empleado_nombre,
                e.correo AS empleado_correo,
                e.rol AS empleado_rol,
                p.id AS profile_id,
                p.name AS profile_name,
                p.profile_key AS profile_key,
                p.base_role AS profile_base_role,
                p.permissions AS profile_permissions,
                a.is_primary,
                a.created_at
            FROM empleado_access_profiles a
            JOIN empleados e ON e.id = a.empleado_id
            JOIN access_profiles p ON p.id = a.profile_id
            ORDER BY a.created_at DESC
            LIMIT 400
            """
        )
    )
    assignments = assignments_result.fetchall()
    audit_result = await session.execute(
        text(
            """
            SELECT
                log.event_type,
                log.payload,
                log.created_at,
                actor.nombre AS actor_nombre,
                target.nombre AS empleado_nombre,
                profile.name AS profile_name
            FROM access_profile_audit_log log
            LEFT JOIN empleados actor ON actor.id = log.actor_empleado_id
            LEFT JOIN empleados target ON target.id = log.empleado_id
            LEFT JOIN access_profiles profile ON profile.id = log.profile_id
            ORDER BY log.created_at DESC
            LIMIT 80
            """
        )
    )
    audit_rows = audit_result.fetchall()

    success_html = (
        f'<div style="background:#d4edda;border:1px solid #c3e6cb;color:#155724;padding:10px;border-radius:6px;margin-bottom:12px;"><strong>✅ {escape(success_msg)}</strong></div>'
        if success_msg
        else ""
    )
    error_html = (
        f'<div style="background:#f8d7da;border:1px solid #f5c6cb;color:#721c24;padding:10px;border-radius:6px;margin-bottom:12px;"><strong>⚠️ {escape(error_msg)}</strong></div>'
        if error_msg
        else ""
    )

    profile_rows = ""
    for p in profiles:
        (
            p_id,
            p_key,
            p_name,
            p_desc,
            p_base_role,
            p_permissions,
            p_active,
            p_assigned,
        ) = p
        permissions_text = json.dumps(p_permissions or {}, ensure_ascii=False, indent=2)
        selected_tokens = _collect_profile_tokens(p_permissions)
        selected_scopes = _profile_scopes(p_permissions)
        preset_key = (
            str((p_permissions or {}).get("preset_key") or "").strip().lower()
            if isinstance(p_permissions, dict)
            else ""
        )
        token_badges = "".join(
            f'<span class="pill">{escape(token)}</span>'
            for token in list(sorted(selected_tokens))[:8]
        )
        scope_badges = "".join(
            f'<span class="pill pill-scope">{escape(scope)}</span>'
            for scope in selected_scopes[:6]
        )
        active_checked = "checked" if p_active else ""
        profile_rows += f"""
        <tr>
            <td><code>{escape(str(p_key or ""))}</code></td>
            <td>{escape(str(p_name or ""))}</td>
            <td>{escape(str(p_base_role or "empleado"))}</td>
            <td>{int(p_assigned or 0)}</td>
            <td>{'Sí' if p_active else 'No'}</td>
            <td>
                <div style="margin-bottom:8px;">
                    <div style="font-size:12px;color:#475569;">{len(selected_tokens)} permisos · {len(selected_scopes)} scopes</div>
                    <div class="pills">{token_badges or '<span style="color:#64748b;">Sin tokens explícitos</span>'}</div>
                    <div class="pills">{scope_badges}</div>
                </div>
                <details>
                    <summary style="cursor:pointer;color:#3b82f6;">Editar</summary>
                    <form method="POST" action="/admin/perfiles/update/{p_id}" style="margin-top:8px;padding:8px;border:1px solid #e5e7eb;border-radius:6px;">
                        <label>Nombre</label>
                        <input type="text" name="name" value="{escape(str(p_name or ''))}" required style="width:100%;margin-bottom:6px;">
                        <label>Descripción</label>
                        <input type="text" name="description" value="{escape(str(p_desc or ''))}" style="width:100%;margin-bottom:6px;">
                        <label>Rol base</label>
                        <select name="base_role" style="width:100%;margin-bottom:6px;">
                            <option value="empleado" {"selected" if p_base_role == "empleado" else ""}>empleado</option>
                            <option value="coordinador" {"selected" if p_base_role == "coordinador" else ""}>coordinador</option>
                            <option value="finanzas" {"selected" if p_base_role == "finanzas" else ""}>finanzas</option>
                            <option value="admin" {"selected" if p_base_role == "admin" else ""}>admin</option>
                            <option value="superadmin" {"selected" if p_base_role == "superadmin" else ""}>superadmin</option>
                        </select>
                        <label>Preset</label>
                        <select name="preset_key" data-profile-preset-select="1" style="width:100%;margin-bottom:6px;">
                            {_render_preset_options(preset_key)}
                        </select>
                        <div class="matrix-wrap">{_render_profile_matrix(form_prefix="", selected_tokens=selected_tokens)}</div>
                        <div style="margin-top:8px;">{_render_profile_scope_inputs(form_prefix="", selected_scopes=selected_scopes)}</div>
                        <details style="margin-top:8px;">
                            <summary style="cursor:pointer;">JSON avanzado</summary>
                            <textarea name="permissions_json" rows="5" style="width:100%;font-family:monospace;margin-top:8px;">{escape(permissions_text)}</textarea>
                        </details>
                        <label style="display:block;margin-top:6px;"><input type="checkbox" name="active" {active_checked}> Activo</label>
                        <button type="submit" style="margin-top:8px;">Guardar</button>
                    </form>
                </details>
            </td>
        </tr>
        """

    empleado_options = "".join(
        f'<option value="{e[0]}">{escape(str(e[1] or ""))} ({escape(str(e[3] or "empleado"))})</option>'
        for e in empleados
    )
    profile_options = "".join(
        f'<option value="{p[0]}">{escape(str(p[2] or ""))} [{escape(str(p[1] or ""))}]</option>'
        for p in profiles
        if p[6]
    )

    assignment_rows = ""
    effective_preview_map: dict[str, dict[str, Any]] = {}
    for a in assignments:
        (
            a_id,
            empleado_id,
            empleado_nombre,
            empleado_correo,
            empleado_rol,
            _profile_id,
            profile_name,
            profile_key,
            profile_base_role,
            profile_permissions,
            is_primary,
            created_at,
        ) = a
        empleado_key = str(empleado_id)
        preview_entry = effective_preview_map.setdefault(
            empleado_key,
            {
                "empleado_nombre": str(empleado_nombre or ""),
                "empleado_correo": str(empleado_correo or ""),
                "empleado_rol": str(empleado_rol or "empleado"),
                "profile_names": [],
                "permission_payloads": [],
            },
        )
        preview_entry["profile_names"].append(str(profile_name or profile_key or ""))
        preview_entry["permission_payloads"].append(profile_permissions)
        assignment_rows += f"""
        <tr>
            <td>{escape(str(empleado_nombre or ""))}<br><small style="color:#6b7280;">{escape(str(empleado_correo or ""))}</small></td>
            <td><code>{escape(str(profile_key or ""))}</code> - {escape(str(profile_name or ""))}</td>
            <td>{escape(str(profile_base_role or "empleado"))}</td>
            <td>{'Sí' if is_primary else 'No'}</td>
            <td>{format_value(created_at)}</td>
            <td>
                <form method="POST" action="/admin/perfiles/unassign/{a_id}" onsubmit="return confirm('¿Quitar perfil de este usuario?');">
                    <button type="submit" style="background:#ef4444;color:#fff;border:none;padding:6px 10px;border-radius:4px;cursor:pointer;">Quitar</button>
                </form>
            </td>
        </tr>
        """

    effective_preview_cards = ""
    for preview in effective_preview_map.values():
        effective = _build_effective_profile_preview(
            empleado_role=preview["empleado_rol"],
            permission_payloads=list(preview["permission_payloads"]),
        )
        preview["effective"] = effective
        token_badges = "".join(
            f'<span class="pill">{escape(token)}</span>'
            for token in effective["tokens"][:10]
        )
        scope_badges = "".join(
            f'<span class="pill pill-scope">{escape(scope)}</span>'
            for scope in effective["scopes"][:8]
        )
        surface_badges = (
            "".join(
                f'<span class="pill">{escape(label)}</span>'
                for label in effective["enabled_surfaces"]
            )
            or '<span style="color:#64748b;">Sin superficie destacada</span>'
        )
        effective_preview_cards += f"""
        <div class="preset-card">
            <div style="display:flex;justify-content:space-between;gap:10px;align-items:flex-start;">
                <div>
                    <div style="font-weight:700;">{escape(preview["empleado_nombre"])}</div>
                    <div style="font-size:12px;color:#64748b;">{escape(preview["empleado_correo"])} · rol base `{escape(preview["empleado_rol"])}`</div>
                </div>
                <span class="pill">{len(preview["profile_names"])} perfiles</span>
            </div>
            <div style="font-size:12px;color:#475569;margin-top:8px;">Tokens efectivos: {effective["token_count"]} · scopes: {effective["scope_count"]}</div>
            <div class="pills">{surface_badges}</div>
            <div class="pills">{token_badges or '<span style="color:#64748b;">Sin tokens visibles</span>'}</div>
            <div class="pills">{scope_badges}</div>
        </div>
        """

    employee_beneficiary_access_html = _render_employee_beneficiary_access_summary(
        effective_preview_map
    )

    comparison_html = '<div style="color:#64748b;">Se necesitan al menos dos empleados con perfiles activos para comparar.</div>'
    comparison_options = ""
    sorted_preview_items = sorted(
        effective_preview_map.items(),
        key=lambda item: (
            str(item[1].get("empleado_nombre") or "").lower(),
            str(item[0]),
        ),
    )
    if len(sorted_preview_items) >= 2:
        preview_lookup = {str(key): value for key, value in sorted_preview_items}
        left_key = str(compare_left or sorted_preview_items[0][0])
        right_key = str(compare_right or sorted_preview_items[1][0])
        if left_key not in preview_lookup:
            left_key = str(sorted_preview_items[0][0])
        if right_key not in preview_lookup or right_key == left_key:
            for candidate_key, _candidate_preview in sorted_preview_items:
                if str(candidate_key) != left_key:
                    right_key = str(candidate_key)
                    break

        for empleado_key, preview in sorted_preview_items:
            empleado_id = str(empleado_key)
            selected_left = " selected" if empleado_id == left_key else ""
            selected_right = " selected" if empleado_id == right_key else ""
            option_label = (
                f'{preview["empleado_nombre"]} ({preview["empleado_rol"]})'
                if preview.get("empleado_nombre")
                else empleado_id
            )
            comparison_options += f'<option value="{escape(empleado_id)}"{selected_left}>{escape(option_label)}</option>'
        comparison_right_options = ""
        for empleado_key, preview in sorted_preview_items:
            empleado_id = str(empleado_key)
            selected_right = " selected" if empleado_id == right_key else ""
            option_label = (
                f'{preview["empleado_nombre"]} ({preview["empleado_rol"]})'
                if preview.get("empleado_nombre")
                else empleado_id
            )
            comparison_right_options += f'<option value="{escape(empleado_id)}"{selected_right}>{escape(option_label)}</option>'

        left_preview_entry = preview_lookup[left_key]
        right_preview_entry = preview_lookup[right_key]
        comparison = _build_effective_profile_comparison(
            left_label=str(left_preview_entry["empleado_nombre"] or left_key),
            left_preview=dict(left_preview_entry["effective"]),
            right_label=str(right_preview_entry["empleado_nombre"] or right_key),
            right_preview=dict(right_preview_entry["effective"]),
        )

        def _pill_html(items: list[str], class_name: str = "pill") -> str:
            if not items:
                return '<span style="color:#64748b;">Ninguno</span>'
            return "".join(
                f'<span class="{class_name}">{escape(item)}</span>'
                for item in items[:14]
            )

        comparison_html = f"""
            <form method="GET" action="/admin/perfiles" style="display:grid;grid-template-columns:1fr 1fr auto;gap:12px;align-items:end;margin-bottom:14px;">
                <div>
                    <label>Empleado A</label>
                    <select name="compare_left">{comparison_options}</select>
                </div>
                <div>
                    <label>Empleado B</label>
                    <select name="compare_right">{comparison_right_options}</select>
                </div>
                <div>
                    <button type="submit">Comparar</button>
                </div>
            </form>
            <div class="grid" style="grid-template-columns:1fr 1fr;gap:14px;">
                <div class="preset-card">
                    <h3 style="margin-top:0;">{escape(comparison["left_label"])}</h3>
                    <div style="font-size:12px;color:#475569;">Sólo este usuario</div>
                    <div class="pills">{_pill_html(comparison["left_only_surfaces"])}</div>
                    <div class="pills">{_pill_html(comparison["left_only_tokens"])}</div>
                    <div class="pills">{_pill_html(comparison["left_only_scopes"], "pill pill-scope")}</div>
                </div>
                <div class="preset-card">
                    <h3 style="margin-top:0;">{escape(comparison["right_label"])}</h3>
                    <div style="font-size:12px;color:#475569;">Sólo este usuario</div>
                    <div class="pills">{_pill_html(comparison["right_only_surfaces"])}</div>
                    <div class="pills">{_pill_html(comparison["right_only_tokens"])}</div>
                    <div class="pills">{_pill_html(comparison["right_only_scopes"], "pill pill-scope")}</div>
                </div>
            </div>
            <div class="preset-card" style="margin-top:14px;">
                <h3 style="margin-top:0;">Intersección efectiva</h3>
                <div style="font-size:12px;color:#475569;">Superficies, tokens y scopes compartidos por ambos perfiles efectivos.</div>
                <div class="pills">{_pill_html(comparison["shared_surfaces"])}</div>
                <div class="pills">{_pill_html(comparison["shared_tokens"])}</div>
                <div class="pills">{_pill_html(comparison["shared_scopes"], "pill pill-scope")}</div>
            </div>
        """

    audit_html = ""
    for (
        event_type,
        payload,
        created_at,
        actor_nombre,
        target_nombre,
        profile_name,
    ) in audit_rows:
        payload_text = ""
        if isinstance(payload, dict):
            payload_text = json.dumps(payload, ensure_ascii=False)
        elif payload is not None:
            payload_text = str(payload)
        audit_html += f"""
        <tr>
            <td><code>{escape(str(event_type or ""))}</code></td>
            <td>{escape(str(profile_name or "—"))}</td>
            <td>{escape(str(target_nombre or "—"))}</td>
            <td>{escape(str(actor_nombre or "Sistema"))}</td>
            <td>{format_value(created_at)}</td>
            <td><small style="color:#64748b;">{escape(payload_text[:260])}</small></td>
        </tr>
        """

    preset_cards = "".join(
        f"""
        <div class="preset-card">
            <div style="display:flex;justify-content:space-between;gap:10px;align-items:flex-start;">
                <div>
                    <div style="font-weight:700;">{escape(str(preset["label"]))}</div>
                    <div style="font-size:12px;color:#64748b;">{escape(str(preset["description"]))}</div>
                </div>
                <span class="pill">{escape(str(preset["base_role"]))}</span>
            </div>
            <div class="pills">
                {''.join(f'<span class="pill">{escape(token)}</span>' for token in preset["permissions"][:6])}
            </div>
        </div>
        """
        for preset in _PROFILE_PRESETS.values()
    )
    preset_map_json = json.dumps(
        {
            key: {
                "base_role": value["base_role"],
                "permissions": value["permissions"],
            }
            for key, value in _PROFILE_PRESETS.items()
        },
        ensure_ascii=False,
    )
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>Perfiles Ad-hoc - Administración</title>
        <style>
            body {{ font-family: Arial, sans-serif; margin: 0; padding: 20px; background:#f3f4f6; }}
            .container {{ max-width: 1300px; margin:0 auto; background:#fff; border-radius:12px; padding:20px; box-shadow:0 6px 24px rgba(0,0,0,.08); }}
            .nav-links a {{ color:#2563eb; text-decoration:none; margin-right:14px; font-weight:600; }}
            .grid {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; }}
            .card {{ border:1px solid #e5e7eb; border-radius:10px; padding:14px; background:#fafafa; }}
            label {{ display:block; font-weight:600; margin-bottom:4px; }}
            input[type="text"], select, textarea {{ width:100%; border:1px solid #d1d5db; border-radius:6px; padding:8px; margin-bottom:8px; }}
            button {{ background:#2563eb; color:#fff; border:none; border-radius:6px; padding:8px 12px; cursor:pointer; }}
            table {{ width:100%; border-collapse:collapse; margin-top:8px; }}
            th, td {{ border-bottom:1px solid #e5e7eb; padding:8px; text-align:left; font-size:14px; vertical-align:top; }}
            th {{ background:#f9fafb; }}
            h1, h2 {{ margin-top:0; }}
            .preset-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:12px; }}
            .preset-card {{ border:1px solid #dbe2ea; border-radius:12px; background:#fff; padding:12px; }}
            .matrix-wrap {{ overflow:auto; border:1px solid #e5e7eb; border-radius:10px; background:#fff; }}
            .pills {{ display:flex; flex-wrap:wrap; gap:6px; margin-top:6px; }}
            .pill {{ display:inline-flex; align-items:center; padding:4px 8px; border-radius:999px; background:#e2e8f0; color:#0f172a; font-size:11px; }}
            .pill-scope {{ background:#dcfce7; color:#166534; }}
            @media (max-width: 960px) {{ .grid {{ grid-template-columns:1fr; }} }}
        </style>
    </head>
    <body>
        <div class="container">
            {render_admin_navigation(current_empleado, "perfiles", subtitle="Perfiles ad hoc para separar facultades internas sin mezclar roles base.")}
            <h1>Perfiles de Acceso Ad-hoc</h1>
            <p>Configura presets, perfiles tailored y scopes sin editar JSON manual. El JSON avanzado sigue disponible sólo como escape hatch técnico.</p>
            {success_html}
            {error_html}

            <div class="card" style="margin-bottom:16px;">
                <h2>Presets precargados</h2>
                <div class="preset-grid">{preset_cards}</div>
            </div>

            <div class="grid">
                <div class="card">
                    <h2>Crear Perfil</h2>
                    <form method="POST" action="/admin/perfiles/create">
                        <label>Clave de perfil (única)</label>
                        <input type="text" name="profile_key" required placeholder="ej: operaciones-entidad-avanzado">
                        <label>Nombre</label>
                        <input type="text" name="name" required placeholder="Operaciones Entidad Avanzado">
                        <label>Descripción</label>
                        <input type="text" name="description" placeholder="Qué habilita este perfil">
                        <label>Rol base</label>
                        <select name="base_role">
                            <option value="empleado">empleado</option>
                            <option value="coordinador">coordinador</option>
                            <option value="finanzas">finanzas</option>
                            <option value="admin">admin</option>
                            <option value="superadmin">superadmin</option>
                        </select>
                        <label>Preset</label>
                        <select name="preset_key" data-profile-preset-select="1">
                            {_render_preset_options(None)}
                        </select>
                        <div class="matrix-wrap">{_render_profile_matrix(form_prefix="", selected_tokens=set())}</div>
                        <div style="margin-top:8px;">{_render_profile_scope_inputs(form_prefix="", selected_scopes=[])}</div>
                        <details style="margin-top:8px;">
                            <summary style="cursor:pointer;">JSON avanzado</summary>
                            <textarea name="permissions_json" rows="8" style="margin-top:8px;">{{"permissions": ["admin.perfiles.manage"], "read": ["gastos", "reportes"], "write": []}}</textarea>
                            <small style="color:#6b7280;display:block;margin-top:6px;">Compatibilidad con perfiles viejos o cargas manuales.</small>
                        </details>
                        <label><input type="checkbox" name="active" checked> Activo</label>
                        <button type="submit">Crear perfil</button>
                    </form>
                </div>
                <div class="card">
                    <h2>Asignar Perfil a Usuario</h2>
                    <form method="POST" action="/admin/perfiles/assign">
                        <label>Empleado</label>
                        <select name="empleado_id" required>{empleado_options}</select>
                        <label>Perfil</label>
                        <select name="profile_id" required>{profile_options}</select>
                        <label><input type="checkbox" name="is_primary"> Marcar como perfil principal</label>
                        <label><input type="checkbox" name="apply_base_role" checked> Aplicar rol base del perfil al empleado</label>
                        <button type="submit">Asignar perfil</button>
                    </form>
                </div>
            </div>

            <div class="card" style="margin-top:16px;">
                <h2>Perfiles ({len(profiles)})</h2>
                <table>
                    <thead>
                        <tr>
                            <th>Clave</th>
                            <th>Nombre</th>
                            <th>Rol Base</th>
                            <th>Asignados</th>
                            <th>Activo</th>
                            <th>Acciones</th>
                        </tr>
                    </thead>
                    <tbody>
                        {profile_rows if profile_rows else '<tr><td colspan="6">No hay perfiles aún.</td></tr>'}
                    </tbody>
                </table>
            </div>

            <div class="card" style="margin-top:16px;">
                <h2>Permisos efectivos por usuario</h2>
                <p style="color:#475569;">Vista previa del estado efectivo actual por empleado: rol base, union de tokens/scopes activos y superficies relevantes ya habilitadas.</p>
                <div class="preset-grid">{effective_preview_cards or '<div style="color:#64748b;">Sin asignaciones activas todavía.</div>'}</div>
            </div>

            <div class="card" style="margin-top:16px;">
                <h2>Solicitudes para empleados terceros</h2>
                <p style="color:#475569;">Usuarios con perfil activo que pueden crear anticipos o informes a nombre de otro empleado sin cambiar la ruta de autorizaci?n.</p>
                {employee_beneficiary_access_html}
            </div>

            <div class="card" style="margin-top:16px;">
                <h2>Comparador de perfiles efectivos</h2>
                <p style="color:#475569;">Compara dos empleados desde el estado efectivo real, no desde un perfil aislado: union de tokens, scopes y superficies habilitadas.</p>
                {comparison_html}
            </div>

            <div class="card" style="margin-top:16px;">
                <h2>Asignaciones ({len(assignments)})</h2>
                <table>
                    <thead>
                        <tr>
                            <th>Empleado</th>
                            <th>Perfil</th>
                            <th>Rol Base Perfil</th>
                            <th>Principal</th>
                            <th>Asignado</th>
                            <th>Acción</th>
                        </tr>
                    </thead>
                    <tbody>
                        {assignment_rows if assignment_rows else '<tr><td colspan="6">No hay asignaciones.</td></tr>'}
                    </tbody>
                </table>
            </div>

            <div class="card" style="margin-top:16px;">
                <h2>Auditoría reciente ({len(audit_rows)})</h2>
                <table>
                    <thead>
                        <tr>
                            <th>Evento</th>
                            <th>Perfil</th>
                            <th>Empleado</th>
                            <th>Actor</th>
                            <th>Fecha</th>
                            <th>Payload</th>
                        </tr>
                    </thead>
                    <tbody>
                        {audit_html if audit_html else '<tr><td colspan="6">Sin eventos todavía.</td></tr>'}
                    </tbody>
                </table>
            </div>
        </div>
        <script>
            const PROFILE_PRESETS = {preset_map_json};
            function applyPresetToForm(form) {{
                const select = form.querySelector('[data-profile-preset-select="1"]');
                const presetKey = select ? select.value : "";
                if (!presetKey || !PROFILE_PRESETS[presetKey]) {{
                    return;
                }}
                const preset = PROFILE_PRESETS[presetKey];
                const roleSelect = form.querySelector('select[name="base_role"]');
                if (roleSelect && preset.base_role) {{
                    roleSelect.value = preset.base_role;
                }}
                form.querySelectorAll('input[name="permission_token"]').forEach((input) => {{
                    input.checked = false;
                }});
                (preset.permissions || []).forEach((token) => {{
                    const input = Array.from(form.querySelectorAll('input[name="permission_token"]')).find((candidate) => candidate.value === token);
                    if (input) {{
                        input.checked = true;
                    }}
                }});
            }}
            document.querySelectorAll('[data-profile-preset-select="1"]').forEach((select) => {{
                select.addEventListener('change', () => applyPresetToForm(select.closest('form')));
            }});
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html)


@router.post("/admin/perfiles/create")
async def admin_perfiles_create(
    request: Request,
    profile_key: str = Form(...),
    name: str = Form(...),
    description: Optional[str] = Form(None),
    base_role: str = Form("empleado"),
    preset_key: Optional[str] = Form(None),
    permissions_json: str = Form("{}"),
    active: Optional[str] = Form(None),
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = Depends(
        require_permission_factory(
            ["admin.perfiles.manage", "admin.perfiles.create", "admin.perfiles.*"],
            allowed_roles=["admin", "superadmin", "super_admin"],
        )
    ),
):
    await _ensure_access_profiles_schema(session)
    try:
        form = await request.form()
        key = profile_key.strip().lower()
        if not key:
            return HTMLResponse(status_code=400, content="Clave de perfil requerida")
        allowed_roles = {
            "empleado",
            "coordinador",
            "finanzas",
            "admin",
            "superadmin",
            "super_admin",
        }
        if base_role not in allowed_roles:
            base_role = "empleado"
        custom_scopes = [
            item.strip()
            for item in str(form.get("scope_custom") or "")
            .replace("\n", ",")
            .split(",")
            if item.strip()
        ]
        permissions_obj = _build_profile_permissions_payload(
            preset_key=preset_key,
            permission_tokens=[str(item) for item in form.getlist("permission_token")],
            scope_tokens=[str(item) for item in form.getlist("scope_token")]
            + custom_scopes,
            permissions_json=permissions_json,
        )
        is_active = bool(active)
        new_profile_id = str(uuid4())
        await session.execute(
            text(
                """
                INSERT INTO access_profiles (
                    id, profile_key, name, description, base_role, permissions, active,
                    created_by_empleado_id, created_at, updated_at
                ) VALUES (
                    :id, :profile_key, :name, :description, :base_role, CAST(:permissions AS jsonb), :active,
                    :created_by, NOW(), NOW()
                )
                """
            ),
            {
                "id": new_profile_id,
                "profile_key": key,
                "name": name.strip(),
                "description": description.strip() if description else None,
                "base_role": "superadmin" if base_role == "super_admin" else base_role,
                "permissions": json.dumps(permissions_obj, ensure_ascii=False),
                "active": is_active,
                "created_by": str(current_empleado.id),
            },
        )
        await _audit_access_profile_event(
            session,
            event_type="profile_created",
            actor_empleado_id=str(current_empleado.id),
            profile_id=new_profile_id,
            payload={"profile_key": key, "permissions": permissions_obj},
        )
        await session.commit()
        return HTMLResponse(
            content='<meta http-equiv="refresh" content="0;url=/admin/perfiles?success_msg=Perfil+creado+correctamente">',
        )
    except Exception as exc:
        await session.rollback()
        msg = quote(str(exc)[:180])
        return HTMLResponse(
            content=f'<meta http-equiv="refresh" content="0;url=/admin/perfiles?error_msg={msg}">'
        )


@router.post("/admin/perfiles/update/{profile_id}")
async def admin_perfiles_update(
    request: Request,
    profile_id: UUIDType,
    name: str = Form(...),
    description: Optional[str] = Form(None),
    base_role: str = Form("empleado"),
    preset_key: Optional[str] = Form(None),
    permissions_json: str = Form("{}"),
    active: Optional[str] = Form(None),
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = Depends(
        require_permission_factory(
            ["admin.perfiles.manage", "admin.perfiles.update", "admin.perfiles.*"],
            allowed_roles=["admin", "superadmin", "super_admin"],
        )
    ),
):
    await _ensure_access_profiles_schema(session)
    try:
        form = await request.form()
        allowed_roles = {
            "empleado",
            "coordinador",
            "finanzas",
            "admin",
            "superadmin",
            "super_admin",
        }
        if base_role not in allowed_roles:
            base_role = "empleado"
        before_row = await session.execute(
            text(
                "SELECT permissions, base_role, active FROM access_profiles WHERE id = :profile_id"
            ),
            {"profile_id": profile_id},
        )
        before = before_row.first()
        custom_scopes = [
            item.strip()
            for item in str(form.get("scope_custom") or "")
            .replace("\n", ",")
            .split(",")
            if item.strip()
        ]
        permissions_obj = _build_profile_permissions_payload(
            preset_key=preset_key,
            permission_tokens=[str(item) for item in form.getlist("permission_token")],
            scope_tokens=[str(item) for item in form.getlist("scope_token")]
            + custom_scopes,
            permissions_json=permissions_json,
        )
        await session.execute(
            text(
                """
                UPDATE access_profiles
                SET
                    name = :name,
                    description = :description,
                    base_role = :base_role,
                    permissions = CAST(:permissions AS jsonb),
                    active = :active,
                    updated_at = NOW()
                WHERE id = :profile_id
                """
            ),
            {
                "profile_id": profile_id,
                "name": name.strip(),
                "description": description.strip() if description else None,
                "base_role": "superadmin" if base_role == "super_admin" else base_role,
                "permissions": json.dumps(permissions_obj, ensure_ascii=False),
                "active": bool(active),
            },
        )
        await _audit_access_profile_event(
            session,
            event_type="profile_updated",
            actor_empleado_id=str(current_empleado.id),
            profile_id=str(profile_id),
            payload={
                "before": {
                    "permissions": before[0] if before else None,
                    "base_role": before[1] if before else None,
                    "active": before[2] if before else None,
                },
                "after": {
                    "permissions": permissions_obj,
                    "base_role": (
                        "superadmin" if base_role == "super_admin" else base_role
                    ),
                    "active": bool(active),
                },
            },
        )
        await session.commit()
        return HTMLResponse(
            content='<meta http-equiv="refresh" content="0;url=/admin/perfiles?success_msg=Perfil+actualizado">'
        )
    except Exception as exc:
        await session.rollback()
        msg = quote(str(exc)[:180])
        return HTMLResponse(
            content=f'<meta http-equiv="refresh" content="0;url=/admin/perfiles?error_msg={msg}">'
        )


@router.post("/admin/perfiles/assign")
async def admin_perfiles_assign(
    empleado_id: UUIDType = Form(...),
    profile_id: UUIDType = Form(...),
    is_primary: Optional[str] = Form(None),
    apply_base_role: Optional[str] = Form(None),
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = Depends(
        require_permission_factory(
            ["admin.perfiles.manage", "admin.perfiles.assign", "admin.perfiles.*"],
            allowed_roles=["admin", "superadmin", "super_admin"],
        )
    ),
):
    await _ensure_access_profiles_schema(session)
    try:
        if is_primary:
            await session.execute(
                text(
                    "UPDATE empleado_access_profiles SET is_primary = FALSE WHERE empleado_id = :empleado_id"
                ),
                {"empleado_id": empleado_id},
            )
        await session.execute(
            text(
                """
                INSERT INTO empleado_access_profiles (
                    id, empleado_id, profile_id, is_primary, assigned_by_empleado_id, created_at
                ) VALUES (
                    :id, :empleado_id, :profile_id, :is_primary, :assigned_by, NOW()
                )
                ON CONFLICT (empleado_id, profile_id)
                DO UPDATE SET
                    is_primary = EXCLUDED.is_primary,
                    assigned_by_empleado_id = EXCLUDED.assigned_by_empleado_id
                """
            ),
            {
                "id": str(uuid4()),
                "empleado_id": empleado_id,
                "profile_id": profile_id,
                "is_primary": bool(is_primary),
                "assigned_by": str(current_empleado.id),
            },
        )

        if apply_base_role:
            role_row = await session.execute(
                text("SELECT base_role FROM access_profiles WHERE id = :profile_id"),
                {"profile_id": profile_id},
            )
            role_value = role_row.scalar_one_or_none()
            if role_value:
                normalized = (
                    "superadmin"
                    if role_value in ("superadmin", "super_admin")
                    else role_value
                )
                await session.execute(
                    text(
                        "UPDATE empleados SET rol = :rol, actualizado_en = NOW() WHERE id = :empleado_id"
                    ),
                    {"rol": normalized, "empleado_id": empleado_id},
                )

        await _audit_access_profile_event(
            session,
            event_type="profile_assigned",
            actor_empleado_id=str(current_empleado.id),
            profile_id=str(profile_id),
            empleado_id=str(empleado_id),
            payload={
                "is_primary": bool(is_primary),
                "apply_base_role": bool(apply_base_role),
            },
        )
        await session.commit()
        return HTMLResponse(
            content='<meta http-equiv="refresh" content="0;url=/admin/perfiles?success_msg=Perfil+asignado">'
        )
    except Exception as exc:
        await session.rollback()
        msg = quote(str(exc)[:180])
        return HTMLResponse(
            content=f'<meta http-equiv="refresh" content="0;url=/admin/perfiles?error_msg={msg}">'
        )


@router.post("/admin/perfiles/unassign/{assignment_id}")
async def admin_perfiles_unassign(
    assignment_id: UUIDType,
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = Depends(
        require_permission_factory(
            ["admin.perfiles.manage", "admin.perfiles.assign", "admin.perfiles.*"],
            allowed_roles=["admin", "superadmin", "super_admin"],
        )
    ),
):
    await _ensure_access_profiles_schema(session)
    try:
        assignment_row = await session.execute(
            text(
Warning: truncated output (original token count: 58663)
Total output lines: 5000

                """
                SELECT empleado_id, profile_id
                FROM empleado_access_profiles
                WHERE id = :assignment_id
                """
            ),
            {"assignment_id": assignment_id},
        )
        assignment = assignment_row.first()
        await session.execute(
            text("DELETE FROM empleado_access_profiles WHERE id = :assignment_id"),
            {"assignment_id": assignment_id},
        )
        await _audit_access_profile_event(
            session,
            event_type="profile_unassigned",
            actor_empleado_id=str(current_empleado.id),
            profile_id=str(assignment[1]) if assignment else None,
            empleado_id=str(assignment[0]) if assignment else None,
            payload={"assignment_id": str(assignment_id)},
        )
        await session.commit()
        return HTMLResponse(
            content='<meta http-equiv="refresh" content="0;url=/admin/perfiles?success_msg=Asignación+eliminada">'
        )
    except Exception as exc:
        await session.rollback()
        msg = quote(str(exc)[:180])
        return HTMLResponse(
            content=f'<meta http-equiv="refresh" content="0;url=/admin/perfiles?error_msg={msg}">'
        )


# Presupuestos ownership boundary:
# - The canonical dashboard/detail routes are registered from admin_budget_routes.py.
# - This module keeps the explicit legacy UI and bridge action handlers used by
#   the canonical UI until a separate route extraction is approved.
@router.get("/admin/presupuestos-legacy", response_class=HTMLResponse)
async def admin_presupuestos_legacy(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = Depends(get_current_empleado),
    version_id: Optional[str] = Query(None),
    drill_dimension: Optional[str] = Query(None),
    drill_value: Optional[str] = Query(None),
    drill_tournament: Optional[str] = Query(None),
    drill_document: Optional[str] = Query(None),
    scenario_run_rate_delta_pct: float = Query(0),
    scenario_discretionary_cut_pct: float = Query(0),
    scenario_added_commitments: float = Query(0),
    scenario_cash_acceleration: float = Query(0),
    success_msg: Optional[str] = Query(None),
    error_msg: Optional[str] = Query(None),
):
    _require_budget_access(current_empleado, "read")
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
    selected_lines = (
        await list_budget_lines(
            session,
            version_id=selected_version["id"],
            limit=80,
        )
        if selected_version
        else []
    )
    line_monthly_map = (
        await list_monthly_allocations_for_lines(
            session,
            line_ids=[str(line.get("id") or "") for line in selected_lines if line.get("id")],
        )
        if selected_lines
        else {}
    )
    budget_concepts = await list_budget_concepts(
        session,
        active_only=False,
        limit=5000,
    )
    budget_concepts_by_code: dict[str, list[dict[str, Any]]] = {}
    budget_concepts_by_id = {
        str(item.get("id") or ""): item
        for item in budget_concepts
        if str(item.get("id") or "")
    }
    for concept in budget_concepts:
        if not concept.get("active"):
            continue
        budget_concepts_by_code.setdefault(
            str(concept.get("tournament_code") or ""), []
        ).append(concept)
    summary = snapshot.get("summary", {}) if isinstance(snapshot, dict) else {}
    forecast_summary = (
        snapshot.get("forecast", {}) if isinstance(snapshot, dict) else {}
    )
    scenarios = snapshot.get("scenarios", {}) if isinstance(snapshot, dict) else {}
    breakdowns = snapshot.get("breakdowns", {}) if isinstance(snapshot, dict) else {}
    tournaments = snapshot.get("tournaments", []) if isinstance(snapshot, dict) else []
    executive_comparison = list(snapshot.get("executive_comparison") or [])
    if not executive_comparison:
        executive_comparison = build_budget_executive_comparison(
            summary, forecast_summary
        )
    executive_alerts = list(snapshot.get("executive_alerts") or [])
    if not executive_alerts:
        executive_alerts = _budget_executive_alerts(
            summary,
            forecast_summary,
            scenarios,
        )
    scenario_player = build_budget_scenario_player(
        summary,
        forecast_summary,
        run_rate_delta_pct=scenario_run_rate_delta_pct,
        discretionary_cut_pct=scenario_discretionary_cut_pct,
        added_commitments=scenario_added_commitments,
        cash_acceleration=scenario_cash_acceleration,
    )
    line_drilldown = _build_budget_line_drilldown(
        selected_lines,
        dimension=drill_dimension,
        value=drill_value,
    )
    visible_lines = list(line_drilldown["rows"])
    active_tournament = None
    for item in tournaments:
        if str(item.get("tournament_id") or "") == str(drill_tournament or ""):
            active_tournament = item
            break
        if str(item.get("tournament_code") or "") == str(drill_tournament or ""):
            active_tournament = item
            break
    tournament_commitments = (
        await list_budget_tournament_commitments(
            session,
            edition_year=2026,
            tournament_id=str(active_tournament.get("tournament_id") or "") or None,
            tournament_name=str(active_tournament.get("tournament_name") or "") or None,
            tournament_code=str(active_tournament.get("tournament_code") or "") or None,
            limit=20,
        )
        if active_tournament
        else []
    )
    active_commitment = next(
        (
            item
            for item in tournament_commitments
            if str(item.get("documento_id") or "") == str(drill_document or "")
        ),
        None,
    )
    active_commitment_expense = build_budget_commitment_expense_preview(
        active_commitment
    )
    access = _budget_access_map(current_empleado)
    audit_events = (
        await list_budget_audit_events(
            session,
            version_id=selected_version["id"] if selected_version else None,
            limit=60,
        )
        if access.get("audit_read")
        else []
    )
    success_html = (
        f'<div style="background:#d4edda;border:1px solid #c3e6cb;color:#155724;padding:10px;border-radius:6px;margin-bottom:12px;"><strong>✅ {escape(success_msg)}</strong></div>'
        if success_msg
        else ""
    )
    error_html = (
        f'<div style="background:#f8d7da;border:1px solid #f5c6cb;color:#721c24;padding:10px;border-radius:6px;margin-bottom:12px;"><strong>⚠️ {escape(error_msg)}</strong></div>'
        if error_msg
        else ""
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
    version_options = "".join(
        f'<option value="{escape(item["id"])}" {"selected" if selected_version and item["id"] == selected_version["id"] else ""}>{escape(item["version_name"])} · {escape(item["status"])} · ${float(item["budget_total"] or 0):,.2f}</option>'
        for item in versions
    )
    version_rows_parts: list[str] = []
    for row in versions:
        actions_html = [
            f'<a href="/admin/presupuestos?version_id={escape(str(row.get("id") or ""))}" style="text-decoration:none;background:#e2e8f0;color:#0f172a;border-radius:999px;padding:6px 10px;font-size:11px;font-weight:700;">Abrir</a>'
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
                    f'<input type="hidden" name="status" value="{escape(next_status)}">'
                    f'<button type="submit" style="background:#0f766e;color:#fff;border:none;border-radius:999px;padding:6px 10px;font-size:11px;font-weight:700;cursor:pointer;">{escape(label)}</button>'
                    f"</form>"
                )
        version_rows_parts.append(
            f"""
            <tr>
                <td>{int(row.get("edition_year") or 0)}</td>
                <td>
                    <div style="font-weight:700;">{escape(str(row.get("version_name") or ""))}</div>
                    <div style="font-size:12px;color:#64748b;">{int(row.get("line_count") or 0)} líneas · ${float(row.get("budget_total") or 0):,.2f}</div>
                </td>
                <td><span style="display:inline-flex;padding:4px 8px;border-radius:999px;background:#e0f2fe;color:#075985;font-size:11px;font-weight:700;">{escape(str(row.get("status") or ""))}</span></td>
                <td>{escape(str(row.get("source") or ""))}</td>
                <td><small>{escape(str(row.get("artifact_path") or "—"))}</small></td>
                <td>{format_value(row.get("updated_at") or row.get("created_at"))}</td>
                <td><div style="display:flex;flex-wrap:wrap;gap:6px;">{"".join(actions_html)}</div></td>
            </tr>
            """
        )
    version_rows = "".join(version_rows_parts)
    selected_version_edit_form = (
        f"""
        <form method="POST" action="/admin/presupuestos/versiones/{selected_version["id"]}/update" style="display:grid;gap:8px;">
            <label style="font-weight:700;">Nombre versión</label>
            <input type="text" name="version_name" value="{escape(str(selected_version.get("version_name") or ""))}" {'disabled' if not access.get("version_update") else ''}>
            <label style="font-weight:700;">Notas</label>
            <textarea name="notes" rows="4" {'disabled' if not access.get("version_update") else ''}>{escape(str(selected_version.get("notes") or ""))}</textarea>
            {f'<button type="submit" style="width:max-content;background:#1d4ed8;color:#fff;border:none;border-radius:999px;padding:10px 14px;font-weight:700;cursor:pointer;">Guardar metadatos</button>' if access.get("version_update") else '<div style="color:#64748b;font-size:12px;">Sin permiso para editar metadatos de versión.</div>'}
        </form>
        """
        if selected_version
        else '<div style="color:#64748b;">Sin versión seleccionada.</div>'
    )
    create_version_form = (
        """
        <form method="POST" action="/admin/presupuestos/versiones/create" style="display:grid;gap:8px;">
            <label style="font-weight:700;">Nuevo presupuesto desde cero</label>
            <input type="text" name="version_name" placeholder="Ej. Presupuesto 2026 Dirección" required>
            <textarea name="notes" rows="3" placeholder="Notas de alcance, supuestos o dueño"></textarea>
            <button type="submit" style="width:max-content;background:#0f766e;color:#fff;border:none;border-radius:999px;padding:10px 14px;font-weight:700;cursor:pointer;">Crear borrador vacío</button>
        </form>
        """
        if access.get("create")
        else '<div style="color:#64748b;">Sin permiso para crear presupuestos desde cero.</div>'
    )
    drill_base_params = []
    if selected_version and selected_version.get("id"):
        drill_base_params.append(f'version_id={quote(str(selected_version.get("id")))}')
    if success_msg:
        drill_base_params.append(f"success_msg={quote(success_msg)}")
    if error_msg:
        drill_base_params.append(f"error_msg={quote(error_msg)}")
    drill_base_query = "&".join(drill_base_params)
    scenario_hidden_inputs = "".join(
        f'<input type="hidden" name="{escape(name)}" value="{escape(str(value))}">'
        for name, value in [
            ("version_id", selected_version["id"] if selected_version else ""),
            ("drill_dimension", drill_dimension or ""),
            ("drill_value", drill_value or ""),
            ("drill_tournament", drill_tournament or ""),
            ("drill_document", drill_document or ""),
        ]
        if value
    )

    def _render_catalog_hide_form(concept_id: str) -> str:
        clean_id = str(concept_id or "").strip()
        if not clean_id:
            return ""
        return f"""
        <form method="POST" action="/admin/presupuestos/conceptos/{escape(clean_id)}/hide"
              onsubmit="return confirm('¿Quitar esta partida del catálogo visible?');"
              style="margin:0;">
            {catalog_hidden_context}
            <button type="submit"
                style="background:#fee2e2;color:#991b1b;border:1px solid #fecaca;border-radius:999px;padding:6px 10px;font-size:11px;font-weight:700;cursor:pointer;">
                Quitar
            </button>
        </form>
        """

    def _budget_concept_admin_label(item: dict[str, Any]) -> str:
        prefix = str(item.get("tournament_code") or "").strip() or "SIN-CODIGO"
        return f"{prefix} · {str(item.get('concept_name') or '').strip()}"

    def _render_budget_concept_options_for_line(
        *,
        tournament_code: Optional[str],
        selected_id: Optional[str],
    ) -> str:
        concept_rows = list(budget_concepts_by_code.get(str(tournament_code or ""), []))
        selected_key = str(selected_id or "")
        if selected_key and not any(
            str(item.get("id") or "") == selected_key for item in concept_rows
        ):
            extra_item = budget_concepts_by_id.get(selected_key)
            if extra_item:
                concept_rows.append(extra_item)
        concept_rows.sort(key=lambda item: _budget_concept_admin_label(item).lower())
        options = [
            '<option value="">— Sin ligar catálogo —</option>',
        ]
        for item in concept_rows:
            concept_id = str(item.get("id") or "")
            if not concept_id:
                continue
            selected_attr = " selected" if concept_id == selected_key else ""
            options.append(
                f'<option value="{escape(concept_id)}"{selected_attr}>'
                f"{escape(_budget_concept_admin_label(item))}</option>"
            )
        return "".join(options)

    budget_concepts_count = len([item for item in budget_concepts if item.get("active")])
    budget_concepts_tournaments_count = len(
        {
            str(item.get("tournament_code") or "").strip()
            for item in budget_concepts
            if item.get("active") and str(item.get("tournament_code") or "").strip()
        }
    )

    catalog_tournaments_result = await session.execute(
        select(Tournament)
        .where(Tournament.active == True)
        .order_by(Tournament.display_order.asc(), Tournament.name.asc())
    )
    catalog_tournaments = catalog_tournaments_result.scalars().all()
    catalog_cuentas_result = await session.execute(
        select(CuentaContable)
        .where(CuentaContable.activo.is_(True))
        .order_by(CuentaContable.codigo.asc())
    )
    catalog_cuentas = catalog_cuentas_result.scalars().all()
    default_pasivo_cuenta_id = next(
        (
            str(cuenta.id)
            for cuenta in catalog_cuentas
            if str(cuenta.codigo or "").strip()
            == DEFAULT_BUDGET_CONCEPT_PASIVO_ACCOUNT_CODE
        ),
        "",
    )
    catalog_etapas_by_tournament: dict[str, list[str]] = {}
    for tournament in catalog_tournaments:
        catalog_etapas_by_tournament[str(tournament.id)] = get_tournament_scope_options(
            tournament
        )["etapas"]

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

    def _catalog_sub_proyecto_value(metadata: dict[str, Any]) -> str:
        payload = metadata if isinstance(metadata, dict) else {}
        labels = [
            str(label).strip()
            for label in list(payload.get("applicable_phase_labels") or [])
            if str(label).strip()
        ]
        return labels[0] if labels else ""

    catalog_table_rows = sorted(
        [item for item in budget_concepts if item.get("active")],
        key=lambda row: (
            str(row.get("tournament_name") or "").lower(),
            str(row.get("concept_name") or "").lower(),
        ),
    )

    catalog_hidden_context = "".join(
        f'<input type="hidden" name="{escape(name)}" value="{escape(str(value))}">'
        for name, value in [
            ("version_id", selected_version["id"] if selected_version else ""),
            ("drill_dimension", drill_dimension or ""),
            ("drill_value", drill_value or ""),
            ("drill_tournament", drill_tournament or ""),
            ("drill_document", drill_document or ""),
        ]
        if value
    )

    def _render_catalog_sub_proyecto_input(
        *,
        row_index: int,
        tournament_id: str,
        selected_value: str,
    ) -> str:
        etapas = catalog_etapas_by_tournament.get(tournament_id, [])
        field_name = f"sub_proyectos"
        input_style = 'style="width:100%;padding:8px;border:1px solid #cbd5e1;border-radius:8px;"'
        if etapas:
            options = ['<option value="">Todas</option>']
            for etapa in etapas:
                selected_attr = " selected" if etapa == selected_value else ""
                options.append(
                    f'<option value="{escape(etapa)}"{selected_attr}>'
                    f"{escape(etapa)}</option>"
                )
            if selected_value and selected_value not in etapas:
                options.append(
                    f'<option value="{escape(selected_value)}" selected>'
                    f"{escape(selected_value)}</option>"
                )
            return (
                f'<select class="catalog-subproyecto" name="{field_name}" {input_style}>'
                f'{"".join(options)}</select>'
            )
        return (
            f'<input class="catalog-subproyecto" type="text" name="{field_name}" '
            f'value="{escape(selected_value)}" placeholder="Todas" {input_style} '
            f'title="Configure etapas en Torneos y proyectos">'
        )

    def _render_catalog_cuenta_select(
        *, selected_id: str = "", field_name: str = "cuenta_contable_ids"
    ) -> str:
        options = ['<option value="">— Sin cuenta contable —</option>']
        selected_clean = str(selected_id or "").strip()
        for cuenta in catalog_cuentas:
            cuenta_id = str(cuenta.id)
            selected_attr = " selected" if cuenta_id == selected_clean else ""
            options.append(
                f'<option value="{escape(cuenta_id)}"{selected_attr}>'
                f"{escape(cuenta.codigo)} · {escape(cuenta.nombre)}</option>"
            )
        return (
            f'<select class="catalog-cuenta" name="{escape(field_name)}" '
            f'style="width:100%;padding:8px;border:1px solid #cbd5e1;border-radius:8px;">'
            f'{"".join(options)}</select>'
        )

    def _render_catalog_table_row(
        *,
        row_index: int,
        concept_id: str = "",
        concept_name: str = "",
        tournament_id: str = "",
        sub_proyecto: str = "",
        cuenta_contable_id: str = "",
        cuenta_contable_codigo: str = "",
        cuenta_contable_nombre: str = "",
        pasivo_cuenta_contable_id: str = "",
        pasivo_cuenta_contable_codigo: str = "",
        pasivo_cuenta_contable_nombre: str = "",
        readonly: bool = False,
        action_cell_html: str = "",
    ) -> str:
        if readonly:
            tournament_label = next(
                (
                    item.name
                    for item in catalog_tournaments
                    if str(item.id) == tournament_id
                ),
                tournament_id or "—",
            )
            if cuenta_contable_codigo:
                cuenta_label = f"{cuenta_contable_codigo} · {cuenta_contable_nombre}".strip(
                    " ·"
                )
            else:
                cuenta_label = "—"
            if pasivo_cuenta_contable_codigo:
                pasivo_label = (
                    f"{pasivo_cuenta_contable_codigo} · "
                    f"{pasivo_cuenta_contable_nombre}"
                ).strip(" ·")
            else:
                pasivo_label = "—"
            return f"""
            <tr>
                <td>{escape(concept_name or "—")}</td>
                <td>{escape(str(tournament_label) or "—")}</td>
                <td>{escape(sub_proyecto or "Todas")}</td>
                <td>{escape(cuenta_label)}</td>
                <td>{escape(pasivo_label)}</td>
            </tr>
            """
        proyecto_selected = tournament_id or (
            str(catalog_tournaments[0].id) if catalog_tournaments else ""
        )
        proyecto_options = "".join(
            f'<option value="{escape(str(item.id))}"'
            f'{" selected" if str(item.id) == proyecto_selected else ""}>'
            f"{escape(item.name or '')}</option>"
            for item in catalog_tournaments
        )
        return f"""
        <tr class="catalog-row">
            <td>
                <input type="hidden" name="concept_ids" value="{escape(concept_id)}">
                <input type="text" name="concept_names" value="{escape(concept_name)}" placeholder="Ej. Hospedaje"
                    style="width:100%;padding:8px;border:1px solid #cbd5e1;border-radius:8px;">
            </td>
            <td>
                <select class="catalog-proyecto" name="tournament_ids"
                    style="width:100%;padding:8px;border:1px solid #cbd5e1;border-radius:8px;">
                    {proyecto_options}
                </select>
            </td>
            <td class="catalog-subproyecto-cell">
                {_render_catalog_sub_proyecto_input(row_index=row_index, tournament_id=proyecto_selected, selected_value=sub_proyecto)}
            </td>
            <td class="catalog-cuenta-cell">
                {_render_catalog_cuenta_select(selected_id=cuenta_contable_id)}
            </td>
            <td class="catalog-pasivo-cuenta-cell">
                {_render_catalog_cuenta_select(selected_id=pasivo_cuenta_contable_id, field_name="pasivo_cuenta_contable_ids")}
            </td>
            <td style="white-space:nowrap;">{action_cell_html}</td>
        </tr>
        """

    catalog_editor_rows: list[str] = []
    if access.get("line_update"):
        for index, item in enumerate(catalog_table_rows):
            metadata = (
                item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            )
            catalog_editor_rows.append(
                _render_catalog_table_row(
                    row_index=index,
                    concept_id=str(item.get("id") or ""),
                    concept_name=str(item.get("concept_name") or ""),
                    tournament_id=_resolve_catalog_tournament_id(item),
                    sub_proyecto=_catalog_sub_proyecto_value(metadata),
                    cuenta_contable_id=str(item.get("cuenta_contable_id") or ""),
                    pasivo_cuenta_contable_id=str(
                        item.get("pasivo_cuenta_contable_id") or default_pasivo_cuenta_id
                    ),
                    action_cell_html=_render_catalog_hide_form(str(item.get("id") or "")),
                )
            )
        for blank_index in range(2):
            catalog_editor_rows.append(
                _render_catalog_table_row(
                    row_index=len(catalog_table_rows) + blank_index,
                    concept_id="",
                    concept_name="",
                    tournament_id=str(catalog_tournaments[0].id)
                    if catalog_tournaments
                    else "",
                    sub_proyecto="",
                    pasivo_cuenta_contable_id=default_pasivo_cuenta_id,
                    action_cell_html="",
                )
            )
    else:
        for index, item in enumerate(catalog_table_rows):
            metadata = (
                item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            )
            catalog_editor_rows.append(
                _render_catalog_table_row(
                    row_index=index,
                    concept_id=str(item.get("id") or ""),
                    concept_name=str(item.get("concept_name") or ""),
                    tournament_id=_resolve_catalog_tournament_id(item),
                    sub_proyecto=_catalog_sub_proyecto_value(metadata),
                    cuenta_contable_id=str(item.get("cuenta_contable_id") or ""),
                    cuenta_contable_codigo=str(item.get("cuenta_contable_codigo") or ""),
                    cuenta_contable_nombre=str(item.get("cuenta_contable_nombre") or ""),
                    pasivo_cuenta_contable_id=str(
                        item.get("pasivo_cuenta_contable_id") or ""
                    ),
                    pasivo_cuenta_contable_codigo=str(
                        item.get("pasivo_cuenta_contable_codigo") or ""
                    ),
                    pasivo_cuenta_contable_nombre=str(
                        item.get("pasivo_cuenta_contable_nombre") or ""
                    ),
                    readonly=True,
                )
            )

    catalog_etapas_map_json = json.dumps(
        catalog_etapas_by_tournament, ensure_ascii=False
    )
    catalog_proyecto_options_json = json.dumps(
        [
            {"id": str(item.id), "name": item.name or ""}
            for item in catalog_tournaments
        ],
        ensure_ascii=False,
    )
    if access.get("line_update"):
        catalog_editor_html = f"""
            <form method="POST" action="/admin/presupuestos/conceptos/bulk-save" style="margin-top:16px;">
                {catalog_hidden_context}
            <div class="table-shell" style="overflow-x:auto;overflow-y:visible;">
                    <table id="catalog-partidas-table">
                        <thead>
                            <tr>
                                <th>Partida</th>
                                <th>Proyecto</th>
                                <th>Sub Proyecto</th>
                                <th>Cuenta Contable</th>
                                <th>Cuenta Pasivo</th>
                                <th>Acciones</th>
                            </tr>
                        </thead>
                        <tbody id="catalog-partidas-body">
                            {''.join(catalog_editor_rows) or '<tr><td colspan="6">Sin partidas cargadas. Agrega filas nuevas o edita las existentes.</td></tr>'}
                        </tbody>
                    </table>
                </div>
                <div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:14px;align-items:center;">
                    <button type="button" id="catalog-add-row" class="button secondary">Agregar fila</button>
                    <button type="submit" class="button primary">Guardar cambios</button>
                </div>
                <div style="margin-top:8px;font-size:12px;color:#64748b;">
                    Las opciones de Sub Proyecto provienen de las etapas/fases de cada proyecto en
                    <a href="/admin/torneos" style="color:#0f766e;">Torneos y proyectos</a>.
                    Déjalo en blanco o “Todas” para que la partida aplique a todo el proyecto.
                </div>
            </form>
            <template id="catalog-row-template">
                {_render_catalog_table_row(row_index=9999, concept_id="", concept_name="", tournament_id=str(catalog_tournaments[0].id) if catalog_tournaments else "", sub_proyecto="", cuenta_contable_id="", pasivo_cuenta_contable_id=default_pasivo_cuenta_id, action_cell_html="")}
            </template>
            <script>
            (function() {{
                const etapasMap = {catalog_etapas_map_json};
                const proyectos = {catalog_proyecto_options_json};

                function buildSubProyectoField(tournamentId, selectedValue) {{
                    const etapas = etapasMap[tournamentId] || [];
                    if (etapas.length) {{
                        const select = document.createElement('select');
                        select.className = 'catalog-subproyecto';
                        select.name = 'sub_proyectos';
                        select.style.cssText = 'width:100%;padding:8px;border:1px solid #cbd5e1;border-radius:8px;';
                        const blank = document.createElement('option');
                        blank.value = '';
                        blank.textContent = 'Todas';
                        select.appendChild(blank);
                        etapas.forEach(function(etapa) {{
                            const opt = document.createElement('option');
                            opt.value = etapa;
                            opt.textContent = etapa;
                            if (etapa === selectedValue) opt.selected = true;
                            select.appendChild(opt);
                        }});
                        if (selectedValue && !etapas.includes(selectedValue)) {{
                            const custom = document.createElement('option');
                            custom.value = selectedValue;
                            custom.textContent = selectedValue;
                            custom.selected = true;
                            select.appendChild(custom);
                        }}
                        return select;
                    }}
                    const input = document.createElement('input');
                    input.type = 'text';
                    input.className = 'catalog-subproyecto';
                    input.name = 'sub_proyectos';
                    input.value = selectedValue || '';
                    input.placeholder = 'Todas';
                    input.title = 'Configure etapas en Torneos y proyectos';
                    input.style.cssText = 'width:100%;padding:8px;border:1px solid #cbd5e1;border-radius:8px;';
                    return input;
                }}

                function bindProyectoSelect(select) {{
                    select.addEventListener('change', function() {{
                        const row = select.closest('tr');
                        if (!row) return;
                        const cell = row.querySelector('.catalog-subproyecto-cell');
                        if (!cell) return;
                        cell.innerHTML = '';
                        cell.appendChild(buildSubProyectoField(select.value, ''));
                    }});
                }}

                document.querySelectorAll('.catalog-proyecto').forEach(bindProyectoSelect);

                const addRowBtn = document.getElementById('catalog-add-row');
                const tbody = document.getElementById('catalog-partidas-body');
                const template = document.getElementById('catalog-row-template');
                if (addRowBtn && tbody && template) {{
                    addRowBtn.addEventListener('click', function() {{
                        const clone = template.content.cloneNode(true);
                        tbody.appendChild(clone);
                        const newSelect = tbody.querySelector('tr:last-child .catalog-proyecto');
                        if (newSelect) bindProyectoSelect(newSelect);
                    }});
                }}
            }})();
            </script>
        """
    else:
        catalog_editor_html = f"""
            <div class="table-shell" style="margin-top:16px;overflow-x:auto;overflow-y:visible;">
                <table>
                    <thead>
                        <tr>
                            <th>Partida</th>
                            <th>Proyecto</th>
                            <th>Sub Proyecto</th>
                            <th>Cuenta Contable</th>
                            <th>Cuenta Pasivo</th>
                        </tr>
                    </thead>
                    <tbody>
                        {''.join(catalog_editor_rows) or '<tr><td colspan="5">Sin partidas cargadas.</td></tr>'}
                    </tbody>
                </table>
            </div>
            <div style="margin-top:8px;font-size:12px;color:#64748b;">Sin permiso para editar el catálogo.</div>
        """

    def render_breakdown_column(
        title: str,
        rows: list[dict[str, Any]],
        *,
        dimension_key: str,
        primary_key: str,
        secondary_key: str,
        empty_label: str,
    ) -> str:
        if not rows:
            rows_html = f'<div style="color:#64748b;font-size:12px;">{escape(empty_label)}</div>'
        else:
            rows_html = "".join(
                f"""
                <div style="display:flex;justify-content:space-between;gap:10px;padding:10px 0;border-bottom:1px solid #eef2f7;">
                    <div>
                        <div style="font-weight:700;color:#0f172a;"><a href="/admin/presupuestos?{drill_base_query}{'&' if drill_base_query else ''}drill_dimension={quote(dimension_key)}&drill_value={quote(str(item.get('label') or ''))}" style="color:#0f172a;text-decoration:none;">{escape(str(item.get("label") or "Sin dato"))}</a></div>
                        <div style="font-size:12px;color:#64748b;">
                            {int(item.get("line_count") or 0)} líneas · {int(item.get("document_count") or 0)} docs · {int(item.get("expense_count") or 0)} gastos
                        </div>
                    </div>
                    <div style="text-align:right;">
                        <div style="font-weight:800;color:#0f172a;">${float(item.get(primary_key) or 0):,.2f}</div>
                        <div style="font-size:12px;color:#64748b;">{secondary_key}: ${float(item.get(secondary_key) or 0):,.2f}</div>
                    </div>
                </div>
                """
                for item in rows[:6]
            )
        return (
            f'<div style="padding:14px;border:1px solid #dbe2ea;border-radius:14px;background:#fff;">'
            f'<div style="font-size:12px;text-transform:uppercase;letter-spacing:.14em;color:#64748b;">{escape(title)}</div>'
            f'<div style="margin-top:10px;">{rows_html}</div>'
            f"</div>"
        )

    def render_scenario_card(item: dict[str, Any]) -> str:
        return f"""
        <div style="padding:14px;border:1px solid #dbe2ea;border-radius:14px;background:#fff;">
            <div style="display:flex;justify-content:space-between;gap:12px;align-items:flex-start;">
                <div>
                    <div style="font-size:11px;text-transform:uppercase;letter-spacing:.14em;color:#64748b;">{escape(str(item.get("label") or "Escenario"))}</div>
                    <div style="margin-top:6px;font-size:24px;font-weight:800;color:#0f172a;">${float(item.get("projected_close_total") or 0):,.2f}</div>
                </div>
                <span style="display:inline-flex;padding:4px 8px;border-radius:999px;background:#e2e8f0;color:#0f172a;font-size:11px;font-weight:700;">{escape(str(item.get("health") or "sin dato"))}</span>
            </div>
            <div style="margin-top:8px;color:#475569;">Varianza: ${float(item.get("projected_variance") or 0):,.2f}</div>
            <div style="margin-top:4px;color:#475569;">Caja requerida: ${float(item.get("projected_cash_need") or 0):,.2f}</div>
            <div style="margin-top:4px;color:#475569;">Ajuste vs base: {float(item.get("adjustment_vs_base_pct") or 0):,.2f}%</div>
            <div style="margin-top:8px;font-size:12px;color:#64748b;">{escape(str(item.get("assumption") or ""))}</div>
        </div>
        """

    def render_executive_alert_card(item: dict[str, str]) -> str:
        severity = str(item.get("severity") or "info").strip().lower()
        palette = {
            "critical": ("#991b1b", "#fee2e2", "#fecaca"),
            "warning": ("#9a3412", "#ffedd5", "#fdba74"),
            "info": ("#1d4ed8", "#dbeafe", "#93c5fd"),
        }
        color, bg, border = palette.get(severity, palette["info"])
        return f"""
        <div style="padding:14px;border:1px solid {border};border-radius:14px;background:{bg};">
            <div style="display:flex;justify-content:space-between;gap:12px;align-items:flex-start;">
                <div style="font-size:16px;font-weight:800;color:{color};">{escape(str(item.get("title") or "Alerta"))}</div>
                <span style="display:inline-flex;padding:4px 8px;border-radius:999px;background:#fff;color:{color};font-size:11px;font-weight:800;text-transform:uppercase;">{escape(severity)}</span>
            </div>
            <div style="margin-top:8px;color:#334155;line-height:1.5;">{escape(str(item.get("detail") or ""))}</div>
            <div style="margin-top:10px;font-size:12px;color:#475569;"><strong>Playbook:</strong> {escape(str(item.get("playbook") or ""))}</div>
        </div>
        """

    def render_executive_comparison_card(item: dict[str, Any]) -> str:
        variance = item.get("variance_to_budget")
        variance_html = (
            f'<div style="margin-top:4px;color:#475569;">Gap vs presupuesto: ${float(variance or 0):,.2f}</div>'
            if variance is not None
            else ""
        )
        return f"""
        <div style="padding:14px;border:1px solid #dbe2ea;border-radius:14px;background:#fff;">
            <div style="display:flex;justify-content:space-between;gap:12px;align-items:flex-start;">
                <div>
                    <div style="font-size:11px;text-transform:uppercase;letter-spacing:.14em;color:#64748b;">{escape(str(item.get("label") or "Métrica"))}</div>
                    <div style="margin-top:6px;font-size:24px;font-weight:800;color:#0f172a;">${float(item.get("total") or 0):,.2f}</div>
                </div>
                <span style="display:inline-flex;padding:4px 8px;border-radius:999px;background:#e2e8f0;color:#0f172a;font-size:11px;font-weight:700;">{float(item.get("pct_of_budget") or 0):,.2f}%</span>
            </div>
            {variance_html}
            <div style="margin-top:8px;font-size:12px;color:#64748b;">{escape(str(item.get("detail") or ""))}</div>
        </div>
        """

    def render_commitment_expense_cell(item: dict[str, Any]) -> str:
        expense_preview = build_budget_commitment_expense_preview(item)
        if expense_preview["has_generated_expense"]:
            return (
                f'<div style="display:grid;gap:4px;">'
                f'<a href="{escape(str(expense_preview["generated_expense_href"]))}" style="color:#0f172a;text-decoration:none;font-weight:800;">'
                f'{escape(str(expense_preview["generated_expense_reference"] or "Ver gasto"))}'
                f"</a>"
                f'<div style="font-size:12px;color:#64748b;">'
                f'{escape(str(expense_preview["generated_expense_state"] or "activo"))} · ${float(expense_preview["generated_expense_total"] or 0):,.2f}'
                f"</div>"
                f"</div>"
            )
        if expense_preview["related_expense_count"]:
            return (
                f'<div style="display:grid;gap:4px;">'
                f'<div style="font-weight:800;color:#0f172a;">{int(expense_preview["related_expense_count"] or 0)} gastos relacionados</div>'
                f'<div style="font-size:12px;color:#64748b;">${float(expense_preview["related_expense_total"] or 0):,.2f} acumulados</div>'
                f"</div>"
            )
        return '<span style="color:#94a3b8;">Sin gasto</span>'

    commitment_rows = "".join(
        f"""
        <tr>
            <td><a href="/admin/presupuestos?{drill_base_query}{'&' if drill_base_query else ''}drill_tournament={quote(str(active_tournament.get('tournament_id') or active_tournament.get('tournament_code') or ''))}&drill_document={quote(str(item.get('documento_id') or ''))}" style="color:#0f172a;text-decoration:none;">{escape(str(item.get("numero_referencia") or "-"))}</a></td>
            <td>{escape(str(item.get("estado") or "-"))}</td>
            <td>{escape(str(item.get("proveedor_nombre") or "-"))}</td>
            <td>{escape(str(item.get("concepto_pago") or "-"))}</td>
            <td>${float(item.get("monto_solicitado") or 0):,.2f}</td>
            <td>${float(item.get("monto_total") or 0):,.2f}</td>
            <td>{render_commitment_expense_cell(item)}</td>
            <td>{escape(str(item.get("fecha_pago") or "-"))}</td>
            <td>{escape(str(item.get("creado_en") or "-"))}</td>
        </tr>
        """
        for item in tournament_commitments
    )
    active_commitment_html = (
        f"""
        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;">
            <div><div style="font-size:12px;color:#64748b;">Referencia</div><div style="font-weight:800;color:#0f172a;">{escape(str(active_commitment.get("numero_referencia") or "-"))}</div></div>
            <div><div style="font-size:12px;color:#64748b;">Estado</div><div style="font-weight:800;color:#0f172a;">{escape(str(active_commitment.get("estado") or "-"))}</div></div>
            <div><div style="font-size:12px;color:#64748b;">Proveedor</div><div style="font-weight:800;color:#0f172a;">{escape(str(active_commitment.get("proveedor_nombre") or "-"))}</div></div>
            <div><div style="font-size:12px;color:#64748b;">Solicitado</div><div style="font-weight:800;color:#0f172a;">${float(active_commitment.get("monto_solicitado") or 0):,.2f}</div></div>
            <div><div style="font-size:12px;color:#64748b;">Total</div><div style="font-weight:800;color:#0f172a;">${float(active_commitment.get("monto_total") or 0):,.2f}</div></div>
            <div><div style="font-size:12px;color:#64748b;">Fecha pago</div><div style="font-weight:800;color:#0f172a;">{escape(str(active_commitment.get("fecha_pago") or "-"))}</div></div>
            <div><div style="font-size:12px;color:#64748b;">Gastos relacionados</div><div style="font-weight:800;color:#0f172a;">{int(active_commitment_expense.get("related_expense_count") or 0)} · ${float(active_commitment_expense.get("related_expense_total") or 0):,.2f}</div></div>
            <div><div style="font-size:12px;color:#64748b;">Último gasto</div><div style="font-weight:800;color:#0f172a;">{escape(str(active_commitment_expense.get("related_expense_latest_date") or "-"))}</div></div>
        </div>
        <div style="margin-top:10px;color:#475569;">{escape(str(active_commitment.get("concepto_pago") or "Sin concepto"))}</div>
        {f'<div style="margin-top:10px;padding:12px;border:1px solid #dbe2ea;border-radius:12px;background:#f8fafc;"><div style="font-size:12px;color:#64748b;">Gasto generado</div><div style="margin-top:4px;font-weight:800;color:#0f172a;">{escape(str(active_commitment_expense.get("generated_expense_reference") or "Sin referencia"))}</div><div style="margin-top:4px;color:#475569;">{escape(str(active_commitment_expense.get("generated_expense_state") or "activo"))} · ${float(active_commitment_expense.get("generated_expense_total") or 0):,.2f} · {escape(str(active_commitment_expense.get("generated_expense_actor") or "Sin responsable"))}</div><div style="margin-top:4px;color:#64748b;">{escape(str(active_commitment_expense.get("generated_expense_concept") or "Sin concepto"))}</div></div>' if active_commitment_expense.get("has_generated_expense") else '<div style="margin-top:10px;padding:12px;border:1px dashed #cbd5e1;border-radius:12px;background:#f8fafc;color:#64748b;">Este compromiso todavía no tiene `gasto_generado_id`, aunque puede acumular gastos relacionados por documento o cuenta de gastos.</div>'}
        <div style="margin-top:10px;display:flex;flex-wrap:wrap;gap:8px;">
            <a href="/documentos/{escape(str(active_commitment.get('documento_id') or ''))}" style="text-decoration:none;background:#0f766e;color:#fff;border-radius:999px;padding:8px 12px;font-size:12px;font-weight:700;">Abrir documento</a>
            {f'<a href="{escape(str(active_commitment_expense.get("generated_expense_href") or ""))}" style="text-decoration:none;background:#1d4ed8;color:#fff;border-radius:999px;padding:8px 12px;font-size:12px;font-weight:700;">Ver gasto generado</a>' if active_commitment_expense.get("generated_expense_href") else ''}
        </div>
        """
        if active_commitment
        else '<div style="color:#64748b;">Selecciona una referencia de la tabla para fijar un compromiso específico dentro del torneo.</div>'
    )
    drill_summary_html = (
        f"""
        <div style="display:flex;justify-content:space-between;gap:14px;align-items:flex-start;flex-wrap:wrap;">
            <div>
                <div style="font-size:12px;text-transform:uppercase;letter-spacing:.14em;color:#64748b;">Drilldown activo</div>
                <div style="margin-top:6px;font-size:20px;font-weight:800;color:#0f172a;">{escape(str(line_drilldown.get("dimension") or "global"))} · {escape(str(line_drilldown.get("value") or "Todas las líneas"))}</div>
                <div style="margin-top:6px;color:#475569;">{int(line_drilldown.get("line_count") or 0)} líneas · presupuesto ${float(line_drilldown.get("budget_total") or 0):,.2f} · referencia ${float(line_drilldown.get("reference_total") or 0):,.2f}</div>
            </div>
            <div>
                <a href="/admin/presupuestos{('?' + drill_base_query) if drill_base_query else ''}" style="text-decoration:none;background:#e2e8f0;color:#0f172a;border-radius:999px;padding:8px 12px;font-size:12px;font-weight:700;">Limpiar drilldown</a>
            </div>
        </div>
        """
        if line_drilldown.get("active")
        else '<div style="color:#64748b;">Selecciona un concepto, proveedor, fase, entidad, responsable o cuenta final desde los breakdowns para abrir un drilldown ejecutivo sobre la misma versión.</div>'
    )

    _month_short = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]

    def _render_monthly_budget_inputs(line_id: str) -> str:
        monthly = line_monthly_map.get(str(line_id or ""), {})
        disabled = "" if access.get("line_update") else " disabled"
        cells = []
        for month in range(1, 13):
            amount = float(monthly.get(month, 0) or 0)
            label = _month_short[month - 1]
            cells.append(
                f'<label style="display:grid;gap:2px;font-size:10px;color:#64748b;">'
                f"{escape(label)}"
                f'<input type="number" step="0.01" min="0" name="month_{month}" value="{amount:.2f}"'
                f' style="width:100%;padding:4px 6px;border:1px solid #cbd5e1;border-radius:6px;font-size:11px;"{disabled}>'
                f"</label>"
            )
        return (
            '<div style="margin-top:8px;">'
            '<div style="font-size:11px;font-weight:700;color:#475569;margin-bottom:4px;">Distribución mensual</div>'
            f'<div style="display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:6px;">{"".join(cells)}</div>'
            "</div>"
        )

    line_rows = "".join(
        f"""
        <tr>
            <td><code>{escape(str(line.get("tournament_code") or "—"))}</code><br><small>{escape(str(line.get("tournament_name") or ""))}</small></td>
            <td>
                <form method="POST" action="/admin/presupuestos/lineas/{line.get('id')}/update" style="display:grid;gap:6px;">
                    <input type="hidden" name="version_id" value="{escape(str(selected_version.get('id') if selected_version else ''))}">
                    <select name="budget_concept_id" {'disabled' if not access.get("line_update") else ''}>
                        {_render_budget_concept_options_for_line(tournament_code=str(line.get("tournament_code") or ""), selected_id=str(line.get("budget_concept_id") or ""))}
                    </select>
                    <input type="text" name="concept_name" value="{escape(str(line.get('concept_name') or ''))}" placeholder="Concepto" {'disabled' if not access.get("line_update") else ''}>
                    <input type="text" name="account_code_final" value="{escape(str(line.get('account_code_final') or ''))}" placeholder="Cuenta final" {'disabled' if not access.get("line_update") else ''}>
                    <input type="text" name="phase" value="{escape(str(line.get('phase') or ''))}" placeholder="Fase" {'disabled' if not access.get("line_update") else ''}>
                    <input type="text" name="owner_name" value="{escape(str(line.get('owner_name') or ''))}" placeholder="Responsable" {'disabled' if not access.get("line_update") else ''}>
                    <input type="text" name="priority" value="{escape(str(line.get('priority') or ''))}" placeholder="Prioridad" {'disabled' if not access.get("line_update") else ''}>
                    <input type="number" step="0.01" min="0" name="budget_amount" value="{float(line.get('budget_amount') or 0):.2f}" placeholder="Monto total" {'disabled' if not access.get("line_update") else ''}>
                    {_render_monthly_budget_inputs(str(line.get('id') or ''))}
                    <textarea name="criteria_note" rows="2" placeholder="Criterio" {'disabled' if not access.get("line_update") else ''}>{escape(str(line.get('criteria_note') or ''))}</textarea>
                    <textarea name="observations" rows="2" placeholder="Observaciones" {'disabled' if not access.get("line_update") else ''}>{escape(str(line.get('observations') or ''))}</textarea>
                    {f'<button type="submit" style="width:max-content;">Guardar línea</button>' if access.get("line_update") else '<div style="color:#64748b;font-size:12px;">Sin permiso para editar líneas.</div>'}
                </form>
            </td>
            <td style="white-space:nowrap;">
                <div>Ref: ${float(line.get("reference_amount") or 0):,.2f}</div>
                <div>Var: ${float(line.get("variance_amount") or 0):,.2f}</div>
                <div><small>{escape(str(line.get("updated_at") or "—"))}</small></div>
            </td>
        </tr>
        """
        for line in visible_lines
    )
    create_line_form = (
        f"""
        <div style="margin:12px 0 16px 0;padding:14px;border:1px solid #dbe2ea;border-radius:14px;background:#fff;">
            <form method="POST" action="/admin/presupuestos/versiones/{escape(str(selected_version.get('id') if selected_version else ''))}/lineas/create" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px;align-items:end;">
                <div style="grid-column:1/-1;"><label style="display:block;font-size:12px;font-weight:700;color:#475569;margin-bottom:4px;">Concepto</label><select name="budget_concept_id" required>{_render_budget_concept_options_for_line(tournament_code="", selected_id=None)}</select></div>
                <div><label style="display:block;font-size:12px;font-weight:700;color:#475569;margin-bottom:4px;">Torneo código</label><input type="text" name="tournament_code" placeholder="Autocompletado por catálogo"></div>
                <div><label style="display:block;font-size:12px;font-weight:700;color:#475569;margin-bottom:4px;">Torneo</label><input type="text" name="tournament_name" placeholder="Autocompletado por catálogo"></div>
                <div><label style="display:block;font-size:12px;font-weight:700;color:#475569;margin-bottom:4px;">Concepto</label><input type="text" name="concept_name" placeholder="Opcional si eliges catálogo"></div>
                <div><label style="display:block;font-size:12px;font-weight:700;color:#475569;margin-bottom:4px;">Cuenta final</label><input type="text" name="account_code_final" placeholder="6100-00"></div>
                <div><label style="display:block;font-size:12px;font-weight:700;color:#475569;margin-bottom:4px;">Fase</label><input type="text" name="phase" placeholder="Regional"></div>
                <div><label style="display:block;font-size:12px;font-weight:700;color:#475569;margin-bottom:4px;">Responsable</label><input type="text" name="owner_name" placeholder="Dirección"></div>
                <div><label style="display:block;font-size:12px;font-weight:700;color:#475569;margin-bottom:4px;">Prioridad</label><input type="text" name="priority" placeholder="Alta"></div>
                <div><label style="display:block;font-size:12px;font-weight:700;color:#475569;margin-bottom:4px;">Presupuesto</label><input type="number" step="0.01" min="0" name="budget_amount" value="0.00"></div>
                <div><label style="display:block;font-size:12px;font-weight:700;color:#475569;margin-bottom:4px;">Referencia</label><input type="number" step="0.01" min="0" name="reference_amount" value="0.00"></div>
                <div style="grid-column:1/-1;"><label style="display:block;font-size:12px;font-weight:700;color:#475569;margin-bottom:4px;">Criterio / observaciones</label><textarea name="criteria_note" rows="2" placeholder="Supuesto presupuestal"></textarea></div>
                <div style="grid-column:1/-1;"><button type="submit" style="background:#0f766e;color:#fff;border:none;border-radius:999px;padding:10px 14px;font-weight:700;cursor:pointer;">Agregar línea al borrador</button></div>
            </form>
        </div>
        """
        if selected_version and access.get("line_update")
        else ""
    )
    catalog_management_html = f"""
        <section class="workspace-card" style="margin-top:18px;">
            <div class="workspace-section-title">Catálogo presupuestal</div>
            <div class="workspace-section-subtitle">Administra las partidas presupuestales por proyecto y fase/subproyecto. Estas partidas alimentan el selector de <a href="/documentos/nueva-solicitud-terceros" style="color:#0f766e;">Solicitud a terceros</a> y otros formularios operativos.</div>
            <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:14px;margin-top:14px;">
                <div style="padding:14px;border:1px solid #dbe2ea;border-radius:14px;background:#fff;">
                    <div style="font-size:11px;text-transform:uppercase;letter-spacing:.14em;color:#64748b;">Catálogo activo</div>
                    <div style="margin-top:6px;font-size:24px;font-weight:800;color:#0f172a;">{budget_concepts_count}</div>
                    <div style="margin-top:6px;color:#475569;">{budget_concepts_tournaments_count} torneo(s) con partida cargada.</div>
                </div>
                <div style="padding:14px;border:1px solid #dbe2ea;border-radius:14px;background:#fff;">
                    {f'<form method="POST" action="/admin/presupuestos/versiones/{escape(str(selected_version.get("id") or ""))}/lineas/import" enctype="multipart/form-data" style="display:grid;gap:8px;"><div style="font-weight:700;color:#0f172a;">Importar líneas anuales</div><div style="font-size:12px;color:#64748b;">Columnas mínimas: torneo, partida_presupuestal, monto_anual.</div><input type="file" name="archivo_presupuesto" accept=".xlsx,.xlsm,.csv" required><button type="submit" style="width:max-content;background:#0f766e;color:#fff;border:none;border-radius:999px;padding:10px 14px;font-weight:700;cursor:pointer;">Cargar presupuesto anual</button></form>' if selected_version and access.get("line_update") else '<div style="font-weight:700;color:#0f172a;">Importar líneas anuales</div><div style="margin-top:6px;color:#64748b;">Selecciona una versión editable para cargar líneas desde archivo.</div>'}
                </div>
            </div>
            <div style="margin-top:16px;padding:14px;border:1px solid #dbe2ea;border-radius:14px;background:#fff;">
                <div style="font-weight:700;color:#0f172a;">Partidas presupuestales</div>
                <div style="margin-top:6px;font-size:12px;color:#64748b;">
                    Edita el catálogo usado en
                    <a href="/documentos/nueva-solicitud-terceros" style="color:#0f766e;">Solicitud a terceros</a>.
                    Cambia las celdas y guarda todo con un solo botón.
                </div>
                {catalog_editor_html}
            </div>
        </section>
    """
    tournament_cards = "".join(
        f"""
        <div style="border:1px solid #dbe2ea;border-radius:14px;background:#fff;padding:14px;">
            <div style="display:flex;justify-content:space-between;gap:12px;align-items:flex-start;">
                <div>
                    <div style="font-size:16px;font-weight:800;color:#0f172a;"><a href="/admin/presupuestos?{drill_base_query}{'&' if drill_base_query else ''}drill_tournament={quote(str(item.get('tournament_id') or item.get('tournament_code') or ''))}" style="color:#0f172a;text-decoration:none;">{escape(str(item.get("tournament_name") or "Torneo"))}</a></div>
                    <div style="font-size:12px;color:#64748b;">{escape(str(item.get("tournament_code") or "sin código"))} · {int(item.get("line_count") or 0)} líneas</div>
                </div>
                <div style="text-align:right;">
                    <div style="font-size:11px;text-transform:uppercase;letter-spacing:.12em;color:#64748b;">Presupuesto</div>
                    <div style="font-size:18px;font-weight:800;color:#0f766e;">${float(item.get("budget_total") or 0):,.2f}</div>
                </div>
            </div>
            <div style="margin-top:10px;font-size:12px;color:#475569;">Referencia: ${float(item.get("reference_total") or 0):,.2f}</div>
            <div style="margin-top:10px;display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;font-size:12px;">
                <div style="padding:8px;border:1px solid #e5e7eb;border-radius:10px;background:#f8fafc;">
                    <div style="color:#64748b;">Solicitado</div>
                    <div style="font-weight:800;">${float(item.get("comparison", {}).get("requested_total") or 0):,.2f}</div>
                </div>
                <div style="padding:8px;border:1px solid #e5e7eb;border-radius:10px;background:#f8fafc;">
                    <div style="color:#64748b;">Comprometido</div>
                    <div style="font-weight:800;">${float(item.get("comparison", {}).get("committed_total")…28663 tokens truncated…div>
            </form>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)


@router.post("/admin/contabilidad/coi/carga-masiva")
async def carga_masiva_coi_post(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = require_admin_finanzas(),
    archivo_xlsx: UploadFile = File(...),
    modo: str = Form("apply"),
):
    """Process COI workbook upload from admin UI."""
    from fastapi.responses import RedirectResponse

    try:
        filename = archivo_xlsx.filename or ""
        if not filename or not filename.lower().endswith(".xlsx"):
            return RedirectResponse(
                url="/admin/contabilidad/coi/carga-masiva?error_msg=Debe seleccionar un archivo XLSX válido",
                status_code=303,
            )
        contents = await archivo_xlsx.read()
        if not contents:
            return RedirectResponse(
                url="/admin/contabilidad/coi/carga-masiva?error_msg=El archivo está vacío",
                status_code=303,
            )
        apply_changes = modo != "dry_run"
        result = await import_coi_workbook(
            session,
            filename=filename,
            contents=contents,
            apply_changes=apply_changes,
            started_by_empleado_id=current_empleado.id,
        )
        summary = COIUploadSummary.from_result(result)
        summary_msg = (
            f"COI {summary.mode}: {summary.polizas} pólizas, "
            f"{summary.lines} partidas, {summary.cfdi_created} CFDI nuevos, "
            f"{summary.cfdi_reused} CFDI reutilizados, "
            f"{summary.created} creadas, {summary.updated} actualizadas."
        )
        return RedirectResponse(
            url=f"/admin/contabilidad/coi/carga-masiva?success_msg={quote(summary_msg)}",
            status_code=303,
        )
    except Exception as exc:
        await session.rollback()
        logger.exception(
            "Unexpected error processing COI upload",
            extra={
                "filename": archivo_xlsx.filename or "",
                "modo": modo,
                "actor_id": str(getattr(current_empleado, "id", "")),
            },
        )
        return _redirect_with_error_message(
            "/admin/contabilidad/coi/carga-masiva",
            _BULK_GENERIC_ERROR,
        )


# ============================================================================
# Bulk XLSX Upload for Auxiliary Ledger
# ============================================================================


@router.get("/admin/contabilidad/auxiliar/carga-masiva", response_class=HTMLResponse)
async def carga_masiva_auxiliar_form(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = require_admin_finanzas(),
):
    """Bulk upload form for auxiliary ledger imports."""
    success_msg = request.query_params.get("success_msg", "")
    error_msg = request.query_params.get("error_msg", "")

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Carga Auxiliar - Sam.chat</title>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                padding: 20px;
                min-height: 100vh;
            }}
            .container {{
                max-width: 920px;
                margin: 0 auto;
                background: white;
                border-radius: 12px;
                box-shadow: 0 10px 40px rgba(0,0,0,0.2);
                padding: 30px;
            }}
            h1 {{ color: #333; margin-bottom: 10px; border-bottom: 3px solid #667eea; padding-bottom: 10px; }}
            .subtitle {{ color: #666; margin-bottom: 30px; }}
            .alert {{ padding: 15px 20px; border-radius: 8px; margin-bottom: 20px; font-weight: 500; }}
            .alert-success {{ background: #d4edda; color: #155724; border: 1px solid #c3e6cb; }}
            .alert-error {{ background: #f8d7da; color: #721c24; border: 1px solid #f5c6cb; }}
            .instructions {{ background: #f8f9fa; padding: 20px; border-radius: 8px; margin-bottom: 30px; border-left: 4px solid #667eea; }}
            .instructions h2 {{ color: #333; margin-bottom: 15px; font-size: 18px; }}
            .instructions li {{ margin-left: 20px; margin-bottom: 8px; color: #555; }}
            .form-group {{ margin-bottom: 20px; }}
            label {{ display: block; margin-bottom: 8px; font-weight: 600; color: #333; }}
            input[type="file"], select {{
                width: 100%;
                padding: 12px;
                border: 2px dashed #ddd;
                border-radius: 6px;
                background: #f8f9fa;
                font-size: 14px;
            }}
            .btn {{
                padding: 12px 24px;
                border: none;
                border-radius: 6px;
                font-size: 16px;
                font-weight: 600;
                cursor: pointer;
                transition: all 0.3s;
                text-decoration: none;
                display: inline-block;
                margin-right: 10px;
            }}
            .btn-primary {{ background: #667eea; color: white; }}
            .btn-secondary {{ background: #6c757d; color: white; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>📗 Carga Auxiliar</h1>
            <p class="subtitle">Importa movimientos del auxiliar contable bancario desde XLSX</p>
            {'<div class="alert alert-success">✅ ' + escape(success_msg) + '</div>' if success_msg else ''}
            {'<div class="alert alert-error">❌ ' + escape(error_msg) + '</div>' if error_msg else ''}
            <div class="instructions">
                <h2>Qué hace esta carga</h2>
                <ul>
                    <li>Lee el auxiliar bancario por cuenta contable y renglón.</li>
                    <li>Guarda movimientos en `aux_ledger_entries`.</li>
                    <li>Intenta enlazar por `tipo_poliza + numero_poliza` contra pólizas COI.</li>
                    <li>Detecta UUID CFDI dentro del concepto cuando exista.</li>
                </ul>
            </div>
            <form method="POST" action="/admin/contabilidad/auxiliar/carga-masiva" enctype="multipart/form-data">
                <div class="form-group">
                    <label for="archivo_xlsx">Archivo XLSX auxiliar</label>
                    <input type="file" id="archivo_xlsx" name="archivo_xlsx" accept=".xlsx" required>
                </div>
                <div class="form-group">
                    <label for="modo">Modo</label>
                    <select id="modo" name="modo">
                        <option value="apply">Aplicar importación</option>
                        <option value="dry_run">Solo validar (dry-run)</option>
                    </select>
                </div>
                <div style="margin-top: 30px;">
                    <button type="submit" class="btn btn-primary">📤 Procesar auxiliar</button>
                    <a href="/panel" class="btn btn-secondary">⬅️ Volver</a>
                </div>
            </form>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)


@router.post("/admin/contabilidad/auxiliar/carga-masiva")
async def carga_masiva_auxiliar_post(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = require_admin_finanzas(),
    archivo_xlsx: UploadFile = File(...),
    modo: str = Form("apply"),
):
    """Process auxiliary ledger workbook upload from admin UI."""
    from fastapi.responses import RedirectResponse

    try:
        filename = archivo_xlsx.filename or ""
        if not filename or not filename.lower().endswith(".xlsx"):
            return RedirectResponse(
                url="/admin/contabilidad/auxiliar/carga-masiva?error_msg=Debe seleccionar un archivo XLSX válido",
                status_code=303,
            )
        contents = await archivo_xlsx.read()
        if not contents:
            return RedirectResponse(
                url="/admin/contabilidad/auxiliar/carga-masiva?error_msg=El archivo está vacío",
                status_code=303,
            )
        apply_changes = modo != "dry_run"
        result = await import_aux_workbook(
            session,
            filename=filename,
            contents=contents,
            apply_changes=apply_changes,
            started_by_empleado_id=current_empleado.id,
        )
        summary_msg = (
            f"Auxiliar {result.get('mode')}: {result.get('entries', 0)} movimientos, "
            f"{result.get('created', 0)} creados, {result.get('updated', 0)} actualizados, "
            f"{result.get('linked_polizas', 0)} ligados a pólizas, "
            f"{result.get('linked_cfdi', 0)} con UUID detectado."
        )
        return RedirectResponse(
            url=f"/admin/contabilidad/auxiliar/carga-masiva?success_msg={quote(summary_msg)}",
            status_code=303,
        )
    except Exception as exc:
        await session.rollback()
        logger.exception(
            "Unexpected error processing auxiliary ledger upload",
            extra={
                "filename": archivo_xlsx.filename or "",
                "modo": modo,
                "actor_id": str(getattr(current_empleado, "id", "")),
            },
        )
        return _redirect_with_error_message(
            "/admin/contabilidad/auxiliar/carga-masiva",
            _BULK_GENERIC_ERROR,
        )


# ============================================================================
# Bulk CSV Upload for Bank Movements
# ============================================================================


@router.get("/admin/contabilidad/banco/carga-masiva", response_class=HTMLResponse)
async def carga_masiva_banco_form(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = require_admin_finanzas(),
):
    """Bulk upload form for bank statement CSV imports."""
    success_msg = request.query_params.get("success_msg", "")
    error_msg = request.query_params.get("error_msg", "")

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Carga Banco - Sam.chat</title>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                padding: 20px;
                min-height: 100vh;
            }}
            .container {{
                max-width: 920px;
                margin: 0 auto;
                background: white;
                border-radius: 12px;
                box-shadow: 0 10px 40px rgba(0,0,0,0.2);
                padding: 30px;
            }}
            h1 {{ color: #333; margin-bottom: 10px; border-bottom: 3px solid #667eea; padding-bottom: 10px; }}
            .subtitle {{ color: #666; margin-bottom: 30px; }}
            .alert {{ padding: 15px 20px; border-radius: 8px; margin-bottom: 20px; font-weight: 500; }}
            .alert-success {{ background: #d4edda; color: #155724; border: 1px solid #c3e6cb; }}
            .alert-error {{ background: #f8d7da; color: #721c24; border: 1px solid #f5c6cb; }}
            .instructions {{ background: #f8f9fa; padding: 20px; border-radius: 8px; margin-bottom: 30px; border-left: 4px solid #667eea; }}
            .instructions h2 {{ color: #333; margin-bottom: 15px; font-size: 18px; }}
            .instructions li {{ margin-left: 20px; margin-bottom: 8px; color: #555; }}
            .form-group {{ margin-bottom: 20px; }}
            label {{ display: block; margin-bottom: 8px; font-weight: 600; color: #333; }}
            input[type="file"], select {{
                width: 100%;
                padding: 12px;
                border: 2px dashed #ddd;
                border-radius: 6px;
                background: #f8f9fa;
                font-size: 14px;
            }}
            .btn {{
                padding: 12px 24px;
                border: none;
                border-radius: 6px;
                font-size: 16px;
                font-weight: 600;
                cursor: pointer;
                transition: all 0.3s;
                text-decoration: none;
                display: inline-block;
                margin-right: 10px;
            }}
            .btn-primary {{ background: #667eea; color: white; }}
            .btn-secondary {{ background: #6c757d; color: white; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🏦 Carga Estado de Cuenta</h1>
            <p class="subtitle">Importa movimientos bancarios desde CSV para conciliación</p>
            {'<div class="alert alert-success">✅ ' + escape(success_msg) + '</div>' if success_msg else ''}
            {'<div class="alert alert-error">❌ ' + escape(error_msg) + '</div>' if error_msg else ''}
            <div class="instructions">
                <h2>Qué hace esta carga</h2>
                <ul>
                    <li>Lee movimientos bancarios desde CSV.</li>
                    <li>Guarda renglones en `bank_movements`.</li>
                    <li>Intenta match conservador con proveedor por CLABE o nombre.</li>
                    <li>Intenta conciliación básica contra auxiliar y pólizas enlazadas.</li>
                </ul>
            </div>
            <form method="POST" action="/admin/contabilidad/banco/carga-masiva" enctype="multipart/form-data">
                <div class="form-group">
                    <label for="archivo_csv">Archivo CSV bancario</label>
                    <input type="file" id="archivo_csv" name="archivo_csv" accept=".csv" required>
                </div>
                <div class="form-group">
                    <label for="modo">Modo</label>
                    <select id="modo" name="modo">
                        <option value="apply">Aplicar importación</option>
                        <option value="dry_run">Solo validar (dry-run)</option>
                    </select>
                </div>
                <div style="margin-top: 30px;">
                    <button type="submit" class="btn btn-primary">📤 Procesar banco</button>
                    <a href="/panel" class="btn btn-secondary">⬅️ Volver</a>
                </div>
            </form>
            <div class="instructions" style="margin-top:24px;">
                <h2>Seed dirigido de gastos para conciliación</h2>
                <ul>
                    <li>Crea gastos sintéticos alineados a movimientos bancarios pendientes.</li>
                    <li>Sirve para probar y elevar `matched_expense` sin meter ruido aleatorio.</li>
                    <li>Después conviene recalcular banco con el mismo estado de cuenta.</li>
                </ul>
            </div>
            <form method="POST" action="/admin/contabilidad/banco/seed-expense-matches">
                <div class="form-group">
                    <label for="empleado_correo">Correo del empleado</label>
                    <input type="text" id="empleado_correo" name="empleado_correo" value="alberto@agentius.ai" style="width:100%;padding:12px;border:1px solid #ddd;border-radius:6px;background:#fff;">
                </div>
                <div class="form-group">
                    <label for="proyecto_seed">Proyecto</label>
                    <input type="text" id="proyecto_seed" name="proyecto" value="Copa Telmex 2026" style="width:100%;padding:12px;border:1px solid #ddd;border-radius:6px;background:#fff;">
                </div>
                <div class="form-group" style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;">
                    <div>
                        <label for="year_seed">Año</label>
                        <input type="number" id="year_seed" name="year" value="2026" style="width:100%;padding:12px;border:1px solid #ddd;border-radius:6px;background:#fff;">
                    </div>
                    <div>
                        <label for="month_seed">Mes</label>
                        <input type="number" id="month_seed" name="month" value="2" min="1" max="12" style="width:100%;padding:12px;border:1px solid #ddd;border-radius:6px;background:#fff;">
                    </div>
                    <div>
                        <label for="limit_seed">Límite</label>
                        <input type="number" id="limit_seed" name="limit" value="25" min="1" max="500" style="width:100%;padding:12px;border:1px solid #ddd;border-radius:6px;background:#fff;">
                    </div>
                </div>
                <div class="form-group">
                    <label for="modo_seed">Modo</label>
                    <select id="modo_seed" name="modo_seed">
                        <option value="apply">Aplicar seed</option>
                        <option value="dry_run">Solo validar (dry-run)</option>
                    </select>
                </div>
                <div class="form-group">
                    <label><input type="checkbox" name="require_outflow" value="1" checked> Solo cargos/salidas bancarias</label>
                </div>
                <div style="margin-top: 20px;">
                    <button type="submit" class="btn btn-primary">🧪 Crear gastos compatibles</button>
                </div>
            </form>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)


@router.post("/admin/contabilidad/banco/carga-masiva")
async def carga_masiva_banco_post(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = require_admin_finanzas(),
    archivo_csv: UploadFile = File(...),
    modo: str = Form("apply"),
):
    """Process bank statement CSV upload from admin UI."""
    from fastapi.responses import RedirectResponse

    try:
        filename = archivo_csv.filename or ""
        if not filename or not filename.lower().endswith(".csv"):
            return RedirectResponse(
                url="/admin/contabilidad/banco/carga-masiva?error_msg=Debe seleccionar un archivo CSV válido",
                status_code=303,
            )
        contents = await archivo_csv.read()
        if not contents:
            return RedirectResponse(
                url="/admin/contabilidad/banco/carga-masiva?error_msg=El archivo está vacío",
                status_code=303,
            )
        _bank_upload_cache_path(filename).write_bytes(contents)
        apply_changes = modo != "dry_run"
        result = await import_bank_movements_csv(
            session,
            filename=filename,
            contents=contents,
            apply_changes=apply_changes,
            started_by_empleado_id=current_empleado.id,
        )
        summary_msg = (
            f"Banco {result.get('mode')}: {result.get('entries', 0)} movimientos, "
            f"{result.get('created', 0)} creados, {result.get('updated', 0)} actualizados, "
            f"{result.get('matched_proveedor', 0)} con proveedor, "
            f"{result.get('matched_aux', 0)} ligados a auxiliar, "
            f"{result.get('related_poliza', 0)} ligados a póliza."
        )
        return RedirectResponse(
            url=f"/admin/contabilidad/banco/carga-masiva?success_msg={quote(summary_msg)}",
            status_code=303,
        )
    except Exception as exc:
        await session.rollback()
        logger.exception(
            "Unexpected error processing bank statement upload",
            extra={
                "filename": archivo_csv.filename or "",
                "modo": modo,
                "actor_id": str(getattr(current_empleado, "id", "")),
            },
        )
        return _redirect_with_error_message(
            "/admin/contabilidad/banco/carga-masiva",
            _BULK_GENERIC_ERROR,
        )


@router.get("/admin/nomina/runa/carga-masiva", response_class=HTMLResponse)
async def carga_masiva_runa_nomina_form(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = require_admin_finanzas(),
):
    """Bulk upload form for the Runa payroll activation layout."""
    success_msg = request.query_params.get("success_msg", "")
    error_msg = request.query_params.get("error_msg", "")

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Importar layout Runa - Sam.chat</title>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
                background: linear-gradient(135deg, #0f172a 0%, #1d4ed8 100%);
                padding: 20px;
                min-height: 100vh;
            }}
            .container {{
                max-width: 920px;
                margin: 0 auto;
                background: white;
                border-radius: 12px;
                box-shadow: 0 10px 40px rgba(0,0,0,0.2);
                padding: 30px;
            }}
            h1 {{ color: #111827; margin-bottom: 10px; border-bottom: 3px solid #1d4ed8; padding-bottom: 10px; }}
            .subtitle {{ color: #6b7280; margin-bottom: 30px; }}
            .alert {{ padding: 15px 20px; border-radius: 8px; margin-bottom: 20px; font-weight: 500; white-space: pre-wrap; }}
            .alert-success {{ background: #d1fae5; color: #065f46; border: 1px solid #a7f3d0; }}
            .alert-error {{ background: #fee2e2; color: #991b1b; border: 1px solid #fecaca; }}
            .instructions {{ background: #f8fafc; padding: 20px; border-radius: 8px; margin-bottom: 30px; border-left: 4px solid #1d4ed8; }}
            .instructions h2 {{ color: #111827; margin-bottom: 15px; font-size: 18px; }}
            .instructions li {{ margin-left: 20px; margin-bottom: 8px; color: #475569; }}
            .form-group {{ margin-bottom: 20px; }}
            label {{ display: block; margin-bottom: 8px; font-weight: 600; color: #333; }}
            input[type="file"], select {{
                width: 100%;
                padding: 12px;
                border: 2px dashed #cbd5e1;
                border-radius: 6px;
                background: #f8fafc;
                font-size: 14px;
            }}
            .btn {{
                padding: 12px 24px;
                border: none;
                border-radius: 6px;
                font-size: 16px;
                font-weight: 600;
                cursor: pointer;
                transition: all 0.3s;
                text-decoration: none;
                display: inline-block;
                margin-right: 10px;
            }}
            .btn-primary {{ background: #1d4ed8; color: white; }}
            .btn-secondary {{ background: #6b7280; color: white; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🧾 Importar layout Runa</h1>
            <p class="subtitle">Valida o importa el layout de activación de nómina de Runa hacia la ficha normalizada de empleados.</p>
            {'<div class="alert alert-success">✅ ' + escape(success_msg) + '</div>' if success_msg else ''}
            {'<div class="alert alert-error">❌ ' + escape(error_msg) + '</div>' if error_msg else ''}
            <div class="instructions">
                <h2>Qué hace esta carga</h2>
                <ul>
                    <li>Lee la hoja <code>Empleados</code> del layout Runa.</li>
                    <li>Mapea datos fiscales, empleo, compensación, pago, deducciones, beneficios y domicilio.</li>
                    <li><strong>No crea empleados internos nuevos</strong>; solo aplica si la fila puede vincularse a un <code>empleado</code> existente o a una ficha de nómina ya creada.</li>
                    <li>Usa <code>dry_run</code> para validar primero el mapping y los faltantes.</li>
                </ul>
            </div>
            <form method="POST" action="/admin/nomina/runa/carga-masiva" enctype="multipart/form-data">
                <div class="form-group">
                    <label for="archivo_xlsx">Archivo XLSX de Runa</label>
                    <input type="file" id="archivo_xlsx" name="archivo_xlsx" accept=".xlsx" required>
                </div>
                <div class="form-group">
                    <label for="modo">Modo</label>
                    <select id="modo" name="modo">
                        <option value="dry_run">Solo validar (dry-run)</option>
                        <option value="apply">Aplicar importación</option>
                    </select>
                </div>
                <div style="margin-top: 30px;">
                    <button type="submit" class="btn btn-primary">Procesar layout</button>
                    <a href="/admin/nomina/empleados" class="btn btn-secondary">Volver a nómina</a>
                </div>
            </form>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)


@router.post("/admin/nomina/runa/carga-masiva")
async def carga_masiva_runa_nomina_post(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = require_admin_finanzas(),
    archivo_xlsx: UploadFile = File(...),
    modo: str = Form("dry_run"),
):
    """Process Runa payroll layout upload from admin UI."""
    try:
        filename = archivo_xlsx.filename or ""
        if not filename or not filename.lower().endswith(".xlsx"):
            return RedirectResponse(
                url="/admin/nomina/runa/carga-masiva?error_msg=Debe seleccionar un archivo XLSX válido",
                status_code=303,
            )
        contents = await archivo_xlsx.read()
        if not contents:
            return RedirectResponse(
                url="/admin/nomina/runa/carga-masiva?error_msg=El archivo está vacío",
                status_code=303,
            )

        result = await import_runa_payroll_workbook(
            session,
            filename=filename,
            contents=contents,
            apply_changes=(modo or "dry_run").strip().lower() == "apply",
        )
        sample_text = ""
        if result.get("samples"):
            sample_lines = [
                f"fila {sample.get('row')}: {sample.get('name') or sample.get('employee_code') or '-'} -> {sample.get('action')} ({sample.get('match_source') or 'sin match'})"
                for sample in result["samples"][:5]
            ]
            sample_text = " Ejemplos: " + " | ".join(sample_lines)
        summary_msg = (
            f"Runa {result['mode']}: {result['rows_seen']} filas, "
            f"{result['matched_internal_employee']} vinculadas, "
            f"{result['created']} creadas, "
            f"{result['updated']} actualizadas, "
            f"{result['skipped']} omitidas, "
            f"{result['warning_count']} warnings."
            f"{sample_text}"
        )
        return RedirectResponse(
            url=f"/admin/nomina/runa/carga-masiva?success_msg={quote(summary_msg)}",
            status_code=303,
        )
    except Exception as exc:
        await session.rollback()
        logger.exception(
            "Unexpected error processing Runa payroll upload",
            extra={
                "filename": archivo_xlsx.filename or "",
                "modo": modo,
                "actor_id": str(getattr(current_empleado, "id", "")),
            },
        )
        return _redirect_with_error_message(
            "/admin/nomina/runa/carga-masiva",
            _BULK_GENERIC_ERROR,
        )


@router.post("/admin/contabilidad/banco/seed-expense-matches")
async def seed_expense_matches_from_bank_post(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = require_admin_finanzas(),
    empleado_correo: str = Form("alberto@agentius.ai"),
    proyecto: str = Form("Copa Telmex 2026"),
    year: int = Form(2026),
    month: int = Form(2),
    limit: int = Form(25),
    modo_seed: str = Form("apply"),
    require_outflow: Optional[str] = Form(None),
    return_to: str = Form("/admin/contabilidad/banco/carga-masiva"),
):
    """Run directed synthetic expense seed aligned to unmatched bank movements."""
    redirect_base = (return_to or "/admin/contabilidad/banco/carga-masiva").strip()
    if not redirect_base.startswith("/"):
        redirect_base = "/admin/contabilidad/banco/carga-masiva"
    separator = "&" if "?" in redirect_base else "?"
    try:
        repo_root = _repo_root()
        script_path = (
            repo_root / "scripts" / "seed_expense_matches_from_bank_movements.py"
        )
        if not script_path.exists():
            logger.warning(
                "Seed script not found",
                extra={"script_path": str(script_path)},
            )
            return _redirect_with_error_message(redirect_base, _SEED_GENERIC_ERROR)

        env = _domain_alignment_subprocess_env(ACTIVE_TOURNAMENT_SCOPE)
        cmd = [
            sys.executable,
            str(script_path),
            "--empleado-correo",
            (empleado_correo or "").strip(),
            "--proyecto",
            (proyecto or "").strip(),
            "--year",
            str(year),
            "--month",
            str(month),
            "--limit",
            str(max(1, min(int(limit), 500))),
        ]
        if require_outflow:
            cmd.append("--require-outflow")
        cmd.append("--dry-run" if modo_seed == "dry_run" else "--apply")

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(repo_root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=180)
        stdout_text = _clean_domain_alignment_output(
            (stdout or b"").decode("utf-8", errors="replace").strip()
        )
        stderr_text = _clean_domain_alignment_output(
            (stderr or b"").decode("utf-8", errors="replace").strip()
        )
        if proc.returncode != 0:
            msg = stderr_text or stdout_text or "Seed falló"
            return RedirectResponse(
                url=f"{redirect_base}{separator}error_msg={quote(msg)}",
                status_code=303,
            )
        try:
            summary = json.loads(stdout_text) if stdout_text else {}
            msg = f"Seed {summary.get('mode')}: {summary.get('created', 0)} gastos creados, {summary.get('skipped_existing', 0)} omitidos."
        except Exception:
            msg = "Seed ejecutado correctamente."
        return RedirectResponse(
            url=f"{redirect_base}{separator}success_msg={quote(msg)}",
            status_code=303,
        )
    except asyncio.TimeoutError:
        return RedirectResponse(
            url=f"{redirect_base}{separator}error_msg=El+seed+excedio+el+timeout+de+180s",
            status_code=303,
        )
    except Exception as exc:
        logger.exception(
            "Unexpected error running directed expense seed",
            extra={
                "empleado_correo": (empleado_correo or "").strip(),
                "proyecto": (proyecto or "").strip(),
                "year": year,
                "month": month,
                "limit": limit,
                "modo_seed": modo_seed,
                "actor_id": str(getattr(current_empleado, "id", "")),
            },
        )
        return _redirect_with_error_message(redirect_base, _SEED_GENERIC_ERROR)


# ============================================================================
# Bulk CSV Upload for Cuentas Contables
# ============================================================================


@router.get("/admin/cuentas-contables/carga-masiva", response_class=HTMLResponse)
async def carga_masiva_cuentas_contables_form(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = require_admin_finanzas(),
):
    """Bulk upload form for accounting accounts (CSV)."""
    # Get query params for success/error messages
    success_msg = request.query_params.get("success_msg", "")
    error_msg = request.query_params.get("error_msg", "")

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Carga Masiva de Cuentas Contables - Copa Telmex</title>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                padding: 20px;
                min-height: 100vh;
            }}
            .container {{
                max-width: 900px;
                margin: 0 auto;
                background: white;
                border-radius: 12px;
                box-shadow: 0 10px 40px rgba(0,0,0,0.2);
                padding: 30px;
            }}
            h1 {{
                color: #333;
                margin-bottom: 10px;
                border-bottom: 3px solid #667eea;
                padding-bottom: 10px;
            }}
            .subtitle {{
                color: #666;
                margin-bottom: 30px;
            }}
            .alert {{
                padding: 15px 20px;
                border-radius: 8px;
                margin-bottom: 20px;
                font-weight: 500;
            }}
            .alert-success {{
                background: #d4edda;
                color: #155724;
                border: 1px solid #c3e6cb;
            }}
            .alert-error {{
                background: #f8d7da;
                color: #721c24;
                border: 1px solid #f5c6cb;
            }}
            .instructions {{
                background: #f8f9fa;
                padding: 20px;
                border-radius: 8px;
                margin-bottom: 30px;
                border-left: 4px solid #667eea;
            }}
            .instructions h2 {{
                color: #333;
                margin-bottom: 15px;
                font-size: 18px;
            }}
            .instructions ul {{
                margin-left: 20px;
            }}
            .instructions li {{
                margin-bottom: 8px;
                color: #555;
            }}
            .code {{
                background: #e9ecef;
                padding: 2px 6px;
                border-radius: 3px;
                font-family: monospace;
                font-size: 13px;
            }}
            .form-group {{
                margin-bottom: 20px;
            }}
            label {{
                display: block;
                margin-bottom: 8px;
                font-weight: 600;
                color: #333;
            }}
            input[type="file"] {{
                width: 100%;
                padding: 12px;
                border: 2px dashed #ddd;
                border-radius: 6px;
                background: #f8f9fa;
                cursor: pointer;
                font-size: 14px;
            }}
            input[type="file"]:hover {{
                border-color: #667eea;
                background: #fff;
            }}
            .btn {{
                padding: 12px 24px;
                border: none;
                border-radius: 6px;
                font-size: 16px;
                font-weight: 600;
                cursor: pointer;
                transition: all 0.3s;
                text-decoration: none;
                display: inline-block;
                margin-right: 10px;
            }}
            .btn-primary {{
                background: #667eea;
                color: white;
            }}
            .btn-primary:hover {{
                background: #5568d3;
                transform: translateY(-2px);
                box-shadow: 0 4px 12px rgba(102, 126, 234, 0.4);
            }}
            .btn-secondary {{
                background: #6c757d;
                color: white;
            }}
            .btn-secondary:hover {{
                background: #5a6268;
            }}
            table {{
                width: 100%;
                border-collapse: collapse;
                margin-top: 15px;
                background: white;
                border-radius: 4px;
                overflow: hidden;
            }}
            th, td {{
                padding: 10px;
                text-align: left;
                border-bottom: 1px solid #ddd;
            }}
            th {{
                background-color: #667eea;
                color: white;
                font-weight: 600;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            {_CONFIG_PANEL_BACK_LINK_HTML}
            <h1>📥 Carga Masiva de Cuentas Contables</h1>
            <p class="subtitle">Importar cuentas contables desde archivo CSV o XLSX (Balanza)</p>

            {'<div class="alert alert-success">✅ ' + success_msg + '</div>' if success_msg else ''}
            {'<div class="alert alert-error">❌ ' + error_msg + '</div>' if error_msg else ''}

            <div class="instructions">
                <h2>📋 Instrucciones</h2>
                <ul>
                    <li>El archivo puede ser un CSV genérico o un XLSX tipo Balanza.</li>
                    <li>Para CSV, debe contener las siguientes columnas (no sensible a mayúsculas/minúsculas):</li>
                    <li>
                        <table>
                            <tr>
                                <th>Columna</th>
                                <th>Requerida</th>
                                <th>Descripción</th>
                                <th>Valores Ejemplo</th>
                            </tr>
                            <tr>
                                <td><span class="code">codigo</span></td>
                                <td>✅ Sí</td>
                                <td>Código único de la cuenta</td>
                                <td>1001, 2002, PROV-001</td>
                            </tr>
                            <tr>
                                <td><span class="code">nombre</span></td>
                                <td>✅ Sí</td>
                                <td>Nombre descriptivo</td>
                                <td>Caja General, Proveedores</td>
                            </tr>
                            <tr>
                                <td><span class="code">activo</span></td>
                                <td>❌ No</td>
                                <td>Estado activo/inactivo</td>
                                <td>true, false, 1, 0, si, no</td>
                            </tr>
                        </table>
                    </li>
                    <li><strong>Comportamiento de importación (UPSERT):</strong>
                        <ul>
                            <li>Si el <span class="code">codigo</span> ya existe: se actualizan el <span class="code">nombre</span>, <span class="code">activo</span> y <span class="code">tipo</span></li>
                            <li>Si el <span class="code">codigo</span> no existe: se crea una nueva cuenta</li>
                            <li>Las filas con <span class="code">codigo</span> o <span class="code">nombre</span> vacíos se omiten</li>
                        </ul>
                    </li>
                    <li>Para CSV: codificación UTF-8 (preferido) o Latin-1</li>
                    <li>Para XLSX: se toma la primera hoja y se detectan columnas tipo <span class="code">Cuenta</span> y <span class="code">Descripción de la cuenta</span></li>
                    <li>Se ignoran filas vacías</li>
                </ul>
            </div>

            <form method="POST" action="/admin/cuentas-contables/carga-masiva" enctype="multipart/form-data">
                <div class="form-group">
                    <label for="archivo_csv">📂 Selecciona el archivo CSV o XLSX:</label>
                    <input type="file" id="archivo_csv" name="archivo_csv" accept=".csv,.xlsx" required>
                </div>

                <div style="margin-top: 30px;">
                    <button type="submit" class="btn btn-primary">📤 Importar Cuentas Contables</button>
                    <a href="/admin/cuentas-contables" class="btn btn-secondary">⬅️ Volver</a>
                </div>
            </form>
        </div>
    </body>
    </html>
    """

    return HTMLResponse(content=html_content)


@router.post("/admin/cuentas-contables/carga-masiva")
async def carga_masiva_cuentas_contables_post(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = require_admin_finanzas(),
    archivo_csv: UploadFile = File(...),
):
    """Process bulk CSV/XLSX upload for accounting accounts (UPSERT)."""
    from fastapi.responses import RedirectResponse
    from urllib.parse import quote

    try:
        filename = archivo_csv.filename or ""
        if not filename or not filename.lower().endswith((".csv", ".xlsx")):
            return RedirectResponse(
                url="/admin/cuentas-contables/carga-masiva?error_msg=Debe seleccionar un archivo CSV o XLSX válido",
                status_code=303,
            )

        contents = await archivo_csv.read()
        try:
            import_rows = parse_cuentas_contables_upload(filename, contents)
        except ValueError as exc:
            return RedirectResponse(
                url=f"/admin/cuentas-contables/carga-masiva?error_msg={quote(str(exc))}",
                status_code=303,
            )
        if not import_rows:
            return RedirectResponse(
                url="/admin/cuentas-contables/carga-masiva?error_msg=El archivo no contiene filas válidas para importar",
                status_code=303,
            )

        created_count = 0
        updated_count = 0
        skipped_count = max(
            0,
            len(import_rows)
            - len([row for row in import_rows if row.codigo and row.nombre]),
        )

        for import_row in import_rows:
            result = await session.execute(
                select(CuentaContable).where(CuentaContable.codigo == import_row.codigo)
            )
            cuenta = result.scalar_one_or_none()

            if cuenta:
                cuenta.nombre = import_row.nombre
                cuenta.activo = import_row.activo
                cuenta.tipo = import_row.tipo
                updated_count += 1
            else:
                nueva_cuenta = CuentaContable(
                    codigo=import_row.codigo,
                    nombre=import_row.nombre,
                    tipo=import_row.tipo,
                    activo=import_row.activo,
                )
                session.add(nueva_cuenta)
                created_count += 1

        if created_count > 0 or updated_count > 0:
            await session.commit()
            success_msg = (
                f"Se importaron {created_count + updated_count} cuentas contables desde {filename} "
                f"({created_count} creadas, {updated_count} actualizadas, {skipped_count} omitidas)."
            )
            return RedirectResponse(
                url=f"/admin/cuentas-contables/carga-masiva?success_msg={quote(success_msg)}",
                status_code=303,
            )
        else:
            await session.rollback()
            return RedirectResponse(
                url="/admin/cuentas-contables/carga-masiva?error_msg=No se encontraron registros válidos para importar",
                status_code=303,
            )

    except Exception as e:
        await session.rollback()
        logger.exception(
            "Unexpected error processing cuentas contables upload",
            extra={
                "filename": archivo_csv.filename or "",
                "actor_id": str(getattr(current_empleado, "id", "")),
            },
        )
        return _redirect_with_error_message(
            "/admin/cuentas-contables/carga-masiva",
            _BULK_GENERIC_ERROR,
        )


# ============================================================================
# Cost Centers Management Routes
# ============================================================================


@router.get("/admin/centros-costo", response_class=HTMLResponse)
async def admin_centros_costo(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = require_admin_finanzas(),
):
    """Admin interface for managing cost centers."""
    result = await session.execute(select(CentroDeCosto).order_by(CentroDeCosto.codigo))
    centros = result.scalars().all()

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Gestión de Centros de Costo - Copa Telmex</title>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                padding: 20px;
                min-height: 100vh;
            }}
            .container {{
                max-width: 1200px;
                margin: 0 auto;
                background: white;
                border-radius: 12px;
                box-shadow: 0 10px 40px rgba(0,0,0,0.2);
                padding: 30px;
            }}
            h1 {{
                color: #333;
                margin-bottom: 10px;
                border-bottom: 3px solid #667eea;
                padding-bottom: 10px;
            }}
            .subtitle {{
                color: #666;
                margin-bottom: 30px;
            }}
            .form-section {{
                background: #f8f9fa;
                padding: 20px;
                border-radius: 8px;
                margin-bottom: 30px;
            }}
            .form-group {{
                margin-bottom: 15px;
            }}
            label {{
                display: block;
                margin-bottom: 5px;
                font-weight: 600;
                color: #333;
            }}
            input[type="text"] {{
                width: 100%;
                padding: 10px;
                border: 2px solid #ddd;
                border-radius: 6px;
                font-size: 14px;
            }}
            input[type="text"]:focus {{
                outline: none;
                border-color: #667eea;
            }}
            .checkbox-group {{
                display: flex;
                align-items: center;
                gap: 10px;
            }}
            input[type="checkbox"] {{
                width: 20px;
                height: 20px;
            }}
            .btn {{
                padding: 12px 24px;
                border: none;
                border-radius: 6px;
                font-size: 16px;
                font-weight: 600;
                cursor: pointer;
                transition: all 0.3s;
                text-decoration: none;
                display: inline-block;
            }}
            .btn-primary {{
                background: #667eea;
                color: white;
            }}
            .btn-primary:hover {{
                background: #5568d3;
                transform: translateY(-2px);
                box-shadow: 0 4px 12px rgba(102, 126, 234, 0.4);
            }}
            .btn-secondary {{
                background: #6c757d;
                color: white;
            }}
            .btn-secondary:hover {{
                background: #5a6268;
            }}
            table {{
                width: 100%;
                border-collapse: collapse;
                background: white;
                border-radius: 8px;
                overflow: hidden;
                box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            }}
            th {{
                background-color: #667eea;
                color: white;
                padding: 12px;
                text-align: left;
                font-weight: bold;
            }}
            td {{
                padding: 10px 12px;
                border-bottom: 1px solid #ddd;
            }}
            tr:hover {{
                background-color: #f9f9f9;
            }}
            .badge {{
                padding: 4px 12px;
                border-radius: 12px;
                font-size: 12px;
                font-weight: 600;
            }}
            .badge-active {{
                background: #d4edda;
                color: #155724;
            }}
            .badge-inactive {{
                background: #f8d7da;
                color: #721c24;
            }}
            .nav-links {{
                margin-bottom: 20px;
            }}
            .nav-links a {{
                color: #667eea;
                text-decoration: none;
                margin-right: 20px;
                font-weight: 600;
            }}
            .nav-links a:hover {{
                text-decoration: underline;
            }}
            .btn-small {{
                padding: 8px 16px;
                font-size: 14px;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            {render_admin_navigation(current_empleado, "centros", subtitle="Centros de costo alineados al control contable y operativo.")}

            <h1>Gestión de Centros de Costo</h1>
            <p class="subtitle">Administra el catálogo de centros de costo</p>

            <div class="form-section">
                <h2 style="margin-bottom: 15px;">➕ Agregar Nuevo Centro de Costo</h2>
                <form method="POST" action="/admin/centros-costo/create">
                    <div class="form-group">
                        <label for="codigo">Código *</label>
                        <input type="text" id="codigo" name="codigo" required placeholder="Ej: CC001">
                    </div>
                    <div class="form-group">
                        <label for="nombre">Nombre *</label>
                        <input type="text" id="nombre" name="nombre" required placeholder="Ej: Centro de Costo Principal">
                    </div>
                    <div class="form-group">
                        <div class="checkbox-group">
                            <input type="checkbox" id="activo" name="activo" checked>
                            <label for="activo" style="margin: 0;">Activo</label>
                        </div>
                    </div>
                    <button type="submit" class="btn btn-primary">Crear Centro de Costo</button>
                </form>
            </div>

            <div style="margin-top: 30px;">
                <h2 style="margin-bottom: 20px;">📋 Centros de Costo ({len(centros)})</h2>
                {"<p style='color: #666;'>No hay centros de costo registrados aún.</p>" if not centros else ""}
                <table>
                    <thead>
                        <tr>
                            <th>Código</th>
                            <th>Nombre</th>
                            <th>Estado</th>
                            <th>Acciones</th>
                        </tr>
                    </thead>
                    <tbody>
"""

    for centro in centros:
        status_class = "badge-active" if centro.activo else "badge-inactive"
        status_text = "Activo" if centro.activo else "Inactivo"
        html_content += f"""
                        <tr>
                            <td>{centro.codigo}</td>
                            <td>{centro.nombre}</td>
                            <td><span class="badge {status_class}">{status_text}</span></td>
                            <td>
                                <a href="/admin/centros-costo/edit/{centro.id}" class="btn btn-secondary btn-small">✏️ Editar</a>
                            </td>
                        </tr>
"""

    html_content += """
                    </tbody>
                </table>
            </div>
        </div>
    </body>
    </html>
    """

    return HTMLResponse(content=html_content)


@router.post("/admin/centros-costo/create")
async def create_centro_costo(
    request: Request,
    codigo: str = Form(...),
    nombre: str = Form(...),
    session: AsyncSession = Depends(get_db_session),
):
    """Create a new cost center."""
    try:
        form_data = await request.form()
        activo = form_data.get("activo") == "on"

        centro = CentroDeCosto(
            codigo=codigo.strip(), nombre=nombre.strip(), activo=activo
        )
        session.add(centro)
        await session.commit()
        await session.refresh(centro)

        return HTMLResponse(
            content="""
            <!DOCTYPE html>
            <html>
            <head>
                <meta http-equiv="refresh" content="2;url=/admin/centros-costo">
                <title>Éxito</title>
            </head>
            <body style="font-family: sans-serif; padding: 20px; text-align: center;">
                <h2 style="color: green;">✅ Centro de costo creado exitosamente</h2>
                <p>Redirigiendo...</p>
            </body>
            </html>
            """
        )
    except Exception as e:
        await session.rollback()
        logger.exception(
            "Unexpected error creating centro de costo",
            extra={"codigo": codigo.strip()},
        )
        return _catalog_error_response(
            back_href="/admin/centros-costo",
            message=_CATALOG_GENERIC_SAVE_ERROR,
            status_code=400,
        )


@router.get("/admin/centros-costo/edit/{centro_id}", response_class=HTMLResponse)
async def edit_centro_costo_form(
    centro_id: UUIDType,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
):
    """Edit cost center form."""
    result = await session.execute(
        select(CentroDeCosto).where(CentroDeCosto.id == centro_id)
    )
    centro = result.scalar_one_or_none()

    if not centro:
        raise HTTPException(status_code=404, detail="Centro de costo not found")

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Editar Centro de Costo - Copa Telmex</title>
        <meta charset="utf-8">
        <style>
            body {{ font-family: sans-serif; padding: 20px; max-width: 800px; margin: 0 auto; }}
            .form-group {{ margin-bottom: 15px; }}
            label {{ display: block; margin-bottom: 5px; font-weight: 600; }}
            input[type="text"] {{
                width: 100%;
                padding: 10px;
                border: 2px solid #ddd;
                border-radius: 6px;
            }}
            .btn {{
                padding: 12px 24px;
                border: none;
                border-radius: 6px;
                cursor: pointer;
                text-decoration: none;
                display: inline-block;
            }}
            .btn-primary {{ background: #667eea; color: white; }}
            .btn-secondary {{ background: #6c757d; color: white; margin-left: 10px; }}
        </style>
    </head>
    <body>
        {_CONFIG_PANEL_BACK_LINK_HTML}
        <h1>✏️ Editar Centro de Costo</h1>
        <form method="POST" action="/admin/centros-costo/update/{centro_id}">
            <div class="form-group">
                <label>Código *</label>
                <input type="text" name="codigo" value="{centro.codigo}" required>
            </div>
            <div class="form-group">
                <label>Nombre *</label>
                <input type="text" name="nombre" value="{centro.nombre}" required>
            </div>
            <div class="form-group">
                <label>
                    <input type="checkbox" name="activo" {'checked' if centro.activo else ''}>
                    Activo
                </label>
            </div>
            <button type="submit" class="btn btn-primary">Guardar Cambios</button>
            <a href="/admin/centros-costo" class="btn btn-secondary">Cancelar</a>
        </form>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)


@router.post("/admin/centros-costo/update/{centro_id}")
async def update_centro_costo(
    centro_id: UUIDType,
    request: Request,
    codigo: str = Form(...),
    nombre: str = Form(...),
    session: AsyncSession = Depends(get_db_session),
):
    """Update a cost center."""
    form_data = await request.form()
    activo = form_data.get("activo") == "on"

    result = await session.execute(
        select(CentroDeCosto).where(CentroDeCosto.id == centro_id)
    )
    centro = result.scalar_one_or_none()

    if not centro:
        raise HTTPException(status_code=404, detail="Centro de costo not found")

    try:
        centro.codigo = codigo.strip()
        centro.nombre = nombre.strip()
        centro.activo = activo

        await session.commit()

        return HTMLResponse(
            content="""
            <!DOCTYPE html>
            <html>
            <head>
                <meta http-equiv="refresh" content="2;url=/admin/centros-costo">
                <title>Éxito</title>
            </head>
            <body style="font-family: sans-serif; padding: 20px; text-align: center;">
                <h2 style="color: green;">✅ Centro de costo actualizado exitosamente</h2>
                <p>Redirigiendo...</p>
            </body>
            </html>
            """
        )
    except Exception:
        await session.rollback()
        logger.exception(
            "Unexpected error updating centro de costo",
            extra={"centro_id": str(centro_id), "codigo": codigo.strip()},
        )
        return _catalog_error_response(
            back_href="/admin/centros-costo",
            message=_CATALOG_GENERIC_SAVE_ERROR,
            status_code=400,
        )


# ============================================================================
# Suppliers and Clients Placeholder Route
# ============================================================================

# ============================================================================
# Proveedores/Clientes Management Routes
# ============================================================================


@router.get("/admin/proveedores-clientes", response_class=HTMLResponse)
async def admin_proveedores_clientes(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = Depends(get_current_empleado),
    search: Optional[str] = Query(None),
    tipo_filter: Optional[str] = Query(None),
    activo_filter: Optional[str] = Query(None),
    success_msg: Optional[str] = Query(None),
    error_msg: Optional[str] = Query(None),
):
    """Admin interface for managing suppliers and clients."""
    try:
        # Build query with filters
        # Note: Using joinedload instead of selectinload to avoid potential issues
        query = select(ProveedorCliente)
Warning: truncated output (original token count: 55781)
Total output lines: 5000


        conditions = []
        if search:
            conditions.append(
                or_(
                    ProveedorCliente.nombre.ilike(f"%{search}%"),
                    ProveedorCliente.rfc.ilike(f"%{search}%"),
                )
            )
        if tipo_filter and tipo_filter != "todos":
            conditions.append(ProveedorCliente.tipo == tipo_filter)
        if activo_filter and activo_filter != "todos":
            if activo_filter == "activos":
                conditions.append(ProveedorCliente.activo.is_(True))
            elif activo_filter == "inactivos":
                conditions.append(ProveedorCliente.activo.is_(False))

        if conditions:
            query = query.where(and_(*conditions))

        query = query.order_by(ProveedorCliente.nombre)

        result = await session.execute(query)
        proveedores = result.scalars().all()

        empleados_result = await session.execute(
            select(Empleado)
            .where(Empleado.activo.is_(True))
            .order_by(Empleado.nombre)
        )
        empleados_activos = empleados_result.scalars().all()
        empleado_options = "".join(
            f'<option value="{empleado.id}">{escape(empleado.nombre)}</option>'
            for empleado in empleados_activos
        )

        success_html = ""
        if success_msg:
            success_html = f"""
                <div style="background: #d4edda; border: 1px solid #c3e6cb; border-radius: 5px; padding: 15px; margin-bottom: 20px; color: #155724;">
                    <strong>✅ Éxito:</strong> {success_msg}
                </div>
            """

        error_html = ""
        if error_msg:
            error_html = f"""
                <div style="background: #f8d7da; border: 1px solid #f5c6cb; border-radius: 5px; padding: 15px; margin-bottom: 20px; color: #721c24;">
                    <strong>❌ Error:</strong> {error_msg}
                </div>
            """

        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
        <title>Proveedores, Clientes, Operadores y Empleados - Copa Telmex</title>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                padding: 20px;
                min-height: 100vh;
            }}
            .container {{
                max-width: 1400px;
                margin: 0 auto;
                background: white;
                border-radius: 12px;
                box-shadow: 0 10px 40px rgba(0,0,0,0.2);
                padding: 30px;
            }}
            h1 {{
                color: #333;
                margin-bottom: 10px;
                border-bottom: 3px solid #667eea;
                padding-bottom: 10px;
            }}
            .subtitle {{
                color: #666;
                margin-bottom: 30px;
            }}
            .filters-section {{
                background: #f8f9fa;
                padding: 20px;
                border-radius: 8px;
                margin-bottom: 30px;
            }}
            .filters-row {{
                display: grid;
                grid-template-columns: 2fr 1fr 1fr auto;
                gap: 15px;
                align-items: end;
            }}
            .form-section {{
                background: #f8f9fa;
                padding: 20px;
                border-radius: 8px;
                margin-bottom: 30px;
            }}
            .form-group {{
                margin-bottom: 15px;
            }}
            .form-row {{
                display: grid;
                grid-template-columns: 1fr 1fr;
                gap: 15px;
            }}
            label {{
                display: block;
                margin-bottom: 5px;
                font-weight: 600;
                color: #333;
            }}
            input[type="text"], select {{
                width: 100%;
                padding: 10px;
                border: 2px solid #ddd;
                border-radius: 6px;
                font-size: 14px;
            }}
            input[type="text"]:focus, select:focus {{
                outline: none;
                border-color: #667eea;
            }}
            .checkbox-group {{
                display: flex;
                align-items: center;
                gap: 10px;
            }}
            input[type="checkbox"] {{
                width: 20px;
                height: 20px;
            }}
            .btn {{
                padding: 12px 24px;
                border: none;
                border-radius: 6px;
                font-size: 16px;
                font-weight: 600;
                cursor: pointer;
                transition: all 0.3s;
                text-decoration: none;
                display: inline-block;
            }}
            .btn-primary {{
                background: #667eea;
                color: white;
            }}
            .btn-primary:hover {{
                background: #5568d3;
                transform: translateY(-2px);
                box-shadow: 0 4px 12px rgba(102, 126, 234, 0.4);
            }}
            .btn-secondary {{
                background: #6c757d;
                color: white;
            }}
            .btn-secondary:hover {{
                background: #5a6268;
            }}
            table {{
                width: 100%;
                border-collapse: collapse;
                background: white;
                border-radius: 8px;
                overflow: hidden;
                box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            }}
            th {{
                background-color: #667eea;
                color: white;
                padding: 12px;
                text-align: left;
                font-weight: bold;
            }}
            td {{
                padding: 10px 12px;
                border-bottom: 1px solid #ddd;
            }}
            tr:hover {{
                background-color: #f9f9f9;
            }}
            .badge {{
                padding: 4px 12px;
                border-radius: 12px;
                font-size: 12px;
                font-weight: 600;
            }}
            .badge-active {{
                background: #d4edda;
                color: #155724;
            }}
            .badge-inactive {{
                background: #f8d7da;
                color: #721c24;
            }}
            .badge-proveedor {{
                background: #cfe2ff;
                color: #084298;
            }}
            .badge-cliente {{
                background: #fff3cd;
                color: #856404;
            }}
            .badge-operadores-regionales {{
                background: #d1e7dd;
                color: #0f5132;
            }}
            .badge-empleado {{
                background: #e2d9f3;
                color: #432874;
            }}
            .badge-participante-torneo {{
                background: #dcfce7;
                color: #166534;
            }}
            .nav-links {{
                margin-bottom: 20px;
            }}
            .nav-links a {{
                color: #667eea;
                text-decoration: none;
                margin-right: 20px;
                font-weight: 600;
            }}
            .nav-links a:hover {{
                text-decoration: underline;
            }}
            .btn-small {{
                padding: 8px 16px;
                font-size: 14px;
            }}
            .cuenta-search-container {{
                position: relative;
            }}
            .cuenta-search-input {{
                margin-bottom: 10px;
            }}
            .cuenta-select {{
                max-height: 200px;
                overflow-y: auto;
            }}
        </style>
        </head>
        <body>
        <div class="container">
            {render_admin_navigation(current_empleado, "proveedores", subtitle="Catálogo comercial, operativo y de cuentas destino para empleados.")}

            <h1>Proveedores, Clientes, Operadores y Empleados</h1>
            <p class="subtitle">Administra contrapartes y cuentas destino sin confundir empleados con proveedores.</p>

            {success_html}
            {error_html}

            <div class="filters-section">
                <h2 style="margin-bottom: 15px;">🔍 Buscar y Filtrar</h2>
                <form method="GET" action="/admin/proveedores-clientes">
                    <div class="filters-row">
                        <div class="form-group">
                            <label for="search">Buscar (Nombre o RFC)</label>
                            <input type="text" id="search" name="search" value="{search or ''}" placeholder="Buscar por nombre o RFC...">
                        </div>
                        <div class="form-group">
                            <label for="tipo_filter">Tipo</label>
                            <select id="tipo_filter" name="tipo_filter">
                                <option value="todos" {'selected' if not tipo_filter or tipo_filter == 'todos' else ''}>Todos</option>
                                <option value="proveedor" {'selected' if tipo_filter == 'proveedor' else ''}>Proveedor</option>
                                <option value="cliente" {'selected' if tipo_filter == 'cliente' else ''}>Cliente</option>
                                <option value="operadores_regionales" {'selected' if tipo_filter == 'operadores_regionales' else ''}>Operadores Regionales</option>
                                <option value="empleado" {'selected' if tipo_filter == 'empleado' else ''}>Empleado</option>
                                <option value="participante_torneo" {'selected' if tipo_filter == 'participante_torneo' else ''}>Participante de Torneos</option>
                            </select>
                        </div>
                        <div class="form-group">
                            <label for="activo_filter">Estado</label>
                            <select id="activo_filter" name="activo_filter">
                                <option value="todos" {'selected' if not activo_filter or activo_filter == 'todos' else ''}>Todos</option>
                                <option value="activos" {'selected' if activo_filter == 'activos' else ''}>Activos</option>
                                <option value="inactivos" {'selected' if activo_filter == 'inactivos' else ''}>Inactivos</option>
                            </select>
                        </div>
                        <div class="form-group">
                            <button type="submit" class="btn btn-primary">🔍 Buscar</button>
                        </div>
                    </div>
                </form>
            </div>

            <div class="form-section">
                <h2 style="margin-bottom: 15px;">➕ Agregar registro al padrón</h2>
                <form method="POST" action="/admin/proveedores-clientes/create">
                    <div class="form-row">
                        <div class="form-group">
                            <label for="tipo">Tipo *</label>
                            <select id="tipo" name="tipo" required>
                                <option value="">Seleccionar...</option>
                                <option value="proveedor">Proveedor</option>
                                <option value="cliente">Cliente</option>
                                <option value="operadores_regionales">Operadores Regionales</option>
                                <option value="empleado">Empleado</option>
                                <option value="participante_torneo">Participante de Torneos</option>
                            </select>
                        </div>
                        <div class="form-group">
                            <label for="nombre">Nombre *</label>
                            <input type="text" id="nombre" name="nombre" required placeholder="Nombre del proveedor/cliente/empleado">
                        </div>
                    </div>
                    <div class="form-row">
                        <div class="form-group">
                            <div class="checkbox-group">
                                <input type="checkbox" id="es_empleado" name="es_empleado">
                                <label for="es_empleado" style="margin: 0;">Es empleado (no proveedor)</label>
                            </div>
                            <small style="color:#666;">Al marcarlo, la categoría canónica será Empleado.</small>
                        </div>
                        <div class="form-group" id="empleado_selector_group" style="display:none;">
                            <label for="empleado_id">Empleado activo *</label>
                            <select id="empleado_id" name="empleado_id">
                                <option value="">Seleccionar empleado...</option>
                                {empleado_options}
                            </select>
                        </div>
                    </div>
                    <div class="form-row">
                        <div class="form-group">
                            <label for="rfc">RFC</label>
                            <input type="text" id="rfc" name="rfc" placeholder="RFC (opcional)">
                        </div>
                        <div class="form-group">
                            <label for="banco">Banco</label>
                            <input type="text" id="banco" name="banco" placeholder="Nombre del banco (opcional)">
                        </div>
                    </div>
                    <div class="form-row">
                        <div class="form-group">
                            <label for="cuenta_clabe">Cuenta CLABE</label>
                            <input type="text" id="cuenta_clabe" name="cuenta_clabe" placeholder="18 dígitos (opcional)" maxlength="18">
                            <small style="color: #666; display: block; margin-top: 5px;">Debe tener exactamente 18 dígitos numéricos</small>
                        </div>
                        <div class="form-group">
                            <label for="cuenta_bancaria">Cuenta Bancaria</label>
                            <input type="text" id="cuenta_bancaria" name="cuenta_bancaria" placeholder="Número de cuenta bancaria (opcional)">
                            <small style="color: #666; display: block; margin-top: 5px;">Número de cuenta bancaria para transferencias (no confundir con cuenta contable)</small>
                        </div>
                    </div>
                    <div class="form-row">
                        <div class="form-group">
                            <label for="entidad_region">Entidad/Región</label>
                            <input type="text" id="entidad_region" name="entidad_region" placeholder="Ej. CDMX, Norte (opcional)">
                        </div>
                        <div class="form-group"></div>
                    </div>
                    <div class="form-group">
                        <div class="checkbox-group">
                            <input type="checkbox" id="activo" name="activo" checked>
                            <label for="activo" style="margin: 0;">Activo</label>
                        </div>
                    </div>
                    <button type="submit" class="btn btn-primary">Crear registro</button>
                </form>
            </div>

            <div style="margin-top: 30px;">
                <h2 style="margin-bottom: 20px;">📋 Padrón ({len(proveedores)})</h2>
                {"<p style='color: #666;'>No hay proveedores/clientes registrados aún.</p>" if not proveedores else ""}
                <table>
                    <thead>
                        <tr>
                            <th>Nombre</th>
                            <th>Tipo</th>
                            <th>RFC</th>
                            <th>Banco</th>
                            <th>Cuenta CLABE</th>
                            <th>Cuenta Bancaria</th>
                            <th>Entidad/Región</th>
                            <th>Estado</th>
                            <th>Acciones</th>
                        </tr>
                    </thead>
                    <tbody>
        """

        for prov in proveedores:
            if prov.tipo == "proveedor":
                tipo_class = "badge-proveedor"
            elif prov.tipo == "cliente":
                tipo_class = "badge-cliente"
            elif prov.tipo == "empleado":
                tipo_class = "badge-empleado"
            elif prov.tipo == "participante_torneo":
                tipo_class = "badge-participante-torneo"
            else:
                tipo_class = "badge-operadores-regionales"
            tipo_display = (
                "Operadores Regionales"
                if prov.tipo == "operadores_regionales"
                else (
                    "Empleado"
                    if prov.tipo == "empleado"
                    else (
                        "Participante de Torneos"
                        if prov.tipo == "participante_torneo"
                        else prov.tipo.capitalize()
                    )
                )
            )
            status_class = "badge-active" if prov.activo else "badge-inactive"
            status_text = "Activo" if prov.activo else "Inactivo"
            toggle_label = "Dar de baja" if prov.activo else "Reactivar"
            toggle_value = "false" if prov.activo else "true"
            toggle_confirm = (
                "Dar de baja este proveedor/cliente?"
                if prov.activo
                else "Reactivar este proveedor/cliente?"
            )
            cuenta_bancaria_display = (
                str(prov.cuenta_bancaria or "—")
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
            )

            # Escape HTML entities for display
            nombre_escaped = (
                str(prov.nombre or "—")
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
            )
            rfc_escaped = (
                str(prov.rfc or "—")
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
            )
            banco_escaped = (
                str(prov.banco or "—")
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
            )

            html_content += f"""
                        <tr>
                            <td><strong>{nombre_escaped}</strong></td>
                            <td><span class="badge {tipo_class}">{tipo_display}</span></td>
                            <td>{rfc_escaped}</td>
                            <td>{banco_escaped}</td>
                            <td>{prov.cuenta_clabe or '—'}</td>
                            <td>{cuenta_bancaria_display}</td>
                            <td>{str(prov.entidad_region or '—').replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")}</td>
                            <td><span class="badge {status_class}">{status_text}</span></td>
                            <td>
                                <a href="/admin/proveedores-clientes/edit/{prov.id}" class="btn btn-secondary btn-small">✏️ Editar</a>
                                <form method="POST" action="/admin/proveedores-clientes/{prov.id}/estado" style="display:inline;">
                                    <input type="hidden" name="activo" value="{toggle_value}">
                                    <button type="submit" class="btn btn-secondary btn-small" onclick="return confirm('{toggle_confirm}')">{toggle_label}</button>
                                </form>
                            </td>
                        </tr>
"""

        html_content += """
                    </tbody>
                </table>
            </div>
        </div>
        <script>
        (() => {
            const tipo = document.getElementById('tipo');
            const checkbox = document.getElementById('es_empleado');
            const group = document.getElementById('empleado_selector_group');
            const employee = document.getElementById('empleado_id');
            const name = document.getElementById('nombre');
            const sync = (fromCheckbox = false) => {
                if (fromCheckbox && checkbox.checked) tipo.value = 'empleado';
                checkbox.checked = tipo.value === 'empleado' || checkbox.checked;
                if (tipo.value !== 'empleado' && !fromCheckbox) checkbox.checked = false;
                const enabled = checkbox.checked || tipo.value === 'empleado';
                group.style.display = enabled ? 'block' : 'none';
                employee.required = enabled;
            };
            checkbox.addEventListener('change', () => sync(true));
            tipo.addEventListener('change', () => sync(false));
            employee.addEventListener('change', () => {
                if (employee.selectedIndex > 0) {
                    name.value = employee.options[employee.selectedIndex].text;
                }
            });
            sync(false);
        })();
        </script>
        </body>
        </html>
        """

        return HTMLResponse(content=html_content)
    except Exception as e:
        logger.error(f"Error in admin_proveedores_clientes: {e}", exc_info=True)
        import traceback

        error_details = traceback.format_exc()
        logger.error(f"Traceback: {error_details}")
        return HTMLResponse(
            content=f"""
            <!DOCTYPE html>
            <html>
            <head>
                <title>Error - Proveedores, Operadores y Clientes</title>
            </head>
            <body style="font-family: sans-serif; padding: 20px;">
                <h1>❌ Error al cargar la página</h1>
                <p>Ocurrió un error al cargar la página de proveedores y clientes.</p>
                <p><strong>Error:</strong> {str(e)}</p>
                <p><a href="/panel">⬅️ Volver al Panel</a></p>
            </body>
            </html>
            """,
            status_code=500,
        )


@router.post("/admin/proveedores-clientes/create")
async def create_proveedor_cliente(
    request: Request,
    tipo: str = Form(...),
    nombre: str = Form(...),
    rfc: Optional[str] = Form(None),
    banco: Optional[str] = Form(None),
    cuenta_clabe: Optional[str] = Form(None),
    cuenta_bancaria: Optional[str] = Form(None),
    entidad_region: Optional[str] = Form(None),
    empleado_id: Optional[str] = Form(None),
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = Depends(get_current_empleado),
):
    """Create a new supplier/client."""
    try:
        form_data = await request.form()
        activo = form_data.get("activo") == "on"
        es_empleado = form_data.get("es_empleado") == "on" or tipo == "empleado"

        # Validations
        nombre = nombre.strip()
        if not nombre and not es_empleado:
            return HTMLResponse(
                content="""
                <!DOCTYPE html>
                <html>
                <head>
                    <meta http-equiv="refresh" content="3;url=/admin/proveedores-clientes?error_msg=El nombre es requerido">
                </head>
                <body>Error: El nombre es requerido. Redirigiendo...</body>
                </html>
                """,
                status_code=400,
            )

        if tipo not in ["proveedor", "cliente", "operadores_regionales", "empleado", "participante_torneo"]:
            return HTMLResponse(
                content="""
                <!DOCTYPE html>
                <html>
                <head>
                    <meta http-equiv="refresh" content="3;url=/admin/proveedores-clientes?error_msg=Tipo inválido">
                </head>
                <body>Error: Tipo inválido. Redirigiendo...</body>
                </html>
                """,
                status_code=400,
            )

        empleado_uuid = None
        if es_empleado:
            tipo = "empleado"
            try:
                empleado_uuid = UUIDType((empleado_id or "").strip())
            except (TypeError, ValueError):
                return RedirectResponse(
                    url=(
                        "/admin/proveedores-clientes?error_msg="
                        + quote("Seleccione un empleado activo válido.")
                    ),
                    status_code=303,
                )
            empleado_result = await session.execute(
                select(Empleado).where(
                    Empleado.id == empleado_uuid,
                    Empleado.activo.is_(True),
                )
            )
            linked_employee = empleado_result.scalar_one_or_none()
            if linked_employee is None:
                return RedirectResponse(
                    url=(
                        "/admin/proveedores-clientes?error_msg="
                        + quote("El empleado seleccionado no existe o está inactivo.")
                    ),
                    status_code=303,
                )
            nombre = linked_employee.nombre
            rfc = None

        # Validate cuenta_clabe if provided
        if cuenta_clabe:
            cuenta_clabe = cuenta_clabe.strip()
            if cuenta_clabe and (len(cuenta_clabe) != 18 or not cuenta_clabe.isdigit()):
                return HTMLResponse(
                    content="""
                    <!DOCTYPE html>
                    <html>
                    <head>
                        <meta http-equiv="refresh" content="3;url=/admin/proveedores-clientes?error_msg=La cuenta CLABE debe tener exactamente 18 dígitos numéricos">
                    </head>
                    <body>Error: La cuenta CLABE debe tener exactamente 18 dígitos numéricos. Redirigiendo...</body>
                    </html>
                    """,
                    status_code=400,
                )

        # Process cuenta_bancaria - store as plain text (no FK, no auto-creation)
        cuenta_bancaria_val = cuenta_bancaria.strip() if cuenta_bancaria else None
        entidad_region_val = entidad_region.strip() if entidad_region else None

        proveedor = ProveedorCliente(
            tipo=tipo,
            nombre=nombre,
            rfc=rfc.strip() if rfc else None,
            banco=banco.strip() if banco else None,
            cuenta_clabe=cuenta_clabe if cuenta_clabe else None,
            cuenta_bancaria=cuenta_bancaria_val,
            entidad_region=entidad_region_val,
            empleado_id=empleado_uuid,
            activo=activo,
        )
        session.add(proveedor)
        await session.commit()
        await session.refresh(proveedor)

        return HTMLResponse(
            content="""
            <!DOCTYPE html>
            <html>
            <head>
                <meta http-equiv="refresh" content="2;url=/admin/proveedores-clientes?success_msg=Proveedor/Cliente creado exitosamente">
                <title>Éxito</title>
            </head>
            <body style="font-family: sans-serif; padding: 20px; text-align: center;">
                <h2 style="color: green;">✅ Proveedor/Cliente creado exitosamente</h2>
                <p>Redirigiendo...</p>
            </body>
            </html>
            """
        )
    except Exception as e:
        await session.rollback()
        error_msg = str(e)
        if "unique constraint" in error_msg.lower() or "duplicate" in error_msg.lower():
            error_msg = "Ya existe un registro con esa información y no se pudo guardar."
        else:
            logger.exception(
                "Unexpected error creating proveedor/cliente",
                extra={
                    "tipo": tipo,
                    "nombre": nombre.strip(),
                    "actor_id": str(getattr(current_empleado, "id", "")),
                },
            )
            error_msg = _CATALOG_GENERIC_SAVE_ERROR
        return HTMLResponse(
            content=f"""
            <!DOCTYPE html>
            <html>
            <head>
                <meta http-equiv="refresh" content="3;url=/admin/proveedores-clientes?error_msg={quote(error_msg)}">
            </head>
            <body>Error: {escape(error_msg)}. Redirigiendo...</body>
            </html>
            """,
            status_code=400,
        )


@router.post("/admin/proveedores-clientes/{proveedor_id}/estado")
async def update_proveedor_cliente_estado(
    proveedor_id: UUIDType,
    activo: str = Form(...),
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = Depends(get_current_empleado),
):
    """Soft-disable or reactivate a supplier/client without deleting history."""
    result = await session.execute(
        select(ProveedorCliente).where(ProveedorCliente.id == proveedor_id)
    )
    prov = result.scalar_one_or_none()
    if not prov:
        raise HTTPException(
            status_code=404,
            detail="Proveedor/Cliente no encontrado",
        )

    should_activate = (activo or "").strip().lower() in {"1", "true", "on", "yes"}
    prov.activo = should_activate
    await session.commit()
    msg = (
        "Proveedor/Cliente reactivado exitosamente"
        if should_activate
        else "Proveedor/Cliente dado de baja exitosamente"
    )
    return RedirectResponse(
        url=f"/admin/proveedores-clientes?success_msg={quote(msg)}",
        status_code=303,
    )


@router.get(
    "/admin/proveedores-clientes/edit/{proveedor_id}", response_class=HTMLResponse
)
async def edit_proveedor_cliente_form(
    proveedor_id: UUIDType,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = Depends(get_current_empleado),
):
    """Edit supplier/client form."""
    result = await session.execute(
        select(ProveedorCliente).where(ProveedorCliente.id == proveedor_id)
    )
    prov = result.scalar_one_or_none()

    if not prov:
        raise HTTPException(status_code=404, detail="Proveedor/Cliente no encontrado")

    empleados_result = await session.execute(
        select(Empleado)
        .where(Empleado.activo.is_(True))
        .order_by(Empleado.nombre)
    )
    empleados_activos = empleados_result.scalars().all()
    empleado_options = "".join(
        (
            f'<option value="{empleado.id}" '
            f'{"selected" if prov.empleado_id == empleado.id else ""}>'
            f'{escape(empleado.nombre)}</option>'
        )
        for empleado in empleados_activos
    )

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Editar Proveedor/Cliente - Copa Telmex</title>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                padding: 20px;
                min-height: 100vh;
            }}
            .container {{
                max-width: 900px;
                margin: 0 auto;
                background: white;
                border-radius: 12px;
                box-shadow: 0 10px 40px rgba(0,0,0,0.2);
                padding: 30px;
            }}
            h1 {{
                color: #333;
                margin-bottom: 20px;
                border-bottom: 3px solid #667eea;
                padding-bottom: 10px;
            }}
            .form-group {{
                margin-bottom: 15px;
            }}
            .form-row {{
                display: grid;
                grid-template-columns: 1fr 1fr;
                gap: 15px;
            }}
            label {{
                display: block;
                margin-bottom: 5px;
                font-weight: 600;
                color: #333;
            }}
            input[type="text"], select {{
                width: 100%;
                padding: 10px;
                border: 2px solid #ddd;
                border-radius: 6px;
                font-size: 14px;
            }}
            input[type="text"]:focus, select:focus {{
                outline: none;
                border-color: #667eea;
            }}
            .checkbox-group {{
                display: flex;
                align-items: center;
                gap: 10px;
            }}
            input[type="checkbox"] {{
                width: 20px;
                height: 20px;
            }}
            .btn {{
                padding: 12px 24px;
                border: none;
                border-radius: 6px;
                font-size: 16px;
                font-weight: 600;
                cursor: pointer;
                transition: all 0.3s;
                text-decoration: none;
                display: inline-block;
            }}
            .btn-primary {{
                background: #667eea;
                color: white;
            }}
            .btn-primary:hover {{
                background: #5568d3;
            }}
            .btn-secondary {{
                background: #6c757d;
                color: white;
                margin-left: 10px;
            }}
            .cuenta-search-container {{
                position: relative;
            }}
            .cuenta-search-input {{
                margin-bottom: 10px;
            }}
            .cuenta-select {{
                max-height: 200px;
                overflow-y: auto;
            }}
            .nav-links {{
                margin-bottom: 20px;
            }}
            .nav-links a {{
                color: #667eea;
                text-decoration: none;
                margin-right: 20px;
                font-weight: 600;
            }}
            .nav-links a:hover {{
                text-decoration: underline;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            {render_admin_navigation(current_empleado, "proveedores", subtitle="Edición puntual del catálogo comercial.")}

            <h1>Editar Proveedor/Cliente</h1>

            <form method="POST" action="/admin/proveedores-clientes/update/{proveedor_id}">
                <div class="form-row">
                    <div class="form-group">
                        <label for="tipo">Tipo *</label>
                        <select id="tipo" name="tipo" required>
                            <option value="proveedor" {'selected' if prov.tipo == 'proveedor' else ''}>Proveedor</option>
                            <option value="cliente" {'selected' if prov.tipo == 'cliente' else ''}>Cliente</option>
                            <option value="operadores_regionales" {'selected' if prov.tipo == 'operadores_regionales' else ''}>Operadores Regionales</option>
                            <option value="empleado" {'selected' if prov.tipo == 'empleado' else ''}>Empleado</option>
                            <option value="participante_torneo" {'selected' if prov.tipo == 'participante_torneo' else ''}>Participante de Torneos</option>
                        </select>
                    </div>
                    <div class="form-group">
                        <label for="nombre">Nombre *</label>
                        <input type="text" id="nombre" name="nombre" value="{prov.nombre or ''}" required>
                    </div>
                </div>
                <div class="form-row">
                    <div class="form-group">
                        <div class="checkbox-group">
                            <input type="checkbox" id="es_empleado" name="es_empleado" {'checked' if prov.tipo == 'empleado' else ''}>
                            <label for="es_empleado" style="margin:0;">Es empleado (no proveedor)</label>
                        </div>
                    </div>
                    <div class="form-group" id="empleado_selector_group" {'style="display:none;"' if prov.tipo != 'empleado' else ''}>
                        <label for="empleado_id">Empleado activo *</label>
                        <select id="empleado_id" name="empleado_id">
                            <option value="">Seleccionar empleado...</option>
                            {empleado_options}
                        </select>
                    </div>
                </div>
                <div class="form-row">
                    <div class="form-group">
                        <label for="rfc">RFC</label>
                        <input type="text" id="rfc" name="rfc" value="{prov.rfc or ''}" placeholder="RFC (opcional)">
                    </div>
                    <div class="form-group">
                        <label for="banco">Banco</label>
                        <input type="text" id="banco" name="banco" value="{prov.banco or ''}" placeholder="Nombre del banco (opcional)">
                    </div>
                </div>
                <div class="form-row">
                    <div class="form-group">
                        <label for="cuenta_clabe">Cuenta CLABE</label>
                        <input type="text" id="cuenta_clabe" name="cuenta_clabe" value="{prov.cuenta_clabe or ''}" placeholder="18 dígitos (opcional)" maxlength="18">
                        <small style="color: #666; display: block; margin-top: 5px;">Debe tener exactamente 18 dígitos numéricos</small>
                    </div>
                    <div class="form-group">
                        <label for="cuenta_bancaria">Cuenta Bancaria</label>
                        <input type="text" id="cuenta_bancaria" name="cuenta_bancaria" value="{prov.cuenta_bancaria or ''}" placeholder="Número de cuenta bancaria (opcional)">
                        <small style="color: #666; display: block; margin-top: 5px;">Número de cuenta bancaria para transferencias (no confundir con cuenta contable)</small>
                    </div>
                </div>
                <div class="form-row">
                    <div class="form-group">
                        <label for="entidad_region">Entidad/Región</label>
                        <input type="text" id="entidad_region" name="entidad_region" value="{prov.entidad_region or ''}" placeholder="Ej. CDMX, Norte (opcional)">
                    </div>
                    <div class="form-group"></div>
                </div>
                <div class="form-group">
                    <div class="checkbox-group">
                        <input type="checkbox" id="activo" name="activo" {'checked' if prov.activo else ''}>
                        <label for="activo" style="margin: 0;">Activo</label>
                    </div>
                </div>
                <button type="submit" class="btn btn-primary">Guardar Cambios</button>
                <a href="/admin/proveedores-clientes" class="btn btn-secondary">Cancelar</a>
            </form>
        </div>
        <script>
        (() => {{
            const tipo = document.getElementById('tipo');
            const checkbox = document.getElementById('es_empleado');
            const group = document.getElementById('empleado_selector_group');
            const employee = document.getElementById('empleado_id');
            const name = document.getElementById('nombre');
            const sync = (fromCheckbox = false) => {{
                if (fromCheckbox && checkbox.checked) tipo.value = 'empleado';
                if (tipo.value !== 'empleado' && !fromCheckbox) checkbox.checked = false;
                if (tipo.value === 'empleado') checkbox.checked = true;
                const enabled = checkbox.checked;
                group.style.display = enabled ? 'block' : 'none';
                employee.required = enabled;
            }};
            checkbox.addEventListener('change', () => sync(true));
            tipo.addEventListener('change', () => sync(false));
            employee.addEventListener('change', () => {{
                if (employee.selectedIndex > 0) {{
                    name.value = employee.options[employee.selectedIndex].text;
                }}
            }});
            sync(false);
        }})();
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)


@router.post("/admin/proveedores-clientes/update/{proveedor_id}")
async def update_proveedor_cliente(
    proveedor_id: UUIDType,
    request: Request,
    tipo: str = Form(...),
    nombre: str = Form(...),
    rfc: Optional[str] = Form(None),
    banco: Optional[str] = Form(None),
    cuenta_clabe: Optional[str] = Form(None),
    cuenta_bancaria: Optional[str] = Form(None),
    entidad_region: Optional[str] = Form(None),
    empleado_id: Optional[str] = Form(None),
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = Depends(get_current_empleado),
):
    """Update a supplier/client."""
    result = await session.execute(
        select(ProveedorCliente).where(ProveedorCliente.id == proveedor_id)
    )
    prov = result.scalar_one_or_none()

    if not prov:
        raise HTTPException(status_code=404, detail="Proveedor/Cliente no encontrado")

    try:
        form_data = await request.form()
        activo = form_data.get("activo") == "on"
        es_empleado = form_data.get("es_empleado") == "on" or tipo == "empleado"

        # Validations (same as create)
        nombre = nombre.strip()
        if not nombre and not es_empleado:
            return HTMLResponse(
                content="""
                <!DOCTYPE html>
                <html>
                <head>
                    <meta http-equiv="refresh" content="3;url=/admin/proveedores-clientes?error_msg=El nombre es requerido">
                </head>
                <body>Error: El nombre es requerido. Redirigiendo...</body>
                </html>
                """,
                status_code=400,
            )

        if tipo not in ["proveedor", "cliente", "operadores_regionales", "empleado", "participante_torneo"]:
            return HTMLResponse(
                content="""
                <!DOCTYPE html>
                <html>
                <head>
                    <meta http-equiv="refresh" content="3;url=/admin/proveedores-clientes?error_msg=Tipo inválido">
                </head>
                <body>Error: Tipo inválido. Redirigiendo...</body>
                </html>
                """,
                status_code=400,
            )

        empleado_uuid = None
        if es_empleado:
            tipo = "empleado"
            try:
                empleado_uuid = UUIDType((empleado_id or "").strip())
            except (TypeError, ValueError):
                return RedirectResponse(
                    url=(
                        "/admin/proveedores-clientes?error_msg="
                        + quote("Seleccione un empleado activo válido.")
                    ),
                    status_code=303,
                )
            empleado_result = await session.execute(
                select(Empleado).where(
                    Empleado.id == empleado_uuid,
                    Empleado.activo.is_(True),
                )
            )
            linked_employee = empleado_result.scalar_one_or_none()
            if linked_employee is None:
                return RedirectResponse(
                    url=(
                        "/admin/proveedores-clientes?error_msg="
                        + quote("El empleado seleccionado no existe o está inactivo.")
                    ),
                    status_code=303,
                )
            nombre = linked_employee.nombre
            rfc = None

        # Validate cuenta_clabe if provided
        if cuenta_clabe:
            cuenta_clabe = cuenta_clabe.strip()
            if cuenta_clabe and (len(cuenta_clabe) != 18 or not cuenta_clabe.isdigit()):
                return HTMLResponse(
                    content="""
                    <!DOCTYPE html>
                    <html>
                    <head>
                        <meta http-equiv="refresh" content="3;url=/admin/proveedores-clientes?error_msg=La cuenta CLABE debe tener exactamente 18 dígitos numéricos">
                    </head>
                    <body>Error: La cuenta CLABE debe tener exactamente 18 dígitos numéricos. Redirigiendo...</body>
                    </html>
                    """,
                    status_code=400,
                )

        # Process cuenta_bancaria - store as plain text (no FK, no auto-creation)
        cuenta_bancaria_val = cuenta_bancaria.strip() if cuenta_bancaria else None
        entidad_region_val = entidad_region.strip() if entidad_region else None

        # Update fields
        prov.tipo = tipo
        prov.nombre = nombre
        prov.rfc = rfc.strip() if rfc else None
        prov.banco = banco.strip() if banco else None
        prov.cuenta_clabe = cuenta_clabe if cuenta_clabe else None
        prov.cuenta_bancaria = cuenta_bancaria_val
        prov.entidad_region = entidad_region_val
        prov.empleado_id = empleado_uuid
        prov.activo = activo

        await session.commit()

        return HTMLResponse(
            content="""
            <!DOCTYPE html>
            <html>
            <head>
                <meta http-equiv="refresh" content="2;url=/admin/proveedores-clientes?success_msg=Proveedor/Cliente actualizado exitosamente">
                <title>Éxito</title>
            </head>
            <body style="font-family: sans-serif; padding: 20px; text-align: center;">
                <h2 style="color: green;">✅ Proveedor/Cliente actualizado exitosamente</h2>
                <p>Redirigiendo...</p>
            </body>
            </html>
            """
        )
    except Exception as e:
        await session.rollback()
        error_msg = str(e)
        if "unique constraint" in error_msg.lower() or "duplicate" in error_msg.lower():
            error_msg = "Ya existe un registro con esa información y no se pudo guardar."
        else:
            logger.exception(
                "Unexpected error updating proveedor/cliente",
                extra={
                    "proveedor_id": str(proveedor_id),
                    "tipo": tipo,
                    "nombre": nombre.strip(),
                    "actor_id": str(getattr(current_empleado, "id", "")),
                },
            )
            error_msg = _CATALOG_GENERIC_SAVE_ERROR
        return HTMLResponse(
            content=f"""
            <!DOCTYPE html>
            <html>
            <head>
                <meta http-equiv="refresh" content="3;url=/admin/proveedores-clientes?error_msg={quote(error_msg)}">
            </head>
            <body>Error: {escape(error_msg)}. Redirigiendo...</body>
            </html>
            """,
            status_code=400,
        )


# ============================================================================
# Bulk CSV Upload for Proveedores/Clientes
# ============================================================================


@router.get("/admin/proveedores-clientes/carga-masiva", response_class=HTMLResponse)
async def carga_masiva_proveedores_clientes_form(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = Depends(get_current_empleado),
):
    """Bulk upload form for proveedores/clientes (CSV)."""
    from html import escape

    success_msg = request.query_params.get("success_msg", "")
    error_msg = request.query_params.get("error_msg", "")

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Carga Masiva de Proveedores/Clientes/Empleados - Copa Telmex</title>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                padding: 20px;
                min-height: 100vh;
            }}
            .container {{
                max-width: 1000px;
                margin: 0 auto;
                background: white;
                border-radius: 12px;
                box-shadow: 0 10px 40px rgba(0,0,0,0.2);
                padding: 30px;
            }}
            h1 {{
                color: #333;
                margin-bottom: 10px;
                border-bottom: 3px solid #667eea;
                padding-bottom: 10px;
            }}
            .subtitle {{
                color: #666;
                margin-bottom: 30px;
            }}
            .alert {{
                padding: 15px 20px;
                border-radius: 8px;
                margin-bottom: 20px;
                font-weight: 500;
            }}
            .alert-success {{
                background: #d4edda;
                color: #155724;
                border: 1px solid #c3e6cb;
            }}
            .alert-error {{
                background: #f8d7da;
                color: #721c24;
                border: 1px solid #f5c6cb;
            }}
            .instructions {{
                background: #f8f9fa;
                padding: 20px;
                border-radius: 8px;
                margin-bottom: 30px;
                border-left: 4px solid #667eea;
            }}
            .instructions h2 {{
                color: #333;
                margin-bottom: 15px;
                font-size: 18px;
            }}
            .instructions ul {{
                margin-left: 20px;
            }}
            .instructions li {{
                margin-bottom: 8px;
                color: #555;
            }}
            .code {{
                background: #e9ecef;
                padding: 2px 6px;
                border-radius: 3px;
                font-family: monospace;
                font-size: 13px;
            }}
            .form-group {{
                margin-bottom: 20px;
            }}
            label {{
                display: block;
                margin-bottom: 8px;
                font-weight: 600;
                color: #333;
            }}
            input[type="file"] {{
                width: 100%;
                padding: 12px;
                border: 2px dashed #ddd;
                border-radius: 6px;
                background: #f8f9fa;
                cursor: pointer;
                font-size: 14px;
            }}
            input[type="file"]:hover {{
                border-color: #667eea;
                background: #fff;
            }}
            .btn {{
                padding: 12px 24px;
                border: none;
                border-radius: 6px;
                font-size: 16px;
                font-weight: 600;
                cursor: pointer;
                transition: all 0.3s;
                text-decoration: none;
                display: inline-block;
                margin-right: 10px;
            }}
            .btn-primary {{
                background: #667eea;
                color: white;
            }}
            .btn-primary:hover {{
                background: #5568d3;
                transform: translateY(-2px);
                box-shadow: 0 4px 12px rgba(102, 126, 234, 0.4);
            }}
            .btn-secondary {{
                background: #6c757d;
                color: white;
            }}
            .btn-secondary:hover {{
                background: #5a6268;
            }}
            table {{
                width: 100%;
                border-collapse: collapse;
                margin-top: 15px;
                background: white;
                border-radius: 4px;
                overflow: hidden;
            }}
            th, td {{
                padding: 10px;
                text-align: left;
                border-bottom: 1px solid #ddd;
            }}
            th {{
                background-color: #667eea;
                color: white;
                font-weight: 600;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            {_CONFIG_PANEL_BACK_LINK_HTML}
            <h1>📥 Carga Masiva de Proveedores/Clientes/Empleados</h1>
            <p class="subtitle">Importar proveedores, clientes, operadores regionales, empleados y participantes de torneos desde archivo CSV o XLSX tipo RFC</p>

            {f'<div class="alert alert-success">✅ {escape(success_msg)}</div>' if success_msg else ''}
            {f'<div class="alert alert-error">❌ {escape(error_msg)}</div>' if error_msg else ''}

            <div class="instructions">
                <h2>📋 Instrucciones</h2>
                <ul>
                    <li>El archivo puede ser un CSV genérico o un XLSX tipo RFC del cliente.</li>
                    <li>Para CSV, debe contener las siguientes columnas (no sensible a mayúsculas/minúsculas):</li>
                    <li>
                        <table>
                            <tr>
                                <th>Columna</th>
                                <th>Requerida</th>
                                <th>Descripción</th>
                                <th>Valores Ejemplo</th>
                            </tr>
                            <tr>
                                <td><span class="code">tipo</span></td>
                                <td>✅ Sí</td>
                                <td>Tipo de registro</td>
                                <td>proveedor, cliente, operadores_regionales, empleado, participante_torneo</td>
                            </tr>
                            <tr>
                                <td><span class="code">nombre</span></td>
                                <td>✅ Sí</td>
                                <td>Nombre del proveedor/cliente/empleado</td>
                                <td>Acme Corporation S.A. de C.V.</td>
                            </tr>
                            <tr>
                                <td><span class="code">rfc</span></td>
                                <td>❌ No</td>
                                <td>RFC (opcional)</td>
                                <td>ACM123456789</td>
                            </tr>
                            <tr>
                                <td><span class="code">banco</span></td>
                                <td>❌ No</td>
                                <td>Nombre del banco</td>
                                <td>Banco Nacional de México</td>
                            </tr>
                            <tr>
                                <td><span class="code">cuenta_clabe</span></td>
                                <td>❌ No</td>
                                <td>Cuenta CLABE (18 dígitos si se proporciona)</td>
                                <td>012345678901234567</td>
                            </tr>
                            <tr>
                                <td><span class="code">cuenta_bancaria</span></td>
                                <td>❌ No</td>
                                <td>Número de cuenta bancaria (no confundir con cuenta contable)</td>
                                <td>0192630164, 0027889954</td>
                            </tr>
                            <tr>
                                <td><span class="code">entidad_region</span></td>
                                <td>❌ No</td>
                                <td>Entidad o región asociada (opcional)</td>
                                <td>CDMX, Norte, etc.</td>
                            </tr>
                            <tr>
                                <td><span class="code">activo</span></td>
                                <td>❌ No</td>
                                <td>Estado activo/inactivo (por defecto: true)</td>
                                <td>true, false, 1, 0, si, no</td>
                            </tr>
                        </table>
                    </li>
                    <li><strong>Comportamiento de importación (UPSERT):</strong>
                        <ul>
                            <li>Si existe coincidencia (por RFC, o por nombre + CLABE/cuenta si aplica): se actualizan los campos proporcionados</li>
                            <li>Si NO existe coincidencia: se crea un nuevo registro</li>
                            <li>Las filas duplicadas dentro del mismo archivo se deduplican automáticamente</li>
                            <li>Las filas con <span class="code">tipo</span> o <span class="code">nombre</span> vacíos se omiten</li>
                            <li>Las filas con <span class="code">cuenta_clabe</span> que no tenga exactamente 18 dígitos se omiten</li>
                            <li>La <span class="code">cuenta_bancaria</span> se almacena como texto tal cual (no es cuenta contable)</li>
                            <li>La columna <span class="code">entidad_region</span> es opcional; puede dejarse vacía</li>
                        </ul>
                    </li>
                    <li>Para CSV: codificación UTF-8 (preferido) o Latin-1</li>
                    <li>Para XLSX RFC: se detectan columnas tipo <span class="code">BENEFICIARIO</span>, <span class="code">BANCOS</span>, <span class="code">CUENTA</span>, <span class="code">CLABE</span></li>
                    <li>Se ignoran filas vacías</li>
                </ul>

                <div style="margin-top: 20px; padding: 15px; background: #e7f3ff; border: 1px solid #b3d9ff; border-radius: 6px;">
                    <a href="/admin/proveedores-clientes/plantilla.csv" class="btn btn-primary" style="text-decoration: none; display: inline-block; margin-bottom: 10px;">⬇️ Descargar plantilla CSV</a>
                    <p style="margin: 0; color: #555; font-size: 14px;">Usa esta plantilla para evitar errores de columnas.</p>
                </div>

                <div style="margin-top: 20px; padding: 15px; background: #fff3cd; border: 1px solid #ffeaa7; border-radius: 6px;">
                    <h3 style="margin-top: 0; color: #856404;">⚠️ Reglas de Validación</h3>
                    <ul>
                        <li><strong>tipo</strong>: Debe ser "proveedor", "cliente", "operadores_regionales" (Operadores Regionales) o "empleado"</li>
                        <li><strong>nombre</strong>: No puede estar vacío</li>
                        <li><strong>cuenta_clabe</strong>: Si se proporciona, debe tener exactamente 18 dígitos numéricos</li>
                        <li><strong>cuenta_bancaria</strong>: Se almacena como texto tal cual (número de cuenta bancaria para transferencias)</li>
                        <li><strong>activo</strong>: Por defecto es "true" si no se especifica</li>
                    </ul>
                </div>

                <div style="margin-top: 20px; padding: 15px; background: #d4edda; border: 1px solid #c3e6cb; border-radius: 6px;">
                    <h3 style="margin-top: 0; color: #155724;">ℹ️ Nota Importante</h3>
                    <p style="margin: 0; color: #155724;">
                        <strong>cuenta_bancaria</strong> almacena información bancaria (dónde se envía el dinero).<br>
                        <strong>NO es lo mismo</strong> que cuenta contable (cómo se clasifica …25781 tokens truncated…_sat_redirect_url(error_msg=str(exc)),
            status_code=303,
        )
    except Exception:
        await session.rollback()
        logger.exception(
            "Unexpected error enqueueing SAT process job",
            extra={
                "solicitud_id": (solicitud_id or "").strip(),
                "expense_id": (expense_id or "").strip(),
                "actor_id": str(getattr(current_empleado, "id", "")),
            },
        )
        return RedirectResponse(
            url=_sat_redirect_url(error_msg=_SAT_GENERIC_ERROR),
            status_code=303,
        )



@router.post("/admin/gastos/cfdis/{cfdi_id}/project-assignment")
async def assign_cfdi_operational_project(
    cfdi_id: UUIDType,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = require_admin_finanzas(),
    project_scope: str = Form(""),
    phase: str = Form(""),
    clear_assignment: str = Form(""),
):
    """Assign or remove a manual operational project for a loose CFDI."""
    await _ensure_cfdi_project_assignment_schema(session)
    cfdi = await session.get(CFDIReport, cfdi_id)
    if cfdi is None:
        raise HTTPException(status_code=404, detail="CFDI no encontrado")

    actor_id = str(getattr(current_empleado, "id", "") or "") or None
    if (clear_assignment or "").strip():
        await session.execute(
            text(
                """
                UPDATE cfdi_project_assignments
                SET removed_at = NOW(), removed_by_empleado_id = :actor_id, updated_at = NOW()
                WHERE cfdi_report_id = :cfdi_id AND removed_at IS NULL
                """
            ),
            {"cfdi_id": str(cfdi_id), "actor_id": actor_id},
        )
        await session.commit()
        return RedirectResponse(url="/admin/gastos/cfdis/matching?view=sin_gasto", status_code=303)

    raw_scope = (project_scope or "").strip()
    raw_phase = (phase or "").strip()[:200]
    tournament_id = None
    proyecto_otro = None
    if raw_scope.startswith("tournament:"):
        try:
            tournament_id = str(UUIDType(raw_scope.split(":", 1)[1]))
        except (TypeError, ValueError):
            tournament_id = None
    elif raw_scope.startswith("otro:"):
        proyecto_otro = raw_scope.split(":", 1)[1].strip()[:300] or None

    if not tournament_id and not proyecto_otro:
        await session.execute(
            text(
                """
                UPDATE cfdi_project_assignments
                SET removed_at = NOW(), removed_by_empleado_id = :actor_id, updated_at = NOW()
                WHERE cfdi_report_id = :cfdi_id AND removed_at IS NULL
                """
            ),
            {"cfdi_id": str(cfdi_id), "actor_id": actor_id},
        )
        await session.commit()
        return RedirectResponse(url="/admin/gastos/cfdis/matching?view=sin_gasto", status_code=303)

    await session.execute(
        text(
            """
            UPDATE cfdi_project_assignments
            SET removed_at = NOW(), removed_by_empleado_id = :actor_id, updated_at = NOW()
            WHERE cfdi_report_id = :cfdi_id AND removed_at IS NULL
            """
        ),
        {"cfdi_id": str(cfdi_id), "actor_id": actor_id},
    )
    await session.execute(
        text(
            """
            INSERT INTO cfdi_project_assignments (
                id, cfdi_report_id, tournament_id, proyecto_otro, phase,
                assigned_by_empleado_id, created_at, updated_at
            ) VALUES (
                :id, :cfdi_id, :tournament_id, :proyecto_otro, :phase,
                :actor_id, NOW(), NOW()
            )
            """
        ),
        {
            "id": str(uuid4()),
            "cfdi_id": str(cfdi_id),
            "tournament_id": tournament_id,
            "proyecto_otro": proyecto_otro,
            "phase": raw_phase or None,
            "actor_id": actor_id,
        },
    )
    await session.commit()
    return RedirectResponse(url="/admin/gastos/cfdis/matching?view=sin_gasto", status_code=303)


@router.get("/admin/gastos/cfdis/matching", response_class=HTMLResponse)
async def cfdi_matching_control_room(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = require_admin_finanzas(),
    view: Optional[str] = Query(None),  # 'pendiente', 'vinculado', 'sin_gasto'
    tipo: Optional[str] = Query(None),  # Backcompat: CFDI TipoDeComprobante I/E/P/N/T
    flujo: Optional[str] = Query(None),  # Plataforma perspective: ingresos, egresos, pagos, nomina, etc.
):
    """
    Operator control room for CFDI ↔ expense matching.

    Per LEAP_SPEC_2, this page shows:
    1. Gastos con CFDI pendiente (cfdi_uuid_manual IS NOT NULL, cfdi_report_id IS NULL)
    2. Gastos con CFDI vinculado (cfdi_report_id IS NOT NULL)
    3. CFDIs sin gasto vinculado (cfdi_reports not referenced by any expense)

    This is the "digital stapling" verification dashboard for finance operators.
    """
    try:
        active_view = (view or "").strip().lower()
        if active_view not in {"pendiente", "vinculado", "sin_gasto"}:
            active_view = ""
        cfdi_type_labels = {
            "I": "Factura",
            "E": "Nota de credito",
            "P": "Complemento de pago",
            "N": "Nomina",
            "T": "Traslado",
        }
        flow_filter_labels = {
            "ingresos": "Ingresos / facturas emitidas",
            "egresos": "Egresos / facturas recibidas",
            "notas_emitidas": "Notas de credito emitidas",
            "notas_recibidas": "Notas de credito recibidas",
            "pagos": "Complementos de pago",
            "nomina": "Nomina",
            "traslado": "Traslado",
        }
        active_flujo = (flujo or "").strip().lower()
        if active_flujo not in flow_filter_labels:
            active_flujo = ""
        active_tipo = (tipo or "").strip().upper()
        if active_tipo not in cfdi_type_labels:
            active_tipo = ""
        # Backcompat with links like ?tipo=P. Business filters use flujo=...
        if active_tipo and not active_flujo:
            active_flujo = {
                "P": "pagos",
                "N": "nomina",
                "T": "traslado",
            }.get(active_tipo, "")
        active_tipo = ""

        def matching_url(view_value: str = "", flujo_value: str = active_flujo) -> str:
            params = []
            if view_value:
                params.append(f"view={quote(view_value)}")
            if flujo_value:
                params.append(f"flujo={quote(flujo_value)}")
            return "/admin/gastos/cfdis/matching" + ("?" + "&".join(params) if params else "")

        def _platform_rfc_conditions(column):
            if not platform_rfcs:
                return [text("1=0")]
            return [func.upper(column).in_(platform_rfcs)]

        def _cfdi_flow_conditions():
            if not active_flujo:
                return []
            if active_flujo == "ingresos":
                return [CFDIReport.tipo_de_comprobante == "I", *_platform_rfc_conditions(CFDIReport.emisor_rfc)]
            if active_flujo == "egresos":
                return [CFDIReport.tipo_de_comprobante == "I", *_platform_rfc_conditions(CFDIReport.receptor_rfc)]
            if active_flujo == "notas_emitidas":
                return [CFDIReport.tipo_de_comprobante == "E", *_platform_rfc_conditions(CFDIReport.emisor_rfc)]
            if active_flujo == "notas_recibidas":
                return [CFDIReport.tipo_de_comprobante == "E", *_platform_rfc_conditions(CFDIReport.receptor_rfc)]
            if active_flujo == "pagos":
                return [CFDIReport.tipo_de_comprobante == "P"]
            if active_flujo == "nomina":
                return [CFDIReport.tipo_de_comprobante == "N"]
            if active_flujo == "traslado":
                return [CFDIReport.tipo_de_comprobante == "T"]
            return []

        show_pending = active_view in {"", "pendiente"} and not active_flujo
        show_linked = active_view in {"", "vinculado"}
        show_unlinked = active_view in {"", "sin_gasto"}

        with session.no_autoflush:
            rfc_result = await session.execute(
                select(RFCConfig.tax_id).where(RFCConfig.active.is_(True))
            )
            platform_rfcs = [
                str(row[0]).strip().upper()
                for row in rfc_result.all()
                if row and row[0] and str(row[0]).strip()
            ]

            await _ensure_cfdi_project_assignment_schema(session)
            tournaments_result = await session.execute(select(Tournament).order_by(Tournament.name.asc()))
            matching_tournaments = tournaments_result.scalars().all()
            tournament_map = {
                str(tournament.id).lower(): tournament.name
                for tournament in matching_tournaments
            }
            other_projects_result = await session.execute(
                select(Documento.proyecto_otro)
                .where(Documento.proyecto_otro.isnot(None), Documento.proyecto_otro != "")
                .distinct()
                .order_by(Documento.proyecto_otro.asc())
            )
            matching_other_projects = [
                str(row[0]).strip()
                for row in other_projects_result.all()
                if row and str(row[0] or "").strip()
            ]

            # 1. Gastos con CFDI pendiente (UUID captured but not yet linked)
            pending_query = (
                select(ExpenseReport)
                .options(selectinload(ExpenseReport.empleado))
                .where(
                    and_(
                        ExpenseReport.cfdi_uuid_manual.isnot(None),
                        ExpenseReport.cfdi_report_id.is_(None),
                        ExpenseReport.estado_gasto == "activo",
                    )
                )
                .order_by(ExpenseReport.created_at.desc())
                .limit(500)
            )
            pending_result = await session.execute(pending_query)
            pending_expenses = pending_result.scalars().all()

            # 2. Gastos con CFDI vinculado
            linked_conditions = [
                ExpenseReport.cfdi_report_id.isnot(None),
                ExpenseReport.estado_gasto == "activo",
            ]
            linked_select = select(ExpenseReport)
            flow_conditions = _cfdi_flow_conditions()
            if flow_conditions:
                linked_select = linked_select.join(
                    CFDIReport, ExpenseReport.cfdi_report_id == CFDIReport.id
                )
                linked_conditions.extend(flow_conditions)
            linked_query = (
                linked_select
                .options(selectinload(ExpenseReport.empleado))
                .options(selectinload(ExpenseReport.cfdi_report))
                .where(and_(*linked_conditions))
                .order_by(ExpenseReport.created_at.desc())
                .limit(500)
            )
            linked_result = await session.execute(linked_query)
            linked_expenses = linked_result.scalars().all()

            # 3. CFDIs sin gasto vinculado
            # Subquery to get all cfdi_report_ids that ARE linked to expenses
            linked_cfdi_ids_subquery = (
                select(ExpenseReport.cfdi_report_id)
                .where(ExpenseReport.cfdi_report_id.isnot(None))
                .distinct()
            )

            # Get CFDIs not in that list
            unlinked_conditions = [~CFDIReport.id.in_(linked_cfdi_ids_subquery)]
            unlinked_conditions.extend(_cfdi_flow_conditions())
            unlinked_cfdis_query = (
                select(CFDIReport)
                .where(and_(*unlinked_conditions))
                .order_by(CFDIReport.created_at.desc())
                .limit(500)
            )
            unlinked_result = await session.execute(unlinked_cfdis_query)
            unlinked_cfdis = unlinked_result.scalars().all()

            assignment_by_cfdi: dict[str, dict[str, Any]] = {}
            unlinked_cfdi_ids = [str(cfdi.id) for cfdi in unlinked_cfdis]
            if unlinked_cfdi_ids:
                assignment_result = await session.execute(
                    text(
                        """
                        SELECT a.cfdi_report_id, a.tournament_id, a.proyecto_otro, a.phase, a.note,
                               a.created_at, t.name AS tournament_name
                        FROM cfdi_project_assignments a
                        LEFT JOIN tournaments t ON t.id = a.tournament_id
                        WHERE a.removed_at IS NULL AND a.cfdi_report_id::text = ANY(:cfdi_ids)
                        """
                    ),
                    {"cfdi_ids": unlinked_cfdi_ids},
                )
                assignment_by_cfdi = {
                    str(row.cfdi_report_id): {
                        "tournament_id": str(row.tournament_id) if row.tournament_id else "",
                        "tournament_name": row.tournament_name or "",
                        "proyecto_otro": row.proyecto_otro or "",
                        "phase": row.phase or "",
                        "note": row.note or "",
                    }
                    for row in assignment_result
                }

            # Get counts for summary
            pending_count = len(pending_expenses)
            linked_count = len(linked_expenses)
            unlinked_cfdi_count = len(unlinked_cfdis)

        # Build pending expenses table rows
        pending_rows = ""
        for expense in pending_expenses:
            empleado_name = expense.empleado.nombre if expense.empleado else "N/A"
            fecha_str = expense.fecha.strftime("%Y-%m-%d") if expense.fecha else "-"
            project_name = resolve_project_name(expense.proyecto or "", tournament_map)
            expense_origin = format_value(
                getattr(expense, "origen", None) or "Captura directa"
            )
            ar_status = evaluate_ar_status(expense)
            match_status = evaluate_three_way_match(expense)
            pending_rows += f"""
            <tr>
                <td>{format_value(expense.numero_referencia)}</td>
                <td>{fecha_str}</td>
                <td>{format_value(empleado_name)}</td>
                <td>{format_value(project_name)}<br><small>{expense_origin}</small></td>
                <td>{format_value(expense.concepto)}</td>
                <td>${expense.gasto_cantidad:,.2f}</td>
                <td><code style="font-size: 11px; background: #fff3cd; padding: 2px 4px; border-radius: 3px;">{expense.cfdi_uuid_manual}</code></td>
                <td>{format_value(ar_status.status)}</td>
                <td>{format_value(match_status.status)}</td>
                <td>
                    <a href="/gastos/{expense.id}" target="_blank" class="action-btn view">👁️ Ver</a>
                </td>
            </tr>
            """

        # Build linked expenses table rows
        linked_rows = ""
        for expense in linked_expenses:
            empleado_name = expense.empleado.nombre if expense.empleado else "N/A"
            fecha_str = expense.fecha.strftime("%Y-%m-%d") if expense.fecha else "-"
            project_name = resolve_project_name(expense.proyecto or "", tournament_map)
            expense_origin = format_value(
                getattr(expense, "origen", None) or "Captura directa"
            )
            cfdi = expense.cfdi_report
            cfdi_uuid = cfdi.cfdi_uuid if cfdi else "-"
            cfdi_tipo = cfdi_type_labels.get(cfdi.tipo_de_comprobante, cfdi.tipo_de_comprobante or "-") if cfdi else "-"
            cfdi_total = f"${cfdi.total:,.2f}" if cfdi and cfdi.total is not None else "-"
            ar_status = evaluate_ar_status(expense, cfdi=cfdi)
            match_status = evaluate_three_way_match(expense, cfdi=cfdi)
            match_title = (
                f' title="{escape(" | ".join(match_status.exceptions))}"'
                if match_status.exceptions
                else ""
            )
            linked_rows += f"""
            <tr>
                <td>{format_value(expense.numero_referencia)}</td>
                <td>{fecha_str}</td>
                <td>{format_value(empleado_name)}</td>
                <td>{format_value(project_name)}<br><small>{expense_origin}</small></td>
                <td>{format_value(expense.concepto)}</td>
                <td>${expense.gasto_cantidad:,.2f}</td>
                <td><code style="font-size: 11px; background: #d4edda; padding: 2px 4px; border-radius: 3px;">{cfdi_uuid}</code></td>
                <td>{format_value(cfdi_tipo)}</td>
                <td>{cfdi_total}</td>
                <td>{format_value(ar_status.status)}</td>
                <td{match_title}>{format_value(match_status.status)}</td>
                <td>
                    <a href="/gastos/{expense.id}" target="_blank" class="action-btn view">👁️ Ver</a>
                    {f'<form method="POST" action="/admin/gastos/sat/cfdi/{cfdi.id}/status" style="display:inline;"><button type="submit" class="action-btn view">Validar SAT</button></form>' if cfdi else ''}
                </td>
            </tr>
            """

        # Build unlinked CFDIs table rows
        unlinked_rows = ""
        for cfdi in unlinked_cfdis:
            fecha_str = cfdi.fecha.strftime("%Y-%m-%d") if cfdi.fecha else "-"
            origen_badge = {
                "csv": '<span style="color: #2196F3;">CSV</span>',
                "tocino": '<span style="color: #4CAF50;">Tocino</span>',
                "user_upload": '<span style="color: #7E57C2;">XML usuario</span>',
            }.get(
                cfdi.origen,
                f'<span style="color: #607D8B;">{format_value(cfdi.origen)}</span>',
            )
            cfdi_tipo = cfdi_type_labels.get(cfdi.tipo_de_comprobante, cfdi.tipo_de_comprobante or "-")
            cfdi_total = f"${cfdi.total:,.2f}" if cfdi.total is not None else "-"
            assignment = assignment_by_cfdi.get(str(cfdi.id), {})
            assignment_scope_value = (
                f"tournament:{assignment.get('tournament_id')}"
                if assignment.get("tournament_id")
                else (f"otro:{assignment.get('proyecto_otro')}" if assignment.get("proyecto_otro") else "")
            )
            assignment_label = assignment.get("tournament_name") or assignment.get("proyecto_otro") or "Sin proyecto"
            project_options = '<option value="">— Sin proyecto —</option>' + "".join(
                f'<option value="tournament:{t.id}" {"selected" if assignment_scope_value == f"tournament:{t.id}" else ""}>{escape(str(t.name or ""))}</option>'
                for t in matching_tournaments
            ) + "".join(
                f'<option value="otro:{escape(project, quote=True)}" {"selected" if assignment_scope_value == f"otro:{project}" else ""}>Otro: {escape(project)}</option>'
                for project in matching_other_projects
            )
            assignment_badge = (
                f'<div style="margin-bottom:6px;color:#0f766e;font-weight:800;">Proyecto manual: {escape(assignment_label)}</div>'
                if assignment_scope_value
                else '<div style="margin-bottom:6px;color:#64748b;">Sin proyecto operativo asignado</div>'
            )
            clear_assignment_button = (
                '<button type="submit" name="clear_assignment" value="1" class="action-btn" style="background:#e5e7eb;color:#111827;">Quitar</button>'
                if assignment_scope_value
                else ''
            )
            unlinked_rows += f"""
            <tr>
                <td><code style="font-size: 11px;">{format_value(cfdi.cfdi_uuid)}</code></td>
                <td>{fecha_str}</td>
                <td>{origen_badge}</td>
                <td>{format_value(cfdi.emisor_nombre)[:40] if cfdi.emisor_nombre else '-'}...</td>
                <td>{format_value(cfdi.receptor_nombre)[:40] if cfdi.receptor_nombre else '-'}...</td>
                <td>{format_value(cfdi_tipo)}</td>
                <td>{cfdi_total}</td>
                <td>{format_value(cfdi.serie)}</td>
                <td>{format_value(cfdi.folio)}</td>
                <td>
                    {assignment_badge}
                    <form method="POST" action="/admin/gastos/cfdis/{cfdi.id}/project-assignment" style="display:grid;grid-template-columns:minmax(180px,1fr) 110px auto auto;gap:6px;align-items:center;min-width:520px;">
                        <select name="project_scope" style="padding:6px;border:1px solid #cbd5e1;border-radius:8px;">{project_options}</select>
                        <input type="text" name="phase" value="{escape(assignment.get('phase', ''), quote=True)}" placeholder="Fase" style="padding:6px;border:1px solid #cbd5e1;border-radius:8px;">
                        <button type="submit" class="action-btn view">Asignar</button>
                        {clear_assignment_button}
                    </form>
                    <form method="POST" action="/admin/gastos/sat/cfdi/{cfdi.id}/status" style="display:inline-block;margin-top:6px;">
                        <button type="submit" class="action-btn view">Validar SAT</button>
                    </form>
                </td>
            </tr>
            """

        hero_actions_html = """
            <a href="/admin/gastos/cfdis/carga-masiva" class="button">Importar CFDIs CSV</a>
            <a href="/admin/gastos/sat" class="button secondary">Operación SAT</a>
            <a href="/admin/gastos/expenses" class="button secondary">Ver gastos</a>
            <a href="/admin/gastos/invoices" class="button secondary">Ver facturas</a>
        """
        type_filter_links = "".join(
            f'<a href="{matching_url(active_view, code)}" class="{'active' if active_flujo == code else ''}">{label}</a>'
            for code, label in [
                ("", "Todos"),
                ("ingresos", "Ingresos / facturas emitidas"),
                ("egresos", "Egresos / facturas recibidas"),
                ("notas_emitidas", "Notas emitidas"),
                ("notas_recibidas", "Notas recibidas"),
                ("pagos", "Complementos de pago"),
                ("nomina", "Nomina"),
                ("traslado", "Traslado"),
            ]
        )

        hero_side_html = f"""
            <div class="eyebrow">Cobertura{f' - {flow_filter_labels[active_flujo]}' if active_flujo else ''}</div>
            <div class="meta-grid">
                <div class="meta-card"><span>Pendientes</span><strong>{pending_count}</strong><small>Gastos con UUID manual aún sin match.</small></div>
                <div class="meta-card"><span>Vinculados</span><strong>{linked_count}</strong><small>Gastos ya pegados al CFDI correcto.</small></div>
                <div class="meta-card"><span>CFDI sin gasto</span><strong>{unlinked_cfdi_count}</strong><small>Comprobantes disponibles para investigar.</small></div>
            </div>
        """
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Emparejar CFDIs y Gastos - Copa Telmex</title>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <style>
                {_admin_workspace_styles("1640px", layout="data")}
                .empty-state {{ padding:40px; text-align:center; color:#64748b; }}
                .empty-state .icon {{ font-size:42px; margin-bottom:10px; }}
                .summary-links {{ display:flex; gap:12px; flex-wrap:wrap; }}
                .summary-links a {{
                    text-decoration:none;
                    color:#0f172a;
                    padding:10px 12px;
                    border:1px solid #dbe2ea;
                    border-radius:14px;
                    background:#fff;
                    font-weight:700;
                    font-size:13px;
                }}
                .summary-links a.active {{
                    background:#0f766e;
                    color:#f8fafc;
                    border-color:rgba(15,118,110,.45);
                    box-shadow:0 12px 24px rgba(15,118,110,.18);
                }}
                .flow-list {{
                    margin:0;
                    padding-left:20px;
                    color:#334155;
                    line-height:1.8;
                }}
            </style>
        </head>
        <body>
            <div class="container">
                {render_admin_navigation(current_empleado, "matching", subtitle="Controla el emparejamiento de CFDI y gasto desde la misma consola de finanzas, sin pantallas paralelas.")}
                {_render_admin_workspace_hero(
                    eyebrow="Finanzas",
                    title="Emparejar CFDI y gastos",
                    description="Panel de verificación para pegar evidencia fiscal con gasto operativo y detectar CFDI que siguen sueltos.",
                    actions_html=hero_actions_html,
                    side_html=hero_side_html,
                )}
                <div class="stack">
                <section class="surface">
                    <div class="section-head">
                        <div>
                            <div class="eyebrow">Vistas</div>
                            <h2>Priorizar la revisión</h2>
                            <div class="section-note">Puedes concentrarte en pendientes, vinculados o CFDI sin gasto sin perder el contexto de la consola.</div>
                        </div>
                    </div>
                    <div class="summary-links">
                        <a href="{matching_url('', active_flujo)}" class="{'active' if not active_view else ''}">Todos</a>
                        <a href="{matching_url('pendiente', '')}" class="{'active' if active_view == 'pendiente' else ''}">Pendientes ({pending_count})</a>
                        <a href="{matching_url('vinculado', active_flujo)}" class="{'active' if active_view == 'vinculado' else ''}">Vinculados ({linked_count})</a>
                        <a href="{matching_url('sin_gasto', active_flujo)}" class="{'active' if active_view == 'sin_gasto' else ''}">CFDI sin gasto ({unlinked_cfdi_count})</a>
                    </div>
                    <div class="section-note" style="margin-top:16px;">Filtrar por perspectiva de Plataforma y tipo fiscal.</div>
                    <div class="summary-links" style="margin-top:10px;">
                        {type_filter_links}
                    </div>
                </section>
                {f'''
                <section class="surface">
                    <div class="section-head">
                        <div>
                            <div class="eyebrow">Pendientes</div>
                            <h2>Gastos con CFDI pendiente ({pending_count})</h2>
                            <div class="section-note">Gastos activos con UUID manual capturado, pero todavía sin CFDI enlazado. Revisa empleado, proyecto y origen antes de corregir una excepción.</div>
                        </div>
                    </div>
                    <div class="table-shell">
                        {'<div class="empty-state"><div class="icon">✅</div><p>No hay gastos con CFDI pendiente</p></div>' if not pending_expenses else f'''
                        <table>
                            <thead>
                                <tr>
                                    <th>Referencia</th>
                                    <th>Fecha</th>
                                    <th>Empleado</th>
                                    <th>Proyecto / origen</th>
                                    <th>Concepto</th>
                                    <th>Total</th>
                                    <th>UUID CFDI</th>
                                    <th>AR</th>
                                    <th>3-Way Match</th>
                                    <th>Acciones</th>
                                </tr>
                            </thead>
                            <tbody>
                                {pending_rows}
                            </tbody>
                        </table>
                        '''}
                    </div>
                </section>
                ''' if show_pending else ''}
                {f'''
                <section class="surface">
                    <div class="section-head">
                        <div>
                            <div class="eyebrow">Vinculados</div>
                            <h2>Gastos con CFDI vinculado ({linked_count})</h2>
                            <div class="section-note">Gastos ya amarrados a un CFDI válido, listos para revisión fiscal y conciliación posterior.</div>
                        </div>
                    </div>
                    <div class="table-shell">
                        {'<div class="empty-state"><div class="icon">📭</div><p>No hay gastos con CFDI vinculado aún</p></div>' if not linked_expenses else f'''
                        <table>
                            <thead>
                                <tr>
                                    <th>Referencia</th>
                                    <th>Fecha</th>
                                    <th>Empleado</th>
                                    <th>Proyecto / origen</th>
                                    <th>Concepto</th>
                                    <th>Total Gasto</th>
                                    <th>UUID CFDI</th>
                                    <th>Tipo fiscal</th>
                                    <th>Total CFDI</th>
                                    <th>AR</th>
                                    <th>3-Way Match</th>
                                    <th>Acciones</th>
                                </tr>
                            </thead>
                            <tbody>
                                {linked_rows}
                            </tbody>
                        </table>
                        '''}
                    </div>
                </section>
                ''' if show_linked else ''}
                {f'''
                <section class="surface">
                    <div class="section-head">
                        <div>
                            <div class="eyebrow">Sin gasto</div>
                            <h2>CFDI sin gasto vinculado ({unlinked_cfdi_count})</h2>
                            <div class="section-note">Comprobantes cargados por CSV o Tocino que siguen sin un gasto operativo asociado.</div>
                        </div>
                    </div>
                    <div class="table-shell">
                        {'<div class="empty-state"><div class="icon">📭</div><p>No hay CFDIs sin vincular</p></div>' if not unlinked_cfdis else f'''
                        <table>
                            <thead>
                                <tr>
                                    <th>UUID CFDI</th>
                                    <th>Fecha</th>
                                    <th>Origen</th>
                                    <th>Emisor</th>
                                    <th>Receptor</th>
                                    <th>Tipo</th>
                                    <th>Total</th>
                                    <th>Serie</th>
                                    <th>Folio</th>
                                    <th>Proyecto operativo / Acciones</th>
                                </tr>
                            </thead>
                            <tbody>
                                {unlinked_rows}
                            </tbody>
                        </table>
                        '''}
                    </div>
                </section>
                ''' if show_unlinked else ''}
                <section class="surface">
                    <div class="section-head">
                        <div>
                            <div class="eyebrow">Flujo</div>
                            <h2>Secuencia operativa</h2>
                            <div class="section-note">Esta bandeja resuelve la unión entre evidencia fiscal y gasto antes de preparar COI; no crea pólizas ni asigna proyectos automáticamente.</div>
                        </div>
                    </div>
                    <ol class="flow-list">
                        <li><strong>Empleado registra gasto</strong> y proporciona UUID de CFDI (directo o desde QR/link)</li>
                        <li><strong>Finanzas importa CFDIs</strong> desde CSV con columna UUID; la carga alimenta esta bandeja, no descarga comprobantes</li>
                        <li><strong>Sistema vincula automáticamente</strong> gastos con CFDIs por UUID</li>
                        <li><strong>Operador verifica</strong> empleado, proyecto, origen y discrepancias; después prepara la salida COI</li>
                    </ol>
                </section>
                </div>
            </div>
        </body>
        </html>
        """
        return html

    except Exception as e:
        logger.error(f"Error in cfdi_matching_control_room: {e}", exc_info=True)
        return _render_admin_error_page(
            title="Error al cargar matching CFDI",
            message="La consola de emparejamiento no pudo renderizarse. Puedes volver a finanzas o revisar otra bandeja sin perder el shell administrativo.",
            detail=str(e),
            current_empleado=current_empleado,
            return_href="/admin/gastos",
            return_label="Volver a finanzas",
        )


@router.get("/admin/gastos/sin-cuenta-contable", response_class=HTMLResponse)
async def gastos_sin_cuenta_contable(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    period: Optional[str] = Query(None),
    bi_year: Optional[str] = Query(None),
    bi_scope: Optional[str] = Query(None),
    current_empleado: Empleado = require_admin_finanzas(),
) -> str:
    """
    Admin review page for expenses with incomplete accounting configuration.
    Covers missing expense account and/or missing counterpart account.
    """
    import json
    from html import escape

    # Import suggestion service
    from devnous.gastos.services.cuenta_contable_suggester import (
        CuentaContableSuggester,
    )

    bi_year_safe = (bi_year or "").strip()
    bi_year_safe = (
        bi_year_safe if (bi_year_safe.isdigit() and len(bi_year_safe) == 4) else ""
    )
    bi_scope_safe = (bi_scope or "").strip().lower()
    if bi_scope_safe not in {"all", ACTIVE_TOURNAMENT_SCOPE}:
        bi_scope_safe = ""
    # Keep an inherited BI year when the caller has not chosen a month yet.
    selected_period, period_start, period_end = _cleanup_period_bounds(
        period,
        default_year=int(bi_year_safe) if bi_year_safe else None,
    )
    bi_query_suffix = ""
    if bi_year_safe or bi_scope_safe:
        parts = []
        if bi_year_safe:
            parts.append(f"bi_year={bi_year_safe}")
        if bi_scope_safe:
            parts.append(f"bi_scope={bi_scope_safe}")
        bi_query_suffix = f"?{'&'.join(parts)}"
    bi_context_html = (
        f'<div style="margin: 8px 0 14px 0; color:#444; font-size:13px;">'
        f'Contexto BI activo: año={bi_year_safe or "n/a"} | ámbito={bi_scope_safe or "all"}'
        f"</div>"
        if (bi_year_safe or bi_scope_safe)
        else ""
    )
    bi_conditions = []
    _append_bi_expense_filters(
        conditions=bi_conditions,
        bi_year=bi_year_safe or None,
        bi_scope=bi_scope_safe or None,
    )
    bi_conditions.extend(
        [
            ExpenseReport.fecha >= period_start,
            ExpenseReport.fecha < period_end,
        ]
    )
    gastos = await load_cleanup_expenses(session, extra_conditions=bi_conditions)
    cfdi_options = await list_unassigned_cfdi_options(session)

    # Get active cuentas contables
    cuentas_result = await session.execute(
        select(CuentaContable)
        .where(CuentaContable.activo.is_(True))
        .order_by(CuentaContable.codigo)
    )
    cuentas_contables = cuentas_result.scalars().all()
    default_contra_cuenta = resolve_default_cleanup_contra_cuenta(cuentas_contables)

    # Generate suggestions for all expenses (batch operation)
    # NOTE: LLM disabled for batch page to prevent timeouts. LLM calls are slow (10s each)
    # and with many expenses would exceed the 60s nginx timeout. Per LEAP_SPEC_3, LLM is
    # "strictly optional" - the deterministic rules and learned mappings work without it.
    suggestions = {}
    try:
        suggester = CuentaContableSuggester(session)
        expense_data = []
        for gasto in gastos:
            effective_concept = resolve_effective_budget_concept(gasto)
            expense_data.append(
                {
                    "id": gasto.id,
                    "concepto": gasto.concepto or "",
                    "proveedor_cliente_id": None,
                    "metodo_pago": gasto.metodo_pago,
                    "proyecto": gasto.proyecto,
                    "gasto_cantidad": gasto.gasto_cantidad,
                    "tournament_id": None,
                    "origen": getattr(gasto, "origen", None),
                    "fase_torneo": gasto.fase_torneo,
                    "empleado_id": gasto.empleado_id,
                    "ultimos_4_digitos": getattr(gasto, "ultimos_4_digitos", None),
                    "budget_concept_id": (
                        gasto.budget_concept_id
                        or (effective_concept.id if effective_concept else None)
                    ),
                    "has_cfdi": bool(gasto.cfdi_report_id),
                }
            )
        suggestions = await suggester.get_suggestions_batch(
            expenses=expense_data,
            use_llm=False,  # Disabled for batch page to prevent timeouts
            llm_confidence_threshold=0.7,
        )
    except Exception as e:
        logger.warning(f"Failed to generate suggestions: {e}")
        # Continue without suggestions - they are advisory only

    # Prepare cuentas data for JSON
    cuentas_data = []
    for cuenta in cuentas_contables:
        cuentas_data.append(
            {
                "id": str(cuenta.id),
                "codigo": cuenta.codigo,
                "nombre": cuenta.nombre,
                "tipo": cuenta.tipo,
            }
        )
    cuentas_json = json.dumps(cuentas_data)

    # Prepare suggestions data for JSON (for pre-selection)
    suggestions_data = {}
    for gasto_id, suggestion in suggestions.items():
        if suggestion:
            suggestions_data[str(gasto_id)] = {
                "cuenta_id": str(suggestion.cuenta_contable_id),
                "cuenta_codigo": suggestion.cuenta_codigo,
                "cuenta_nombre": suggestion.cuenta_nombre,
                "confidence": suggestion.confidence_score,
                "confidence_label": suggestion.confidence_label,
                "confidence_color": suggestion.confidence_color,
                "reason": suggestion.reason,
                "tier": suggestion.tier,
            }
    suggestions_json = json.dumps(suggestions_data)
    cfdi_options_json = json.dumps(
        [
            {
                "id": str(option.id),
                "label": option.label,
                "uuid": option.uuid,
            }
            for option in cfdi_options
        ]
    )

    # Count suggestions and readiness issues using the same cleanup contract used
    # by the rows/export path. Do not count deterministic counterpart fallbacks as
    # human-pending work, otherwise the module appears not to update after save.
    high_confidence_count = sum(
        1 for s in suggestions.values() if s and s.confidence_score >= 0.8
    )
    cleanup_states = {}
    for gasto in gastos:
        cleanup_states[gasto.id] = await safe_build_cleanup_preview(session, gasto)
    missing_main_count = sum(
        1
        for state in cleanup_states.values()
        if "Falta cuenta de cargo" in (state.get("issues") or [])
    )
    missing_contra_count = sum(
        1
        for state in cleanup_states.values()
        if "Falta contrapartida" in (state.get("issues") or [])
    )
    missing_cfdi_count = sum(
        1
        for state in cleanup_states.values()
        if "Falta CFDI vinculado" in (state.get("issues") or [])
    )

    # Tournament map for proyecto UUID -> name
    tournament_map = {}
    try:
        tournaments_result = await session.execute(select(Tournament))
        tournaments = tournaments_result.scalars().all()
        tournament_map = {str(t.id).lower(): t.name for t in tournaments}
    except Exception:
        pass

    # Build table rows
    rows_html = ""
    tax_visible_count = 0
    for gasto in gastos:
        empleado_nombre = gasto.empleado.nombre if gasto.empleado else "N/A"
        fecha_str = gasto.fecha.strftime("%Y-%m-%d") if gasto.fecha else "N/A"
        document_origin_label, documento_ref = _cleanup_document_origin(gasto)

        # Escape user data (proyecto: show name when UUID, else as-is)
        referencia_safe = escape(gasto.numero_referencia or "N/A")
        empleado_safe = escape(empleado_nombre)
        concepto_safe = escape(gasto.concepto or "N/A")
        proyecto_display = resolve_project_name(gasto.proyecto or "", tournament_map)
        proyecto_safe = escape(proyecto_display if proyecto_display != "-" else "—")
        cuenta_base_safe = escape(gasto.cuenta_contable_base or "—")
        accounting_display = build_cleanup_accounting_display(gasto)
        if accounting_display.partida_name:
            partida_label = escape(accounting_display.partida_name)
            if accounting_display.partida_from_document:
                partida_presupuestal_safe = (
                    f'{partida_label} '
                    f'<span style="font-size:11px;color:#64748b;">(desde documento)</span>'
                )
            else:
                partida_presupuestal_safe = partida_label
        else:
            partida_presupuestal_safe = "—"
        if accounting_display.assigned_cuenta:
            cuenta_asignada_safe = escape(
                f"{accounting_display.assigned_cuenta.codigo} · "
                f"{accounting_display.assigned_cuenta.nombre}"
            )
        elif accounting_display.mapped_cuenta:
            cuenta_asignada_safe = (
                f'{escape(accounting_display.mapped_cuenta.codigo)} · '
                f'{escape(accounting_display.mapped_cuenta.nombre)} '
                f'<span style="font-size:11px;color:#64748b;">(mapeada, sin asignar)</span>'
            )
        else:
            cuenta_asignada_safe = "—"
        metodo_pago_safe = escape(gasto.metodo_pago or "N/A")
        documento_ref_safe = escape(documento_ref)
        document_origin_safe = escape(document_origin_label)

        # Get suggestion for this expense
        suggestion = suggestions.get(gasto.id)
        cleanup_state = await safe_build_cleanup_preview(
            session,
            gasto,
            include_historical_precedent=True,
        )
        preview = cleanup_state["preview"]
        readiness_issues = list(cleanup_state["issues"] or [])
        historical_precedent = cleanup_state.get("historical_precedent_evidence") or {}
        suggestion_html = ""
        historical_precedent_html = ""
        preselect_data = ""
        main_assigned_html = ""
        contra_assigned_html = ""
        cfdi_match_html = ""
        preview_notes = list(preview.get("notes") or [])
        contra_suggestion = preview.get("contra_account") or {}
        taxes = preview.get("taxes") or {}
        iva_account = taxes.get("iva_account") or {}
        retenciones = list(taxes.get("retenciones") or [])
        retenciones_total = float(taxes.get("retenciones_total") or 0.0)
        impuestos_locales = list(taxes.get("impuestos_locales") or [])
        impuestos_locales_total = float(taxes.get("impuestos_locales_total") or 0.0)
        iva_trasladado = float(taxes.get("iva_trasladado") or 0.0)
        no_deducibles = list(taxes.get("gastos_no_deducibles") or [])
        no_deducibles_total = float(taxes.get("gastos_no_deducibles_total") or 0.0)
        base_gasto = float(taxes.get("base_gasto") or 0.0)
        neto_contrapartida = float(taxes.get("neto_contrapartida") or 0.0)

        if iva_trasladado > 0 or retenciones_total > 0 or impuestos_locales_total > 0:
            tax_visible_count += 1

        if gasto.cuenta_contable:
            main_assigned_html = (
                f'<div style="font-size:12px; color:#166534; margin-bottom:6px;">'
                f"Actual: <strong>{escape(gasto.cuenta_contable.codigo)}</strong> - "
                f'{escape(gasto.cuenta_contable.nombre or "")}</div>'
            )
        if gasto.contra_cuenta_contable:
            contra_assigned_html = (
                f'<div style="font-size:12px; color:#1d4ed8; margin-bottom:6px;">'
                f"Actual: <strong>{escape(gasto.contra_cuenta_contable.codigo)}</strong> - "
                f'{escape(gasto.contra_cuenta_contable.nombre or "")}</div>'
            )
        iva_assigned_html = ""
        current_iva_account = getattr(gasto, "cuenta_iva", None)
        current_retention_accounts = (
            getattr(gasto, "retencion_cuentas_json", None) or {}
        )
        if current_iva_account:
            iva_assigned_html = (
                f'<div style="font-size:12px; color:#7c3aed; margin-bottom:6px;">'
                f"Actual: <strong>{escape(current_iva_account.codigo)}</strong> - "
                f'{escape(current_iva_account.nombre or "")}</div>'
            )

        selected_cfdi_id = str(gasto.cfdi_report_id or "")
        selected_cfdi_label = ""
        if gasto.cfdi_report:
            selected_cfdi_label = (
                f"{str(gasto.cfdi_report.cfdi_uuid or gasto.cfdi_report.id)}"
                f" · {gasto.cfdi_report.emisor_nombre or gasto.cfdi_report.emisor_rfc or 'emisor sin dato'}"
                f" · ${float(gasto.cfdi_report.total or 0):,.2f}"
            )
        cfdi_status = (
            "CFDI vinculado"
            if gasto.cfdi_report_id
            else (
                "UUID manual pendiente"
                if (gasto.cfdi_uuid_manual or "").strip()
                else "Falta CFDI"
            )
        )
        cfdi_origen_display = (
            _cfdi_origen_badge(gasto.cfdi_report.origen)
            if gasto.cfdi_report
            else "-"
        )

        cfdi_match_html = f"""
            <div class="cfdi-selector" style="min-width:280px; position:relative;">
                <div style="font-size:12px; font-weight:700; color:#0f172a; margin-bottom:6px;">Vinculación CFDI</div>
                <input type="text"
                       class="cfdi-search"
                       data-gasto-id="{gasto.id}"
                       value="{escape(selected_cfdi_label)}"
                       placeholder="Buscar CFDI por UUID, emisor, total o fecha..."
                       autocomplete="off"
                       style="width:100%; padding:8px; border:1px solid #cbd5e1; border-radius:8px; background:white;">
                <div class="cfdi-results" style="display:none; position:absolute; background:white; border:1px solid #ddd; max-height:240px; overflow-y:auto; z-index:1000; width:100%; box-shadow:0 2px 8px rgba(0,0,0,0.15);"></div>
                <input type="hidden" class="cfdi-report-id" data-gasto-id="{gasto.id}" value="{escape(selected_cfdi_id)}">
                <div style="font-size:11px; color:#64748b; margin-top:5px;">
                    {escape(cfdi_status)}
                    {f" · UUID manual: {escape(gasto.cfdi_uuid_manual)}" if gasto.cfdi_uuid_manual else ""}
                </div>
            </div>
        """

        historical_candidates = list(historical_precedent.get("candidates") or [])[:3]
        if historical_precedent.get("status") == "precedents_found" and historical_candidates:
            candidate_items = "".join(
                f"<li style='margin-bottom:6px;'><strong>{escape(str(item.get('account_code') or ''))}</strong> - "
                f"{escape(str(item.get('account_name') or ''))}"
                f"<div style='font-size:11px;color:#64748b;'>"
                f"{int(item.get('policy_count') or 0)} poliza(s), "
                f"{int(item.get('match_count') or 0)} linea(s); "
                f"confianza {escape(str(item.get('confidence') or 'low'))}</div></li>"
                for item in historical_candidates
            )
            historical_query_safe = escape(str(historical_precedent.get("query") or ""))
            historical_precedent_html = f"""
                <div style="background:#f8fafc; border:1px solid #cbd5e1; border-radius:10px; padding:9px; margin-bottom:8px;">
                    <div style="font-size:12px; color:#334155; margin-bottom:4px;">
                        Evidencia historica contable <span style="background:#e0f2fe;color:#0369a1;padding:2px 7px;border-radius:999px;font-size:11px;font-weight:700;">solo consulta</span>
                    </div>
                    <ul style="margin:4px 0 0 18px; padding:0; color:#0f172a; font-size:12px;">{candidate_items}</ul>
                    <div style="font-size:11px; color:#64748b; margin-top:6px;">
                        Busqueda: {historical_query_safe}. No asigna cuenta automaticamente; solo respalda la revision humana.
                    </div>
                </div>
            """

        if suggestion:
            # Show suggestion with confidence badge
            badge_style = f"background: {suggestion.confidence_color}; color: white; padding: 2px 8px; border-radius: 10px; font-size: 11px; margin-left: 5px;"
            suggestion_html = f"""
                <div class="suggestion-box" style="background: #f0f7ff; border: 1px solid #b3d4fc; border-radius: 4px; padding: 8px; margin-bottom: 8px;">
                    <div style="font-size: 12px; color: #666; margin-bottom: 4px;">
                        💡 Sugerencia <span style="{badge_style}">{escape(suggestion.confidence_label)}</span>
                    </div>
                    <div style="font-weight: bold; color: #333;">
                        {escape(suggestion.cuenta_codigo)} - {escape(suggestion.cuenta_nombre)}
                    </div>
                    <div style="font-size: 11px; color: #666; margin-top: 4px;">
                        {escape(suggestion.reason)}
                    </div>
                    <button class="btn-accept-suggestion"
                            data-gasto-id="{gasto.id}"
                            data-cuenta-id="{suggestion.cuenta_contable_id}"
                            data-cuenta-codigo="{escape(suggestion.cuenta_codigo)}"
                            data-cuenta-nombre="{escape(suggestion.cuenta_nombre)}"
                            style="margin-top: 6px; padding: 4px 10px; background: #2196F3; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 12px;">
                        ✓ Aceptar
                    </button>
                </div>
            """
            # Pre-select high confidence suggestions
            if suggestion.confidence_score >= 0.8 and gasto.cuenta_contable_id is None:
                preselect_data = f'data-preselect-id="{suggestion.cuenta_contable_id}" data-preselect-codigo="{escape(suggestion.cuenta_codigo)}" data-preselect-nombre="{escape(suggestion.cuenta_nombre)}"'

        contra_preselect_data = ""
        if gasto.contra_cuenta_contable is None and default_contra_cuenta is not None:
            contra_preselect_data = (
                f'data-preselect-id="{default_contra_cuenta.id}" '
                f'data-preselect-codigo="{escape(default_contra_cuenta.codigo)}" '
                f'data-preselect-nombre="{escape(default_contra_cuenta.nombre)}"'
            )
        contra_prefill_note = (
            f"Prefill: {escape(default_contra_cuenta.codigo)} · "
            f"{escape(default_contra_cuenta.nombre)}"
            if gasto.contra_cuenta_contable is None and default_contra_cuenta is not None
            else (
                escape((contra_suggestion.get("source") or "heuristic").replace("_", " "))
                if contra_suggestion
                else "Se resolverá automáticamente si no la eliges."
            )
        )
        iva_preselect_data = ""
        if current_iva_account is None and iva_account:
            iva_preselect_data = (
                f'data-preselect-id="{escape(iva_account.get("cuenta_contable_id", ""))}" '
                f'data-preselect-codigo="{escape(iva_account.get("codigo", ""))}" '
                f'data-preselect-nombre="{escape(iva_account.get("nombre", ""))}"'
            )

        retenciones_html = ""
        if retenciones:
            items = "".join(
                (
                    f"<li>{escape(item['label'])}: ${float(item['importe']):,.2f}"
                    + (
                        f" <span style='color:#475569;'>→ {escape(item['account']['codigo'])}</span>"
                        if item.get("account")
                        else " <span style='color:#b91c1c;'>(sin cuenta)</span>"
                    )
                    + "<div style='margin-top:6px;'>"
                    + _retention_account_selector_html(
                        gasto_id=gasto.id,
                        retention=item,
                        current_retention_accounts=current_retention_accounts,
                    )
                    + "</div></li>"
                )
                for item in retenciones
            )
            retenciones_html = f"<ul style='margin:6px 0 0 18px; padding:0; color:#334155; font-size:12px;'>{items}</ul>"

        impuestos_locales_html = ""
        if impuestos_locales:
            items = "".join(
                f"<li>{escape(item.get('label') or 'Impuesto local')}: ${float(item.get('importe') or 0.0):,.2f}"
                + (
                    f" <span style='color:#475569;'>→ {escape(((item.get('account') or {}).get('codigo') or ''))}</span>"
                    if (item.get("account") or {}).get("codigo")
                    else ""
                )
                + (
                    f" <span style='color:#475569;'>({escape(item.get('entidad') or 'sin entidad')} / {float(item.get('tasa_pct') or 0):.2f}%)</span>"
                    if item.get("tasa_pct")
                    else ""
                )
                + (
                    " <span style='color:#166534;'>(confirmado)</span>"
                    if item.get("confirmado")
                    else ""
                )
                + "</li>"
                for item in impuestos_locales
            )
            impuestos_locales_html = f"<ul style='margin:6px 0 0 18px; padding:0; color:#334155; font-size:12px;'>{items}</ul>"

        no_deducibles_html = ""
        if no_deducibles:
            items = "".join(
                f"<li>{escape(item.get('label') or 'No deducible')}: ${float(item.get('importe') or 0.0):,.2f}"
                + (
                    f" <span style='color:#475569;'>→ {escape(((item.get('account') or {}).get('codigo') or ''))}</span>"
                    if (item.get("account") or {}).get("codigo")
                    else " <span style='color:#b91c1c;'>(sin cuenta)</span>"
                )
                + "</li>"
                for item in no_deducibles
            )
            no_deducibles_html = f"<ul style='margin:6px 0 0 18px; padding:0; color:#334155; font-size:12px;'>{items}</ul>"

        notes_html = ""
        if preview_notes:
            notes_html = "".join(
                f"<div style='margin-top:4px; font-size:11px; color:#92400e;'>{escape(note)}</div>"
                for note in preview_notes
            )

        if readiness_issues:
            readiness_html = "".join(
                f'<span class="cleanup-badge warn">{escape(issue)}</span>'
                for issue in readiness_issues
            )
        else:
            readiness_html = '<span class="cleanup-badge ok">Listo COI</span>'

        fiscal_fields_html = f"""
            <div style="display:grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap:8px; margin-top:10px;">
                <label style="font-size:11px; color:#475569;">IVA de respaldo
                    <input class="fiscal-input" data-gasto-id="{gasto.id}" data-field="iva" type="number" step="0.01" value="{'' if gasto.iva is None else float(gasto.iva)}" placeholder="Solo sin CFDI" style="width:100%; padding:7px; border:1px solid #cbd5e1; border-radius:8px;">
                </label>
                <label style="font-size:11px; color:#475569;">Entidad hospedaje
                    <input class="fiscal-input" data-gasto-id="{gasto.id}" data-field="hospedaje_entidad_fiscal" type="text" value="{escape(gasto.hospedaje_entidad_fiscal or '')}" placeholder="Ej. cdmx" style="width:100%; padding:7px; border:1px solid #cbd5e1; border-radius:8px;">
                </label>
                <label style="font-size:11px; color:#475569;">Tasa hospedaje
                    <input class="fiscal-input" data-gasto-id="{gasto.id}" data-field="hospedaje_tasa_impuesto" type="number" step="0.0001" value="{'' if gasto.hospedaje_tasa_impuesto is None else float(gasto.hospedaje_tasa_impuesto)}" placeholder="0.035 o 3.5" style="width:100%; padding:7px; border:1px solid #cbd5e1; border-radius:8px;">
                </label>
                <label style="font-size:11px; color:#475569;">Monto hospedaje
                    <input class="fiscal-input" data-gasto-id="{gasto.id}" data-field="hospedaje_impuesto_monto" type="number" step="0.01" value="{'' if gasto.hospedaje_impuesto_monto is None else float(gasto.hospedaje_impuesto_monto)}" placeholder="0.00" style="width:100%; padding:7px; border:1px solid #cbd5e1; border-radius:8px;">
                </label>
                <label style="font-size:11px; color:#475569; display:flex; align-items:center; gap:6px;">
                    <input class="fiscal-input" data-gasto-id="{gasto.id}" data-field="hospedaje_impuesto_confirmado" type="checkbox" {'checked' if gasto.hospedaje_impuesto_confirmado else ''}>
                    Hospedaje confirmado
                </label>
            </div>
        """

        tax_summary_html = f"""
            <div style="padding:10px 12px; border:1px solid #dbe2ea; border-radius:12px; background:#f8fafc; margin-bottom:10px;">
                <div style="font-size:12px; font-weight:700; color:#0f172a; margin-bottom:6px;">Desglose fiscal</div>
                <div style="display:flex; gap:6px; flex-wrap:wrap; margin-bottom:8px;">{readiness_html}</div>
                <div style="font-size:12px; color:#334155;">Base gasto: <strong>${base_gasto:,.2f}</strong></div>
                <div style="font-size:12px; color:#334155;">IVA trasladado: <strong>${iva_trasladado:,.2f}</strong></div>
                <div style="font-size:12px; color:#334155;">Impuestos locales: <strong>${impuestos_locales_total:,.2f}</strong></div>
                <div style="font-size:12px; color:#334155;">No deducibles sugeridos: <strong>${no_deducibles_total:,.2f}</strong></div>
                <div style="font-size:12px; color:#334155;">Retenciones: <strong>${retenciones_total:,.2f}</strong></div>
                <div style="font-size:12px; color:#0f172a; margin-top:4px;">Neto contrapartida: <strong>${neto_contrapartida:,.2f}</strong></div>
                {impuestos_locales_html}
                {no_deducibles_html}
                {retenciones_html}
                {notes_html}
                {fiscal_fields_html}
            </div>
        """

        row_state_chips = "".join(
            [
                '<span class="cleanup-pill cleanup-pill-warn">Falta cuenta cargo</span>'
                if gasto.cuenta_contable_id is None
                else '<span class="cleanup-pill cleanup-pill-ok">Cargo listo</span>',
                '<span class="cleanup-pill cleanup-pill-warn">Falta contrapartida</span>'
                if gasto.contra_cuenta_contable_id is None
                else '<span class="cleanup-pill cleanup-pill-ok">Contrapartida lista</span>',
                '<span class="cleanup-pill cleanup-pill-warn">Falta CFDI</span>'
                if not gasto.cfdi_report_id and not (gasto.cfdi_uuid_manual or "").strip()
                else '<span class="cleanup-pill cleanup-pill-info">CFDI identificado</span>',
            ]
        )
        detail_id = f"cleanup-detail-{gasto.id}"
        rows_html += f"""
        <tr id="row-{gasto.id}" class="cleanup-summary-row">
            <td>
                <strong>{referencia_safe}</strong><br>
                <span class="cleanup-origin">{document_origin_safe}</span><br>
                <span class="muted-mini">{documento_ref_safe}</span>
            </td>
            <td>{fecha_str}</td>
            <td>{empleado_safe}</td>
            <td style="max-width:260px;">{concepto_safe}</td>
            <td style="max-width:240px;">{proyecto_safe}<br><span class="muted-mini">{partida_presupuestal_safe}</span></td>
            <td>${gasto.gasto_cantidad:,.2f}<br><span class="muted-mini">{metodo_pago_safe}</span></td>
            <td>{cuenta_asignada_safe}<br><span class="muted-mini">Base: {cuenta_base_safe}</span></td>
            <td>{cfdi_origen_display}<br><span class="muted-mini">{escape(cfdi_status)}</span></td>
            <td><div class="cleanup-pill-stack">{row_state_chips}</div></td>
            <td>
                <button class="cleanup-toggle" type="button" data-target="{detail_id}">Revisar</button>
            </td>
        </tr>
        <tr id="{detail_id}" class="cleanup-detail-row" style="display:none;">
            <td colspan="10">
                <div class="cleanup-detail-panel">
                    <div class="cleanup-detail-head">
                        <div>
                            <strong>{referencia_safe}</strong>
                            <span class="muted-mini"> - {concepto_safe} - ${gasto.gasto_cantidad:,.2f}</span>
                            <div class="cleanup-origin-detail">
                                Origen: {document_origin_safe} · {documento_ref_safe}
                            </div>
                        </div>
                        <button class="cleanup-toggle secondary" type="button" data-target="{detail_id}">Cerrar</button>
                    </div>
                    <div class="cleanup-detail-grid">
                        <section class="cleanup-card cleanup-card-wide">
                            {cfdi_match_html}
                        </section>
                        <section class="cleanup-card">
                            {tax_summary_html}
                        </section>
                        <section class="cleanup-card">
                            <div style="font-size:12px; font-weight:700; color:#0f172a; margin:0 0 8px;">Cuentas contables</div>
                            {historical_precedent_html}
                            {suggestion_html}
                            <div style="display:grid; gap:10px;">
                                <div class="cuenta-selector" {preselect_data}>
                                    <div style="font-size:12px; font-weight:700; color:#0f172a; margin-bottom:4px;">Cargo gasto</div>
                                    {main_assigned_html}
                                    <input type="text" class="account-search" data-gasto-id="{gasto.id}" data-target="main" data-existing-id="{escape(str(gasto.cuenta_contable_id or ''))}" placeholder="Buscar o escribir cuenta del gasto..." autocomplete="off" style="width: 100%; padding: 8px; border: 1px solid #ddd; border-radius: 4px;">
                                    <div class="account-results" style="display: none; position: absolute; background: white; border: 1px solid #ddd; max-height: 200px; overflow-y: auto; z-index: 1000; width: 100%; box-shadow: 0 2px 8px rgba(0,0,0,0.15);"></div>
                                    <input type="hidden" class="main-cuenta-id" data-gasto-id="{gasto.id}" value="{escape(str(gasto.cuenta_contable_id or ''))}">
                                </div>
                                <div class="cuenta-selector" {contra_preselect_data}>
                                    <div style="font-size:12px; font-weight:700; color:#0f172a; margin-bottom:4px;">Contrapartida</div>
                                    {contra_assigned_html}
                                    <input type="text" class="account-search" data-gasto-id="{gasto.id}" data-target="contra" data-existing-id="{escape(str(gasto.contra_cuenta_contable_id or ''))}" placeholder="Buscar cuenta origen del dinero..." autocomplete="off" style="width: 100%; padding: 8px; border: 1px solid #ddd; border-radius: 4px;">
                                    <div class="account-results" style="display: none; position: absolute; background: white; border: 1px solid #ddd; max-height: 200px; overflow-y: auto; z-index: 1000; width: 100%; box-shadow: 0 2px 8px rgba(0,0,0,0.15);"></div>
                                    <input type="hidden" class="contra-cuenta-id" data-gasto-id="{gasto.id}" value="{escape(str(gasto.contra_cuenta_contable_id or ''))}">
                                    <div style="font-size:11px; color:#64748b; margin-top:4px;">{contra_prefill_note}</div>
                                </div>
                                <div class="cuenta-selector" {iva_preselect_data}>
                                    <div style="font-size:12px; font-weight:700; color:#0f172a; margin-bottom:4px;">Cuenta IVA</div>
                                    {iva_assigned_html}
                                    <input type="text" class="account-search" data-gasto-id="{gasto.id}" data-target="iva" data-existing-id="{escape(str(getattr(gasto, 'cuenta_iva_id', '') or ''))}" placeholder="Buscar cuenta de IVA acreditable..." autocomplete="off" style="width: 100%; padding: 8px; border: 1px solid #ddd; border-radius: 4px;">
                                    <div class="account-results" style="display: none; position: absolute; background: white; border: 1px solid #ddd; max-height: 200px; overflow-y: auto; z-index: 1000; width: 100%; box-shadow: 0 2px 8px rgba(0,0,0,0.15);"></div>
                                    <input type="hidden" class="iva-cuenta-id" data-gasto-id="{gasto.id}" value="{escape(str(getattr(gasto, 'cuenta_iva_id', '') or ''))}">
                                    <div style="font-size:11px; color:#64748b; margin-top:4px;">{escape("manual" if current_iva_account else "heuristic") if (current_iva_account or iva_account) else "Se resolvera automaticamente si no la eliges."}</div>
                                </div>
                            </div>
                        </section>
                        <section class="cleanup-card cleanup-action-card">
                            <div style="font-size:12px; font-weight:700; color:#0f172a; margin-bottom:8px;">Guardar fila</div>
                            <p class="muted-mini" style="margin:0 0 12px;">Guarda CFDI, impuestos y cuentas contables de este gasto sin afectar las demas filas.</p>
                            <button class="btn-asignar" data-gasto-id="{gasto.id}" style="padding: 10px 16px; background: #0f766e; color: white; border: none; border-radius: 10px; cursor: pointer; font-weight: bold; width:100%;">Guardar preparación COI</button>
                        </section>
                    </div>
                </div>
            </td>
        </tr>
        """

    # Get messages from query params
    success_msg = request.query_params.get("success_msg", "")
    error_msg = request.query_params.get("error_msg", "")
    message_html = ""
    if error_msg:
        message_html = f"""
            <div class="status-banner error">
                <strong>⚠️ Error:</strong> {escape(error_msg)}
            </div>
        """
    elif success_msg:
        message_html = f"""
            <div class="status-banner success">
                <strong>✅ Éxito:</strong> {escape(success_msg)}
            </div>
        """

    bi_context_label = f"año={bi_year_safe or 'n/a'} · ámbito={bi_scope_safe or 'all'}"
    hero_actions_html = f"""
        <a href="/admin/gastos{bi_query_suffix}" class="button secondary">Volver a finanzas</a>
        <a href="/admin/gastos/expenses{bi_query_suffix}" class="button secondary">Ver gastos</a>
        <a href="/admin/gastos/sat" class="button secondary">SAT / CFDI</a>
        <a href="/admin/gastos/cfdis/matching" class="button secondary">Matching CFDI</a>
        <a href="/admin/cuentas-contables" class="button secondary">Catálogo contable</a>
    """
    hero_side_html = f"""
        <div class="eyebrow">Cobertura</div>
        <div class="meta-grid">
            <div class="meta-card">
                <span>Pendientes</span>
                <strong>{len(gastos)}</strong>
                <small>Gastos activos con cuenta, contrapartida o CFDI aún incompletos.</small>
            </div>
            <div class="meta-card">
                <span>Contexto BI</span>
                <strong style="font-size:1rem;">{escape(bi_context_label)}</strong>
                <small>Se mantiene al navegar entre revisión, asignación y conciliación.</small>
            </div>
        </div>
    """
    bi_year_input = (
        f'<input type="hidden" name="bi_year" value="{escape(bi_year_safe)}">'
        if bi_year_safe
        else ""
    )
    bi_scope_input = (
        f'<input type="hidden" name="bi_scope" value="{escape(bi_scope_safe)}">'
        if bi_scope_safe
        else ""
    )

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Limpieza contable - Admin</title>
        <style>
            {_admin_workspace_styles("1760px", layout="data")}
            .status-banner {{
                border-radius:18px;
                padding:14px 16px;
                border:1px solid transparent;
                font-size:14px;
                line-height:1.55;
            }}
            .status-banner.error {{
                background:#fef2f2;
                border-color:#fecaca;
                color:#991b1b;
            }}
            .status-banner.success {{
                background:#ecfdf3;
                border-color:#bbf7d0;
                color:#166534;
            }}
            .legend {{
                display:flex;
                gap:16px;
                flex-wrap:wrap;
                font-size:13px;
                color:#475569;
            }}
            .legend > span {{
                font-weight:700;
                color:#0f172a;
            }}
            .review-toolbar {{
                display:flex;
                justify-content:space-between;
                align-items:center;
                gap:12px;
                flex-wrap:wrap;
            }}
            .toolbar-actions {{
                display:flex;
                gap:10px;
                flex-wrap:wrap;
            }}
            .cuenta-selector {{
                position: relative;
            }}
            .cuenta-search {{
                width:100%;
                padding:10px 12px;
                border:1px solid #cbd5e1;
                border-radius:12px;
                font-size:13px;
                background:#fff;
            }}
            .cuenta-search:focus {{
                outline:none;
                border-color:#0f766e;
                box-shadow:0 0 0 4px rgba(15,118,110,.12);
            }}
            .cuenta-results {{
                display:none;
                position:absolute;
                top:calc(100% + 6px);
                left:0;
                right:0;
                background:#fff;
                border:1px solid #dbe2ea;
                border-radius:14px;
                max-height:240px;
                overflow-y:auto;
                z-index:1000;
                box-shadow:0 14px 28px rgba(15,23,42,.12);
            }}
            .cuenta-option {{
                padding: 10px;
                cursor: pointer;
                border-bottom: 1px solid #eee;
            }}
            .cuenta-option:hover {{
                background-color: #f0f7ff;
            }}
            .legend-item {{
                display: flex;
                align-items: center;
                gap: 5px;
            }}
            .legend-dot {{
                width: 12px;
                height: 12px;
                border-radius: 50%;
            }}
            .account-results {{
                display:none;
                position:absolute;
                top:calc(100% + 6px);
                left:0;
                right:0;
                background:#fff;
                border:1px solid #dbe2ea;
                border-radius:14px;
                max-height:240px;
                overflow-y:auto;
                z-index:1000;
                box-shadow:0 14px 28px rgba(15,23,42,.12);
            }}
            .account-option {{
                padding:10px;
                cursor:pointer;
                border-bottom:1px solid #eee;
            }}
            .account-option:hover {{
                background:#f0f7ff;
            }}
            .cleanup-badge {{
                display:inline-flex;
                align-items:center;
                border-radius:999px;
                padding:3px 8px;
                font-size:11px;
                font-weight:700;
            }}
            .cleanup-badge.warn {{
                background:#fef3c7;
                color:#92400e;
                border:1px solid #fde68a;
            }}
            .cleanup-badge.ok {{
                background:#dcfce7;
                color:#166534;
                border:1px solid #bbf7d0;
            }}
            .cleanup-summary-row td {{
                vertical-align:middle;
            }}
            .muted-mini {{
                color:#64748b;
                font-size:11px;
                line-height:1.35;
            }}
            .cleanup-pill-stack {{
                display:flex;
                flex-direction:column;
                gap:5px;
                min-width:130px;
            }}
            .cleanup-pill {{
                display:inline-flex;
                width:max-content;
                align-items:center;
                border-radius:999px;
                padding:3px 8px;
                font-size:11px;
                font-weight:700;
                border:1px solid transparent;
            }}
            .cleanup-pill-ok {{
                background:#dcfce7;
                color:#166534;
                border-color:#bbf7d0;
            }}
            .cleanup-pill-warn {{
                background:#fef3c7;
                color:#92400e;
                border-color:#fde68a;
            }}
            .cleanup-pill-info {{
                background:#dbeafe;
                color:#1d4ed8;
                border-color:#bfdbfe;
            }}
            .cleanup-toggle {{
                border:0;
                border-radius:10px;
                background:#0f766e;
                color:#fff;
                font-weight:800;
                padding:8px 12px;
                cursor:pointer;
            }}
            .cleanup-toggle.secondary {{
                background:#e2e8f0;
                color:#0f172a;
            }}
            .cleanup-origin {{
                display:inline-flex;
                margin-bottom:3px;
                border-radius:999px;
                padding:3px 7px;
                background:#e0f2fe;
                color:#075985;
                font-size:11px;
                font-weight:800;
                line-height:1.25;
            }}
            .cleanup-origin-detail {{
                margin-top:6px;
                color:#475569;
                font-size:12px;
                font-weight:700;
            }}
            .cleanup-period-form {{
                display:flex;
                align-items:end;
                gap:10px;
                flex-wrap:wrap;
                margin:0 0 16px;
            }}
            .cleanup-period-form label {{
                display:grid;
                gap:5px;
                color:#334155;
                font-size:12px;
                font-weight:800;
            }}
            .cleanup-period-form input {{
                min-height:40px;
                padding:8px 10px;
                border:1px solid #cbd5e1;
                border-radius:10px;
                color:#0f172a;
                font:inherit;
            }}
            .button, .cleanup-toggle, .btn-asignar, .btn-accept-suggestion {{
                white-space:nowrap;
            }}
            .cleanup-detail-row > td {{
                padding:0 12px 18px;
                background:#f8fafc;
            }}
            .cleanup-detail-panel {{
                border:1px solid #dbe2ea;
                border-radius:18px;
                background:#fff;
                padding:14px;
                box-shadow:0 10px 26px rgba(15,23,42,.08);
            }}
            .cleanup-detail-head {{
                display:flex;
                justify-content:space-between;
                align-items:center;
                gap:12px;
                flex-wrap:wrap;
                padding-bottom:12px;
                border-bottom:1px solid #e2e8f0;
                margin-bottom:12px;
            }}
            .cleanup-detail-grid {{
                display:grid;
                grid-template-columns:minmax(280px, 1fr) minmax(300px, 1fr) minmax(340px, 1.2fr) minmax(190px, .65fr);
                gap:14px;
                align-items:start;
            }}
            .cleanup-card {{
                border:1px solid #e2e8f0;
                border-radius:16px;
                background:#f8fafc;
                padding:12px;
            }}
            .cleanup-card-wide {{
                background:#fff;
            }}
            .cleanup-action-card {{
                position:sticky;
                top:12px;
            }}
            @media (max-width: 1300px) {{
                .cleanup-detail-grid {{
                    grid-template-columns:1fr 1fr;
                }}
            }}
            @media (max-width: 900px) {{
                .cleanup-detail-grid {{
                    grid-template-columns:1fr;
                }}
                .review-toolbar,
                .toolbar-actions {{
                    flex-direction:column;
                    align-items:stretch;
                }}
                .cleanup-pill-stack {{
                    min-width:0;
                    align-items:flex-start;
                }}
                .cleanup-pill {{
                    width:auto;
                    max-width:100%;
                    white-space:normal;
                }}
                .cfdi-selector {{
                    min-width:0 !important;
                }}
                .cleanup-detail-row > td {{
                    padding:0 8px 12px;
                }}
            }}
            @media (max-width: 640px) {{
                .cleanup-period-form {{
                    align-items:stretch;
                }}
                .cleanup-period-form label,
                .cleanup-period-form input,
                .cleanup-period-form .button,
                .toolbar-actions .button,
                .toolbar-actions .cleanup-toggle {{
                    width:100%;
                }}
                .cleanup-origin {{
                    white-space:normal;
                }}
            }}
        </style>
    </head>
    <body>
        <div class="container">
            {render_admin_navigation(current_empleado, "limpieza", subtitle="Prepara pólizas COI desde la misma consola financiera: CFDI, cuentas contables y desglose fiscal.")}
            {_render_admin_workspace_hero(
                eyebrow="Contabilidad",
                title="Limpieza contable",
                description="Bandeja de preparación para completar CFDI, cuenta de cargo, contrapartida y campos fiscales existentes antes de exportar a COI.",
                actions_html=hero_actions_html,
                side_html=hero_side_html,
            )}
            <div class="stack">
                <section class="meta-grid">
                    <div class="meta-card">
                        <span>Falta cuenta gasto</span>
                        <strong>{missing_main_count}</strong>
                        <small>Gastos sin clasificación del cargo.</small>
                    </div>
                    <div class="meta-card">
                        <span>Falta contrapartida</span>
                        <strong>{missing_contra_count}</strong>
                        <small>Origen del dinero aún no definido.</small>
                    </div>
                    <div class="meta-card">
                        <span>Falta CFDI</span>
                        <strong>{missing_cfdi_count}</strong>
                        <small>Gastos sin factura vinculada para usar impuestos CFDI como fuente de verdad.</small>
                    </div>
                    <div class="meta-card">
                        <span>Con impuestos CFDI</span>
                        <strong>{tax_visible_count}</strong>
                        <small>Traen IVA y/o retenciones que afectan el asiento.</small>
                    </div>
                    <div class="meta-card">
                        <span>Alta confianza</span>
                        <strong>{high_confidence_count}</strong>
                        <small>Sugerencias de cargo listas para aceptar en bloque.</small>
                    </div>
                </section>

                <section class="surface">
                    <div class="section-head">
                        <div>
                            <div class="eyebrow">Lectura</div>
                            <h2>Niveles de confianza</h2>
                            <div class="section-note">La sugerencia de cuenta de gasto viene de reglas determinísticas y aprendizaje histórico. La contrapartida se propone con base en método de pago y catálogo contable.</div>
                        </div>
                    </div>
                    <div class="legend">
                        <span>Niveles:</span>
                        <div class="legend-item">
                            <div class="legend-dot" style="background: #4CAF50;"></div>
                            <span>Alta (≥80%) - preseleccionada</span>
                        </div>
                        <div class="legend-item">
                            <div class="legend-dot" style="background: #FF9800;"></div>
                            <span>Media (50-79%)</span>
                        </div>
                        <div class="legend-item">
                            <div class="legend-dot" style="background: #f44336;"></div>
                            <span>Baja (&lt;50%)</span>
                        </div>
                        <div class="legend-item">
                            <div class="legend-dot" style="background: #1d4ed8;"></div>
                            <span>Azul: contrapartida sugerida o ya asignada</span>
                        </div>
                    </div>
                </section>

                <section class="surface" id="feedback-anchor">
                    <form method="GET" action="/admin/gastos/sin-cuenta-contable"
                          class="cleanup-period-form">
                        <label>Mes de trabajo
                            <input type="month" name="period"
                                   value="{selected_period}">
                        </label>
                        {bi_year_input}
                        {bi_scope_input}
                        <button class="button" type="submit">Ver período</button>
                    </form>
                    <div class="review-toolbar">
                        <div>
                            <div class="eyebrow">Acciones</div>
                            <h2 style="margin:0;">Pendientes de clasificación contable</h2>
                            <div class="section-note">Puedes aceptar sugerencias de alta confianza y guardar por fila la cuenta de cargo, contrapartida, CFDI y campos fiscales editables.</div>
                        </div>
                        <div class="toolbar-actions">
                            <button id="btn-accept-all-high" class="button" type="button">
                                Aceptar alta confianza ({high_confidence_count})
                            </button>
                        </div>
                    </div>
                    {message_html}
                    {bi_context_html}
                </section>

                <section class="surface">
                    <div class="section-head">
                        <div>
                            <div class="eyebrow">Pendientes</div>
                            <h2>Bandeja de clasificación</h2>
                            <div class="section-note">Cada fila muestra cargo, contrapartida y desglose fiscal del CFDI para que no captures media póliza.</div>
                        </div>
                    </div>
                    <div class="table-shell">
                        <table>
                            <thead>
                                <tr>
                                    <th>Referencia</th>
                                    <th>Fecha</th>
                                    <th>Responsable</th>
                                    <th>Descripción</th>
                                    <th>Proyecto / Concepto</th>
                                    <th>Monto / Metodo</th>
                                    <th>Cuenta</th>
                                    <th>CFDI</th>
                                    <th>Estado COI</th>
                                    <th>Acción</th>
                                </tr>
                            </thead>
                            <tbody>
                                {rows_html if rows_html else '<tr><td colspan="10" style="text-align: center; padding: 40px;">No hay gastos pendientes de preparación COI</td></tr>'}
                            </tbody>
                        </table>
                    </div>
                </section>
            </div>
        </div>

        <script>
            const cuentasContables = {cuentas_json};
            const suggestions = {suggestions_json};
            const cfdiOptions = {cfdi_options_json};

            document.querySelectorAll('.cleanup-toggle').forEach(button => {{
                button.addEventListener('click', function() {{
                    const targetId = this.getAttribute('data-target');
                    const row = document.getElementById(targetId);
                    if (!row) {{
                        return;
                    }}
                    const isOpen = row.style.display !== 'none';
                    row.style.display = isOpen ? 'none' : 'table-row';
                    document.querySelectorAll(`.cleanup-toggle[data-target="${{targetId}}"]`).forEach(toggle => {{
                        if (!toggle.classList.contains('secondary')) {{
                            toggle.textContent = isOpen ? 'Revisar' : 'Ocultar';
                        }}
                    }});
                }});
            }});

            // Pre-select high confidence suggestions on page load
            document.addEventListener('DOMContentLoaded', function() {{
                const cleanupFlash = window.sessionStorage.getItem('cleanupContableFlash');
                if (cleanupFlash) {{
                    window.sessionStorage.removeItem('cleanupContableFlash');
                    const anchor = document.getElementById('feedback-anchor');
                    if (anchor) {{
                        const banner = document.createElement('div');
                        banner.className = 'status-banner success';
                        banner.style.marginTop = '12px';
                        banner.innerHTML = `<strong>✅ Éxito:</strong> ${{cleanupFlash}}`;
                        anchor.appendChild(banner);
                        anchor.scrollIntoView({{ behavior: 'smooth', block: 'start' }});
                    }}
                }}

                document.querySelectorAll('.cuenta-selector[data-preselect-id]').forEach(selector => {{
                    const searchInput = selector.querySelector('.account-search');
                    const gastoId = searchInput.getAttribute('data-gasto-id');
                    const target = searchInput.getAttribute('data-target');
                    const preId = selector.getAttribute('data-preselect-id');
                    const preCodigo = selector.getAttribute('data-preselect-codigo');
                    const preNombre = selector.getAttribute('data-preselect-nombre');

                    const hiddenInput = selector.querySelector('input[type="hidden"]');

                    searchInput.value = `${{preCodigo}} - ${{preNombre}}`;
                    hiddenInput.value = preId;
                    searchInput.style.borderColor = '#4CAF50';
                    searchInput.style.background = '#f0fff0';
                }});
            }});

            // Accept suggestion button handler
            document.querySelectorAll('.btn-accept-suggestion').forEach(button => {{
                button.addEventListener('click', function() {{
                    const gastoId = this.getAttribute('data-gasto-id');
                    const cuentaId = this.getAttribute('data-cuenta-id');
                    const cuentaCodigo = this.getAttribute('data-cuenta-codigo');
                    const cuentaNombre = this.getAttribute('data-cuenta-nombre');

                    const selector = document.querySelector(`.account-search[data-gasto-id="${{gastoId}}"][data-target="main"]`).parentElement;
                    const searchInput = selector.querySelector('.account-search');
                    const hiddenInput = selector.querySelector('.main-cuenta-id');

                    searchInput.value = `${{cuentaCodigo}} - ${{cuentaNombre}}`;
                    hiddenInput.value = cuentaId;
                    searchInput.style.borderColor = '#4CAF50';
                    searchInput.style.background = '#f0fff0';

                    // Visual feedback
                    this.textContent = '✓ Seleccionado';
                    this.style.background = '#45a049';
                }});
            }});

            // Accept all high confidence suggestions
            document.getElementById('btn-accept-all-high').addEventListener('click', async function() {{
                const highConfidenceRows = [];

                // Find all rows with high confidence suggestions (preselected)
                document.querySelectorAll('.cuenta-selector[data-preselect-id]').forEach(selector => {{
                    const searchInput = selector.querySelector('.account-search');
                    if (searchInput.getAttribute('data-target') !== 'main') {{
                        return;
                    }}
                    const gastoId = searchInput.getAttribute('data-gasto-id');
                    const cuentaId = selector.getAttribute('data-preselect-id');
                    const contraId = document.querySelector(`.contra-cuenta-id[data-gasto-id="${{gastoId}}"]`)?.value || '';
                    highConfidenceRows.push({{ gastoId, cuentaId, contraId }});
                }});

                if (highConfidenceRows.length === 0) {{
                    alert('No hay sugerencias de alta confianza para aceptar');
                    return;
                }}

                if (!confirm(`¿Asignar cuenta contable a ${{highConfidenceRows.length}} gastos con sugerencias de alta confianza?`)) {{
                    return;
                }}

                this.disabled = true;
                this.textContent = 'Procesando...';

                let successCount = 0;
                let errorCount = 0;

                for (const {{ gastoId, cuentaId, contraId }} of highConfidenceRows) {{
                    try {{
                        const params = new URLSearchParams();
                        params.set('cuenta_contable_id', cuentaId);
                        if (contraId) {{
                            params.set('contra_cuenta_contable_id', contraId);
                        }}
                        const response = await fetch(`/admin/gastos/${{gastoId}}/asignar-cuenta-contable`, {{
                            method: 'POST',
                            headers: {{ 'Content-Type': 'application/x-www-form-urlencoded' }},
                            body: params.toString()
                        }});

                        if (response.ok) {{
                            successCount++;
                            const row = document.getElementById(`row-${{gastoId}}`);
                            if (row) {{
                                row.style.opacity = '0.3';
                            }}
                        }} else {{
                            errorCount++;
                        }}
                    }} catch (e) {{
                        errorCount++;
                    }}
                }}

                // Show result and reload
                alert(`Procesado: ${{successCount}} exitosos, ${{errorCount}} errores`);
                window.location.reload();
            }});

            function normalizeCuentaSearchText(value) {{
                return (value || '')
                    .toString()
                    .toLowerCase()
                    .normalize('NFD')
                    .replace(/[\u0300-\u036f]/g, '')
                    .replace(/[^a-z0-9]+/g, ' ')
                    .trim()
                    .replace(/\\s+/g, ' ');
            }}

            // Setup search functionality for all rows
            document.querySelectorAll('.account-search').forEach(searchInput => {{
                const gastoId = searchInput.getAttribute('data-gasto-id');
                const target = searchInput.getAttribute('data-target');
                const resultsDiv = searchInput.nextElementSibling;
                const hiddenInput = searchInput.parentElement.querySelector('input[type="hidden"]');

                searchInput.addEventListener('input', function() {{
                    const query = normalizeCuentaSearchText(this.value);

                    // Reset styling when user types
                    this.style.borderColor = '#ddd';
                    this.style.background = 'white';

                    if (query.length < 2) {{
                        resultsDiv.style.display = 'none';
                        return;
                    }}

                    const filtered = cuentasContables.filter(c => {{
                        const searchableText = normalizeCuentaSearchText(
                            (c.codigo || '') + ' ' + (c.nombre || '') + ' ' + (c.tipo || '')
                        );
                        return searchableText.includes(query);
                    }});

                    if (filtered.length === 0) {{
                        resultsDiv.innerHTML = '<div style="padding: 10px; color: #999;">No se encontraron cuentas</div>';
                        resultsDiv.style.display = 'block';
                        return;
                    }}

                    let html = '';
                    filtered.slice(0, 50).forEach(c => {{
                        html += `<div class="account-option" data-id="${{c.id}}" data-codigo="${{c.codigo}}" data-nombre="${{c.nombre}}">
                            <strong>${{c.codigo}}</strong> - ${{c.nombre}}<br>
                            <small style="color: #666;">Tipo: ${{c.tipo}}</small>
                        </div>`;
                    }});

                    resultsDiv.innerHTML = html;
                    resultsDiv.style.display = 'block';

                    // Add click handlers
                    resultsDiv.querySelectorAll('.account-option').forEach(option => {{
                        option.addEventListener('click', function() {{
                            const id = this.getAttribute('data-id');
                            const codigo = this.getAttribute('data-codigo');
                            const nombre = this.getAttribute('data-nombre');

                            hiddenInput.value = id;
                            searchInput.value = `${{codigo}} - ${{nombre}}`;
                            searchInput.style.borderColor = '#2196F3';
                            searchInput.style.background = '#f0f7ff';
                            resultsDiv.style.display = 'none';
                        }});
                    }});
                }});

                // Hide results when clicking outside
                document.addEventListener('click', function(e) {{
                    if (!searchInput.contains(e.target) && !resultsDiv.contains(e.target)) {{
                        resultsDiv.style.display = 'none';
                    }}
                }});
            }});

            // Setup searchable CFDI selector for all rows
            document.querySelectorAll('.cfdi-search').forEach(searchInput => {{
                const gastoId = searchInput.getAttribute('data-gasto-id');
                const selector = searchInput.parentElement;
                const resultsDiv = selector.querySelector('.cfdi-results');
                const hiddenInput = selector.querySelector('.cfdi-report-id');

                const renderResults = (query) => {{
                    const normalized = (query || '').toLowerCase().trim();
                    const filtered = cfdiOptions.filter(c =>
                        !normalized ||
                        c.label.toLowerCase().includes(normalized) ||
                        (c.uuid || '').toLowerCase().includes(normalized)
                    );

                    if (filtered.length === 0) {{
                        resultsDiv.innerHTML = '<div style="padding: 10px; color: #999;">No se encontraron CFDI disponibles</div>';
                        resultsDiv.style.display = 'block';
                        return;
                    }}

                    let html = '';
                    const visibleOptions = filtered.slice(0, 50);
                    visibleOptions.forEach((c, index) => {{
                        html += `<div class="account-option cfdi-option" data-index="${{index}}">
                            <strong>${{c.uuid || 'CFDI sin UUID'}}</strong><br>
                            <small style="color: #666;">${{c.label}}</small>
                        </div>`;
                    }});

                    resultsDiv.innerHTML = html;
                    resultsDiv.style.display = 'block';
                    resultsDiv.querySelectorAll('.cfdi-option').forEach(option => {{
                        option.addEventListener('click', function() {{
                            const selected = visibleOptions[Number(this.getAttribute('data-index'))];
                            hiddenInput.value = selected.id;
                            searchInput.value = selected.label;
                            searchInput.style.borderColor = '#2196F3';
                            searchInput.style.background = '#f0f7ff';
                            resultsDiv.style.display = 'none';
                        }});
                    }});
                }};

                searchInput.addEventListener('focus', function() {{
                    renderResults(this.value);
                }});

                searchInput.addEventListener('input', function() {{
                    hiddenInput.value = '';
                    this.style.borderColor = '#ddd';
                    this.style.background = 'white';
                    renderResults(this.value);
                }});

                document.addEventListener('click', function(e) {{
                    if (!searchInput.contains(e.target) && !resultsDiv.contains(e.target)) {{
                        resultsDiv.style.display = 'none';
                    }}
                }});
            }});

            // Setup assign button handlers
            document.querySelectorAll('.btn-asignar').forEach(button => {{
                button.addEventListener('click', async function() {{
                    const gastoId = this.getAttribute('data-gasto-id');
                    const mainInput = document.querySelector(`.main-cuenta-id[data-gasto-id="${{gastoId}}"]`);
                    const contraInput = document.querySelector(`.contra-cuenta-id[data-gasto-id="${{gastoId}}"]`);
                    const ivaInput = document.querySelector(`.iva-cuenta-id[data-gasto-id="${{gastoId}}"]`);
                    const retentionInputs = document.querySelectorAll(`.retention-cuenta-id[data-gasto-id="${{gastoId}}"]`);
                    const cuentaId = mainInput.value;
                    const contraId = contraInput.value;
                    const cuentaIvaId = ivaInput ? ivaInput.value : '';

                    if (!cuentaId && !document.querySelector(`.account-search[data-gasto-id="${{gastoId}}"][data-target="main"]`)?.getAttribute('data-existing-id')) {{
                        alert('Por favor seleccione una cuenta contable del gasto');
                        return;
                    }}

                    const cfdiInput = document.querySelector(`.cfdi-report-id[data-gasto-id="${{gastoId}}"]`);
                    const fiscalInputs = document.querySelectorAll(`.fiscal-input[data-gasto-id="${{gastoId}}"]`);

                    // Disable button
                    this.disabled = true;
                    this.textContent = 'Guardando...';

                    try {{
                        const params = new URLSearchParams();
                        if (cuentaId) {{
                            params.set('cuenta_contable_id', cuentaId);
                        }}
                        if (contraId) {{
                            params.set('contra_cuenta_contable_id', contraId);
                        }}
                        if (cuentaIvaId) {{
                            params.set('cuenta_iva_id', cuentaIvaId);
                        }}
                        retentionInputs.forEach(input => {{
                            const impuesto = input.getAttribute('data-impuesto');
                            if (impuesto && input.value) {{
                                params.set(`retention_account_${{impuesto}}`, input.value);
                            }}
                        }});
                        if (cfdiInput && cfdiInput.value) {{
                            params.set('cfdi_report_id', cfdiInput.value);
                        }}
                        fiscalInputs.forEach(input => {{
                            const field = input.getAttribute('data-field');
                            if (!field) {{
                                return;
                            }}
                            if (input.type === 'checkbox') {{
                                params.set(field, input.checked ? 'on' : '');
                            }} else {{
                                params.set(field, input.value || '');
                            }}
                        }});
                        const response = await fetch(`/admin/gastos/${{gastoId}}/cleanup-contable`, {{
                            method: 'POST',
                            headers: {{
                                'Content-Type': 'application/x-www-form-urlencoded',
                            }},
                            body: params.toString()
                        }});

                        if (response.ok) {{
                            this.textContent = 'Guardado ✓';
                            this.style.background = '#16a34a';
                            this.style.cursor = 'default';
                            window.sessionStorage.setItem(
                                'cleanupContableFlash',
                                'Preparación COI guardada correctamente. La bandeja se actualizó con los datos vigentes.'
                            );
                            setTimeout(() => window.location.reload(), 450);
                        }} else {{
                            const error = await response.text();
                            alert('Error al guardar configuración contable: ' + error);
                            this.disabled = false;
                            this.textContent = 'Guardar preparación COI';
                        }}
                    }} catch (e) {{
                        alert('Error de red: ' + e.message);
                        this.disabled = false;
                        this.textContent = 'Guardar preparación COI';
                    }}
                }});
            }});
        </script>
    </body>
    </html>
    """
    return html


@router.post("/admin/gastos/{gasto_id}/cleanup-contable")
async def cleanup_contable_gasto(
    gasto_id: UUIDType,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = require_admin_finanzas(),
    cuenta_contable_id: str = Form(""),
    contra_cuenta_contable_id: str = Form(""),
    cuenta_iva_id: str = Form(""),
    cfdi_report_id: str = Form(""),
    iva: str = Form(""),
    hospedaje_entidad_fiscal: str = Form(""),
    hospedaje_tasa_impuesto: str = Form(""),
    hospedaje_impuesto_monto: str = Form(""),
    hospedaje_impuesto_confirmado: str = Form(""),
) -> Response:
    """Save one Accounting Cleanup Center row."""
    from fastapi.responses import PlainTextResponse

    try:
        form = await request.form()
        retention_account_ids = {
            str(key).replace("retention_account_", "", 1): str(value).strip()
            for key, value in form.multi_items()
            if str(key).startswith("retention_account_") and str(value).strip()
        }
        contra_id = (contra_cuenta_contable_id or "").strip()
        if not contra_id:
            result = await session.execute(
                select(ExpenseReport).where(ExpenseReport.id == gasto_id)
            )
            expense = result.scalar_one_or_none()
            if expense is None:
                return PlainTextResponse("Gasto no encontrado", status_code=404)
            if expense.contra_cuenta_contable_id:
                contra_id = str(expense.contra_cuenta_contable_id)
            else:
                contra, _ = await resolve_counterpart_account(
                    session,
                    metodo_pago=expense.metodo_pago,
                )
                if contra:
                    contra_id = str(contra.id)

        await save_expense_cleanup(
            session,
            gasto_id,
            cuenta_contable_id=(cuenta_contable_id or "").strip() or None,
            contra_cuenta_contable_id=contra_id or None,
            cuenta_iva_id=(cuenta_iva_id or "").strip() or None,
            retention_account_ids=retention_account_ids or None,
            cfdi_report_id=(cfdi_report_id or "").strip() or None,
            iva=iva,
            hospedaje_entidad_fiscal=hospedaje_entidad_fiscal,
            hospedaje_tasa_impuesto=hospedaje_tasa_impuesto,
            hospedaje_impuesto_monto=hospedaje_impuesto_monto,
            hospedaje_impuesto_confirmado=hospedaje_impuesto_confirmado,
        )

        logger.info(
            "Accounting cleanup saved for expense %s by %s",
            gasto_id,
            current_empleado.id,
        )
        return PlainTextResponse("OK", status_code=200)
    except ValueError as exc:
        await session.rollback()
        return PlainTextResponse(str(exc), status_code=400)
    except Exception as exc:
        logger.error("Error saving accounting cleanup: %s", exc, exc_info=True)
        await session.rollback()
        return PlainTextResponse(f"Error: {str(exc)}", status_code=500)


@router.post("/admin/gastos/{gasto_id}/asignar-cuenta-contable")
async def asignar_cuenta_contable(
    gasto_id: UUIDType,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = require_admin_finanzas(),
    cuenta_contable_id: str = Form(""),
    contra_cuenta_contable_id: str = Form(""),
    cuenta_iva_id: str = Form(""),
) -> Response:
    """
    Assign complete accounting configuration to an expense.
    Requires finanzas/admin role.
    """
    from fastapi.responses import PlainTextResponse
    from uuid import UUID as UUIDType

    try:
        # Load expense
        result = await session.execute(
            select(ExpenseReport).where(ExpenseReport.id == gasto_id)
        )
        expense = result.scalar_one_or_none()

        if not expense:
            return PlainTextResponse("Gasto no encontrado", status_code=404)

        cuenta_uuid = None
        cuenta = getattr(expense, "cuenta_contable", None)
        cuenta_contable_id = (cuenta_contable_id or "").strip()
        if cuenta_contable_id:
            try:
                cuenta_uuid = UUIDType(cuenta_contable_id)
            except ValueError:
                return PlainTextResponse("Cuenta contable inválida", status_code=400)
            cuenta_result = await session.execute(
                select(CuentaContable).where(
                    and_(
                        CuentaContable.id == cuenta_uuid,
                        CuentaContable.activo.is_(True),
                    )
                )
            )
            cuenta = cuenta_result.scalar_one_or_none()
            if not cuenta:
                return PlainTextResponse(
                    "Cuenta contable inválida o inactiva", status_code=400
                )
        elif expense.cuenta_contable_id:
            cuenta_uuid = expense.cuenta_contable_id
        else:
            return PlainTextResponse(
                "Debe asignar la cuenta contable del gasto", status_code=400
            )

        contra_uuid = None
        contra = getattr(expense, "contra_cuenta_contable", None)
        contra_cuenta_contable_id = (contra_cuenta_contable_id or "").strip()
        if contra_cuenta_contable_id:
            try:
                contra_uuid = UUIDType(contra_cuenta_contable_id)
            except ValueError:
                return PlainTextResponse("Contrapartida inválida", status_code=400)
            contra_result = await session.execute(
                select(CuentaContable).where(
                    and_(
                        CuentaContable.id == contra_uuid,
                        CuentaContable.activo.is_(True),
                    )
                )
            )
            contra = contra_result.scalar_one_or_none()
            if not contra:
                return PlainTextResponse(
                    "Contrapartida inválida o inactiva", status_code=400
                )
        elif expense.contra_cuenta_contable_id:
            contra_uuid = expense.contra_cuenta_contable_id
        else:
            contra, _ = await resolve_counterpart_account(
                session,
                metodo_pago=expense.metodo_pago,
            )
            if contra:
                contra_uuid = contra.id

        if not contra_uuid:
            return PlainTextResponse(
                "No se pudo resolver la contrapartida. Selecciona una cuenta origen del dinero.",
                status_code=400,
            )

        iva_uuid = None
        cuenta_iva = getattr(expense, "cuenta_iva", None)
        cuenta_iva_id = (cuenta_iva_id or "").strip()
        if cuenta_iva_id:
            try:
                iva_uuid = UUIDType(cuenta_iva_id)
            except ValueError:
                return PlainTextResponse("Cuenta IVA inválida", status_code=400)
            iva_result = await session.execute(
                select(CuentaContable).where(
                    and_(
                        CuentaContable.id == iva_uuid,
                        CuentaContable.activo.is_(True),
                    )
                )
            )
            cuenta_iva = iva_result.scalar_one_or_none()
            if not cuenta_iva:
                return PlainTextResponse(
                    "Cuenta IVA inválida o inactiva", status_code=400
                )
        elif expense.cuenta_iva_id:
            iva_uuid = expense.cuenta_iva_id

        expense.cuenta_contable_id = cuenta_uuid
        expense.contra_cuenta_contable_id = contra_uuid
        expense.cuenta_contable_budget_concept_id = None
        expense.contra_cuenta_contable_budget_concept_id = None
        expense.cuenta_iva_id = iva_uuid

        await session.commit()

        logger.info(
            "Accounting configuration saved for expense %s by %s: cargo=%s contra=%s iva=%s",
            gasto_id,
            current_empleado.id,
            getattr(cuenta, "codigo", None),
            getattr(contra, "codigo", None),
            getattr(cuenta_iva, "codigo", None),
        )

        return PlainTextResponse("OK", status_code=200)

    except Exception as e:
        logger.error(f"Error assigning cuenta contable: {e}", exc_info=True)
        await session.rollback()
        return PlainTextResponse(f"Error: {str(e)}", status_code=500)


async def admin_presupuestos_create_line(
    version_id: UUIDType,
    concept_name: Optional[str] = None,
    budget_amount: Optional[float] = 0,
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = Depends(get_current_empleado),
) -> RedirectResponse:
    """Legacy direct-call bridge; canonical route lives in admin_budget_routes."""
    _require_budget_access(current_empleado, "line_update")
    await create_budget_line(
        session,
        version_id=str(version_id),
        actor_empleado_id=str(current_empleado.id),
        concept_name=concept_name or "",
        budget_amount=budget_amount or 0,
    )
    return RedirectResponse(
        url=_presupuestos_redirect_url(version_id=str(version_id)),
        status_code=303,
    )


async def admin_presupuestos_update_line(
    request: Request,
    line_id: UUIDType,
    version_id: str,
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = Depends(get_current_empleado),
) -> RedirectResponse:
    """Legacy direct-call bridge; canonical route lives in admin_budget_routes."""
    _require_budget_access(current_empleado, "line_update")
    form = await request.form()
    updates = {
        key: form.get(key)
        for key in (
            "budget_concept_id",
            "concept_name",
            "account_code_final",
            "phase",
            "owner_name",
            "priority",
            "budget_amount",
            "criteria_note",
            "observations",
        )
        if key in form
    }
    await update_budget_line(
        session,
        line_id=str(line_id),
        actor_empleado_id=str(current_empleado.id),
        updates=updates,
    )
    return RedirectResponse(
        url=_presupuestos_redirect_url(version_id=version_id),
        status_code=303,
    )


from .admin_budget_routes import _presupuestos_redirect_url, register_presupuestos_routes

register_presupuestos_routes(router)
