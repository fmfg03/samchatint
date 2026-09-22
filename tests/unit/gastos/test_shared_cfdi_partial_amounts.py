from decimal import Decimal

import pytest

from devnous.gastos.services.documento_service import (
    SolicitudValidationError,
    shared_cfdi_remaining_amount,
)


def test_shared_cfdi_allows_exact_remaining_balance() -> None:
    remaining = shared_cfdi_remaining_amount(
        invoice_total="204624.00",
        reserved_amounts=[Decimal("102312.00")],
        requested_amount="102312.00",
    )

    assert remaining == Decimal("102312.00")


def test_shared_cfdi_rejects_amount_over_remaining_balance() -> None:
    with pytest.raises(SolicitudValidationError) as raised:
        shared_cfdi_remaining_amount(
            invoice_total="204624.00",
            reserved_amounts=["102312.00"],
            requested_amount="102312.01",
        )

    assert raised.value.code == "cfdi_amount_exceeds_remaining"
    assert "$102,312.00" in raised.value.user_message


def test_shared_cfdi_rejects_when_invoice_is_already_fully_allocated() -> None:
    with pytest.raises(SolicitudValidationError) as raised:
        shared_cfdi_remaining_amount(
            invoice_total="204624.00",
            reserved_amounts=["204624.00"],
            requested_amount="1.00",
        )

    assert raised.value.code == "cfdi_fully_allocated"
