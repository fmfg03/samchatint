"""Approved budget-account and tax regressions, without production data."""

import inspect

from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from devnous.gastos.routes import user_routes
from devnous.gastos.services import budget_concept_account_service as mapping
from devnous.gastos.services import expense_accounting_service as accounting
from devnous.gastos.services import expense_service
from devnous.gastos.services import expense_coi_export_service as export_service
from devnous.gastos.services.coi_poliza_exporter import build_coi_poliza_preview


def invoice(**changes):
    values = dict(subtotal=1000, descuento=0, total=1196,
                  total_impuestos_trasladados=160,
                  impuestos_detalle={
                      "traslados": [{"impuesto": "002", "importe": 160}],
                      "retenciones": [],
                      "locales": [{"tipo": "traslado", "importe": 36}]})
    values.update(changes)
    return SimpleNamespace(**values)


def test_local_tax_does_not_inflate_iva():
    taxes = accounting.summarize_cfdi_tax_components(invoice())
    assert taxes["iva_trasladado"] == 160


def test_ieps_only_does_not_become_iva():
    cfdi = invoice(
        total=1080, total_impuestos_trasladados=80,
        impuestos_detalle={"traslados": [{"impuesto": "003", "importe": 80}]},
    )
    assert accounting.summarize_cfdi_tax_components(cfdi)["iva_trasladado"] == 0


def test_legacy_derivation_excludes_local_tax():
    cfdi = invoice(total_impuestos_trasladados=0,
                   impuestos_detalle={"locales": [{"tipo": "traslado", "importe": 36}]})
    assert accounting.summarize_cfdi_tax_components(cfdi)["iva_trasladado"] == 160


@pytest.mark.asyncio
async def test_document_partida_propagates_account_without_overwriting(monkeypatch):
    concept_id, account_id = uuid4(), uuid4()
    expense = SimpleNamespace(cuenta_contable_id=None, budget_concept_id=None,
                              documento_id=uuid4())
    session = SimpleNamespace(execute=AsyncMock(side_effect=[
        SimpleNamespace(scalar_one_or_none=lambda: concept_id),
        SimpleNamespace(scalar_one_or_none=lambda: SimpleNamespace(
            id=concept_id, cuenta_contable_id=account_id)),
    ]))
    monkeypatch.setattr(mapping, "validate_active_cuenta_contable_id",
                        AsyncMock(return_value=str(account_id)))
    assert await mapping.apply_budget_concept_cuenta_mapping(session, expense)
    assert expense.cuenta_contable_id == account_id
    assert expense.budget_concept_id == concept_id
    # Existing explicit selections are authoritative and cause no reads/writes.
    session.execute.reset_mock()
    assert not await mapping.apply_budget_concept_cuenta_mapping(session, expense)
    session.execute.assert_not_called()


@pytest.mark.asyncio
async def test_inactive_mapping_is_not_assigned(monkeypatch):
    expense = SimpleNamespace(cuenta_contable_id=None, budget_concept_id=uuid4())
    session = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(
        scalar_one_or_none=lambda: SimpleNamespace(
            id=expense.budget_concept_id, cuenta_contable_id=uuid4()))))
    monkeypatch.setattr(mapping, "validate_active_cuenta_contable_id",
                        AsyncMock(side_effect=ValueError("inactive")))
    assert not await mapping.apply_budget_concept_cuenta_mapping(session, expense)
    assert expense.cuenta_contable_id is None


@pytest.mark.asyncio
async def test_preview_warns_on_catalog_discrepancy_and_unknown_manual_iva(monkeypatch):
    assigned, mapped = uuid4(), uuid4()
    expense = SimpleNamespace(
        cuenta_contable_id=assigned,
        budget_concept=SimpleNamespace(id=uuid4(), cuenta_contable_id=mapped),
        cfdi_report=None, iva=None, gasto_cantidad=112, propina_no_deducible=0)
    monkeypatch.setattr(
        accounting, "resolve_counterpart_account", AsyncMock(return_value=(None, None))
    )
    monkeypatch.setattr(accounting, "_load_active_accounts", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        accounting, "resolve_hospedaje_local_tax", lambda *a, **k: {"amount": 0}
    )
    preview = await accounting.build_expense_accounting_preview(object(), expense)
    assert any("difiere del catálogo" in note for note in preview["notes"])
    assert any("Sin desglose fiscal" in note for note in preview["notes"])
    assert preview["taxes"]["iva_trasladado"] == 0
    assert expense.cuenta_contable_id == assigned


