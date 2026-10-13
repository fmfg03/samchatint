"""Route and rendered UI coverage for explicit AMEX card identification."""

import ast
import os
import subprocess
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from fastapi import HTTPException

from devnous.gastos.routes import user_routes


def card(*, active=True):
    from devnous.gastos.models import AmexCardAccount, CuentaContable

    return AmexCardAccount(
        id=uuid4(),
        active=active,
        card_label="Tarjeta <empresa>",
        cardholder_key="FGV",
        last4="1005",
        liability_cuenta_contable=CuentaContable(id=uuid4(), codigo="201-001"),
    )


def item(*, last4=None):
    return SimpleNamespace(
        id=uuid4(),
        numero_referencia="G-001",
        concepto="Alimentos",
        gasto_cantidad=116,
        fecha=datetime(2026, 9, 15),
        ultimos_4_digitos=last4,
        cuenta_gastos_id=uuid4(),
        informe_documento_id=uuid4(),
        moneda="MXN",
        empleado=None,
        empleado_id=uuid4(),
        cfdi_report_id=None,
        cfdi_uuid_manual=None,
    )


class Result:
    def __init__(self, items):
        self.items = items

    def scalars(self):
        return self

    def all(self):
        return self.items

    def scalar_one_or_none(self):
        return self.items[0] if self.items else None


async def render(monkeypatch, *, candidates, historical=None, informe_links=None):
    charge = item(last4="1005")
    catalog_card = card()
    session = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                Result([charge]),
                Result(candidates),
                Result(
                    informe_links
                    if informe_links is not None
                    else [
                        (
                            candidate.id,
                            SimpleNamespace(
                                numero_referencia="I-001", estado="aprobado"
                            ),
                        )
                        for candidate in candidates
                        if not candidate.ultimos_4_digitos
                    ]
                ),
            ]
        ),
        get=AsyncMock(
            return_value=SimpleNamespace(numero_referencia="I-001", estado="aprobado")
        ),
    )
    monkeypatch.setattr(
        user_routes, "list_amex_card_accounts", AsyncMock(return_value=[catalog_card])
    )
    monkeypatch.setattr(
        user_routes, "activation_reason", AsyncMock(return_value=historical)
    )
    monkeypatch.setattr(
        user_routes, "suggest_amex_cfdi_matches", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        user_routes, "suggest_pase_monthly_cfdi_matches", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(user_routes, "render_top_navigation", lambda *_a, **_k: "")
    actor = SimpleNamespace(id=uuid4(), rol="finanzas", nombre="Finanzas")
    view = user_routes.amex_conciliacion_view
    # Re-run the same acceptance test against the unmodified view for red evidence.
    if os.environ.get("AMEX_IDENTITY_BASELINE"):
        source = subprocess.check_output(
            ["git", "show", "HEAD:src/devnous/gastos/routes/user_routes.py"], text=True
        )
        node = next(
            n
            for n in ast.parse(source).body
            if isinstance(n, ast.AsyncFunctionDef) and n.name == view.__name__
        )
        node.decorator_list = []
        namespace = dict(user_routes.__dict__)
        exec(
            compile(
                ast.Module(body=[node], type_ignores=[]), "baseline_routes", "exec"
            ),
            namespace,
        )
        view = namespace[view.__name__]
    html = await view(
        request=None,
        session=session,
        current_empleado=actor,
        year=2026,
        month=9,
        msg=None,
        error_msg=None,
    )
    return html, catalog_card


@pytest.mark.asyncio
async def test_missing_identity_visible_but_not_offered_for_another_card(monkeypatch):
    expense = item()
    html, selected_card = await render(monkeypatch, candidates=[expense])
    assert f'<option value="{expense.id}">' not in html
    assert "Partidas AMEX sin tarjeta" in html
    assert f'name="expense_ids" value="{expense.id}"' in html
    assert "Hay partidas AMEX sin identidad de tarjeta" in html
    section = html.split('<section id="sin-tarjeta"')[1].split("</section>")[0]
    assert 'value="">-- Selecciona tarjeta AMEX --' in section
    assert f'value="{selected_card.id}"' in section
    assert "selected" not in section
    assert 'name="reason" required' in section
    assert 'name="confirmed" value="true" required' in section
    assert 'name="year" value="2026"' in section
    assert 'name="month" value="9"' in section
    assert "I-001" in section and "aprobado" in section
    assert f"/informes-de-gastos/{expense.cuenta_gastos_id}" in section
    assert "/admin/contabilidad/amex" in section
    assert "Tarjeta &lt;empresa&gt;" in section


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "candidates, expected",
    [
        ([], "No hay partidas AMEX registradas en informes de este período"),
        (
            [item(last4="9999")],
            "No hay partidas de esta tarjeta en los informes del período",
        ),
    ],
)
async def test_empty_selector_explains_actual_missing_context(
    monkeypatch, candidates, expected
):
    html, _ = await render(monkeypatch, candidates=candidates)
    assert expected in html


