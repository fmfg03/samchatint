"""Supplier advance business regressions with real journals and persistence."""

from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import event, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.ext.compiler import compiles

from devnous.gastos.models import (
    AccountingPoliza,
    AccountingPolizaLine,
    Adjunto,
    Base,
    BudgetConcept,
    CFDIReport,
    CuentaContable,
    Documento,
    Empleado,
    ExpenseReport,
    ProveedorCliente,
    RFCConfig,
)
from devnous.gastos.routes import supplier_advance_routes as routes
from devnous.gastos.routes import user_routes
from devnous.gastos.services import documento_workflow_service as workflow
from devnous.gastos.services import employee_debtor_accounting_service as accounting
from devnous.gastos.services import supplier_advance_service as service
from devnous.gastos.services.documento_payment_service import (
    DocumentoPaymentPermissionError,
    DocumentoPaymentValidationError,
    register_document_payment,
)
from devnous.gastos.services.documento_service import (
    SolicitudTercerosAttachment,
    SolicitudValidationError,
    add_solicitud_documento_adjuntos,
)
from samchat.budgets.service import budget_document_effect_snapshot


@compiles(JSONB, "sqlite")
def sqlite_jsonb(_type, _compiler, **_kw):
    return "JSON"


@pytest_asyncio.fixture
async def case(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://")

    @event.listens_for(engine.sync_engine, "connect")
    def sqlite_lock_stubs(dbapi_connection, _record):
        # SQLite verifies persistence/journals; PostgreSQL lock semantics are separate.
        dbapi_connection.create_function("hashtext", 1, lambda _value: 1)
        dbapi_connection.create_function("pg_advisory_xact_lock", 1, lambda _value: 1)

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        await connection.execute(
            text("CREATE TABLE documento_authorization_routes (documento_id TEXT)")
        )
    async with AsyncSession(engine, expire_on_commit=False) as session:
        owner = Empleado(id=uuid4(), nombre="Solicitante", rol="empleado")
        accountant = Empleado(id=uuid4(), nombre="Contabilidad", rol="superadmin")
        outsider = Empleado(id=uuid4(), nombre="Otro", rol="empleado")
        provider = ProveedorCliente(
            id=uuid4(),
            nombre="Proveedor",
            rfc="AAA010101AAA",
            tipo="proveedor",
            activo=True,
        )
        accounts = {
            code: CuentaContable(
                id=uuid4(), codigo=code, nombre=code, tipo=kind, activo=True
            )
            for code, kind in [
                (service.ADVANCE_ACCOUNT, "anticipo"),
                (service.LIABILITY_ACCOUNT, "proveedor"),
                ("1120-001-001", "banco"),
                ("6000-001-001", "gasto"),
                ("1180-001-001", "iva"),
                ("2160-001-001", "retencion"),
            ]
        }
        concept = BudgetConcept(
            id=uuid4(),
            concept_name="Gasto",
            tournament_name="Proyecto",
            concept_key="gasto",
            active=True,
            cuenta_contable_id=accounts["6000-001-001"].id,
            pasivo_cuenta_contable_id=accounts[service.LIABILITY_ACCOUNT].id,
        )
        advance = Documento(
            id=uuid4(),
            empleado_id=owner.id,
            tipo="SOLICITUD",
            numero_referencia="S-ANT",
            estado="aprobado",
            is_supplier_advance=True,
            supplier_advance_due_date=date(2026, 10, 31),
            proveedor_cliente_id=provider.id,
            monto_solicitado=Decimal("1000"),
            monto_total=Decimal("1000"),
            currency="MXN",
            proyecto_otro="Proyecto",
            concepto_pago="Servicio",
        )
        receiver = RFCConfig(
            id=uuid4(),
            name="Empresa",
            tax_id="BBB010101BBB",
            taxpayer="Empresa",
            active=True,
        )
        session.add_all(
            [
                owner,
                accountant,
                outsider,
                provider,
                concept,
                advance,
                receiver,
                *accounts.values(),
            ]
        )
        await session.commit()
        from devnous.gastos.services import (
            authorization_profile_service,
            documento_telegram,
            project_authorization_service,
        )
        from devnous.gastos.utils import receipt_bytes

        monkeypatch.setattr(
            receipt_bytes,
            "get_adjunto_columns",
            AsyncMock(return_value={c.name for c in Adjunto.__table__.columns}),
        )
        monkeypatch.setattr(
            project_authorization_service,
            "prepare_document_authorization_route",
            AsyncMock(),
        )
        monkeypatch.setattr(
            workflow, "prepare_document_authorization_route", AsyncMock()
        )
        monkeypatch.setattr(workflow, "invalidate_document_route", AsyncMock())
        monkeypatch.setattr(
            workflow, "record_customer_success_audit_event", AsyncMock()
        )
        monkeypatch.setattr(
            authorization_profile_service,
            "build_authorization_route_hard_block",
            AsyncMock(return_value=None),
        )
        monkeypatch.setattr(
            authorization_profile_service,
            "build_authorization_route_soft_warning",
            AsyncMock(return_value=None),
        )
        monkeypatch.setattr(
            documento_telegram,
            "schedule_document_workflow_telegram_notifications",
            lambda **_: None,
        )
        monkeypatch.setattr(
            documento_telegram,
            "schedule_budget_control_telegram_notifications",
            lambda **_: None,
        )
        yield SimpleNamespace(
            session=session,
            owner=owner,
            accountant=accountant,
            outsider=outsider,
            provider=provider,
            accounts=accounts,
            concept=concept,
            advance=advance,
            monkeypatch=monkeypatch,
        )
    await engine.dispose()


def xml_attachment(
    total="1000",
    *,
    issuer="AAA010101AAA",
    receiver="BBB010101BBB",
    currency="MXN",
    kind="I",
    uuid=None,
):
    fiscal_id = uuid or str(uuid4()).upper()
    xml = f"""<?xml version="1.0"?><cfdi:Comprobante xmlns:cfdi="http://www.sat.gob.mx/cfd/4" xmlns:tfd="http://www.sat.gob.mx/TimbreFiscalDigital" Version="4.0" Fecha="2026-10-08T12:00:00" SubTotal="{total}" Total="{total}" Moneda="{currency}" TipoDeComprobante="{kind}" MetodoPago="PUE"><cfdi:Emisor Rfc="{issuer}" Nombre="Proveedor"/><cfdi:Receptor Rfc="{receiver}" Nombre="Empresa"/><cfdi:Conceptos><cfdi:Concepto Descripcion="Servicio" Cantidad="1" ValorUnitario="{total}" Importe="{total}"/></cfdi:Conceptos><cfdi:Complemento><tfd:TimbreFiscalDigital UUID="{fiscal_id}" Version="1.1" FechaTimbrado="2026-10-08T12:00:01"/></cfdi:Complemento></cfdi:Comprobante>"""
    return SolicitudTercerosAttachment(
        raw_bytes=xml.encode(),
        filename="factura.xml",
        mime_type="application/xml",
        categoria="cfdi_xml",
    )


async def pay(case, document):
    return await register_document_payment(
        case.session,
        documento_id=document.id,
        actor_id=case.accountant.id,
        actor=case.accountant,
        notify=False,
    )


async def submit(case, total="1000", **kwargs):
    child = await service.submit_supplier_invoice(
        case.session,
        advance_id=case.advance.id,
        actor=case.owner,
        submission_id=kwargs.pop("submission_id", uuid4()),
        xml=kwargs.pop("xml", xml_attachment(total)),
        **kwargs,
    )
    await case.session.commit()
    return child


async def approve(case, child):
    child.budget_concept_id = case.concept.id
    child.estado = "enviado"
    await case.session.commit()
    return await workflow.transition_documento_workflow(
        case.session,
        documento_id=child.id,
        actor_id=case.accountant.id,
        action="approve",
        comentario="Factura validada",
    )


async def balance(case, code):
    lines = list(
        (
            await case.session.execute(
                select(AccountingPolizaLine).where(
                    AccountingPolizaLine.cuenta_codigo == code
                )
            )
        )
        .scalars()
        .all()
    )
    return sum(
        (Decimal(str(l.debe or 0)) - Decimal(str(l.haber or 0)) for l in lines),
        Decimal("0"),
    )


@pytest.mark.parametrize(
    "invoice,available,applied,remainder",
    [
        ("1000", "1000", "1000", "0"),
        ("1500", "1000", "1000", "500"),
        ("600", "1000", "600", "0"),
        ("0.03", "0.02", "0.02", "0.01"),
    ],
)
def test_allocation(invoice, available, applied, remainder):
    assert service.invoice_allocation(invoice, available) == (
        Decimal(applied),
        Decimal(remainder),
    )


@pytest.mark.parametrize("value", [None, "bad", "NaN", "Infinity", "-1", "0"])
def test_invalid_invoice_amount(value):
    with pytest.raises(SolicitudValidationError):
        service.invoice_allocation(value, 1000)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "total,remainder", [("1000", "0"), ("1500", "500"), ("600", "0")]
)
async def test_full_lifecycle_never_repays_original_or_creates_advance_expense(
    case, total, remainder
):
    assert not workflow.documento_requires_budget_control(case.advance)
    assert (
        await accounting.ensure_provider_approval_posting(
            case.session, documento=case.advance
        )
    ).status == "skipped"
    await pay(case, case.advance)
    assert await balance(case, service.ADVANCE_ACCOUNT) == Decimal("1000")
    assert await balance(case, "1120-001-001") == Decimal("-1000")
    assert case.advance.gasto_generado_id is None
    child = await submit(case, total)
    assert child.estado == "control_presupuestal"
    assert child.monto_total == Decimal(remainder)
    assert child.supplier_invoice_total == Decimal(total)
    # Use the real journal builder and canonical tax preview in this untaxed fixture.
    await approve(case, child)
    assert child.estado == ("aprobado" if Decimal(remainder) > 0 else "cerrado")
    expense = (
        await case.session.execute(
            select(ExpenseReport).where(ExpenseReport.documento_id == child.id)
        )
    ).scalar_one()
    assert expense.gasto_cantidad == float(total)
    assert expense.cfdi_report_id == child.cfdi_report_id
    cfdi = await case.session.get(CFDIReport, child.cfdi_report_id)
    assert budget_document_effect_snapshot(child, cfdi, expenses=[])[
        "amount"
    ] == Decimal(total)
    assert not budget_document_effect_snapshot(case.advance, expenses=[])[
        "affects_budget"
    ]
    if Decimal(remainder) > 0:
        await pay(case, child)
        assert child.estado == "pagado"
    else:
        assert child.pagado_en is None
        with pytest.raises(DocumentoPaymentValidationError):
            await pay(case, child)
    assert await balance(case, service.LIABILITY_ACCOUNT) == 0
    assert await balance(case, service.ADVANCE_ACCOUNT) == max(
        Decimal("1000") - Decimal(total), Decimal(0)
    )
    assert await balance(case, "1120-001-001") == -(
        Decimal("1000") + Decimal(remainder)
    )
    polizas = list(
        (await case.session.execute(select(AccountingPoliza))).scalars().all()
    )
    assert all(len(p.numero_poliza) <= 50 for p in polizas)
    for poliza in polizas:
        assert sum(l.debe or 0 for l in poliza.lines) == sum(
            l.haber or 0 for l in poliza.lines
        )
    before = len(polizas)
    assert (
        await accounting.ensure_provider_approval_posting(case.session, documento=child)
    ).status == "exists"
    assert (
        len(
            list((await case.session.execute(select(AccountingPoliza))).scalars().all())
        )
        == before
    )


