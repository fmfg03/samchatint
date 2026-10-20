"""Local, contextual CFDI payment evidence and strict DIRECT tax proof.

These readers neither download SAT evidence nor mutate fiscal or accounting state.
REP availability is informational; it never authorizes a fiscal treatment.
"""

from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace
from typing import Any

from sqlalchemy import func, select

from ..models import CFDIReport
from .cfdi_parser import parse_cfdi_xml, parse_datetime

EVIDENCE_VERSION = "amex_direct_fiscal_evidence_v1"
POLICY_VERSION = "plataforma_amex_xml_iva_optional_rep_v1"
PLATAFORMA_RECEIVER_RFC = "PSP1705058S4"
CFDI_NS = "http://www.sat.gob.mx/cfd/4"
PAYMENT_NAMESPACES = {"http://www.sat.gob.mx/Pagos20", "http://www.sat.gob.mx/Pagos"}


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _number(value: Any) -> Decimal:
    try:
        result = Decimal(str(value))
        if not result.is_finite() or result < 0:
            raise InvalidOperation
        return result
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("direct_cfdi_tax_evidence_incomplete") from exc


def _children(node: ET.Element, local: str) -> list[ET.Element]:
    return [child for child in node if child.tag == f"{{{CFDI_NS}}}{local}"]


def _tax_amount(node: ET.Element) -> Decimal:
    if node.get("Impuesto") not in {"001", "002", "003"}:
        raise ValueError("direct_cfdi_tax_evidence_incomplete")
    factor = node.get("TipoFactor")
    if factor == "Exento":
        _number(node.get("Base"))
        if node.get("Importe") is not None:
            raise ValueError("direct_cfdi_tax_evidence_inconsistent")
        return Decimal(0)
    if node.tag.endswith("Traslado"):
        if factor not in {"Tasa", "Cuota"}:
            raise ValueError("direct_cfdi_tax_evidence_incomplete")
        base = _number(node.get("Base"))
        rate = _number(node.get("TasaOCuota"))
        amount = _number(node.get("Importe"))
        if abs(base * rate - amount) > Decimal("0.01"):
            raise ValueError("direct_cfdi_tax_evidence_inconsistent")
        return amount
    return _number(node.get("Importe"))