@pytest.mark.asyncio
async def test_historical_missing_identity_keeps_explicit_review_warning(monkeypatch):
    html, _ = await render(
        monkeypatch,
        candidates=[item()],
        historical="historical_amex_representation_requires_review",
    )
    assert "Requiere revisión histórica AMEX" in html
    assert "ni sustituye la revisión histórica AMEX" in html


@pytest.mark.asyncio
async def test_correct_identity_becomes_option_but_other_card_does_not(monkeypatch):
    matching, other = item(last4="1005"), item(last4="9999")
    html, _ = await render(monkeypatch, candidates=[matching, other])
    assert f'<option value="{matching.id}">' in html
    assert f'<option value="{other.id}">' not in html


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status, commit", [("assigned", True), ("unchanged", True), ("pending", False)]
)
async def test_identification_transaction_and_period_redirect(
    monkeypatch, status, commit
):
    service = AsyncMock(
        return_value=SimpleNamespace(
            status=status,
            reason="invalid_amex_report_expense",
            updated_count=1,
            unchanged_count=0,
        )
    )
    monkeypatch.setattr(user_routes, "assign_missing_amex_card", service)
    session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    actor, expense_id, card_id = SimpleNamespace(id=uuid4()), uuid4(), uuid4()
    response = await user_routes.amex_conciliacion_identify_card(
        session=session,
        current_empleado=actor,
        expense_ids=[str(expense_id)],
        amex_card_account_id=str(card_id),
        reason="Verificado con titular",
        confirmed=True,
        year=2026,
        month=9,
    )
    parts = urlsplit(response.headers["location"])
    assert parts.fragment == "sin-tarjeta"
    assert parse_qs(parts.query)["year"] == ["2026"]
    assert parse_qs(parts.query)["month"] == ["9"]
    assert bool(session.commit.await_count) is commit
    assert bool(session.rollback.await_count) is not commit
    assert service.await_args.kwargs["expense_ids"] == [expense_id]
    assert service.await_args.kwargs["actor_id"] == actor.id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ids, selected_card, reason, confirmed",
    [
        ([], str(uuid4()), "motivo", True),
        (["bad"], str(uuid4()), "motivo", True),
        ([str(uuid4())], "bad", "motivo", True),
        ([str(uuid4())], str(uuid4()), "", True),
        ([str(uuid4())], str(uuid4()), "motivo", False),
    ],
)
async def test_incomplete_identity_never_calls_service(
    monkeypatch, ids, selected_card, reason, confirmed
):
    service = AsyncMock()
    monkeypatch.setattr(user_routes, "assign_missing_amex_card", service)
    session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    response = await user_routes.amex_conciliacion_identify_card(
        session=session,
        current_empleado=SimpleNamespace(id=uuid4()),
        expense_ids=ids,
        amex_card_account_id=selected_card,
        reason=reason,
        confirmed=confirmed,
        year=2026,
        month=9,
    )
    assert "error_msg" in response.headers["location"]
    service.assert_not_awaited()
    session.commit.assert_not_awaited()
    session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_catalog_selection_validates_canonical_account(monkeypatch):
    validate = AsyncMock(return_value=(None, "invalid_amex_liability"))
    monkeypatch.setattr(user_routes, "validate_amex_card_identity", validate)
    with pytest.raises(ValueError, match="Selecciona"):
        await user_routes._require_amex_form_card(None, "")
    validate.assert_not_awaited()
    with pytest.raises(ValueError, match="cuenta pasivo"):
        await user_routes._require_amex_form_card(None, str(uuid4()))
    selected = card()
    validate.return_value = selected, None
    assert await user_routes._require_amex_form_card(None, str(selected.id)) is selected


def test_catalog_skips_inactive_cards_and_does_not_infer_selection():
    inactive, active = card(active=False), card()
    html = user_routes._amex_identity_card_options([inactive, active])
    assert str(inactive.id) not in html
    assert str(active.id) in html
    assert "selected" not in html


