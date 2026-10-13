"""Owner adapters for exact delegation and canonical budget snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Mapping

from .contracts import Principal, ReadError
from .delegation import Delegation, DelegationFile
from .postgres import ScopeGrant


class DelegatedScopeOwner:
    """Resolve only the bridge-bound grant; never infer visibility from role."""

    def __init__(self, source: DelegationFile, transport_binding: str) -> None:
        self.source = source
        self.transport_binding = transport_binding

    def delegation(self) -> Delegation:
        return self.source.resolve(self.transport_binding)

    def resolve(self, principal: Principal) -> ScopeGrant:
        delegation = self.delegation()
        if (
            principal.actor_binding != delegation.actor_binding
            or principal.installation != delegation.installation
            or principal.generation != delegation.generation
            or principal.tournaments != delegation.tournament_ids
            or principal.expires_at != delegation.expires_at
        ):
            raise ReadError("SCOPE_UNPROVEN")
        return ScopeGrant(
            actor_uuid=delegation.actor_uuid,
            document_ids=delegation.document_ids,
            budget_version_ids=delegation.budget_version_ids,
            installation=delegation.installation,
            generation=delegation.generation,
            owner_version=delegation.owner_version,
        )


@dataclass(frozen=True)
class BudgetRequest:
    tournament_id: str
    version_id: str
    edition_year: int
    date_from: date
    date_to: date


class CanonicalBudgetOwner:
    """Call an injected budgets-owned projection with strict, no-DDL arguments.

    The injected callable is composition code around
    ``build_budget_snapshot``. It owns the dedicated read-only session factory;
    this class owns scope binding and output minimisation.
    """

    OUTPUT_KEYS = frozenset(
        {
            "tournament_id",
            "version_id",
            "edition_year",
            "period_start",
            "period_end",
            "currency",
            "approved_amount",
            "consumed_amount",
            "committed_amount",
            "available_amount",
            "coverage",
        }
    )

    def __init__(self, build: Callable[..., Mapping[str, Any]]) -> None:
        self.build = build

    def snapshot(
        self, principal: Principal, grant: ScopeGrant, request: BudgetRequest
    ) -> dict[str, Any]:
        if (
            request.tournament_id not in principal.tournaments
            or request.version_id not in grant.budget_version_ids
        ):
            raise ReadError("NOT_FOUND_OR_NOT_VISIBLE")
        raw = self.build(
            tournament_id=request.tournament_id,
            version_id=request.version_id,
            edition_year=request.edition_year,
            date_from=request.date_from,
            date_to=request.date_to,
            ensure_schema=False,
            strict_tournament_scope=True,
            executive_read=True,
        )
        if not isinstance(raw, Mapping) or set(raw) != self.OUTPUT_KEYS:
            raise ReadError("SOURCE_VERSION_UNPROVEN")
        result = dict(raw)
        if (
            result["tournament_id"] != request.tournament_id
            or result["version_id"] != request.version_id
            or result["edition_year"] != request.edition_year
        ):
            raise ReadError("SOURCE_VERSION_UNPROVEN")
        return result


class DenyBudgetOwner:
    def snapshot(
        self, principal: Principal, grant: ScopeGrant, request: BudgetRequest
    ) -> dict[str, Any]:
        del principal, grant, request
        raise ReadError("CANONICAL_BUDGET_OWNER_UNBOUND")
