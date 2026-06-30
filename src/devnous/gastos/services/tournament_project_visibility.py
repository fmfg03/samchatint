"""Per-project visibility for expense/informe/solicitud form dropdowns by departamento."""

from __future__ import annotations

from typing import Any, Iterable, Optional, Sequence, Set
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Empleado, Tournament

# Canonical departamentos (must match /admin/empleados select options).
EMPLEADO_DEPARTAMENTOS: tuple[str, ...] = (
    "Finanzas",
    "Mercadotecnia",
    "Operaciones",
    "Dirección",
)

_DEPARTAMENTO_BY_FOLDED: dict[str, str] = {
    dept.casefold(): dept for dept in EMPLEADO_DEPARTAMENTOS
}

# Legacy area tokens stored before department-based visibility.
_LEGACY_AREA_TO_DEPARTAMENTO: dict[str, str] = {
    "operations": "Operaciones",
    "operaciones": "Operaciones",
    "finance": "Finanzas",
    "finanzas": "Finanzas",
}

DEFAULT_OPERATIONS_ONLY_VISIBILITY: list[str] = ["Operaciones"]

SCOPED_LIST_VIEW_DEPARTAMENTOS: tuple[str, ...] = ("Operaciones", "Mercadotecnia")


def empleado_list_view_department_scope(empleado: Empleado) -> Optional[str]:
    """Return departamento for list-view scoping, or None when no filter applies.

    Admin/coordinador users in Operaciones or Mercadotecnia only see list rows
    whose solicitante belongs to the same departamento. Other roles/departments
    are unchanged (global lists or own records only).
    """
    rol = (getattr(empleado, "rol", None) or "").strip().casefold()
    if rol not in ("admin", "coordinador"):
        return None
    dept = canonical_departamento(getattr(empleado, "departamento", None))
    if dept in SCOPED_LIST_VIEW_DEPARTAMENTOS:
        return dept
    return None


def departamento_column_matches(column, canonical_departamento: str):
    """SQL expression: column value equals canonical departamento (case-insensitive)."""
    from sqlalchemy import func

    return func.lower(func.trim(column)) == canonical_departamento.casefold()


def canonical_departamento(value: Optional[str]) -> Optional[str]:
    """Return canonical departamento label or None if unknown/empty."""
    text = (value or "").strip()
    if not text:
        return None
    return _DEPARTAMENTO_BY_FOLDED.get(text.casefold())


def normalize_form_visibility_departments(raw: Any) -> list[str]:
    """Normalize stored JSON/list to canonical departamento names."""
    if raw is None:
        return []
    items: Iterable[Any]
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        items = [part.strip() for part in text.replace(",", " ").split()]
    elif isinstance(raw, (list, tuple, set)):
        items = raw
    else:
        return []

    normalized: list[str] = []
    seen: Set[str] = set()
    for item in items:
        token = str(item or "").strip()
        if not token:
            continue
        mapped = _LEGACY_AREA_TO_DEPARTAMENTO.get(token.casefold())
        if mapped:
            token = mapped
        canonical = canonical_departamento(token)
        if canonical and canonical not in seen:
            seen.add(canonical)
            normalized.append(canonical)
    return normalized


# Backward-compatible alias used by admin routes / model field name.
normalize_form_visibility_areas = normalize_form_visibility_departments


def parse_form_visibility_areas_from_form(form_values: Any) -> list[str]:
    """Parse repeated checkbox values from an HTML form."""
    if form_values is None:
        return []
    if isinstance(form_values, (list, tuple)):
        raw_items = form_values
    else:
        raw_items = [form_values]
    return normalize_form_visibility_departments(raw_items)


def empleado_form_visibility_department(empleado: Empleado) -> Optional[str]:
    """Departamento used when filtering project dropdowns (ignores rol)."""
    return canonical_departamento(getattr(empleado, "departamento", None))


def tournament_visible_in_forms(
    tournament: Tournament,
    empleado: Empleado,
    *,
    empleado_departamento: Optional[str] = None,
) -> bool:
    """
    Return True if the project should appear in form dropdowns for this empleado.

    Empty/null visibility config means all departamentos (backward compatible).
    """
    allowed = normalize_form_visibility_departments(
        getattr(tournament, "form_visibility_areas", None)
    )
    if not allowed:
        return True
    dept = canonical_departamento(empleado_departamento) or empleado_form_visibility_department(
        empleado
    )
    if not dept:
        return False
    return dept in allowed


