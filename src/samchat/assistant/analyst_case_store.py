from __future__ import annotations

import copy
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, cast

from sqlalchemy.orm import Session, selectinload

from .analyst_case import (
    CASE_STATUS_CLOSED,
    CASE_STATUS_REVIEWED,
    AnalystCase,
    AnalystCaseVersion,
    normalize_suggested_routes,
)
from .analyst_case_models import AnalystCaseRecord, AnalystCaseVersionRecord


CASE_MUTABLE_FIELDS: Sequence[str] = (
    "status",
    "evidence",
    "current_answer",
    "next_questions",
    "suggested_routes",
    "caveats",
    "writes_policy",
)


class AnalystCaseStoreError(ValueError):
    """Raised when AnalystCase persistence violates the contract."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def to_utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def json_clone(value: Any) -> Any:
    return copy.deepcopy(value)


def deterministic_changed_fields(
    previous: Dict[str, Any],
    current: Dict[str, Any],
) -> List[str]:
    changed: List[str] = []
    for field_name in sorted(set(previous) | set(current)):
        if previous.get(field_name) != current.get(field_name):
            changed.append(field_name)
    return changed


def validate_case_actor_requirements(
    *,
    status: str,
    updated_by: Optional[str],
    closed_by: Optional[str],
) -> None:
    if status == CASE_STATUS_REVIEWED and not updated_by:
        raise AnalystCaseStoreError(
            "reviewed AnalystCase updates require updated_by"
        )
    if status == CASE_STATUS_CLOSED and not closed_by:
        raise AnalystCaseStoreError(
            "closed AnalystCase updates require closed_by"
        )


def version_id_for(case_id: str, version_number: int) -> str:
    raw = f"{case_id}|{version_number}"
    return f"analyst_case_version_{uuid.uuid5(uuid.NAMESPACE_URL, raw).hex}"


class AnalystCaseStore:
    """SQLAlchemy-backed store for product-internal AnalystCase writes."""

    def __init__(self, session: Session):
        self.session = session

    def create_case(self, case: AnalystCase) -> AnalystCase:
        if self.session.get(AnalystCaseRecord, case.case_id) is not None:
            raise AnalystCaseStoreError(
                f"AnalystCase already exists: {case.case_id}"
            )
        routes = normalize_suggested_routes(json_clone(case.suggested_routes))
        created_at = (
            _parse_iso(case.versions[0].created_at)
            if case.versions
            else utc_now()
        )
        record = AnalystCaseRecord(
            case_id=case.case_id,
            user_id=case.user_id,
            role=case.role,
            question=case.question,
            analyst_intent=json_clone(case.analyst_intent),
            status=case.status,
            evidence=json_clone(case.evidence),
            current_answer=case.current_answer,
            next_questions=json_clone(case.next_questions),
            suggested_routes=routes,
            caveats=json_clone(case.caveats),
            writes_policy=json_clone(case.writes_policy),
            created_at=created_at,
            updated_at=created_at,
        )
        self.session.add(record)

        versions = list(case.versions or [])
        if not versions:
            raise AnalystCaseStoreError(
                "AnalystCase requires an initial version"
            )
        for position, version in enumerate(versions, start=1):
            self.session.add(
                _version_record_from_case_version(
                    case_id=case.case_id,
                    version=version,
                    version_number=version.version_number or position,
                )
            )
        self.session.flush()
        return self.get_case(case.case_id)  # type: ignore[return-value]

    def get_case(self, case_id: str) -> Optional[AnalystCase]:
        record = (
            self.session.query(AnalystCaseRecord)
            .options(selectinload(AnalystCaseRecord.versions))
            .populate_existing()
            .filter(AnalystCaseRecord.case_id == case_id)
            .one_or_none()
        )
        if record is None:
            return None
        return rehydrate_analyst_case(record)

    def update_case(
        self,
        case_id: str,
        *,
        status: Optional[str] = None,
        current_answer: Optional[str] = None,
        evidence: Optional[List[Dict[str, Any]]] = None,
        next_questions: Optional[List[str]] = None,
        suggested_routes: Optional[List[Dict[str, Any]]] = None,
        caveats: Optional[List[str]] = None,
        answer_contract: Optional[Dict[str, Any]] = None,
        updated_by: Optional[str] = None,
        closed_by: Optional[str] = None,
        updated_at: Optional[datetime] = None,
    ) -> AnalystCase:
        record = (
            self.session.query(AnalystCaseRecord)
            .options(selectinload(AnalystCaseRecord.versions))
            .filter(AnalystCaseRecord.case_id == case_id)
            .one_or_none()
        )
        if record is None:
            raise AnalystCaseStoreError(f"AnalystCase not found: {case_id}")

        next_status = status if status is not None else _as_str(record.status)
        validate_case_actor_requirements(
            status=next_status,
            updated_by=updated_by,
            closed_by=closed_by,
        )

        previous_payload = _mutable_payload(record)
        next_payload = dict(previous_payload)
        if status is not None:
            next_payload["status"] = status
        if current_answer is not None:
            next_payload["current_answer"] = current_answer
        if evidence is not None:
            next_payload["evidence"] = json_clone(evidence)
        if next_questions is not None:
            next_payload["next_questions"] = json_clone(next_questions)
        if suggested_routes is not None:
            next_payload["suggested_routes"] = normalize_suggested_routes(
                json_clone(suggested_routes)
            )
        if caveats is not None:
            next_payload["caveats"] = json_clone(caveats)

        changed_fields = deterministic_changed_fields(
            previous_payload,
            next_payload,
        )
        timestamp = updated_at or utc_now()
        actor = closed_by or updated_by
        if not actor:
            actor = _as_str(record.user_id)

        for field_name, value in next_payload.items():
            setattr(record, field_name, value)
        setattr(record, "updated_at", timestamp)
        setattr(record, "updated_by", updated_by or closed_by)
        if next_status == CASE_STATUS_CLOSED:
            setattr(record, "closed_at", timestamp)
            setattr(record, "closed_by", closed_by)

        next_version_number = _next_version_number(record.versions)
        self.session.add(
            AnalystCaseVersionRecord(
                version_id=version_id_for(case_id, next_version_number),
                case_id=case_id,
                version_number=next_version_number,
                created_at=timestamp,
                created_by=actor,
                status=_as_str(next_payload["status"]),
                answer=next_payload["current_answer"],
                evidence=json_clone(next_payload["evidence"]),
                next_questions=json_clone(next_payload["next_questions"]),
                suggested_routes=json_clone(next_payload["suggested_routes"]),
                caveats=json_clone(next_payload["caveats"]),
                answer_contract=json_clone(answer_contract or {}),
                changed_fields=changed_fields,
            )
        )
        self.session.flush()
        return self.get_case(case_id)  # type: ignore[return-value]


def rehydrate_analyst_case(record: AnalystCaseRecord) -> AnalystCase:
    versions = [
        AnalystCaseVersion(
            version_id=version.version_id,
            created_at=to_utc_iso(version.created_at),
            created_by=version.created_by,
            status=_as_str(version.status),
            answer=_as_str(version.answer),
            evidence=json_clone(version.evidence or []),
            next_questions=json_clone(version.next_questions or []),
            suggested_routes=normalize_suggested_routes(
                json_clone(version.suggested_routes or [])
            ),
            caveats=json_clone(version.caveats or []),
            answer_contract=json_clone(version.answer_contract or {}),
            version_number=_as_int(version.version_number),
            changed_fields=list(version.changed_fields or []),
        )
        for version in sorted(
            record.versions,
            key=lambda item: item.version_number,
        )
    ]
    return AnalystCase(
        case_id=_as_str(record.case_id),
        user_id=_as_str(record.user_id),
        role=_as_str(record.role),
        question=_as_str(record.question),
        analyst_intent=json_clone(record.analyst_intent or {}),
        status=_as_str(record.status),
        evidence=json_clone(record.evidence or []),
        current_answer=_as_str(record.current_answer),
        next_questions=json_clone(record.next_questions or []),
        suggested_routes=normalize_suggested_routes(
            json_clone(record.suggested_routes or [])
        ),
        caveats=json_clone(record.caveats or []),
        versions=versions,
        writes_policy=json_clone(record.writes_policy or {}),
    )


def _version_record_from_case_version(
    *,
    case_id: str,
    version: AnalystCaseVersion,
    version_number: int,
) -> AnalystCaseVersionRecord:
    return AnalystCaseVersionRecord(
        version_id=version.version_id,
        case_id=case_id,
        version_number=version_number,
        created_at=_parse_iso(version.created_at),
        created_by=version.created_by,
        status=version.status,
        answer=version.answer,
        evidence=json_clone(version.evidence),
        next_questions=json_clone(version.next_questions),
        suggested_routes=normalize_suggested_routes(
            json_clone(version.suggested_routes)
        ),
        caveats=json_clone(version.caveats),
        answer_contract=json_clone(version.answer_contract),
        changed_fields=list(version.changed_fields or ["case_created"]),
    )


def _mutable_payload(record: AnalystCaseRecord) -> Dict[str, Any]:
    return {
        "status": _as_str(record.status),
        "evidence": json_clone(record.evidence or []),
        "current_answer": _as_str(record.current_answer),
        "next_questions": json_clone(record.next_questions or []),
        "suggested_routes": normalize_suggested_routes(
            json_clone(record.suggested_routes or [])
        ),
        "caveats": json_clone(record.caveats or []),
        "writes_policy": json_clone(record.writes_policy or {}),
    }


def _next_version_number(
    versions: Sequence[AnalystCaseVersionRecord],
) -> int:
    if not versions:
        return 1
    return max(_as_int(version.version_number) for version in versions) + 1


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _as_str(value: Any) -> str:
    return cast(str, value)


def _as_int(value: Any) -> int:
    return cast(int, value)
