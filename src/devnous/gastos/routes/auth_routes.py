"""
Authentication routes for web app.

Provides login/logout functionality using session-based authentication.
"""

import logging
from html import escape
from typing import Any, Optional
from urllib.parse import quote
from uuid import UUID
from fastapi import APIRouter, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
import bcrypt

from ..services.customer_success_audit import record_customer_success_audit_event

logger = logging.getLogger(__name__)

# Try to import slowapi for rate limiting, but make it optional
try:
    from slowapi import Limiter, _rate_limit_exceeded_handler
    from slowapi.util import get_remote_address
    from slowapi.errors import RateLimitExceeded

    SLOWAPI_AVAILABLE = True
except ImportError:
    SLOWAPI_AVAILABLE = False
    logger.warning("slowapi not installed - rate limiting disabled for security")

    def get_remote_address(request: Request) -> str:
        client = getattr(request, "client", None)
        return getattr(client, "host", "") or "unknown"


import asyncio
from datetime import datetime, timedelta, timezone

# Rate limiter for authentication endpoints (only if slowapi is available)
if SLOWAPI_AVAILABLE:
    limiter = Limiter(key_func=get_remote_address)
else:
    limiter = None

router = APIRouter()

# This will be set by the app that includes these routes
_db_session_maker = None

# Track failed login attempts per IP and account
_failed_login_attempts = {}  # {ip_address: {timestamp: datetime, count: int}}
_account_lockouts = {}  # {account_id: {lockout_until: datetime, attempts: int}}


def _coerce_attempt_timestamp(value) -> Optional[datetime]:
    """Best-effort parser for in-memory auth rate-limit keys."""
    if isinstance(value, datetime):
        return value
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    normalized = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def set_db_session_maker(session_maker):
    """Set the database session maker for auth routes."""
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


async def check_ip_rate_limit(request: Request) -> Optional[str]:
    """
    Check IP-based rate limiting for authentication attempts.

    Returns:
        Error message if rate limited, None otherwise
    """
    client_ip = get_remote_address(request)
    now = datetime.utcnow()

    # Clean up old entries (older than 15 minutes)
    cutoff_time = now - timedelta(minutes=15)
    cleaned_attempts = {}
    for ts, count in _failed_login_attempts.get(client_ip, {}).items():
        parsed_ts = _coerce_attempt_timestamp(ts)
        if parsed_ts is None:
            continue
        if parsed_ts > cutoff_time:
            cleaned_attempts[parsed_ts] = int(count or 0)
    _failed_login_attempts[client_ip] = cleaned_attempts

    # Get recent failed attempts for this IP
    ip_attempts = _failed_login_attempts.get(client_ip, {})

    # Check if IP is rate limited (10 failed attempts in 15 minutes)
    if sum(ip_attempts.values()) >= 10:
        logger.warning(
            f"IP rate limited due to excessive failed login attempts: {client_ip}"
        )
        return "Demasiados intentos fallidos. Por favor espere 15 minutos antes de intentar de nuevo."

    return None


async def check_account_lockout(correo: str, session: AsyncSession) -> Optional[str]:
    """
    Check if an account is locked due to excessive failed login attempts.

    Returns:
        Error message if account is locked, None otherwise
    """
    # Check in-memory lockouts first
    account_lockouts_to_clean = {
        acc_id: lockout_data
        for acc_id, lockout_data in _account_lockouts.items()
        if lockout_data["lockout_until"] < datetime.utcnow()
    }

    # Clean up expired lockouts
    for acc_id in account_lockouts_to_clean:
        del _account_lockouts[acc_id]

    # Check if account is currently locked
    for acc_id, lockout_data in _account_lockouts.items():
        if lockout_data["lockout_until"] > datetime.utcnow():
            remaining = int(
                (lockout_data["lockout_until"] - datetime.utcnow()).total_seconds() / 60
            )
            logger.warning(
                f"Account locked due to excessive failed login attempts: {correo}"
            )
            return f"Esta cuenta ha sido bloqueada temporalmente debido a intentos fallidos. Por favor espere {remaining} minutos."

    # Check database for recent failed attempts (optional persistent tracking)
    try:
        result = await session.execute(
            text(
                """
                SELECT COUNT(*) as failed_count
                FROM auth_logs
                WHERE correo = :correo
                AND success = false
                AND created_at > :cutoff_time
            """
            ),
            {"correo": correo, "cutoff_time": datetime.utcnow() - timedelta(hours=1)},
        )
        failed_count = int(result.scalar_one() or 0)
    except SQLAlchemyError as exc:
        try:
            await session.rollback()
        except Exception:
            pass
        logger.warning(
            "Account lockout DB check unavailable for %s; continuing without persistent auth_logs lookup: %s",
            correo,
            exc,
        )
        failed_count = 0

    if failed_count >= 10:
        logger.warning(
            f"Account locked due to excessive failed login attempts (database): {correo}"
        )
        # Store in-memory lockout for faster checking
        account_id = f"account_{correo}"
        _account_lockouts[account_id] = {
            "lockout_until": datetime.utcnow() + timedelta(hours=1),
            "attempts": failed_count,
        }
        return "Esta cuenta ha sido bloqueada temporalmente debido a intentos fallidos. Por favor espere 1 hora."

    return None