@pytest.mark.asyncio
async def test_bulk_authority_and_catalog_reason_are_required(monkeypatch):
    with pytest.raises(HTTPException) as error:
        await user_routes.actualizar_gastos_amex_en_informe(
            cuenta_id=uuid4(),
            session=None,
            current_empleado=SimpleNamespace(rol="empleado"),
            expense_ids=[],
            amex_action="mark",
            amex_card_account_id=None,
            amex_reason=None,
        )
    assert error.value.status_code == 403
    cuenta = SimpleNamespace(comprobacion_parcial=False)
    session = SimpleNamespace(
        execute=AsyncMock(return_value=Result([cuenta])),
        rollback=AsyncMock(),
        commit=AsyncMock(),
    )
    monkeypatch.setattr(user_routes, "_cuenta_allows_company_amex", lambda *_: True)
    monkeypatch.setattr(
        user_routes, "_require_amex_form_card", AsyncMock(return_value=card())
    )
    service = AsyncMock(return_value=[])
    monkeypatch.setattr(user_routes, "set_company_amex_status", service)
    response = await user_routes.actualizar_gastos_amex_en_informe(
        cuenta_id=uuid4(),
        session=session,
        current_empleado=SimpleNamespace(rol="finanzas"),
        expense_ids=[str(uuid4())],
        amex_action="mark",
        amex_card_account_id=str(uuid4()),
        amex_reason="",
    )
    assert "error_msg" in response.headers["location"]
    service.assert_not_awaited()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "current_last4, requested_amex, requested_last4, fails",
    [
        (None, True, None, True),
        ("9999", True, "9999", True),
        ("1005", True, "9999", False),
        ("1005", False, "9999", True),
        ("1005", False, "1005", False),
    ],
)
async def test_edit_existing_amex_identity_requires_audited_recovery(
    monkeypatch,
    current_last4,
    requested_amex,
    requested_last4,
    fails,
):
    selected = card()
    monkeypatch.setattr(
        user_routes, "_require_amex_form_card", AsyncMock(return_value=selected)
    )
    monkeypatch.setattr(user_routes, "is_company_amex_expense", lambda *_: True)
    kwargs = dict(
        requested_amex=requested_amex,
        card_account_id=str(selected.id),
        requested_last4=requested_last4,
    )
    expense = item(last4=current_last4)
    if fails:
        with pytest.raises(ValueError, match="identidad"):
            await user_routes._validated_amex_edit_identity(None, expense, **kwargs)
    else:
        value = await user_routes._validated_amex_edit_identity(None, expense, **kwargs)
        assert value == "1005"
    assert expense.ultimos_4_digitos == current_last4


@pytest.mark.asyncio
async def test_edit_new_amex_uses_selected_catalog_identity(monkeypatch):
    selected = card()
    monkeypatch.setattr(
        user_routes, "_require_amex_form_card", AsyncMock(return_value=selected)
    )
    monkeypatch.setattr(user_routes, "is_company_amex_expense", lambda *_: False)
    monkeypatch.setattr(
        user_routes,
        "validate_amex_card_edit_transition",
        AsyncMock(return_value=(selected, None)),
    )
    assert (
        await user_routes._validated_amex_edit_identity(
            None,
            item(last4="9999"),
            requested_amex=True,
            card_account_id=str(selected.id),
            requested_last4="9999",
        )
        == selected.last4
    )


