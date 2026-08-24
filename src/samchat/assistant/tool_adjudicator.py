"""Tool candidate adjudication for the SamChat assistant runtime.

The WorkFrame says what job the user asked SamChat to do. This module checks
whether a candidate tool is semantically allowed to answer that job before the
runtime trusts its result. It is intentionally deterministic and read-only.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .work_frame import WorkFrame


@dataclass(frozen=True)
class ToolCandidateDecision:
    tool: str
    accepted: bool
    reason: str
    work_frame_domain: str
    work_frame_task_kind: str
    required_evidence: tuple[str, ...]
    rejected_interpretations: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["required_evidence"] = list(self.required_evidence)
        payload["rejected_interpretations"] = list(self.rejected_interpretations)
        return payload


def _normalized_tool(tool: str | None) -> str:
    return str(tool or "").strip()


def adjudicate_tool_candidate(
    *,
    work_frame: WorkFrame,
    tool: str | None,
) -> ToolCandidateDecision:
    """Decide if a tool is a valid candidate for a WorkFrame.

    This is not a permission check and does not execute anything. It is a
    semantic guard: a safe read tool can still be the wrong tool for the user's
    question.
    """

    tool_name = _normalized_tool(tool)
    forbidden = tuple(work_frame.forbidden_interpretations or ())
    required = tuple(work_frame.required_evidence or ())

    if not tool_name:
        return ToolCandidateDecision(
            tool="",
            accepted=False,
            reason="missing_tool_name",
            work_frame_domain=work_frame.domain,
            work_frame_task_kind=work_frame.task_kind,
            required_evidence=required,
            rejected_interpretations=forbidden,
        )

    if (
        "pending_payment_queue" in forbidden
        and tool_name == "receipts.pending_payment_overview"
    ):
        return ToolCandidateDecision(
            tool=tool_name,
            accepted=False,
            reason="pending_payment_queue_cannot_answer_historical_payment_evidence",
            work_frame_domain=work_frame.domain,
            work_frame_task_kind=work_frame.task_kind,
            required_evidence=required,
            rejected_interpretations=forbidden,
        )

    if (
        "historical_payment_evidence" in forbidden
        and tool_name == "assistant_owner_variable_query"
    ):
        return ToolCandidateDecision(
            tool=tool_name,
            accepted=False,
            reason="historical_payment_evidence_tool_cannot_answer_pending_payment_queue",
            work_frame_domain=work_frame.domain,
            work_frame_task_kind=work_frame.task_kind,
            required_evidence=required,
            rejected_interpretations=forbidden,
        )

    if work_frame.domain == "owner":
        if work_frame.task_kind == "readiness" and tool_name in {
            "assistant_owner_pack_readiness",
            "assistant_owner_entity_folder_workspace",
        }:
            return ToolCandidateDecision(
                tool=tool_name,
                accepted=True,
                reason="owner_readiness_candidate_matches_work_frame",
                work_frame_domain=work_frame.domain,
                work_frame_task_kind=work_frame.task_kind,
                required_evidence=required,
                rejected_interpretations=forbidden,
            )
        if work_frame.task_kind == "evidence" and tool_name in {
            "assistant_owner_variable_query",
            "assistant_owner_entity_folder_workspace",
        }:
            return ToolCandidateDecision(
                tool=tool_name,
                accepted=True,
                reason="owner_evidence_candidate_matches_work_frame",
                work_frame_domain=work_frame.domain,
                work_frame_task_kind=work_frame.task_kind,
                required_evidence=required,
                rejected_interpretations=forbidden,
            )

    if work_frame.domain == "finance" and tool_name in {
        "assistant_finance_accounting_qa",
        "finance.read_only_comparison",
        "receipts.cfdi_matching_overview",
        "receipts.pending_payment_overview",
    }:
        return ToolCandidateDecision(
            tool=tool_name,
            accepted=True,
            reason="finance_candidate_matches_work_frame",
            work_frame_domain=work_frame.domain,
            work_frame_task_kind=work_frame.task_kind,
            required_evidence=required,
            rejected_interpretations=forbidden,
        )

    if work_frame.domain == "mixed" and tool_name in {
        "assistant_owner_variable_query",
        "assistant_finance_accounting_qa",
    }:
        return ToolCandidateDecision(
            tool=tool_name,
            accepted=True,
            reason="mixed_domain_read_only_candidate_matches_work_frame",
            work_frame_domain=work_frame.domain,
            work_frame_task_kind=work_frame.task_kind,
            required_evidence=required,
            rejected_interpretations=forbidden,
        )

    if work_frame.needs_clarification:
        return ToolCandidateDecision(
            tool=tool_name,
            accepted=True,
            reason="clarification_or_fallback_allowed_for_unknown_work_frame",
            work_frame_domain=work_frame.domain,
            work_frame_task_kind=work_frame.task_kind,
            required_evidence=required,
            rejected_interpretations=forbidden,
        )

    return ToolCandidateDecision(
        tool=tool_name,
        accepted=True,
        reason="legacy_candidate_allowed_pending_full_registry_mapping",
        work_frame_domain=work_frame.domain,
        work_frame_task_kind=work_frame.task_kind,
        required_evidence=required,
        rejected_interpretations=forbidden,
    )


def build_tool_adjudication_trace(
    *,
    work_frame: WorkFrame,
    tool: str | None,
) -> dict[str, Any]:
    decision = adjudicate_tool_candidate(work_frame=work_frame, tool=tool)
    return {
        "tool": "assistant.tool_candidate_adjudicator",
        "assistant_tool_candidate_adjudicator": {
            "stage": "pre_sufficiency_tool_candidate_adjudication",
            **decision.to_dict(),
        },
        "result": decision.to_dict(),
    }


__all__ = [
    "ToolCandidateDecision",
    "adjudicate_tool_candidate",
    "build_tool_adjudication_trace",
]
