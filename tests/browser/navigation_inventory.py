"""Test-only navigation contract for RQF-UX-002 effective profiles."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


NavigationClassification = Literal["canonical", "legacy_blocked", "local"]
RouteOwner = Literal["user", "admin", "direction"]


@dataclass(frozen=True)
class NavigationInventoryEntry:
    """Expected visible or blocked navigation target in the browser harness.

    The inventory describes browser-harness evidence; it never grants access.
    """

    profile: str
    test_path: str
    section_label: str | None
    label: str | None
    href: str
    classification: NavigationClassification
    route_owner: RouteOwner | None
    visible: bool = True


NAVIGATION_INVENTORY = (
    NavigationInventoryEntry(
        profile="employee",
        test_path="/_test/profile/employee",
        section_label="Navegación global",
        label="Panel de administración",
        href="/panel",
        classification="canonical",
        route_owner="user",
    ),
    NavigationInventoryEntry(
        profile="employee",
        test_path="/_test/profile/employee",
        section_label="Navegación de Gastos",
        label="Informes de gastos",
        href="/informes-de-gastos",
        classification="canonical",
        route_owner="user",
    ),
    NavigationInventoryEntry(
        profile="employee",
        test_path="/_test/profile/employee",
        section_label="Navegación de Gastos",
        label="Solicitudes de transferencia",
        href="/gastos-terceros",
        classification="canonical",
        route_owner="user",
    ),
    NavigationInventoryEntry(
        profile="employee",
        test_path="/_test/profile/employee",
        section_label="Navegación de Gastos",
        label="Todos los documentos",
        href="/documentos/todos",
        classification="canonical",
        route_owner="user",
    ),
    NavigationInventoryEntry(
        profile="approver",
        test_path="/_test/panel/approver",
        section_label=None,
        label="Aprobaciones pendientes",
        href="/documentos/pendientes",
        classification="canonical",
        route_owner="user",
    ),
    NavigationInventoryEntry(
        profile="budget_control",
        test_path="/_test/panel/budget_control",
        section_label=None,
        label="Control Presupuestal",
        href="/documentos/control-presupuestal",
        classification="canonical",
        route_owner="user",
    ),
    NavigationInventoryEntry(
        profile="finance",
        test_path="/_test/profile/finance",
        section_label="Navegación administrativa",
        label="Finanzas",
        href="/admin/finanzas",
        classification="canonical",
        route_owner="admin",
    ),
    NavigationInventoryEntry(
        profile="finance",
        test_path="/_test/profile/finance",
        section_label="Navegación administrativa",
        label="Cuentas por Cobrar",
        href="/admin/finanzas/cuentas-por-cobrar",
        classification="canonical",
        route_owner="admin",
    ),
    NavigationInventoryEntry(
        profile="direction",
        test_path="/_test/profile/direction",
        section_label="Navegación global",
        label="Presupuestos",
        href="/admin/presupuestos",
        classification="canonical",
        route_owner="admin",
    ),
    NavigationInventoryEntry(
        profile="accounting",
        test_path="/_test/profile/accounting",
        section_label="Navegación administrativa",
        label="Limpieza contable",
        href="/admin/gastos/sin-cuenta-contable",
        classification="canonical",
        route_owner="admin",
    ),
    NavigationInventoryEntry(
        profile="accounting",
        test_path="/_test/profile/accounting",
        section_label="Navegación contable",
        label="COI",
        href="/admin/contabilidad/coi",
        classification="canonical",
        route_owner="user",
    ),
    NavigationInventoryEntry(
        profile="accounting",
        test_path="/_test/profile/accounting",
        section_label="Navegación contable",
        label="CxC",
        href="/admin/contabilidad/cuentas-por-cobrar",
        classification="canonical",
        route_owner="user",
    ),
    NavigationInventoryEntry(
        profile="accounting",
        test_path="/_test/profile/accounting",
        section_label="Navegación contable",
        label="Conciliación",
        href="/admin/contabilidad/conciliacion",
        classification="canonical",
        route_owner="user",
    ),
    NavigationInventoryEntry(
        profile="direction",
        test_path="/_test/panel/direction",
        section_label=None,
        label="Dirección",
        href="/direccion/tableros",
        classification="canonical",
        route_owner="direction",
    ),
    NavigationInventoryEntry(
        profile="all",
        test_path="/_test/profile/finance",
        section_label=None,
        label=None,
        href="/admin/presupuestos-legacy",
        classification="legacy_blocked",
        route_owner="admin",
        visible=False,
    ),
)


VISIBLE_NAVIGATION_ENTRIES = tuple(
    entry for entry in NAVIGATION_INVENTORY if entry.visible
)
BLOCKED_NAVIGATION_ENTRIES = tuple(
    entry for entry in NAVIGATION_INVENTORY if not entry.visible
)