def test_identification_http_route_denies_non_finance_actor(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from devnous.gastos.routes import dependencies

    app = FastAPI()
    app.add_api_route(
        "/identify", user_routes.amex_conciliacion_identify_card, methods=["POST"]
    )
    app.dependency_overrides[dependencies.get_current_empleado] = (
        lambda: SimpleNamespace(
            id=uuid4(),
            rol="empleado",
            permisos=[],
        )
    )
    app.dependency_overrides[user_routes.get_db_session] = lambda: None
    service = AsyncMock()
    monkeypatch.setattr(user_routes, "assign_missing_amex_card", service)
    response = TestClient(app).post(
        "/identify",
        data={
            "expense_ids": str(uuid4()),
            "amex_card_account_id": str(uuid4()),
            "reason": "Verificado",
            "confirmed": "true",
            "year": "2026",
            "month": "9",
        },
    )
    assert response.status_code == 403
    service.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "card_id, supplement",
    [(None, False), (None, True), (str(uuid4()), False), (str(uuid4()), True)],
)
async def test_quick_amex_identity_is_explicit_and_preserved_for_supplement(
    monkeypatch, card_id, supplement
):
    import inspect
    from unittest.mock import Mock

    actor = SimpleNamespace(id=uuid4(), nombre="Finanzas", departamento="Operaciones")
    cuenta = SimpleNamespace(
        id=uuid4(),
        empleado_id=actor.id,
        empleado=actor,
        torneo=None,
        referencia_base="001",
        fase="Nacional",
        categorias=[],
        edicion=2026,
        currency="MXN",
        beneficiario_proveedor_cliente=SimpleNamespace(),
    )
    informe = SimpleNamespace(id=uuid4())
    session = SimpleNamespace(
        execute=AsyncMock(side_effect=[Result([cuenta]), Result([informe])]),
        commit=AsyncMock(),
        rollback=AsyncMock(),
        add=Mock(),
        flush=AsyncMock(),
    )
    selected = card()
    created = []

    async def create(**kwargs):
        expense = SimpleNamespace(id=uuid4(), gasto_cantidad=kwargs["gasto_cantidad"])
        created.append(expense)
        return expense

    monkeypatch.setattr(user_routes, "_can_quick_capture_expense", lambda *_: True)
    monkeypatch.setattr(user_routes, "_cuenta_allows_company_amex", lambda *_: True)
    monkeypatch.setattr(user_routes, "is_company_amex_account", lambda *_: True)
    monkeypatch.setattr(user_routes, "_ensure_expense_tip_schema", AsyncMock())
    monkeypatch.setattr(user_routes, "resolve_cfdi_upload", lambda **_: (None, None))
    monkeypatch.setattr(user_routes, "create_expense_from_data", create)
    monkeypatch.setattr(user_routes, "apply_budget_concept_cuenta_mapping", AsyncMock())
    monkeypatch.setattr(
        user_routes, "link_expense_to_cfdi_if_manual_uuid_set", AsyncMock()
    )
    monkeypatch.setattr(user_routes, "create_adjunto_record", AsyncMock())
    monkeypatch.setattr(user_routes, "is_pdf_content", lambda *_: True)
    monkeypatch.setattr(
        user_routes,
        "validate_amex_card_identity",
        AsyncMock(return_value=(selected, None)),
    )
    handler = user_routes.crear_gasto_rapido_en_informe
    kwargs = {
        name: getattr(param.default, "default", param.default)
        for name, param in inspect.signature(handler).parameters.items()
        if param.default is not inspect.Parameter.empty
    }
    kwargs.update(
        cuenta_id=cuenta.id,
        session=session,
        current_empleado=actor,
        concepto="Manual",
        fecha="2026-09-15",
        numero_factura="vale azul",
        subtotal="100",
        pagado_con_amex_empresa=None if supplement else "1",
        amex_card_account_id=card_id,
    )
    if supplement:
        kwargs.update(
            asiento_preferencial_cfdi_pdf=SimpleNamespace(
                filename="factura.pdf", read=AsyncMock(return_value=b"%PDF prueba")
            ),
            asiento_preferencial_subtotal="50",
        )
    response = await handler(**kwargs)
    assert response.status_code == 303
    if card_id is None:
        assert "error_msg" in response.headers["location"]
        assert not created
        session.rollback.assert_awaited_once()
        session.commit.assert_not_awaited()
    else:
        assert "gasto_rapido_creado" in response.headers["location"]
        assert len(created) == (2 if supplement else 1)
        identified = created[-1]
        assert identified.pagado_con_amex_empresa is True
        assert identified.ultimos_4_digitos == selected.last4
        if supplement:
            assert created[0].pagado_con_amex_empresa is False
        session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_ambiguous_informe_does_not_display_inferred_latest_reference(
    monkeypatch,
):
    expense = item()
    html, _ = await render(
        monkeypatch,
        candidates=[expense],
        informe_links=[
            (
                expense.id,
                SimpleNamespace(numero_referencia="I-AMBIGUO-1", estado="aprobado"),
            ),
            (
                expense.id,
                SimpleNamespace(numero_referencia="I-AMBIGUO-2", estado="borrador"),
            ),
        ],
    )
    assert "Informe faltante o ambiguo" in html
    assert "I-AMBIGUO-1" not in html
    assert "I-AMBIGUO-2" not in html


@pytest.mark.asyncio
async def test_duplicate_selection_never_calls_identity_service(monkeypatch):
    service = AsyncMock()
    monkeypatch.setattr(user_routes, "assign_missing_amex_card", service)
    session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    expense_id = str(uuid4())
    response = await user_routes.amex_conciliacion_identify_card(
        session=session,
        current_empleado=SimpleNamespace(id=uuid4()),
        expense_ids=[expense_id, expense_id],
        amex_card_account_id=str(uuid4()),
        reason="Verificado",
        confirmed=True,
        year=2026,
        month=9,
    )
    assert "error_msg" in response.headers["location"]
    service.assert_not_awaited()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["mark", "unmark"])
