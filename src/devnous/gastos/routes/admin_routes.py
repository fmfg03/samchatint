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
from collections import deque
from datetime import datetime
from html import escape
from pathlib import Path
from threading import Lock
from typing import Optional, List, Any, Union, Dict
from urllib.parse import quote
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
)
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
    get_tournament_scope_options,
    tournament_scope_config_changed,
)
from ..services.tournament_project_visibility import (
    DEFAULT_OPERATIONS_ONLY_VISIBILITY,
    format_form_visibility_areas_label,
    parse_form_visibility_areas_from_form,
    render_form_visibility_areas_checkboxes,
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
from ..services.expense_accounting_cleanup_service import (
    build_cleanup_preview,
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
from ..services.telegram_console import TELEGRAM_APPROVER_ROLES
from ..utils.receipt_bytes import (
    MAX_DECODE_BYTES,
    fetch_expense_ids_with_archivo_data,
    fetch_gasto_adjuntos_meta_batch,
    html_expense_archivos_cell,
    read_upload_limited,
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
from samchat.budgets.exporter import generate_budget_review_xlsx
from samchat.budgets.service import (
    build_budget_commitment_expense_preview,
    DEFAULT_BUDGET_ARTIFACT,
    budget_alias_candidates,
    build_budget_executive_alerts,
    build_budget_executive_comparison,
    build_budget_scenario_player,
    build_budget_snapshot,
    bulk_save_budget_concepts,
    clear_budget_concept_scope_for_tournament,
    create_budget_line,
    create_budget_version,
    ensure_budget_schema,
    hide_budget_concept,
    import_budget_artifact,
    import_budget_lines_upload,
    list_budget_audit_events,
    list_budget_concepts,
    list_budget_lines,
    list_budget_tournament_commitments,
    list_budget_versions,
    transition_budget_version,
    update_budget_line,
    update_budget_concept,
    update_budget_version_metadata,
)
from samchat.sam_inbox import build_sam_inbox_payload
from devnous.sat.sat_handler import SATExpenseHandler
from devnous.gastos.services.sat_catalog_service import (
    list_sat_catalogs,
    render_catalog_preview_rows,
)

logger = logging.getLogger(__name__)

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
    "finanzas": {
        "label": "Finanzas",
        "base_role": "finanzas",
        "description": "Solicitudes, pagos, comprobaciones y reportes financieros.",
        "permissions": [
            "admin.finanzas.manage",
            "finance.solicitudes.*",
            "finance.payments.*",
            "finance.reimbursements.*",
            "executive.reports.read",
            "budgets.read",
            "budgets.version.read",
            "budgets.line.read",
            "budgets.audit.read",
            "budgets.export",
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
            "executive.reports.read",
            "budgets.read",
            "budgets.version.read",
            "budgets.line.read",
            "budgets.audit.read",
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
            "budgets.update",
            "budgets.approve",
            "budgets.freeze",
            "budgets.export",
            "budgets.version.read",
            "budgets.version.update",
            "budgets.version.approve",
            "budgets.version.manage",
            "budgets.line.read",
            "budgets.line.update",
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
            "budgets.read",
            "budgets.version.read",
            "budgets.line.read",
            "budgets.audit.read",
            "marketing.media.read",
            "communications.email.read",
            "communications.whatsapp.read",
        ],
    },
}

_BUDGET_ROLE_ALLOW = {"admin", "finanzas", "superadmin", "super_admin"}


def _budget_can(current_empleado: Empleado, *tokens: str) -> bool:
    role = str(getattr(current_empleado, "rol", "") or "").strip().lower()
    if role in _BUDGET_ROLE_ALLOW:
        return True
    return any(has_permission(current_empleado, token) for token in tokens if token)


def _budget_access_map(current_empleado: Empleado) -> dict[str, bool]:
    return {
        "read": _budget_can(
            current_empleado,
            "budgets.read",
            "budgets.version.read",
            "budgets.line.read",
            "budgets.manage",
            "budgets.*",
        ),
        "create": _budget_can(
            current_empleado,
            "budgets.create",
            "budgets.version.create",
            "budgets.manage",
            "budgets.version.manage",
            "budgets.*",
        ),
        "version_update": _budget_can(
            current_empleado,
            "budgets.update",
            "budgets.version.update",
            "budgets.manage",
            "budgets.version.manage",
            "budgets.*",
        ),
        "line_update": _budget_can(
            current_empleado,
            "budgets.update",
            "budgets.line.update",
            "budgets.manage",
            "budgets.line.manage",
            "budgets.*",
        ),
        "approve": _budget_can(
            current_empleado,
            "budgets.approve",
            "budgets.version.approve",
            "budgets.manage",
            "budgets.version.manage",
            "budgets.*",
        ),
        "freeze": _budget_can(
            current_empleado,
            "budgets.freeze",
            "budgets.version.manage",
            "budgets.manage",
            "budgets.*",
        ),
        "audit_read": _budget_can(
            current_empleado,
            "budgets.audit.read",
            "audit.logs.read",
            "budgets.manage",
            "budgets.*",
        ),
        "export": _budget_can(
            current_empleado,
            "budgets.export",
            "budgets.manage",
            "budgets.*",
        ),
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
        "budgets": role_norm in _BUDGET_ROLE_ALLOW
        or _has_prefix("budgets", "executive"),
    }
    enabled_surfaces = [
        label
        for label, is_enabled in (
            ("Telegram", highlights["telegram"]),
            ("Operaciones", highlights["operations"]),
            ("Presupuestos", highlights["budgets"]),
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
    inicio_items = [
        ("/admin/gastos", "Resumen", "dashboard"),
        ("/admin/sam-inbox", "Sam Inbox", "sam_inbox"),
    ]
    finanzas_items = [
        ("/admin/finanzas", "Finanzas", "finanzas"),
        ("/admin/gastos/cfdis/matching", "Matching CFDI", "matching"),
        ("/admin/gastos/sat", "e.firma SAT", "sat"),
        ("/admin/gastos/sin-cuenta-contable", "Limpieza contable", "limpieza"),
    ]
    catalogos_items = [
        ("/admin/empleados", "Empleados", "empleados"),
        ("/admin/perfiles", "Perfiles", "perfiles"),
        ("/admin/rfc", "RFC", "rfc"),
        ("/admin/cuentas-contables", "Cuentas", "cuentas"),
        ("/admin/centros-costo", "Centros", "centros"),
        ("/admin/proveedores-clientes", "Proveedores", "proveedores"),
        ("/admin/torneos", "Torneos y proyectos", "torneos"),
    ]
    avanzado_items = [
        ("/admin/sports", "Sports", "sports"),
        ("/admin/torneos/domain-alignment", "Alineación", "alineacion"),
        ("/admin/presupuestos", "Presupuestos", "presupuestos"),
    ]
    if role_norm in {"admin", "superadmin", "super_admin"}:
        avanzado_items.append(
            ("/admin/customer-success/uso", "Customer Success", "customer_success")
        )
    avanzado_keys = {key for _, _, key in avanzado_items}
    avanzado_open = active_area in avanzado_keys
    is_superadmin = role_norm in {"superadmin", "super_admin"}
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


def _admin_workspace_styles(max_width: str = "1240px") -> str:
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
        body {{
            font-family:"Segoe UI","Helvetica Neue",sans-serif;
            margin:0;
            padding:26px 16px;
            color:var(--shell-ink);
            background:
                radial-gradient(circle at top left, rgba(15,118,110,.10), transparent 26%),
                radial-gradient(circle at top right, rgba(29,78,216,.08), transparent 22%),
                linear-gradient(180deg, #eaf1f6 0%, #dfe9f1 100%);
            min-height:100vh;
        }}
        .container,
        .workspace-shell {{
            max-width:{max_width};
            margin:0 auto;
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
            grid-template-columns:repeat(auto-fit,minmax(180px,1fr));
            gap:12px;
        }}
        .meta-card {{
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
            grid-template-columns:repeat(auto-fit,minmax(220px,1fr));
            gap:14px;
        }}
        .action-card {{
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
        }}
        .button.secondary {{
            background:#fff;
            color:#334155;
            border:1px solid var(--shell-line);
            box-shadow:none;
        }}
        @media (max-width: 980px) {{
            .workspace-hero {{ grid-template-columns:1fr; }}
        }}
    """


def _render_admin_workspace_hero(
    *,
    eyebrow: str,
    title: str,
    description: str,
    actions_html: str = "",
    side_html: str = "",
) -> str:
    return f"""
    <section class="workspace-hero">
        <div class="workspace-hero-main">
            <div class="eyebrow">{escape(eyebrow)}</div>
            <h1>{escape(title)}</h1>
            <p class="lead">{escape(description)}</p>
            {f'<div class="hero-actions">{actions_html}</div>' if actions_html else ''}
        </div>
        <aside class="workspace-hero-side">
            {side_html}
        </aside>
    </section>
    """


def _render_admin_error_page(
    *,
    title: str,
    message: str,
    detail: str,
    current_empleado: Optional[Empleado] = None,
    return_href: str = "/admin/gastos",
    return_label: str = "Volver a finanzas",
) -> str:
    navigation_html = (
        render_admin_navigation(
            current_empleado,
            "dashboard",
            subtitle="La consola sigue disponible aunque esta vista haya fallado.",
        )
        if current_empleado
        else ""
    )
    actions_html = f"""
        <a href="{return_href}" class="button">{escape(return_label)}</a>
        <a href="/panel" class="button secondary">Panel de administración</a>
    """
    safe_detail = escape((detail or "").strip()[:240] or "Sin detalle adicional.")
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>{escape(title)}</title>
        <style>
            {_admin_workspace_styles("1080px")}
            .error-stack {{
                display:grid;
                gap:16px;
            }}
            .status-banner {{
                border-radius:18px;
                padding:16px 18px;
                border:1px solid #fecaca;
                background:#fef2f2;
                color:#991b1b;
                line-height:1.6;
            }}
            code {{
                display:block;
                margin-top:10px;
                padding:12px 14px;
                border-radius:14px;
                background:rgba(127,29,29,.08);
                color:#7f1d1d;
                white-space:pre-wrap;
                word-break:break-word;
                font-size:12px;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            {navigation_html}
            {_render_admin_workspace_hero(
                eyebrow="Incidencia",
                title=title,
                description=message,
                actions_html=actions_html,
                side_html="""
                    <div class="eyebrow">Estado</div>
                    <div class="meta-grid">
                        <div class="meta-card">
                            <span>Resultado</span>
                            <strong style="font-size:1rem;">Error controlado</strong>
                            <small>La consola respondió sin 500 y mantiene una ruta de salida clara.</small>
                        </div>
                    </div>
                """,
            )}
            <div class="error-stack">
                <section class="surface">
                    <div class="section-head">
                        <div>
                            <div class="eyebrow">Diagnóstico</div>
                            <h2>Detalle técnico resumido</h2>
                            <div class="section-note">Se limita el detalle expuesto, pero se conserva suficiente contexto para operar y revisar logs.</div>
                        </div>
                    </div>
                    <div class="status-banner">
                        <strong>La vista no pudo completarse.</strong>
                        <code>{safe_detail}</code>
                    </div>
                </section>
            </div>
        </div>
    </body>
    </html>
    """


def _record_health_probe(payload: dict[str, Any]) -> None:
    with _HEALTH_HISTORY_LOCK:
        _HEALTH_HISTORY.appendleft(payload)


def _bank_upload_cache_dir() -> Path:
    cache_dir = _repo_root() / "data" / "accounting" / "bank_uploads"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def _bank_upload_cache_path(filename: str) -> Path:
    safe_name = Path(filename or "bank.csv").name
    return _bank_upload_cache_dir() / safe_name


async def _ensure_access_profiles_schema(session: AsyncSession) -> None:
    """Create access profile tables/indexes if missing (idempotent runtime guard)."""
    await session.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS access_profiles (
                id UUID PRIMARY KEY,
                profile_key VARCHAR(80) NOT NULL UNIQUE,
                name VARCHAR(120) NOT NULL,
                description TEXT NULL,
                base_role VARCHAR(50) NOT NULL DEFAULT 'empleado',
                permissions JSONB NOT NULL DEFAULT '{}'::jsonb,
                active BOOLEAN NOT NULL DEFAULT TRUE,
                created_by_empleado_id UUID NULL REFERENCES empleados(id),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
    )
    await session.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS empleado_access_profiles (
                id UUID PRIMARY KEY,
                empleado_id UUID NOT NULL REFERENCES empleados(id) ON DELETE CASCADE,
                profile_id UUID NOT NULL REFERENCES access_profiles(id) ON DELETE CASCADE,
                is_primary BOOLEAN NOT NULL DEFAULT FALSE,
                assigned_by_empleado_id UUID NULL REFERENCES empleados(id),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (empleado_id, profile_id)
            )
            """
        )
    )
    await session.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS access_profile_audit_log (
                id UUID PRIMARY KEY,
                event_type VARCHAR(80) NOT NULL,
                actor_empleado_id UUID NULL REFERENCES empleados(id),
                profile_id UUID NULL REFERENCES access_profiles(id) ON DELETE SET NULL,
                empleado_id UUID NULL REFERENCES empleados(id) ON DELETE SET NULL,
                payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
    )
    await session.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_access_profiles_profile_key ON access_profiles(profile_key)"
        )
    )
    await session.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_access_profiles_active ON access_profiles(active)"
        )
    )
    await session.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_empleado_access_profiles_unique ON empleado_access_profiles(empleado_id, profile_id)"
        )
    )
    await session.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_empleado_access_profiles_empleado_id ON empleado_access_profiles(empleado_id)"
        )
    )
    await session.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_empleado_access_profiles_profile_id ON empleado_access_profiles(profile_id)"
        )
    )
    await session.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_access_profile_audit_log_profile_id ON access_profile_audit_log(profile_id)"
        )
    )
    await session.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_access_profile_audit_log_empleado_id ON access_profile_audit_log(empleado_id)"
        )
    )
    await session.commit()


async def _ensure_tournaments_admin_schema(session: AsyncSession) -> None:
    """Create auxiliary table used to link gastos projects with Torneos records."""
    await session.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS tournament_operations_links (
                id UUID PRIMARY KEY,
                tournament_id UUID NOT NULL UNIQUE REFERENCES tournaments(id) ON DELETE CASCADE,
                operations_tournament_id VARCHAR(64) NOT NULL,
                operations_tournament_slug VARCHAR(200) NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
    )
    await session.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_tournament_operations_links_tournament_id "
            "ON tournament_operations_links(tournament_id)"
        )
    )
    await session.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_tournament_operations_links_operations_tournament_id "
            "ON tournament_operations_links(operations_tournament_id)"
        )
    )


def _admin_torneos_redirect(
    *,
    success_msg: Optional[str] = None,
    error_msg: Optional[str] = None,
) -> RedirectResponse:
    params = []
    if success_msg:
        params.append(f"success_msg={quote(success_msg)}")
    if error_msg:
        params.append(f"error_msg={quote(error_msg)}")
    suffix = f"?{'&'.join(params)}" if params else ""
    return RedirectResponse(url=f"/admin/torneos{suffix}", status_code=303)


def _normalize_linked_operations_tournament_id(value: Optional[str]) -> Optional[str]:
    normalized = str(value or "").strip()
    return normalized or None


def _operations_tournament_label(item: dict[str, Any]) -> str:
    name = str(item.get("name") or "").strip() or "Torneo sin nombre"
    slug = str(item.get("slug") or "").strip()
    suffix = f" ({slug})" if slug else ""
    status = "" if bool(item.get("is_active", True)) else " [inactivo]"
    return f"{name}{suffix}{status}"


def _render_operations_tournament_options(
    tournaments: list[dict[str, Any]],
    *,
    selected_id: Optional[str] = None,
    blank_label: str = "Sin ligar",
) -> str:
    options = [
        f'<option value="">{escape(blank_label)}</option>',
    ]
    for item in tournaments:
        option_id = str(item.get("id") or "").strip()
        if not option_id:
            continue
        selected_attr = " selected" if option_id == (selected_id or "") else ""
        label = _operations_tournament_label(item)
        options.append(
            f'<option value="{escape(option_id)}"{selected_attr}>{escape(label)}</option>'
        )
    return "".join(options)


async def _load_operations_tournaments() -> tuple[list[dict[str, Any]], Optional[str]]:
    try:
        _ensure_repo_env_loaded()
        from samchat.tournaments_v2.config import load_tournaments_v2_config
        from samchat.tournaments_v2.supabase_client import SupabaseRestClient

        config = load_tournaments_v2_config()
        if not config.supabase_url:
            return [], "No se encontró configuración de Supabase para la app Torneos."
        client = SupabaseRestClient(config)
        rows = await client.select_rows(
            table="tournaments",
            select_expr="id,name,slug,description,is_active",
            order="name.asc",
            limit=250,
        )
        items: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            items.append(
                {
                    "id": str(row.get("id") or "").strip(),
                    "name": str(row.get("name") or "").strip(),
                    "slug": str(row.get("slug") or "").strip() or None,
                    "description": str(row.get("description") or "").strip() or None,
                    "is_active": bool(row.get("is_active", True)),
                }
            )
        items.sort(
            key=lambda item: (
                not bool(item.get("is_active", True)),
                str(item.get("name") or "").lower(),
                str(item.get("slug") or "").lower(),
            )
        )
        return items, None
    except Exception as exc:
        logger.warning(
            "Unable to load operations tournaments for gastos admin: %s", exc
        )
        return [], str(exc)


def _find_operations_tournament(
    tournaments: list[dict[str, Any]],
    tournament_id: Optional[str],
) -> Optional[dict[str, Any]]:
    normalized_id = _normalize_linked_operations_tournament_id(tournament_id)
    if not normalized_id:
        return None
    for item in tournaments:
        if str(item.get("id") or "").strip() == normalized_id:
            return item
    return None


async def _find_local_tournament_by_name(
    session: AsyncSession,
    name: str,
) -> Optional[Tournament]:
    normalized_name = (name or "").strip().lower()
    if not normalized_name:
        return None
    result = await session.execute(
        select(Tournament).where(func.lower(Tournament.name) == normalized_name)
    )
    return result.scalar_one_or_none()


async def _next_tournament_display_order(session: AsyncSession) -> int:
    result = await session.execute(select(func.max(Tournament.display_order)))
    max_value = result.scalar_one_or_none()
    return int(max_value or 0) + (0 if max_value is None else 1)


@router.get("/admin/health/live", response_class=JSONResponse)
async def admin_health_live():
    """
    Lightweight liveness endpoint for external uptime probes.
    No authentication and no external network calls.
    """
    return JSONResponse(
        {
            "ok": True,
            "service": "samchat-gastos",
            "timestamp": datetime.utcnow().isoformat(),
            "tocino_configured": bool(
                os.getenv("TOCINO_BASE_URL", "").strip()
                and os.getenv("TOCINO_API_KEY", "").strip()
            ),
        }
    )


@router.get("/admin/health", response_class=JSONResponse)
async def admin_health(
    current_empleado: Empleado = require_admin_finanzas(),
):
    """
    Admin health endpoint with Tocino connectivity probe.
    """
    started = time.perf_counter()
    tocino_base_url = os.getenv("TOCINO_BASE_URL", "").strip()
    tocino_api_key = os.getenv("TOCINO_API_KEY", "").strip()
    configured = bool(tocino_base_url and tocino_api_key)

    tocino_status = {
        "configured": configured,
        "reachable": False,
        "auth_ok": False,
        "http_status": None,
        "message": "",
    }

    if configured:
        try:
            client = get_tocino_client()
            try:
                # Intentional minimal payload probe:
                # 400 means endpoint reachable + auth accepted, fields missing.
                client.submit_ticket({})
                tocino_status.update(
                    {
                        "reachable": True,
                        "auth_ok": True,
                        "http_status": 200,
                        "message": "Tocino reachable and accepted request.",
                    }
                )
            except TocinoAPIError as exc:
                status = int(exc.status_code or 0)
                if status in (400, 422):
                    tocino_status.update(
                        {
                            "reachable": True,
                            "auth_ok": True,
                            "http_status": status,
                            "message": "Tocino reachable and authenticated (validation error expected).",
                        }
                    )
                elif status in (401, 403):
                    tocino_status.update(
                        {
                            "reachable": True,
                            "auth_ok": False,
                            "http_status": status,
                            "message": f"Tocino auth rejected request: {exc}",
                        }
                    )
                else:
                    tocino_status.update(
                        {
                            "reachable": status > 0,
                            "auth_ok": False,
                            "http_status": status if status > 0 else None,
                            "message": f"Tocino probe error: {exc}",
                        }
                    )
        except Exception as exc:
            tocino_status.update(
                {
                    "configured": configured,
                    "reachable": False,
                    "auth_ok": False,
                    "http_status": None,
                    "message": f"Tocino client init failed: {exc}",
                }
            )
    else:
        missing = []
        if not tocino_base_url:
            missing.append("TOCINO_BASE_URL")
        if not tocino_api_key:
            missing.append("TOCINO_API_KEY")
        tocino_status["message"] = (
            f"Missing env vars: {', '.join(missing)}" if missing else "Not configured"
        )

    duration_ms = int((time.perf_counter() - started) * 1000)
    probe = {
        "timestamp": datetime.utcnow().isoformat(),
        "tocino": tocino_status,
        "duration_ms": duration_ms,
        "checked_by": {
            "empleado_id": str(current_empleado.id),
            "rol": getattr(current_empleado, "rol", None),
        },
    }
    assistant_rag = None
    try:
        from samchat.assistant.router import get_assistant_rag_health_snapshot

        assistant_rag = get_assistant_rag_health_snapshot()
    except Exception:
        assistant_rag = None
    if assistant_rag is not None:
        probe["assistant_rag"] = assistant_rag
    _record_health_probe(probe)

    return JSONResponse(
        {
            "ok": True,
            "service": "samchat-gastos",
            **probe,
        }
    )


@router.get("/admin/health/history", response_class=JSONResponse)
async def admin_health_history(
    limit: int = Query(default=20, ge=1, le=100),
    current_empleado: Empleado = require_admin_finanzas(),
):
    """
    Recent health probe history for admin monitoring dashboards.
    """
    with _HEALTH_HISTORY_LOCK:
        items = list(_HEALTH_HISTORY)[:limit]
    assistant_eval_latest = None
    try:
        from samchat.assistant.router import get_assistant_rag_health_snapshot

        assistant_eval_latest = (get_assistant_rag_health_snapshot() or {}).get(
            "latest_eval"
        )
    except Exception:
        assistant_eval_latest = None
    return JSONResponse(
        {
            "ok": True,
            "service": "samchat-gastos",
            "count": len(items),
            "items": items,
            "assistant_eval_latest": assistant_eval_latest,
        }
    )


def format_value(value) -> str:
    """Format a value for display."""
    if value is None:
        return "-"
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    return str(value)


def is_valid_uuid(value: str) -> bool:
    """Check if a string is a valid UUID."""
    if not value or not isinstance(value, str):
        return False
    try:
        UUIDType(value)
        return True
    except (ValueError, TypeError):
        return False


def resolve_project_name(proyecto: str, tournament_map: dict) -> str:
    """
    Resolve project name from UUID or return as-is.

    If proyecto is a valid UUID and exists in tournament_map, return the tournament name.
    Otherwise, return the proyecto value as-is.

    Args:
        proyecto: The project value (could be UUID or name string)
        tournament_map: Dictionary mapping tournament UUID strings to tournament names

    Returns:
        The resolved project name
    """
    if not proyecto:
        return "-"

    # Check if it's a UUID and we have a mapping
    if is_valid_uuid(proyecto):
        tournament_name = tournament_map.get(proyecto.lower())
        if tournament_name:
            return tournament_name

    return proyecto


def format_datetime(dt: Optional[datetime]) -> str:
    """Format datetime for CSV export."""
    if dt is None:
        return ""
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def determine_admin_redirect_url(
    next_param: Optional[str], default: str = "/panel"
) -> str:
    """
    Determine the redirect URL for admin actions.

    Args:
        next_param: The 'next' parameter from query string or form
        default: Default redirect URL (typically /panel for admin pages)

    Returns:
        Redirect URL string

    Note:
        For security, only allows relative URLs starting with / (but not //)
    """
    from urllib.parse import unquote

    # If next parameter is provided and looks safe, use it
    if next_param:
        next_url = unquote(next_param).strip()
        # Security: only allow relative URLs starting with /
        if next_url.startswith("/") and not next_url.startswith("//"):
            return next_url

    # Default redirect
    return default


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _ensure_repo_env_loaded() -> None:
    env_path = _repo_root() / ".env"
    if env_path.exists():
        load_dotenv(env_path, override=False)


def _domain_alignment_subprocess_env(scope: str) -> dict[str, str]:
    _ensure_repo_env_loaded()
    env = os.environ.copy()
    env_path = _repo_root() / ".env"
    if env_path.exists():
        for key, value in dotenv_values(env_path).items():
            if value is None:
                continue
            env.setdefault(str(key), str(value))
    normalized_scope = (
        scope or ACTIVE_TOURNAMENT_SCOPE
    ).strip().upper() or ACTIVE_TOURNAMENT_SCOPE.upper()
    scope_key = f"DATABASE_URL_{normalized_scope}"
    env.setdefault(scope_key, env.get("DATABASE_URL_TOURNAMENTS") or "")
    return env


def _is_superadmin_role(empleado: Any) -> bool:
    return (getattr(empleado, "rol", "") or "").strip().lower() in {
        "superadmin",
        "super_admin",
    }


def _is_admin_or_finance_role(empleado: Any) -> bool:
    return (getattr(empleado, "rol", "") or "").strip().lower() in {
        "admin",
        "finanzas",
        "superadmin",
        "super_admin",
    }


def _can_access_sam_inbox(empleado: Any) -> bool:
    if _is_admin_or_finance_role(empleado):
        return True
    for token in (
        "admin.operaciones.manage",
        "admin.torneos.manage",
        "operations.folders.read",
        "operations.folders.manage",
        "operations.teams.read",
        "operations.teams.manage",
        "admin.*",
    ):
        if has_permission(empleado, token):
            return True
    return False


def _safe_money(value: Any) -> str:
    try:
        return f"{float(value or 0):,.2f}"
    except (TypeError, ValueError):
        return "0.00"


async def _probe_tournaments_v2_domain_schema() -> dict[str, Any]:
    try:
        _ensure_repo_env_loaded()
        from samchat.tournaments_v2.config import load_tournaments_v2_config
        from samchat.tournaments_v2.supabase_client import (
            SupabaseRestClient,
            TournamentsV2Error,
        )

        config = load_tournaments_v2_config()
        if not config.supabase_url:
            return {
                "ok": False,
                "message": "SUPABASE_URL no configurado",
                "columns": {},
            }
        client = SupabaseRestClient(config)
        checks = [
            ("teams", "municipality"),
            ("categories", "branch"),
            ("players", "email"),
        ]
        columns: dict[str, dict[str, Any]] = {}
        all_ok = True
        for table, column in checks:
            key = f"{table}.{column}"
            try:
                await client.select_rows(
                    table=table, select_expr=f"id,{column}", limit=1
                )
                columns[key] = {"ok": True, "message": "Disponible"}
            except TournamentsV2Error as exc:
                msg = str(exc)
                lower = msg.lower()
                is_missing = (
                    "column" in lower
                    and "does not exist" in lower
                    and column.lower() in lower
                )
                columns[key] = {
                    "ok": False if is_missing else None,
                    "message": "Falta migración remota" if is_missing else msg,
                }
                if is_missing:
                    all_ok = False
            except Exception as exc:
                columns[key] = {"ok": None, "message": str(exc)}
                all_ok = False
        return {"ok": all_ok, "message": "Schema remoto verificado", "columns": columns}
    except Exception as exc:
        return {
            "ok": False,
            "message": f"No se pudo verificar schema remoto: {exc}",
            "columns": {},
        }


def _render_tournaments_domain_alignment_page(
    *,
    current_empleado: Empleado,
    schema_probe: dict[str, Any],
    mode: str = "dry_run",
    scope: str = ACTIVE_TOURNAMENT_SCOPE,
    tournament_slug: str = "",
    team_limit: int = 0,
    run_output: Optional[str] = None,
    run_summary: Optional[dict[str, Any]] = None,
    run_returncode: Optional[int] = None,
    audit_output: Optional[str] = None,
    audit_summary: Optional[dict[str, Any]] = None,
    audit_returncode: Optional[int] = None,
    error_msg: Optional[str] = None,
) -> HTMLResponse:
    root = _repo_root()
    migration_path = (
        root
        / "goal-fest-page"
        / "supabase"
        / "migrations"
        / "20260319193500_tournaments_v2_domain_alignment.sql"
    )
    script_path = root / "scripts" / "backfill_tournaments_v2_domain_alignment.py"
    audit_script_path = root / "scripts" / "audit_tournaments_v2_alignment.py"
    mode = (mode or "dry_run").strip().lower()
    scope = (
        scope or ACTIVE_TOURNAMENT_SCOPE
    ).strip().lower() or ACTIVE_TOURNAMENT_SCOPE
    tournament_slug = (tournament_slug or "").strip()
    team_limit = max(0, int(team_limit or 0))
    role = (getattr(current_empleado, "rol", "") or "").strip().lower()
    probe_rows = ""
    for key, item in (schema_probe.get("columns") or {}).items():
        ok = item.get("ok")
        if ok is True:
            badge = '<span class="badge badge-ok">OK</span>'
        elif ok is False:
            badge = '<span class="badge badge-warn">Pendiente</span>'
        else:
            badge = '<span class="badge badge-neutral">No verificado</span>'
        probe_rows += f"""
            <tr>
                <td><code>{escape(key)}</code></td>
                <td>{badge}</td>
                <td>{escape(str(item.get("message") or ""))}</td>
            </tr>
        """

    feedback_html = ""
    if error_msg:
        feedback_html += f"""
            <div class="message error">
                <strong>Error:</strong> {escape(error_msg)}
            </div>
        """
    if run_summary is not None:
        status_class = "ok" if (run_returncode == 0) else "warn"
        feedback_html += f"""
            <div class="message {status_class}">
                <strong>Ejecución completada:</strong> modo={escape(str(run_summary.get("mode") or mode).upper())},
                returncode={escape(str(run_returncode))}
            </div>
        """

    summary_html = ""
    if run_summary is not None:
        summary_html = f"""
            <div class="summary-grid">
                <div class="metric"><span>Teams vistos</span><strong>{escape(str(run_summary.get("teams_seen", 0)))}</strong></div>
                <div class="metric"><span>Teams actualizados</span><strong>{escape(str(run_summary.get("teams_updated", 0)))}</strong></div>
                <div class="metric"><span>Players vistos</span><strong>{escape(str(run_summary.get("players_seen", 0)))}</strong></div>
                <div class="metric"><span>Players actualizados</span><strong>{escape(str(run_summary.get("players_updated", 0)))}</strong></div>
                <div class="metric"><span>Solo local teams</span><strong>{escape(str(run_summary.get("team_local_only", 0)))}</strong></div>
                <div class="metric"><span>Solo local players</span><strong>{escape(str(run_summary.get("player_local_only", 0)))}</strong></div>
            </div>
        """
    if audit_summary is not None:
        status_class = "ok" if (audit_returncode == 0) else "warn"
        feedback_html += f"""
            <div class="message {status_class}">
                <strong>Auditoría completada:</strong> modo=AUDIT,
                returncode={escape(str(audit_returncode))}
            </div>
        """
        summary_html += f"""
            <div class="summary-grid">
                <div class="metric"><span>Equipos auditados</span><strong>{escape(str(audit_summary.get("teams_seen", 0)))}</strong></div>
                <div class="metric"><span>Equipos sincronizados</span><strong>{escape(str(audit_summary.get("teams_in_sync", 0)))}</strong></div>
                <div class="metric"><span>Sin equipo remoto</span><strong>{escape(str(audit_summary.get("teams_missing_remote", 0)))}</strong></div>
                <div class="metric"><span>Drift team fields</span><strong>{escape(str(audit_summary.get("teams_with_team_field_drift", 0)))}</strong></div>
                <div class="metric"><span>Drift jugadores</span><strong>{escape(str(audit_summary.get("teams_with_player_identity_drift", 0)))}</strong></div>
                <div class="metric"><span>Drift emails</span><strong>{escape(str(audit_summary.get("teams_with_player_email_drift", 0)))}</strong></div>
            </div>
        """

    output_html = ""
    if run_output:
        output_html = f"""
            <section class="card">
                <h3>Salida</h3>
                <pre>{escape(run_output)}</pre>
            </section>
        """
    if audit_output:
        output_html += f"""
            <section class="card" style="margin-top:20px;">
                <h3>Salida Auditoría</h3>
                <pre>{escape(audit_output)}</pre>
            </section>
        """

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Alineación de Dominio Torneos</title>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {{
                margin: 0;
                padding: 24px;
                font-family: "Segoe UI", Arial, sans-serif;
                background: linear-gradient(180deg, #f8fafc 0%, #e2e8f0 100%);
                color: #0f172a;
            }}
            .container {{
                max-width: 1180px;
                margin: 0 auto;
            }}
            .topnav {{
                display: flex;
                gap: 14px;
                flex-wrap: wrap;
                margin-bottom: 20px;
            }}
            .topnav a {{
                color: #1d4ed8;
                text-decoration: none;
                font-weight: 600;
            }}
            .hero {{
                background: #ffffff;
                border: 1px solid #dbe2ea;
                border-radius: 16px;
                padding: 24px;
                box-shadow: 0 20px 50px rgba(15, 23, 42, 0.08);
                margin-bottom: 20px;
            }}
            .hero h1 {{
                margin: 0 0 8px 0;
                font-size: 30px;
            }}
            .hero p {{
                margin: 0;
                color: #475569;
                line-height: 1.6;
            }}
            .grid {{
                display: grid;
                grid-template-columns: 1.15fr 0.85fr;
                gap: 20px;
            }}
            .card {{
                background: #ffffff;
                border: 1px solid #dbe2ea;
                border-radius: 16px;
                padding: 20px;
                box-shadow: 0 18px 40px rgba(15, 23, 42, 0.06);
            }}
            .card h3 {{
                margin: 0 0 14px 0;
                font-size: 18px;
            }}
            .form-grid {{
                display: grid;
                grid-template-columns: repeat(2, minmax(0, 1fr));
                gap: 14px;
            }}
            .form-grid .full {{
                grid-column: 1 / -1;
            }}
            label {{
                display: block;
                margin-bottom: 6px;
                font-size: 13px;
                font-weight: 700;
                color: #334155;
            }}
            input, select {{
                width: 100%;
                padding: 12px 14px;
                border: 1px solid #cbd5e1;
                border-radius: 10px;
                font-size: 14px;
                background: #fff;
            }}
            input:focus, select:focus {{
                outline: none;
                border-color: #2563eb;
                box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.16);
            }}
            .actions {{
                display: flex;
                gap: 12px;
                flex-wrap: wrap;
                margin-top: 16px;
            }}
            .btn {{
                border: none;
                border-radius: 10px;
                padding: 12px 18px;
                font-weight: 700;
                cursor: pointer;
            }}
            .btn-primary {{
                background: #0f766e;
                color: #fff;
            }}
            .btn-danger {{
                background: #b91c1c;
                color: #fff;
            }}
            .btn-muted {{
                background: #e2e8f0;
                color: #0f172a;
            }}
            .message {{
                margin-bottom: 16px;
                padding: 14px 16px;
                border-radius: 12px;
                border: 1px solid transparent;
            }}
            .message.ok {{
                background: #dcfce7;
                border-color: #86efac;
                color: #166534;
            }}
            .message.warn {{
                background: #fef3c7;
                border-color: #fcd34d;
                color: #92400e;
            }}
            .message.error {{
                background: #fee2e2;
                border-color: #fca5a5;
                color: #991b1b;
            }}
            .loading-overlay {{
                position: fixed;
                inset: 0;
                background: rgba(15, 23, 42, 0.58);
                display: none;
                align-items: center;
                justify-content: center;
                z-index: 9999;
                padding: 20px;
            }}
            .loading-overlay.active {{
                display: flex;
            }}
            .loading-card {{
                width: min(460px, 100%);
                background: #ffffff;
                border-radius: 18px;
                padding: 28px 24px;
                box-shadow: 0 24px 60px rgba(15, 23, 42, 0.25);
                text-align: center;
            }}
            .spinner {{
                width: 44px;
                height: 44px;
                margin: 0 auto 14px auto;
                border-radius: 50%;
                border: 4px solid #cbd5e1;
                border-top-color: #0f766e;
                animation: spin 0.9s linear infinite;
            }}
            .loading-title {{
                margin: 0 0 8px 0;
                font-size: 20px;
                font-weight: 800;
                color: #0f172a;
            }}
            .loading-copy {{
                margin: 0;
                color: #475569;
                line-height: 1.6;
                font-size: 14px;
            }}
            .is-busy {{
                opacity: 0.7;
                pointer-events: none;
            }}
            @keyframes spin {{
                from {{ transform: rotate(0deg); }}
                to {{ transform: rotate(360deg); }}
            }}
            table {{
                width: 100%;
                border-collapse: collapse;
            }}
            th, td {{
                padding: 10px 8px;
                border-bottom: 1px solid #e2e8f0;
                text-align: left;
                vertical-align: top;
                font-size: 14px;
            }}
            .badge {{
                display: inline-block;
                padding: 4px 10px;
                border-radius: 999px;
                font-size: 12px;
                font-weight: 700;
            }}
            .badge-ok {{ background: #dcfce7; color: #166534; }}
            .badge-warn {{ background: #fef3c7; color: #92400e; }}
            .badge-neutral {{ background: #e2e8f0; color: #334155; }}
            .summary-grid {{
                display: grid;
                grid-template-columns: repeat(3, minmax(0, 1fr));
                gap: 12px;
                margin: 16px 0;
            }}
            .metric {{
                background: #f8fafc;
                border: 1px solid #e2e8f0;
                border-radius: 12px;
                padding: 14px;
            }}
            .metric span {{
                display: block;
                color: #64748b;
                font-size: 12px;
                margin-bottom: 4px;
                text-transform: uppercase;
                letter-spacing: .04em;
            }}
            .metric strong {{
                font-size: 22px;
            }}
            .meta {{
                color: #475569;
                font-size: 13px;
                line-height: 1.6;
            }}
            pre {{
                margin: 0;
                padding: 14px;
                background: #0f172a;
                color: #e2e8f0;
                border-radius: 12px;
                overflow: auto;
                font-size: 12px;
                line-height: 1.5;
            }}
            code {{
                font-family: "SFMono-Regular", Consolas, monospace;
            }}
            @media (max-width: 900px) {{
                .grid, .form-grid, .summary-grid {{
                    grid-template-columns: 1fr;
                }}
            }}
        </style>
    </head>
    <body>
        <div id="loadingOverlay" class="loading-overlay" aria-hidden="true">
            <div class="loading-card">
                <div class="spinner" aria-hidden="true"></div>
                <h2 id="loadingTitle" class="loading-title">Procesando...</h2>
                <p id="loadingCopy" class="loading-copy">
                    La operación sigue ejecutándose en el servidor. No cierres esta pestaña.
                </p>
            </div>
        </div>
        <div class="container">
            {render_admin_navigation(current_empleado, "alineacion", subtitle="Consola de backfill y auditoría entre legado y Supabase para torneos.")}

            <section class="hero">
                <h1>Alineación de Dominio de Torneos</h1>
                <p>
                    Consola operativa para validar el schema remoto de `Supabase` y ejecutar el backfill
                    de campos canónicos (`teams.municipality`, `categories.branch`, `players.email`) a partir de las tablas legacy.
                    `dry-run` está disponible para `{escape(role or '-')}`; `apply` está restringido a `superadmin`.
                </p>
            </section>

            {feedback_html}
            {summary_html}

            <div class="grid">
                <section class="card">
                    <h3>Ejecutar Backfill</h3>
                    <form method="POST" action="/admin/torneos/domain-alignment/run" data-loading-title="Ejecutando backfill" data-loading-copy="Estamos sincronizando datos legacy hacia Supabase. Esta operación puede tardar varios minutos.">
                        <div class="form-grid">
                            <div>
                                <label for="scope">Scope</label>
                                <select id="scope" name="scope">
                                    <option value="beisbol" {"selected" if scope == "beisbol" else ""}>beisbol</option>
                                </select>
                            </div>
                            <div>
                                <label for="team_limit">Límite de equipos</label>
                                <input id="team_limit" name="team_limit" type="number" min="0" value="{team_limit}">
                            </div>
                            <div class="full">
                                <label for="tournament_slug">Tournament slug opcional</label>
                                <input id="tournament_slug" name="tournament_slug" type="text" value="{escape(tournament_slug)}" placeholder="liga_telmex_telcel">
                            </div>
                            <div class="full">
                                <label for="mode">Modo</label>
                                <select id="mode" name="mode">
                                    <option value="dry_run" {"selected" if mode == "dry_run" else ""}>dry_run</option>
                                    <option value="apply" {"selected" if mode == "apply" else ""}>apply</option>
                                </select>
                            </div>
                        </div>
                        <div class="actions">
                            <button class="btn btn-primary" type="submit">Ejecutar</button>
                            <a class="btn btn-muted" href="/admin/torneos/domain-alignment" style="text-decoration:none;">Limpiar</a>
                        </div>
                    </form>
                    <p class="meta" style="margin-top:14px;">
                        `apply` escribe en Supabase. Si el schema remoto no está alineado, el runtime hace fallback,
                        pero la recomendación sigue siendo aplicar primero la migración SQL.
                    </p>
                </section>

                <section class="card">
                    <h3>Estado del Schema Remoto</h3>
                    <table>
                        <thead>
                            <tr>
                                <th>Campo</th>
                                <th>Estado</th>
                                <th>Detalle</th>
                            </tr>
                        </thead>
                        <tbody>
                            {probe_rows or '<tr><td colspan="3">Sin datos</td></tr>'}
                        </tbody>
                    </table>
                    <p class="meta" style="margin-top:14px;">
                        Archivo de migración esperado:<br>
                        <code>{escape(str(migration_path))}</code>
                    </p>
                    <p class="meta">
                        Script usado por la consola:<br>
                        <code>{escape(str(script_path))}</code>
                    </p>
                    <p class="meta">
                        Script de auditoría:<br>
                        <code>{escape(str(audit_script_path))}</code>
                    </p>
                </section>
            </div>

            <section class="card" style="margin-top:20px;">
                <h3>Auditar Drift Local vs Supabase</h3>
                <form method="POST" action="/admin/torneos/domain-alignment/audit" data-loading-title="Ejecutando auditoría" data-loading-copy="Estamos comparando legacy contra Supabase para detectar drift. Esta revisión puede tardar un poco.">
                    <div class="form-grid">
                        <div>
                            <label for="audit_scope">Scope</label>
                            <select id="audit_scope" name="scope">
                                <option value="beisbol" {"selected" if scope == "beisbol" else ""}>beisbol</option>
                            </select>
                        </div>
                        <div>
                            <label for="audit_team_limit">Límite de equipos</label>
                            <input id="audit_team_limit" name="team_limit" type="number" min="0" value="{team_limit}">
                        </div>
                        <div class="full">
                            <label for="audit_tournament_slug">Tournament slug opcional</label>
                            <input id="audit_tournament_slug" name="tournament_slug" type="text" value="{escape(tournament_slug)}" placeholder="liga_telmex_telcel">
                        </div>
                    </div>
                    <div class="actions">
                        <button class="btn btn-primary" type="submit">Auditar</button>
                    </div>
                </form>
                <p class="meta" style="margin-top:14px;">
                    Esta auditoría no escribe datos. Compara municipio, estado, categoría, rama, conteo de jugadores,
                    identidad de jugadores y email por jugador entre legacy y Supabase.
                </p>
            </section>

            {output_html}
        </div>
        <script>
            (() => {{
                const overlay = document.getElementById('loadingOverlay');
                const titleEl = document.getElementById('loadingTitle');
                const copyEl = document.getElementById('loadingCopy');
                const forms = document.querySelectorAll('form[action="/admin/torneos/domain-alignment/run"], form[action="/admin/torneos/domain-alignment/audit"]');
                let busy = false;

                function showLoading(form) {{
                    if (busy) return;
                    busy = true;
                    const title = form.getAttribute('data-loading-title') || 'Procesando...';
                    const copy = form.getAttribute('data-loading-copy') || 'La operación sigue ejecutándose en el servidor.';
                    if (titleEl) titleEl.textContent = title;
                    if (copyEl) copyEl.textContent = copy;
                    if (overlay) {{
                        overlay.classList.add('active');
                        overlay.setAttribute('aria-hidden', 'false');
                    }}
                    document.body.classList.add('is-busy');
                    forms.forEach((candidate) => {{
                        candidate.querySelectorAll('button, input, select, a').forEach((el) => {{
                            if (el.tagName === 'A') return;
                            el.setAttribute('disabled', 'disabled');
                        }});
                    }});
                }}

                forms.forEach((form) => {{
                    form.addEventListener('submit', () => showLoading(form));
                }});
            }})();
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html)


def _clean_domain_alignment_output(text_value: str) -> str:
    lines = []
    for line in (text_value or "").splitlines():
        lower = line.lower()
        if "can't initialize nvml" in lower:
            continue
        if "torch/cuda/__init__.py" in lower and "userwarning" in lower:
            continue
        lines.append(line)
    return "\n".join(lines).strip()


# This will be set by the app that includes these routes
_db_session_maker = None


def set_db_session_maker(session_maker):
    """Set the database session maker for admin routes."""
    global _db_session_maker
    _db_session_maker = session_maker


async def get_db_session() -> AsyncSession:
    """Dependency to get database session."""
    if _db_session_maker is None:
        raise RuntimeError(
            "Database session maker not set. Call set_db_session_maker() first."
        )
    async with _db_session_maker() as session:
        yield session


def build_expense_filters(
    id: Optional[str] = None,
    numero_referencia: Optional[str] = None,
    proyecto: Optional[str] = None,
    cantidad_min: Optional[float] = None,
    cantidad_max: Optional[float] = None,
    concepto: Optional[str] = None,
    tipo_gasto: Optional[str] = None,
    estado_factura: Optional[str] = None,
    estado_reembolso: Optional[str] = None,
    created_from: Optional[str] = None,
    created_to: Optional[str] = None,
) -> List:
    """Build filter conditions for expenses."""
    conditions = []

    if id:
        conditions.append(ExpenseReport.id == id)
    if numero_referencia:
        conditions.append(
            ExpenseReport.numero_referencia.ilike(f"%{numero_referencia}%")
        )
    if proyecto:
        conditions.append(ExpenseReport.proyecto.ilike(f"%{proyecto}%"))
    if cantidad_min is not None:
        conditions.append(ExpenseReport.gasto_cantidad >= cantidad_min)
    if cantidad_max is not None:
        conditions.append(ExpenseReport.gasto_cantidad <= cantidad_max)
    if concepto:
        conditions.append(ExpenseReport.concepto.ilike(f"%{concepto}%"))
    if tipo_gasto:
        conditions.append(ExpenseReport.tipo_gasto == tipo_gasto)
    if estado_factura:
        conditions.append(ExpenseReport.estado_factura == estado_factura)
    if estado_reembolso:
        conditions.append(ExpenseReport.estado_reembolso == estado_reembolso)
    if created_from:
        try:
            date_from = datetime.strptime(created_from, "%Y-%m-%d")
            conditions.append(func.date(ExpenseReport.created_at) >= date_from.date())
        except ValueError:
            pass
    if created_to:
        try:
            date_to = datetime.strptime(created_to, "%Y-%m-%d")
            conditions.append(func.date(ExpenseReport.created_at) <= date_to.date())
        except ValueError:
            pass

    return conditions


def build_invoice_filters(
    id: Optional[str] = None,
    nova_request_id: Optional[str] = None,
    estado_factura: Optional[str] = None,
    has_error: Optional[bool] = None,
    created_from: Optional[str] = None,
    created_to: Optional[str] = None,
) -> List:
    """Build filter conditions for invoices."""
    conditions = []

    if id:
        conditions.append(InvoiceReport.id == id)
    if nova_request_id:
        conditions.append(InvoiceReport.nova_request_id.ilike(f"%{nova_request_id}%"))
    if estado_factura:
        conditions.append(InvoiceReport.estado_factura == estado_factura)
    if has_error is not None:
        if has_error:
            conditions.append(InvoiceReport.mensaje_error.isnot(None))
        else:
            conditions.append(InvoiceReport.mensaje_error.is_(None))
    if created_from:
        try:
            date_from = datetime.strptime(created_from, "%Y-%m-%d")
            conditions.append(func.date(InvoiceReport.created_at) >= date_from.date())
        except ValueError:
            pass
    if created_to:
        try:
            date_to = datetime.strptime(created_to, "%Y-%m-%d")
            conditions.append(func.date(InvoiceReport.created_at) <= date_to.date())
        except ValueError:
            pass

    return conditions


def _bi_scope_terms(bi_scope: Optional[str]) -> List[str]:
    scope = (bi_scope or "").strip().lower()
    if not scope or scope == "all":
        return []
    if scope in {"beisbol", "béisbol", "baseball"}:
        return ["beisbol", "béisbol", "liga telmex telcel de beisbol"]
    return []


def _append_bi_expense_filters(
    *,
    conditions: List,
    bi_year: Optional[str],
    bi_scope: Optional[str],
) -> None:
    year = (bi_year or "").strip()
    if year.isdigit() and len(year) == 4:
        conditions.append(
            func.date(ExpenseReport.created_at) >= datetime(int(year), 1, 1).date()
        )
        conditions.append(
            func.date(ExpenseReport.created_at) <= datetime(int(year), 12, 31).date()
        )

    terms = _bi_scope_terms(bi_scope)
    if terms:
        scope_expr = []
        for term in terms:
            like = f"%{term}%"
            scope_expr.extend(
                [
                    ExpenseReport.proyecto.ilike(like),
                    ExpenseReport.concepto.ilike(like),
                    ExpenseReport.departamento.ilike(like),
                    ExpenseReport.fase_torneo.ilike(like),
                ]
            )
        conditions.append(or_(*scope_expr))


@router.get("/admin/gastos", response_class=HTMLResponse)
async def admin_dashboard(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = require_admin_finanzas(),
    bi_year: Optional[str] = Query(None),
    bi_scope: Optional[str] = Query(None),
) -> str:
    """Admin dashboard with statistics."""

    # Get counts
    expenses_result = await session.execute(select(func.count(ExpenseReport.id)))
    expenses_count = expenses_result.scalar() or 0

    invoices_result = await session.execute(select(func.count(InvoiceReport.id)))
    invoices_count = invoices_result.scalar() or 0

    bi_year_safe = (bi_year or "").strip()
    bi_year_safe = (
        bi_year_safe if (bi_year_safe.isdigit() and len(bi_year_safe) == 4) else ""
    )
    bi_scope_safe = (bi_scope or "").strip().lower()
    if bi_scope_safe not in {"all", ACTIVE_TOURNAMENT_SCOPE}:
        bi_scope_safe = ""
    bi_context_label = f"año={bi_year_safe or 'n/a'} · ámbito={bi_scope_safe or 'all'}"

    hero_actions_html = """
        <a href="/admin/gastos/expenses" class="button">Ver gastos</a>
        <a href="/admin/gastos/invoices" class="button secondary">Ver facturas</a>
        <a href="/admin/gastos/finance-training" class="button secondary">Dataset capacitación</a>
        <a href="/admin/contabilidad/estado" class="button secondary">Estado contable</a>
        <a href="/admin/nomina/prenomina" class="button secondary">Prenómina</a>
    """
    hero_side_html = f"""
        <div class="eyebrow">Contexto BI</div>
        <div class="meta-grid">
            <div class="meta-card">
                <span>Ámbito</span>
                <strong>{escape(bi_scope_safe or 'all')}</strong>
                <small>Filtro operativo heredado desde el shell React cuando aplica.</small>
            </div>
            <div class="meta-card">
                <span>Año</span>
                <strong>{escape(bi_year_safe or 'n/a')}</strong>
                <small>Contexto analítico activo para navegación cruzada.</small>
            </div>
        </div>
    """

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Gastos Admin - Copa Telmex</title>
        <style>
            {_admin_workspace_styles("1240px")}
        </style>
    </head>
    <body>
        <div class="container">
            {render_admin_navigation(current_empleado, "dashboard", subtitle="Punto de entrada para finanzas, catálogos y operación administrativa.")}
            {_render_admin_workspace_hero(
                eyebrow="Finanzas",
                title="Resumen financiero",
                description="Entrada común para gastos, facturas, conciliación y cierre operativo. Mantiene el contexto BI sin separar la consola administrativa del resto del sistema.",
                actions_html=hero_actions_html,
                side_html=hero_side_html,
            )}
            <div class="stack">
                <section class="meta-grid">
                    <div class="meta-card">
                        <span>Reportes de gastos</span>
                        <strong>{expenses_count}</strong>
                        <small>Gastos cargados actualmente en la base operativa.</small>
                    </div>
                    <div class="meta-card">
                        <span>Facturas</span>
                        <strong>{invoices_count}</strong>
                        <small>Registros fiscales disponibles para cruce y matching.</small>
                    </div>
                    <div class="meta-card">
                        <span>Contexto BI</span>
                        <strong style="font-size:1rem;">{escape(bi_context_label)}</strong>
                        <small>Se hereda cuando la navegación entra desde el shell analítico.</small>
                    </div>
                </section>
                <section class="surface">
                    <div class="section-head">
                        <div>
                            <div class="eyebrow">Acceso rápido</div>
                            <h2>Workspaces financieros</h2>
                            <div class="section-note">Desde aquí entras al detalle de gasto, control fiscal, contabilidad y nómina sin cambiar de consola.</div>
                        </div>
                    </div>
                    <div class="action-grid">
                        <a href="/admin/finanzas" class="action-card">
                            <strong>Cierre del mes</strong>
                            <p>Prioriza pagos, COI, DIOT y pólizas del periodo activo.</p>
                        </a>
                        <a href="/admin/gastos/sin-cuenta-contable" class="action-card">
                            <strong>Limpieza contable</strong>
                            <p>Corrige CFDI, cuentas contables y desglose fiscal antes de exportar COI.</p>
                        </a>
                        <a href="/admin/contabilidad/estado" class="action-card">
                            <strong>Estado contable</strong>
                            <p>Resumen mensual de COI, auxiliar, banco y conciliación.</p>
                        </a>
                        <a href="/admin/gastos/expenses" class="action-card">
                            <strong>Gastos</strong>
                            <p>Tabla global de gastos, filtros operativos y exportaciones.</p>
                        </a>
                        <a href="/admin/gastos/invoices" class="action-card">
                            <strong>Facturas</strong>
                            <p>Seguimiento fiscal, CFDI y estado documental.</p>
                        </a>
                    </div>
                </section>
            </div>
        </div>
    </body>
    </html>
    """
    return html


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
                {_admin_workspace_styles("1820px")}
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
    origen: Optional[str] = Query(None),  # 'tocino', 'csv'
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
            origen_display = (
                '<span style="color: #4CAF50;">Tocino</span>' if cfdi else "-"
            )

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
                <td>{format_value(invoice.created_at)}</td>
            </tr>
            """

        # Add standalone CFDIs (CSV-imported) that aren't linked to invoice_reports
        invoice_nova_ids = {i.nova_request_id for i in invoices if i.nova_request_id}
        for cfdi in standalone_cfdis:
            if cfdi.nova_request_id and cfdi.nova_request_id in invoice_nova_ids:
                continue  # Already shown via invoice_reports

            origen_display = (
                '<span style="color: #2196F3;">CSV</span>'
                if cfdi.origen == "csv"
                else '<span style="color: #4CAF50;">Tocino</span>'
            )

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
                {_admin_workspace_styles("1820px")}
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
                        <div class="meta-card"><span>Total visible</span><strong>{len(invoices) + len(standalone_cfdis)}</strong><small>Suma de invoice reports y CFDI standalone.</small></div>
                    </section>
                    <section class="surface">
                        <div class="section-head">
                            <div>
                                <div class="eyebrow">Origen</div>
                                <h2>Lectura rápida</h2>
                                <div class="section-note">Salta entre origen Tocino, CSV o la vista completa sin abandonar la consola.</div>
                            </div>
                        </div>
                        <div class="summary-links">
                            <a href="?origen=tocino">Tocino: {tocino_count}</a>
                            <a href="?origen=csv">CSV import: {csv_count}</a>
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
    suffix = f"?{'&'.join(params)}" if params else ""
    return f"/admin/gastos/sat{suffix}"


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
    contents = await read_upload_limited(
        upload,
        max_bytes=_SAT_EFIRMA_MAX_FILE_BYTES,
        too_large_message=f"{label} excede el tamaño máximo de 1 MB.",
    )
    if not contents:
        raise ValueError(f"{label} no puede estar vacío.")
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
                        <div style="font-size:11px;text-transform:uppercase;letter-spacing:.12em;color:#64748b;">Equipo seleccionado</div>
                        <div style="margin-top:8px;font-size:22px;font-weight:900;color:#0f172a;">{escape(str(active_team.get("team_name") or "Sin equipo"))}</div>
                        <div style="margin-top:6px;color:#475569;">{escape(str(active_team.get("entity_name") or "-"))} · {escape(str(active_team.get("category") or "-"))}</div>
                        <div style="margin-top:12px;font-weight:800;color:#0f172a;">Siguientes acciones</div>
                        <div style="margin-top:8px;">{active_team_actions}</div>
                    </div>
                </div>
            </section>
            <section class="workspace-card" style="margin-bottom:18px;">
                <div class="workspace-section-title">Matchday Ops</div>
                <div class="workspace-section-subtitle">Operación móvil: check-in, cédulas, resultados, incidencias y evidencia.</div>
                <div class="sports-grid" style="margin-top:14px;">
                    {_sports_card("Partidos", matchday.get("matches_count", 0), "Calendario cargado")}
                    {_sports_card("Cédulas abiertas", matchday.get("open_cedulas_count", 0), "Requieren cierre")}
                    {_sports_card("Acciones móvil", len(mobile_app.get("primary_actions") or []), "Flujo de cancha")}
                </div>
                <table class="sports-table" style="margin-top:16px;">
                    <thead><tr><th>Fecha</th><th>Fase</th><th>Cancha</th><th>Status</th><th>Cédula</th></tr></thead>
                    <tbody>{match_rows or '<tr><td colspan="5">Sin calendario visible.</td></tr>'}</tbody>
                </table>
            </section>
            <section class="workspace-card" style="margin-bottom:18px;">
                <div class="workspace-section-title">Match Center</div>
                <div class="workspace-section-subtitle">Pantalla por partido: equipos, cancha, status, cédula y checklist de cierre.</div>
                <div class="sports-grid" style="margin-top:14px;">
                    {_sports_card("Abiertos", match_center.get("open_count", 0), "Requieren acción")}
                    {_sports_card("Cerrados", match_center.get("closed_count", 0), "Con cédula cerrada")}
                    {_sports_card("Partido activo", active_match.get("id") or "-", str(active_match.get("match_date") or "-"))}
                </div>
                <div class="sports-section-grid" style="margin-top:16px;">
                    <div>
                        <table class="sports-table">
                            <thead><tr><th>Fecha</th><th>Local</th><th>Visitante</th><th>Cancha</th><th>Status</th><th>Cédula</th></tr></thead>
                            <tbody>{match_center_table_rows or '<tr><td colspan="6">Sin partidos para Match Center.</td></tr>'}</tbody>
                        </table>
                    </div>
                    <div style="border:1px solid #dbe2ea;border-radius:16px;background:#fff;padding:16px;">
                        <div style="font-size:11px;text-transform:uppercase;letter-spacing:.12em;color:#64748b;">Partido seleccionado</div>
                        <div style="margin-top:8px;font-size:20px;font-weight:900;color:#0f172a;">{escape(str(active_match.get("home_team_name") or "Local"))} vs {escape(str(active_match.get("away_team_name") or "Visitante"))}</div>
                        <div style="margin-top:6px;color:#475569;">Cancha {escape(str(active_match.get("field_number") or "-"))} · {escape(str(active_match.get("match_status") or "-"))} · cédula {escape(str(active_match.get("cedula_status") or "-"))}</div>
                        <div style="margin-top:12px;font-weight:800;color:#0f172a;">Checklist de cancha</div>
                        <div style="margin-top:8px;">{active_match_checklist}</div>
                    </div>
                </div>
            </section>
            <section class="sports-section-grid" style="margin-bottom:18px;">
                <div class="workspace-card">
                    <div class="workspace-section-title">Sports CRM</div>
                    <div class="workspace-section-subtitle">Relación por entidad/club/estado.</div>
                    <table class="sports-table" style="margin-top:12px;">
                        <thead><tr><th>Entidad</th><th>Equipos</th><th>Jugadores</th></tr></thead>
                        <tbody>{crm_rows or '<tr><td colspan="3">Sin entidades visibles.</td></tr>'}</tbody>
                    </table>
                </div>
                <div class="workspace-card">
                    <div class="workspace-section-title">Fan/Public Layer</div>
                    <div class="workspace-section-subtitle">Publicación de calendario, standings y media.</div>
                    <div class="sports-grid" style="margin-top:12px;">
                        {_sports_card("Calendario", "sí" if public_layer.get("calendar_ready") else "no", "Partidos publicables")}
                        {_sports_card("Standings", "sí" if public_layer.get("standings_ready") else "no", "Tabla visible")}
                        {_sports_card("Media", "sí" if public_layer.get("media_ready") else "no", "Fotos/videos/streams")}
                    </div>
                </div>
            </section>
            <section class="workspace-card" style="margin-bottom:18px;">
                <div class="workspace-section-title">Joyas Sports</div>
                <div class="workspace-section-subtitle">Readiness global, copilot, microsite público, sponsor/media, incidentes, sedes y reporte post-torneo.</div>
                <div class="sports-grid" style="margin-top:14px;">
                    {_sports_card("Readiness global", f'{float(global_readiness.get("score") or 0):.1f}', f'Status {global_readiness.get("status") or "-"}')}
                    {_sports_card("Incidentes abiertos", incident_center.get("open_count", 0), f'High {incident_center.get("high_count", 0)}')}
                    {_sports_card("Sedes", venue_ops.get("venues_count", 0), "Venue Ops")}
                    {_sports_card("Microsite", public_microsite.get("publish_state") or "draft", public_microsite.get("preview_url") or "-")}
                    {_sports_card("Sponsor media", "ready" if sponsor_media.get("export_ready") else "draft", str(sponsor_media.get("narrative") or "")[:90])}
                    {_sports_card("Reporte", "PDF/Excel", post_tournament_report.get("report_name") or "-")}
                </div>
            </section>
            <section class="sports-section-grid" style="margin-bottom:18px;">
                <div class="workspace-card">
                    <div class="workspace-section-title">Ops Copilot</div>
                    <div class="workspace-section-subtitle">Borradores operativos listos para comunicación y briefing.</div>
                    <div style="margin-top:12px;display:grid;gap:10px;">{ops_draft_rows or '<div style="color:#64748b;">Sin drafts activos.</div>'}</div>
                </div>
                <div class="workspace-card">
                    <div class="workspace-section-title">Public microsite generator</div>
                    <div class="workspace-section-subtitle">Publicación automática de calendario, standings, media y comunicados.</div>
                    <div style="margin-top:12px;font-size:22px;font-weight:900;color:#0f172a;">{escape(str(public_microsite.get("preview_url") or "-"))}</div>
                    <div style="display:flex;flex-wrap:wrap;gap:8px;margin-top:12px;">{microsite_sections or '<span class="sports-chip">sin secciones</span>'}</div>
                </div>
            </section>
            <section class="workspace-card" style="margin-bottom:18px;">
                <div class="workspace-section-title">Sponsor / Media dashboard</div>
                <div class="workspace-section-subtitle">Evidencia para patrocinadores y medios: participación, cobertura y material.</div>
                <div class="sports-grid" style="margin-top:14px;">{sponsor_points or _sports_card("Sin evidencia", 0, "Aun sin media")}</div>
            </section>
            <section class="sports-section-grid" style="margin-bottom:18px;">
                <div class="workspace-card">
                    <div class="workspace-section-title">Incident Center</div>
                    <div class="workspace-section-subtitle">Lesiones, protestas, documentos inválidos, no-shows, sanciones y cédulas abiertas como cola operativa.</div>
                    <table class="sports-table" style="margin-top:12px;">
                        <thead><tr><th>Tipo</th><th>Severidad</th><th>Mensaje</th><th>Fuente</th></tr></thead>
                        <tbody>{incident_rows or '<tr><td colspan="4">Sin incidentes abiertos.</td></tr>'}</tbody>
                    </table>
                </div>
                <div class="workspace-card">
                    <div class="workspace-section-title">Venue Ops</div>
                    <div class="workspace-section-subtitle">Sedes, canchas, partidos abiertos, fases y checklist operativo.</div>
                    <table class="sports-table" style="margin-top:12px;">
                        <thead><tr><th>Sede/cancha</th><th>Partidos</th><th>Abiertos</th><th>Fases</th></tr></thead>
                        <tbody>{venue_rows or '<tr><td colspan="4">Sin sedes visibles.</td></tr>'}</tbody>
                    </table>
                </div>
            </section>
            <section class="workspace-card" style="margin-bottom:18px;">
                <div class="workspace-section-title">Post-tournament report</div>
                <div class="workspace-section-subtitle">Paquete ejecutivo final para dirección, patrocinadores y operación.</div>
                <div style="display:flex;flex-wrap:wrap;gap:8px;margin-top:12px;">
                    {''.join(f'<span class="sports-chip">{escape(str(section))}</span>' for section in list(post_tournament_report.get("sections") or []))}
                </div>
            </section>
            <section class="workspace-card">
                <div class="workspace-section-title">Módulos activados</div>
                <div class="workspace-section-subtitle">Esta primera capa sports queda conectada al snapshot canónico y lista para profundizar flujos de escritura controlada.</div>
                <div style="display:flex;flex-wrap:wrap;gap:8px;margin-top:12px;">
                    {''.join(f'<span class="sports-chip">{escape(label)}</span>' for label in ["Action Queue", "One-click Ops Brief", "Mission Control", "Command Center", "Team Journey", "Match Center", "Readiness global", "Ops Copilot", "Public microsite", "Sponsor/Media", "Incident Center", "Venue Ops", "Post-tournament report", "Portal equipos", "Roster inteligente", "Matchday Ops", "Comunicación oficial", "Risk Radar", "Sports CRM", "Fan/Public Layer", "Mobile field app", "AI Ops Assistant"])}
                </div>
            </section>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html)


@router.get("/admin/finanzas", response_class=HTMLResponse)
async def admin_finance_platform(
    request: Request,
    current_empleado: Empleado = Depends(get_current_empleado),
    session: AsyncSession = Depends(get_db_session),
    year: Optional[int] = Query(None),
    month: Optional[int] = Query(None),
):
    """Finance command center over gastos, pagos, COI, DIOT and polizas."""
    from samchat.finance_platform import (
        build_finance_platform_snapshot,
        build_finance_source_snapshot,
    )

    error_html = ""
    snapshot: dict[str, Any] = {}
    platform: dict[str, Any] = {"ok": False}
    try:
        snapshot = await build_finance_source_snapshot(
            session,
            year=year,
            month=month,
            limit=300,
        )
        platform = build_finance_platform_snapshot(snapshot)
    except Exception as exc:
        error_html = f"""
            <div style="margin:16px 0;padding:14px;border:1px solid #fecaca;border-radius:14px;background:#fef2f2;color:#991b1b;">
                No se pudo construir Finanzas: {escape(str(exc)[:220])}
            </div>
        """

    query_params = getattr(request, "query_params", {}) or {}
    query_success = (query_params.get("success_msg") or "").strip()
    query_error = (query_params.get("error_msg") or "").strip()
    feedback_html = ""
    if query_success:
        feedback_html += f"""
            <div style="margin:16px 0;padding:14px;border:1px solid #bbf7d0;border-radius:14px;background:#f0fdf4;color:#166534;">
                {escape(query_success)}
            </div>
        """
    if query_error:
        feedback_html += f"""
            <div style="margin:16px 0;padding:14px;border:1px solid #fecaca;border-radius:14px;background:#fef2f2;color:#991b1b;">
                {escape(query_error)}
            </div>
        """

    period = platform.get("period") or snapshot.get("period") or {}
    summary = platform.get("summary") or {}
    action_queue = platform.get("action_queue") or {}
    finance_brief = platform.get("finance_brief") or {}
    cash_control = platform.get("cash_control_center") or {}
    accounting_close = platform.get("accounting_close_center") or {}
    tax_readiness = platform.get("tax_readiness") or {}
    payment_run = platform.get("payment_run") or {}
    finance_copilot = platform.get("finance_copilot") or {}
    actions = list(action_queue.get("actions") or [])
    payable_items = list(payment_run.get("items") or [])
    pending_coi_expenses = list(accounting_close.get("pending_coi_expenses") or [])
    unbalanced_polizas = list(accounting_close.get("unbalanced_polizas") or [])
    tax_blockers = list(tax_readiness.get("blockers") or [])
    cross_month_receipts = list(tax_readiness.get("cross_month_receipts") or [])
    prompts = list(finance_copilot.get("suggested_prompts") or [])
    account_rows: list[CuentaContable] = []
    try:
        accounts_result = await session.execute(
            select(CuentaContable)
            .where(CuentaContable.activo.is_(True))
            .order_by(CuentaContable.codigo)
        )
        account_rows = list(accounts_result.scalars().all())
    except Exception:
        account_rows = []

    def _account_options(selected_id: Any = None) -> str:
        selected = str(selected_id or "")
        options = ['<option value="">-- seleccionar --</option>']
        for account in account_rows[:500]:
            account_id = str(account.id)
            is_selected = " selected" if selected == account_id else ""
            options.append(
                f'<option value="{escape(account_id)}"{is_selected}>'
                f"{escape(str(account.codigo or ''))} · {escape(str(account.nombre or ''))}"
                f"</option>"
            )
        return "".join(options)

    action_rows = "".join(
        f"""
        <tr>
            <td><span class="finance-pill finance-{escape(str(item.get("severity") or "low"))}">{escape(str(item.get("severity") or "-"))}</span></td>
            <td>{escape(str(item.get("title") or "-"))}</td>
            <td>{escape(str(item.get("module") or "-"))}</td>
            <td>{escape(str(item.get("owner") or "-"))}</td>
            <td>{escape(str(item.get("due") or "-"))}</td>
            <td>{escape(str(item.get("detail") or "-"))}</td>
        </tr>
        """
        for item in actions[:20]
    )
    payable_rows = "".join(
        f"""
        <tr>
            <td><input type="checkbox" name="document_ids" value="{escape(str(item.get("id") or ""))}"></td>
            <td>{escape(str(item.get("numero_referencia") or item.get("id") or "-"))}</td>
            <td>{escape(str(item.get("tipo") or "-"))}</td>
            <td>{escape(str(item.get("beneficiario_nombre") or item.get("proveedor_nombre") or "-"))}</td>
            <td>${float(item.get("monto_total") or item.get("monto_solicitado") or 0):,.2f}</td>
            <td>{escape(str(item.get("fecha_pago") or item.get("aprobado_en") or "-"))[:10]}</td>
        </tr>
        """
        for item in payable_items[:12]
    )
    pending_coi_rows = "".join(
        f"""
        <tr>
            <td><input type="checkbox" name="expense_ids" value="{escape(str(item.get("id") or ""))}"></td>
            <td>{escape(str(item.get("numero_referencia") or item.get("id") or "-"))}</td>
            <td>{escape(str(item.get("concepto") or "-"))}</td>
            <td>${float(item.get("gasto_cantidad") or 0):,.2f}</td>
            <td><select name="cuenta_contable_id_{escape(str(item.get("id") or ""))}">{_account_options(item.get("cuenta_contable_id"))}</select></td>
            <td><select name="contra_cuenta_contable_id_{escape(str(item.get("id") or ""))}">{_account_options(item.get("contra_cuenta_contable_id"))}</select></td>
            <td><select name="cuenta_iva_id_{escape(str(item.get("id") or ""))}">{_account_options(item.get("cuenta_iva_id"))}</select></td>
            <td>{
                'CFDI ligado'
                if item.get("cfdi_report_id")
                else (
                    'No deducible sin CFDI'
                    if _allows_coi_without_cfdi_name(item.get("cuenta_contable_nombre"))
                    else (
                        'UUID capturado; falta ligar CFDI'
                        if item.get("cfdi_uuid_manual")
                        else 'Falta CFDI'
                    )
                )
            }</td>
        </tr>
        """
        for item in pending_coi_expenses[:12]
    )
    poliza_rows = "".join(
        f"""
        <tr>
            <td>{escape(str(item.get("tipo_poliza") or "-"))}-{escape(str(item.get("numero_poliza") or "-"))}</td>
            <td>{escape(str(item.get("beneficiario_nombre") or "-"))}</td>
            <td>${float(item.get("debe") or 0):,.2f}</td>
            <td>${float(item.get("haber") or 0):,.2f}</td>
            <td>${float(item.get("debe") or 0) - float(item.get("haber") or 0):,.2f}</td>
        </tr>
        """
        for item in unbalanced_polizas[:12]
    )
    blocker_rows = "".join(
        f"""
        <tr>
            <td><input type="checkbox" name="target_keys" value="{escape(str(item.get("entity_type") or ""))}:{escape(str(item.get("id") or ""))}"></td>
            <td>{escape(str(item.get("numero_referencia") or item.get("id") or "-"))}</td>
            <td>{escape("Documento" if item.get("entity_type") == "documento" else "Gasto")}</td>
            <td>{escape(str(item.get("estado") or item.get("estado_reembolso") or "-"))}</td>
            <td>${float(item.get("monto_total") or item.get("monto_solicitado") or item.get("gasto_cantidad") or 0):,.2f}</td>
            <td>{escape(str(item.get("beneficiario_nombre") or item.get("empleado_nombre") or item.get("proveedor_nombre") or "-"))}</td>
            <td><input type="text" name="cfdi_uuid_{escape(str(item.get("entity_type") or ""))}_{escape(str(item.get("id") or ""))}" value="{escape(str(item.get("cfdi_uuid_manual") or ""))}" placeholder="UUID CFDI"></td>
        </tr>
        """
        for item in tax_blockers[:12]
    )
    cross_month_rows = "".join(
        f"""
        <tr>
            <td>{escape(str(item.get("numero_referencia") or item.get("id") or "-"))}</td>
            <td>{escape(str(item.get("concepto") or "-"))}</td>
            <td>{escape(str(item.get("fecha") or "-"))[:10]}</td>
            <td>{escape(str(item.get("cfdi_fecha") or "-"))[:10]}</td>
            <td>{escape(str(item.get("cfdi_period_warning") or "Revisar comprobante"))}</td>
        </tr>
        """
        for item in cross_month_receipts[:12]
    )
    brief_text = escape(
        str(finance_brief.get("plain_text") or "Sin brief financiero disponible.")
    )
    current_year = int(period.get("year") or year or datetime.utcnow().year)
    current_month = int(period.get("month") or month or datetime.utcnow().month)
    quick_period_links = []
    for offset in range(0, 4):
        month_cursor = current_month - offset
        year_cursor = current_year
        while month_cursor <= 0:
            month_cursor += 12
            year_cursor -= 1
        label = (
            "Mes actual"
            if offset == 0
            else ("Mes anterior" if offset == 1 else f"Hace {offset} meses")
        )
        quick_period_links.append(
            f'<a class="button secondary" href="/admin/finanzas?year={year_cursor}&month={month_cursor}">{escape(label)} · {month_cursor:02d}/{year_cursor}</a>'
        )

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>Finanzas - Samchat</title>
        <style>
            {_admin_workspace_styles("1380px")}
            .finance-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:14px; }}
            .finance-section-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(340px,1fr)); gap:16px; }}
            .finance-table {{ width:100%; border-collapse:separate; border-spacing:0; }}
            .finance-table th, .finance-table td {{ text-align:left; padding:12px; border-bottom:1px solid #e2e8f0; vertical-align:top; }}
            .finance-table th {{ color:#64748b; font-size:11px; text-transform:uppercase; letter-spacing:.11em; background:#f8fafc; }}
            .finance-pill {{ display:inline-flex; padding:5px 9px; border-radius:999px; font-size:11px; font-weight:900; text-transform:uppercase; }}
            .finance-high {{ background:#fee2e2; color:#991b1b; }}
            .finance-medium {{ background:#fef3c7; color:#92400e; }}
            .finance-low {{ background:#dcfce7; color:#166534; }}
            input, select {{ width:100%; padding:10px 12px; border-radius:12px; border:1px solid #cbd5e1; }}
            input[type="checkbox"] {{ width:auto; }}
        </style>
    </head>
    <body>
        <div class="workspace-shell">
            {render_admin_navigation(current_empleado, "finanzas", subtitle="Finanzas: pagos, COI, DIOT, pólizas y cierre.")}
            {_render_admin_workspace_hero(
                eyebrow="Finance Platform",
                title="Command Center financiero",
                description="Una capa read-only para priorizar pagos, detectar bloqueos COI/DIOT, revisar pólizas descuadradas y generar un brief financiero sin duplicar fuentes.",
                actions_html=(
                    '<form method="GET" action="/admin/finanzas" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;align-items:end;">'
                    f'<div><label style="font-size:12px;font-weight:800;color:#475569;">Año</label><input name="year" value="{current_year}" type="number" min="2020" max="2100"></div>'
                    f'<div><label style="font-size:12px;font-weight:800;color:#475569;">Mes</label><input name="month" value="{current_month}" type="number" min="1" max="12"></div>'
                    '<button class="button" type="submit">Actualizar Finanzas</button>'
                    '</form>'
                    f'<div style="display:flex;flex-wrap:wrap;gap:8px;margin-top:12px;">{"".join(quick_period_links)}</div>'
                    f'<div style="margin-top:12px;"><a class="button secondary" href="/admin/finanzas/export.xlsx?year={current_year}&month={current_month}">Descargar Excel financiero</a></div>'
                    f'<div style="margin-top:12px;"><a class="button secondary" href="/admin/finanzas/coi-lote-consolidado.xlsx?year={current_year}&month={current_month}">Descargar COI consolidado</a></div>'
                    f'<div style="margin-top:12px;"><a class="button secondary" href="/admin/finanzas/coi-lote.zip?year={current_year}&month={current_month}">Descargar COI por póliza</a></div>'
                ),
                side_html=(
                    '<div class="eyebrow">Periodo activo</div>'
                    f'<div style="font-size:1.3rem;font-weight:900;color:#0f172a;">{current_month}/{current_year}</div>'
                    f'<div style="margin-top:8px;color:#64748b;">{int(summary.get("documents") or 0)} documentos · {int(summary.get("expenses") or 0)} gastos · {int(summary.get("polizas") or 0)} pólizas</div>'
                    '<div style="margin-top:12px;"><span class="finance-pill finance-low">read-only</span></div>'
                ),
            )}
            {error_html}
            {feedback_html}
            <section class="workspace-card" style="margin-bottom:18px;">
                <div class="workspace-section-title">Finance Action Queue</div>
                <div class="workspace-section-subtitle">Una sola cola para pagos autorizados, clasificación COI, CFDI/DIOT y pólizas descuadradas.</div>
                <div class="finance-grid" style="margin-top:14px;">
                    {_sports_card("Abiertas", action_queue.get("open_count", 0), "Acciones por cerrar")}
                    {_sports_card("Alta", action_queue.get("high_count", 0), "Impactan pago/cierre")}
                    {_sports_card("Media", action_queue.get("medium_count", 0), "Fiscal o trazabilidad")}
                    {_sports_card("Baja", action_queue.get("low_count", 0), "Seguimiento")}
                </div>
                <table class="finance-table" style="margin-top:16px;">
                    <thead><tr><th>Sev</th><th>Acción</th><th>Módulo</th><th>Responsable</th><th>Vence</th><th>Detalle</th></tr></thead>
                    <tbody>{action_rows or '<tr><td colspan="6">Sin acciones abiertas.</td></tr>'}</tbody>
                </table>
            </section>
            <section class="workspace-card" style="margin-bottom:18px;">
                <div class="workspace-section-title">One-click Finance Brief</div>
                <div class="workspace-section-subtitle">Resumen listo para WhatsApp, email o PDF ejecutivo.</div>
                <div class="finance-grid" style="margin-top:14px;">
                    {_sports_card("WhatsApp", "listo", str(finance_brief.get("whatsapp_text") or "")[:90])}
                    {_sports_card("Email", "listo", str(finance_brief.get("email_subject") or "-"))}
                    {_sports_card("PDF", "ready" if finance_brief.get("pdf_ready") else "draft", ", ".join(finance_brief.get("export_targets") or []))}
                </div>
                <pre style="white-space:pre-wrap;margin-top:14px;padding:16px;border:1px solid #dbe2ea;border-radius:16px;background:#0f172a;color:#e2e8f0;font-size:13px;line-height:1.55;">{brief_text}</pre>
            </section>
            <section class="finance-section-grid" style="margin-bottom:18px;">
                <div class="workspace-card">
                    <div class="workspace-section-title">Cash Control Center</div>
                    <div class="workspace-section-subtitle">Presión de caja por documentos autorizados y pólizas de ingreso.</div>
                    <div class="finance-grid" style="margin-top:14px;">
                        {_sports_card("Por pagar", cash_control.get("approved_unpaid_count", 0), f'${float(cash_control.get("approved_unpaid_total") or 0):,.2f}')}
                        {_sports_card("Pagado", cash_control.get("paid_documents_count", 0), f'${float(cash_control.get("paid_total") or 0):,.2f}')}
                        {_sports_card("Ingresos", cash_control.get("income_polizas_count", 0), f'${float(cash_control.get("income_total") or 0):,.2f}')}
                        {_sports_card("Presión", cash_control.get("payment_pressure", "-"), "Basado en autorizados sin pago")}
                    </div>
                </div>
                <div class="workspace-card">
                    <div class="workspace-section-title">Accounting Close Center</div>
                    <div class="workspace-section-subtitle">Preparación COI y balance debe/haber.</div>
                    <div class="finance-grid" style="margin-top:14px;">
                        {_sports_card("Pólizas", accounting_close.get("polizas_count", 0), "Periodo visible")}
                        {_sports_card("Descuadradas", accounting_close.get("unbalanced_count", 0), "Deben corregirse")}
                        {_sports_card("COI listo", accounting_close.get("coi_ready_expenses_count", 0), "Gastos con cuentas + CFDI")}
                        {_sports_card("COI pendiente", accounting_close.get("pending_coi_expenses_count", 0), "Falta cuenta/CFDI")}
                    </div>
                </div>
            </section>
            <section class="finance-section-grid" style="margin-bottom:18px;">
                <div class="workspace-card">
                    <div class="workspace-section-title">Payment Run</div>
                    <div class="workspace-section-subtitle">SOLICITUDES aprobadas todavía no pagadas. Registrar pago genera el gasto operativo con la lógica canónica.</div>
                    <div class="finance-grid" style="margin-top:14px;">
                        {_sports_card("Items", payment_run.get("payable_count", 0), "Autorizados sin pago")}
                        {_sports_card("Total", f'${float(payment_run.get("payable_total") or 0):,.2f}', str(payment_run.get("next_step") or "-"))}
                    </div>
                    <form method="POST" action="/admin/finanzas/payment-run/pay" style="margin-top:16px;">
                        <input type="hidden" name="year" value="{current_year}">
                        <input type="hidden" name="month" value="{current_month}">
                        <table class="finance-table">
                            <thead><tr><th></th><th>Referencia</th><th>Tipo</th><th>Beneficiario</th><th>Monto</th><th>Fecha</th></tr></thead>
                            <tbody>{payable_rows or '<tr><td colspan="6">Sin pagos pendientes.</td></tr>'}</tbody>
                        </table>
                        <button class="button" type="submit" style="margin-top:12px;" {'disabled' if not payable_items else ''}>Registrar seleccionados como pagados</button>
                    </form>
                </div>
                <div class="workspace-card">
                    <div class="workspace-section-title">COI pendientes</div>
                    <div class="workspace-section-subtitle">Completa cuenta y contracuenta del gasto desde Finanzas. El CFDI sigue siendo requisito fiscal separado.</div>
                    <form method="POST" action="/admin/finanzas/coi-pendientes/clasificar" style="margin-top:16px;">
                        <input type="hidden" name="year" value="{current_year}">
                        <input type="hidden" name="month" value="{current_month}">
                        <table class="finance-table">
                            <thead><tr><th></th><th>Gasto</th><th>Concepto</th><th>Monto</th><th>Cuenta</th><th>Contracuenta</th><th>Cuenta IVA</th><th>Fiscal</th></tr></thead>
                            <tbody>{pending_coi_rows or '<tr><td colspan="8">Sin gastos pendientes de clasificación COI.</td></tr>'}</tbody>
                        </table>
                        <button class="button" type="submit" style="margin-top:12px;" {'disabled' if not pending_coi_expenses or not account_rows else ''}>Guardar clasificación COI</button>
                    </form>
                </div>
            </section>
            <section class="finance-section-grid" style="margin-bottom:18px;">
                <div class="workspace-card">
                    <div class="workspace-section-title">Tax Readiness</div>
                    <div class="workspace-section-subtitle">Bloqueos DIOT/CFDI y atención especial para AMEX/propinas.</div>
                    <div class="finance-grid" style="margin-top:14px;">
                        {_sports_card("CFDI faltante", tax_readiness.get("cfdi_missing_count", 0), "Bloquean DIOT")}
                        {_sports_card("DIOT blockers", tax_readiness.get("diot_blockers_count", 0), str(tax_readiness.get("status") or "-"))}
                        {_sports_card("Otro mes", tax_readiness.get("cross_month_receipts_count", 0), "CFDI fuera del mes del gasto")}
                        {_sports_card("AMEX", tax_readiness.get("amex_rows_count", 0), "Revisar voucher vs factura")}
                        {_sports_card("Propina inferible", tax_readiness.get("amex_tip_attention_count", 0), "Voucher - consumo - IVA")}
                    </div>
                    <form method="POST" action="/admin/finanzas/diot-blockers/link-cfdi" style="margin-top:16px;">
                        <input type="hidden" name="year" value="{current_year}">
                        <input type="hidden" name="month" value="{current_month}">
                        <table class="finance-table">
                            <thead><tr><th></th><th>Referencia</th><th>Tipo</th><th>Estado</th><th>Monto</th><th>Persona/proveedor</th><th>UUID CFDI</th></tr></thead>
                            <tbody>{blocker_rows or '<tr><td colspan="7">Sin bloqueos fiscales visibles.</td></tr>'}</tbody>
                        </table>
                        <button class="button" type="submit" style="margin-top:12px;" {'disabled' if not tax_blockers else ''}>Amarrar CFDI para DIOT</button>
                    </form>
                    <div style="margin-top:18px;">
                        <div class="workspace-section-subtitle">Warnings cuando el comprobante pertenece a otro mes que el gasto.</div>
                        <table class="finance-table" style="margin-top:12px;">
                            <thead><tr><th>Gasto</th><th>Concepto</th><th>Fecha gasto</th><th>Fecha CFDI</th><th>Warning</th></tr></thead>
                            <tbody>{cross_month_rows or '<tr><td colspan="5">Sin comprobantes cruzados entre meses.</td></tr>'}</tbody>
                        </table>
                    </div>
                </div>
            </section>
            <section class="finance-section-grid" style="margin-bottom:18px;">
                <div class="workspace-card">
                    <div class="workspace-section-title">Pólizas descuadradas</div>
                    <div class="workspace-section-subtitle">Debe/haber que impide cierre limpio para COI.</div>
                    <table class="finance-table" style="margin-top:16px;">
                        <thead><tr><th>Póliza</th><th>Beneficiario</th><th>Debe</th><th>Haber</th><th>Diferencia</th></tr></thead>
                        <tbody>{poliza_rows or '<tr><td colspan="5">Sin pólizas descuadradas.</td></tr>'}</tbody>
                    </table>
                </div>
                <div class="workspace-card">
                    <div class="workspace-section-title">Finance Copilot</div>
                    <div class="workspace-section-subtitle">Preguntas útiles para operar el cierre y no perder trazabilidad.</div>
                    <div style="margin-top:12px;">{_sports_list(prompts, "Sin prompts sugeridos.")}</div>
                </div>
            </section>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html)


@router.get("/admin/sam-inbox", response_class=HTMLResponse)
async def admin_sam_inbox(
    request: Request,
    tab: Optional[str] = Query(default="todo"),
    severity: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    source_type: Optional[str] = Query(default=None),
    module: Optional[str] = Query(default=None),
    current_empleado: Empleado = Depends(get_current_empleado),
    session: AsyncSession = Depends(get_db_session),
) -> str:
    if not _can_access_sam_inbox(current_empleado):
        raise HTTPException(
            status_code=403,
            detail="Sam Inbox requiere permisos de operaciones, finanzas o administración.",
        )

    active_tab = (tab or "todo").strip().lower() or "todo"
    if active_tab == "direccion" and not (
        (getattr(current_empleado, "rol", "") or "").strip().lower()
        in {"admin", "superadmin", "super_admin"}
    ):
        active_tab = "todo"

    payload = await build_sam_inbox_payload(
        session,
        current_empleado=current_empleado,
        tab=active_tab,
        severity=severity,
        status=status,
        source_type=source_type,
        module=module,
    )

    tabs = payload.get("tabs") or []
    items = payload.get("items") or []
    source_health = payload.get("source_health") or {}
    direction = payload.get("direction") or {}
    active_filters = payload.get("filters") or {}
    finance_brief = direction.get("finance_brief") or {}
    cash_control = direction.get("cash_control_center") or {}
    current_period = direction.get("current_period") or {}
    ytd = direction.get("ytd") or {}
    alerts = list((direction.get("executive_alerts") or {}).get("alerts") or [])
    limitations = list(direction.get("limitations") or [])

    tab_links = []
    for item in tabs:
        key = str(item.get("key") or "")
        params = [f"tab={quote(key)}"]
        if active_filters.get("severity"):
            params.append(f"severity={quote(str(active_filters['severity']))}")
        if active_filters.get("status"):
            params.append(f"status={quote(str(active_filters['status']))}")
        if active_filters.get("source_type"):
            params.append(f"source_type={quote(str(active_filters['source_type']))}")
        if active_filters.get("module"):
            params.append(f"module={quote(str(active_filters['module']))}")
        href = "/admin/sam-inbox?" + "&".join(params)
        active = key == payload.get("tab")
        tab_links.append(
            f"""
            <a href="{href}" style="
                text-decoration:none;
                padding:10px 14px;
                border-radius:999px;
                border:1px solid {'rgba(15,118,110,.28)' if active else '#dbe2ea'};
                background:{'#0f766e' if active else '#ffffff'};
                color:{'#f8fafc' if active else '#0f172a'};
                font-weight:800;
                font-size:13px;
            ">{escape(str(item.get('label') or key))} <span style="opacity:.82;">({int(item.get('count') or 0)})</span></a>
            """
        )

    filter_source_options = [
        ("", "Todos los orígenes"),
        ("finance_action", "Finance action queue"),
        ("pending_payment", "Pagos pendientes"),
        ("cfdi_matching", "CFDI matching"),
        ("tournament_pending", "Operaciones pendientes"),
        ("tournament_risk", "Riesgos de torneo"),
        ("executive_alert", "Alertas ejecutivas"),
    ]
    filter_status_options = [
        ("", "Todos los estados"),
        ("needs_attention", "needs_attention"),
        ("pending", "pending"),
        ("ready", "ready"),
        ("done", "done"),
        ("blocked", "blocked"),
        ("info", "info"),
    ]
    filter_severity_options = [
        ("", "Todas las prioridades"),
        ("high", "high"),
        ("medium", "medium"),
        ("low", "low"),
    ]
    filters_html = f"""
    <section class="workspace-card" style="margin-bottom:18px;">
        <form method="GET" action="/admin/sam-inbox" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;align-items:end;">
            <input type="hidden" name="tab" value="{escape(str(payload.get('tab') or 'todo'))}">
            <label style="font-size:12px;font-weight:700;color:#475569;">
                Prioridad
                <select name="severity" style="margin-top:6px;width:100%;padding:10px 12px;border:1px solid #cbd5e1;border-radius:12px;">
                    {''.join(f'<option value="{escape(value)}"' + (' selected' if active_filters.get('severity') == value else '') + f'>{escape(label)}</option>' for value, label in filter_severity_options)}
                </select>
            </label>
            <label style="font-size:12px;font-weight:700;color:#475569;">
                Estado
                <select name="status" style="margin-top:6px;width:100%;padding:10px 12px;border:1px solid #cbd5e1;border-radius:12px;">
                    {''.join(f'<option value="{escape(value)}"' + (' selected' if active_filters.get('status') == value else '') + f'>{escape(label)}</option>' for value, label in filter_status_options)}
                </select>
            </label>
            <label style="font-size:12px;font-weight:700;color:#475569;">
                Origen
                <select name="source_type" style="margin-top:6px;width:100%;padding:10px 12px;border:1px solid #cbd5e1;border-radius:12px;">
                    {''.join(f'<option value="{escape(value)}"' + (' selected' if active_filters.get('source_type') == value else '') + f'>{escape(label)}</option>' for value, label in filter_source_options)}
                </select>
            </label>
            <label style="font-size:12px;font-weight:700;color:#475569;">
                Módulo
                <input type="text" name="module" value="{escape(str(active_filters.get('module') or ''))}" placeholder="Pagos, COI, Operaciones..." style="margin-top:6px;width:100%;padding:10px 12px;border:1px solid #cbd5e1;border-radius:12px;">
            </label>
            <div style="display:flex;gap:10px;flex-wrap:wrap;">
                <button type="submit" class="button">Filtrar</button>
                <a href="/admin/sam-inbox?tab={escape(str(payload.get('tab') or 'todo'))}" class="button secondary">Limpiar</a>
            </div>
        </form>
    </section>
    """

    source_notes_html = "".join(
        f"""
        <div style="padding:12px 14px;border-radius:14px;border:1px solid #fcd34d;background:#fffbeb;color:#92400e;font-size:13px;margin-bottom:12px;">
            <strong>{escape(name)}</strong>: {escape(str(state.get('message') or 'No disponible en esta carga'))}
        </div>
        """
        for name, state in source_health.items()
        if not state.get("ok")
    )

    item_cards = []
    for item in items:
        severity_label = escape(str(item.get("severity") or "low"))
        status_label = escape(str(item.get("status") or "info"))
        href = item.get("href")
        primary_link = (
            f'<a href="{escape(str(href))}" class="button secondary">Abrir módulo</a>'
            if href
            else '<span class="button secondary" style="pointer-events:none;opacity:.72;">Detalle inline</span>'
        )
        secondary_href = item.get("secondary_href")
        secondary_link = (
            f'<a href="{escape(str(secondary_href))}" class="button secondary">{escape(str(item.get("secondary_label") or "Preguntar a Sam"))}</a>'
            if secondary_href
            else ""
        )
        prepare_label = escape(
            str((item.get("prepared_action") or {}).get("label") or "Revisar detalle")
        )
        source_ref = item.get("source_ref") or {}
        ref_bits = []
        for key in ("expense_id", "document_id", "tournament_id", "commitment_id"):
            value = source_ref.get(key)
            if value:
                ref_bits.append(f"{key}: {escape(str(value))}")
        refs_html = (
            f'<div style="margin-top:10px;font-size:12px;color:#64748b;">{" · ".join(ref_bits)}</div>'
            if ref_bits
            else ""
        )
        item_cards.append(
            f"""
            <article class="workspace-card" style="border-left:4px solid {'#b91c1c' if severity_label == 'high' else '#d97706' if severity_label == 'medium' else '#0f766e'};">
                <div style="display:flex;justify-content:space-between;gap:14px;align-items:flex-start;flex-wrap:wrap;">
                    <div>
                        <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:8px;">
                            <span style="padding:4px 8px;border-radius:999px;background:#e2e8f0;color:#0f172a;font-size:11px;font-weight:800;text-transform:uppercase;">{escape(str(item.get('domain') or ''))}</span>
                            <span style="padding:4px 8px;border-radius:999px;background:#f8fafc;border:1px solid #dbe2ea;color:#334155;font-size:11px;font-weight:700;">{severity_label}</span>
                            <span style="padding:4px 8px;border-radius:999px;background:#f8fafc;border:1px solid #dbe2ea;color:#334155;font-size:11px;font-weight:700;">{status_label}</span>
                            <span style="font-size:11px;color:#64748b;">{escape(str(item.get('source_type') or ''))}</span>
                        </div>
                        <h3 style="margin:0 0 8px;font-size:18px;line-height:1.25;color:#0f172a;">{escape(str(item.get('title') or 'Item'))}</h3>
                        <div style="font-size:13px;color:#475569;line-height:1.6;">{escape(str(item.get('detail') or ''))}</div>
                        <div style="margin-top:10px;font-size:12px;color:#0f766e;font-weight:700;">Siguiente paso canónico: {prepare_label}</div>
                        {refs_html}
                    </div>
                    <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:flex-start;">
                        {primary_link}
                        {secondary_link}
                    </div>
                </div>
            </article>
            """
        )
    if not item_cards:
        item_cards.append(
            """
            <section class="workspace-card" style="padding:28px;text-align:center;color:#64748b;">
                No hay items para esos filtros. La bandeja sigue siendo stateless y sólo consume read models existentes.
            </section>
            """
        )

    direction_html = ""
    if payload.get("tab") == "direccion" and direction:
        brief_text = escape(
            str(finance_brief.get("plain_text") or "Brief no disponible.")
        ).replace("\n", "<br>")
        alert_html = "".join(
            f"<li style='margin-bottom:8px;'><strong>{escape(str(alert.get('title') or 'Alerta'))}</strong>: {escape(str(alert.get('detail') or ''))}</li>"
            for alert in alerts[:5]
        ) or "<li>Sin alertas ejecutivas activas en esta carga.</li>"
        limitation_html = "".join(
            f"<li>{escape(str(note))}</li>" for note in limitations
        )
        direction_html = f"""
        <section class="finance-grid" style="margin-bottom:18px;">
            {_sports_card("Periodo actual", f"{escape(str((current_period.get('period') or {}).get('month') or 'N/D'))}/{escape(str((current_period.get('period') or {}).get('year') or 'N/D'))}", f"Acciones abiertas: {escape(str((current_period.get('summary') or {}).get('open_actions') or 0))}")}
            {_sports_card("YTD", f"${_safe_money(ytd.get('total'))}", f"Run rate: ${_safe_money(ytd.get('run_rate_projection'))}")}
            {_sports_card("Pagos por liberar", f"${_safe_money(cash_control.get('approved_unpaid_total'))}", f"{escape(str(cash_control.get('approved_unpaid_count') or 0))} documentos")}
        </section>
        <section class="workspace-card" style="margin-bottom:18px;">
            <div class="workspace-section-title">Finance brief</div>
            <div class="workspace-section-subtitle">Fuente: finance_brief + cash_control_center existentes.</div>
            <div style="margin-top:10px;font-size:14px;line-height:1.7;color:#334155;">{brief_text}</div>
        </section>
        <section class="workspace-card" style="margin-bottom:18px;">
            <div class="workspace-section-title">Alertas ejecutivas</div>
            <div class="workspace-section-subtitle">Fuente: executive alerts actuales, sin crear lógica nueva.</div>
            <ul style="margin:12px 0 0 18px;color:#334155;line-height:1.6;">{alert_html}</ul>
        </section>
        <section class="workspace-card" style="margin-bottom:18px;">
            <div class="workspace-section-title">Límites de S1</div>
            <ul style="margin:12px 0 0 18px;color:#334155;line-height:1.6;">{limitation_html}</ul>
        </section>
        """

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Sam Inbox - sam.chat</title>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <meta charset="utf-8">
        <style>
            {_admin_workspace_styles("1480px")}
            .stack {{ display:flex; flex-direction:column; gap:16px; }}
            .sam-inbox-panel {{
                background:#ffffff;
                border:1px solid var(--shell-line);
                border-radius:22px;
                padding:20px;
                box-shadow:0 12px 30px rgba(15,23,42,.06);
            }}
            .sam-inbox-panel .workspace-card {{
                background:#ffffff;
                border:1px solid var(--shell-line);
                border-radius:18px;
                padding:18px;
            }}
        </style>
    </head>
    <body>
        <div class="workspace-shell">
            {render_admin_navigation(current_empleado, "sam_inbox", subtitle="Bandeja operativa y financiera stateless sobre read models existentes, sin crear writes nuevos.")}
            {_render_admin_workspace_hero(
                eyebrow="Product Spine S1",
                title="Sam Inbox",
                description="Centro operativo read-only/read-mostly para Operaciones, Finanzas y Dirección. El assistant queda como copiloto contextual, no como contenedor principal.",
                actions_html='<a href="/assistant" class="button secondary">Preguntar a Sam</a><a href="/admin/finanzas" class="button secondary">Abrir Finanzas</a>',
                side_html='''
                    <div class="eyebrow">Modo</div>
                    <div style="margin-top:8px;font-size:13px;color:#475569;line-height:1.6;">
                        Ruta aislada, sin tablas nuevas, sin POST/PATCH y sin deep links a APIs crudas.
                    </div>
                ''',
            )}
            <div class="sam-inbox-panel">
                <section class="workspace-card" style="margin-bottom:18px;">
                    <div style="display:flex;gap:8px;flex-wrap:wrap;">{''.join(tab_links)}</div>
                </section>
                {filters_html}
                {source_notes_html}
                {direction_html}
                <section class="stack">
                    {''.join(item_cards)}
                </section>
            </div>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html)


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


@router.post("/admin/finanzas/payment-run/pay")
async def admin_finance_payment_run_pay(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = require_admin_finanzas(),
    document_ids: Optional[List[str]] = Form(None),
    year: Optional[int] = Form(None),
    month: Optional[int] = Form(None),
) -> RedirectResponse:
    """Register selected approved SOLICITUDES as paid from Finance Platform."""
    from devnous.gastos.services.documento_payment_service import (
        DocumentoPaymentPermissionError,
        DocumentoPaymentValidationError,
        register_document_payment,
    )

    ids = [str(item).strip() for item in document_ids or [] if str(item).strip()]
    base_url = "/admin/finanzas"
    query_parts = []
    if year:
        query_parts.append(f"year={int(year)}")
    if month:
        query_parts.append(f"month={int(month)}")
    redirect_url = base_url + (("?" + "&".join(query_parts)) if query_parts else "")
    separator = "&" if "?" in redirect_url else "?"

    if not ids:
        return RedirectResponse(
            url=(
                f"{redirect_url}{separator}"
                f"error_msg={quote('Selecciona al menos una SOLICITUD aprobada.')}"
            ),
            status_code=303,
        )

    paid_refs: list[str] = []
    failures: list[str] = []
    for documento_id in ids:
        try:
            result = await register_document_payment(
                session,
                documento_id=documento_id,
                actor_id=current_empleado.id,
            )
            paid_refs.append(
                str(
                    result.documento.numero_referencia
                    or result.documento.id
                    or documento_id
                )
            )
        except DocumentoPaymentPermissionError as exc:
            raise HTTPException(status_code=403, detail=exc.message)
        except DocumentoPaymentValidationError as exc:
            failures.append(f"{documento_id}: {exc.message}")
        except ValueError as exc:
            failures.append(f"{documento_id}: ID inválido ({exc})")
        except Exception:
            await session.rollback()
            logger.exception(
                "Unexpected error registering payment from finance payment run",
                extra={
                    "documento_id": str(documento_id),
                    "actor_id": str(current_empleado.id),
                },
            )
            failures.append(
                f"{documento_id}: Error inesperado al registrar el pago"
            )

    if paid_refs and not failures:
        message = f"Pagos registrados: {', '.join(paid_refs)}."
        return RedirectResponse(
            url=f"{redirect_url}{separator}success_msg={quote(message)}",
            status_code=303,
        )
    if paid_refs and failures:
        message = (
            f"Pagos registrados: {', '.join(paid_refs)}. "
            f"Pendientes: {'; '.join(failures[:3])}"
        )
        return RedirectResponse(
            url=f"{redirect_url}{separator}success_msg={quote(message)}",
            status_code=303,
        )
    return RedirectResponse(
        url=(
            f"{redirect_url}{separator}"
            f"error_msg={quote('No se registró ningún pago. ' + '; '.join(failures[:3]))}"
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


@router.get("/admin/torneos", response_class=HTMLResponse)
async def admin_tournaments(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = Depends(get_current_empleado),
):
    """Admin interface for managing gastos projects and linked tournaments."""
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
                            <button type="submit" class="btn btn-primary">Crear proyecto</button>
                        </form>
                    </div>
                    <div class="creation-card">
                        <h3>Crear proyecto desde torneo existente</h3>
                        <p>Crea un proyecto local usando un torneo ya existente en la app de Torneos y deja la liga guardada desde el inicio.</p>
                        <form method="POST" action="/admin/torneos/create/from-torneo">
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
                            <button type="submit" class="btn btn-primary" {'disabled' if not operations_tournaments else ''}>Crear desde torneo</button>
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
                                <button type="submit" class="btn btn-secondary btn-small">{'Desactivar' if tournament.active else 'Activar'}</button>
                            </form>
                        </div>
                    </div>
                    <div class="link-panel">
                        <div style="font-weight: 600; margin-bottom: 8px; color: #333;">Ligar proyecto</div>
                        {"<div class='helper-text'>No se pudo cargar el catálogo remoto de Torneos en este momento.</div>" if operations_error else f'''
                        <form method="POST" action="/admin/torneos/link/{tournament.id}" class="link-form">
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
    current_empleado: Empleado = require_admin_finanzas(),
):
    schema_probe = await _probe_tournaments_v2_domain_schema()
    return _render_tournaments_domain_alignment_page(
        current_empleado=current_empleado,
        schema_probe=schema_probe,
    )


@router.post("/admin/torneos/domain-alignment/run", response_class=HTMLResponse)
async def admin_tournaments_domain_alignment_run(
    mode: str = Form("dry_run"),
    scope: str = Form(ACTIVE_TOURNAMENT_SCOPE),
    tournament_slug: str = Form(""),
    team_limit: int = Form(0),
    current_empleado: Empleado = require_admin_finanzas(),
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
    scope: str = Form(ACTIVE_TOURNAMENT_SCOPE),
    tournament_slug: str = Form(""),
    team_limit: int = Form(0),
    current_empleado: Empleado = require_admin_finanzas(),
):
    scope_value = (
        scope or ACTIVE_TOURNAMENT_SCOPE
    ).strip().lower() or ACTIVE_TOURNAMENT_SCOPE
    slug_value = (tournament_slug or "").strip()
    limit_value = max(0, int(team_limit or 0))
    schema_probe = await _probe_tournaments_v2_domain_schema()

    repo_root = _repo_root()
    script_path = repo_root / "scripts" / "audit_tournaments_v2_alignment.py"
    cmd = [sys.executable, str(script_path), "--scope", scope_value]
    if slug_value:
        cmd.extend(["--tournament-slug", slug_value])
    if limit_value > 0:
        cmd.extend(["--team-limit", str(limit_value)])

    stdout_text = ""
    stderr_text = ""
    audit_summary: Optional[dict[str, Any]] = None
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
                current_empleado=current_empleado,
                schema_probe=schema_probe,
                scope=scope_value,
                tournament_slug=slug_value,
                team_limit=limit_value,
                error_msg="La auditoría excedió el timeout de 240s.",
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
                audit_summary = json.loads(stdout_text)
            except Exception:
                audit_summary = None
    except Exception as exc:
        return _render_tournaments_domain_alignment_page(
            current_empleado=current_empleado,
            schema_probe=schema_probe,
            scope=scope_value,
            tournament_slug=slug_value,
            team_limit=limit_value,
            error_msg=f"No se pudo ejecutar la auditoría: {exc}",
        )

    output_parts = []
    if stdout_text:
        output_parts.append(stdout_text)
    if stderr_text:
        output_parts.append(f"STDERR:\n{stderr_text}")
    if not output_parts:
        output_parts.append("(sin salida)")

    return _render_tournaments_domain_alignment_page(
        current_empleado=current_empleado,
        schema_probe=schema_probe,
        scope=scope_value,
        tournament_slug=slug_value,
        team_limit=limit_value,
        audit_output="\n\n".join(output_parts),
        audit_summary=audit_summary,
        audit_returncode=return_code,
        error_msg=(
            None
            if (return_code == 0 or audit_summary is not None)
            else "La auditoría devolvió error."
        ),
    )


def _parse_newline_delimited_list(raw: Optional[str]) -> Optional[list]:
    """Parse a textarea value (one item per line) into a list of strings, or None."""
    if not raw or not raw.strip():
        return None
    lines = [s.strip() for s in raw.strip().splitlines() if s.strip()]
    return lines if lines else None


def _parse_etapas_from_form(etapas_raw: Optional[str]) -> Optional[list]:
    """Parse etapas form value (one per line) into a list of non-empty strings, or None."""
    return _parse_newline_delimited_list(etapas_raw)


@router.post("/admin/torneos/create")
async def create_tournament(
    request: Request,
    name: str = Form(...),
    description: Optional[str] = Form(None),
    display_order: int = Form(0),
    cuenta_contable_relacionada: Optional[str] = Form(None),
    etapas: Optional[str] = Form(None),
    categorias: Optional[str] = Form(None),
    active: Optional[str] = Form(None),
    session: AsyncSession = Depends(get_db_session),
):
    """Create a local gastos project/tournament manually."""
    try:
        await _ensure_tournaments_admin_schema(session)
        form_data = await request.form()
        is_active = form_data.get("active") == "on"
        visibility_areas = parse_form_visibility_areas_from_form(
            form_data.getlist("form_visibility_areas")
            if hasattr(form_data, "getlist")
            else form_data.get("form_visibility_areas")
        )
        normalized_name = (name or "").strip()
        if not normalized_name:
            return _admin_torneos_redirect(
                error_msg="El nombre del proyecto es requerido."
            )
        existing = await _find_local_tournament_by_name(session, normalized_name)
        if existing:
            return _admin_torneos_redirect(
                error_msg=f"Ya existe un proyecto en gastos con el nombre {normalized_name}."
            )
        tournament = Tournament(
            name=normalized_name,
            description=(description or "").strip() or None,
            display_order=max(0, int(display_order or 0)),
            cuenta_contable_relacionada=(cuenta_contable_relacionada or "").strip()
            or None,
            etapas=_parse_etapas_from_form(etapas),
            categorias=_parse_newline_delimited_list(categorias),
            form_visibility_areas=visibility_areas or None,
            active=is_active,
        )
        session.add(tournament)
        await session.commit()
        return _admin_torneos_redirect(
            success_msg=f"Proyecto {normalized_name} creado correctamente."
        )
    except IntegrityError:
        await session.rollback()
        return _admin_torneos_redirect(
            error_msg=f"Ya existe un proyecto en gastos con el nombre {normalized_name}."
        )
    except Exception as e:
        await session.rollback()
        logger.error(f"Error creating tournament: {e}")
        return _admin_torneos_redirect(error_msg=f"No se pudo crear el proyecto: {e}")


@router.post("/admin/torneos/create/from-torneo")
async def create_tournament_from_operations(
    request: Request,
    linked_operations_tournament_id: str = Form(...),
    display_order: int = Form(-1),
    active: Optional[str] = Form(None),
    session: AsyncSession = Depends(get_db_session),
):
    """Create a local gastos project from an existing operations tournament."""
    try:
        await _ensure_tournaments_admin_schema(session)
        form_data = await request.form()
        is_active = form_data.get("active") == "on"
        visibility_areas = parse_form_visibility_areas_from_form(
            form_data.getlist("form_visibility_areas")
            if hasattr(form_data, "getlist")
            else form_data.get("form_visibility_areas")
        )
        operations_tournaments, operations_error = await _load_operations_tournaments()
        if operations_error:
            return _admin_torneos_redirect(
                error_msg=f"No se pudo consultar la app Torneos: {operations_error}"
            )
        selected = _find_operations_tournament(
            operations_tournaments,
            linked_operations_tournament_id,
        )
        if not selected:
            return _admin_torneos_redirect(
                error_msg="Selecciona un torneo válido de la app Torneos."
            )
        existing = await _find_local_tournament_by_name(
            session,
            str(selected.get("name") or ""),
        )
        if existing:
            return _admin_torneos_redirect(
                error_msg=(
                    f"Ya existe un proyecto local con el nombre "
                    f"{selected.get('name') or 'seleccionado'}."
                )
            )
        resolved_order = (
            await _next_tournament_display_order(session)
            if int(display_order or -1) < 0
            else max(0, int(display_order or 0))
        )
        tournament = Tournament(
            name=str(selected.get("name") or "").strip(),
            description=str(selected.get("description") or "").strip() or None,
            display_order=resolved_order,
            form_visibility_areas=visibility_areas or DEFAULT_OPERATIONS_ONLY_VISIBILITY,
            active=is_active,
        )
        session.add(tournament)
        await session.flush()
        session.add(
            TournamentOperationsLink(
                tournament_id=tournament.id,
                operations_tournament_id=str(selected.get("id") or "").strip(),
                operations_tournament_slug=str(selected.get("slug") or "").strip()
                or None,
            )
        )
        await session.commit()
        return _admin_torneos_redirect(
            success_msg=(
                f"Proyecto {tournament.name} creado y ligado al torneo "
                f"{selected.get('slug') or selected.get('name') or ''}."
            )
        )
    except Exception as exc:
        await session.rollback()
        logger.error("Error creating local project from operations tournament: %s", exc)
        return _admin_torneos_redirect(
            error_msg=f"No se pudo crear el proyecto desde Torneos: {exc}"
        )


@router.post("/admin/torneos/link/{tournament_id}")
async def link_tournament_to_operations(
    tournament_id: UUIDType,
    linked_operations_tournament_id: Optional[str] = Form(None),
    session: AsyncSession = Depends(get_db_session),
):
    """Link or unlink a local gastos project to an operations tournament."""
    await _ensure_tournaments_admin_schema(session)
    result = await session.execute(
        select(Tournament).where(Tournament.id == tournament_id)
    )
    tournament = result.scalar_one_or_none()
    if not tournament:
        raise HTTPException(status_code=404, detail="Tournament not found")
    link_result = await session.execute(
        select(TournamentOperationsLink).where(
            TournamentOperationsLink.tournament_id == tournament_id
        )
    )
    existing_link = link_result.scalar_one_or_none()

    normalized_id = _normalize_linked_operations_tournament_id(
        linked_operations_tournament_id
    )
    if not normalized_id:
        if existing_link:
            await session.delete(existing_link)
        await session.commit()
        return _admin_torneos_redirect(
            success_msg=f"Liga removida para {tournament.name}."
        )

    operations_tournaments, operations_error = await _load_operations_tournaments()
    if operations_error:
        return _admin_torneos_redirect(
            error_msg=f"No se pudo consultar la app Torneos: {operations_error}"
        )
    selected = _find_operations_tournament(operations_tournaments, normalized_id)
    if not selected:
        return _admin_torneos_redirect(
            error_msg="El torneo seleccionado ya no existe en la app Torneos."
        )

    if existing_link is None:
        existing_link = TournamentOperationsLink(
            tournament_id=tournament_id,
            operations_tournament_id=normalized_id,
        )
        session.add(existing_link)
    existing_link.operations_tournament_id = normalized_id
    existing_link.operations_tournament_slug = (
        str(selected.get("slug") or "").strip() or None
    )
    await session.commit()
    return _admin_torneos_redirect(
        success_msg=(
            f"Proyecto {tournament.name} ligado a "
            f"{selected.get('slug') or selected.get('name') or 'torneo remoto'}."
        )
    )


@router.get("/admin/torneos/edit/{tournament_id}", response_class=HTMLResponse)
async def edit_tournament_form(
    tournament_id: UUIDType,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
):
    """Edit tournament form."""
    await _ensure_tournaments_admin_schema(session)
    result = await session.execute(
        select(Tournament).where(Tournament.id == tournament_id)
    )
    tournament = result.scalar_one_or_none()

    if not tournament:
        raise HTTPException(status_code=404, detail="Tournament not found")

    html_content = f"""
    <!DOCTYPE html>
    <html lang="es">
    <head>
        <title>Editar Torneo - Copa Telmex</title>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <meta name="color-scheme" content="light">
        <style>
            * {{ box-sizing: border-box; }}
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                margin: 0;
                padding: 20px;
                min-height: 100vh;
                color: #1e293b;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            }}
            .edit-container {{
                max-width: 800px;
                margin: 0 auto;
                background: #ffffff;
                border-radius: 12px;
                box-shadow: 0 10px 40px rgba(0, 0, 0, 0.18);
                padding: 28px 30px 32px;
            }}
            h1 {{
                color: #1e293b;
                margin: 0 0 24px;
                padding-bottom: 12px;
                border-bottom: 3px solid #667eea;
                font-size: 1.5rem;
            }}
            .config-panel-back a {{
                color: #4f46e5 !important;
            }}
            .form-group {{ margin-bottom: 18px; }}
            .form-group > label:first-child {{
                display: block;
                margin-bottom: 6px;
                font-weight: 600;
                color: #334155;
            }}
            .form-group small {{
                color: #64748b;
                display: block;
                margin-top: 6px;
                line-height: 1.45;
            }}
            input[type="text"],
            input[type="number"],
            textarea {{
                width: 100%;
                padding: 10px 12px;
                border: 2px solid #cbd5e1;
                border-radius: 6px;
                font-size: 14px;
                color: #0f172a;
                background: #ffffff;
            }}
            input[type="text"]:focus,
            input[type="number"]:focus,
            textarea:focus {{
                outline: none;
                border-color: #667eea;
                box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.2);
            }}
            textarea {{ min-height: 80px; resize: vertical; }}
            .visibility-panel {{
                background: #f8fafc;
                border: 1px solid #e2e8f0;
                border-radius: 8px;
                padding: 14px 16px;
            }}
            .active-row {{
                display: flex;
                align-items: center;
                gap: 10px;
                color: #334155;
                font-weight: 500;
            }}
            .active-row input[type="checkbox"] {{
                width: 18px;
                height: 18px;
                accent-color: #667eea;
            }}
            .btn {{
                padding: 12px 24px;
                border: none;
                border-radius: 6px;
                cursor: pointer;
                text-decoration: none;
                display: inline-block;
                font-size: 15px;
                font-weight: 600;
            }}
            .btn-primary {{ background: #667eea; color: #ffffff; }}
            .btn-primary:hover {{ background: #5568d3; }}
            .btn-secondary {{ background: #64748b; color: #ffffff; margin-left: 10px; }}
            .btn-secondary:hover {{ background: #475569; }}
        </style>
    </head>
    <body>
        <div class="edit-container">
        {_CONFIG_PANEL_BACK_LINK_HTML}
        <h1>✏️ Editar Torneo</h1>
        <form method="POST" action="/admin/torneos/update/{tournament_id}">
            <div class="form-group">
                <label>Nombre del Torneo *</label>
                <input type="text" name="name" value="{tournament.name}" required>
            </div>
            <div class="form-group">
                <label>Descripción</label>
                <textarea name="description">{tournament.description or ''}</textarea>
            </div>
            <div class="form-group">
                <label>Orden de Visualización</label>
                <input type="number" name="display_order" value="{tournament.display_order}" min="0">
            </div>
            <div class="form-group">
                <label>Cuenta Contable</label>
                <input type="text" name="cuenta_contable_relacionada" value="{tournament.cuenta_contable_relacionada or ''}" placeholder="Ej: 5300-010">
                <small style="color: #666; display: block; margin-top: 5px;">Cuenta contable asociada a este torneo</small>
            </div>
            <div class="form-group">
                <label for="etapas_editar">Etapas del torneo</label>
                <textarea name="etapas" id="etapas_editar" rows="4" placeholder="Una por línea. Vacío = lista por defecto.">{escape("\n".join(getattr(tournament, "etapas", None) or []))}</textarea>
                <small style="color: #666; display: block; margin-top: 5px;">Opciones de Fase para este torneo.</small>
            </div>
            <div class="form-group">
                <label for="categorias_editar">Categoría</label>
                <textarea name="categorias" id="categorias_editar" rows="4" placeholder="Una por línea">{escape("\n".join(getattr(tournament, "categorias", None) or []))}</textarea>
                <small style="color: #666; display: block; margin-top: 5px;">Opciones de categoría para este torneo.</small>
            </div>
            <div class="form-group">
                <label>Visible en formularios para</label>
                <div class="visibility-panel">
                {render_form_visibility_areas_checkboxes(getattr(tournament, "form_visibility_areas", None), html_id_prefix="edit_vis")}
                </div>
            </div>
            <div class="form-group">
                <div class="active-row">
                    <input type="checkbox" name="active" id="active_edit" {'checked' if tournament.active else ''}>
                    <label for="active_edit" style="margin:0;">Activo (visible en Telegram y listas activas)</label>
                </div>
            </div>
            <button type="submit" class="btn btn-primary">Guardar Cambios</button>
            <a href="/admin/torneos" class="btn btn-secondary">Cancelar</a>
        </form>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)


@router.post("/admin/torneos/update/{tournament_id}")
async def update_tournament(
    tournament_id: UUIDType,
    request: Request,
    name: str = Form(...),
    description: Optional[str] = Form(None),
    display_order: int = Form(0),
    cuenta_contable_relacionada: Optional[str] = Form(None),
    etapas: Optional[str] = Form(None),
    categorias: Optional[str] = Form(None),
    session: AsyncSession = Depends(get_db_session),
):
    """Update a tournament."""
    normalized_name = (name or "").strip()
    try:
        await _ensure_tournaments_admin_schema(session)
        form_data = await request.form()
        active = form_data.get("active") == "on"
        etapas_list = _parse_etapas_from_form(etapas)
        categorias_list = _parse_newline_delimited_list(categorias)
        visibility_areas = parse_form_visibility_areas_from_form(
            form_data.getlist("form_visibility_areas")
            if hasattr(form_data, "getlist")
            else form_data.get("form_visibility_areas")
        )

        result = await session.execute(
            select(Tournament).where(Tournament.id == tournament_id)
        )
        tournament = result.scalar_one_or_none()

        if not tournament:
            return _admin_torneos_redirect(error_msg="Torneo no encontrado.")

        if not normalized_name:
            return _admin_torneos_redirect(
                error_msg="El nombre del proyecto es requerido."
            )

        name_conflict = await _find_local_tournament_by_name(session, normalized_name)
        if name_conflict and name_conflict.id != tournament.id:
            return _admin_torneos_redirect(
                error_msg=(
                    f"Ya existe otro proyecto con el nombre «{normalized_name}». "
                    "Use un nombre distinto."
                )
            )

        scope_changed = tournament_scope_config_changed(
            previous_etapas=tournament.etapas,
            previous_categorias=tournament.categorias,
            next_etapas=etapas_list,
            next_categorias=categorias_list,
        )

        tournament.name = normalized_name
        tournament.description = description.strip() if description else None
        tournament.display_order = display_order
        tournament.cuenta_contable_relacionada = (
            cuenta_contable_relacionada.strip() if cuenta_contable_relacionada else None
        )
        tournament.etapas = etapas_list
        tournament.categorias = categorias_list
        tournament.form_visibility_areas = visibility_areas or None
        tournament.active = active

        cleared_partidas = 0
        if scope_changed:
            cleared_partidas = await clear_budget_concept_scope_for_tournament(
                session,
                tournament_id=str(tournament.id),
                commit=False,
            )

        await session.commit()

        success_msg = "Torneo actualizado exitosamente."
        if scope_changed:
            if cleared_partidas:
                success_msg += (
                    f" Se reseteó el subproyecto/fase en {cleared_partidas} "
                    "partida(s) presupuestal(es); revísalas en Presupuestos si "
                    "necesitas acotarlas de nuevo."
                )
            else:
                success_msg += (
                    " Las partidas presupuestales de este proyecto aplican a "
                    "todas las fases/subproyectos."
                )
        return _admin_torneos_redirect(success_msg=success_msg)
    except IntegrityError:
        await session.rollback()
        return _admin_torneos_redirect(
            error_msg=(
                f"Ya existe otro proyecto con el nombre «{normalized_name}». "
                "Use un nombre distinto."
            )
        )
    except Exception as e:
        await session.rollback()
        logger.error("Error updating tournament %s: %s", tournament_id, e)
        return _admin_torneos_redirect(
            error_msg=f"No se pudo actualizar el proyecto: {e}"
        )


@router.post("/admin/torneos/toggle/{tournament_id}")
async def toggle_tournament(
    tournament_id: UUIDType, session: AsyncSession = Depends(get_db_session)
):
    """Toggle tournament active status."""
    await _ensure_tournaments_admin_schema(session)
    result = await session.execute(
        select(Tournament).where(Tournament.id == tournament_id)
    )
    tournament = result.scalar_one_or_none()

    if not tournament:
        raise HTTPException(status_code=404, detail="Tournament not found")

    tournament.active = not tournament.active
    await session.commit()

    return HTMLResponse(
        content="""
        <!DOCTYPE html>
        <html>
        <head>
            <meta http-equiv="refresh" content="1;url=/admin/torneos">
            <title>Actualizado</title>
        </head>
        <body style="font-family: sans-serif; padding: 20px; text-align: center;">
            <h2>✅ Estado actualizado</h2>
            <p>Redirigiendo...</p>
        </body>
        </html>
        """
    )


@router.post("/admin/torneos/delete/{tournament_id}")
async def delete_tournament(
    tournament_id: UUIDType, session: AsyncSession = Depends(get_db_session)
):
    """Delete a tournament."""
    await _ensure_tournaments_admin_schema(session)
    result = await session.execute(
        select(Tournament).where(Tournament.id == tournament_id)
    )
    tournament = result.scalar_one_or_none()

    if not tournament:
        raise HTTPException(status_code=404, detail="Tournament not found")

    await session.delete(tournament)
    await session.commit()

    return HTMLResponse(
        content="""
        <!DOCTYPE html>
        <html>
        <head>
            <meta http-equiv="refresh" content="1;url=/admin/torneos">
            <title>Eliminado</title>
        </head>
        <body style="font-family: sans-serif; padding: 20px; text-align: center;">
            <h2>✅ Torneo eliminado</h2>
            <p>Redirigiendo...</p>
        </body>
        </html>
        """
    )


@router.get("/api/torneos")
async def get_tournaments_api(
    active_only: bool = Query(True), session: AsyncSession = Depends(get_db_session)
):
    """API endpoint to get tournaments (for Telegram bot)."""
    from fastapi.responses import JSONResponse

    await _ensure_tournaments_admin_schema(session)
    query = select(Tournament)
    if active_only:
        query = query.where(Tournament.active.is_(True))
    query = query.order_by(Tournament.display_order, Tournament.name)

    result = await session.execute(query)
    tournaments = result.scalars().all()

    return JSONResponse(content=[t.to_dict() for t in tournaments])


@router.get("/api/torneos/{tournament_id}/etapas")
async def get_tournament_etapas_api(
    tournament_id: UUIDType,
    session: AsyncSession = Depends(get_db_session),
):
    """Return list of etapa names for a tournament (for Fase dropdown)."""
    from fastapi.responses import JSONResponse

    result = await session.execute(
        select(Tournament).where(Tournament.id == tournament_id)
    )
    tournament = result.scalar_one_or_none()
    if not tournament:
        raise HTTPException(status_code=404, detail="Tournament not found")
    return JSONResponse(content={"etapas": get_tournament_etapas(tournament)})


# RFC Configuration Management Routes
# ============================================================================


@router.get("/admin/rfc", response_class=HTMLResponse)
async def admin_rfc(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = require_admin_finanzas(),
):
    """Admin interface for managing RFC configurations."""
    result = await session.execute(
        select(RFCConfig).order_by(RFCConfig.display_order, RFCConfig.name)
    )
    rfc_configs = result.scalars().all()

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Gestión de RFC</title>
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
            input[type="text"], input[type="number"], textarea {{
                width: 100%;
                padding: 10px;
                border: 2px solid #ddd;
                border-radius: 6px;
                font-size: 14px;
            }}
            input[type="text"]:focus, input[type="number"]:focus, textarea:focus {{
                outline: none;
                border-color: #667eea;
            }}
            textarea {{
                resize: vertical;
                min-height: 80px;
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
            .rfc-list {{
                margin-top: 30px;
            }}
            .rfc-item {{
                background: white;
                border: 2px solid #e9ecef;
                border-radius: 8px;
                padding: 20px;
                margin-bottom: 15px;
                transition: all 0.3s;
            }}
            .rfc-item:hover {{
                border-color: #667eea;
                box-shadow: 0 4px 12px rgba(0,0,0,0.1);
            }}
            .rfc-info {{
                margin-bottom: 15px;
            }}
            .rfc-name {{
                font-weight: 600;
                font-size: 18px;
                color: #333;
                margin-bottom: 10px;
            }}
            .rfc-details {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
                gap: 10px;
                font-size: 14px;
                color: #666;
            }}
            .rfc-detail-item {{
                padding: 5px 0;
            }}
            .rfc-detail-label {{
                font-weight: 600;
                color: #333;
            }}
            .rfc-badges {{
                margin-top: 10px;
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
            .rfc-actions {{
                display: flex;
                gap: 10px;
                margin-top: 15px;
            }}
            .btn-small {{
                padding: 8px 16px;
                font-size: 14px;
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
            {render_admin_navigation(current_empleado, "rfc", subtitle="Configuración fiscal reutilizable para flujos internos, Telegram y administración.")}
            
            <h1>Gestión de RFC</h1>
            <p class="subtitle">Configura los RFC disponibles para selección en Telegram</p>
            
            <div class="form-section">
                <h2 style="margin-bottom: 15px;">➕ Agregar Nueva Configuración RFC</h2>
                <form id="rfcForm" method="POST" action="/admin/rfc/create">
                    <div class="form-group">
                        <label for="name">Nombre de la Configuración RFC *</label>
                        <input type="text" id="name" name="name" required placeholder="Ej: RFC matriz — Deportes del Norte">
                    </div>
                    <div class="form-row">
                        <div class="form-group">
                            <label for="tax_id">RFC (Tax ID) *</label>
                            <input type="text" id="tax_id" name="tax_id" required placeholder="Ej: DPN920101XY9">
                        </div>
                        <div class="form-group">
                            <label for="taxpayer">Razón Social *</label>
                            <input type="text" id="taxpayer" name="taxpayer" required placeholder="Ej: DEPORTES DEL NORTE S.A. DE C.V.">
                        </div>
                    </div>
                    <div class="form-row">
                        <div class="form-group" style="display: none;">
                            <label for="taxpayer_name">Nombre</label>
                            <input type="text" id="taxpayer_name" name="taxpayer_name" placeholder="Ej: OPERACIONES">
                        </div>
                        <div class="form-group" style="display: none;">
                            <label for="taxpayer_last_name">Apellido Paterno</label>
                            <input type="text" id="taxpayer_last_name" name="taxpayer_last_name" placeholder="Ej: EVENTOS">
                        </div>
                    </div>
                    <div class="form-group" style="display: none;">
                        <label for="taxpayer_second_last_name">Apellido Materno</label>
                        <input type="text" id="taxpayer_second_last_name" name="taxpayer_second_last_name" placeholder="Ej: DEPORTIVOS">
                    </div>
                    <div class="form-group">
                        <label for="street_address_1">Dirección (Calle) *</label>
                        <input type="text" id="street_address_1" name="street_address_1" required placeholder="Ej: Av. Olímpica">
                    </div>
                    <div class="form-row">
                        <div class="form-group">
                            <label for="ext_num">Número Exterior *</label>
                            <input type="text" id="ext_num" name="ext_num" required placeholder="Ej: 100">
                        </div>
                        <div class="form-group">
                            <label for="int_num">Número Interior</label>
                            <input type="text" id="int_num" name="int_num" placeholder="Opcional">
                        </div>
                    </div>
                    <div class="form-group">
                        <label for="street_address_2">Dirección Adicional</label>
                        <input type="text" id="street_address_2" name="street_address_2" placeholder="Opcional">
                    </div>
                    <div class="form-row">
                        <div class="form-group">
                            <label for="city">Ciudad *</label>
                            <input type="text" id="city" name="city" required placeholder="Ej: Guadalajara">
                        </div>
                        <div class="form-group">
                            <label for="state">Estado *</label>
                            <input type="text" id="state" name="state" required placeholder="Ej: Jalisco">
                        </div>
                    </div>
                    <div class="form-row">
                        <div class="form-group">
                            <label for="postal_code">Código Postal *</label>
                            <input type="text" id="postal_code" name="postal_code" required placeholder="Ej: 44100">
                        </div>
                        <div class="form-group">
                            <label for="country">País</label>
                            <input type="text" id="country" name="country" value="México" placeholder="México">
                        </div>
                    </div>
                    <div class="form-group">
                        <label for="invoice_fiscal_regimen">Régimen Fiscal *</label>
                        <input type="text" id="invoice_fiscal_regimen" name="invoice_fiscal_regimen" required placeholder="Ej: 626">
                    </div>
                    <div class="form-row">
                        <div class="form-group">
                            <label for="display_order">Orden de Visualización</label>
                            <input type="number" id="display_order" name="display_order" value="0" min="0">
                            <small style="color: #666; display: block; margin-top: 5px;">Los números menores aparecen primero</small>
                        </div>
                        <div class="form-group">
                            <div class="checkbox-group" style="margin-top: 25px;">
                                <input type="checkbox" id="active" name="active" checked>
                                <label for="active" style="margin: 0;">Activo (visible en Telegram)</label>
                            </div>
                        </div>
                    </div>
                    <button type="submit" class="btn btn-primary">Crear RFC</button>
                </form>
            </div>
            
            <div class="rfc-list">
                <h2 style="margin-bottom: 20px;">📋 Configuraciones RFC ({len(rfc_configs)})</h2>
                {"<p style='color: #666;'>No hay RFC configurados aún. Crea el primero usando el formulario arriba.</p>" if not rfc_configs else ""}
    """

    for rfc in rfc_configs:
        status_class = "badge-active" if rfc.active else "badge-inactive"
        status_text = "Activo" if rfc.active else "Inactivo"
        html_content += f"""
                <div class="rfc-item">
                    <div class="rfc-info">
                        <div class="rfc-name">{rfc.name}</div>
                        <div class="rfc-details">
                            <div class="rfc-detail-item">
                                <span class="rfc-detail-label">RFC:</span> {rfc.tax_id}
                            </div>
                            <div class="rfc-detail-item">
                                <span class="rfc-detail-label">Razón Social:</span> {rfc.taxpayer}
                            </div>
                            <div class="rfc-detail-item">
                                <span class="rfc-detail-label">Dirección:</span> {rfc.street_address_1} {rfc.ext_num or ''} {f'Int. {rfc.int_num}' if rfc.int_num else ''}
                            </div>
                            <div class="rfc-detail-item">
                                <span class="rfc-detail-label">Ciudad:</span> {rfc.city or '-'}, {rfc.state or '-'}
                            </div>
                            <div class="rfc-detail-item">
                                <span class="rfc-detail-label">C.P.:</span> {rfc.postal_code or '-'}
                            </div>
                            <div class="rfc-detail-item">
                                <span class="rfc-detail-label">Régimen Fiscal:</span> {rfc.invoice_fiscal_regimen or '-'}
                            </div>
                        </div>
                        <div class="rfc-badges">
                            <span class="badge {status_class}">{status_text}</span>
                            <span class="badge" style="background: #e9ecef; color: #495057;">Orden: {rfc.display_order}</span>
                        </div>
                    </div>
                    <div class="rfc-actions">
                        <a href="/admin/rfc/edit/{rfc.id}" class="btn btn-secondary btn-small">✏️ Editar</a>
                        <form method="POST" action="/admin/rfc/toggle/{rfc.id}" style="display: inline;">
                            <button type="submit" class="btn {'btn-secondary' if rfc.active else 'btn-primary'} btn-small">
                                {'Desactivar' if rfc.active else 'Activar'}
                            </button>
                        </form>
                        <form method="POST" action="/admin/rfc/delete/{rfc.id}" style="display: inline;" 
                              onsubmit="return confirm('¿Estás seguro de eliminar esta configuración RFC?');">
                            <button type="submit" class="btn btn-danger btn-small">🗑️ Eliminar</button>
                        </form>
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


@router.post("/admin/rfc/create")
async def create_rfc(
    request: Request,
    name: str = Form(...),
    tax_id: str = Form(...),
    taxpayer: str = Form(...),
    taxpayer_name: Optional[str] = Form(None),
    taxpayer_last_name: Optional[str] = Form(None),
    taxpayer_second_last_name: Optional[str] = Form(None),
    street_address_1: str = Form(...),
    ext_num: str = Form(...),
    int_num: Optional[str] = Form(None),
    street_address_2: Optional[str] = Form(None),
    city: str = Form(...),
    state: str = Form(...),
    postal_code: str = Form(...),
    country: str = Form("México"),
    invoice_fiscal_regimen: str = Form(...),
    display_order: int = Form(0),
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = require_admin_finanzas(),
):
    """Create a new RFC configuration."""
    try:
        form_data = await request.form()
        active = form_data.get("active") == "on"

        rfc = RFCConfig(
            name=name.strip(),
            tax_id=tax_id.strip(),
            taxpayer=taxpayer.strip(),
            taxpayer_name=taxpayer_name.strip() if taxpayer_name else None,
            taxpayer_last_name=(
                taxpayer_last_name.strip() if taxpayer_last_name else None
            ),
            taxpayer_second_last_name=(
                taxpayer_second_last_name.strip() if taxpayer_second_last_name else None
            ),
            street_address_1=street_address_1.strip(),
            ext_num=ext_num.strip(),
            int_num=int_num.strip() if int_num else None,
            street_address_2=street_address_2.strip() if street_address_2 else None,
            city=city.strip(),
            state=state.strip(),
            postal_code=postal_code.strip(),
            country=country.strip() if country else "México",
            invoice_fiscal_regimen=invoice_fiscal_regimen.strip(),
            display_order=display_order,
            active=active,
        )
        session.add(rfc)
        await session.commit()
        await session.refresh(rfc)

        return HTMLResponse(
            content="""
            <!DOCTYPE html>
            <html>
            <head>
                <meta http-equiv="refresh" content="2;url=/admin/rfc">
                <title>Éxito</title>
            </head>
            <body style="font-family: sans-serif; padding: 20px; text-align: center;">
                <h2 style="color: green;">✅ RFC creado exitosamente</h2>
                <p>Redirigiendo...</p>
            </body>
            </html>
            """
        )
    except Exception as e:
        logger.error(f"Error creating RFC: {e}")
        return HTMLResponse(
            content=f"<h2>Error: {str(e)}</h2><a href='/admin/rfc'>Volver</a>",
            status_code=400,
        )


@router.get("/admin/rfc/edit/{rfc_id}", response_class=HTMLResponse)
async def edit_rfc_form(
    rfc_id: UUIDType,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = require_admin_finanzas(),
):
    """Edit RFC form."""
    result = await session.execute(select(RFCConfig).where(RFCConfig.id == rfc_id))
    rfc = result.scalar_one_or_none()

    if not rfc:
        raise HTTPException(status_code=404, detail="RFC not found")

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Editar RFC</title>
        <meta charset="utf-8">
        <style>
            body {{ font-family: sans-serif; padding: 20px; max-width: 1000px; margin: 0 auto; }}
            .form-group {{ margin-bottom: 15px; }}
            .form-row {{ display: grid; grid-template-columns: 1fr 1fr; gap: 15px; }}
            label {{ display: block; margin-bottom: 5px; font-weight: 600; }}
            input[type="text"], input[type="number"] {{ width: 100%; padding: 10px; border: 2px solid #ddd; border-radius: 6px; }}
            .btn {{ padding: 12px 24px; border: none; border-radius: 6px; cursor: pointer; text-decoration: none; display: inline-block; }}
            .btn-primary {{ background: #667eea; color: white; }}
            .btn-secondary {{ background: #6c757d; color: white; margin-left: 10px; }}
        </style>
    </head>
    <body>
        {_CONFIG_PANEL_BACK_LINK_HTML}
        <h1>✏️ Editar RFC</h1>
        <form method="POST" action="/admin/rfc/update/{rfc_id}">
            <div class="form-group">
                <label>Nombre de la Configuración RFC *</label>
                <input type="text" name="name" value="{rfc.name}" required>
            </div>
            <div class="form-row">
                <div class="form-group">
                    <label>RFC (Tax ID) *</label>
                    <input type="text" name="tax_id" value="{rfc.tax_id}" required>
                </div>
                <div class="form-group">
                    <label>Razón Social *</label>
                    <input type="text" name="taxpayer" value="{rfc.taxpayer}" required>
                </div>
            </div>
            <div class="form-row">
                <div class="form-group" style="display: none;">
                    <label>Nombre</label>
                    <input type="text" name="taxpayer_name" value="{rfc.taxpayer_name or ''}">
                </div>
                <div class="form-group" style="display: none;">
                    <label>Apellido Paterno</label>
                    <input type="text" name="taxpayer_last_name" value="{rfc.taxpayer_last_name or ''}">
                </div>
            </div>
            <div class="form-group" style="display: none;">
                <label>Apellido Materno</label>
                <input type="text" name="taxpayer_second_last_name" value="{rfc.taxpayer_second_last_name or ''}">
            </div>
            <div class="form-group">
                <label>Dirección (Calle) *</label>
                <input type="text" name="street_address_1" value="{rfc.street_address_1 or ''}" required>
            </div>
            <div class="form-row">
                <div class="form-group">
                    <label>Número Exterior *</label>
                    <input type="text" name="ext_num" value="{rfc.ext_num or ''}" required>
                </div>
                <div class="form-group">
                    <label>Número Interior</label>
                    <input type="text" name="int_num" value="{rfc.int_num or ''}">
                </div>
            </div>
            <div class="form-group">
                <label>Dirección Adicional</label>
                <input type="text" name="street_address_2" value="{rfc.street_address_2 or ''}">
            </div>
            <div class="form-row">
                <div class="form-group">
                    <label>Ciudad *</label>
                    <input type="text" name="city" value="{rfc.city or ''}" required>
                </div>
                <div class="form-group">
                    <label>Estado *</label>
                    <input type="text" name="state" value="{rfc.state or ''}" required>
                </div>
            </div>
            <div class="form-row">
                <div class="form-group">
                    <label>Código Postal *</label>
                    <input type="text" name="postal_code" value="{rfc.postal_code or ''}" required>
                </div>
                <div class="form-group">
                    <label>País</label>
                    <input type="text" name="country" value="{rfc.country or 'México'}">
                </div>
            </div>
            <div class="form-group">
                <label>Régimen Fiscal *</label>
                <input type="text" name="invoice_fiscal_regimen" value="{rfc.invoice_fiscal_regimen or ''}" required>
            </div>
            <div class="form-row">
                <div class="form-group">
                    <label>Orden de Visualización</label>
                    <input type="number" name="display_order" value="{rfc.display_order}" min="0">
                </div>
                <div class="form-group">
                    <label style="margin-top: 25px;">
                        <input type="checkbox" name="active" {'checked' if rfc.active else ''}>
                        Activo (visible en Telegram)
                    </label>
                </div>
            </div>
            <button type="submit" class="btn btn-primary">Guardar Cambios</button>
            <a href="/admin/rfc" class="btn btn-secondary">Cancelar</a>
        </form>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)


@router.post("/admin/rfc/update/{rfc_id}")
async def update_rfc(
    rfc_id: UUIDType,
    request: Request,
    name: str = Form(...),
    tax_id: str = Form(...),
    taxpayer: str = Form(...),
    taxpayer_name: Optional[str] = Form(None),
    taxpayer_last_name: Optional[str] = Form(None),
    taxpayer_second_last_name: Optional[str] = Form(None),
    street_address_1: str = Form(...),
    ext_num: str = Form(...),
    int_num: Optional[str] = Form(None),
    street_address_2: Optional[str] = Form(None),
    city: str = Form(...),
    state: str = Form(...),
    postal_code: str = Form(...),
    country: str = Form("México"),
    invoice_fiscal_regimen: str = Form(...),
    display_order: int = Form(0),
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = require_admin_finanzas(),
):
    """Update an RFC configuration."""
    form_data = await request.form()
    active = form_data.get("active") == "on"

    result = await session.execute(select(RFCConfig).where(RFCConfig.id == rfc_id))
    rfc = result.scalar_one_or_none()

    if not rfc:
        raise HTTPException(status_code=404, detail="RFC not found")

    rfc.name = name.strip()
    rfc.tax_id = tax_id.strip()
    rfc.taxpayer = taxpayer.strip()
    rfc.taxpayer_name = taxpayer_name.strip() if taxpayer_name else None
    rfc.taxpayer_last_name = taxpayer_last_name.strip() if taxpayer_last_name else None
    rfc.taxpayer_second_last_name = (
        taxpayer_second_last_name.strip() if taxpayer_second_last_name else None
    )
    rfc.street_address_1 = street_address_1.strip()
    rfc.ext_num = ext_num.strip()
    rfc.int_num = int_num.strip() if int_num else None
    rfc.street_address_2 = street_address_2.strip() if street_address_2 else None
    rfc.city = city.strip()
    rfc.state = state.strip()
    rfc.postal_code = postal_code.strip()
    rfc.country = country.strip() if country else "México"
    rfc.invoice_fiscal_regimen = invoice_fiscal_regimen.strip()
    rfc.display_order = display_order
    rfc.active = active

    await session.commit()

    return HTMLResponse(
        content="""
        <!DOCTYPE html>
        <html>
        <head>
            <meta http-equiv="refresh" content="2;url=/admin/rfc">
            <title>Éxito</title>
        </head>
        <body style="font-family: sans-serif; padding: 20px; text-align: center;">
            <h2 style="color: green;">✅ RFC actualizado exitosamente</h2>
            <p>Redirigiendo...</p>
        </body>
        </html>
        """
    )


@router.post("/admin/rfc/toggle/{rfc_id}")
async def toggle_rfc(
    rfc_id: UUIDType,
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = require_admin_finanzas(),
):
    """Toggle RFC active status."""
    result = await session.execute(select(RFCConfig).where(RFCConfig.id == rfc_id))
    rfc = result.scalar_one_or_none()

    if not rfc:
        raise HTTPException(status_code=404, detail="RFC not found")

    rfc.active = not rfc.active
    await session.commit()

    return HTMLResponse(
        content="""
        <!DOCTYPE html>
        <html>
        <head>
            <meta http-equiv="refresh" content="1;url=/admin/rfc">
            <title>Actualizado</title>
        </head>
        <body style="font-family: sans-serif; padding: 20px; text-align: center;">
            <h2>✅ Estado actualizado</h2>
            <p>Redirigiendo...</p>
        </body>
        </html>
        """
    )


@router.post("/admin/rfc/delete/{rfc_id}")
async def delete_rfc(
    rfc_id: UUIDType,
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = require_admin_finanzas(),
):
    """Delete an RFC configuration."""
    result = await session.execute(select(RFCConfig).where(RFCConfig.id == rfc_id))
    rfc = result.scalar_one_or_none()

    if not rfc:
        raise HTTPException(status_code=404, detail="RFC not found")

    await session.delete(rfc)
    await session.commit()

    return HTMLResponse(
        content="""
        <!DOCTYPE html>
        <html>
        <head>
            <meta http-equiv="refresh" content="1;url=/admin/rfc">
            <title>Eliminado</title>
        </head>
        <body style="font-family: sans-serif; padding: 20px; text-align: center;">
            <h2>✅ RFC eliminado</h2>
            <p>Redirigiendo...</p>
        </body>
        </html>
        """
    )


@router.get("/api/rfc")
async def get_rfc_api(
    active_only: bool = Query(True), session: AsyncSession = Depends(get_db_session)
):
    """API endpoint to get RFC configurations (for Telegram bot)."""
    from fastapi.responses import JSONResponse

    query = select(RFCConfig)
    if active_only:
        query = query.where(RFCConfig.active.is_(True))
    query = query.order_by(RFCConfig.display_order, RFCConfig.name)

    result = await session.execute(query)
    rfc_configs = result.scalars().all()

    return JSONResponse(content=[r.to_dict() for r in rfc_configs])


# ============================================================================
# Employee Management Routes
# ============================================================================


@router.get("/admin/empleados", response_class=HTMLResponse)
async def admin_empleados(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = require_admin_finanzas(),
    search: Optional[str] = Query(None),
    success_msg: Optional[str] = Query(None),
):
    """Admin interface for managing employees."""
    if search:
        result = await session.execute(
            text(
                """
                SELECT e.id, e.nombre, e.correo, e.telefono, e.departamento, e.rol, e.activo,
                       e.telegram_user_id, e.proyecto_predeterminado, e.centro_costo_predeterminado,
                       e.creado_en, e.actualizado_en, e.aprobador_id, a.nombre
                FROM empleados e
                LEFT JOIN empleados a ON e.aprobador_id = a.id
                WHERE e.nombre ILIKE :search OR e.correo ILIKE :search
                ORDER BY e.nombre
            """
            ),
            {"search": f"%{search}%"},
        )
    else:
        result = await session.execute(
            text(
                """
                SELECT e.id, e.nombre, e.correo, e.telefono, e.departamento, e.rol, e.activo,
                       e.telegram_user_id, e.proyecto_predeterminado, e.centro_costo_predeterminado,
                       e.creado_en, e.actualizado_en, e.aprobador_id, a.nombre
                FROM empleados e
                LEFT JOIN empleados a ON e.aprobador_id = a.id
                ORDER BY e.nombre
            """
            )
        )
    rows = result.fetchall()

    # Convert rows to simple objects for template
    class EmpleadoRow:
        def __init__(self, row):
            self.id = row[0]
            self.nombre = row[1]
            self.correo = row[2]
            self.telefono = row[3]
            self.departamento = row[4]
            self.rol = row[5]
            self.activo = row[6]
            self.telegram_user_id = row[7]
            self.proyecto_predeterminado = row[8]
            self.centro_costo_predeterminado = row[9]
            self.creado_en = row[10]
            self.actualizado_en = row[11]
            self.aprobador_id = row[12]
            self.aprobador_nombre = row[13]

    empleados = [EmpleadoRow(row) for row in rows]

    # Fetch all active empleados for approver dropdown in create form
    aprobadores_result = await session.execute(
        text(
            """
            SELECT id, nombre, correo, rol
            FROM empleados 
            WHERE activo = TRUE
            ORDER BY nombre
        """
        )
    )
    aprobadores_rows = aprobadores_result.fetchall()

    # Convert to simple objects
    class AprobadorRow:
        def __init__(self, row):
            self.id = row[0]
            self.nombre = row[1]
            self.correo = row[2]
            self.rol = row[3]

    aprobadores = [AprobadorRow(row) for row in aprobadores_rows]

    # Build approver options for create form
    aprobador_options_create = '<option value="">(Sin asignar)</option>'
    for aprobador in aprobadores:
        rol_display = f" ({aprobador.rol})" if aprobador.rol else ""
        aprobador_options_create += f'\n                            <option value="{aprobador.id}">{aprobador.nombre}{rol_display}</option>'

    success_html = ""
    if success_msg:
        success_html = f"""
            <div style="background: #d4edda; border: 1px solid #c3e6cb; border-radius: 5px; padding: 15px; margin-bottom: 20px; color: #155724;">
                <strong>✅ Éxito:</strong> {success_msg}
            </div>
        """

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Gestión de Empleados - Copa Telmex</title>
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
            input[type="text"], input[type="email"], input[type="number"], select {{
                width: 100%;
                padding: 10px;
                border: 2px solid #ddd;
                border-radius: 6px;
                font-size: 14px;
            }}
            input[type="text"]:focus, input[type="email"]:focus, select:focus {{
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
            .btn-danger {{
                background: #dc3545;
                color: white;
            }}
            .btn-danger:hover {{
                background: #c82333;
            }}
            .search-box {{
                margin-bottom: 20px;
                display: flex;
                gap: 10px;
            }}
            .search-box input {{
                flex: 1;
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
            .btn-small {{
                padding: 8px 16px;
                font-size: 14px;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            {render_admin_navigation(current_empleado, "empleados", subtitle="Catálogo de empleados internos, permisos y vinculación operativa.")}
            
            <h1>Gestión de Empleados</h1>
            <p class="subtitle">Administra el catálogo de empleados</p>
            {success_html}
            
            <div class="form-section">
                <h2 style="margin-bottom: 15px;">➕ Agregar Nuevo Empleado</h2>
                <form method="POST" action="/admin/empleados/create">
                    <div class="form-row">
                        <div class="form-group">
                            <label for="nombre">Nombre *</label>
                            <input type="text" id="nombre" name="nombre" required placeholder="Ej: Juan Pérez">
                        </div>
                        <div class="form-group">
                            <label for="correo">Correo Electrónico *</label>
                            <input type="email" id="correo" name="correo" required placeholder="juan.perez@example.com">
                        </div>
                    </div>
                    <div class="form-row">
                        <div class="form-group">
                            <label for="telefono">Teléfono</label>
                            <input type="text" id="telefono" name="telefono" placeholder="Opcional">
                        </div>
                        <div class="form-group">
                            <label for="departamento">Departamento</label>
                            <select id="departamento" name="departamento">
                                <option value="">—</option>
                                <option value="Finanzas">Finanzas</option>
                                <option value="Mercadotecnia">Mercadotecnia</option>
                                <option value="Operaciones">Operaciones</option>
                                <option value="Dirección">Dirección</option>
                            </select>
                        </div>
                    </div>
                    <div class="form-row">
                        <div class="form-group">
                            <label for="rol">Rol *</label>
                            <select id="rol" name="rol" required>
                                <option value="empleado">Empleado</option>
                                <option value="coordinador">Coordinador</option>
                                <option value="finanzas">Finanzas</option>
                                <option value="admin">Admin</option>
                                <option value="superadmin">Superadmin</option>
                            </select>
                        </div>
                        <div class="form-group">
                            <label for="telegram_user_id">Telegram User ID
                                <span title="Si no se proporciona, el empleado no tendrá acceso al chatbot de Telegram hasta que se actualice esta información." style="cursor: help; color: #666; font-size: 14px;">&#9432;</span>
                            </label>
                            <input type="number" id="telegram_user_id" name="telegram_user_id" placeholder="Opcional">
                        </div>
                    </div>
                    <div class="form-group">
                        <label for="aprobador_id">Aprobador</label>
                        <select id="aprobador_id" name="aprobador_id">
                            {aprobador_options_create}
                        </select>
                        <small style="color: #666; font-size: 12px;">Opcional: Selecciona quién aprobará los documentos de este empleado</small>
                    </div>
                    <div class="form-group">
                        <div class="checkbox-group">
                            <input type="checkbox" id="activo" name="activo" checked>
                            <label for="activo" style="margin: 0;">Activo</label>
                        </div>
                    </div>
                    <button type="submit" class="btn btn-primary">Crear Empleado</button>
                </form>
            </div>
            
            <div style="margin-top: 30px;">
                <h2 style="margin-bottom: 20px;">📋 Empleados ({len(empleados)})</h2>
                <div class="search-box">
                    <form method="GET" action="/admin/empleados" style="display: flex; gap: 10px; width: 100%;">
                        <input type="text" name="search" placeholder="Buscar por nombre o correo..." value="{search or ''}">
                        <button type="submit" class="btn btn-secondary">🔍 Buscar</button>
                        <a href="/admin/empleados" class="btn btn-secondary">Limpiar</a>
                    </form>
                </div>
                {"<p style='color: #666;'>No hay empleados registrados aún.</p>" if not empleados else ""}
                <table>
                    <thead>
                        <tr>
                            <th>Nombre</th>
                            <th>Correo</th>
                            <th>Teléfono</th>
                            <th>Departamento</th>
                            <th>Rol</th>
                            <th>Aprobador</th>
                            <th>Telegram ID</th>
                            <th>Estado</th>
                            <th>Acciones</th>
                        </tr>
                    </thead>
                    <tbody>
"""

    for empleado in empleados:
        status_class = "badge-active" if empleado.activo else "badge-inactive"
        status_text = "Activo" if empleado.activo else "Inactivo"
        aprobador_nombre = empleado.aprobador_nombre or "—"
        html_content += f"""
                        <tr>
                            <td>{empleado.nombre}</td>
                            <td>{empleado.correo or '-'}</td>
                            <td>{empleado.telefono or '-'}</td>
                            <td>{empleado.departamento or '-'}</td>
                            <td>{empleado.rol}</td>
                            <td>{aprobador_nombre}</td>
                            <td>{empleado.telegram_user_id or '-'}</td>
                            <td><span class="badge {status_class}">{status_text}</span></td>
                            <td>
                                <a href="/admin/empleados/edit/{empleado.id}" class="btn btn-secondary btn-small">✏️ Editar</a>
                                <a href="/admin/empleados/{empleado.id}/password" class="btn btn-secondary btn-small" style="margin-left: 5px;">🔑 Cambiar contraseña</a>
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


@router.get("/admin/customer-success/uso", response_class=HTMLResponse)
async def admin_customer_success_usage(
    request: Request,
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
    limit: int = Query(25),
):
    report = await build_customer_success_usage_report(
        session,
        days=days,
        area=area,
        tournament_id=tournament_id,
        customer_label=customer_label,
        limit=limit,
    )
    summary = report.get("summary") or {}
    users = list(report.get("users") or [])
    areas = list(report.get("areas") or [])
    pages = list(report.get("pages") or [])
    tournaments = list(report.get("tournaments") or [])
    cohorts = list(report.get("cohorts") or [])
    selected_days = max(1, min(int(days or 14), 180))
    selected_area = str(area or "").strip().lower()
    selected_tournament_id = str(tournament_id or "").strip()
    selected_customer_label = str(customer_label or "").strip()
    top_area = areas[0] if areas else {}
    top_page = pages[0] if pages else {}
    top_tournament = tournaments[0] if tournaments else {}
    latest_cohort = cohorts[0] if cohorts else {}

    filter_chips = [
        f'<span class="cs-filter-chip">Ventana · {selected_days} días</span>',
        (
            f'<span class="cs-filter-chip">Área · {escape(selected_area)}</span>'
            if selected_area
            else '<span class="cs-filter-chip cs-filter-chip-muted">Área · todas</span>'
        ),
        (
            f'<span class="cs-filter-chip">Torneo · {escape(selected_tournament_id)}</span>'
            if selected_tournament_id
            else '<span class="cs-filter-chip cs-filter-chip-muted">Torneo · cualquiera</span>'
        ),
        (
            f'<span class="cs-filter-chip">Cliente · {escape(selected_customer_label)}</span>'
            if selected_customer_label
            else '<span class="cs-filter-chip cs-filter-chip-muted">Cliente · cualquiera</span>'
        ),
    ]

    quick_cards = "".join(
        [
            f"""
            <div class="cs-stat-card cs-stat-card-primary">
                <div class="cs-stat-label">Usuarios activos</div>
                <div class="cs-stat-value">{int(summary.get("active_users") or 0)}</div>
                <div class="cs-stat-note">Personas con actividad dentro del filtro actual.</div>
            </div>
            """,
            f"""
            <div class="cs-stat-card cs-stat-card-info">
                <div class="cs-stat-label">Minutos activos</div>
                <div class="cs-stat-value">{int(summary.get("total_active_minutes") or 0)}</div>
                <div class="cs-stat-note">Buckets de uso confirmados por heartbeat autenticado.</div>
            </div>
            """,
            f"""
            <div class="cs-stat-card cs-stat-card-warning">
                <div class="cs-stat-label">Promedio por usuario</div>
                <div class="cs-stat-value">{float(summary.get("avg_active_minutes_per_user") or 0):,.1f} min</div>
                <div class="cs-stat-note">Profundidad estimada de uso por persona activa.</div>
            </div>
            """,
            f"""
            <div class="cs-stat-card cs-stat-card-dark">
                <div class="cs-stat-label">Sesiones rastreadas</div>
                <div class="cs-stat-value">{int(summary.get("tracked_sessions") or 0)}</div>
                <div class="cs-stat-note">Session keys distintas observadas en el periodo.</div>
            </div>
            """,
        ]
    )

    spotlight_cards = "".join(
        [
            f"""
            <div class="cs-spotlight-card">
                <div class="cs-spotlight-label">Área dominante</div>
                <div class="cs-spotlight-title">{escape(str(top_area.get("product_area") or "Sin lectura"))}</div>
                <div class="cs-spotlight-meta">{int(top_area.get("active_minutes") or 0)} min · {int(top_area.get("active_users") or 0)} usuarios · {int(top_area.get("page_count") or 0)} páginas</div>
            </div>
            """,
            f"""
            <div class="cs-spotlight-card">
                <div class="cs-spotlight-label">Torneo / cliente líder</div>
                <div class="cs-spotlight-title">{escape(str(top_tournament.get("tournament_name") or "Sin torneo"))}</div>
                <div class="cs-spotlight-meta">{escape(str(top_tournament.get("customer_label") or "—"))} · {int(top_tournament.get("active_minutes") or 0)} min · {int(top_tournament.get("page_count") or 0)} páginas</div>
            </div>
            """,
            f"""
            <div class="cs-spotlight-card">
                <div class="cs-spotlight-label">Pantalla más usada</div>
                <div class="cs-spotlight-title">{escape(str(top_page.get("page_title") or top_page.get("page_path") or "Sin lectura"))}</div>
                <div class="cs-spotlight-meta"><code>{escape(str(top_page.get("page_path") or "—"))}</code> · {int(top_page.get("active_minutes") or 0)} min · {int(top_page.get("active_users") or 0)} usuarios</div>
            </div>
            """,
            f"""
            <div class="cs-spotlight-card">
                <div class="cs-spotlight-label">Cohorte más reciente</div>
                <div class="cs-spotlight-title">{escape(str(latest_cohort.get("cohort_date") or "Sin cohortes"))}</div>
                <div class="cs-spotlight-meta">{int(latest_cohort.get("users_count") or 0)} usuarios · {float(latest_cohort.get("avg_active_minutes") or 0):,.1f} min promedio</div>
            </div>
            """,
        ]
    )

    user_rows = "".join(
        f"""
        <tr>
            <td>
                <div style="font-weight:700;">{escape(str(row.get("empleado_nombre") or "Sin nombre"))}</div>
                <div style="font-size:12px;color:#64748b;">{escape(str(row.get("correo") or "—"))}</div>
            </td>
            <td><span class="cs-pill">{escape(str(row.get("rol") or "empleado"))}</span></td>
            <td>{int(row.get("active_minutes") or 0)}<div class="cs-cell-sub">{float(row.get("active_hours") or 0):,.2f} h</div></td>
            <td>{int(row.get("session_count") or 0)}</td>
            <td>{int(row.get("area_count") or 0)}</td>
            <td>{int(row.get("page_count") or 0)}<div class="cs-cell-sub">{int(row.get("tournament_count") or 0)} torneos</div></td>
            <td>{escape(str(row.get("customer_label") or "—"))}</td>
            <td>{escape(str(row.get("last_seen_at") or "—"))}</td>
        </tr>
        """
        for row in users
    )

    area_rows = "".join(
        f"""
        <tr>
            <td><span class="cs-pill">{escape(str(row.get("product_area") or "unknown"))}</span></td>
            <td>{int(row.get("active_users") or 0)}</td>
            <td>{int(row.get("active_minutes") or 0)}<div class="cs-cell-sub">{float(row.get("active_hours") or 0):,.2f} h</div></td>
            <td>{int(row.get("page_count") or 0)}<div class="cs-cell-sub">{int(row.get("tournament_count") or 0)} torneos</div></td>
            <td>{escape(str(row.get("last_seen_at") or "—"))}</td>
        </tr>
        """
        for row in areas
    )

    page_rows = "".join(
        f"""
        <tr>
            <td>
                <div style="font-weight:700;">{escape(str(row.get("page_title") or row.get("page_path") or "—"))}</div>
                <div style="font-size:12px;color:#64748b;"><code>{escape(str(row.get("page_path") or "—"))}</code></div>
                <div class="cs-cell-sub">{escape(str(row.get("tournament_name") or "—"))} · {escape(str(row.get("customer_label") or "—"))}</div>
            </td>
            <td><span class="cs-pill">{escape(str(row.get("product_area") or "unknown"))}</span></td>
            <td>{int(row.get("active_users") or 0)}</td>
            <td>{int(row.get("active_minutes") or 0)}<div class="cs-cell-sub">{float(row.get("active_hours") or 0):,.2f} h</div></td>
            <td>{escape(str(row.get("last_seen_at") or "—"))}</td>
        </tr>
        """
        for row in pages
    )

    tournament_rows = "".join(
        f"""
        <tr>
            <td>
                <div style="font-weight:700;">{escape(str(row.get("tournament_name") or "Sin torneo"))}</div>
                <div style="font-size:12px;color:#64748b;"><code>{escape(str(row.get("tournament_id") or "—"))}</code></div>
            </td>
            <td>{escape(str(row.get("customer_label") or "—"))}</td>
            <td>{int(row.get("active_users") or 0)}</td>
            <td>{int(row.get("active_minutes") or 0)}<div class="cs-cell-sub">{float(row.get("active_hours") or 0):,.2f} h</div></td>
            <td>{int(row.get("page_count") or 0)}</td>
            <td>{escape(str(row.get("last_seen_at") or "—"))}</td>
        </tr>
        """
        for row in tournaments
    )

    cohort_rows = "".join(
        f"""
        <tr>
            <td><span class="cs-pill">{escape(str(row.get("cohort_date") or "—"))}</span></td>
            <td>{int(row.get("users_count") or 0)}</td>
            <td>{float(row.get("avg_active_minutes") or 0):,.2f}<div class="cs-cell-sub">{float(row.get("avg_active_hours") or 0):,.2f} h</div></td>
            <td>{escape(str(row.get("last_seen_at") or "—"))}</td>
        </tr>
        """
        for row in cohorts
    )

    area_option_values = [
        "",
        "panel",
        "operaciones",
        "telegram",
        "informes",
        "finanzas",
        "contabilidad",
        "nomina",
        "presupuestos",
        "administracion",
        "documentos",
        "gastos",
        "customer_success",
    ]
    area_options = "".join(
        f'<option value="{escape(value)}" {"selected" if value == selected_area else ""}>{escape(value or "todas")}</option>'
        for value in area_option_values
    )

    page_styles = """
        .cs-toolbar {
            display:grid;
            gap:16px;
            margin-bottom:18px;
        }
        .cs-filter-strip {
            display:flex;
            gap:8px;
            flex-wrap:wrap;
        }
        .cs-filter-chip {
            display:inline-flex;
            align-items:center;
            padding:8px 12px;
            border-radius:999px;
            background:#e2f3ee;
            color:#0f766e;
            font-size:12px;
            font-weight:700;
            border:1px solid rgba(15,118,110,.12);
        }
        .cs-filter-chip-muted {
            background:#fff;
            color:#64748b;
            border-color:#dbe2ea;
        }
        .cs-control-card {
            border:1px solid var(--shell-line);
            border-radius:22px;
            background:linear-gradient(180deg,#ffffff 0%,#f8fafc 100%);
            box-shadow:0 12px 30px rgba(15,23,42,.06);
            padding:18px;
        }
        .cs-control-head {
            display:flex;
            justify-content:space-between;
            align-items:flex-end;
            gap:16px;
            flex-wrap:wrap;
            margin-bottom:14px;
        }
        .cs-control-head h2 {
            margin:0;
            font-size:1.05rem;
            letter-spacing:-.02em;
        }
        .cs-control-head p {
            margin:6px 0 0;
            color:var(--shell-muted);
            font-size:13px;
            line-height:1.55;
        }
        .cs-filter-grid {
            display:grid;
            grid-template-columns:repeat(auto-fit,minmax(170px,1fr));
            gap:12px;
        }
        .cs-filter-grid label {
            display:block;
            margin-bottom:6px;
            color:#475569;
            font-size:12px;
            font-weight:700;
        }
        .cs-input, .cs-select {
            width:100%;
            padding:11px 12px;
            border-radius:14px;
            border:1px solid #cbd5e1;
            background:#fff;
            color:#0f172a;
        }
        .cs-action-cluster {
            display:flex;
            gap:8px;
            flex-wrap:wrap;
            align-items:flex-end;
        }
        .cs-stat-grid {
            display:grid;
            grid-template-columns:repeat(auto-fit,minmax(220px,1fr));
            gap:14px;
        }
        .cs-stat-card {
            border-radius:20px;
            padding:18px;
            color:#fff;
            box-shadow:0 14px 28px rgba(15,23,42,.10);
        }
        .cs-stat-card-primary { background:linear-gradient(135deg,#0f766e,#14b8a6); }
        .cs-stat-card-info { background:linear-gradient(135deg,#1d4ed8,#38bdf8); }
        .cs-stat-card-warning { background:linear-gradient(135deg,#b45309,#f59e0b); }
        .cs-stat-card-dark { background:linear-gradient(135deg,#0f172a,#334155); }
        .cs-stat-label {
            font-size:11px;
            text-transform:uppercase;
            letter-spacing:.14em;
            opacity:.82;
        }
        .cs-stat-value {
            margin-top:10px;
            font-size:2rem;
            font-weight:800;
            letter-spacing:-.04em;
        }
        .cs-stat-note {
            margin-top:8px;
            font-size:12px;
            line-height:1.5;
            opacity:.82;
        }
        .cs-spotlight-grid {
            display:grid;
            grid-template-columns:repeat(auto-fit,minmax(220px,1fr));
            gap:14px;
        }
        .cs-spotlight-card {
            border:1px solid var(--shell-line);
            border-radius:18px;
            background:#fff;
            padding:16px;
            box-shadow:0 10px 24px rgba(15,23,42,.04);
        }
        .cs-spotlight-label {
            font-size:11px;
            text-transform:uppercase;
            letter-spacing:.12em;
            color:#64748b;
        }
        .cs-spotlight-title {
            margin-top:10px;
            font-size:1.15rem;
            font-weight:800;
            letter-spacing:-.03em;
            color:#0f172a;
        }
        .cs-spotlight-meta {
            margin-top:8px;
            font-size:12px;
            line-height:1.55;
            color:#475569;
        }
        .cs-table-card table {
            width:100%;
            border-collapse:separate;
            border-spacing:0;
        }
        .cs-table-card thead th {
            font-size:11px;
            text-transform:uppercase;
            letter-spacing:.11em;
            color:#64748b;
            background:#f8fafc;
            border-bottom:1px solid #dbe2ea;
        }
        .cs-table-card th, .cs-table-card td {
            text-align:left;
            padding:14px 12px;
            vertical-align:top;
        }
        .cs-table-card tbody tr:nth-child(odd) {
            background:rgba(248,250,252,.65);
        }
        .cs-table-card tbody tr:hover {
            background:rgba(226,232,240,.55);
        }
        .cs-pill {
            display:inline-flex;
            align-items:center;
            padding:5px 9px;
            border-radius:999px;
            background:#eff6ff;
            color:#1d4ed8;
            font-size:11px;
            font-weight:700;
            border:1px solid rgba(29,78,216,.10);
        }
        .cs-cell-sub {
            margin-top:4px;
            font-size:12px;
            color:#64748b;
            line-height:1.45;
        }
        @media (max-width: 900px) {
            .cs-control-head {
                align-items:flex-start;
            }
        }
    """

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>Customer Success Usage - Administración</title>
        <style>{_admin_workspace_styles("1480px")}{page_styles}</style>
    </head>
    <body>
        <div class="workspace-shell">
            {render_admin_navigation(current_empleado, "customer_success", subtitle="Consola de adopción y uso por usuario para Customer Success. Visible sólo para admin y superadmin.")}
            {_render_admin_workspace_hero(
                eyebrow="Customer Success",
                title="Uso de la plataforma",
                description="Lectura consolidada de quién usa qué y cuánto tiempo dentro de la plataforma autenticada.",
                actions_html=(
                    '<div class="cs-toolbar">'
                    f'<div class="cs-filter-strip">{"".join(filter_chips)}</div>'
                    '<div class="cs-control-card">'
                    '<div class="cs-control-head">'
                    '<div><h2>Pulso ejecutivo</h2><p>Vista inspirada en ExpenseDesk: KPIs arriba, navegación táctica y lectura operativa abajo.</p></div>'
                    '</div>'
                    f'<form method="GET" action="/admin/customer-success/uso">'
                    f'<div class="cs-filter-grid">'
                    f'<div><label>Días</label><input class="cs-input" type="number" min="1" max="180" name="days" value="{selected_days}"></div>'
                    f'<div><label>Área</label><select class="cs-select" name="area">{area_options}</select></div>'
                    f'<div><label>Torneo</label><input class="cs-input" type="text" name="tournament_id" value="{escape(selected_tournament_id)}" placeholder="UUID torneo"></div>'
                    f'<div><label>Cliente</label><input class="cs-input" type="text" name="customer_label" value="{escape(selected_customer_label)}" placeholder="cliente o dominio"></div>'
                    f'<div><label>Límite</label><input class="cs-input" type="number" min="5" max="200" name="limit" value="{max(5, min(int(limit or 25), 200))}"></div>'
                    '<div class="cs-action-cluster">'
                    f'<button type="submit" class="button">Actualizar</button>'
                    f'<a class="button secondary" href="/admin/customer-success/uso/export?days={selected_days}&area={quote(selected_area)}&tournament_id={quote(selected_tournament_id)}&customer_label={quote(selected_customer_label)}&limit={max(5, min(int(limit or 25), 200))}&view=users">CSV usuarios</a>'
                    f'<a class="button secondary" href="/admin/customer-success/uso/export?days={selected_days}&area={quote(selected_area)}&tournament_id={quote(selected_tournament_id)}&customer_label={quote(selected_customer_label)}&limit={max(5, min(int(limit or 25), 200))}&view=areas">CSV áreas</a>'
                    f'<a class="button secondary" href="/admin/customer-success/uso/export?days={selected_days}&area={quote(selected_area)}&tournament_id={quote(selected_tournament_id)}&customer_label={quote(selected_customer_label)}&limit={max(5, min(int(limit or 25), 200))}&view=pages">CSV páginas</a>'
                    f'<a class="button secondary" href="/admin/customer-success/uso/export?days={selected_days}&area={quote(selected_area)}&tournament_id={quote(selected_tournament_id)}&customer_label={quote(selected_customer_label)}&limit={max(5, min(int(limit or 25), 200))}&view=tournaments">CSV torneos</a>'
                    f'<a class="button secondary" href="/admin/customer-success/uso/export?days={selected_days}&area={quote(selected_area)}&tournament_id={quote(selected_tournament_id)}&customer_label={quote(selected_customer_label)}&limit={max(5, min(int(limit or 25), 200))}&view=cohorts">CSV cohortes</a>'
                    '</div>'
                    '</div>'
                    '</form>'
                    '</div>'
                    '</div>'
                ),
                side_html=(
                    f'<div class="eyebrow">Ventana</div>'
                    f'<div class="meta-grid">'
                    f'<div class="meta-card"><span>Días</span><strong>{selected_days}</strong><small>Rango del reporte</small></div>'
                    f'<div class="meta-card"><span>Usuarios</span><strong>{int(summary.get("active_users") or 0)}</strong><small>Con actividad registrada</small></div>'
                    f'<div class="meta-card"><span>Minutos</span><strong>{int(summary.get("total_active_minutes") or 0)}</strong><small>Buckets de actividad</small></div>'
                    f'<div class="meta-card"><span>Sesiones</span><strong>{int(summary.get("tracked_sessions") or 0)}</strong><small>Session keys activas</small></div>'
                    f"</div>"
                ),
            )}
            <section class="workspace-card" style="margin-bottom:18px;">
                <div class="workspace-section-title">Resumen</div>
                <div class="workspace-section-subtitle">Señales rápidas de adopción sobre páginas autenticadas con tracking activo.</div>
                <div class="cs-stat-grid" style="margin-top:14px;">
                    {quick_cards}
                </div>
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


@router.get("/admin/presupuestos", response_class=HTMLResponse)
async def admin_presupuestos(
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

    def _render_catalog_cuenta_select(*, selected_id: str = "") -> str:
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
            f'<select class="catalog-cuenta" name="cuenta_contable_ids" '
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
            return f"""
            <tr>
                <td>{escape(concept_name or "—")}</td>
                <td>{escape(str(tournament_label) or "—")}</td>
                <td>{escape(sub_proyecto or "Todas")}</td>
                <td>{escape(cuenta_label)}</td>
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
                <div class="table-shell" style="overflow:auto;">
                    <table id="catalog-partidas-table">
                        <thead>
                            <tr>
                                <th>Partida</th>
                                <th>Proyecto</th>
                                <th>Sub Proyecto</th>
                                <th>Cuenta Contable</th>
                                <th>Acciones</th>
                            </tr>
                        </thead>
                        <tbody id="catalog-partidas-body">
                            {''.join(catalog_editor_rows) or '<tr><td colspan="5">Sin partidas cargadas. Agrega filas nuevas o edita las existentes.</td></tr>'}
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
                {_render_catalog_table_row(row_index=9999, concept_id="", concept_name="", tournament_id=str(catalog_tournaments[0].id) if catalog_tournaments else "", sub_proyecto="", cuenta_contable_id="", action_cell_html="")}
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
            <div class="table-shell" style="margin-top:16px;overflow:auto;">
                <table>
                    <thead>
                        <tr>
                            <th>Partida</th>
                            <th>Proyecto</th>
                            <th>Sub Proyecto</th>
                            <th>Cuenta Contable</th>
                        </tr>
                    </thead>
                    <tbody>
                        {''.join(catalog_editor_rows) or '<tr><td colspan="4">Sin partidas cargadas.</td></tr>'}
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
                    <input type="number" step="0.01" min="0" name="budget_amount" value="{float(line.get('budget_amount') or 0):.2f}" placeholder="Monto" {'disabled' if not access.get("line_update") else ''}>
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
                <div style="grid-column:1/-1;"><label style="display:block;font-size:12px;font-weight:700;color:#475569;margin-bottom:4px;">Partida presupuestal</label><select name="budget_concept_id" required>{_render_budget_concept_options_for_line(tournament_code="", selected_id=None)}</select></div>
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
                    <div style="font-weight:800;">${float(item.get("comparison", {}).get("committed_total") or 0):,.2f}</div>
                </div>
                <div style="padding:8px;border:1px solid #e5e7eb;border-radius:10px;background:#f8fafc;">
                    <div style="color:#64748b;">Pagado</div>
                    <div style="font-weight:800;">${float(item.get("comparison", {}).get("paid_total") or 0):,.2f}</div>
                </div>
                <div style="padding:8px;border:1px solid #e5e7eb;border-radius:10px;background:#f8fafc;">
                    <div style="color:#64748b;">Real</div>
                    <div style="font-weight:800;">${float(item.get("comparison", {}).get("actual_total") or 0):,.2f}</div>
                </div>
            </div>
            <div style="margin-top:10px;display:flex;flex-wrap:wrap;gap:6px;">
                {''.join(f'<span style="display:inline-flex;padding:4px 8px;border-radius:999px;background:#e2e8f0;font-size:11px;">{escape(str(concept.get("concepto") or "sin concepto"))}</span>' for concept in item.get("top_concepts", [])[:5])}
            </div>
            <div style="margin-top:12px;display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;font-size:12px;">
                <div style="padding:8px;border:1px solid #e5e7eb;border-radius:10px;background:#f8fafc;">
                    <div style="color:#64748b;">Fases</div>
                    <div style="font-weight:800;">{escape(str(((item.get("breakdowns") or {}).get("by_phase") or [{}])[0].get("label") or "Sin fase"))}</div>
                </div>
                <div style="padding:8px;border:1px solid #e5e7eb;border-radius:10px;background:#f8fafc;">
                    <div style="color:#64748b;">Entidades</div>
                    <div style="font-weight:800;">{escape(str(((item.get("breakdowns") or {}).get("by_entity") or [{}])[0].get("label") or "Sin entidad"))}</div>
                </div>
                <div style="padding:8px;border:1px solid #e5e7eb;border-radius:10px;background:#f8fafc;">
                    <div style="color:#64748b;">Proveedor top</div>
                    <div style="font-weight:800;">{escape(str(((item.get("breakdowns") or {}).get("by_provider") or [{}])[0].get("label") or "Sin proveedor"))}</div>
                </div>
            </div>
            <div style="margin-top:12px;display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;font-size:12px;">
                {''.join(
                    f'<div style="padding:8px;border:1px solid #e5e7eb;border-radius:10px;background:#f8fafc;"><div style="color:#64748b;">{escape(str(scenario.get("label") or "Escenario"))}</div><div style="font-weight:800;">${float(scenario.get("projected_close_total") or 0):,.2f}</div><div style="color:#64748b;">{escape(str(scenario.get("health") or "sin dato"))}</div></div>'
                    for scenario in [item.get("scenarios", {}).get("optimistic", {}), item.get("scenarios", {}).get("base", {}), item.get("scenarios", {}).get("stressed", {})]
                )}
            </div>
        </div>
        """
        for item in tournaments[:8]
    )
    access_badges = "".join(
        f'<span style="display:inline-flex;padding:4px 8px;border-radius:999px;background:{"#dcfce7" if enabled else "#e5e7eb"};color:{"#166534" if enabled else "#64748b"};font-size:11px;font-weight:700;">{escape(label)}</span>'
        for label, enabled in [
            ("Leer", access.get("read", False)),
            ("Crear", access.get("create", False)),
            ("Editar versión", access.get("version_update", False)),
            ("Editar línea", access.get("line_update", False)),
            ("Aprobar", access.get("approve", False)),
            ("Congelar", access.get("freeze", False)),
            ("Exportar", access.get("export", False)),
            ("Auditoría", access.get("audit_read", False)),
        ]
    )
    budget_audit_html = "".join(
        f"""
        <tr>
            <td><code>{escape(str(item.get("event_type") or ""))}</code></td>
            <td>{escape(str(item.get("version_name") or "—"))}</td>
            <td>{escape(str(item.get("actor_nombre") or "Sistema"))}</td>
            <td>{escape(str(item.get("from_status") or "—"))} → {escape(str(item.get("to_status") or "—"))}</td>
            <td>{format_value(item.get("created_at"))}</td>
            <td><small style="color:#64748b;">{escape(json.dumps(item.get("payload") or {}, ensure_ascii=False)[:280])}</small></td>
        </tr>
        """
        for item in audit_events
    )
    active_version_label = (
        str(selected_version.get("version_name") or "Sin versión")
        if selected_version
        else "Sin versión"
    )
    active_version_status = (
        str(selected_version.get("status") or "sin status")
        if selected_version
        else "sin status"
    )
    active_tournament_name = (
        str(
            active_tournament.get("tournament_name")
            or active_tournament.get("tournament_code")
            or "Sin torneo"
        )
        if active_tournament
        else "Sin torneo seleccionado"
    )
    drill_state_label = (
        f"{str(drill_dimension or '').strip()}: {str(drill_value or '').strip()}"
        if line_drilldown.get("active")
        else "Sin drilldown activo"
    )
    top_alert = executive_alerts[0] if executive_alerts else None
    stressed_scenario = (
        scenarios.get("stressed", {}) if isinstance(scenarios, dict) else {}
    )
    filter_chips = [
        f'<span class="budget-filter-chip">Versión · {escape(active_version_label)}</span>',
        f'<span class="budget-filter-chip">Estado · {escape(active_version_status)}</span>',
        f'<span class="budget-filter-chip">Drill · {escape(drill_state_label)}</span>',
        f'<span class="budget-filter-chip">Torneo · {escape(active_tournament_name)}</span>',
        (
            f'<span class="budget-filter-chip">Documento · {escape(str(active_commitment.get("numero_referencia") or "-"))}</span>'
            if active_commitment
            else '<span class="budget-filter-chip budget-filter-chip-muted">Documento · sin selección</span>'
        ),
    ]
    quick_cards = "".join(
        [
            f"""
            <article class="budget-stat-card budget-stat-card-primary">
                <div class="budget-stat-label">Bolsa 2026</div>
                <div class="budget-stat-value">${float(summary.get("budget_total") or 0):,.2f}</div>
                <div class="budget-stat-note">{int(summary.get("line_count") or 0)} líneas activas en la versión cargada.</div>
            </article>
            """,
            f"""
            <article class="budget-stat-card budget-stat-card-info">
                <div class="budget-stat-label">Cierre proyectado</div>
                <div class="budget-stat-value">${float(forecast_summary.get("projected_close_total") or 0):,.2f}</div>
                <div class="budget-stat-note">Varianza ${float(forecast_summary.get("projected_variance") or 0):,.2f} con salud {escape(str(forecast_summary.get("health") or "sin dato"))}.</div>
            </article>
            """,
            f"""
            <article class="budget-stat-card budget-stat-card-warning">
                <div class="budget-stat-label">Caja próxima</div>
                <div class="budget-stat-value">${float(summary.get("due_next_30_total") or 0):,.2f}</div>
                <div class="budget-stat-note">${float(summary.get("pending_to_pay_total") or 0):,.2f} siguen pendientes por pagar.</div>
            </article>
            """,
            f"""
            <article class="budget-stat-card budget-stat-card-dark">
                <div class="budget-stat-label">Compromisos visibles</div>
                <div class="budget-stat-value">{len(tournament_commitments)}</div>
                <div class="budget-stat-note">{escape(active_tournament_name)} · {int(summary.get("tournaments_count") or 0)} torneos cubiertos.</div>
            </article>
            """,
        ]
    )
    scenario_player_html = f"""
        <div style="display:grid;grid-template-columns:minmax(280px,.9fr) minmax(320px,1.1fr);gap:16px;align-items:start;">
            <form method="GET" action="/admin/presupuestos" style="display:grid;gap:12px;padding:14px;border:1px solid #dbe2ea;border-radius:16px;background:#fff;">
                {scenario_hidden_inputs}
                <div>
                    <label style="display:block;font-size:12px;font-weight:800;color:#475569;margin-bottom:5px;">Cambio run-rate %</label>
                    <input class="budget-input" type="number" step="0.5" name="scenario_run_rate_delta_pct" value="{float(scenario_player.get('run_rate_delta_pct') or 0):.2f}">
                </div>
                <div>
                    <label style="display:block;font-size:12px;font-weight:800;color:#475569;margin-bottom:5px;">Recorte discrecional % sobre bolsa</label>
                    <input class="budget-input" type="number" step="0.5" min="0" name="scenario_discretionary_cut_pct" value="{float(scenario_player.get('discretionary_cut_pct') or 0):.2f}">
                </div>
                <div>
                    <label style="display:block;font-size:12px;font-weight:800;color:#475569;margin-bottom:5px;">Compromisos adicionales</label>
                    <input class="budget-input" type="number" step="1000" name="scenario_added_commitments" value="{float(scenario_player.get('added_commitments') or 0):.2f}">
                </div>
                <div>
                    <label style="display:block;font-size:12px;font-weight:800;color:#475569;margin-bottom:5px;">Aceleración de caja</label>
                    <input class="budget-input" type="number" step="1000" min="0" name="scenario_cash_acceleration" value="{float(scenario_player.get('cash_acceleration') or 0):.2f}">
                </div>
                <div style="display:flex;gap:8px;flex-wrap:wrap;">
                    <button type="submit" class="button">Jugar escenario</button>
                    <a class="button secondary" href="/admin/presupuestos{('?version_id=' + quote(str(selected_version.get('id')))) if selected_version else ''}">Reset player</a>
                </div>
                <div style="font-size:12px;color:#64748b;line-height:1.5;">Read-only: esto no altera versiones, líneas, gastos ni contabilidad. Sirve para discutir supuestos antes de reforecast o aprobación.</div>
            </form>
            <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;">
                <div style="padding:16px;border:1px solid #dbe2ea;border-radius:16px;background:#fff;">
                    <div style="font-size:11px;text-transform:uppercase;letter-spacing:.12em;color:#64748b;">Cierre jugado</div>
                    <div style="margin-top:8px;font-size:24px;font-weight:900;color:#0f172a;">${float(scenario_player.get('projected_close_total') or 0):,.2f}</div>
                    <div style="margin-top:6px;color:#475569;">Base ${float(scenario_player.get('base_close_total') or 0):,.2f}</div>
                </div>
                <div style="padding:16px;border:1px solid #dbe2ea;border-radius:16px;background:#fff;">
                    <div style="font-size:11px;text-transform:uppercase;letter-spacing:.12em;color:#64748b;">Varianza</div>
                    <div style="margin-top:8px;font-size:24px;font-weight:900;color:{'#991b1b' if float(scenario_player.get('projected_variance') or 0) < 0 else '#166534'};">${float(scenario_player.get('projected_variance') or 0):,.2f}</div>
                    <div style="margin-top:6px;color:#475569;">vs presupuesto ${float(scenario_player.get('budget_total') or 0):,.2f}</div>
                </div>
                <div style="padding:16px;border:1px solid #dbe2ea;border-radius:16px;background:#fff;">
                    <div style="font-size:11px;text-transform:uppercase;letter-spacing:.12em;color:#64748b;">Caja requerida</div>
                    <div style="margin-top:8px;font-size:24px;font-weight:900;color:#0f172a;">${float(scenario_player.get('projected_cash_need') or 0):,.2f}</div>
                    <div style="margin-top:6px;color:#475569;">después de aceleración</div>
                </div>
                <div style="padding:16px;border:1px solid #dbe2ea;border-radius:16px;background:#fff;">
                    <div style="font-size:11px;text-transform:uppercase;letter-spacing:.12em;color:#64748b;">Salud</div>
                    <div style="margin-top:8px;font-size:24px;font-weight:900;color:#0f172a;">{escape(str(scenario_player.get('health') or 'sin dato'))}</div>
                    <div style="margin-top:6px;color:#475569;">{escape(str(scenario_player.get('recommendation') or ''))}</div>
                </div>
            </div>
        </div>
    """
    spotlight_cards = "".join(
        [
            f"""
            <article class="budget-spotlight-card">
                <div class="budget-spotlight-label">Riesgo principal</div>
                <div class="budget-spotlight-title">{escape(str(top_alert.get("title") if top_alert else "Sin alerta crítica"))}</div>
                <div class="budget-spotlight-meta">{escape(str(top_alert.get("detail") if top_alert else "El snapshot no reporta tensiones críticas en este corte."))}</div>
            </article>
            """,
            f"""
            <article class="budget-spotlight-card">
                <div class="budget-spotlight-label">Escenario estresado</div>
                <div class="budget-spotlight-title">${float(stressed_scenario.get("projected_close_total") or 0):,.2f}</div>
                <div class="budget-spotlight-meta">Health {escape(str(stressed_scenario.get("health") or "sin dato"))} · ajuste {float(stressed_scenario.get("adjustment_vs_base_pct") or 0):,.2f}% vs base.</div>
            </article>
            """,
            f"""
            <article class="budget-spotlight-card">
                <div class="budget-spotlight-label">Versión activa</div>
                <div class="budget-spotlight-title">{escape(active_version_label)}</div>
                <div class="budget-spotlight-meta">Status {escape(active_version_status)} · {int(selected_version.get("line_count") if selected_version else 0)} líneas · ${float(selected_version.get("budget_total") if selected_version else 0):,.2f}.</div>
            </article>
            """,
            f"""
            <article class="budget-spotlight-card">
                <div class="budget-spotlight-label">Compromiso abierto</div>
                <div class="budget-spotlight-title">{escape(str(active_commitment.get("numero_referencia") or "Sin documento")) if active_commitment else "Sin documento"}</div>
                <div class="budget-spotlight-meta">{escape(str(active_commitment.get("proveedor_nombre") or "Selecciona un torneo para ver compromisos")) if active_commitment else "Selecciona un torneo desde el snapshot para bajar al compromiso y al gasto."}</div>
            </article>
            """,
        ]
    )
    page_styles = """
        .budget-toolbar { display:grid; gap:14px; }
        .budget-filter-strip { display:flex; flex-wrap:wrap; gap:8px; }
        .budget-filter-chip {
            display:inline-flex;
            align-items:center;
            padding:7px 11px;
            border-radius:999px;
            background:rgba(15,118,110,.10);
            border:1px solid rgba(15,118,110,.16);
            color:#0f766e;
            font-size:11px;
            font-weight:700;
            letter-spacing:.04em;
        }
        .budget-filter-chip-muted { background:#f8fafc; border-color:#dbe2ea; color:#64748b; }
        .budget-control-card {
            border:1px solid rgba(15,23,42,.08);
            border-radius:20px;
            background:linear-gradient(135deg,rgba(255,255,255,.96),rgba(241,245,249,.96));
            padding:18px;
            box-shadow:0 10px 24px rgba(15,23,42,.05);
        }
        .budget-control-head h2 { margin:0; font-size:1.08rem; letter-spacing:-.02em; color:#0f172a; }
        .budget-control-head p { margin:6px 0 0; color:#475569; font-size:13px; line-height:1.55; }
        .budget-filter-grid {
            display:grid;
            grid-template-columns:repeat(auto-fit,minmax(180px,1fr));
            gap:14px;
            margin-top:14px;
        }
        .budget-filter-grid label {
            display:block;
            margin-bottom:6px;
            color:#475569;
            font-size:12px;
            font-weight:700;
        }
        .budget-input, .budget-select {
            width:100%;
            padding:11px 12px;
            border-radius:14px;
            border:1px solid #cbd5e1;
            background:#fff;
            color:#0f172a;
        }
        .budget-action-cluster { display:flex; flex-wrap:wrap; gap:10px; align-items:flex-end; }
        .budget-stat-grid {
            display:grid;
            grid-template-columns:repeat(auto-fit,minmax(230px,1fr));
            gap:14px;
        }
        .budget-stat-card {
            border-radius:20px;
            padding:18px;
            color:#fff;
            box-shadow:0 14px 28px rgba(15,23,42,.10);
        }
        .budget-stat-card-primary { background:linear-gradient(135deg,#0f766e,#14b8a6); }
        .budget-stat-card-info { background:linear-gradient(135deg,#1d4ed8,#38bdf8); }
        .budget-stat-card-warning { background:linear-gradient(135deg,#b45309,#f59e0b); }
        .budget-stat-card-dark { background:linear-gradient(135deg,#0f172a,#334155); }
        .budget-stat-label { font-size:11px; text-transform:uppercase; letter-spacing:.14em; opacity:.82; }
        .budget-stat-value { margin-top:10px; font-size:2rem; font-weight:800; letter-spacing:-.04em; }
        .budget-stat-note { margin-top:8px; font-size:12px; line-height:1.5; opacity:.82; }
        .budget-spotlight-grid {
            display:grid;
            grid-template-columns:repeat(auto-fit,minmax(220px,1fr));
            gap:14px;
        }
        .budget-spotlight-card {
            border:1px solid var(--shell-line);
            border-radius:18px;
            background:#fff;
            padding:16px;
            box-shadow:0 10px 24px rgba(15,23,42,.04);
        }
        .budget-spotlight-label { font-size:11px; text-transform:uppercase; letter-spacing:.12em; color:#64748b; }
        .budget-spotlight-title { margin-top:10px; font-size:1.15rem; font-weight:800; letter-spacing:-.03em; color:#0f172a; }
        .budget-spotlight-meta { margin-top:8px; font-size:12px; line-height:1.55; color:#475569; }
        .budget-table-shell table { width:100%; border-collapse:separate; border-spacing:0; }
        .budget-table-shell thead th {
            font-size:11px;
            text-transform:uppercase;
            letter-spacing:.11em;
            color:#64748b;
            background:#f8fafc;
            border-bottom:1px solid #dbe2ea;
        }
        .budget-table-shell th, .budget-table-shell td { text-align:left; padding:14px 12px; vertical-align:top; }
        .budget-table-shell tbody tr:nth-child(odd) { background:rgba(248,250,252,.65); }
        .budget-table-shell tbody tr:hover { background:rgba(226,232,240,.55); }
    """
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>Presupuestos - Administración</title>
        <style>{_admin_workspace_styles("1380px")}{page_styles}</style>
    </head>
    <body>
        <div class="workspace-shell">
            {render_admin_navigation(current_empleado, "presupuestos", subtitle="Presupuesto canónico base 2026 con snapshot read-only, importación formal y puente hacia C-suite.")}
            {_render_admin_workspace_hero(
                eyebrow="C-suite",
                title="Presupuestos 2026",
                description="Presupuesto canónico 2026 con lectura ejecutiva, puente a compromisos reales y cobertura compartida para UI, operaciones y assistant.",
                actions_html=(
                    '<div class="budget-toolbar">'
                    f'<div class="budget-filter-strip">{"".join(filter_chips)}</div>'
                    '<div class="budget-control-card">'
                    '<div class="budget-control-head">'
                    '<div><h2>Pulso ejecutivo</h2><p>Vista inspirada en ExpenseDesk: navegación táctica arriba, lectura financiera y operativa abajo, sin duplicar lógica del snapshot.</p></div>'
                    '</div>'
                    '<form method="GET" action="/admin/presupuestos">'
                    '<div class="budget-filter-grid">'
                    f'<div><label>Versión</label><select class="budget-select" name="version_id">{version_options or "<option value=\"\">Sin versiones</option>"}</select></div>'
                    f'<div><label>Dimensión</label><input class="budget-input" type="text" name="drill_dimension" value="{escape(str(drill_dimension or ""))}" placeholder="owner, account, concept"></div>'
                    f'<div><label>Valor</label><input class="budget-input" type="text" name="drill_value" value="{escape(str(drill_value or ""))}" placeholder="Operaciones Norte"></div>'
                    f'<div><label>Torneo</label><input class="budget-input" type="text" name="drill_tournament" value="{escape(str(drill_tournament or ""))}" placeholder="UUID o código"></div>'
                    f'<div><label>Documento</label><input class="budget-input" type="text" name="drill_document" value="{escape(str(drill_document or ""))}" placeholder="doc-123"></div>'
                    '<div class="budget-action-cluster">'
                    '<button type="submit" class="button">Actualizar lectura</button>'
                    '<a class="button secondary" href="/admin/presupuestos">Limpiar filtros</a>'
                    + (
                        f'<a class="button secondary" href="/admin/presupuestos/export.xlsx{("?version_id=" + quote(str(selected_version.get("id")))) if selected_version else ""}">Descargar Excel</a>'
                        if access.get("export")
                        else '<span class="button secondary" style="cursor:default;">Sin permiso para exportar</span>'
                    )
                    + (
                        '<button type="submit" formmethod="post" formaction="/admin/presupuestos/import-default" onclick="return confirm(\'¿Importar el borrador 2026 a tablas canónicas?\');" class="button secondary">Importar borrador 2026</button>'
                        if access.get("create")
                        else '<span class="button secondary" style="cursor:default;">Sin permiso para importar</span>'
                    )
                    + '</div>'
                    '</div>'
                    '</form>'
                    '</div>'
                    '</div>'
                ),
                side_html=(
                    f'<div class="eyebrow">Snapshot activo</div>'
                    f'<div class="meta-grid">'
                    f'<div class="meta-card"><span>Fuente</span><strong>{escape(str(snapshot.get("source") or "sin snapshot"))}</strong><small>DB o artefacto base</small></div>'
                    f'<div class="meta-card"><span>Torneos</span><strong>{int(summary.get("tournaments_count") or 0)}</strong><small>Cobertura visible 2026</small></div>'
                    f'<div class="meta-card"><span>Real</span><strong>${float(summary.get("actual_total") or 0):,.2f}</strong><small>Gasto observado al corte</small></div>'
                    f'<div class="meta-card"><span>Pagado</span><strong>${float(summary.get("paid_total") or 0):,.2f}</strong><small>Salida de caja ejecutada</small></div>'
                    f'</div>'
                ),
            )}
            {success_html}
            {error_html}
            <section class="workspace-card" style="margin-bottom:18px;">
                <div class="workspace-section-title">Resumen</div>
                <div class="workspace-section-subtitle">Bolsa, proyección, caja y presión operativa en una sola lectura.</div>
                <div class="budget-stat-grid" style="margin-top:14px;">
                    {quick_cards}
                </div>
            </section>
            <section class="workspace-card" style="margin-bottom:18px;">
                <div class="workspace-section-title">Spotlight</div>
                <div class="workspace-section-subtitle">La versión, el riesgo, el escenario y el compromiso que más rápido explican el estado del presupuesto.</div>
                <div class="budget-spotlight-grid" style="margin-top:14px;">
                    {spotlight_cards}
                </div>
            </section>
            <section class="workspace-card" style="margin-bottom:18px;">
                <div class="workspace-section-title">Acceso efectivo</div>
                <div class="workspace-section-subtitle">Permisos finos activos para esta sesión en presupuesto.</div>
                <div style="display:flex;flex-wrap:wrap;gap:8px;margin-top:12px;">{access_badges}</div>
            </section>
            <section class="workspace-card" style="margin-bottom:18px;">
                <div class="workspace-section-title">Origen del borrador</div>
                <div class="workspace-section-subtitle">Artefacto base usado para la siembra inicial del módulo.</div>
                <div style="margin-top:10px;padding:12px 14px;border:1px solid #dbe2ea;border-radius:14px;background:#fff;">
                    <code>{escape(str(DEFAULT_BUDGET_ARTIFACT))}</code>
                </div>
            </section>
            <section class="workspace-card" style="margin-bottom:18px;">
                <div class="workspace-section-title">Crear presupuesto desde cero</div>
                <div class="workspace-section-subtitle">Abre un borrador vacío, agrega líneas manuales y después usa el mismo flujo de someter, aprobar, congelar y reforecast.</div>
                <div style="margin-top:12px;padding:14px;border:1px solid #dbe2ea;border-radius:16px;background:#fff;">
                    {create_version_form}
                </div>
            </section>
            <section class="workspace-card budget-table-shell" style="margin-bottom:18px;">
                <div class="workspace-section-title">Versiones registradas</div>
                <table>
                    <thead>
                        <tr>
                            <th>Año</th>
                            <th>Versión</th>
                            <th>Status</th>
                            <th>Source</th>
                            <th>Artefacto</th>
                            <th>Actualizado</th>
                            <th>Acciones</th>
                        </tr>
                    </thead>
                    <tbody>
                        {version_rows if version_rows else '<tr><td colspan="7">No hay versiones en DB todavía.</td></tr>'}
                    </tbody>
                </table>
            </section>
            <section class="workspace-card" style="margin-bottom:18px;">
                <div class="workspace-section-title">Versión activa</div>
                <div class="workspace-section-subtitle">Editar solo aplica sobre `draft` o `reforecast`. Las acciones del asistente usan estas mismas guardas.</div>
                <div style="display:grid;grid-template-columns:1.1fr .9fr;gap:16px;margin-top:12px;">
                    <div style="padding:14px;border:1px solid #dbe2ea;border-radius:16px;background:#fff;">
                        <form method="GET" action="/admin/presupuestos" style="margin-bottom:12px;">
                            <label style="display:block;font-weight:700;margin-bottom:6px;">Seleccionar versión</label>
                            <select name="version_id" style="width:100%;padding:10px;border:1px solid #cbd5e1;border-radius:12px;">{version_options or '<option value="">Sin versiones</option>'}</select>
                            <button type="submit" style="margin-top:10px;background:#0f766e;color:#fff;border:none;border-radius:999px;padding:10px 14px;font-weight:700;cursor:pointer;">Cargar versión</button>
                        </form>
                        {selected_version_edit_form}
                    </div>
                    <div style="padding:14px;border:1px solid #dbe2ea;border-radius:16px;background:#fff;">
                        <div style="font-size:12px;text-transform:uppercase;letter-spacing:.14em;color:#64748b;">Estado actual</div>
                        <div style="margin-top:8px;font-size:24px;font-weight:800;color:#0f172a;">{escape(str(selected_version.get("status") if selected_version else "sin versión"))}</div>
                        <div style="margin-top:10px;color:#475569;">{escape(str(selected_version.get("version_name") if selected_version else ""))}</div>
                        <div style="margin-top:10px;color:#475569;">Monto: ${float(selected_version.get("budget_total") if selected_version else 0):,.2f}</div>
                        <div style="margin-top:6px;color:#475569;">Líneas: {int(selected_version.get("line_count") if selected_version else 0)}</div>
                        <div style="margin-top:6px;color:#475569;">Actualizado: {escape(str(selected_version.get("updated_at") if selected_version else "—"))}</div>
                    </div>
                </div>
            </section>
            <section class="workspace-card">
                <div class="workspace-section-title">Snapshot 2026</div>
                <div class="workspace-section-subtitle">Vista read-only que también puede consumir `/folders` y el asistente, ya con comparativo contra solicitudes, comprometido, pagado y gasto real.</div>
                <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px;margin-top:14px;">
                    {tournament_cards or '<div style="color:#64748b;">Sin torneos presupuestales disponibles todavía.</div>'}
                </div>
            </section>
            <section class="workspace-card" style="margin-top:18px;">
                <div class="workspace-section-title">Forecast / flujo</div>
                <div class="workspace-section-subtitle">Planeador presupuestal base con run-rate, pendiente por pagar y señales de riesgo inmediato.</div>
                <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px;margin-top:14px;">
                    <div style="padding:14px;border:1px solid #dbe2ea;border-radius:14px;background:#fff;">
                        <div style="font-size:11px;text-transform:uppercase;letter-spacing:.14em;color:#64748b;">Cierre proyectado</div>
                        <div style="margin-top:8px;font-size:24px;font-weight:800;color:#0f172a;">${float(forecast_summary.get("projected_close_total") or 0):,.2f}</div>
                        <div style="margin-top:6px;color:#475569;">Varianza: ${float(forecast_summary.get("projected_variance") or 0):,.2f}</div>
                    </div>
                    <div style="padding:14px;border:1px solid #dbe2ea;border-radius:14px;background:#fff;">
                        <div style="font-size:11px;text-transform:uppercase;letter-spacing:.14em;color:#64748b;">Necesidad de caja</div>
                        <div style="margin-top:8px;font-size:24px;font-weight:800;color:#0f172a;">${float(forecast_summary.get("projected_cash_need") or 0):,.2f}</div>
                        <div style="margin-top:6px;color:#475569;">Pendiente 30 días: ${float(summary.get("due_next_30_total") or 0):,.2f}</div>
                    </div>
                    <div style="padding:14px;border:1px solid #dbe2ea;border-radius:14px;background:#fff;">
                        <div style="font-size:11px;text-transform:uppercase;letter-spacing:.14em;color:#64748b;">Run rate diario</div>
                        <div style="margin-top:8px;font-size:24px;font-weight:800;color:#0f172a;">${float(forecast_summary.get("run_rate_daily") or 0):,.2f}</div>
                        <div style="margin-top:6px;color:#475569;">Días restantes: {int(forecast_summary.get("remaining_days") or 0)}</div>
                    </div>
                    <div style="padding:14px;border:1px solid #dbe2ea;border-radius:14px;background:#fff;">
                        <div style="font-size:11px;text-transform:uppercase;letter-spacing:.14em;color:#64748b;">Salud forecast</div>
                        <div style="margin-top:8px;font-size:24px;font-weight:800;color:#0f172a;">{escape(str(forecast_summary.get("health") or "sin dato"))}</div>
                        <div style="margin-top:6px;color:#475569;">Over budget: {int((summary.get("forecast_health_counts") or {}).get("over_budget") or 0)} torneos</div>
                    </div>
                </div>
            </section>
            <section class="workspace-card" style="margin-top:18px;">
                <div class="workspace-section-title">Comparativo ejecutivo</div>
                <div class="workspace-section-subtitle">Presupuesto vs solicitado vs comprometido vs pagado vs real vs cierre proyectado, desde el mismo snapshot canónico.</div>
                <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px;margin-top:14px;">
                    {''.join(render_executive_comparison_card(item) for item in executive_comparison)}
                </div>
            </section>
            <section class="workspace-card" style="margin-top:18px;">
                <div class="workspace-section-title">Alertas ejecutivas / playbooks</div>
                <div class="workspace-section-subtitle">Señales accionables para dirección y finanzas usando el mismo snapshot canónico, sin lógica paralela.</div>
                <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px;margin-top:14px;">
                    {''.join(render_executive_alert_card(item) for item in executive_alerts) if executive_alerts else '<div style="padding:14px;border:1px solid #bbf7d0;border-radius:14px;background:#f0fdf4;color:#166534;">Sin alertas críticas activas en este snapshot. El presupuesto se mantiene en rango saludable.</div>'}
                </div>
            </section>
            <section class="workspace-card" style="margin-top:18px;">
                <div class="workspace-section-title">Escenarios formales</div>
                <div class="workspace-section-subtitle">Lectura ejecutiva `optimista`, `base` y `estresado` sobre el mismo snapshot canónico.</div>
                <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px;margin-top:14px;">
                    {''.join(render_scenario_card(item) for item in [scenarios.get("optimistic", {}), scenarios.get("base", {}), scenarios.get("stressed", {})])}
                </div>
            </section>
            <section class="workspace-card" style="margin-top:18px;">
                <div class="workspace-section-title">Scenario player</div>
                <div class="workspace-section-subtitle">Juega supuestos de run-rate, recorte discrecional, compromisos nuevos y caja sin mutar el presupuesto activo.</div>
                <div style="margin-top:14px;">
                    {scenario_player_html}
                </div>
            </section>
            <section class="workspace-card" style="margin-top:18px;">
                <div class="workspace-section-title">Desglose fino</div>
                <div class="workspace-section-subtitle">Comparativo operativo-financiero por concepto, proveedor, fase, entidad, responsable y cuenta final, desde el mismo snapshot canónico.</div>
                <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px;margin-top:14px;">
                    {render_breakdown_column("Conceptos", breakdowns.get("by_concept", []), dimension_key="concept", primary_key="budget_total", secondary_key="actual_total", empty_label="Sin conceptos desglosados todavía.")}
                    {render_breakdown_column("Proveedores", breakdowns.get("by_provider", []), dimension_key="provider", primary_key="committed_total", secondary_key="actual_total", empty_label="Sin proveedor asociado todavía.")}
                    {render_breakdown_column("Fases", breakdowns.get("by_phase", []), dimension_key="phase", primary_key="budget_total", secondary_key="reference_total", empty_label="Sin fases visibles todavía.")}
                    {render_breakdown_column("Entidades", breakdowns.get("by_entity", []), dimension_key="entity", primary_key="budget_total", secondary_key="reference_total", empty_label="Sin entidad presupuestal visible todavía.")}
                    {render_breakdown_column("Responsables", breakdowns.get("by_owner", []), dimension_key="owner", primary_key="budget_total", secondary_key="reference_total", empty_label="Sin responsable visible todavía.")}
                    {render_breakdown_column("Cuenta final", breakdowns.get("by_account", []), dimension_key="account", primary_key="budget_total", secondary_key="reference_total", empty_label="Sin cuenta final visible todavía.")}
                </div>
            </section>
            {catalog_management_html}
            <section class="workspace-card budget-table-shell" style="margin-top:18px;">
                <div class="workspace-section-title">Líneas editables</div>
                <div class="workspace-section-subtitle">Edición controlada de conceptos, cuenta final, owner, fase y monto. La varianza se recalcula automáticamente.</div>
                <div style="margin:12px 0 16px 0;padding:14px;border:1px solid #dbe2ea;border-radius:14px;background:#fff;">{drill_summary_html}</div>
                {create_line_form}
                <table>
                    <thead>
                        <tr>
                            <th>Torneo</th>
                            <th>Edición</th>
                            <th>Control</th>
                        </tr>
                    </thead>
                    <tbody>
                        {line_rows if line_rows else '<tr><td colspan="3">Sin líneas para la versión seleccionada.</td></tr>'}
                    </tbody>
                </table>
            </section>
            <section class="workspace-card budget-table-shell" style="margin-top:18px;">
                <div class="workspace-section-title">Compromisos reales del torneo</div>
                <div class="workspace-section-subtitle">Solicitudes/documentos reales del torneo seleccionado desde el snapshot operativo, para conectar presupuesto con compromiso vivo sin escribir nada sobre el aprobado.</div>
                <div style="margin:12px 0 16px 0;padding:14px;border:1px solid #dbe2ea;border-radius:14px;background:#fff;">
                    {f'<div style="font-size:12px;text-transform:uppercase;letter-spacing:.14em;color:#64748b;">Torneo activo</div><div style="margin-top:6px;font-size:20px;font-weight:800;color:#0f172a;">{escape(str(active_tournament.get("tournament_name") or active_tournament.get("tournament_code") or "Torneo"))}</div><div style="margin-top:6px;color:#475569;">{len(tournament_commitments)} compromisos visibles en esta lectura.</div>' if active_tournament else '<div style="color:#64748b;">Selecciona un torneo desde el snapshot 2026 para ver sus compromisos reales.</div>'}
                </div>
                <div style="margin:0 0 16px 0;padding:14px;border:1px solid #dbe2ea;border-radius:14px;background:#fff;">{active_commitment_html}</div>
                <table>
                    <thead>
                        <tr>
                            <th>Referencia</th>
                            <th>Estado</th>
                            <th>Proveedor</th>
                            <th>Concepto</th>
                            <th>Solicitado</th>
                            <th>Total</th>
                            <th>Gasto real</th>
                            <th>Fecha pago</th>
                            <th>Creado</th>
                        </tr>
                    </thead>
                    <tbody>
                        {commitment_rows if commitment_rows else '<tr><td colspan="8">Sin compromisos visibles para el torneo seleccionado.</td></tr>'}
                    </tbody>
                </table>
            </section>
            <section class="workspace-card budget-table-shell" style="margin-top:18px;">
                <div class="workspace-section-title">Auditoría presupuestal</div>
                <div class="workspace-section-subtitle">Bitácora expandida por versión/línea. Visible sólo si la sesión tiene permiso de auditoría.</div>
                {f'<table><thead><tr><th>Evento</th><th>Versión</th><th>Actor</th><th>Estado</th><th>Fecha</th><th>Payload</th></tr></thead><tbody>{budget_audit_html if budget_audit_html else "<tr><td colspan=\"6\">Sin eventos todavía.</td></tr>"}</tbody></table>' if access.get("audit_read") else '<div style="margin-top:10px;color:#64748b;">Sin permiso para ver la auditoría presupuestal.</div>'}
            </section>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html)


@router.post("/admin/presupuestos/import-default")
async def admin_presupuestos_import_default(
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = Depends(get_current_empleado),
):
    _require_budget_access(current_empleado, "create")
    try:
        result = await import_budget_artifact(
            session=session,
            artifact_path=DEFAULT_BUDGET_ARTIFACT,
            edition_year=2026,
            version_name="Presupuesto 2026 Draft",
            status="draft",
            source="artifact_csv",
            created_by_empleado_id=str(current_empleado.id),
            replace_existing=False,
        )
        msg = (
            "Presupuesto 2026 importado"
            if result.get("created")
            else "La versión 2026 ya existía"
        )
        return RedirectResponse(
            url=f"/admin/presupuestos?success_msg={quote(msg)}", status_code=303
        )
    except Exception as exc:
        await session.rollback()
        logger.exception(
            "Unexpected error importing default budget artifact",
            extra={"actor_id": str(getattr(current_empleado, "id", ""))},
        )
        return RedirectResponse(
            url=_presupuestos_redirect_url(error_msg=_OPERATION_GENERIC_ERROR),
            status_code=303,
        )


def _presupuestos_redirect_url(
    *,
    version_id: Optional[str] = None,
    success_msg: Optional[str] = None,
    error_msg: Optional[str] = None,
    drill_dimension: Optional[str] = None,
    drill_value: Optional[str] = None,
    drill_tournament: Optional[str] = None,
    drill_document: Optional[str] = None,
) -> str:
    params: list[str] = []
    for key, value in [
        ("version_id", version_id),
        ("success_msg", success_msg),
        ("error_msg", error_msg),
        ("drill_dimension", drill_dimension),
        ("drill_value", drill_value),
        ("drill_tournament", drill_tournament),
        ("drill_document", drill_document),
    ]:
        if value:
            params.append(f"{key}={quote(str(value))}")
    query = f"?{'&'.join(params)}" if params else ""
    return f"/admin/presupuestos{query}"


@router.post("/admin/presupuestos/conceptos/bulk-save")
async def admin_presupuestos_bulk_save_concepts(
    concept_ids: List[str] = Form([]),
    concept_names: List[str] = Form([]),
    tournament_ids: List[str] = Form([]),
    sub_proyectos: List[str] = Form([]),
    cuenta_contable_ids: List[str] = Form([]),
    version_id: Optional[str] = Form(None),
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = Depends(get_current_empleado),
):
    _require_budget_access(current_empleado, "line_update")
    try:
        rows: list[dict[str, Any]] = []
        row_count = max(
            len(concept_ids),
            len(concept_names),
            len(tournament_ids),
            len(sub_proyectos),
            len(cuenta_contable_ids),
        )
        for index in range(row_count):
            concept_name = (concept_names[index] if index < len(concept_names) else "").strip()
            concept_id = (concept_ids[index] if index < len(concept_ids) else "").strip()
            tournament_id = (tournament_ids[index] if index < len(tournament_ids) else "").strip()
            sub_proyecto = (sub_proyectos[index] if index < len(sub_proyectos) else "").strip()
            cuenta_contable_id = (
                cuenta_contable_ids[index] if index < len(cuenta_contable_ids) else ""
            ).strip()
            if not concept_name and not concept_id:
                continue
            rows.append(
                {
                    "concept_id": concept_id or None,
                    "concept_name": concept_name,
                    "tournament_id": tournament_id,
                    "sub_proyecto": sub_proyecto,
                    "cuenta_contable_id": cuenta_contable_id or None,
                }
            )
        result = await bulk_save_budget_concepts(
            session,
            rows=rows,
            actor_empleado_id=str(current_empleado.id),
        )
        msg = (
            "Catálogo actualizado: "
            f"{int(result.get('updated') or 0)} editada(s), "
            f"{int(result.get('created') or 0)} nueva(s)."
        )
        return RedirectResponse(
            url=_presupuestos_redirect_url(
                version_id=version_id,
                success_msg=msg,
            ),
            status_code=303,
        )
    except ValueError as exc:
        await session.rollback()
        return RedirectResponse(
            url=_presupuestos_redirect_url(
                version_id=version_id,
                error_msg=str(exc)[:180],
            ),
            status_code=303,
        )
    except Exception as exc:
        await session.rollback()
        logger.exception(
            "Unexpected error bulk-saving budget concepts",
            extra={
                "version_id": version_id or "",
                "actor_id": str(getattr(current_empleado, "id", "")),
            },
        )
        return RedirectResponse(
            url=_presupuestos_redirect_url(
                version_id=version_id,
                error_msg=_OPERATION_GENERIC_ERROR,
            ),
            status_code=303,
        )


@router.post("/admin/presupuestos/conceptos/{concept_id}/hide")
async def admin_presupuestos_hide_concept(
    concept_id: UUIDType,
    version_id: Optional[str] = Form(None),
    drill_dimension: Optional[str] = Form(None),
    drill_value: Optional[str] = Form(None),
    drill_tournament: Optional[str] = Form(None),
    drill_document: Optional[str] = Form(None),
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = Depends(get_current_empleado),
):
    _require_budget_access(current_empleado, "line_update")
    redirect_params = {
        "version_id": version_id,
        "drill_dimension": drill_dimension,
        "drill_value": drill_value,
        "drill_tournament": drill_tournament,
        "drill_document": drill_document,
    }
    try:
        hidden = await hide_budget_concept(
            session,
            concept_id=str(concept_id),
            actor_empleado_id=str(current_empleado.id),
        )
        label = str(hidden.get("concept_name") or "Partida").strip() or "Partida"
        return RedirectResponse(
            url=_presupuestos_redirect_url(
                **redirect_params,
                success_msg=f"«{label}» quitada del catálogo visible.",
            ),
            status_code=303,
        )
    except ValueError as exc:
        return RedirectResponse(
            url=_presupuestos_redirect_url(
                **redirect_params,
                error_msg=str(exc)[:180],
            ),
            status_code=303,
        )
    except Exception as exc:
        await session.rollback()
        logger.exception(
            "Unexpected error hiding budget concept",
            extra={
                "concept_id": str(concept_id),
                "actor_id": str(getattr(current_empleado, "id", "")),
            },
        )
        return RedirectResponse(
            url=_presupuestos_redirect_url(
                **redirect_params,
                error_msg=_OPERATION_GENERIC_ERROR,
            ),
            status_code=303,
        )


@router.post("/admin/presupuestos/versiones/{version_id}/lineas/import")
async def admin_presupuestos_import_lines(
    version_id: UUIDType,
    archivo_presupuesto: UploadFile = File(...),
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = Depends(get_current_empleado),
):
    _require_budget_access(current_empleado, "line_update")
    try:
        payload = await read_upload_limited(
            archivo_presupuesto,
            max_bytes=MAX_DECODE_BYTES,
            too_large_message="El archivo de presupuesto excede el tamaño máximo permitido.",
        )
        if not payload:
            raise ValueError("El archivo de presupuesto está vacío.")
        result = await import_budget_lines_upload(
            session,
            version_id=str(version_id),
            actor_empleado_id=str(current_empleado.id),
            file_bytes=payload,
            filename=archivo_presupuesto.filename or "presupuesto.xlsx",
        )
        msg = (
            "Líneas presupuestales cargadas: "
            f"{int(result.get('rows_processed') or 0)} fila(s)"
        )
        return RedirectResponse(
            url=f"/admin/presupuestos?version_id={quote(str(version_id))}&success_msg={quote(msg)}",
            status_code=303,
        )
    except ValueError as exc:
        await session.rollback()
        return RedirectResponse(
            url=f"/admin/presupuestos?version_id={quote(str(version_id))}&error_msg={quote(str(exc)[:180])}",
            status_code=303,
        )
    except Exception as exc:
        await session.rollback()
        logger.exception(
            "Unexpected error importing budget lines",
            extra={
                "version_id": str(version_id),
                "filename": archivo_presupuesto.filename or "",
                "actor_id": str(getattr(current_empleado, "id", "")),
            },
        )
        return RedirectResponse(
            url=f"/admin/presupuestos?version_id={quote(str(version_id))}&error_msg={quote(_OPERATION_GENERIC_ERROR)}",
            status_code=303,
        )


@router.get("/admin/presupuestos/export.xlsx")
async def admin_presupuestos_export_xlsx(
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = Depends(get_current_empleado),
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
        await list_budget_lines(session, version_id=selected_version["id"], limit=500)
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
        filename = f"presupuesto_2026_{str(selected_version.get('version_name') or 'version').lower().replace(' ', '_')}"
    headers = {"Content-Disposition": f'attachment; filename="{filename}.xlsx"'}
    return Response(
        content=payload,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers,
    )


@router.post("/admin/presupuestos/versiones/create")
async def admin_presupuestos_create_version(
    version_name: str = Form(...),
    notes: Optional[str] = Form(None),
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = Depends(get_current_empleado),
):
    _require_budget_access(current_empleado, "create")
    try:
        version = await create_budget_version(
            session,
            edition_year=2026,
            version_name=version_name,
            notes=notes,
            created_by_empleado_id=str(current_empleado.id),
        )
        msg = f"Borrador creado: {version.get('version_name') or version_name}"
        return RedirectResponse(
            url=f"/admin/presupuestos?version_id={quote(str(version.get('id') or ''))}&success_msg={quote(msg)}",
            status_code=303,
        )
    except Exception as exc:
        await session.rollback()
        logger.exception(
            "Unexpected error creating budget version",
            extra={"actor_id": str(getattr(current_empleado, "id", ""))},
        )
        return RedirectResponse(
            url=f"/admin/presupuestos?error_msg={quote(_OPERATION_GENERIC_ERROR)}",
            status_code=303,
        )


@router.post("/admin/presupuestos/versiones/{version_id}/transition")
async def admin_presupuestos_transition_version(
    version_id: UUIDType,
    status: str = Form(...),
    note: Optional[str] = Form(None),
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = Depends(get_current_empleado),
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
        msg = f"Versión {version.get('version_name') or ''} -> {version.get('status') or status}"
        return RedirectResponse(
            url=f"/admin/presupuestos?success_msg={quote(msg)}", status_code=303
        )
    except Exception as exc:
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
            url=f"/admin/presupuestos?error_msg={quote(_OPERATION_GENERIC_ERROR)}",
            status_code=303,
        )


@router.post("/admin/presupuestos/versiones/{version_id}/update")
async def admin_presupuestos_update_version(
    version_id: UUIDType,
    version_name: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = Depends(get_current_empleado),
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
            url=f"/admin/presupuestos?version_id={quote(str(version_id))}&success_msg={quote(msg)}",
            status_code=303,
        )
    except Exception as exc:
        await session.rollback()
        logger.exception(
            "Unexpected error updating budget version metadata",
            extra={
                "version_id": str(version_id),
                "actor_id": str(getattr(current_empleado, "id", "")),
            },
        )
        return RedirectResponse(
            url=f"/admin/presupuestos?version_id={quote(str(version_id))}&error_msg={quote(_OPERATION_GENERIC_ERROR)}",
            status_code=303,
        )


@router.post("/admin/presupuestos/versiones/{version_id}/lineas/create")
async def admin_presupuestos_create_line(
    version_id: UUIDType,
    budget_concept_id: Optional[str] = Form(None),
    tournament_code: Optional[str] = Form(None),
    tournament_name: Optional[str] = Form(None),
    concept_name: Optional[str] = Form(None),
    account_code_final: Optional[str] = Form(None),
    phase: Optional[str] = Form(None),
    owner_name: Optional[str] = Form(None),
    priority: Optional[str] = Form(None),
    budget_amount: Optional[float] = Form(0),
    reference_amount: Optional[float] = Form(0),
    criteria_note: Optional[str] = Form(None),
    observations: Optional[str] = Form(None),
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = Depends(get_current_empleado),
):
    _require_budget_access(current_empleado, "line_update")
    try:
        line = await create_budget_line(
            session,
            version_id=str(version_id),
            actor_empleado_id=str(current_empleado.id),
            budget_concept_id=budget_concept_id,
            tournament_code=tournament_code,
            tournament_name=tournament_name,
            concept_name=concept_name or "",
            account_code_final=account_code_final,
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
            url=f"/admin/presupuestos?version_id={quote(str(version_id))}&success_msg={quote(msg)}",
            status_code=303,
        )
    except Exception as exc:
        await session.rollback()
        logger.exception(
            "Unexpected error creating budget line",
            extra={
                "version_id": str(version_id),
                "actor_id": str(getattr(current_empleado, "id", "")),
            },
        )
        return RedirectResponse(
            url=f"/admin/presupuestos?version_id={quote(str(version_id))}&error_msg={quote(_OPERATION_GENERIC_ERROR)}",
            status_code=303,
        )


@router.post("/admin/presupuestos/lineas/{line_id}/update")
async def admin_presupuestos_update_line(
    line_id: UUIDType,
    version_id: str = Form(...),
    budget_concept_id: Optional[str] = Form(None),
    concept_name: Optional[str] = Form(None),
    account_code_final: Optional[str] = Form(None),
    phase: Optional[str] = Form(None),
    owner_name: Optional[str] = Form(None),
    priority: Optional[str] = Form(None),
    budget_amount: Optional[float] = Form(None),
    criteria_note: Optional[str] = Form(None),
    observations: Optional[str] = Form(None),
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = Depends(get_current_empleado),
):
    _require_budget_access(current_empleado, "line_update")
    try:
        line = await update_budget_line(
            session,
            line_id=str(line_id),
            actor_empleado_id=str(current_empleado.id),
            updates={
                key: value
                for key, value in {
                    "budget_concept_id": budget_concept_id,
                    "concept_name": concept_name,
                    "account_code_final": account_code_final,
                    "phase": phase,
                    "owner_name": owner_name,
                    "priority": priority,
                    "budget_amount": budget_amount,
                    "criteria_note": criteria_note,
                    "observations": observations,
                }.items()
                if value is not None
            },
        )
        msg = f"Línea actualizada: {line.get('concept_name') or ''}"
        return RedirectResponse(
            url=f"/admin/presupuestos?version_id={quote(version_id)}&success_msg={quote(msg)}",
            status_code=303,
        )
    except Exception as exc:
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
            url=f"/admin/presupuestos?version_id={quote(version_id)}&error_msg={quote(_OPERATION_GENERIC_ERROR)}",
            status_code=303,
        )


def _parse_telegram_user_id(value: Optional[str]) -> Optional[int]:
    """Parse telegram_user_id from form string; empty/invalid becomes None."""
    if not value or not value.strip():
        return None
    try:
        return int(value.strip())
    except ValueError:
        return None


_CATALOG_GENERIC_SAVE_ERROR = (
    "Ocurrió un error al guardar la información. Intenta de nuevo o contacta a soporte."
)
_BULK_GENERIC_ERROR = (
    "Ocurrió un error al procesar la carga. Intenta de nuevo o contacta a soporte."
)
_SAT_GENERIC_ERROR = (
    "Ocurrió un error al procesar la operación SAT. Intenta de nuevo o contacta a soporte."
)
_SEED_GENERIC_ERROR = (
    "Ocurrió un error al ejecutar el seed. Intenta de nuevo o contacta a soporte."
)
_OPERATION_GENERIC_ERROR = (
    "Ocurrió un error al procesar la operación. Intenta de nuevo o contacta a soporte."
)


def _catalog_error_response(
    *,
    back_href: str,
    message: str,
    status_code: int = 400,
) -> HTMLResponse:
    return HTMLResponse(
        content=f"<h2>Error: {escape(message)}</h2><a href='{back_href}'>Volver</a>",
        status_code=status_code,
    )


def _redirect_with_error_message(base_url: str, message: str) -> RedirectResponse:
    separator = "&" if "?" in base_url else "?"
    return RedirectResponse(
        url=f"{base_url}{separator}error_msg={quote(message)}",
        status_code=303,
    )


@router.post("/admin/empleados/create")
async def create_empleado(
    request: Request,
    nombre: str = Form(...),
    correo: str = Form(...),
    telefono: Optional[str] = Form(None),
    departamento: Optional[str] = Form(None),
    telegram_user_id: Optional[str] = Form(None),
    aprobador_id: Optional[str] = Form(None),
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = require_admin_finanzas(),
):
    """Create a new employee."""
    try:
        form_data = await request.form()
        activo = form_data.get("activo") == "on"
        telegram_user_id_val = _parse_telegram_user_id(telegram_user_id)
        try:
            rol_norm = normalize_empleado_rol_from_form(form_data.get("rol"))
        except ValueError:
            return HTMLResponse(
                content=(
                    "<h2>Error: Rol inválido o no recibido. Selecciona un rol y vuelve a intentar.</h2>"
                    "<a href='/admin/empleados'>Volver</a>"
                ),
                status_code=400,
            )

        aprobador_uuid: Optional[UUIDType] = None
        if aprobador_id and aprobador_id.strip():
            try:
                aprobador_uuid = UUIDType(aprobador_id.strip())
                aprobador_result = await session.execute(
                    text(
                        """
                        SELECT id FROM empleados
                        WHERE id = :aprobador_id AND activo = TRUE
                    """
                    ),
                    {"aprobador_id": aprobador_uuid},
                )
                if not aprobador_result.fetchone():
                    raise HTTPException(
                        status_code=400,
                        detail="El aprobador seleccionado no existe o no está activo",
                    )
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail="ID de aprobador inválido",
                )

        from uuid import uuid4

        new_id = uuid4()
        temp_password = secrets.token_urlsafe(12)
        password_hash = get_password_hash(temp_password)
        await session.execute(
            text(
                """
                INSERT INTO empleados (id, nombre, correo, telefono, departamento, rol,
                                      telegram_user_id, activo, password_hash, aprobador_id,
                                      creado_en, actualizado_en)
                VALUES (:id, :nombre, :correo, :telefono, :departamento, :rol,
                        :telegram_user_id, :activo, :password_hash, :aprobador_id,
                        NOW(), NOW())
            """
            ),
            {
                "id": new_id,
                "nombre": nombre.strip(),
                "correo": correo.strip(),
                "telefono": telefono.strip() if telefono else None,
                "departamento": departamento.strip() if departamento else None,
                "rol": rol_norm,
                "telegram_user_id": telegram_user_id_val,
                "activo": activo,
                "password_hash": password_hash,
                "aprobador_id": aprobador_uuid,
            },
        )
        await session.commit()

        email_ok, email_note = await send_initial_password_email(
            to_email=correo.strip(),
            nombre=nombre.strip(),
            plain_password=temp_password,
        )
        logger.info(
            "Created empleado %s by %s; email_sent=%s",
            new_id,
            getattr(current_empleado, "id", None),
            email_ok,
        )

        safe_pw = escape(temp_password)
        safe_note = escape(email_note)
        email_block = (
            f'<p style="color:#15803d;"><strong>Correo:</strong> {safe_note}</p>'
            if email_ok
            else f'<p style="color:#b45309;"><strong>Correo:</strong> {safe_note}</p>'
        )
        return HTMLResponse(
            content=f"""
            <!DOCTYPE html>
            <html>
            <head>
                <meta charset="utf-8">
                <title>Empleado creado</title>
            </head>
            <body style="font-family: sans-serif; padding: 24px; max-width: 640px; margin: 0 auto;">
                <h2 style="color: green;">Empleado creado exitosamente</h2>
                <p>Comparte esta contraseña temporal con el usuario (también se envió por correo si el envío fue posible):</p>
                <p style="font-size:18px;font-family:monospace;background:#f1f5f9;padding:12px;border-radius:8px;word-break:break-all;">{safe_pw}</p>
                {email_block}
                <p><a href="/admin/empleados">Volver al listado de empleados</a></p>
            </body>
            </html>
            """
        )
    except Exception as e:
        await session.rollback()
        error_msg = str(e)
        if "unique constraint" in error_msg.lower() or "duplicate" in error_msg.lower():
            error_msg = "El correo electrónico ya está registrado"
        else:
            logger.exception(
                "Unexpected error creating empleado",
                extra={
                    "correo": correo.strip(),
                    "actor_id": str(getattr(current_empleado, "id", "")),
                },
            )
            error_msg = _CATALOG_GENERIC_SAVE_ERROR
        return _catalog_error_response(
            back_href="/admin/empleados",
            message=error_msg,
            status_code=400,
        )


@router.get("/admin/empleados/edit/{empleado_id}", response_class=HTMLResponse)
async def edit_empleado_form(
    empleado_id: UUIDType,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    _empleado: Empleado = require_admin_finanzas(),
):
    """Edit employee form."""
    result = await session.execute(
        text(
            """
            SELECT id, nombre, correo, telefono, departamento, rol, activo,
                   telegram_user_id, proyecto_predeterminado, centro_costo_predeterminado,
                   creado_en, actualizado_en, aprobador_id
            FROM empleados
            WHERE id = :empleado_id
        """
        ),
        {"empleado_id": empleado_id},
    )
    row = result.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Empleado not found")

    # Create simple object
    class EmpleadoRow:
        def __init__(self, row):
            self.id = row[0]
            self.nombre = row[1]
            self.correo = row[2]
            self.telefono = row[3]
            self.departamento = row[4]
            self.rol = row[5]
            self.activo = row[6]
            self.telegram_user_id = row[7]
            self.proyecto_predeterminado = row[8]
            self.centro_costo_predeterminado = row[9]
            self.creado_en = row[10]
            self.actualizado_en = row[11]
            self.aprobador_id = row[12]

    empleado = EmpleadoRow(row)

    # Fetch all active empleados excluding the current one (for approver dropdown)
    aprobadores_result = await session.execute(
        text(
            """
            SELECT id, nombre, correo, rol
            FROM empleados 
            WHERE activo = TRUE AND id != :empleado_id
            ORDER BY nombre
        """
        ),
        {"empleado_id": empleado_id},
    )
    aprobadores_rows = aprobadores_result.fetchall()

    # Build approver options HTML
    aprobador_options = '<option value="">(Sin asignar)</option>'
    for aprobador_row in aprobadores_rows:
        sel = (
            "selected"
            if empleado.aprobador_id and aprobador_row[0] == empleado.aprobador_id
            else ""
        )
        rol_display = f" ({aprobador_row[3]})" if aprobador_row[3] else ""
        aprobador_options += f'\n                    <option value="{aprobador_row[0]}" {sel}>{aprobador_row[1]}{rol_display}</option>'

    activo_checked = "checked" if empleado.activo else ""
    rol_norm = (empleado.rol or "").strip().lower()
    rol_empleado_selected = "selected" if rol_norm == "empleado" else ""
    rol_coordinador_selected = "selected" if rol_norm == "coordinador" else ""
    rol_finanzas_selected = "selected" if rol_norm == "finanzas" else ""
    rol_admin_selected = "selected" if rol_norm == "admin" else ""
    rol_superadmin_selected = (
        "selected" if rol_norm in ("superadmin", "super_admin") else ""
    )

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Editar Empleado - Copa Telmex</title>
        <meta charset="utf-8">
        <style>
            body {{ font-family: sans-serif; padding: 20px; max-width: 800px; margin: 0 auto; }}
            .form-group {{ margin-bottom: 15px; }}
            .form-row {{ display: grid; grid-template-columns: 1fr 1fr; gap: 15px; }}
            label {{ display: block; margin-bottom: 5px; font-weight: 600; }}
            input[type="text"], input[type="email"], input[type="number"], select {{
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
        <h1>✏️ Editar Empleado</h1>
        <form method="POST" action="/admin/empleados/update/{empleado_id}">
            <div class="form-group">
                <label>Nombre *</label>
                <input type="text" name="nombre" value="{escape(str(empleado.nombre or ''))}" required>
            </div>
            <div class="form-row">
                <div class="form-group">
                    <label>Correo Electrónico *</label>
                    <input type="email" name="correo" value="{escape(str(empleado.correo or ''))}" required>
                </div>
                <div class="form-group">
                    <label>Teléfono</label>
                    <input type="text" name="telefono" value="{escape(str(empleado.telefono or ''))}">
                </div>
            </div>
            <div class="form-row">
                <div class="form-group">
                    <label>Departamento</label>
                    <select name="departamento">
                        <option value="">—</option>
                        <option value="Finanzas" {('selected' if (empleado.departamento or '').strip() == 'Finanzas' else '')}>Finanzas</option>
                        <option value="Mercadotecnia" {('selected' if (empleado.departamento or '').strip() == 'Mercadotecnia' else '')}>Mercadotecnia</option>
                        <option value="Operaciones" {('selected' if (empleado.departamento or '').strip() == 'Operaciones' else '')}>Operaciones</option>
                        <option value="Dirección" {('selected' if (empleado.departamento or '').strip() == 'Dirección' else '')}>Dirección</option>
                    </select>
                </div>
                <div class="form-group">
                    <label>Rol *</label>
                    <select name="rol" required>
                        <option value="empleado" {rol_empleado_selected}>Empleado</option>
                        <option value="coordinador" {rol_coordinador_selected}>Coordinador</option>
                        <option value="finanzas" {rol_finanzas_selected}>Finanzas</option>
                        <option value="admin" {rol_admin_selected}>Admin</option>
                        <option value="superadmin" {rol_superadmin_selected}>Superadmin</option>
                    </select>
                </div>
            </div>
            <div class="form-group">
                <label>Telegram User ID
                    <span title="Si no se proporciona, el empleado no tendrá acceso al chatbot de Telegram hasta que se actualice esta información." style="cursor: help; color: #666; font-size: 14px;">&#9432;</span>
                </label>
                <input type="number" name="telegram_user_id" value="{escape(str(empleado.telegram_user_id) if empleado.telegram_user_id is not None else '')}">
            </div>
            <div class="form-group">
                <label>Aprobador</label>
                <select name="aprobador_id">
                    {aprobador_options}
                </select>
            </div>
            <div class="form-group">
                <label>
                    <input type="checkbox" name="activo" {activo_checked}>
                    Activo
                </label>
            </div>
            <div class="form-group" style="margin-top: 20px; padding-top: 20px; border-top: 1px solid #ddd;">
                <a href="/admin/empleados/{empleado_id}/password" class="btn btn-secondary">🔑 Cambiar contraseña de este empleado</a>
            </div>
            <button type="submit" class="btn btn-primary">Guardar Cambios</button>
            <a href="/admin/empleados" class="btn btn-secondary">Cancelar</a>
        </form>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)


@router.post("/admin/empleados/update/{empleado_id}")
async def update_empleado(
    empleado_id: UUIDType,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = require_admin_finanzas(),
):
    """Update an employee."""
    form_data = await request.form()
    nombre = str(form_data.get("nombre") or "").strip()
    correo = str(form_data.get("correo") or "").strip()
    telefono_raw = form_data.get("telefono")
    telefono = str(telefono_raw).strip() if telefono_raw not in (None, "") else None
    departamento_raw = form_data.get("departamento")
    departamento = (
        str(departamento_raw).strip() if departamento_raw not in (None, "") else None
    )
    aprobador_id = form_data.get("aprobador_id")
    aprobador_id_str = (
        str(aprobador_id).strip() if aprobador_id not in (None, "") else None
    )
    telegram_user_id_val = _parse_telegram_user_id(
        str(form_data.get("telegram_user_id") or "").strip() or None
    )
    activo = form_data.get("activo") == "on"

    if not nombre or not correo:
        return HTMLResponse(
            content="<h2>Error: Nombre y correo son obligatorios</h2><a href='/admin/empleados'>Volver</a>",
            status_code=400,
        )

    try:
        rol = normalize_empleado_rol_from_form(form_data.get("rol"))
    except ValueError:
        return HTMLResponse(
            content=(
                "<h2>Error: Rol inválido o no recibido. Vuelve a abrir el formulario, "
                "selecciona un rol y guarda de nuevo.</h2>"
                f"<a href='/admin/empleados/edit/{empleado_id}'>Volver a editar</a>"
            ),
            status_code=400,
        )

    # Check if empleado exists using raw SQL
    result = await session.execute(
        text("SELECT id FROM empleados WHERE id = :empleado_id"),
        {"empleado_id": empleado_id},
    )
    if not result.fetchone():
        raise HTTPException(status_code=404, detail="Empleado not found")

    try:
        aprobador_uuid: Optional[UUIDType] = None
        if aprobador_id_str:
            try:
                aprobador_uuid = UUIDType(aprobador_id_str)
                aprobador_result = await session.execute(
                    text(
                        """
                        SELECT id FROM empleados
                        WHERE id = :aprobador_id AND activo = TRUE
                    """
                    ),
                    {"aprobador_id": aprobador_uuid},
                )
                if not aprobador_result.fetchone():
                    raise HTTPException(
                        status_code=400,
                        detail="El aprobador seleccionado no existe o no está activo",
                    )
                if aprobador_uuid == empleado_id:
                    raise HTTPException(
                        status_code=400,
                        detail="Un empleado no puede ser su propio aprobador",
                    )
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail="ID de aprobador inválido",
                )

        # Update empleado using raw SQL
        upd = await session.execute(
            text(
                """
                UPDATE empleados
                SET nombre = :nombre,
                    correo = :correo,
                    telefono = :telefono,
                    departamento = :departamento,
                    rol = :rol,
                    telegram_user_id = :telegram_user_id,
                    activo = :activo,
                    aprobador_id = :aprobador_id,
                    actualizado_en = NOW()
                WHERE id = :empleado_id
            """
            ),
            {
                "empleado_id": empleado_id,
                "nombre": nombre,
                "correo": correo,
                "telefono": telefono,
                "departamento": departamento,
                "rol": rol,
                "telegram_user_id": telegram_user_id_val,
                "activo": activo,
                "aprobador_id": aprobador_uuid,
            },
        )
        if getattr(upd, "rowcount", None) == 0:
            await session.rollback()
            logger.warning("UPDATE empleados matched 0 rows for id=%s", empleado_id)
            return HTMLResponse(
                content="<h2>Error: No se pudo actualizar el empleado</h2><a href='/admin/empleados'>Volver</a>",
                status_code=400,
            )
        await session.commit()

        logger.info(
            "Updated empleado %s by admin %s (rol=%s)",
            empleado_id,
            getattr(current_empleado, "id", None),
            rol,
        )

        return RedirectResponse(
            url=f"/admin/empleados?success_msg={quote('Empleado actualizado exitosamente')}",
            status_code=303,
        )
    except HTTPException:
        raise
    except Exception as e:
        await session.rollback()
        error_msg = str(e)
        if "unique constraint" in error_msg.lower() or "duplicate" in error_msg.lower():
            error_msg = "El correo electrónico ya está registrado"
        else:
            logger.exception(
                "Unexpected error updating empleado",
                extra={
                    "empleado_id": str(empleado_id),
                    "actor_id": str(getattr(current_empleado, "id", "")),
                },
            )
            error_msg = _CATALOG_GENERIC_SAVE_ERROR
        return _catalog_error_response(
            back_href="/admin/empleados",
            message=error_msg,
            status_code=400,
        )


@router.get("/admin/empleados/{empleado_id}/password", response_class=HTMLResponse)
async def password_form(
    empleado_id: UUIDType,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = require_admin_finanzas(),
    error_msg: Optional[str] = Query(None),
):
    """Form to set/reset password for an empleado."""
    # Load empleado using raw SQL
    result = await session.execute(
        text("SELECT id, nombre, correo FROM empleados WHERE id = :empleado_id"),
        {"empleado_id": empleado_id},
    )
    row = result.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Empleado no encontrado")

    # Create simple object
    class EmpleadoRow:
        def __init__(self, row):
            self.id = row[0]
            self.nombre = row[1]
            self.correo = row[2]

    empleado = EmpleadoRow(row)

    error_html = ""
    if error_msg:
        error_html = f"""
            <div style="background: #f8d7da; border: 1px solid #f5c6cb; border-radius: 5px; padding: 15px; margin-bottom: 20px; color: #721c24;">
                <strong>⚠️ Error:</strong> {error_msg}
            </div>
        """

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Cambiar Contraseña - Copa Telmex</title>
        <meta charset="utf-8">
        <style>
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                padding: 20px;
                min-height: 100vh;
            }}
            .container {{
                max-width: 600px;
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
            .info-box {{
                background: #f8f9fa;
                padding: 15px;
                border-radius: 8px;
                margin-bottom: 20px;
                border-left: 4px solid #667eea;
            }}
            .info-box p {{
                margin: 5px 0;
                color: #666;
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
            input[type="password"] {{
                width: 100%;
                padding: 12px;
                border: 2px solid #ddd;
                border-radius: 6px;
                font-size: 16px;
                box-sizing: border-box;
            }}
            input[type="password"]:focus {{
                outline: none;
                border-color: #667eea;
                box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1);
            }}
            .note {{
                background: #fff3cd;
                border: 1px solid #ffc107;
                border-radius: 5px;
                padding: 15px;
                margin-bottom: 20px;
                color: #856404;
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
                margin-left: 10px;
            }}
            .btn-secondary:hover {{
                background: #5a6268;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            {_CONFIG_PANEL_BACK_LINK_HTML}
            <h1>🔑 Cambiar Contraseña</h1>
            {error_html}
            <div class="info-box">
                <p><strong>Empleado:</strong> {empleado.nombre}</p>
                <p><strong>Correo:</strong> {empleado.correo or '-'}</p>
            </div>
            <div class="note">
                <strong>⚠️ Nota:</strong> Esta acción cambiará la contraseña de acceso web de este empleado.
            </div>
            <form method="POST" action="/admin/empleados/{empleado_id}/password">
                <div class="form-group">
                    <label for="password">Nueva Contraseña *</label>
                    <input type="password" name="password" id="password" required minlength="8" autofocus>
                    <small style="color: #666; font-size: 12px;">Mínimo 8 caracteres</small>
                </div>
                <div class="form-group">
                    <label for="password_confirm">Confirmar Contraseña *</label>
                    <input type="password" name="password_confirm" id="password_confirm" required minlength="8">
                </div>
                <button type="submit" class="btn btn-primary">Actualizar Contraseña</button>
                <a href="/admin/empleados" class="btn btn-secondary">Cancelar</a>
            </form>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)


@router.post("/admin/empleados/{empleado_id}/password")
async def update_password(
    empleado_id: UUIDType,
    request: Request,
    password: str = Form(...),
    password_confirm: str = Form(...),
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = require_admin_finanzas(),
) -> HTMLResponse:
    """Update password for an empleado."""
    try:
        # Check if empleado exists using raw SQL
        result = await session.execute(
            text("SELECT id FROM empleados WHERE id = :empleado_id"),
            {"empleado_id": empleado_id},
        )
        if not result.fetchone():
            return HTMLResponse(
                content="<h2>Error: Empleado no encontrado</h2><a href='/admin/empleados'>Volver</a>",
                status_code=404,
            )

        # Validate password is not empty
        if not password or not password.strip():
            return HTMLResponse(
                content=f"""
                <html>
                <head>
                    <meta http-equiv="refresh" content="2;url=/admin/empleados/{empleado_id}/password?error_msg=La contraseña no puede estar vacía">
                </head>
                <body>
                    <h2>Error: La contraseña no puede estar vacía</h2>
                    <p>Redirigiendo...</p>
                </body>
                </html>
                """,
                status_code=400,
            )

        # Validate password length (minimum 8 characters)
        if len(password) < 8:
            return HTMLResponse(
                content=f"""
                <html>
                <head>
                    <meta http-equiv="refresh" content="2;url=/admin/empleados/{empleado_id}/password?error_msg=La contraseña debe tener al menos 8 caracteres">
                </head>
                <body>
                    <h2>Error: La contraseña debe tener al menos 8 caracteres</h2>
                    <p>Redirigiendo...</p>
                </body>
                </html>
                """,
                status_code=400,
            )

        # Validate passwords match
        if password != password_confirm:
            return HTMLResponse(
                content=f"""
                <html>
                <head>
                    <meta http-equiv="refresh" content="2;url=/admin/empleados/{empleado_id}/password?error_msg=Las contraseñas no coinciden">
                </head>
                <body>
                    <h2>Error: Las contraseñas no coinciden</h2>
                    <p>Redirigiendo...</p>
                </body>
                </html>
                """,
                status_code=400,
            )

        # Hash the password using the same mechanism as login
        password_hash = get_password_hash(password)

        # Update password using raw SQL
        await session.execute(
            text(
                "UPDATE empleados SET password_hash = :hash, actualizado_en = NOW() WHERE id = :empleado_id"
            ),
            {"hash": password_hash, "empleado_id": empleado_id},
        )
        await session.commit()

        logger.info(
            f"Password updated for empleado {empleado_id} by admin {current_empleado.id}"
        )

        # Redirect with success message
        return HTMLResponse(
            content="""
            <html>
            <head>
                <meta http-equiv="refresh" content="2;url=/admin/empleados?success_msg=Contraseña actualizada correctamente">
            </head>
            <body>
                <h2>✅ Contraseña actualizada correctamente</h2>
                <p>Redirigiendo...</p>
            </body>
            </html>
            """,
            status_code=200,
        )

    except Exception as e:
        logger.exception(
            "Unexpected error updating empleado password",
            extra={
                "empleado_id": str(empleado_id),
                "actor_id": str(getattr(current_empleado, "id", "")),
            },
        )
        await session.rollback()
        return HTMLResponse(
            content=f"""
            <html>
            <head>
                <meta http-equiv="refresh" content="2;url=/admin/empleados/{empleado_id}/password?error_msg=Ocurrió un error al actualizar la contraseña. Intenta más tarde o contacta a soporte.">
            </head>
            <body>
                <h2>Error: Ocurrió un error al actualizar la contraseña</h2>
                <p>Redirigiendo...</p>
            </body>
            </html>
            """,
            status_code=500,
        )


# ============================================================================
# Accounting Accounts Management Routes
# ============================================================================


@router.get("/admin/cuentas-contables", response_class=HTMLResponse)
async def admin_cuentas_contables(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    bi_year: Optional[str] = Query(None),
    bi_scope: Optional[str] = Query(None),
    current_empleado: Empleado = require_admin_finanzas(),
):
    """Admin interface for managing accounting accounts."""
    query = select(CuentaContable).order_by(CuentaContable.codigo)

    # If BI context is present, show only cuentas used by filtered expenses.
    bi_year_safe = (bi_year or "").strip()
    bi_year_safe = (
        bi_year_safe if (bi_year_safe.isdigit() and len(bi_year_safe) == 4) else ""
    )
    bi_scope_safe = (bi_scope or "").strip().lower()
    if bi_scope_safe not in {"all", ACTIVE_TOURNAMENT_SCOPE}:
        bi_scope_safe = ""
    if bi_year_safe or bi_scope_safe:
        expense_conditions: List[Any] = [ExpenseReport.cuenta_contable_id.isnot(None)]
        _append_bi_expense_filters(
            conditions=expense_conditions,
            bi_year=bi_year_safe or None,
            bi_scope=bi_scope_safe or None,
        )
        used_ids_result = await session.execute(
            select(func.distinct(ExpenseReport.cuenta_contable_id)).where(
                and_(*expense_conditions)
            )
        )
        used_ids = [row[0] for row in used_ids_result.all() if row[0]]
        if used_ids:
            query = query.where(CuentaContable.id.in_(used_ids))
        else:
            query = query.where(text("1=0"))
    bi_query_suffix = ""
    if bi_year_safe or bi_scope_safe:
        parts = []
        if bi_year_safe:
            parts.append(f"bi_year={bi_year_safe}")
        if bi_scope_safe:
            parts.append(f"bi_scope={bi_scope_safe}")
        bi_query_suffix = f"?{'&'.join(parts)}"
    bi_context_html = (
        f'<p style="margin: -10px 0 16px 0; color:#4b5563; font-size:13px;">'
        f'Contexto BI activo: año={bi_year_safe or "n/a"} | ámbito={bi_scope_safe or "all"}'
        f"</p>"
        if (bi_year_safe or bi_scope_safe)
        else ""
    )

    result = await session.execute(query)
    cuentas = result.scalars().all()

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Gestión de Cuentas Contables - Copa Telmex</title>
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
            .btn-danger {{
                background: #dc3545;
                color: white;
            }}
            .btn-danger:hover {{
                background: #c82333;
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
            .btn-small {{
                padding: 8px 16px;
                font-size: 14px;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            {render_admin_navigation(current_empleado, "cuentas", subtitle="Plan de cuentas y clasificación contable base para gastos, pólizas y conciliación.")}
            
            <h1>Gestión de Cuentas Contables</h1>
            <p class="subtitle">Administra el catálogo de cuentas contables</p>
            {bi_context_html}
            
            <div class="form-section">
                <h2 style="margin-bottom: 15px;">➕ Agregar Nueva Cuenta Contable</h2>
                <form method="POST" action="/admin/cuentas-contables/create{bi_query_suffix}">
                    <div class="form-group">
                        <label for="codigo">Código *</label>
                        <input type="text" id="codigo" name="codigo" required placeholder="Ej: 5300-010-001">
                        <small style="color: #666; display: block; margin-top: 5px;">El código debe ser único</small>
                    </div>
                    <div class="form-group">
                        <label for="nombre">Nombre *</label>
                        <input type="text" id="nombre" name="nombre" required placeholder="Ej: GASTOS DE VIAJE TRANSPORTE">
                    </div>
                    <div class="form-group">
                        <label for="tipo">Tipo *</label>
                        <select id="tipo" name="tipo" required>
                            <option value="gasto">Gasto</option>
                            <option value="proveedor">Proveedor</option>
                            <option value="anticipo">Anticipo</option>
                            <option value="iva">IVA</option>
                            <option value="retencion">Retención</option>
                            <option value="banco">Banco</option>
                            <option value="otro">Otro</option>
                        </select>
                    </div>
                    <div class="form-group">
                        <div class="checkbox-group">
                            <input type="checkbox" id="activo" name="activo" checked>
                            <label for="activo" style="margin: 0;">Activo</label>
                        </div>
                    </div>
                    <button type="submit" class="btn btn-primary">Crear Cuenta</button>
                </form>
            </div>
            
            <div style="margin-top: 30px;">
                <h2 style="margin-bottom: 20px;">📋 Cuentas Contables ({len(cuentas)})</h2>
                {"<p style='color: #666;'>No hay cuentas contables registradas aún.</p>" if not cuentas else ""}
                <table>
                    <thead>
                        <tr>
                            <th>Código</th>
                            <th>Nombre</th>
                            <th>Tipo</th>
                            <th>Estado</th>
                            <th>Acciones</th>
                        </tr>
                    </thead>
                    <tbody>
"""

    for cuenta in cuentas:
        status_class = "badge-active" if cuenta.activo else "badge-inactive"
        status_text = "Activo" if cuenta.activo else "Inactivo"
        html_content += f"""
                        <tr>
                            <td>{cuenta.codigo}</td>
                            <td>{cuenta.nombre}</td>
                            <td>{cuenta.tipo}</td>
                            <td><span class="badge {status_class}">{status_text}</span></td>
                            <td>
                                <a href="/admin/cuentas-contables/edit/{cuenta.id}{bi_query_suffix}" class="btn btn-secondary btn-small">✏️ Editar</a>
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


@router.post("/admin/cuentas-contables/create")
async def create_cuenta_contable(
    request: Request,
    codigo: str = Form(...),
    nombre: str = Form(...),
    tipo: str = Form(...),
    bi_year: Optional[str] = Query(None),
    bi_scope: Optional[str] = Query(None),
    session: AsyncSession = Depends(get_db_session),
):
    """Create a new accounting account."""
    try:
        form_data = await request.form()
        activo = form_data.get("activo") == "on"

        cuenta = CuentaContable(
            codigo=codigo.strip(), nombre=nombre.strip(), tipo=tipo, activo=activo
        )
        session.add(cuenta)
        await session.commit()
        await session.refresh(cuenta)
        bi_query_suffix = ""
        if bi_year or bi_scope:
            parts = []
            if bi_year:
                parts.append(f"bi_year={bi_year}")
            if bi_scope:
                parts.append(f"bi_scope={bi_scope}")
            bi_query_suffix = f"?{'&'.join(parts)}"

        return HTMLResponse(
            content=f"""
            <!DOCTYPE html>
            <html>
            <head>
                <meta http-equiv="refresh" content="2;url=/admin/cuentas-contables{bi_query_suffix}">
                <title>Éxito</title>
            </head>
            <body style="font-family: sans-serif; padding: 20px; text-align: center;">
                <h2 style="color: green;">✅ Cuenta contable creada exitosamente</h2>
                <p>Redirigiendo...</p>
            </body>
            </html>
            """
        )
    except Exception as e:
        await session.rollback()
        error_msg = str(e)
        if "unique constraint" in error_msg.lower() or "duplicate" in error_msg.lower():
            error_msg = "El código ya está registrado. Debe ser único."
        else:
            logger.exception(
                "Unexpected error creating cuenta contable",
                extra={"codigo": codigo.strip()},
            )
            error_msg = _CATALOG_GENERIC_SAVE_ERROR
        bi_query_suffix = ""
        if bi_year or bi_scope:
            parts = []
            if bi_year:
                parts.append(f"bi_year={bi_year}")
            if bi_scope:
                parts.append(f"bi_scope={bi_scope}")
            bi_query_suffix = f"?{'&'.join(parts)}"
        return _catalog_error_response(
            back_href=f"/admin/cuentas-contables{bi_query_suffix}",
            message=error_msg,
            status_code=400,
        )


@router.get("/admin/cuentas-contables/edit/{cuenta_id}", response_class=HTMLResponse)
async def edit_cuenta_contable_form(
    cuenta_id: UUIDType,
    request: Request,
    bi_year: Optional[str] = Query(None),
    bi_scope: Optional[str] = Query(None),
    session: AsyncSession = Depends(get_db_session),
):
    """Edit accounting account form."""
    result = await session.execute(
        select(CuentaContable).where(CuentaContable.id == cuenta_id)
    )
    cuenta = result.scalar_one_or_none()

    if not cuenta:
        raise HTTPException(status_code=404, detail="Cuenta contable not found")
    bi_query_suffix = ""
    if bi_year or bi_scope:
        parts = []
        if bi_year:
            parts.append(f"bi_year={bi_year}")
        if bi_scope:
            parts.append(f"bi_scope={bi_scope}")
        bi_query_suffix = f"?{'&'.join(parts)}"

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Editar Cuenta Contable - Copa Telmex</title>
        <meta charset="utf-8">
        <style>
            body {{ font-family: sans-serif; padding: 20px; max-width: 800px; margin: 0 auto; }}
            .form-group {{ margin-bottom: 15px; }}
            label {{ display: block; margin-bottom: 5px; font-weight: 600; }}
            input[type="text"], select {{
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
        <h1>✏️ Editar Cuenta Contable</h1>
        <form method="POST" action="/admin/cuentas-contables/update/{cuenta_id}{bi_query_suffix}">
            <div class="form-group">
                <label>Código *</label>
                <input type="text" name="codigo" value="{cuenta.codigo}" required>
            </div>
            <div class="form-group">
                <label>Nombre *</label>
                <input type="text" name="nombre" value="{cuenta.nombre}" required>
            </div>
            <div class="form-group">
                <label>Tipo *</label>
                <select name="tipo" required>
                    <option value="gasto" {'selected' if cuenta.tipo == 'gasto' else ''}>Gasto</option>
                    <option value="proveedor" {'selected' if cuenta.tipo == 'proveedor' else ''}>Proveedor</option>
                    <option value="anticipo" {'selected' if cuenta.tipo == 'anticipo' else ''}>Anticipo</option>
                    <option value="iva" {'selected' if cuenta.tipo == 'iva' else ''}>IVA</option>
                    <option value="retencion" {'selected' if cuenta.tipo == 'retencion' else ''}>Retención</option>
                    <option value="banco" {'selected' if cuenta.tipo == 'banco' else ''}>Banco</option>
                    <option value="otro" {'selected' if cuenta.tipo == 'otro' else ''}>Otro</option>
                </select>
            </div>
            <div class="form-group">
                <label>
                    <input type="checkbox" name="activo" {'checked' if cuenta.activo else ''}>
                    Activo
                </label>
            </div>
            <button type="submit" class="btn btn-primary">Guardar Cambios</button>
            <a href="/admin/cuentas-contables{bi_query_suffix}" class="btn btn-secondary">Cancelar</a>
        </form>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)


@router.post("/admin/cuentas-contables/update/{cuenta_id}")
async def update_cuenta_contable(
    cuenta_id: UUIDType,
    request: Request,
    codigo: str = Form(...),
    nombre: str = Form(...),
    tipo: str = Form(...),
    bi_year: Optional[str] = Query(None),
    bi_scope: Optional[str] = Query(None),
    session: AsyncSession = Depends(get_db_session),
):
    """Update an accounting account."""
    form_data = await request.form()
    activo = form_data.get("activo") == "on"

    result = await session.execute(
        select(CuentaContable).where(CuentaContable.id == cuenta_id)
    )
    cuenta = result.scalar_one_or_none()

    if not cuenta:
        raise HTTPException(status_code=404, detail="Cuenta contable not found")

    try:
        cuenta.codigo = codigo.strip()
        cuenta.nombre = nombre.strip()
        cuenta.tipo = tipo
        cuenta.activo = activo

        await session.commit()
        bi_query_suffix = ""
        if bi_year or bi_scope:
            parts = []
            if bi_year:
                parts.append(f"bi_year={bi_year}")
            if bi_scope:
                parts.append(f"bi_scope={bi_scope}")
            bi_query_suffix = f"?{'&'.join(parts)}"

        return HTMLResponse(
            content=f"""
            <!DOCTYPE html>
            <html>
            <head>
                <meta http-equiv="refresh" content="2;url=/admin/cuentas-contables{bi_query_suffix}">
                <title>Éxito</title>
            </head>
            <body style="font-family: sans-serif; padding: 20px; text-align: center;">
                <h2 style="color: green;">✅ Cuenta contable actualizada exitosamente</h2>
                <p>Redirigiendo...</p>
            </body>
            </html>
            """
        )
    except Exception as e:
        await session.rollback()
        error_msg = str(e)
        if "unique constraint" in error_msg.lower() or "duplicate" in error_msg.lower():
            error_msg = "El código ya está registrado. Debe ser único."
        else:
            logger.exception(
                "Unexpected error updating cuenta contable",
                extra={"cuenta_id": str(cuenta_id), "codigo": codigo.strip()},
            )
            error_msg = _CATALOG_GENERIC_SAVE_ERROR
        bi_query_suffix = ""
        if bi_year or bi_scope:
            parts = []
            if bi_year:
                parts.append(f"bi_year={bi_year}")
            if bi_scope:
                parts.append(f"bi_scope={bi_scope}")
            bi_query_suffix = f"?{'&'.join(parts)}"
        return _catalog_error_response(
            back_href=f"/admin/cuentas-contables{bi_query_suffix}",
            message=error_msg,
            status_code=400,
        )


# ============================================================================
# Bulk XLSX Upload for COI Accounting Journals
# ============================================================================


@router.get("/admin/contabilidad/coi/carga-masiva", response_class=HTMLResponse)
async def carga_masiva_coi_form(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = require_admin_finanzas(),
):
    """Bulk upload form for COI workbook imports."""
    success_msg = request.query_params.get("success_msg", "")
    error_msg = request.query_params.get("error_msg", "")

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Carga COI - Sam.chat</title>
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
            <h1>📘 Carga COI</h1>
            <p class="subtitle">Importa pólizas contables y CFDI desde un archivo XLSX de COI</p>
            {'<div class="alert alert-success">✅ ' + escape(success_msg) + '</div>' if success_msg else ''}
            {'<div class="alert alert-error">❌ ' + escape(error_msg) + '</div>' if error_msg else ''}
            <div class="instructions">
                <h2>Qué hace esta carga</h2>
                <ul>
                    <li>Lee bloques de póliza tipo COI (`Eg`, partidas, `INICIO_CFDI`, `FIN_PARTIDAS`).</li>
                    <li>Guarda encabezados en `accounting_polizas`.</li>
                    <li>Guarda partidas en `accounting_poliza_lines`.</li>
                    <li>Crea o reutiliza CFDI por UUID en `cfdi_reports`.</li>
                    <li>Alimenta el histórico para recomendación de cuentas contables.</li>
                </ul>
            </div>
            <form method="POST" action="/admin/contabilidad/coi/carga-masiva" enctype="multipart/form-data">
                <div class="form-group">
                    <label for="archivo_xlsx">Archivo XLSX COI</label>
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
                    <button type="submit" class="btn btn-primary">📤 Procesar COI</button>
                    <a href="/panel" class="btn btn-secondary">⬅️ Volver</a>
                </div>
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
        contents = await read_upload_limited(
            archivo_xlsx,
            max_bytes=MAX_DECODE_BYTES,
            too_large_message="El archivo XLSX excede el tamaño máximo permitido",
        )
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
        contents = await read_upload_limited(
            archivo_xlsx,
            max_bytes=MAX_DECODE_BYTES,
            too_large_message="El archivo XLSX excede el tamaño máximo permitido",
        )
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
        contents = await read_upload_limited(
            archivo_csv,
            max_bytes=MAX_DECODE_BYTES,
            too_large_message="El archivo CSV excede el tamaño máximo permitido",
        )
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
        contents = await read_upload_limited(
            archivo_xlsx,
            max_bytes=MAX_DECODE_BYTES,
            too_large_message="El archivo XLSX excede el tamaño máximo permitido",
        )
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

        contents = await read_upload_limited(
            archivo_csv,
            max_bytes=MAX_DECODE_BYTES,
            too_large_message="El archivo excede el tamaño máximo permitido",
        )
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
        <title>Proveedores, Operadores y Clientes - Copa Telmex</title>
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
            {render_admin_navigation(current_empleado, "proveedores", subtitle="Catálogo comercial y operativo para proveedores, clientes y operadores regionales.")}
            
            <h1>Proveedores, Operadores y Clientes</h1>
            <p class="subtitle">Administra el catálogo de proveedores, operadores y clientes</p>
            
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
                <h2 style="margin-bottom: 15px;">➕ Agregar Nuevo Proveedor/Cliente</h2>
                <form method="POST" action="/admin/proveedores-clientes/create">
                    <div class="form-row">
                        <div class="form-group">
                            <label for="tipo">Tipo *</label>
                            <select id="tipo" name="tipo" required>
                                <option value="">Seleccionar...</option>
                                <option value="proveedor">Proveedor</option>
                                <option value="cliente">Cliente</option>
                                <option value="operadores_regionales">Operadores Regionales</option>
                            </select>
                        </div>
                        <div class="form-group">
                            <label for="nombre">Nombre *</label>
                            <input type="text" id="nombre" name="nombre" required placeholder="Nombre del proveedor/cliente">
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
                    <button type="submit" class="btn btn-primary">Crear Proveedor/Cliente</button>
                </form>
            </div>
            
            <div style="margin-top: 30px;">
                <h2 style="margin-bottom: 20px;">📋 Proveedores, Operadores y Clientes ({len(proveedores)})</h2>
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
            else:
                tipo_class = "badge-operadores-regionales"
            tipo_display = (
                "Operadores Regionales"
                if prov.tipo == "operadores_regionales"
                else prov.tipo.capitalize()
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
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = Depends(get_current_empleado),
):
    """Create a new supplier/client."""
    try:
        form_data = await request.form()
        activo = form_data.get("activo") == "on"

        # Validations
        nombre = nombre.strip()
        if not nombre:
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

        if tipo not in ["proveedor", "cliente", "operadores_regionales"]:
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
                        </select>
                    </div>
                    <div class="form-group">
                        <label for="nombre">Nombre *</label>
                        <input type="text" id="nombre" name="nombre" value="{prov.nombre or ''}" required>
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

        # Validations (same as create)
        nombre = nombre.strip()
        if not nombre:
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

        if tipo not in ["proveedor", "cliente", "operadores_regionales"]:
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
        <title>Carga Masiva de Proveedores/Clientes - Copa Telmex</title>
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
            <h1>📥 Carga Masiva de Proveedores/Clientes</h1>
            <p class="subtitle">Importar proveedores y clientes desde archivo CSV o XLSX tipo RFC</p>
            
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
                                <td>proveedor, cliente, operadores_regionales</td>
                            </tr>
                            <tr>
                                <td><span class="code">nombre</span></td>
                                <td>✅ Sí</td>
                                <td>Nombre del proveedor/cliente</td>
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
                        <li><strong>tipo</strong>: Debe ser "proveedor", "cliente" o "operadores_regionales" (Operadores Regionales)</li>
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
                        <strong>NO es lo mismo</strong> que cuenta contable (cómo se clasifica en contabilidad).<br>
                        Las cuentas contables se asignan explícitamente en los gastos/informes.
                    </p>
                </div>
            </div>
            
            <form method="POST" action="/admin/proveedores-clientes/carga-masiva" enctype="multipart/form-data">
                <div class="form-group">
                    <label for="archivo_csv">📂 Selecciona el archivo CSV o XLSX:</label>
                    <input type="file" id="archivo_csv" name="archivo_csv" accept=".csv,.xlsx" required>
                </div>
                
                <div style="margin-top: 30px;">
                    <button type="submit" class="btn btn-primary">📤 Importar Proveedores/Clientes</button>
                    <a href="/admin/proveedores-clientes" class="btn btn-secondary">⬅️ Volver</a>
                </div>
            </form>
        </div>
    </body>
    </html>
    """

    return HTMLResponse(content=html_content)


@router.get("/admin/proveedores-clientes/plantilla.csv")
async def descargar_plantilla_proveedores_clientes(
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = Depends(get_current_empleado),
):
    """Download CSV template for proveedores/clientes bulk upload."""
    headers = [
        "tipo",
        "nombre",
        "rfc",
        "banco",
        "cuenta_clabe",
        "cuenta_bancaria",
        "entidad_region",
        "activo",
    ]

    sample_rows = [
        [
            "proveedor",
            "Acme Corporation S.A. de C.V.",
            "ACM123456789",
            "Banco Nacional de México",
            "012345678901234567",
            "0192630164",
            "",
            "true",
        ],
        ["cliente", "Cliente Ejemplo S.A.", "", "", "", "", "", "true"],
        [
            "operadores_regionales",
            "Operador Regional Norte",
            "",
            "",
            "",
            "",
            "Norte",
            "true",
        ],
    ]

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(headers)
    for row in sample_rows:
        writer.writerow(row)

    csv_content = output.getvalue()
    output.close()

    csv_bytes = "\ufeff" + csv_content
    csv_bytes = csv_bytes.encode("utf-8")

    return Response(
        content=csv_bytes,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="plantilla_proveedores_clientes.csv"'
        },
    )


@router.post("/admin/proveedores-clientes/carga-masiva")
async def carga_masiva_proveedores_clientes_post(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = Depends(get_current_empleado),
    archivo_csv: UploadFile = File(...),
):
    """Process bulk CSV/XLSX upload for proveedores/clientes (UPSERT)."""
    from fastapi.responses import RedirectResponse
    from urllib.parse import quote

    try:
        filename = archivo_csv.filename or ""
        if not filename or not filename.lower().endswith((".csv", ".xlsx")):
            return RedirectResponse(
                url="/admin/proveedores-clientes/carga-masiva?error_msg=Debe seleccionar un archivo CSV o XLSX válido",
                status_code=303,
            )

        contents = await read_upload_limited(
            archivo_csv,
            max_bytes=MAX_DECODE_BYTES,
            too_large_message="El archivo excede el tamaño máximo permitido",
        )
        try:
            import_rows = parse_proveedores_clientes_upload(filename, contents)
        except ValueError as exc:
            return RedirectResponse(
                url=f"/admin/proveedores-clientes/carga-masiva?error_msg={quote(str(exc))}",
                status_code=303,
            )
        if not import_rows:
            return RedirectResponse(
                url="/admin/proveedores-clientes/carga-masiva?error_msg=El archivo no contiene filas válidas para importar",
                status_code=303,
            )

        # Statistics
        created_count = 0
        updated_count = 0
        omitted_count = 0

        for import_row in import_rows:
            proveedor = None
            if import_row.rfc:
                result = await session.execute(
                    select(ProveedorCliente).where(
                        and_(
                            ProveedorCliente.tipo == import_row.tipo,
                            ProveedorCliente.rfc == import_row.rfc,
                        )
                    )
                )
                proveedor = result.scalar_one_or_none()
            else:
                result = await session.execute(
                    select(ProveedorCliente).where(
                        ProveedorCliente.tipo == import_row.tipo
                    )
                )
                for prov in result.scalars().all():
                    prov_key = proveedor_match_key(
                        type(
                            "RowLike",
                            (),
                            {
                                "tipo": prov.tipo,
                                "nombre": prov.nombre,
                                "rfc": prov.rfc,
                                "cuenta_clabe": prov.cuenta_clabe,
                                "cuenta_bancaria": prov.cuenta_bancaria,
                            },
                        )()
                    )
                    if prov_key == proveedor_match_key(import_row):
                        proveedor = prov
                        break

            if proveedor:
                proveedor.nombre = import_row.nombre
                if import_row.rfc is not None:
                    proveedor.rfc = import_row.rfc
                if import_row.banco is not None:
                    proveedor.banco = import_row.banco
                if import_row.cuenta_clabe is not None:
                    proveedor.cuenta_clabe = import_row.cuenta_clabe
                if import_row.cuenta_bancaria is not None:
                    proveedor.cuenta_bancaria = import_row.cuenta_bancaria
                if import_row.entidad_region is not None:
                    proveedor.entidad_region = import_row.entidad_region
                proveedor.activo = import_row.activo
                updated_count += 1
            else:
                nuevo_proveedor = ProveedorCliente(
                    tipo=import_row.tipo,
                    nombre=import_row.nombre,
                    rfc=import_row.rfc,
                    banco=import_row.banco,
                    cuenta_clabe=import_row.cuenta_clabe,
                    cuenta_bancaria=import_row.cuenta_bancaria,
                    entidad_region=import_row.entidad_region,
                    activo=import_row.activo,
                )
                session.add(nuevo_proveedor)
                created_count += 1

        # Commit if any changes
        if created_count > 0 or updated_count > 0:
            await session.commit()
            success_msg = (
                f"Se importaron {created_count + updated_count} registros desde {filename} "
                f"({created_count} creados, {updated_count} actualizados, {omitted_count} omitidos)."
            )
            return RedirectResponse(
                url=f"/admin/proveedores-clientes/carga-masiva?success_msg={quote(success_msg)}",
                status_code=303,
            )
        else:
            await session.rollback()
            return RedirectResponse(
                url="/admin/proveedores-clientes/carga-masiva?error_msg=No se encontraron registros válidos para importar",
                status_code=303,
            )

    except Exception as e:
        logger.exception(
            "Unexpected error processing proveedores/clientes upload",
            extra={
                "filename": archivo_csv.filename or "",
                "actor_id": str(getattr(current_empleado, "id", "")),
            },
        )
        await session.rollback()
        return _redirect_with_error_message(
            "/admin/proveedores-clientes/carga-masiva",
            _BULK_GENERIC_ERROR,
        )


# ============================================================================
# Bulk CSV Upload for CFDI Reports (Issued CFDIs)
# ============================================================================


@router.get("/admin/gastos/cfdis/carga-masiva", response_class=HTMLResponse)
async def carga_masiva_cfdis_form(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = require_admin_finanzas(),
):
    """Bulk upload form for CFDI reports from CSV (issued CFDIs)."""
    success_msg = request.query_params.get("success_msg", "")
    error_msg = request.query_params.get("error_msg", "")

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Carga Masiva de CFDIs - Copa Telmex</title>
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
        </style>
    </head>
    <body>
        <div class="container">
            <h1>📥 Carga Masiva de CFDIs</h1>
            <p class="subtitle">Importar CFDIs emitidos desde archivo CSV</p>
            
            {'<div class="alert alert-success">✅ ' + success_msg + '</div>' if success_msg else ''}
            {'<div class="alert alert-error">❌ ' + error_msg + '</div>' if error_msg else ''}
            
            <div class="instructions">
                <h2>📋 Instrucciones</h2>
                <ul>
                    <li><strong>Columna requerida:</strong> <span class="code">UUID</span> (o <span class="code">cfdi_uuid</span>, <span class="code">uuid</span>) - Identificador único del CFDI</li>
                    <li><strong>Columnas opcionales comunes:</strong> Fecha, RFC Emisor, Nombre Emisor, RFC Receptor, Nombre Receptor, Serie, Folio, Total, Subtotal, IVA, Moneda, Tipo de Comprobante, Metodo de Pago, Forma de Pago, Uso CFDI, Fecha Timbrado, etc.</li>
                    <li><strong>Comportamiento (UPSERT):</strong> Si el UUID ya existe, se actualizan los campos; si no existe, se crea un nuevo registro.</li>
                    <li><strong>Codificación:</strong> UTF-8 (preferido) o Latin-1</li>
                    <li>Se ignoran filas vacías y filas con UUID inválido o duplicado en el mismo CSV</li>
                </ul>
            </div>
            
            <form method="POST" action="/admin/gastos/cfdis/carga-masiva" enctype="multipart/form-data">
                <div class="form-group">
                    <label for="archivo_csv">📂 Selecciona el archivo CSV:</label>
                    <input type="file" id="archivo_csv" name="archivo_csv" accept=".csv" required>
                </div>
                
                <div style="margin-top: 30px;">
                    <button type="submit" class="btn btn-primary">📤 Importar CFDIs</button>
                    <a href="/admin/gastos/invoices" class="btn btn-secondary">⬅️ Volver</a>
                </div>
            </form>
        </div>
    </body>
    </html>
    """

    return HTMLResponse(content=html_content)


@router.post("/admin/gastos/cfdis/carga-masiva")
async def carga_masiva_cfdis_post(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = require_admin_finanzas(),
    archivo_csv: UploadFile = File(...),
):
    """
    Process bulk CSV upload for CFDI reports (UPSERT by cfdi_uuid).

    HARDENED VERSION per LEAP_SPEC_01:
    - Handles VARCHAR overflow by truncating + preserving full values in JSONB
    - Handles unexpected columns (stored in conceptos JSONB)
    - Handles mixed encoding (UTF-8/Latin-1 with fallback)
    - Handles duplicate UUIDs (skips with count)
    - Handles invalid UUIDs (validates format, skips invalid)
    - Handles large CSVs (savepoints per row, streaming)
    - NEVER throws 500/Internal Server Errors

    CSV Field Mapping to cfdi_reports columns:
    - UUID/cfdi_uuid → cfdi_uuid (required, unique identifier)
    - Fecha → fecha (DateTime)
    - RFC Emisor → emisor_rfc (String, max 20)
    - Nombre Emisor → emisor_nombre (String, max 500)
    - RFC Receptor → receptor_rfc (String, max 20)
    - Nombre Receptor → receptor_nombre (String, max 500)
    - Serie → serie (String, max 50)
    - Folio → folio (String, max 50)
    - Total → total (Float)
    - Subtotal → subtotal (Float)
    - IVA/Total Impuestos → total_impuestos_trasladados (Float)
    - Moneda → moneda (String, max 10)
    - Tipo de Comprobante → tipo_de_comprobante (String, max 10)
    - Metodo de Pago → metodo_pago (Text - unlimited)
    - Forma de Pago → forma_pago (Text - unlimited)
    - Uso CFDI → receptor_uso_cfdi (Text - unlimited)
    - Fecha Timbrado → fecha_timbrado (DateTime)
    - Descuento → descuento (Float)
    - Tipo de Cambio → tipo_cambio (Float)
    - Lugar de Expedicion → lugar_expedicion (Text - unlimited)
    - Regimen Fiscal Emisor → emisor_regimen_fiscal (Text - unlimited)
    - Regimen Fiscal Receptor → receptor_regimen_fiscal (Text - unlimited)
    - Domicilio Fiscal Receptor → receptor_domicilio_fiscal (Text - unlimited)
    - Descripcion/Concepto → descripcion_concepto_principal (Text - unlimited)

    CSV-imported CFDIs are marked with:
    - origen = 'csv'
    - xml_parsed = False
    - nova_request_id = NULL

    Any unmapped columns and truncated values are preserved in:
    - conceptos JSONB (under "_csv_original_values" and "_csv_unmapped_columns" keys)
    """
    from fastapi.responses import RedirectResponse
    from urllib.parse import quote
    from uuid import uuid4
    from decimal import Decimal, InvalidOperation
    import re

    # Column length limits for VARCHAR columns (TEXT columns have no limit)
    # Per models.py and migration v1.0.16, these columns are now TEXT (unlimited):
    # forma_pago, metodo_pago, lugar_expedicion, emisor_regimen_fiscal,
    # receptor_uso_cfdi, receptor_domicilio_fiscal, receptor_regimen_fiscal
    VARCHAR_LIMITS = {
        "version": 10,
        "serie": 50,
        "folio": 50,
        "no_certificado": 50,
        "moneda": 10,
        "tipo_de_comprobante": 10,
        "exportacion": 10,
        "emisor_rfc": 20,
        "emisor_nombre": 500,
        "receptor_rfc": 20,
        "receptor_nombre": 500,
        "timbre_version": 10,
        "cfdi_uuid": 100,
        "rfc_prov_certif": 20,
        "no_certificado_sat": 50,
    }

    # UUID validation regex (standard UUID format)
    UUID_REGEX = re.compile(
        r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
    )

    def is_valid_uuid(uuid_str: str) -> bool:
        """Validate UUID format."""
        if not uuid_str:
            return False
        return bool(UUID_REGEX.match(uuid_str.strip()))

    def safe_truncate(value: str, max_length: int) -> tuple:
        """
        Safely truncate a value to max_length.
        Returns (truncated_value, was_truncated).
        """
        if not value:
            return (value, False)
        if len(value) <= max_length:
            return (value, False)
        return (value[:max_length], True)

    try:
        # Validate file
        if not archivo_csv.filename:
            return RedirectResponse(
                url="/admin/gastos/cfdis/carga-masiva?error_msg=Debe seleccionar un archivo",
                status_code=303,
            )

        if not archivo_csv.filename.lower().endswith(".csv"):
            return RedirectResponse(
                url="/admin/gastos/cfdis/carga-masiva?error_msg=Debe seleccionar un archivo CSV válido (extensión .csv)",
                status_code=303,
            )

        # Read and decode CSV file with UTF-8/Latin-1 fallback
        try:
            contents = await read_upload_limited(
                archivo_csv,
                max_bytes=MAX_DECODE_BYTES,
                too_large_message="El archivo CSV excede el tamaño máximo permitido",
            )
        except Exception as read_error:
            logger.error(f"Error reading CSV file: {read_error}")
            return RedirectResponse(
                url="/admin/gastos/cfdis/carga-masiva?error_msg=Error al leer el archivo",
                status_code=303,
            )

        if not contents:
            return RedirectResponse(
                url="/admin/gastos/cfdis/carga-masiva?error_msg=El archivo está vacío",
                status_code=303,
            )

        text = None

        # Try UTF-8 first (with BOM support), then Latin-1
        encodings_to_try = [
            ("utf-8-sig", "UTF-8 (with BOM)"),
            ("utf-8", "UTF-8"),
            ("latin-1", "Latin-1"),
            ("cp1252", "Windows-1252"),
        ]

        for encoding, _encoding_name in encodings_to_try:
            try:
                text = contents.decode(encoding)
                break
            except UnicodeDecodeError:
                continue

        if text is None:
            return RedirectResponse(
                url="/admin/gastos/cfdis/carga-masiva?error_msg=No se pudo decodificar el archivo. Intente guardarlo como UTF-8",
                status_code=303,
            )

        # Parse CSV with error handling for malformed rows
        try:
            csv_reader = csv.DictReader(io.StringIO(text))
            fieldnames = csv_reader.fieldnames
        except Exception as csv_error:
            logger.error(f"Error parsing CSV: {csv_error}")
            return RedirectResponse(
                url="/admin/gastos/cfdis/carga-masiva?error_msg=Error al analizar el archivo CSV. Verifique el formato",
                status_code=303,
            )

        # Validate headers
        if not fieldnames:
            return RedirectResponse(
                url="/admin/gastos/cfdis/carga-masiva?error_msg=El archivo CSV está vacío o no tiene encabezados",
                status_code=303,
            )

        # Normalize fieldnames (case-insensitive, trim whitespace)
        fieldnames_lower = {}
        original_fieldnames = []
        for f in fieldnames:
            if f:
                f_clean = f.strip()
                original_fieldnames.append(f_clean)
                fieldnames_lower[f_clean.lower()] = f_clean

        # Check required column: UUID
        uuid_col_keys = [
            "uuid",
            "cfdi_uuid",
            "cfdi uuid",
            "folio fiscal",
            "foliofiscal",
        ]
        uuid_col = None
        for key in uuid_col_keys:
            if key in fieldnames_lower:
                uuid_col = fieldnames_lower[key]
                break

        if not uuid_col:
            return RedirectResponse(
                url="/admin/gastos/cfdis/carga-masiva?error_msg=Falta columna requerida: UUID (o cfdi_uuid, uuid, folio fiscal)",
                status_code=303,
            )

        # Helper function to parse date
        def parse_date(date_str: str):
            """Parse date string in various formats."""
            if not date_str or not date_str.strip():
                return None
            date_str = date_str.strip()
            formats = [
                "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%dT%H:%M:%S.%f",
                "%Y-%m-%d %H:%M:%S.%f",
                "%Y-%m-%d",
                "%d/%m/%Y %H:%M:%S",
                "%d/%m/%Y",
                "%d-%m-%Y %H:%M:%S",
                "%d-%m-%Y",
                "%m/%d/%Y %H:%M:%S",
                "%m/%d/%Y",
            ]
            for fmt in formats:
                try:
                    return datetime.strptime(date_str, fmt)
                except ValueError:
                    continue
            return None

        # Helper function to parse money fields
        def parse_decimal(val: str):
            """Parse money value with proper handling."""
            if not val or not val.strip():
                return None
            try:
                # Remove currency symbols, commas, whitespace
                cleaned = (
                    val.strip()
                    .replace(",", "")
                    .replace(" ", "")
                    .replace("$", "")
                    .replace("MXN", "")
                    .replace("USD", "")
                )
                if not cleaned:
                    return None
                # Handle negative values in parentheses: (100.00) -> -100.00
                if cleaned.startswith("(") and cleaned.endswith(")"):
                    cleaned = "-" + cleaned[1:-1]
                decimal_val = Decimal(cleaned)
                return float(decimal_val)
            except (ValueError, InvalidOperation, AttributeError):
                return None

        # Column mapping (case-insensitive, multiple aliases)
        col_map = {
            "fecha": [
                "fecha",
                "date",
                "fecha emision",
                "fecha emisión",
                "fechaemision",
            ],
            "emisor_rfc": [
                "rfc emisor",
                "rfc_emisor",
                "emisor_rfc",
                "emisor rfc",
                "rfcemisor",
            ],
            "emisor_nombre": [
                "nombre emisor",
                "nombre_emisor",
                "emisor_nombre",
                "emisor nombre",
                "razon social emisor",
                "razón social emisor",
                "nombreemisor",
            ],
            "receptor_rfc": [
                "rfc receptor",
                "rfc_receptor",
                "receptor_rfc",
                "receptor rfc",
                "rfcreceptor",
            ],
            "receptor_nombre": [
                "nombre receptor",
                "nombre_receptor",
                "receptor_nombre",
                "receptor nombre",
                "razon social receptor",
                "razón social receptor",
                "nombrereceptor",
            ],
            "serie": ["serie"],
            "folio": ["folio"],
            "total": ["total", "importe total", "monto total"],
            "subtotal": ["subtotal", "sub total", "importe"],
            "total_impuestos_trasladados": [
                "iva",
                "total_impuestos_trasladados",
                "total impuestos trasladados",
                "impuestos",
                "impuesto",
                "iva trasladado",
            ],
            "moneda": ["moneda", "currency", "divisa"],
            "tipo_de_comprobante": [
                "tipo de comprobante",
                "tipo_de_comprobante",
                "tipo comprobante",
                "tipocomprobante",
                "tipo",
            ],
            "metodo_pago": [
                "metodo de pago",
                "metodo_de_pago",
                "metodo pago",
                "metodopago",
                "método de pago",
            ],
            "forma_pago": ["forma de pago", "forma_de_pago", "forma pago", "formapago"],
            "receptor_uso_cfdi": [
                "uso cfdi",
                "uso_cfdi",
                "uso",
                "receptor_uso_cfdi",
                "usocfdi",
            ],
            "fecha_timbrado": [
                "fecha timbrado",
                "fecha_timbrado",
                "fecha timbrado sat",
                "fechatimbrado",
            ],
            "descuento": ["descuento", "discount"],
            "tipo_cambio": [
                "tipo de cambio",
                "tipo_de_cambio",
                "tipo cambio",
                "exchange rate",
                "tipocambio",
            ],
            "lugar_expedicion": [
                "lugar de expedicion",
                "lugar_de_expedicion",
                "lugar expedicion",
                "codigo postal",
                "cp emisor",
                "lugarexpedicion",
            ],
            "emisor_regimen_fiscal": [
                "regimen fiscal emisor",
                "regimen_fiscal_emisor",
                "regimen emisor",
                "régimen fiscal emisor",
            ],
            "receptor_regimen_fiscal": [
                "regimen fiscal receptor",
                "regimen_fiscal_receptor",
                "regimen receptor",
                "régimen fiscal receptor",
            ],
            "receptor_domicilio_fiscal": [
                "domicilio fiscal receptor",
                "domicilio_fiscal_receptor",
                "domicilio receptor",
                "codigo postal receptor",
                "cp receptor",
            ],
            "descripcion_concepto_principal": [
                "descripcion",
                "descripcion_concepto_principal",
                "concepto",
                "conceptos",
                "descripción",
            ],
            "version": ["version", "versión"],
            "no_certificado": [
                "no certificado",
                "no_certificado",
                "nocertificado",
                "número certificado",
            ],
            "exportacion": ["exportacion", "exportación"],
            "rfc_prov_certif": [
                "rfc prov certif",
                "rfc_prov_certif",
                "rfc proveedor certificacion",
            ],
            "no_certificado_sat": [
                "no certificado sat",
                "no_certificado_sat",
                "nocertificadosat",
            ],
        }

        # Build column mapping and track unmapped columns
        mapped_cols = {}
        mapped_col_names = set()
        for field, possible_names in col_map.items():
            for name in possible_names:
                if name in fieldnames_lower:
                    mapped_cols[field] = fieldnames_lower[name]
                    mapped_col_names.add(fieldnames_lower[name])
                    break

        # UUID column is always mapped
        mapped_col_names.add(uuid_col)

        # Find unmapped columns (to store in JSONB)
        unmapped_cols = [f for f in original_fieldnames if f not in mapped_col_names]

        # Statistics
        created_count = 0
        updated_count = 0
        skipped_count = 0
        truncated_fields_count = 0
        invalid_uuid_count = 0
        duplicate_uuid_count = 0
        seen_uuids = set()  # Track UUIDs within CSV to skip duplicates

        # Process rows with savepoints to prevent one bad row from killing the entire upload
        # Use session.no_autoflush to prevent implicit flushes during rendering
        rows = list(csv_reader)  # Convert to list to avoid partial stream reads

        for row_num, row in enumerate(rows, start=2):
            # Use savepoint for each row - if one fails, only that row is rolled back
            try:
                async with session.begin_nested():
                    # Skip blank rows
                    if not any(v.strip() if v else "" for v in row.values()):
                        continue

                    # Extract UUID
                    cfdi_uuid_val = (row.get(uuid_col, "") or "").strip().upper()
                    if not cfdi_uuid_val:
                        skipped_count += 1
                        continue

                    # Validate UUID format
                    if not is_valid_uuid(cfdi_uuid_val):
                        invalid_uuid_count += 1
                        skipped_count += 1
                        continue

                    # Normalize UUID to uppercase (SAT standard)
                    cfdi_uuid_val = cfdi_uuid_val.upper()

                    # Skip if UUID already seen in this CSV (case-insensitive)
                    uuid_key = cfdi_uuid_val.lower()
                    if uuid_key in seen_uuids:
                        duplicate_uuid_count += 1
                        skipped_count += 1
                        continue
                    seen_uuids.add(uuid_key)

                    # Check if CFDI already exists (case-insensitive match)
                    result = await session.execute(
                        select(CFDIReport).where(
                            func.upper(CFDIReport.cfdi_uuid) == cfdi_uuid_val
                        )
                    )
                    existing_cfdi = result.scalar_one_or_none()

                    # Track truncations and original values for this row
                    row_truncations = {}
                    row_unmapped = {}

                    # Helper to get column value (with trimming and truncation)
                    def get_col(field: str, default=None):
                        nonlocal truncated_fields_count
                        col_name = mapped_cols.get(field)
                        if col_name and col_name in row:
                            val = (row[col_name] or "").strip()
                            if not val:
                                return default

                            # Check if field has a length limit
                            if field in VARCHAR_LIMITS:
                                truncated_val, was_truncated = safe_truncate(
                                    val, VARCHAR_LIMITS[field]
                                )
                                if was_truncated:
                                    truncated_fields_count += 1
                                    row_truncations[field] = (
                                        val  # Store original full value
                                    )
                                return truncated_val if truncated_val else default
                            return val
                        return default

                    # Collect unmapped column values
                    for col in unmapped_cols:
                        if col in row and row[col] and row[col].strip():
                            row_unmapped[col] = row[col].strip()

                    # Build conceptos JSONB with preserved values
                    conceptos_data = None
                    if row_truncations or row_unmapped:
                        conceptos_data = {}
                        if row_truncations:
                            conceptos_data["_csv_original_values"] = row_truncations
                        if row_unmapped:
                            conceptos_data["_csv_unmapped_columns"] = row_unmapped

                    if existing_cfdi:
                        # Update existing record
                        if "fecha" in mapped_cols:
                            existing_cfdi.fecha = parse_date(get_col("fecha"))
                        if "emisor_rfc" in mapped_cols:
                            existing_cfdi.emisor_rfc = get_col("emisor_rfc")
                        if "emisor_nombre" in mapped_cols:
                            existing_cfdi.emisor_nombre = get_col("emisor_nombre")
                        if "receptor_rfc" in mapped_cols:
                            existing_cfdi.receptor_rfc = get_col("receptor_rfc")
                        if "receptor_nombre" in mapped_cols:
                            existing_cfdi.receptor_nombre = get_col("receptor_nombre")
                        if "serie" in mapped_cols:
                            existing_cfdi.serie = get_col("serie")
                        if "folio" in mapped_cols:
                            existing_cfdi.folio = get_col("folio")
                        if "total" in mapped_cols:
                            existing_cfdi.total = parse_decimal(get_col("total"))
                        if "subtotal" in mapped_cols:
                            existing_cfdi.subtotal = parse_decimal(get_col("subtotal"))
                        if "total_impuestos_trasladados" in mapped_cols:
                            existing_cfdi.total_impuestos_trasladados = parse_decimal(
                                get_col("total_impuestos_trasladados")
                            )
                        if "moneda" in mapped_cols:
                            existing_cfdi.moneda = get_col("moneda")
                        if "tipo_de_comprobante" in mapped_cols:
                            existing_cfdi.tipo_de_comprobante = get_col(
                                "tipo_de_comprobante"
                            )
                        if "metodo_pago" in mapped_cols:
                            existing_cfdi.metodo_pago = get_col("metodo_pago")
                        if "forma_pago" in mapped_cols:
                            existing_cfdi.forma_pago = get_col("forma_pago")
                        if "receptor_uso_cfdi" in mapped_cols:
                            existing_cfdi.receptor_uso_cfdi = get_col(
                                "receptor_uso_cfdi"
                            )
                        if "fecha_timbrado" in mapped_cols:
                            existing_cfdi.fecha_timbrado = parse_date(
                                get_col("fecha_timbrado")
                            )
                        if "descuento" in mapped_cols:
                            existing_cfdi.descuento = parse_decimal(
                                get_col("descuento")
                            )
                        if "tipo_cambio" in mapped_cols:
                            existing_cfdi.tipo_cambio = parse_decimal(
                                get_col("tipo_cambio")
                            )
                        if "lugar_expedicion" in mapped_cols:
                            existing_cfdi.lugar_expedicion = get_col("lugar_expedicion")
                        if "emisor_regimen_fiscal" in mapped_cols:
                            existing_cfdi.emisor_regimen_fiscal = get_col(
                                "emisor_regimen_fiscal"
                            )
                        if "receptor_regimen_fiscal" in mapped_cols:
                            existing_cfdi.receptor_regimen_fiscal = get_col(
                                "receptor_regimen_fiscal"
                            )
                        if "receptor_domicilio_fiscal" in mapped_cols:
                            existing_cfdi.receptor_domicilio_fiscal = get_col(
                                "receptor_domicilio_fiscal"
                            )
                        if "descripcion_concepto_principal" in mapped_cols:
                            existing_cfdi.descripcion_concepto_principal = get_col(
                                "descripcion_concepto_principal"
                            )
                        if "version" in mapped_cols:
                            existing_cfdi.version = get_col("version")
                        if "no_certificado" in mapped_cols:
                            existing_cfdi.no_certificado = get_col("no_certificado")
                        if "exportacion" in mapped_cols:
                            existing_cfdi.exportacion = get_col("exportacion")
                        if "rfc_prov_certif" in mapped_cols:
                            existing_cfdi.rfc_prov_certif = get_col("rfc_prov_certif")
                        if "no_certificado_sat" in mapped_cols:
                            existing_cfdi.no_certificado_sat = get_col(
                                "no_certificado_sat"
                            )

                        # Merge conceptos data (preserve existing, add new)
                        if conceptos_data:
                            if existing_cfdi.conceptos:
                                merged = dict(existing_cfdi.conceptos)
                                merged.update(conceptos_data)
                                existing_cfdi.conceptos = merged
                            else:
                                existing_cfdi.conceptos = conceptos_data

                        # Set origen='csv' for CSV-imported CFDIs
                        existing_cfdi.origen = "csv"
                        existing_cfdi.updated_at = datetime.utcnow()
                        updated_count += 1
                    else:
                        # Create new record
                        new_cfdi = CFDIReport(
                            id=uuid4(),
                            cfdi_uuid=cfdi_uuid_val,
                            # Leave nova_request_id and numero_referencia as NULL (CSV imports)
                            nova_request_id=None,
                            numero_referencia=None,
                            # Map fields
                            fecha=(
                                parse_date(get_col("fecha"))
                                if "fecha" in mapped_cols
                                else None
                            ),
                            emisor_rfc=(
                                get_col("emisor_rfc")
                                if "emisor_rfc" in mapped_cols
                                else None
                            ),
                            emisor_nombre=(
                                get_col("emisor_nombre")
                                if "emisor_nombre" in mapped_cols
                                else None
                            ),
                            receptor_rfc=(
                                get_col("receptor_rfc")
                                if "receptor_rfc" in mapped_cols
                                else None
                            ),
                            receptor_nombre=(
                                get_col("receptor_nombre")
                                if "receptor_nombre" in mapped_cols
                                else None
                            ),
                            serie=get_col("serie") if "serie" in mapped_cols else None,
                            folio=get_col("folio") if "folio" in mapped_cols else None,
                            total=(
                                parse_decimal(get_col("total"))
                                if "total" in mapped_cols
                                else None
                            ),
                            subtotal=(
                                parse_decimal(get_col("subtotal"))
                                if "subtotal" in mapped_cols
                                else None
                            ),
                            total_impuestos_trasladados=(
                                parse_decimal(get_col("total_impuestos_trasladados"))
                                if "total_impuestos_trasladados" in mapped_cols
                                else None
                            ),
                            moneda=(
                                get_col("moneda") if "moneda" in mapped_cols else None
                            ),
                            tipo_de_comprobante=(
                                get_col("tipo_de_comprobante")
                                if "tipo_de_comprobante" in mapped_cols
                                else None
                            ),
                            metodo_pago=(
                                get_col("metodo_pago")
                                if "metodo_pago" in mapped_cols
                                else None
                            ),
                            forma_pago=(
                                get_col("forma_pago")
                                if "forma_pago" in mapped_cols
                                else None
                            ),
                            receptor_uso_cfdi=(
                                get_col("receptor_uso_cfdi")
                                if "receptor_uso_cfdi" in mapped_cols
                                else None
                            ),
                            fecha_timbrado=(
                                parse_date(get_col("fecha_timbrado"))
                                if "fecha_timbrado" in mapped_cols
                                else None
                            ),
                            descuento=(
                                parse_decimal(get_col("descuento"))
                                if "descuento" in mapped_cols
                                else None
                            ),
                            tipo_cambio=(
                                parse_decimal(get_col("tipo_cambio"))
                                if "tipo_cambio" in mapped_cols
                                else None
                            ),
                            lugar_expedicion=(
                                get_col("lugar_expedicion")
                                if "lugar_expedicion" in mapped_cols
                                else None
                            ),
                            emisor_regimen_fiscal=(
                                get_col("emisor_regimen_fiscal")
                                if "emisor_regimen_fiscal" in mapped_cols
                                else None
                            ),
                            receptor_regimen_fiscal=(
                                get_col("receptor_regimen_fiscal")
                                if "receptor_regimen_fiscal" in mapped_cols
                                else None
                            ),
                            receptor_domicilio_fiscal=(
                                get_col("receptor_domicilio_fiscal")
                                if "receptor_domicilio_fiscal" in mapped_cols
                                else None
                            ),
                            descripcion_concepto_principal=(
                                get_col("descripcion_concepto_principal")
                                if "descripcion_concepto_principal" in mapped_cols
                                else None
                            ),
                            version=(
                                get_col("version") if "version" in mapped_cols else None
                            ),
                            no_certificado=(
                                get_col("no_certificado")
                                if "no_certificado" in mapped_cols
                                else None
                            ),
                            exportacion=(
                                get_col("exportacion")
                                if "exportacion" in mapped_cols
                                else None
                            ),
                            rfc_prov_certif=(
                                get_col("rfc_prov_certif")
                                if "rfc_prov_certif" in mapped_cols
                                else None
                            ),
                            no_certificado_sat=(
                                get_col("no_certificado_sat")
                                if "no_certificado_sat" in mapped_cols
                                else None
                            ),
                            # CSV imports are not XML-parsed
                            xml_parsed=False,
                            parsed_at=None,
                            xml_raw=None,
                            # Store preserved values
                            conceptos=conceptos_data,
                            impuestos_detalle=None,
                            # Mark as CSV-imported
                            origen="csv",
                        )
                        session.add(new_cfdi)
                        created_count += 1
                    # Savepoint commits automatically on successful exit
            except Exception as row_error:
                # Savepoint automatically rolls back on exception
                # Log and continue processing other rows - NEVER fail the whole import
                logger.warning(
                    f"Error processing row {row_num}: {row_error}", exc_info=True
                )
                skipped_count += 1
                continue

        # Commit if any changes
        if created_count > 0 or updated_count > 0:
            try:
                await session.commit()
            except Exception as commit_error:
                logger.exception(
                    "Unexpected error committing CFDI import",
                    extra={
                        "filename": archivo_csv.filename or "",
                        "actor_id": str(getattr(current_empleado, "id", "")),
                    },
                )
                await session.rollback()
                return _redirect_with_error_message(
                    "/admin/gastos/cfdis/carga-masiva",
                    _BULK_GENERIC_ERROR,
                )

            # Auto-link matching expenses and documentos (e.g. SOLICITUD a terceros) after
            # CFDI import (case/trim-insensitive; shared helpers).
            linked_count = 0
            linked_documentos_count = 0
            try:
                linked_count = await bulk_link_pending_expenses_to_cfdi_reports(session)
                linked_documentos_count = (
                    await bulk_link_pending_documentos_to_cfdi_reports(session)
                )
                if linked_count > 0 or linked_documentos_count > 0:
                    await session.commit()
            except Exception as link_error:
                logger.warning(
                    f"Error auto-linking expenses/documentos: {link_error}",
                    exc_info=True,
                )

            # Build success message with all statistics
            success_parts = []
            success_parts.append(f"{created_count + updated_count} CFDIs importados")
            if created_count > 0:
                success_parts.append(f"{created_count} creados")
            if updated_count > 0:
                success_parts.append(f"{updated_count} actualizados")
            if linked_count > 0:
                success_parts.append(
                    f"{linked_count} gastos vinculados automáticamente"
                )
            if linked_documentos_count > 0:
                success_parts.append(
                    f"{linked_documentos_count} solicitudes vinculadas automáticamente"
                )

            success_msg = ", ".join(success_parts) + "."

            # Add warnings if any
            warnings = []
            if skipped_count > 0:
                warnings.append(f"{skipped_count} filas omitidas")
            if truncated_fields_count > 0:
                warnings.append(
                    f"{truncated_fields_count} campos truncados (valores completos preservados)"
                )
            if invalid_uuid_count > 0:
                warnings.append(f"{invalid_uuid_count} UUIDs inválidos")
            if duplicate_uuid_count > 0:
                warnings.append(f"{duplicate_uuid_count} UUIDs duplicados en CSV")

            if warnings:
                success_msg += " Advertencias: " + ", ".join(warnings) + "."

            return RedirectResponse(
                url=f"/admin/gastos/cfdis/carga-masiva?success_msg={quote(success_msg)}",
                status_code=303,
            )
        else:
            # No valid records found
            error_details = []
            if skipped_count > 0:
                error_details.append(f"{skipped_count} filas omitidas")
            if invalid_uuid_count > 0:
                error_details.append(f"{invalid_uuid_count} UUIDs inválidos")
            if duplicate_uuid_count > 0:
                error_details.append(f"{duplicate_uuid_count} UUIDs duplicados")

            error_msg = "No se encontraron registros válidos para importar"
            if error_details:
                error_msg += ": " + ", ".join(error_details)

            return RedirectResponse(
                url=f"/admin/gastos/cfdis/carga-masiva?error_msg={quote(error_msg)}",
                status_code=303,
            )

    except Exception as e:
        # Catch-all for any unexpected errors - NEVER return 500
        logger.exception(
            "Unexpected error in CFDI CSV upload",
            extra={
                "filename": archivo_csv.filename or "",
                "actor_id": str(getattr(current_empleado, "id", "")),
            },
        )
        try:
            await session.rollback()
        except Exception:
            pass
        return _redirect_with_error_message(
            "/admin/gastos/cfdis/carga-masiva",
            _BULK_GENERIC_ERROR,
        )


# ============================================================================
# CFDI MATCHING CONTROL ROOM
# Per LEAP_SPEC_2: Operator-facing dashboard for CFDI ↔ expense matching
# ============================================================================


@router.get("/admin/gastos/sat", response_class=HTMLResponse)
async def admin_sat_dashboard(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = require_admin_finanzas(),
) -> str:
    sat_handler = SATExpenseHandler()
    success_msg = (request.query_params.get("success_msg") or "").strip()
    error_msg = (request.query_params.get("error_msg") or "").strip()
    current_result = _decode_sat_result_payload(
        request.query_params.get("sat_result", "")
    )
    cfdi_status_result = _decode_sat_result_payload(
        request.query_params.get("cfdi_status_result", "")
    )
    cfdi_status_html = _format_sat_status_result(cfdi_status_result)
    sat_catalogs_html = _sat_catalogs_html()

    credentials_status = await sat_handler.get_credentials_status(session)
    credentials = credentials_status.get("credentials")
    current_request_id = ""
    recent_requests: List[Dict[str, Any]] = []

    recent_expense_result = await session.execute(
        select(ExpenseReport)
        .options(selectinload(ExpenseReport.empleado))
        .where(ExpenseReport.nova_request_id.isnot(None))
        .order_by(ExpenseReport.updated_at.desc(), ExpenseReport.created_at.desc())
        .limit(20)
    )
    recent_expenses = recent_expense_result.scalars().all()
    seen_request_ids: set[str] = set()
    for expense in recent_expenses:
        solicitud_id = (expense.nova_request_id or "").strip()
        if not solicitud_id or solicitud_id in seen_request_ids:
            continue
        seen_request_ids.add(solicitud_id)
        recent_requests.append(
            {
                "solicitud_id": solicitud_id,
                "expense_id": str(expense.id),
                "reference": expense.numero_referencia or "—",
                "employee": expense.empleado.nombre if expense.empleado else "—",
                "fecha_inicial": expense.fecha.strftime("%Y-%m-%d") if expense.fecha else "—",
                "fecha_final": expense.fecha.strftime("%Y-%m-%d") if expense.fecha else "—",
                "estado": expense.estado_factura or "pendiente",
                "num_cfdis": "",
                "num_paquetes": "",
                "ingested_cfdis": "",
                "message": expense.mensaje_error or "",
                "source": "expense",
            }
        )

    if current_result:
        current_request_id = str(current_result.get("solicitud_id") or "").strip()
        if current_request_id and current_request_id not in seen_request_ids:
            recent_requests.insert(0, current_result)
        elif current_request_id:
            recent_requests = [
                current_result if str(row.get("solicitud_id") or "").strip() == current_request_id else row
                for row in recent_requests
            ]

    alerts_html = ""
    if success_msg:
        alerts_html += f'<div class="status-banner success"><strong>✅ Éxito:</strong> {escape(success_msg)}</div>'
    if error_msg:
        alerts_html += f'<div class="status-banner error"><strong>⚠️ Error:</strong> {escape(error_msg)}</div>'

    cred_badge = "No configurado"
    cred_badge_style = "background:#fee2e2;color:#991b1b;"
    cred_detail_html = "<div style='font-size:13px;color:#64748b;'>No hay e.firma activa cargada.</div>"
    if credentials_status.get("status") == "configured" and credentials:
        days_until_expiry = credentials_status.get("days_until_expiry")
        expired = bool(credentials_status.get("expired"))
        if expired:
            cred_badge = "Expirado"
            cred_badge_style = "background:#fee2e2;color:#991b1b;"
        elif days_until_expiry is not None and days_until_expiry < 30:
            cred_badge = "Por vencer"
            cred_badge_style = "background:#fef3c7;color:#92400e;"
        else:
            cred_badge = "Vigente"
            cred_badge_style = "background:#dcfce7;color:#166534;"
        cred_detail_html = f"""
            <div class="meta-grid">
                <div class="meta-card"><span>RFC</span><strong>{escape(credentials.rfc)}</strong><small>Credencial activa.</small></div>
                <div class="meta-card"><span>Vigencia</span><strong>{escape(credentials.certificate_expiry.strftime('%Y-%m-%d') if credentials.certificate_expiry else 'N/A')}</strong><small>Fecha del certificado.</small></div>
                <div class="meta-card"><span>Último uso</span><strong>{escape(credentials.last_used.strftime('%Y-%m-%d %H:%M') if credentials.last_used else 'Nunca')}</strong><small>Trazabilidad operativa.</small></div>
            </div>
        """

    request_rows = ""
    for row in recent_requests:
        solicitud_id = escape(str(row.get("solicitud_id") or ""))
        expense_id = escape(str(row.get("expense_id") or ""))
        reference = escape(str(row.get("reference") or "—"))
        employee = escape(str(row.get("employee") or "—"))
        fecha_inicial = escape(str(row.get("fecha_inicial") or "—"))
        fecha_final = escape(str(row.get("fecha_final") or "—"))
        estado = escape(str(row.get("estado") or "—"))
        num_cfdis = escape(str(row.get("num_cfdis") or "—"))
        num_paquetes = escape(str(row.get("num_paquetes") or "—"))
        ingested_cfdis = escape(str(row.get("ingested_cfdis") or "—"))
        message = escape(str(row.get("message") or ""))
        request_rows += f"""
        <tr>
            <td><code>{solicitud_id}</code></td>
            <td>{reference}</td>
            <td>{employee}</td>
            <td>{fecha_inicial}</td>
            <td>{fecha_final}</td>
            <td>{estado}</td>
            <td>{num_cfdis}</td>
            <td>{num_paquetes}</td>
            <td>{ingested_cfdis}</td>
            <td title="{message}">{message[:80] + ('…' if len(message) > 80 else '')}</td>
            <td>
                <form method="POST" action="/admin/gastos/sat/check" style="display:inline-block;margin:0 6px 6px 0;">
                    <input type="hidden" name="solicitud_id" value="{solicitud_id}">
                    <input type="hidden" name="expense_id" value="{expense_id}">
                    <button type="submit" class="button secondary">Refrescar</button>
                </form>
                <form method="POST" action="/admin/gastos/sat/process" style="display:inline-block;">
                    <input type="hidden" name="solicitud_id" value="{solicitud_id}">
                    <input type="hidden" name="expense_id" value="{expense_id}">
                    <button type="submit" class="button primary">Procesar</button>
                </form>
            </td>
        </tr>
        """

    hero_actions_html = """
        <a href="/admin/gastos/cfdis/matching" class="button secondary">Matching CFDI</a>
        <a href="/admin/gastos/cfdis/carga-masiva" class="button secondary">Carga CFDIs</a>
    """
    hero_side_html = f"""
        <div class="eyebrow">Estado SAT</div>
        <div style="display:inline-flex;padding:8px 12px;border-radius:999px;font-size:12px;font-weight:800;{cred_badge_style}">{escape(cred_badge)}</div>
        <div class="meta-grid" style="margin-top:14px;">
            <div class="meta-card"><span>Solicitudes visibles</span><strong>{len(recent_requests)}</strong><small>Gastos recientes + solicitud activa.</small></div>
            <div class="meta-card"><span>Solicitud activa</span><strong>{escape(current_request_id or '—')}</strong><small>Contexto en revisión.</small></div>
        </div>
    """

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Operación SAT CFDI - Admin</title>
        <style>
            {_admin_workspace_styles("1700px")}
            .status-banner {{
                border-radius:18px;padding:14px 16px;border:1px solid transparent;font-size:14px;line-height:1.55;
            }}
            .status-banner.error {{ background:#fef2f2;border-color:#fecaca;color:#991b1b; }}
            .status-banner.success {{ background:#ecfdf3;border-color:#bbf7d0;color:#166534; }}
            .form-grid {{ display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px; }}
            .form-grid .full {{ grid-column:1 / -1; }}
            .field label {{ display:block;font-size:12px;font-weight:700;color:#334155;margin-bottom:6px; }}
            .field input {{ width:100%;padding:10px 12px;border:1px solid #cbd5e1;border-radius:12px;background:#fff; }}
            .sat-tabs {{ display:flex;flex-wrap:wrap;gap:8px;margin-top:-4px; }}
            .sat-tab {{ text-decoration:none;padding:10px 14px;border-radius:14px;border:1px solid #dbe2ea;background:#fff;color:#334155;font-size:13px;font-weight:800; }}
            .sat-tab.active {{ background:#0f766e;color:#f8fafc;border-color:rgba(16,185,129,.34);box-shadow:0 10px 24px rgba(15,118,110,.18); }}
            @media (max-width: 900px) {{ .form-grid {{ grid-template-columns:1fr; }} }}
        </style>
    </head>
    <body>
        <div class="container">
            {render_admin_navigation(current_empleado, "sat", subtitle="e.firma SAT, solicitudes de descarga y procesamiento de CFDI desde la misma consola financiera.")}
            {_render_admin_workspace_hero(
                eyebrow="Finanzas",
                title="e.firma SAT",
                description="Carga certificado, llave privada y password para habilitar el conector SAT de descarga de facturas.",
                actions_html=hero_actions_html,
                side_html=hero_side_html,
            )}
            <div class="stack">
                {alerts_html}
                <nav class="sat-tabs" aria-label="Pestañas SAT">
                    <a class="sat-tab active" href="#efirma-sat">e.firma SAT</a>
                    <a class="sat-tab" href="#descarga-sat">Descarga masiva</a>
                    <a class="sat-tab" href="#consulta-cfdi">Consulta CFDI</a>
                    <a class="sat-tab" href="/admin/gastos/cfdis/matching">Matching</a>
                    <a class="sat-tab" href="#catalogos-rfc">Catálogos/RFC</a>
                    <a class="sat-tab" href="#bandeja-sat">Bandeja SAT</a>
                </nav>
                {cfdi_status_html}
                <section class="surface">
                    <div class="section-head"><div><div class="eyebrow">Credenciales</div><h2>Estado de e.firma</h2><div class="section-note">Se reutiliza la credencial SAT activa del ambiente actual.</div></div></div>
                    {cred_detail_html}
                </section>
                <section class="surface" id="efirma-sat">
                    <div class="section-head"><div><div class="eyebrow">Pestaña e.firma SAT</div><h2>Cargar archivos de firma electrónica</h2><div class="section-note">Carga RFC, certificado `.cer` o `.cert`, llave `.key` y password.</div></div></div>
                    <form method="POST" action="/admin/gastos/sat/credentials" enctype="multipart/form-data">
                        <div class="form-grid">
                            <div class="field"><label>RFC</label><input type="text" name="rfc" value="{escape(credentials.rfc if credentials else '')}" required></div>
                            <div class="field"><label>Password</label><input type="password" name="passphrase" required></div>
                            <div class="field"><label>Archivo certificado .cer/.cert</label><input type="file" name="certificate_file" accept=".cer,.cert" required></div>
                            <div class="field"><label>Archivo .key</label><input type="file" name="private_key_file" accept=".key" required></div>
                        </div>
                        <div class="hero-actions" style="margin-top:14px;"><button type="submit" class="button primary">Guardar credenciales</button></div>
                    </form>
                </section>
                <section class="surface" id="descarga-sat">
                    <div class="section-head"><div><div class="eyebrow">Descarga masiva</div><h2>Nueva solicitud SAT</h2><div class="section-note">Crea una solicitud por rango de fechas, con filtros opcionales de emisor y receptor. Si mandas `expense_id`, la solicitud se amarra al gasto.</div></div></div>
                    <form method="POST" action="/admin/gastos/sat/request">
                        <div class="form-grid">
                            <div class="field"><label>Fecha inicial</label><input type="date" name="fecha_inicial" required></div>
                            <div class="field"><label>Fecha final</label><input type="date" name="fecha_final" required></div>
                            <div class="field"><label>RFC emisor</label><input type="text" name="rfc_emisor"></div>
                            <div class="field"><label>RFC receptor</label><input type="text" name="rfc_receptor"></div>
                            <div class="field full"><label>Expense ID opcional</label><input type="text" name="expense_id" placeholder="UUID del gasto si quieres vincular la solicitud al flujo AR"></div>
                        </div>
                        <div class="hero-actions" style="margin-top:14px;"><button type="submit" class="button primary">Crear solicitud SAT</button></div>
                    </form>
                </section>
                <section class="surface" id="consulta-cfdi">
                    <div class="section-head"><div><div class="eyebrow">Consulta CFDI</div><h2>Validar estatus de comprobante</h2><div class="section-note">Consulta un CFDI por UUID, RFC emisor, RFC receptor y total. No requiere e.firma.</div></div></div>
                    <form method="POST" action="/admin/gastos/sat/cfdi-status">
                        <div class="form-grid">
                            <div class="field full"><label>UUID / folio fiscal</label><input type="text" name="cfdi_uuid" required></div>
                            <div class="field"><label>RFC emisor</label><input type="text" name="rfc_emisor" required></div>
                            <div class="field"><label>RFC receptor</label><input type="text" name="rfc_receptor" required></div>
                            <div class="field"><label>Total</label><input type="text" name="total" inputmode="decimal" required></div>
                        </div>
                        <div class="hero-actions" style="margin-top:14px;"><button type="submit" class="button primary">Consultar estatus CFDI</button></div>
                    </form>
                </section>
                <section class="surface">
                    <div class="section-head"><div><div class="eyebrow">Consulta</div><h2>Consultar solicitud puntual</h2><div class="section-note">Refresca una solicitud SAT ya emitida, con o sin vínculo a gasto.</div></div></div>
                    <form method="POST" action="/admin/gastos/sat/check">
                        <div class="form-grid">
                            <div class="field"><label>Solicitud ID</label><input type="text" name="solicitud_id" value="{escape(current_request_id)}" required></div>
                            <div class="field"><label>Expense ID opcional</label><input type="text" name="expense_id" value="{escape(str(current_result.get('expense_id') or '') if current_result else '')}"></div>
                        </div>
                        <div class="hero-actions" style="margin-top:14px;"><button type="submit" class="button secondary">Consultar estado</button></div>
                    </form>
                </section>
                <section class="surface" id="catalogos-rfc">
                    <div class="section-head"><div><div class="eyebrow">Catálogos/RFC</div><h2>Catálogos fiscales locales</h2><div class="section-note">Catálogos SAT mínimos para formularios y validaciones operativas sin depender del SAT en vivo.</div></div></div>
                    <div class="meta-grid">{sat_catalogs_html}</div>
                </section>
                <section class="surface" id="bandeja-sat">
                    <div class="section-head"><div><div class="eyebrow">Bandeja</div><h2>Solicitudes SAT recientes</h2><div class="section-note">Combina solicitudes ligadas a gastos recientes con la solicitud activa que estés revisando.</div></div></div>
                    <div class="table-shell">
                        <table>
                            <thead>
                                <tr>
                                    <th>Solicitud</th>
                                    <th>Referencia</th>
                                    <th>Empleado</th>
                                    <th>Inicio</th>
                                    <th>Fin</th>
                                    <th>Estado SAT</th>
                                    <th>CFDIs</th>
                                    <th>Paquetes</th>
                                    <th>Ingeridos</th>
                                    <th>Resultado</th>
                                    <th>Acciones</th>
                                </tr>
                            </thead>
                            <tbody>
                                {request_rows if request_rows else '<tr><td colspan="11" style="text-align:center;padding:24px;">No hay solicitudes SAT visibles todavía.</td></tr>'}
                            </tbody>
                        </table>
                    </div>
                </section>
            </div>
        </div>
    </body>
    </html>
    """


@router.post("/admin/gastos/sat/credentials")
async def admin_sat_upload_credentials(
    rfc: str = Form(...),
    passphrase: str = Form(...),
    certificate_file: UploadFile = File(...),
    private_key_file: UploadFile = File(...),
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = require_admin_finanzas(),
):
    rfc_clean = (rfc or "").strip().upper()
    if not rfc_clean:
        return RedirectResponse(
            url=_sat_redirect_url(
                error_msg=f"{_SAT_EFIRMA_VALIDATION_ERROR_PREFIX}: RFC requerido."
            ),
            status_code=303,
        )
    if not (passphrase or "").strip():
        return RedirectResponse(
            url=_sat_redirect_url(
                error_msg=f"{_SAT_EFIRMA_VALIDATION_ERROR_PREFIX}: password requerido."
            ),
            status_code=303,
        )

    try:
        cert_bytes = await _read_sat_efirma_upload(
            certificate_file,
            allowed_extensions=_SAT_CERTIFICATE_EXTENSIONS,
            label="El certificado",
        )
        key_bytes = await _read_sat_efirma_upload(
            private_key_file,
            allowed_extensions=_SAT_PRIVATE_KEY_EXTENSIONS,
            label="La llave privada",
        )
    except ValueError as exc:
        return RedirectResponse(
            url=_sat_redirect_url(
                error_msg=f"{_SAT_EFIRMA_VALIDATION_ERROR_PREFIX}: {exc}"
            ),
            status_code=303,
        )

    sat_handler = SATExpenseHandler()
    try:
        result = await sat_handler.setup_credentials(
            session,
            rfc=rfc_clean,
            certificate_file_data=cert_bytes,
            private_key_file_data=key_bytes,
            passphrase=passphrase,
        )
        redirect = _sat_redirect_url(
            success_msg=(
                result.get("message")
                if result.get("status") == "success"
                else None
            ),
            error_msg=(
                result.get("message")
                if result.get("status") != "success"
                else None
            ),
        )
        return RedirectResponse(url=redirect, status_code=303)
    except Exception as exc:
        await session.rollback()
        logger.exception(
            "Unexpected error saving SAT credentials",
            extra={
                "rfc": rfc_clean,
                "actor_id": str(getattr(current_empleado, "id", "")),
            },
        )
        return RedirectResponse(
            url=_sat_redirect_url(error_msg=_SAT_GENERIC_ERROR),
            status_code=303,
        )


@router.post("/admin/gastos/sat/cfdi-status")
async def admin_sat_cfdi_status(
    cfdi_uuid: str = Form(...),
    rfc_emisor: str = Form(...),
    rfc_receptor: str = Form(...),
    total: str = Form(...),
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = require_admin_finanzas(),
):
    sat_handler = SATExpenseHandler()
    try:
        result = await sat_handler.consult_cfdi_status(
            uuid=cfdi_uuid,
            rfc_emisor=rfc_emisor,
            rfc_receptor=rfc_receptor,
            total=total,
        )
        ok = result.get("status") not in {"error", ""}
        return RedirectResponse(
            url=_sat_redirect_url(
                success_msg="Consulta CFDI completada" if ok else None,
                error_msg=result.get("message") if not ok else None,
                cfdi_status_result=result,
            ),
            status_code=303,
        )
    except Exception:
        await session.rollback()
        logger.exception(
            "Unexpected error consulting SAT CFDI status",
            extra={
                "cfdi_uuid": (cfdi_uuid or "").strip().upper(),
                "actor_id": str(getattr(current_empleado, "id", "")),
            },
        )
        return RedirectResponse(
            url=_sat_redirect_url(error_msg=_SAT_GENERIC_ERROR),
            status_code=303,
        )


@router.post("/admin/gastos/sat/cfdi/{cfdi_id}/status")
async def admin_sat_saved_cfdi_status(
    cfdi_id: UUIDType,
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = require_admin_finanzas(),
):
    try:
        cfdi = await session.get(CFDIReport, cfdi_id)
        if not cfdi:
            return RedirectResponse(
                url=_sat_redirect_url(error_msg="CFDI no encontrado."),
                status_code=303,
            )
        sat_handler = SATExpenseHandler()
        result = await sat_handler.consult_cfdi_status(
            uuid=cfdi.cfdi_uuid,
            rfc_emisor=cfdi.emisor_rfc,
            rfc_receptor=cfdi.receptor_rfc,
            total=cfdi.total,
        )
        ok = result.get("status") not in {"error", ""}
        return RedirectResponse(
            url=_sat_redirect_url(
                success_msg="Consulta CFDI completada" if ok else None,
                error_msg=result.get("message") if not ok else None,
                cfdi_status_result=result,
            ),
            status_code=303,
        )
    except Exception:
        await session.rollback()
        logger.exception(
            "Unexpected error consulting saved CFDI status",
            extra={
                "cfdi_id": str(cfdi_id),
                "actor_id": str(getattr(current_empleado, "id", "")),
            },
        )
        return RedirectResponse(
            url=_sat_redirect_url(error_msg=_SAT_GENERIC_ERROR),
            status_code=303,
        )


@router.post("/admin/gastos/sat/matching/validate-selected")
async def admin_sat_validate_selected_cfdis(
    cfdi_ids: List[str] = Form(default=[]),
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = require_admin_finanzas(),
):
    selected = [(value or "").strip() for value in cfdi_ids if (value or "").strip()]
    if not selected:
        return RedirectResponse(
            url=_sat_redirect_url(error_msg="Selecciona al menos un CFDI."),
            status_code=303,
        )
    sat_handler = SATExpenseHandler()
    checked = 0
    last_result: Optional[Dict[str, Any]] = None
    try:
        for raw_id in selected[:20]:
            cfdi = await session.get(CFDIReport, UUIDType(raw_id))
            if not cfdi:
                continue
            last_result = await sat_handler.consult_cfdi_status(
                uuid=cfdi.cfdi_uuid,
                rfc_emisor=cfdi.emisor_rfc,
                rfc_receptor=cfdi.receptor_rfc,
                total=cfdi.total,
            )
            checked += 1
        if checked == 0:
            return RedirectResponse(
                url=_sat_redirect_url(error_msg="No se encontró ningún CFDI."),
                status_code=303,
            )
        return RedirectResponse(
            url=_sat_redirect_url(
                success_msg=f"Se validaron {checked} CFDI(s) ante SAT.",
                cfdi_status_result=last_result,
            ),
            status_code=303,
        )
    except Exception:
        await session.rollback()
        logger.exception(
            "Unexpected error validating selected CFDIs",
            extra={"actor_id": str(getattr(current_empleado, "id", ""))},
        )
        return RedirectResponse(
            url=_sat_redirect_url(error_msg=_SAT_GENERIC_ERROR),
            status_code=303,
        )


@router.post("/admin/gastos/sat/request")
async def admin_sat_create_request(
    fecha_inicial: str = Form(...),
    fecha_final: str = Form(...),
    rfc_emisor: str = Form(""),
    rfc_receptor: str = Form(""),
    expense_id: str = Form(""),
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = require_admin_finanzas(),
):
    sat_handler = SATExpenseHandler()
    try:
        start_date = _parse_sat_form_date(fecha_inicial)
        end_date = _parse_sat_form_date(fecha_final, end_of_day=True)
        expense_id_clean = (expense_id or "").strip()
        if expense_id_clean:
            result = await sat_handler.create_download_request_for_expense(
                session,
                UUIDType(expense_id_clean),
                fecha_inicial=start_date,
                fecha_final=end_date,
                rfc_emisor=(rfc_emisor or "").strip().upper() or None,
                rfc_receptor=(rfc_receptor or "").strip().upper() or None,
            )
        else:
            result = await sat_handler.create_download_request(
                session,
                fecha_inicial=start_date,
                fecha_final=end_date,
                rfc_emisor=(rfc_emisor or "").strip().upper() or None,
                rfc_receptor=(rfc_receptor or "").strip().upper() or None,
            )

        sat_payload = {
            "solicitud_id": ((result.get("result") or {}).get("solicitud_id") or "").strip(),
            "expense_id": expense_id_clean,
            "reference": "",
            "employee": "",
            "fecha_inicial": fecha_inicial,
            "fecha_final": fecha_final,
            "estado": "solicitud_creada" if result.get("status") == "success" else "error",
            "num_cfdis": "",
            "num_paquetes": "",
            "ingested_cfdis": "",
            "message": result.get("message") or "",
            "source": "manual",
        }
        return RedirectResponse(
            url=_sat_redirect_url(
                success_msg=result.get("message") if result.get("status") == "success" else None,
                error_msg=result.get("message") if result.get("status") != "success" else None,
                current_result=sat_payload if sat_payload["solicitud_id"] else None,
            ),
            status_code=303,
        )
    except Exception as exc:
        await session.rollback()
        logger.exception(
            "Unexpected error creating SAT request",
            extra={
                "expense_id": (expense_id or "").strip(),
                "rfc_emisor": (rfc_emisor or "").strip().upper(),
                "rfc_receptor": (rfc_receptor or "").strip().upper(),
                "actor_id": str(getattr(current_empleado, "id", "")),
            },
        )
        return RedirectResponse(
            url=_sat_redirect_url(error_msg=_SAT_GENERIC_ERROR),
            status_code=303,
        )


@router.post("/admin/gastos/sat/check")
async def admin_sat_check_request(
    solicitud_id: str = Form(...),
    expense_id: str = Form(""),
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = require_admin_finanzas(),
):
    sat_handler = SATExpenseHandler()
    try:
        result = await sat_handler.check_download_request(
            session,
            solicitud_id=(solicitud_id or "").strip(),
            poll_until_complete=False,
        )
        sat_result = result.get("result") or {}
        payload = {
            "solicitud_id": (solicitud_id or "").strip(),
            "expense_id": (expense_id or "").strip(),
            "reference": "",
            "employee": "",
            "fecha_inicial": "",
            "fecha_final": "",
            "estado": sat_result.get("estado") or result.get("status") or "—",
            "num_cfdis": sat_result.get("num_cfdis", ""),
            "num_paquetes": len(sat_result.get("paquetes") or []),
            "ingested_cfdis": "",
            "message": result.get("message") or sat_result.get("mensaje") or "",
            "source": "manual",
        }
        return RedirectResponse(
            url=_sat_redirect_url(
                success_msg=result.get("message") if result.get("status") == "success" else None,
                error_msg=result.get("message") if result.get("status") != "success" else None,
                current_result=payload,
            ),
            status_code=303,
        )
    except Exception as exc:
        await session.rollback()
        logger.exception(
            "Unexpected error checking SAT request",
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


@router.post("/admin/gastos/sat/process")
async def admin_sat_process_request(
    solicitud_id: str = Form(...),
    expense_id: str = Form(""),
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = require_admin_finanzas(),
):
    sat_handler = SATExpenseHandler()
    try:
        expense = None
        expense_id_clean = (expense_id or "").strip()
        if expense_id_clean:
            expense = await session.get(ExpenseReport, UUIDType(expense_id_clean))
        result = await sat_handler.process_download_request(
            session,
            solicitud_id=(solicitud_id or "").strip(),
            expense=expense,
        )
        process_result = result.get("result") or {}
        verification = process_result.get("verification") or {}
        payload = {
            "solicitud_id": (solicitud_id or "").strip(),
            "expense_id": expense_id_clean,
            "reference": getattr(expense, "numero_referencia", "") if expense else "",
            "employee": getattr(getattr(expense, "empleado", None), "nombre", "") if expense else "",
            "fecha_inicial": "",
            "fecha_final": "",
            "estado": verification.get("estado") or result.get("status") or "—",
            "num_cfdis": verification.get("num_cfdis", ""),
            "num_paquetes": len(process_result.get("packages") or []),
            "ingested_cfdis": process_result.get("ingested_cfdis", ""),
            "message": "; ".join(process_result.get("warnings") or []) or result.get("message") or "",
            "source": "manual",
        }
        return RedirectResponse(
            url=_sat_redirect_url(
                success_msg=result.get("message") if result.get("status") in {"success", "warning"} else None,
                error_msg=result.get("message") if result.get("status") not in {"success", "warning"} else None,
                current_result=payload,
            ),
            status_code=303,
        )
    except Exception as exc:
        await session.rollback()
        logger.exception(
            "Unexpected error processing SAT request",
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


@router.get("/admin/gastos/cfdis/matching", response_class=HTMLResponse)
async def cfdi_matching_control_room(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = require_admin_finanzas(),
    view: Optional[str] = Query(None),  # 'pendiente', 'vinculado', 'sin_gasto'
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
        show_pending = active_view in {"", "pendiente"}
        show_linked = active_view in {"", "vinculado"}
        show_unlinked = active_view in {"", "sin_gasto"}

        with session.no_autoflush:
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
            linked_query = (
                select(ExpenseReport)
                .options(selectinload(ExpenseReport.empleado))
                .options(selectinload(ExpenseReport.cfdi_report))
                .where(
                    and_(
                        ExpenseReport.cfdi_report_id.isnot(None),
                        ExpenseReport.estado_gasto == "activo",
                    )
                )
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
            unlinked_cfdis_query = (
                select(CFDIReport)
                .where(~CFDIReport.id.in_(linked_cfdi_ids_subquery))
                .order_by(CFDIReport.created_at.desc())
                .limit(500)
            )
            unlinked_result = await session.execute(unlinked_cfdis_query)
            unlinked_cfdis = unlinked_result.scalars().all()

            # Get counts for summary
            pending_count = len(pending_expenses)
            linked_count = len(linked_expenses)
            unlinked_cfdi_count = len(unlinked_cfdis)

        # Build pending expenses table rows
        pending_rows = ""
        for expense in pending_expenses:
            empleado_name = expense.empleado.nombre if expense.empleado else "N/A"
            fecha_str = expense.fecha.strftime("%Y-%m-%d") if expense.fecha else "-"
            ar_status = evaluate_ar_status(expense)
            match_status = evaluate_three_way_match(expense)
            pending_rows += f"""
            <tr>
                <td>{format_value(expense.numero_referencia)}</td>
                <td>{fecha_str}</td>
                <td>{format_value(empleado_name)}</td>
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
            cfdi = expense.cfdi_report
            cfdi_uuid = cfdi.cfdi_uuid if cfdi else "-"
            cfdi_total = f"${cfdi.total:,.2f}" if cfdi and cfdi.total else "-"
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
                <td>{format_value(expense.concepto)}</td>
                <td>${expense.gasto_cantidad:,.2f}</td>
                <td><code style="font-size: 11px; background: #d4edda; padding: 2px 4px; border-radius: 3px;">{cfdi_uuid}</code></td>
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
            unlinked_rows += f"""
            <tr>
                <td><code style="font-size: 11px;">{format_value(cfdi.cfdi_uuid)}</code></td>
                <td>{fecha_str}</td>
                <td>{origen_badge}</td>
                <td>{format_value(cfdi.emisor_nombre)[:40] if cfdi.emisor_nombre else '-'}...</td>
                <td>{format_value(cfdi.receptor_nombre)[:40] if cfdi.receptor_nombre else '-'}...</td>
                <td>${cfdi.total:,.2f}</td>
                <td>{format_value(cfdi.serie)}</td>
                <td>{format_value(cfdi.folio)}</td>
                <td>
                    <form method="POST" action="/admin/gastos/sat/cfdi/{cfdi.id}/status" style="display:inline;">
                        <button type="submit" class="action-btn view">Validar SAT</button>
                    </form>
                </td>
            </tr>
            """

        hero_actions_html = """
            <a href="/admin/gastos/cfdis/carga-masiva" class="button">Carga CFDIs</a>
            <a href="/admin/gastos/sat" class="button secondary">Operación SAT</a>
            <a href="/admin/gastos/expenses" class="button secondary">Ver gastos</a>
            <a href="/admin/gastos/invoices" class="button secondary">Ver facturas</a>
        """
        hero_side_html = f"""
            <div class="eyebrow">Cobertura</div>
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
                {_admin_workspace_styles("1640px")}
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
                        <a href="/admin/gastos/cfdis/matching" class="{'active' if not active_view else ''}">Todos</a>
                        <a href="/admin/gastos/cfdis/matching?view=pendiente" class="{'active' if active_view == 'pendiente' else ''}">Pendientes ({pending_count})</a>
                        <a href="/admin/gastos/cfdis/matching?view=vinculado" class="{'active' if active_view == 'vinculado' else ''}">Vinculados ({linked_count})</a>
                        <a href="/admin/gastos/cfdis/matching?view=sin_gasto" class="{'active' if active_view == 'sin_gasto' else ''}">CFDI sin gasto ({unlinked_cfdi_count})</a>
                    </div>
                </section>
                {f'''
                <section class="surface">
                    <div class="section-head">
                        <div>
                            <div class="eyebrow">Pendientes</div>
                            <h2>Gastos con CFDI pendiente ({pending_count})</h2>
                            <div class="section-note">Gastos activos con UUID manual capturado, pero todavía sin CFDI enlazado al gasto.</div>
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
                                    <th>Concepto</th>
                                    <th>Total Gasto</th>
                                    <th>UUID CFDI</th>
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
                                    <th>Total</th>
                                    <th>Serie</th>
                                    <th>Folio</th>
                                    <th>Acciones</th>
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
                            <div class="section-note">Esta bandeja existe para resolver la unión entre evidencia fiscal y gasto antes del cierre financiero.</div>
                        </div>
                    </div>
                    <ol class="flow-list">
                        <li><strong>Empleado registra gasto</strong> y proporciona UUID de CFDI (directo o desde QR/link)</li>
                        <li><strong>Finanzas carga CFDIs</strong> desde CSV con columna UUID</li>
                        <li><strong>Sistema vincula automáticamente</strong> gastos con CFDIs por UUID</li>
                        <li><strong>Operador verifica</strong> usando esta página para resolver discrepancias</li>
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

    # Get all active expenses with incomplete accounting setup
    bi_year_safe = (bi_year or "").strip()
    bi_year_safe = (
        bi_year_safe if (bi_year_safe.isdigit() and len(bi_year_safe) == 4) else ""
    )
    bi_scope_safe = (bi_scope or "").strip().lower()
    if bi_scope_safe not in {"all", ACTIVE_TOURNAMENT_SCOPE}:
        bi_scope_safe = ""
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
                    "budget_concept_id": (
                        gasto.budget_concept_id
                        or (effective_concept.id if effective_concept else None)
                    ),
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

    # Count suggestions by confidence level
    high_confidence_count = sum(
        1 for s in suggestions.values() if s and s.confidence_score >= 0.8
    )
    missing_main_count = sum(1 for gasto in gastos if gasto.cuenta_contable_id is None)
    missing_contra_count = sum(
        1 for gasto in gastos if gasto.contra_cuenta_contable_id is None
    )
    missing_cfdi_count = sum(1 for gasto in gastos if gasto.cfdi_report_id is None)

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
        documento_ref = (
            gasto.documento.numero_referencia if gasto.documento else "Sin asignar"
        )

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

        # Get suggestion for this expense
        suggestion = suggestions.get(gasto.id)
        cleanup_state = await build_cleanup_preview(session, gasto)
        preview = cleanup_state["preview"]
        readiness_issues = list(cleanup_state["issues"] or [])
        suggestion_html = ""
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

        rows_html += f"""
        <tr id="row-{gasto.id}">
            <td>{referencia_safe}</td>
            <td>{empleado_safe}</td>
            <td>{fecha_str}</td>
            <td style="max-width: 200px;">{concepto_safe}</td>
            <td style="max-width: 150px;">{proyecto_safe}</td>
            <td style="max-width: 180px;">{partida_presupuestal_safe}</td>
            <td style="max-width: 220px;">{cuenta_asignada_safe}</td>
            <td style="max-width: 120px;">{cuenta_base_safe}</td>
            <td>${gasto.gasto_cantidad:,.2f}</td>
            <td>{metodo_pago_safe}</td>
            <td>{documento_ref_safe}</td>
            <td>{cfdi_match_html}</td>
            <td style="min-width: 420px;">
                {tax_summary_html}
                <div style="font-size:12px; font-weight:700; color:#0f172a; margin:8px 0;">Cuentas contables</div>
                {suggestion_html}
                <div style="display:grid; gap:10px;">
                    <div class="cuenta-selector" {preselect_data}>
                        <div style="font-size:12px; font-weight:700; color:#0f172a; margin-bottom:4px;">Cargo gasto</div>
                        {main_assigned_html}
                        <input type="text" 
                               class="account-search" 
                               data-gasto-id="{gasto.id}"
                               data-target="main"
                               data-existing-id="{escape(str(gasto.cuenta_contable_id or ''))}"
                               placeholder="Buscar o escribir cuenta del gasto..." 
                               autocomplete="off"
                               style="width: 100%; padding: 8px; border: 1px solid #ddd; border-radius: 4px;">
                        <div class="account-results" style="display: none; position: absolute; background: white; border: 1px solid #ddd; max-height: 200px; overflow-y: auto; z-index: 1000; width: 100%; box-shadow: 0 2px 8px rgba(0,0,0,0.15);"></div>
                        <input type="hidden" class="main-cuenta-id" data-gasto-id="{gasto.id}" value="{escape(str(gasto.cuenta_contable_id or ''))}">
                    </div>
                    <div class="cuenta-selector" {contra_preselect_data}>
                        <div style="font-size:12px; font-weight:700; color:#0f172a; margin-bottom:4px;">Contrapartida</div>
                        {contra_assigned_html}
                        <input type="text" 
                               class="account-search" 
                               data-gasto-id="{gasto.id}"
                               data-target="contra"
                               data-existing-id="{escape(str(gasto.contra_cuenta_contable_id or ''))}"
                               placeholder="Buscar cuenta origen del dinero..." 
                               autocomplete="off"
                               style="width: 100%; padding: 8px; border: 1px solid #ddd; border-radius: 4px;">
                        <div class="account-results" style="display: none; position: absolute; background: white; border: 1px solid #ddd; max-height: 200px; overflow-y: auto; z-index: 1000; width: 100%; box-shadow: 0 2px 8px rgba(0,0,0,0.15);"></div>
                        <input type="hidden" class="contra-cuenta-id" data-gasto-id="{gasto.id}" value="{escape(str(gasto.contra_cuenta_contable_id or ''))}">
                        <div style="font-size:11px; color:#64748b; margin-top:4px;">
                            {contra_prefill_note}
                        </div>
                    </div>
                    <div class="cuenta-selector" {iva_preselect_data}>
                        <div style="font-size:12px; font-weight:700; color:#0f172a; margin-bottom:4px;">Cuenta IVA</div>
                        {iva_assigned_html}
                        <input type="text"
                               class="account-search"
                               data-gasto-id="{gasto.id}"
                               data-target="iva"
                               data-existing-id="{escape(str(getattr(gasto, 'cuenta_iva_id', '') or ''))}"
                               placeholder="Buscar cuenta de IVA acreditable..."
                               autocomplete="off"
                               style="width: 100%; padding: 8px; border: 1px solid #ddd; border-radius: 4px;">
                        <div class="account-results" style="display: none; position: absolute; background: white; border: 1px solid #ddd; max-height: 200px; overflow-y: auto; z-index: 1000; width: 100%; box-shadow: 0 2px 8px rgba(0,0,0,0.15);"></div>
                        <input type="hidden" class="iva-cuenta-id" data-gasto-id="{gasto.id}" value="{escape(str(getattr(gasto, 'cuenta_iva_id', '') or ''))}">
                        <div style="font-size:11px; color:#64748b; margin-top:4px;">
                            {escape("manual" if current_iva_account else "heuristic") if (current_iva_account or iva_account) else "Se resolverá automáticamente si no la eliges."}
                        </div>
                    </div>
                </div>
            </td>
            <td>
                <button class="btn-asignar" 
                        data-gasto-id="{gasto.id}"
                        style="padding: 8px 16px; background: #4CAF50; color: white; border: none; border-radius: 4px; cursor: pointer; font-weight: bold;">
                    Guardar limpieza contable
                </button>
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

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Centro de Limpieza Contable - Admin</title>
        <style>
            {_admin_workspace_styles("1760px")}
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
        </style>
    </head>
    <body>
        <div class="container">
            {render_admin_navigation(current_empleado, "limpieza", subtitle="Limpia gastos para COI desde la misma consola financiera: CFDI, cuentas contables y desglose fiscal.")}
            {_render_admin_workspace_hero(
                eyebrow="Contabilidad",
                title="Centro de Limpieza Contable",
                description="Bandeja de clasificación para completar CFDI, cuenta de cargo, contrapartida y campos fiscales existentes antes de exportar a COI.",
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
                    <div class="review-toolbar">
                        <div>
                            <div class="eyebrow">Acciones</div>
                            <h2 style="margin:0;">Resolver limpieza contable</h2>
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
                                    <th>Empleado</th>
                                    <th>Fecha</th>
                                    <th>Concepto</th>
                                    <th>Proyecto</th>
                                    <th>Partida presupuestal</th>
                                    <th>Cuenta contable</th>
                                    <th>Cuenta base</th>
                                    <th>Monto</th>
                                    <th>Método Pago</th>
                                    <th>Documento</th>
                                    <th>Vinculación CFDI</th>
                                    <th>Configuración contable</th>
                                    <th>Acción</th>
                                </tr>
                            </thead>
                            <tbody>
                                {rows_html if rows_html else '<tr><td colspan="14" style="text-align: center; padding: 40px;">No hay gastos pendientes de limpieza contable</td></tr>'}
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
            
            // Pre-select high confidence suggestions on page load
            document.addEventListener('DOMContentLoaded', function() {{
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
            
            // Setup search functionality for all rows
            document.querySelectorAll('.account-search').forEach(searchInput => {{
                const gastoId = searchInput.getAttribute('data-gasto-id');
                const target = searchInput.getAttribute('data-target');
                const resultsDiv = searchInput.nextElementSibling;
                const hiddenInput = searchInput.parentElement.querySelector('input[type="hidden"]');
                
                searchInput.addEventListener('input', function() {{
                    const query = this.value.toLowerCase().trim();
                    
                    // Reset styling when user types
                    this.style.borderColor = '#ddd';
                    this.style.background = 'white';
                    
                    if (query.length < 2) {{
                        resultsDiv.style.display = 'none';
                        return;
                    }}
                    
                    const filtered = cuentasContables.filter(c => 
                        c.codigo.toLowerCase().includes(query) || 
                        c.nombre.toLowerCase().includes(query) ||
                        c.tipo.toLowerCase().includes(query)
                    );
                    
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
                            window.location.reload();
                        }} else {{
                            const error = await response.text();
                            alert('Error al guardar configuración contable: ' + error);
                            this.disabled = false;
                            this.textContent = 'Guardar';
                        }}
                    }} catch (e) {{
                        alert('Error de red: ' + e.message);
                        this.disabled = false;
                        this.textContent = 'Guardar';
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
