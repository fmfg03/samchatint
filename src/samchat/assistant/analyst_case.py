from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .analyst_intent import AnalystIntent
from .analyst_workbench import AnalystWorkbenchResult


CASE_STATUS_OPEN = "open"
CASE_STATUS_WAITING_CONTEXT = "waiting_context"
CASE_STATUS_ANALYZED = "analyzed"
CASE_STATUS_REVIEWED = "reviewed"
CASE_STATUS_CLOSED = "closed"

CASE_WRITE_POLICY = {
    "product_case_writes_allowed": True,
    "operational_writes_allowed": False,
    "route_execution_allowed": False,
    "provider_activation_allowed": False,
}


@dataclass(frozen=True)
class AnalystCaseVersion:
    version_id: str
    created_at: str
    created_by: str
    status: str
    answer: str
    evidence: List[Dict[str, Any]]
    next_questions: List[str]
    suggested_routes: List[Dict[str, Any]]
    caveats: List[str]
    answer_contract: Dict[str, Any]
    version_number: int = 1
    changed_fields: List[str] = field(default_factory=lambda: ["case_created"])

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AnalystCase:
    case_id: str
    user_id: str
    role: str
    question: str
    analyst_intent: Dict[str, Any]
    status: str
    evidence: List[Dict[str, Any]]
    current_answer: str
    next_questions: List[str]
    suggested_routes: List[Dict[str, Any]]
    caveats: List[str]
    versions: List[AnalystCaseVersion] = field(default_factory=list)
    writes_policy: Dict[str, bool] = field(
        default_factory=lambda: dict(CASE_WRITE_POLICY)
    )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _utc_iso(value: Optional[datetime]) -> str:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).isoformat()


def _case_id(
    *,
    user_id: str,
    role: str,
    question: str,
    intent: AnalystIntent,
) -> str:
    raw = "|".join(
        [
            user_id or "",
            role or "",
            question or "",
            intent.request_id or "",
        ]
    )
    return f"analyst_case_{uuid.uuid5(uuid.NAMESPACE_URL, raw).hex}"


def _version_id(
    *,
    case_id: str,
    result: AnalystWorkbenchResult,
) -> str:
    contract = result.answer_contract or {}
    raw = "|".join(
        [
            case_id,
            result.status or "",
            str(contract.get("version") or ""),
            str(contract.get("status") or ""),
        ]
    )
    return f"analyst_case_version_{uuid.uuid5(uuid.NAMESPACE_URL, raw).hex}"


def _status_for_result(result: AnalystWorkbenchResult) -> str:
    if result.status == "needs_context":
        return CASE_STATUS_WAITING_CONTEXT
    if result.status == "success":
        return CASE_STATUS_ANALYZED
    if result.status == "provider_unavailable":
        return CASE_STATUS_WAITING_CONTEXT
    if result.status == "routed_to_operational":
        return CASE_STATUS_OPEN
    return CASE_STATUS_OPEN


def normalize_suggested_routes(
    routes: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    inert: List[Dict[str, Any]] = []
    for route in routes:
        current = dict(route)
        current["execution_status"] = "not_executed"
        current["writes_enabled"] = False
        blocked = list(current.get("blocked_capabilities") or [])
        for capability in ("writes", "route_execution"):
            if capability not in blocked:
                blocked.append(capability)
        current["blocked_capabilities"] = blocked
        inert.append(current)
    return inert


def build_analyst_case(
    *,
    user_id: str,
    role: str,
    question: str,
    intent: AnalystIntent,
    result: AnalystWorkbenchResult,
    created_at: Optional[datetime] = None,
) -> AnalystCase:
    case_id = _case_id(
        user_id=user_id,
        role=role,
        question=question,
        intent=intent,
    )
    status = _status_for_result(result)
    routes = normalize_suggested_routes(list(result.suggested_routes or []))
    timestamp = _utc_iso(created_at)
    version = AnalystCaseVersion(
        version_id=_version_id(case_id=case_id, result=result),
        created_at=timestamp,
        created_by=user_id,
        status=status,
        answer=result.answer,
        evidence=list(result.evidence or []),
        next_questions=list(result.next_questions or []),
        suggested_routes=routes,
        caveats=list(result.caveats or []),
        answer_contract=dict(result.answer_contract or {}),
    )
    return AnalystCase(
        case_id=case_id,
        user_id=user_id,
        role=role,
        question=question,
        analyst_intent=intent.to_dict(),
        status=status,
        evidence=list(result.evidence or []),
        current_answer=result.answer,
        next_questions=list(result.next_questions or []),
        suggested_routes=routes,
        caveats=list(result.caveats or []),
        versions=[version],
        writes_policy=dict(CASE_WRITE_POLICY),
    )
