from starlette.responses import Response

from devnous.gastos.utils.receipt_bytes import comprobante_response_headers


def test_unicode_attachment_filename_is_safe_for_starlette_headers() -> None:
    # Visually this is "COMPROBACIÓN", but the accent is decomposed (O + U+0301).
    filename = "COMPROBACIO\u0301N NO DEDUCIBLES.pdf"

    media_type, disposition = comprobante_response_headers(
        filename, "application/pdf"
    )

    # Regression: Starlette encodes response headers as latin-1. The raw
    # combining accent used to raise UnicodeEncodeError and the global handler
    # rendered the branded 500 page inside the attachment iframe.
    disposition.encode("latin-1")
    response = Response(
        content=b"%PDF-1.7",
        media_type=media_type,
        headers={"Content-Disposition": disposition},
    )

    assert response.status_code == 200
    assert 'filename="COMPROBACION NO DEDUCIBLES.pdf"' in disposition
    assert (
        "filename*=UTF-8''COMPROBACIO%CC%81N%20NO%20DEDUCIBLES.pdf"
        in disposition
    )


def test_ascii_attachment_filename_preserves_simple_inline_header() -> None:
    media_type, disposition = comprobante_response_headers(
        "comprobante.pdf", "application/pdf"
    )

    assert media_type == "application/pdf"
    assert disposition == 'inline; filename="comprobante.pdf"'


def test_attachment_filename_strips_path_and_header_control_characters() -> None:
    _, disposition = comprobante_response_headers(
        '../malo\\archivo"\r\n.pdf', "application/pdf"
    )

    assert "\r" not in disposition
    assert "\n" not in disposition
    assert "/" not in disposition
    assert "\\" not in disposition
