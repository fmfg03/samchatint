"""
Dedicated Telegram intake bot for Copa Telmex registration documents.

This bot intentionally exposes only the registration OCR intake surface. It
creates web review sessions through OperationsModule and never commits teams or
players directly.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple
from uuid import UUID

import aiohttp
import yaml
from PIL import Image, UnidentifiedImageError
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import aliased

from devnous.copa_telmex.draft_versioning import append_draft_version
from devnous.gastos.schema_guard import apply_schema_guard, check_schema_health
from devnous.tournaments.core.operations_module import OperationsModule
from devnous.tournaments.core.telegram_security import (
    TelegramActor,
    actor_from_callback,
    actor_from_message,
)
from devnous.tournaments.core.tournament_bot import Message, MessageIntent

from .ctt_review_handoff import CttCanonicalReviewSink
from .ctt_shadow_observer import CttRegistrationShadowObserver

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[5]
TEAM_UPLOAD_MAX_PAGES = 3


def _parse_int_set(raw_value: Optional[str]) -> Set[int]:
    values: Set[int] = set()
    if not raw_value:
        return values
    for token in raw_value.replace("\n", ",").replace(" ", ",").split(","):
        token = token.strip()
        if not token:
            continue
        try:
            values.add(int(token))
        except ValueError:
            logger.warning(
                "Ignoring invalid Telegram id in registration bot allowlist: %r", token
            )
    return values


def _normalize_label(value: Any) -> str:
    raw = str(value or "").strip().lower()
    normalized = unicodedata.normalize("NFKD", raw)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def _load_yaml_config(config_path: Optional[str]) -> Dict[str, Any]:
    if not config_path:
        return {}
    path = Path(config_path)
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _default_config_path() -> Optional[str]:
    dedicated = _REPO_ROOT / "config" / "registration_bot.yaml"
    if dedicated.exists():
        return str(dedicated)
    fallback = _REPO_ROOT / "config" / "copa_telmex.yaml"
    return str(fallback) if fallback.exists() else None


def _normalize_async_db_url(db_url: str) -> str:
    if db_url.startswith("postgresql://") and "+asyncpg" not in db_url:
        return db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return db_url


@dataclass(frozen=True)
class RegistrationBotEmployee:
    telegram_user_id: int
    nombre: str
    rol: str
    departamento: str


@dataclass
class TeamUploadSession:
    """Private in-memory collection for one explicit team upload."""

    user_id: int
    pages: List[bytes] = field(default_factory=list)
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    touched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class RegistrationBotAccessPolicy:
    """Authorization for the registration-only Telegram bot."""

    def __init__(
        self,
        *,
        session_maker=None,
        mode: Optional[str] = None,
        allowed_user_ids: Optional[Iterable[int]] = None,
        allowed_chat_ids: Optional[Iterable[int]] = None,
        allowed_roles: Optional[Iterable[str]] = None,
        allowed_departments: Optional[Iterable[str]] = None,
    ) -> None:
        self.session_maker = session_maker
        self.mode = (
            (mode or os.getenv("REGISTRATION_BOT_ACCESS_MODE") or "db").strip().lower()
        )
        if self.mode not in {"allowlist", "db"}:
            self.mode = "db"

        self.allowed_user_ids = set(allowed_user_ids or ()) | _parse_int_set(
            os.getenv("REGISTRATION_BOT_ALLOWED_USER_IDS")
        )
        self.allowed_chat_ids = set(allowed_chat_ids or ()) | _parse_int_set(
            os.getenv("REGISTRATION_BOT_ALLOWED_CHAT_IDS")
        )
        roles_env = os.getenv("REGISTRATION_BOT_ALLOWED_ROLES")
        departments_env = os.getenv("REGISTRATION_BOT_ALLOWED_DEPARTMENTS")
        roles = (
            allowed_roles
            if allowed_roles is not None
            else (roles_env or "superadmin").split(",")
        )
        departments = (
            allowed_departments
            if allowed_departments is not None
            else (departments_env or "operaciones").split(",")
        )
        self.allowed_roles = {
            _normalize_label(role) for role in roles if str(role or "").strip()
        }
        self.allowed_departments = {
            _normalize_label(department)
            for department in departments
            if str(department or "").strip()
        }

    def _allowlisted(self, actor: TelegramActor) -> bool:
        return actor.chat_id in self.allowed_chat_ids or (
            actor.user_id is not None and actor.user_id in self.allowed_user_ids
        )

    async def is_allowed(self, actor: TelegramActor) -> bool:
        if self._allowlisted(actor):
            return True
        if self.mode == "allowlist":
            return False
        employee = await self._lookup_employee(actor.user_id)
        if employee is None:
            return False
        role = _normalize_label(employee.rol)
        department = _normalize_label(employee.departamento)
        return role in self.allowed_roles or department in self.allowed_departments

    async def _lookup_employee(
        self, user_id: Optional[int]
    ) -> Optional[RegistrationBotEmployee]:
        if user_id is None or self.session_maker is None:
            return None
        async with self.session_maker() as session:
            result = await session.execute(
                text(
                    """
                    SELECT telegram_user_id, nombre, rol, departamento
                    FROM empleados
                    WHERE telegram_user_id = :telegram_user_id
                      AND activo = TRUE
                    """
                ),
                {"telegram_user_id": int(user_id)},
            )
            row = result.fetchone()
        if not row:
            return None
        return RegistrationBotEmployee(
            telegram_user_id=int(row[0]),
            nombre=str(row[1] or ""),
            rol=str(row[2] or ""),
            departamento=str(row[3] or ""),
        )

    def describe(self) -> str:
        return (
            f"{self.mode}(users={len(self.allowed_user_ids)}, chats={len(self.allowed_chat_ids)}, "
            f"roles={sorted(self.allowed_roles)}, departments={sorted(self.allowed_departments)})"
        )


class RegistrationIntakeBot:
    """Registration-only runtime wrapper around OperationsModule."""

    def __init__(
        self,
        *,
        config_path: Optional[str] = None,
        telegram_token: Optional[str] = None,
        anthropic_key: Optional[str] = None,
        openai_key: Optional[str] = None,
        shadow_observer: Optional[CttRegistrationShadowObserver] = None,
    ) -> None:
        self.tournament_id = "copa_telmex"
        self.config_path = config_path or _default_config_path()
        self.config = _load_yaml_config(self.config_path)
        self.telegram_token = telegram_token or os.getenv("REGISTRATION_BOT_TOKEN")
        self.anthropic_key = anthropic_key or os.getenv("ANTHROPIC_API_KEY")
        self.openai_key = openai_key or os.getenv("OPENAI_API_KEY")
        self.db_engine = None
        self.async_session_maker = None
        self.db_session = None
        self._setup_database()
        self.operations = OperationsModule(
            tournament_id=self.tournament_id,
            config=self.config,
            db=self.db_session,
            anthropic_key=self.anthropic_key,
            openai_key=self.openai_key,
        )
        self.shadow_observer = (
            shadow_observer or CttRegistrationShadowObserver.from_environment()
        )
        self.canonical_review_sink = None
        if self.db_session is not None:
            self.canonical_review_sink = CttCanonicalReviewSink(
                session_maker=self.db_session,
                photos_base_dir=self.operations.photos_base_dir,
            )
            bind_result_handler = getattr(
                self.shadow_observer, "bind_result_handler", None
            )
            if callable(bind_result_handler):
                bind_result_handler(self.canonical_review_sink.persist)
        self.finance = None
        self.marketing = None
        self.active_sessions_by_chat: Dict[int, str] = {}
        self.active_session_touched_at: Dict[int, datetime] = {}
        self.reupload_sessions_by_chat: Dict[int, str] = {}
        self.team_upload_sessions_by_chat: Dict[int, TeamUploadSession] = {}
        configured_timeout = (self.config.get("telegram") or {}).get(
            "session_idle_timeout_seconds"
        )
        self.session_idle_timeout_seconds = _env_int(
            "REGISTRATION_BOT_SESSION_IDLE_TIMEOUT_SECONDS",
            int(configured_timeout or 180),
        )
        configured_upload_timeout = (self.config.get("telegram") or {}).get(
            "team_upload_idle_timeout_seconds"
        )
        self.team_upload_idle_timeout_seconds = _env_seconds(
            "REGISTRATION_BOT_TEAM_UPLOAD_IDLE_TIMEOUT_SECONDS",
            int(configured_upload_timeout or 900),
        )

    def _setup_database(self) -> None:
        db_url = (
            os.getenv("COPA_TELMEX_DATABASE_URL")
            or os.getenv("TOURNAMENT_DATABASE_URL")
            or os.getenv("EXPENSES_DATABASE_URL")
            or os.getenv("DATABASE_URL")
            or os.getenv("POSTGRESQL_URL")
            or ""
        ).strip()
        if not db_url:
            logger.warning("Registration bot started without database URL")
            return
        self.db_engine = create_async_engine(
            _normalize_async_db_url(db_url),
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,
        )
        self.async_session_maker = async_sessionmaker(
            self.db_engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        self.db_session = self.async_session_maker
        logger.info("Registration bot database connection initialized")

    async def ensure_schema(self) -> None:
        if not self.db_engine:
            return
        async with self.db_engine.begin() as conn:
            guard_report = await apply_schema_guard(conn, logger=logger, strict=False)
            health_report = await check_schema_health(conn)
        if guard_report.get("failed_count"):
            logger.warning("Schema guard had failures: %s", guard_report.get("failed"))
        if not health_report.get("ok"):
            logger.warning("Schema health still has gaps: %s", health_report)

    async def process_registration_image(
        self, *, chat_id: int, user_id: int, image_bytes: bytes
    ):
        previous_session_id = self._active_or_pending_session_id(chat_id)
        await self._capture_shadow_page(
            chat_id,
            image_bytes,
            review_session_id=previous_session_id,
        )
        # OperationsModule gates web-review creation through admin_chat_ids.
        # This bot performs its own access check before calling this method, so
        # an authorized intake chat is allowed to create a review session.
        self.operations.admin_chat_ids.add(int(chat_id))
        message = Message(
            text="registro_ocr",
            chat_id=chat_id,
            user_id=user_id,
            intent=MessageIntent.OPERATIONS,
            photo=image_bytes,
        )
        response = await self.operations.process_ocr_registration(message)
        await self._sync_intake_metadata_after_ocr(
            chat_id=chat_id,
            user_id=user_id,
            previous_session_id=previous_session_id,
        )
        return await self._decorate_response_with_folio(
            chat_id=chat_id, response=response
        )

    async def process_registration_pdf(
        self, *, chat_id: int, user_id: int, pdf_bytes: bytes
    ):
        pages = _render_pdf_pages(
            pdf_bytes, max_pages=self.operations._telegram_review_max_pages()
        )
        responses = []
        for page_bytes in pages:
            responses.append(
                await self.process_registration_image(
                    chat_id=chat_id,
                    user_id=user_id,
                    image_bytes=page_bytes,
                )
            )
        if not responses:
            return "No pude leer páginas del PDF."
        await self.finish_current_session(chat_id, reason="pdf_complete")
        return responses[-1]

    async def begin_team_upload(self, *, chat_id: int, user_id: int) -> str:
        """Start a new explicit 1-3 image collection for one team."""
        chat_id = int(chat_id)
        closed_note = ""
        if self._active_or_pending_session_id(chat_id):
            closed_note = await self.finish_current_session(
                chat_id,
                reason="new_team_upload",
            )
        self.reupload_sessions_by_chat.pop(chat_id, None)
        self.team_upload_sessions_by_chat[chat_id] = TeamUploadSession(
            user_id=int(user_id)
        )
        prefix = f"{closed_note}\n\n" if closed_note else ""
        return (
            f"{prefix}Carga por equipo iniciada. Envía la primera imagen como "
            "foto o archivo de imagen. La guardaré temporalmente y no ejecutaré "
            "OCR hasta que selecciones 'No, procesar equipo'."
        )

    def has_team_upload_session(self, chat_id: int) -> bool:
        return int(chat_id) in getattr(self, "team_upload_sessions_by_chat", {})

    def owns_team_upload_session(self, *, chat_id: int, user_id: int) -> bool:
        session = getattr(self, "team_upload_sessions_by_chat", {}).get(int(chat_id))
        return session is not None and session.user_id == int(user_id)

    def add_team_upload_page(
        self,
        *,
        chat_id: int,
        user_id: int,
        image_bytes: bytes,
    ) -> Dict[str, Any]:
        session = getattr(self, "team_upload_sessions_by_chat", {}).get(int(chat_id))
        if session is None:
            return {
                "accepted": False,
                "page_count": 0,
                "max_pages": TEAM_UPLOAD_MAX_PAGES,
                "message": "No hay una carga por equipo activa. Usa /subir_equipo.",
            }
        if session.user_id != int(user_id):
            return {
                "accepted": False,
                "page_count": len(session.pages),
                "max_pages": TEAM_UPLOAD_MAX_PAGES,
                "message": "Esta carga pertenece a otro operador del chat.",
            }
        if len(session.pages) >= TEAM_UPLOAD_MAX_PAGES:
            return {
                "accepted": False,
                "page_count": len(session.pages),
                "max_pages": TEAM_UPLOAD_MAX_PAGES,
                "message": "Este equipo ya tiene el máximo de 3 imágenes.",
            }
        session.pages.append(bytes(image_bytes))
        session.touched_at = datetime.now(timezone.utc)
        return {
            "accepted": True,
            "page_count": len(session.pages),
            "max_pages": TEAM_UPLOAD_MAX_PAGES,
        }

    async def expire_team_upload_if_needed(self, chat_id: int) -> bool:
        sessions = getattr(self, "team_upload_sessions_by_chat", {})
        session = sessions.get(int(chat_id))
        if session is None:
            return False
        timeout = float(getattr(self, "team_upload_idle_timeout_seconds", 900))
        age_seconds = (datetime.now(timezone.utc) - session.touched_at).total_seconds()
        if age_seconds < timeout:
            return False
        sessions.pop(int(chat_id), None)
        return True

    async def cancel_team_upload(self, *, chat_id: int, user_id: int) -> str:
        sessions = getattr(self, "team_upload_sessions_by_chat", {})
        session = sessions.get(int(chat_id))
        if session is None:
            return "No hay una carga por equipo activa."
        if session.user_id != int(user_id):
            return "Esta carga pertenece a otro operador del chat."
        sessions.pop(int(chat_id), None)
        return "Carga cancelada. Eliminé las imágenes temporales de este equipo."

    async def process_team_upload(self, *, chat_id: int, user_id: int):
        """Process a closed upload batch in arrival order and finalize the team."""
        sessions = getattr(self, "team_upload_sessions_by_chat", {})
        session = sessions.get(int(chat_id))
        if session is None:
            return "No hay una carga por equipo activa. Usa /subir_equipo."
        if session.user_id != int(user_id):
            return "Esta carga pertenece a otro operador del chat."
        if not session.pages:
            return "Aún no has enviado imágenes para este equipo."

        pages = tuple(session.pages)
        review_markup = None
        try:
            for image_bytes in pages:
                response = await self.process_registration_image(
                    chat_id=int(chat_id),
                    user_id=int(user_id),
                    image_bytes=image_bytes,
                )
                if isinstance(response, dict) and review_markup is None:
                    review_markup = response.get("reply_markup")
            closed = await self.finish_current_session(
                int(chat_id),
                reason="team_upload_complete",
            )
        finally:
            sessions.pop(int(chat_id), None)

        result: Dict[str, Any] = {
            "text": (f"Procesé el equipo completo con {len(pages)} imágenes.\n{closed}")
        }
        if review_markup:
            result["reply_markup"] = review_markup
        return result

    async def finish_current_session(
        self, chat_id: int, *, reason: str = "manual_finalizar"
    ) -> str:
        session_id = self._active_or_pending_session_id(chat_id)
        await self._finalize_shadow(chat_id, review_session_id=session_id)
        pending = self.operations.pending_back_photos.pop(chat_id, None)
        self.active_sessions_by_chat.pop(int(chat_id), None)
        self.active_session_touched_at.pop(int(chat_id), None)
        if not session_id and not pending:
            return "No hay una captura multipágina activa."
        if session_id:
            metadata = await self._set_intake_metadata(
                session_id=session_id,
                telegram_intake_status="INTAKE_CLOSED",
                quality_status="QUALITY_PENDING",
                closed_reason=reason,
            )
            folio = metadata.get("intake_folio") or _folio_for_session_id(session_id)
            return f"Listo. Cerré el expediente {folio}. La siguiente imagen iniciará otra precaptura."
        return "Listo. La siguiente imagen iniciará otra precaptura."

    async def reset_current_session(self, chat_id: int) -> str:
        await self._discard_shadow(chat_id)
        getattr(self, "team_upload_sessions_by_chat", {}).pop(int(chat_id), None)
        session_id = self._active_or_pending_session_id(chat_id)
        self.operations.pending_back_photos.pop(chat_id, None)
        self.operations.pending_saves.pop(chat_id, None)
        self.active_sessions_by_chat.pop(int(chat_id), None)
        self.active_session_touched_at.pop(int(chat_id), None)
        self.reupload_sessions_by_chat.pop(int(chat_id), None)
        if session_id:
            await self._set_intake_metadata(
                session_id=session_id,
                telegram_intake_status="INTAKE_CLOSED",
                quality_status="QUALITY_PENDING",
                closed_reason="manual_nuevo",
            )
        return "Listo. Empecemos con el siguiente equipo."

    async def status_for_folio(self, folio: str) -> str:
        match = await self._find_session_by_folio(folio)
        if not match:
            return f"No encontré el folio {folio.strip()}."
        session_id, metadata = match
        quality_status = metadata.get("quality_status") or "QUALITY_PENDING"
        intake_status = metadata.get("telegram_intake_status") or "INTAKE_CLOSED"
        page_count = metadata.get("page_count") or "?"
        return (
            f"Folio {metadata.get('intake_folio') or _folio_for_session_id(session_id)}\n"
            f"Intake: {intake_status}\n"
            f"Calidad: {quality_status}\n"
            f"Páginas: {page_count}\n"
            f"Revisión: {self._review_workspace_url(session_id)}"
        )

    async def start_reupload(self, *, chat_id: int, folio: str) -> str:
        match = await self._find_session_by_folio(folio)
        if not match:
            return f"No encontré el folio {folio.strip()}."
        session_id, metadata = match
        self.reupload_sessions_by_chat[int(chat_id)] = session_id
        self.active_sessions_by_chat[int(chat_id)] = session_id
        self.active_session_touched_at[int(chat_id)] = datetime.now(timezone.utc)
        await self._set_intake_metadata(
            session_id=session_id,
            telegram_intake_status="INTAKE_OPEN",
            quality_status="NEEDS_REUPLOAD",
            reupload_requested_at=_utc_now_iso(),
        )
        return (
            f"Listo. Envía ahora la imagen o PDF de reposición para "
            f"{metadata.get('intake_folio') or _folio_for_session_id(session_id)}."
        )

    def _active_or_pending_session_id(self, chat_id: int) -> Optional[str]:
        operations = getattr(self, "operations", None)
        pending_back_photos = getattr(operations, "pending_back_photos", {}) or {}
        pending = pending_back_photos.get(int(chat_id)) or {}
        return (
            str(pending.get("review_session_id") or "").strip()
            or getattr(self, "reupload_sessions_by_chat", {}).get(int(chat_id))
            or getattr(self, "active_sessions_by_chat", {}).get(int(chat_id))
        )

    async def _prepare_reupload_if_needed(self, chat_id: int) -> None:
        session_id = self.reupload_sessions_by_chat.get(int(chat_id))
        if not session_id:
            return
        if int(chat_id) in self.operations.pending_back_photos:
            return
        page_count = await self._asset_count_for_session(session_id)
        self.operations.pending_back_photos[int(chat_id)] = {
            "review_session_id": session_id,
            "provider": (self.operations.ocr_provider or "openai").strip().lower(),
            "page_count": page_count,
            "max_pages": max(
                page_count + 1, self.operations._telegram_review_max_pages()
            ),
        }

    async def _sync_intake_metadata_after_ocr(
        self,
        *,
        chat_id: int,
        user_id: int,
        previous_session_id: Optional[str],
    ) -> None:
        session_id = self._active_or_pending_session_id(chat_id) or previous_session_id
        if not session_id:
            return
        self.active_sessions_by_chat[int(chat_id)] = session_id
        self.active_session_touched_at[int(chat_id)] = datetime.now(timezone.utc)
        pending_after = self.operations.pending_back_photos.get(int(chat_id)) or {}
        page_count = int(
            pending_after.get("page_count")
            or await self._asset_count_for_session(session_id)
            or 1
        )
        intake_status = "INTAKE_OPEN" if pending_after else "INTAKE_CLOSED"
        quality_status = (
            "QUALITY_PENDING" if intake_status == "INTAKE_CLOSED" else "OCR_READY"
        )
        closed_reason = "max_pages" if intake_status == "INTAKE_CLOSED" else None
        if int(chat_id) in self.reupload_sessions_by_chat:
            quality_status = "QUALITY_PENDING"
            if intake_status == "INTAKE_CLOSED":
                self.reupload_sessions_by_chat.pop(int(chat_id), None)
        await self._set_intake_metadata(
            session_id=session_id,
            telegram_user_id=user_id,
            telegram_chat_id=chat_id,
            telegram_intake_status=intake_status,
            quality_status=quality_status,
            closed_reason=closed_reason,
            page_count=page_count,
        )

    async def close_idle_session_if_needed(self, chat_id: int) -> Optional[str]:
        if int(chat_id) in self.reupload_sessions_by_chat:
            return None
        session_id = self._active_or_pending_session_id(chat_id)
        touched_at = self.active_session_touched_at.get(int(chat_id))
        if not session_id or touched_at is None:
            return None
        age_seconds = (datetime.now(timezone.utc) - touched_at).total_seconds()
        if age_seconds < float(self.session_idle_timeout_seconds):
            return None
        await self._finalize_shadow(chat_id)
        self.operations.pending_back_photos.pop(int(chat_id), None)
        self.active_sessions_by_chat.pop(int(chat_id), None)
        self.active_session_touched_at.pop(int(chat_id), None)
        metadata = await self._set_intake_metadata(
            session_id=session_id,
            telegram_intake_status="INTAKE_CLOSED",
            quality_status="QUALITY_PENDING",
            closed_reason="idle_timeout",
        )
        return str(metadata.get("intake_folio") or _folio_for_session_id(session_id))

    async def _decorate_response_with_folio(self, *, chat_id: int, response):
        session_id = self._active_or_pending_session_id(chat_id)
        if not session_id:
            return response
        metadata = await self._get_intake_metadata(session_id)
        folio = metadata.get("intake_folio") or _folio_for_session_id(session_id)
        line = f"\n\nFolio: {folio}"
        if isinstance(response, dict) and "text" in response:
            decorated = dict(response)
            if folio not in decorated["text"]:
                decorated["text"] = decorated["text"] + line
            return decorated
        text_value = str(response)
        return text_value if folio in text_value else text_value + line

    async def _asset_count_for_session(self, session_id: str) -> int:
        if not self.async_session_maker:
            return 0
        from devnous.copa_telmex.models import RegistrationReviewAsset

        async with self.async_session_maker() as session:
            result = await session.execute(
                select(RegistrationReviewAsset).where(
                    RegistrationReviewAsset.session_id == UUID(str(session_id))
                )
            )
            return len(result.scalars().all())

    async def _get_intake_metadata(self, session_id: str) -> Dict[str, Any]:
        if not self.async_session_maker:
            return {"intake_folio": _folio_for_session_id(session_id)}
        from devnous.copa_telmex.models import RegistrationReviewDraft

        async with self.async_session_maker() as session:
            result = await session.execute(
                select(RegistrationReviewDraft).where(
                    RegistrationReviewDraft.session_id == UUID(str(session_id))
                )
                .order_by(RegistrationReviewDraft.draft_version.desc())
                .limit(1)
            )
            draft = result.scalar_one_or_none()
            validation = (
                draft.validation if draft and isinstance(draft.validation, dict) else {}
            )
            return _intake_metadata_from_validation(validation, session_id=session_id)

    async def _set_intake_metadata(
        self, session_id: str, **updates: Any
    ) -> Dict[str, Any]:
        metadata = {"intake_folio": _folio_for_session_id(session_id)}
        if not self.async_session_maker:
            metadata.update(
                {key: value for key, value in updates.items() if value is not None}
            )
            return metadata
        from devnous.copa_telmex.models import (
            RegistrationReviewDraft,
            RegistrationReviewSession,
        )

        async with self.async_session_maker() as session:
            result = await session.execute(
                select(RegistrationReviewSession).where(
                    RegistrationReviewSession.id == UUID(str(session_id))
                )
            )
            review_session = result.scalar_one_or_none()
            draft_result = await session.execute(
                select(RegistrationReviewDraft).where(
                    RegistrationReviewDraft.session_id == UUID(str(session_id))
                )
                .order_by(RegistrationReviewDraft.draft_version.desc())
                .limit(1)
            )
            draft = draft_result.scalar_one_or_none()
            if draft is None:
                return metadata
            validation = dict(draft.validation or {})
            audit = dict(validation.get("audit") or {})
            metadata = _intake_metadata_from_validation(
                validation, session_id=session_id
            )
            for key, value in updates.items():
                if value is not None:
                    metadata[key] = value
            metadata["updated_at"] = _utc_now_iso()
            audit["telegram_intake"] = metadata
            validation["audit"] = audit
            if review_session is None:
                return metadata
            await append_draft_version(
                session,
                review_session,
                mutation_type="telegram_intake_metadata_updated",
                actor_id="telegram-intake-bot",
                expected_draft=draft,
                validation=validation,
            )
            if review_session is not None:
                if metadata.get("telegram_intake_status") == "INTAKE_OPEN":
                    review_session.status = "uploaded"
                elif metadata.get("quality_status") in {"QUALITY_PENDING", "OCR_READY"}:
                    review_session.status = "ready"
            await session.commit()
            return metadata

    async def _find_session_by_folio(
        self, folio: str
    ) -> Optional[Tuple[str, Dict[str, Any]]]:
        if not self.async_session_maker:
            return None
        from devnous.copa_telmex.models import (
            RegistrationReviewDraft,
            RegistrationReviewSession,
        )

        normalized = _normalize_folio(folio)
        if not normalized:
            return None
        async with self.async_session_maker() as session:
            draft_lookup = aliased(RegistrationReviewDraft)
            latest_draft_id = (
                select(draft_lookup.id)
                .where(
                    draft_lookup.session_id == RegistrationReviewSession.id
                )
                .order_by(
                    draft_lookup.draft_version.desc(),
                    draft_lookup.created_at.desc(),
                )
                .limit(1)
                .correlate(RegistrationReviewSession)
                .scalar_subquery()
            )
            result = await session.execute(
                select(RegistrationReviewSession, RegistrationReviewDraft)
                .join(
                    RegistrationReviewDraft,
                    RegistrationReviewDraft.id == latest_draft_id,
                )
                .where(RegistrationReviewSession.source == "telegram")
                .order_by(RegistrationReviewSession.started_at.desc())
                .limit(500)
            )
            rows = result.all()
        for review_session, draft in rows:
            session_id = str(review_session.id)
            metadata = _intake_metadata_from_validation(
                draft.validation if isinstance(draft.validation, dict) else {},
                session_id=session_id,
            )
            if _normalize_folio(metadata.get("intake_folio")) == normalized:
                return session_id, metadata
        return None

    def _review_workspace_url(self, session_id: str) -> str:
        return f"{(os.getenv('APP_URL') or 'https://sam.chat').rstrip('/')}/registration-review/{session_id}"

    async def _capture_shadow_page(
        self,
        chat_id: int,
        image_bytes: bytes,
        *,
        review_session_id: Optional[str] = None,
    ) -> None:
        observer = getattr(self, "shadow_observer", None)
        if observer is not None:
            await observer.capture_page(
                chat_id,
                image_bytes,
                review_session_id=review_session_id,
            )

    async def _finalize_shadow(
        self, chat_id: int, *, review_session_id: Optional[str] = None
    ) -> None:
        observer = getattr(self, "shadow_observer", None)
        if observer is not None:
            await observer.finalize(
                chat_id,
                review_session_id=review_session_id,
            )

    async def _discard_shadow(self, chat_id: int) -> None:
        observer = getattr(self, "shadow_observer", None)
        if observer is not None:
            await observer.discard(chat_id)

    async def cleanup(self) -> None:
        getattr(self, "team_upload_sessions_by_chat", {}).clear()
        observer = getattr(self, "shadow_observer", None)
        if observer is not None:
            await observer.close()
        if self.db_engine:
            await self.db_engine.dispose()

    async def run_telegram_bot(self) -> None:
        if not self.telegram_token:
            raise ValueError("REGISTRATION_BOT_TOKEN is required")
        await self.ensure_schema()
        access_policy = RegistrationBotAccessPolicy(
            session_maker=self.async_session_maker
        )
        adapter = RegistrationIntakeTelegramAdapter(
            self, self.telegram_token, access_policy=access_policy
        )
        logger.info(
            "Starting registration intake Telegram bot with access=%s",
            access_policy.describe(),
        )
        try:
            await adapter.run()
        finally:
            await self.cleanup()


class RegistrationIntakeTelegramAdapter:
    """Small Telegram adapter restricted to registration intake."""

    def __init__(
        self,
        bot: RegistrationIntakeBot,
        telegram_token: str,
        *,
        access_policy: Optional[RegistrationBotAccessPolicy] = None,
    ) -> None:
        self.bot = bot
        self.telegram_token = telegram_token
        self.api_base = f"https://api.telegram.org/bot{telegram_token}"
        self.last_update_id = 0
        self.access_policy = access_policy or RegistrationBotAccessPolicy(
            session_maker=bot.async_session_maker
        )
        self.max_image_bytes = _env_int(
            "REGISTRATION_BOT_MAX_IMAGE_BYTES", 12 * 1024 * 1024
        )
        self.max_document_bytes = _env_int(
            "REGISTRATION_BOT_MAX_DOCUMENT_BYTES", 20 * 1024 * 1024
        )

    async def handle_update(self, update: Dict[str, Any]) -> None:
        if "callback_query" in update:
            await self._handle_callback(update["callback_query"])
            return
        if "message" in update:
            await self._handle_message(update["message"])

    async def _handle_callback(self, callback_query: Dict[str, Any]) -> None:
        actor = actor_from_callback(callback_query)
        if not await self.access_policy.is_allowed(actor):
            await self.answer_callback_query(callback_query["id"], "No autorizado")
            return
        chat_id = int(callback_query["message"]["chat"]["id"])
        user_id = int(callback_query["from"]["id"])
        data = (callback_query.get("data") or "").strip()
        if data.startswith(
            "team_upload:"
        ) and await self.bot.expire_team_upload_if_needed(chat_id):
            await self.answer_callback_query(callback_query["id"], "Carga expirada")
            await self.send_message(
                chat_id,
                "La carga expiró por inactividad. Usa /subir_equipo para comenzar otra.",
            )
            return
        if (
            data.startswith("team_upload:")
            and self.bot.has_team_upload_session(chat_id)
            and not self.bot.owns_team_upload_session(
                chat_id=chat_id,
                user_id=user_id,
            )
        ):
            await self.answer_callback_query(
                callback_query["id"], "Carga de otro operador"
            )
            return
        if data == "team_upload:add":
            if not self.bot.has_team_upload_session(chat_id):
                await self.answer_callback_query(callback_query["id"], "Carga expirada")
                await self.send_message(
                    chat_id,
                    "La carga ya no está activa. Usa /subir_equipo para comenzar otra.",
                )
                return
            await self.answer_callback_query(callback_query["id"], "Envía la siguiente")
            await self.send_message(
                chat_id, "Envía ahora la siguiente imagen del equipo."
            )
            return
        if data == "team_upload:process":
            await self.answer_callback_query(callback_query["id"], "Procesando equipo")
            await self.send_message(chat_id, "Procesando las imágenes del equipo...")
            try:
                response = await self.bot.process_team_upload(
                    chat_id=chat_id,
                    user_id=user_id,
                )
            except Exception as exc:
                logger.warning("Team upload processing failed: %s", exc, exc_info=True)
                await self.send_message(
                    chat_id,
                    "No pude procesar el lote completo. La carga temporal fue cerrada.",
                )
                return
            await self._send_bot_response(chat_id, response)
            return
        if data == "team_upload:cancel":
            await self.answer_callback_query(callback_query["id"], "Carga cancelada")
            await self.send_message(
                chat_id,
                await self.bot.cancel_team_upload(
                    chat_id=chat_id,
                    user_id=user_id,
                ),
            )
            return
        if data == "back_done":
            await self.answer_callback_query(callback_query["id"], "Captura cerrada")
            await self.send_message(
                chat_id, await self.bot.finish_current_session(chat_id)
            )
            return
        await self.answer_callback_query(
            callback_query["id"], "Acción no disponible en este bot"
        )

    async def _handle_message(self, message: Dict[str, Any]) -> None:
        actor = actor_from_message(message)
        if not await self.access_policy.is_allowed(actor):
            await self.send_message(
                actor.chat_id,
                "Acceso restringido. Este bot solo acepta operadores autorizados.",
            )
            return

        chat_id = int(message["chat"]["id"])
        user_id = int(message["from"]["id"])
        text_value = (message.get("text") or "").strip()
        if text_value.startswith("/"):
            await self.send_message(
                chat_id,
                await self._handle_command(chat_id, user_id, text_value),
            )
            return

        if "photo" in message:
            photos = message["photo"]
            largest_photo = max(photos, key=lambda item: item.get("file_size", 0))
            file_bytes, _ = await self.download_file(
                largest_photo["file_id"], max_bytes=self.max_image_bytes
            )
            image_error = _validate_image_payload(file_bytes)
            if image_error:
                await self.send_message(chat_id, image_error)
                return
            await self._process_media(
                chat_id=chat_id, user_id=user_id, file_bytes=file_bytes, is_pdf=False
            )
            return

        document = message.get("document")
        if document:
            mime = (document.get("mime_type") or "").lower()
            file_name = (document.get("file_name") or "").lower()
            is_pdf = mime == "application/pdf" or file_name.endswith(".pdf")
            is_image = mime.startswith("image/")
            if not (is_pdf or is_image):
                await self.send_message(
                    chat_id, "Envía una foto, imagen o PDF de la cédula."
                )
                return
            max_bytes = self.max_document_bytes if is_pdf else self.max_image_bytes
            file_bytes, _ = await self.download_file(
                document["file_id"], max_bytes=max_bytes
            )
            if is_image:
                image_error = _validate_image_payload(file_bytes)
                if image_error:
                    await self.send_message(chat_id, image_error)
                    return
            await self._process_media(
                chat_id=chat_id, user_id=user_id, file_bytes=file_bytes, is_pdf=is_pdf
            )
            return

        await self.send_message(
            chat_id, "Envía una foto o PDF de la cédula para iniciar la precaptura."
        )

    async def _handle_command(
        self,
        chat_id: int,
        user_id: int,
        command: str,
    ) -> str:
        parts = command.split()
        normalized = parts[0].lower()
        if normalized in {"/start", "/help"}:
            return (
                "Bot de registro de equipos.\n\n"
                "Envía fotos o PDFs de cédulas para crear una precaptura web. "
                "La revisión y el guardado final se hacen en la plataforma.\n\n"
                "Comandos: /subir_equipo, /nuevo, /finalizar, /cancelar, "
                "/estado FOLIO, /reponer FOLIO, /help"
            )
        if normalized == "/subir_equipo":
            return await self.bot.begin_team_upload(
                chat_id=chat_id,
                user_id=user_id,
            )
        if normalized in {"/nuevo", "/cancelar"}:
            return await self.bot.reset_current_session(chat_id)
        if normalized in {"/finalizar", "/listo"}:
            return await self.bot.finish_current_session(chat_id)
        if normalized == "/estado":
            if len(parts) < 2:
                return "Uso: /estado REG-2026-XXXXXXXX"
            return await self.bot.status_for_folio(parts[1])
        if normalized == "/reponer":
            if len(parts) < 2:
                return "Uso: /reponer REG-2026-XXXXXXXX"
            return await self.bot.start_reupload(chat_id=chat_id, folio=parts[1])
        return "Comando no disponible en este bot. Usa /help."

    async def _process_media(
        self, *, chat_id: int, user_id: int, file_bytes: bytes, is_pdf: bool
    ) -> None:
        if await self.bot.expire_team_upload_if_needed(chat_id):
            await self.send_message(
                chat_id,
                "La carga por equipo expiró por inactividad. Usa /subir_equipo "
                "y vuelve a enviar las imágenes.",
            )
            return
        if self.bot.has_team_upload_session(chat_id):
            if is_pdf:
                await self.send_message(
                    chat_id,
                    "En el modo /subir_equipo envía cada página como imagen. "
                    "Usa /cancelar si prefieres procesar el PDF directamente.",
                )
                return
            result = self.bot.add_team_upload_page(
                chat_id=chat_id,
                user_id=user_id,
                image_bytes=file_bytes,
            )
            page_count = int(result["page_count"])
            max_pages = int(result["max_pages"])
            if not result["accepted"]:
                await self.send_message(
                    chat_id,
                    str(result["message"]),
                    reply_markup=_team_upload_keyboard(page_count, max_pages),
                )
                return
            if page_count >= max_pages:
                text_value = (
                    f"Imagen {page_count} de {max_pages} recibida. Ya alcanzaste "
                    "el máximo; procesa el equipo o cancela la carga."
                )
            else:
                text_value = (
                    f"Imagen {page_count} de {max_pages} recibida. "
                    "¿Este equipo tiene otra imagen?"
                )
            await self.send_message(
                chat_id,
                text_value,
                reply_markup=_team_upload_keyboard(page_count, max_pages),
            )
            return

        await self.send_message(chat_id, "Procesando cédula para precaptura web...")
        try:
            closed_folio = await self.bot.close_idle_session_if_needed(chat_id)
            if closed_folio:
                await self.send_message(
                    chat_id,
                    f"Cerré por inactividad el expediente anterior {closed_folio}. "
                    "Esta carga iniciará otro folio.",
                )
            await self.bot._prepare_reupload_if_needed(chat_id)
            if is_pdf:
                response = await self.bot.process_registration_pdf(
                    chat_id=chat_id,
                    user_id=user_id,
                    pdf_bytes=file_bytes,
                )
            else:
                response = await self.bot.process_registration_image(
                    chat_id=chat_id,
                    user_id=user_id,
                    image_bytes=file_bytes,
                )
        except Exception as exc:
            logger.warning(
                "Registration intake media processing failed: %s", exc, exc_info=True
            )
            await self.send_message(chat_id, f"No pude procesar el archivo: {exc}")
            return
        await self._send_bot_response(chat_id, response)

    async def _send_bot_response(self, chat_id: int, response: Any) -> None:
        if isinstance(response, dict) and "text" in response:
            await self.send_message(
                chat_id, response["text"], reply_markup=response.get("reply_markup")
            )
        else:
            await self.send_message(chat_id, str(response))

    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        parse_mode: Optional[str] = None,
        reply_markup: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        async with aiohttp.ClientSession() as session:
            payload: Dict[str, Any] = {"chat_id": chat_id, "text": text}
            if parse_mode:
                payload["parse_mode"] = parse_mode
            if reply_markup:
                payload["reply_markup"] = reply_markup
            async with session.post(
                f"{self.api_base}/sendMessage", json=payload
            ) as resp:
                return await resp.json()

    async def answer_callback_query(
        self, callback_query_id: str, text: Optional[str] = None
    ) -> Dict[str, Any]:
        async with aiohttp.ClientSession() as session:
            payload: Dict[str, Any] = {"callback_query_id": callback_query_id}
            if text:
                payload["text"] = text
            async with session.post(
                f"{self.api_base}/answerCallbackQuery", json=payload
            ) as resp:
                return await resp.json()

    async def download_file(self, file_id: str, *, max_bytes: int) -> Tuple[bytes, str]:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{self.api_base}/getFile", params={"file_id": file_id}
            ) as resp:
                result = await resp.json()
                file_path = result["result"]["file_path"]
            file_url = (
                f"https://api.telegram.org/file/bot{self.telegram_token}/{file_path}"
            )
            async with session.get(file_url) as resp:
                chunks: List[bytes] = []
                total = 0
                async for chunk in resp.content.iter_chunked(64 * 1024):
                    total += len(chunk)
                    if total > max_bytes:
                        raise ValueError(
                            "El archivo excede el tamaño máximo permitido."
                        )
                    chunks.append(chunk)
        return b"".join(chunks), file_path

    async def poll_updates(self) -> None:
        while True:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        f"{self.api_base}/getUpdates",
                        params={"offset": self.last_update_id + 1, "timeout": 30},
                        timeout=aiohttp.ClientTimeout(total=35),
                    ) as resp:
                        data = await resp.json()
                for update in data.get("result", []):
                    self.last_update_id = int(update["update_id"])
                    await self.handle_update(update)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Registration bot polling error: %s", exc, exc_info=True)
                await asyncio.sleep(5)

    async def run(self) -> None:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{self.api_base}/getMe") as resp:
                data = await resp.json()
                logger.info(
                    "Registration Telegram bot: @%s", data["result"]["username"]
                )
        await self.poll_updates()


def _team_upload_keyboard(page_count: int, max_pages: int) -> Dict[str, Any]:
    rows = []
    if page_count < max_pages:
        rows.append(
            [
                {
                    "text": "Sí, agregar otra imagen",
                    "callback_data": "team_upload:add",
                }
            ]
        )
    rows.extend(
        [
            [
                {
                    "text": "No, procesar equipo",
                    "callback_data": "team_upload:process",
                }
            ],
            [{"text": "Cancelar", "callback_data": "team_upload:cancel"}],
        ]
    )
    return {"inline_keyboard": rows}


def _env_int(key: str, default: int) -> int:
    raw = (os.getenv(key) or "").strip()
    if not raw:
        return default
    try:
        return max(1024, int(raw))
    except ValueError:
        logger.warning("Invalid integer for %s=%r; using default %s", key, raw, default)
        return default


def _env_seconds(key: str, default: int) -> int:
    raw = (os.getenv(key) or "").strip()
    if not raw:
        return default
    try:
        return max(60, int(raw))
    except ValueError:
        logger.warning(
            "Invalid duration for %s=%r; using default %s", key, raw, default
        )
        return default


def _validate_image_payload(file_bytes: bytes) -> Optional[str]:
    try:
        with Image.open(io.BytesIO(file_bytes)) as image:
            image.verify()
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError):
        return "No pude leer la imagen. Reenvíala en JPG, PNG o como PDF claro."
    return None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _folio_for_session_id(session_id: str) -> str:
    return f"REG-{datetime.now(timezone.utc).year}-{str(session_id).replace('-', '')[:8].upper()}"


def _normalize_folio(value: Any) -> str:
    return str(value or "").strip().upper()


def _intake_metadata_from_validation(
    validation: Optional[Dict[str, Any]],
    *,
    session_id: str,
) -> Dict[str, Any]:
    audit = validation.get("audit") if isinstance(validation, dict) else None
    metadata = audit.get("telegram_intake") if isinstance(audit, dict) else None
    payload = dict(metadata or {})
    payload.setdefault("schema_version", "registration_telegram_intake.v1")
    payload.setdefault("intake_folio", _folio_for_session_id(session_id))
    payload.setdefault("telegram_intake_status", "INTAKE_CLOSED")
    payload.setdefault("quality_status", "QUALITY_PENDING")
    return payload


def _render_pdf_pages(pdf_bytes: bytes, *, max_pages: int) -> Sequence[bytes]:
    try:
        import fitz  # type: ignore
    except Exception as exc:  # pragma: no cover - depends on runtime extras
        raise RuntimeError("Los PDF no están habilitados en este entorno.") from exc

    document = fitz.open(stream=pdf_bytes, filetype="pdf")
    pages: List[bytes] = []
    try:
        for page_index in range(min(int(max_pages), len(document))):
            page = document.load_page(page_index)
            pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            image = Image.frombytes(
                "RGB", (pixmap.width, pixmap.height), pixmap.samples
            )
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=95)
            pages.append(buffer.getvalue())
    finally:
        document.close()
    return pages


async def create_registration_intake_bot(
    *,
    config_path: Optional[str] = None,
    telegram_token: Optional[str] = None,
    anthropic_key: Optional[str] = None,
    openai_key: Optional[str] = None,
    shadow_observer: Optional[CttRegistrationShadowObserver] = None,
) -> RegistrationIntakeBot:
    return RegistrationIntakeBot(
        config_path=config_path,
        telegram_token=telegram_token,
        anthropic_key=anthropic_key,
        openai_key=openai_key,
        shadow_observer=shadow_observer,
    )
