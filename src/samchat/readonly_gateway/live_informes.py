"""Live report visibility through actor-bound PostgreSQL security-barrier views.

No document allowlist, identity payload or mutable session setting grants scope.
The database views resolve the delegated employee and explicit denials per SELECT.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable
from uuid import UUID

from .agentius import CanonicalGateway
from .contracts import Principal, ReadError, utc_now
from .delegation import DelegationFile
from .postgres import PostgresCanonicalAdapter, ScopeGrant

POLICY = "live_informes_v1"
SCHEMA = "samchat_ro_fmfg_v1"
ROLE = "samchat_ro_fmfg"
ACTOR = "b8816679-ad77-4590-83d5-50ffce335854"
REPORT_TOOLS = frozenset(
    {
        "samchat.resolve_reference",
        "samchat.document_status",
        "samchat.document_diagnose",
        "samchat.document_audit",
        "samchat.study",
    }
)


@dataclass(frozen=True)
class ReportDelegation:
    actor_uuid: str
    email: str
    installation: str
    actor_binding: str
    generation: str
    expires_at: datetime
    owner_version: str

    def principal(self, now: datetime | None = None) -> Principal:
        if (
            str(UUID(self.actor_uuid)) != ACTOR
            or self.email != "fmfg@pm.me"
            or not all(
                (
                    self.installation,
                    self.actor_binding,
                    self.generation,
                    self.owner_version,
                )
            )
            or self.expires_at.tzinfo is None
            or self.expires_at <= (now or utc_now())
        ):
            raise ReadError("AUTH_REQUIRED")
        return Principal(
            self.actor_binding,
            self.installation,
            frozenset(),
            self.expires_at,
            self.generation,
            POLICY,
        )


class LiveInformeOwner:
    """A trusted file loader revalidates hash, expiry and binding on every call."""

    def __init__(self, load: Callable[[], ReportDelegation]) -> None:
        self.load = load

    def resolve(self, principal: Principal) -> ScopeGrant:
        delegation = self.load()
        if principal != delegation.principal():
            raise ReadError("AUTH_REQUIRED")
        return ScopeGrant(
            delegation.actor_uuid,
            frozenset(),
            delegation.installation,
            delegation.generation,
            delegation.owner_version,
        )


def load_report_delegation(source: DelegationFile) -> ReportDelegation:
    """Read the hash/UID/GID-bound server file; never a request payload."""
    try:
        value = json.loads(source._read())
        if (
            set(value)
            != {
                "actor_uuid",
                "email",
                "installation",
                "actor_binding",
                "generation",
                "expires_at",
                "owner_version",
                "transport_binding",
                "scope_policy",
            }
            or value["scope_policy"] != POLICY
            or value["transport_binding"] != ("tunnel_6abc52a731608191a27c90d670fb5502")
            or any(not isinstance(v, str) for v in value.values())
        ):
            raise ReadError("AUTH_REQUIRED")
        record = ReportDelegation(
            **{
                k: v
                for k, v in value.items()
                if k not in {"transport_binding", "scope_policy", "expires_at"}
            },
            expires_at=datetime.fromisoformat(
                value["expires_at"].replace("Z", "+00:00")
            ),
        )
        record.principal()
        return record
    except ReadError:
        raise
    except (ValueError, TypeError, AttributeError) as exc:
        raise ReadError("AUTH_REQUIRED") from exc


class LiveInformeAdapter(PostgresCanonicalAdapter):
    """Run the canonical closed queries over protected report projections only."""

    @staticmethod
    def _route_authority_sql() -> str:
        """Limited report views do not admit current approval-policy context.

        Do not read additional base tables or infer authority from an old folio.
        Existing projection receipts and the scope owner remain unchanged.
        """
        return "NULL::boolean"

    def _grant(self, principal: Principal) -> ScopeGrant:
        self.binding.check()
        if principal.scope_policy != POLICY or not principal.valid(utc_now()):
            raise ReadError("AUTH_REQUIRED")
        grant = self.owner.resolve(principal)
        if (
            grant.actor_uuid != ACTOR
            or grant.document_ids
            or grant.budget_version_ids
            or grant.installation != principal.installation
            or grant.installation != self.binding.installation
            or grant.generation != principal.generation
            or grant.owner_version != self.binding.owner_version
            or self.binding.role != ROLE
        ):
            raise ReadError("SCOPE_UNPROVEN")
        return grant

    @staticmethod
    def _scope(principal: Principal, grant: ScopeGrant) -> tuple[str, dict[str, Any]]:
        del principal, grant
        return "d.tipo='INFORME'", {}

    def _query(
        self, principal: Principal, build: Callable, cap: int
    ) -> list[dict[str, Any]]:
        grant = self._grant(principal)
        sql, args = build(grant)
        connection = self.connect()
        cursor = connection.cursor()
        with self._lock:
            self._active = connection
        try:
            cursor.execute("BEGIN READ ONLY")
            cursor.execute("SET LOCAL statement_timeout='3000ms'")
            cursor.execute("SET LOCAL lock_timeout='500ms'")
            cursor.execute("SET LOCAL TIME ZONE 'UTC'")
            cursor.execute(f"SET LOCAL search_path={SCHEMA},pg_catalog")
            identity = self._rows(
                cursor,
                "SELECT current_database() AS db,current_user AS role,"
                "session_user AS session_role,current_setting('transaction_read_only') AS readonly,"
                "rolsuper,rolcreaterole,rolcreatedb,rolreplication,rolbypassrls,"
                "has_database_privilege(current_user,current_database(),'TEMP') AS temp,"
                "has_database_privilege(current_user,current_database(),'CREATE') AS db_create,"
                "EXISTS (SELECT 1 FROM pg_namespace n WHERE n.nspname NOT IN ('information_schema') "
                "AND n.nspname !~ '^pg_' AND has_schema_privilege(current_user,n.oid,'CREATE')) AS schema_create "
                "FROM pg_catalog.pg_roles WHERE rolname=current_user",
                {},
                1,
            )
            if (
                len(identity) != 1
                or identity[0]["db"] != self.binding.database
                or identity[0]["role"] != ROLE
                or identity[0]["session_role"] != ROLE
                or identity[0]["readonly"] != "on"
                or any(
                    identity[0][key]
                    for key in (
                        "rolsuper",
                        "rolcreaterole",
                        "rolcreatedb",
                        "rolreplication",
                        "rolbypassrls",
                        "temp",
                        "db_create",
                        "schema_create",
                    )
                )
            ):
                raise ReadError("ROLE_UNPROVEN")
            if self._rows(
                cursor,
                "SELECT roleid FROM pg_auth_members WHERE member="
                "(SELECT oid FROM pg_roles WHERE rolname=current_user)",
                {},
                1,
            ):
                raise ReadError("ROLE_UNPROVEN")
            if self._rows(
                cursor,
                "SELECT p.oid FROM pg_proc p JOIN pg_namespace n ON "
                "n.oid=p.pronamespace WHERE n.nspname NOT IN ('pg_catalog','information_schema') "
                "AND p.prosecdef AND has_function_privilege(current_user,p.oid,'EXECUTE') LIMIT 1",
                {},
                1,
            ):
                raise ReadError("ROLE_UNPROVEN")
            # Reject any effective base-column access, including inherited PUBLIC.
            base_access = self._rows(
                cursor,
                "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                "JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped "
                "WHERE n.nspname NOT IN (%(schema)s,'pg_catalog','information_schema') "
                "AND n.nspname !~ '^pg_' AND c.relkind IN ('r','p','v','m') AND ("
                "has_column_privilege(current_user,c.oid,a.attnum,'SELECT,INSERT,UPDATE,REFERENCES') "
                "OR has_table_privilege(current_user,c.oid,'DELETE,TRUNCATE,TRIGGER')) LIMIT 1",
                {"schema": SCHEMA},
                1,
            )
            if base_access:
                raise ReadError("ROLE_UNPROVEN")
            relations = self._rows(
                cursor,
                "SELECT c.relname,c.relkind::text AS relkind,c.reloptions,"
                "has_table_privilege(current_user,c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') AS mutates,"
                "pg_get_viewdef(c.oid,true) AS definition "
                "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname=%(schema)s ORDER BY c.relname",
                {"schema": SCHEMA},
                20,
            )
            invalid_relation = any(
                r["relkind"] != "v"
                or r["mutates"]
                or "security_barrier=true" not in (r["reloptions"] or [])
                for r in relations
            )
            if not relations or invalid_relation:
                raise ReadError("ROLE_UNPROVEN")
            fingerprint = hashlib.sha256(
                json.dumps(
                    [
                        {"name": r["relname"], "definition": r["definition"]}
                        for r in relations
                    ],
                    sort_keys=True,
                ).encode()
            ).hexdigest()
            if fingerprint != self.binding.expected_schema:
                raise ReadError("SCHEMA_MISMATCH")
            before = self._rows(
                cursor, "SELECT actor_uuid,permission_stamp FROM scope_guard", {}, 1
            )
            if len(before) != 1 or str(before[0]["actor_uuid"]) != ACTOR:
                raise ReadError("AUTH_REQUIRED")
            self.query_count += 1
            rows = self._rows(cursor, sql, args, cap)
            # READ COMMITTED observes committed revocation in the next statement.
            after = self._rows(
                cursor, "SELECT actor_uuid,permission_stamp FROM scope_guard", {}, 1
            )
            if before != after:
                raise ReadError("AUTH_REQUIRED")
            self._grant(principal)
            self._last_proof = (build, cap, copy.deepcopy(rows))
            return rows
        except ReadError:
            raise
        except Exception as exc:
            raise ReadError("SOURCE_UNAVAILABLE") from exc
        finally:
            with self._lock:
                self._active = None
            cleanup_failed = False
            for cleanup in (connection.rollback, cursor.close, connection.close):
                try:
                    cleanup()
                except Exception:
                    cleanup_failed = True
            if cleanup_failed:
                raise ReadError("SOURCE_UNAVAILABLE")

    def revalidate(self, principal: Principal) -> None:
        build, cap, expected = self._last_proof
        if self._query(principal, build, cap) != expected:
            raise ReadError("AUTH_REQUIRED")

    def study(
        self, principal: Principal, args: dict[str, Any], **kwargs: Any
    ) -> dict[str, Any]:
        if args.get("dataset", "documents") != "documents" or any(
            key in args for key in ("version_id", "edition_year")
        ):
            raise ReadError("ACTION_NOT_REGISTERED")
        return super().study(principal, args, **kwargs)


class ReportOnlyGateway(CanonicalGateway):
    allowed_tools = REPORT_TOOLS

    def call(
        self, operation: str, args: dict[str, Any], principal: Principal | None
    ) -> dict[str, Any]:
        if operation not in REPORT_TOOLS:
            raise ReadError("ACTION_NOT_REGISTERED")
        return super().call(operation, args, principal)