@pytest.mark.asyncio
@pytest.mark.parametrize("net_tax,total", [("12", "112"), ("-4", "96"), ("0", "100")])
async def test_manual_net_tax_is_not_persisted_as_iva(monkeypatch, net_tax, total):
    values = user_routes._quick_expense_values(
        concepto="Manual", fecha="2026-10-09",
        numero_factura="vale azul", subtotal="100", descuento="0",
        impuestos_y_retenciones=net_tax)
    assert values["total"] == Decimal(total)
    assert values["iva"] is None
    monkeypatch.setattr(
        expense_service, "generate_reference_number",
        AsyncMock(return_value="Operaciones 001"),
    )
    monkeypatch.setattr(
        expense_service, "get_concepto_mapping", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        expense_service, "apply_budget_concept_cuenta_mapping",
        AsyncMock(return_value=False),
    )
    session = SimpleNamespace(add=Mock(), flush=AsyncMock())
    expense = await expense_service.create_expense_from_data(
        session=session,
        concepto=values["concepto"], fecha=values["fecha"],
        gasto_cantidad=float(values["total"]), iva=values["iva"],
        tipo_gasto="manual", origen="informe_quick_entry")
    session.add.assert_called_once_with(expense)
    session.flush.assert_awaited_once()
    assert expense.iva is None
    assert expense.gasto_cantidad == float(total)


@pytest.mark.asyncio
async def test_unloaded_relationships_are_not_accessed_implicitly():
    concept_id = uuid4()
    concept = SimpleNamespace(id=concept_id, cuenta_contable_id=uuid4())

    class UnloadedExpense:
        budget_concept_id = concept_id

        @property
        def budget_concept(self):
            raise AssertionError("Implicit relationship load")

    session = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(
        scalar_one_or_none=lambda: concept)))
    loaded = await mapping.load_effective_budget_concept(session, UnloadedExpense())
    assert loaded is concept
    session.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_catalog_account_is_flagged_without_assignment(monkeypatch):
    expense = SimpleNamespace(
        cuenta_contable_id=None,
        budget_concept=SimpleNamespace(id=uuid4(), cuenta_contable_id=None),
        cfdi_report=invoice(), gasto_cantidad=1196, propina_no_deducible=0)
    monkeypatch.setattr(
        accounting, "resolve_counterpart_account", AsyncMock(return_value=(None, None))
    )
    monkeypatch.setattr(accounting, "_load_active_accounts", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        accounting, "_resolve_iva_account", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        accounting, "resolve_hospedaje_local_tax", lambda *a, **k: {"amount": 0}
    )
    preview = await accounting.build_expense_accounting_preview(object(), expense)
    assert any("no tiene cuenta contable mapeada" in note for note in preview["notes"])
    assert expense.cuenta_contable_id is None


