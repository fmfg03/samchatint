from pathlib import Path


def _approval_controls_source() -> str:
    source = Path("src/devnous/gastos/routes/user_routes.py").read_text()
    start = source.index(
        "<!-- Aprobar / Rechazar (only if estado = enviado AND user has permission) -->"
    )
    end = source.index("<!-- Saldar cuenta", start)
    return source[start:end]


def test_document_detail_approval_submit_does_not_depend_on_javascript() -> None:
    block = _approval_controls_source()

    assert '<summary class="button primary">Aprobar</summary>' in block
    assert "Confirmar aprobación</button>" in block
    assert 'action="/documentos/{documento_id}/aprobar"' in block
    assert 'id="aprobar-form"' not in block
    assert "document.getElementById" not in block


def test_document_detail_rejection_uses_native_disclosure_and_post_form() -> None:
    block = _approval_controls_source()

    assert "<details>" in block
    assert '<summary class="button danger">Rechazar</summary>' in block
    assert 'action="/documentos/{documento_id}/rechazar"' in block
    assert 'textarea name="comentario" id="comentario_rechazar" required' in block
