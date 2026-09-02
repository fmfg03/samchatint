from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from devnous.gastos import schema_guard
from devnous.gastos.services.loan_request_service import (
    PRESTAMO_BENEFICIARIO_EMPLEADO,
    PRESTAMO_BENEFICIARIO_PROPIO,
    PRESTAMO_SANTANDER_CUENTA_CODIGO,
    PRESTAMO_STATUS_APROBADA,
    PRESTAMO_STATUS_BORRADOR,
    PRESTAMO_STATUS_CANCELADA,
    PRESTAMO_STATUS_ENVIADA,
    PrestamoCreatePayload,
    PrestamoWorkflowPermissionError,
    PrestamoWorkflowValidationError,
    build_abono_from_application,
    build_prestamo_from_payload,
    can_approve_prestamo,
    can_approve_prestamo_abono,
    can_edit_prestamo,
    can_view_all_prestamos,
    can_view_prestamo,
    cancel_prestamo,
    compute_abono_application,
    submit_prestamo,
)


def _payload(**overrides):
    data = {
        "solicitante_empleado_id": uuid4(),
        "beneficiario_tipo": PRESTAMO_BENEFICIARIO_PROPIO,
        "monto_solicitado": "15000",
        "motivo": "Apoyo temporal",
        "numero_referencia": "PRE-26000001",
    }
    data.update(overrides)
    return PrestamoCreatePayload(**data)


def test_build_prestamo_captures_requested_amount_as_initial_balance() -> None:
    prestamo = build_prestamo_from_payload(
        _payload(monto_solicitado="12345.678")
    )

    assert prestamo.estado == PRESTAMO_STATUS_BORRADOR
    assert prestamo.monto_solicitado == Decimal("12345.68")
    assert prestamo.saldo_pendiente == Decimal("12345.68")
    assert prestamo.motivo == "Apoyo temporal"
    assert PRESTAMO_SANTANDER_CUENTA_CODIGO == "1120-001-001"


def test_build_prestamo_requires_positive_amount_and_motive() -> None:
    with pytest.raises(PrestamoWorkflowValidationError) as amount_error:
        build_prestamo_from_payload(_payload(monto_solicitado="0"))
    with pytest.raises(PrestamoWorkflowValidationError) as motive_error:
        build_prestamo_from_payload(_payload(motivo=" "))

    assert amount_error.value.code == "invalid_amount"
    assert motive_error.value.code == "missing_required_field"


def test_beneficiary_selection_is_mutually_exclusive() -> None:
    requester_id = uuid4()
    employee_id = uuid4()
    provider_id = uuid4()

    employee_loan = build_prestamo_from_payload(
        _payload(
            solicitante_empleado_id=requester_id,
            beneficiario_tipo=PRESTAMO_BENEFICIARIO_EMPLEADO,
            beneficiario_empleado_id=employee_id,
        )
    )

    assert employee_loan.beneficiario_empleado_id == employee_id
    with pytest.raises(PrestamoWorkflowValidationError) as missing_error:
        build_prestamo_from_payload(
            _payload(beneficiario_tipo=PRESTAMO_BENEFICIARIO_EMPLEADO)
        )
    with pytest.raises(PrestamoWorkflowValidationError) as mixed_error:
        build_prestamo_from_payload(
            _payload(
                beneficiario_tipo=PRESTAMO_BENEFICIARIO_EMPLEADO,
                beneficiario_empleado_id=employee_id,
                beneficiario_proveedor_cliente_id=provider_id,
            )
        )

    assert missing_error.value.code == "missing_beneficiary"
    assert mixed_error.value.code == "invalid_beneficiary_selection"


def test_submit_locks_editing_and_cancel_before_approval() -> None:
    requester_id = uuid4()
    actor = SimpleNamespace(id=requester_id)
    prestamo = build_prestamo_from_payload(
        _payload(solicitante_empleado_id=requester_id)
    )

    assert can_edit_prestamo(prestamo)
    submit_prestamo(prestamo, actor)

    assert prestamo.estado == PRESTAMO_STATUS_ENVIADA
    assert prestamo.enviado_en is not None
    assert not can_edit_prestamo(prestamo)

    cancel_prestamo(prestamo, actor)

    assert prestamo.estado == PRESTAMO_STATUS_CANCELADA
    assert prestamo.cancelado_por_empleado_id == requester_id


def test_cancel_after_approval_is_rejected() -> None:
    requester_id = uuid4()
    actor = SimpleNamespace(id=requester_id)
    prestamo = build_prestamo_from_payload(
        _payload(solicitante_empleado_id=requester_id)
    )
    prestamo.estado = PRESTAMO_STATUS_APROBADA

    with pytest.raises(PrestamoWorkflowValidationError) as exc_info:
        cancel_prestamo(prestamo, actor)

    assert exc_info.value.code == "not_cancelable"


