from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Mapping, Sequence
from xml.etree import ElementTree as ET

from .document_action_planner import (
    candidate_workflows_for_type,
    plan_document_actions,
)
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
    classify_document,
)
from .document_confirmation import build_safety_status
from .file_parsing import normalize_spreadsheet_records

MONTHS = {
    "enero": "01",
    "febrero": "02",
    "marzo": "03",
    "abril": "04",
    "mayo": "05",
    "junio": "06",
    "julio": "07",
    "agosto": "08",
    "septiembre": "09",
    "setiembre": "09",
    "octubre": "10",
    "noviembre": "11",
    "diciembre": "12",
}


@dataclass(frozen=True)
class DocumentIntakeResult:
    intake_id: str
    file_name: str
    file_kind: str
    detected_document_type: str
    confidence: float
    summary: str
    entities: Dict[str, Any]
    candidate_workflows: List[str]
    missing_fields: List[str]
    risks_or_caveats: List[str]
    proposed_actions: List[Dict[str, Any]]
    questions_for_user: List[str]
    safety: Dict[str, Any]
    evidence_sha256: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, indent=2)


def _stable_intake_id(
    *,
    conversation_id: str,
    file_name: str,
    text: str,
    records: Sequence[Mapping[str, Any]] | None,
    evidence_sha256: str = "",
) -> str:
    preview = {
        "conversation_id": conversation_id,
        "file_name": file_name,
        "text": (text or "")[:2000],
        "records": list(records or [])[:5],
        "evidence_sha256": evidence_sha256,
    }
    encoded = json.dumps(preview, ensure_ascii=False, sort_keys=True, default=str)
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]
    return f"docint_{digest}"


def _to_decimal(value: Any) -> Decimal | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    cleaned = re.sub(r"[^0-9.\-]", "", raw.replace(",", ""))
    if cleaned in {"", "-", "."}:
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _money(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return f"{value.quantize(Decimal('0.01'))}"


def _first_match(text: str, patterns: Sequence[str]) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE)
        if match:
            return (match.group(1) or "").strip()
    return ""


def _period_from_text(text: str) -> str:
    lowered = (text or "").lower()
    year_match = re.search(r"(20\d{2})", lowered)
    year = year_match.group(1) if year_match else ""
    for month_name, month_num in MONTHS.items():
        if month_name in lowered and year:
            return f"{year}-{month_num}"
    date_match = re.search(r"(20\d{2})[-/](0?[1-9]|1[0-2])", lowered)
    if date_match:
        return f"{date_match.group(1)}-{int(date_match.group(2)):02d}"
    return year


def _extract_accounting_entities(
    *,
    file_name: str,
    text: str,
    records: Sequence[Mapping[str, Any]] | None,
) -> Dict[str, Any]:
    normalized = normalize_spreadsheet_records(list(records or []))
    debit_total = Decimal("0")
    credit_total = Decimal("0")
    final_balance_total = Decimal("0")
    debit_seen = credit_seen = balance_seen = False
    account_count = 0
    accounts: List[Dict[str, Any]] = []
    for row in normalized:
        account = row.get("cuenta") or row.get("account")
        if account:
            account_count += 1
        debit = _to_decimal(
            row.get("total_de_cargos") or row.get("debe") or row.get("debit")
        )
        credit = _to_decimal(
            row.get("total_de_abonos") or row.get("haber") or row.get("credit")
        )
        balance = _to_decimal(
            row.get("saldo_final") or row.get("balance") or row.get("saldo")
        )
        if debit is not None:
            debit_total += debit
            debit_seen = True
        if credit is not None:
            credit_total += credit
            credit_seen = True
        if balance is not None:
            final_balance_total += balance
            balance_seen = True
        if len(accounts) < 12 and account:
            accounts.append(
                {
                    "account": account,
                    "description": row.get("descripcion_de_la_cuenta")
                    or row.get("descripcion")
                    or row.get("description"),
                    "final_balance": _money(balance),
                }
            )
    entities: Dict[str, Any] = {
        "period": _period_from_text(f"{file_name}\n{text}"),
        "account_count": account_count or len(normalized),
        "accounts_sample": accounts,
    }
    if debit_seen:
        entities["debit_total"] = _money(debit_total)
    if credit_seen:
        entities["credit_total"] = _money(credit_total)
    if debit_seen and credit_seen:
        entities["imbalance"] = _money(debit_total - credit_total)
    if balance_seen:
        entities["final_balance_total"] = _money(final_balance_total)
    return entities


