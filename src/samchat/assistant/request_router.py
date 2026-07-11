from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional

from .action_router import supported_read_actions
from .request_intent import OperationalRequestIntent


@dataclass(frozen=True)
class RequestRoute:
    type: str
    canonical_action: Optional[str]
    requires_provider: bool
    requires_confirmation: bool
    read_only: bool
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def route_request(intent: OperationalRequestIntent) -> RequestRoute:
    if intent.domain == "unknown":
        return RequestRoute(
            type="clarification",
            canonical_action=None,
            requires_provider=False,
            requires_confirmation=False,
            read_only=True,
            reason="unsupported_or_ambiguous_request",
        )

    if intent.missing_fields:
        return RequestRoute(
            type="clarification",
            canonical_action=None,
            requires_provider=False,
            requires_confirmation=False,
            read_only=True,
            reason="missing_required_fields",
        )

    if intent.domain == "finance" and intent.intent == "compare":
        return RequestRoute(
            type="read_only_report",
            canonical_action="finance.read_only_comparison",
            requires_provider=False,
            requires_confirmation=False,
            read_only=True,
            reason="deterministic_finance_comparison",
        )

    canonical_by_domain = {
        "finance": "executive.realtime_report",
        "cfdi": "receipts.cfdi_matching_overview",
        "payments": "receipts.pending_payment_overview",
        "tournament": "operations.tournament_soul_snapshot",
        "executive": "executive.planner_snapshot",
    }
    canonical = canonical_by_domain.get(intent.domain)
    if canonical and canonical in supported_read_actions():
        return RequestRoute(
            type="read_only_report",
            canonical_action=canonical,
            requires_provider=False,
            requires_confirmation=False,
            read_only=True,
            reason=f"{intent.domain}_{intent.intent}_read_only_route",
        )
    return RequestRoute(
        type="unsupported",
        canonical_action=canonical,
        requires_provider=False,
        requires_confirmation=False,
        read_only=True,
        reason="safe_read_only_route_unavailable",
    )


def build_request_contract(
    *,
    intent: OperationalRequestIntent,
    route: RequestRoute,
) -> Dict[str, Any]:
    return {
        "request_id": intent.request_id,
        "domain": intent.domain,
        "intent": intent.intent,
        "confidence": intent.confidence,
        "slots": intent.slots,
        "missing_fields": intent.missing_fields,
        "route": {
            "type": route.type,
            "canonical_action": route.canonical_action,
            "requires_provider": route.requires_provider,
            "requires_confirmation": route.requires_confirmation,
        },
        "safety": {
            "read_only": route.read_only,
            "write_requested": False,
            "provider_bypassed": not route.requires_provider,
            "fail_closed": True,
        },
    }
