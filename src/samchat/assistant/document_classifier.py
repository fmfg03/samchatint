from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, List, Mapping, Sequence

from .file_parsing import normalize_spreadsheet_records

ACCOUNTING_BALANCE = "accounting_balance"
ROSTER = "roster"
PLAYER_REGISTRATION = "player_registration"
DOCUMENT_VALIDATION = "document_validation"
TOURNAMENT_OPS = "tournament_ops"
CFDI_INVOICE = "cfdi_invoice"
INVOICE_DOCUMENT = "invoice_document"
PAYMENT_PROOF = "payment_proof"
EXPENSE_RECEIPT = "expense_receipt"
UNKNOWN_OR_GENERIC = "unknown_or_generic"


@dataclass(frozen=True)
class DocumentClassification:
    detected_document_type: str
    confidence: float
    signals: List[str]
    file_kind: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _text_blob(text: str, records: Sequence[Mapping[str, Any]] | None) -> str:
    pieces = [(text or "").lower()]
    for row in list(records or [])[:8]:
        pieces.append(" ".join(str(value or "").lower() for value in row.values()))
        pieces.append(" ".join(str(key or "").lower() for key in row.keys()))
    return "\n".join(pieces)


def _normalized_keys(records: Sequence[Mapping[str, Any]] | None) -> set[str]:
    keys: set[str] = set()
    for row in normalize_spreadsheet_records(list(records or [])[:8]):
        keys.update(row.keys())
    return keys


def _has_any(haystack: str, needles: Iterable[str]) -> bool:
    return any(needle in haystack for needle in needles)


def _bounded_confidence(score: float) -> float:
    return max(0.0, min(0.99, round(score, 2)))


def classify_document(
    *,
    file_name: str = "",
    file_kind: str = "",
    text: str = "",
    records: Sequence[Mapping[str, Any]] | None = None,
) -> DocumentClassification:
    name = (file_name or "").lower()
    kind = (file_kind or "").lower()
    blob = _text_blob(f"{name}\n{text}", records)
    keys = _normalized_keys(records)
    signals: List[str] = []

    accounting_keys = {
        "cuenta",
        "descripcion_de_la_cuenta",
        "saldo_inicial",
        "total_de_cargos",
        "total_de_abonos",
        "saldo_final",
        "debe",
        "haber",
        "balance",
    }
    if len(keys.intersection(accounting_keys)) >= 3 or _has_any(
        blob, ["balanza", "saldo final", "total de cargos", "total de abonos", "coi"]
    ):
        signals.append("accounting_balance_markers")
        return DocumentClassification(
            detected_document_type=ACCOUNTING_BALANCE,
            confidence=_bounded_confidence(
                0.72 + 0.04 * min(len(keys.intersection(accounting_keys)), 5)
            ),
            signals=signals,
            file_kind=kind or "spreadsheet",
        )

    roster_keys = {
        "equipo",
        "team_name",
        "categoria",
        "category_name",
        "nombre",
        "apellido",
        "curp",
        "fecha_nacimiento",
        "birth_date",
        "dorsal",
    }
    if len(keys.intersection(roster_keys)) >= 3 or _has_any(
        blob, ["curp", "roster", "plantilla", "jugadores", "categoria"]
    ):
        signals.append("roster_or_registration_markers")
        doc_type = (
            ROSTER if "equipo" in keys or "team_name" in keys else PLAYER_REGISTRATION
        )
        return DocumentClassification(
            detected_document_type=doc_type,
            confidence=_bounded_confidence(
                0.68 + 0.04 * min(len(keys.intersection(roster_keys)), 6)
            ),
            signals=signals,
            file_kind=kind or "spreadsheet",
        )

    if (
        "<cfdi:" in blob
        or "timbrefiscaldigital" in blob
        or "uuid" in blob
        and "rfc" in blob
    ):
        signals.append("cfdi_xml_markers")
        return DocumentClassification(
            detected_document_type=CFDI_INVOICE,
            confidence=0.93,
            signals=signals,
            file_kind=kind or "text",
        )

    if _has_any(blob, ["factura", "invoice", "rfc emisor", "rfc receptor", "subtotal"]):
        signals.append("invoice_markers")
        return DocumentClassification(
            detected_document_type=INVOICE_DOCUMENT,
            confidence=0.74,
            signals=signals,
            file_kind=kind or "text",
        )

    if _has_any(
        blob,
        [
            "spei",
            "clave de rastreo",
            "comprobante de pago",
            "beneficiario",
            "ordenante",
        ],
    ):
        signals.append("payment_proof_markers")
        return DocumentClassification(
            detected_document_type=PAYMENT_PROOF,
            confidence=0.86,
            signals=signals,
            file_kind=kind or "text",
        )

    receipt_marker = _has_any(
        blob,
        ["ticket", "recibo", "nota de consumo", "nota de venta"],
    )
    receipt_structure = "total" in blob and _has_any(
        blob,
        ["comercio", "establecimiento", "proveedor", "fecha"],
    )
    if receipt_marker or receipt_structure:
        signals.append("expense_receipt_markers")
        return DocumentClassification(
            detected_document_type=EXPENSE_RECEIPT,
            confidence=0.78 if receipt_marker else 0.68,
            signals=signals,
            file_kind=kind or "image",
        )

    if _has_any(
        blob,
        ["fixture", "calendario", "sede", "venue", "torneo", "compromiso", "milestone"],
    ):
        signals.append("tournament_ops_markers")
        return DocumentClassification(
            detected_document_type=TOURNAMENT_OPS,
            confidence=0.7,
            signals=signals,
            file_kind=kind or "text",
        )

    if _has_any(blob, ["acta", "ine", "identificacion", "documento jugador"]):
        signals.append("document_validation_markers")
        return DocumentClassification(
            detected_document_type=DOCUMENT_VALIDATION,
            confidence=0.66,
            signals=signals,
            file_kind=kind or "text",
        )

    return DocumentClassification(
        detected_document_type=UNKNOWN_OR_GENERIC,
        confidence=0.35 if (text or records) else 0.1,
        signals=["insufficient_specific_markers"],
        file_kind=kind or "unknown",
    )
