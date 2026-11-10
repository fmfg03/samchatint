"""Explicitly enabled synthetic engine composition, never global instrumentation.

The hook precedes the first SQL in each outer transaction on these exact engines.
Markers are assertions for cooperating application writers, not protection from a
DB owner who can forge records/disable triggers. No roles or grants are created.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import event, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import DBAPIError

from samchat.batisenal_policy_fence import PolicyFenceBusy, PolicyFenceUnavailable


@dataclass(frozen=True)
class SyntheticProtocol:
    schema: str
    case_id: str
    enabled: bool = False

    def install(self, engine: Engine, *, synchronous: bool) -> None:
        """Call once after explicit fixture migration; no DDL or provisioning."""
        if self.enabled is not True:
            raise PolicyFenceUnavailable("V01_SYNTHETIC_ACTIVATION_REQUIRED")
        if not re.fullmatch(r"bs_test_v01_[a-f0-9]{32}", self.schema):
            raise PolicyFenceUnavailable("V01_SYNTHETIC_SCHEMA_REQUIRED")
        if str(UUID(self.case_id)) != self.case_id:
            raise PolicyFenceUnavailable("V01_BINDING_UNAVAILABLE")
        if engine.dialect.name != "postgresql":
            raise PolicyFenceUnavailable("V01_POSTGRESQL_REQUIRED")

        def begin(connection: Connection) -> None:
            if (
                getattr(connection.connection.dbapi_connection, "autocommit", None)
                is not False
            ):
                raise PolicyFenceUnavailable("V01_TRANSACTION_REQUIRED")
            if (
                connection.execute(text("SELECT current_schema()")).scalar_one()
                != self.schema
            ):
                raise PolicyFenceUnavailable("V01_SYNTHETIC_SCHEMA_REQUIRED")
            try:
                connection.execute(
                    text(
                        "SELECT * FROM support_batisenal_v01_begin(CAST(:case AS uuid),"
                        " :no_wait)"
                    ),
                    {"case": self.case_id, "no_wait": synchronous},
                ).one()
            except DBAPIError as error:
                if getattr(error.orig, "sqlstate", None) == "55P03":
                    raise PolicyFenceBusy("V01_POLICY_BUSY") from None
                raise

        event.listen(engine, "begin", begin)
