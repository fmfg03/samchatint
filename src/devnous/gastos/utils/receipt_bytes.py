"""
Helpers for expense receipts and Adjunto payloads: base64 decode, MIME sniffing,
lightweight batch metadata for list UIs, and safe-ish remote URL fetch for Tocino links.
"""

from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass
from html import escape
from ipaddress import ip_address, ip_network
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple
from urllib.parse import urlparse
from uuid import UUID

import httpx
from sqlalchemy import Select, literal, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from devnous.gastos.models import Adjunto, ExpenseReport

LEGACY_RECEIPT_KEY = "legacy-receipt"

# Decode / upload limits (bytes)
MAX_DECODE_BYTES = 40 * 1024 * 1024
MAX_SOLICITUD_PDF_BYTES = 15 * 1024 * 1024
MAX_SOLICITUD_ATTACHMENT_BYTES = 15 * 1024 * 1024
MAX_REMOTE_URL_BYTES = 40 * 1024 * 1024
UPLOAD_READ_CHUNK_BYTES = 1024 * 1024

ALLOWED_SOLICITUD_ATTACHMENT_MIME_TYPES = frozenset(
    {
        "application/pdf",
        "application/xml",
        "text/xml",
        "image/jpeg",
        "image/png",
        "image/gif",
        "image/webp",
        "text/plain",
        "text/csv",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }
)

_HTTP_TIMEOUT = httpx.Timeout(60.0, connect=15.0)

# Private / loopback — block SSRF on URL fetches (Tocino uses public HTTPS).
_PRIVATE_NETS = (
    ip_network("10.0.0.0/8"),
    ip_network("172.16.0.0/12"),
    ip_network("192.168.0.0/16"),
    ip_network("127.0.0.0/8"),
    ip_network("::1/128"),
    ip_network("fc00::/7"),
    ip_network("fe80::/10"),
)

_ADJUNTO_COLUMN_CACHE: Optional[Set[str]] = None


class ReceiptDecodeError(ValueError):
    """Invalid or oversized receipt payload."""


