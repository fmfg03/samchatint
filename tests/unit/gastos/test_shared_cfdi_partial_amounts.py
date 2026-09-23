from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from devnous.gastos.services import documento_service
from devnous.gastos.services import documento_workflow_service
from devnous.gastos.services.documento_service import (
    SolicitudTercerosPayload,
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


def test_shared_cfdi_rejects_invalid_and_nonpositive_totals() -> None:
    for total in ("not-a-number", "-1.00", "0.00"):
        with pytest.raises(SolicitudValidationError) as raised:
            shared_cfdi_remaining_amount(
                invoice_total=total,
                reserved_amounts=[],
                requested_amount="1.00",
            )
        assert raised.value.code == "invalid_cfdi_amount"


class _AmountResult:
    def __init__(self, amounts):
        self._amounts = amounts

    def scalars(self):
        return SimpleNamespace(all=lambda: self._amounts)


class _AmountSession:
    def __init__(self, *amount_sets):
        self.amount_sets = amount_sets
        self.calls = []

    async def execute(self, statement, *args):
        self.calls.append((statement, args))
        if len(self.calls) == 1:
            return None
        return _AmountResult(self.amount_sets[len(self.calls) - 2])


@pytest.mark.asyncio
async def test_shared_cfdi_amount_validation_locks_and_reads_existing_reservations() -> None:
    session = _AmountSession([Decimal("102312.00")], [])
    report = SimpleNamespace(id=uuid4(), total=204624.00)

    remaining = await documento_service.validate_shared_cfdi_payment_amount(
        session,
        cfdi_report=report,
        requested_amount=Decimal("102312.00"),
    )

    assert remaining == Decimal("102312.00")
    assert len(session.calls) == 3


@pytest.mark.asyncio
async def test_shared_cfdi_amount_validation_excludes_expenses_reserved_by_any_linked_document() -> None:
    current_document_id = uuid4()
    session = _AmountSession([Decimal("50000.00")], [Decimal("52312.00")])
    report = SimpleNamespace(id=uuid4(), total=204624.00)

    remaining = await documento_service.validate_shared_cfdi_payment_amount(
        session,
        cfdi_report=report,
        requested_amount=Decimal("102312.00"),
        exclude_documento_id=current_document_id,
    )

    assert remaining == Decimal("102312.00")
    assert len(session.calls) == 3
    expense_query = str(session.calls[2][0])
    assert "NOT (EXISTS" in expense_query
    assert "expense_reports.documento_id" in expense_query
    assert "expense_reports.solicitud_documento_id" in expense_query
    assert "expense_reports.informe_documento_id" in expense_query


@pytest.mark.asyncio
async def test_shared_cfdi_reservation_revalidates_shared_document(monkeypatch) -> None:
    document_id = uuid4()
    report = SimpleNamespace(id=uuid4(), total=204624.00)
    calls = []

    class _ReservationSession:
        async def get(self, _model, _id):
            return report

    async def fake_validate(
        _session, *, cfdi_report, requested_amount, exclude_documento_id
    ):
        calls.append((cfdi_report, requested_amount, exclude_documento_id))
        return Decimal("102312.00")

    monkeypatch.setattr(
        documento_workflow_service, "validate_shared_cfdi_payment_amount", fake_validate
    )

    await documento_workflow_service.reserve_documento_cfdis_or_raise(
        _ReservationSession(),
        SimpleNamespace(
            id=document_id,
            tipo="SOLICITUD",
            cfdi_report_id=report.id,
            cfdi_compartido_confirmado=True,
            monto_solicitado=Decimal("102312.00"),
        ),
        SimpleNamespace(id=uuid4(), rol="usuario"),
    )

    assert calls == [(report, Decimal("102312.00"), document_id)]


@pytest.mark.asyncio
async def test_shared_cfdi_amount_validation_requires_report_identity() -> None:
    with pytest.raises(SolicitudValidationError) as raised:
        await documento_service.validate_shared_cfdi_payment_amount(
            _AmountSession([]),
            cfdi_report=SimpleNamespace(total=204624.00),
            requested_amount="1.00",
        )

    assert raised.value.code == "invalid_cfdi_amount"


@pytest.mark.asyncio
async def test_attachment_ingestion_applies_balance_guard_for_shared_cfdi(monkeypatch) -> None:
    report = SimpleNamespace(id=uuid4(), total=204624.00)
    document = SimpleNamespace(
        id=None,
        cfdi_compartido_confirmado=True,
        monto_solicitado=Decimal("102312.00"),
    )
    balance_calls = []

    async def fake_ingest(*_args, **_kwargs):
        return SimpleNamespace(cfdi_report=report)

    async def fake_validate(_session, *, cfdi_report, requested_amount):
        balance_calls.append((cfdi_report, requested_amount))
        return Decimal("102312.00")

    monkeypatch.setattr(documento_service, "ingest_cfdi_from_upload", fake_ingest)
    monkeypatch.setattr(
        documento_service, "validate_shared_cfdi_payment_amount", fake_validate
    )

    await documento_service._ingest_solicitud_cfdi_from_attachments(
        object(),
        documento=document,
        validated_attachments=[(b"<cfdi/>", "application/xml", "factura.xml", "cfdi_xml")],
        numero_referencia="S-TEST",
    )

    assert balance_calls == [(report, Decimal("102312.00"))]


class _CreationResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _CreationSession:
    def __init__(self, values):
        self.values = iter(values)
        self.added = []

    async def execute(self, *_args, **_kwargs):
        return _CreationResult(next(self.values))

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        return None

    async def commit(self):
        return None

    async def refresh(self, _value):
        return None


class _UpdateSession:
    def __init__(self, report, proveedor, empleado):
        self.report = report
        self.results = iter([proveedor, empleado])

    async def execute(self, *_args, **_kwargs):
        return _CreationResult(next(self.results))

    async def get(self, _model, _id):
        return self.report

    async def commit(self):
        return None

    async def refresh(self, _value):
        return None


def _shared_payload(*, confirmed: bool) -> SolicitudTercerosPayload:
    return SolicitudTercerosPayload(
        empleado_id=uuid4(),
        monto_solicitado=102312.00,
        proveedor_cliente_id=uuid4(),
        torneo_id=None,
        proyecto_otro="Nacional Morelos",
        concepto_pago="Segundo 50% de factura",
        cfdi_uuid_manual="11111111-1111-1111-1111-111111111111",
        cfdi_compartido_confirmado=confirmed,
    )


@pytest.mark.asyncio
async def test_manual_shared_cfdi_validates_remaining_balance_before_creation(monkeypatch) -> None:
    payload = _shared_payload(confirmed=True)
    session = _CreationSession([SimpleNamespace(), SimpleNamespace()])
    report = SimpleNamespace(id=uuid4(), total=204624.00)
    calls = []

    async def fake_reference(*_args):
        return "S-TEST"

    async def fake_operations_reference(*_args):
        return "1"

    async def fake_find_report(*_args):
        return report

    async def fake_conflict(*_args):
        return SimpleNamespace(empleado_id=payload.empleado_id)

    async def fake_validate(_session, *, cfdi_report, requested_amount):
        calls.append((cfdi_report, requested_amount))
        return Decimal("102312.00")

    monkeypatch.setattr(documento_service, "generate_documento_reference_number", fake_reference)
    monkeypatch.setattr(
        documento_service, "allocate_referencia_operaciones_for_empleado",
        fake_operations_reference,
    )
    monkeypatch.setattr(documento_service, "find_cfdi_report_by_fiscal_uuid", fake_find_report)
    monkeypatch.setattr(documento_service, "find_blocking_cfdi_usage", fake_conflict)
    monkeypatch.setattr(
        documento_service, "validate_shared_cfdi_payment_amount", fake_validate
    )

    documento = await documento_service.create_solicitud_terceros_document(session, payload)

    assert documento.cfdi_report_id == report.id
    assert calls == [(report, 102312.00)]


@pytest.mark.asyncio
async def test_editing_linked_shared_cfdi_revalidates_new_amount(monkeypatch) -> None:
    empleado_id = uuid4()
    report = SimpleNamespace(id=uuid4(), total=204624.00)
    document = SimpleNamespace(
        id=uuid4(),
        tipo="SOLICITUD",
        estado="control_presupuestal",
        empleado_id=empleado_id,
        budget_concept_id=None,
        cfdi_report_id=report.id,
        cfdi_compartido_confirmado=True,
    )
    calls = []

    async def fake_validate(
        _session, *, cfdi_report, requested_amount, exclude_documento_id
    ):
        calls.append((cfdi_report, requested_amount, exclude_documento_id))
        return Decimal("102312.00")

    monkeypatch.setattr(
        documento_service, "validate_shared_cfdi_payment_amount", fake_validate
    )

    updated = await documento_service.update_solicitud_terceros_document(
        _UpdateSession(report, SimpleNamespace(), SimpleNamespace()),
        documento=document,
        payload=SolicitudTercerosPayload(
            empleado_id=empleado_id,
            monto_solicitado=102312.00,
            proveedor_cliente_id=uuid4(),
            torneo_id=None,
            proyecto_otro="Nacional Morelos",
            concepto_pago="Segundo 50% de factura",
            cfdi_compartido_confirmado=True,
        ),
    )

    assert updated.monto_solicitado == 102312.00
    assert calls == [(report, 102312.00, document.id)]


@pytest.mark.asyncio
async def test_editing_reused_cfdi_persists_its_canonical_link(monkeypatch) -> None:
    empleado_id = uuid4()
    report = SimpleNamespace(id=uuid4(), total=204624.00)
    document = SimpleNamespace(
        id=uuid4(), tipo="SOLICITUD", estado="borrador", empleado_id=empleado_id,
        budget_concept_id=None, cfdi_report_id=None, cfdi_compartido_confirmado=False,
    )
    monkeypatch.setattr(
        documento_service, "find_cfdi_report_by_fiscal_uuid", AsyncMock(return_value=report)
    )

    updated = await documento_service.update_solicitud_terceros_document(
        _UpdateSession(report, SimpleNamespace(), SimpleNamespace()), documento=document,
        payload=SolicitudTercerosPayload(
            empleado_id=empleado_id, monto_solicitado=100.00, proveedor_cliente_id=uuid4(),
            torneo_id=None, proyecto_otro="Nacional Morelos", concepto_pago="Pago",
            cfdi_uuid_manual="ABCD1234-1111-2222-3333-444444444444",
        ),
    )

    assert updated.cfdi_report_id == report.id
    assert updated.cfdi_uuid_manual == "ABCD1234-1111-2222-3333-444444444444"


@pytest.mark.asyncio
async def test_manual_duplicate_cfdi_requires_explicit_confirmation(monkeypatch) -> None:
    payload = _shared_payload(confirmed=False)
    session = _CreationSession([SimpleNamespace(), SimpleNamespace()])
    report = SimpleNamespace(id=uuid4(), total=204624.00)
    conflict = SimpleNamespace(
        empleado_id=payload.empleado_id,
        message=lambda: "La factura está reservada en S-ANTERIOR.",
    )

    async def fake_reference(*_args):
        return "S-TEST"

    async def fake_operations_reference(*_args):
        return "1"

    async def fake_find_report(*_args):
        return report

    async def fake_conflict(*_args):
        return conflict

    monkeypatch.setattr(documento_service, "generate_documento_reference_number", fake_reference)
    monkeypatch.setattr(
        documento_service, "allocate_referencia_operaciones_for_empleado",
        fake_operations_reference,
    )
    monkeypatch.setattr(documento_service, "find_cfdi_report_by_fiscal_uuid", fake_find_report)
    monkeypatch.setattr(documento_service, "find_blocking_cfdi_usage", fake_conflict)

    with pytest.raises(SolicitudValidationError) as raised:
        await documento_service.create_solicitud_terceros_document(session, payload)

    assert raised.value.code == "duplicate_cfdi"
    assert "factura compartida" in raised.value.user_message.lower()