async def record_failed_login(client_ip: str, correo: Optional[str] = None):
    """
    Record a failed login attempt for rate limiting and account lockout.

    Args:
        client_ip: The IP address of the client
        correo: The email address (if available)
    """
    now = datetime.utcnow()

    # Record IP-based failed attempt
    if client_ip not in _failed_login_attempts:
        _failed_login_attempts[client_ip] = {}

    # Use timestamp as key to allow multiple attempts per minute
    _failed_login_attempts[client_ip][now] = (
        _failed_login_attempts[client_ip].get(now, 0) + 1
    )

    # Record account-based failed attempt if email provided
    if correo:
        account_id = f"account_{correo}"
        if account_id not in _account_lockouts:
            _account_lockouts[account_id] = {
                "lockout_until": datetime.utcnow(),
                "attempts": 0,
            }

        _account_lockouts[account_id]["attempts"] += 1

        # If too many attempts, lock the account
        if _account_lockouts[account_id]["attempts"] >= 10:
            _account_lockouts[account_id][
                "lockout_until"
            ] = datetime.utcnow() + timedelta(hours=1)

    logger.warning(
        f"Failed login attempt recorded - IP: {client_ip}, Email: {correo or 'unknown'}"
    )


async def reset_failed_login_attempts(client_ip: str, correo: str):
    """
    Reset failed login attempts after successful login.

    Args:
        client_ip: The IP address of the client
        correo: The email address of the account
    """
    # Clear IP-based attempts
    if client_ip in _failed_login_attempts:
        del _failed_login_attempts[client_ip]

    # Clear account-based lockout
    account_id = f"account_{correo}"
    if account_id in _account_lockouts:
        del _account_lockouts[account_id]

    logger.info(
        f"Failed login attempts reset after successful login - IP: {client_ip}, Email: {correo}"
    )


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against a hash using bcrypt.

    Args:
        plain_password: The plain text password to verify
        hashed_password: The bcrypt hash to verify against

    Returns:
        bool: True if password matches, False otherwise

    Raises:
        ValueError: If password would be truncated (exceeds 72 bytes)

    Security Note:
        Bcrypt has a 72-byte limit. We reject passwords that would be truncated
        to prevent password equivalence attacks where different passwords that
        share the first 72 bytes would hash to the same value.
    """
    if not hashed_password:
        return False
    try:
        password_bytes = plain_password.encode("utf-8")

        # CRITICAL SECURITY FIX: Reject passwords that would be truncated
        if len(password_bytes) > 72:
            logger.warning(
                f"Password verification rejected: password exceeds bcrypt limit of 72 bytes "
                f"(actual: {len(password_bytes)} bytes). "
                f"This prevents password equivalence attacks."
            )
            # Log the attempt for security monitoring
            logger.warning(f"Potential password equivalence attack attempt detected")
            return False  # Reject the password

        # bcrypt hashes are stored as strings in DB, need to convert to bytes
        if isinstance(hashed_password, str):
            hash_bytes = hashed_password.encode("utf-8")
        else:
            hash_bytes = hashed_password

        return bcrypt.checkpw(password_bytes, hash_bytes)
    except Exception as e:
        logger.error(f"Password verification failed: {e}")
        return False


def get_password_hash(password: str) -> str:
    """Hash a password using bcrypt.

    Args:
        password: The plain text password to hash

    Returns:
        str: The bcrypt hash as a string for database storage

    Raises:
        ValueError: If password would be truncated (exceeds 72 bytes)

    Security Note:
        Bcrypt has a 72-byte limit. We reject passwords that would be truncated
        to prevent password equivalence attacks where different passwords that
        share the first 72 bytes would hash to the same value.
    """
    password_bytes = password.encode("utf-8")

    # CRITICAL SECURITY FIX: Reject passwords that would be truncated
    if len(password_bytes) > 72:
        logger.warning(
            f"Password hashing rejected: password exceeds bcrypt limit of 72 bytes "
            f"(actual: {len(password_bytes)} bytes). "
            f"This prevents password equivalence attacks."
        )
        raise ValueError(
            f"Password too long for secure bcrypt hashing ({len(password_bytes)} bytes). "
            f"Maximum allowed: 72 bytes. Please use a shorter password."
        )

    # Generate salt and hash
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(password_bytes, salt)

    # Return as string for storage in DB
    return hashed.decode("utf-8")


def validate_new_password_pair(
    password: str, password_confirm: str
) -> tuple[bool, str]:
    """
    Validate a new password and confirmation field (admin or self-service).

    Returns (True, "") on success, or (False, spanish_error_message).
    """
    if not password or not str(password).strip():
        return False, "La nueva contraseña no puede estar vacía."
    p = str(password).strip()
    if len(p) < 8:
        return False, "La nueva contraseña debe tener al menos 8 caracteres."
    if p != str(password_confirm or "").strip():
        return False, "Las contraseñas nuevas no coinciden."
    return True, ""


def validate_self_service_password_change(
    *,
    old_password: str,
    new_password: str,
    new_confirm: str,
    stored_hash: Optional[str],
) -> tuple[bool, str]:
    """
    Full validation for logged-in users changing their own password.

    Verifies the current password against stored_hash, then validates the new pair.
    """
    if not stored_hash or not str(stored_hash).strip():
        return (
            False,
            "Tu cuenta no tiene contraseña de acceso web configurada. "
            "Pide a un administrador que la asigne desde Empleados.",
        )
    if old_password is None or not str(old_password):
        return False, "Ingresa tu contraseña actual."
    if not verify_password(str(old_password), str(stored_hash)):
        return False, "La contraseña actual es incorrecta."
    ok, err = validate_new_password_pair(new_password, new_confirm)
    if not ok:
        return False, err
    if str(new_password).strip() == str(old_password).strip():
        return False, "La nueva contraseña debe ser distinta de la actual."
    return True, ""


def _sanitize_next_path(candidate: Optional[str]) -> str:
    raw = (candidate or "").strip()
    if not raw.startswith("/"):
        return "/"
    if raw.startswith("//"):
        return "/"
    return raw


def _post_login_redirect_path(next_path: str, rol: Optional[str]) -> str:
    """
    Default post-login landing is /panel for every role.

    Preserves explicit deep links (e.g. gastos, documentos, admin) when ``next`` points elsewhere.
    """
    _ = rol
    path = (next_path or "").strip()
    low = path.lower()
    if low in ("", "/"):
        return "/panel"
    if low == "/assistant" or low.startswith("/assistant/"):
        return "/panel"
    return next_path


def _is_superadmin_role(role: Optional[str]) -> bool:
    return (str(role or "").strip().lower()) in {"superadmin", "super_admin"}


async def _load_session_employee(
    session: AsyncSession, empleado_id: Any
) -> Optional[dict[str, Any]]:
    try:
        empleado_uuid = UUID(str(empleado_id))
    except (TypeError, ValueError):
        return None
    result = await session.execute(
        text(
            """
            SELECT id, nombre, correo, rol, activo
            FROM empleados
            WHERE id = :empleado_id
            """
        ),
        {"empleado_id": empleado_uuid},
    )
    row = result.fetchone()
    if not row:
        return None
    return {
        "id": row[0],
        "nombre": row[1],
        "correo": row[2],
        "rol": row[3],
        "activo": row[4],
    }


async def _require_identity_switch_authority(
    request: Request,
    session: AsyncSession,
) -> dict[str, Any]:
    """
    Return the real superadmin actor allowed to switch identities.

    During impersonation the effective employee may be non-admin, so authority is
    anchored to the original superadmin id stored in the signed session.
    """
    original_id = request.session.get("impersonator_empleado_id")
    current_id = request.session.get("empleado_id")
    actor_id = original_id or current_id
    actor = await _load_session_employee(session, actor_id)
    if (
        not actor
        or not actor.get("activo")
        or not _is_superadmin_role(actor.get("rol"))
    ):
        raise HTTPException(
            status_code=403, detail="Sólo superadmin puede cambiar identidad."
        )
    return actor


def _set_effective_identity(request: Request, empleado: dict[str, Any]) -> None:
    request.session["empleado_id"] = str(empleado["id"])
    request.session["rol"] = str(empleado.get("rol") or "empleado")
    request.session["nombre"] = str(empleado.get("nombre") or "")


def _login_error_html(error: Optional[str]) -> str:
    """Render a user-facing login error banner."""
    banners = {
        "unknown_account": (
            "background: #fff3cd; border: 1px solid #ffc107; color: #856404;",
            "Ese correo no está registrado. Pide a un administrador que cree tu cuenta o verifique tu acceso.",
        ),
        "invalid_password": (
            "background: #fee; border: 1px solid #fcc; color: #c33;",
            "La contraseña es incorrecta. Intenta nuevamente.",
        ),
        "inactive": (
            "background: #fff3cd; border: 1px solid #ffc107; color: #856404;",
            "Su cuenta está inactiva. Contacte al administrador.",
        ),
        "no_password": (
            "background: #fff3cd; border: 1px solid #ffc107; color: #856404;",
            "Su cuenta no tiene contraseña configurada. Contacte al administrador.",
        ),
        "invalid_session": (
            "background: #fff3cd; border: 1px solid #ffc107; color: #856404;",
            "Tu sesión ya no es válida. Inicia sesión nuevamente.",
        ),
        "account_missing": (
            "background: #fff3cd; border: 1px solid #ffc107; color: #856404;",
            "Tu cuenta ya no existe o fue removida. Inicia sesión de nuevo o contacta a un administrador.",
        ),
        "invalid_credentials": (
            "background: #fee; border: 1px solid #fcc; color: #c33;",
            "Credenciales inválidas. Por favor intente nuevamente.",
        ),
        "rate_limited": (
            "background: #fff3cd; border: 1px solid #ffc107; color: #856404;",
            "Demasiados intentos fallidos. Espera unos minutos antes de intentar nuevamente.",
        ),
        "account_locked": (
            "background: #fff3cd; border: 1px solid #ffc107; color: #856404;",
            "Tu cuenta fue bloqueada temporalmente por seguridad. Espera el tiempo indicado antes de intentar nuevamente.",
        ),
    }
    if error not in banners:
        return ""
    style, message = banners[error]
    return (
        f'<div style="{style} padding: 10px; border-radius: 5px; margin-bottom: 15px;">'
        f"{message}</div>"
    )


@router.get("/login", response_class=HTMLResponse)
async def login_page(
    request: Request, error: Optional[str] = None, next: Optional[str] = None
):
    """
    Render login form.
    """
    error_message = _login_error_html(error)
    next_path = _sanitize_next_path(next)

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Iniciar Sesión - Copa Telmex</title>
        <style>
            body {{
                font-family: Arial, sans-serif;
                margin: 0;
                padding: 20px;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                min-height: 100vh;
                box-sizing: border-box;
                display: flex;
                align-items: center;
                justify-content: center;
            }}
            .container {{
                background: white;
                border-radius: 12px;
                padding: 40px;
                box-shadow: 0 10px 40px rgba(0,0,0,0.2);
                max-width: 380px;
                width: 100%;
            }}
            h1 {{
                color: #333;
                border-bottom: 3px solid #4CAF50;
                padding-bottom: 10px;
                margin-top: 0;
                text-align: center;
            }}
            .form-group {{
                margin-bottom: 20px;
            }}
            .form-group label {{
                display: block;
                margin-bottom: 8px;
                font-weight: bold;
                color: #333;
            }}
            .form-group input {{
                width: 100%;
                padding: 12px;
                border: 1px solid #ddd;
                border-radius: 6px;
                font-size: 16px;
                box-sizing: border-box;
            }}
            .form-group input:focus {{
                outline: none;
                border-color: #4CAF50;
                box-shadow: 0 0 0 3px rgba(76, 175, 80, 0.1);
            }}
            .password-wrapper {{
                position: relative;
            }}
            .password-wrapper input {{
                padding-right: 44px;
            }}
            .password-toggle {{
                position: absolute;
                right: 8px;
                top: 50%;
                transform: translateY(-50%);
                background: none;
                border: none;
                cursor: pointer;
                padding: 4px;
                color: #666;
                display: flex;
                align-items: center;
                justify-content: center;
                line-height: 0;
            }}
            .password-toggle:hover {{
                color: #333;
            }}
            .password-toggle:focus {{
                outline: 2px solid #4CAF50;
                outline-offset: 2px;
                border-radius: 4px;
            }}
            .btn {{
                width: 100%;
                padding: 12px;
                background-color: #4CAF50;
                color: white;
                border: none;
                border-radius: 6px;
                font-size: 16px;
                cursor: pointer;
                transition: background-color 0.3s;
            }}
            .btn:hover {{
                background-color: #45a049;
            }}
            .btn:active {{
                background-color: #3d8b40;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🔐 Iniciar Sesión</h1>
            {error_message}
            <form method="POST" action="/login">
                <input type="hidden" name="next" value="{next_path}">
                <div class="form-group">
                    <label for="correo">Correo Electrónico</label>
                    <input type="email" name="correo" id="correo" required autofocus>
                </div>
                <div class="form-group">
                    <label for="password">Contraseña</label>
                    <div class="password-wrapper">
                        <input type="password" name="password" id="password" required>
                        <button
                            type="button"
                            class="password-toggle"
                            id="password-toggle"
                            aria-label="Mostrar contraseña"
                            title="Mostrar contraseña"
                        >
                            <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                                <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path>
                                <circle cx="12" cy="12" r="3"></circle>
                            </svg>
                        </button>
                    </div>
                </div>
                <button type="submit" class="btn">Iniciar Sesión</button>
            </form>
        </div>
        <script>
            (function () {{
                var input = document.getElementById("password");
                var toggle = document.getElementById("password-toggle");
                if (!input || !toggle) {{
                    return;
                }}
                var eyeOpen =
                    '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
                    '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path>' +
                    '<circle cx="12" cy="12" r="3"></circle></svg>';
                var eyeClosed =
                    '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
                    '<path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94"></path>' +
                    '<path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19"></path>' +
                    '<line x1="1" y1="1" x2="23" y2="23"></line></svg>';
                toggle.addEventListener("click", function () {{
                    var show = input.type === "password";
                    input.type = show ? "text" : "password";
                    toggle.innerHTML = show ? eyeClosed : eyeOpen;
                    toggle.setAttribute(
                        "aria-label",
                        show ? "Ocultar contraseña" : "Mostrar contraseña"
                    );
                    toggle.setAttribute(
                        "title",
                        show ? "Ocultar contraseña" : "Mostrar contraseña"
                    );
                }});
            }})();
        </script>
    </body>
    </html>
    """
    return html


