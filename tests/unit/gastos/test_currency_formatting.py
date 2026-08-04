from decimal import Decimal

from devnous.gastos.routes import operations_analytics_routes, user_routes
from devnous.gastos.services import documento_telegram


def test_format_currency_always_displays_two_decimals() -> None:
    assert user_routes.format_currency(Decimal("123.456")) == "$123.46"
    assert user_routes.format_currency(100) == "$100.00"
    assert user_routes.format_currency(None) == "$0.00"
    assert user_routes.format_currency(Decimal("12.3"), "USD") == "US$12.30"
    assert user_routes.format_currency(Decimal("12"), "EUR") == "€12.00"


def test_telegram_money_context_always_displays_two_decimals() -> None:
    assert documento_telegram._fmt_mxn(Decimal("628.005")) == "$628.01 MXN"
    assert documento_telegram._fmt_mxn(500) == "$500.00 MXN"
    assert documento_telegram._fmt_mxn(None) == "—"


def test_operations_analytics_money_payload_values_are_rounded() -> None:
    assert operations_analytics_routes._money(Decimal("123.456")) == 123.46
    assert operations_analytics_routes._money("100") == 100.0
    assert operations_analytics_routes._money(None) == 0.0


def test_budget_line_response_money_fields_are_rounded() -> None:
    line = {
        "budget_amount": Decimal("1000.555"),
        "monthly_allocations": [
            {"month_number": 1, "allocated_amount": Decimal("10.005")},
            {"month_number": 2, "allocated_amount": "20"},
        ],
    }

    formatted = operations_analytics_routes._format_budget_line_money(line)

    assert formatted["budget_amount"] == 1000.56
    assert formatted["monthly_allocations"][0]["allocated_amount"] == 10.01
    assert formatted["monthly_allocations"][1]["allocated_amount"] == 20.0
