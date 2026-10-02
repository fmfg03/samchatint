"""Unwired Direction read adapter over existing canonical owners.

No live imports, connection, credential, write, or authorization fallback. The
composition root must bind the named owners to the existing Direction functions
and provide a current local employee plus an exact employee/scope organization
mapping. Those production integrations remain unproven; none is inferred here.
Canon unchanged: this adapter consumes the established read authority only.
"""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from jsonschema import Draft202012Validator

from .contracts import Denied, Identity


@dataclass(frozen=True)
class DirectionContext:
    """Trusted request-local context, never constructed from model arguments.

    session must be a read-only, request-owned database session. Each context
    resolution must refresh employee and authorization from current sources,
    not reuse an ORM cache or an old transaction snapshot. No session is opened
    by this module. Cross-store atomicity is not claimed.
    """

    identity: Identity
    employee: Any
    session: Any


@dataclass(frozen=True)
class DirectionOwners:
    """Required canonical bindings, supplied by a reviewed composition root.

    routes: _assigned_direction_portfolios, _direction_source_access, _is_superadmin
    home: resolve_scope, build_home. No replacement business rules are supplied.
    """

    assigned_portfolios: Callable[..., Awaitable[list[str]]]
    source_access: Callable[..., Awaitable[dict[str, bool]]]
    is_superadmin: Callable[[Any], bool]
    resolve_scope: Callable[..., Awaitable[dict]]
    build_home: Callable[..., Awaitable[tuple[dict, dict]]]
    access_errors: tuple[type[Exception], ...] = ()


_METRICS = frozenset(
    {
        "budget",
        "actual",
        "committed",
        "paid",
        "forecast",
        "deviation",
        "obligations",
        "receivables",
        "overdue",
        "liquidity",
    }
)
_INDICATOR_FIELDS = (
    "id",
    "label",
    "definition",
    "formula",
    "value",
    "formatted_value",
    "status",
    "unit",
    "source",
    "period",
    "as_of",
    "validator",
    "gaps",
    "validation_status",
)


def _object(properties: dict) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


