"""Unwired Direction read adapter over existing canonical owners.

No live imports, connection, credential, write, or authorization fallback. The
composition root must bind the named owners to the existing Direction functions
and provide a current local employee plus an exact employee/scope organization
mapping. Those production integrations remain unproven; none is inferred here.
Canon unchanged: this adapter consumes the established read authority only.
"""

from __future__ import annotations

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

    session must be a read-only, request-owned database session; composition
    must provide a consistent authorization/read snapshot. No session is opened
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
                "samchat.finance_platform.service.build_finance_source_snapshot",
                "samchat.ar.service.build_ar_read_model",
                "bank_balance_source_unavailable",
            ]
        },
        "gaps": {"type": "array", "maxItems": 100, "items": _TEXT},
        "validation_status": {"const": "pending_business_validation"},
        "coverage": _object(
            {
                key: {"type": "integer", "minimum": 0, "maximum": 1000}
                for key in ("covered", "total")
            }
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
            "const": "sequential_reads_with_individual_cuts_not_cross_store_atomic"
        },
        "business_acceptance": {"const": "pending"},
    }
)
_OUTPUT_VALIDATOR = Draft202012Validator(DIRECTION_OUTPUT_SCHEMA)


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
    return {
        "schema": "samchat.private.direction.read.v1",
        "read_only": True,
        "snapshot_id": snapshot["snapshot_id"],
        "edition_year": snapshot["edition_year"],
        "as_of": snapshot["as_of"],
        "period": {k: snapshot["period"][k] for k in ("start", "end")},
        "scope": {
            k: snapshot["scope"][k]
            for k in ("portfolio_ids", "portfolio_id", "tournament_id")
        },
        "tournament_ids": list(snapshot["tournament_ids"]),
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
            owners = self._owners
            assigned = await owners.assigned_portfolios(context.session, employee)
            if not assigned:
                raise Denied("FORBIDDEN")
            superadmin = owners.is_superadmin(employee)
            args = dict(
                actor=identity.actor_id,
                superadmin=superadmin,
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
            snapshot, final_scope = await owners.build_home(
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
                    k: scope[k]
                    for k in ("portfolio_ids", "portfolio_id", "tournament_id")
                }
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
        except Denied as exc:
            allowed = {
                "UNAUTHENTICATED",
                "FORBIDDEN",
                "ORGANIZATION_UNPROVEN",
                "SCOPE_CHANGED",
            }
            raise Denied(
                str(exc) if str(exc) in allowed else "SOURCE_UNAVAILABLE"
            ) from None
        except Exception as exc:
            # Canonical access exceptions and source errors never expose details.
            if (
                isinstance(exc, self._owners.access_errors)
                and getattr(exc, "status_code", 403) == 403
            ):
                raise Denied("FORBIDDEN") from None
            raise Denied("SOURCE_UNAVAILABLE") from None
