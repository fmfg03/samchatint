"""Focused tests for solicitud payment scheduling."""

from datetime import date, datetime

from devnous.gastos.services.payment_schedule_service import compute_fecha_pago
from devnous.gastos.utils.mexico_city_dates import MEXICO_CITY_TZ


def _cdmx(year: int, month: int, day: int, hour: int = 12, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=MEXICO_CITY_TZ)


def test_weekly_payment_uses_next_monday_when_approved_by_wednesday_cutoff() -> None:
    assert compute_fecha_pago(_cdmx(2026, 8, 3, 10)) == date(2026, 8, 10)
    assert compute_fecha_pago(_cdmx(2026, 8, 5, 23, 59)) == date(2026, 8, 10)


def test_weekly_payment_moves_to_following_monday_after_wednesday_cutoff() -> None:
    assert compute_fecha_pago(_cdmx(2026, 8, 6, 0, 1)) == date(2026, 8, 17)


def test_month_end_payment_still_wins_when_earlier_than_monday_run() -> None:
    assert compute_fecha_pago(_cdmx(2026, 7, 28, 9)) == date(2026, 7, 31)