def _extract_roster_entities(
    records: Sequence[Mapping[str, Any]] | None,
    text: str,
) -> Dict[str, Any]:
    normalized = normalize_spreadsheet_records(list(records or []))

    def pick(row: Mapping[str, str], keys: Sequence[str]) -> str:
        for key in keys:
            value = str(row.get(key) or "").strip()
            if value:
                return value
        return ""

    team_name = category = tournament = ""
    players: List[Dict[str, Any]] = []
    invalid_curp_count = 0
    curp_pattern = re.compile(r"^[A-Z]{4}\d{6}[HM][A-Z]{5}[A-Z0-9]\d$")
    for row in normalized:
        team_name = team_name or pick(row, ["team_name", "equipo", "nombre_equipo"])
        category = category or pick(row, ["category_name", "categoria"])
        tournament = tournament or pick(row, ["tournament_name", "torneo"])
        first_name = pick(row, ["first_name", "nombre", "nombres", "name"])
        last_name = pick(row, ["last_name", "apellido", "apellidos"])
        curp = pick(row, ["curp"]).upper()
        if first_name or last_name or curp:
            if curp and not curp_pattern.match(curp):
                invalid_curp_count += 1
            players.append(
                {"name": " ".join([first_name, last_name]).strip(), "curp": curp}
            )
    if not team_name:
        team_name = _first_match(text, [r"equipo[:\s]+([^\n]+)", r"team[:\s]+([^\n]+)"])
    if not category:
        category = _first_match(
            text, [r"categoria[:\s]+([^\n]+)", r"category[:\s]+([^\n]+)"]
        )
    return {
        "team_name": team_name,
        "category": category,
        "tournament": tournament,
        "player_count": len(players),
        "invalid_curp_count": invalid_curp_count,
        "players_sample": players[:12],
    }


def _extract_cfdi_entities(text: str) -> Dict[str, Any]:
    entities: Dict[str, Any] = {}
    raw = text or ""
    try:
        root = ET.fromstring(raw.strip())
        namespaces = {
            "cfdi": "http://www.sat.gob.mx/cfd/4",
            "cfdi3": "http://www.sat.gob.mx/cfd/3",
            "tfd": "http://www.sat.gob.mx/TimbreFiscalDigital",
        }
        tfd = root.find(".//tfd:TimbreFiscalDigital", namespaces)
        if tfd is not None:
            entities["uuid"] = tfd.attrib.get("UUID") or tfd.attrib.get("Uuid")
        emisor = root.find(".//cfdi:Emisor", namespaces)
        if emisor is None:
            emisor = root.find(".//cfdi3:Emisor", namespaces)
        receptor = root.find(".//cfdi:Receptor", namespaces)
        if receptor is None:
            receptor = root.find(".//cfdi3:Receptor", namespaces)
        if emisor is not None:
            entities["issuer_rfc"] = emisor.attrib.get("Rfc") or emisor.attrib.get(
                "rfc"
            )
            entities["issuer_name"] = emisor.attrib.get("Nombre") or emisor.attrib.get(
                "nombre"
            )
        if receptor is not None:
            entities["receiver_rfc"] = receptor.attrib.get(
                "Rfc"
            ) or receptor.attrib.get("rfc")
        entities["amount"] = root.attrib.get("Total") or root.attrib.get("total")
        entities["date"] = root.attrib.get("Fecha") or root.attrib.get("fecha")
        entities["currency"] = root.attrib.get("Moneda") or root.attrib.get("moneda")
        concept = root.find(".//cfdi:Concepto", namespaces)
        if concept is None:
            concept = root.find(".//cfdi3:Concepto", namespaces)
        if concept is not None:
            entities["concept"] = concept.attrib.get(
                "Descripcion"
            ) or concept.attrib.get("descripcion")
    except ET.ParseError:
        pass
    entities.setdefault("uuid", _first_match(raw, [r"uuid[:=\s]+([A-Fa-f0-9-]{20,})"]))
    entities.setdefault(
        "issuer_rfc", _first_match(raw, [r"rfc emisor[:=\s]+([A-Z&Ñ0-9]{12,13})"])
    )
    entities.setdefault(
        "receiver_rfc", _first_match(raw, [r"rfc receptor[:=\s]+([A-Z&Ñ0-9]{12,13})"])
    )
    entities.setdefault(
        "amount", _first_match(raw, [r"(?:total|monto|importe)[:=\s$]+([0-9,.]+)"])
    )
    entities.setdefault("date", _first_match(raw, [r"(20\d{2}-\d{2}-\d{2})"]))
    return {key: value for key, value in entities.items() if value not in (None, "")}


