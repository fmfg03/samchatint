"""
Dependencies for route authentication and authorization.
"""

import json
import logging
import os
from typing import Optional, Any, Set, Iterable
from uuid import UUID
from fastapi import Depends, HTTPException, Request, Header
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Empleado

logger = logging.getLogger(__name__)

# This will be set by the app that includes these routes
_db_session_maker = None


def _normalize_role(value: Any) -> str:
    """Normalize role strings for consistent comparisons."""
    return (str(value or "")).strip().lower()


def set_db_session_maker(session_maker):
    """Set the database session maker for dependencies."""
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


def _collect_permission_tokens(value: Any, prefix: str = "") -> Set[str]:
    """
    Flatten flexible JSON permissions into token strings.
    Examples produced:
    - "admin.perfiles.manage"
    - "gastos:read"
    - "reportes:write"
    - "assistant.tools.cfdi"
    """
    tokens: Set[str] = set()
    if value is None:
        return tokens

    if isinstance(value, str):
        val = value.strip().lower()
        if val:
            tokens.add(val)
            if prefix:
                tokens.add(f"{prefix}{val}")
        return tokens

    if isinstance(value, bool):
        if value and prefix:
            tokens.add(prefix[:-1].lower())
        return tokens

    if isinstance(value, (int, float)):
        return tokens

    if isinstance(value, list):
        for item in value:
            tokens.update(_collect_permission_tokens(item, prefix))
        return tokens

    if isinstance(value, dict):
        for key, item in value.items():
            key_norm = str(key).strip().lower()
            if not key_norm:
                continue
            nested_prefix = f"{prefix}{key_norm}."
            tokens.update(_collect_permission_tokens(item, nested_prefix))

            if key_norm in {"read", "write", "manage", "admin"} and isinstance(
                item, list
            ):
                for scope in item:
                    if isinstance(scope, str) and scope.strip():
                        scope_norm = scope.strip().lower()
                        tokens.add(f"{scope_norm}:{key_norm}")
                        if key_norm in {"manage", "admin"}:
                            tokens.add(scope_norm)
        return tokens

    return tokens


async def _load_effective_permissions(
    session: AsyncSession, empleado_id: UUID
) -> Set[str]:
    """
    Load union of active profile permissions assigned to empleado.
    Safe when profile tables are absent.
    """
    try:
        result = await session.execute(
            text(
                """
                SELECT p.permissions
                FROM empleado_access_profiles a
                JOIN access_profiles p ON p.id = a.profile_id
                WHERE a.empleado_id = :empleado_id
                  AND p.active = TRUE
                """
            ),
            {"empleado_id": empleado_id},
        )
        rows = result.fetchall()
    except Exception as exc:
        logger.debug("Access profile tables unavailable or unreadable: %s", exc)
        return set()

    permissions: Set[str] = set()
    for row in rows:
        raw = row[0]
        parsed: Any = raw
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
            except Exception:
                parsed = raw
        permissions.update(_collect_permission_tokens(parsed))
    return permissions


def has_permission(current_empleado: Empleado, required_permission: str) -> bool:
    """
    Check whether empleado has required permission token.
    Supports exact, wildcard '*' and prefix wildcard like 'admin.perfiles.*'.
    Super roles always pass.
    """
    role = _normalize_role(getattr(current_empleado, "rol", ""))
    if role in {"superadmin", "super_admin"}:
        return True

    required = (required_permission or "").strip().lower()
    if not required:
        return True

    permissions: Set[str] = set(
        getattr(current_empleado, "permissions", set()) or set()
    )
    if "*" in permissions or required in permissions:
        return True

    parts = required.split(".")
    for i in range(len(parts), 0, -1):
        wildcard = ".".join(parts[:i]) + ".*"
        if wildcard in permissions:
            return True
    return False


