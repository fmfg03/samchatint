from __future__ import annotations

import pytest

from devnous.gastos.services import import_balanza_service, import_proveedores_service


def test_cuentas_contables_xlsx_parser_error_is_generic(monkeypatch):
    def _raise_parser_error(*_args, **_kwargs):
        raise RuntimeError("openpyxl leaked /tmp/private-balanza.xlsx SECRET_BALANZA")

    monkeypatch.setattr(import_balanza_service, "load_workbook", _raise_parser_error)

    with pytest.raises(ValueError) as exc_info:
        import_balanza_service.parse_cuentas_contables_upload(
            "balanza.xlsx",
            b"not-xlsx",
        )

    assert str(exc_info.value) == "No se pudo leer el archivo XLSX."
    assert "SECRET_BALANZA" not in str(exc_info.value)
    assert "/tmp/private-balanza.xlsx" not in str(exc_info.value)


def test_proveedores_xlsx_parser_error_is_generic(monkeypatch):
    def _raise_parser_error(*_args, **_kwargs):
        raise RuntimeError("openpyxl leaked /tmp/private-rfc.xlsx SECRET_RFC")

    monkeypatch.setattr(import_proveedores_service, "load_workbook", _raise_parser_error)

    with pytest.raises(ValueError) as exc_info:
        import_proveedores_service.parse_proveedores_clientes_upload(
            "proveedores.xlsx",
            b"not-xlsx",
        )

    assert str(exc_info.value) == "No se pudo leer el archivo XLSX."
    assert "SECRET_RFC" not in str(exc_info.value)
    assert "/tmp/private-rfc.xlsx" not in str(exc_info.value)