def strict_direct_cfdi(cfdi: Any) -> SimpleNamespace:
    """Require real XML tax detail; valid zero/exempt/non-object remains valid.

    Canonical parsing and the shared posting formulas remain the owners of tax
    arithmetic. This validates evidence before those formulas can use fallbacks.
    """
    from .amex_direct_matching_service import cfdi_identity

    if cfdi is None:
        raise ValueError("direct_cfdi_tax_evidence_incomplete")
    cfdi_identity(cfdi)
    parsed = parse_cfdi_xml(cfdi.xml_raw)
    try:
        root = ET.fromstring(cfdi.xml_raw)
        if root.tag != f"{{{CFDI_NS}}}Comprobante":
            raise ValueError("direct_cfdi_tax_evidence_incomplete")
        subtotal = _number(root.get("SubTotal"))
        discount = _number(root.get("Descuento", "0"))
        total = _number(root.get("Total"))
        groups = _children(root, "Conceptos")
        concepts = _children(groups[0], "Concepto") if len(groups) == 1 else []
        if not concepts:
            raise ValueError("direct_cfdi_tax_evidence_incomplete")
        concept_total = sum((_number(c.get("Importe")) for c in concepts), Decimal(0))
        if abs(concept_total - subtotal) > Decimal("0.01"):
            raise ValueError("direct_cfdi_tax_evidence_inconsistent")
        concept_transfers: dict[tuple[str, str], Decimal] = {}
        concept_retentions: dict[str, Decimal] = {}
        for concept in concepts:
            tax_groups = _children(concept, "Impuestos")
            transfers = [
                t
                for g in tax_groups
                for ts in _children(g, "Traslados")
                for t in _children(ts, "Traslado")
            ]
            retention_nodes = [
                t
                for g in tax_groups
                for ts in _children(g, "Retenciones")
                for t in _children(ts, "Retencion")
            ]
            if concept.get("ObjetoImp") == "01" and (transfers or retention_nodes):
                raise ValueError("direct_cfdi_tax_evidence_inconsistent")
            for retention in retention_nodes:
                code = str(retention.get("Impuesto"))
                concept_retentions[code] = concept_retentions.get(
                    code, Decimal(0)
                ) + _tax_amount(retention)
            if concept.get("ObjetoImp") not in {"01", "04"} and not transfers:
                raise ValueError("direct_cfdi_tax_evidence_incomplete")
            for transfer in transfers:
                key = (str(transfer.get("Impuesto")), str(transfer.get("TipoFactor")))
                concept_transfers[key] = concept_transfers.get(
                    key, Decimal(0)
                ) + _tax_amount(transfer)
        taxes = _children(root, "Impuestos")
        transfers = [
            t
            for g in taxes
            for ts in _children(g, "Traslados")
            for t in _children(ts, "Traslado")
        ]
        retentions = [
            t
            for g in taxes
            for ts in _children(g, "Retenciones")
            for t in _children(ts, "Retencion")
        ]
        global_transfers: dict[tuple[str, str], Decimal] = {}
        for transfer in transfers:
            key = (str(transfer.get("Impuesto")), str(transfer.get("TipoFactor")))
            global_transfers[key] = global_transfers.get(key, Decimal(0)) + _tax_amount(
                transfer
            )
        if set(global_transfers) != set(concept_transfers) or any(
            abs(value - global_transfers[key]) > Decimal("0.01")
            for key, value in concept_transfers.items()
        ):
            raise ValueError("direct_cfdi_tax_evidence_inconsistent")
        transferred = sum(global_transfers.values(), Decimal(0))
        global_retentions: dict[str, Decimal] = {}
        for retention in retentions:
            code = str(retention.get("Impuesto"))
            global_retentions[code] = global_retentions.get(
                code, Decimal(0)
            ) + _tax_amount(retention)
        if set(global_retentions) != set(concept_retentions) or any(
            abs(value - global_retentions[key]) > Decimal("0.01")
            for key, value in concept_retentions.items()
        ):
            raise ValueError("direct_cfdi_tax_evidence_inconsistent")
        retained = sum(global_retentions.values(), Decimal(0))
        for tax in taxes:
            for attribute, expected in (
                ("TotalImpuestosTrasladados", transferred),
                ("TotalImpuestosRetenidos", retained),
            ):
                if tax.get(attribute) is not None and abs(
                    _number(tax.get(attribute)) - expected
                ) > Decimal("0.01"):
                    raise ValueError("direct_cfdi_tax_evidence_inconsistent")
        local_net = sum(
            (
                _number(t["importe"]) * (-1 if t["tipo"] == "retencion" else 1)
                for t in parsed["impuestos_detalle"]["locales"]
            ),
            Decimal(0),
        )
        if abs(
            subtotal - discount + transferred - retained + local_net - total
        ) > Decimal("0.01"):
            raise ValueError("direct_cfdi_tax_evidence_inconsistent")
        return SimpleNamespace(**parsed)
    except (ET.ParseError, KeyError, TypeError) as exc:
        raise ValueError("direct_cfdi_tax_evidence_incomplete") from exc