@pytest.mark.asyncio
async def test_linked_partida_and_tax_breakdown_reach_final_coi_movements(monkeypatch):
    charge = SimpleNamespace(id=uuid4(), codigo="5300-012-031", nombre="Hospedaje")
    iva = SimpleNamespace(id=uuid4(), codigo="1180-001-001", nombre="IVA")
    local = SimpleNamespace(id=uuid4(), codigo="5300-012-032", nombre="ISH")
    bank = SimpleNamespace(id=uuid4(), codigo="1100-001-001", nombre="Banco")
    concept = SimpleNamespace(id=uuid4(), cuenta_contable_id=charge.id)
    cfdi = invoice(cfdi_uuid="12345678-1234-1234-1234-1234567890AB")
    expense = SimpleNamespace(
        cuenta_contable_id=None, budget_concept_id=None,
        documento=SimpleNamespace(budget_concept=concept),
        cfdi_report=cfdi, cfdi_report_id=uuid4(),
        cuenta_contable=None, contra_cuenta_contable=bank, cuenta_iva=iva,
        gasto_cantidad=1196, propina_no_deducible=0,
        hospedaje_impuesto_monto=36, hospedaje_impuesto_confirmado=True,
        fecha=datetime(2026, 10, 9), concepto="Hospedaje", proyecto="LTTB",
        numero_referencia="Operaciones 001",
    )
    session = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(
        scalar_one_or_none=lambda: concept)))
    monkeypatch.setattr(
        mapping, "validate_active_cuenta_contable_id",
        AsyncMock(return_value=str(charge.id)),
    )
    assert await mapping.apply_budget_concept_cuenta_mapping(session, expense)
    # Reflect the relationship after persisting/refreshing the mapped FK.
    expense.cuenta_contable = charge
    monkeypatch.setattr(
        accounting, "_load_active_accounts", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        accounting, "_resolve_hospedaje_tax_account", AsyncMock(return_value=local)
    )
    monkeypatch.setattr(
        export_service, "assess_expense_coi_cleanup_ready",
        AsyncMock(return_value=(True, [])),
    )
    dto = await export_service.build_expense_cfdi_for_export(session, expense)
    final = build_coi_poliza_preview([dto])[0]
    movements = {item["kind"]: item for item in final["movements"]}
    assert movements["gasto_base"]["cuenta"] == charge.codigo
    assert movements["gasto_base"]["debe"] == 1000
    assert movements["iva"]["cuenta"] == iva.codigo
    assert movements["iva"]["debe"] == 160
    assert movements["impuesto_local_hospedaje"]["cuenta"] == local.codigo
    assert movements["impuesto_local_hospedaje"]["debe"] == 36
    assert final["totals"] == {"debe": 1196, "haber": 1196}


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize("supplement", [False, True])
async def test_quick_route_maps_after_informe_ownership_link(
    monkeypatch, explicit, supplement
):
    concept_id, mapped_id, explicit_id = uuid4(), uuid4(), uuid4()
    employee = SimpleNamespace(id=uuid4(), nombre="Captura", departamento="Operaciones")
    cuenta = SimpleNamespace(
        id=uuid4(), empleado_id=employee.id, empleado=employee, torneo=None,
        referencia_base="001", fase="Nacional", categorias=[], edicion=2026,
        currency="MXN", beneficiario_proveedor_cliente=None,
    )
    informe = SimpleNamespace(id=uuid4(), budget_concept_id=concept_id)
    concept = SimpleNamespace(id=concept_id, cuenta_contable_id=mapped_id)
    created = []
    session = SimpleNamespace(
        execute=AsyncMock(), commit=AsyncMock(), rollback=AsyncMock(),
        add=Mock(), flush=AsyncMock(),
    )

    async def execute(statement):
        sql = str(statement)
        if "FROM cuentas_de_gastos" in sql:
            result = cuenta
        elif "SELECT documentos.budget_concept_id" in sql:
            result = concept_id
        elif "FROM documentos" in sql:
            result = informe
        elif "FROM budget_concepts" in sql:
            result = concept
        else:
            raise AssertionError(sql)
        return SimpleNamespace(scalar_one_or_none=lambda: result)

    session.execute.side_effect = execute
    original_create = expense_service.create_expense_from_data

    async def create(**kwargs):
        expense = await original_create(**kwargs)
        assert expense.informe_documento_id is None
        assert expense.cuenta_contable_id is None
        if explicit:
            expense.cuenta_contable_id = explicit_id
        created.append(expense)
        return expense

    monkeypatch.setattr(user_routes, "create_expense_from_data", create)
    monkeypatch.setattr(user_routes, "_can_quick_capture_expense", lambda *a: True)
    monkeypatch.setattr(user_routes, "_ensure_expense_tip_schema", AsyncMock())
    monkeypatch.setattr(user_routes, "resolve_cfdi_upload", lambda **k: (None, None))
    monkeypatch.setattr(user_routes, "is_pdf_content", lambda *a: True)
    monkeypatch.setattr(user_routes, "create_adjunto_record", AsyncMock())
    monkeypatch.setattr(
        user_routes, "link_expense_to_cfdi_if_manual_uuid_set", AsyncMock()
    )
    monkeypatch.setattr(
        expense_service, "generate_reference_number", AsyncMock(return_value="001")
    )
    monkeypatch.setattr(
        expense_service, "get_concepto_mapping", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        mapping, "validate_active_cuenta_contable_id",
        AsyncMock(return_value=str(mapped_id)),
    )
    handler = user_routes.crear_gasto_rapido_en_informe
    kwargs = {
        name: getattr(parameter.default, "default", parameter.default)
        for name, parameter in inspect.signature(handler).parameters.items()
        if parameter.default is not inspect.Parameter.empty
    }
    kwargs.update(
        cuenta_id=cuenta.id, session=session, current_empleado=employee,
        concepto="Manual", fecha="2026-10-09", numero_factura="vale azul",
        subtotal="100", descuento="0", impuestos_y_retenciones="12",
    )
    if supplement:
        kwargs.update(
            asiento_preferencial_cfdi_pdf=SimpleNamespace(
                filename="test.pdf", read=AsyncMock(return_value=b"%PDF test")
            ),
            asiento_preferencial_subtotal="50",
            asiento_preferencial_impuestos_y_retenciones="6",
        )
    response = await handler(**kwargs)
    assert response.status_code == 303
    assert "gasto_rapido_creado" in response.headers["location"]
    session.commit.assert_awaited_once()
    session.rollback.assert_not_called()
    assert len(created) == (2 if supplement else 1)
    for expense in created:
        assert expense.informe_documento_id == informe.id
        assert expense.cuenta_contable_id == (explicit_id if explicit else mapped_id)
        if not explicit:
            assert expense.budget_concept_id == concept_id
        assert expense.iva is None
    assert created[0].gasto_cantidad == 112
    if supplement:
        assert created[1].gasto_cantidad == 56
