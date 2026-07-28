from types import SimpleNamespace
from uuid import uuid4

from devnous.gastos.services.payment_run_service import (
    DEFAULT_PAYMENT_RUN_MANAGER_EMPLOYEE_IDS,
    can_manage_payment_run,
    configured_payment_run_manager_ids,
    parse_payment_run_date,
)


def test_payment_run_access_allows_superadmin_without_allowlist() -> None:
    empleado = SimpleNamespace(id=uuid4(), rol="superadmin")

    assert can_manage_payment_run(empleado)


def test_payment_run_access_uses_empleado_id_allowlist(monkeypatch) -> None:
    empleado_id = uuid4()
    monkeypatch.setenv(
        "SAMCHAT_PAYMENT_RUN_MANAGER_EMPLOYEE_IDS",
        str(empleado_id),
    )

    assert can_manage_payment_run(
        SimpleNamespace(id=empleado_id, rol="finanzas")
    )
    assert not can_manage_payment_run(
        SimpleNamespace(id=uuid4(), rol="finanzas")
    )


def test_payment_run_manager_ids_accept_both_env_keys(monkeypatch) -> None:
    first = uuid4()
    second = uuid4()
    monkeypatch.setenv("SAMCHAT_PAYMENT_RUN_MANAGER_EMPLOYEE_IDS", str(first))
    monkeypatch.setenv("PAYMENT_RUN_MANAGER_EMPLOYEE_IDS", str(second))

    configured = configured_payment_run_manager_ids()
    assert str(first) in configured
    assert str(second) in configured
    assert DEFAULT_PAYMENT_RUN_MANAGER_EMPLOYEE_IDS.issubset(configured)


def test_payment_run_access_allows_default_manager_id() -> None:
    empleado_id = next(iter(DEFAULT_PAYMENT_RUN_MANAGER_EMPLOYEE_IDS))

    assert can_manage_payment_run(
        SimpleNamespace(id=empleado_id, rol="finanzas")
    )


def test_parse_payment_run_date_from_iso_string() -> None:
    parsed = parse_payment_run_date("2026-07-31")

    assert parsed.isoformat() == "2026-07-31"
