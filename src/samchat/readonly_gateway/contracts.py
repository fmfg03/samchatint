"""Trusted adapter contracts; no caller-supplied identity or credentials."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol


class ReadError(ValueError):
    """Public errors contain a code only, never backend exception text."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Principal:
    """Resolved by a trusted transport adapter, never decoded from tool arguments."""

    actor_binding: str
    installation: str
    tournaments: frozenset[str]
    expires_at: datetime
    generation: str
    scope_policy: str = ""

    def valid(self, now: datetime) -> bool:
        return bool(
            self.actor_binding
            and self.installation
            and (self.tournaments or self.scope_policy == "live_informes_v1")
            and self.generation
            and self.expires_at.tzinfo is not None
            and self.expires_at > now
        )


@dataclass(frozen=True)
class Admission:
    """Explicit local synthetic admission. Production is not admitted by this type."""

    mode: str = "disabled"
    installation: str = ""
    schema_fingerprint: str = ""
    source_commit: str = ""
    policy_version: str = ""
    activation_receipt_sha256: str = ""

    def check(self) -> None:
        if self.mode not in {"synthetic", "production"}:
            raise ReadError("ACTIVATION_REQUIRED")
        if not all(
            (
                self.installation,
                self.schema_fingerprint,
                re.fullmatch(r"[0-9a-f]{40}", self.source_commit) is not None,
                self.policy_version,
            )
        ):
            raise ReadError("SCOPE_UNPROVEN")
        if (
            self.mode == "production"
            and re.fullmatch(r"[0-9a-f]{64}", self.activation_receipt_sha256) is None
        ):
            raise ReadError("ACTIVATION_REQUIRED")


class AuditSink(Protocol):
    def append(self, event: Mapping[str, Any]) -> None:
        """Append minimised technical metadata outside the business database."""


class MemoryAudit:
    """Synthetic-test audit only, not evidence of durable production audit."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def append(self, event: Mapping[str, Any]) -> None:
        self.events.append(dict(event))


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