@pytest.mark.asyncio
async def test_partial_invoices_reserve_and_release_without_overapplication(case):
    await pay(case, case.advance)
    first = await submit(case, "600")
    assert (await service.advance_balances(case.session, case.advance))[
        "available"
    ] == 400
    second = await submit(case, "600")
    assert second.supplier_advance_applied == 400 and second.monto_total == 200
    with pytest.raises(SolicitudValidationError, match="reservado"):
        await submit(case, "100")
    second.estado = "rechazado"
    await case.session.commit()
    third = await submit(case, "300")
    assert third.supplier_advance_applied == 300
    await approve(case, first)
    await approve(case, third)
    amounts = await service.advance_balances(case.session, case.advance)
    assert (
        amounts["applied"] == 900
        and amounts["pending"] == 100
        and amounts["available"] == 100
    )
    assert await balance(case, service.ADVANCE_ACCOUNT) == 100


@pytest.mark.asyncio
async def test_invoice_submission_is_idempotent_and_duplicate_uuid_blocked(case):
    await pay(case, case.advance)
    key = uuid4()
    xml = xml_attachment("600")
    first = await submit(case, submission_id=key, xml=xml)
    replay = await submit(case, submission_id=key, xml=xml)
    assert replay.id == first.id
    with pytest.raises(Exception, match="CFDI|UUID|factura|vinculad"):
        await submit(case, xml=xml)
    await case.session.rollback()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs,code",
    [
        ({"issuer": "OTHER"}, "issuer_mismatch"),
        ({"receiver": "OTHER"}, "receiver_mismatch"),
        ({"currency": "USD"}, "currency_mismatch"),
        ({"kind": "P"}, "invalid_invoice"),
    ],
)
async def test_fiscal_identity_validation(case, kwargs, code):
    await pay(case, case.advance)
    with pytest.raises(SolicitudValidationError) as error:
        await submit(case, xml=xml_attachment(**kwargs))
    assert error.value.code == code


