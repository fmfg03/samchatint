"""Synthetic evidence for beneficiary/card context without financial inference."""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from lxml import html as html_parser
from sqlalchemy import JSON, Column, MetaData, Table, event, insert
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from starlette.requests import Request

from devnous.gastos.models import (
    CuentaDeGastos,
    Documento,
    Empleado,
    ProveedorCliente,
)
from devnous.gastos.routes import admin_amex_accounting_routes as routes
from devnous.gastos.routes.dependencies import get_current_empleado, get_db_session
from devnous.gastos.services.amex_recognition_service import (
    load_amex_card_display_identities,
)
from devnous.gastos.services.documento_semantics import (
    effective_document_beneficiary_name,
)
from tests.unit.gastos.test_amex_accounting_cut_routes import session_with


def identity_review_fixture():
    """Return an isolated view with distinct capturer, beneficiary and debtor."""
    informe = SimpleNamespace(
        id=uuid4(),
        tipo="INFORME",
        cuenta_gastos_id=uuid4(),
        numero_referencia="I-SYNTHETIC",
        empleado=SimpleNamespace(nombre="Capturista sintético"),
        beneficiario_empleado=SimpleNamespace(nombre="Beneficiaria sintética"),
    )
    expenses = [
        SimpleNamespace(
            id=uuid4(),
            numero_referencia=f"O-SYNTHETIC-{index}",
            concepto="Alimentos de prueba",
            fecha=datetime(2026, 7, 2),
            gasto_cantidad=780.16,
            metodo_pago="TARJETA CREDITO AMEX",
            currency="MXN",
            ultimos_4_digitos="9999",  # Not an identity source for this view.
        )
        for index in range(2)
    ]
    accounts = [
        SimpleNamespace(id=uuid4(), codigo="1170-002-001", nombre="Titular sintético"),
        SimpleNamespace(id=uuid4(), codigo="1170-002-002", nombre="Deudor distinto"),
    ]
    review = SimpleNamespace(
        expense_id=expenses[0].id,
        treatment="partner_receivable",
        debtor_account_id=accounts[1].id,
        version=3,
        reason="Decisión manual de Finanzas",
    )
    identities = [
        (expense.id, uuid4(), "Titular sintético", "1234") for expense in expenses
    ]
    review.actor_id = uuid4()
    review.reviewed_at = datetime(2026, 7, 3)
    session = session_with(accounts, [], identities, [])
    session.preview = SimpleNamespace(
        status="ready",
        reason=None,
        informe=informe,
        expenses=expenses,
        reviews={review.expense_id: review},
        fresh_review_ids={review.expense_id},
        current_versions={
            str(expense.id): 3 if expense.id == review.expense_id else 0
            for expense in expenses
        },
        operational_evidence=None,
        approval_event=None,
        approval_version=None,
        source_key="a" * 64,
        exceptions={"source": "missing_amex_operational_attestation"},
    )
    session.identity_rows = identities
    session.get.return_value = informe
    return session, informe, expenses, accounts


async def render_identity_review(session, informe):
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            routes,
            "preview_amex_approved_informe",
            AsyncMock(
                return_value=session.__dict__.get("preview")
                or SimpleNamespace(
                    status="ready",
                    reason=None,
                    informe=SimpleNamespace(
                        id=informe.id, tipo="INFORME", numero_referencia="ORM"
                    ),
                    expenses=[],
                    reviews={},
                    fresh_review_ids=set(),
                    current_versions={},
                    operational_evidence=None,
                    approval_event=None,
                    approval_version=None,
                    source_key="a" * 64,
                    exceptions={},
                )
            ),
        )
        return await routes.amex_accounting_report_review(
            informe.id,
            Request({"type": "http", "query_string": b""}),
            session,
            SimpleNamespace(rol="finanzas", activo=True, nombre="Finanzas de prueba"),
        )


@pytest.mark.asyncio
async def test_distinct_people_preserve_manual_selection_and_read_only_view():
    session, informe, expenses, accounts = identity_review_fixture()
    response = await render_identity_review(session, informe)
    html = response.body.decode()
    tree = html_parser.fromstring(html)
    assert (
        tree.xpath('//*[@class="report-beneficiary"]')[0]
        .text_content()
        .endswith("Beneficiaria sintética")
    )
    assert "Capturista sintético" not in html
    for row in tree.xpath("//tbody/tr"):
        assert "Titular AMEX: Titular sintético" in row.text_content()
        assert "Tarjeta: ••••1234" in row.text_content()
    selected = tree.xpath('//select[@id="amex-batch-debtor"]/option[@selected]')
    assert selected == []
    assert "Cargo al socio por el total" in html and "Deudor distinto" in html
    assert "9999" not in html
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()
    session.add.assert_not_called()
    assert session.execute.await_count == 4  # Batch reads, independent of item count.
    statement = str(session.execute.await_args_list[2].args[0])
    assert "card_account_id" in statement and "consumption_id" in statement
    assert "last4 =" not in statement


