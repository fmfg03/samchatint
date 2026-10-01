"""Validate concrete canonical read envelopes; never infer evidence from truthiness."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeGuard


def _map(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _number(value: Any) -> TypeGuard[int | float]:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _period(data: Mapping[str, Any]) -> bool:
    period = _map(data.get("period"))
    return bool(period.get("from") and period.get("to"))


def _incomplete(data: Mapping[str, Any]) -> bool:
    if (
        data.get("ok") is False
        or data.get("status")
        in {
            "failed",
            "error",
            "unsupported",
            "capability_unavailable",
        }
        or data.get("coverage") in ("not_queried", "partial", "sample")
    ):
        return True
    status = _map(data.get("source_status"))
    return any(
        bool(value)
        for key, value in status.items()
        if key.endswith(("_truncated", "_gap"))
    ) or bool(data.get("coverage_notice") or data.get("executive_quality_gaps"))


# Canonical actions and direct tools share exactly the same module-owned payload.
_ACTION_READERS = {
    "executive.realtime_report": "finance_realtime_report",
    "executive.strategy_snapshot": "finance_strategy_snapshot",
    "executive.planner_snapshot": "finance_planner_snapshot",
    "executive.accounting_report": "finance_accounting_report",
    "executive.alerts_scan": "finance_alerts_scan",
    "expense.full_workflow_snapshot": "expense.full_workflow_snapshot",
    "receipts.cfdi_workflow_snapshot": "finance_expense_workflow_status",
    "receipts.cfdi_matching_overview": "receipts.cfdi_matching_overview",
    "accounting.build_expense_preview": "accounting.build_expense_preview",
    "operations.folder_planner_snapshot": "operations.folder_planner_snapshot",
    "budgets.snapshot": "budget.snapshot",
    "operations.tournament_registration_executive_reports": "tournament_registration_executive_reports",
    "receipts.pending_payment_overview": "receipts.pending_payment_overview",
    "operations.tournament_soul_snapshot": "operations.tournament_soul_snapshot",
}


def _payload_matches(reader: str, data: Mapping[str, Any]) -> bool:
    if not data or _incomplete(data):
        return False
    if reader in {"finance_realtime_report", "finance_strategy_snapshot"}:
        totals = _map(data.get("totals"))
        return bool(
            _period(data)
            and data.get("generated_at")
            and isinstance(data.get("filters"), Mapping)
            and _number(totals.get("gasto_total"))
            and _number(totals.get("registros"))
            and totals.get("moneda")
            and isinstance(_map(data.get("breakdown")).get("items"), list)
            and data.get("notes")
        )
    if reader == "finance_planner_snapshot":
        return _payload_matches(
            "finance_strategy_snapshot", _map(data.get("source_strategy"))
        )
    if reader == "finance_alerts_scan":
        return bool(
            _period(data)
            and data.get("generated_at")
            and _number(_map(data.get("summary")).get("weeks_analyzed"))
            and isinstance(data.get("alerts"), list)
            and data.get("notes")
        )
    if reader == "finance_accounting_report":
        period = _map(data.get("period"))
        return bool(
            period.get("year")
            and period.get("month")
            and data.get("report_type")
            and data.get("summary")
            and isinstance(data.get("rows", data.get("imports")), list)
        )
    if reader == "finance_expense_workflow_status":
        return bool(
            data.get("expense_id")
            and data.get("numero_referencia")
            and data.get("stages")
            and data.get("expense")
        )
    if reader == "expense.full_workflow_snapshot":
        return bool(
            data.get("expense")
            and _payload_matches(
                "finance_expense_workflow_status", _map(data.get("workflow"))
            )
        )
    if reader == "accounting.build_expense_preview":
        expense = _map(data.get("expense"))
        preview = _map(data.get("preview"))
        return bool(
            expense.get("expense_id")
            and expense.get("numero_referencia")
            and _number(_map(preview.get("taxes")).get("base_gasto"))
            and preview.get("contra_account")
        )
    if reader == "receipts.cfdi_matching_overview":
        limit = data.get("limit")
        matching_groups = [
            data.get(k)
            for k in ("pending_expenses", "linked_expenses", "unlinked_cfdis")
        ]
        return bool(
            _number(limit)
            and limit > 0
            and data.get("summary")
            and all(isinstance(r, list) and len(r) < limit for r in matching_groups)
        )
    if reader == "operations.folder_planner_snapshot":
        rows = data.get("commitments")
        limit = _map(data.get("filters")).get("limit")
        return bool(
            data.get("ok") is True
            and data.get("sources")
            and isinstance(rows, list)
            and _number(limit)
            and len(rows) < min(limit, 200)
            and _map(data.get("summary")).get("commitments_count") == len(rows)
        )
    if reader == "budget.snapshot":
        version = _map(data.get("version"))
        summary = _map(data.get("summary"))
        return bool(
            data.get("ok") is True
            and data.get("source") == "budget_db"
            and version.get("id")
            and version.get("edition_year")
            and _number(summary.get("budget_total"))
            and _number(summary.get("line_count"))
            and summary.get("line_count", 0) > 0
        )
    if reader == "receipts.pending_payment_overview":
        summary = _map(data.get("summary"))
        rows = data.get("documentos")
        return bool(
            isinstance(rows, list)
            and summary.get("pending_count") == len(rows)
            and _number(summary.get("total_pendiente"))
            and all(
                _map(row).get("documento_id")
                and _number(_map(row).get("monto_pendiente"))
                for row in rows
            )
        )
    if reader == "operations.tournament_soul_snapshot":
        return bool(
            data.get("snapshot_type") == "tournament_soul_service"
            and data.get("tournaments")
            and data.get("soul")
            and not data.get("warnings")
        )
    if reader == "tournament_registration_executive_reports":
        return bool(
            data.get("source")
            and _map(data.get("tournament")).get("id")
            and data.get("as_of_date")
            and _number(_map(data.get("summary")).get("equipos"))
            and isinstance(_map(data.get("reports")).get("participacion_general"), list)
            and isinstance(data.get("caveats"), list)
        )
    if reader in {"ar.summary", "ar.matching"}:
        rows_key = "expected_income" if reader == "ar.summary" else "items"
        return bool(
            data.get("ok") is True
            and data.get("read_only") is True
            and data.get("budget_version_id")
            and data.get("summary")
            and isinstance(data.get(rows_key), list)
        )
    if reader == "cashflow.summary":
        return bool(
            data.get("ok") is True
            and data.get("read_only") is True
            and _map(data.get("period")).get("year")
            and data.get("summary")
            and data.get("monthly_buckets")
            and data.get("source_notes")
        )
    if reader in {"cashflow.statement", "budget.vs_actual"}:
        expected = (
            "cashflow_statement"
            if reader == "cashflow.statement"
            else "budget_vs_actual"
        )
        return bool(
            data.get("ok") is True
            and data.get("read_only") is True
            and data.get("report_type") == expected
            and data.get("period")
            and data.get("columns")
            and data.get("rows")
            and data.get("source_notes")
        )
    if reader == "finance.platform":
        return bool(
            data.get("ok") is True
            and data.get("read_only") is True
            and _map(data.get("period")).get("year")
            and data.get("summary")
            and data.get("cash_control_center")
            and data.get("accounting_close_center")
            and not _incomplete(_map(data.get("action_queue")))
        )
    return False


def validate_read_evidence(tool: str, result: Mapping[str, Any]) -> tuple[bool, str]:
    """Return sufficiency and semantic action, separately from authorization.

    Unmapped readers fail closed rather than treating arbitrary dicts as evidence.
    """
    if _incomplete(result):
        return False, tool
    if tool == "assistant_canonical_query":
        action = str(result.get("action") or "")
        reader = _ACTION_READERS.get(action)
        valid = bool(
            reader
            and result.get("status") == "completed"
            and isinstance(result.get("context"), Mapping)
            and _payload_matches(reader, _map(result.get("data")))
        )
        return valid, action
    if tool == "assistant_finance_read":
        intent = str(result.get("intent") or "")
        if not (
            result.get("ok") is True
            and result.get("read_only") is True
            and result.get("source_function")
            and result.get("source_notes")
        ):
            return False, tool
        if intent == "finance.platform" and not (
            isinstance(
                _map(result.get("source_status")).get("expense_scan_truncated"), bool
            )
            and _number(_map(result.get("source_status")).get("expense_scan_limit"))
        ):
            return False, tool
        return _payload_matches(intent, _map(result.get("payload"))), tool
    return _payload_matches(tool, result), tool