def format_form_visibility_areas_label(tournament: Tournament) -> str:
    allowed = normalize_form_visibility_departments(
        getattr(tournament, "form_visibility_areas", None)
    )
    if not allowed or set(allowed) == set(EMPLEADO_DEPARTAMENTOS):
        return "Todos los departamentos"
    return ", ".join(allowed)


def visibility_validation_error(tournament: Tournament, empleado: Empleado) -> Optional[str]:
    if tournament_visible_in_forms(tournament, empleado):
        return None
    allowed = normalize_form_visibility_departments(
        getattr(tournament, "form_visibility_areas", None)
    )
    dept = empleado_form_visibility_department(empleado)
    if not dept:
        return (
            "Su usuario no tiene departamento asignado. "
            "Pida a administración que lo configure en Empleados."
        )
    if allowed:
        dept_list = ", ".join(allowed)
        return f"Este proyecto solo está disponible para el departamento: {dept_list}."
    return "No puede usar este proyecto en formularios con su departamento actual."


async def fetch_active_tournaments_for_telegram_user(
    session: AsyncSession,
    telegram_user_id: int,
    *,
    extra_tournament_ids: Optional[Sequence[Any]] = None,
) -> list[Tournament]:
    """Resolve tournaments for Telegram expense flow using linked empleado departamento."""
    empleado_result = await session.execute(
        select(Empleado).where(
            Empleado.telegram_user_id == telegram_user_id,
            Empleado.activo.is_(True),
        )
    )
    empleado = empleado_result.scalar_one_or_none()
    if empleado is not None:
        return await fetch_active_tournaments_for_empleado(
            session,
            empleado,
            extra_tournament_ids=extra_tournament_ids,
        )
    result = await session.execute(
        select(Tournament)
        .where(Tournament.active.is_(True))
        .order_by(Tournament.display_order, Tournament.name)
    )
    return list(result.scalars().all())


async def fetch_active_tournaments_for_empleado(
    session: AsyncSession,
    empleado: Empleado,
    *,
    extra_tournament_ids: Optional[Sequence[Any]] = None,
) -> list[Tournament]:
    """
    Active tournaments visible to empleado in form dropdowns.

    ``extra_tournament_ids`` includes inactive or restricted rows already stored
    on a record being edited (so the current value remains selectable).
    """
    extra_ids: list[UUID] = []
    for raw in extra_tournament_ids or ():
        if raw is None:
            continue
        try:
            extra_ids.append(raw if isinstance(raw, UUID) else UUID(str(raw)))
        except (ValueError, TypeError):
            continue

    condition = Tournament.active.is_(True)
    if extra_ids:
        condition = or_(condition, Tournament.id.in_(extra_ids))

    result = await session.execute(
        select(Tournament).where(condition).order_by(
            Tournament.display_order,
            Tournament.name,
        )
    )
    rows = list(result.scalars().all())
    return [
        t
        for t in rows
        if tournament_visible_in_forms(t, empleado)
        or (extra_ids and t.id in extra_ids)
    ]


def render_form_visibility_areas_checkboxes(
    selected: Optional[Any],
    *,
    input_name: str = "form_visibility_areas",
    html_id_prefix: str = "visibility",
) -> str:
    from html import escape

    selected_set = set(normalize_form_visibility_departments(selected))
    if not selected_set:
        selected_set = set(EMPLEADO_DEPARTAMENTOS)
    parts: list[str] = []
    for dept in EMPLEADO_DEPARTAMENTOS:
        slug = dept.casefold().replace("ó", "o").replace("í", "i").replace(" ", "_")
        checked = " checked" if dept in selected_set else ""
        cid = f"{html_id_prefix}_{slug}"
        parts.append(
            '<div class="visibility-dept-row" style="display:flex;align-items:center;gap:10px;'
            'margin-bottom:8px;">'
            f'<input type="checkbox" id="{escape(cid)}" name="{escape(input_name)}" '
            f'value="{escape(dept)}"{checked} style="width:18px;height:18px;accent-color:#667eea;">'
            f'<label for="{escape(cid)}" style="margin:0;font-weight:500;color:#334155;">'
            f"{escape(dept)}</label></div>"
        )
    parts.append(
        '<small style="color:#64748b;display:block;margin-top:6px;line-height:1.45;">'
        "Solo empleados con el departamento indicado en "
        "<strong>/admin/empleados</strong> verán este proyecto al capturar gastos, "
        "informes o solicitudes. Si marcas todos, todos los departamentos lo verán."
        "</small>"
    )
    return "".join(parts)