@pytest.mark.parametrize(
    "source",
    ["document_employee", "document_provider", "account_employee", "account_provider"],
)
def test_canonical_explicit_beneficiary_priority_and_legacy_compatibility(source):
    person = SimpleNamespace(nombre="Persona explícita")
    informe = SimpleNamespace(empleado=SimpleNamespace(nombre="Capturista"))
    cuenta = SimpleNamespace(empleado=SimpleNamespace(nombre="Dueño de cuenta"))
    informe.cuenta_gastos = cuenta
    target = informe if source.startswith("document") else cuenta
    field = (
        "beneficiario_empleado"
        if source.endswith("employee")
        else "beneficiario_proveedor_cliente"
    )
    setattr(target, field, person)
    assert (
        effective_document_beneficiary_name(informe, explicit_only=True)
        == person.nombre
    )
    assert effective_document_beneficiary_name(informe) == person.nombre
    informe.beneficiario_empleado = SimpleNamespace(nombre="Prioridad documento")
    assert (
        effective_document_beneficiary_name(informe, explicit_only=True)
        == "Prioridad documento"
    )


@pytest.mark.asyncio
async def test_missing_beneficiary_and_card_links_never_use_capturer_or_suffix():
    session, informe, _, _ = identity_review_fixture()
    informe.beneficiario_empleado = None
    informe.cuenta_gastos = SimpleNamespace(empleado=informe.empleado)
    session.identity_rows.clear()
    response = await render_identity_review(session, informe)
    tree = html_parser.fromstring(response.body.decode())
    assert (
        tree.xpath('//*[@class="report-beneficiary"]')[0]
        .text_content()
        .endswith("Sin identificar")
    )
    assert effective_document_beneficiary_name(informe) == "Capturista sintético"
    for row in tree.xpath("//tbody/tr"):
        assert "Titular AMEX: Sin identificar" in row.text_content()
        assert "Tarjeta: Sin identificar" in row.text_content()


@pytest.mark.asyncio
async def test_person_context_escapes_html_and_rejects_full_pan_as_suffix():
    session, informe, expenses, _ = identity_review_fixture()
    malicious = '<img src=x onerror="alert(1)"> & Persona'
    informe.beneficiario_empleado.nombre = malicious
    session.identity_rows[:] = [
        (expenses[0].id, uuid4(), malicious, "378282246310005"),
        (expenses[1].id, uuid4(), None, "0001"),
    ]
    response = await render_identity_review(session, informe)
    html = response.body.decode()
    assert "<img" not in html and "&lt;img" in html and "&amp; Persona" in html
    assert "378282246310005" not in html
    tree = html_parser.fromstring(html)
    rows = tree.xpath("//tbody/tr")
    assert "Tarjeta: Sin identificar" in rows[0].text_content()
    assert "Titular AMEX: Sin identificar" in rows[1].text_content()
    assert "Tarjeta: ••••0001" in rows[1].text_content()


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", [None, "", "123", "12345", "<123", "１２３４"])
async def test_invalid_suffix_is_never_emitted(suffix):
    expense_id = uuid4()
    session = session_with([(expense_id, uuid4(), "Persona", suffix)])
    identities = await load_amex_card_display_identities(session, [expense_id])
    assert identities[expense_id].last4 is None


