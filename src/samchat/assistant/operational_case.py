"""Operational-case primitives for SamChat institutional knowledge."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

SUPPORTED_CASE_TYPES = {
    "tournament",
    "team",
    "player_validation",
    "document_incident",
    "money_request",
    "expense_report",
    "budget",
    "supplier",
}


@dataclass(frozen=True)
class EvidenceRef:
    """A source-backed reference to a document, database row, or event."""

    evidence_id: str
    source_type: str
    source_ref: str
    summary: str = ""
    fields: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "EvidenceRef":
        return cls(
            evidence_id=str(payload.get("evidence_id") or "").strip(),
            source_type=str(payload.get("source_type") or "").strip(),
            source_ref=str(payload.get("source_ref") or "").strip(),
            summary=str(payload.get("summary") or "").strip(),
            fields=dict(payload.get("fields") or {}),
        ).validate()

    def validate(self) -> "EvidenceRef":
        if not self.evidence_id:
            raise ValueError("evidence_id is required")
        if not self.source_type:
            raise ValueError(f"source_type is required for {self.evidence_id}")
        if not self.source_ref:
            raise ValueError(f"source_ref is required for {self.evidence_id}")
        return self

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CaseRelationship:
    """Typed edge to another operational case."""

    relationship_type: str
    target_case_id: str

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "CaseRelationship":
        return cls(
            relationship_type=str(payload.get("relationship_type") or "").strip(),
            target_case_id=str(payload.get("target_case_id") or "").strip(),
        ).validate()

    def validate(self) -> "CaseRelationship":
        if not self.relationship_type:
            raise ValueError("relationship_type is required")
        if not self.target_case_id:
            raise ValueError("target_case_id is required")
        return self

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OperationalCase:
    """Portable envelope for SamChat institutional/operational memory."""

    case_id: str
    case_type: str
    title: str
    summary: str
    facts: dict[str, Any] = field(default_factory=dict)
    evidence: tuple[EvidenceRef, ...] = ()
    relationships: tuple[CaseRelationship, ...] = ()
    tags: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "OperationalCase":
        return cls(
            case_id=str(payload.get("case_id") or "").strip(),
            case_type=str(payload.get("case_type") or "").strip(),
            title=str(payload.get("title") or "").strip(),
            summary=str(payload.get("summary") or "").strip(),
            facts=dict(payload.get("facts") or {}),
            evidence=tuple(
                EvidenceRef.from_dict(item) for item in payload.get("evidence", [])
            ),
            relationships=tuple(
                CaseRelationship.from_dict(item)
                for item in payload.get("relationships", [])
            ),
            tags=tuple(
                str(item).strip() for item in payload.get("tags", []) if str(item).strip()
            ),
        ).validate()

    def validate(self) -> "OperationalCase":
        if not self.case_id:
            raise ValueError("case_id is required")
        if self.case_type not in SUPPORTED_CASE_TYPES:
            raise ValueError(f"unsupported case_type: {self.case_type}")
        if not self.title:
            raise ValueError(f"title is required for {self.case_id}")
        evidence_ids = {item.evidence_id for item in self.evidence}
        for key, value in self.facts.items():
            if key.endswith("_evidence_id") and value and str(value) not in evidence_ids:
                raise ValueError(
                    f"fact {key} references missing evidence {value} in {self.case_id}"
                )
        return self

    def evidence_by_id(self) -> dict[str, EvidenceRef]:
        return {item.evidence_id: item for item in self.evidence}

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "case_type": self.case_type,
            "title": self.title,
            "summary": self.summary,
            "facts": dict(self.facts),
            "evidence": [item.to_dict() for item in self.evidence],
            "relationships": [item.to_dict() for item in self.relationships],
            "tags": list(self.tags),
        }


def load_operational_cases(payload: str | Path | Sequence[Mapping[str, Any]]) -> tuple[OperationalCase, ...]:
    if isinstance(payload, (str, Path)):
        payload = json.loads(Path(payload).read_text(encoding="utf-8"))
    return tuple(OperationalCase.from_dict(item) for item in payload)
