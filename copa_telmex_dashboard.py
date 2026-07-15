#!/usr/bin/env python3
"""
Copa Telmex - Web Dashboard
FastAPI application to view teams, players, and OCR registrations.

Usage:
    systemctl restart samchat-gastos.service

Then visit: http://localhost:8000
"""

import copy
import io
import hashlib
import json
import logging
import os
import sys
import secrets
from html import escape
from pathlib import Path
from datetime import datetime, date, timezone
from typing import List, Optional, Dict, Any, Tuple, Iterable
from urllib.parse import urlparse
from urllib.parse import quote, unquote
from uuid import UUID

# Load environment variables from .env file
from dotenv import load_dotenv

_env_file = (os.getenv("SAMCHAT_ENV_FILE") or "").strip()
if _env_file:
    load_dotenv(_env_file, override=True)
else:
    load_dotenv()

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException
import uvicorn

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import selectinload
from devnous.copa_telmex.database import CopaTelmexDB
from devnous.copa_telmex.models import (
    Base,
    RegistrationReviewAsset,
    RegistrationReviewDraft,
    RegistrationReviewSession,
)
from devnous.copa_telmex.runtime_controls import (
    REGISTRATION_REVIEW_CANONICAL_INTAKE as RUNTIME_REGISTRATION_REVIEW_CANONICAL_INTAKE,
    REVIEW_ASSET_RETENTION_DAYS as RUNTIME_REVIEW_ASSET_RETENTION_DAYS,
    REVIEW_DRAFT_RETENTION_DAYS as RUNTIME_REVIEW_DRAFT_RETENTION_DAYS,
    REVIEW_PURGE_DRY_RUN as RUNTIME_REVIEW_PURGE_DRY_RUN,
    build_review_pii_path_inventory,
    plan_review_data_retention,
)
from devnous.gastos.routes import (
    admin_router,
    auth_router,
    support_router,
    user_router,
    webhook_router,
)
try:
    from devnous.gastos.routes import operations_analytics_router
except ImportError:
    operations_analytics_router = None
from devnous.gastos.routes.admin_routes import set_db_session_maker as set_admin_session_maker
from devnous.gastos.routes.webhook_handler import set_db_session_maker as set_webhook_session_maker
try:
    from devnous.sat.sat_background_runner import (
        recover_orphaned_sat_jobs,
        set_session_maker as set_sat_background_session_maker,
        shutdown_background_jobs,
    )
except ImportError:
    async def recover_orphaned_sat_jobs():
        return 0

    def set_sat_background_session_maker(_session_maker):
        return None

    async def shutdown_background_jobs():
        return None
from devnous.gastos.routes.user_routes import set_db_session_maker as set_user_session_maker
from devnous.gastos.routes.auth_routes import set_db_session_maker as set_auth_session_maker
from devnous.gastos.routes.dependencies import set_db_session_maker as set_dependencies_session_maker
from devnous.gastos.schema_guard import apply_schema_guard, check_schema_health

from samchat.assistant import assistant_router
# Import CFDIReport to ensure table is created
from devnous.gastos.models import CFDIReport  # noqa: F401 - register SQLAlchemy table
from devnous.agents.ocr_agent import OCRAgent
from devnous.agents.ocr_schemas import RegistrationFormExtraction
from devnous.tournaments.core.local_ocr_runner import LocalOCRRunner
from devnous.tournaments.core.ocr_integrity import (
    average_hash_hex,
    compute_sha256_hex,
    crop_player_photo,
    image_has_photo_like_content,
    slugify_filename,
)
from devnous.tournaments.instances.copa_telmex.ctt_review_ui import (
    build_canonical_review_view,
)
from PIL import Image

try:
    import fitz
except ImportError:
    fitz = None

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


REVIEW_TOURNAMENT_OPTIONS: List[Tuple[str, str]] = [
    ("copa_telmex", "Copa Telmex"),
    ("copa-telmex-2026", "Copa Telmex 2026"),
    ("copa-telmex-2025", "Copa Telmex 2025"),
    ("liga-telmex-2026", "Liga Telmex 2026"),
    ("copa-club-america", "Copa Club America"),
    ("homeless-world-cup", "Homeless World Cup"),
]


def _split_csv_env(value: Optional[str]) -> List[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _build_allowed_origins() -> List[str]:
    configured_app_url = (os.getenv("APP_URL") or "").strip()
    app_url = (configured_app_url or "https://sam.chat").rstrip("/")
    parsed = urlparse(app_url)
    hostname = (parsed.hostname or "sam.chat").strip()

    origins = {
        "https://sam.chat",
        "https://www.sam.chat",
    }
    # APP_URL and ALLOWED_APP_ORIGINS are explicit operator configuration.
    # Keep the built-in production defaults HTTPS-only; local HTTP development
    # can still be enabled deliberately through either setting.
    if configured_app_url or parsed.scheme == "https":
        origins.add(app_url)
        origins.add(f"{parsed.scheme or 'https'}://{hostname}")
    origins.update(_split_csv_env(os.getenv("ALLOWED_APP_ORIGINS")))
    return sorted(origin for origin in origins if origin)


def _review_tournament_options(selected_slug: Optional[str] = None) -> List[Dict[str, Any]]:
    selected = (selected_slug or "").strip().lower()
    options: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for value, label in REVIEW_TOURNAMENT_OPTIONS:
        options.append(
            {
                "value": value,
                "label": label,
                "selected": value.lower() == selected,
            }
        )
        seen.add(value.lower())
    if selected and selected not in seen:
        options.insert(
            0,
            {
                "value": selected_slug,
                "label": selected_slug,
                "selected": True,
            }
        )
    return options


def _resolve_dist_dir(*candidates: Path) -> Optional[Path]:
    for candidate in candidates:
        if candidate and candidate.exists():
            return candidate
    return None


def _session_next_target(request: Request, fallback: str = "/assistant") -> str:
    raw_path = (request.url.path or "").strip() or fallback
    if not raw_path.startswith("/"):
        raw_path = fallback
    query = f"?{request.url.query}" if request.url.query else ""
    return f"{raw_path}{query}"


def _has_internal_session(request: Request) -> bool:
    try:
        session = request.session or {}
    except Exception:
        return False
    empleado_id = str(session.get("empleado_id") or "").strip()
    return bool(empleado_id)


def _is_session_empleado_role(request: Request) -> bool:
    """True when the logged-in user has base role ``empleado`` (not admin/coordinador/etc.)."""
    try:
        session = request.session or {}
    except Exception:
        return False
    rol = str(session.get("rol") or "").strip().lower()
    return rol == "empleado"


def _redirect_to_login(request: Request, fallback: str = "/assistant") -> RedirectResponse:
    next_target = _session_next_target(request, fallback=fallback)
    return RedirectResponse(url=f"/login?next={quote(next_target, safe='/%?=&')}", status_code=307)


def _require_session_secret_key() -> str:
    secret = (os.getenv("SESSION_SECRET_KEY") or "").strip()
    if not secret:
        raise RuntimeError("SESSION_SECRET_KEY must be configured before starting samchat-gastos.")
    return secret


PRODUCTION_ENV_VALUES = frozenset({"production", "prod", "live"})


def _samchat_runtime_env() -> str:
    for name in ("SAMCHAT_ENV", "ENVIRONMENT", "APP_ENV", "FASTAPI_ENV"):
        value = (os.getenv(name) or "").strip().lower()
        if value:
            return value
    return ""


def _is_production_runtime() -> bool:
    return _samchat_runtime_env() in PRODUCTION_ENV_VALUES


def _require_database_url_for_runtime() -> str:
    configured_url = (os.getenv("DATABASE_URL") or "").strip()
    if configured_url:
        return configured_url
    if _is_production_runtime():
        raise RuntimeError(
            "DATABASE_URL must be configured before starting samchat-gastos in production mode."
        )
    return "postgresql+asyncpg://copa_user:copa_pass_2025@localhost:5432/copa_telmex"


REVIEW_ALLOWED_SESSION_ROLES = frozenset(
    {"coordinador", "finanzas", "admin", "superadmin", "super_admin"}
)
TEAM_PLAYER_MUTATION_ALLOWED_SESSION_ROLES = REVIEW_ALLOWED_SESSION_ROLES
LEGACY_COPA_DASHBOARD_ALLOWED_SESSION_ROLES = REVIEW_ALLOWED_SESSION_ROLES
REVIEW_SESSION_MAX_FILES = 4
MAX_REVIEW_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_REVIEW_IMAGE_PIXELS = 25_000_000
MAX_REVIEW_IMAGE_DIMENSION = 8000
REVIEW_UPLOAD_READ_CHUNK_BYTES = 1024 * 1024
REVIEW_ALLOWED_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".pdf"})
REVIEW_ALLOWED_IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp"})
REVIEW_ALLOWED_MIME_TYPES = frozenset(
    {"image/jpeg", "image/png", "image/webp", "application/pdf"}
)
# Canonical pilot rule: registration-review is the only authorized intake path.
# Legacy OCR bots are non-canonical and must not persist final players without human review.
REGISTRATION_REVIEW_CANONICAL_INTAKE = RUNTIME_REGISTRATION_REVIEW_CANONICAL_INTAKE
REVIEW_ASSET_RETENTION_DAYS = RUNTIME_REVIEW_ASSET_RETENTION_DAYS
REVIEW_DRAFT_RETENTION_DAYS = RUNTIME_REVIEW_DRAFT_RETENTION_DAYS
REVIEW_PURGE_DRY_RUN = RUNTIME_REVIEW_PURGE_DRY_RUN


def _normalized_session_role(request: Request) -> str:
    try:
        session = request.session or {}
    except Exception:
        return ""
    return str(session.get("rol") or "").strip().lower()


def _review_error(
    code: str,
    message: str,
    *,
    status_code: int = 400,
    extra: Optional[Dict[str, Any]] = None,
) -> HTTPException:
    detail: Dict[str, Any] = {"ok": False, "error": code, "message": message}
    if isinstance(extra, dict):
        detail.update(extra)
    return HTTPException(status_code=status_code, detail=detail)


def _ensure_review_session_mutable(review_session: RegistrationReviewSession) -> None:
    if (
        str(review_session.status or "").strip().lower() == "committed"
        or review_session.committed_at is not None
        or review_session.committed_team_id is not None
    ):
        raise _review_error(
            "session_already_committed",
            "Esta sesión ya fue capturada y no admite más cambios.",
            status_code=409,
        )


def _ensure_review_session_not_rejected(
    review_session: RegistrationReviewSession,
) -> None:
    if str(review_session.status or "").strip().lower() == "rejected":
        raise _review_error(
            "session_rejected",
            "Esta revisión fue rechazada. Modifica o reprocesa el borrador antes de capturar.",
            status_code=409,
        )


def _ensure_registration_review_access(
    request: Request,
    *,
    html_fallback: Optional[str] = None,
) -> Optional[RedirectResponse]:
    if not _has_internal_session(request):
        if html_fallback:
            return _redirect_to_login(request, fallback=html_fallback)
        raise HTTPException(
            status_code=401,
            detail="No has iniciado sesión. Inicia sesión para continuar.",
            headers={"Location": "/login"},
        )

    role = _normalized_session_role(request)
    if role not in REVIEW_ALLOWED_SESSION_ROLES:
        raise HTTPException(
            status_code=403,
            detail="No tienes permisos para operar la precaptura de cédulas.",
        )
    return None


def _ensure_legacy_copa_dashboard_access(
    request: Request,
    *,
    html_fallback: Optional[str] = None,
) -> Optional[RedirectResponse]:
    if not _has_internal_session(request):
        if html_fallback:
            return _redirect_to_login(request, fallback=html_fallback)
        raise HTTPException(
            status_code=401,
            detail="No has iniciado sesión. Inicia sesión para continuar.",
            headers={"Location": "/login"},
        )

    role = _normalized_session_role(request)
    if role not in LEGACY_COPA_DASHBOARD_ALLOWED_SESSION_ROLES:
        raise HTTPException(
            status_code=403,
            detail="No tienes permisos para consultar datos de equipos o jugadores.",
        )
    return None


def _ensure_team_player_mutation_access(request: Request) -> None:
    if not _has_internal_session(request):
        raise HTTPException(
            status_code=401,
            detail="No has iniciado sesión. Inicia sesión para continuar.",
            headers={"Location": "/login"},
        )

    role = _normalized_session_role(request)
    if role not in TEAM_PLAYER_MUTATION_ALLOWED_SESSION_ROLES:
        raise HTTPException(
            status_code=403,
            detail="No tienes permisos para modificar equipos o jugadores.",
        )


async def _read_upload_limited(upload: Any) -> bytes:
    payload = bytearray()
    while True:
        chunk = await upload.read(REVIEW_UPLOAD_READ_CHUNK_BYTES)
        if not chunk:
            break
        payload.extend(chunk)
        if len(payload) > MAX_REVIEW_UPLOAD_BYTES:
            raise _review_error(
                "file_too_large",
                "El archivo excede el tamaño máximo permitido.",
            )
    return bytes(payload)


def _sniff_pdf_bytes(payload: bytes) -> bool:
    return payload[:5] == b"%PDF-"


def _detect_review_extension(filename: str) -> str:
    return (Path(filename or "").suffix or "").strip().lower()


def _validate_review_upload_metadata(upload: Any, payload: bytes) -> str:
    filename = str(getattr(upload, "filename", "") or "").strip()
    if not filename:
        raise _review_error("invalid_file_type", "El archivo debe tener nombre y extensión válidos.")

    extension = _detect_review_extension(filename)
    if extension not in REVIEW_ALLOWED_EXTENSIONS:
        raise _review_error(
            "invalid_file_type",
            "El archivo debe ser JPG, PNG, WEBP o PDF.",
        )

    content_type = str(getattr(upload, "content_type", "") or "").strip().lower()
    if content_type and content_type not in REVIEW_ALLOWED_MIME_TYPES:
        raise _review_error(
            "invalid_mime_type",
            "El tipo de archivo no es válido para esta precaptura.",
        )

    if extension == ".pdf":
        if content_type and content_type != "application/pdf":
            raise _review_error(
                "invalid_mime_type",
                "El tipo de archivo no coincide con un PDF válido.",
            )
        if not _sniff_pdf_bytes(payload):
            raise _review_error(
                "invalid_file_type",
                "El archivo debe ser JPG, PNG, WEBP o PDF.",
            )
        return extension

    if content_type and not content_type.startswith("image/"):
        raise _review_error(
            "invalid_mime_type",
            "El tipo de archivo no es válido para esta precaptura.",
        )
    return extension


def _validate_review_image_payload(payload: bytes) -> Tuple[int, int]:
    try:
        with Image.open(io.BytesIO(payload)) as img:
            img.verify()
        with Image.open(io.BytesIO(payload)) as img:
            width, height = img.size
    except Exception as exc:
        raise _review_error(
            "corrupt_image",
            "No pude leer la imagen. Sube una foto clara del documento.",
        ) from exc

    if width <= 0 or height <= 0:
        raise _review_error(
            "corrupt_image",
            "No pude leer la imagen. Sube una foto clara del documento.",
        )
    if width > MAX_REVIEW_IMAGE_DIMENSION or height > MAX_REVIEW_IMAGE_DIMENSION:
        raise _review_error(
            "image_too_large",
            "La imagen excede las dimensiones máximas permitidas.",
        )
    if width * height > MAX_REVIEW_IMAGE_PIXELS:
        raise _review_error(
            "image_too_large",
            "La imagen excede las dimensiones máximas permitidas.",
        )
    return width, height


def _render_pdf_review_assets(
    session_dir: Path,
    payload: bytes,
    *,
    start_page_index: int,
    remaining_slots: int,
) -> List[Dict[str, Any]]:
    if fitz is None:
        raise _review_error(
            "invalid_file_type",
            "Los PDF no están habilitados en este entorno de precaptura.",
        )
    try:
        document = fitz.open(stream=payload, filetype="pdf")
    except Exception as exc:
        raise _review_error(
            "corrupt_image",
            "No pude leer el PDF. Sube una versión clara del documento.",
        ) from exc

    with document:
        page_count = int(getattr(document, "page_count", 0) or 0)
        if page_count <= 0:
            raise _review_error(
                "corrupt_image",
                "No pude leer el PDF. Sube una versión clara del documento.",
            )
        if page_count > remaining_slots:
            raise _review_error(
                "too_many_files",
                f"Solo se permiten hasta {REVIEW_SESSION_MAX_FILES} páginas por revisión.",
            )

        stored_assets: List[Dict[str, Any]] = []
        for page_offset in range(page_count):
            page_index = start_page_index + page_offset
            page = document.load_page(page_offset)
            pix = page.get_pixmap(alpha=False)
            width, height = int(pix.width), int(pix.height)
            if width > MAX_REVIEW_IMAGE_DIMENSION or height > MAX_REVIEW_IMAGE_DIMENSION:
                raise _review_error(
                    "image_too_large",
                    "La imagen excede las dimensiones máximas permitidas.",
                )
            if width * height > MAX_REVIEW_IMAGE_PIXELS:
                raise _review_error(
                    "image_too_large",
                    "La imagen excede las dimensiones máximas permitidas.",
                )
            page_bytes = pix.tobytes("png")
            output_path = session_dir / f"page-{page_index:02d}.png"
            output_path.write_bytes(page_bytes)
            stored_assets.append(
                {
                    "page_index": page_index,
                    "image_path": str(output_path),
                    "sha256": hashlib.sha256(page_bytes).hexdigest(),
                    "width": width,
                    "height": height,
                }
            )
        return stored_assets