@pytest.mark.asyncio
async def test_empty_context_read_skips_query():
    session = AsyncMock()
    assert await load_amex_card_display_identities(session, []) == {}
    session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_sql_association_handles_same_suffix_missing_or_ambiguous_links():
    """Execute the real batch SQL against deliberately imperfect synthetic data."""
    engine = create_async_engine("sqlite+aiosqlite://")
    expense_ids = [uuid4() for _ in range(6)]
    cards, consumptions = [uuid4(), uuid4()], [uuid4(), uuid4(), uuid4()]
    try:
        async with engine.begin() as connection:
            for ddl in (
                "CREATE TABLE amex_recognition_representations (expense_id TEXT, consumption_id TEXT)",
                "CREATE TABLE amex_recognition_consumptions (id TEXT, card_account_id TEXT)",
                "CREATE TABLE amex_card_accounts (id TEXT, cardholder_name TEXT, last4 TEXT)",
            ):
                await connection.exec_driver_sql(ddl)
            for card, name in zip(cards, ("Titular A", "Titular B")):
                await connection.exec_driver_sql(
                    "INSERT INTO amex_card_accounts VALUES (?, ?, '1234')",
                    (card.hex, name),
                )
            for consumption, card in zip(consumptions, [*cards, uuid4()]):
                await connection.exec_driver_sql(
                    "INSERT INTO amex_recognition_consumptions VALUES (?, ?)",
                    (consumption.hex, card.hex),
                )
            for index, consumption in (
                (0, consumptions[0]),
                (1, consumptions[1]),
                (2, consumptions[0]),
                (2, consumptions[1]),
                (3, uuid4()),
                (4, consumptions[2]),
            ):
                await connection.exec_driver_sql(
                    "INSERT INTO amex_recognition_representations VALUES (?, ?)",
                    (expense_ids[index].hex, consumption.hex),
                )
        queries = []
        event.listen(
            engine.sync_engine,
            "before_cursor_execute",
            lambda *args: queries.append(args[2]),
        )
        async with engine.connect() as connection:
            identities = await load_amex_card_display_identities(
                connection, expense_ids
            )
        assert set(identities) == set(expense_ids[:2])
        assert [
            identities[identifier].cardholder_name for identifier in expense_ids[:2]
        ] == ["Titular A", "Titular B"]
        assert len(queries) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_beneficiary_orm_loading_works_without_lazy_reads():
    """Exercise actual ORM joins in the route, without unrelated graph loading."""
    engine = create_async_engine("sqlite+aiosqlite://")
    metadata = MetaData()
    for model in (Documento, CuentaDeGastos, Empleado, ProveedorCliente):
        Table(
            model.__tablename__,
            metadata,
            *(
                Column(
                    column.name,
                    JSON() if isinstance(column.type, JSONB) else column.type,
                    primary_key=column.primary_key,
                )
                for column in model.__table__.columns
            ),
        )
    report_id, account_id, beneficiary_id, capturer_id = [uuid4() for _ in range(4)]
    try:
        async with engine.begin() as connection:
            await connection.run_sync(metadata.create_all)
            for identifier, name in (
                (beneficiary_id, "Beneficiaria SQL"),
                (capturer_id, "Capturista SQL"),
            ):
                await connection.execute(
                    insert(metadata.tables["empleados"]).values(
                        id=identifier, nombre=name
                    )
                )
            await connection.execute(
                insert(metadata.tables["cuentas_de_gastos"]).values(
                    id=account_id,
                    empleado_id=capturer_id,
                    beneficiario_empleado_id=beneficiary_id,
                )
            )
            await connection.execute(
                insert(metadata.tables["documentos"]).values(
                    id=report_id,
                    tipo="INFORME",
                    cuenta_gastos_id=account_id,
                    empleado_id=capturer_id,
                )
            )
        queries = []
        event.listen(
            engine.sync_engine,
            "before_cursor_execute",
            lambda *args: queries.append(args[2]),
        )
        async with AsyncSession(engine) as real_session:
            session = session_with([], [], [], [])
            session.get.side_effect = real_session.get
            response = await render_identity_review(
                session, SimpleNamespace(id=report_id)
            )
        assert "Beneficiaria SQL" in response.body.decode()
        assert "Capturista SQL" not in response.body.decode()
        assert len(queries) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role,permissions,status",
    [
        ("finanzas", [], 200),
        ("admin", [], 200),
        ("superadmin", [], 200),
        ("empleado", [], 403),
        ("empleado", ["finanzas.manage"], 403),
    ],
)
async def test_context_is_only_visible_through_existing_finance_guard(
    monkeypatch, role, permissions, status
):
    session, informe, _, _ = identity_review_fixture()
    monkeypatch.setattr(
        routes, "preview_amex_approved_informe", AsyncMock(return_value=session.preview)
    )
    app = FastAPI()
    app.include_router(routes.router)
    actor = SimpleNamespace(
        id=uuid4(),
        rol=role,
        permissions=permissions,
        activo=True,
        nombre="Finanzas de prueba",
    )
    app.dependency_overrides[get_current_empleado] = lambda: actor
    app.dependency_overrides[get_db_session] = lambda: session
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(f"{routes.BASE}/informes/{informe.id}")
    assert response.status_code == status
    assert ("Beneficiaria sintética" in response.text) == (status == 200)
    assert ("Titular sintético" in response.text) == (status == 200)
    if status == 403:
        session.get.assert_not_awaited()
        session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_anonymous_context_denial_contains_no_identity():
    session, informe, _, _ = identity_review_fixture()
    app = FastAPI()
    app.include_router(routes.router)

    async def anonymous():
        raise HTTPException(401, "Sesión requerida")

    app.dependency_overrides[get_current_empleado] = anonymous
    app.dependency_overrides[get_db_session] = lambda: session
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(f"{routes.BASE}/informes/{informe.id}")
    assert response.status_code == 401
    assert "Beneficiaria" not in response.text and "Titular" not in response.text
    session.get.assert_not_awaited()
