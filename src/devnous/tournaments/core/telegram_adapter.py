"""
Telegram Adapter - Handles Telegram bot integration for tournament bots.

This adapter wraps a TournamentBot and provides Telegram interface:
- Polling for updates
- Photo download and processing
- Message formatting
- Inline keyboards for interactions
"""

import asyncio
import io
import logging
import os
import base64
import json
import re
from collections import deque
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from time import monotonic
from typing import Deque, Dict, Any, Optional, Set, Tuple, List
from uuid import UUID
import aiohttp
from PIL import Image, UnidentifiedImageError
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from ...gastos.services.tournament_phase_service import get_tournament_etapas
from ...gastos.services import documento_telegram as gastos_tg
from ...gastos.services.telegram_document_runtime import TelegramDocumentRuntime
from ...gastos.services.telegram_console import (
    telegram_console_bot_commands,
    telegram_console_bot_menu_lines,
)
from .telegram_command_surface import classify_telegram_command_surface
from .telegram_security import TelegramAccessControl, actor_from_callback, actor_from_message

logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class UploadedImage:
    image_id: str
    image_path: str
    meta_path: str


class UserFacingInputError(RuntimeError):
    """Raised when Telegram input must be rejected with a user-visible message."""


class TelegramAdapter:
    """
    Telegram adapter for TournamentBot.

    Handles:
    - Telegram API communication
    - Message conversion (Telegram <-> TournamentBot)
    - Photo download
    - Inline keyboards
    """

    def __init__(self, bot, telegram_token: str):
        """
        Initialize Telegram adapter.

        Args:
            bot: TournamentBot instance
            telegram_token: Telegram bot token
        """
        self.bot = bot
        self.telegram_token = telegram_token
        self.api_base = f"https://api.telegram.org/bot{telegram_token}"
        self.last_update_id = 0
        # Simple in-memory state to support "upload an image to communicate".
        # This avoids interfering with the default photo->OCR flow.
        self._awaiting_image_upload: Set[int] = set()  # chat_id
        self._last_upload_by_chat: Dict[int, UploadedImage] = {}
        self._uploads_dir = Path("data/telegram_uploads")
        self._uploads_dir.mkdir(parents=True, exist_ok=True)
        self._assistant_mode_by_chat: Dict[int, bool] = {}
        self._assistant_domain_mode_by_chat: Dict[int, str] = {}
        self._assistant_pending_domain_switch_by_chat: Dict[int, Dict[str, str]] = {}
        self._assistant_conv_by_chat: Dict[Tuple[int, str], str] = {}
        self._assistant_pending_run_by_chat: Dict[Tuple[int, str], str] = {}
        self._assistant_pending_direct_action_by_chat: Dict[Tuple[int, str], Dict[str, Any]] = {}
        self._assistant_pending_ticket_by_chat: Dict[Tuple[int, str], Dict[str, Any]] = {}
        self._assistant_last_export_by_chat: Dict[Tuple[int, str], Dict[str, str]] = {}
        self._assistant_quality_mode_by_chat: Dict[int, str] = {}
        self._nuevo_gasto_by_chat: Dict[int, Dict[str, Any]] = {}
        self._access_control = TelegramAccessControl()
        self._media_rate_window_sec = self._env_int("TELEGRAM_MEDIA_RATE_WINDOW_SEC", 60, min_value=5)
        self._media_rate_limit = self._env_int("TELEGRAM_MEDIA_RATE_LIMIT", 8, min_value=1)
        self._max_image_bytes = self._env_int("TELEGRAM_MAX_IMAGE_BYTES", 12 * 1024 * 1024, min_value=1024)
        self._max_document_bytes = self._env_int("TELEGRAM_MAX_DOCUMENT_BYTES", 20 * 1024 * 1024, min_value=1024)
        self._max_audio_bytes = self._env_int("TELEGRAM_MAX_AUDIO_BYTES", 15 * 1024 * 1024, min_value=1024)
        self._max_image_pixels = self._env_int("TELEGRAM_MAX_IMAGE_PIXELS", 25_000_000, min_value=10_000)
        self._upload_retention_hours = self._env_int("TELEGRAM_UPLOAD_RETENTION_HOURS", 72, min_value=1)
        self._upload_max_files_per_chat = self._env_int("TELEGRAM_UPLOAD_MAX_FILES_PER_CHAT", 20, min_value=1)
        self._upload_max_bytes_per_chat = self._env_int(
            "TELEGRAM_UPLOAD_MAX_BYTES_PER_CHAT",
            50 * 1024 * 1024,
            min_value=1024,
        )
        self._authz_cache_ttl_sec = self._env_int("TELEGRAM_AUTHZ_CACHE_TTL_SEC", 300, min_value=5)
        self._media_events: Dict[Tuple[int, int], Deque[float]] = {}
        self._authorized_empleado_cache: Dict[int, Tuple[float, Optional[Any]]] = {}
        self._authz_engine = None
        self._authz_session_maker: Optional[async_sessionmaker[AsyncSession]] = None
        self._gastos_reject_pending: Dict[int, str] = {}
        self._gastos_document_runtime = TelegramDocumentRuntime(self)
        self._bot_commands_installed = False
        Image.MAX_IMAGE_PIXELS = self._max_image_pixels

        logger.info(f"📱 Telegram adapter initialized for {bot.tournament_id}")
        logger.info(f"🔐 Telegram access mode: {self._access_control.describe()}")
        logger.info(
            "🛡️ Telegram media guardrails: rate=%s/%ss image<=%sB doc<=%sB audio<=%sB pixels<=%s",
            self._media_rate_limit,
            self._media_rate_window_sec,
            self._max_image_bytes,
            self._max_document_bytes,
            self._max_audio_bytes,
            self._max_image_pixels,
        )
        logger.info(
            "🗄️ Telegram upload retention: %sh max_files/chat=%s max_bytes/chat=%s",
            self._upload_retention_hours,
            self._upload_max_files_per_chat,
            self._upload_max_bytes_per_chat,
        )

    async def _deny_message_access(self, chat_id: int) -> None:
        await self.send_message(
            chat_id,
            "🔒 *Acceso restringido*\n\nEste bot solo acepta operadores autorizados.",
        )

    async def _deny_callback_access(self, callback_id: str) -> None:
        await self.answer_callback_query(callback_id, "🔒 No autorizado")

    def _normalize_async_db_url(self, db_url: str) -> str:
        if db_url.startswith("postgresql://") and "+asyncpg" not in db_url:
            return db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return db_url

    def _resolve_auth_session_maker(self):
        if self._authz_session_maker is not None:
            return self._authz_session_maker

        auth_db_url = (
            os.getenv("TELEGRAM_AUTH_DATABASE_URL")
            or os.getenv("EXPENSES_DATABASE_URL")
            or os.getenv("DATABASE_URL")
            or os.getenv("POSTGRESQL_URL")
            or ""
        ).strip()
        if auth_db_url:
            self._authz_engine = create_async_engine(
                self._normalize_async_db_url(auth_db_url),
                pool_size=2,
                max_overflow=4,
                pool_pre_ping=True,
            )
            self._authz_session_maker = async_sessionmaker(
                self._authz_engine,
                class_=AsyncSession,
                expire_on_commit=False,
            )
            logger.info("🔐 Telegram DB auth using administrative database URL")
            return self._authz_session_maker

        session_maker = getattr(self.bot, "db_session", None)
        if session_maker:
            logger.warning("Telegram DB auth falling back to tournament database session")
        return session_maker

    async def _get_authorized_empleado(self, user_id: Optional[int]):
        if user_id is None:
            return None

        cache_key = int(user_id)
        now = monotonic()
        cached = self._authorized_empleado_cache.get(cache_key)
        if cached and cached[0] > now:
            return cached[1]

        session_maker = self._resolve_auth_session_maker()
        if not session_maker:
            logger.warning("Telegram DB auth requested but db_session is not configured")
            self._authorized_empleado_cache[cache_key] = (now + self._authz_cache_ttl_sec, None)
            return None

        class EmpleadoProxy:
            def __init__(
                self,
                *,
                emp_id,
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
            ) -> None:
                self.id = emp_id
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
                self.permissions = set()

        async with session_maker() as session:
            result = await session.execute(
                text(
                    """
                    SELECT id, nombre, correo, rol, activo, telefono, telegram_user_id,
                           departamento, proyecto_predeterminado, centro_costo_predeterminado,
                           creado_en, actualizado_en
                    FROM empleados
                    WHERE telegram_user_id = :telegram_user_id
                      AND activo = TRUE
                    """
                ),
                {"telegram_user_id": cache_key},
            )
            row = result.fetchone()
            empleado = None
            if row:
                empleado = EmpleadoProxy(
                    emp_id=row[0],
                    nombre=row[1],
                    correo=row[2],
                    rol=row[3],
                    activo=row[4],
                    telefono=row[5],
                    telegram_user_id=row[6],
                    departamento=row[7],
                    proyecto_predeterminado=row[8],
                    centro_costo_predeterminado=row[9],
                    creado_en=row[10],
                    actualizado_en=row[11],
                )
        self._authorized_empleado_cache[cache_key] = (now + self._authz_cache_ttl_sec, empleado)
        return empleado

    async def _is_actor_allowed(self, actor) -> bool:
        if self._access_control.is_allowed(actor):
            return True
        if not self._access_control.requires_db_lookup():
            return False
        return await self._get_authorized_empleado(actor.user_id) is not None

    def _env_int(self, key: str, default: int, *, min_value: int = 1) -> int:
        raw = (os.getenv(key) or "").strip()
        if not raw:
            return default
        try:
            value = int(raw)
        except ValueError:
            logger.warning("Invalid integer for %s=%r; using default %s", key, raw, default)
            return default
        return max(min_value, value)

    def _consume_media_rate_limit(self, *, chat_id: int, user_id: int) -> Optional[str]:
        now = monotonic()
        key = (int(chat_id), int(user_id))
        bucket = self._media_events.get(key)
        if bucket is None:
            bucket = deque()
            self._media_events[key] = bucket
        cutoff = now - float(self._media_rate_window_sec)
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= self._media_rate_limit:
            return (
                "⏳ Estás enviando demasiados archivos seguidos.\n"
                f"Espera unos segundos y vuelve a intentar. Límite actual: "
                f"{self._media_rate_limit} archivos por {self._media_rate_window_sec}s."
            )
        bucket.append(now)
        return None

    def _validate_declared_size(self, *, label: str, declared_size: Optional[int], max_bytes: int) -> Optional[str]:
        if declared_size and int(declared_size) > int(max_bytes):
            return (
                f"⚠️ {label} demasiado grande.\n"
                f"Máximo permitido: {max_bytes // (1024 * 1024)} MB."
            )
        return None

    def _validate_image_payload(self, file_bytes: bytes) -> Optional[str]:
        try:
            with Image.open(io.BytesIO(file_bytes)) as img:
                width, height = img.size
        except Image.DecompressionBombError:
            return "⚠️ Imagen rechazada por tamaño inseguro. Usa una foto más pequeña."
        except (UnidentifiedImageError, OSError):
            return "⚠️ No pude leer la imagen. Reenvíala en JPG o PNG."

        pixels = int(width) * int(height)
        if pixels > self._max_image_pixels:
            return (
                "⚠️ Imagen demasiado grande para procesarla.\n"
                f"Máximo permitido: {self._max_image_pixels:,} pixeles."
            )
        return None

    def _assistant_openai_key(self) -> Optional[str]:
        return (
            getattr(getattr(self.bot, "operations", None), "openai_key", None)
            or os.getenv("OPENAI_API_KEY")
            or None
        )

    def _assistant_anthropic_key(self) -> Optional[str]:
        return (
            getattr(getattr(self.bot, "operations", None), "anthropic_key", None)
            or os.getenv("ANTHROPIC_API_KEY")
            or None
        )

    def _assistant_prefers_anthropic(self) -> bool:
        pref = (os.getenv("TELEGRAM_ASSISTANT_PROVIDER", "anthropic_first") or "").strip().lower()
        return pref in {"anthropic", "anthropic_first", "claude", "claude_first"}

    def _assistant_default_mode(self) -> str:
        raw = (os.getenv("TELEGRAM_ASSISTANT_MODE", os.getenv("ASSISTANT_MODE_DEFAULT", "ahorro")) or "").strip().lower()
        if raw in {"ahorro", "balanceado", "calidad"}:
            return raw
        return "ahorro"

    def _assistant_default_domain(self) -> str:
        raw = (os.getenv("TELEGRAM_ASSISTANT_DATA_MODE", "empresa") or "").strip().lower()
        if raw in {"empresa", "finanzas", "operaciones"}:
            return raw
        return "empresa"

    def _assistant_domain(self, chat_id: int) -> str:
        current = (self._assistant_domain_mode_by_chat.get(chat_id) or self._assistant_default_domain()).strip().lower()
        if current in {"empresa", "finanzas", "operaciones"}:
            return current
        return "empresa"

    def _assistant_context_key(self, chat_id: int) -> Tuple[int, str]:
        return (int(chat_id), self._assistant_domain(chat_id))

    def _assistant_tournament_key(self, chat_id: int) -> Optional[str]:
        domain = self._assistant_domain(chat_id)
        if domain == "finanzas":
            return None
        return "copa_telmex"

    def _assistant_title(self, chat_id: int) -> str:
        return f"Telegram chat {chat_id} [{self._assistant_domain(chat_id)}]"

    def _assistant_domain_instruction(self, chat_id: int) -> str:
        domain = self._assistant_domain(chat_id)
        if domain == "finanzas":
            return (
                "Modo activo del chat: FINANZAS. "
                "Prioriza exclusivamente datos y tools de gastos, contabilidad, proveedores, presupuestos y CFDI. "
                "Si la solicitud es operativa de torneos, pide al usuario cambiar a /modo operaciones o /modo empresa."
            )
        if domain == "operaciones":
            return (
                "Modo activo del chat: OPERACIONES. "
                "Prioriza exclusivamente datos y tools de torneos, equipos, jugadores, registros, OCR y calendarios. "
                "Si la solicitud es financiera/contable, pide al usuario cambiar a /modo finanzas o /modo empresa."
            )
        return (
            "Modo activo del chat: EMPRESA. "
            "Puedes resolver tanto finanzas como operaciones; elige el dominio correcto segun la solicitud."
        )

    def _assistant_apply_domain_context(self, *, chat_id: int, text: str) -> str:
        body = (text or "").strip()
        instruction = self._assistant_domain_instruction(chat_id)
        if not body:
            return instruction
        return f"{instruction}\n\nSolicitud del usuario:\n{body}"

    def _assistant_domain_label(self, chat_id: int) -> str:
        return self._assistant_domain(chat_id).upper()

    def _assistant_describe_context(self, chat_id: int) -> str:
        return (
            f"assistant_mode={'on' if self._assistant_mode_by_chat.get(chat_id, False) else 'off'}\n"
            f"llm_mode={self._assistant_mode(chat_id)}\n"
            f"data_mode={self._assistant_domain(chat_id)}\n"
            f"tournament_key={self._assistant_tournament_key(chat_id) or 'none'}"
        )

    def _assistant_detect_domain_intent(self, text: str) -> Optional[str]:
        normalized = (text or "").strip().lower()
        if not normalized:
            return None

        finance_signals = [
            "gasto",
            "gastos",
            "hospedaje",
            "hotel",
            "factura",
            "cfdi",
            "tocino",
            "proveedor",
            "proveedores",
            "pago",
            "pagos",
            "contabilidad",
            "cuenta contable",
            "viatico",
            "viáticos",
            "presupuesto",
            "amex",
            "reembolso",
            "solicitud de pago",
        ]
        operations_signals = [
            "jugador",
            "jugadores",
            "equipo",
            "equipos",
            "torneo",
            "torneos",
            "categoria",
            "categoría",
            "rama",
            "municipio",
            "estado",
            "calendario",
            "partido",
            "partidos",
            "inscrito",
            "inscritos",
            "inscripcion",
            "inscripción",
            "registro",
            "registrar equipo",
            "dar de alta",
            "cedula",
            "cédula",
        ]

        finance_hits = sum(1 for token in finance_signals if token in normalized)
        operations_hits = sum(1 for token in operations_signals if token in normalized)

        if finance_hits == 0 and operations_hits == 0:
            return None
        if finance_hits > operations_hits:
            return "finanzas"
        if operations_hits > finance_hits:
            return "operaciones"
        return None

    def _assistant_pending_switch_prompt(self, *, chat_id: int, target: str) -> str:
        current = self._assistant_domain_label(chat_id)
        target_label = target.upper()
        return (
            f"Estas trabajando en modo *{current}*, pero parece que tu solicitud es de *{target_label}*.\n\n"
            f"¿Quieres cambiar de modalidad a *{target_label}*?\n"
            "Responde `si` para cambiar y reintentar automáticamente, o `no` para seguir en el modo actual.\n"
            f"También puedes usar `/modo {target}`."
        )

    async def _assistant_handle_pending_domain_switch(
        self,
        *,
        chat_id: int,
        user_id: int,
        text: str,
    ) -> Optional[str]:
        pending = self._assistant_pending_domain_switch_by_chat.get(chat_id)
        if not pending:
            return None

        normalized = (text or "").strip().lower()
        affirmative = {"si", "sí", "ok", "cambiar", "cambia", "yes"}
        negative = {"no", "seguir", "continuar", "quedate", "quédate"}

        if normalized in affirmative:
            target = str(pending.get("target") or "empresa").strip().lower()
            original_text = str(pending.get("original_text") or "").strip()
            self._assistant_pending_domain_switch_by_chat.pop(chat_id, None)
            switch_msg = self._set_assistant_domain(chat_id, target)
            empleado = await self._assistant_get_empleado(user_id)
            if not empleado:
                return (
                    f"{switch_msg}\n\n"
                    "⚠️ Tu Telegram no esta vinculado a un usuario interno activo."
                )
            retried = await self._assistant_send_text(
                chat_id=chat_id,
                empleado=empleado,
                text=original_text,
            )
            return f"{switch_msg}\n\n{retried}"

        if normalized in negative:
            target = str(pending.get("target") or "").strip().lower()
            self._assistant_pending_domain_switch_by_chat.pop(chat_id, None)
            return (
                f"Entendido. Sigo en modo *{self._assistant_domain_label(chat_id)}*.\n"
                f"Si quieres cambiar después, usa `/modo {target or 'empresa'}`."
            )

        return None

    def _assistant_mode(self, chat_id: int) -> str:
        current = (self._assistant_quality_mode_by_chat.get(chat_id) or self._assistant_default_mode()).strip().lower()
        if current in {"ahorro", "balanceado", "calidad"}:
            return current
        return "ahorro"

    def _assistant_has_tool_activity(self, tool_trace: Any) -> bool:
        if not isinstance(tool_trace, list):
            return False
        return any(isinstance(item, dict) and "tool" in item for item in tool_trace)

    def _assistant_response_looks_deferred(self, text: str) -> bool:
        normalized = (text or "").strip().lower()
        if not normalized:
            return False
        markers = (
            "sam.chat",
            "panel web",
            "desde el panel",
            "puedes registrar",
            "puedes consultarlo",
            "puedo orientarte",
            "te puedo ayudar a hacerlo",
            "ingresa a",
        )
        return any(marker in normalized for marker in markers)

    def _assistant_direct_pending_summary(self, payload: Dict[str, Any]) -> str:
        action = str(payload.get("action") or "").strip().lower()
        args = payload.get("args") or {}
        if action == "finance_expense_create":
            return (
                "Voy a registrar este gasto:\n"
                f"• Proyecto: {args.get('proyecto')}\n"
                f"• Concepto: {args.get('concepto')}\n"
                f"• Monto: ${float(args.get('gasto_cantidad') or 0):,.2f} MXN\n"
                f"• Fecha: {args.get('fecha')}\n\n"
                "Responde /ok para guardarlo o /cancel para abortar."
            )
        return "Hay una acción pendiente. Responde /ok para continuar o /cancel para abortar."

    def _assistant_extract_amount(self, text: str) -> Optional[float]:
        match = re.search(r"gasto\s+de\s+\$?\s*([0-9]+(?:[.,][0-9]{1,2})?)", text, flags=re.IGNORECASE)
        if not match:
            match = re.search(r"\$?\s*([0-9]+(?:[.,][0-9]{1,2})?)\s*(?:pesos|mxn)", text, flags=re.IGNORECASE)
        if not match:
            return None
        raw = match.group(1).replace(",", ".")
        try:
            amount = round(float(raw), 2)
        except ValueError:
            return None
        return amount if amount > 0 else None

    def _assistant_extract_concept(self, text: str) -> Optional[str]:
        quoted = re.search(r"[\"“”']([^\"“”']{3,120})[\"“”']", text)
        if quoted:
            return quoted.group(1).strip()
        attributed = re.search(
            r"(?:atribuido a|concepto(?: es)?|para concepto)\s+([a-z0-9áéíóúñ _\\-/]{3,120})",
            text,
            flags=re.IGNORECASE,
        )
        if attributed:
            return attributed.group(1).strip(" .,:;")
        return None

    def _assistant_extract_project(self, text: str) -> Optional[str]:
        patterns = (
            r"proyecto\s+[\"“”']?([^\"“”'\n]{2,120})",
            r"para el proyecto\s+[\"“”']?([^\"“”'\n]{2,120})",
            r"cargo al proyecto\s+[\"“”']?([^\"“”'\n]{2,120})",
        )
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                return match.group(1).strip(" .,:;")
        return None

    def _assistant_extract_date_text(self, text: str) -> Optional[str]:
        iso_match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", text)
        if iso_match:
            return iso_match.group(1)
        slash_match = re.search(r"\b(\d{2}/\d{2}/\d{4})\b", text)
        if slash_match:
            return slash_match.group(1)
        dash_match = re.search(r"\b(\d{2}-\d{2}-\d{4})\b", text)
        if dash_match:
            day, month, year = dash_match.group(1).split("-")
            return f"{year}-{month}-{day}"

        months = {
            "enero": 1,
            "febrero": 2,
            "marzo": 3,
            "abril": 4,
            "mayo": 5,
            "junio": 6,
            "julio": 7,
            "agosto": 8,
            "septiembre": 9,
            "setiembre": 9,
            "octubre": 10,
            "noviembre": 11,
            "diciembre": 12,
        }
        lower = text.lower()
        for month_name, month_number in months.items():
            m1 = re.search(rf"\b(\d{{1,2}})\s+de\s+{month_name}\s+(?:de|del)?\s*(\d{{4}})\b", lower)
            if m1:
                year = int(m1.group(2))
                day = int(m1.group(1))
                return date(year, month_number, day).isoformat()
            m2 = re.search(rf"\b{month_name}\s+(\d{{1,2}})\s+(?:de|del)?\s*(\d{{4}})\b", lower)
            if m2:
                year = int(m2.group(2))
                day = int(m2.group(1))
                return date(year, month_number, day).isoformat()
        return None

    def _assistant_yes_no(self, text: str) -> Optional[bool]:
        normalized = (text or "").strip().lower()
        if normalized in {"si", "sí", "s", "yes", "y", "ok", "claro", "afirmativo"}:
            return True
        if normalized in {"no", "n", "nope", "negativo"}:
            return False
        return None

    async def _assistant_list_open_expense_accounts(self, empleado: Any) -> List[Dict[str, Any]]:
        from sqlalchemy import select
        from devnous.gastos.models import CuentaDeGastos

        session_maker = self._assistant_session_maker()
        async with session_maker() as session:
            rows = (
                await session.execute(
                    select(CuentaDeGastos)
                    .where(
                        CuentaDeGastos.empleado_id == empleado.id,
                        CuentaDeGastos.estado == "abierta",
                    )
                    .order_by(CuentaDeGastos.created_at.desc())
                    .limit(10)
                )
            ).scalars().all()
        return [
            {
                "id": str(row.id),
                "referencia_base": str(row.referencia_base or ""),
                "nombre": str(getattr(row, "nombre", "") or ""),
            }
            for row in rows
        ]

    async def _assistant_start_ticket_flow(
        self,
        *,
        chat_id: int,
        empleado: Any,
        file_bytes: bytes,
        filename: str,
        content_type: str,
        caption: Optional[str] = None,
    ) -> str:
        context_key = self._assistant_context_key(chat_id)
        cuentas = await self._assistant_list_open_expense_accounts(empleado)
        concept_hint = self._assistant_extract_concept(caption or "")
        amount_hint = self._assistant_extract_amount(caption or "")
        date_hint = self._assistant_extract_date_text(caption or "")
        self._assistant_pending_ticket_by_chat[context_key] = {
            "stage": "project",
            "file_b64": base64.b64encode(file_bytes).decode("ascii"),
            "filename": filename,
            "content_type": content_type,
            "caption": caption or "",
            "project": None,
            "cuenta_gastos_id": None,
            "cuenta_gastos_ref": None,
            "concepto": concept_hint,
            "gasto_cantidad": amount_hint,
            "fecha": date_hint,
            "request_cfdi_now": None,
            "cuentas": cuentas,
        }

        hints: List[str] = ["🧾 Recibí el ticket y voy a registrarlo en tus gastos."]
        if concept_hint or amount_hint or date_hint:
            hints.append("")
            hints.append("Pistas detectadas del ticket/caption:")
            if concept_hint:
                hints.append(f"• Concepto sugerido: {concept_hint}")
            if amount_hint is not None:
                hints.append(f"• Monto sugerido: ${amount_hint:,.2f} MXN")
            if date_hint:
                hints.append(f"• Fecha sugerida: {date_hint}")
        hints.append("")
        default_project = str(getattr(empleado, "proyecto_predeterminado", "") or "").strip()
        if default_project:
            hints.append(
                f"1. ¿Qué proyecto uso?\n"
                f"Responde con el nombre del proyecto, o escribe `default` para usar *{default_project}*."
            )
        else:
            hints.append("1. ¿Qué proyecto uso?")
        return "\n".join(hints)

    async def _assistant_handle_pending_ticket(
        self,
        *,
        chat_id: int,
        user_id: int,
        text: str,
    ) -> Optional[str]:
        from devnous.gastos.services.expense_service import create_expense_from_data, trigger_cfdi_generation

        context_key = self._assistant_context_key(chat_id)
        draft = self._assistant_pending_ticket_by_chat.get(context_key)
        if not draft:
            return None

        empleado = await self._assistant_get_empleado(user_id)
        if not empleado:
            self._assistant_pending_ticket_by_chat.pop(context_key, None)
            return "⚠️ Tu Telegram no está vinculado a un usuario interno activo."

        reply = (text or "").strip()
        if not reply:
            return "Necesito una respuesta para continuar con el ticket."

        stage = str(draft.get("stage") or "project")
        if stage == "project":
            default_project = str(getattr(empleado, "proyecto_predeterminado", "") or "").strip()
            project = default_project if reply.lower() == "default" and default_project else reply
            project = project.strip()
            if not project:
                return "Indícame el proyecto. Si quieres usar tu proyecto predeterminado, responde `default`."
            draft["project"] = project
            draft["stage"] = "cuenta"
            cuentas = draft.get("cuentas") or []
            if cuentas:
                lines = [
                    f"✅ Proyecto: *{project}*",
                    "",
                    "2. ¿A qué cuenta de gastos lo cargo?",
                    "Responde con el número de la opción o escribe `ninguna`.",
                ]
                for idx, cuenta in enumerate(cuentas, start=1):
                    label = str(cuenta.get("nombre") or cuenta.get("referencia_base") or f"Cuenta {idx}")
                    ref = str(cuenta.get("referencia_base") or "")
                    lines.append(f"{idx}. {label}{f' ({ref})' if ref else ''}")
                return "\n".join(lines)
            draft["stage"] = "concepto"
            return f"✅ Proyecto: *{project}*\n\n2. No encontré cuentas de gastos abiertas.\nContinuamos sin asignarla.\n\n3. ¿Cuál es el concepto? Ejemplo: Gasolina."

        if stage == "cuenta":
            cuentas = draft.get("cuentas") or []
            lowered = reply.lower()
            if lowered in {"ninguna", "ninguno", "sin cuenta", "omitir", "skip"}:
                draft["cuenta_gastos_id"] = None
                draft["cuenta_gastos_ref"] = None
            else:
                selected = None
                if reply.isdigit():
                    idx = int(reply) - 1
                    if 0 <= idx < len(cuentas):
                        selected = cuentas[idx]
                if not selected:
                    for cuenta in cuentas:
                        haystack = " ".join(
                            [
                                str(cuenta.get("referencia_base") or ""),
                                str(cuenta.get("nombre") or ""),
                            ]
                        ).lower()
                        if lowered in haystack:
                            selected = cuenta
                            break
                if not selected:
                    return "No reconocí esa cuenta. Responde con el número de la opción o escribe `ninguna`."
                draft["cuenta_gastos_id"] = str(selected.get("id") or "")
                draft["cuenta_gastos_ref"] = str(selected.get("referencia_base") or "") or None
            draft["stage"] = "concepto"
            concept_hint = str(draft.get("concepto") or "").strip()
            if concept_hint:
                return (
                    "✅ Cuenta de gastos asignada.\n\n"
                    f"3. Detecté como concepto sugerido: *{concept_hint}*.\n"
                    "Si te sirve, responde `default`. Si no, escribe el concepto correcto."
                )
            return "✅ Cuenta de gastos registrada.\n\n3. ¿Cuál es el concepto? Ejemplo: Gasolina."

        if stage == "concepto":
            concept_hint = str(draft.get("concepto") or "").strip()
            concepto = concept_hint if reply.lower() == "default" and concept_hint else reply
            concepto = concepto.strip()
            if not concepto:
                return "Indícame el concepto del gasto. Ejemplo: Gasolina."
            draft["concepto"] = concepto
            draft["stage"] = "amount"
            amount_hint = draft.get("gasto_cantidad")
            if amount_hint is not None:
                return (
                    f"✅ Concepto: *{concepto}*\n\n"
                    f"4. Detecté monto sugerido: *${float(amount_hint):,.2f} MXN*.\n"
                    "Responde `default` para usarlo o escribe el monto correcto."
                )
            return f"✅ Concepto: *{concepto}*\n\n4. ¿Cuál es el monto en MXN?"

        if stage == "amount":
            amount = draft.get("gasto_cantidad") if reply.lower() == "default" else self._assistant_extract_amount(reply)
            if amount is None:
                return "No pude leer el monto. Escríbelo así: `77.77 pesos`."
            draft["gasto_cantidad"] = amount
            draft["stage"] = "date"
            date_hint = str(draft.get("fecha") or "").strip()
            if date_hint:
                return (
                    f"✅ Monto: *${float(amount):,.2f} MXN*\n\n"
                    f"5. Detecté fecha sugerida: *{date_hint}*.\n"
                    "Responde `default` para usarla o escribe la fecha correcta."
                )
            return f"✅ Monto: *${float(amount):,.2f} MXN*\n\n5. ¿Qué fecha uso? Ejemplo: `2026-03-22`."

        if stage == "date":
            fecha = str(draft.get("fecha") or "") if reply.lower() == "default" else (self._assistant_extract_date_text(reply) or "")
            if not fecha:
                return "No pude leer la fecha. Usa `YYYY-MM-DD`, `DD/MM/YYYY` o algo como `22 de marzo de 2026`."
            draft["fecha"] = fecha
            draft["stage"] = "cfdi_now"
            return (
                f"✅ Fecha: *{fecha}*\n\n"
                "6. ¿Quieres enviarlo a Tocino para solicitar CFDI ahora?\n"
                "Responde `si` o `no`."
            )

        if stage == "cfdi_now":
            wants_cfdi = self._assistant_yes_no(reply)
            if wants_cfdi is None:
                return "Respóndeme `si` o `no` para saber si lo mando a Tocino ahora."
            draft["request_cfdi_now"] = wants_cfdi

            session_maker = self._assistant_session_maker()
            async with session_maker() as session:
                expense = await create_expense_from_data(
                    session=session,
                    empleado_id=empleado.id,
                    nombre_enviador=empleado.nombre,
                    proyecto=str(draft.get("project") or ""),
                    concepto=str(draft.get("concepto") or ""),
                    gasto_cantidad=float(draft.get("gasto_cantidad") or 0),
                    fecha=datetime.combine(date.fromisoformat(str(draft.get("fecha"))), datetime.min.time()),
                    tipo_gasto="ticket",
                    departamento=str(getattr(empleado, "departamento", "") or "Operaciones"),
                    archivo_nombre=str(draft.get("filename") or "ticket.jpg"),
                    archivo_data=str(draft.get("file_b64") or ""),
                    cfdi_use="G03",
                    origen="telegram_bot",
                    skip_initial_tocino=not wants_cfdi,
                )

                cuenta_gastos_id = str(draft.get("cuenta_gastos_id") or "").strip()
                if cuenta_gastos_id:
                    try:
                        from uuid import UUID
                        from sqlalchemy import select
                        from devnous.gastos.models import CuentaDeGastos, Documento

                        cg_uuid = UUID(cuenta_gastos_id)
                        cg_result = await session.execute(
                            select(CuentaDeGastos).where(
                                CuentaDeGastos.id == cg_uuid,
                                CuentaDeGastos.empleado_id == empleado.id,
                                CuentaDeGastos.estado == "abierta",
                            )
                        )
                        cuenta = cg_result.scalar_one_or_none()
                        if cuenta:
                            expense.cuenta_gastos_id = cuenta.id
                            expense.referencia_base = cuenta.referencia_base
                            informe_res = await session.execute(
                                select(Documento).where(
                                    Documento.cuenta_gastos_id == cuenta.id,
                                    Documento.tipo == "INFORME",
                                )
                            )
                            informe_doc = informe_res.scalar_one_or_none()
                            if informe_doc:
                                expense.informe_documento_id = informe_doc.id
                    except Exception:
                        pass

                nova_request_id = None
                if wants_cfdi:
                    nova_request_id = await trigger_cfdi_generation(
                        session=session,
                        expense=expense,
                        cfdi_use="G03",
                    )

                await session.commit()
                await session.refresh(expense)

            self._assistant_pending_ticket_by_chat.pop(context_key, None)
            lines = [
                "✅ Ticket registrado como gasto.",
                f"• Proyecto: {draft.get('project')}",
                f"• Concepto: {draft.get('concepto')}",
                f"• Monto: ${float(draft.get('gasto_cantidad') or 0):,.2f} MXN",
                f"• Fecha: {draft.get('fecha')}",
                f"• Referencia: {expense.numero_referencia}",
                f"• Expense ID: {expense.id}",
            ]
            if draft.get("cuenta_gastos_ref"):
                lines.append(f"• Cuenta de gastos: {draft.get('cuenta_gastos_ref')}")
            if wants_cfdi:
                if nova_request_id:
                    lines.append(f"• Tocino: enviado (`{nova_request_id}`)")
                    lines.append("Te avisaré cuando llegue el CFDI y quedará vinculado al mismo gasto.")
                else:
                    lines.append("• Tocino: no se pudo enviar ahora; el gasto quedó guardado y puedes pedir la factura después.")
            else:
                lines.append("• Factura: pendiente, no se envió a Tocino todavía.")
                lines.append("Si luego quieres solicitarla, pídeselo al bot indicando la referencia o el expense_id.")
            return "\n".join(lines)

        return "No reconocí el estado del ticket. Usa /cancel y vuelve a intentarlo."

    def _nuevo_gasto_cancel(self, chat_id: int) -> None:
        self._nuevo_gasto_by_chat.pop(int(chat_id), None)

    def _nuevo_gasto__format_numbered(self, items: List[str]) -> str:
        lines: List[str] = []
        for i, label in enumerate(items, start=1):
            lines.append(f"{i}. {label}")
        return "\n".join(lines)

    async def _nuevo_gasto_start(self, chat_id: int, user_id: int) -> str:
        from devnous.gastos.models import Tournament

        empleado = await self._assistant_get_empleado(user_id)
        if not empleado:
            self._nuevo_gasto_cancel(chat_id)
            return (
                "⚠️ Tu Telegram no está vinculado a un usuario interno activo.\n"
                "Pide a un admin configurar tu `telegram_user_id`."
            )

        cuentas = await self._assistant_list_open_expense_accounts(empleado)

        session_maker = self._assistant_session_maker()
        async with session_maker() as session:
            rows = (
                await session.execute(
                    select(Tournament)
                    .where(Tournament.active == True)
                    .order_by(Tournament.display_order.asc(), Tournament.name.asc())
                    .limit(30)
                )
            ).scalars().all()

        tournaments: List[Dict[str, Any]] = []
        for row in rows:
            tournaments.append(
                {
                    "id": str(row.id),
                    "name": str(getattr(row, "name", "") or getattr(row, "nombre", "") or "").strip() or "Sin nombre",
                    "etapas": get_tournament_etapas(row),
                }
            )

        self._nuevo_gasto_by_chat[int(chat_id)] = {
            "stage": "cuenta",
            "empleado_id": str(empleado.id),
            "empleado_nombre": str(getattr(empleado, "nombre", "") or ""),
            "departamento": str(getattr(empleado, "departamento", "") or "Operaciones"),
            "cuentas": cuentas,
            "tournaments": tournaments,
            "cuenta_gastos_id": None,
            "cuenta_gastos_ref": None,
            "tipo_gasto": None,  # "ticket" | "manual"
            "generar_cfdi_tocino": None,  # bool
            "tournament_id": None,
            "proyecto": None,
            "fase_torneo": None,
            "concepto": None,
            "gasto_cantidad": None,
            "fecha": None,  # ISO string
            "metodo_pago": None,
            "iva": None,  # float | None
            "file_b64": None,
            "filename": None,
            "content_type": None,
        }

        lines: List[str] = [
            "💰 *Nuevo gasto*",
            "",
            "Responde con el *número* de la opción (o escribe texto cuando se pida).",
            "En cualquier momento: `/restart` para reiniciar o `/cancel` para cancelar este flujo.",
            "",
            "1. ¿A qué *cuenta de gastos* lo cargo?",
        ]
        if cuentas:
            labels = []
            for c in cuentas:
                ref = str(c.get("referencia_base") or "").strip()
                name = str(c.get("nombre") or "").strip()
                tail = " — ".join([x for x in (ref, name) if x])
                labels.append(tail or str(c.get("id") or ""))
            lines.append(self._nuevo_gasto__format_numbered(labels))
            lines.append(f"{len(cuentas) + 1}. Ninguna (no asignar)")
        else:
            lines.append("No tienes cuentas de gastos *abiertas*.")
            lines.append("1. Ninguna (no asignar)")
        return "\n".join(lines).strip()

    async def _nuevo_gasto_handle_reply(self, chat_id: int, user_id: int, text: str) -> Optional[str]:
        from devnous.gastos.services.expense_service import create_expense_from_data

        draft = self._nuevo_gasto_by_chat.get(int(chat_id))
        if not draft:
            return None

        empleado = await self._assistant_get_empleado(user_id)
        if not empleado:
            self._nuevo_gasto_cancel(chat_id)
            return "⚠️ Tu Telegram no está vinculado a un usuario interno activo."

        reply = (text or "").strip()
        if not reply:
            return "Necesito una respuesta para continuar."

        stage = str(draft.get("stage") or "cuenta")

        def _prompt_proyecto() -> str:
            tournaments: List[Dict[str, Any]] = draft.get("tournaments") or []
            if not tournaments:
                return "3. Escribe el *proyecto*."
            labels = [str(t.get("name") or "").strip() or "Sin nombre" for t in tournaments]
            return "\n".join(
                [
                    "3. ¿Qué *proyecto/torneo* uso?",
                    self._nuevo_gasto__format_numbered(labels),
                    "",
                    "Responde con el número, o escribe el nombre si no está en la lista.",
                ]
            ).strip()

        def _as_int(s: str) -> Optional[int]:
            try:
                return int((s or "").strip())
            except Exception:
                return None

        if stage == "cuenta":
            cuentas: List[Dict[str, Any]] = draft.get("cuentas") or []
            choice = _as_int(reply)
            if not choice:
                return "Responde con el número de la cuenta (ej. `1`)."
            if cuentas:
                if choice == len(cuentas) + 1:
                    draft["cuenta_gastos_id"] = None
                    draft["cuenta_gastos_ref"] = None
                elif 1 <= choice <= len(cuentas):
                    picked = cuentas[choice - 1]
                    draft["cuenta_gastos_id"] = str(picked.get("id") or "").strip() or None
                    draft["cuenta_gastos_ref"] = str(picked.get("referencia_base") or "").strip() or None
                else:
                    return "Opción inválida. Responde con un número de la lista."
            else:
                if choice != 1:
                    return "Opción inválida. Responde `1` para continuar sin cuenta."
                draft["cuenta_gastos_id"] = None
                draft["cuenta_gastos_ref"] = None

            draft["stage"] = "tipo"
            return (
                "2. ¿El gasto es con *ticket* (adjuntar comprobante)?\n"
                "1. Sí (tengo ticket)\n"
                "2. No (manual, sin ticket)"
            )

        if stage == "tipo":
            choice = _as_int(reply)
            if choice not in (1, 2):
                return "Responde `1` (ticket) o `2` (manual)."
            draft["tipo_gasto"] = "ticket" if choice == 1 else "manual"
            if draft["tipo_gasto"] == "ticket":
                draft["stage"] = "tocino"
                return (
                    "3. ¿Quieres enviar el ticket a *Tocino* para solicitar CFDI?\n"
                    "Responde `si` o `no`."
                )
            draft["generar_cfdi_tocino"] = False
            draft["stage"] = "proyecto"
            return _prompt_proyecto()

        if stage == "tocino":
            wants = self._assistant_yes_no(reply)
            if wants is None:
                return "Respóndeme `si` o `no` para saber si lo mando a Tocino."
            draft["generar_cfdi_tocino"] = bool(wants)
            draft["stage"] = "proyecto"
            return _prompt_proyecto()

        if stage == "proyecto":
            tournaments: List[Dict[str, Any]] = draft.get("tournaments") or []
            choice = _as_int(reply)
            picked_t = None
            if choice and 1 <= choice <= len(tournaments):
                picked_t = tournaments[choice - 1]
                draft["tournament_id"] = str(picked_t.get("id") or "").strip() or None
                draft["proyecto"] = str(picked_t.get("name") or "").strip() or None
            else:
                if choice and tournaments:
                    return "Opción inválida. Responde con un número de la lista, o escribe el nombre del proyecto."
                project = reply.strip()
                if not project:
                    return "Escríbeme el proyecto."
                draft["tournament_id"] = None
                draft["proyecto"] = project

            draft["stage"] = "fase"
            etapas = get_tournament_etapas(picked_t)
            draft["fase_options"] = etapas
            return "\n".join(
                [
                    f"✅ Proyecto: *{draft.get('proyecto')}*",
                    "",
                    "4. ¿Qué *fase* aplica?",
                    self._nuevo_gasto__format_numbered([str(x) for x in etapas]),
                ]
            ).strip()

        if stage == "fase":
            opciones = draft.get("fase_options") or get_tournament_etapas(None)
            choice = _as_int(reply)
            if not choice or not (1 <= choice <= len(opciones)):
                return "Responde con el número de la fase (ej. `1`)."
            draft["fase_torneo"] = str(opciones[choice - 1]).strip()
            draft["stage"] = "concepto"
            return (
                f"✅ Fase: *{draft.get('fase_torneo')}*\n\n"
                "5. Escribe el *concepto* (ej. `Gasolina`, `Hospedaje`, `Comidas`)."
            )

        if stage == "concepto":
            concepto = reply.strip()
            if len(concepto) < 2:
                return "El concepto es demasiado corto. Escríbelo de nuevo."
            draft["concepto"] = concepto
            draft["stage"] = "monto"
            return f"✅ Concepto: *{concepto}*\n\n6. ¿Cuál es el *monto*? Ejemplo: `777.77`."

        if stage == "monto":
            amount = self._assistant_extract_amount(reply)
            if amount is None:
                return "No pude leer el monto. Escríbelo así: `777.77`."
            draft["gasto_cantidad"] = float(amount)
            draft["stage"] = "fecha"
            return (
                f"✅ Monto: *${float(amount):,.2f} MXN*\n\n"
                "7. ¿Qué *fecha* uso? Ejemplo: `2026-03-22` o `22/03/2026`."
            )

        if stage == "fecha":
            fecha = self._assistant_extract_date_text(reply) or ""
            if not fecha:
                return "No pude leer la fecha. Usa `YYYY-MM-DD`, `DD/MM/YYYY` o `22 de marzo de 2026`."
            if "/" in fecha:
                try:
                    dd, mm, yyyy = fecha.split("/")
                    fecha = f"{yyyy}-{mm}-{dd}"
                except Exception:
                    pass
            draft["fecha"] = fecha
            draft["stage"] = "metodo"
            return (
                f"✅ Fecha: *{fecha}*\n\n"
                "8. ¿Método de pago?\n"
                "1. Efectivo\n"
                "2. Tarjeta\n"
                "3. Omitir"
            )

        if stage == "metodo":
            choice = _as_int(reply)
            if choice not in (1, 2, 3):
                return "Responde `1`, `2` o `3`."
            draft["metodo_pago"] = "Efectivo" if choice == 1 else ("Tarjeta" if choice == 2 else None)
            draft["stage"] = "iva"
            return (
                f"✅ Método de pago: *{draft.get('metodo_pago') or 'Omitido'}*\n\n"
                "9. ¿Incluye IVA?\n"
                "Responde `si` o `no`."
            )

        if stage == "iva":
            has_iva = self._assistant_yes_no(reply)
            if has_iva is None:
                return "Respóndeme `si` o `no` para el IVA."
            draft["iva"] = 0.0 if has_iva else None
            draft["stage"] = "confirm"
            lines = [
                "10. Confirma el gasto.",
                "",
                "*Resumen*",
                f"• Cuenta de gastos: {draft.get('cuenta_gastos_ref') or '—'}",
                f"• Tipo: {draft.get('tipo_gasto')}",
                f"• Tocino CFDI: {'Sí' if draft.get('generar_cfdi_tocino') else 'No'}",
                f"• Proyecto: {draft.get('proyecto')}",
                f"• Fase: {draft.get('fase_torneo')}",
                f"• Concepto: {draft.get('concepto')}",
                f"• Monto: ${float(draft.get('gasto_cantidad') or 0):,.2f} MXN",
                f"• Fecha: {draft.get('fecha')}",
                f"• Método: {draft.get('metodo_pago') or 'Omitido'}",
                f"• IVA: {'Sí' if draft.get('iva') is not None else 'No'}",
                "",
                "Responde `si` para guardar o `no` para cancelar.",
            ]
            return "\n".join(lines).strip()

        if stage == "confirm":
            yes = self._assistant_yes_no(reply)
            if yes is None:
                lines = [
                    "10. Confirma el gasto.",
                    "",
                    "*Resumen*",
                    f"• Cuenta de gastos: {draft.get('cuenta_gastos_ref') or '—'}",
                    f"• Tipo: {draft.get('tipo_gasto')}",
                    f"• Tocino CFDI: {'Sí' if draft.get('generar_cfdi_tocino') else 'No'}",
                    f"• Proyecto: {draft.get('proyecto')}",
                    f"• Fase: {draft.get('fase_torneo')}",
                    f"• Concepto: {draft.get('concepto')}",
                    f"• Monto: ${float(draft.get('gasto_cantidad') or 0):,.2f} MXN",
                    f"• Fecha: {draft.get('fecha')}",
                    f"• Método: {draft.get('metodo_pago') or 'Omitido'}",
                    f"• IVA: {'Sí' if draft.get('iva') is not None else 'No'}",
                    "",
                    "Responde `si` para guardar o `no` para cancelar.",
                ]
                return "\n".join(lines).strip()
            if not yes:
                self._nuevo_gasto_cancel(chat_id)
                return "Flujo cancelado. Usa /nuevo_gasto para iniciar de nuevo."

            if str(draft.get("tipo_gasto") or "") == "ticket":
                draft["stage"] = "photo"
                return (
                    "📷 Listo. Ahora envía la *foto del ticket* para adjuntarla y guardar el gasto.\n"
                    "Tip: si te equivocaste, usa `/restart`."
                )

            session_maker = self._assistant_session_maker()
            async with session_maker() as session:
                fecha_iso = str(draft.get("fecha") or "").strip()
                expense_date = datetime.combine(date.fromisoformat(fecha_iso), datetime.min.time())
                expense = await create_expense_from_data(
                    session=session,
                    empleado_id=empleado.id,
                    nombre_enviador=str(getattr(empleado, "nombre", "") or ""),
                    proyecto=str(draft.get("proyecto") or ""),
                    concepto=str(draft.get("concepto") or ""),
                    gasto_cantidad=float(draft.get("gasto_cantidad") or 0),
                    fecha=expense_date,
                    tipo_gasto="manual",
                    departamento=str(getattr(empleado, "departamento", "") or "Operaciones"),
                    fase_torneo=str(draft.get("fase_torneo") or ""),
                    metodo_pago=str(draft.get("metodo_pago") or "") or None,
                    iva=draft.get("iva"),
                    tournament_id=str(draft.get("tournament_id") or "") or None,
                    origen="telegram_bot",
                    skip_initial_tocino=True,
                )

                cuenta_gastos_id = str(draft.get("cuenta_gastos_id") or "").strip()
                if cuenta_gastos_id:
                    try:
                        from uuid import UUID
                        from devnous.gastos.models import CuentaDeGastos, Documento

                        cg_uuid = UUID(cuenta_gastos_id)
                        cg_result = await session.execute(
                            select(CuentaDeGastos).where(
                                CuentaDeGastos.id == cg_uuid,
                                CuentaDeGastos.empleado_id == empleado.id,
                                CuentaDeGastos.estado == "abierta",
                            )
                        )
                        cuenta = cg_result.scalar_one_or_none()
                        if cuenta:
                            expense.cuenta_gastos_id = cuenta.id
                            expense.referencia_base = cuenta.referencia_base
                            informe_res = await session.execute(
                                select(Documento).where(
                                    Documento.cuenta_gastos_id == cuenta.id,
                                    Documento.tipo == "INFORME",
                                )
                            )
                            informe_doc = informe_res.scalar_one_or_none()
                            if informe_doc:
                                expense.informe_documento_id = informe_doc.id
                    except Exception:
                        pass

                await session.commit()
                await session.refresh(expense)

            self._nuevo_gasto_cancel(chat_id)
            return (
                "✅ Gasto registrado.\n"
                f"• Referencia: {expense.numero_referencia}\n"
                f"• Expense ID: {expense.id}\n"
                f"• Monto: ${float(draft.get('gasto_cantidad') or 0):,.2f} MXN"
            )

        if stage == "photo":
            return "📷 Envíame la foto del ticket para continuar (o usa `/restart`)."

        return "No reconocí el estado del flujo. Usa /restart y vuelve a intentar."

    async def _nuevo_gasto_handle_photo(
        self,
        chat_id: int,
        user_id: int,
        file_bytes: bytes,
        filename: str,
    ) -> Optional[str]:
        from devnous.gastos.services.expense_service import create_expense_from_data, trigger_cfdi_generation

        draft = self._nuevo_gasto_by_chat.get(int(chat_id))
        if not draft or str(draft.get("stage") or "") != "photo":
            return None

        empleado = await self._assistant_get_empleado(user_id)
        if not empleado:
            self._nuevo_gasto_cancel(chat_id)
            return "⚠️ Tu Telegram no está vinculado a un usuario interno activo."

        file_b64 = base64.b64encode(file_bytes).decode("ascii")
        draft["file_b64"] = file_b64
        draft["filename"] = filename or "ticket.jpg"
        draft["content_type"] = "image/jpeg"

        session_maker = self._assistant_session_maker()
        async with session_maker() as session:
            fecha_iso = str(draft.get("fecha") or "").strip()
            expense_date = datetime.combine(date.fromisoformat(fecha_iso), datetime.min.time())
            wants_cfdi = bool(draft.get("generar_cfdi_tocino"))

            expense = await create_expense_from_data(
                session=session,
                empleado_id=empleado.id,
                nombre_enviador=str(getattr(empleado, "nombre", "") or ""),
                proyecto=str(draft.get("proyecto") or ""),
                concepto=str(draft.get("concepto") or ""),
                gasto_cantidad=float(draft.get("gasto_cantidad") or 0),
                fecha=expense_date,
                tipo_gasto="ticket",
                departamento=str(getattr(empleado, "departamento", "") or "Operaciones"),
                fase_torneo=str(draft.get("fase_torneo") or ""),
                metodo_pago=str(draft.get("metodo_pago") or "") or None,
                iva=draft.get("iva"),
                archivo_nombre=str(draft.get("filename") or "ticket.jpg"),
                archivo_data=str(draft.get("file_b64") or ""),
                tournament_id=str(draft.get("tournament_id") or "") or None,
                cfdi_use="G03",
                origen="telegram_bot",
                skip_initial_tocino=not wants_cfdi,
            )

            cuenta_gastos_id = str(draft.get("cuenta_gastos_id") or "").strip()
            if cuenta_gastos_id:
                try:
                    from uuid import UUID
                    from devnous.gastos.models import CuentaDeGastos, Documento

                    cg_uuid = UUID(cuenta_gastos_id)
                    cg_result = await session.execute(
                        select(CuentaDeGastos).where(
                            CuentaDeGastos.id == cg_uuid,
                            CuentaDeGastos.empleado_id == empleado.id,
                            CuentaDeGastos.estado == "abierta",
                        )
                    )
                    cuenta = cg_result.scalar_one_or_none()
                    if cuenta:
                        expense.cuenta_gastos_id = cuenta.id
                        expense.referencia_base = cuenta.referencia_base
                        informe_res = await session.execute(
                            select(Documento).where(
                                Documento.cuenta_gastos_id == cuenta.id,
                                Documento.tipo == "INFORME",
                            )
                        )
                        informe_doc = informe_res.scalar_one_or_none()
                        if informe_doc:
                            expense.informe_documento_id = informe_doc.id
                except Exception:
                    pass

            nova_request_id = None
            if wants_cfdi:
                nova_request_id = await trigger_cfdi_generation(
                    session=session,
                    expense=expense,
                    cfdi_use="G03",
                )

            await session.commit()
            await session.refresh(expense)

        self._nuevo_gasto_cancel(chat_id)
        lines = [
            "✅ Ticket registrado como gasto.",
            f"• Referencia: {expense.numero_referencia}",
            f"• Expense ID: {expense.id}",
            f"• Monto: ${float(draft.get('gasto_cantidad') or 0):,.2f} MXN",
        ]
        if draft.get("cuenta_gastos_ref"):
            lines.append(f"• Cuenta de gastos: {draft.get('cuenta_gastos_ref')}")
        if wants_cfdi:
            if nova_request_id:
                lines.append(f"• Tocino: enviado (`{nova_request_id}`)")
            else:
                lines.append("• Tocino: no se pudo enviar ahora; el gasto quedó guardado.")
        else:
            lines.append("• Factura: no se envió a Tocino.")
        return "\n".join(lines)

    def _assistant_infer_finance_range(self, text: str) -> Tuple[Optional[str], Optional[str]]:
        lower = (text or "").lower()
        today = datetime.now(timezone.utc).date()
        if "ultimos tres meses" in lower or "últimos tres meses" in lower:
            return (today - timedelta(days=90)).isoformat(), today.isoformat()
        if "ultimo mes" in lower or "último mes" in lower:
            return (today - timedelta(days=30)).isoformat(), today.isoformat()
        if "ultima semana" in lower or "última semana" in lower:
            return (today - timedelta(days=7)).isoformat(), today.isoformat()
        return None, None

    async def _assistant_try_direct_finance_fallback(
        self,
        *,
        chat_id: int,
        empleado: Any,
        text: str,
    ) -> Optional[str]:
        from samchat.assistant.tools import finance_expense_create, finance_ops_query

        lower = (text or "").strip().lower()
        if not lower:
            return None

        context_key = self._assistant_context_key(chat_id)
        role = str(getattr(empleado, "rol", "") or "").strip().lower()
        write_intent = any(
            token in lower
            for token in ("registra", "registrar", "agrega", "agregar", "crea", "crear", "captura")
        ) and "gasto" in lower

        if write_intent:
            if role not in {"admin", "superadmin"}:
                return "Tu usuario puede consultar finanzas, pero este alta de gasto por bot requiere rol admin o superadmin."

            amount = self._assistant_extract_amount(text)
            concept = self._assistant_extract_concept(text)
            project = self._assistant_extract_project(text) or str(getattr(empleado, "proyecto_predeterminado", "") or "").strip()
            expense_date = self._assistant_extract_date_text(text) or datetime.now(timezone.utc).date().isoformat()

            if amount is None:
                return "Puedo registrarlo, pero me falta el monto. Dímelo en formato claro, por ejemplo: 77.77 pesos."
            if not concept:
                return "Puedo registrarlo, pero me falta el concepto del gasto."
            if not project:
                return "Puedo registrarlo, pero me falta el proyecto. Dímelo así: proyecto Copa Telmex 2026."

            args = {
                "empleado_id": str(empleado.id),
                "proyecto": project,
                "concepto": concept,
                "gasto_cantidad": amount,
                "fecha": expense_date,
            }
            self._assistant_pending_direct_action_by_chat[context_key] = {
                "action": "finance_expense_create",
                "args": args,
            }
            return self._assistant_direct_pending_summary({"action": "finance_expense_create", "args": args})

        date_from, date_to = self._assistant_infer_finance_range(lower)
        concepto = None
        if any(token in lower for token in ("hospedaje", "hotel", "hoteles")):
            concepto = "hosped"
        elif any(token in lower for token in ("alimento", "alimentos", "restaurante", "comida")):
            concepto = "alimento"

        if not any(token in lower for token in ("cuanto", "cuánto", "gasto", "gastado", "total", "pagado")):
            return None

        session_maker = self._assistant_session_maker()
        async with session_maker() as session:
            result = await finance_ops_query(
                session,
                question=text,
                concepto=concepto,
                date_from=date_from,
                date_to=date_to,
                limit=20,
            )

        totals = ((result or {}).get("expenses") or {}).get("totals") or {}
        breakdown = (((result or {}).get("expenses") or {}).get("breakdowns") or {}).get("por_proyecto") or []
        total_amount = float(totals.get("monto_total") or 0)
        total_records = int(totals.get("registros") or 0)
        date_desc = (
            f"del {date_from} al {date_to}"
            if date_from and date_to
            else "en el rango consultado"
        )
        lines = [
            f"Total identificado {date_desc}: *${total_amount:,.2f} MXN* en *{total_records}* gasto(s)."
        ]
        top_rows = [row for row in breakdown if isinstance(row, dict)][:3]
        if top_rows:
            lines.append("")
            lines.append("Top proyectos:")
            for row in top_rows:
                lines.append(
                    f"• {row.get('proyecto')}: ${float(row.get('monto') or 0):,.2f} MXN"
                )
        return "\n".join(lines)

    async def _assistant_confirm_direct_pending(
        self,
        *,
        chat_id: int,
        empleado: Any,
        approve: bool,
    ) -> Optional[str]:
        from samchat.assistant.tools import finance_expense_create

        context_key = self._assistant_context_key(chat_id)
        pending = self._assistant_pending_direct_action_by_chat.get(context_key)
        if not pending:
            return None
        if not approve:
            self._assistant_pending_direct_action_by_chat.pop(context_key, None)
            return "Acción cancelada."

        action = str(pending.get("action") or "").strip().lower()
        args = pending.get("args") or {}
        if action != "finance_expense_create":
            self._assistant_pending_direct_action_by_chat.pop(context_key, None)
            return "La acción pendiente ya no es válida."

        session_maker = self._assistant_session_maker()
        async with session_maker() as session:
            result = await finance_expense_create(session, **args)

        self._assistant_pending_direct_action_by_chat.pop(context_key, None)
        return (
            "✅ Gasto registrado.\n"
            f"• Referencia: {result.get('numero_referencia')}\n"
            f"• Expense ID: {result.get('expense_id')}\n"
            f"• Monto: ${float(result.get('monto') or 0):,.2f} MXN"
        )

    def _welcome_menu(self, chat_id: int) -> str:
        assistant_on = self._assistant_mode_by_chat.get(chat_id, False)
        mode = self._assistant_mode(chat_id)
        return "\n".join(
            [
                "🏆 *Copa Telmex Bot*",
                "",
                "🤖 *Funciones agénticas (sam.chat assistant)*",
                "• `/assistant on` activar modo agente con acceso a datos",
                "• `/mode ahorro|balanceado|calidad` cambiar modo de costo/calidad",
                "• `/modo empresa|finanzas|operaciones` cambiar dominio activo del chat",
                "• `/db actual` ver contexto actual; `/db cambiar finanzas|operaciones|empresa` cambiarlo",
                "• Haz preguntas libres: gastos, contabilidad, torneos, jugadores, reportes",
                "• Envía foto/audio/documento (Excel/CSV/Word/MD/TXT) para extracción y acciones",
                "• Writes con aprobación: `/ok` confirmar, `/cancel` rechazar",
                "• Exporta reportes: responde `Excel` o `PDF` cuando se te ofrezca",
                "",
                "📎 *Carga de imágenes*",
                "• `/img` subir imagen para referencia",
                "• `/lastimg` ver última imagen guardada",
                "",
                "💰 *Registro de gastos*",
                "• `/nuevo_gasto` registrar un gasto paso a paso (sin asistente)",
                "• `/restart` reiniciar el flujo de gasto si te equivocaste",
                "",
                "📑 *Solicitudes e informes (aprobaciones)*",
                *telegram_console_bot_menu_lines(),
                "",
                "⚙️ *Comandos rápidos*",
                "• `/assistant off` desactivar modo agente",
                "• `/status` estado del torneo",
                "",
                f"Estado actual asistente: *{'ON' if assistant_on else 'OFF'}*",
                f"Modo LLM actual: *{mode.upper()}*",
                f"Contexto de datos actual: *{self._assistant_domain_label(chat_id)}*",
            ]
        )

    async def _ensure_bot_menu_commands(self) -> None:
        if self._bot_commands_installed:
            return
        commands = [
            {"command": "start", "description": "Inicio y bienvenida"},
            {"command": "menu", "description": "Menú principal del bot"},
            {"command": "help", "description": "Ayuda y lista de funciones"},
            {"command": "ayuda", "description": "Ayuda en español"},
            *telegram_console_bot_commands(),
            {"command": "nuevo_gasto", "description": "Registrar un gasto paso a paso"},
            {"command": "assistant", "description": "Activar modo asistente (off: escribe /assistant off)"},
            {"command": "modo", "description": "Ver contexto; luego /modo finanzas u operaciones"},
            {"command": "db", "description": "Ver contexto de datos; /db cambiar …"},
            {"command": "mode", "description": "Modo del asistente: /mode ahorro|balanceado|calidad"},
            {"command": "status", "description": "Estado del torneo y del chat"},
            {"command": "estado", "description": "Igual que /status"},
            {"command": "ok", "description": "Confirmar acción pendiente del asistente"},
            {"command": "cancel", "description": "Cancelar acción o rechazo pendiente"},
            {"command": "restart", "description": "Reiniciar el flujo /nuevo_gasto"},
            {"command": "img", "description": "Modo subir imagen de referencia"},
            {"command": "upload", "description": "Alias de /img para subir imagen"},
            {"command": "foto", "description": "Alias de /img para subir imagen"},
            {"command": "lastimg", "description": "Última imagen guardada en este chat"},
        ]
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.api_base}/setMyCommands",
                    json={"commands": commands},
                    timeout=15,
                ) as resp:
                    data = await resp.json()
                    if not data.get("ok"):
                        logger.warning("setMyCommands failed: %s", data)
        except Exception as exc:
            logger.warning("setMyCommands error: %s", exc)
        self._bot_commands_installed = True

    async def _gastos_execute_approve(self, chat_id: int, empleado: Any, doc_uuid: UUID) -> None:
        await self._gastos_document_runtime.execute_approve(chat_id, empleado, doc_uuid)

    async def _gastos_execute_reject(
        self,
        chat_id: int,
        empleado: Any,
        doc_uuid: UUID,
        comentario: str,
    ) -> None:
        await self._gastos_document_runtime.execute_reject(
            chat_id,
            empleado,
            doc_uuid,
            comentario,
        )

    async def _gastos_complete_pending_reject(self, chat_id: int, user_id: int, text: str) -> None:
        await self._gastos_document_runtime.complete_pending_reject(
            chat_id,
            user_id,
            text,
        )

    async def _gastos_send_pendientes(self, chat_id: int, user_id: int) -> None:
        await self._gastos_document_runtime.send_pendientes(chat_id, user_id)

    async def _gastos_send_mis_solicitudes(self, chat_id: int, user_id: int) -> None:
        await self._gastos_document_runtime.send_mis_solicitudes(chat_id, user_id)

    async def _gastos_send_solicitud_ref(self, chat_id: int, user_id: int, ref: str) -> None:
        await self._gastos_document_runtime.send_solicitud_ref(chat_id, user_id, ref)

    async def _handle_gastos_document_callback(self, callback_query: Dict[str, Any]) -> bool:
        return await self._gastos_document_runtime.handle_callback(callback_query)

    async def _assistant_anthropic_text(self, text: str) -> Optional[str]:
        key = self._assistant_anthropic_key()
        if not key:
            return None
        try:
            import anthropic
        except Exception:
            return None
        model = os.getenv("TELEGRAM_ASSISTANT_ANTHROPIC_MODEL", "claude-sonnet-4-5-20250929")
        system = (
            "Eres el asistente de sam.chat en Telegram. "
            "Responde en espanol, de forma concreta. "
            "No remitas al usuario al panel web. "
            "Si piden una escritura y no puedes ejecutarla, pide solo el dato faltante o explica que el backend no estuvo disponible."
        )

        def _call() -> str:
            client = anthropic.Anthropic(api_key=key)
            resp = client.messages.create(
                model=model,
                max_tokens=900,
                temperature=0.2,
                system=system,
                messages=[{"role": "user", "content": text}],
            )
            parts = []
            for block in getattr(resp, "content", []) or []:
                bt = getattr(block, "type", "")
                if bt == "text":
                    parts.append(getattr(block, "text", ""))
            return "\n".join([p for p in parts if p]).strip()

        out = await asyncio.to_thread(_call)
        return out or None

    async def _assistant_anthropic_image(self, *, text: str, image_bytes: bytes, content_type: str) -> Optional[str]:
        key = self._assistant_anthropic_key()
        if not key:
            return None
        try:
            import anthropic
        except Exception:
            return None
        model = os.getenv("TELEGRAM_ASSISTANT_ANTHROPIC_MODEL", "claude-sonnet-4-5-20250929")
        media_type = content_type or "image/jpeg"
        b64 = base64.b64encode(image_bytes).decode("ascii")
        prompt = (
            "Analiza esta imagen y responde en espanol. "
            "Si parece ticket/nota de gasto, extrae: comercio, fecha, monto, concepto. "
            "Si el usuario incluyo nota, tomala en cuenta.\n\n"
            f"Nota del usuario: {text or '(sin nota)'}"
        )

        def _call() -> str:
            client = anthropic.Anthropic(api_key=key)
            resp = client.messages.create(
                model=model,
                max_tokens=900,
                temperature=0.2,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": media_type,
                                    "data": b64,
                                },
                            },
                            {"type": "text", "text": prompt},
                        ],
                    }
                ],
            )
            parts = []
            for block in getattr(resp, "content", []) or []:
                if getattr(block, "type", "") == "text":
                    parts.append(getattr(block, "text", ""))
            return "\n".join([p for p in parts if p]).strip()

        out = await asyncio.to_thread(_call)
        return out or None

    async def _assistant_get_empleado(self, user_id: int):
        return await self._get_authorized_empleado(user_id)

    def _assistant_session_maker(self):
        session_maker = self._resolve_auth_session_maker()
        if not session_maker:
            raise RuntimeError("Assistant DB session not configured")
        return session_maker

    async def _assistant_get_or_create_conversation_id(
        self,
        *,
        chat_id: int,
        empleado: Any,
    ) -> str:
        from devnous.gastos.models import AssistantConversation
        from samchat.assistant.router import ConversationCreateRequest, create_conversation

        context_key = self._assistant_context_key(chat_id)
        domain = context_key[1]
        cached = self._assistant_conv_by_chat.get(context_key)
        if cached:
            return cached

        session_maker = self._assistant_session_maker()

        async with session_maker() as session:
            rows = (
                await session.execute(
                    select(AssistantConversation)
                    .where(
                        AssistantConversation.empleado_id == empleado.id,
                        AssistantConversation.archived == False,
                        AssistantConversation.tournament_key == "copa_telmex",
                    )
                    .order_by(AssistantConversation.updated_at.desc())
                    .limit(20)
                )
            ).scalars().all()
            for row in rows:
                md = row.metadata_ if isinstance(row.metadata_, dict) else {}
                if int(md.get("telegram_chat_id") or 0) == int(chat_id) and str(md.get("assistant_domain") or "empresa").strip().lower() == domain:
                    conv_id = str(row.id)
                    self._assistant_conv_by_chat[context_key] = conv_id
                    return conv_id

        async with session_maker() as session:
            created = await create_conversation(
                payload=ConversationCreateRequest(
                    title=self._assistant_title(chat_id),
                    tournament_key=self._assistant_tournament_key(chat_id),
                ),
                request=None,
                current_empleado=empleado,
                session=session,
            )
            conv = (
                await session.execute(
                    select(AssistantConversation).where(AssistantConversation.id == created.conversation_id)
                )
            ).scalars().first()
            if conv:
                md = conv.metadata_ if isinstance(conv.metadata_, dict) else {}
                md["telegram_chat_id"] = int(chat_id)
                md["source"] = "telegram"
                md["assistant_domain"] = domain
                conv.metadata_ = md
                await session.commit()
            conv_id = str(created.conversation_id)
            self._assistant_conv_by_chat[context_key] = conv_id
            return conv_id

    def _assistant_has_exportable_trace(self, tool_trace: Any) -> bool:
        if not isinstance(tool_trace, list):
            return False
        for step in reversed(tool_trace):
            if not isinstance(step, dict):
                continue
            result = step.get("result")
            if not isinstance(result, dict):
                continue
            if any(k in result for k in ("totals", "budget", "comparison_yoy", "projection", "breakdown", "trend_monthly")):
                return True
            rows = result.get("rows")
            if isinstance(rows, list) and rows and all(isinstance(r, dict) for r in rows):
                return True
            items = result.get("items")
            if isinstance(items, list) and items and all(isinstance(r, dict) for r in items):
                return True
        return False

    def _assistant_export_intent(self, text: str) -> Optional[str]:
        msg = (text or "").strip().lower()
        if not msg:
            return None
        if msg in {"pdf", "exportar pdf", "exporta pdf"} or " pdf" in f" {msg}":
            return "pdf"
        if msg in {"excel", "csv", "xlsx", "exportar excel", "exporta excel"}:
            return "csv"
        if any(token in msg for token in ("excel", "csv", "xlsx")):
            return "csv"
        return None

    async def send_document(
        self,
        chat_id: int,
        file_bytes: bytes,
        filename: str,
        caption: Optional[str] = None,
    ) -> Dict[str, Any]:
        async with aiohttp.ClientSession() as session:
            form = aiohttp.FormData()
            form.add_field("chat_id", str(chat_id))
            if caption:
                form.add_field("caption", caption)
            form.add_field(
                "document",
                file_bytes,
                filename=filename,
                content_type="application/octet-stream",
            )
            async with session.post(f"{self.api_base}/sendDocument", data=form) as resp:
                return await resp.json()

    async def _assistant_export_last_report(
        self,
        *,
        chat_id: int,
        empleado: Any,
        export_format: str,
    ) -> str:
        from samchat.assistant.router import (
            AssistantReportExportRequest,
            export_assistant_report,
        )

        export_ctx = self._assistant_last_export_by_chat.get(self._assistant_context_key(chat_id))
        if not export_ctx:
            return "No tengo un reporte reciente para exportar en este chat."
        session_maker = self._assistant_session_maker()
        conv_id = str(export_ctx.get("conversation_id") or "")
        run_id = str(export_ctx.get("run_id") or "")
        if not conv_id:
            return "No encontré conversación activa para exportar."

        async with session_maker() as session:
            response = await export_assistant_report(
                payload=AssistantReportExportRequest(
                    conversation_id=conv_id,
                    run_id=run_id or None,
                    format=("pdf" if export_format == "pdf" else "csv"),
                    filename=f"assistant_telegram_report_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.{ 'pdf' if export_format == 'pdf' else 'csv'}",
                ),
                current_empleado=empleado,
                session=session,
            )
        data = bytes(response.body or b"")
        if not data:
            return "No pude generar el archivo de exportación."
        filename = (
            f"reporte_financiero_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.pdf"
            if export_format == "pdf"
            else f"reporte_financiero_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.csv"
        )
        sent = await self.send_document(
            chat_id=chat_id,
            file_bytes=data,
            filename=filename,
            caption="Reporte generado por sam.chat assistant",
        )
        if not sent.get("ok"):
            return f"No pude enviar el archivo por Telegram: {sent.get('description') or 'error desconocido'}"
        return f"Listo. Te envié el reporte en {'PDF' if export_format == 'pdf' else 'Excel (CSV)'}."

    async def _assistant_send_text(self, *, chat_id: int, empleado: Any, text: str) -> str:
        from samchat.assistant.router import MessageCreateRequest, create_message

        conv_id = await self._assistant_get_or_create_conversation_id(chat_id=chat_id, empleado=empleado)
        session_maker = self._assistant_session_maker()
        effective_text = self._assistant_apply_domain_context(chat_id=chat_id, text=text)
        context_key = self._assistant_context_key(chat_id)

        try:
            async with session_maker() as session:
                response = await create_message(
                    payload=MessageCreateRequest(
                        message=effective_text,
                        tournament_key=self._assistant_tournament_key(chat_id),
                        assistant_mode=self._assistant_mode(chat_id),
                    ),
                    conversation_id=conv_id,
                    openai_api_key=self._assistant_openai_key(),
                    current_empleado=empleado,
                    session=session,
                )
        except Exception:
            direct = await self._assistant_try_direct_finance_fallback(
                chat_id=chat_id,
                empleado=empleado,
                text=text,
            )
            if direct:
                return direct
            # Last-resort fallback when assistant backend/tool path is unavailable.
            if self._assistant_prefers_anthropic():
                anth = await self._assistant_anthropic_text(text)
                if anth:
                    return anth
            raise

        route_info = next(
            (
                item.get("assistant_route")
                for item in (response.tool_trace or [])
                if isinstance(item, dict) and isinstance(item.get("assistant_route"), dict)
            ),
            {},
        )
        direct_finance = self._assistant_domain(chat_id) == "finanzas"
        if (
            direct_finance
            and not response.pending_confirmation
            and not self._assistant_has_tool_activity(response.tool_trace)
            and (
                str(route_info.get("route") or "").strip().lower() == "agentic_write"
                or self._assistant_response_looks_deferred(response.assistant_message)
            )
        ):
            direct = await self._assistant_try_direct_finance_fallback(
                chat_id=chat_id,
                empleado=empleado,
                text=text,
            )
            if direct:
                return direct

        if response.pending_confirmation:
            self._assistant_pending_run_by_chat[context_key] = response.pending_confirmation.run_id
            summary = response.pending_confirmation.summary or "Accion write pendiente de confirmacion."
            return (
                f"{response.assistant_message}\n\n"
                f"⚠️ Confirmacion requerida:\n{summary}\n\n"
                "Responde /ok para aprobar o /cancel para cancelar."
            )
        if self._assistant_has_exportable_trace(response.tool_trace):
            self._assistant_last_export_by_chat[context_key] = {
                "conversation_id": conv_id,
                "run_id": str(response.run_id or ""),
            }
            return (
                f"{response.assistant_message}\n\n"
                "📤 ¿Quieres exportarlo? Responde `Excel` o `PDF`."
            )
        return response.assistant_message

    async def _assistant_send_media(
        self,
        *,
        chat_id: int,
        empleado: Any,
        kind: str,
        file_bytes: bytes,
        filename: str,
        content_type: str,
        note: Optional[str] = None,
    ) -> str:
        from starlette.datastructures import Headers, UploadFile
        from samchat.assistant.router import create_media_message

        conv_id = await self._assistant_get_or_create_conversation_id(chat_id=chat_id, empleado=empleado)
        session_maker = self._assistant_session_maker()
        context_key = self._assistant_context_key(chat_id)
        effective_note = self._assistant_apply_domain_context(chat_id=chat_id, text=note or "")

        upload = UploadFile(
            file=io.BytesIO(file_bytes),
            filename=filename,
            headers=Headers({"content-type": content_type}),
        )

        try:
            async with session_maker() as session:
                response = await create_media_message(
                    conversation_id=conv_id,
                    kind=kind,
                    note=effective_note,
                    tournament_key=self._assistant_tournament_key(chat_id),
                    assistant_mode=self._assistant_mode(chat_id),
                    file=upload,
                    openai_api_key=self._assistant_openai_key(),
                    current_empleado=empleado,
                    session=session,
                )
        except Exception:
            # Last-resort fallback when assistant backend/tool path is unavailable.
            if self._assistant_prefers_anthropic():
                if kind == "image":
                    anth = await self._assistant_anthropic_image(
                        text=note or "",
                        image_bytes=file_bytes,
                        content_type=content_type,
                    )
                    if anth:
                        return anth
                if kind == "voice":
                    return "No pude procesar la voz con el backend. Intenta de nuevo en texto o imagen."
            raise

        if response.pending_confirmation:
            self._assistant_pending_run_by_chat[context_key] = response.pending_confirmation.run_id
            summary = response.pending_confirmation.summary or "Accion write pendiente de confirmacion."
            return (
                f"{response.assistant_message}\n\n"
                f"⚠️ Confirmacion requerida:\n{summary}\n\n"
                "Responde /ok para aprobar o /cancel para cancelar."
            )
        if self._assistant_has_exportable_trace(response.tool_trace):
            self._assistant_last_export_by_chat[context_key] = {
                "conversation_id": conv_id,
                "run_id": str(response.run_id or ""),
            }
            return (
                f"{response.assistant_message}\n\n"
                "📤 ¿Quieres exportarlo? Responde `Excel` o `PDF`."
            )
        return response.assistant_message

    async def _assistant_confirm_pending(self, *, chat_id: int, empleado: Any, approve: bool) -> str:
        from samchat.assistant.router import ConfirmRequest, confirm_write

        context_key = self._assistant_context_key(chat_id)
        run_id = self._assistant_pending_run_by_chat.get(context_key)
        if not run_id:
            return "No hay ninguna accion pendiente por confirmar."

        conv_id = await self._assistant_get_or_create_conversation_id(chat_id=chat_id, empleado=empleado)
        session_maker = self._assistant_session_maker()

        async with session_maker() as session:
            response = await confirm_write(
                payload=ConfirmRequest(
                    run_id=run_id,
                    approve=approve,
                    assistant_mode=self._assistant_mode(chat_id),
                ),
                conversation_id=conv_id,
                openai_api_key=self._assistant_openai_key(),
                current_empleado=empleado,
                session=session,
            )
        if response.pending_confirmation:
            self._assistant_pending_run_by_chat[context_key] = response.pending_confirmation.run_id
            return (
                f"{response.assistant_message}\n\n"
                "Responde /ok para aprobar definitivamente o /cancel para cancelar."
            )
        self._assistant_pending_run_by_chat.pop(context_key, None)
        return response.assistant_message

    def _set_assistant_domain(self, chat_id: int, domain: str) -> str:
        normalized = (domain or "").strip().lower()
        aliases = {
            "empresa": "empresa",
            "general": "empresa",
            "all": "empresa",
            "finanzas": "finanzas",
            "finanza": "finanzas",
            "finance": "finanzas",
            "operaciones": "operaciones",
            "operacion": "operaciones",
            "ops": "operaciones",
            "torneo": "operaciones",
        }
        target = aliases.get(normalized)
        if not target:
            return "Modo invalido. Usa: /modo empresa, /modo finanzas o /modo operaciones."

        self._assistant_domain_mode_by_chat[chat_id] = target
        self._assistant_pending_domain_switch_by_chat.pop(chat_id, None)
        old_keys = [key for key in self._assistant_pending_run_by_chat if key[0] == int(chat_id)]
        for key in old_keys:
            self._assistant_pending_run_by_chat.pop(key, None)
        direct_keys = [key for key in self._assistant_pending_direct_action_by_chat if key[0] == int(chat_id)]
        for key in direct_keys:
            self._assistant_pending_direct_action_by_chat.pop(key, None)
        ticket_keys = [key for key in self._assistant_pending_ticket_by_chat if key[0] == int(chat_id)]
        for key in ticket_keys:
            self._assistant_pending_ticket_by_chat.pop(key, None)
        old_exports = [key for key in self._assistant_last_export_by_chat if key[0] == int(chat_id)]
        for key in old_exports:
            self._assistant_last_export_by_chat.pop(key, None)

        return (
            "✅ Contexto de datos actualizado.\n"
            f"Modo activo: *{self._assistant_domain_label(chat_id)}*\n"
            f"{self._assistant_describe_context(chat_id)}"
        )

    async def send_message(
        self,
        chat_id: int,
        text: str,
        parse_mode: str = "Markdown",
        reply_markup: Optional[Dict] = None
    ):
        """Send message to Telegram"""
        async with aiohttp.ClientSession() as session:
            payload = {
                "chat_id": chat_id,
                "text": text,
            }
            if parse_mode:
                payload["parse_mode"] = parse_mode

            if reply_markup:
                payload["reply_markup"] = reply_markup

            async with session.post(
                f"{self.api_base}/sendMessage",
                json=payload
            ) as resp:
                data = await resp.json()
                # Fallback to plain text when Markdown entity parsing fails.
                if not data.get("ok") and payload.get("parse_mode"):
                    desc = str(data.get("description") or "").lower()
                    if "parse entities" in desc or "can't parse" in desc:
                        payload.pop("parse_mode", None)
                        async with session.post(
                            f"{self.api_base}/sendMessage",
                            json=payload
                        ) as retry_resp:
                            return await retry_resp.json()
                return data

    async def answer_callback_query(
        self,
        callback_query_id: str,
        text: Optional[str] = None
    ):
        """Answer callback query from inline keyboard"""
        async with aiohttp.ClientSession() as session:
            payload = {
                "callback_query_id": callback_query_id
            }
            if text:
                payload["text"] = text

            async with session.post(
                f"{self.api_base}/answerCallbackQuery",
                json=payload
            ) as resp:
                return await resp.json()

    async def download_photo(self, file_id: str) -> bytes:
        """Download photo from Telegram"""
        file_bytes, _ = await self.download_file(file_id, max_bytes=self._max_image_bytes)
        return file_bytes

    async def download_file(self, file_id: str, *, max_bytes: Optional[int] = None) -> Tuple[bytes, str]:
        """
        Download a file (photo/document) from Telegram.

        Returns:
            (file_bytes, file_path_from_telegram)
        """
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{self.api_base}/getFile",
                params={"file_id": file_id},
            ) as resp:
                result = await resp.json()
                file_path = result["result"]["file_path"]

            file_url = f"https://api.telegram.org/file/bot{self.telegram_token}/{file_path}"
            async with session.get(file_url) as resp:
                chunks = []
                total = 0
                async for chunk in resp.content.iter_chunked(64 * 1024):
                    total += len(chunk)
                    if max_bytes is not None and total > int(max_bytes):
                        raise UserFacingInputError(
                            "⚠️ El archivo excede el tamaño máximo permitido para este tipo de carga."
                        )
                    chunks.append(chunk)
                return b"".join(chunks), file_path

    async def _transcribe_audio(self, audio_bytes: bytes, filename: str = "voice.ogg") -> Optional[str]:
        """
        Transcribe audio bytes using OpenAI Audio Transcriptions API.

        Returns transcribed text or None if transcription is not available.
        """
        openai_key = (
            getattr(getattr(self.bot, "operations", None), "openai_key", None)
            or __import__("os").getenv("OPENAI_API_KEY")
        )
        if not openai_key:
            return None

        model = __import__("os").getenv("OPENAI_TRANSCRIBE_MODEL", "gpt-4o-mini-transcribe")
        url = "https://api.openai.com/v1/audio/transcriptions"
        headers = {"Authorization": f"Bearer {openai_key}"}

        form = aiohttp.FormData()
        form.add_field("model", model)
        form.add_field("language", "es")
        form.add_field("response_format", "json")
        form.add_field("file", audio_bytes, filename=filename, content_type="audio/ogg")

        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, data=form, timeout=aiohttp.ClientTimeout(total=45)) as resp:
                if resp.status >= 400:
                    body = await resp.text()
                    logger.warning(f"audio transcription failed: {resp.status} {body[:200]}")
                    return None
                data = await resp.json()
                txt = (data.get("text") or "").strip()
                return txt or None

    def _caption_requests_upload(self, caption: str) -> bool:
        cap = (caption or "").strip().lower()
        return cap.startswith("/img") or cap.startswith("/upload") or cap.startswith("/foto")

    def _safe_ext_from_path(self, telegram_file_path: str) -> str:
        # Telegram file_path includes extension for documents; for photos usually ends with .jpg.
        p = Path(telegram_file_path)
        ext = p.suffix.lower()
        if ext in {".jpg", ".jpeg", ".png", ".webp"}:
            return ext
        return ".jpg"

    def _build_upload_paths(self, chat_id: int, message_id: int, ext: str) -> Tuple[Path, Path]:
        chat_dir = self._uploads_dir / str(chat_id)
        chat_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        base = f"{ts}_{message_id}"
        return chat_dir / f"{base}{ext}", chat_dir / f"{base}.json"

    def _make_image_id(self, chat_id: int, message_id: int, content: bytes) -> str:
        h = sha256(content).hexdigest()[:16]
        return f"tg_{chat_id}_{message_id}_{h}"

    def _iter_upload_records(self, chat_id: int) -> list[dict[str, Any]]:
        chat_dir = self._uploads_dir / str(chat_id)
        if not chat_dir.exists():
            return []

        records: list[dict[str, Any]] = []
        for meta_path in sorted(chat_dir.glob("*.json")):
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                logger.warning("Invalid upload metadata file: %s", meta_path)
                continue

            image_id = str(meta.get("image_id") or "")
            image_path_raw = meta.get("image_path")
            image_path = Path(image_path_raw) if image_path_raw else None
            if image_path is None or not image_path.exists():
                stem = meta_path.stem
                matches = list(chat_dir.glob(f"{stem}.*"))
                image_path = next((path for path in matches if path.suffix.lower() != ".json"), None)

            size_bytes = int(meta.get("size_bytes") or (image_path.stat().st_size if image_path and image_path.exists() else 0))
            created_at = datetime.fromisoformat(meta.get("created_at")) if meta.get("created_at") else datetime.fromtimestamp(meta_path.stat().st_mtime, tz=timezone.utc)
            records.append(
                {
                    "image_id": image_id,
                    "meta_path": meta_path,
                    "image_path": image_path,
                    "size_bytes": size_bytes,
                    "created_at": created_at,
                }
            )
        return records

    def _delete_upload_record(self, record: Dict[str, Any]) -> None:
        image_path = record.get("image_path")
        meta_path = record.get("meta_path")
        image_id = record.get("image_id")

        if isinstance(image_path, Path) and image_path.exists():
            image_path.unlink(missing_ok=True)
        if isinstance(meta_path, Path) and meta_path.exists():
            meta_path.unlink(missing_ok=True)

        stale_chats = [chat_id for chat_id, uploaded in self._last_upload_by_chat.items() if uploaded.image_id == image_id]
        for chat_id in stale_chats:
            self._last_upload_by_chat.pop(chat_id, None)

    def _prune_uploads_for_chat(self, chat_id: int) -> None:
        records = self._iter_upload_records(chat_id)
        if not records:
            return

        now = datetime.now(timezone.utc)
        retention_cutoff = now.timestamp() - (self._upload_retention_hours * 3600)

        kept: list[dict[str, Any]] = []
        removed = 0
        for record in sorted(records, key=lambda item: item["created_at"]):
            created_at = record["created_at"]
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            if created_at.timestamp() < retention_cutoff:
                self._delete_upload_record(record)
                removed += 1
            else:
                kept.append(record)

        if len(kept) > self._upload_max_files_per_chat:
            overflow = len(kept) - self._upload_max_files_per_chat
            for record in kept[:overflow]:
                self._delete_upload_record(record)
                removed += 1
            kept = kept[overflow:]

        total_bytes = sum(record["size_bytes"] for record in kept)
        while kept and total_bytes > self._upload_max_bytes_per_chat:
            record = kept.pop(0)
            total_bytes -= record["size_bytes"]
            self._delete_upload_record(record)
            removed += 1

        if removed:
            logger.info("Pruned %s telegram uploads for chat %s", removed, chat_id)

    async def _handle_image_upload(
        self,
        chat_id: int,
        user_id: int,
        message_id: int,
        caption: str,
        file_bytes: bytes,
        telegram_file_path: str,
    ) -> UploadedImage:
        ext = self._safe_ext_from_path(telegram_file_path)
        image_path, meta_path = self._build_upload_paths(chat_id, message_id, ext)
        image_id = self._make_image_id(chat_id, message_id, file_bytes)

        image_path.write_bytes(file_bytes)
        meta = {
            "image_id": image_id,
            "chat_id": chat_id,
            "user_id": user_id,
            "message_id": message_id,
            "caption": caption or "",
            "telegram_file_path": telegram_file_path,
            "image_path": str(image_path),
            "size_bytes": len(file_bytes),
            "sha256": sha256(file_bytes).hexdigest(),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        meta_path.write_text(json.dumps(meta, ensure_ascii=True, indent=2), encoding="utf-8")

        uploaded = UploadedImage(
            image_id=image_id,
            image_path=str(image_path),
            meta_path=str(meta_path),
        )
        self._last_upload_by_chat[chat_id] = uploaded
        self._awaiting_image_upload.discard(chat_id)
        self._prune_uploads_for_chat(chat_id)
        return uploaded

    async def handle_update(self, update: Dict[str, Any]):
        """Handle Telegram update"""
        try:
            if 'callback_query' in update:
                # Delegate to bot's operations module
                callback_query = update['callback_query']
                actor = actor_from_callback(callback_query)
                if not await self._is_actor_allowed(actor):
                    await self._deny_callback_access(callback_query['id'])
                    return
                chat_id = callback_query['message']['chat']['id']

                data_raw = (callback_query.get("data") or "").strip()
                if gastos_tg.parse_documento_callback(data_raw):
                    handled = await self._handle_gastos_document_callback(callback_query)
                    if handled:
                        return

                if hasattr(self.bot.operations, 'handle_callback_query'):
                    await self.bot.operations.handle_callback_query(
                        callback_query,
                        self
                    )

            elif 'message' in update:
                message = update['message']
                actor = actor_from_message(message)
                if not await self._is_actor_allowed(actor):
                    await self._deny_message_access(actor.chat_id)
                    return
                chat_id = message['chat']['id']
                user_id = message['from']['id']
                message_id = int(message.get("message_id", 0))

                # Handle photo messages
                if 'photo' in message:
                    logger.info(f"📸 Photo from chat {chat_id}")
                    rate_limit_error = self._consume_media_rate_limit(chat_id=chat_id, user_id=user_id)
                    if rate_limit_error:
                        await self.send_message(chat_id, rate_limit_error)
                        return

                    caption = message.get("caption", "") or ""
                    photos = message['photo']
                    largest_photo = max(photos, key=lambda p: p.get('file_size', 0))
                    size_error = self._validate_declared_size(
                        label="Imagen",
                        declared_size=largest_photo.get("file_size"),
                        max_bytes=self._max_image_bytes,
                    )
                    if size_error:
                        await self.send_message(chat_id, size_error)
                        return
                    photo_bytes, telegram_file_path = await self.download_file(
                        largest_photo["file_id"],
                        max_bytes=self._max_image_bytes,
                    )
                    image_error = self._validate_image_payload(photo_bytes)
                    if image_error:
                        await self.send_message(chat_id, image_error)
                        return

                    # If user explicitly asked to upload an image (communication), do not send to OCR.
                    if chat_id in self._awaiting_image_upload or self._caption_requests_upload(caption):
                        uploaded = await self._handle_image_upload(
                            chat_id=chat_id,
                            user_id=user_id,
                            message_id=message_id,
                            caption=caption,
                            file_bytes=photo_bytes,
                            telegram_file_path=telegram_file_path,
                        )
                        await self.send_message(
                            chat_id,
                            "\n".join(
                                [
                                    "✅ Imagen recibida y guardada.",
                                    f"• image_id: `{uploaded.image_id}`",
                                    f"• path: `{uploaded.image_path}`",
                                    "",
                                    "Puedes referenciarla después con /lastimg o pegando el image_id.",
                                ]
                            ),
                        )
                        return

                    if chat_id in self._nuevo_gasto_by_chat:
                        draft = self._nuevo_gasto_by_chat.get(int(chat_id)) or {}
                        if str(draft.get("stage") or "") == "photo":
                            answer = await self._nuevo_gasto_handle_photo(
                                chat_id=chat_id,
                                user_id=user_id,
                                file_bytes=photo_bytes,
                                filename=Path(telegram_file_path).name or "receipt.jpg",
                            )
                            if answer:
                                await self.send_message(chat_id, answer)
                                return

                    if self._assistant_mode_by_chat.get(chat_id, False):
                        empleado = await self._assistant_get_empleado(user_id)
                        if not empleado:
                            await self.send_message(
                                chat_id,
                                "⚠️ Tu Telegram no esta vinculado a un usuario interno activo. "
                                "Pide a un admin configurar tu telegram_user_id.",
                            )
                            return
                        if self._assistant_domain(chat_id) == "finanzas":
                            answer = await self._assistant_start_ticket_flow(
                                chat_id=chat_id,
                                empleado=empleado,
                                file_bytes=photo_bytes,
                                filename=Path(telegram_file_path).name or "photo.jpg",
                                content_type="image/jpeg",
                                caption=caption,
                            )
                            await self.send_message(chat_id, answer)
                            return
                        await self.send_message(chat_id, "🤖 Asistente: procesando imagen...")
                        answer = await self._assistant_send_media(
                            chat_id=chat_id,
                            empleado=empleado,
                            kind="image",
                            file_bytes=photo_bytes,
                            filename=Path(telegram_file_path).name or "photo.jpg",
                            content_type="image/jpeg",
                            note=caption,
                        )
                        await self.send_message(chat_id, answer)
                        return

                    # Create Message object with photo
                    from .tournament_bot import Message, MessageIntent
                    msg = Message(
                        text="registro_ocr",  # Trigger OCR registration
                        chat_id=chat_id,
                        user_id=user_id,
                        intent=MessageIntent.OPERATIONS,
                        photo=photo_bytes
                    )

                    # Send to bot
                    await self.send_message(chat_id, "📸 Procesando...\n⏳ 3-5 segundos")
                    response = await self.bot.process_message(msg)

                    # Send response back to Telegram
                    if isinstance(response, dict) and 'text' in response:
                        await self.send_message(
                            chat_id,
                            response['text'],
                            reply_markup=response.get('reply_markup')
                        )
                    else:
                        await self.send_message(chat_id, response)

                # Handle image documents (sent as file)
                elif "document" in message:
                    doc = message["document"]
                    mime = (doc.get("mime_type") or "").lower()
                    caption = message.get("caption", "") or ""
                    if mime.startswith("image/"):
                        rate_limit_error = self._consume_media_rate_limit(chat_id=chat_id, user_id=user_id)
                        if rate_limit_error:
                            await self.send_message(chat_id, rate_limit_error)
                            return
                        size_error = self._validate_declared_size(
                            label="Imagen",
                            declared_size=doc.get("file_size"),
                            max_bytes=self._max_image_bytes,
                        )
                        if size_error:
                            await self.send_message(chat_id, size_error)
                            return
                        file_bytes, telegram_file_path = await self.download_file(
                            doc["file_id"],
                            max_bytes=self._max_image_bytes,
                        )
                        image_error = self._validate_image_payload(file_bytes)
                        if image_error:
                            await self.send_message(chat_id, image_error)
                            return

                        if chat_id in self._awaiting_image_upload or self._caption_requests_upload(caption):
                            uploaded = await self._handle_image_upload(
                                chat_id=chat_id,
                                user_id=user_id,
                                message_id=message_id,
                                caption=caption,
                                file_bytes=file_bytes,
                                telegram_file_path=telegram_file_path,
                            )
                            await self.send_message(
                                chat_id,
                                "\n".join(
                                    [
                                        "✅ Imagen recibida y guardada (document).",
                                        f"• image_id: `{uploaded.image_id}`",
                                        f"• path: `{uploaded.image_path}`",
                                        "",
                                        "Puedes referenciarla después con /lastimg o pegando el image_id.",
                                    ]
                                ),
                            )
                            return

                        if chat_id in self._nuevo_gasto_by_chat:
                            draft = self._nuevo_gasto_by_chat.get(int(chat_id)) or {}
                            if str(draft.get("stage") or "") == "photo":
                                answer = await self._nuevo_gasto_handle_photo(
                                    chat_id=chat_id,
                                    user_id=user_id,
                                    file_bytes=file_bytes,
                                    filename=Path(telegram_file_path).name or doc.get("file_name") or "receipt.jpg",
                                )
                                if answer:
                                    await self.send_message(chat_id, answer)
                                    return

                        if self._assistant_mode_by_chat.get(chat_id, False):
                            empleado = await self._assistant_get_empleado(user_id)
                            if not empleado:
                                await self.send_message(
                                    chat_id,
                                    "⚠️ Tu Telegram no esta vinculado a un usuario interno activo.",
                                )
                                return
                            if self._assistant_domain(chat_id) == "finanzas":
                                answer = await self._assistant_start_ticket_flow(
                                    chat_id=chat_id,
                                    empleado=empleado,
                                    file_bytes=file_bytes,
                                    filename=Path(telegram_file_path).name or doc.get("file_name") or "ticket.jpg",
                                    content_type=mime or "image/jpeg",
                                    caption=caption,
                                )
                                await self.send_message(chat_id, answer)
                                return
                            await self.send_message(chat_id, "🤖 Asistente: procesando imagen...")
                            answer = await self._assistant_send_media(
                                chat_id=chat_id,
                                empleado=empleado,
                                kind="image",
                                file_bytes=file_bytes,
                                filename=Path(telegram_file_path).name or doc.get("file_name") or "image.jpg",
                                content_type=mime or "image/jpeg",
                                note=caption,
                            )
                            await self.send_message(chat_id, answer)
                            return

                        # If user sent an image document without /img, treat it as OCR input for convenience.
                        from .tournament_bot import Message, MessageIntent
                        msg = Message(
                            text="registro_ocr",
                            chat_id=chat_id,
                            user_id=user_id,
                            intent=MessageIntent.OPERATIONS,
                            photo=file_bytes,
                        )
                        await self.send_message(chat_id, "📸 Procesando...\n⏳ 3-5 segundos")
                        response = await self.bot.process_message(msg)
                        if isinstance(response, dict) and "text" in response:
                            await self.send_message(chat_id, response["text"], reply_markup=response.get("reply_markup"))
                        else:
                            await self.send_message(chat_id, response)
                    elif mime.startswith("audio/"):
                        rate_limit_error = self._consume_media_rate_limit(chat_id=chat_id, user_id=user_id)
                        if rate_limit_error:
                            await self.send_message(chat_id, rate_limit_error)
                            return
                        size_error = self._validate_declared_size(
                            label="Audio",
                            declared_size=doc.get("file_size"),
                            max_bytes=self._max_audio_bytes,
                        )
                        if size_error:
                            await self.send_message(chat_id, size_error)
                            return
                        if self._assistant_mode_by_chat.get(chat_id, False):
                            empleado = await self._assistant_get_empleado(user_id)
                            if not empleado:
                                await self.send_message(
                                    chat_id,
                                    "⚠️ Tu Telegram no esta vinculado a un usuario interno activo.",
                                )
                                return
                            file_bytes, telegram_file_path = await self.download_file(
                                doc["file_id"],
                                max_bytes=self._max_audio_bytes,
                            )
                            await self.send_message(chat_id, "🤖 Asistente: procesando audio...")
                            answer = await self._assistant_send_media(
                                chat_id=chat_id,
                                empleado=empleado,
                                kind="voice",
                                file_bytes=file_bytes,
                                filename=Path(telegram_file_path).name or "audio.ogg",
                                content_type=(doc.get("mime_type") or "audio/ogg"),
                                note=caption,
                            )
                            await self.send_message(chat_id, answer)
                            return
                        # Audio document -> transcribe -> process as normal text.
                        await self.send_message(chat_id, "🎤 Transcribiendo audio...\n⏳ 3-10 segundos")
                        file_bytes, telegram_file_path = await self.download_file(
                            doc["file_id"],
                            max_bytes=self._max_audio_bytes,
                        )
                        transcript = await self._transcribe_audio(
                            file_bytes,
                            filename=Path(telegram_file_path).name or "audio.ogg",
                        )
                        if not transcript:
                            await self.send_message(
                                chat_id,
                                "⚠️ No pude transcribir el audio.\n"
                                "Intenta de nuevo o escribe el mensaje en texto.",
                            )
                            return

                        from .tournament_bot import Message
                        msg = Message(text=transcript, chat_id=chat_id, user_id=user_id)
                        response = await self.bot.process_message(msg)
                        await self.send_message(chat_id, f"📝 Te entendí: {transcript}")
                        await self.send_message(chat_id, response)
                    else:
                        # Handle spreadsheets/text documents through assistant media pipeline.
                        if self._assistant_mode_by_chat.get(chat_id, False):
                            rate_limit_error = self._consume_media_rate_limit(chat_id=chat_id, user_id=user_id)
                            if rate_limit_error:
                                await self.send_message(chat_id, rate_limit_error)
                                return
                            empleado = await self._assistant_get_empleado(user_id)
                            if not empleado:
                                await self.send_message(
                                    chat_id,
                                    "⚠️ Tu Telegram no esta vinculado a un usuario interno activo.",
                                )
                                return
                            size_error = self._validate_declared_size(
                                label="Documento",
                                declared_size=doc.get("file_size"),
                                max_bytes=self._max_document_bytes,
                            )
                            if size_error:
                                await self.send_message(chat_id, size_error)
                                return
                            file_bytes, telegram_file_path = await self.download_file(
                                doc["file_id"],
                                max_bytes=self._max_document_bytes,
                            )
                            filename = Path(telegram_file_path).name or doc.get("file_name") or "document.bin"
                            content_type = doc.get("mime_type") or "application/octet-stream"
                            spreadsheet_mimes = {
                                "text/csv",
                                "application/csv",
                                "application/vnd.ms-excel",
                                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            }
                            text_mimes = {
                                "text/plain",
                                "text/markdown",
                                "application/msword",
                                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                            }
                            name_lower = (filename or "").lower()
                            if (
                                content_type in spreadsheet_mimes
                                or name_lower.endswith((".csv", ".xls", ".xlsx"))
                            ):
                                media_kind = "spreadsheet"
                                await self.send_message(chat_id, "🤖 Asistente: procesando spreadsheet...")
                            elif (
                                content_type in text_mimes
                                or name_lower.endswith((".txt", ".md", ".markdown", ".doc", ".docx"))
                            ):
                                media_kind = "text"
                                await self.send_message(chat_id, "🤖 Asistente: procesando documento de texto...")
                            else:
                                await self.send_message(
                                    chat_id,
                                    "⚠️ Documento no soportado. Usa imagen, audio, Excel/CSV o texto (Word/MD/TXT).",
                                )
                                return

                            answer = await self._assistant_send_media(
                                chat_id=chat_id,
                                empleado=empleado,
                                kind=media_kind,
                                file_bytes=file_bytes,
                                filename=filename,
                                content_type=content_type,
                                note=caption,
                            )
                            await self.send_message(chat_id, answer)
                            return

                        await self.send_message(
                            chat_id,
                            "📎 Documento recibido. Activa `/assistant on` para procesar Excel/Word/MD/TXT en modo agéntico.",
                        )
                        return

                # Handle voice notes/audio messages
                elif "voice" in message or "audio" in message:
                    audio = message.get("voice") or message.get("audio") or {}
                    file_id = audio.get("file_id")
                    if not file_id:
                        return
                    rate_limit_error = self._consume_media_rate_limit(chat_id=chat_id, user_id=user_id)
                    if rate_limit_error:
                        await self.send_message(chat_id, rate_limit_error)
                        return
                    size_error = self._validate_declared_size(
                        label="Audio",
                        declared_size=audio.get("file_size"),
                        max_bytes=self._max_audio_bytes,
                    )
                    if size_error:
                        await self.send_message(chat_id, size_error)
                        return
                    if self._assistant_mode_by_chat.get(chat_id, False):
                        empleado = await self._assistant_get_empleado(user_id)
                        if not empleado:
                            await self.send_message(
                                chat_id,
                                "⚠️ Tu Telegram no esta vinculado a un usuario interno activo.",
                            )
                            return
                        file_bytes, telegram_file_path = await self.download_file(
                            file_id,
                            max_bytes=self._max_audio_bytes,
                        )
                        await self.send_message(chat_id, "🤖 Asistente: procesando audio...")
                        answer = await self._assistant_send_media(
                            chat_id=chat_id,
                            empleado=empleado,
                            kind="voice",
                            file_bytes=file_bytes,
                            filename=Path(telegram_file_path).name or "voice.ogg",
                            content_type="audio/ogg",
                            note=None,
                        )
                        await self.send_message(chat_id, answer)
                        return
                    await self.send_message(chat_id, "🎤 Transcribiendo audio...\n⏳ 3-10 segundos")
                    file_bytes, telegram_file_path = await self.download_file(
                        file_id,
                        max_bytes=self._max_audio_bytes,
                    )
                    transcript = await self._transcribe_audio(
                        file_bytes,
                        filename=Path(telegram_file_path).name or "voice.ogg",
                    )
                    if not transcript:
                        await self.send_message(
                            chat_id,
                            "⚠️ No pude transcribir el audio.\n"
                            "Intenta de nuevo o escribe el mensaje en texto.",
                        )
                        return

                    from .tournament_bot import Message
                    msg = Message(text=transcript, chat_id=chat_id, user_id=user_id)
                    response = await self.bot.process_message(msg)
                    await self.send_message(chat_id, f"📝 Te entendí: {transcript}")
                    await self.send_message(chat_id, response)

                # Handle text messages
                elif 'text' in message:
                    text = message['text']
                    logger.info(f"📨 Text from chat {chat_id}: {text[:120]}")

                    cmd = text.strip().lower()
                    command_surface = classify_telegram_command_surface(text)
                    if command_surface is not None and command_surface.status in {"blocked", "unknown", "ambiguous"}:
                        await self.send_message(
                            chat_id,
                            command_surface.user_message or (
                                "No pude interpretar ese comando. Usa /menu para ver la superficie oficial."
                            ),
                        )
                        return
                    if cmd == "/nuevo_gasto":
                        answer = await self._nuevo_gasto_start(chat_id, user_id)
                        await self.send_message(chat_id, answer)
                        return

                    if cmd == "/restart":
                        if chat_id in self._nuevo_gasto_by_chat:
                            self._nuevo_gasto_cancel(chat_id)
                            answer = await self._nuevo_gasto_start(chat_id, user_id)
                            await self.send_message(chat_id, answer)
                        else:
                            await self.send_message(chat_id, "No hay un flujo activo. Usa /nuevo_gasto para iniciar.")
                        return

                    if cmd == "/cancel" and chat_id in self._nuevo_gasto_by_chat:
                        self._nuevo_gasto_cancel(chat_id)
                        await self.send_message(chat_id, "Flujo de gasto cancelado.")
                        return

                    if chat_id in self._nuevo_gasto_by_chat and not cmd.startswith("/"):
                        answer = await self._nuevo_gasto_handle_reply(chat_id, user_id, text)
                        if answer:
                            await self.send_message(chat_id, answer)
                            return
                    if int(user_id) in self._gastos_reject_pending and not text.strip().startswith(
                        "/"
                    ):
                        await self._gastos_complete_pending_reject(chat_id, user_id, text)
                        return
                    if (
                        self._assistant_mode_by_chat.get(chat_id, False)
                        and not cmd.startswith("/")
                        and not any(key[0] == int(chat_id) for key in self._assistant_pending_run_by_chat)
                    ):
                        pending_ticket_answer = await self._assistant_handle_pending_ticket(
                            chat_id=chat_id,
                            user_id=user_id,
                            text=text,
                        )
                        if pending_ticket_answer is not None:
                            await self.send_message(chat_id, pending_ticket_answer)
                            return
                        pending_switch_answer = await self._assistant_handle_pending_domain_switch(
                            chat_id=chat_id,
                            user_id=user_id,
                            text=text,
                        )
                        if pending_switch_answer is not None:
                            await self.send_message(chat_id, pending_switch_answer)
                            return
                    if cmd in ("/start", "/help", "/menu", "/ayuda"):
                        await self.send_message(chat_id, self._welcome_menu(chat_id))
                        return
                    base_cmd, rest_cmd = gastos_tg.telegram_command_base(text)
                    if base_cmd == "/pendientes":
                        await self._gastos_send_pendientes(chat_id, user_id)
                        return
                    if base_cmd == "/mis_solicitudes":
                        await self._gastos_send_mis_solicitudes(chat_id, user_id)
                        return
                    if base_cmd == "/solicitud":
                        if not rest_cmd:
                            await self.send_message(chat_id, "Uso: `/solicitud REFERENCIA`")
                            return
                        await self._gastos_send_solicitud_ref(chat_id, user_id, rest_cmd)
                        return
                    if cmd in ("/modo", "/db", "/db actual"):
                        await self.send_message(
                            chat_id,
                            (
                                "🧭 Contexto actual del chat\n\n"
                                f"{self._assistant_describe_context(chat_id)}\n\n"
                                "Usa `/modo empresa`, `/modo finanzas` o `/modo operaciones`.\n"
                                "Alias: `/db cambiar finanzas|operaciones|empresa`."
                            ),
                        )
                        return
                    if cmd.startswith("/modo "):
                        answer = self._set_assistant_domain(chat_id, cmd.split(" ", 1)[1])
                        await self.send_message(chat_id, answer)
                        return
                    if cmd.startswith("/db cambiar "):
                        answer = self._set_assistant_domain(chat_id, cmd.split(" ", 2)[2])
                        await self.send_message(chat_id, answer)
                        return
                    if cmd.startswith("/mode"):
                        parts = cmd.split()
                        if len(parts) == 1:
                            await self.send_message(
                                chat_id,
                                (
                                    "Modo actual: "
                                    f"*{self._assistant_mode(chat_id).upper()}*\n"
                                    "Usa `/mode ahorro`, `/mode balanceado` o `/mode calidad`."
                                ),
                            )
                            return
                        wanted = (parts[1] or "").strip().lower()
                        if wanted not in {"ahorro", "balanceado", "calidad"}:
                            await self.send_message(
                                chat_id,
                                "Modo inválido. Usa: `/mode ahorro`, `/mode balanceado` o `/mode calidad`.",
                            )
                            return
                        self._assistant_quality_mode_by_chat[chat_id] = wanted
                        await self.send_message(
                            chat_id,
                            f"✅ Modo del asistente actualizado a *{wanted.upper()}*.",
                        )
                        return
                    if cmd in ("/assistant", "/assistant on"):
                        self._assistant_mode_by_chat[chat_id] = True
                        await self.send_message(
                            chat_id,
                            "🤖 Modo asistente activado.\n"
                            "Ya puedes hacer consultas y ejecutar flujos agénticos.\n"
                            f"Modo actual: {self._assistant_mode(chat_id).upper()}.\n"
                            f"Contexto de datos: {self._assistant_domain_label(chat_id)}.\n"
                            "Usa /ok o /cancel para confirmar acciones write.\n\n"
                            "Tip: usa /modo finanzas o /modo operaciones para fijar el dominio del chat.\n"
                            "Escribe /menu para ver funciones.",
                        )
                        return
                    if cmd in ("/status", "/estado"):
                        status_text = await self.bot.get_status()
                        formatted = self.bot.format_status(status_text)
                        await self.send_message(
                            chat_id,
                            f"{formatted}\n\n🧭 Contexto del chat\n{self._assistant_describe_context(chat_id)}",
                        )
                        return
                    if cmd == "/tgid":
                        await self.send_message(
                            chat_id,
                            (
                                f"user_id={user_id}\n"
                                f"chat_id={chat_id}\n"
                                f"{self._assistant_describe_context(chat_id)}"
                            ),
                        )
                        return
                    if cmd == "/assistant off":
                        self._assistant_mode_by_chat[chat_id] = False
                        self._assistant_pending_domain_switch_by_chat.pop(chat_id, None)
                        pending_keys = [key for key in self._assistant_pending_run_by_chat if key[0] == int(chat_id)]
                        for key in pending_keys:
                            self._assistant_pending_run_by_chat.pop(key, None)
                        direct_keys = [key for key in self._assistant_pending_direct_action_by_chat if key[0] == int(chat_id)]
                        for key in direct_keys:
                            self._assistant_pending_direct_action_by_chat.pop(key, None)
                        ticket_keys = [key for key in self._assistant_pending_ticket_by_chat if key[0] == int(chat_id)]
                        for key in ticket_keys:
                            self._assistant_pending_ticket_by_chat.pop(key, None)
                        export_keys = [key for key in self._assistant_last_export_by_chat if key[0] == int(chat_id)]
                        for key in export_keys:
                            self._assistant_last_export_by_chat.pop(key, None)
                        await self.send_message(chat_id, "✅ Modo asistente desactivado.")
                        return
                    if cmd == "/ok":
                        empleado = await self._assistant_get_empleado(user_id)
                        if not empleado:
                            await self.send_message(chat_id, "⚠️ Tu Telegram no esta vinculado a un usuario interno activo.")
                            return
                        direct_answer = await self._assistant_confirm_direct_pending(
                            chat_id=chat_id,
                            empleado=empleado,
                            approve=True,
                        )
                        if direct_answer:
                            await self.send_message(chat_id, direct_answer)
                            return
                        answer = await self._assistant_confirm_pending(
                            chat_id=chat_id,
                            empleado=empleado,
                            approve=True,
                        )
                        await self.send_message(chat_id, answer)
                        return
                    if cmd == "/cancel":
                        if self._gastos_reject_pending.pop(int(user_id), None):
                            await self.send_message(chat_id, "Listo: cancelé el rechazo pendiente.")
                            return
                        empleado = await self._assistant_get_empleado(user_id)
                        if not empleado:
                            await self.send_message(chat_id, "⚠️ Tu Telegram no esta vinculado a un usuario interno activo.")
                            return
                        direct_answer = await self._assistant_confirm_direct_pending(
                            chat_id=chat_id,
                            empleado=empleado,
                            approve=False,
                        )
                        if direct_answer:
                            await self.send_message(chat_id, direct_answer)
                            return
                        ticket_keys = [key for key in self._assistant_pending_ticket_by_chat if key[0] == int(chat_id)]
                        if ticket_keys:
                            for key in ticket_keys:
                                self._assistant_pending_ticket_by_chat.pop(key, None)
                            await self.send_message(chat_id, "Acción cancelada.")
                            return
                        answer = await self._assistant_confirm_pending(
                            chat_id=chat_id,
                            empleado=empleado,
                            approve=False,
                        )
                        await self.send_message(chat_id, answer)
                        return
                    if cmd in ("/img", "/upload", "/foto"):
                        self._awaiting_image_upload.add(chat_id)
                        await self.send_message(
                            chat_id,
                            "\n".join(
                                [
                                    "📎 Modo subir imagen activado.",
                                    "Ahora envíame 1 foto (o archivo imagen).",
                                    "Tip: también puedes enviar la foto con caption `/img ...` para subirla en un solo paso.",
                                ]
                            ),
                        )
                        return
                    if cmd == "/lastimg":
                        last = self._last_upload_by_chat.get(chat_id)
                        if not last:
                            await self.send_message(chat_id, "No tengo ninguna imagen guardada en este chat todavía. Usa /img y envía una foto.")
                            return
                        await self.send_message(
                            chat_id,
                            "\n".join(
                                [
                                    "🧾 Ultima imagen guardada:",
                                    f"• image_id: `{last.image_id}`",
                                    f"• path: `{last.image_path}`",
                                ]
                            ),
                        )
                        return

                    if self._assistant_mode_by_chat.get(chat_id, False):
                        empleado = await self._assistant_get_empleado(user_id)
                        if not empleado:
                            await self.send_message(
                                chat_id,
                                "⚠️ Tu Telegram no esta vinculado a un usuario interno activo. "
                                "Pide a un admin configurar tu telegram_user_id.",
                            )
                            return
                        current_domain = self._assistant_domain(chat_id)
                        detected_domain = self._assistant_detect_domain_intent(text)
                        if (
                            current_domain in {"finanzas", "operaciones"}
                            and detected_domain
                            and detected_domain != current_domain
                        ):
                            self._assistant_pending_domain_switch_by_chat[chat_id] = {
                                "target": detected_domain,
                                "original_text": text,
                            }
                            await self.send_message(
                                chat_id,
                                self._assistant_pending_switch_prompt(
                                    chat_id=chat_id,
                                    target=detected_domain,
                                ),
                            )
                            return
                        export_format = self._assistant_export_intent(text)
                        if export_format:
                            answer = await self._assistant_export_last_report(
                                chat_id=chat_id,
                                empleado=empleado,
                                export_format=export_format,
                            )
                            await self.send_message(chat_id, answer)
                            return
                        answer = await self._assistant_send_text(
                            chat_id=chat_id,
                            empleado=empleado,
                            text=text,
                        )
                        await self.send_message(chat_id, answer)
                    else:
                        from .tournament_bot import Message
                        msg = Message(
                            text=text,
                            chat_id=chat_id,
                            user_id=user_id
                        )

                        response = await self.bot.process_message(msg)
                        await self.send_message(chat_id, response)

        except UserFacingInputError as e:
            logger.warning("Telegram input rejected: %s", e)
            try:
                if "chat_id" in locals():
                    await self.send_message(chat_id, str(e))
            except Exception:
                pass
        except Exception as e:
            logger.error(f"❌ Error handling update: {e}", exc_info=True)
            try:
                if "chat_id" in locals():
                    await self.send_message(
                        chat_id,
                        "❌ No pude procesar ese mensaje.\nIntenta de nuevo en unos segundos.",
                    )
            except Exception:
                pass

    async def poll_updates(self):
        """Poll for Telegram updates"""
        logger.info("🚀 Telegram bot started!")
        logger.info("📸 Waiting for messages...")
        await self._ensure_bot_menu_commands()

        while True:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        f"{self.api_base}/getUpdates",
                        params={
                            "offset": self.last_update_id + 1,
                            "timeout": 30
                        }
                    ) as resp:
                        data = await resp.json()

                        if data.get('ok') and data.get('result'):
                            for update in data['result']:
                                self.last_update_id = update['update_id']
                                await self.handle_update(update)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"❌ Polling error: {e}")
                await asyncio.sleep(5)

    async def run(self):
        """Run Telegram bot"""
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{self.api_base}/getMe") as resp:
                data = await resp.json()
                bot_username = data['result']['username']
                logger.info(f"✅ Telegram Bot: @{bot_username}")

        await self.poll_updates()
