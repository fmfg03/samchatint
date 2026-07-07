from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from samchat.assistant import file_parsing


def test_decode_spreadsheet_xlsx_error_does_not_expose_parser_exception(monkeypatch):
    def _raise_parser_error(*_args, **_kwargs):
        raise RuntimeError("openpyxl leaked /tmp/private-upload.xlsx SECRET_XLSX")

    monkeypatch.setattr(file_parsing, "load_workbook", _raise_parser_error)

    with pytest.raises(HTTPException) as exc_info:
        file_parsing.decode_spreadsheet_xlsx(b"not-xlsx")

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "No se pudo leer el archivo XLSX"
    assert "SECRET_XLSX" not in exc_info.value.detail
    assert "/tmp/private-upload.xlsx" not in exc_info.value.detail


def test_spreadsheet_records_error_does_not_expose_parser_exception(monkeypatch):
    class _Pandas:
        @staticmethod
        def read_excel(*_args, **_kwargs):
            raise RuntimeError("pandas leaked /tmp/private.xlsx SECRET_TABULAR")

    monkeypatch.setattr(file_parsing, "pd", _Pandas)

    with pytest.raises(HTTPException) as exc_info:
        file_parsing.spreadsheet_records_from_bytes(
            raw=b"not-xlsx",
            filename="upload.xlsx",
            content_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "No se pudo leer el archivo tabular"
    assert "SECRET_TABULAR" not in exc_info.value.detail


def test_extract_pdf_error_does_not_expose_parser_exception(monkeypatch):
    monkeypatch.setattr(
        file_parsing,
        "fitz",
        SimpleNamespace(
            open=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("fitz leaked /tmp/private.pdf SECRET_PDF")
            )
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        file_parsing.extract_document_text_from_bytes(
            raw=b"not-pdf",
            filename="upload.pdf",
            mime_type="application/pdf",
            allow_pdf=True,
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "No se pudo extraer texto del PDF"
    assert "SECRET_PDF" not in exc_info.value.detail


def test_extract_docx_error_does_not_expose_parser_exception(monkeypatch):
    monkeypatch.setattr(
        file_parsing.zipfile,
        "ZipFile",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("zip leaked /tmp/private.docx SECRET_DOCX")
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        file_parsing.extract_document_text_from_bytes(
            raw=b"not-docx",
            filename="upload.docx",
            mime_type=(
                "application/vnd.openxmlformats-officedocument."
                "wordprocessingml.document"
            ),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "No se pudo leer DOCX"
    assert "SECRET_DOCX" not in exc_info.value.detail