@pytest.mark.asyncio
async def test_owner_and_payment_authority_boundaries(case):
    with pytest.raises(SolicitudValidationError, match="confirmarse"):
        await submit(case)
    with pytest.raises(DocumentoPaymentPermissionError):
        await register_document_payment(
            case.session,
            documento_id=case.advance.id,
            actor_id=case.owner.id,
            actor=case.owner,
            notify=False,
        )
    await pay(case, case.advance)
    with pytest.raises(SolicitudValidationError, match="solicitante"):
        await service.submit_supplier_invoice(
            case.session,
            advance_id=case.advance.id,
            actor=case.outsider,
            submission_id=uuid4(),
            xml=xml_attachment(),
        )
    with pytest.raises(HTTPException) as error:
        await routes.supplier_invoice_form(case.advance.id, case.session, case.outsider)
    assert error.value.status_code == 403
    with pytest.raises(DocumentoPaymentValidationError):
        await pay(case, case.advance)


@pytest.mark.asyncio
async def test_ui_and_evidence_immutability(case):
    await pay(case, case.advance)
    html = await routes.supplier_invoice_form(case.advance.id, case.session, case.owner)
    assert 'name="archivo_xml"' in html and 'name="submission_id"' in html
    assert "sin transferencia" in html
    html = await routes.render_supplier_advance_controls(
        case.session, case.advance, case.owner
    )
    assert "Pendiente de comprobar" in html and "Comprobar anticipo" in html
    child = await submit(case, "600")
    assert "Cantidad a pagar: 0.00" in await routes.render_supplier_advance_controls(
        case.session, child, case.owner
    )
    assert not user_routes._can_edit_solicitud_terceros(child, case.owner)
    assert not user_routes._can_remove_solicitud_adjunto(child, case.owner, "cfdi_xml")
    with pytest.raises(SolicitudValidationError):
        await add_solicitud_documento_adjuntos(
            case.session, documento=child, attachments=[xml_attachment()]
        )
    assert "S-" in await routes.render_supplier_advance_controls(
        case.session, case.advance, case.owner
    )