_UUID = {"type": "string", "pattern": r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$"}
_SELECTOR = {"anyOf": [_UUID, {"type": "null"}]}
_DATE = {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"}
_TEXT = {"type": "string", "maxLength": 2000}
_PAGE_SIZE = 25
_INLINE_IDS = 200
_SCOPE_MANIFEST = _object(
    {
        "portfolio_count": {"type": "integer", "minimum": 0},
        "tournament_count": {"type": "integer", "minimum": 0},
        "portfolio_ids_complete": {"type": "boolean"},
        "tournament_ids_complete": {"type": "boolean"},
        "scope_digest": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
    }
)
_INDICATOR_SCHEMA = _object(
    {
        **{
            key: _TEXT
            for key in (
                "label",
                "definition",
                "formula",
                "formatted_value",
                "period",
                "as_of",
                "validator",
            )
        },
        "id": {"enum": sorted(_METRICS)},
        "value": {
            "anyOf": [
                {"type": "null"},
                {"type": "string", "maxLength": 64, "pattern": r"^-?\d+(?:\.\d+)?$"},
            ]
        },
        "status": {"enum": ["available", "partial", "unavailable"]},
        "unit": {"const": "MXN"},
        "source": {
            "enum": [
                "samchat.budgets.service.build_budget_snapshot",
                "samchat.budgets.executive_facts.build_executive_facts",
                "samchat.finance_platform.service.build_finance_source_snapshot",
                "samchat.ar.service.build_ar_read_model",
                "bank_balance_source_unavailable",
            ]
        },
        "gaps": {"type": "array", "maxItems": 100, "items": _TEXT},
        "validation_status": {"const": "pending_business_validation"},
        "coverage": _object(
            {key: {"type": "integer", "minimum": 0} for key in ("covered", "total")}
        ),
    }
)
DIRECTION_OUTPUT_SCHEMA = _object(
    {
        "schema": {"const": "samchat.private.direction.read.v1"},
        "read_only": {"const": True},
        "snapshot_id": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
        "edition_year": {"type": "integer", "minimum": 2000, "maximum": 2100},
        "as_of": {"type": "string", "maxLength": 50},
        "period": _object({"start": _DATE, "end": _DATE}),
        "scope": _object(
            {
                "portfolio_ids": {
                    "type": "array",
                    "maxItems": 1000,
                    "uniqueItems": True,
                    "items": _UUID,
                },
                "portfolio_id": _SELECTOR,
                "tournament_id": _SELECTOR,
            }
        ),
        "tournament_ids": {
            "type": "array",
            "maxItems": 1000,
            "uniqueItems": True,
            "items": _UUID,
        },
        "source_access": _object(
            {key: {"type": "boolean"} for key in ("budget", "finance")}
        ),
        "indicators": {
            "type": "array",
            "minItems": 10,
            "maxItems": 10,
            "items": _INDICATOR_SCHEMA,
        },
        "source_consistency": {
            "const": "independent_reads_with_individual_cuts_not_cross_store_atomic"
        },
        "business_acceptance": {"const": "pending"},
    }
)
DIRECTION_OUTPUT_SCHEMA["properties"]["scope_manifest"] = _SCOPE_MANIFEST
DIRECTION_OUTPUT_SCHEMA["required"].append("scope_manifest")
_OUTPUT_VALIDATOR = Draft202012Validator(DIRECTION_OUTPUT_SCHEMA)
DIRECTION_SCOPES_OUTPUT_SCHEMA = _object(
    {
        "schema": {"const": "samchat.private.direction.scopes.v1"},
        "read_only": {"const": True},
        "scope": DIRECTION_OUTPUT_SCHEMA["properties"]["scope"],
        **{
            key: {
                "type": "array",
                "maxItems": 1000,
                "items": _object(
                    {
                        "id": _UUID,
                        "label": {"type": "string", "maxLength": 200},
                    }
                ),
            }
            for key in ("portfolios", "tournaments")
        },
    }
)
DIRECTION_SCOPES_OUTPUT_SCHEMA["properties"].update(
    {
        "scope_manifest": _SCOPE_MANIFEST,
        "pagination": _object(
            {
                "next_cursor": {"type": ["string", "null"], "maxLength": 1024},
                "page_size": {"const": _PAGE_SIZE},
            }
        ),
    }
)
DIRECTION_SCOPES_OUTPUT_SCHEMA["required"].extend(["scope_manifest", "pagination"])
_SCOPES_VALIDATOR = Draft202012Validator(DIRECTION_SCOPES_OUTPUT_SCHEMA)


def _bounded_scope(
    scope: dict, tournament_ids: list[str]
) -> tuple[dict, list[str], dict]:
    """Omit long ID arrays explicitly; counts/digest still describe full authority.

    Financial indicators always aggregate the full authorized snapshot. Fetch
    catalog pages to enumerate IDs when the manifest marks an array incomplete.
    """
    for values in (scope["portfolio_ids"], tournament_ids):
        if any(
            type(value) is not str or _selection(value) != value for value in values
        ) or len(set(values)) != len(values):
            raise Denied("SOURCE_UNAVAILABLE")
    portfolios, tournaments = sorted(scope["portfolio_ids"]), sorted(tournament_ids)
    full = {
        "portfolio_ids": portfolios,
        "tournament_ids": tournaments,
        "portfolio_id": scope["portfolio_id"],
        "tournament_id": scope["tournament_id"],
    }
    manifest = {
        "portfolio_count": len(portfolios),
        "tournament_count": len(tournaments),
        "portfolio_ids_complete": len(portfolios) <= _INLINE_IDS,
        "tournament_ids_complete": len(tournaments) <= _INLINE_IDS,
        "scope_digest": hashlib.sha256(
            json.dumps(full, sort_keys=True).encode()
        ).hexdigest(),
    }
    bounded = {k: full[k] for k in ("portfolio_id", "tournament_id")}
    bounded["portfolio_ids"] = portfolios if manifest["portfolio_ids_complete"] else []
    return bounded, tournaments if manifest["tournament_ids_complete"] else [], manifest


def _selection(value: str | None) -> str | None:
    if value is None:
        return None
    if type(value) is not str:
        raise Denied("INVALID_SELECTOR")
    try:
        return str(UUID(value))
    except ValueError:
        raise Denied("INVALID_SELECTOR") from None


def _scope_key(scope: dict) -> tuple:
    return (
        tuple(sorted(scope["portfolio_ids"])),
        scope["portfolio_id"],
        scope["tournament_id"],
        tuple(sorted(t["id"] for t in scope["selected"])),
    )


def _minimize(snapshot: dict) -> dict:
    """Expose aggregate evidence, not contacts, payment rows or roster records."""
    indicators = []
    for row in snapshot["indicators"]:
        if row["id"] not in _METRICS:
            continue
        indicator = {key: row.get(key) for key in _INDICATOR_FIELDS}
        indicator["coverage"] = {
            key: row["coverage"].get(key) for key in ("covered", "total")
        }
        indicators.append(indicator)
    scope, tournament_ids, manifest = _bounded_scope(
        snapshot["scope"], snapshot["tournament_ids"]
    )
    return {
        "schema": "samchat.private.direction.read.v1",
        "read_only": True,
        "snapshot_id": snapshot["snapshot_id"],
        "edition_year": snapshot["edition_year"],
        "as_of": snapshot["as_of"],
        "period": {k: snapshot["period"][k] for k in ("start", "end")},
        "scope": scope,
        "tournament_ids": tournament_ids,
        "scope_manifest": manifest,
        "source_access": dict(snapshot["source_access"]),
        "indicators": indicators,
        "source_consistency": snapshot["source_consistency"],
        "business_acceptance": snapshot["business_acceptance"],
    }


class DirectionReadAdapter:
    """Local executable integration; no production composition or MCP enablement.

    organization_for_scope must resolve BOTH employee membership and every
    selected portfolio/tournament to a single current organization. Return None
    for absent/ambiguous mapping. A token claim alone is never this evidence.
    """

    def __init__(
        self,
        *,
        current_context: Callable[[], Awaitable[DirectionContext]],
        organization_for_scope: Callable[
            [DirectionContext, dict], Awaitable[str | None]
        ],
        owners: DirectionOwners,
        now: Callable[[], int],
    ):
        self._context = current_context
        self._organization = organization_for_scope
        self._owners = owners
        self._now = now

    async def _current(self, identity: Identity) -> DirectionContext:
        context = await self._context()
        employee = context.employee
        if (
            context.identity != identity
            or identity.active is not True
            or identity.revoked is not False
            or type(identity.expires_at) is not int
            or identity.expires_at <= self._now()
            or not identity.organization_id
            or not identity.actor_id
            or not identity.grant_id
            or "direction:read" not in identity.oauth_scopes
            or getattr(employee, "activo", None) is not True
            or str(getattr(employee, "id", "")) != identity.actor_id
        ):
            raise Denied("UNAUTHENTICATED")
        return context

    async def _authorize(self, identity, portfolio_id, tournament_id):
        context = await self._current(identity)
        employee, owners = context.employee, self._owners
        assigned = await owners.assigned_portfolios(context.session, employee)
        if not assigned and not owners.is_superadmin(employee):
            raise Denied("FORBIDDEN")
        args = dict(
            actor=identity.actor_id,
            superadmin=owners.is_superadmin(employee),
            portfolio_id=portfolio_id,
            tournament_id=tournament_id,
        )
        scope = await owners.resolve_scope(context.session, **args)
        if not set(scope["portfolio_ids"]).issubset(assigned):
            raise Denied("FORBIDDEN")
        organization = await self._organization(context, scope)
        if not organization or organization != identity.organization_id:
            raise Denied("ORGANIZATION_UNPROVEN")
        access = await owners.source_access(context.session, employee)
        if set(access) != {"budget", "finance"} or any(
            type(value) is not bool for value in access.values()
        ):
            raise Denied("FORBIDDEN")
        return context, args, scope, access, organization

    async def list_scopes(
        self, *, identity: Identity, cursor: str | None = None
    ) -> dict:
        """List authorized current selectors; never load financial/roster data."""
        offset, expected_digest = 0, None
        if cursor is not None:
            try:
                if type(cursor) is not str or len(cursor) > 1024:
                    raise ValueError()
                expected_digest, offset = json.loads(base64.urlsafe_b64decode(cursor))
                if (
                    type(offset) is not int
                    or offset < 0
                    or not isinstance(expected_digest, str)
                ):
                    raise ValueError()
            except Exception:
                raise Denied("INVALID_SELECTOR") from None
        try:
            _, args, scope, access, organization = await self._authorize(
                identity, None, None
            )
            portfolios = sorted(scope["portfolios"], key=lambda row: row["id"])
            tournaments = sorted(scope["selected"], key=lambda row: row["id"])
            bounded, _, manifest = _bounded_scope(
                scope, [row["id"] for row in tournaments]
            )
            if (
                expected_digest is not None
                and expected_digest != manifest["scope_digest"]
            ):
                raise Denied("SCOPE_CHANGED")
            if (
                {row["id"] for row in portfolios} != set(scope["portfolio_ids"])
                or len({row["id"] for row in portfolios}) != len(portfolios)
                or len({row["id"] for row in tournaments}) != len(tournaments)
            ):
                raise Denied("SOURCE_UNAVAILABLE")
            next_offset = offset + _PAGE_SIZE
            next_cursor = (
                base64.urlsafe_b64encode(
                    json.dumps([manifest["scope_digest"], next_offset]).encode()
                ).decode()
                if next_offset < max(len(portfolios), len(tournaments))
                else None
            )
            result = {
                "schema": "samchat.private.direction.scopes.v1",
                "read_only": True,
                "scope": bounded,
                "scope_manifest": manifest,
                "pagination": {"next_cursor": next_cursor, "page_size": _PAGE_SIZE},
                "portfolios": [
                    {"id": row["id"], "label": row["label"]}
                    for row in portfolios[offset:next_offset]
                ],
                "tournaments": [
                    {"id": row["id"], "label": row["name"]}
                    for row in tournaments[offset:next_offset]
                ],
            }
            _SCOPES_VALIDATOR.validate(result)
            if len(json.dumps(result, ensure_ascii=False).encode()) > 65536:
                raise Denied("SOURCE_UNAVAILABLE")
            _, current_args, current_scope, current_access, current_org = (
                await self._authorize(identity, None, None)
            )
            if (
                current_args != args
                or _scope_key(current_scope) != _scope_key(scope)
                or current_access != access
                or current_org != organization
            ):
                raise Denied("SCOPE_CHANGED")
            return result
        except Exception as exc:
            raise self._safe_error(exc) from None

    def _safe_error(self, exc: Exception) -> Denied:
        if isinstance(exc, Denied):
            allowed = {
                "UNAUTHENTICATED",
                "FORBIDDEN",
                "ORGANIZATION_UNPROVEN",
                "SCOPE_CHANGED",
            }
            return Denied(str(exc) if str(exc) in allowed else "SOURCE_UNAVAILABLE")
        if (
            isinstance(exc, self._owners.access_errors)
            and getattr(exc, "status_code", 403) == 403
        ):
            return Denied("FORBIDDEN")
        return Denied("SOURCE_UNAVAILABLE")

    async def read(
        self,
        *,
        identity: Identity,
        portfolio_id: str | None = None,
        tournament_id: str | None = None,
        year: int = 2026,
    ) -> dict:
        """Read for the gate's trusted identity; only selectors come from a model."""
        portfolio_id, tournament_id = _selection(portfolio_id), _selection(
            tournament_id
        )
        if type(year) is not int or not 2000 <= year <= 2100:
            raise Denied("INVALID_SELECTOR")
        try:
            context, args, scope, access, organization = await self._authorize(
                identity, portfolio_id, tournament_id
            )
            snapshot, final_scope = await self._owners.build_home(
                context.session, year=year, source_access=access, **args
            )
            if (
                _scope_key(final_scope) != _scope_key(scope)
                or snapshot["edition_year"] != year
                or snapshot["source_access"] != access
                or set(snapshot["tournament_ids"])
                != {t["id"] for t in scope["selected"]}
                or snapshot["scope"]
                != {
                    "tournament_ids": scope.get("tournament_ids", []),
                    **{
                        k: scope[k]
                        for k in ("portfolio_ids", "portfolio_id", "tournament_id")
                    },
                }
            ):
                raise Denied("SCOPE_CHANGED")
            # JWT identity does not encode current employee positions or source
            # denials. Refresh canonical authority after the awaited read too.
            _, current_args, current_scope, current_access, current_organization = (
                await self._authorize(identity, portfolio_id, tournament_id)
            )
            if (
                current_args != args
                or _scope_key(current_scope) != _scope_key(scope)
                or current_access != access
                or current_organization != organization
            ):
                raise Denied("SCOPE_CHANGED")
            result = _minimize(snapshot)
            _OUTPUT_VALIDATOR.validate(result)
            if {row["id"] for row in result["indicators"]} != _METRICS or any(
                row["coverage"]["covered"] > row["coverage"]["total"]
                for row in result["indicators"]
            ):
                raise Denied("SOURCE_UNAVAILABLE")
            if len(json.dumps(result, ensure_ascii=False).encode()) > 128_000:
                raise Denied("SOURCE_UNAVAILABLE")
            return result
        except Exception as exc:
            raise self._safe_error(exc) from None
