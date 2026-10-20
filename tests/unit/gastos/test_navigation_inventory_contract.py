"""Route-registration checks for the RQF-UX-002 navigation inventory."""

from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys

from devnous.gastos.routes import (
    admin_routes,
    client_executive_routes,
    user_routes,
)


INVENTORY_PATH = Path("tests/browser/navigation_inventory.py")
SPEC = spec_from_file_location("navigation_inventory", INVENTORY_PATH)
assert SPEC and SPEC.loader
INVENTORY_MODULE = module_from_spec(SPEC)
sys.modules[SPEC.name] = INVENTORY_MODULE
SPEC.loader.exec_module(INVENTORY_MODULE)

ROUTERS = {
    "admin": admin_routes.router,
    "direction": client_executive_routes.router,
    "user": user_routes.router,
}


def _registered_paths(owner: str) -> set[str]:
    return {
        route.path
        for route in ROUTERS[owner].routes
        if getattr(route, "path", None)
    }


def test_canonical_navigation_targets_are_registered_by_their_owner() -> None:
    for entry in INVENTORY_MODULE.VISIBLE_NAVIGATION_ENTRIES:
        assert entry.classification == "canonical"
        assert entry.route_owner is not None
        assert entry.href in _registered_paths(entry.route_owner)


def test_legacy_blocked_navigation_targets_are_explicitly_classified() -> None:
    blocked_entries = INVENTORY_MODULE.BLOCKED_NAVIGATION_ENTRIES

    assert blocked_entries
    assert all(
        entry.classification == "legacy_blocked" for entry in blocked_entries
    )
    assert all(
        entry.href == "/admin/presupuestos-legacy" for entry in blocked_entries
    )
    assert "/admin/presupuestos-legacy" in _registered_paths("admin")