@pytest.mark.asyncio
async def test_missing_accounts_blocks_payment_without_bank_or_status_change(case):
    case.accounts[service.ADVANCE_ACCOUNT].activo = False
    await case.session.commit()
    with pytest.raises(DocumentoPaymentValidationError):
        await pay(case, case.advance)
    assert case.advance.estado == "aprobado"
    assert await balance(case, "1120-001-001") == 0


@pytest.mark.asyncio
async def test_changed_invoice_blocks_approval_without_posting(case):
    await pay(case, case.advance)
    child = await submit(case)
    cfdi = await case.session.get(CFDIReport, child.cfdi_report_id)
    cfdi.total = 2000
    await case.session.commit()
    with pytest.raises(
        workflow.DocumentoWorkflowValidationError, match="invoice_amount_changed"
    ):
        await approve(case, child)
    await case.session.rollback()
    assert await balance(case, service.LIABILITY_ACCOUNT) == 0


@pytest.mark.asyncio
async def test_http_submit_commits_canonical_movement_and_replay(case):
    from io import BytesIO

    from starlette.datastructures import UploadFile

    await pay(case, case.advance)
    key = uuid4()
    payload = xml_attachment("700")
    response = await routes.supplier_invoice_submit(
        case.advance.id,
        key,
        UploadFile(BytesIO(payload.raw_bytes), filename="factura.xml"),
        None,
        case.session,
        case.owner,
    )
    assert response.status_code == 303 and response.headers["location"].startswith(
        "/documentos/"
    )
    # The same browser key returns the same movement and cannot allocate twice.
    replay = await routes.supplier_invoice_submit(
        case.advance.id,
        key,
        UploadFile(BytesIO(payload.raw_bytes), filename="factura.xml"),
        None,
        case.session,
        case.owner,
    )
    assert replay.headers["location"] == response.headers["location"]
    assert (await service.advance_balances(case.session, case.advance))[
        "reserved"
    ] == 700


