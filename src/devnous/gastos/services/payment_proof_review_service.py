"""Local, proposal-only extraction and comparison for bank payment proofs."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import HTTPException

from samchat.assistant.document_intake import _extract_payment_entities
from samchat.assistant.file_parsing import extract_document_text_from_bytes


@dataclass(frozen=True)
class PaymentProofReview:
    status: str
    detected_date: date | None
    detected_amount: Decimal | None
    detected_beneficiary: str | None
    detected_reference: str | None
    reasons: tuple[str, ...]


def _normalized(value: Any) -> str:
    raw = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(char for char in raw if not unicodedata.combining(char)).upper().strip()


def _parse_date(value: Any) -> date | None:
    match = re.search(r"20\d{2}-\d{2}-\d{2}", str(value or ""))
    if not match:
        return None
    try:
        return date.fromisoformat(match.group(0))
    except ValueError:
        return None


def _parse_amount(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value or "").replace(",", ""))
    except (InvalidOperation, ValueError):
        return None


def review_payment_proof(
    *, raw: bytes, filename: str, mime_type: str, expected_amount: Any,
    expected_beneficiary: str | None,
) -> PaymentProofReview:
    """Return audit-safe candidates; never persists or treats a candidate as fact."""
    try:
        text = extract_document_text_from_bytes(
            raw=raw, filename=filename, mime_type=mime_type, allow_pdf=True
        )
    except HTTPException:
        return PaymentProofReview("revision_required", None, None, None, None, ("No fue posible extraer texto local del comprobante.",))
    entities = _extract_payment_entities(text)
    detected_date = _parse_date(entities.get("date"))
    detected_amount = _parse_amount(entities.get("amount"))
    detected_beneficiary = str(entities.get("beneficiary") or "").strip() or None
    reasons: list[str] = []
    try:
        expected = Decimal(str(expected_amount))
    except (InvalidOperation, ValueError):
        expected = None
    if detected_amount is not None and expected is not None and detected_amount != expected:
        reasons.append("El monto detectado no coincide con el monto programado.")
    if detected_beneficiary and expected_beneficiary and _normalized(detected_beneficiary) != _normalized(expected_beneficiary):
        reasons.append("El beneficiario detectado no coincide con el beneficiario programado.")
    if reasons:
        status = "conflict"
    elif not (detected_date and detected_amount and detected_beneficiary):
        status = "revision_required"
        reasons.append("Faltan datos detectables; Finanzas debe revisarlos manualmente.")
    else:
        status = "match"
    return PaymentProofReview(
        status, detected_date, detected_amount, detected_beneficiary,
        str(entities.get("bank_reference") or "").strip() or None, tuple(reasons)
    )