async def read_upload_limited(
    upload: Any,
    *,
    max_bytes: int,
    too_large_message: str,
    empty_message: Optional[str] = None,
    chunk_size: int = UPLOAD_READ_CHUNK_BYTES,
) -> bytes:
    chunks: List[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(chunk_size)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise ValueError(too_large_message)
        chunks.append(chunk)
    payload = b"".join(chunks)
    if empty_message is not None and not payload:
        raise ValueError(empty_message)
    return payload


def is_probably_url(value: Optional[str]) -> bool:
    if not value:
        return False
    s = value.strip().lower()
    return s.startswith("http://") or s.startswith("https://")


def _strip_data_url_prefix(raw: str) -> str:
    s = (raw or "").strip()
    if s.startswith("data:") and "base64," in s:
        return s.split("base64,", 1)[1]
    return s


def decode_base64_to_bytes(raw: str, max_size: int = MAX_DECODE_BYTES) -> bytes:
    """Decode base64 text to bytes with size cap."""
    payload = _strip_data_url_prefix(raw)
    payload = re.sub(r"\s+", "", payload, flags=re.UNICODE)
    if not payload:
        raise ReceiptDecodeError("Comprobante vacío")
    try:
        decoded = base64.b64decode(payload, validate=False)
    except binascii.Error as e:
        raise ReceiptDecodeError(f"Base64 inválido: {e}") from e
    if len(decoded) > max_size:
        raise ReceiptDecodeError(
            f"Comprobante demasiado grande (>{max_size // (1024 * 1024)} MB)"
        )
    return decoded


def is_pdf_content(raw: bytes) -> bool:
    return bool(raw) and raw[:4] == b"%PDF"


def resolve_media_type(filename: Optional[str], raw: bytes) -> str:
    """Guess Content-Type from filename extension or magic bytes."""
    name = (filename or "").lower()
    if name.endswith(".pdf"):
        return "application/pdf"
    if name.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if name.endswith(".png"):
        return "image/png"
    if name.endswith(".gif"):
        return "image/gif"
    if name.endswith(".webp"):
        return "image/webp"
    if name.endswith(".xml"):
        return "application/xml"
    if raw.startswith(b"%PDF-"):
        return "application/pdf"
    if raw.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if raw.startswith(b"GIF87a") or raw.startswith(b"GIF89a"):
        return "image/gif"
    if raw.startswith(b"<?xml") or raw.startswith(b"<cfdi:"):
        return "application/xml"
    return "application/octet-stream"


def comprobante_response_headers(
    filename: Optional[str], media_type: str
) -> Tuple[str, str]:
    """Return (media_type, Content-Disposition value)."""
    safe = (filename or "comprobante").replace('"', "_").replace("\r", "").replace("\n", "")
    disp = f'inline; filename="{safe}"'
    return media_type, disp


def _url_fetch_allowed(url: str) -> bool:
    try:
        p = urlparse(url.strip())
    except Exception:
        return False
    if p.scheme not in ("http", "https"):
        return False
    host = (p.hostname or "").lower()
    if not host:
        return False
    if host in ("localhost",):
        return False
    try:
        addr = ip_address(host)
        for net in _PRIVATE_NETS:
            if addr in net:
                return False
    except ValueError:
        pass
    return True


async def load_adjunto_payload_bytes(
    ruta_archivo: str,
    mime_hint: Optional[str] = None,
    nombre_archivo: Optional[str] = None,
) -> Tuple[bytes, str, str]:
    """
    Load bytes from base64/text in ruta_archivo or from HTTPS URL.
    Returns (raw_bytes, media_type, download_filename).
    """
    raw_in = (ruta_archivo or "").strip()
    if not raw_in:
        raise ReceiptDecodeError("Adjunto vacío")

    if is_probably_url(raw_in):
        if not _url_fetch_allowed(raw_in):
            raise ReceiptDecodeError("URL de descarga no permitida")
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, follow_redirects=True) as client:
            async with client.stream("GET", raw_in) as resp:
                resp.raise_for_status()
                chunks: List[bytes] = []
                total = 0
                async for chunk in resp.aiter_bytes():
                    total += len(chunk)
                    if total > MAX_REMOTE_URL_BYTES:
                        raise ReceiptDecodeError("Descarga demasiado grande")
                    chunks.append(chunk)
                body = b"".join(chunks)
        ctype = resp.headers.get("content-type", "")
        media = (ctype.split(";")[0].strip() if ctype else "") or (
            mime_hint or resolve_media_type(nombre_archivo, body)
        )
        fname = nombre_archivo
        if not fname:
            cd = resp.headers.get("content-disposition") or ""
            m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)', cd, re.I)
            if m:
                fname = m.group(1).strip()
        if not fname:
            fname = "adjunto"
        return body, media, fname

    raw = decode_base64_to_bytes(raw_in)
    media = mime_hint or resolve_media_type(nombre_archivo, raw)
    fname = nombre_archivo or ("documento.pdf" if is_pdf_content(raw) else "adjunto")
    return raw, media, fname


@dataclass(frozen=True)
class GastoAdjuntoMeta:
    id: UUID
    categoria: Optional[str]
    mime_type: Optional[str]
    tipo_archivo: Optional[str]
    nombre_archivo: Optional[str]


@dataclass(frozen=True)
class DocumentoAdjuntoMeta:
    id: UUID
    categoria: Optional[str]
    mime_type: Optional[str]
    tipo_archivo: Optional[str]
    nombre_archivo: Optional[str]


@dataclass(frozen=True)
class ReembolsoAdjuntoMeta:
    id: UUID
    categoria: Optional[str]
    mime_type: Optional[str]
    tipo_archivo: Optional[str]
    nombre_archivo: Optional[str]


