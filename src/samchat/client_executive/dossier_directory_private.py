"""Optional user-bound Copa Telmex directory reads through upstream RLS."""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from uuid import UUID

import httpx

from samchat.client_executive.dossier_registry import (
    DIRECTORY_KEY_ENV,
    DIRECTORY_ORIGIN,
    _plain,
    _public_key_kind,
)

PRIVATE_DIRECTORY_SOURCE = "Copa Telmex · Directorio personal autorizado"


@dataclass(frozen=True)
class RepresentativeSourceIdentity:
    """Provided only by a trusted server-side identity binding for this employee."""

    employee_id: UUID
    source_subject: UUID
    access_token: str = field(repr=False)


def _user_access_token(token: str, subject: UUID) -> bool:
    try:
        if len(token) > 8192:
            return False
        _, claims, signature = token.split(".")
        payload = json.loads(
            base64.urlsafe_b64decode(claims + "=" * (-len(claims) % 4))
        )
        return (
            bool(signature)
            and payload.get("role") == "authenticated"
            and UUID(str(payload.get("sub"))) == subject
        )
    except (AttributeError, TypeError, UnicodeError, ValueError):
        return False


def _birthday(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) != 5 or value[2] != "-":
        return None
    try:
        month, day = (int(part) for part in value.split("-"))
        date(2000, month, day)
    except ValueError:
        return None
    return f"{day:02d}/{month:02d}"


async def read_directory_private(
    directory: dict,
    identity: RepresentativeSourceIdentity,
    *,
    employee_id: UUID,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict:
    """Check the bearer via GoTrue and query only scoped representatives under RLS."""
    result: dict[str, Any] = {
        "source": PRIVATE_DIRECTORY_SOURCE,
        "status": "denied",
        "rows": [],
    }
    if identity.employee_id != employee_id or not _user_access_token(
        identity.access_token, identity.source_subject
    ):
        return result
    key = os.environ.get(DIRECTORY_KEY_ENV, "").strip()
    if not _public_key_kind(key):
        return {**result, "status": "not_configured"}
    try:
        contacts = {
            UUID(row["representative_id"]): row
            for row in directory.get("rows", [])
            if row.get("representative_id")
        }
    except (AttributeError, TypeError, ValueError):
        return {**result, "status": "unavailable"}
    if not contacts:
        return {**result, "status": "not_configured"}
    headers = {"apikey": key, "Authorization": f"Bearer {identity.access_token}"}
    try:
        async with httpx.AsyncClient(
            base_url=DIRECTORY_ORIGIN,
            headers=headers,
            transport=transport,
            timeout=5,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            auth = await client.get("/auth/v1/user")
            if auth.status_code != 200 or auth.json().get("id") != str(
                identity.source_subject
            ):
                return result
            response = await client.get(
                "/rest/v1/representative_personal_details",
                params={
                    "select": "representative_id,spouse_name,representative_birthday,spouse_birthday",
                    "representative_id": "in.(" + ",".join(map(str, contacts)) + ")",
                    "limit": len(contacts) + 1,
                },
            )
            if response.status_code in (401, 403):
                return result
            response.raise_for_status()
            rows = response.json()
        if not isinstance(rows, list) or len(rows) > len(contacts):
            return {**result, "status": "unavailable"}
        for row in rows:
            current = contacts.get(UUID(row["representative_id"]))
            if current is None:
                return {**result, "status": "unavailable"}
            result["rows"].append(
                {
                    "label": current.get("representative_name")
                    or "Representante estatal",
                    "entity": current["entity"],
                    "zone": current.get("zone"),
                    "representative_birthday": _birthday(
                        row.get("representative_birthday")
                    ),
                    "spouse_name": _plain(row.get("spouse_name")),
                    "spouse_birthday": _birthday(row.get("spouse_birthday")),
                    "source": PRIVATE_DIRECTORY_SOURCE,
                    "status": "available",
                }
            )
    except (AttributeError, KeyError, TypeError, ValueError, httpx.HTTPError):
        return {**result, "status": "unavailable", "rows": []}
    return {**result, "status": "available" if result["rows"] else "denied"}