@pytest.mark.asyncio
async def test_http_invalid_invoice_rolls_back_without_reserving(case):
    from io import BytesIO

    from starlette.datastructures import UploadFile

    await pay(case, case.advance)
    advance_id = case.advance.id
    owner = case.owner
    with pytest.raises(HTTPException) as error:
        await routes.supplier_invoice_submit(
            advance_id,
            uuid4(),
            UploadFile(BytesIO(b"bad"), filename="bad.xml"),
            None,
            case.session,
            owner,
        )
    assert error.value.status_code == 422
    advance = await case.session.get(Documento, advance_id)
    assert (await service.advance_balances(case.session, advance))["reserved"] == 0


@pytest.mark.asyncio
async def test_missing_advance_and_fully_reserved_form(case):
    with pytest.raises(SolicitudValidationError, match="no encontrado"):
        await service._lock_advance(case.session, uuid4())
    with pytest.raises(HTTPException) as error:
        await routes.supplier_invoice_form(uuid4(), case.session, case.owner)
    assert error.value.status_code == 404
    with pytest.raises(HTTPException) as error:
        await routes.supplier_invoice_form(case.advance.id, case.session, case.owner)
    assert error.value.status_code == 409
    await pay(case, case.advance)
    child = await submit(case)
    with pytest.raises(HTTPException) as error:
        await routes.supplier_invoice_form(case.advance.id, case.session, case.owner)
    assert error.value.status_code == 409
    await approve(case, child)
    assert "Comprobado" in await routes.render_supplier_advance_controls(
        case.session, case.advance, case.owner
    )


def test_initial_requires_due_date_and_reserves_invoice_capture_for_later():
    payload = SimpleNamespace(
        supplier_advance_due_date=None,
        monto_solicitado=1000,
        cfdi_uuid_manual=None,
        cfdi_compartido_confirmado=False,
        attachments=[],
    )
    with pytest.raises(SolicitudValidationError):
        service.validate_initial_advance(payload, None)
    service.validate_initial_advance(payload, "2026-10-31")
    assert payload.supplier_advance_due_date == date(2026, 10, 31)
    payload.attachments = [xml_attachment()]
    with pytest.raises(SolicitudValidationError):
        service.validate_initial_advance(payload, "2026-10-31")


