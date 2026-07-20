from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Sequence

from .document_classifier import (
    ACCOUNTING_BALANCE,
    CFDI_INVOICE,
    DOCUMENT_VALIDATION,
    EXPENSE_RECEIPT,
    INVOICE_DOCUMENT,
    PAYMENT_PROOF,
    PLAYER_REGISTRATION,
    ROSTER,
    TOURNAMENT_OPS,
    UNKNOWN_OR_GENERIC,
)
from .document_confirmation import ProposedDocumentAction, build_proposed_action


def _pick_payload(
    entities: Mapping[str, Any],
    keys: Iterable[str],
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    for key in keys:
        value = entities.get(key)
        if value not in (None, "", [], {}):
            payload[key] = value
    return payload


def _supported_or_empty(action: str, supported_actions: Sequence[str] | None) -> str:
    if supported_actions is None or action in supported_actions:
        return action
    return ""


def plan_document_actions(
    *,
    intake_id: str,
    document_type: str,
    entities: Mapping[str, Any],
    missing_fields: Sequence[str],
    supported_actions: Sequence[str] | None = None,
    writes_enabled: bool = False,
) -> List[ProposedDocumentAction]:
    actions: List[ProposedDocumentAction] = []

    def add(
        *,
        canonical_action: str,
        title: str,
        payload_preview: Mapping[str, Any],
        risk_level: str,
    ) -> None:
        resolved = _supported_or_empty(canonical_action, supported_actions)
        if not resolved:
            return
        actions.append(
            build_proposed_action(
                intake_id=intake_id,
                canonical_action=resolved,
                title=title,
                payload_preview=payload_preview,
                risk_level=risk_level,
                writes_enabled=writes_enabled,
            )
        )

    if document_type == ACCOUNTING_BALANCE:
        payload = _pick_payload(
            entities,
            [
                "period",
                "company",
                "project",
                "account_count",
                "debit_total",
                "credit_total",
                "imbalance",
            ],
        )
        add(
            canonical_action="executive.accounting_report",
            title="Generar preview de reporte contable",
            payload_preview=payload,
            risk_level="read",
        )
        if not missing_fields:
            add(
                canonical_action="accounting.build_expense_preview",
                title="Preparar preview contable sin contabilizar",
                payload_preview=payload,
                risk_level="read",
            )
        return actions

    if document_type in {ROSTER, PLAYER_REGISTRATION, DOCUMENT_VALIDATION}:
        payload = _pick_payload(
            entities,
            [
                "team_name",
                "category",
                "tournament",
                "player_count",
                "invalid_curp_count",
            ],
        )
        add(
            canonical_action="operations.tournament_soul_snapshot",
            title="Revisar contexto de torneo para registro",
            payload_preview=payload,
            risk_level="read",
        )
        add(
            canonical_action="operations.verify_player_document",
            title="Crear revisión de registro/documento",
            payload_preview=payload,
            risk_level="medium",
        )
        return actions

    if document_type == TOURNAMENT_OPS:
        payload = _pick_payload(
            entities,
            ["tournament", "dates", "venues", "teams", "commitments", "milestones"],
        )
        add(
            canonical_action="operations.folder_planner_snapshot",
            title="Generar snapshot operativo del documento",
            payload_preview=payload,
            risk_level="read",
        )
        add(
            canonical_action="operations.create_solicitud_from_commitment",
            title="Proponer acción operativa desde compromiso",
            payload_preview=payload,
            risk_level="medium",
        )
        return actions

    if document_type in {CFDI_INVOICE, INVOICE_DOCUMENT}:
        payload = _pick_payload(
            entities,
            [
                "uuid",
                "issuer_rfc",
                "receiver_rfc",
                "amount",
                "date",
                "currency",
                "concept",
            ],
        )
        add(
            canonical_action="receipts.cfdi_matching_overview",
            title="Buscar gastos candidatos para CFDI",
            payload_preview=payload,
            risk_level="read",
        )
        add(
            canonical_action="receipts.link_expense_to_cfdi",
            title="Vincular CFDI a gasto/documento",
            payload_preview=payload,
            risk_level="medium",
        )
        add(
            canonical_action="receipts.request_cfdi",
            title="Crear snapshot de solicitud CFDI",
            payload_preview=payload,
            risk_level="medium",
        )
        return actions

    if document_type == PAYMENT_PROOF:
        payload = _pick_payload(
            entities,
            [
                "amount",
                "date",
                "bank_reference",
                "beneficiary",
                "payer",
                "concept",
                "candidate_match",
            ],
        )
        add(
            canonical_action="receipts.pending_payment_overview",
            title="Buscar documentos candidatos para pago",
            payload_preview=payload,
            risk_level="read",
        )
        add(
            canonical_action="receipts.register_document_payment",
            title="Registrar pago contra documento",
            payload_preview=payload,
            risk_level="high",
        )
        return actions

    if document_type == EXPENSE_RECEIPT:
        payload = _pick_payload(
            entities,
            [
                "amount",
                "date",
                "merchant",
                "concept",
                "currency",
                "payment_subject_type",
                "tournament",
                "evidence_sha256",
            ],
        )
        add(
            canonical_action="expenses.create_personal_receipt_workflow",
            title="Preparar gasto personal y solicitud de pago",
            payload_preview=payload,
            risk_level="high",
        )
        add(
            canonical_action="expenses.create_third_party_receipt_workflow",
            title="Preparar solicitud de pago a tercero",
            payload_preview=payload,
            risk_level="high",
        )
        return actions

    if document_type == UNKNOWN_OR_GENERIC:
        return []

    return actions


def candidate_workflows_for_type(document_type: str) -> List[str]:
    mapping = {
        ACCOUNTING_BALANCE: [
            "historical_accounting_import_preview",
            "finance_accounting_report_preview",
            "reconciliation_analysis",
        ],
        ROSTER: ["registration_review", "team_player_action"],
        PLAYER_REGISTRATION: ["registration_review", "document_validation"],
        DOCUMENT_VALIDATION: ["registration_review", "document_validation"],
        TOURNAMENT_OPS: ["tournament_soul_snapshot", "folder_commitment_snapshot"],
        CFDI_INVOICE: ["cfdi_matching", "expense_document_linking", "exception_queue"],
        INVOICE_DOCUMENT: [
            "invoice_review",
            "expense_document_linking",
            "exception_queue",
        ],
        PAYMENT_PROOF: ["payment_matching", "payment_registration_proposal"],
        EXPENSE_RECEIPT: [
            "expense_receipt_intake",
            "personal_expense_account_preview",
            "third_party_payment_request_preview",
        ],
        UNKNOWN_OR_GENERIC: ["manual_workflow_selection"],
    }
    return list(mapping.get(document_type, ["manual_workflow_selection"]))
