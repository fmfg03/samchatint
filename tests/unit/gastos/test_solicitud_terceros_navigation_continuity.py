from pathlib import Path


SOURCE = Path("src/devnous/gastos/routes/user_routes.py").read_text(encoding="utf-8")


def _third_party_form_source() -> str:
    start = SOURCE.index("async def _render_solicitud_terceros_form")
    end = SOURCE.index('@router.post("/documentos/nueva-solicitud-terceros"', start)
    return SOURCE[start:end]


def test_new_third_party_request_cancel_returns_to_transfer_workspace() -> None:
    block = _third_party_form_source()

    assert 'cancel_href = "/gastos-terceros"' in block
    assert '<a href="{cancel_href}" class="button secondary">Cancelar</a>' in block


def test_edit_third_party_request_cancel_still_returns_to_document_detail() -> None:
    block = _third_party_form_source()

    assert 'cancel_href = f"/documentos/{edit_documento.id}"' in block
