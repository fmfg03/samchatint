"""Operational case schema for SamChat institutional knowledge.

The case model is the bridge between Harvey-style matters and SamChat's real
objects: tournaments, teams, expense reports, budgets, suppliers, and document
incidents. Facts are not treated as verified unless they are explicitly bound to
an evidence id.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .specialist_contract import CASE_TYPES


@dataclass(frozen=True)
class EvidenceRef:
    evidence_id: str
    source_type: str
    title: str
    uri: Optional[str] = None
    excerpt: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def errors(self) -> List[str]:
        errors: List[str] = []
        if not self.evidence_id.strip():
            errors.append("evidence_id_required")
        if not self.source_type.strip():
            errors.append("evidence_source_type_required")
        if not self.title.strip():
            errors.append("evidence_title_required")
        return errors

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CaseRelationship:
    relationship_type: str
    target_case_id: str

    def to_dict(self) -> Dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class OperationalCase:
    case_id: str
    case_type: str
    title: str
    facts: Mapping[str, Any]
    evidence: Tuple[EvidenceRef, ...]
    relationships: Tuple[CaseRelationship, ...] = ()
    status: str = "active"

    def evidence_by_id(self) -> Dict[str, EvidenceRef]:
        return {item.evidence_id: item for item in self.evidence}

    def fact_evidence_id(self, fact_key: str) -> Optional[str]:
        value = self.facts.get(f"{fact_key}_evidence_id")
        return str(value) if value is not None and str(value).strip() else None

    def supports_fact_claim(
        self,
        *,
        fact_key: str,
        value: Any,
        evidence_id: Optional[str],
    ) -> bool:
        """Return true only when value and evidence binding both match.

        This is intentionally fail-closed: merely having evidence in the same
        case is not enough. The exact fact must equal the case value and the
        exact evidence id must match the fact's binding.
        """

        if fact_key not in self.facts:
            return False
        if self.facts.get(fact_key) != value:
            return False
        bound_evidence_id = self.fact_evidence_id(fact_key)
        if not bound_evidence_id or evidence_id != bound_evidence_id:
            return False
        return evidence_id in self.evidence_by_id()

    def errors(self) -> List[str]:
        errors: List[str] = []
        if not self.case_id.strip():
            errors.append("case_id_required")
        if self.case_type not in CASE_TYPES:
            errors.append(f"unsupported_case_type:{self.case_type}")
        if not self.title.strip():
            errors.append("case_title_required")
        if not self.evidence:
            errors.append("case_evidence_required")
        seen: set[str] = set()
        for item in self.evidence:
            if item.evidence_id in seen:
                errors.append(f"duplicate_evidence_id:{item.evidence_id}")
            seen.add(item.evidence_id)
            errors.extend(item.errors())
        evidence_ids = self.evidence_by_id()
        for key, raw_evidence_id in self.facts.items():
            if not key.endswith("_evidence_id"):
                continue
            evidence_id = str(raw_evidence_id)
            if evidence_id not in evidence_ids:
                errors.append(f"unbound_fact_evidence:{key}:{evidence_id}")
        return errors

    def validate(self) -> "OperationalCase":
        errors = self.errors()
        if errors:
            raise ValueError(", ".join(errors))
        return self

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["evidence"] = [item.to_dict() for item in self.evidence]
        payload["relationships"] = [
            relationship.to_dict() for relationship in self.relationships
        ]
        return payload


def validate_operational_case(case: OperationalCase) -> List[str]:
    return case.errors()