async def get_current_empleado(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> Empleado:
    """
    Get the current logged-in empleado from session.
    Raises HTTPException if not logged in.
    """
    empleado_id_str = request.session.get("empleado_id")

    if not empleado_id_str:
        raise HTTPException(
            status_code=401,
            detail="No has iniciado sesión. Inicia sesión para continuar.",
            headers={"Location": "/login"},
        )

    try:
        empleado_id = UUID(empleado_id_str)
    except ValueError:
        # Invalid UUID in session, clear it
        request.session.clear()
        raise HTTPException(
            status_code=401,
            detail="La sesión ya no es válida. Inicia sesión nuevamente.",
            headers={"Location": "/login"},
        )

    empleado = await _load_empleado_proxy_by_id(session, empleado_id)

    if not empleado:
        request.session.clear()
        raise HTTPException(
            status_code=401,
            detail="La cuenta asociada a esta sesión ya no existe. Inicia sesión nuevamente.",
            headers={"Location": "/login"},
        )

    if not getattr(empleado, "activo", False):
        request.session.clear()
        raise HTTPException(
            status_code=401,
            detail="La cuenta está inactiva. Contacta a un administrador.",
            headers={"Location": "/login"},
        )

    empleado.impersonator_empleado_id = request.session.get("impersonator_empleado_id")
    empleado.impersonator_nombre = request.session.get("impersonator_nombre")
    empleado.impersonator_rol = request.session.get("impersonator_rol")

    return empleado


class EmpleadoProxy:
    def __init__(
        self,
        id,
        nombre,
        correo,
        rol,
        activo,
        telefono=None,
        telegram_user_id=None,
        departamento=None,
        proyecto_predeterminado=None,
        centro_costo_predeterminado=None,
        creado_en=None,
        actualizado_en=None,
        permissions=None,
        impersonator_empleado_id=None,
        impersonator_nombre=None,
        impersonator_rol=None,
    ):
        self.id = id
        self.nombre = nombre
        self.correo = correo
        self.rol = rol
        self.activo = activo
        self.telefono = telefono
        self.telegram_user_id = telegram_user_id
        self.departamento = departamento
        self.proyecto_predeterminado = proyecto_predeterminado
        self.centro_costo_predeterminado = centro_costo_predeterminado
        self.creado_en = creado_en
        self.actualizado_en = actualizado_en
        self.permissions = permissions or set()
        self.impersonator_empleado_id = impersonator_empleado_id
        self.impersonator_nombre = impersonator_nombre
        self.impersonator_rol = impersonator_rol


async def _load_empleado_proxy_by_id(
    session: AsyncSession,
    empleado_id: UUID,
) -> Optional[Empleado]:
    result = await session.execute(
        text(
            """
            SELECT id, nombre, correo, rol, activo, password_hash,
                   telefono, telegram_user_id, departamento,
                   proyecto_predeterminado, centro_costo_predeterminado,
                   creado_en, actualizado_en
            FROM empleados
            WHERE id = :empleado_id
            """
        ),
        {"empleado_id": empleado_id},
    )
    row = result.fetchone()
    if not row:
        return None
    return await _empleado_proxy_from_row(session, row)


async def _load_empleado_proxy_by_email(
    session: AsyncSession,
    correo: str,
) -> Optional[Empleado]:
    normalized_email = str(correo or "").strip().lower()
    if not normalized_email:
        return None
    result = await session.execute(
        text(
            """
            SELECT id, nombre, correo, rol, activo, password_hash,
                   telefono, telegram_user_id, departamento,
                   proyecto_predeterminado, centro_costo_predeterminado,
                   creado_en, actualizado_en
            FROM empleados
            WHERE lower(correo) = :correo
            """
        ),
        {"correo": normalized_email},
    )
    row = result.fetchone()
    if not row:
        return None
    return await _empleado_proxy_from_row(session, row)


async def _empleado_proxy_from_row(
    session: AsyncSession,
    row: Any,
) -> Empleado:
    (
        emp_id,
        nombre,
        correo,
        rol,
        activo,
        password_hash,
        telefono,
        telegram_user_id,
        departamento,
        proyecto_predeterminado,
        centro_costo_predeterminado,
        creado_en,
        actualizado_en,
    ) = row
    effective_permissions = await _load_effective_permissions(session, emp_id)
    return EmpleadoProxy(
        id=emp_id,
        nombre=nombre,
        correo=correo,
        rol=rol,
        activo=activo,
        telefono=telefono,
        telegram_user_id=telegram_user_id,
        departamento=departamento,
        proyecto_predeterminado=proyecto_predeterminado,
        centro_costo_predeterminado=centro_costo_predeterminado,
        creado_en=creado_en,
        actualizado_en=actualizado_en,
        permissions=effective_permissions,
    )


async def get_current_empleado_or_service(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    hermes_service_token: Optional[str] = Header(
        default=None, alias="X-Hermes-Service-Token"
    ),
    hermes_actor_email: Optional[str] = Header(
        default=None, alias="X-Hermes-Actor-Email"
    ),
    hermes_actor_id: Optional[str] = Header(default=None, alias="X-Hermes-Actor-Id"),
) -> Empleado:
    empleado_id_str = request.session.get("empleado_id")
    if empleado_id_str:
        return await get_current_empleado(request, session)

    configured_token = (os.getenv("HERMES_SERVICE_TOKEN") or "").strip()
    if not hermes_service_token:
        raise HTTPException(
            status_code=401,
            detail="No has iniciado sesión. Inicia sesión o usa credenciales de servicio.",
            headers={"Location": "/login"},
        )
    if not configured_token:
        raise HTTPException(
            status_code=503,
            detail="Hermes service auth is not configured on the server.",
        )
    if hermes_service_token.strip() != configured_token:
        raise HTTPException(status_code=401, detail="Hermes service token inválido.")

    actor_email = (
        hermes_actor_email or os.getenv("HERMES_SERVICE_DEFAULT_EMAIL") or ""
    ).strip()
    actor_id_raw = (hermes_actor_id or "").strip()
    empleado: Optional[Empleado] = None
    if actor_id_raw:
        try:
            actor_id = UUID(actor_id_raw)
        except ValueError as exc:
            raise HTTPException(
                status_code=400, detail="X-Hermes-Actor-Id inválido."
            ) from exc
        empleado = await _load_empleado_proxy_by_id(session, actor_id)
    elif actor_email:
        empleado = await _load_empleado_proxy_by_email(session, actor_email)
    else:
        raise HTTPException(
            status_code=400,
            detail="Hermes service auth requiere X-Hermes-Actor-Email o X-Hermes-Actor-Id.",
        )

    if not empleado:
        raise HTTPException(status_code=404, detail="Hermes actor no encontrado.")
    if not getattr(empleado, "activo", False):
        raise HTTPException(status_code=403, detail="Hermes actor inactivo.")

    request.state.auth_via_service = True
    request.state.hermes_actor_id = str(getattr(empleado, "id", ""))
    request.state.hermes_actor_email = getattr(empleado, "correo", None)
    return empleado


def require_role_factory(allowed_roles: list[str]):
    """
    Factory function to create a dependency that requires specific roles.
    """

    async def require_role(
        current_empleado: Empleado = Depends(get_current_empleado),
    ) -> Empleado:
        """
        Require that the current empleado has one of the allowed roles.
        Raises HTTPException if role check fails.
        """
        allowed = {_normalize_role(r) for r in (allowed_roles or [])}
        if _normalize_role(getattr(current_empleado, "rol", "")) not in allowed:
            raise HTTPException(
                status_code=403,
                detail=f"Access denied. Required role: {', '.join(allowed_roles)}",
            )

        return current_empleado

    return require_role


def require_permission_factory(
    required_permissions: Iterable[str],
    *,
    allowed_roles: Optional[Iterable[str]] = None,
):
    """
    Factory dependency that grants access when:
    - empleado role is in allowed_roles, OR
    - empleado has at least one required permission token.
    """
    permission_list = [
        p.strip().lower() for p in required_permissions if p and str(p).strip()
    ]
    role_allow = {
        r.strip().lower() for r in (allowed_roles or []) if r and str(r).strip()
    }

    async def require_permission(
        current_empleado: Empleado = Depends(get_current_empleado),
    ) -> Empleado:
        role = _normalize_role(getattr(current_empleado, "rol", ""))
        if role in role_allow:
            return current_empleado

        for permission in permission_list:
            if has_permission(current_empleado, permission):
                return current_empleado

        detail = "Access denied. Missing required permission."
        if permission_list:
            detail = f"Access denied. Required permission: {', '.join(permission_list)}"
        raise HTTPException(status_code=403, detail=detail)

    return require_permission


# Convenience dependencies for common role checks
def require_admin_finanzas():
    """Dependency for admin/finanzas role."""
    return Depends(
        require_permission_factory(
            ["admin.finanzas.manage", "finanzas.manage", "admin.*"],
            allowed_roles=["admin", "finanzas", "superadmin", "super_admin"],
        )
    )
