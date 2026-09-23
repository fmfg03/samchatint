"""Local, proposal-only extraction and comparison for bank payment proofs."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
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
    detected_currency: str | None
    detected_beneficiary: str | None
    detected_reference: str | None
    reasons: tuple[str, ...]
    template_id: str | None = None


def _normalized(value: Any) -> str:
    raw = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(char for char in raw if not unicodedata.combining(char)).upper().strip()


def _parse_date(value: Any) -> date | None:
    raw = str(value or "")
    iso_match = re.search(r"20\d{2}-\d{2}-\d{2}", raw)
    if iso_match:
        try:
            return date.fromisoformat(iso_match.group(0))
        except ValueError:
            return None
    local_match = re.search(r"\b\d{2}/\d{2}/20\d{2}\b", raw)
    if local_match:
        try:
            return datetime.strptime(local_match.group(0), "%d/%m/%Y").date()
        except ValueError:
            return None
    return None


def _detected_currency(text: str) -> str | None:
    match = re.search(r"\b(MXN|USD|EUR)\b", text or "", flags=re.IGNORECASE)
    return match.group(1).upper() if match else None


def _parse_amount(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value or "").replace(",", ""))
    except (InvalidOperation, ValueError):
        return None


def _bank_template_id(text: str, *, reference: str | None) -> str | None:
    normalized = _normalized(text)
    markers = ("FECHA", "BENEFICIARIO")
    has_amount_marker = any(marker in normalized for marker in ("MONTO", "IMPORTE", "CANTIDAD"))
    has_reference_marker = "CLAVE DE RASTREO" in normalized or "REFERENCIA" in normalized
    if reference and has_amount_marker and has_reference_marker and all(marker in normalized for marker in markers):
        return "spei_transferencia_v1"
    return None


def review_payment_proof(
    *, raw: bytes, filename: str, mime_type: str, expected_amount: Any,
    expected_beneficiary: str | None, expected_currency: str | None = None,
) -> PaymentProofReview:
    """Return audit-safe candidates; never persists or treats a candidate as fact."""
    try:
        text = extract_document_text_from_bytes(
            raw=raw, filename=filename, mime_type=mime_type, allow_pdf=True
        )
    except HTTPException:
        return PaymentProofReview("revision_required", None, None, None, None, None, ("No fue posible extraer texto local del comprobante.",))
    entities = _extract_payment_entities(text)
    detected_date = _parse_date(entities.get("date"))
    detected_amount = _parse_amount(entities.get("amount"))
    detected_currency = _detected_currency(text)
    detected_beneficiary = str(entities.get("beneficiary") or "").strip() or None
    detected_reference = str(entities.get("bank_reference") or "").strip() or None
    template_id = _bank_template_id(text, reference=detected_reference)
    reasons: list[str] = []
    has_conflict = False
    date_candidates = re.findall(
        r"20\d{2}-\d{2}-\d{2}|\b\d{2}/\d{2}/20\d{2}\b", text
    )
    dates = {parsed for candidate in date_candidates if (parsed := _parse_date(candidate))}
    if len(dates) > 1:
        detected_date = None
        reasons.append("Se detectaron varias fechas; Finanzas debe elegir la fecha efectiva.")
    try:
        expected = Decimal(str(expected_amount))
    except (InvalidOperation, ValueError):
        expected = None
    if detected_amount is not None and expected is not None and detected_amount != expected:
        reasons.append("El monto detectado no coincide con el monto programado.")
        has_conflict = True
    if detected_currency and expected_currency and detected_currency != expected_currency.upper():
        reasons.append("La moneda detectada no coincide con la moneda programada.")
        has_conflict = True
    if detected_beneficiary and expected_beneficiary and _normalized(detected_beneficiary) != _normalized(expected_beneficiary):
        reasons.append("El beneficiario detectado no coincide con el beneficiario programado.")
        has_conflict = True
    if has_conflict:
        status = "conflict"
    elif not (detected_date and detected_amount and detected_beneficiary):
        status = "revision_required"
        reasons.append("Faltan datos detectables; Finanzas debe revisarlos manualmente.")
    elif not template_id:
        status = "revision_required"
        reasons.append("La plantilla bancaria no coincide; Finanzas debe revisarla.")
    else:
        status = "match"
    return PaymentProofReview(
        status, detected_date, detected_amount, detected_currency, detected_beneficiary,
        detected_reference, tuple(reasons), template_id
    )