async def get_adjunto_columns(session: AsyncSession) -> Set[str]:
    """Read the live DB schema once so routes tolerate old prod schemas."""
    global _ADJUNTO_COLUMN_CACHE
    if _ADJUNTO_COLUMN_CACHE is not None:
        return _ADJUNTO_COLUMN_CACHE
    result = await session.execute(
        text(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = 'adjuntos'
            """
        )
    )
    _ADJUNTO_COLUMN_CACHE = {str(row[0]) for row in result.all()}
    return _ADJUNTO_COLUMN_CACHE


def _adjunto_expr(name: str, available: Set[str]):
    if name in available:
        return getattr(Adjunto, name)
    return literal(None).label(name)


def _supported_adjunto_values(available: Set[str], **values: Any) -> Dict[str, Any]:
    supported = {"id", "gasto_id", "documento_id", "ruta_archivo", "tipo_archivo"}
    supported |= available
    return {
        key: value
        for key, value in values.items()
        if key in supported and value is not None
    }


def invalidate_adjunto_columns_cache() -> None:
    """Reset the cached `adjuntos` schema introspection (test / migration hook)."""
    global _ADJUNTO_COLUMN_CACHE
    _ADJUNTO_COLUMN_CACHE = None


def derive_adjunto_category(
    *,
    categoria: Optional[str],
    mime_type: Optional[str],
    tipo_archivo: Optional[str],
    nombre_archivo: Optional[str],
) -> Optional[str]:
    if categoria:
        return categoria
    raw = (mime_type or tipo_archivo or "").strip().lower()
    filename = (nombre_archivo or "").strip().lower()
    if "xml" in raw or filename.endswith(".xml"):
        return "cfdi_xml"
    if "pdf" in raw or filename.endswith(".pdf"):
        return "cfdi_pdf"
    if "image" in raw:
        return "receipt"
    return None


def derive_adjunto_filename(
    *,
    nombre_archivo: Optional[str],
    mime_type: Optional[str],
    tipo_archivo: Optional[str],
    categoria: Optional[str],
    default_prefix: str = "adjunto",
) -> str:
    if nombre_archivo:
        return nombre_archivo
    category = derive_adjunto_category(
        categoria=categoria,
        mime_type=mime_type,
        tipo_archivo=tipo_archivo,
        nombre_archivo=nombre_archivo,
    )
    if category == "cfdi_pdf":
        return "cfdi.pdf"
    if category == "cfdi_xml":
        return "cfdi.xml"
    media = (mime_type or tipo_archivo or "").lower()
    if "pdf" in media:
        return f"{default_prefix}.pdf"
    if "xml" in media:
        return f"{default_prefix}.xml"
    if "jpeg" in media or "jpg" in media:
        return f"{default_prefix}.jpg"
    if "png" in media:
        return f"{default_prefix}.png"
    return default_prefix


def _meta_sort_key(m: GastoAdjuntoMeta) -> Tuple[int, Any]:
    cat = (
        derive_adjunto_category(
            categoria=m.categoria,
            mime_type=m.mime_type,
            tipo_archivo=m.tipo_archivo,
            nombre_archivo=m.nombre_archivo,
        )
        or ""
    ).strip().lower()
    order = {
        "receipt": 0,
        "cfdi_pdf": 1,
        "cfdi_xml": 2,
        "supporting": 9,
    }.get(cat, 5)
    return (order, str(m.id))


def _label_for_gasto_meta(m: GastoAdjuntoMeta) -> str:
    cat = (
        derive_adjunto_category(
            categoria=m.categoria,
            mime_type=m.mime_type,
            tipo_archivo=m.tipo_archivo,
            nombre_archivo=m.nombre_archivo,
        )
        or ""
    ).strip().lower()
    if cat == "cfdi_pdf":
        return "PDF"
    if cat == "cfdi_xml":
        return "XML"
    if cat == "receipt":
        return "Recibo"
    if cat == "supporting":
        return "Anexo"
    mime = (m.mime_type or m.tipo_archivo or "").lower()
    if "pdf" in mime:
        return "PDF"
    if "xml" in mime:
        return "XML"
    if "image" in mime:
        return "Img"
    return "Archivo"


def _truncated_attachment_label(value: str, max_chars: int = 24) -> str:
    label = (value or "").strip()
    if len(label) <= max_chars:
        return label
    return label[: max_chars - 3].rstrip() + "..."


def _documento_categoria_title(cat: str) -> str:
    normalized = (cat or "").strip().lower()
    titles = {
        "cfdi_pdf": "CFDI PDF",
        "cfdi_xml": "CFDI XML",
        "supporting": "Materialidades",
        "comprobante_pago": "Comprobante de Pago",
    }
    return titles.get(normalized, "Archivo")


def _documento_meta_category(m: DocumentoAdjuntoMeta) -> str:
    return (
        derive_adjunto_category(
            categoria=m.categoria,
            mime_type=m.mime_type,
            tipo_archivo=m.tipo_archivo,
            nombre_archivo=m.nombre_archivo,
        )
        or ""
    ).strip().lower()


def _documento_meta_sort_key(m: DocumentoAdjuntoMeta) -> Tuple[int, Any]:
    cat = _documento_meta_category(m)
    order = {
        "cfdi_pdf": 1,
        "cfdi_xml": 2,
        "supporting": 3,
        "comprobante_pago": 4,
    }.get(cat, 5)
    return (order, str(m.id))


def _label_for_documento_meta(m: DocumentoAdjuntoMeta, index: int) -> str:
    cat = (
        derive_adjunto_category(
            categoria=m.categoria,
            mime_type=m.mime_type,
            tipo_archivo=m.tipo_archivo,
            nombre_archivo=m.nombre_archivo,
        )
        or ""
    ).strip().lower()
    if cat == "cfdi_pdf":
        return "PDF"
    if cat == "cfdi_xml":
        return "XML"
    if cat == "comprobante_pago":
        return "Comprobante pago"
    if cat == "supporting" and m.nombre_archivo:
        return _truncated_attachment_label(m.nombre_archivo)
    mime = (m.mime_type or m.tipo_archivo or "").lower()
    if m.nombre_archivo and cat not in {"cfdi_pdf", "cfdi_xml"}:
        return _truncated_attachment_label(m.nombre_archivo)
    if "pdf" in mime:
        return f"PDF{index}"
    if "xml" in mime:
        return f"XML{index}"
    return f"Archivo{index}"


def html_expense_archivos_cell(
    gasto_id: UUID,
    legacy_present: bool,
    adj_meta: Sequence[GastoAdjuntoMeta],
    link_pdf: Optional[str],
    link_xml: Optional[str],
    max_links: int = 6,
) -> str:
    """Compact multi-link cell for expense list views (no blob loads)."""
    links: List[Tuple[str, str, bool]] = []  # label, href, external

    if legacy_present:
        links.append(
            (
                "Recibo",
                f"/gastos/{gasto_id}/adjuntos/{LEGACY_RECEIPT_KEY}",
                False,
            )
        )

    has_pdf_adj = any(
        derive_adjunto_category(
            categoria=m.categoria,
            mime_type=m.mime_type,
            tipo_archivo=m.tipo_archivo,
            nombre_archivo=m.nombre_archivo,
        )
        == "cfdi_pdf"
        for m in adj_meta
    )
    has_xml_adj = any(
        derive_adjunto_category(
            categoria=m.categoria,
            mime_type=m.mime_type,
            tipo_archivo=m.tipo_archivo,
            nombre_archivo=m.nombre_archivo,
        )
        == "cfdi_xml"
        for m in adj_meta
    )

    for m in sorted(adj_meta, key=_meta_sort_key):
        links.append(
            (_label_for_gasto_meta(m), f"/gastos/{gasto_id}/adjuntos/{m.id}", False)
        )

    if link_pdf and is_probably_url(link_pdf) and not has_pdf_adj:
        links.append(("PDF", link_pdf, True))
    if link_xml and is_probably_url(link_xml) and not has_xml_adj:
        links.append(("XML", link_xml, True))

    if not links:
        return "—"

    shown = links[:max_links]
    parts: List[str] = []
    for lab, href, external in shown:
        safe_href = escape(href, quote=True)
        if external:
            parts.append(
                f'<a href="{safe_href}" target="_blank" rel="noopener noreferrer">{escape(lab)}</a>'
            )
        else:
            parts.append(
                f'<a href="{safe_href}" target="_blank" rel="noopener noreferrer">{escape(lab)}</a>'
            )
    if len(links) > max_links:
        extra = len(links) - max_links
        parts.append(f'<span title="Más adjuntos ({extra})">+{extra}</span>')
    return " ".join(parts)


def html_documento_archivos_detail(
    documento_id: UUID,
    metas: Sequence[DocumentoAdjuntoMeta],
    *,
    removable_adjunto_ids: Optional[Set[UUID]] = None,
) -> str:
    """Labeled file list for document detail views."""
    if not metas:
        return "—"
    removable = removable_adjunto_ids or set()
    items: List[str] = []
    for m in sorted(metas, key=_documento_meta_sort_key):
        cat = _documento_meta_category(m)
        title = _documento_categoria_title(cat)
        filename = derive_adjunto_filename(
            nombre_archivo=m.nombre_archivo,
            mime_type=m.mime_type,
            tipo_archivo=m.tipo_archivo,
            categoria=m.categoria,
            default_prefix="adjunto",
        )
        href = f"/documentos/{documento_id}/adjuntos/{m.id}"
        remove_control = ""
        if m.id in removable:
            remove_control = (
                f'<form method="POST" '
                f'action="/documentos/{documento_id}/adjuntos/{m.id}/eliminar" '
                f'style="display:inline;margin-left:10px;" '
                f'onsubmit="return confirm(\'¿Eliminar este archivo?\');">'
                f'<button type="submit" class="button secondary" '
                f'style="padding:4px 10px;font-size:12px;">Eliminar</button>'
                f"</form>"
            )
        items.append(
            "<li style=\"display:flex;align-items:center;flex-wrap:wrap;gap:4px;\">"
            f"<strong>{escape(title)}:</strong> "
            f'<a href="{escape(href, quote=True)}" target="_blank" rel="noopener noreferrer">'
            f"{escape(filename)}</a>"
            f"{remove_control}"
            "</li>"
        )
    return (
        '<ul style="list-style:none;padding:0;margin:0;display:flex;flex-direction:column;gap:8px;">'
        + "".join(items)
        + "</ul>"
    )


def html_documento_archivos_cell(
    documento_id: UUID,
    metas: Sequence[DocumentoAdjuntoMeta],
    max_links: int = 6,
) -> str:
    if not metas:
        return "—"
    parts: List[str] = []
    for i, m in enumerate(sorted(metas, key=_meta_sort_key), start=1):
        if len(parts) >= max_links:
            break
        lab = _label_for_documento_meta(m, i)
        href = f"/documentos/{documento_id}/adjuntos/{m.id}"
        parts.append(
            f'<a href="{escape(href, quote=True)}" target="_blank" rel="noopener noreferrer">{escape(lab)}</a>'
        )
    if len(metas) > max_links:
        extra = len(metas) - max_links
        parts.append(f'<span title="Más adjuntos ({extra})">+{extra}</span>')
    return " ".join(parts)


def html_documento_cfdi_cell(
    documento_id: UUID,
    metas: Sequence[DocumentoAdjuntoMeta],
) -> str:
    """Compact CFDI-only cell for list views (PDF/XML filename links)."""
    cfdi_metas = [
        m
        for m in sorted(metas, key=_documento_meta_sort_key)
        if _documento_meta_category(m) in {"cfdi_pdf", "cfdi_xml"}
    ]
    if not cfdi_metas:
        return "—"
    lines: List[str] = []
    for m in cfdi_metas:
        cat = _documento_meta_category(m)
        title = _documento_categoria_title(cat)
        filename = derive_adjunto_filename(
            nombre_archivo=m.nombre_archivo,
            mime_type=m.mime_type,
            tipo_archivo=m.tipo_archivo,
            categoria=m.categoria,
            default_prefix="cfdi",
        )
        href = f"/documentos/{documento_id}/adjuntos/{m.id}"
        lines.append(
            f"<span><strong>{escape(title)}:</strong> "
            f'<a href="{escape(href, quote=True)}" target="_blank" '
            f'rel="noopener noreferrer">{escape(filename)}</a></span>'
        )
    return "<br>".join(lines)


async def fetch_expense_ids_with_archivo_data(
    session: AsyncSession, expense_ids: Sequence[UUID]
) -> Set[UUID]:
    if not expense_ids:
        return set()
    q: Select[Any] = select(ExpenseReport.id).where(
        ExpenseReport.id.in_(list(expense_ids)),
        ExpenseReport.archivo_data.isnot(None),
        ExpenseReport.archivo_data != "",
    )
    result = await session.execute(q)
    return {row[0] for row in result.all()}


async def fetch_gasto_adjuntos_meta_batch(
    session: AsyncSession, gasto_ids: Sequence[UUID]
) -> Dict[UUID, List[GastoAdjuntoMeta]]:
    if not gasto_ids:
        return {}
    available = await get_adjunto_columns(session)
    q = (
        select(
            Adjunto.id,
            Adjunto.gasto_id,
            _adjunto_expr("categoria", available),
            _adjunto_expr("mime_type", available),
            Adjunto.tipo_archivo,
            _adjunto_expr("nombre_archivo", available),
            Adjunto.subido_en,
        )
        .where(Adjunto.gasto_id.in_(list(gasto_ids)))
        .order_by(Adjunto.subido_en.asc())
    )
    result = await session.execute(q)
    by_gasto: Dict[UUID, List[GastoAdjuntoMeta]] = {}
    for row in result.all():
        gid = row.gasto_id
        if not gid:
            continue
        by_gasto.setdefault(gid, []).append(
            GastoAdjuntoMeta(
                id=row.id,
                categoria=row.categoria,
                mime_type=row.mime_type,
                tipo_archivo=row.tipo_archivo,
                nombre_archivo=row.nombre_archivo,
            )
        )
    return by_gasto


async def fetch_documento_adjuntos_meta_batch(
    session: AsyncSession, documento_ids: Sequence[UUID]
) -> Dict[UUID, List[DocumentoAdjuntoMeta]]:
    if not documento_ids:
        return {}
    available = await get_adjunto_columns(session)
    q = (
        select(
            Adjunto.id,
            Adjunto.documento_id,
            _adjunto_expr("categoria", available),
            _adjunto_expr("mime_type", available),
            Adjunto.tipo_archivo,
            _adjunto_expr("nombre_archivo", available),
        )
        .where(Adjunto.documento_id.in_(list(documento_ids)))
        .order_by(Adjunto.subido_en.asc())
    )
    result = await session.execute(q)
    by_doc: Dict[UUID, List[DocumentoAdjuntoMeta]] = {}
    for row in result.all():
        did = row.documento_id
        if not did:
            continue
        by_doc.setdefault(did, []).append(
            DocumentoAdjuntoMeta(
                id=row.id,
                categoria=row.categoria,
                mime_type=row.mime_type,
                tipo_archivo=row.tipo_archivo,
                nombre_archivo=row.nombre_archivo,
            )
        )
    return by_doc


async def fetch_reembolso_adjuntos_meta_batch(
    session: AsyncSession, reembolso_ids: Sequence[UUID]
) -> Dict[UUID, List[ReembolsoAdjuntoMeta]]:
    if not reembolso_ids:
        return {}
    available = await get_adjunto_columns(session)
    if "reembolso_id" not in available:
        return {}
    q = (
        select(
            Adjunto.id,
            Adjunto.reembolso_id,
            _adjunto_expr("categoria", available),
            _adjunto_expr("mime_type", available),
            Adjunto.tipo_archivo,
            _adjunto_expr("nombre_archivo", available),
        )
        .where(Adjunto.reembolso_id.in_(list(reembolso_ids)))
        .order_by(Adjunto.subido_en.asc())
    )
    result = await session.execute(q)
    by_reembolso: Dict[UUID, List[ReembolsoAdjuntoMeta]] = {}
    for row in result.all():
        rid = row.reembolso_id
        if not rid:
            continue
        by_reembolso.setdefault(rid, []).append(
            ReembolsoAdjuntoMeta(
                id=row.id,
                categoria=row.categoria,
                mime_type=row.mime_type,
                tipo_archivo=row.tipo_archivo,
                nombre_archivo=row.nombre_archivo,
            )
        )
    return by_reembolso


async def fetch_adjunto_payload(
    session: AsyncSession,
    *,
    adjunto_id: UUID,
    gasto_id: Optional[UUID] = None,
    documento_id: Optional[UUID] = None,
    reembolso_id: Optional[UUID] = None,
) -> Optional[Dict[str, Any]]:
    available = await get_adjunto_columns(session)
    q = select(
        Adjunto.id,
        Adjunto.gasto_id,
        Adjunto.documento_id,
        _adjunto_expr("reembolso_id", available),
        Adjunto.ruta_archivo,
        Adjunto.tipo_archivo,
        _adjunto_expr("mime_type", available),
        _adjunto_expr("nombre_archivo", available),
        _adjunto_expr("categoria", available),
    ).where(Adjunto.id == adjunto_id)
    if gasto_id is not None:
        q = q.where(Adjunto.gasto_id == gasto_id)
    if documento_id is not None:
        q = q.where(Adjunto.documento_id == documento_id)
    if reembolso_id is not None and "reembolso_id" in available:
        q = q.where(Adjunto.reembolso_id == reembolso_id)
    result = await session.execute(q)
    row = result.one_or_none()
    if row is None:
        return None
    return dict(row._mapping)


async def create_adjunto_record(
    session: AsyncSession,
    *,
    gasto_id: Optional[UUID] = None,
    documento_id: Optional[UUID] = None,
    reembolso_id: Optional[UUID] = None,
    ruta_archivo: str,
    tipo_archivo: Optional[str] = None,
    nombre_archivo: Optional[str] = None,
    mime_type: Optional[str] = None,
    categoria: Optional[str] = None,
    origen: Optional[str] = None,
) -> None:
    available = await get_adjunto_columns(session)
    values = _supported_adjunto_values(
        available,
        gasto_id=gasto_id,
        documento_id=documento_id,
        reembolso_id=reembolso_id,
        ruta_archivo=ruta_archivo,
        tipo_archivo=tipo_archivo,
        nombre_archivo=nombre_archivo,
        mime_type=mime_type,
        categoria=categoria,
        origen=origen,
    )
    await session.execute(Adjunto.__table__.insert().values(**values))


async def upsert_gasto_tocino_adjunto(
    session: AsyncSession,
    *,
    gasto_id: UUID,
    categoria: str,
    ruta_archivo: str,
    mime_type: str,
    nombre_archivo: Optional[str],
) -> None:
    """
    Schema-compatible upsert:
    - New schema: dedupe by (gasto_id, categoria)
    - Old schema: dedupe by (gasto_id, tipo_archivo)
    """
    available = await get_adjunto_columns(session)
    q = select(Adjunto.id).where(Adjunto.gasto_id == gasto_id)
    if "categoria" in available:
        q = q.where(Adjunto.categoria == categoria)
    else:
        q = q.where(Adjunto.tipo_archivo == mime_type)
    result = await session.execute(q)
    existing_id = result.scalar_one_or_none()
    values = _supported_adjunto_values(
        available,
        ruta_archivo=ruta_archivo,
        tipo_archivo=mime_type,
        nombre_archivo=nombre_archivo,
        mime_type=mime_type,
        categoria=categoria,
        origen="tocino_webhook",
    )
    if existing_id:
        await session.execute(
            Adjunto.__table__.update()
            .where(Adjunto.id == existing_id)
            .values(**values)
        )
        return
    await create_adjunto_record(
        session,
        gasto_id=gasto_id,
        ruta_archivo=ruta_archivo,
        tipo_archivo=mime_type,
        nombre_archivo=nombre_archivo,
        mime_type=mime_type,
        categoria=categoria,
        origen="tocino_webhook",
    )