def _extract_payment_entities(text: str) -> Dict[str, Any]:
    raw = text or ""
    return {
        "amount": _first_match(raw, [r"(?:monto|importe|cantidad)[:=\s$]+([0-9,.]+)"]),
        "date": _first_match(raw, [r"(20\d{2}-\d{2}-\d{2})", r"fecha[:=\s]+([^\n]+)"]),
        "bank_reference": _first_match(
            raw,
            [r"clave de rastreo[:=\s]+([A-Z0-9-]+)", r"referencia[:=\s]+([A-Z0-9-]+)"],
        ),
        "beneficiary": _first_match(raw, [r"beneficiario[:=\s]+([^\n]+)"]),
        "payer": _first_match(
            raw, [r"ordenante[:=\s]+([^\n]+)", r"pagador[:=\s]+([^\n]+)"]
        ),
        "concept": _first_match(raw, [r"concepto[:=\s]+([^\n]+)"]),
    }


def _extract_expense_receipt_entities(text: str) -> Dict[str, Any]:
    raw = text or ""
    currency = _first_match(raw, [r"\b(MXN|USD|EUR)\b"])
    amount = _first_match(
        raw,
        [
            r"(?:total|importe|monto)[:=\s$]+([0-9][0-9,.]*)",
            r"\$\s*([0-9][0-9,.]*)",
        ],
    )
    date = _first_match(
        raw,
        [
            r"(20\d{2}[-/]\d{1,2}[-/]\d{1,2})",
            r"(\d{1,2}[-/]\d{1,2}[-/]20\d{2})",
            r"fecha[:=\s]+([^\n]+)",
        ],
    )
    merchant = _first_match(
        raw,
        [
            r"(?:comercio|establecimiento|proveedor|razon social)[:=\s]+([^\n]+)",
        ],
    )
    concept = _first_match(
        raw,
        [r"(?:concepto|descripcion)[:=\s]+([^\n]+)"],
    )
    return {
        key: value
        for key, value in {
            "amount": amount,
            "date": date,
            "merchant": merchant,
            "concept": concept,
            "currency": currency or "MXN",
        }.items()
        if value not in (None, "")
    }


def _extract_tournament_entities(text: str) -> Dict[str, Any]:
    raw = text or ""
    venues = re.findall(r"(?:sede|venue)[:=\s]+([^\n]+)", raw, flags=re.IGNORECASE)
    dates = re.findall(r"20\d{2}-\d{2}-\d{2}", raw)
    return {
        "tournament": _first_match(
            raw, [r"torneo[:=\s]+([^\n]+)", r"tournament[:=\s]+([^\n]+)"]
        ),
        "dates": dates[:8],
        "venues": venues[:8],
        "teams": re.findall(r"equipo[:=\s]+([^\n]+)", raw, flags=re.IGNORECASE)[:12],
        "commitments": re.findall(
            r"compromiso[:=\s]+([^\n]+)", raw, flags=re.IGNORECASE
        )[:12],
        "milestones": re.findall(r"milestone[:=\s]+([^\n]+)", raw, flags=re.IGNORECASE)[
            :12
        ],
    }


