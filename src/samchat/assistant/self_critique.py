from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional


SAFE_TO_ANSWER = "SAFE_TO_ANSWER"
NEEDS_REVISION = "NEEDS_REVISION"
BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class CritiqueIssue:
    code: str
    severity: str
    detail: str
    required_edit: str

    def to_trace(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SelfCritiqueResult:
    passed: bool
    issues: List[CritiqueIssue] = field(default_factory=list)
    required_edits: List[str] = field(default_factory=list)
    safe_wording_suggestions: List[str] = field(default_factory=list)
    final_release_decision: str = SAFE_TO_ANSWER

    def to_trace(self) -> Dict[str, Any]:
        return asdict(self)


BLOCKING_PHRASES = {
    "unsupported_execution_claim": (
        "executed payment",
        "payment completed",
        "paid payroll",
        "pago ejecutado",
        "pagué",
        "ya pagué",
    ),
    "writes_or_runtime_implied": (
        "writes are enabled",
        "write execution is enabled",
        "general runtime is enabled",
        "runtime is enabled",
    ),
    "external_side_effect_implied": (
        "sent whatsapp",
        "sent telegram",
        "sent email",
        "webhook delivered",
        "notificación enviada",
        "correo enviado",
    ),
    "unsafe_write_language": (
        "i updated",
        "i created the record",
        "i deleted",
        "ya actualicé",
        "ya registré",
        "ya eliminé",
    ),
}

REVISION_PHRASES = {
    "provider_isolation_overclaim": (
        "all providers " + "isolated",
        "universal provider " + "isolation",
        "every provider is isolated",
    ),
    "production_evidence_overclaim": (
        "production proves",
        "live db confirms",
        "verified in production",
    ),
    "soak_rerun_overclaim": (
        "soak " + "rerun",
        "reran the soak",
    ),
    "overconfidence": (
        "certainly",
        "guaranteed",
        "without any doubt",
        "definitivamente",
    ),
}


def _has_any(text: str, phrases: Iterable[str]) -> bool:
    return any(phrase in text for phrase in phrases)


def _evidence_kinds(evidence: Optional[Iterable[Mapping[str, Any]]]) -> List[str]:
    kinds = []
    for item in evidence or []:
        if isinstance(item, Mapping):
            kinds.append(str(item.get("kind") or item.get("source") or "evidence"))
    return kinds


def _issue(code: str, severity: str, detail: str, required_edit: str) -> CritiqueIssue:
    return CritiqueIssue(
        code=code,
        severity=severity,
        detail=detail,
        required_edit=required_edit,
    )


def critique_assistant_draft(
    *,
    draft: str,
    evidence: Optional[Iterable[Mapping[str, Any]]] = None,
    proposal_boundary: Optional[Mapping[str, Any]] = None,
    missing_evidence: Optional[Iterable[str]] = None,
) -> SelfCritiqueResult:
    text = (draft or "").strip()
    lowered = text.lower()
    issues: List[CritiqueIssue] = []
    evidence_labels = _evidence_kinds(evidence)
    missing = [str(item) for item in missing_evidence or [] if str(item).strip()]

    for code, phrases in BLOCKING_PHRASES.items():
        if _has_any(lowered, phrases):
            issues.append(
                _issue(
                    code,
                    "block",
                    f"Draft contains unsafe wording for {code}.",
                    "Replace completion/execution wording with read-only or proposal wording.",
                )
            )

    for code, phrases in REVISION_PHRASES.items():
        if _has_any(lowered, phrases):
            severity = "revision"
            if code == "soak_rerun_overclaim" and evidence_labels == ["artifact"]:
                severity = "block"
            issues.append(
                _issue(
                    code,
                    severity,
                    f"Draft overstates available evidence for {code}.",
                    "Qualify the claim and cite only the evidence actually present.",
                )
            )

    if missing:
        issues.append(
            _issue(
                "missing_evidence",
                "revision",
                "Draft has unresolved evidence gaps.",
                "Name the missing evidence or ask for clarification before answering.",
            )
        )

    if proposal_boundary is not None:
        status = str(proposal_boundary.get("status") or "").strip().lower()
        receipt = str(proposal_boundary.get("receipt_status") or "").strip().lower()
        handler_invoked = bool(proposal_boundary.get("handler_invoked"))
        if status != "proposed" or receipt != "not_executed" or handler_invoked:
            issues.append(
                _issue(
                    "invalid_proposal_boundary",
                    "block",
                    "Proposal boundary does not prove inert status.",
                    "Use an inert proposed-action boundary with not_executed receipt.",
                )
            )
        elif any(word in lowered for word in ("completed", "done", "applied", "ejecut")):
            issues.append(
                _issue(
                    "proposal_claimed_as_completed",
                    "block",
                    "Draft describes a proposed action as completed.",
                    "Say the action is proposed/prepared and not executed.",
                )
            )

    required_edits = [issue.required_edit for issue in issues]
    suggestions = [
        "I can answer from the evidence available.",
        "I can prepare this as an inert proposal for human review.",
        "I do not have evidence that this action was performed.",
    ]

    if any(issue.severity == "block" for issue in issues):
        decision = BLOCKED
    elif issues:
        decision = NEEDS_REVISION
    else:
        decision = SAFE_TO_ANSWER

    return SelfCritiqueResult(
        passed=not issues,
        issues=issues,
        required_edits=required_edits,
        safe_wording_suggestions=suggestions,
        final_release_decision=decision,
    )
