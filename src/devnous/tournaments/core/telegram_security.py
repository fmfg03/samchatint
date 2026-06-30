"""
Shared Telegram security helpers for tournament bots.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional, Set


def _parse_id_set(raw_value: Optional[str]) -> Set[int]:
    values: Set[int] = set()
    if not raw_value:
        return values

    normalized = raw_value.replace("\n", ",").replace(" ", ",")
    for token in normalized.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            values.add(int(token))
        except ValueError:
            continue
    return values


@dataclass(frozen=True)
class TelegramActor:
    chat_id: int
    user_id: Optional[int]
    message_id: Optional[int] = None


class TelegramAccessControl:
    """
    Access gate for Telegram bots.

    Modes:
    - allow_all: preserve legacy behavior
    - allowlist: only configured chat/user IDs are allowed
    - db: authorize active internal users from the employee directory, while
      still honoring explicit env/chat allowlists as emergency overrides
    """

    def __init__(
        self,
        access_mode: Optional[str] = None,
        allowed_chat_ids: Optional[Iterable[int]] = None,
        allowed_user_ids: Optional[Iterable[int]] = None,
    ) -> None:
        env_chat_ids = _parse_id_set(os.getenv("TELEGRAM_ALLOWED_CHAT_IDS"))
        env_user_ids = _parse_id_set(os.getenv("TELEGRAM_ALLOWED_USER_IDS"))

        self.allowed_chat_ids: Set[int] = set(allowed_chat_ids or ()) | env_chat_ids
        self.allowed_user_ids: Set[int] = set(allowed_user_ids or ()) | env_user_ids

        configured_mode = (access_mode or os.getenv("TELEGRAM_ACCESS_MODE") or "").strip().lower()
        if configured_mode in {"allow_all", "allowlist", "db"}:
            self.mode = configured_mode
        elif self.allowed_chat_ids or self.allowed_user_ids:
            self.mode = "allowlist"
        else:
            self.mode = "db"

    def is_allowed(self, actor: TelegramActor) -> bool:
        if self.mode == "allow_all":
            return True
        if actor.chat_id in self.allowed_chat_ids:
            return True
        if actor.user_id is not None and actor.user_id in self.allowed_user_ids:
            return True
        return False

    def requires_db_lookup(self) -> bool:
        return self.mode == "db"

    def describe(self) -> str:
        if self.mode == "allow_all":
            return "allow_all"
        if self.mode == "db":
            return (
                f"db(active_empleados, overrides users={len(self.allowed_user_ids)}, "
                f"chats={len(self.allowed_chat_ids)})"
            )
        return f"allowlist(users={len(self.allowed_user_ids)}, chats={len(self.allowed_chat_ids)})"


def actor_from_message(message: Dict[str, Any]) -> TelegramActor:
    chat = message.get("chat", {}) or {}
    sender = message.get("from", {}) or {}
    return TelegramActor(
        chat_id=int(chat.get("id", 0)),
        user_id=int(sender["id"]) if sender.get("id") is not None else None,
        message_id=int(message["message_id"]) if message.get("message_id") is not None else None,
    )


def actor_from_callback(callback_query: Dict[str, Any]) -> TelegramActor:
    message = callback_query.get("message", {}) or {}
    sender = callback_query.get("from", {}) or {}
    chat = message.get("chat", {}) or {}
    return TelegramActor(
        chat_id=int(chat.get("id", 0)),
        user_id=int(sender["id"]) if sender.get("id") is not None else None,
        message_id=int(message["message_id"]) if message.get("message_id") is not None else None,
    )