def _summary_for(document_type: str, entities: Mapping[str, Any]) -> str:
    if document_type == ACCOUNTING_BALANCE:
        return (
            f"Detecte balanza contable; periodo probable {entities.get('period') or 'pendiente'}, "
            f"{entities.get('account_count') or 0} cuentas, descuadre {entities.get('imbalance') or 'no calculado'}."
        )
    if document_type in {ROSTER, PLAYER_REGISTRATION, DOCUMENT_VALIDATION}:
        return (
            f"Detecte roster/documento de registro; equipo {entities.get('team_name') or 'pendiente'}, "
            f"categoria {entities.get('category') or 'pendiente'}, "
            f"{entities.get('player_count') or 0} jugadores."
        )
    if document_type in {CFDI_INVOICE, INVOICE_DOCUMENT}:
        return (
            f"Detecte CFDI/factura; UUID {entities.get('uuid') or 'pendiente'}, "
            f"proveedor {entities.get('issuer_rfc') or entities.get('issuer_name') or 'pendiente'}, "
            f"monto {entities.get('amount') or 'pendiente'}."
        )
    if document_type == PAYMENT_PROOF:
        return (
            f"Detecte comprobante de pago; monto {entities.get('amount') or 'pendiente'}, "
            f"referencia {entities.get('bank_reference') or 'pendiente'}."
        )
    if document_type == EXPENSE_RECEIPT:
        return (
            f"Detecte comprobante de gasto; comercio "
            f"{entities.get('merchant') or 'pendiente'}, monto "
            f"{entities.get('amount') or 'pendiente'}, fecha "
            f"{entities.get('date') or 'pendiente'}."
        )
    if document_type == TOURNAMENT_OPS:
        return f"Detecte documento operativo de torneo; torneo {entities.get('tournament') or 'pendiente'}."
    return "Documento generico; no hay workflow deterministico suficiente para proponer escritura."


def _missing_fields(document_type: str, entities: Mapping[str, Any]) -> List[str]:
    required = {
        ACCOUNTING_BALANCE: ["company", "project", "period"],
        ROSTER: ["tournament", "category", "team_name"],
        PLAYER_REGISTRATION: ["tournament", "category", "team_name"],
        DOCUMENT_VALIDATION: ["tournament", "category"],
        TOURNAMENT_OPS: ["tournament"],
        CFDI_INVOICE: ["uuid", "amount", "expense_or_document_candidate"],
        INVOICE_DOCUMENT: [
            "uuid_or_invoice_id",
            "amount",
            "expense_or_document_candidate",
        ],
        PAYMENT_PROOF: ["amount", "bank_reference", "document_or_expense_candidate"],
        EXPENSE_RECEIPT: [
            "amount",
            "date",
            "concept",
            "tournament",
            "payment_subject_type",
        ],
        UNKNOWN_OR_GENERIC: ["target_workflow"],
    }
    return [
        field for field in required.get(document_type, []) if not entities.get(field)
    ]


def _questions(document_type: str, missing_fields: Sequence[str]) -> List[str]:
    if not missing_fields:
        return []
    if document_type == ACCOUNTING_BALANCE:
        return [
            "Confirma empresa/proyecto y periodo antes de generar el preview contable."
        ]
    if document_type in {ROSTER, PLAYER_REGISTRATION, DOCUMENT_VALIDATION}:
        return [
            "Confirma torneo, categoria y equipo antes de crear una revision de registro."
        ]
    if document_type in {CFDI_INVOICE, INVOICE_DOCUMENT}:
        return [
            "Elige el gasto/documento candidato antes de vincular el CFDI o factura."
        ]
    if document_type == PAYMENT_PROOF:
        return ["Elige el documento/informe candidato antes de registrar el pago."]
    if document_type == EXPENSE_RECEIPT:
        return [
            "Confirma si es un gasto personal o un pago a tercero, "
            "y selecciona el torneo/proyecto antes de preparar el borrador."
        ]
    if document_type == TOURNAMENT_OPS:
        return ["Confirma el torneo o slug operativo al que pertenece este documento."]
    return ["Indica a que workflow pertenece este documento."]


