"""Hash-bound server-side delegation; no identity is accepted from tool input."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from uuid import UUID

from .contracts import Principal, ReadError

MAX_DELEGATION_BYTES = 65536
DOCUMENT_LIMIT = 1000
VERSION = "samchat.readonly.delegation.v1"
RECORD_FIELDS = frozenset(
    {
        "transport_binding",
        "actor_binding",
        "actor_uuid",
        "installation",
        "generation",
        "expires_at",
        "tournament_ids",
        "document_ids",
        "budget_version_ids",
        "owner_version",
    }
)


def _uuid_set(value: Any, *, minimum: int, maximum: int) -> frozenset[str]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise ReadError("SCOPE_UNPROVEN")
    result: set[str] = set()
    for item in value:
        try:
            normalized = str(UUID(str(item)))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ReadError("SCOPE_UNPROVEN") from exc
        if normalized != item:
            raise ReadError("SCOPE_UNPROVEN")
        result.add(normalized)
    if len(result) != len(value):
        raise ReadError("SCOPE_UNPROVEN")
    return frozenset(result)


@dataclass(frozen=True)
class Delegation:
    """One explicit, expiring, least-privilege actor delegation."""

    transport_binding: str
    actor_binding: str
    actor_uuid: str
    installation: str
    generation: str
    expires_at: datetime
    tournament_ids: frozenset[str]
    document_ids: frozenset[str]
    budget_version_ids: frozenset[str]
    owner_version: str

    @classmethod
    def parse(cls, value: Mapping[str, Any]) -> "Delegation":
        if not isinstance(value, Mapping) or set(value) != RECORD_FIELDS:
            raise ReadError("SCOPE_UNPROVEN")
        strings = (
            "transport_binding",
            "actor_binding",
            "actor_uuid",
            "installation",
            "generation",
            "expires_at",
            "owner_version",
        )
        if any(not isinstance(value[key], str) or not value[key] for key in strings):
            raise ReadError("SCOPE_UNPROVEN")
        try:
            actor_uuid = str(UUID(value["actor_uuid"]))
            expires = datetime.fromisoformat(value["expires_at"].replace("Z", "+00:00"))
        except (ValueError, TypeError) as exc:
            raise ReadError("SCOPE_UNPROVEN") from exc
        if actor_uuid != value["actor_uuid"] or expires.tzinfo is None:
            raise ReadError("SCOPE_UNPROVEN")
        return cls(
            transport_binding=value["transport_binding"],
            actor_binding=value["actor_binding"],
            actor_uuid=actor_uuid,
            installation=value["installation"],
            generation=value["generation"],
            expires_at=expires.astimezone(timezone.utc),
            tournament_ids=_uuid_set(value["tournament_ids"], minimum=1, maximum=100),
            document_ids=_uuid_set(
                value["document_ids"], minimum=1, maximum=DOCUMENT_LIMIT
            ),
            budget_version_ids=_uuid_set(
                value["budget_version_ids"], minimum=0, maximum=100
            ),
            owner_version=value["owner_version"],
        )

    def principal(self, now: datetime) -> Principal:
        if self.expires_at <= now.astimezone(timezone.utc):
            raise ReadError("AUTH_REQUIRED")
        return Principal(
            actor_binding=self.actor_binding,
            installation=self.installation,
            tournaments=self.tournament_ids,
            expires_at=self.expires_at,
            generation=self.generation,
        )


@dataclass(frozen=True)
class DelegationFileBinding:
    path: Path
    sha256: str
    owner_uid: int
    owner_gid: int
    mode: int = 0o640

    def check(self) -> None:
        if (
            not self.path.is_absolute()
            or not self.sha256
            or len(self.sha256) != 64
            or self.owner_uid < 0
            or self.owner_gid < 0
            or self.mode & 0o137
        ):
            raise ReadError("ACTIVATION_REQUIRED")


class DelegationFile:
    """Reload an exact owner-managed file so expiry/revocation takes effect."""

    def __init__(self, binding: DelegationFileBinding) -> None:
        binding.check()
        self.binding = binding

    def _read(self) -> bytes:
        flags = os.O_RDONLY | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(self.binding.path, flags)
        except OSError as exc:
            raise ReadError("AUTH_REQUIRED") from exc
        try:
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_uid != self.binding.owner_uid
                or info.st_gid != self.binding.owner_gid
                or stat.S_IMODE(info.st_mode) != self.binding.mode
                or info.st_size > MAX_DELEGATION_BYTES
            ):
                raise ReadError("AUTH_REQUIRED")
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                data = stream.read(MAX_DELEGATION_BYTES + 1)
        finally:
            os.close(descriptor)
        if (
            len(data) > MAX_DELEGATION_BYTES
            or hashlib.sha256(data).hexdigest() != self.binding.sha256
        ):
            raise ReadError("AUTH_REQUIRED")
        return data

    def records(self) -> dict[str, Delegation]:
        try:
            value = json.loads(self._read())
        except (UnicodeError, ValueError, TypeError) as exc:
            raise ReadError("AUTH_REQUIRED") from exc
        if not isinstance(value, dict) or set(value) != {"version", "delegations"}:
            raise ReadError("AUTH_REQUIRED")
        if value["version"] != VERSION or not isinstance(value["delegations"], list):
            raise ReadError("AUTH_REQUIRED")
        records: dict[str, Delegation] = {}
        for raw in value["delegations"]:
            record = Delegation.parse(raw)
            if record.transport_binding in records:
                raise ReadError("AUTH_REQUIRED")
            records[record.transport_binding] = record
        return records

    def resolve(self, transport_binding: str) -> Delegation:
        record = self.records().get(transport_binding)
        if record is None:
            raise ReadError("AUTH_REQUIRED")
        return record


class BoundPrincipalResolver:
    """Bridge-owned binding adapter. The MCP arguments never reach this object."""

    def __init__(
        self,
        source: DelegationFile,
        transport_binding: str,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        if not transport_binding:
            raise ReadError("ACTIVATION_REQUIRED")
        self.source = source
        self.transport_binding = transport_binding
        self.clock = clock

    def delegation(self) -> Delegation:
        return self.source.resolve(self.transport_binding)

    def __call__(self) -> Principal:
        return self.delegation().principal(self.clock())
