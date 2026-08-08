"""Deterministic v0 specialist agents for RQF-ASSISTANT-052."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping, Sequence

from .operational_case import OperationalCase
from .specialist_task import AgentVisibleTask

READ_ONLY = "READ_ONLY"
PROPOSE_ONLY = "PROPOSE_ONLY"


@dataclass(frozen=True)
class AgentContract:
    agent_id: str
    scope: str
    tools_allowed: tuple[str, ...]
    case_types: tuple[str, ...]
    input_artifacts: tuple[str, ...]
    output_artifacts: tuple[str, ...]
    authority_boundary: str
    rubric: tuple[str, ...]
    regression_set: tuple[str, ...]

    def validate_tool(self, tool_name: str) -> None:
        if tool_name not in self.tools_allowed:
            raise PermissionError(
                f"tool {tool_name!r} is not allowed for agent {self.agent_id}"
            )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AgentArtifact:
    artifact_type: str
    title: str
    content: dict[str, Any]
    evidence_refs: tuple[str, ...] = ()
    authority_boundary: str = PROPOSE_ONLY

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_type": self.artifact_type,
            "title": self.title,
            "content": dict(self.content),
            "evidence_refs": list(self.evidence_refs),
            "authority_boundary": self.authority_boundary,
        }


@dataclass(frozen=True)
class AgentRunResult:
    agent_id: str
    task_id: str
    artifacts: tuple[AgentArtifact, ...]
    trace: tuple[dict[str, Any], ...]
    unsupported_facts: tuple[str, ...] = ()
    side_effects_detected: int = 0

    @property
    def authority_boundary(self) -> str:
        boundaries = {artifact.authority_boundary for artifact in self.artifacts}
        if len(boundaries) == 1:
            return next(iter(boundaries))
        if PROPOSE_ONLY in boundaries:
            return PROPOSE_ONLY
        return READ_ONLY

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "task_id": self.task_id,
            "artifacts": [item.to_dict() for item in self.artifacts],
            "trace": list(self.trace),
            "unsupported_facts": list(self.unsupported_facts),
            "side_effects_detected": self.side_effects_detected,
            "authority_boundary": self.authority_boundary,
        }


STOPWORDS = {
    "the", "and", "for", "with", "from", "into", "this", "that", "task",
    "find", "verify", "prepare", "proposal", "only", "case", "request",
    "precedent", "evidence", "operational", "samchat", "para", "con",
    "del", "las", "los", "una", "uno", "por", "que", "ref", "system",
    "agent", "finance", "knowledge", "verifier", "orchestrator",
}
PERSON_FACT_TOKENS = {"birthplace", "lugar_nacimiento", "place_of_birth"}


def _task_query_text(task: AgentVisibleTask) -> str:
    return " ".join([task.title, task.instructions, " ".join(task.tags)])


def _query_tokens(task: AgentVisibleTask) -> tuple[str, ...]:
    tokens = []
    for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]{2,}", _task_query_text(task).casefold()):
        if token not in STOPWORDS:
            tokens.append(token)
    return tuple(dict.fromkeys(tokens))


def _case_text(case: OperationalCase) -> str:
    return " ".join(
        [
            case.case_id,
            case.case_type,
            case.title,
            case.summary,
            " ".join(case.tags),
            " ".join(str(v) for v in case.facts.values()),
            " ".join(ev.summary for ev in case.evidence),
        ]
    ).casefold()


def _case_score(case: OperationalCase, tokens: Sequence[str]) -> int:
    text = _case_text(case)
    return sum(1 for token in tokens if token in text)


def _matches_keywords(case: OperationalCase, keywords: Iterable[str]) -> bool:
    text = _case_text(case)
    return all(
        str(keyword).strip().casefold() in text
        for keyword in keywords
        if str(keyword).strip()
    )


def _select_cases_from_task(
    task: AgentVisibleTask,
    cases: Sequence[OperationalCase],
    *,
    enforce_case_type: bool = True,
) -> list[OperationalCase]:
    candidates = [case for case in cases if not enforce_case_type or case.case_type == task.case_type]
    tokens = _query_tokens(task)
    if not tokens:
        return candidates
    scored = [(case, _case_score(case, tokens)) for case in candidates]
    selected = [case for case, score in sorted(scored, key=lambda item: (-item[1], item[0].case_id)) if score > 0]
    return selected or candidates


def _explicit_evidence_for_fact(case: OperationalCase, fact_key: str) -> str | None:
    explicit = case.facts.get(f"{fact_key}_evidence_id")
    if explicit and str(explicit) in case.evidence_by_id():
        return str(explicit)
    return None


def _claims_for_case(case: OperationalCase) -> list[dict[str, Any]]:
    claims: list[dict[str, Any]] = []
    for fact_key, value in case.facts.items():
        if fact_key.endswith("_evidence_id"):
            continue
        claims.append(
            {
                "case_id": case.case_id,
                "fact_key": fact_key,
                "value": value,
                "evidence_id": _explicit_evidence_for_fact(case, fact_key),
                "status": "claimed",
            }
        )
    return claims


def _claims_from_artifacts(artifacts: Sequence[AgentArtifact]) -> list[dict[str, Any]]:
    claims: list[dict[str, Any]] = []
    for artifact in artifacts:
        content = artifact.content
        for claim in content.get("claims", []) or []:
            claims.append(dict(claim))
        for case in content.get("cases", []) or []:
            case_obj = OperationalCase.from_dict(case)
            claims.extend(_claims_for_case(case_obj))
    return claims


def _selected_case_ids_from_artifacts(artifacts: Sequence[AgentArtifact]) -> set[str]:
    case_ids: set[str] = set()
    for artifact in artifacts:
        content = artifact.content
        for case_id in content.get("case_ids", []) or []:
            case_ids.add(str(case_id))
        for claim in content.get("claims", []) or []:
            if claim.get("case_id"):
                case_ids.add(str(claim["case_id"]))
        for case in content.get("cases", []) or []:
            if case.get("case_id"):
                case_ids.add(str(case["case_id"]))
    return case_ids


def _supported_facts_from_artifacts(artifacts: Sequence[AgentArtifact]) -> dict[str, dict[str, Any]]:
    supported: dict[str, dict[str, Any]] = {}
    for artifact in artifacts:
        for fact in artifact.content.get("supported_facts", []) or []:
            case_id = str(fact.get("case_id") or "")
            fact_key = str(fact.get("fact_key") or "")
            if case_id and fact_key:
                supported.setdefault(case_id, {})[fact_key] = fact.get("value")
    return supported


class InstitutionalKnowledgeAgent:
    contract = AgentContract(
        agent_id="institutional_knowledge",
        scope="Find operational precedents and source-backed evidence across cases.",
        tools_allowed=("read_operational_cases", "search_precedents"),
        case_types=(
            "tournament",
            "team",
            "player_validation",
            "document_incident",
            "money_request",
            "expense_report",
            "budget",
            "supplier",
        ),
        input_artifacts=("operational_case",),
        output_artifacts=("precedent_set",),
        authority_boundary=READ_ONLY,
        rubric=("retrieval_correctness", "evidence_correctness", "precision"),
        regression_set=("RQF-ASSISTANT-052",),
    )

    def run(
        self,
        task: AgentVisibleTask,
        cases: tuple[OperationalCase, ...],
        input_artifacts: Sequence[AgentArtifact] = (),
    ) -> AgentRunResult:
        self.contract.validate_tool("read_operational_cases")
        self.contract.validate_tool("search_precedents")
        selected = _select_cases_from_task(task, cases)
        claims = [claim for case in selected for claim in _claims_for_case(case)]
        evidence_refs = tuple(ev.evidence_id for case in selected for ev in case.evidence)
        artifact = AgentArtifact(
            artifact_type="precedent_set",
            title=f"Precedents for {task.task_id}",
            content={
                "case_ids": [case.case_id for case in selected],
                "count": len(selected),
                "cases": [case.to_dict() for case in selected],
                "claims": claims,
            },
            evidence_refs=evidence_refs,
            authority_boundary=READ_ONLY,
        )
        return AgentRunResult(
            agent_id=self.contract.agent_id,
            task_id=task.task_id,
            artifacts=(artifact,),
            trace=(
                {"tool": "read_operational_cases", "case_count": len(cases)},
                {"tool": "search_precedents", "selected": len(selected)},
                {"handoff": "claims", "count": len(claims)},
            ),
        )


class EvidenceVerifierAgent:
    contract = AgentContract(
        agent_id="evidence_verifier",
        scope="Verify facts, people, documents, dates, amounts, and provenance.",
        tools_allowed=("read_operational_cases", "verify_evidence"),
        case_types=(
            "tournament",
            "team",
            "player_validation",
            "document_incident",
            "money_request",
            "expense_report",
            "budget",
            "supplier",
        ),
        input_artifacts=("operational_case", "agent_artifact"),
        output_artifacts=("verification_report",),
        authority_boundary=READ_ONLY,
        rubric=("factual_correctness", "provenance_correctness", "unsupported_fact_rejection"),
        regression_set=("RQF-ASSISTANT-052",),
    )

    def run(
        self,
        task: AgentVisibleTask,
        cases: tuple[OperationalCase, ...],
        input_artifacts: Sequence[AgentArtifact] = (),
    ) -> AgentRunResult:
        self.contract.validate_tool("read_operational_cases")
        self.contract.validate_tool("verify_evidence")
        case_by_id = {case.case_id: case for case in cases}
        claims = _claims_from_artifacts(input_artifacts)
        if not claims:
            selected = _select_cases_from_task(task, cases)
            claims = [claim for case in selected for claim in _claims_for_case(case)]
        selected_case_ids = {str(claim["case_id"]) for claim in claims if claim.get("case_id")}
        selected_cases = [case_by_id[case_id] for case_id in sorted(selected_case_ids) if case_id in case_by_id]

        supported: list[dict[str, Any]] = []
        unverified: list[dict[str, Any]] = []
        evidence_refs: list[str] = []
        for claim in claims:
            case = case_by_id.get(str(claim.get("case_id") or ""))
            fact_key = str(claim.get("fact_key") or "")
            evidence_id = str(claim.get("evidence_id") or "")
            bound_evidence = str(case.facts.get(f"{fact_key}_evidence_id") or "") if case and fact_key else ""
            source_value = case.facts.get(fact_key) if case and fact_key else None
            supported_claim = (
                bool(case)
                and bool(fact_key)
                and fact_key in case.facts
                and claim.get("value") == source_value
                and bool(evidence_id)
                and evidence_id == bound_evidence
                and evidence_id in case.evidence_by_id()
            )
            if supported_claim:
                supported.append({**claim, "evidence_id": evidence_id, "status": "supported"})
                evidence_refs.append(evidence_id)
            else:
                unverified.append({**claim, "status": "unverified", "reason": "claim_value_or_evidence_binding_mismatch"})

        query_tokens = _query_tokens(task)
        known_fact_tokens = {str(claim.get("fact_key") or "").casefold() for claim in claims}
        for token in query_tokens:
            if token in PERSON_FACT_TOKENS and token not in known_fact_tokens:
                unverified.append(
                    {
                        "case_id": None,
                        "fact_key": token,
                        "value": None,
                        "evidence_id": None,
                        "status": "unverified",
                        "reason": "not_present_in_sources",
                    }
                )

        artifact = AgentArtifact(
            artifact_type="verification_report",
            title=f"Verification for {task.task_id}",
            content={
                "supported_facts": supported,
                "unverified_facts": unverified,
                "unsupported_facts": [item["fact_key"] for item in unverified],
                "selected_cases": [case.to_dict() for case in selected_cases],
            },
            evidence_refs=tuple(dict.fromkeys(evidence_refs)),
            authority_boundary=READ_ONLY,
        )
        return AgentRunResult(
            agent_id=self.contract.agent_id,
            task_id=task.task_id,
            artifacts=(artifact,),
            trace=(
                {"tool": "read_operational_cases", "case_count": len(cases)},
                {"tool": "verify_evidence", "supported": len(supported), "unverified": len(unverified)},
                {"handoff": "supported_facts", "count": len(supported)},
            ),
            unsupported_facts=tuple(item["fact_key"] for item in unverified),
        )


class FinanceAgent:
    contract = AgentContract(
        agent_id="finance",
        scope="Prepare finance classifications and accounting proposals from evidence.",
        tools_allowed=("read_operational_cases", "classify_finance", "propose_finance_artifact"),
        case_types=("money_request", "expense_report", "budget", "supplier"),
        input_artifacts=("operational_case", "cfdi", "accounting_catalog", "verification_report"),
        output_artifacts=("finance_proposal",),
        authority_boundary=PROPOSE_ONLY,
        rubric=("classification_correctness", "accounting_evidence", "authority_boundary"),
        regression_set=("RQF-ASSISTANT-052",),
    )

    def run(
        self,
        task: AgentVisibleTask,
        cases: tuple[OperationalCase, ...],
        input_artifacts: Sequence[AgentArtifact] = (),
    ) -> AgentRunResult:
        self.contract.validate_tool("read_operational_cases")
        self.contract.validate_tool("classify_finance")
        self.contract.validate_tool("propose_finance_artifact")
        case_by_id = {case.case_id: case for case in cases}
        supported_by_case = _supported_facts_from_artifacts(input_artifacts)
        if supported_by_case:
            selected = [case_by_id[case_id] for case_id in sorted(supported_by_case) if case_id in case_by_id]
        else:
            selected = _select_cases_from_task(task, cases)
            supported_by_case = {
                case.case_id: {
                    claim["fact_key"]: claim["value"]
                    for claim in _claims_for_case(case)
                    if claim.get("evidence_id")
                }
                for case in selected
            }

        proposals = []
        evidence_refs: list[str] = []
        for case in selected:
            supported = supported_by_case.get(case.case_id, {})
            debit = supported.get("debit_account") or supported.get("expense_account") or supported.get("receivable_account")
            credit = supported.get("credit_account") or supported.get("counterparty_account") or supported.get("income_account")
            label = (
                f"REF {supported.get('operations_ref')} / {supported.get('system_ref')}"
                if supported.get("operations_ref") and supported.get("system_ref")
                else None
            )
            proposals.append(
                {
                    "case_id": case.case_id,
                    "amount": supported.get("amount"),
                    "debit_account": debit,
                    "credit_account": credit,
                    "cfdi_uuid": supported.get("cfdi_uuid"),
                    "reference_label": label,
                    "proposal_only": True,
                }
            )
            for fact_key in supported:
                evidence_id = _explicit_evidence_for_fact(case, fact_key)
                if evidence_id:
                    evidence_refs.append(evidence_id)
        artifact = AgentArtifact(
            artifact_type="finance_proposal",
            title=f"Finance proposal for {task.task_id}",
            content={"proposals": proposals, "proposal_count": len(proposals)},
            evidence_refs=tuple(dict.fromkeys(evidence_refs)),
            authority_boundary=PROPOSE_ONLY,
        )
        return AgentRunResult(
            agent_id=self.contract.agent_id,
            task_id=task.task_id,
            artifacts=(artifact,),
            trace=(
                {"tool": "read_operational_cases", "case_count": len(cases)},
                {"tool": "classify_finance", "selected": len(selected)},
                {"tool": "propose_finance_artifact", "side_effects": 0},
            ),
        )


def default_agent_contracts() -> dict[str, AgentContract]:
    agents = (InstitutionalKnowledgeAgent(), EvidenceVerifierAgent(), FinanceAgent())
    return {agent.contract.agent_id: agent.contract for agent in agents}


def default_agents() -> dict[str, Any]:
    return {
        "institutional_knowledge": InstitutionalKnowledgeAgent(),
        "evidence_verifier": EvidenceVerifierAgent(),
        "finance": FinanceAgent(),
    }
