"""Resolve CFDI fiscal data from uploaded XML and/or PDF bytes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from .cfdi_parser import parse_cfdi_xml
from .cfdi_pdf_reader import extract_cfdi_xml_from_pdf, parse_cfdi_pdf


@dataclass(frozen=True)
class ResolvedCfdiUpload:
    parsed: Dict[str, Any]
    xml_text: Optional[str]
    from_uploaded_xml: bool


def _decode_xml_bytes(xml_bytes: bytes) -> Optional[str]:
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return xml_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    return None


def _non_empty_upload_bytes(data: Optional[bytes]) -> bool:
    return bool(data and data.strip())


def resolve_cfdi_upload(
    *,
    xml_bytes: Optional[bytes] = None,
    pdf_bytes: Optional[bytes] = None,
) -> Tuple[Optional[ResolvedCfdiUpload], Optional[str]]:
    """
    Resolve CFDI data for submit-time ingestion.

    Non-empty uploaded XML takes precedence over PDF. Empty or missing XML falls
    back to PDF parsing (embedded XML first, then text heuristics).
    """
    if _non_empty_upload_bytes(xml_bytes):
        assert xml_bytes is not None
        xml_text = _decode_xml_bytes(xml_bytes)
        if xml_text is None:
            return None, "El CFDI XML no usa una codificación válida."
        parsed = parse_cfdi_xml(xml_text)
        if not parsed:
            return None, "El archivo XML no es un CFDI válido."
        return ResolvedCfdiUpload(
            parsed=parsed,
            xml_text=xml_text,
            from_uploaded_xml=True,
        ), None

    if _non_empty_upload_bytes(pdf_bytes):
        assert pdf_bytes is not None
        embedded_xml = extract_cfdi_xml_from_pdf(pdf_bytes)
        if embedded_xml:
            parsed = parse_cfdi_xml(embedded_xml)
            if parsed:
                return ResolvedCfdiUpload(
                    parsed=parsed,
                    xml_text=embedded_xml,
                    from_uploaded_xml=False,
                ), None

        parsed = parse_cfdi_pdf(pdf_bytes)
        if not parsed or not (parsed.get("cfdi_uuid") or "").strip():
            return None, "No se pudieron extraer datos fiscales del CFDI PDF."
        return ResolvedCfdiUpload(
            parsed=parsed,
            xml_text=None,
            from_uploaded_xml=False,
        ), None

    return None, None