def _dedupe_review_items(items: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    deduped: List[Dict[str, Any]] = []
    for item in items:
        key = (
            str(item.get("code") or ""),
            str(item.get("field") or ""),
            str(item.get("message") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _player_has_usable_signal(player: Dict[str, Any]) -> bool:
    name = (player.get("name") or "").strip()
    birth_date = (player.get("birth_date") or "").strip()
    curp = (player.get("curp") or "").strip()
    return bool(
        name
        or birth_date
        or curp
        or bool(player.get("needs_review"))
        or float(player.get("confidence") or 0.0) > 0.0
    )


def _build_review_commit_validation(
    extraction: Dict[str, Any],
    validation: Optional[Dict[str, Any]],
    *,
    review_session: Optional[RegistrationReviewSession] = None,
) -> Dict[str, Any]:
    enriched = dict(validation or {})
    blockers: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []

    team = extraction.get("team") or {}
    players = list(extraction.get("players") or [])
    team_name = (team.get("name") or "").strip()
    if not team_name or team_name.lower() == "unknown team":
        blockers.append(
            {
                "code": "NO_TEAM_NAME",
                "field": "team.name",
                "message": "No pude identificar un nombre usable para el equipo.",
            }
        )

    valid_players = 0
    for idx, player in enumerate(players):
        player_number = idx + 1
        field_prefix = f"players[{idx}]"
        if not _player_has_usable_signal(player):
            continue

        name = (player.get("name") or "").strip()
        birth_date = (player.get("birth_date") or "").strip()
        raw_curp = (player.get("curp") or "").strip().upper()
        curp, curp_truncated = _normalize_curp_for_storage(raw_curp)
        confidence = float(player.get("confidence") or 0.0)
        needs_review = bool(player.get("needs_review"))

        if not name:
            blockers.append(
                {
                    "code": "PLAYER_MISSING_NAME",
                    "field": f"{field_prefix}.name",
                    "message": f"El jugador {player_number} no tiene nombre usable.",
                }
            )
        if not birth_date or _birth_date_has_two_digit_year(birth_date) or _parse_birth_date(birth_date) is None:
            blockers.append(
                {
                    "code": "PLAYER_INVALID_BIRTHDATE",
                    "field": f"{field_prefix}.birth_date",
                    "message": f"El jugador {player_number} tiene una fecha de nacimiento inválida o ambigua.",
                }
            )
        if raw_curp:
            if curp_truncated or len(curp or "") != 18:
                blockers.append(
                    {
                        "code": "CURP_TRUNCATED_OR_INVALID_SEVERE",
                        "field": f"{field_prefix}.curp",
                        "message": f"El CURP del jugador {player_number} está truncado o no es confiable.",
                    }
                )
        else:
            warnings.append(
                {
                    "code": "MISSING_OPTIONAL_CURP",
                    "field": f"{field_prefix}.curp",
                    "message": f"El jugador {player_number} no tiene CURP capturado.",
                }
            )

        if confidence < 0.7 or needs_review:
            warnings.append(
                {
                    "code": "LOW_CONFIDENCE_FIELD",
                    "field": field_prefix,
                    "message": f"El jugador {player_number} requiere revisión por baja confianza OCR.",
                }
            )

        if name and birth_date and not _birth_date_has_two_digit_year(birth_date) and _parse_birth_date(birth_date) is not None:
            valid_players += 1

    if valid_players == 0:
        blockers.append(
            {
                "code": "NO_VALID_PLAYERS",
                "field": "players",
                "message": "No hay jugadores válidos listos para captura.",
            }
        )

    manager = extraction.get("manager") or {}
    if not (manager.get("email") or "").strip():
        warnings.append(
            {
                "code": "MISSING_OPTIONAL_EMAIL",
                "field": "manager.email",
                "message": "El responsable no tiene email capturado.",
            }
        )
    if not (team.get("league") or "").strip():
        warnings.append(
            {
                "code": "UNCLEAR_LEAGUE",
                "field": "team.league",
                "message": "La liga no está clara en el draft actual.",
            }
        )
    if not (team.get("municipality") or "").strip():
        warnings.append(
            {
                "code": "UNCLEAR_MUNICIPALITY",
                "field": "team.municipality",
                "message": "El municipio no está claro en el draft actual.",
            }
        )

    if review_session is not None:
        if review_session.draft is None:
            blockers.append(
                {
                    "code": "DRAFT_MISSING",
                    "field": "draft",
                    "message": "La sesión no tiene draft activo para capturar.",
                }
            )
        if (
            str(review_session.status or "").strip().lower() == "committed"
            or review_session.committed_at is not None
            or review_session.committed_team_id is not None
        ):
            blockers.append(
                {
                    "code": "SESSION_ALREADY_COMMITTED",
                    "field": "session",
                    "message": "Esta sesión ya fue capturada previamente.",
                }
            )

    if bool(enriched.get("needs_review")):
        blockers.append(
            {
                "code": "REVIEW_NOT_READY",
                "field": "draft.validation",
                "message": "Todavía hay señales de revisión pendientes antes de capturar.",
            }
        )

    blockers = _dedupe_review_items(blockers)
    warnings = _dedupe_review_items(warnings)
    enriched["blockers"] = blockers
    enriched["warnings"] = warnings
    enriched["blocking_issue_count"] = len(blockers)
    enriched["warning_count"] = len(warnings)
    enriched["ready_to_commit"] = len(blockers) == 0
    enriched["capture_warning_count"] = len(warnings)
    enriched["capture_warning_messages"] = [item["message"] for item in warnings]
    return enriched


def _wants_html_response(request: Request) -> bool:
    accept = (request.headers.get("accept") or "").lower()
    return "text/html" in accept or "*/*" in accept


def _looks_like_asset_path(path: str) -> bool:
    clean = (path or "").strip().lower()
    if not clean:
        return False
    filename = clean.rsplit("/", 1)[-1]
    return "." in filename


def _render_not_found_page(request: Request) -> HTMLResponse:
    home_href = "/"
    attempted_path = escape(request.url.path or "/", quote=True)
    html = f"""
    <!DOCTYPE html>
    <html lang="es">
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Página no encontrada | sam.chat</title>
        <style>
            :root {{
                --ink: #0f172a;
                --muted: #5b6474;
                --line: rgba(148, 163, 184, 0.28);
                --bg-start: #07111f;
                --bg-end: #10324a;
                --card: rgba(255,255,255,0.96);
                --primary: #0f766e;
                --accent: #eab308;
            }}
            * {{ box-sizing: border-box; }}
            body {{
                margin: 0;
                min-height: 100vh;
                display: grid;
                place-items: center;
                font-family: "Segoe UI", "Helvetica Neue", sans-serif;
                color: var(--ink);
                background:
                    radial-gradient(circle at top left, rgba(234,179,8,0.18), transparent 28%),
                    radial-gradient(circle at bottom right, rgba(15,118,110,0.22), transparent 30%),
                    linear-gradient(140deg, var(--bg-start), var(--bg-end));
                padding: 24px;
            }}
            .card {{
                width: min(100%, 720px);
                background: var(--card);
                border: 1px solid var(--line);
                border-radius: 28px;
                padding: 32px;
                box-shadow: 0 24px 80px rgba(2, 8, 23, 0.35);
            }}
            .eyebrow {{
                display: inline-flex;
                gap: 8px;
                align-items: center;
                padding: 8px 12px;
                border-radius: 999px;
                background: rgba(15,118,110,0.1);
                color: var(--primary);
                font-size: 12px;
                font-weight: 700;
                letter-spacing: 0.08em;
                text-transform: uppercase;
            }}
            h1 {{
                margin: 18px 0 12px;
                font-size: clamp(2.4rem, 7vw, 4.5rem);
                line-height: 0.95;
                letter-spacing: -0.04em;
            }}
            p {{
                margin: 0;
                color: var(--muted);
                font-size: 1.02rem;
                line-height: 1.6;
            }}
            .path {{
                margin-top: 18px;
                padding: 14px 16px;
                border-radius: 16px;
                background: rgba(15, 23, 42, 0.04);
                border: 1px dashed rgba(148, 163, 184, 0.4);
                color: var(--ink);
                font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
                font-size: 0.95rem;
                word-break: break-word;
            }}
            .actions {{
                margin-top: 26px;
                display: flex;
                flex-wrap: wrap;
                gap: 12px;
            }}
            .btn {{
                text-decoration: none;
                border-radius: 14px;
                padding: 13px 18px;
                font-weight: 700;
                transition: transform 0.18s ease, box-shadow 0.18s ease, background 0.18s ease;
            }}
            .btn-primary {{
                background: linear-gradient(135deg, var(--accent), #f59e0b);
                color: #111827;
                box-shadow: 0 14px 28px rgba(245, 158, 11, 0.28);
            }}
            .btn-secondary {{
                background: rgba(15, 118, 110, 0.08);
                color: var(--primary);
                border: 1px solid rgba(15, 118, 110, 0.18);
            }}
            .btn:hover {{
                transform: translateY(-1px);
            }}
        </style>
    </head>
    <body>
        <main class="card">
            <div class="eyebrow">sam.chat · 404</div>
            <h1>Página no encontrada</h1>
            <p>La ruta que intentaste abrir no existe o ya no está disponible en esta instancia.</p>
            <div class="path">{attempted_path}</div>
            <div class="actions">
                <a class="btn btn-primary" href="{home_href}">Ir al inicio</a>
                <a class="btn btn-secondary" href="/assistant">Abrir assistant</a>
            </div>
        </main>
    </body>
    </html>
    """
    return HTMLResponse(content=html, status_code=404)


# FastAPI app
app = FastAPI(
    title="Copa Telmex Dashboard",
    description="View teams, players, and OCR registrations",
    version="1.0.0"
)


@app.exception_handler(404)
async def not_found_exception_handler(request: Request, exc: StarletteHTTPException):
    path = request.url.path or "/"
    if (
        request.method not in {"GET", "HEAD"}
        or path.startswith("/api/")
        or path.startswith("/ingress/")
        or _looks_like_asset_path(path)
        or not _wants_html_response(request)
    ):
        return JSONResponse(status_code=404, content={"detail": exc.detail or "Not Found"})
    return _render_not_found_page(request)

MODERN_UI_HEAD_INJECTION = """
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style id="samchat-modern-theme">
  :root {
    --sam-ink: #0f172a;
    --sam-muted: #475569;
    --sam-line: #dbe2ea;
    --sam-primary: #0f766e;
    --sam-accent: #1d4ed8;
    --sam-bg-start: #0f172a;
    --sam-bg-end: #123a59;
    --sam-surface: #ffffff;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    font-family: "Segoe UI", "Helvetica Neue", sans-serif !important;
    color: var(--sam-ink);
    background:
      radial-gradient(circle at 20% 12%, rgba(255,255,255,0.10), transparent 35%),
      linear-gradient(140deg, var(--sam-bg-start), var(--sam-bg-end)) !important;
    min-height: 100vh;
  }
  .container {
    max-width: 1220px !important;
    margin: 20px auto !important;
    background: var(--sam-surface) !important;
    border-radius: 18px !important;
    border: 1px solid rgba(255,255,255,0.35) !important;
    box-shadow: 0 20px 60px rgba(5, 12, 24, 0.30) !important;
  }
  h1, h2, h3 {
    color: var(--sam-ink) !important;
    letter-spacing: -0.01em;
  }
  p, small, label, td, li {
    color: var(--sam-muted);
  }
  nav {
    background: linear-gradient(110deg, #0b2539, #11354b) !important;
    border-radius: 16px 16px 0 0 !important;
    border: none !important;
  }
  .top-nav {
    margin: -24px -24px 18px;
    padding: 14px 20px;
    border-radius: 16px 16px 0 0;
    background: linear-gradient(110deg, #0b2539, #11354b);
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 16px;
    flex-wrap: wrap;
  }
  .top-nav-left, .top-nav-right {
    display: flex;
    align-items: center;
    gap: 12px;
    flex-wrap: wrap;
  }
  .brand-pill {
    color: #f8fafc !important;
    text-decoration: none;
    font-weight: 700;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    font-size: 0.75rem;
    border: 1px solid rgba(255,255,255,0.25);
    border-radius: 999px;
    padding: 8px 12px;
  }
  .top-links {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
  }
  .top-link {
    color: #dbeafe !important;
    text-decoration: none;
    padding: 8px 12px;
    border-radius: 10px;
    font-size: 0.95rem;
  }
  .top-link:hover {
    background: rgba(255,255,255,0.10);
    color: #ffffff !important;
  }
  .identity {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .identity-name {
    color: #f1f5f9 !important;
    font-size: 0.92rem;
  }
  .role-badge {
    color: #c7d2fe !important;
    background: rgba(79, 70, 229, 0.25);
    border: 1px solid rgba(199, 210, 254, 0.45);
    border-radius: 999px;
    padding: 3px 8px;
    font-size: 0.75rem;
    text-transform: uppercase;
    letter-spacing: 0.03em;
  }
  .logout-btn {
    text-decoration: none;
    background: #991b1b;
    color: #fff !important;
    padding: 8px 12px;
    border-radius: 10px;
    font-size: 0.9rem;
  }
  nav a {
    border-radius: 10px !important;
  }
  .btn, button, input[type="submit"], input[type="button"] {
    border-radius: 10px !important;
    border: 1px solid transparent;
    transition: all .18s ease;
  }
  /* Improve WCAG contrast for legacy bright button colors used inline across routes */
  a[style*="background: #4CAF50"], a[style*="background-color: #4CAF50"],
  button[style*="background: #4CAF50"], button[style*="background-color: #4CAF50"],
  input[style*="background: #4CAF50"], input[style*="background-color: #4CAF50"],
  a[style*="background: #2196F3"], a[style*="background-color: #2196F3"],
  button[style*="background: #2196F3"], button[style*="background-color: #2196F3"],
  input[style*="background: #2196F3"], input[style*="background-color: #2196F3"],
  a[style*="background: #FF9800"], a[style*="background-color: #FF9800"],
  button[style*="background: #FF9800"], button[style*="background-color: #FF9800"],
  input[style*="background: #FF9800"], input[style*="background-color: #FF9800"] {
    color: #0f172a !important;
    font-weight: 700;
  }
  button:hover, .btn:hover, input[type="submit"]:hover, input[type="button"]:hover {
    transform: translateY(-1px);
  }
  table {
    border-collapse: separate !important;
    border-spacing: 0 !important;
    border: 1px solid var(--sam-line) !important;
    border-radius: 12px !important;
    overflow: hidden !important;
    background: #fff !important;
  }
  th {
    background: linear-gradient(90deg, #0f766e, #0e7490) !important;
    color: #f8fafc !important;
    border-bottom: 1px solid rgba(255,255,255,0.12) !important;
  }
  td {
    border-bottom: 1px solid #edf2f7 !important;
  }
  tr:hover td {
    background: #f8fafc;
  }
  input[type="text"], input[type="email"], input[type="password"], input[type="number"], input[type="date"], select, textarea {
    border: 1px solid #cbd5e1 !important;
    border-radius: 10px !important;
    padding: 10px 12px !important;
    background: #fff;
  }
  input:focus, select:focus, textarea:focus {
    outline: none;
    border-color: var(--sam-accent) !important;
    box-shadow: 0 0 0 3px rgba(29,78,216,0.14);
  }
  /* 1) Form UX polish */
  form {
    display: block;
  }
  .form-group {
    margin-bottom: 18px !important;
  }
  .form-group label {
    color: var(--sam-ink) !important;
    font-weight: 600 !important;
    margin-bottom: 6px !important;
  }
  .form-group small {
    color: #64748b !important;
    font-size: 12px !important;
    line-height: 1.4;
  }
  details {
    border: 1px solid var(--sam-line);
    border-radius: 10px;
  }
  details > summary {
    color: var(--sam-ink) !important;
  }

  /* 2) Unified alert feedback blocks */
  .sam-alert {
    border-radius: 10px !important;
    padding: 12px 14px !important;
    margin-bottom: 16px !important;
    border-left-width: 5px !important;
    border-left-style: solid !important;
    box-shadow: 0 2px 10px rgba(15, 23, 42, 0.06);
  }
  .sam-alert-error {
    background: #fef2f2 !important;
    color: #991b1b !important;
    border: 1px solid #fecaca !important;
    border-left-color: #dc2626 !important;
  }
  .sam-alert-success {
    background: #f0fdf4 !important;
    color: #14532d !important;
    border: 1px solid #bbf7d0 !important;
    border-left-color: #16a34a !important;
  }
  .sam-alert-warning {
    background: #fffbeb !important;
    color: #92400e !important;
    border: 1px solid #fde68a !important;
    border-left-color: #d97706 !important;
  }

  /* 3) Mobile-first table usability */
  .sam-table-wrap {
    width: 100%;
    overflow-x: auto;
    border-radius: 12px;
    border: 1px solid var(--sam-line);
    background: #fff;
  }
  .sam-table-wrap table {
    min-width: 920px;
    margin: 0 !important;
    border: none !important;
    border-radius: 0 !important;
  }
  .sam-table-wrap::-webkit-scrollbar {
    height: 10px;
  }
  .sam-table-wrap::-webkit-scrollbar-thumb {
    background: #94a3b8;
    border-radius: 999px;
  }
  @media (max-width: 768px) {
    body { padding: 10px !important; }
    .container { margin: 8px auto !important; border-radius: 14px !important; padding: 14px !important; }
    .top-nav { margin: -14px -14px 14px; padding: 12px; border-radius: 14px 14px 0 0; }
    table { font-size: 13px; white-space: nowrap; }
    .btn, button, input[type="submit"], input[type="button"] {
      width: 100%;
      margin-top: 6px;
    }
    .actions {
      display: grid !important;
      grid-template-columns: 1fr !important;
      gap: 8px !important;
    }
  }
</style>
<script>
  document.addEventListener('DOMContentLoaded', function() {
    document.querySelectorAll('table').forEach(function(table) {
      if (table.closest('.sam-table-wrap')) return;
      const wrapper = document.createElement('div');
      wrapper.className = 'sam-table-wrap';
      table.parentNode.insertBefore(wrapper, table);
      wrapper.appendChild(table);
    });

    document.querySelectorAll('div[style*=\"#f8d7da\"], div[style*=\"#721c24\"], div[style*=\"#dc3545\"]').forEach(function(el) {
      el.classList.add('sam-alert', 'sam-alert-error');
    });
    document.querySelectorAll('div[style*=\"#d4edda\"], div[style*=\"#155724\"], div[style*=\"#28a745\"]').forEach(function(el) {
      el.classList.add('sam-alert', 'sam-alert-success');
    });
    document.querySelectorAll('div[style*=\"#fff3cd\"], div[style*=\"#856404\"], div[style*=\"#ffc107\"]').forEach(function(el) {
      el.classList.add('sam-alert', 'sam-alert-warning');
    });
  });
</script>
"""

# Add session middleware for authentication
session_secret_key = _require_session_secret_key()
session_https_only = (os.getenv("APP_URL") or "https://sam.chat").lower().startswith("https://")
app.add_middleware(
    SessionMiddleware,
    secret_key=session_secret_key,
    session_cookie="samchat_session",
    max_age=86400,  # 24 hours
    same_site="lax",
    https_only=session_https_only,
)


def _inject_modern_theme(html: str) -> str:
    if "samchat-modern-theme" in html:
        return html
    if "</head>" in html:
        return html.replace("</head>", f"{MODERN_UI_HEAD_INJECTION}</head>", 1)
    return html


@app.middleware("http")
async def modernize_html_middleware(request: Request, call_next):
    response = await call_next(request)
    content_type = (response.headers.get("content-type") or "").lower()

    # Keep static SPA bundles untouched.
    if isinstance(response, FileResponse) or "text/html" not in content_type:
        return response

    body = b""
    async for chunk in response.body_iterator:
        body += chunk

    try:
        html = body.decode("utf-8")
    except UnicodeDecodeError:
        return _rebuild_buffered_response(response, body)

    themed_html = _inject_modern_theme(html)
    if themed_html == html:
        return _rebuild_buffered_response(response, body)

    return _rebuild_buffered_response(response, themed_html.encode("utf-8"), body_changed=True)


def _rebuild_buffered_response(response, body: bytes, *, body_changed: bool = False) -> Response:
    """Rebuild a drained response without collapsing repeated raw headers."""
    rebuilt = Response(
        content=body,
        status_code=response.status_code,
        background=response.background,
    )
    raw_headers = list(response.raw_headers)
    if body_changed:
        raw_headers = [
            (name, value)
            for name, value in raw_headers
            if name.lower() != b"content-length"
        ]
        raw_headers.append((b"content-length", str(len(body)).encode("ascii")))
    rebuilt.raw_headers = raw_headers
    return rebuilt

allowed_origins = _build_allowed_origins()

# Add CORS middleware to allow requests from the public app domain.
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Accept", "Authorization", "Content-Type", "X-Requested-With"],
)


def _raise_dashboard_internal_error(action: str) -> None:
    """Return a stable error without exposing SQL values or constraint details."""
    current_error = sys.exc_info()[1]
    if isinstance(current_error, DBAPIError):
        logger.error("%s: database operation failed", action)
    else:
        logger.exception("%s: unexpected failure", action)
    raise HTTPException(
        status_code=500,
        detail="No se pudo completar la operación. Intenta nuevamente.",
    ) from None

# Mount static files directory for photos
photos_dir = Path(__file__).parent / "photos"
photos_dir.mkdir(parents=True, exist_ok=True)
PUBLIC_PHOTO_TOP_LEVEL_DIRS = frozenset({"public"})


def _normalized_photo_asset_path(asset_path: str) -> str:
    return unquote(str(asset_path or "")).replace("\\", "/").lstrip("/")


def _resolve_photo_asset_path(asset_path: str) -> Path:
    normalized = _normalized_photo_asset_path(asset_path)
    if not normalized:
        raise HTTPException(status_code=404, detail="Photo not found")

    root = photos_dir.resolve()
    candidate = (root / normalized).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        raise HTTPException(status_code=404, detail="Photo not found")

    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="Photo not found")
    return candidate


def _is_requested_public_photo_asset(asset_path: str) -> bool:
    parts = [part for part in _normalized_photo_asset_path(asset_path).split("/") if part]
    return bool(parts) and parts[0] in PUBLIC_PHOTO_TOP_LEVEL_DIRS


def _is_canonical_review_photo_asset(asset_path: str) -> bool:
    parts = [part for part in _normalized_photo_asset_path(asset_path).split("/") if part]
    return (
        len(parts) >= 4
        and parts[0] == "review_sessions"
        and parts[2] == "canonical_shadow"
    )


def _is_public_photo_asset_path(asset_path: Path) -> bool:
    for public_dir_name in PUBLIC_PHOTO_TOP_LEVEL_DIRS:
        public_root = (photos_dir / public_dir_name).resolve()
        try:
            asset_path.relative_to(public_root)
            return True
        except ValueError:
            continue
    return False


@app.get("/photos/{path:path}", include_in_schema=False)
async def serve_photo_asset(path: str, request: Request):
    requested_public_asset = _is_requested_public_photo_asset(path)
    canonical_review_asset = _is_canonical_review_photo_asset(path)
    asset_path = _resolve_photo_asset_path(path)
    public_asset = _is_public_photo_asset_path(asset_path)
    if requested_public_asset and not public_asset:
        raise HTTPException(status_code=404, detail="Photo not found")
    if canonical_review_asset:
        _ensure_registration_review_access(request)
    elif not public_asset:
        _ensure_legacy_copa_dashboard_access(request)
    return FileResponse(str(asset_path))

# Mount static files directory for general static files (manuals, etc.)
static_dir = Path(__file__).parent / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# Include expense management routes
app.include_router(auth_router)  # Auth routes (login/logout) - no prefix
app.include_router(admin_router)
if operations_analytics_router is not None:
    app.include_router(operations_analytics_router)
app.include_router(webhook_router, prefix="/ingress")
app.include_router(user_router)
app.include_router(support_router)
app.include_router(assistant_router)
# Some deployments/proxies only expose the app under `/copa-america/*`.
# Mount assistant API there too so the SPA can reach it reliably.
app.include_router(assistant_router, prefix="/copa-america", include_in_schema=False)

# Database setup: use DATABASE_URL in production (e.g. sam.chat) so the same DB backs /admin/gastos
_db_url = _require_database_url_for_runtime()
if _db_url.startswith("postgresql://") and "+asyncpg" not in _db_url:
    _db_url = _db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
db_url = _db_url
db_engine = create_async_engine(
    db_url,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True
)
async_session_maker = async_sessionmaker(
    db_engine,
    class_=AsyncSession,
    expire_on_commit=False
)

# Set session maker for expense admin, webhook, user, auth routes, and dependencies
set_admin_session_maker(async_session_maker)
set_webhook_session_maker(async_session_maker)
set_sat_background_session_maker(async_session_maker)
set_user_session_maker(async_session_maker)
set_auth_session_maker(async_session_maker)
set_dependencies_session_maker(async_session_maker)

# Templates directory
templates_dir = Path(__file__).parent / "templates"
templates_dir.mkdir(exist_ok=True)
templates = Jinja2Templates(directory=str(templates_dir))

public_app_url = (os.getenv("APP_URL") or "https://sam.chat").rstrip("/")
copa_america_frontend_url = os.getenv("COPA_AMERICA_FRONTEND_URL", f"{public_app_url}/copa-america/")
copa_telmex_frontend_url = os.getenv("COPA_TELMEX_FRONTEND_URL", f"{public_app_url}/telmex/")

review_uploads_dir = photos_dir / "review_sessions"
review_uploads_dir.mkdir(parents=True, exist_ok=True)
ctt_layout_path = Path(__file__).parent / "config" / "layout_ctt_2026.json"
_ctt_layout_cache: Optional[Dict[str, Any]] = None


def _load_ctt_layout() -> Optional[Dict[str, Any]]:
    global _ctt_layout_cache
    if _ctt_layout_cache is not None:
        return _ctt_layout_cache
    if not ctt_layout_path.exists():
        return None
    try:
        _ctt_layout_cache = json.loads(ctt_layout_path.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("Could not load CTT layout from %s", ctt_layout_path, exc_info=True)
        _ctt_layout_cache = None
    return _ctt_layout_cache


def _review_pii_path_inventory() -> List[Dict[str, Any]]:
    return build_review_pii_path_inventory(
        photos_root=photos_dir,
        review_uploads_dir=review_uploads_dir,
    )


def _build_review_retention_session_summaries(
    sessions: Optional[Iterable[RegistrationReviewSession]],
) -> List[Dict[str, Any]]:
    summaries: List[Dict[str, Any]] = []
    for review_session in list(sessions or []):
        draft = getattr(review_session, "draft", None)
        updated_at = getattr(review_session, "updated_at", None) or getattr(review_session, "started_at", None)
        summaries.append(
            {
                "session_id": str(getattr(review_session, "id", "") or ""),
                "status": str(getattr(review_session, "status", "") or ""),
                "started_at": getattr(review_session, "started_at", None),
                "updated_at": updated_at,
                "has_assets": bool(list(getattr(review_session, "assets", []) or [])),
                "has_draft": draft is not None,
            }
        )
    return summaries


def _plan_registration_review_retention(
    *,
    sessions: Optional[Iterable[RegistrationReviewSession]] = None,
    now: Optional[datetime] = None,
    dry_run: Optional[bool] = None,
) -> Dict[str, Any]:
    return plan_review_data_retention(
        photos_root=photos_dir,
        review_uploads_dir=review_uploads_dir,
        session_summaries=_build_review_retention_session_summaries(sessions),
        now=now,
        dry_run=REVIEW_PURGE_DRY_RUN if dry_run is None else dry_run,
        apply_changes=False,
    )


review_ocr_runner = LocalOCRRunner(
    repo_root=Path(__file__).parent,
    timeout_seconds=max(10.0, float(os.getenv("LOCAL_OCR_TIMEOUT_SECONDS", "180"))),
)
review_ocr_provider = (os.getenv("REVIEW_OCR_PROVIDER") or "").strip().lower()
review_anthropic_key = (os.getenv("ANTHROPIC_API_KEY") or "").strip()
review_openai_key = (os.getenv("OPENAI_API_KEY") or "").strip()
if not review_ocr_provider:
    if review_anthropic_key:
        review_ocr_provider = "anthropic"
    elif review_openai_key:
        review_ocr_provider = "openai"
    else:
        review_ocr_provider = "local"

review_ocr_agent: Optional[OCRAgent] = None
if review_anthropic_key:
    try:
        review_ocr_agent = OCRAgent(anthropic_api_key=review_anthropic_key)
    except Exception:
        logger.warning("Could not initialize review OCRAgent; local fallback only", exc_info=True)


def _minimal_review_extraction(note: Optional[str] = None) -> Dict[str, Any]:
    return {
        "team": {
            "name": "",
            "category": None,
            "gender": None,
            "league": None,
            "municipality": None,
            "state": None,
            "confidence": 0.0,
        },
        "responsables": [],
        "manager": {
            "name": "",
            "role": "delegado",
            "phone": None,
            "email": None,
            "confidence": 0.0,
        },
        "players": [],
        "is_front": True,
        "overall_confidence": 0.0,
        "form_type": "roster",
        "notes": note,
    }


def _normalize_review_extraction(payload: Optional[Dict[str, Any]], note: Optional[str] = None) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return _minimal_review_extraction(note=note)
    try:
        normalized = RegistrationFormExtraction.model_validate(payload).model_dump(mode="json")
    except Exception as exc:
        fallback_note = note or f"No pude normalizar la extracción OCR: {exc}"
        return _minimal_review_extraction(note=fallback_note)
    if note:
        merged_note = " | ".join(part for part in [normalized.get("notes"), note] if part)
        normalized["notes"] = merged_note
    return normalized


def _review_extraction_has_signal(extraction: Dict[str, Any]) -> bool:
    team_name = ((extraction.get("team") or {}).get("name") or "").strip()
    if team_name and team_name.lower() != "unknown team":
        return True
    return bool(extraction.get("players"))


def _backend_unavailable_issues(raw_payload: Optional[Dict[str, Any]]) -> List[str]:
    raw = raw_payload if isinstance(raw_payload, dict) else {}
    backend = raw.get("backend") if isinstance(raw.get("backend"), dict) else {}
    issues: List[str] = []

    for key, label in (
        ("qianfan", "Qianfan"),
        ("moondream", "Moondream"),
        ("trocr", "TrOCR"),
    ):
        status = backend.get(key)
        if not isinstance(status, dict):
            continue
        if not status.get("configured", True) or status.get("available"):
            continue
        detail = str(status.get("error") or "modelo no disponible").strip()
        issues.append(f"{label}: {detail}")
    return issues


def _backend_failure_note(raw_payload: Optional[Dict[str, Any]]) -> Optional[str]:
    issues = _backend_unavailable_issues(raw_payload)
    if not issues:
        return None
    return "OCR local no disponible todavía. El servidor no pudo cargar sus modelos: " + " | ".join(issues)


def _review_public_path(image_path: str) -> str:
    path = Path(image_path)
    try:
        relative = path.relative_to(photos_dir)
        return f"/photos/{relative.as_posix()}"
    except ValueError:
        return image_path


def _storage_relative_path(image_path: str) -> str:
    path = Path(image_path)
    try:
        return path.relative_to(Path(__file__).parent).as_posix()
    except ValueError:
        return image_path


def _public_storage_path(image_path: Optional[str]) -> Optional[str]:
    clean = str(image_path or "").strip().replace("\\", "/")
    if not clean:
        return None
    if clean.startswith("/photos/"):
        return clean
    if clean.startswith("/"):
        return clean
    return f"/{clean}"


def _session_asset_payload(asset: RegistrationReviewAsset) -> Dict[str, Any]:
    return {
        "id": str(asset.id),
        "page_index": asset.page_index,
        "image_path": asset.image_path,
        "image_url": _review_public_path(asset.image_path),
        "width": asset.width,
        "height": asset.height,
        "sha256": asset.sha256,
    }


def _get_review_extraction(draft: Optional[RegistrationReviewDraft]) -> Dict[str, Any]:
    if not draft:
        return _minimal_review_extraction()
    review_edits = draft.review_edits if isinstance(draft.review_edits, dict) else None
    extraction = draft.extraction if isinstance(draft.extraction, dict) else None
    return _normalize_review_extraction(review_edits or extraction)


def _build_review_validation(
    extraction: Dict[str, Any],
    raw_payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    team = extraction.get("team") or {}
    players = extraction.get("players") or []
    issues: List[Dict[str, str]] = []
    player_rows: List[Dict[str, Any]] = []

    for backend_issue in _backend_unavailable_issues(raw_payload):
        issues.append({"level": "error", "message": backend_issue})

    team_name = (team.get("name") or "").strip()
    if not team_name or team_name.lower() == "unknown team":
        issues.append({"level": "error", "message": "Falta confirmar el nombre del equipo."})

    if not players:
        issues.append({"level": "error", "message": "No se detectaron jugadores en la cédula."})

    low_confidence_rows = 0
    review_rows = 0
    long_curp_rows = 0
    ambiguous_birth_year_rows = 0
    invalid_birth_date_rows = 0
    for idx, player in enumerate(players, 1):
        row_issues: List[str] = []
        confidence = float(player.get("confidence") or 0.0)
        name = (player.get("name") or "").strip()
        raw_curp = (player.get("curp") or "").strip().upper()
        curp, curp_truncated = _normalize_curp_for_storage(raw_curp)
        birth_date = (player.get("birth_date") or "").strip()
        needs_review = bool(player.get("needs_review"))

        if not name:
            row_issues.append("Nombre vacío")
        if not birth_date:
            row_issues.append("Falta fecha")
        else:
            if _birth_date_has_two_digit_year(birth_date):
                row_issues.append("Fecha con año de 2 dígitos")
                ambiguous_birth_year_rows += 1
            elif _parse_birth_date(birth_date) is None:
                row_issues.append("Fecha no reconocida")
                invalid_birth_date_rows += 1
        if not raw_curp:
            row_issues.append("Falta CURP")
        elif curp_truncated:
            row_issues.append("CURP mayor a 18; se truncará al capturar")
            long_curp_rows += 1
        elif len(curp or "") != 18:
            row_issues.append("CURP incompleto")
        if confidence < 0.7:
            row_issues.append("Confianza baja")
            low_confidence_rows += 1
        if needs_review:
            row_issues.append("Marcado para revisión")
            review_rows += 1

        player_rows.append(
            {
                "index": idx,
                "confidence": confidence,
                "needs_review": needs_review,
                "issues": row_issues,
                "warning_count": len(row_issues),
            }
        )

    if low_confidence_rows:
        issues.append(
            {
                "level": "warning",
                "message": f"{low_confidence_rows} jugador(es) con confianza OCR menor a 0.70.",
            }
        )
    if review_rows:
        issues.append(
            {
                "level": "warning",
                "message": f"{review_rows} jugador(es) marcados para revisión manual.",
            }
        )
    if long_curp_rows:
        issues.append(
            {
                "level": "warning",
                "message": f"{long_curp_rows} CURP(s) superan 18 caracteres y se truncarán al capturar.",
            }
        )
    if ambiguous_birth_year_rows:
        issues.append(
            {
                "level": "warning",
                "message": f"{ambiguous_birth_year_rows} fecha(s) usan año de 2 dígitos. Conviene confirmarlas antes de capturar.",
            }
        )
    if invalid_birth_date_rows:
        issues.append(
            {
                "level": "warning",
                "message": f"{invalid_birth_date_rows} fecha(s) no tienen un formato reconocible. Se guardarán sin fecha si no se corrigen.",
            }
        )

    capture_warning_messages: List[str] = []
    for issue in issues:
        message = str(issue.get("message") or "").strip()
        if "se truncarán al capturar" in message or "año de 2 dígitos" in message or "Se guardarán sin fecha" in message:
            capture_warning_messages.append(message)

    return {
        "needs_review": any(item["level"] == "error" for item in issues) or review_rows > 0,
        "needs_human_review": any(item["level"] == "error" for item in issues) or review_rows > 0,
        "issue_count": len(issues),
        "player_count": len(players),
        "review_player_count": review_rows,
        "low_confidence_player_count": low_confidence_rows,
        "long_curp_player_count": long_curp_rows,
        "ambiguous_birth_year_player_count": ambiguous_birth_year_rows,
        "invalid_birth_date_player_count": invalid_birth_date_rows,
        "capture_warning_count": len(capture_warning_messages),
        "capture_warning_messages": capture_warning_messages,
        "issues": issues,
        "player_rows": player_rows,
    }


def _merge_team_payload(primary: Dict[str, Any], candidate: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(primary or {})
    for key in ("name", "category", "gender", "league", "municipality", "state"):
        current = (merged.get(key) or "").strip() if isinstance(merged.get(key), str) else merged.get(key)
        incoming = (candidate.get(key) or "").strip() if isinstance(candidate.get(key), str) else candidate.get(key)
        if key == "name" and current and str(current).lower() == "unknown team":
            current = None
        if not current and incoming:
            merged[key] = incoming
    merged["confidence"] = max(float(primary.get("confidence") or 0.0), float(candidate.get("confidence") or 0.0))
    return merged


def _merge_manager_payload(primary: Optional[Dict[str, Any]], candidate: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not primary and not candidate:
        return None
    merged = dict(primary or {})
    for key in ("name", "role", "phone", "email"):
        current = (merged.get(key) or "").strip() if isinstance(merged.get(key), str) else merged.get(key)
        incoming = (candidate or {}).get(key)
        if isinstance(incoming, str):
            incoming = incoming.strip()
        if not current and incoming:
            merged[key] = incoming
    merged["confidence"] = max(float((primary or {}).get("confidence") or 0.0), float((candidate or {}).get("confidence") or 0.0))
    return merged if any(merged.get(k) for k in ("name", "phone", "email")) else None


def _player_identity_key(player: Dict[str, Any]) -> Tuple[str, str]:
    curp = (player.get("curp") or "").strip().upper()
    if curp:
        return ("curp", curp)
    name = " ".join((player.get("name") or "").upper().split())
    birth_date = (player.get("birth_date") or "").strip()
    return ("name_birth", f"{name}|{birth_date}")


def _build_page_layout_regions(
    extraction: Dict[str, Any],
    *,
    raw_payload: Dict[str, Any],
    page_index: int,
    asset_width: int,
    asset_height: int,
    player_offset: int,
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    overlays: List[Dict[str, Any]] = []
    player_page_map: Dict[str, int] = {}

    def add_overlay(
        label: str,
        bbox: Optional[Dict[str, Any]],
        kind: str,
        player_index: Optional[int] = None,
        field_key: Optional[str] = None,
    ) -> None:
        if not bbox:
            return
        width = max(float(asset_width or 1), 1.0)
        height = max(float(asset_height or 1), 1.0)
        overlays.append(
            {
                "label": label,
                "kind": kind,
                "player_index": player_index,
                "field_key": field_key,
                "page_index": page_index,
                "x": int(bbox.get("x") or 0),
                "y": int(bbox.get("y") or 0),
                "width": int(bbox.get("width") or 0),
                "height": int(bbox.get("height") or 0),
                "x_pct": round((float(bbox.get("x") or 0) / width) * 100, 3),
                "y_pct": round((float(bbox.get("y") or 0) / height) * 100, 3),
                "width_pct": round((float(bbox.get("width") or 0) / width) * 100, 3),
                "height_pct": round((float(bbox.get("height") or 0) / height) * 100, 3),
            }
        )

    qianfan_layout = raw_payload.get("layout_qianfan") if isinstance(raw_payload, dict) else None
    if isinstance(qianfan_layout, dict):
        header = qianfan_layout.get("header") if isinstance(qianfan_layout.get("header"), dict) else {}
        header_bbox = header.get("bbox")
        if isinstance(header_bbox, dict):
            add_overlay(
                "Encabezado",
                {
                    "x": int((float(header_bbox.get("x") or 0) / 1000.0) * asset_width),
                    "y": int((float(header_bbox.get("y") or 0) / 1000.0) * asset_height),
                    "width": int((float(header_bbox.get("width") or 0) / 1000.0) * asset_width),
                    "height": int((float(header_bbox.get("height") or 0) / 1000.0) * asset_height),
                },
                "header",
            )
        for idx, player_payload in enumerate(qianfan_layout.get("players") or [], 1):
            if not isinstance(player_payload, dict):
                continue
            global_idx = player_offset + idx
            for source_key, kind, label, field_key in (
                ("row_bbox", "row", f"J{global_idx} fila", None),
                ("photo_bbox", "photo", f"J{global_idx} foto", "photo"),
                ("name_bbox", "field", f"J{global_idx} nombre", "name"),
                ("birth_date_bbox", "field", f"J{global_idx} fecha", "birth_date"),
                ("curp_bbox", "field", f"J{global_idx} CURP", "curp"),
            ):
                bbox = player_payload.get(source_key)
                if not isinstance(bbox, dict):
                    continue
                add_overlay(
                    label,
                    {
                        "x": int((float(bbox.get("x") or 0) / 1000.0) * asset_width),
                        "y": int((float(bbox.get("y") or 0) / 1000.0) * asset_height),
                        "width": int((float(bbox.get("width") or 0) / 1000.0) * asset_width),
                        "height": int((float(bbox.get("height") or 0) / 1000.0) * asset_height),
                    },
                    kind,
                    global_idx,
                    field_key,
                )
            player_page_map[str(global_idx)] = page_index

    players = extraction.get("players") or []
    for local_idx, player in enumerate(players, 1):
        global_idx = player_offset + local_idx
        player_page_map.setdefault(str(global_idx), page_index)
        photo_region = player.get("photo_region") if isinstance(player, dict) else None
        if isinstance(photo_region, dict):
            add_overlay(f"J{global_idx} foto", photo_region, "photo", global_idx, "photo")

    return overlays, player_page_map


def _merge_review_extractions(page_results: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], Dict[str, Any], str, Dict[str, Any]]:
    merged = _minimal_review_extraction()
    notes: List[str] = []
    confidences: List[float] = []
    page_raw: List[Dict[str, Any]] = []
    layout_regions: Dict[str, Any] = {"pages": {}, "player_page_map": {}}
    merged_players: List[Dict[str, Any]] = []
    player_offset = 0

    for page_result in page_results:
        extraction = page_result["extraction"]
        raw_payload = page_result["raw"]
        asset = page_result["asset"]
        page_index = int(asset.get("page_index") or len(page_raw) + 1)

        merged["team"] = _merge_team_payload(merged.get("team") or {}, extraction.get("team") or {})
        merged["manager"] = _merge_manager_payload(merged.get("manager"), extraction.get("manager"))

        page_players = list(extraction.get("players") or [])
        overlays, page_map = _build_page_layout_regions(
            extraction,
            raw_payload=raw_payload or {},
            page_index=page_index,
            asset_width=int(asset.get("width") or 1),
            asset_height=int(asset.get("height") or 1),
            player_offset=player_offset,
        )
        layout_regions["pages"][str(page_index)] = overlays
        layout_regions["player_page_map"].update(page_map)
        player_offset += len(page_players)
        merged_players.extend(page_players)

        confidence_value = float(extraction.get("overall_confidence") or 0.0)
        if confidence_value:
            confidences.append(confidence_value)
        note = (extraction.get("notes") or "").strip()
        if note:
            notes.append(f"P{page_index}: {note}")
        page_raw.append(
            {
                "page_index": page_index,
                "raw": raw_payload,
                "player_count": len(page_players),
            }
        )

    deduped_players: List[Dict[str, Any]] = []
    seen_players: Dict[Tuple[str, str], int] = {}
    retained_source_indices: List[int] = []
    for original_index, player in enumerate(merged_players, 1):
        key = _player_identity_key(player)
        if key[1] and key in seen_players:
            existing_index = seen_players[key]
            existing_player = deduped_players[existing_index]
            if float(player.get("confidence") or 0.0) > float(existing_player.get("confidence") or 0.0):
                deduped_players[existing_index] = player
                retained_source_indices[existing_index] = original_index
            continue
        seen_players[key] = len(deduped_players)
        deduped_players.append(player)
        retained_source_indices.append(original_index)

    original_page_map = dict(layout_regions.get("player_page_map") or {})
    layout_regions["player_page_map"] = {
        str(deduped_index): original_page_map[str(original_index)]
        for deduped_index, original_index in enumerate(retained_source_indices, 1)
        if str(original_index) in original_page_map
    }

    merged["players"] = deduped_players
    merged["overall_confidence"] = round(sum(confidences) / len(confidences), 4) if confidences else 0.0
    merged["notes"] = " | ".join(notes) if notes else merged.get("notes")
    provider = str(page_results[0].get("provider") or "local") if page_results else "local"
    raw_payload = {
        "provider": provider,
        "page_count": len(page_results),
        "pages": page_raw,
    }
    return _normalize_review_extraction(merged), raw_payload, provider, layout_regions


def _reindex_layout_regions(layout_regions: Dict[str, Any], player_offset: int) -> Dict[str, Any]:
    adjusted = {"pages": {}, "player_page_map": {}}
    for page_key, overlays in dict(layout_regions.get("pages") or {}).items():
        adjusted_overlays: List[Dict[str, Any]] = []
        for item in overlays or []:
            updated = dict(item)
            if updated.get("player_index") is not None:
                try:
                    updated["player_index"] = int(updated["player_index"]) + player_offset
                    label = str(updated.get("label") or "")
                    if label.startswith("J"):
                        suffix = label.split(" ", 1)[1] if " " in label else ""
                        updated["label"] = f"J{updated['player_index']}" + (f" {suffix}" if suffix else "")
                except Exception:
                    pass
            adjusted_overlays.append(updated)
        adjusted["pages"][str(page_key)] = adjusted_overlays
    for key, value in dict(layout_regions.get("player_page_map") or {}).items():
        try:
            adjusted["player_page_map"][str(int(key) + player_offset)] = value
        except Exception:
            adjusted["player_page_map"][str(key)] = value
    return adjusted


def _merge_layout_regions(base_layout: Dict[str, Any], incoming_layout: Dict[str, Any], player_offset: int) -> Dict[str, Any]:
    merged = {
        "pages": dict((base_layout or {}).get("pages") or {}),
        "player_page_map": dict((base_layout or {}).get("player_page_map") or {}),
    }
    adjusted_incoming = _reindex_layout_regions(incoming_layout or {}, player_offset)
    merged["pages"].update(adjusted_incoming.get("pages") or {})
    merged["player_page_map"].update(adjusted_incoming.get("player_page_map") or {})
    return merged


def _append_review_pages_to_extraction(
    base_extraction: Dict[str, Any],
    *,
    incoming_extraction: Dict[str, Any],
    incoming_raw: Dict[str, Any],
    incoming_layout: Dict[str, Any],
    existing_layout: Dict[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    merged = _normalize_review_extraction(base_extraction)
    merged["team"] = _merge_team_payload(merged.get("team") or {}, incoming_extraction.get("team") or {})
    merged["manager"] = _merge_manager_payload(merged.get("manager"), incoming_extraction.get("manager"))
    base_players = list(merged.get("players") or [])
    merged["players"] = base_players + list(incoming_extraction.get("players") or [])
    merged["overall_confidence"] = max(
        float(merged.get("overall_confidence") or 0.0),
        float(incoming_extraction.get("overall_confidence") or 0.0),
    )
    incoming_note = (incoming_extraction.get("notes") or "").strip()
    if incoming_note:
        merged["notes"] = " | ".join(part for part in [merged.get("notes"), incoming_note] if part)

    merged_layout = _merge_layout_regions(existing_layout or {}, incoming_layout or {}, len(base_players))
    combined_raw = dict(incoming_raw or {})
    existing_pages = list((incoming_raw or {}).get("pages") or [])
    if existing_pages:
        combined_raw["pages"] = existing_pages
    return _normalize_review_extraction(merged), combined_raw, merged_layout


def _load_review_asset_images(assets: List[RegistrationReviewAsset]) -> Dict[int, Image.Image]:
    loaded: Dict[int, Image.Image] = {}
    for asset in assets:
        try:
            loaded[int(asset.page_index)] = Image.open(asset.image_path).convert("RGB")
        except Exception:
            logger.warning("Could not load review asset image %s", asset.image_path, exc_info=True)
    return loaded


def _estimate_ctt_photo_region(
    *,
    page_index: int,
    page_player_index: int,
    image_size: Tuple[int, int],
) -> Optional[Dict[str, Any]]:
    layout = _load_ctt_layout()
    if not layout:
        return None

    page_side = "front" if int(page_index) <= 1 else "back"
    cards = (layout.get("pages") or {}).get(page_side, {}).get("cards") or {}
    player_cards = [(name, fields) for name, fields in cards.items() if str(name).startswith("jugador_")]
    if page_player_index < 1 or page_player_index > len(player_cards):
        return None

    _, fields = player_cards[page_player_index - 1]
    anchors = [fields.get("nombre"), fields.get("apellidos"), fields.get("nacimiento"), fields.get("curp")]
    anchors = [item for item in anchors if isinstance(item, dict)]
    if not anchors:
        return None

    left_anchor = min(float(item.get("x") or 0.0) for item in anchors)
    top_anchor = min(float(item.get("y") or 0.0) for item in anchors)
    bottom_anchor = max(float(item.get("y") or 0.0) + float(item.get("h") or 0.0) for item in anchors)

    left = max(0.01, left_anchor - 0.20)
    right = max(left + 0.12, left_anchor - 0.012)
    top = max(0.01, top_anchor - 0.005)
    bottom = min(0.99, bottom_anchor + 0.005)

    image_width, image_height = image_size
    x = int(round(left * image_width))
    y = int(round(top * image_height))
    width = int(round((right - left) * image_width))
    height = int(round((bottom - top) * image_height))
    if width < 40 or height < 40:
        return None
    return {"x": x, "y": y, "width": width, "height": height, "confidence": 0.45}


def _resolve_review_photo_region(
    *,
    player_payload: Dict[str, Any],
    page_index: int,
    page_player_index: int,
    image_size: Tuple[int, int],
) -> Tuple[Optional[Dict[str, Any]], str]:
    photo_region = player_payload.get("photo_region") if isinstance(player_payload, dict) else None
    if isinstance(photo_region, dict):
        return photo_region, "detected"

    estimated = _estimate_ctt_photo_region(
        page_index=page_index,
        page_player_index=page_player_index,
        image_size=image_size,
    )
    if estimated:
        return estimated, "ctt_estimated"
    return None, "heuristic"


def _build_review_photo_artifacts(
    *,
    team_id: Any,
    players_payload: List[Dict[str, Any]],
    assets: List[RegistrationReviewAsset],
    layout_regions: Dict[str, Any],
) -> Dict[int, Dict[str, Any]]:
    photos_dir = Path(__file__).parent / "photos" / "players" / str(team_id)
    photos_dir.mkdir(parents=True, exist_ok=True)
    loaded_pages = _load_review_asset_images(assets)
    player_page_map = dict((layout_regions or {}).get("player_page_map") or {})
    artifacts: Dict[int, Dict[str, Any]] = {}
    page_player_counters: Dict[int, int] = {}

    for idx, player_payload in enumerate(players_payload, 1):
        page_index = int(player_page_map.get(str(idx)) or 1)
        source_image = loaded_pages.get(page_index)
        page_player_counters[page_index] = int(page_player_counters.get(page_index) or 0) + 1
        if source_image is None:
            continue
        try:
            photo_region, _photo_mode = _resolve_review_photo_region(
                player_payload=player_payload,
                page_index=page_index,
                page_player_index=page_player_counters[page_index],
                image_size=source_image.size,
            )
            crop = crop_player_photo(
                image=source_image,
                photo_region=photo_region,
                player_index=max(idx - 1, 0),
                total_players=max(len(players_payload), 1),
                side=f"review_p{page_index}",
            )
            if not image_has_photo_like_content(crop):
                continue
            buffer = io.BytesIO()
            crop.save(buffer, format="JPEG", quality=90)
            raw = buffer.getvalue()
            filename = (
                f"review_{page_index}_{idx:02d}_"
                f"{slugify_filename(player_payload.get('name'), fallback='jugador')}_"
                f"{datetime.utcnow().strftime('%Y%m%dT%H%M%S%f')}.jpg"
            )
            filepath = photos_dir / filename
            filepath.write_bytes(raw)
            artifacts[idx] = {
                "photo_path": str(filepath),
                "photo_sha256": compute_sha256_hex(raw),
                "photo_ahash": average_hash_hex(crop),
            }
        except Exception:
            logger.warning("Could not build review photo artifact for player idx=%s", idx, exc_info=True)
    return artifacts


def _build_review_player_photo_previews(
    *,
    session_id: UUID,
    players_payload: List[Dict[str, Any]],
    assets: List[RegistrationReviewAsset],
    layout_regions: Dict[str, Any],
) -> Dict[int, Dict[str, Any]]:
    previews_dir = review_uploads_dir / str(session_id) / "player_previews"
    previews_dir.mkdir(parents=True, exist_ok=True)
    loaded_pages = _load_review_asset_images(assets)
    player_page_map = dict((layout_regions or {}).get("player_page_map") or {})
    previews: Dict[int, Dict[str, Any]] = {}
    page_player_counters: Dict[int, int] = {}

    for idx, player_payload in enumerate(players_payload, 1):
        page_index = int(player_page_map.get(str(idx)) or 1)
        source_image = loaded_pages.get(page_index)
        page_player_counters[page_index] = int(page_player_counters.get(page_index) or 0) + 1
        if source_image is None:
            continue
        try:
            photo_region, photo_mode = _resolve_review_photo_region(
                player_payload=player_payload,
                page_index=page_index,
                page_player_index=page_player_counters[page_index],
                image_size=source_image.size,
            )
            crop = crop_player_photo(
                image=source_image,
                photo_region=photo_region,
                player_index=max(idx - 1, 0),
                total_players=max(len(players_payload), 1),
                side=f"review_preview_p{page_index}",
            )
            if not image_has_photo_like_content(crop):
                continue
            filepath = previews_dir / f"player_{idx:02d}.jpg"
            crop.save(filepath, format="JPEG", quality=88)
            previews[idx] = {
                "preview_path": str(filepath),
                "preview_url": _review_public_path(str(filepath)),
                "preview_mode": photo_mode,
            }
        except Exception:
            logger.warning("Could not build review photo preview for player idx=%s", idx, exc_info=True)
    return previews


def _split_player_name(full_name: str) -> Tuple[str, str]:
    clean = " ".join((full_name or "").split()).strip()
    if not clean:
        return "", ""
    parts = clean.split()
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def _parse_birth_date(value: Optional[str]) -> Optional[date]:
    text = (value or "").strip()
    if not text:
        return None
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _normalize_curp_for_storage(value: Optional[str]) -> Tuple[Optional[str], bool]:
    cleaned = "".join(ch for ch in str(value or "").strip().upper() if ch.isalnum())
    if not cleaned:
        return None, False
    truncated = len(cleaned) > 18
    return cleaned[:18], truncated


def _review_event_timestamp() -> tuple[datetime, str]:
    now = datetime.utcnow()
    return now, now.replace(microsecond=0).isoformat() + "Z"


def _review_session_actor(request: Request) -> Dict[str, Optional[str]]:
    try:
        session = request.session or {}
    except Exception:
        session = {}
    user_id = str(session.get("empleado_id") or "").strip() or None
    role = str(session.get("rol") or "").strip().lower() or None
    display_name = str(session.get("nombre") or "").strip() or None
    return {
        "user_id": user_id,
        "role": role,
        "display_name": display_name,
    }


def _redact_email(value: Optional[str]) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if "@" not in text:
        return "***"
    local, domain = text.split("@", 1)
    local_hint = local[:1] if local else "*"
    return f"{local_hint}***@***"


def _redact_curp(value: Optional[str]) -> str:
    cleaned = "".join(ch for ch in str(value or "").strip().upper() if ch.isalnum())
    if not cleaned:
        return ""
    if len(cleaned) <= 4:
        return "***"
    return f"{cleaned[:4]}***{cleaned[-2:]}"


def _redact_birth_date(value: Optional[str]) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    parsed = _parse_birth_date(text)
    if parsed is not None:
        return f"{parsed.year}-**-**"
    parts = [part for part in text.replace(".", "/").replace("-", "/").split("/") if part]
    if len(parts) == 3 and parts[-1].isdigit():
        return f"{parts[-1]}-**-**"
    return "****-**-**"


def _redact_review_value(field_path: str, value: Any) -> Any:
    if value is None:
        return None
    text = str(value).strip()
    field = str(field_path or "").lower()
    if "curp" in field:
        return _redact_curp(text)
    if "email" in field:
        return _redact_email(text)
    if "birth_date" in field or "fecha" in field:
        return _redact_birth_date(text)
    if ".name" in field or field.endswith("name"):
        return "***"
    return text[:80] if len(text) > 80 else text


def _log_registration_review_event(event_name: str, *, session_id: Any, status: str, extra: Optional[Dict[str, Any]] = None) -> None:
    payload = {
        "event": event_name,
        "session_id": str(session_id),
        "status": status,
    }
    if isinstance(extra, dict):
        payload.update(extra)
    logger.info(
        "registration_review_event event=%s session_id=%s status=%s details=%s",
        payload["event"],
        payload["session_id"],
        payload["status"],
        {k: v for k, v in payload.items() if k not in {"event", "session_id", "status"}},
    )


def _review_audit_state(validation: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if isinstance(validation, dict) and isinstance(validation.get("audit"), dict):
        audit = copy.deepcopy(validation["audit"])
    else:
        audit = {}
    audit.setdefault("field_corrections", [])
    audit.setdefault("edit_events", [])
    audit.setdefault("commit_events", [])
    return audit


def _attach_review_audit(validation: Dict[str, Any], audit: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    updated = dict(validation or {})
    updated["audit"] = _review_audit_state({"audit": audit} if isinstance(audit, dict) else None)
    return updated


def _build_review_extraction_metadata(
    raw_payload: Optional[Dict[str, Any]],
    extraction: Dict[str, Any],
    *,
    review_session: Optional[RegistrationReviewSession] = None,
    assets: Optional[List[Any]] = None,
) -> Dict[str, Any]:
    raw = raw_payload if isinstance(raw_payload, dict) else {}
    backend = raw.get("backend") if isinstance(raw.get("backend"), dict) else {}
    layout = raw.get("layout") if isinstance(raw.get("layout"), dict) else {}
    pages = list(raw.get("pages") or []) if isinstance(raw.get("pages"), list) else []
    _, extracted_at_iso = _review_event_timestamp()
    return {
        "provider": str(raw.get("provider") or backend.get("provider") or getattr(review_session, "provider", None) or "unknown"),
        "pipeline": str(
            backend.get("pipeline")
            or raw.get("response_source")
            or backend.get("layout_provider")
            or layout.get("provider")
            or "unknown"
        ),
        "model": str(backend.get("model") or raw.get("model") or "unknown"),
        "extracted_at": str(raw.get("extracted_at") or extracted_at_iso),
        "source_asset_count": len(list(assets or [])),
        "page_count": len(pages) or len(list(assets or [])) or int(raw.get("page_count") or 0),
        "overall_confidence": float(extraction.get("overall_confidence") or 0.0),
        "schema_version": str(raw.get("schema_version") or "registration_review_v1"),
        "fallback_used": bool(raw.get("fallback_used") or bool(raw.get("error"))),
    }


def _extract_correction_items(
    before: Any,
    after: Any,
    *,
    path: str = "",
) -> List[Dict[str, Any]]:
    changes: List[Dict[str, Any]] = []
    if isinstance(before, dict) and isinstance(after, dict):
        for key in sorted(set(before.keys()) | set(after.keys())):
            next_path = f"{path}.{key}" if path else str(key)
            changes.extend(_extract_correction_items(before.get(key), after.get(key), path=next_path))
        return changes
    if isinstance(before, list) and isinstance(after, list):
        max_len = max(len(before), len(after))
        for idx in range(max_len):
            left = before[idx] if idx < len(before) else None
            right = after[idx] if idx < len(after) else None
            changes.extend(_extract_correction_items(left, right, path=f"{path}[{idx}]"))
        return changes
    if before == after:
        return changes
    changes.append(
        {
            "path": path or "root",
            "before": before,
            "after": after,
        }
    )
    return changes


def _build_field_corrections(
    before_extraction: Dict[str, Any],
    after_extraction: Dict[str, Any],
    *,
    actor: Dict[str, Optional[str]],
    changed_at: str,
) -> List[Dict[str, Any]]:
    raw_changes = _extract_correction_items(
        _normalize_review_extraction(before_extraction),
        _normalize_review_extraction(after_extraction),
    )
    corrections: List[Dict[str, Any]] = []
    for item in raw_changes:
        before_value = item.get("before")
        after_value = item.get("after")
        if before_value == after_value:
            continue
        corrections.append(
            {
                "path": item["path"],
                "before": before_value,
                "after": after_value,
                "changed_by": actor.get("user_id"),
                "changed_role": actor.get("role"),
                "changed_at": changed_at,
                "source": "manual_review",
            }
        )
    return corrections


def _build_commit_audit_envelope(
    *,
    review_session: RegistrationReviewSession,
    draft: RegistrationReviewDraft,
    actor: Dict[str, Optional[str]],
    validation: Dict[str, Any],
    extraction: Dict[str, Any],
    outcome: str,
    commit_request_id: str,
    approved_at: str,
) -> Dict[str, Any]:
    audit = _review_audit_state(validation)
    return {
        "session_id": str(review_session.id),
        "draft_id": str(getattr(draft, "id", "") or ""),
        "commit_request_id": commit_request_id,
        "approved_by": {
            "user_id": actor.get("user_id"),
            "role": actor.get("role"),
            "display_name": actor.get("display_name"),
        },
        "approved_at": approved_at,
        "source_asset_ids": [str(getattr(asset, "id", "") or "") for asset in list(review_session.assets or [])],
        "validation_snapshot": {
            "ready_to_commit": bool(validation.get("ready_to_commit")),
            "blockers": copy.deepcopy(validation.get("blockers") or []),
            "warnings": copy.deepcopy(validation.get("warnings") or []),
        },
        "field_corrections_count": len(audit.get("field_corrections") or []),
        "players_count": len(list(extraction.get("players") or [])),
        "team_name": (extraction.get("team") or {}).get("name"),
        "extraction_metadata": copy.deepcopy(audit.get("extraction_metadata") or {}),
        "outcome": outcome,
    }


def _birth_date_has_two_digit_year(value: Optional[str]) -> bool:
    text = (value or "").strip()
    if not text:
        return False
    for separator in ("/", "-", "."):
        parts = text.split(separator)
        if len(parts) == 3 and len(parts[-1]) == 2 and all(part.isdigit() for part in parts):
            return True
    return False


def _apply_review_form_edits(form_data: Any, base_extraction: Dict[str, Any]) -> Dict[str, Any]:
    extraction = _normalize_review_extraction(base_extraction)
    team = extraction.setdefault("team", {})
    existing_manager = extraction.get("manager") if isinstance(extraction.get("manager"), dict) else None
    manager = dict(existing_manager or {})
    manager_keys_present = any(key in form_data for key in ("manager_name", "manager_phone", "manager_email", "manager_role"))

    for key in ("name", "category", "gender", "league", "municipality", "state"):
        form_key = f"team_{key}"
        if form_key not in form_data:
            continue
        value = (form_data.get(form_key) or "").strip()
        team[key] = value or None
    if "team_name" in form_data:
        team["name"] = (form_data.get("team_name") or "").strip()

    if "manager_name" in form_data:
        manager["name"] = (form_data.get("manager_name") or "").strip()
    if "manager_phone" in form_data:
        manager["phone"] = (form_data.get("manager_phone") or "").strip() or None
    if "manager_email" in form_data:
        manager["email"] = (form_data.get("manager_email") or "").strip() or None
    if "manager_role" in form_data:
        manager["role"] = (form_data.get("manager_role") or "").strip() or "delegado"
    extraction["manager"] = manager if manager or existing_manager or manager_keys_present else None

    if "notes" in form_data:
        extraction["notes"] = (form_data.get("notes") or "").strip() or None

    existing_players = list(extraction.get("players") or [])
    player_count = int(form_data.get("player_count") or len(existing_players) or 0) if "player_count" in form_data else len(existing_players)
    updated_players: List[Dict[str, Any]] = []
    for idx in range(player_count):
        existing = dict(existing_players[idx]) if idx < len(existing_players) else {}
        name_key = f"player_{idx}_name"
        birth_date_key = f"player_{idx}_birth_date"
        curp_key = f"player_{idx}_curp"
        needs_review_key = f"player_{idx}_needs_review"
        row_keys_present = any(key in form_data for key in (name_key, birth_date_key, curp_key, needs_review_key))

        if name_key in form_data:
            existing["name"] = (form_data.get(name_key) or "").strip()
        if birth_date_key in form_data:
            existing["birth_date"] = (form_data.get(birth_date_key) or "").strip() or None
        if curp_key in form_data:
            existing["curp"] = (form_data.get(curp_key) or "").strip().upper() or None
        if needs_review_key in form_data or row_keys_present:
            existing["needs_review"] = str(form_data.get(needs_review_key) or "").lower() in {
                "1",
                "true",
                "on",
                "yes",
            }
        updated_players.append(existing)
    extraction["players"] = updated_players
    return _normalize_review_extraction(extraction)


async def _store_review_uploads(session_id: UUID, uploads: List[Any], *, start_index: int = 1) -> List[Dict[str, Any]]:
    session_dir = review_uploads_dir / str(session_id)
    session_dir.mkdir(parents=True, exist_ok=True)
    stored_assets: List[Dict[str, Any]] = []

    for upload in uploads:
        if not getattr(upload, "filename", None):
            continue
        payload = await _read_upload_limited(upload)
        if not payload:
            continue

        extension = _validate_review_upload_metadata(upload, payload)
        remaining_slots = REVIEW_SESSION_MAX_FILES - len(stored_assets) - (start_index - 1)
        if remaining_slots <= 0:
            raise _review_error(
                "too_many_files",
                f"Solo se permiten hasta {REVIEW_SESSION_MAX_FILES} páginas por revisión.",
            )

        if extension == ".pdf":
            stored_assets.extend(
                _render_pdf_review_assets(
                    session_dir,
                    payload,
                    start_page_index=start_index + len(stored_assets),
                    remaining_slots=remaining_slots,
                )
            )
            continue

        width, height = _validate_review_image_payload(payload)
        page_index = start_index + len(stored_assets)
        output_path = session_dir / f"page-{page_index:02d}{extension}"
        output_path.write_bytes(payload)
        stored_assets.append(
            {
                "page_index": page_index,
                "image_path": str(output_path),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "width": width,
                "height": height,
            }
        )
    return stored_assets


async def _run_review_ocr(primary_image_path: str) -> tuple[Dict[str, Any], Dict[str, Any], str]:
    image_bytes = Path(primary_image_path).read_bytes()

    if review_ocr_provider in {"anthropic", "claude_structured", "claude_vision"} and review_ocr_agent is not None:
        try:
            extraction = await review_ocr_agent.extract_registration_form_structured(image_bytes)
            extraction_dict = extraction.model_dump(mode="json")
            team_name = str(((extraction_dict.get("team") or {}).get("name") or "")).strip().lower()
            if team_name == "error" and not list(extraction_dict.get("players") or []):
                raise RuntimeError(str(extraction_dict.get("notes") or "Anthropic structured OCR failed"))
            raw_payload = {
                "provider": "anthropic",
                "backend": {"provider": "anthropic"},
                "response_source": "ocr_agent",
            }
            return _normalize_review_extraction(extraction_dict), raw_payload, "anthropic"
        except Exception:
            logger.warning("Review OCR via Anthropic failed; falling back to local OCR", exc_info=True)

    extraction_dict, raw_payload = await review_ocr_runner.extract_registration_form_from_bytes_async(
        image_bytes
    )
    if extraction_dict:
        normalized = _normalize_review_extraction(extraction_dict)
        provider = str((raw_payload or {}).get("backend", {}).get("provider") or "local")
        failure_note = _backend_failure_note(raw_payload)
        if failure_note and not _review_extraction_has_signal(normalized):
            return _minimal_review_extraction(note=failure_note), raw_payload or {}, provider
        if failure_note:
            normalized = _normalize_review_extraction(normalized, note=failure_note)
        return normalized, raw_payload or {}, provider

    raw = raw_payload or {}
    note = _backend_failure_note(raw_payload) or str(raw.get("error") or "OCR no disponible para esta imagen.")
    return _minimal_review_extraction(note=note), raw, "local"


async def _process_review_assets(assets: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], Dict[str, Any], str, Dict[str, Any]]:
    page_results: List[Dict[str, Any]] = []
    for asset in assets:
        extraction, raw_payload, provider = await _run_review_ocr(asset["image_path"])
        page_results.append(
            {
                "asset": asset,
                "extraction": extraction,
                "raw": raw_payload,
                "provider": provider,
            }
        )
    return _merge_review_extractions(page_results)


async def _upsert_review_draft(
    db_session: AsyncSession,
    review_session: RegistrationReviewSession,
    extraction: Dict[str, Any],
    raw_payload: Dict[str, Any],
    *,
    layout_regions: Optional[Dict[str, Any]] = None,
    preserve_edits: bool = False,
) -> RegistrationReviewDraft:
    validation = _build_review_commit_validation(
        extraction,
        _build_review_validation(extraction, raw_payload=raw_payload),
        review_session=review_session,
    )
    draft_result = await db_session.execute(
        select(RegistrationReviewDraft).where(
            RegistrationReviewDraft.session_id == review_session.id
        )
    )
    draft = draft_result.scalar_one_or_none()
    if draft is None:
        draft = RegistrationReviewDraft(session_id=review_session.id)
        db_session.add(draft)

    existing_audit = _review_audit_state(draft.validation if isinstance(draft.validation, dict) else None)
    extraction_metadata = _build_review_extraction_metadata(
        raw_payload,
        extraction,
        review_session=review_session,
        assets=list(review_session.assets or []),
    )
    existing_audit["extraction_metadata"] = extraction_metadata
    validation = _attach_review_audit(validation, existing_audit)

    raw_payload = dict(raw_payload or {})
    raw_payload["extraction_metadata"] = extraction_metadata

    draft.ocr_raw = raw_payload
    draft.extraction = extraction
    if not preserve_edits or not isinstance(draft.review_edits, dict):
        draft.review_edits = extraction
    draft.validation = validation
    draft.layout_regions = layout_regions or (raw_payload.get("layout") if isinstance(raw_payload, dict) else None)
    draft.overall_confidence = float(extraction.get("overall_confidence") or 0.0)
    draft.needs_review = bool(validation.get("needs_review"))

    detected_provider = None
    if isinstance(raw_payload, dict):
        detected_provider = (
            raw_payload.get("provider")
            or ((raw_payload.get("backend") or {}).get("provider") if isinstance(raw_payload.get("backend"), dict) else None)
        )
    review_session.provider = str(detected_provider or review_session.provider or review_ocr_provider or "local")
    review_session.status = "ready"
    review_session.error_message = None
    return draft

# Serve tournament frontends with SPA fallback
copa_america_dist_dir = Path(__file__).parent / "goal-fest-page" / "dist"
if copa_america_dist_dir.exists():
    logger.info("✅ Copa Club America frontend available at /copa-america")
else:
    logger.warning("⚠️ Copa Club America dist not found at %s", copa_america_dist_dir)

_copatelmex_root = Path(__file__).parent
_copatelmex_active_dist_dir = _copatelmex_root / "copatelmex" / "dist"
_copatelmex_backup_dist_candidates = sorted(
    _copatelmex_root.glob("copatelmex.backup-*/dist"),
    reverse=True,
)
copa_telmex_dist_dir = _resolve_dist_dir(
    _copatelmex_active_dist_dir,
    Path(os.getenv("COPA_TELMEX_DIST_DIR")).expanduser()
    if os.getenv("COPA_TELMEX_DIST_DIR")
    else None,
    *_copatelmex_backup_dist_candidates,
)
if copa_telmex_dist_dir:
    logger.info("✅ Copa Telmex frontend available at /telmex from %s", copa_telmex_dist_dir)
else:
    logger.warning(
        "⚠️ Copa Telmex dist not found. Checked %s and %d backup candidates",
        _copatelmex_active_dist_dir,
        len(_copatelmex_backup_dist_candidates),
    )


_SPA_ASSET_EXTENSIONS = {
    ".js",
    ".css",
    ".png",
    ".jpg",
    ".jpeg",
    ".svg",
    ".webp",
    ".ico",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".map",
}


def _resolve_spa_file(dist_dir: Path, path: str) -> Path:
    """Resolve a file in a Vite/React dist folder with safe SPA fallback.

    Rules:
    - If the requested file exists, serve it.
    - If the path looks like a static asset (has an extension) but doesn't exist, return 404
      (do not fall back to index.html; this avoids broken caching/content-types).
    - Otherwise, fall back to index.html (client-side routing).
    """
    dist_root = dist_dir.resolve()
    index_file = dist_root / "index.html"
    if not index_file.is_file():
        raise HTTPException(status_code=404, detail="Frontend index.html not deployed")

    clean_path = path.lstrip("/")
    if clean_path:
        requested_file = (dist_root / clean_path).resolve()
        try:
            requested_file.relative_to(dist_root)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="Invalid path") from exc
        if requested_file.is_file():
            return requested_file

        # If it looks like an asset request, missing assets should be a hard 404.
        # Vite typically serves hashed bundles under `assets/`.
        if clean_path.startswith("assets/") or requested_file.suffix.lower() in _SPA_ASSET_EXTENSIONS:
            raise HTTPException(status_code=404, detail="File not found")

    return index_file


@app.get("/copa-america", include_in_schema=False)
@app.get("/copa-america/", include_in_schema=False)
@app.get("/copa-america/{path:path}", include_in_schema=False)
async def copa_america_spa(path: str = ""):
    if not copa_america_dist_dir.exists():
        raise HTTPException(status_code=404, detail="Copa America frontend not deployed")
    target = _resolve_spa_file(copa_america_dist_dir, path)
    headers = {}
    if target.name == "index.html":
        headers["Cache-Control"] = "no-store"
    elif target.suffix.lower() in _SPA_ASSET_EXTENSIONS or "assets" in target.parts:
        headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return FileResponse(str(target), headers=headers)


@app.get("/assistant", include_in_schema=False)
@app.get("/assistant/", include_in_schema=False)
@app.get("/assistant/{path:path}", include_in_schema=False)
async def assistant_spa_alias(request: Request, path: str = ""):
    """Enterprise assistant alias without tournament-specific URL segment."""
    if not _has_internal_session(request):
        return _redirect_to_login(request, fallback="/assistant")
    if _is_session_empleado_role(request):
        return RedirectResponse(url="/panel", status_code=307)
    if not copa_america_dist_dir.exists():
        raise HTTPException(status_code=404, detail="Assistant frontend not deployed")
    assistant_path = f"assistant/{path.lstrip('/')}" if path else "assistant"
    target = _resolve_spa_file(copa_america_dist_dir, assistant_path)
    headers = {}
    if target.name == "index.html":
        headers["Cache-Control"] = "no-store"
    elif target.suffix.lower() in _SPA_ASSET_EXTENSIONS or "assets" in target.parts:
        headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return FileResponse(str(target), headers=headers)


@app.get("/RAG", include_in_schema=False)
@app.get("/RAG/", include_in_schema=False)
@app.get("/RAG/{path:path}", include_in_schema=False)
@app.get("/rag", include_in_schema=False)
@app.get("/rag/", include_in_schema=False)
@app.get("/rag/{path:path}", include_in_schema=False)
async def rag_spa_alias(request: Request, path: str = ""):
    """Enterprise RAG console alias at root."""
    if not _has_internal_session(request):
        return _redirect_to_login(request, fallback="/RAG")
    if not copa_america_dist_dir.exists():
        raise HTTPException(status_code=404, detail="RAG frontend not deployed")
    rag_path = f"RAG/{path.lstrip('/')}" if path else "RAG"
    target = _resolve_spa_file(copa_america_dist_dir, rag_path)
    headers = {}
    if target.name == "index.html":
        headers["Cache-Control"] = "no-store"
    elif target.suffix.lower() in _SPA_ASSET_EXTENSIONS or "assets" in target.parts:
        headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return FileResponse(str(target), headers=headers)


@app.get("/admin", include_in_schema=False)
@app.get("/admin/", include_in_schema=False)
async def admin_spa_alias(request: Request):
    """Enterprise admin console alias at root."""
    if not _has_internal_session(request):
        return _redirect_to_login(request, fallback="/admin")
    if not copa_america_dist_dir.exists():
        raise HTTPException(status_code=404, detail="Admin frontend not deployed")
    target = _resolve_spa_file(copa_america_dist_dir, "admin")
    headers = {}
    if target.name == "index.html":
        headers["Cache-Control"] = "no-store"
    elif target.suffix.lower() in _SPA_ASSET_EXTENSIONS or "assets" in target.parts:
        headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return FileResponse(str(target), headers=headers)


@app.get("/folders", include_in_schema=False)
@app.get("/folders/", include_in_schema=False)
@app.get("/folders/{path:path}", include_in_schema=False)
async def folders_spa_alias(request: Request, path: str = ""):
    """Enterprise folders console alias at root."""
    if not _has_internal_session(request):
        return _redirect_to_login(request, fallback="/folders")
    if not copa_america_dist_dir.exists():
        raise HTTPException(status_code=404, detail="Folders frontend not deployed")
    folders_path = f"folders/{path.lstrip('/')}" if path else "folders"
    target = _resolve_spa_file(copa_america_dist_dir, folders_path)
    headers = {}
    if target.name == "index.html":
        headers["Cache-Control"] = "no-store"
    elif target.suffix.lower() in _SPA_ASSET_EXTENSIONS or "assets" in target.parts:
        headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return FileResponse(str(target), headers=headers)


@app.get("/operations", include_in_schema=False)
@app.get("/operations/", include_in_schema=False)
@app.get("/operations/{path:path}", include_in_schema=False)
async def operations_spa_alias(request: Request, path: str = ""):
    """Enterprise operaciones BI console (analytics dashboard)."""
    if not _has_internal_session(request):
        return _redirect_to_login(request, fallback="/operations/analytics")
    if not copa_america_dist_dir.exists():
        raise HTTPException(status_code=404, detail="Operations frontend not deployed")
    operations_path = f"operations/{path.lstrip('/')}" if path else "operations"
    target = _resolve_spa_file(copa_america_dist_dir, operations_path)
    headers = {}
    if target.name == "index.html":
        headers["Cache-Control"] = "no-store"
    elif target.suffix.lower() in _SPA_ASSET_EXTENSIONS or "assets" in target.parts:
        headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return FileResponse(str(target), headers=headers)


@app.get("/finance", include_in_schema=False)
@app.get("/finance/", include_in_schema=False)
async def finance_root_alias(request: Request):
    """Redirect finance root to gastos module."""
    if not _has_internal_session(request):
        return _redirect_to_login(request, fallback="/finance")
    query = f"?{request.url.query}" if request.url.query else ""
    return RedirectResponse(url=f"/admin/gastos/expenses{query}", status_code=307)


@app.get("/finance/expenses", include_in_schema=False)
@app.get("/finance/expenses/", include_in_schema=False)
async def finance_expenses_alias(request: Request):
    if not _has_internal_session(request):
        return _redirect_to_login(request, fallback="/finance/expenses")
    query = f"?{request.url.query}" if request.url.query else ""
    return RedirectResponse(url=f"/admin/gastos/expenses{query}", status_code=307)


@app.get("/finance/my-expenses", include_in_schema=False)
@app.get("/finance/my-expenses/", include_in_schema=False)
async def finance_my_expenses_alias(request: Request):
    if not _has_internal_session(request):
        return _redirect_to_login(request, fallback="/finance/my-expenses")
    query = f"?{request.url.query}" if request.url.query else ""
    return RedirectResponse(url=f"/gastos/mis-gastos{query}", status_code=307)


@app.get("/finance/accounts", include_in_schema=False)
@app.get("/finance/accounts/", include_in_schema=False)
async def finance_accounts_alias(request: Request):
    if not _has_internal_session(request):
        return _redirect_to_login(request, fallback="/finance/accounts")
    query = f"?{request.url.query}" if request.url.query else ""
    return RedirectResponse(url=f"/admin/cuentas-contables{query}", status_code=307)


@app.get("/finance/unmapped", include_in_schema=False)
@app.get("/finance/unmapped/", include_in_schema=False)
async def finance_unmapped_alias(request: Request):
    if not _has_internal_session(request):
        return _redirect_to_login(request, fallback="/finance/unmapped")
    query = f"?{request.url.query}" if request.url.query else ""
    return RedirectResponse(url=f"/admin/gastos/sin-cuenta-contable{query}", status_code=307)


@app.get("/telmex", include_in_schema=False)
@app.get("/telmex/", include_in_schema=False)
@app.get("/telmex/{path:path}", include_in_schema=False)
async def copa_telmex_spa(path: str = ""):
    if not copa_telmex_dist_dir.exists():
        raise HTTPException(status_code=404, detail="Copa Telmex frontend not deployed")
    target = _resolve_spa_file(copa_telmex_dist_dir, path)
    headers = {}
    if target.name == "index.html":
        headers["Cache-Control"] = "no-store"
    elif target.suffix.lower() in _SPA_ASSET_EXTENSIONS or "assets" in target.parts:
        headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return FileResponse(str(target), headers=headers)


@app.on_event("startup")
async def startup_event():
    """Startup event - test database connection and create tables"""
    try:
        # Create all tables (including CFDIReport)
        async with db_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            guard_report = await apply_schema_guard(conn, logger=logger, strict=False)
            health_report = await check_schema_health(conn)
        logger.info("✅ Database tables created/verified")
        if guard_report.get("failed_count"):
            logger.warning("⚠️ Schema guard had failures: %s", guard_report.get("failed"))
        if not health_report.get("ok"):
            logger.warning("⚠️ Schema health still has gaps: %s", health_report)

        async with async_session_maker() as session:
            copa_db = CopaTelmexDB(session)
            stats = await copa_db.get_registration_stats()
            logger.info(f"✅ Database connected - {stats['total_teams']} teams, {stats['total_players']} players")

        recovered = await recover_orphaned_sat_jobs()
        if recovered:
            logger.warning("Recovered %s orphaned SAT background job(s) on startup", recovered)
    except Exception as e:
        logger.error(f"❌ Database connection failed: {e}")


@app.on_event("shutdown")
async def shutdown_event():
    """Shutdown event - stop SAT jobs and close database connections"""
    try:
        await shutdown_background_jobs()
    except Exception as exc:
        logger.error("SAT background shutdown failed: %s", exc)
    await db_engine.dispose()
    logger.info("✅ Database connections closed")


@app.get("/", include_in_schema=False)
async def root_enterprise_home(request: Request):
    """Enterprise home entrypoint with hard auth gate."""
    if not _has_internal_session(request):
        return _redirect_to_login(request, fallback="/assistant")
    if _is_session_empleado_role(request):
        return RedirectResponse(url="/panel", status_code=307)
    return RedirectResponse(url="/assistant", status_code=307)


@app.get("/healthz", include_in_schema=False)
async def healthz() -> JSONResponse:
    """Shallow liveness check for nginx/systemd monitoring."""
    return JSONResponse(
        status_code=200,
        content={
            "ok": True,
            "status": "healthy",
            "service": "samchat-gastos",
            "app_url": public_app_url,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        },
    )


@app.get("/readyz", include_in_schema=False)
async def readyz() -> JSONResponse:
    """Readiness check covering DB connectivity and schema health."""
    try:
        async with db_engine.begin() as conn:
            await conn.execute(text("SELECT 1"))
            schema_health = await check_schema_health(conn)
        ok = bool(schema_health.get("ok"))
        status_code = 200 if ok else 503
        status = "healthy" if ok else "degraded"
        return JSONResponse(
            status_code=status_code,
            content={
                "ok": ok,
                "status": status,
                "service": "samchat-gastos",
                "app_url": public_app_url,
                "schema_health": schema_health,
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            },
        )
    except Exception as exc:
        logger.exception("Readiness check failed")
        return JSONResponse(
            status_code=503,
            content={
                "ok": False,
                "status": "unhealthy",
                "service": "samchat-gastos",
                "app_url": public_app_url,
                "error": str(exc),
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            },
        )


@app.get("/torneos", response_class=HTMLResponse)
async def tournament_selector(request: Request):
    """Tournament selector page (kept as optional route)."""
    return templates.TemplateResponse(
        "tournament_selector.html",
        {
            "request": request,
            "copa_america_frontend_url": copa_america_frontend_url,
            "copa_telmex_frontend_url": copa_telmex_frontend_url,
        },
    )


@app.get("/dashboard", response_class=HTMLResponse)
async def home(request: Request):
    """Dashboard page with statistics"""
    redirect = _ensure_legacy_copa_dashboard_access(request, html_fallback="/dashboard")
    if redirect is not None:
        return redirect
    try:
        async with async_session_maker() as session:
            copa_db = CopaTelmexDB(session)
            stats = await copa_db.get_registration_stats()

            # Get recent registrations
            registrations = await copa_db.get_registrations_needing_review()

            return templates.TemplateResponse(
                "home.html",
                {
                    "request": request,
                    "stats": stats,
                    "pending_reviews": len(registrations)
                }
            )
    except Exception:
        _raise_dashboard_internal_error("Error loading home page")


@app.get("/api/stats", response_class=JSONResponse)
async def get_stats(request: Request):
    """API endpoint for statistics"""
    _ensure_legacy_copa_dashboard_access(request)
    async with async_session_maker() as session:
        copa_db = CopaTelmexDB(session)
        stats = await copa_db.get_registration_stats()
        return stats


@app.get("/teams", response_class=HTMLResponse)
async def list_teams(request: Request):
    """List all teams"""
    redirect = _ensure_legacy_copa_dashboard_access(request, html_fallback="/teams")
    if redirect is not None:
        return redirect
    try:
        async with async_session_maker() as session:
            copa_db = CopaTelmexDB(session)

            from sqlalchemy import select
            from devnous.copa_telmex.models import Team

            result = await session.execute(
                select(Team).order_by(Team.created_at.desc())
            )
            all_teams = result.scalars().all()

            # Convert to dict for template
            teams_data = []
            for team in all_teams:
                players = await copa_db.get_players_by_team(team.id)
                teams_data.append({
                    "id": str(team.id),
                    "name": team.name,
                    "category": team.category or "N/A",
                    "gender": team.gender or "N/A",
                    "state": team.state or "N/A",
                    "player_count": len(players),
                    "created_at": team.created_at.strftime("%Y-%m-%d %H:%M")
                })

            return templates.TemplateResponse(
                "teams.html",
                {
                    "request": request,
                    "teams": teams_data
                }
            )
    except Exception:
        _raise_dashboard_internal_error("Error loading teams")


@app.get("/team/{team_id}", response_class=HTMLResponse)
async def view_team(request: Request, team_id: str):
    """View team details and players"""
    redirect = _ensure_legacy_copa_dashboard_access(request, html_fallback=f"/team/{team_id}")
    if redirect is not None:
        return redirect
    try:
        from uuid import UUID

        async with async_session_maker() as session:
            copa_db = CopaTelmexDB(session)

            team = await copa_db.get_team_by_id(UUID(team_id))
            if not team:
                raise HTTPException(status_code=404, detail="Team not found")

            players = await copa_db.get_players_by_team(team.id)

            # Convert to dict for template
            team_data = {
                "id": str(team.id),
                "name": team.name,
                "category": team.category or "N/A",
                "gender": team.gender or "N/A",
                "league": team.league or "N/A",
                "league_phone": team.league_phone or "N/A",
                "league_address": team.league_address or "N/A",
                "representative_name": team.representative_name or "N/A",
                "contact_phone": team.contact_phone or "N/A",
                "state": team.state or "N/A",
                "municipality": team.municipality or "N/A",
                "roster_image_path": team.roster_image_path or None,
                "created_at": team.created_at.strftime("%Y-%m-%d %H:%M")
            }

            players_data = []
            for player in players:
                players_data.append({
                    "id": str(player.id),
                    "full_name": player.full_name,
                    "birth_date": player.birth_date.strftime("%Y-%m-%d") if player.birth_date else "N/A",
                    "curp": player.curp or "N/A",
                    "email": player.email or "N/A",
                    "photo_url": _public_storage_path(player.photo_path),
                    "ocr_confidence": f"{player.ocr_confidence*100:.0f}%" if player.ocr_confidence else "N/A",
                    "needs_review": player.needs_review,
                    "verified_by_human": player.verified_by_human,
                    "created_at": player.created_at.strftime("%Y-%m-%d %H:%M")
                })

            return templates.TemplateResponse(
                "team_detail.html",
                {
                    "request": request,
                    "team": team_data,
                    "players": players_data
                }
            )
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid team ID")
    except HTTPException:
        raise
    except Exception:
        _raise_dashboard_internal_error("Error loading team")


@app.get("/players", response_class=HTMLResponse)
async def list_players(request: Request, needs_review: Optional[bool] = None):
    """List all players or players needing review"""
    redirect = _ensure_legacy_copa_dashboard_access(request, html_fallback="/players")
    if redirect is not None:
        return redirect
    try:
        async with async_session_maker() as session:
            copa_db = CopaTelmexDB(session)

            if needs_review:
                players = await copa_db.get_players_needing_review()
            else:
                # Get all players
                from sqlalchemy import select
                from devnous.copa_telmex.models import Player

                result = await session.execute(
                    select(Player).order_by(Player.created_at.desc())
                )
                players = list(result.scalars().all())

            # Convert to dict for template
            players_data = []
            for player in players:
                # Get team info
                team = await copa_db.get_team_by_id(player.team_id)

                players_data.append({
                    "id": str(player.id),
                    "full_name": player.full_name,
                    "team_name": team.name if team else "Unknown",
                    "team_id": str(player.team_id),
                    "birth_date": player.birth_date.strftime("%Y-%m-%d") if player.birth_date else "N/A",
                    "photo_url": _public_storage_path(player.photo_path),
                    "ocr_confidence": f"{player.ocr_confidence*100:.0f}%" if player.ocr_confidence else "N/A",
                    "needs_review": player.needs_review,
                    "verified_by_human": player.verified_by_human,
                    "created_at": player.created_at.strftime("%Y-%m-%d %H:%M")
                })

            return templates.TemplateResponse(
                "players.html",
                {
                    "request": request,
                    "players": players_data,
                    "filter": "needs_review" if needs_review else "all"
                }
            )
    except Exception:
        _raise_dashboard_internal_error("Error loading players")


@app.get("/registration-review", response_class=HTMLResponse)
async def list_registration_review_sessions(request: Request):
    """List pre-capture OCR review sessions."""
    redirect = _ensure_registration_review_access(request, html_fallback="/registration-review")
    if redirect is not None:
        return redirect
    try:
        async with async_session_maker() as session:
            result = await session.execute(
                select(RegistrationReviewSession)
                .options(
                    selectinload(RegistrationReviewSession.assets),
                    selectinload(RegistrationReviewSession.draft),
                )
                .order_by(RegistrationReviewSession.started_at.desc())
                .limit(100)
            )
            sessions = result.scalars().unique().all()

            session_rows = []
            for review_session in sessions:
                draft = review_session.draft
                validation = draft.validation if draft and isinstance(draft.validation, dict) else {}
                first_asset = review_session.assets[0] if review_session.assets else None
                session_rows.append(
                    {
                        "id": str(review_session.id),
                        "status": review_session.status,
                        "provider": review_session.provider,
                        "source": review_session.source,
                        "tournament_slug": review_session.tournament_slug,
                        "started_at": review_session.started_at.strftime("%Y-%m-%d %H:%M"),
                        "issue_count": int(validation.get("issue_count") or 0),
                        "player_count": int(validation.get("player_count") or 0),
                        "needs_review": bool(validation.get("needs_review")),
                        "committed_team_id": str(review_session.committed_team_id) if review_session.committed_team_id else None,
                        "cover_url": _review_public_path(first_asset.image_path) if first_asset else None,
                    }
                )

            return templates.TemplateResponse(
                "registration_review_list.html",
                {
                    "request": request,
                    "sessions": session_rows,
                },
            )
    except Exception:
        _raise_dashboard_internal_error("Error loading review sessions")


@app.get("/registration-review/new", response_class=HTMLResponse)
async def new_registration_review_session(request: Request):
    """Upload form for a new OCR review session."""
    redirect = _ensure_registration_review_access(request, html_fallback="/registration-review/new")
    if redirect is not None:
        return redirect
    return templates.TemplateResponse(
        "registration_review_new.html",
        {
            "request": request,
            "max_files": REVIEW_SESSION_MAX_FILES,
            "review_provider": review_ocr_provider,
            "tournament_options": _review_tournament_options("copa_telmex"),
        },
    )


@app.post("/api/registration-review")
async def create_registration_review_session(request: Request):
    """Create a new review session from uploaded roster images."""
    _ensure_registration_review_access(request)
    form_data = await request.form()
    uploads = [upload for upload in form_data.getlist("files") if getattr(upload, "filename", None)]
    if not uploads:
        raise _review_error("missing_files", "Sube al menos una imagen.")
    if len(uploads) > REVIEW_SESSION_MAX_FILES:
        raise _review_error(
            "too_many_files",
            f"Solo se permiten hasta {REVIEW_SESSION_MAX_FILES} imágenes por revisión.",
        )

    created_by_user_id = None
    try:
        created_by_user_id = str((request.session or {}).get("empleado_id") or "").strip() or None
    except Exception:
        created_by_user_id = None

    async with async_session_maker() as session:
        review_session = RegistrationReviewSession(
            status="uploaded",
            source="web",
            provider=(form_data.get("provider") or "local").strip() or "local",
            tournament_slug=(form_data.get("tournament_slug") or "").strip() or None,
            created_by_user_id=created_by_user_id,
        )
        session.add(review_session)
        await session.flush()

        stored_assets = await _store_review_uploads(review_session.id, uploads)
        if not stored_assets:
            raise HTTPException(status_code=400, detail="No pude guardar las imágenes de la sesión.")

        for asset_payload in stored_assets:
            session.add(
                RegistrationReviewAsset(
                    session_id=review_session.id,
                    page_index=asset_payload["page_index"],
                    image_path=asset_payload["image_path"],
                    sha256=asset_payload["sha256"],
                    width=asset_payload["width"],
                    height=asset_payload["height"],
                )
            )

        review_session.status = "processing"
        extraction, raw_payload, detected_provider, layout_regions = await _process_review_assets(stored_assets)
        review_session.provider = detected_provider or review_session.provider
        await _upsert_review_draft(
            session,
            review_session,
            extraction,
            raw_payload,
            layout_regions=layout_regions,
        )
        await session.commit()

    return RedirectResponse(url=f"/registration-review/{review_session.id}", status_code=303)


@app.get("/api/registration-review/{session_id}", response_class=JSONResponse)
async def get_registration_review_session_payload(session_id: str, request: Request):
    """Return raw draft payload for a review session."""
    _ensure_registration_review_access(request)
    try:
        session_uuid = UUID(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid review session ID") from exc

    async with async_session_maker() as session:
        result = await session.execute(
            select(RegistrationReviewSession)
            .options(
                selectinload(RegistrationReviewSession.assets),
                selectinload(RegistrationReviewSession.draft),
            )
            .where(RegistrationReviewSession.id == session_uuid)
        )
        review_session = result.scalar_one_or_none()
        if not review_session:
            raise HTTPException(status_code=404, detail="Review session not found")

        draft = review_session.draft
        extraction = _get_review_extraction(draft)
        validation = (
            draft.validation
            if draft and isinstance(draft.validation, dict)
            else _build_review_validation(extraction, raw_payload=draft.ocr_raw if draft else None)
        )
        validation = _build_review_commit_validation(
            extraction,
            validation,
            review_session=review_session,
        )

        return {
            "session": {
                "id": str(review_session.id),
                "status": review_session.status,
                "provider": review_session.provider,
                "source": review_session.source,
                "started_at": review_session.started_at.isoformat() if review_session.started_at else None,
                "committed_team_id": str(review_session.committed_team_id) if review_session.committed_team_id else None,
            },
            "assets": [_session_asset_payload(asset) for asset in review_session.assets],
            "draft": {
                "extraction": extraction,
                "validation": validation,
                "layout_regions": draft.layout_regions if draft else None,
                "ocr_raw": draft.ocr_raw if draft else None,
            },
        }


@app.get("/registration-review/{session_id}", response_class=HTMLResponse)
async def view_registration_review_session(request: Request, session_id: str):
    """Workspace to review OCR before committing to the main tables."""
    redirect = _ensure_registration_review_access(
        request, html_fallback=f"/registration-review/{session_id}"
    )
    if redirect is not None:
        return redirect
    try:
        session_uuid = UUID(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid review session ID") from exc

    async with async_session_maker() as session:
        result = await session.execute(
            select(RegistrationReviewSession)
            .options(
                selectinload(RegistrationReviewSession.assets),
                selectinload(RegistrationReviewSession.draft),
            )
            .where(RegistrationReviewSession.id == session_uuid)
        )
        review_session = result.scalar_one_or_none()
        if not review_session:
            raise HTTPException(status_code=404, detail="Review session not found")

        draft = review_session.draft
        extraction = _get_review_extraction(draft)
        canonical_review = build_canonical_review_view(
            draft.ocr_raw if draft else None,
            extraction,
        )
        validation = (
            draft.validation
            if draft and isinstance(draft.validation, dict)
            else _build_review_validation(extraction)
        )
        validation = _build_review_commit_validation(
            extraction,
            validation,
            review_session=review_session,
        )
        layout_regions = draft.layout_regions if draft and isinstance(draft.layout_regions, dict) else {"pages": {}, "player_page_map": {}}
        player_page_map = layout_regions.get("player_page_map") if isinstance(layout_regions, dict) else {}
        player_rows_map = {
            int(item.get("index") or 0): item for item in (validation.get("player_rows") or [])
        }
        photo_previews = _build_review_player_photo_previews(
            session_id=review_session.id,
            players_payload=list(extraction.get("players") or []),
            assets=list(review_session.assets or []),
            layout_regions=layout_regions,
        )
        players = []
        for idx, player in enumerate(extraction.get("players") or [], 1):
            review_meta = player_rows_map.get(idx) or {}
            players.append(
                {
                    "index": idx,
                    "name": player.get("name") or "",
                    "birth_date": player.get("birth_date") or "",
                    "curp": player.get("curp") or "",
                    "confidence_pct": f"{float(player.get('confidence') or 0.0) * 100:.0f}%",
                    "needs_review": bool(player.get("needs_review")),
                    "issues": review_meta.get("issues") or [],
                    "source_page": int(player_page_map.get(str(idx)) or 1),
                    "has_photo_region": isinstance(player.get("photo_region"), dict),
                    "photo_preview_url": (photo_previews.get(idx) or {}).get("preview_url"),
                    "photo_preview_mode": (photo_previews.get(idx) or {}).get("preview_mode"),
                }
            )

        assets = [_session_asset_payload(asset) for asset in review_session.assets]
        return templates.TemplateResponse(
            "registration_review_detail.html",
            {
                "request": request,
                "review_session": review_session,
                "assets": assets,
                "team": extraction.get("team") or {},
                "manager": extraction.get("manager") or {},
                "players": players,
                "notes": extraction.get("notes") or "",
                "validation": validation,
                "layout_regions": layout_regions,
                "overall_confidence": f"{float(extraction.get('overall_confidence') or 0.0) * 100:.0f}%",
                "canonical_review": canonical_review,
                "tournament_options": _review_tournament_options(review_session.tournament_slug or "copa_telmex"),
            },
        )


@app.post("/api/registration-review/{session_id}/edit")
async def edit_registration_review_session(session_id: str, request: Request):
    """Persist operator edits into the draft."""
    _ensure_registration_review_access(request)
    actor = _review_session_actor(request)
    if not actor.get("user_id"):
        raise HTTPException(status_code=401, detail="Sesión inválida para editar el draft.")
    try:
        session_uuid = UUID(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid review session ID") from exc

    form_data = await request.form()
    async with async_session_maker() as session:
        result = await session.execute(
            select(RegistrationReviewSession)
            .options(selectinload(RegistrationReviewSession.draft))
            .where(RegistrationReviewSession.id == session_uuid)
        )
        review_session = result.scalar_one_or_none()
        if not review_session or not review_session.draft:
            raise HTTPException(status_code=404, detail="Review draft not found")
        _ensure_review_session_mutable(review_session)

        base_extraction = _get_review_extraction(review_session.draft)
        edited_extraction = _apply_review_form_edits(form_data, base_extraction)
        _, changed_at_iso = _review_event_timestamp()
        corrections = _build_field_corrections(
            base_extraction,
            edited_extraction,
            actor=actor,
            changed_at=changed_at_iso,
        )
        existing_audit = _review_audit_state(
            review_session.draft.validation if isinstance(review_session.draft.validation, dict) else None
        )
        review_session.tournament_slug = (form_data.get("tournament_slug") or "").strip() or None
        validation = _build_review_commit_validation(
            edited_extraction,
            _build_review_validation(
                edited_extraction,
                raw_payload=review_session.draft.ocr_raw if isinstance(review_session.draft.ocr_raw, dict) else None,
            ),
            review_session=review_session,
        )
        existing_audit["extraction_metadata"] = existing_audit.get("extraction_metadata") or _build_review_extraction_metadata(
            review_session.draft.ocr_raw if isinstance(review_session.draft.ocr_raw, dict) else None,
            edited_extraction,
            review_session=review_session,
            assets=list(review_session.assets or []),
        )
        if corrections:
            existing_audit["field_corrections"] = list(existing_audit.get("field_corrections") or []) + corrections
            existing_audit["edit_events"] = list(existing_audit.get("edit_events") or []) + [
                {
                    "changed_by": actor.get("user_id"),
                    "changed_role": actor.get("role"),
                    "changed_at": changed_at_iso,
                    "field_corrections_count": len(corrections),
                }
            ]
        validation = _attach_review_audit(validation, existing_audit)
        review_session.draft.review_edits = edited_extraction
        review_session.draft.validation = validation
        review_session.draft.needs_review = bool(validation.get("needs_review"))
        review_session.draft.overall_confidence = float(
            edited_extraction.get("overall_confidence") or review_session.draft.overall_confidence or 0.0
        )
        review_session.status = "ready"
        await session.commit()
        _log_registration_review_event(
            "draft_edited",
            session_id=review_session.id,
            status="ok",
            extra={
                "actor_id": actor.get("user_id"),
                "corrections": len(corrections),
            },
        )

    return RedirectResponse(url=f"/registration-review/{session_id}", status_code=303)


@app.post("/api/registration-review/{session_id}/reject")
async def reject_registration_review_session(session_id: str, request: Request):
    """Reject a review draft without deleting its evidence or audit trail."""
    _ensure_registration_review_access(request)
    actor = _review_session_actor(request)
    if not actor.get("user_id"):
        raise HTTPException(status_code=401, detail="Sesión inválida para rechazar el draft.")
    try:
        session_uuid = UUID(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid review session ID") from exc

    form_data = await request.form()
    reason = str(form_data.get("reason") or "").strip()
    if len(reason) > 500:
        raise _review_error(
            "rejection_reason_too_long",
            "El motivo de rechazo no puede exceder 500 caracteres.",
        )

    async with async_session_maker() as session:
        result = await session.execute(
            select(RegistrationReviewSession)
            .options(selectinload(RegistrationReviewSession.draft))
            .where(RegistrationReviewSession.id == session_uuid)
        )
        review_session = result.scalar_one_or_none()
        if not review_session or not review_session.draft:
            raise HTTPException(status_code=404, detail="Review draft not found")
        _ensure_review_session_mutable(review_session)

        _rejected_at, rejected_at_iso = _review_event_timestamp()
        validation = (
            dict(review_session.draft.validation)
            if isinstance(review_session.draft.validation, dict)
            else {}
        )
        audit = _review_audit_state(validation)
        rejection_event = {
            "rejected_by": actor.get("user_id"),
            "rejected_role": actor.get("role"),
            "rejected_at": rejected_at_iso,
            "reason": reason or None,
        }
        audit["rejection_events"] = list(audit.get("rejection_events") or []) + [
            rejection_event
        ]
        audit["latest_rejection"] = rejection_event
        validation = _attach_review_audit(validation, audit)
        validation["ready_to_commit"] = False
        validation["needs_review"] = True

        review_session.status = "rejected"
        review_session.draft.validation = validation
        review_session.draft.needs_review = True
        await session.commit()
        _log_registration_review_event(
            "draft_rejected",
            session_id=review_session.id,
            status="rejected",
            extra={
                "actor_id": actor.get("user_id"),
                "reason_provided": bool(reason),
            },
        )

    return RedirectResponse(url=f"/registration-review/{session_id}", status_code=303)


@app.post("/api/registration-review/{session_id}/reprocess")
async def reprocess_registration_review_session(session_id: str, request: Request):
    """Re-run OCR over the primary image asset."""
    _ensure_registration_review_access(request)
    try:
        session_uuid = UUID(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid review session ID") from exc

    async with async_session_maker() as session:
        result = await session.execute(
            select(RegistrationReviewSession)
            .options(
                selectinload(RegistrationReviewSession.assets),
                selectinload(RegistrationReviewSession.draft),
            )
            .where(RegistrationReviewSession.id == session_uuid)
        )
        review_session = result.scalar_one_or_none()
        if not review_session:
            raise HTTPException(status_code=404, detail="Review session not found")
        _ensure_review_session_mutable(review_session)
        if not review_session.assets:
            raise HTTPException(status_code=400, detail="La sesión no tiene imágenes para reprocesar.")

        review_session.status = "processing"
        asset_payloads = [
            {
                "page_index": asset.page_index,
                "image_path": asset.image_path,
                "width": asset.width,
                "height": asset.height,
            }
            for asset in review_session.assets
        ]
        extraction, raw_payload, detected_provider, layout_regions = await _process_review_assets(asset_payloads)
        review_session.provider = detected_provider or review_session.provider
        await _upsert_review_draft(
            session,
            review_session,
            extraction,
            raw_payload,
            layout_regions=layout_regions,
        )
        await session.commit()

    return RedirectResponse(url=f"/registration-review/{session_id}", status_code=303)


@app.post("/api/registration-review/{session_id}/assets")
async def append_assets_to_registration_review_session(session_id: str, request: Request):
    """Append extra images to an existing review session and merge them into the current draft."""
    _ensure_registration_review_access(request)
    try:
        session_uuid = UUID(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid review session ID") from exc

    form_data = await request.form()
    uploads = [upload for upload in form_data.getlist("files") if getattr(upload, "filename", None)]
    if not uploads:
        raise _review_error("missing_files", "Sube al menos una imagen nueva.")
    if len(uploads) > REVIEW_SESSION_MAX_FILES:
        raise _review_error(
            "too_many_files",
            f"Solo se permiten hasta {REVIEW_SESSION_MAX_FILES} imágenes por operación.",
        )

    async with async_session_maker() as session:
        result = await session.execute(
            select(RegistrationReviewSession)
            .options(
                selectinload(RegistrationReviewSession.assets),
                selectinload(RegistrationReviewSession.draft),
            )
            .where(RegistrationReviewSession.id == session_uuid)
        )
        review_session = result.scalar_one_or_none()
        if not review_session or not review_session.draft:
            raise HTTPException(status_code=404, detail="Review session not found")
        _ensure_review_session_mutable(review_session)
        if len(review_session.assets) >= REVIEW_SESSION_MAX_FILES:
            raise _review_error(
                "too_many_files",
                f"Solo se permiten hasta {REVIEW_SESSION_MAX_FILES} páginas por revisión.",
            )

        next_index = (max((asset.page_index for asset in review_session.assets), default=0) or 0) + 1
        stored_assets = await _store_review_uploads(review_session.id, uploads, start_index=next_index)
        if not stored_assets:
            raise HTTPException(status_code=400, detail="No pude guardar las nuevas imágenes.")

        for asset_payload in stored_assets:
            session.add(
                RegistrationReviewAsset(
                    session_id=review_session.id,
                    page_index=asset_payload["page_index"],
                    image_path=asset_payload["image_path"],
                    sha256=asset_payload["sha256"],
                    width=asset_payload["width"],
                    height=asset_payload["height"],
                )
            )

        review_session.status = "processing"
        incoming_extraction, incoming_raw, detected_provider, incoming_layout = await _process_review_assets(stored_assets)
        base_extraction = _get_review_extraction(review_session.draft)
        existing_layout = review_session.draft.layout_regions if isinstance(review_session.draft.layout_regions, dict) else {}
        merged_extraction, merged_raw, merged_layout = _append_review_pages_to_extraction(
            base_extraction,
            incoming_extraction=incoming_extraction,
            incoming_raw={
                "provider": detected_provider,
                "page_count": len(stored_assets),
                "pages": list((review_session.draft.ocr_raw or {}).get("pages") or []) + list((incoming_raw or {}).get("pages") or []),
            },
            incoming_layout=incoming_layout,
            existing_layout=existing_layout,
        )

        await _upsert_review_draft(
            session,
            review_session,
            merged_extraction,
            merged_raw,
            layout_regions=merged_layout,
        )
        review_session.provider = detected_provider or review_session.provider
        await session.commit()

    return RedirectResponse(url=f"/registration-review/{session_id}", status_code=303)


@app.post("/api/registration-review/{session_id}/commit")
async def commit_registration_review_session(session_id: str, request: Request):
    """Commit a reviewed draft into Team/Player/OCRRegistration tables."""
    _ensure_registration_review_access(request)
    actor = _review_session_actor(request)
    if not actor.get("user_id"):
        raise HTTPException(status_code=401, detail="Sesión inválida para aprobar la captura.")
    try:
        session_uuid = UUID(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid review session ID") from exc

    async with async_session_maker() as session:
        result = await session.execute(
            select(RegistrationReviewSession)
            .options(
                selectinload(RegistrationReviewSession.assets),
                selectinload(RegistrationReviewSession.draft),
            )
            .where(RegistrationReviewSession.id == session_uuid)
        )
        review_session = result.scalar_one_or_none()
        if not review_session:
            raise HTTPException(status_code=404, detail="Review session not found")
        if not review_session.draft:
            raise _review_error(
                "blocked",
                "La sesión no tiene un draft listo para capturar.",
                extra={
                    "status": "blocked",
                    "blockers": [
                        {
                            "code": "DRAFT_MISSING",
                            "field": "draft",
                            "message": "La sesión no tiene draft activo para capturar.",
                        }
                    ],
                    "warnings": [],
                },
            )
        _ensure_review_session_not_rejected(review_session)

        form_data = await request.form()
        extraction = _apply_review_form_edits(form_data, _get_review_extraction(review_session.draft))
        approved_at_dt, approved_at_iso = _review_event_timestamp()
        commit_request_id = secrets.token_hex(12)
        review_session.tournament_slug = (form_data.get("tournament_slug") or review_session.tournament_slug or "").strip() or None
        existing_audit = _review_audit_state(
            review_session.draft.validation if isinstance(review_session.draft.validation, dict) else None
        )
        existing_audit["extraction_metadata"] = existing_audit.get("extraction_metadata") or _build_review_extraction_metadata(
            review_session.draft.ocr_raw if isinstance(review_session.draft.ocr_raw, dict) else None,
            extraction,
            review_session=review_session,
            assets=list(review_session.assets or []),
        )
        validation = _build_review_commit_validation(
            extraction,
            _build_review_validation(
                extraction,
                raw_payload=review_session.draft.ocr_raw if isinstance(review_session.draft.ocr_raw, dict) else None,
            ),
            review_session=review_session,
        )
        validation = _attach_review_audit(validation, existing_audit)
        team_payload = extraction.get("team") or {}
        team_name = (team_payload.get("name") or "").strip()
        players_payload = [
            player
            for player in (extraction.get("players") or [])
            if _player_has_usable_signal(player) and (player.get("name") or "").strip()
        ]
        tournament_slug = (review_session.tournament_slug or "").strip() or None

        if not tournament_slug:
            validation.setdefault("blockers", []).append(
                {
                    "code": "REVIEW_NOT_READY",
                    "field": "tournament_slug",
                    "message": "Selecciona el torneo antes de capturar.",
                }
            )
            validation["blockers"] = _dedupe_review_items(validation.get("blockers") or [])
            validation["ready_to_commit"] = False

        if validation.get("blockers"):
            commit_audit = _build_commit_audit_envelope(
                review_session=review_session,
                draft=review_session.draft,
                actor=actor,
                validation=validation,
                extraction=extraction,
                outcome="blocked",
                commit_request_id=commit_request_id,
                approved_at=approved_at_iso,
            )
            audit = _review_audit_state(validation)
            audit["approved_by"] = commit_audit["approved_by"]
            audit["approved_at"] = approved_at_iso
            audit["latest_commit"] = commit_audit
            audit["commit_events"] = list(audit.get("commit_events") or []) + [commit_audit]
            validation = _attach_review_audit(validation, audit)
            review_session.draft.review_edits = extraction
            review_session.draft.validation = validation
            review_session.draft.needs_review = True
            review_session.status = "ready"
            await session.commit()
            _log_registration_review_event(
                "commit_blocked",
                session_id=review_session.id,
                status="blocked",
                extra={
                    "actor_id": actor.get("user_id"),
                    "blockers": len(validation.get("blockers") or []),
                    "players": len(list(extraction.get("players") or [])),
                },
            )
            raise _review_error(
                "blocked",
                "La revisión tiene bloqueos y no se puede capturar todavía.",
                extra={
                    "status": "blocked",
                    "blockers": validation.get("blockers") or [],
                    "warnings": validation.get("warnings") or [],
                },
            )

        manager_payload = extraction.get("manager") or {}
        copa_db = CopaTelmexDB(session)
        team_chat_id = review_session.telegram_chat_id if review_session.telegram_chat_id is not None else None
        team = await copa_db.get_team_by_name(
            name=team_name,
            category=team_payload.get("category"),
            telegram_chat_id=team_chat_id,
            tournament_slug=tournament_slug,
        )
        if not team:
            team = await copa_db.create_team(
                name=team_name,
                telegram_chat_id=review_session.telegram_chat_id,
                tournament_slug=tournament_slug,
                gender=team_payload.get("gender"),
                category=team_payload.get("category"),
                league=team_payload.get("league"),
                representative_name=manager_payload.get("name"),
                state=team_payload.get("state"),
                municipality=team_payload.get("municipality"),
                telegram_user_id=review_session.telegram_user_id,
                roster_image_path=_storage_relative_path(review_session.assets[0].image_path) if review_session.assets else None,
            )
        else:
            team.tournament_slug = tournament_slug
            team.gender = team_payload.get("gender") or team.gender
            team.category = team_payload.get("category") or team.category
            team.league = team_payload.get("league") or team.league
            team.representative_name = manager_payload.get("name") or team.representative_name
            team.state = team_payload.get("state") or team.state
            team.municipality = team_payload.get("municipality") or team.municipality
            if review_session.assets and not team.roster_image_path:
                team.roster_image_path = _storage_relative_path(review_session.assets[0].image_path)

        team.contact_phone = manager_payload.get("phone") or team.contact_phone
        team.contact_email = manager_payload.get("email") or team.contact_email

        layout_regions = review_session.draft.layout_regions if isinstance(review_session.draft.layout_regions, dict) else {}
        photo_artifacts = _build_review_photo_artifacts(
            team_id=team.id,
            players_payload=players_payload,
            assets=list(review_session.assets or []),
            layout_regions=layout_regions,
        )

        created_players = 0
        skipped_players = 0
        updated_player_photos = 0
        for idx, player_payload in enumerate(players_payload, 1):
            full_name = (player_payload.get("name") or "").strip()
            first_name = (player_payload.get("first_name") or "").strip()
            last_name = " ".join(
                part
                for part in [
                    (player_payload.get("paternal_surname") or "").strip(),
                    (player_payload.get("maternal_surname") or "").strip(),
                ]
                if part
            ).strip()
            if not first_name or not last_name:
                first_name, last_name = _split_player_name(full_name)

            birth_date = _parse_birth_date(player_payload.get("birth_date"))
            raw_curp = (player_payload.get("curp") or "").strip().upper() or None
            curp, curp_truncated = _normalize_curp_for_storage(raw_curp)
            player_needs_review = bool(player_payload.get("needs_review")) or curp_truncated or bool(raw_curp and curp and len(curp) != 18)
            existing = None
            if curp:
                existing = await copa_db.get_player_by_curp(curp)
            if not existing:
                existing = await copa_db.get_player_by_team_and_identity(
                    team_id=team.id,
                    first_name=first_name,
                    last_name=last_name,
                    birth_date=birth_date,
                )
            player_photo = photo_artifacts.get(idx) or {}
            if existing:
                if player_photo and not existing.photo_path:
                    existing.photo_path = player_photo.get("photo_path")
                    existing.photo_sha256 = player_photo.get("photo_sha256")
                    existing.photo_ahash = player_photo.get("photo_ahash")
                    updated_player_photos += 1
                if curp and not existing.curp:
                    existing.curp = curp
                skipped_players += 1
                continue

            verification_notes = f"Capturado desde revision web {review_session.id}"
            if curp_truncated and raw_curp:
                verification_notes += f" | CURP truncado para persistencia: {raw_curp}"

            await copa_db.create_player(
                team_id=team.id,
                first_name=first_name,
                last_name=last_name,
                birth_date=birth_date,
                curp=curp,
                photo_path=player_photo.get("photo_path"),
                photo_sha256=player_photo.get("photo_sha256"),
                photo_ahash=player_photo.get("photo_ahash"),
                ocr_confidence=float(player_payload.get("confidence") or 0.0),
                needs_review=player_needs_review,
                verified_by_human=True,
                verification_notes=verification_notes,
                roster_index=idx,
            )
            created_players += 1

        commit_audit = _build_commit_audit_envelope(
            review_session=review_session,
            draft=review_session.draft,
            actor=actor,
            validation=validation,
            extraction=extraction,
            outcome="committed",
            commit_request_id=commit_request_id,
            approved_at=approved_at_iso,
        )
        audit = _review_audit_state(validation)
        audit["approved_by"] = commit_audit["approved_by"]
        audit["approved_at"] = approved_at_iso
        audit["latest_commit"] = commit_audit
        audit["commit_events"] = list(audit.get("commit_events") or []) + [commit_audit]
        validation = _attach_review_audit(validation, audit)

        await copa_db.create_ocr_registration(
            telegram_chat_id=review_session.telegram_chat_id or 0,
            telegram_user_id=review_session.telegram_user_id,
            team_id=team.id,
            ocr_result={
                "provider": review_session.provider,
                "session_id": str(review_session.id),
                "source": "web_review",
                "assets": [_session_asset_payload(asset) for asset in review_session.assets],
                "extraction": extraction,
                "audit": commit_audit,
                "extraction_metadata": copy.deepcopy(audit.get("extraction_metadata") or {}),
            },
            validation_result=validation,
        )

        review_session.status = "committed"
        review_session.committed_team_id = team.id
        review_session.approved_at = approved_at_dt
        review_session.committed_at = approved_at_dt
        review_session.draft.review_edits = extraction
        review_session.draft.validation = validation
        review_session.draft.needs_review = False

        await copa_db.commit()
        _log_registration_review_event(
            "commit_succeeded",
            session_id=review_session.id,
            status="committed",
            extra={
                "actor_id": actor.get("user_id"),
                "players_created": created_players,
                "players_skipped": skipped_players,
                "updated_player_photos": updated_player_photos,
            },
        )

    return RedirectResponse(url=f"/team/{team.id}", status_code=303)


@app.post("/api/team/{team_id}/edit", response_class=JSONResponse)
async def edit_team(
    team_id: str,
    request: Request
):
    """Edit team details"""
    _ensure_team_player_mutation_access(request)
    try:
        from uuid import UUID

        # Get form data
        form_data = await request.form()

        async with async_session_maker() as session:
            copa_db = CopaTelmexDB(session)

            team = await copa_db.get_team_by_id(UUID(team_id))
            if not team:
                raise HTTPException(status_code=404, detail="Team not found")

            # Update team fields
            if 'name' in form_data:
                team.name = form_data['name']
            if 'category' in form_data:
                team.category = form_data['category']
            if 'gender' in form_data:
                team.gender = form_data['gender']
            if 'league' in form_data:
                team.league = form_data['league']
            if 'league_phone' in form_data:
                team.league_phone = form_data['league_phone']
            if 'league_address' in form_data:
                team.league_address = form_data['league_address']
            if 'representative_name' in form_data:
                team.representative_name = form_data['representative_name']
            if 'contact_phone' in form_data:
                team.contact_phone = form_data['contact_phone']
            if 'state' in form_data:
                team.state = form_data['state']
            if 'municipality' in form_data:
                team.municipality = form_data['municipality']

            await session.commit()

            return {"success": True, "message": "Equipo actualizado correctamente"}

    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid team ID")
    except HTTPException:
        raise
    except Exception:
        _raise_dashboard_internal_error("Error updating team")


@app.post("/api/player/{player_id}/edit", response_class=JSONResponse)
async def edit_player(
    player_id: str,
    request: Request
):
    """Edit player details"""
    _ensure_team_player_mutation_access(request)
    try:
        from uuid import UUID
        from devnous.copa_telmex.models import Player
        from sqlalchemy import select

        # Get form data
        form_data = await request.form()

        async with async_session_maker() as session:
            result = await session.execute(
                select(Player).where(Player.id == UUID(player_id))
            )
            player = result.scalar_one_or_none()

            if not player:
                raise HTTPException(status_code=404, detail="Player not found")

            # Update player fields
            if 'first_name' in form_data:
                player.first_name = form_data['first_name']
            if 'last_name' in form_data:
                player.last_name = form_data['last_name']
            if 'birth_date' in form_data and form_data['birth_date']:
                from datetime import datetime
                player.birth_date = datetime.strptime(form_data['birth_date'], '%Y-%m-%d').date()
            if 'curp' in form_data:
                player.curp = form_data['curp'] if form_data['curp'] else None
            if 'email' in form_data:
                player.email = form_data['email'] if form_data['email'] else None

            await session.commit()

            return {"success": True, "message": "Jugador actualizado correctamente"}

    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid player ID or date format")
    except HTTPException:
        raise
    except Exception:
        _raise_dashboard_internal_error("Error updating player")


@app.post("/api/player/{player_id}/verify", response_class=JSONResponse)
async def verify_player(player_id: str, request: Request):
    """Mark player as verified by human"""
    _ensure_team_player_mutation_access(request)
    try:
        from uuid import UUID
        from devnous.copa_telmex.models import Player
        from sqlalchemy import select

        async with async_session_maker() as session:
            result = await session.execute(
                select(Player).where(Player.id == UUID(player_id))
            )
            player = result.scalar_one_or_none()

            if not player:
                raise HTTPException(status_code=404, detail="Player not found")

            player.verified_by_human = True
            player.needs_review = False

            await session.commit()

            return {"success": True, "message": "Jugador verificado correctamente"}

    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid player ID")
    except HTTPException:
        raise
    except Exception:
        _raise_dashboard_internal_error("Error verifying player")


@app.delete("/api/player/{player_id}", response_class=JSONResponse)
async def delete_player(player_id: str, request: Request):
    """Delete a player"""
    _ensure_team_player_mutation_access(request)
    try:
        from uuid import UUID
        from devnous.copa_telmex.models import Player
        from sqlalchemy import select

        async with async_session_maker() as session:
            result = await session.execute(
                select(Player).where(Player.id == UUID(player_id))
            )
            player = result.scalar_one_or_none()

            if not player:
                raise HTTPException(status_code=404, detail="Player not found")

            await session.delete(player)
            await session.commit()

            return {"success": True, "message": "Jugador eliminado correctamente"}

    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid player ID")
    except HTTPException:
        raise
    except Exception:
        _raise_dashboard_internal_error("Error deleting player")


@app.delete("/api/team/{team_id}", response_class=JSONResponse)
async def delete_team(team_id: str, request: Request):
    """Delete a team and all its players"""
    _ensure_team_player_mutation_access(request)
    try:
        from uuid import UUID

        async with async_session_maker() as session:
            copa_db = CopaTelmexDB(session)

            team = await copa_db.get_team_by_id(UUID(team_id))
            if not team:
                raise HTTPException(status_code=404, detail="Team not found")

            # Delete team (cascade will delete players)
            await session.delete(team)
            await session.commit()

            return {"success": True, "message": "Equipo eliminado correctamente"}

    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid team ID")
    except HTTPException:
        raise
    except Exception:
        _raise_dashboard_internal_error("Error deleting team")


if __name__ == "__main__":
    print("=" * 60)
    print("🏆 Copa Telmex - Web Dashboard")
    print("=" * 60)
    print()
    print("📊 Starting web server...")
    print("🌐 Open your browser to: http://localhost:8000")
    print()
    print("Press Ctrl+C to stop")
    print()

    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
