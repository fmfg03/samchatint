"""Read candidate anchors in a caller-owned PostgreSQL transaction.

This is not an authority check: admission provenance, original source bytes,
current draft, human faculty, receipt signatures and the effect remain to be
verified independently. No application or writer role is granted access here.
"""

from __future__ import annotations

from typing import Any, Protocol
from uuid import UUID

from .staff_capture_edition import TrustedEditionBinding
from .staff_capture_executor import CaptureDenied, LockedAttempt
from .staff_review_challenge import parse_canonical_scope, scope_hash


class AnchorConnection(Protocol):
    def is_in_transaction(self) -> bool: ...

    async def fetchrow(self, query: str, *args: Any) -> Any: ...


async def load_locked_anchor(
    connection: AnchorConnection, attempt_id: UUID
) -> LockedAttempt:
    """Load and lock immutable anchors; never treat this as approval."""
    if not isinstance(attempt_id, UUID) or not connection.is_in_transaction():
        raise CaptureDenied("capture denied")
    row = await connection.fetchrow(
        "SELECT * FROM ctt_staff_private.lock_capture_anchor($1)", attempt_id
    )
    if row is None:
        raise CaptureDenied("capture denied")
    scope = parse_canonical_scope(row["scope_bytes"])
    if scope_hash(scope) != row["scope_hash"]:
        raise CaptureDenied("capture denied")
    return LockedAttempt(
        attempt_id=row["attempt_id"],
        challenge_id=row["challenge_id"],
        principal_id=row["principal_id"],
        credential_id=row["credential_id"],
        scope_hash=row["scope_hash"],
        scope_bytes=row["scope_bytes"],
        edition_binding=TrustedEditionBinding(
            tenant_id=row["tenant_id"],
            edition_id=row["edition_id"],
            tournament_id=row["tournament_id"],
            edition_year=row["edition_year"],
            roster_slug=row["roster_slug"],
        ),
    )