def _payment_descriptor(
    cfdi: Any, receiver_rfc: str
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    from .amex_direct_matching_service import cfdi_identity

    try:
        identity = cfdi_identity(cfdi)
        if identity["rfc_receptor"] != receiver_rfc:
            raise ValueError("receiver_mismatch")
        parsed = parse_cfdi_xml(cfdi.xml_raw)
        raw_method = str(parsed.get("metodo_pago") or "").upper()
        stored_method = (
            str(getattr(cfdi, "metodo_pago", "") or "").upper().split(" - ")[0]
        )
        method = (
            raw_method
            if raw_method in {"PUE", "PPD"} and stored_method in {"", raw_method}
            else None
        )
        invoice_date = parsed.get("fecha")
        return {
            "version": EVIDENCE_VERSION,
            "payment_method": method,
            "payment_method_status": "verified" if method else "pending",
            "xml_hash": identity["xml_hash"],
            "invoice_date": (invoice_date.isoformat() if invoice_date else None),
            "complement_status": "not_local",
            "complements": [],
        }, identity
    except (ValueError, TypeError):
        return {
            "version": EVIDENCE_VERSION,
            "payment_method": None,
            "payment_method_status": "pending",
            "xml_hash": None,
            "invoice_date": None,
            "complement_status": "not_verifiable",
            "complements": [],
        }, None


async def read_direct_cfdi_fiscal_evidence_batch(
    session: Any,
    cfdis: list[Any],
    *,
    receiver_rfc: str,
    company_code: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Read local REP once for already-authorized DIRECT invoice candidates.

    The caller owns card/context authorization. Exact receiver scope is mandatory;
    neither a display name nor company-code similarity establishes this policy.
    """
    receiver_rfc = str(receiver_rfc).strip().upper()
    descriptors = {
        str(cfdi.id): _payment_descriptor(cfdi, receiver_rfc) for cfdi in cfdis
    }
    identities = [identity for _, identity in descriptors.values() if identity]
    issuers = {identity["rfc_emisor"] for identity in identities}
    local_receipts = []
    if issuers:
        with session.no_autoflush:
            local_receipts = list(
                (
                    await session.execute(
                        select(CFDIReport)
                        .where(
                            CFDIReport.origen == "sat",
                            func.upper(CFDIReport.tipo_de_comprobante) == "P",
                            func.upper(CFDIReport.receptor_rfc) == receiver_rfc,
                            func.upper(CFDIReport.emisor_rfc).in_(sorted(issuers)),
                        )
                        .order_by(CFDIReport.id)
                    )
                )
                .scalars()
                .all()
            )
    for evidence, identity in descriptors.values():
        evidence["policy_applies"] = (
            receiver_rfc == PLATAFORMA_RECEIVER_RFC and identity is not None
        )
        evidence["policy"] = {
            "version": (
                POLICY_VERSION
                if receiver_rfc == PLATAFORMA_RECEIVER_RFC and identity
                else None
            ),
            "company_code": company_code,
            "receiver_rfc": receiver_rfc,
            "rep_required": (
                False if receiver_rfc == PLATAFORMA_RECEIVER_RFC and identity else None
            ),
        }
        if identity:
            invalid = False
            for receipt in local_receipts:
                if str(receipt.emisor_rfc).strip().upper() != identity["rfc_emisor"]:
                    continue
                try:
                    if not receipt.xml_parsed or not receipt.xml_raw:
                        raise ValueError
                    parsed = parse_cfdi_xml(receipt.xml_raw)
                    if (
                        parsed.get("tipo_de_comprobante") != "P"
                        or str(parsed.get("receptor_rfc")).upper() != receiver_rfc
                        or str(parsed.get("emisor_rfc")).upper()
                        != identity["rfc_emisor"]
                        or str(parsed.get("cfdi_uuid")).upper()
                        != str(receipt.cfdi_uuid).upper()
                    ):
                        raise ValueError
                    root = ET.fromstring(receipt.xml_raw)
                    payments = [
                        node
                        for node in root.iter()
                        if any(
                            node.tag == f"{{{namespace}}}Pago"
                            for namespace in PAYMENT_NAMESPACES
                        )
                        and any(
                            child.tag == f"{{{namespace}}}DoctoRelacionado"
                            and str(child.get("IdDocumento") or "").upper()
                            == identity["uuid"]
                            for child in node
                            for namespace in PAYMENT_NAMESPACES
                        )
                    ]
                    if payments:
                        dates = [
                            parse_datetime(node.get("FechaPago", ""))
                            for node in payments
                        ]
                        if any(stamp is None for stamp in dates):
                            raise ValueError
                        evidence["complements"].append(
                            {
                                "uuid": str(receipt.cfdi_uuid).upper(),
                                "xml_hash": _hash(receipt.xml_raw),
                                "payment_dates": sorted(
                                    {stamp.isoformat() for stamp in dates if stamp}
                                ),
                            }
                        )
                except (ValueError, ET.ParseError, TypeError):
                    invalid = True
            evidence["complement_status"] = (
                "available"
                if evidence["complements"]
                else ("not_verifiable" if invalid else "not_local")
            )
        evidence["proof_hash"] = _hash(evidence)
    return {key: evidence for key, (evidence, _) in descriptors.items()}


async def read_direct_cfdi_fiscal_evidence(
    session: Any, cfdi: Any, *, receiver_rfc: str, company_code: str | None = None
) -> dict[str, Any]:
    """Single-invoice convenience reader; use the batch reader for a statement."""
    return (
        await read_direct_cfdi_fiscal_evidence_batch(
            session, [cfdi], receiver_rfc=receiver_rfc, company_code=company_code
        )
    )[str(cfdi.id)]
