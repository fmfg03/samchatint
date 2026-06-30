"""
Extract CFDI fields from PDF representations for form autofill.

Prefers embedded XML when present; falls back to text heuristics on digital PDFs.
"""

from __future__ import annotations

import io
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from devnous.document_parsing import parse_document_bytes

from .cfdi_parser import parse_cfdi_xml, parse_datetime

logger = logging.getLogger(__name__)

_UUID_PATTERN = re.compile(
    r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}"
)
_RFC_PATTERN = re.compile(
    r"\b([A-ZÑ&]{3,4}\d{6}[A-Z0-9]{3})\b",
    re.IGNORECASE,
)
_MONEDA_PATTERN = re.compile(
    r"(?i)(?:moneda|currency)\s*[:\-]?\s*([A-Z]{3})\b"
)
_AMOUNT_CAPTURE = r"([\d]{1,3}(?:,\d{3})*(?:\.\d{2})|\d+\.\d{2})"

_MONEY_PREFIX = r"(?:\$|MXN\s*)?"

_LABELED_AMOUNT_PATTERNS: Dict[str, List[str]] = {
    "subtotal": [
        rf"(?i)sub\s*total(?:es)?(?:\s+s\/\s*iva)?\s*[:\-]?"
        rf"\s*{_MONEY_PREFIX}\s*{_AMOUNT_CAPTURE}",
        rf"(?i)importe\s*(?:s\/iva|sin\s*iva)\s*[:\-]?"
        rf"\s*{_MONEY_PREFIX}\s*{_AMOUNT_CAPTURE}",
        rf"(?i)base\s*[:\-]?\s*{_MONEY_PREFIX}\s*{_AMOUNT_CAPTURE}",
    ],
    "descuento": [
        rf"(?i)descuento(?:s)?\s*[:\-]?\s*{_MONEY_PREFIX}\s*{_AMOUNT_CAPTURE}",
    ],
    "traslados": [
        rf"(?i)total\s+impuestos\s+trasladados\s*[:\-]?"
        rf"\s*{_MONEY_PREFIX}\s*{_AMOUNT_CAPTURE}",
        rf"(?i)impuestos\s+trasladados\s*[:\-]?"
        rf"\s*{_MONEY_PREFIX}\s*{_AMOUNT_CAPTURE}",
        rf"(?i)(?:i\.?\s*v\.?\s*a\.?|iva)"
        rf"(?:\s+\d{{1,2}}(?:\.\d+)?%?)?\s*[:\-]?"
        rf"\s*{_MONEY_PREFIX}\s*{_AMOUNT_CAPTURE}",
        rf"(?i)trasladado\s*[:\-]?\s*{_MONEY_PREFIX}\s*{_AMOUNT_CAPTURE}",
    ],
    "retenciones": [
        rf"(?i)total\s+impuestos\s+retenidos\s*[:\-]?"
        rf"\s*{_MONEY_PREFIX}\s*{_AMOUNT_CAPTURE}",
        rf"(?i)impuestos\s+retenidos\s*[:\-]?"
        rf"\s*{_MONEY_PREFIX}\s*{_AMOUNT_CAPTURE}",
        rf"(?i)(?:isr|i\.?\s*s\.?\s*r\.?)\s*(?:retenid[oa]s?)?"
        rf"\s*[:\-]?\s*{_MONEY_PREFIX}\s*{_AMOUNT_CAPTURE}",
        rf"(?i)retenc(?:i[oó]n|iones)\s*(?:isr|iva)?\s*[:\-]?"
        rf"\s*{_MONEY_PREFIX}\s*{_AMOUNT_CAPTURE}",
    ],
    "total": [
        rf"(?i)(?<![\w/])(?<!sub\s)(?<!sub-)total"
        rf"(?:\s+a\s+pagar|\s+del\s+comprobante|\s+con\s+letra)?"
        rf"\s*[:\-]?\s*{_MONEY_PREFIX}\s*{_AMOUNT_CAPTURE}",
        rf"(?i)importe\s+total\s*[:\-]?"
        rf"\s*{_MONEY_PREFIX}\s*{_AMOUNT_CAPTURE}",
        rf"(?i)monto\s+total\s*[:\-]?\s*{_MONEY_PREFIX}\s*{_AMOUNT_CAPTURE}",
    ],
}

_FECHA_PATTERNS = [
    r"(?i)(?:fecha(?:\s+y\s+hora)?(?:\s+de\s+"
    r"(?:emisi[oó]n|expedici[oó]n|certificaci[oó]n|timbrado))?|"
    r"fecha\s+de\s+emisi[oó]n)\s*[:\-]?\s*"
    r"(\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2})?)?|\d{2}/\d{2}/\d{4})",
    r"(?i)folio\s+fiscal[\s\S]{0,120}?(\d{4}-\d{2}-\d{2})",
]

