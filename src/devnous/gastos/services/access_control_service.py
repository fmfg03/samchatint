"""Role/area access policy for SamChat web modules."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Optional
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


SUPERADMIN_ROLES = frozenset({"superadmin", "super_admin"})
FINANCE_ADMIN_ROLES = frozenset({"finanzas", "admin", "superadmin", "super_admin"})
ADMIN_ROLES = frozenset({"admin", "superadmin", "super_admin"})
ALL_ROLES = ("empleado", "coordinador", "finanzas", "admin", "superadmin")
ACTION_KEYS = (
    "ver",
    "crear",
    "editar",
    "aprobar",
    "pagar",
    "cobrar",
    "exportar",
    "administrar",
)
NON_CONFIGURABLE_GATEWAY_TOOL_KEYS = frozenset({"panel.home", "admin.root"})


@dataclass(frozen=True)
class AccessTool:
    key: str
    label: str
    group: str
    description: str
    paths: tuple[str, ...]
    default_roles: frozenset[str]
    actions: tuple[str, ...] = ("ver",)


ACCESS_TOOLS: tuple[AccessTool, ...] = (
    AccessTool(
        "panel.home",
        "Panel principal",
        "Panel",
        "Entrada principal de operación y administración.",
        ("/panel",),
        frozenset(ALL_ROLES),
    ),
    AccessTool(
        "panel.operaciones",
        "Operaciones",
        "Panel",
        "Consola operativa desde el panel.",
        ("/panel/operaciones-console",),
        FINANCE_ADMIN_ROLES,
    ),
    AccessTool(
        "panel.telegram",
        "Telegram",
        "Panel",
        "Consola y estado de Telegram.",
        ("/panel/telegram-console", "/panel/telegram-status", "/panel/mi-telegram"),
        frozenset(ALL_ROLES),
    ),
    AccessTool(
        "panel.entrenamiento",
        "Entrenamiento",
        "Panel",
        "Manual y videos tutoriales.",
        ("/static/manual_usuario_gastos.pdf",),
        frozenset(ALL_ROLES),
    ),
    AccessTool(
        "gastos.informes",
        "Informes de gastos",
        "Gastos",
        "Informes, saldos y reembolsos de gastos.",
        ("/informes-de-gastos",),
        frozenset(ALL_ROLES),
        ("ver", "crear", "editar", "exportar"),
    ),
    AccessTool(
        "gastos.solicitudes",
        "Solicitudes de transferencia",
        "Gastos",
        "Solicitudes a proveedores, terceros y personales.",
        ("/gastos-terceros", "/documentos"),
        frozenset(ALL_ROLES),
        ("ver", "crear", "editar", "aprobar", "pagar"),
    ),
    AccessTool(
        "gastos.mis_gastos",
        "Mis gastos",
        "Gastos",
        "Gastos propios y comprobantes.",
        ("/gastos/mis-gastos", "/gastos/nuevo"),
        frozenset(ALL_ROLES),
        ("ver", "crear", "editar"),
    ),
    AccessTool(
        "soporte.self",
        "Soporte",
        "Soporte",
        "Tickets de soporte del usuario.",
        ("/soporte",),
        frozenset(ALL_ROLES),
        ("ver", "crear"),
    ),
    AccessTool(
        "admin.root",
        "Admin",
        "Admin",
        "Shell administrativo React.",
        ("/admin",),
        ADMIN_ROLES,
    ),
    AccessTool(
        "assistant.shell",
        "Assistant",
        "Assistant",
        "Assistant/BI web shell.",
        ("/assistant", "/api/assistant"),
        ADMIN_ROLES,
    ),
    AccessTool(
        "admin.gastos.dashboard",
        "Admin gastos",
        "Admin gastos",
        "Dashboard principal de administración de gastos.",
        ("/admin/gastos",),
        FINANCE_ADMIN_ROLES,
    ),
    AccessTool(
        "admin.gastos.expenses",
        "Gastos globales",
        "Admin gastos",
        "Tabla global de gastos.",
        ("/admin/gastos/expenses",),
        FINANCE_ADMIN_ROLES,
        ("ver", "exportar"),
    ),
    AccessTool(
        "admin.gastos.invoices",
        "Facturas",
        "Admin gastos",
        "Facturas y CFDIs administrativos.",
        ("/admin/gastos/invoices",),
        FINANCE_ADMIN_ROLES,
        ("ver", "exportar"),
    ),
    AccessTool(
        "admin.gastos.finance_training",
        "Dataset capacitación",
        "Admin gastos",
        "Generación de datos de entrenamiento financiero.",
        ("/admin/gastos/finance-training",),
        FINANCE_ADMIN_ROLES,
        ("ver", "administrar"),
    ),
    AccessTool(
        "admin.gastos.sat",
        "e.firma SAT",
        "Admin gastos",
        "Configuración y jobs SAT.",
        ("/admin/gastos/sat",),
        FINANCE_ADMIN_ROLES,
        ("ver", "editar", "administrar"),
    ),
    AccessTool(
        "admin.gastos.cfdi_carga",
        "Carga masiva CFDI",
        "Admin gastos",
        "Importación masiva de CFDIs.",
        ("/admin/gastos/cfdis/carga-masiva",),
        FINANCE_ADMIN_ROLES,
        ("ver", "crear"),
    ),
    AccessTool(
        "admin.gastos.cfdi_matching",
        "Emparejar CFDIs",
        "Admin gastos",
        "Matching CFDI contra gastos.",
        ("/admin/gastos/cfdis/matching",),
        FINANCE_ADMIN_ROLES,
        ("ver", "editar"),
    ),
    AccessTool(
        "admin.gastos.limpieza",
        "Centro de Limpieza Contable",
        "Admin gastos",
        "Limpieza contable y fiscal antes de COI.",
        ("/admin/gastos/sin-cuenta-contable",),
        FINANCE_ADMIN_ROLES,
        ("ver", "editar", "exportar"),
    ),
    AccessTool(
        "admin.gastos.amex",
        "AMEX",
        "Admin gastos",
        "Carga y conciliación AMEX.",
        ("/gastos/carga-masiva-amex", "/admin/gastos/amex/conciliacion"),
        FINANCE_ADMIN_ROLES,
        ("ver", "crear", "editar"),
    ),
    AccessTool(
        "presupuestos.ingresos",
        "Ingresos",
        "Presupuestos",
        "Vinculación de CFDI PSP a proyecto y partida presupuestal.",
        ("/admin/presupuestos",),
        FINANCE_ADMIN_ROLES,
        ("ver", "crear", "editar", "exportar"),
    ),
    AccessTool(
        "admin.finanzas",
        "Administración financiera",
        "Admin",
        "Cierre financiero y tablero mensual.",
        ("/admin/finanzas",),
        FINANCE_ADMIN_ROLES,
    ),
    AccessTool(
        "admin.empleados",
        "Empleados",
        "Admin",
        "Administración de empleados.",
        ("/admin/empleados",),
        FINANCE_ADMIN_ROLES,
        ("ver", "crear", "editar", "administrar"),
    ),
    AccessTool(
        "admin.perfiles",
        "Perfiles ad-hoc",
        "Admin",
        "Perfiles personalizados de permisos.",
        ("/admin/perfiles",),
        FINANCE_ADMIN_ROLES,
        ("ver", "crear", "editar", "administrar"),
    ),
    AccessTool(
        "admin.proveedores",
        "Proveedores y clientes",
        "Admin",
        "Catálogo de proveedores y clientes.",
        ("/admin/proveedores-clientes",),
        frozenset(ALL_ROLES),
        ("ver", "crear", "editar"),
    ),
    AccessTool(
        "admin.torneos",
        "Torneos y proyectos",
        "Admin",
        "Catálogo de torneos/proyectos.",
        ("/admin/torneos",),
        FINANCE_ADMIN_ROLES,
        ("ver", "crear", "editar"),
    ),
    AccessTool(
        "admin.cuentas_contables",
        "Cuentas contables",
        "Admin",
        "Plan de cuentas contables.",
        ("/admin/cuentas-contables",),
        FINANCE_ADMIN_ROLES,
        ("ver", "crear", "editar"),
    ),
    AccessTool(
        "admin.centros_costo",
        "Centros de costo",
        "Admin",
        "Catálogo de centros de costo.",
        ("/admin/centros-costo",),
        FINANCE_ADMIN_ROLES,
        ("ver", "crear", "editar"),
    ),
    AccessTool(
        "admin.rfc",
        "RFC",
        "Admin",
        "Configuraciones RFC.",
        ("/admin/rfc",),
        FINANCE_ADMIN_ROLES,
        ("ver", "crear", "editar"),
    ),
    AccessTool(
        "admin.contabilidad",
        "Contabilidad",
        "Admin",
        "COI, DIOT, balanza, mayor, estado y cierre.",
        ("/admin/contabilidad",),
        FINANCE_ADMIN_ROLES,
        ("ver", "crear", "editar", "exportar", "administrar"),
    ),
    AccessTool(
        "admin.nomina",
        "Nómina",
        "Admin",
        "Prenómina, empleados, incidencias y pólizas de nómina.",
        ("/admin/nomina",),
        FINANCE_ADMIN_ROLES,
        ("ver", "crear", "editar", "exportar", "administrar"),
    ),
    AccessTool(
        "admin.customer_success",
        "Customer Success",
        "Admin",
        "Uso y auditoría de customer success.",
        ("/admin/customer-success",),
        ADMIN_ROLES,
    ),
    AccessTool(
        "admin.soporte",
        "Soporte admin",
        "Admin",
        "Triage y respuesta de tickets.",
        ("/admin/soporte",),
        SUPERADMIN_ROLES,
        ("ver", "editar"),
    ),
    AccessTool(
        "configuracion.control_accesos",
        "Control de accesos",
        "Configuración",
        "Matriz superadmin de visibilidad y autorización.",
        ("/admin/control-accesos",),
        SUPERADMIN_ROLES,
        ("ver", "editar", "administrar"),
    ),
    AccessTool(
        "configuracion.estrategias_autorizacion",
        "Estrategias de autorizacion",
        "Configuracion",
        "Matriz de autorizacion por area, erogacion, monto y condicion.",
        ("/admin/estrategias-autorizacion",),
        ADMIN_ROLES,
        ("ver", "editar", "administrar"),
    ),
    AccessTool(
        "configuracion.authorization_warnings",
        "Warnings de autorizacion",
        "Configuracion",
        "Discrepancias auditadas entre matriz de autorizacion y aprobaciones reales.",
        ("/admin/estrategias-autorizacion/warnings",),
        FINANCE_ADMIN_ROLES,
        ("ver",),
    ),
)

TOOLS_BY_KEY = {tool.key: tool for tool in ACCESS_TOOLS}


def normalize_role(value: Any) -> str:
    return (str(value or "")).strip().lower()


def normalize_area(value: Any) -> str:
    return (str(value or "")).strip().lower()


def is_superadmin_role(value: Any) -> bool:
    return normalize_role(value) in SUPERADMIN_ROLES


def empleado_role(empleado: Any) -> str:
    return normalize_role(getattr(empleado, "rol", ""))


def empleado_area(empleado: Any) -> str:
    return normalize_area(getattr(empleado, "departamento", ""))


def action_for_method(method: str) -> str:
    return "ver" if (method or "GET").upper() == "GET" else "editar"


def path_to_tool(path: str) -> Optional[AccessTool]:
    clean_path = (path or "").strip() or "/"
    if clean_path != "/" and clean_path.endswith("/"):
        clean_path = clean_path.rstrip("/")
    matches: list[tuple[int, AccessTool]] = []
    for tool in ACCESS_TOOLS:
        for prefix in tool.paths:
            clean_prefix = prefix.rstrip("/") or "/"
            if clean_path == clean_prefix or clean_path.startswith(f"{clean_prefix}/"):
                matches.append((len(clean_prefix), tool))
    if not matches:
        return None
    return sorted(matches, key=lambda item: item[0], reverse=True)[0][1]


def default_allows(tool_key: str, role: str) -> bool:
    if tool_key in NON_CONFIGURABLE_GATEWAY_TOOL_KEYS:
        return True
    tool = TOOLS_BY_KEY.get(tool_key)
    return bool(tool and normalize_role(role) in tool.default_roles)


async def _load_rule(
    session: AsyncSession,
    *,
    tool_key: str,
    action_key: str,
    role_key: str,
    area_key: str,
) -> Optional[bool]:
    try:
        result = await session.execute(
            text(
                """
                SELECT allowed
                FROM access_control_rules
                WHERE active = TRUE
                  AND tool_key = :tool_key
                  AND action_key = :action_key
                  AND role_key = :role_key
                  AND COALESCE(area_key, '') = :area_key
                ORDER BY updated_at DESC
                LIMIT 1
                """
            ),
            {
                "tool_key": tool_key,
                "action_key": action_key,
                "role_key": role_key,
                "area_key": area_key,
            },
        )
        row = result.fetchone()
    except Exception:
        return None
    if row is None:
        return None
    return bool(row[0])


async def can_access_tool(
    session: AsyncSession,
    empleado: Any,
    tool_key: str,
    action_key: str = "ver",
) -> bool:
    role = empleado_role(empleado)
    if is_superadmin_role(role):
        return True
    if tool_key in NON_CONFIGURABLE_GATEWAY_TOOL_KEYS:
        return True
    action = (action_key or "ver").strip().lower()
    area = empleado_area(empleado)
    specific = await _load_rule(
        session,
        tool_key=tool_key,
        action_key=action,
        role_key=role,
        area_key=area,
    )
    if specific is not None:
        return specific
    if action != "ver":
        read_specific = await _load_rule(
            session,
            tool_key=tool_key,
            action_key="ver",
            role_key=role,
            area_key=area,
        )
        if read_specific is not None:
            return read_specific
    return default_allows(tool_key, role)


async def visible_tools_for(session: AsyncSession, empleado: Any) -> set[str]:
    visible: set[str] = set()
    for tool in ACCESS_TOOLS:
        if await can_access_tool(session, empleado, tool.key, "ver"):
            visible.add(tool.key)
    return visible


async def can_access_path(
    session: AsyncSession,
    empleado: Any,
    path: str,
    method: str = "GET",
) -> bool:
    tool = path_to_tool(path)
    if tool is None:
        return True
    return await can_access_tool(session, empleado, tool.key, action_for_method(method))


async def list_rules(session: AsyncSession) -> dict[tuple[str, str, str, str], bool]:
    try:
        result = await session.execute(
            text(
                """
                SELECT tool_key, action_key, role_key, COALESCE(area_key, ''), allowed
                FROM access_control_rules
                WHERE active = TRUE
                """
            )
        )
    except Exception:
        return {}
    return {
        (str(row[0]), str(row[1]), str(row[2]), str(row[3] or "")): bool(row[4])
        for row in result.fetchall()
    }


async def list_known_areas(session: AsyncSession) -> list[str]:
    areas: set[str] = set()
    try:
        result = await session.execute(
            text(
                """
                SELECT DISTINCT departamento
                FROM empleados
                WHERE departamento IS NOT NULL AND btrim(departamento) <> ''
                ORDER BY departamento
                """
            )
        )
        areas.update(str(row[0]).strip() for row in result.fetchall() if row[0])
    except Exception:
        pass
    return sorted(areas)


async def upsert_rule(
    session: AsyncSession,
    *,
    actor: Any,
    tool_key: str,
    action_key: str,
    role_key: str,
    area_key: str,
    allowed: bool,
    reason: str = "",
) -> None:
    tool = TOOLS_BY_KEY.get(tool_key)
    if tool is None:
        raise ValueError("tool_key inválido")
    action = (action_key or "ver").strip().lower()
    if action not in ACTION_KEYS:
        raise ValueError("action_key inválido")
    role = normalize_role(role_key)
    if role not in ALL_ROLES and role not in SUPERADMIN_ROLES:
        raise ValueError("role_key inválido")
    area = normalize_area(area_key)
    if not area:
        raise ValueError("area_key requerido")
    if (
        tool_key == "configuracion.control_accesos"
        and role in SUPERADMIN_ROLES
        and not allowed
    ):
        raise ValueError("No se puede retirar Control de accesos a superadmin.")

    before = await _load_rule(
        session,
        tool_key=tool_key,
        action_key=action,
        role_key=role,
        area_key=area,
    )
    now = datetime.utcnow()
    rule_id = uuid4()
    await session.execute(
        text(
            """
            INSERT INTO access_control_rules (
                id, tool_key, path_pattern, action_key, role_key, area_key,
                allowed, active, created_by_empleado_id, updated_by_empleado_id,
                created_at, updated_at
            )
            VALUES (
                :id, :tool_key, :path_pattern, :action_key, :role_key, :area_key,
                :allowed, TRUE, :actor_id, :actor_id, :now, :now
            )
            ON CONFLICT (tool_key, action_key, role_key, area_key)
            DO UPDATE SET
                path_pattern = EXCLUDED.path_pattern,
                allowed = EXCLUDED.allowed,
                active = TRUE,
                updated_by_empleado_id = EXCLUDED.updated_by_empleado_id,
                updated_at = EXCLUDED.updated_at
            """
        ),
        {
            "id": rule_id,
            "tool_key": tool_key,
            "path_pattern": ",".join(tool.paths),
            "action_key": action,
            "role_key": role,
            "area_key": area,
            "allowed": bool(allowed),
            "actor_id": getattr(actor, "id", None),
            "now": now,
        },
    )
    await session.execute(
        text(
            """
            INSERT INTO access_control_audit_logs (
                id, actor_empleado_id, tool_key, action_key, role_key, area_key,
                before_allowed, after_allowed, reason, created_at
            )
            VALUES (
                :id, :actor_id, :tool_key, :action_key, :role_key, :area_key,
                :before_allowed, :after_allowed, :reason, :now
            )
            """
        ),
        {
            "id": uuid4(),
            "actor_id": getattr(actor, "id", None),
            "tool_key": tool_key,
            "action_key": action,
            "role_key": role,
            "area_key": area,
            "before_allowed": before,
            "after_allowed": bool(allowed),
            "reason": (reason or "").strip()[:500],
            "now": now,
        },
    )


def filter_cards_by_tools(
    cards: Iterable[tuple[str, str, str, str]],
    visible_tool_keys: set[str],
) -> list[tuple[str, str, str]]:
    return [
        (href, title, description)
        for tool_key, href, title, description in cards
        if tool_key in visible_tool_keys
    ]
