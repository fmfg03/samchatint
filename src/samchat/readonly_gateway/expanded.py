"""Unwired read candidate. Trusted server composition owns identity and policy.

No transport, credentials, grants, database connection or accepted INFORME tool
is changed by importing this module. DomainScope is a policy result, not a grant
that can be requested by a model. Production policy resolution remains unbound.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import math
import re
import secrets
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Awaitable, Callable, Protocol

from .contracts import AuditSink, ReadError
from .expanded_catalog import DATASETS, Query, dataset_catalog


def digest(value: Any) -> str:
    return hashlib.sha256(encode(value)).hexdigest()


def encode(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str
    ).encode()


@dataclass(frozen=True)
class DomainScope:
    dataset: str
    tournament_id: str
    object_ids: frozenset[str]
    version_id: str = ""


@dataclass(frozen=True)
class Authority:
    """Fresh canonical policy result from authenticated server context only.

    Resolver must check active employee, grant revocation, explicit denies,
    domain guard and object/company/tournament/version permission. Missing
    evidence yields no scope; related INFORME access never adds a scope.
    """

    actor_binding: str
    installation: str
    generation: str
    policy_version: str
    expires_at: datetime
    scopes: frozenset[DomainScope]

    def permits(self, query: Query) -> bool:
        return any(
            s.dataset == query.dataset
            and s.tournament_id == query.tournament_id
            and s.version_id == query.version_id
            and set(query.object_ids) <= s.object_ids
            for s in self.scopes
        )

    def fingerprint(self) -> str:
        return digest(
            {
                "actor": self.actor_binding,
                "installation": self.installation,
                "generation": self.generation,
                "policy": self.policy_version,
                "expires": self.expires_at.isoformat(),
                "scopes": sorted(
                    (s.dataset, s.tournament_id, s.version_id, sorted(s.object_ids))
                    for s in self.scopes
                ),
            }
        )


@dataclass(frozen=True)
class Observation:
    rows: list[dict[str, Any]]
    gaps: tuple[str, ...]
    source_version: str
    coverage: str


class ReadAdapter(Protocol):
    async def read(self, query: Query, authority: Authority) -> Observation: ...


def _safe_scalar(value: Any) -> Any:
    if value is None or type(value) in (bool, int):
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ReadError("SOURCE_INVALID")
        return str(value)
    if type(value) is float and math.isfinite(value):
        return value
    if isinstance(value, (datetime,)):
        return value.isoformat()
    if type(value) is str and len(value) <= 120:
        # Only closed categorical fields, identifiers, dates and money are used.
        if re.search(
            r"(?i)bearer\s|postgres(?:ql)?://|PRIVATE KEY|password\s*[:=]|token\s*[:=]",
            value,
        ):
            raise ReadError("SOURCE_INVALID")
        return value
    raise ReadError("SOURCE_INVALID")


def _field_value(key: str, value: Any) -> Any:
    """Field-specific output types; strings are never a numeric escape hatch."""
    value = _safe_scalar(value)
    if value is None:
        return None
    if key == "amount" or key.endswith("_total"):
        try:
            if type(value) is bool:
                raise ValueError
            number = Decimal(str(value))
            if not number.is_finite() or abs(number) >= Decimal("1e15"):
                raise ValueError
            return str(number)
        except (ValueError, InvalidOperation) as exc:
            raise ReadError("SOURCE_INVALID") from exc
    if key.endswith("_count") or key == "total_rows":
        if type(value) is not int or not 0 <= value <= 1000000000:
            raise ReadError("SOURCE_INVALID")
    elif key == "amount_missing":
        if type(value) is not bool:
            raise ReadError("SOURCE_INVALID")
    elif key == "currency":
        if type(value) is not str or not re.fullmatch(r"[A-Z]{3}", value):
            raise ReadError("SOURCE_INVALID")
    elif key == "state":
        if type(value) is not str or not re.fullmatch(r"[a-z_]{1,40}", value):
            raise ReadError("SOURCE_INVALID")
    elif key == "kind" and value != "SOLICITUD":
        raise ReadError("SOURCE_INVALID")
    elif key == "workflow_mode" and value not in {"legacy", "direct"}:
        raise ReadError("SOURCE_INVALID")
    elif key in {
        "created_at",
        "period_start",
        "period_end",
        "scheduled_date",
        "effective_payment_date",
    }:
        try:
            if key == "created_at":
                datetime.fromisoformat(value)
            elif date.fromisoformat(value).isoformat() != value:
                raise ValueError
        except (TypeError, ValueError) as exc:
            raise ReadError("SOURCE_INVALID") from exc
    return value


class ExpandedReads:
    """Local candidate perimeter; deliberately absent from all production factories."""

    def __init__(
        self,
        *,
        resolve: Callable[[], Authority | None],
        adapters: dict[str, ReadAdapter],
        audit: AuditSink,
        source_commit: str,
        clock: Callable[[], datetime] | None = None,
        timeout: float = 3.0,
        resolve_async: Callable[[Query | None], Awaitable[Authority]] | None = None,
    ) -> None:
        if not re.fullmatch(r"[0-9a-f]{40}", source_commit):
            raise ReadError("SOURCE_VERSION_UNPROVEN")
        if set(adapters) - set(DATASETS) or not 0 < timeout <= 5:
            raise ReadError("INVALID_COMPOSITION")
        self.resolve, self.adapters, self.audit = resolve, dict(adapters), audit
        self.resolve_async = resolve_async
        self.commit, self.timeout = source_commit, timeout
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._key = secrets.token_bytes(32)

    def _authority(self) -> Authority:
        if self.resolve_async is not None:
            raise ReadError("ASYNC_AUTHORITY_REQUIRED")
        try:
            return self._validate_authority(self.resolve())
        except ReadError:
            raise
        except Exception as exc:
            raise ReadError("AUTH_REQUIRED") from exc

    def _validate_authority(self, value: Authority | None) -> Authority:
        try:
            if (
                not isinstance(value, Authority)
                or not value.actor_binding
                or not value.installation
                or not value.generation
                or not value.policy_version
                or value.expires_at.tzinfo is None
                or value.expires_at <= self.clock()
            ):
                raise ReadError("AUTH_REQUIRED")
            return value
        except ReadError:
            raise
        except Exception as exc:
            raise ReadError("AUTH_REQUIRED") from exc

    async def _authority_for(self, query: Query | None) -> Authority:
        if self.resolve_async is None:
            return self._authority()
        try:
            async with asyncio.timeout(self.timeout):
                return self._validate_authority(await self.resolve_async(query))
        except asyncio.CancelledError:
            raise
        except ReadError:
            raise
        except Exception as exc:
            raise ReadError("AUTH_REQUIRED") from exc

    async def _revalidate_for(self, before: Authority, query: Query | None) -> None:
        if await self._authority_for(query) != before:
            raise ReadError("AUTH_CHANGED")

    def _revalidate(self, before: Authority) -> None:
        if self._authority() != before:
            raise ReadError("AUTH_CHANGED")

    def _audit(self, event: dict[str, Any]) -> None:
        try:
            self.audit.append(event)
        except Exception as exc:
            raise ReadError("AUDIT_UNAVAILABLE") from exc

    def capabilities(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Proposed samchat.capabilities: no actor/role/scopes in arguments."""
        if arguments != {}:
            raise ReadError("INVALID_ARGUMENTS")
        try:
            actor = self._authority()
            result = self._capabilities(actor)
            self._revalidate(actor)
            return result
        except ReadError as exc:
            self._audit(
                {
                    "operation": "samchat.capabilities",
                    "decision": "deny",
                    "code": exc.code,
                }
            )
            raise

    async def capabilities_async(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if arguments != {}:
            raise ReadError("INVALID_ARGUMENTS")
        try:
            actor = await self._authority_for(None)
            result = self._capabilities(actor)
            await self._revalidate_for(actor, None)
            return result
        except asyncio.CancelledError:
            self._audit({"operation": "samchat.capabilities", "decision": "cancelled"})
            raise
        except ReadError as exc:
            self._audit(
                {
                    "operation": "samchat.capabilities",
                    "decision": "deny",
                    "code": exc.code,
                }
            )
            raise

    def _capabilities(self, actor: Authority) -> dict[str, Any]:
        result = dataset_catalog()
        for name, item in result.items():
            scoped = any(s.dataset == name and s.object_ids for s in actor.scopes)
            adapter = self.adapters.get(name)
            item["candidate_readable"] = (
                scoped
                and adapter is not None
                and (not hasattr(adapter, "supports") or adapter.supports(name))
            )
            item["status"] = (
                "candidate_only"
                if item["candidate_readable"]
                else "not_bound_or_not_authorized"
            )
        self._audit(
            {
                "operation": "samchat.capabilities",
                "decision": "allow",
                "actor_digest": digest(actor.actor_binding),
            }
        )
        return {
            "datasets": result,
            "source_commit": self.commit,
            "production_activation": False,
            "gaps": ["executor_and_production_binding_pending"],
            "business_writes": False,
            "publication": False,
            "deployment": False,
        }

    def _offset(self, query: Query, binding: dict[str, Any]) -> int:
        if not query.cursor:
            return 0
        try:
            raw, signature = query.cursor.split(".")
            expected = hmac.new(self._key, raw.encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(signature, expected):
                raise ValueError
            body = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
            offset, expiry = body.pop("offset"), body.pop("expires")
            if (
                body != binding
                or type(offset) is not int
                or not 0 < offset < 1000
                or type(expiry) not in (int, float)
                or expiry <= self.clock().timestamp()
            ):
                raise ValueError
            return offset
        except (ValueError, KeyError, TypeError) as exc:
            raise ReadError("CURSOR_INVALID_OR_STALE") from exc

    def _cursor(self, offset: int, binding: dict[str, Any]) -> str:
        raw = (
            base64.urlsafe_b64encode(
                encode(
                    {
                        **binding,
                        "offset": offset,
                        "expires": self.clock().timestamp() + 300,
                    }
                )
            )
            .decode()
            .rstrip("=")
        )
        return raw + "." + hmac.new(self._key, raw.encode(), hashlib.sha256).hexdigest()

    async def query(self, arguments: dict[str, Any]) -> dict[str, Any]:
        query = Query.parse(arguments)
        event = {
            "operation": "samchat.dataset_query",
            "dataset": query.dataset,
            "actor_digest": None,
        }
        self._audit({**event, "decision": "attempt"})
        try:
            actor = await self._authority_for(query)
            event.update(
                actor_digest=digest(actor.actor_binding),
                policy_version=actor.policy_version,
            )
            if not actor.permits(query):
                raise ReadError("NOT_FOUND_OR_NOT_VISIBLE")
            adapter = self.adapters.get(query.dataset)
            if adapter is None:
                raise ReadError("CANONICAL_ADAPTER_UNBOUND")
            async with asyncio.timeout(self.timeout):
                result = await adapter.read(query, actor)
            await self._revalidate_for(actor, query)
            if (
                not re.fullmatch(r"[0-9a-f]{64}", result.source_version)
                or len(result.rows) > 1000
                or any(
                    not re.fullmatch(r"[a-z0-9_]{1,100}", gap) for gap in result.gaps
                )
                or not re.fullmatch(r"[a-z0-9_]{1,100}", result.coverage)
            ):
                raise ReadError("SOURCE_INVALID")
            rows = []
            spec = DATASETS[query.dataset]
            for row in result.rows:
                if row.get("object_id") not in query.object_ids:
                    raise ReadError("SOURCE_SCOPE_ESCAPE")
                # Unknown fields never cross the perimeter (PII/accounts/attachments).
                rows.append(
                    {key: _field_value(key, row.get(key)) for key in spec.fields}
                )
            rows.sort(key=lambda r: r["object_id"])
            if len({r["object_id"] for r in rows}) != len(rows):
                raise ReadError("SOURCE_INVALID")
            snapshot = digest(rows)
            binding = {
                "authority": actor.fingerprint(),
                "source_commit": self.commit,
                "query": digest(
                    {k: v for k, v in asdict(query).items() if k != "cursor"}
                ),
                "snapshot": snapshot,
                "source_version": result.source_version,
            }
            if query.group_by:
                groups: dict[tuple[Any, Any], dict[str, Any]] = {}
                for row in rows:
                    key = (row[query.group_by], row.get("currency"))
                    group = groups.setdefault(
                        key,
                        {
                            "group": key[0],
                            "currency": key[1],
                            "count": 0,
                            "sum": Decimal(0),
                            "missing": 0,
                            "missing_currency": 0,
                        },
                    )
                    group["count"] += 1
                    if query.measure:
                        if row.get("currency") not in {
                            "MXN",
                            "USD",
                            "EUR",
                            "GBP",
                            "CAD",
                        }:
                            group["missing_currency"] += 1
                        if row[query.measure] is None:
                            group["missing"] += 1
                        else:
                            group["sum"] += Decimal(str(row[query.measure]))
                rows = [
                    {
                        **g,
                        "sum": (
                            str(g["sum"])
                            if query.measure
                            and not g["missing"]
                            and not g["missing_currency"]
                            else None
                        ),
                    }
                    for _, g in sorted(groups.items(), key=lambda item: str(item[0]))
                ]
            else:
                rows = [{k: row[k] for k in query.fields} for row in rows]
            offset = self._offset(query, binding)
            more = offset + query.page_size < len(rows)
            envelope = {
                "rows": rows[offset : offset + query.page_size],
                "truncated": more,
                "next_cursor": (
                    self._cursor(offset + query.page_size, binding) if more else None
                ),
                "source": spec.source,
                "reviewed_commit": self.commit,
                "source_version": result.source_version,
                "snapshot_sha256": snapshot,
                "observed_at_utc": self.clock().isoformat(),
                "period": {
                    "from": query.start.isoformat(),
                    "to_exclusive": query.end.isoformat(),
                    "basis": spec.period_basis,
                },
                "coverage": result.coverage,
                "gaps": list(result.gaps) + ["candidate_not_activated"],
                "policy_version": actor.policy_version,
            }
            if len(encode(envelope)) > 65536:
                raise ReadError("OUTPUT_LIMIT")
            self._audit({**event, "decision": "allow", "snapshot": snapshot})
            await self._revalidate_for(actor, query)
            return envelope
        except asyncio.CancelledError:
            self._audit({**event, "decision": "cancelled"})
            raise
        except (TimeoutError, InvalidOperation) as exc:
            self._audit({**event, "decision": "deny"})
            raise ReadError(
                "TIMEOUT" if isinstance(exc, TimeoutError) else "SOURCE_INVALID"
            ) from exc
        except ReadError as exc:
            self._audit({**event, "decision": "deny", "code": exc.code})
            raise
        except Exception as exc:
            self._audit({**event, "decision": "deny", "code": "SOURCE_UNAVAILABLE"})
            raise ReadError("SOURCE_UNAVAILABLE") from exc
