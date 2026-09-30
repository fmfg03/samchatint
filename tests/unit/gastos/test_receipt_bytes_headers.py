from starlette.responses import Response

from devnous.gastos.utils.receipt_bytes import comprobante_response_headers


def test_content_disposition_handles_decomposed_accent_filename() -> None:
    filename = "COMPROBACIO\u0301N NO DEDUCIBLES.pdf"

    media_type, disposition = comprobante_response_headers(
        filename, "application/pdf"
    )

    response = Response(
        content=b"%PDF-1.7",
        media_type=media_type,
        headers={"Content-Disposition": disposition},
    )

    assert response.status_code == 200
    header = response.headers["content-disposition"]
    assert 'filename="COMPROBACION NO DEDUCIBLES.pdf"' in header
    assert "filename*=UTF-8''COMPROBACI%C3%93N%20NO%20DEDUCIBLES.pdf" in header


def test_content_disposition_preserves_ascii_filename() -> None:
    media_type, disposition = comprobante_response_headers(
        "comprobante.pdf", "application/pdf"
    )

    assert media_type == "application/pdf"
    assert disposition == 'inline; filename="comprobante.pdf"'


def test_content_disposition_strips_path_and_header_controls() -> None:
    _, disposition = comprobante_response_headers(
        '../malo\\archivo"\r\n.pdf', "application/pdf"
    )

    assert "\r" not in disposition
    assert "\n" not in disposition
    assert "/" not in disposition
    assert "\\" not in disposition