async def test_bulk_passes_explicit_card_only_when_marking(monkeypatch, action):
    selected = card()
    session = SimpleNamespace(
        execute=AsyncMock(
            return_value=Result([SimpleNamespace(comprobacion_parcial=False)])
        ),
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )
    monkeypatch.setattr(user_routes, "_cuenta_allows_company_amex", lambda *_: True)
    validate = AsyncMock(return_value=selected)
    monkeypatch.setattr(user_routes, "_require_amex_form_card", validate)
    service = AsyncMock(return_value=[item()])
    monkeypatch.setattr(user_routes, "set_company_amex_status", service)
    await user_routes.actualizar_gastos_amex_en_informe(
        cuenta_id=uuid4(),
        session=session,
        current_empleado=SimpleNamespace(rol="finanzas"),
        expense_ids=[str(uuid4())],
        amex_action=action,
        amex_card_account_id=str(selected.id),
        amex_reason="Verificado por titular",
    )
    assert service.await_args.kwargs["card_account_id"] == (
        selected.id if action == "mark" else None
    )
    assert service.await_args.kwargs["mark_as_amex"] is (action == "mark")
    validate.assert_not_awaited()
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reason",
    [
        "amex_informe_already_cut",
        "amex_representation_already_bound",
        "invalid_amex_report_expense",
    ],
)
async def test_edit_new_amex_does_not_bypass_source_accounting_guards(
    monkeypatch, reason
):
    monkeypatch.setattr(user_routes, "is_company_amex_expense", lambda *_: False)
    validate = AsyncMock(return_value=(None, reason))
    monkeypatch.setattr(user_routes, "validate_amex_card_edit_transition", validate)
    with pytest.raises(ValueError, match="estado contable"):
        await user_routes._validated_amex_edit_identity(
            None,
            item(),
            requested_amex=True,
            card_account_id=str(uuid4()),
            requested_last4=None,
        )
    validate.assert_awaited_once()


@pytest.mark.parametrize("target", ["different", "unlink", "same"])
def test_actual_edit_post_keeps_validated_amex_source_context(monkeypatch, target):
    from unittest.mock import Mock

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from devnous.gastos.models import ExpenseReport
    from devnous.gastos.routes import dependencies

    actor = SimpleNamespace(id=uuid4(), rol="finanzas", nombre="Finanzas")
    account_id = uuid4()
    expense = ExpenseReport(
        id=uuid4(),
        empleado_id=actor.id,
        cuenta_gastos_id=account_id,
        informe_documento_id=uuid4(),
        estado_gasto="activo",
        concepto="Alimentos",
        gasto_cantidad=116,
        pagado_con_amex_empresa=False,
        ultimos_4_digitos=None,
    )
    account = SimpleNamespace(id=account_id, empleado_id=actor.id)
    session = SimpleNamespace(
        execute=AsyncMock(side_effect=[Result([expense]), Result([account])]),
        rollback=AsyncMock(),
        commit=AsyncMock(),
        add=Mock(),
    )
    monkeypatch.setattr(user_routes, "_ensure_expense_tip_schema", AsyncMock())
    monkeypatch.setattr(user_routes, "_ensure_can_mutate_informe_expense", AsyncMock())
    monkeypatch.setattr(user_routes, "_cuenta_allows_company_amex", lambda *_: True)
    validate = AsyncMock(return_value="1005")
    monkeypatch.setattr(user_routes, "_validated_amex_edit_identity", validate)
    app = FastAPI()
    app.add_api_route(
        "/gastos/{gasto_id}/editar", user_routes.editar_gasto, methods=["POST"]
    )
    app.dependency_overrides[dependencies.get_current_empleado] = lambda: actor
    app.dependency_overrides[user_routes.get_db_session] = lambda: session
    response = TestClient(app).post(
        f"/gastos/{expense.id}/editar",
        data={
            "concepto": "Alimentos actualizados",
            "pagado_con_amex_empresa": "1",
            "amex_card_account_id": str(uuid4()),
            "cuenta_gastos_id": (
                str(account_id)
                if target == "same"
                else str(uuid4()) if target == "different" else ""
            ),
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    if target == "same":
        validate.assert_awaited_once()
        session.commit.assert_awaited_once()
        assert expense.cuenta_gastos_id == account_id
        assert expense.pagado_con_amex_empresa is True
        assert expense.ultimos_4_digitos == "1005"
    else:
        assert "error_msg" in response.headers["location"]
        validate.assert_not_awaited()
        session.rollback.assert_awaited_once()
        session.commit.assert_not_awaited()
        session.add.assert_not_called()
        assert expense.cuenta_gastos_id == account_id
        assert expense.concepto == "Alimentos"
        assert expense.ultimos_4_digitos is None
        assert expense.pagado_con_amex_empresa is False