_CONCEPTO_PATTERNS = [
    r"(?is)(?:descripci[oó]n(?:\s+del\s+producto|\s+del\s+concepto)?"
    r"|concepto)\s*[:\-]?\s*"
    r"([^\n\r]{4,200})",
    r"(?is)(?:producto\s+o\s+servicio)\s*[:\-]?\s*([^\n\r]{4,200})",
]

_SERIE_PATTERN = re.compile(r"(?i)serie\s*[:\-]?\s*([A-Za-z0-9\-]+)")
_FOLIO_PATTERN = re.compile(r"(?i)folio\s*[:\-]?\s*([A-Za-z0-9\-]+)")


def _decode_embedded_xml(blob: bytes) -> Optional[str]:
    for encoding in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            text = blob.decode(encoding)
        except UnicodeDecodeError:
            continue
        if "Comprobante" in text and (
            "cfdi" in text.lower() or "sat.gob.mx" in text or "Comprobante" in text
        ):
            return text
    return None


def _extract_embedded_xml_strings(pdf_bytes: bytes) -> List[str]:
    found: List[str] = []

    def _append_decoded(blob: bytes) -> None:
        decoded = _decode_embedded_xml(blob)
        if decoded and decoded not in found:
            found.append(decoded)

    try:
        from pypdf import PdfReader
    except ImportError:
        logger.warning("pypdf is not installed; PDF CFDI autofill unavailable")
        _scan_raw_xml_chunks(pdf_bytes, _append_decoded)
        return found

    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
    except Exception as exc:
        logger.warning("Could not open PDF for CFDI extraction: %s", exc)
        _scan_raw_xml_chunks(pdf_bytes, _append_decoded)
        return found

    attachments = getattr(reader, "attachments", None) or {}
    for _name, content_list in attachments.items():
        for content in content_list or []:
            if isinstance(content, (bytes, bytearray)):
                _append_decoded(bytes(content))

    for page in reader.pages:
        try:
            contents = page.get_contents()
        except Exception:
            contents = None
        if contents is None:
            continue
        try:
            data = contents.get_data()
        except Exception:
            continue
        if isinstance(data, (bytes, bytearray)):
            _append_decoded(bytes(data))

    _scan_raw_xml_chunks(pdf_bytes, _append_decoded)
    return found


def _scan_raw_xml_chunks(pdf_bytes: bytes, collector) -> None:
    markers = (
        b"<?xml",
        b"<cfdi:Comprobante",
        b"<Comprobante ",
        b"<Comprobante>",
    )
    for marker in markers:
        start = 0
        while True:
            idx = pdf_bytes.find(marker, start)
            if idx == -1:
                break
            end = -1
            closing_tag = b""
            for closing in (b"</cfdi:Comprobante>", b"</Comprobante>"):
                candidate = pdf_bytes.find(closing, idx)
                if candidate != -1 and (end == -1 or candidate < end):
                    end = candidate
                    closing_tag = closing
            if end != -1:
                collector(pdf_bytes[idx : end + len(closing_tag)])
            start = idx + len(marker)