async def login(
    request: Request,
    correo: str = Form(...),
    password: str = Form(...),
    next: Optional[str] = Form(default=None),
    session: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    """
    Handle login form submission with rate limiting and account lockout.
    Look up empleado by correo, verify password, and create session.
    """
    client_ip = get_remote_address(request)
    correo_clean = correo.strip() if correo else ""
    next_path = _sanitize_next_path(next)

    # SECURITY: Check IP-based rate limiting
    ip_rate_limit_error = await check_ip_rate_limit(request)
    if ip_rate_limit_error:
        logger.warning(f"IP rate limit exceeded: {client_ip}")
        return RedirectResponse(
            url=f"/login?error=rate_limited&next={quote(next_path, safe='/%?=&')}",
            status_code=429,  # Too Many Requests
        )

    # SECURITY: Check account lockout status
    account_lockout_error = await check_account_lockout(correo_clean, session)
    if account_lockout_error:
        logger.warning(f"Account lockout check failed: {correo_clean}")
        return RedirectResponse(
            url=f"/login?error=account_locked&next={quote(next_path, safe='/%?=&')}",
            status_code=403,  # Forbidden
        )

    try:
        # Look up empleado by correo
        # Use raw SQL to avoid aprobador_id column issue (column may not exist in DB)
        result = await session.execute(
            text(
                """
                SELECT id, nombre, correo, rol, activo, password_hash
                FROM empleados
                WHERE correo = :correo
            """
            ),
            {"correo": correo_clean},
        )
        row = result.fetchone()

        if not row:
            logger.warning(f"Login attempt with unknown email: {correo_clean}")
            await record_customer_success_audit_event(
                session,
                action="auth.login.failed",
                request=request,
                summary=f"Login fallido para correo desconocido: {correo_clean}",
                metadata={"correo": correo_clean, "reason": "unknown_account"},
                commit=True,
            )
            return RedirectResponse(
                url=f"/login?error=unknown_account&next={quote(next_path, safe='/%?=&')}",
                status_code=303,
            )

        # Access row by index to avoid unpacking issues
        empleado_id = row[0]
        nombre = row[1]
        correo_db = row[2]
        rol = row[3]
        activo = row[4]
        password_hash = row[5]

        # Check if empleado is active
        if not activo:
            logger.warning(f"Login attempt for inactive empleado: {correo_clean}")
            await record_customer_success_audit_event(
                session,
                action="auth.login.failed",
                actor_empleado_id=empleado_id,
                target_empleado_id=empleado_id,
                request=request,
                summary=f"Login fallido para cuenta inactiva: {correo_db}",
                metadata={"correo": correo_db, "reason": "inactive"},
                commit=True,
            )
            return RedirectResponse(
                url=f"/login?error=inactive&next={quote(next_path, safe='/%?=&')}",
                status_code=303,
            )

        # Check if password is set
        if not password_hash:
            logger.warning(
                f"Login attempt for empleado without password: {correo_clean}"
            )
            await record_customer_success_audit_event(
                session,
                action="auth.login.failed",
                actor_empleado_id=empleado_id,
                target_empleado_id=empleado_id,
                request=request,
                summary=f"Login fallido sin contraseña configurada: {correo_db}",
                metadata={"correo": correo_db, "reason": "no_password"},
                commit=True,
            )
            return RedirectResponse(
                url=f"/login?error=no_password&next={quote(next_path, safe='/%?=&')}",
                status_code=303,
            )

        # Verify password
        if not verify_password(password, password_hash):
            # SECURITY: Record failed login attempt
            await record_failed_login(client_ip, correo_clean)
            logger.warning(f"Invalid password for empleado: {correo_clean}")
            await record_customer_success_audit_event(
                session,
                action="auth.login.failed",
                actor_empleado_id=empleado_id,
                target_empleado_id=empleado_id,
                request=request,
                summary=f"Login fallido por contraseña inválida: {correo_db}",
                metadata={"correo": correo_db, "reason": "invalid_password"},
                commit=True,
            )
            return RedirectResponse(
                url=f"/login?error=invalid_password&next={quote(next_path, safe='/%?=&')}",
                status_code=303,
            )

        # Create session
        # Convert UUID to string (handles both UUID and asyncpg UUID types)
        empleado_id_str = str(empleado_id) if empleado_id else None
        if not empleado_id_str:
            logger.error(f"Invalid empleado_id for {correo_clean}")
            return RedirectResponse(
                url=f"/login?error=invalid_credentials&next={quote(next_path, safe='/%?=&')}",
                status_code=303,
            )

        request.session.clear()
        request.session["empleado_id"] = empleado_id_str
        request.session["rol"] = str(rol) if rol else "empleado"
        request.session["nombre"] = str(nombre) if nombre else ""

        # SECURITY: Reset failed login attempts after successful login
        await reset_failed_login_attempts(client_ip, correo_clean)

        logger.info(f"Successful login for empleado: {correo_db} (rol: {rol})")
        await record_customer_success_audit_event(
            session,
            action="auth.login.success",
            actor_empleado_id=empleado_id_str,
            target_empleado_id=empleado_id_str,
            request=request,
            summary=f"Login exitoso: {correo_db}",
            metadata={"correo": correo_db, "rol": rol},
            commit=True,
        )

        redirect_url = _post_login_redirect_path(next_path, rol)
        return RedirectResponse(url=redirect_url, status_code=303)

    except Exception as e:
        # Log the full error with traceback for debugging
        logger.error(f"Login error for {correo_clean}: {e}", exc_info=True)
        # Return to login page with generic error
        try:
            return RedirectResponse(
                url=f"/login?error=invalid_credentials&next={quote(next_path, safe='/%?=&')}",
                status_code=303,
            )
        except Exception as redirect_error:
            # If redirect fails, log and return a simple error response
            logger.error(
                f"Failed to redirect after login error: {redirect_error}", exc_info=True
            )
            from fastapi.responses import PlainTextResponse

            return PlainTextResponse(
                "Internal Server Error: Login failed. Please try again.",
                status_code=500,
            )


router.add_api_route(
    "/login",
    limiter.limit("5/minute")(login) if SLOWAPI_AVAILABLE else login,
    methods=["POST"],
    response_class=RedirectResponse,
)


@router.get("/logout")
async def logout(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    """
    Clear session and redirect to login.
    """
    empleado_id = request.session.get("empleado_id")
    await record_customer_success_audit_event(
        session,
        action="auth.logout",
        actor_empleado_id=empleado_id,
        target_empleado_id=empleado_id,
        request=request,
        summary="Logout de usuario",
        commit=True,
    )
    request.session.clear()
    logger.info("User logged out")
    return RedirectResponse(url="/login", status_code=303)


@router.get("/admin/identidad", response_class=HTMLResponse)
async def identity_switch_page(
    request: Request,
    next: Optional[str] = None,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    actor = await _require_identity_switch_authority(request, session)
    next_path = _sanitize_next_path(next) if next else "/panel"
    current = await _load_session_employee(session, request.session.get("empleado_id"))
    result = await session.execute(
        text(
            """
            SELECT id, nombre, correo, rol, activo
            FROM empleados
            WHERE activo = TRUE
            ORDER BY lower(nombre), lower(correo)
            """
        )
    )
    rows = result.fetchall()
    options = []
    for row in rows:
        empleado_id = str(row[0])
        selected = (
            " selected" if current and str(current.get("id")) == empleado_id else ""
        )
        label = f"{row[1] or 'Sin nombre'} · {row[2] or 'sin correo'} · {row[3] or 'empleado'}"
        options.append(
            f'<option value="{escape(empleado_id)}"{selected}>{escape(label)}</option>'
        )
    restore_form = ""
    if request.session.get("impersonator_empleado_id"):
        restore_form = f"""
        <form method="POST" action="/admin/identidad/restaurar" style="margin-top:14px;">
            <input type="hidden" name="next" value="{escape(next_path)}">
            <button type="submit" class="secondary">Volver a mi identidad superadmin</button>
        </form>
        """
    actor_label = f"{actor.get('nombre') or ''} ({actor.get('rol') or ''})"
    current_label = (
        f"{current.get('nombre') or ''} ({current.get('rol') or ''})"
        if current
        else "Sesión actual no encontrada"
    )
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Cambiar identidad - Samchat</title>
        <style>
            body {{
                margin:0;
                min-height:100vh;
                font-family:Arial, sans-serif;
                background:linear-gradient(135deg,#0f172a,#1f2937);
                display:flex;
                align-items:center;
                justify-content:center;
                color:#0f172a;
            }}
            .card {{
                width:min(720px, calc(100vw - 32px));
                background:#ffffff;
                border-radius:22px;
                padding:28px;
                box-shadow:0 28px 80px rgba(0,0,0,.35);
            }}
            h1 {{ margin:0 0 8px; font-size:28px; }}
            p {{ color:#475569; line-height:1.5; }}
            label {{ display:block; font-weight:700; margin:18px 0 8px; }}
            select {{
                width:100%;
                padding:12px;
                border:1px solid #cbd5e1;
                border-radius:12px;
                font-size:15px;
            }}
            button, .link {{
                display:inline-block;
                margin-top:16px;
                padding:12px 16px;
                border-radius:12px;
                border:0;
                background:#0f766e;
                color:white;
                font-weight:800;
                text-decoration:none;
                cursor:pointer;
            }}
            button.secondary {{
                background:#7f1d1d;
            }}
            .meta {{
                display:grid;
                grid-template-columns:repeat(auto-fit,minmax(220px,1fr));
                gap:10px;
                margin:18px 0;
            }}
            .box {{
                padding:12px;
                background:#f8fafc;
                border:1px solid #e2e8f0;
                border-radius:14px;
            }}
            small {{
                display:block;
                color:#64748b;
                text-transform:uppercase;
                letter-spacing:.08em;
                font-weight:700;
                margin-bottom:4px;
            }}
        </style>
    </head>
    <body>
        <main class="card">
            <h1>Cambiar identidad</h1>
            <p>Herramienta sólo para superadmin. Cambia el empleado efectivo de la sesión para revisar permisos, vistas y flujos como esa persona.</p>
            <div class="meta">
                <div class="box"><small>Superadmin real</small>{escape(actor_label)}</div>
                <div class="box"><small>Identidad efectiva</small>{escape(current_label)}</div>
            </div>
            <form method="POST" action="/admin/identidad/cambiar">
                <input type="hidden" name="next" value="{escape(next_path)}">
                <label for="empleado_id">Empleado</label>
                <select name="empleado_id" id="empleado_id" required>
                    {''.join(options)}
                </select>
                <button type="submit">Cambiar identidad</button>
                <a class="link" href="/panel">Cancelar</a>
            </form>
            {restore_form}
        </main>
    </body>
    </html>
    """
    return HTMLResponse(content=html)


@router.post("/admin/identidad/cambiar")
async def switch_identity(
    request: Request,
    empleado_id: str = Form(...),
    next: Optional[str] = Form(default=None),
    session: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    actor = await _require_identity_switch_authority(request, session)
    target = await _load_session_employee(session, empleado_id)
    if not target or not target.get("activo"):
        raise HTTPException(
            status_code=404, detail="Empleado destino no encontrado o inactivo."
        )

    if not request.session.get("impersonator_empleado_id"):
        request.session["impersonator_empleado_id"] = str(actor["id"])
        request.session["impersonator_nombre"] = str(actor.get("nombre") or "")
        request.session["impersonator_rol"] = str(actor.get("rol") or "superadmin")

    _set_effective_identity(request, target)
    logger.warning(
        "Superadmin identity switch: actor=%s target=%s",
        actor.get("id"),
        target.get("id"),
    )
    await record_customer_success_audit_event(
        session,
        action="auth.identity.switch",
        actor_empleado_id=actor.get("id"),
        target_empleado_id=target.get("id"),
        request=request,
        summary=(
            f"{actor.get('nombre') or actor.get('correo')} cambió identidad a "
            f"{target.get('nombre') or target.get('correo')}"
        ),
        metadata={
            "actor_correo": actor.get("correo"),
            "target_correo": target.get("correo"),
        },
        commit=True,
    )
    next_path = _sanitize_next_path(next) if next else "/panel"
    return RedirectResponse(url=next_path, status_code=303)


@router.post("/admin/identidad/restaurar")
async def restore_identity(
    request: Request,
    next: Optional[str] = Form(default=None),
    session: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    original_id = request.session.get("impersonator_empleado_id")
    if not original_id:
        next_path = _sanitize_next_path(next) if next else "/panel"
        return RedirectResponse(url=next_path, status_code=303)

    actor = await _load_session_employee(session, original_id)
    if (
        not actor
        or not actor.get("activo")
        or not _is_superadmin_role(actor.get("rol"))
    ):
        request.session.clear()
        return RedirectResponse(url="/login?error=invalid_session", status_code=303)

    _set_effective_identity(request, actor)
    request.session.pop("impersonator_empleado_id", None)
    request.session.pop("impersonator_nombre", None)
    request.session.pop("impersonator_rol", None)
    logger.warning("Superadmin identity restored: actor=%s", actor.get("id"))
    await record_customer_success_audit_event(
        session,
        action="auth.identity.restore",
        actor_empleado_id=actor.get("id"),
        target_empleado_id=actor.get("id"),
        request=request,
        summary=f"{actor.get('nombre') or actor.get('correo')} restauró su identidad",
        metadata={"actor_correo": actor.get("correo")},
        commit=True,
    )
    next_path = _sanitize_next_path(next) if next else "/panel"
    return RedirectResponse(url=next_path, status_code=303)