def _risks(document_type: str, missing_fields: Sequence[str]) -> List[str]:
    risks = ["No se ejecutan escrituras durante intake documental."]
    if missing_fields:
        risks.append("Hay campos faltantes; se requiere aclaracion del usuario.")
    if document_type in {
        ACCOUNTING_BALANCE,
        CFDI_INVOICE,
        INVOICE_DOCUMENT,
        PAYMENT_PROOF,
        EXPENSE_RECEIPT,
    }:
        risks.append(
            "Documento financiero: cualquier cambio queda sujeto a confirmacion explicita."
        )
    if document_type == UNKNOWN_OR_GENERIC:
        risks.append("Clasificacion insuficiente para proponer acciones de escritura.")
    return risks


def build_document_intake_result(
    *,
    conversation_id: str = "",
    file_name: str = "",
    file_kind: str = "",
    text: str = "",
    records: Sequence[Mapping[str, Any]] | None = None,
    user_context: Mapping[str, Any] | None = None,
    supported_actions: Sequence[str] | None = None,
    writes_enabled: bool = False,
    evidence_sha256: str = "",
) -> DocumentIntakeResult:
    classification = classify_document(
        file_name=file_name,
        file_kind=file_kind,
        text=text,
        records=records,
    )
    doc_type = classification.detected_document_type
    if doc_type == ACCOUNTING_BALANCE:
        entities = _extract_accounting_entities(
            file_name=file_name, text=text, records=records
        )
    elif doc_type in {ROSTER, PLAYER_REGISTRATION, DOCUMENT_VALIDATION}:
        entities = _extract_roster_entities(records, text)
    elif doc_type in {CFDI_INVOICE, INVOICE_DOCUMENT}:
        entities = _extract_cfdi_entities(text)
    elif doc_type == PAYMENT_PROOF:
        entities = _extract_payment_entities(text)
    elif doc_type == EXPENSE_RECEIPT:
        entities = _extract_expense_receipt_entities(text)
    elif doc_type == TOURNAMENT_OPS:
        entities = _extract_tournament_entities(text)
    else:
        entities = {"text_preview": (text or "")[:500].strip()}

    context = dict(user_context or {})
    for key in [
        "company",
        "project",
        "tournament",
        "category",
        "team_name",
        "payment_subject_type",
    ]:
        if context.get(key) and not entities.get(key):
            entities[key] = context[key]

    intake_id = _stable_intake_id(
        conversation_id=conversation_id,
        file_name=file_name,
        text=text,
        records=records,
        evidence_sha256=evidence_sha256,
    )
    missing = _missing_fields(doc_type, entities)
    proposed = plan_document_actions(
        intake_id=intake_id,
        document_type=doc_type,
        entities=entities,
        missing_fields=missing,
        supported_actions=supported_actions,
        writes_enabled=writes_enabled,
    )
    safety = build_safety_status(
        proposed_actions=proposed,
        missing_fields=missing,
        blocked_reason=(
            "unsupported_document_type" if doc_type == UNKNOWN_OR_GENERIC else ""
        ),
    )
    return DocumentIntakeResult(
        intake_id=intake_id,
        file_name=file_name or "upload",
        file_kind=classification.file_kind or file_kind or "unknown",
        detected_document_type=doc_type,
        confidence=classification.confidence,
        summary=_summary_for(doc_type, entities),
        entities={
            key: value
            for key, value in entities.items()
            if value not in (None, "", [], {})
        },
        candidate_workflows=candidate_workflows_for_type(doc_type),
        missing_fields=missing,
        risks_or_caveats=_risks(doc_type, missing),
        proposed_actions=[action.to_dict() for action in proposed],
        questions_for_user=_questions(doc_type, missing),
        safety=safety,
        evidence_sha256=evidence_sha256,
    )
