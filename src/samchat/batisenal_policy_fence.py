"""Optional owner-provisioned PostgreSQL policy fence, not authorization.

Legacy callers may operate with no migration/guard row. New v0.1 consumers must
pass required=True and keep this transaction through their final authority
check. They must NOT call identity/audit helpers that open another connection.
No DDL, provisioning, grants, commits, retries or isolation changes occur here.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import cast

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession


class PolicyFenceUnavailable(RuntimeError):
    pass


class PolicyFenceBusy(PolicyFenceUnavailable):
    """Retry the unchanged operation after rollback; no mutation committed."""


@dataclass(frozen=True)
class PolicyFence:
    epoch: int
    observed_at: datetime


def acquire_policy_fence(
    connection: Connection, *, required: bool = False, nowait: bool = False
) -> PolicyFence | None:
    """Hold the row until caller ends its transaction; never grant authority.

    Synchronous callers on an event loop must use nowait=True. NOWAIT covers
    contention on this row, not DDL relation locks or other legacy SQL owners.
    """
    if connection.dialect.name != "postgresql":
        if required:
            raise PolicyFenceUnavailable("V01_POSTGRESQL_REQUIRED")
        return None
    # Never swallow permission/catalog errors as an absent optional migration.
    exists = connection.execute(
        text("SELECT to_regclass('support_batisenal_v01_policy_guard')")
    ).scalar_one()
    if exists is None:
        if required:
            raise PolicyFenceUnavailable("V01_POLICY_FENCE_UNAVAILABLE")
        return None
    statement = (
        "SELECT epoch FROM support_batisenal_v01_policy_guard "
        "WHERE singleton = true FOR UPDATE NOWAIT"
        if nowait
        else (
            "SELECT epoch FROM support_batisenal_v01_policy_guard "
            "WHERE singleton = true FOR UPDATE"
        )
    )
    try:
        epoch = connection.execute(text(statement)).scalar_one_or_none()
    except DBAPIError as error:
        if nowait and getattr(error.orig, "sqlstate", None) == "55P03":
            raise PolicyFenceBusy("V01_POLICY_BUSY") from None
        raise
    if epoch is None:
        if required:
            raise PolicyFenceUnavailable("V01_POLICY_FENCE_UNAVAILABLE")
        return None
    # Preserve optional legacy callers when the migration or row is absent.
    # Never return a fence whose row lock was released by AUTOCOMMIT.
    if getattr(connection.connection.dbapi_connection, "autocommit", None) is not False:
        raise PolicyFenceUnavailable("V01_TRANSACTION_REQUIRED")
    # READ COMMITTED is required for fresh policy reads after any lock wait.
    if (
        connection.execute(text("SHOW transaction_isolation")).scalar_one()
        != "read committed"
    ):
        raise PolicyFenceUnavailable("V01_READ_COMMITTED_REQUIRED")
    observed = connection.execute(text("SELECT clock_timestamp()")).scalar_one()
    return PolicyFence(epoch, observed)


async def acquire_policy_fence_async(
    session: AsyncSession, *, required: bool = False
) -> PolicyFence | None:
    connection = await session.connection()
    return cast(
        PolicyFence | None,
        await connection.run_sync(
            lambda sync: acquire_policy_fence(sync, required=required)
        ),
    )
