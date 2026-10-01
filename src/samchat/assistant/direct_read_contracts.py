"""Explicit contracts for the direct readers exposed by the contextual router.

Coverage reports are evidence about readiness, not about financial totals.
Technical readers and proposals do not establish executed business facts.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# Each schema comes from the module-owned report's to_dict, not model prose.
REPORT_SCHEMAS: dict[str, dict[str, type]] = {
    "assistant_owner_pack_readiness": {
        "readiness_id": str,
        "status": str,
        "surfaces": list,
        "source_reports": list,
        "missing_evidence": list,
        "readiness_score": int,
    },
    "assistant_owner_pack_readiness_dashboard": {
        "dashboard_id": str,
        "overall_status": str,
        "cards": list,
        "source_reports": list,
        "source_readiness_id": str,
    },
    "assistant_owner_pack_inventory": {
        "inventory_id": str,
        "surfaces": list,
        "canonical_sources": list,
        "field_count": int,
    },
    "assistant_owner_pack_status": {
        "status_id": str,
        "surfaces": list,
        "prepared_surface_count": int,
        "missing_evidence": list,
    },
    "assistant_owner_pack_export_preview": {
        "preview_id": str,
        "schema_version": str,
        "mode": str,
        "sections": list,
        "source_artifacts": list,
        "non_claims": list,
    },
    "assistant_owner_variable_query": {
        "query_id": str,
        "status": str,
        "question": str,
        "resolutions": list,
        "candidates": list,
    },
    "assistant_owner_pack_live_snapshot": {
        "snapshot_id": str,
        "surfaces": list,
        "supported_field_count": int,
        "missing_field_count": int,
    },
    "assistant_owner_pack_live_brief": {
        "response_id": str,
        "source_type": str,
        "source_id": str,
        "evidence_found": list,
        "missing_evidence": list,
        "approval_boundary": str,
    },
    "assistant_owner_entity_dossier_live": {
        "report_id": str,
        "source_summary": dict,
        "audit": dict,
        "missing_evidence": list,
        "non_claims": list,
    },
    "assistant_owner_entity_folder_workspace": {
        "workspace_id": str,
        "target": dict,
        "workspace_cards": list,
        "folder_sections": list,
        "source_reports": list,
        "missing_fields": list,
    },
    "assistant_sports_operations_status": {
        "report_id": str,
        "tournament": dict,
        "operational_status": str,
        "source_modules": list,
        "source_summary": dict,
        "priorities": list,
    },
    "assistant_historical_accounting_precedent": {
        "report_id": str,
        "status": str,
        "company_code": str,
        "source_summary": dict,
        "candidates": list,
        "non_claims": list,
    },
    "finance_closeout_diagnostics": {
        "report_id": str,
        "scope": str,
        "period": dict,
        "blockers": list,
        "blocker_count": int,
        "source_summary": dict,
    },
}
TECHNICAL_READERS = frozenset(
    {
        "dev_repo_search",
        "dev_file_read",
        "dev_run_checks",
        "db_read_universal",
        "workspace_file_read",
        "workspace_list",
        "workspace_search",
        "workspace_task_list",
        "workspace_task_file_read",
    }
)
PROPOSAL_READERS = frozenset(
    {
        "tournament_goal_shadow",
        "tournament_proposal_review",
        "tournament_draft_inspect",
        "tournament_draft_revise",
        "tournament_draft_freeze",
        "tournament_draft_cancel",
    }
)
EXISTING_READERS = frozenset(
    {
        "assistant_finance_read",
        "assistant_canonical_query",
        "finance_accounting_report",
        "finance_alerts_scan",
        "finance_expense_workflow_status",
        "finance_realtime_report",
        "finance_strategy_snapshot",
    }
)
ROW_READERS = frozenset({"finance_vendor_payments", "finance_expense_search"})
SPECIAL_READERS = frozenset(
    {
        "assistant_institutional_artifacts",
        "assistant_soul_data_coverage",
        "assistant_tournament_soul_coverage",
        "finance_ops_query",
        "tournament_expediente_snapshot",
        "tournament_ops_query",
        "tournament_registration_breakdown",
    }
)
# Closed inventory: adding an exposed read tool without a decision fails tests.
READ_TOOL_CONTRACTS = {
    **{name: "typed_readonly_report" for name in REPORT_SCHEMAS},
    **{name: "canonical_payload" for name in EXISTING_READERS},
    **{name: "bounded_rows" for name in ROW_READERS},
    **{name: "technical_context_not_business_evidence" for name in TECHNICAL_READERS},
    **{name: "proposal_not_execution_evidence" for name in PROPOSAL_READERS},
    **{name: "source_specific" for name in SPECIAL_READERS},
}


def _map(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _report(tool: str, data: Mapping[str, Any]) -> bool:
    fields = REPORT_SCHEMAS[tool]
    if not all(isinstance(data.get(key), kind) for key, kind in fields.items()):
        return False
    if not all(data[key] for key, kind in fields.items() if kind is str):
        return False
    if not (
        data.get("execution_status") == "not_executed"
        and data.get("writes_attempted") == 0
        and data.get("side_effects_detected") == 0
    ):
        return False
    if tool == "assistant_owner_pack_live_brief":
        return bool(data.get("safety_status") and data.get("approval_boundary"))
    safety = _map(data.get("safety_summary"))
    if not safety or safety.get("writes_enabled") is not False:
        return False
    if tool == "assistant_owner_pack_readiness":
        return bool(
            data["source_reports"]
            and all(
                isinstance(row, Mapping)
                and row.get("surface_id")
                and row.get("status")
                and isinstance(row.get("missing_evidence"), list)
                for row in data["surfaces"]
            )
        )
    if tool == "assistant_owner_variable_query":
        return all(
            isinstance(row, Mapping)
            and row.get("field")
            and row.get("status")
            and (
                row.get("evidence")
                if row.get("status") == "supported"
                else row.get("missing_reason") or row.get("conflict_values")
            )
            for row in data["resolutions"]
        )
    return True


def validate_direct_read(tool: str, data: Mapping[str, Any]) -> bool:
    if tool in REPORT_SCHEMAS:
        return _report(tool, data)
    if tool == "assistant_institutional_artifacts":
        rows = data.get("artifacts")
        return bool(
            data.get("registry_id") == "samchat_institutional_artifact_registry_v1"
            and data.get("read_only") is True
            and isinstance(rows, list)
            and data.get("artifact_count") == len(rows)
        )
    if tool in {"assistant_soul_data_coverage", "assistant_tournament_soul_coverage"}:
        if data.get("read_only") is not True or not data.get("executive_summary"):
            return False
        if tool == "assistant_soul_data_coverage":
            return bool(
                data.get("tool_policy") == "soul_data_coverage_only"
                and isinstance(data.get("artifacts"), list)
                and _number(data.get("score"))
            )
        coverage = _map(data.get("coverage"))
        return bool(
            data.get("tool_policy") == "tournament_soul_coverage_only"
            and data.get("tournament_slug")
            and coverage.get("artifact_id")
            and coverage.get("status")
            and isinstance(coverage.get("findings"), (list, tuple))
        )
    if tool in ROW_READERS:
        vendor = tool == "finance_vendor_payments"
        rows = data.get("documentos" if vendor else "gastos")
        return bool(
            data.get("moneda")
            and _number(data.get("total_pagado" if vendor else "monto_total"))
            and isinstance(rows, list)
            and (
                data.get("vendor_name_query") and data.get("nota")
                if vendor
                else data.get("total_registros") == len(rows)
            )
            and all(
                isinstance(row, Mapping)
                and row.get("documento_id" if vendor else "expense_id")
                and row.get("numero_referencia")
                and _number(row.get("monto"))
                for row in rows
            )
        )
    if tool == "finance_ops_query":

        def section(name: str) -> bool:
            part = _map(data.get(name))
            totals = _map(part.get("totals"))
            return bool(
                _number(totals.get("registros"))
                and _number(totals.get("monto_total"))
                and totals.get("moneda")
                and isinstance(part.get("items"), list)
            )

        return bool(
            isinstance(data.get("filters"), Mapping)
            and data.get("nota")
            and section("expenses")
            and section("documents")
        )
    if tool == "tournament_expediente_snapshot":
        return bool(
            data.get("ok") is True
            and _map(data.get("tournament")).get("id")
            and data.get("summary")
            and isinstance(data.get("entities"), list)
            and data.get("sources")
        )
    if tool == "tournament_registration_breakdown":
        return bool(
            data.get("tournament_key")
            and data.get("source")
            and data.get("nota")
            and _number(data.get("total_equipos"))
            and _number(data.get("total_jugadores"))
            and isinstance(data.get("desglose_por_municipio"), list)
        )
    if tool == "tournament_ops_query":
        return bool(
            data.get("source")
            and data.get("nota")
            and isinstance(data.get("filters"), Mapping)
            and data.get("totals")
            and isinstance(data.get("teams"), list)
            and isinstance(data.get("players"), list)
        )
    return False


def business_evidence_limit(tool: str, message: str, domain: str) -> str | None:
    """Separate intrinsic tool scope from mere missing validator coverage."""
    if tool in TECHNICAL_READERS:
        return "technical_context_not_business_evidence"
    if tool in PROPOSAL_READERS:
        return "proposal_not_execution_evidence"
    if tool.startswith("assistant_owner_") or tool in {
        "assistant_soul_data_coverage",
        "assistant_tournament_soul_coverage",
        "assistant_sports_operations_status",
        "assistant_institutional_artifacts",
    }:
        if domain == "finance":
            return "owner_coverage_not_financial_evidence"
    if tool in ROW_READERS:
        if tool == "finance_vendor_payments" and any(
            term in message for term in ("pagado", "pagaron", "pago efectivo")
        ):
            return "document_estimate_not_payment_proof"
        if "iva" in message:
            return "reader_has_no_vat_breakdown"
        if not ("documentos" in message or "registros" in message) or not any(
            word in message
            for word in ("estimad", "muestra", "consultados", "listados")
        ):
            return "bounded_rows_not_complete_population"
    return None
