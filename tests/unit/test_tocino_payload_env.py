import pytest

from devnous.gastos.models import ExpenseReport
from devnous.gastos.services.expense_service import (
    build_tocino_payload_from_env,
)


TOCINO_ENV_KEYS = (
    "TOCINO_TAX_ID",
    "TOCINO_TAXPAYER",
    "TOCINO_TAXPAYER_NAME",
    "TOCINO_TAXPAYER_LAST_NAME",
    "TOCINO_TAXPAYER_SECOND_LAST_NAME",
    "TOCINO_STREET_ADDRESS_1",
    "TOCINO_EXT_NUM",
    "TOCINO_INT_NUM",
    "TOCINO_STREET_ADDRESS_2",
    "TOCINO_CITY",
    "TOCINO_STATE",
    "TOCINO_POSTAL_CODE",
    "TOCINO_FISCAL_REGIMEN",
)


def _clear_tocino_env(monkeypatch):
    for key in TOCINO_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def _expense() -> ExpenseReport:
    return ExpenseReport(
        archivo_nombre="receipt.jpg",
        archivo_data="base64-payload",
        metodo_pago="Tarjeta",
        ultimos_4_digitos="1234",
    )


def test_tocino_payload_env_fails_closed_without_required_config(
    monkeypatch,
):
    _clear_tocino_env(monkeypatch)

    with pytest.raises(ValueError) as exc_info:
        build_tocino_payload_from_env(_expense(), "G03")

    message = str(exc_info.value)
    assert "TOCINO_TAX_ID" in message
    assert "TOCINO_TAXPAYER" in message
    assert "TOCINO_POSTAL_CODE" in message
    assert "TOCINO_FISCAL_REGIMEN" in message


def test_tocino_payload_env_does_not_use_fictitious_defaults(
    monkeypatch,
):
    _clear_tocino_env(monkeypatch)
    monkeypatch.setenv("TOCINO_TAX_ID", "REAL010101ABC")
    monkeypatch.setenv("TOCINO_TAXPAYER", "REAL TAXPAYER SA DE CV")
    monkeypatch.setenv("TOCINO_POSTAL_CODE", "01234")
    monkeypatch.setenv("TOCINO_FISCAL_REGIMEN", "601")

    payload = build_tocino_payload_from_env(_expense(), "G03")

    assert payload["tax_id"] == "REAL010101ABC"
    assert payload["taxpayer"] == "REAL TAXPAYER SA DE CV"
    assert payload["postal_code"] == "01234"
    assert payload["fiscal_regimen_code"] == "601"
    assert payload["taxpayer_name"] == ""
    assert payload["street_address_1"] == ""
    assert payload["payment_form"] == "Tarjeta"
    assert payload["card_last_digits"] == "1234"
    assert "RFC123456789" not in payload.values()
    assert "JUAN PEREZ GARCIA" not in payload.values()
