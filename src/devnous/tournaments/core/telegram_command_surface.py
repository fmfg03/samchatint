"""Canonical command surface contract for the Telegram tournament bot."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


CANONICAL_COMMANDS = (
    ("/menu", ("/start", "/help", "/menu", "/ayuda")),
    ("/pendientes", ("/pendientes",)),
    ("/mis_solicitudes", ("/mis_solicitudes",)),
    ("/solicitud", ("/solicitud",)),
    ("/modo", ("/modo", "/db", "/db actual")),
    ("/mode", ("/mode",)),
    ("/assistant on", ("/assistant", "/assistant on")),
    ("/assistant off", ("/assistant off",)),
    ("/status", ("/status", "/estado")),
    ("/tgid", ("/tgid",)),
    ("/ok", ("/ok",)),
    ("/cancel", ("/cancel",)),
    ("/img", ("/img", "/upload", "/foto")),
    ("/lastimg", ("/lastimg",)),
    ("/nuevo_gasto", ("/nuevo_gasto",)),
    ("/restart", ("/restart",)),
)

BLOCKED_RAW_COMMANDS = frozenset(
    {
        "/gateway",
        "/cron",
        "/plugins",
        "/reload",
        "/reload-mcp",
        "/toolsets",
        "/platforms",
        "/config",
        "/debug",
        "/profile",
        "/branch",
        "/rollback",
        "/codex-runtime",
        "/yolo",
    }
)

KNOWN_COMMAND_PREFIXES = frozenset(alias for _, aliases in CANONICAL_COMMANDS for alias in aliases)
KNOWN_BASE_COMMANDS = frozenset(
    {
        "/menu",
        "/start",
        "/help",
        "/ayuda",
        "/pendientes",
        "/mis_solicitudes",
        "/solicitud",
        "/modo",
        "/db",
        "/mode",
        "/assistant",
        "/status",
        "/estado",
        "/tgid",
        "/ok",
        "/cancel",
        "/img",
        "/upload",
        "/foto",
        "/lastimg",
        "/nuevo_gasto",
        "/restart",
    }
)
ALLOWED_DOMAINS = frozenset({"empresa", "finanzas", "operaciones"})
ALLOWED_QUALITY_MODES = frozenset({"ahorro", "balanceado", "calidad"})


@dataclass(frozen=True)
class TelegramCommandSurfaceMatch:
    status: str
    canonical_command: Optional[str]
    received_command: str
    base_command: str
    is_alias: bool = False
    detail: Optional[str] = None
    user_message: Optional[str] = None


def command_surface_catalog() -> tuple[dict[str, object], ...]:
    return tuple(
        {
            "canonical_command": canonical,
            "aliases": aliases,
        }
        for canonical, aliases in CANONICAL_COMMANDS
    )


def classify_telegram_command_surface(text: str) -> Optional[TelegramCommandSurfaceMatch]:
    normalized = (text or "").strip()
    if not normalized.startswith("/"):
        return None

    pieces = normalized.split(maxsplit=1)
    base_command = pieces[0].lower()
    remainder = pieces[1].strip() if len(pieces) > 1 else ""
    normalized_lower = normalized.lower()

    if base_command in BLOCKED_RAW_COMMANDS:
        return TelegramCommandSurfaceMatch(
            status="blocked",
            canonical_command=None,
            received_command=normalized,
            base_command=base_command,
            detail="raw_command_blocked",
            user_message=(
                "Ese comando raw no está expuesto en este bot de Telegram. "
                "Usa /menu para ver la superficie oficial."
            ),
        )

    if base_command == "/solicitud":
        if not remainder:
            return TelegramCommandSurfaceMatch(
                status="ambiguous",
                canonical_command="/solicitud",
                received_command=normalized,
                base_command=base_command,
                detail="missing_reference",
                user_message="Uso: `/solicitud REFERENCIA`",
            )
        return TelegramCommandSurfaceMatch(
            status="valid",
            canonical_command="/solicitud",
            received_command=normalized,
            base_command=base_command,
            detail="document_reference_lookup",
        )

    if normalized_lower in KNOWN_COMMAND_PREFIXES:
        return _exact_match(normalized_lower)

    if base_command == "/modo":
        if not remainder:
            return TelegramCommandSurfaceMatch(
                status="valid",
                canonical_command="/modo",
                received_command=normalized,
                base_command=base_command,
                detail="mode_context_status",
            )
        if remainder.lower() in ALLOWED_DOMAINS:
            return TelegramCommandSurfaceMatch(
                status="valid",
                canonical_command="/modo",
                received_command=normalized,
                base_command=base_command,
                detail="mode_domain_change",
            )
        return TelegramCommandSurfaceMatch(
            status="ambiguous",
            canonical_command="/modo",
            received_command=normalized,
            base_command=base_command,
            detail="invalid_domain",
            user_message="Usa `/modo empresa`, `/modo finanzas` o `/modo operaciones`.",
        )

    if base_command == "/db":
        if not remainder or remainder.lower() == "actual":
            return TelegramCommandSurfaceMatch(
                status="valid",
                canonical_command="/modo",
                received_command=normalized,
                base_command=base_command,
                is_alias=True,
                detail="mode_context_status_alias",
            )
        if remainder.lower().startswith("cambiar "):
            target = remainder[8:].strip().lower()
            if target in ALLOWED_DOMAINS:
                return TelegramCommandSurfaceMatch(
                    status="valid",
                    canonical_command="/modo",
                    received_command=normalized,
                    base_command=base_command,
                    is_alias=True,
                    detail="mode_domain_change_alias",
                )
            return TelegramCommandSurfaceMatch(
                status="ambiguous",
                canonical_command="/modo",
                received_command=normalized,
                base_command=base_command,
                is_alias=True,
                detail="invalid_domain_alias",
                user_message="Alias válido: `/db cambiar finanzas|operaciones|empresa`.",
            )
        return _unknown_match(normalized, base_command)

    if base_command == "/mode":
        if not remainder:
            return TelegramCommandSurfaceMatch(
                status="valid",
                canonical_command="/mode",
                received_command=normalized,
                base_command=base_command,
                detail="quality_mode_status",
            )
        if remainder.lower() in ALLOWED_QUALITY_MODES:
            return TelegramCommandSurfaceMatch(
                status="valid",
                canonical_command="/mode",
                received_command=normalized,
                base_command=base_command,
                detail="quality_mode_change",
            )
        return TelegramCommandSurfaceMatch(
            status="ambiguous",
            canonical_command="/mode",
            received_command=normalized,
            base_command=base_command,
            detail="invalid_quality_mode",
            user_message="Modo inválido. Usa: `/mode ahorro`, `/mode balanceado` o `/mode calidad`.",
        )

    if base_command == "/assistant":
        if not remainder or remainder.lower() == "on":
            return TelegramCommandSurfaceMatch(
                status="valid",
                canonical_command="/assistant on",
                received_command=normalized,
                base_command=base_command,
                is_alias=not remainder,
                detail="assistant_enable",
            )
        if remainder.lower() == "off":
            return TelegramCommandSurfaceMatch(
                status="valid",
                canonical_command="/assistant off",
                received_command=normalized,
                base_command=base_command,
                detail="assistant_disable",
            )
        return TelegramCommandSurfaceMatch(
            status="ambiguous",
            canonical_command="/assistant on",
            received_command=normalized,
            base_command=base_command,
            detail="invalid_assistant_subcommand",
            user_message="Usa `/assistant`, `/assistant on` o `/assistant off`.",
        )

    if base_command in KNOWN_BASE_COMMANDS:
        return TelegramCommandSurfaceMatch(
            status="ambiguous",
            canonical_command=base_command,
            received_command=normalized,
            base_command=base_command,
            detail="unexpected_arguments",
            user_message=(
                "Ese comando no admite ese formato en la superficie oficial. "
                "Usa /menu para ver los comandos válidos."
            ),
        )

    return _unknown_match(normalized, base_command)


def _exact_match(normalized_lower: str) -> TelegramCommandSurfaceMatch:
    for canonical, aliases in CANONICAL_COMMANDS:
        if normalized_lower in aliases:
            return TelegramCommandSurfaceMatch(
                status="valid",
                canonical_command=canonical,
                received_command=normalized_lower,
                base_command=normalized_lower.split(maxsplit=1)[0],
                is_alias=normalized_lower != canonical,
                detail="exact_alias_match" if normalized_lower != canonical else "exact_canonical_match",
            )
    return _unknown_match(normalized_lower, normalized_lower.split(maxsplit=1)[0])


def _unknown_match(normalized: str, base_command: str) -> TelegramCommandSurfaceMatch:
    return TelegramCommandSurfaceMatch(
        status="unknown",
        canonical_command=None,
        received_command=normalized,
        base_command=base_command,
        detail="unknown_command",
        user_message=(
            "No reconozco ese comando en la superficie oficial del bot. "
            "Usa /menu para ver comandos, aliases y flujos soportados."
        ),
    )