def test_only_requester_can_submit_or_cancel() -> None:
    prestamo = build_prestamo_from_payload(
        _payload(solicitante_empleado_id=uuid4())
    )
    other = SimpleNamespace(id=uuid4())

    with pytest.raises(PrestamoWorkflowPermissionError):
        submit_prestamo(prestamo, other)
    with pytest.raises(PrestamoWorkflowPermissionError):
        cancel_prestamo(prestamo, other)


def test_visibility_allows_named_managers_and_requester_only() -> None:
    requester_id = uuid4()
    prestamo = build_prestamo_from_payload(
        _payload(solicitante_empleado_id=requester_id)
    )
    requester = SimpleNamespace(
        id=requester_id,
        nombre="Usuario",
        rol="empleado",
    )
    juan_pablo = SimpleNamespace(
        id=uuid4(),
        nombre="Juan Pablo Lopez Romero",
        rol="finanzas",
        correo="jlopez@plataformasports.com",
    )
    other = SimpleNamespace(
        id=uuid4(),
        nombre="Otro Usuario",
        rol="empleado",
        correo="otro@example.com",
    )

    assert can_view_prestamo(requester, prestamo)
    assert can_view_all_prestamos(juan_pablo)
    assert can_view_prestamo(juan_pablo, prestamo)
    assert not can_view_all_prestamos(other)
    assert not can_view_prestamo(other, prestamo)


def test_approvers_follow_approved_people_lists() -> None:
    assert can_approve_prestamo(
        SimpleNamespace(
            id=uuid4(),
            nombre="Luis Ángel Orozco Colin",
            rol="admin",
        )
    )
    assert can_approve_prestamo(
        SimpleNamespace(
            id=uuid4(),
            nombre="Federico Gonzalez Nava",
            rol="admin",
        )
    )
    assert not can_approve_prestamo(
        SimpleNamespace(id=uuid4(), nombre="Benjamin Jimenez", rol="finanzas")
    )
    assert can_approve_prestamo_abono(
        SimpleNamespace(
            id=uuid4(),
            nombre="Daniel Dominguez",
            rol="contabilidad",
        )
    )
    assert can_approve_prestamo_abono(
        SimpleNamespace(id=uuid4(), nombre="Jaqueline", rol="contabilidad")
    )
    assert not can_approve_prestamo_abono(
        SimpleNamespace(
            id=uuid4(),
            nombre="Luis Angel Orozco Colin",
            rol="admin",
        )
    )


def test_abono_excess_requires_confirmation_then_manual_adjustment() -> None:
    pending = compute_abono_application(
        saldo_pendiente="100.00",
        monto_reportado="120.00",
    )

    assert pending.requires_excess_confirmation is True
    assert pending.monto_aplicado == Decimal("100.00")
    assert pending.monto_excedente == Decimal("20.00")
    assert pending.saldo_despues == Decimal("0.00")

    confirmed = compute_abono_application(
        saldo_pendiente="100.00",
        monto_reportado="120.00",
        excess_confirmed=True,
    )
    prestamo = build_prestamo_from_payload(_payload())
    abono = build_abono_from_application(
        prestamo,
        SimpleNamespace(id=uuid4()),
        confirmed,
    )

    assert confirmed.requires_excess_confirmation is False
    assert abono.monto_aplicado == Decimal("100.00")
    assert abono.monto_excedente == Decimal("20.00")
    assert abono.saldo_despues == Decimal("0.00")
    assert abono.excedente_confirmado is True


def test_schema_guard_and_migration_include_loan_tables() -> None:
    patch_names = {name for name, _sql in schema_guard.SCHEMA_PATCHES}
    required_columns = {
        (item.table, item.column)
        for item in schema_guard.REQUIRED_COLUMNS
    }
    required_indexes = {
        (item.table, item.index)
        for item in schema_guard.REQUIRED_INDEXES
    }
    migration = open(
        "database/migrations/20260902_prestamos_module.sql",
        encoding="utf-8",
    ).read()

    assert "create_solicitudes_prestamo_table" in patch_names
    assert "create_prestamo_abonos_table" in patch_names
    assert ("solicitudes_prestamo", "estado") in required_columns
    assert ("prestamo_abonos", "prestamo_id") in required_columns
    assert (
        "solicitudes_prestamo",
        "ux_solicitudes_prestamo_referencia",
    ) in required_indexes
    assert (
        "prestamo_abonos",
        "ix_prestamo_abonos_prestamo_estado",
    ) in required_indexes
    assert "CREATE TABLE IF NOT EXISTS solicitudes_prestamo" in migration
    assert "CREATE TABLE IF NOT EXISTS prestamo_abonos" in migration