@pytest.mark.asyncio
async def test_missing_invoice_accounts_blocks_approval(case):
    await pay(case, case.advance)
    child = await submit(case)
    case.accounts[service.LIABILITY_ACCOUNT].activo = False
    await case.session.commit()
    with pytest.raises(
        workflow.DocumentoWorkflowValidationError,
        match="missing_supplier_advance_accounts",
    ):
        await approve(case, child)
    await case.session.rollback()
    assert await balance(case, service.ADVANCE_ACCOUNT) == 1000


@pytest.mark.asyncio
async def test_invoice_with_retention_uses_net_payable_and_full_tax_accrual(case):
    # Account binding is supplied by the existing preview; journal construction
    # and application/payment amounts remain real and persisted.
    from devnous.gastos.services import expense_accounting_service

    await pay(case, case.advance)
    child = await submit(case, "1040")
    iva = case.accounts["1180-001-001"]
    retention = case.accounts["2160-001-001"]

    async def tax_preview(_session, expense, **_kwargs):
        assert expense.gasto_cantidad == 1040
        return {
            "taxes": {
                "base_gasto": 1000,
                "iva_trasladado": 160,
                "iva_account": {
                    "codigo": iva.codigo,
                    "cuenta_contable_id": str(iva.id),
                },
                "retenciones": [
                    {
                        "importe": 120,
                        "account": {
                            "codigo": retention.codigo,
                            "cuenta_contable_id": str(retention.id),
                        },
                    }
                ],
                "neto_contrapartida": 1040,
            }
        }

    case.monkeypatch.setattr(
        expense_accounting_service, "build_expense_accounting_preview", tax_preview
    )
    await approve(case, child)
    assert await balance(case, "6000-001-001") == 1000
    assert await balance(case, iva.codigo) == 160
    assert await balance(case, retention.codigo) == -120
    assert await balance(case, service.LIABILITY_ACCOUNT) == -40
    await pay(case, child)
    assert await balance(case, service.LIABILITY_ACCOUNT) == 0
    assert await balance(case, "1120-001-001") == -1040


@pytest.mark.asyncio
async def test_web_creation_and_submission_bypasses_budget_only_for_initial_advance(
    case,
):
    import httpx
    from fastapi import FastAPI
    from devnous.gastos.routes.dependencies import get_current_empleado, get_db_session

    app = FastAPI()
    app.include_router(user_routes.router)
    app.dependency_overrides[get_current_empleado] = lambda: case.owner
    app.dependency_overrides[get_db_session] = lambda: case.session
    app.dependency_overrides[user_routes.get_db_session] = lambda: case.session
    case.monkeypatch.setattr(
        user_routes, "record_customer_success_audit_event", AsyncMock()
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/documentos/nueva-solicitud-terceros",
            data={
                "monto_solicitado": "2500",
                "proveedor_cliente_id": str(case.provider.id),
                "torneo_id": "__otro__",
                "proyecto_otro": "Proyecto",
                "concepto_pago": "Reserva de servicio",
                "is_supplier_advance": "1",
                "supplier_advance_due_date": "2026-10-31",
                "submit_mode": "create",
            },
        )
    assert response.status_code == 303
    created = (
        await case.session.execute(
            select(Documento).where(Documento.monto_solicitado == 2500)
        )
    ).scalar_one()
    assert created.is_supplier_advance and created.estado == "borrador"
    assert created.supplier_advance_due_date == date(2026, 10, 31)
    await workflow.transition_documento_workflow(
        case.session, documento_id=created.id, actor_id=case.owner.id, action="send"
    )
    assert created.estado == "enviado" and created.budget_concept_id is None
    await workflow.transition_documento_workflow(
        case.session,
        documento_id=created.id,
        actor_id=case.accountant.id,
        action="approve",
    )
    assert created.estado == "aprobado"
    assert await balance(case, "1120-001-001") == 0