def _extract_pdf_text(pdf_bytes: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        return ""

    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
    except Exception:
        return ""

    parts: List[str] = []
    for page in reader.pages:
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        if text:
            parts.append(text)
    return "\n".join(parts)


def _clean_amount(raw: str) -> Optional[float]:
    cleaned = raw.replace(",", "").strip()
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _find_labeled_amount(text: str, labels: List[str]) -> Optional[float]:
    for pattern in labels:
        match = re.search(pattern, text)
        if not match or not match.lastindex:
            continue
        amount = _clean_amount(match.group(1))
        if amount is not None:
            return amount
    return None


def _find_fecha(text: str) -> Optional[str]:
    for pattern in _FECHA_PATTERNS:
        match = re.search(pattern, text)
        if not match:
            continue
        raw = match.group(1).strip()
        if "/" in raw:
            parts = raw.split("/")
            if len(parts) == 3:
                return f"{parts[2]}-{parts[1]}-{parts[0]}"
        return raw
    iso = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", text)
    return iso.group(1) if iso else None


def _find_concepto(text: str) -> str:
    for pattern in _CONCEPTO_PATTERNS:
        match = re.search(pattern, text)
        if match:
            concepto = re.sub(r"\s+", " ", match.group(1)).strip(" :-")
            if len(concepto) >= 4:
                return concepto[:200]
    return ""


_RFC_CAPTURE = r"([A-ZÑ&]{3,4}\d{6}[A-Z0-9]{3})"

# Highest priority: explicit Emisor RFC labels anywhere in the document.
_EMISOR_RFC_LABELED_PATTERNS = [
    rf"(?i)r\.?\s*f\.?\s*c\.?\s*(?:del\s+)?emisor\s*[:\-]?\s*{_RFC_CAPTURE}",
    rf"(?i)emisor\s+rfc\s*[:\-]?\s*{_RFC_CAPTURE}",
    rf"(?i)rfc\s+emisor\s*[:\-]?\s*{_RFC_CAPTURE}",
    rf"(?i)\bemisor\b[^\n\r]{{0,80}}?{_RFC_CAPTURE}",
]

_RECEPTOR_RFC_LABELED_PATTERNS = [
    rf"(?i)r\.?\s*f\.?\s*c\.?\s*(?:del\s+)?receptor\s*[:\-]?\s*{_RFC_CAPTURE}",
    rf"(?i)receptor\s+rfc\s*[:\-]?\s*{_RFC_CAPTURE}",
    rf"(?i)rfc\s+receptor\s*[:\-]?\s*{_RFC_CAPTURE}",
]

_EMISOR_BLOCK_HEADER = re.compile(r"(?im)^[^\n]*\bemisor\b[^\n]*$")
_RECEPTOR_BLOCK_HEADER = re.compile(r"(?im)^[^\n]*\breceptor\b[^\n]*$")
_EMISOR_TO_RECEPTOR_SPAN = re.compile(r"(?is)\bemisor\b(.*?)\breceptor\b")


def _normalize_rfc(value: str) -> str:
    return (value or "").strip().upper()


def _find_receptor_rfc(text: str) -> str:
    if not text:
        return ""
    for pattern in _RECEPTOR_RFC_LABELED_PATTERNS:
        match = re.search(pattern, text)
        if match and match.lastindex:
            return _normalize_rfc(match.group(1))
    header = _RECEPTOR_BLOCK_HEADER.search(text)
    if header:
        window = text[header.end() : header.end() + 200]
        match = _RFC_PATTERN.search(window)
        if match:
            return _normalize_rfc(match.group(1))
    return ""


def _find_emisor_rfc_labeled(text: str) -> str:
    for pattern in _EMISOR_RFC_LABELED_PATTERNS:
        match = re.search(pattern, text)
        if match and match.lastindex:
            return _normalize_rfc(match.group(1))
    return ""


def _find_emisor_rfc_in_emisor_block(text: str) -> str:
    """RFC in the SAT block that starts at a line containing the word Emisor."""
    for header in _EMISOR_BLOCK_HEADER.finditer(text):
        line = header.group(0)
        inline = re.search(
            rf"(?i)\bemisor\b[^\n\r]{{0,80}}?{_RFC_CAPTURE}",
            line,
        )
        if inline and inline.lastindex:
            return _normalize_rfc(inline.group(1))

        window = text[header.end() : header.end() + 220]
        receptor_in_window = re.search(r"(?im)^[^\n]*\breceptor\b", window)
        if receptor_in_window:
            window = window[: receptor_in_window.start()]
        match = _RFC_PATTERN.search(window)
        if match:
            return _normalize_rfc(match.group(1))
    return ""


def _find_emisor_rfc_between_emisor_and_receptor(text: str) -> str:
    span = _EMISOR_TO_RECEPTOR_SPAN.search(text)
    if not span:
        return ""
    match = _RFC_PATTERN.search(span.group(1))
    if match:
        return _normalize_rfc(match.group(1))
    return ""


def _find_emisor_rfc_fallback(text: str, *, receptor_rfc: str) -> str:
    for match in _RFC_PATTERN.finditer(text):
        candidate = _normalize_rfc(match.group(1))
        if receptor_rfc and candidate == receptor_rfc:
            continue
        return candidate
    return ""


def _find_emisor_rfc(text: str) -> str:
    if not text:
        return ""

    labeled = _find_emisor_rfc_labeled(text)
    if labeled:
        return labeled

    in_block = _find_emisor_rfc_in_emisor_block(text)
    if in_block:
        return in_block

    between = _find_emisor_rfc_between_emisor_and_receptor(text)
    if between:
        return between

    receptor_rfc = _find_receptor_rfc(text)
    return _find_emisor_rfc_fallback(text, receptor_rfc=receptor_rfc)


_NOMBRE_CAPTURE = r"([^\n\r]{3,200})"

_EMISOR_NOMBRE_LABELED_PATTERNS = [
    rf"(?i)(?:nombre|raz[oó]n\s+social)"
    rf"(?:\s+(?:o\s+raz[oó]n\s+social\s+)?del?\s+)?"
    rf"[ \t]*emisor\s*[:\-]?\s*{_NOMBRE_CAPTURE}",
    rf"(?i)(?:nombre|raz[oó]n\s+social)[ \t]+emisor\s*[:\-]?"
    rf"\s*{_NOMBRE_CAPTURE}",
    rf"(?im)^[^\n]*\bemisor[ \t]+(?:nombre|raz[oó]n\s+social)"
    rf"\s*[:\-]?\s*{_NOMBRE_CAPTURE}",
    rf"(?i)(?:nombre|raz[oó]n\s+social)\s*(?:del\s+)?emisor"
    rf"\s*[:\-]\s*{_NOMBRE_CAPTURE}",
]

_EMISOR_SECTION_HEADER = re.compile(r"(?im)^\s*emisor\s*$")
_NOMBRE_LABEL_LINE = re.compile(r"(?i)^(nombre|raz[oó]n\s+social)\b")
_RFC_LABEL_LINE = re.compile(r"(?i)^(r\.?\s*f\.?\s*c|rfc)\b")


def _clean_emisor_nombre(raw: str) -> str:
    cleaned = re.sub(r"\s+", " ", (raw or "").strip(" :-"))
    if len(cleaned) < 3:
        return ""
    if _RFC_PATTERN.fullmatch(cleaned):
        return ""
    return cleaned[:200]


def _find_emisor_nombre_labeled(text: str) -> str:
    for pattern in _EMISOR_NOMBRE_LABELED_PATTERNS:
        match = re.search(pattern, text)
        if match and match.lastindex:
            nombre = _clean_emisor_nombre(match.group(1))
            if nombre:
                return nombre
    return ""


def _emisor_block_window(text: str, *, after: int) -> str:
    window = text[after : after + 400]
    receptor_in_window = re.search(r"(?im)^[^\n]*\breceptor\b", window)
    if receptor_in_window:
        window = window[: receptor_in_window.start()]
    return window


def _extract_nombre_from_label_line(line: str) -> str:
    match = re.search(
        rf"(?i)(?:nombre|raz[oó]n\s+social)\s*[:\-]\s*{_NOMBRE_CAPTURE}",
        line,
    )
    if match and match.lastindex:
        return _clean_emisor_nombre(match.group(1))
    return ""


def _candidate_emisor_nombre_lines(block: str):
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if _RFC_PATTERN.fullmatch(stripped):
            break
        if _RFC_LABEL_LINE.search(stripped) and _RFC_PATTERN.search(stripped):
            continue
        label_nombre = _extract_nombre_from_label_line(stripped)
        if label_nombre:
            yield label_nombre
            continue
        if _NOMBRE_LABEL_LINE.search(stripped) and ":" not in stripped:
            yield _clean_emisor_nombre(stripped)
            continue
        if _NOMBRE_LABEL_LINE.search(stripped):
            continue
        if re.search(r"(?i)^(regimen|registro|codigo postal|cp)\b", stripped):
            continue
        candidate = _clean_emisor_nombre(stripped)
        if candidate:
            yield candidate


def _find_emisor_nombre_in_emisor_block(text: str) -> str:
    for header in _EMISOR_SECTION_HEADER.finditer(text):
        window = _emisor_block_window(text, after=header.end())
        for candidate in _candidate_emisor_nombre_lines(window):
            if candidate:
                return candidate
    return ""


def _find_emisor_nombre_between_emisor_and_receptor(text: str) -> str:
    span = _EMISOR_TO_RECEPTOR_SPAN.search(text)
    if not span:
        return ""
    block = span.group(1)
    labeled = _find_emisor_nombre_labeled(block)
    if labeled:
        return labeled
    for candidate in _candidate_emisor_nombre_lines(block):
        if candidate:
            return candidate
    return ""


def _find_emisor_nombre(text: str) -> str:
    if not text:
        return ""

    labeled = _find_emisor_nombre_labeled(text)
    if labeled:
        return labeled

    in_block = _find_emisor_nombre_in_emisor_block(text)
    if in_block:
        return in_block

    return _find_emisor_nombre_between_emisor_and_receptor(text)


def _normalize_text_parsed_amounts(
    *,
    subtotal: Optional[float],
    descuento: Optional[float],
    traslados: Optional[float],
    retenciones: Optional[float],
    total: Optional[float],
) -> Dict[str, float]:
    desc = descuento or 0.0
    tras = traslados or 0.0
    ret = retenciones or 0.0
    sub = subtotal
    tot = total

    if tot is not None and sub is not None and tras == 0.0 and ret == 0.0:
        net = round(tot - sub + desc, 2)
        if net > 0:
            tras = net
        elif net < 0:
            ret = round(abs(net), 2)

    if tot is None and sub is not None:
        tot = round(sub - desc + tras - ret, 2)

    if sub is None and tot is not None:
        sub = round(tot - tras + ret + desc, 2)

    return {
        "subtotal": max(sub or 0.0, 0.0),
        "descuento": max(desc, 0.0),
        "traslados": max(tras, 0.0),
        "retenciones": max(ret, 0.0),
        "total": max(tot or 0.0, 0.0),
    }


def _parse_from_text(text: str) -> Dict[str, Any]:
    if not text.strip():
        return {}

    uuid_match = _UUID_PATTERN.search(text)
    moneda_match = _MONEDA_PATTERN.search(text)
    serie_match = _SERIE_PATTERN.search(text)
    folio_match = _FOLIO_PATTERN.search(text)
    fecha_raw = _find_fecha(text)
    concepto = _find_concepto(text)

    amounts = _normalize_text_parsed_amounts(
        subtotal=_find_labeled_amount(text, _LABELED_AMOUNT_PATTERNS["subtotal"]),
        descuento=_find_labeled_amount(text, _LABELED_AMOUNT_PATTERNS["descuento"]),
        traslados=_find_labeled_amount(text, _LABELED_AMOUNT_PATTERNS["traslados"]),
        retenciones=_find_labeled_amount(text, _LABELED_AMOUNT_PATTERNS["retenciones"]),
        total=_find_labeled_amount(text, _LABELED_AMOUNT_PATTERNS["total"]),
    )

    traslados_list: List[Dict[str, Any]] = []
    retenciones_list: List[Dict[str, Any]] = []
    if amounts["traslados"] > 0:
        traslados_list.append({"impuesto": "002", "importe": amounts["traslados"]})
    if amounts["retenciones"] > 0:
        retenciones_list.append({"impuesto": "001", "importe": amounts["retenciones"]})

    fecha_val: Optional[datetime] = None
    if fecha_raw:
        fecha_val = parse_datetime(fecha_raw)

    conceptos_list: List[Dict[str, Any]] = []
    if concepto:
        conceptos_list.append(
            {
                "descripcion": concepto,
                "importe": amounts["subtotal"],
                "impuestos": traslados_list,
            }
        )

    return {
        "emisor_rfc": _find_emisor_rfc(text),
        "emisor_nombre": _find_emisor_nombre(text),
        "subtotal": amounts["subtotal"],
        "descuento": amounts["descuento"],
        "moneda": (moneda_match.group(1).upper() if moneda_match else "MXN"),
        "serie": serie_match.group(1).strip() if serie_match else "",
        "folio": folio_match.group(1).strip() if folio_match else "",
        "cfdi_uuid": uuid_match.group(0).upper() if uuid_match else "",
        "total": amounts["total"],
        "total_impuestos_trasladados": amounts["traslados"],
        "descripcion_concepto_principal": concepto,
        "fecha": fecha_val,
        "conceptos": conceptos_list,
        "impuestos_detalle": {
            "traslados": traslados_list,
            "retenciones": retenciones_list,
        },
    }


def extract_cfdi_xml_from_pdf(pdf_bytes: bytes) -> Optional[str]:
    """Return embedded CFDI XML text from a PDF when present."""
    if not pdf_bytes:
        return None
    for xml_text in _extract_embedded_xml_strings(pdf_bytes):
        if parse_cfdi_xml(xml_text):
            return xml_text
    return None


def parse_cfdi_pdf(pdf_bytes: bytes) -> Dict[str, Any]:
    """
    Parse a CFDI PDF and return a dict compatible with ``parse_cfdi_xml`` output.
    """
    if not pdf_bytes:
        return {}

    embedded_xml = extract_cfdi_xml_from_pdf(pdf_bytes)
    if embedded_xml:
        parsed = parse_cfdi_xml(embedded_xml)
        if parsed:
            return parsed

    mineru_result = parse_document_bytes(pdf_bytes, suffix=".pdf")
    if mineru_result.has_text:
        parsed = _parse_from_text(mineru_result.text)
        if parsed and (parsed.get("cfdi_uuid") or "").strip():
            return parsed

    return _parse_from_text(_extract_pdf_text(pdf_bytes))
