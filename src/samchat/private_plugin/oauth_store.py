"""Unwired SQLAlchemy OAuth unit of work over reviewed owner-run tables.

No engine creation, startup DDL, credential reading or employee provisioning.
Only a caller-owned SQLite file fixture may use create_local_fixture_schema.
The PostgreSQL migration is review-only until separately applied and verified.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Iterator
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    Uuid,
    and_,
    insert,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Connection, Engine

from .authorization import AuthorizationRequest, ConsentDraft
from .durable_audit import OUTCOMES
from .oauth_identity import CurrentEmployee, ExistingGrant, ExistingLink

METADATA = MetaData()
_JSON = JSON().with_variant(JSONB(), "postgresql")
# Read-only projection of the existing canonical table. Never created here.
EMPLOYEES = Table(
    "empleados",
    METADATA,
    Column("id", Uuid(as_uuid=False), primary_key=True),
    Column("activo", Boolean, nullable=False),
)
PENDING = Table(
    "private_oauth_pending",
    METADATA,
    Column("hash_id", String(64), primary_key=True),
    Column(
        "employee_id", Uuid(as_uuid=False), ForeignKey("empleados.id"), nullable=False
    ),
    Column("request_json", _JSON, nullable=False),
    Column("browser_hash", String(64), nullable=False),
    Column("csrf_hash", String(64), nullable=False),
    Column("expires_at", BigInteger, nullable=False),
    Column("used", Boolean, nullable=False),
    Column("issuer", Text, nullable=False),
    Column("installation_id", Text, nullable=False),
    Column("organization_id", Text, nullable=False),
)
LINKS = Table(
    "private_oauth_links",
    METADATA,
    Column("link_id", String(32), primary_key=True),
    Column("issuer", Text, nullable=False),
    Column("subject", String(32), nullable=False),
    Column("installation_id", Text, nullable=False),
    Column(
        "employee_id", Uuid(as_uuid=False), ForeignKey("empleados.id"), nullable=False
    ),
    Column("organization_id", Text, nullable=False),
    Column("profile_id", String(32), nullable=False),
    Column("active", Boolean, nullable=False),
    UniqueConstraint("issuer", "installation_id", "employee_id"),
    UniqueConstraint("issuer", "subject"),
    UniqueConstraint("profile_id"),
)
CODES = Table(
    "private_oauth_codes",
    METADATA,
    Column("code_hash", String(64), primary_key=True),
    Column(
        "employee_id", Uuid(as_uuid=False), ForeignKey("empleados.id"), nullable=False
    ),
    Column(
        "link_id", String(32), ForeignKey("private_oauth_links.link_id"), nullable=False
    ),
    Column("request_json", _JSON, nullable=False),
    Column("link_snapshot", _JSON, nullable=False),
    Column("expires_at", BigInteger, nullable=False),
    Column("used", Boolean, nullable=False),
    Column("issuer", Text, nullable=False),
    Column("installation_id", Text, nullable=False),
    Column("organization_id", Text, nullable=False),
)
GRANTS = Table(
    "private_oauth_grants",
    METADATA,
    Column("grant_id", Text, primary_key=True),
    Column("issuer", Text, nullable=False),
    Column("subject", Text, nullable=False),
    Column("token_id", Text, nullable=False),
    Column("client_id", Text, nullable=False),
    Column("installation_id", Text, nullable=False),
    Column(
        "link_id", String(32), ForeignKey("private_oauth_links.link_id"), nullable=False
    ),
    Column("scopes_json", _JSON, nullable=False),
    Column("expires_at", BigInteger, nullable=False),
    Column("active", Boolean, nullable=False),
    Column("revoked", Boolean, nullable=False),
    Column(
        "employee_id", Uuid(as_uuid=False), ForeignKey("empleados.id"), nullable=False
    ),
    Column("organization_id", Text, nullable=False),
    Column("profile_id", String(32), nullable=False),
    UniqueConstraint("issuer", "subject", "token_id"),
)
# Existing migration shape, not a second audit table. No raw OAuth values.
AUDIT = Table(
    "agent_action_receipts",
    METADATA,
    Column("id", Uuid(as_uuid=False), primary_key=True),
    Column("action_id", Text, nullable=False),
    Column("action_version", Text, nullable=False),
    Column("actor_id", Text),
    Column("tenant_id", Text),
    Column("correlation_id", Text),
    Column("normalized_redacted_inputs", _JSON, nullable=False),
    Column("policy_version", Text, nullable=False),
    Column("policy_envelope", _JSON, nullable=False),
    Column("evaluated_preconditions", _JSON, nullable=False),
    Column("decision", Text, nullable=False),
    Column("invoked_domain", Text),
    Column("result", Text, nullable=False),
    Column("verifier", Text, nullable=False),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    Column("idempotency_key", Text),
)
Index("ix_agent_action_receipts_correlation_id", AUDIT.c.correlation_id)
Index(
    "uq_agent_action_receipts_mutation_idempotency",
    AUDIT.c.tenant_id,
    AUDIT.c.actor_id,
    AUDIT.c.action_id,
    AUDIT.c.action_version,
    AUDIT.c.idempotency_key,
    unique=True,
    postgresql_where=AUDIT.c.idempotency_key.is_not(None),
    sqlite_where=AUDIT.c.idempotency_key.is_not(None),
)
OWN_TABLES = (LINKS, PENDING, CODES, GRANTS)
AUDIT_EVENTS = {
    "oauth.begin": "CONSENT_PREPARED",
    "oauth.confirm": "CODE_ISSUED",
    "oauth.exchange": "TOKEN_ISSUED",
    "oauth.revoke": "GRANT_REVOKED",
    "oauth.revoke_link": "LINK_REVOKED",
}


class OAuthStoreError(RuntimeError):
    """Stable storage failure; never exposes SQL, binds or database details."""


@dataclass(frozen=True)
class PendingRecord:
    hash_id: str
    draft: ConsentDraft
    used: bool = False


@dataclass(frozen=True)
class CodeRecord:
    code_hash: str
    employee_id: str
    request: AuthorizationRequest
    link: ExistingLink
    expires_at: int
    used: bool = False


def _hash(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise OAuthStoreError("STORE_INVALID_RECORD")
    return value


def _employee_id(value):
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise OAuthStoreError("STORE_INVALID_RECORD")
    return value


def _request_json(request):
    # request.state deliberately contains digest(original OAuth state), not state.
    _hash(request.state)
    if (
        not isinstance(request, AuthorizationRequest)
        or type(request.scopes) is not frozenset
        or not request.scopes
        or any(
            not isinstance(s, str) or not re.fullmatch(r"[a-z][a-z0-9_]*:read", s)
            for s in request.scopes
        )
        or not re.fullmatch(r"[A-Za-z0-9_-]{43}", request.challenge)
        or not all(
            isinstance(v, str) and v and len(v) <= 2048
            for v in (request.client_id, request.redirect_uri, request.resource)
        )
    ):
        raise OAuthStoreError("STORE_INVALID_RECORD")
    return {**asdict(request), "scopes": sorted(request.scopes)}


def _request(row):
    value = dict(row)
    value["scopes"] = frozenset(value["scopes"])
    result = AuthorizationRequest(**value)
    _request_json(result)
    return result


def _normalized(value):
    if isinstance(value, datetime):
        return (
            value.replace(tzinfo=timezone.utc)
            if value.tzinfo is None
            else value.astimezone(timezone.utc)
        )
    return value


def create_local_fixture_schema(engine: Engine) -> None:
    """Explicit local fixture migration; never create/modify empleados.

    PostgreSQL DDL is supplied separately for review. Not a startup helper.
    Caller must create its own synthetic empleados fixture before this call.
    """
    if (
        engine.dialect.name != "sqlite"
        or not engine.url.database
        or engine.url.database == ":memory:"
    ):
        raise OAuthStoreError("LOCAL_FILE_FIXTURE_REQUIRED")
    with engine.begin() as connection:
        METADATA.create_all(connection, tables=[*OWN_TABLES, AUDIT])


class OAuthStore:
    def __init__(
        self, engine: Engine, *, issuer: str, installation_id: str, organization_id: str
    ):
        if not all(
            isinstance(v, str) and v.strip() and len(v) <= 2048
            for v in (issuer, installation_id, organization_id)
        ):
            raise OAuthStoreError("STORE_INVALID_CONFIGURATION")
        self.engine = engine
        self.issuer = issuer
        self.installation_id = installation_id
        self.organization_id = organization_id

    @contextmanager
    def transaction(
        self, *, now: int, clock: Callable[[], int] | None = None
    ) -> Iterator[OAuthTransaction]:
        if type(now) is not int or now <= 0:
            raise OAuthStoreError("STORE_INVALID_TIME")
        transaction = None
        try:
            with self.engine.connect() as connection:
                if connection.dialect.name not in {"sqlite", "postgresql"}:
                    raise OAuthStoreError("STORE_DIALECT_UNPROVEN")
                if (
                    connection.dialect.name == "postgresql"
                    and getattr(
                        connection.connection.driver_connection, "autocommit", None
                    )
                    is not False
                ):
                    raise OAuthStoreError("STORE_TRANSACTION_REQUIRED")
                if connection.dialect.name == "sqlite":
                    if connection.exec_driver_sql(
                        "PRAGMA synchronous"
                    ).scalar_one() not in (2, 3):
                        raise OAuthStoreError("STORE_DURABILITY_REQUIRED")
                    if connection.exec_driver_sql(
                        "PRAGMA journal_mode"
                    ).scalar_one() in ("off", "memory"):
                        raise OAuthStoreError("STORE_DURABILITY_REQUIRED")
                    # Reserve before reading so concurrent consumes cannot both win.
                    connection.exec_driver_sql("BEGIN IMMEDIATE")
                transaction = OAuthTransaction(connection, self, now, clock=clock)
                try:
                    yield transaction
                    if transaction.mutated and not transaction.audit_ids:
                        raise OAuthStoreError("STORE_AUDIT_REQUIRED")
                    # Recheck audit rows after all statements, not only INSERT.
                    transaction._verify_audits()
                    transaction._verify_deadlines()
                    connection.commit()
                except BaseException:
                    connection.rollback()
                    raise
                finally:
                    transaction.closed = True
        except OAuthStoreError:
            raise
        except Exception as exc:
            # Caller domain denials must preserve their stable code.
            from .contracts import Denied

            if isinstance(exc, Denied):
                raise
            raise OAuthStoreError("STORE_UNAVAILABLE") from None


class OAuthTransaction:
    def __init__(
        self,
        connection: Connection,
        store: OAuthStore,
        now: int,
        *,
        clock: Callable[[], int] | None = None,
    ):
        self.connection, self.store, self.now = connection, store, now
        self.mutated = False
        self.audit_ids = {}
        self.closed = False
        self.clock = clock
        self.deadlines: list[int] = []

    def _fresh_time(self):
        if self.clock is None:
            raise OAuthStoreError("STORE_CURRENT_CLOCK_REQUIRED")
        current = self.clock()
        if type(current) is not int or current < self.now:
            raise OAuthStoreError("STORE_INVALID_TIME")
        self.now = current
        return current

    def _deadline(self, expires_at):
        if type(expires_at) is not int or self._fresh_time() >= expires_at:
            raise OAuthStoreError("STORE_EXPIRED")
        self.deadlines.append(expires_at)

    def _verify_deadlines(self):
        if self.deadlines and self._fresh_time() >= min(self.deadlines):
            raise OAuthStoreError("STORE_EXPIRED")

    def _open(self):
        if self.closed:
            raise OAuthStoreError("STORE_TRANSACTION_CLOSED")

    def _namespace(self, table):
        return and_(
            table.c.issuer == self.store.issuer,
            table.c.installation_id == self.store.installation_id,
            table.c.organization_id == self.store.organization_id,
        )

    def _one(self, statement):
        self._open()
        return self.connection.execute(statement).mappings().one_or_none()

    def _insert(self, table, values, primary):
        self._open()
        # SQLAlchemy does not preserve INSERT rowcount on every driver.
        # RETURNING verifies one exact key, then readback verifies the full row.
        returned = (
            self.connection.execute(
                insert(table).values(**values).returning(table.c[primary])
            )
            .scalars()
            .all()
        )
        if returned != [values[primary]]:
            raise OAuthStoreError("STORE_WRITE_UNVERIFIED")
        row = self._one(select(table).where(table.c[primary] == values[primary]))
        if row is None or any(
            _normalized(row[k]) != _normalized(v) for k, v in values.items()
        ):
            raise OAuthStoreError("STORE_WRITE_UNVERIFIED")
        self.mutated = True

    def current_employee(self, employee_id: str) -> CurrentEmployee:
        employee_id = _employee_id(employee_id)
        row = self._one(
            select(EMPLOYEES).where(EMPLOYEES.c.id == employee_id).with_for_update()
        )
        if not row or row["activo"] is not True:
            raise OAuthStoreError("STORE_EMPLOYEE_INACTIVE")
        return CurrentEmployee(employee_id, True)

    def put_pending(self, record: PendingRecord):
        self.current_employee(record.draft.employee_id)
        self._deadline(record.draft.expires_at)
        if (
            record.used is not False
            or type(record.draft.expires_at) is not int
            or record.draft.expires_at <= self.now
        ):
            raise OAuthStoreError("STORE_INVALID_RECORD")
        self._insert(
            PENDING,
            dict(
                hash_id=_hash(record.hash_id),
                employee_id=record.draft.employee_id,
                request_json=_request_json(record.draft.request),
                browser_hash=_hash(record.draft.browser_binding_hash),
                csrf_hash=_hash(record.draft.csrf_hash),
                expires_at=record.draft.expires_at,
                used=False,
                **self._namespace_values(),
            ),
            "hash_id",
        )

    def _namespace_values(self):
        return dict(
            issuer=self.store.issuer,
            installation_id=self.store.installation_id,
            organization_id=self.store.organization_id,
        )

    def read_pending(self, hash_id: str) -> PendingRecord | None:
        row = self._one(
            select(PENDING).where(
                PENDING.c.hash_id == _hash(hash_id), self._namespace(PENDING)
            )
        )
        return (
            PendingRecord(
                row["hash_id"],
                ConsentDraft(
                    str(row["employee_id"]),
                    _request(row["request_json"]),
                    row["browser_hash"],
                    row["csrf_hash"],
                    row["expires_at"],
                ),
                row["used"],
            )
            if row
            else None
        )

    def consume_pending(self, hash_id: str) -> PendingRecord:
        record = self.read_pending(hash_id)
        if record is None:
            raise OAuthStoreError("STORE_PENDING_UNAVAILABLE")
        self.current_employee(record.draft.employee_id)
        self._deadline(record.draft.expires_at)
        self._consume(PENDING, PENDING.c.hash_id == hash_id)
        return record

    def _consume(self, table, key):
        self._fresh_time()
        result = self.connection.execute(
            update(table)
            .where(
                key,
                self._namespace(table),
                table.c.used.is_(False),
                table.c.expires_at > self.now,
            )
            .values(used=True)
        )
        if result.rowcount != 1:
            raise OAuthStoreError("STORE_ALREADY_USED_OR_EXPIRED")
        row = self._one(select(table.c.used).where(key, self._namespace(table)))
        if row is None or row["used"] is not True:
            raise OAuthStoreError("STORE_WRITE_UNVERIFIED")
        self.mutated = True

    def get_or_create_link(self, employee_id: str) -> ExistingLink:
        self.current_employee(employee_id)
        row = self._one(
            select(LINKS)
            .where(
                LINKS.c.issuer == self.store.issuer,
                LINKS.c.installation_id == self.store.installation_id,
                LINKS.c.employee_id == employee_id,
            )
            .with_for_update()
        )
        if row:
            link = ExistingLink(**dict(row))
            self._validate_link(link, employee_id)
            return link
        link = ExistingLink(
            uuid4().hex,
            self.store.issuer,
            uuid4().hex,
            self.store.installation_id,
            employee_id,
            self.store.organization_id,
            uuid4().hex,
            True,
        )
        self._insert(LINKS, asdict(link), "link_id")
        return link

    def read_link(self, link_id: str) -> ExistingLink | None:
        row = self._one(
            select(LINKS)
            .where(LINKS.c.link_id == link_id, self._namespace(LINKS))
            .with_for_update()
        )
        return ExistingLink(**dict(row)) if row else None

    def _validate_link(self, link, employee_id):
        if (
            link is None
            or link.active is not True
            or link.employee_id != employee_id
            or link.issuer != self.store.issuer
            or link.installation_id != self.store.installation_id
            or link.organization_id != self.store.organization_id
        ):
            raise OAuthStoreError("STORE_LINK_UNAVAILABLE")

    def put_code(self, record: CodeRecord):
        self.current_employee(record.employee_id)
        self._validate_link(record.link, record.employee_id)
        self._deadline(record.expires_at)
        if (
            self.read_link(record.link.link_id) != record.link
            or record.used is not False
            or type(record.expires_at) is not int
            or record.expires_at <= self.now
        ):
            raise OAuthStoreError("STORE_INVALID_RECORD")
        self._insert(
            CODES,
            dict(
                code_hash=_hash(record.code_hash),
                employee_id=record.employee_id,
                link_id=record.link.link_id,
                request_json=_request_json(record.request),
                link_snapshot=asdict(record.link),
                expires_at=record.expires_at,
                used=False,
                **self._namespace_values(),
            ),
            "code_hash",
        )

    def read_code(self, code_hash: str) -> CodeRecord | None:
        row = self._one(
            select(CODES).where(
                CODES.c.code_hash == _hash(code_hash), self._namespace(CODES)
            )
        )
        return (
            CodeRecord(
                row["code_hash"],
                str(row["employee_id"]),
                _request(row["request_json"]),
                ExistingLink(**row["link_snapshot"]),
                row["expires_at"],
                row["used"],
            )
            if row
            else None
        )

    def consume_code(self, code_hash: str) -> CodeRecord:
        record = self.read_code(code_hash)
        if record is None:
            raise OAuthStoreError("STORE_CODE_UNAVAILABLE")
        self.current_employee(record.employee_id)
        self._validate_link(record.link, record.employee_id)
        if self.read_link(record.link.link_id) != record.link:
            raise OAuthStoreError("STORE_LINK_CHANGED")
        self._deadline(record.expires_at)
        self._consume(CODES, CODES.c.code_hash == code_hash)
        return record

    def put_grant(self, grant: ExistingGrant):
        self.current_employee(grant.employee_id)
        link = self.read_link(grant.link_id)
        self._validate_link(link, grant.employee_id)
        self._deadline(grant.expires_at)
        if (
            grant.issuer != link.issuer
            or grant.subject != link.subject
            or grant.installation_id != link.installation_id
            or grant.organization_id != link.organization_id
            or grant.profile_id != link.profile_id
            or grant.active is not True
            or grant.revoked is not False
            or type(grant.expires_at) is not int
            or grant.expires_at <= self.now
            or type(grant.scopes) is not frozenset
            or not grant.scopes
            or any(not re.fullmatch(r"[a-z][a-z0-9_]*:read", s) for s in grant.scopes)
            or not all(
                isinstance(v, str) and re.fullmatch(r"[A-Za-z0-9:_-]{1,128}", v)
                for v in (grant.grant_id, grant.token_id, grant.client_id)
            )
        ):
            raise OAuthStoreError("STORE_INVALID_RECORD")
        values = asdict(grant)
        values["scopes_json"] = sorted(values.pop("scopes"))
        self._insert(GRANTS, values, "grant_id")

    def read_grant(self, grant_id: str) -> ExistingGrant | None:
        row = self._one(
            select(GRANTS)
            .where(GRANTS.c.grant_id == grant_id, self._namespace(GRANTS))
            .with_for_update()
        )
        if not row:
            return None
        values = dict(row)
        values["scopes"] = frozenset(values.pop("scopes_json"))
        return ExistingGrant(**values)

    def revoke_grant(self, grant_id: str, *, employee_id: str):
        self.current_employee(employee_id)
        grant = self.read_grant(grant_id)
        if grant is None or grant.employee_id != employee_id:
            raise OAuthStoreError("STORE_GRANT_UNAVAILABLE")
        self._revoke(
            GRANTS, GRANTS.c.grant_id == grant_id, dict(active=False, revoked=True)
        )

    def revoke_link(self, link_id: str, *, employee_id: str):
        self.current_employee(employee_id)
        link = self.read_link(link_id)
        if link is None or link.employee_id != employee_id:
            raise OAuthStoreError("STORE_LINK_UNAVAILABLE")
        self._revoke(LINKS, LINKS.c.link_id == link_id, dict(active=False))
        # Token validation also rechecks the link. Persist grant revocation too.
        self.connection.execute(
            update(GRANTS)
            .where(GRANTS.c.link_id == link_id, self._namespace(GRANTS))
            .values(active=False, revoked=True)
        )
        remaining = self._one(
            select(GRANTS.c.grant_id).where(
                GRANTS.c.link_id == link_id,
                self._namespace(GRANTS),
                (GRANTS.c.active.is_not(False) | GRANTS.c.revoked.is_not(True)),
            )
        )
        if remaining is not None:
            raise OAuthStoreError("STORE_WRITE_UNVERIFIED")

    def _revoke(self, table, key, values):
        result = self.connection.execute(
            update(table).where(key, self._namespace(table)).values(**values)
        )
        row = self._one(select(table).where(key, self._namespace(table)))
        if (
            result.rowcount != 1
            or row is None
            or any(row[k] != v for k, v in values.items())
        ):
            raise OAuthStoreError("STORE_WRITE_UNVERIFIED")
        self.mutated = True

    def append_audit(
        self,
        *,
        actor_id: str,
        action: str,
        outcome: str,
        evidence_digest: str,
        scope_digest: str,
    ) -> str:
        self.current_employee(actor_id)
        if AUDIT_EVENTS.get(action) != outcome:
            raise OAuthStoreError("STORE_INVALID_RECORD")
        return self._audit_values(
            actor_id,
            action,
            outcome,
            evidence_digest,
            scope_digest,
            "identity",
            "allow",
        )

    def append_boundary_audit(
        self, *, actor_id, action, outcome, evidence_digest, scope_digest
    ):
        if (
            action
            not in {
                "get_profile",
                "direction_list_scopes",
                "direction_read_summary",
                "mcp.tools.list",
                "mcp.tools.call",
                "unregistered",
            }
            or outcome not in OUTCOMES
        ):
            raise OAuthStoreError("STORE_INVALID_RECORD")
        if actor_id is not None:
            _employee_id(actor_id)
        if outcome == "READ_VERIFIED":
            self.current_employee(actor_id)
        decision = (
            "allow"
            if outcome in {"READ_VERIFIED", "READ_CATALOG", "READ_REQUESTED"}
            else "deny"
        )
        return self._audit_values(
            actor_id,
            action,
            outcome,
            evidence_digest,
            scope_digest,
            "private_mcp",
            decision,
        )

    def _audit_values(
        self, actor_id, action, outcome, evidence_digest, scope_digest, domain, decision
    ):
        values = dict(
            id=str(uuid4()),
            action_id=action,
            action_version="private-oauth-v1",
            actor_id=actor_id,
            tenant_id=None,
            correlation_id=None,
            normalized_redacted_inputs={
                "evidence_digest": _hash(evidence_digest),
                "scope_digest": _hash(scope_digest),
            },
            policy_version="private-oauth-v1",
            policy_envelope={
                "installation_id": self.store.installation_id,
                "organization_id": self.store.organization_id,
                "issuer": self.store.issuer,
            },
            evaluated_preconditions=(
                ["current_existing_employee"]
                if outcome == "READ_VERIFIED" or domain == "identity"
                else []
            ),
            decision=decision,
            invoked_domain=domain,
            result=outcome,
            verifier="transaction_readback",
            occurred_at=datetime.fromtimestamp(self.now, timezone.utc),
            idempotency_key=None,
        )
        self._insert(AUDIT, values, "id")
        self.audit_ids[values["id"]] = values
        return values["id"]

    def _verify_audits(self):
        for receipt_id, expected in self.audit_ids.items():
            row = self._one(select(AUDIT).where(AUDIT.c.id == receipt_id))
            if row is None or any(
                _normalized(row[k]) != _normalized(v) for k, v in expected.items()
            ):
                raise OAuthStoreError("STORE_WRITE_UNVERIFIED")
