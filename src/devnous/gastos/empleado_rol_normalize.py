"""Normalize empleado `rol` values from admin HTML forms."""

from typing import Any, FrozenSet

_EMPLEADO_ROL_ALLOWED: FrozenSet[str] = frozenset(
    {"empleado", "coordinador", "finanzas", "admin", "superadmin", "super_admin"}
)


def normalize_empleado_rol_from_form(raw: Any) -> str:
    """
    Normalize rol from a submitted form value.

    Raises ValueError if the value is missing or not an allowed role string.
    """
    if raw is None:
        raise ValueError("missing")
    s = str(raw).strip().lower()
    if not s:
        raise ValueError("missing")
    if s not in _EMPLEADO_ROL_ALLOWED:
        raise ValueError("invalid")
    return "superadmin" if s == "super_admin" else s
