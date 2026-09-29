from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql

from devnous.gastos.routes import admin_routes, user_routes
from devnous.gastos.services.coi_poliza_exporter import ExpenseCFDI


class _ScalarRows:
    def __init__(self, rows):
        self.rows = rows

    def scalar_one_or_none(self):
        return self.rows[0] if self.rows else None

    def scalars(self):
        return self

    def all(self):
        return self.rows


def test_coi_exportable_rows_include_selection_and_status_controls():
    expense_id = uuid4()
    documento_id = uuid4()
    html = user_routes._render_coi_exportable_lote_rows_html(
        [
            {
                "tipo_lote": "INFORME",
                "documento": SimpleNamespace(
                    id=documento_id,
                    numero_referencia="I-123456",
                    estado="aprobado",
                ),
                "expenses": [
                    SimpleNamespace(
                        id=expense_id,
                        numero_referencia="O-26000001",
                        concepto="Gasolina",
                        gasto_cantidad=123.45,
                        fecha=datetime(2026, 8, 20),
                        coi_estado="reversar",
                    )
                ],
                "period_label": "2026-08-20",
                "can_export": True,
                "block_reason": "",
            }
        ]
    )

    assert 'name="selected_documento_id"' in html
    assert f'value="{documento_id}"' in html
    assert (
        f'class="coi-selection-checkbox" type="checkbox" '
        f'form="coi-export-form" name="selected_documento_id" '
        f'value="{documento_id}"'
    ) in html
    assert f'value="{documento_id}" checked' not in html
    assert 'name="coi_estado"' in html
    assert 'value="reversar" selected' in html
    assert f"/admin/contabilidad/coi/gastos/{expense_id}/estado" in html
    assert f"/documentos/{documento_id}/exportar-coi.xlsx" in html
    assert "Clasificación: No registrado" in html
    assert "Exportación: No registrado" in html


def test_coi_blocked_report_has_no_selectable_partial_policy():
    documento_id = uuid4()
    html = user_routes._render_coi_exportable_lote_rows_html(
        [
            {
                "tipo_lote": "INFORME",
                "documento": SimpleNamespace(
                    id=documento_id,
                    numero_referencia="I-123456",
                    estado="aprobado",
                ),
                "expenses": [],
                "period_label": "2026-08-20",
                "can_export": False,
                "block_reason": "O-1: falta cuenta contable",
            }
        ]
    )

    assert 'name="selected_documento_id"' not in html
    assert "Bloqueada: O-1: falta cuenta contable" in html


def test_coi_export_page_requires_explicit_visible_selection():
    from pathlib import Path

    source = Path("src/devnous/gastos/routes/user_routes.py").read_text()

    assert '@router.post("/admin/contabilidad/coi/exportar-gastos-lote.xlsx"' in source
    assert 'id="coi-select-all"' in source
    assert 'id="coi-selected-count"' in source
    assert 'id="coi-confirmed-selection-count"' in source
    assert "selected_documento_id" in source
    assert "Exportar todo" not in source
    assert "Confirma nuevamente el número exacto de pólizas" in source
    assert 'coi_estado = "contabilizado"' in source


def test_coi_view_separates_preparation_export_and_history_tasks():
    from pathlib import Path

    source = Path("src/devnous/gastos/routes/user_routes.py").read_text()
    start = source.index("async def contabilidad_coi_view")
    end = source.index(
        '@router.post("/admin/contabilidad/coi/gastos/{expense_id}/estado")',
        start,
    )
    view_source = source[start:end]

    assert '<nav class="task-journey" aria-label="Flujo de trabajo COI">' in view_source
    assert (
        'href="/admin/gastos/sin-cuenta-contable?period={selected_year}-{selected_month:02d}"'
        in view_source
    )
    assert "1. Preparar COI" in view_source
    assert "2. Revisar y exportar" in view_source
    assert "3. Consultar historial" in view_source
    assert 'id="coi-exportacion"' in view_source
    assert (
        '<section id="coi-historial" aria-labelledby="coi-historial-heading">'
        in view_source
    )
    assert "2. Revisar y exportar gastos listos" in view_source
    assert "3. Historial e imports" in view_source


def test_coi_lote_filters_expenses_by_accounting_date_not_document_date():
    from pathlib import Path

    source = Path("src/devnous/gastos/routes/user_routes.py").read_text()
    start = source.index("async def _load_coi_lote_informe_documentos")
    end = source.index("def _coi_lote_documento_period_label", start)
    filter_source = source[start:end]

    assert "ExpenseReport.fecha >= start_dt" in filter_source
    assert "ExpenseReport.fecha < end_dt" in filter_source
    assert "Documento.aprobado_en >= start_dt" not in filter_source
    assert "Documento.pagado_en >= start_dt" not in filter_source


@pytest.mark.asyncio
async def test_coi_batch_export_rejects_empty_or_hidden_selection(monkeypatch):
    visible_document = SimpleNamespace(id=uuid4())

    async def build_visible_rows(*_args, **_kwargs):
        return [{"documento": visible_document, "can_export": True}]

    monkeypatch.setattr(
        user_routes, "_build_coi_exportable_lote_rows", build_visible_rows
    )
    common = {
        "session": SimpleNamespace(),
        "current_empleado": SimpleNamespace(id=uuid4()),
        "year": 2026,
        "month": 9,
        "q": "",
    }

    empty = await user_routes.exportar_coi_gastos_lote_xlsx(
        **common, selected_documento_id=None, confirmed_selection_count=0
    )
    hidden = await user_routes.exportar_coi_gastos_lote_xlsx(
        **common, selected_documento_id=[uuid4()], confirmed_selection_count=1
    )

    assert empty.status_code == 303
    assert "Selecciona%20al%20menos%20una" in empty.headers["location"]
    assert hidden.status_code == 303
    assert "selecci%C3%B3n%20ya%20no%20coincide" in hidden.headers["location"]


@pytest.mark.asyncio
async def test_coi_batch_export_commits_only_the_confirmed_visible_selection(
    monkeypatch,
):
    visible_expense = SimpleNamespace(id=uuid4())
    visible_document = SimpleNamespace(id=uuid4())
    hidden_document = SimpleNamespace(id=uuid4())
    commits = []

    class Session:
        async def commit(self):
            commits.append(True)

    async def build_visible_rows(*_args, **_kwargs):
        return [
            {
                "documento": visible_document,
                "expenses": [visible_expense],
                "can_export": True,
            }
        ]

    async def collect_selected_rows(_session, rows):
        assert [row["documento"].id for row in rows] == [visible_document.id]
        return [], [], 1, {visible_expense.id}

    monkeypatch.setattr(
        user_routes, "_build_coi_exportable_lote_rows", build_visible_rows
    )
    monkeypatch.setattr(
        user_routes, "_collect_coi_lote_expense_cfdis", collect_selected_rows
    )
    monkeypatch.setattr(
        user_routes,
        "generate_coi_poliza_xlsx",
        lambda *_args, **_kwargs: b"xlsx",
    )

    response = await user_routes.exportar_coi_gastos_lote_xlsx(
        session=Session(),
        current_empleado=SimpleNamespace(id=uuid4()),
        year=2026,
        month=9,
        q="",
        selected_documento_id=[visible_document.id],
        confirmed_selection_count=1,
    )

    assert hidden_document.id != visible_document.id
    assert response.status_code == 200
    assert commits == [True]


@pytest.mark.asyncio
async def test_informe_lote_query_is_correlated_to_expense_accounting_date():
    class EmptyResult:
        def scalars(self):
            return self

        def all(self):
            return []

    class CaptureSession:
        statement = None

        async def execute(self, statement):
            self.statement = statement
            return EmptyResult()

    session = CaptureSession()
    await user_routes._load_coi_lote_informe_documentos(
        session, datetime(2026, 9, 1), datetime(2026, 10, 1)
    )
    compiled = str(
        session.statement.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )

    assert "EXISTS" in compiled
    assert "expense_reports.fecha >= '2026-09-01 00:00:00'" in compiled
    assert "expense_reports.fecha < '2026-10-01 00:00:00'" in compiled


@pytest.mark.asyncio
async def test_document_bundle_blocks_entire_informe_when_one_expense_is_not_ready(
    monkeypatch,
):
    employee_id = uuid4()
    document = SimpleNamespace(
        id=uuid4(),
        empleado_id=employee_id,
        tipo="INFORME",
        estado="aprobado",
        cuenta_gastos_id=None,
        numero_referencia="I-26000001",
    )
    expense = SimpleNamespace(
        id=uuid4(),
        numero_referencia="G-26000001",
        estado_gasto="activo",
        fecha=datetime(2026, 9, 10),
    )

    class Session:
        calls = 0

        async def execute(self, _statement):
            self.calls += 1
            return _ScalarRows([document] if self.calls == 1 else [expense])

    async def not_ready(_session, _expense):
        return False, ["Falta cuenta contable"]

    monkeypatch.setattr(user_routes, "assess_expense_coi_cleanup_ready", not_ready)

    with pytest.raises(HTTPException) as exc_info:
        await user_routes._build_documento_coi_bundle(
            document.id,
            Session(),
            SimpleNamespace(id=employee_id, rol="usuario"),
            require_complete_informe=True,
        )

    assert exc_info.value.status_code == 400
    assert "No se generó una póliza parcial" in exc_info.value.detail
    assert "G-26000001: Falta cuenta contable" in exc_info.value.detail


@pytest.mark.asyncio
async def test_document_bundle_blocks_informe_spanning_accounting_months():
    employee_id = uuid4()
    document = SimpleNamespace(
        id=uuid4(),
        empleado_id=employee_id,
        tipo="INFORME",
        estado="aprobado",
        cuenta_gastos_id=None,
        numero_referencia="I-26000001",
    )
    expenses = [
        SimpleNamespace(
            id=uuid4(),
            numero_referencia=reference,
            fecha=expense_date,
        )
        for reference, expense_date in (
            ("G-AUG", datetime(2026, 8, 31)),
            ("G-SEP", datetime(2026, 9, 1)),
        )
    ]

    class Session:
        calls = 0

        async def execute(self, _statement):
            self.calls += 1
            return _ScalarRows([document] if self.calls == 1 else expenses)

    with pytest.raises(HTTPException) as exc_info:
        await user_routes._build_documento_coi_bundle(
            document.id,
            Session(),
            SimpleNamespace(id=employee_id, rol="usuario"),
            require_complete_informe=True,
        )

    assert exc_info.value.status_code == 400
    assert "2026-08, 2026-09" in exc_info.value.detail


@pytest.mark.asyncio
async def test_single_expense_export_redirects_to_owning_informe(monkeypatch):
    employee_id = uuid4()
    informe_id = uuid4()
    expense = SimpleNamespace(
        id=uuid4(),
        empleado_id=employee_id,
        informe_documento_id=informe_id,
        documento_id=None,
        cuenta_gastos_id=None,
    )
    informe = SimpleNamespace(id=informe_id, tipo="INFORME")

    async def load_expense(_session, _expense_id):
        return expense

    class Session:
        async def get(self, _model, entity_id):
            assert entity_id == informe_id
            return informe

    monkeypatch.setattr(user_routes, "load_expense_for_coi_export", load_expense)

    response = await user_routes.exportar_coi_poliza_gasto_excel(
        expense.id,
        Session(),
        SimpleNamespace(id=employee_id, rol="usuario"),
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"/documentos/{informe_id}/exportar-coi.xlsx"


@pytest.mark.asyncio
async def test_monthly_batch_blocks_informe_with_expenses_in_another_period(
    monkeypatch,
):
    document = SimpleNamespace(
        id=uuid4(),
        tipo="INFORME",
        estado="aprobado",
        numero_referencia="I-26000001",
        aprobado_en=datetime(2026, 9, 20),
        creado_en=datetime(2026, 9, 1),
    )
    september = SimpleNamespace(
        id=uuid4(),
        numero_referencia="G-SEP",
        concepto="Gasolina",
        proyecto="Proyecto",
        fecha=datetime(2026, 9, 10),
    )
    august = SimpleNamespace(
        id=uuid4(),
        numero_referencia="G-AUG",
        concepto="Casetas",
        proyecto="Proyecto",
        fecha=datetime(2026, 8, 31),
    )

    async def load_informes(*_args, **_kwargs):
        return [document]

    async def load_terceros(*_args, **_kwargs):
        return []

    async def load_expenses(_session, _document, start_dt=None, end_dt=None):
        if start_dt is not None and end_dt is not None:
            return [september]
        return [august, september]

    async def ready(*_args, **_kwargs):
        return True, []

    monkeypatch.setattr(user_routes, "_load_coi_lote_informe_documentos", load_informes)
    monkeypatch.setattr(
        user_routes, "_load_coi_lote_terceros_documentos", load_terceros
    )
    monkeypatch.setattr(
        user_routes, "_load_documento_active_coi_expenses", load_expenses
    )
    monkeypatch.setattr(user_routes, "assess_expense_coi_cleanup_ready", ready)

    rows = await user_routes._build_coi_exportable_lote_rows(
        SimpleNamespace(),
        start_dt=datetime(2026, 9, 1),
        end_dt=datetime(2026, 10, 1),
        start_date=datetime(2026, 9, 1).date(),
        end_date=datetime(2026, 10, 1).date(),
    )

    assert len(rows) == 1
    assert rows[0]["can_export"] is False
    assert "otro periodo contable: G-AUG" in rows[0]["block_reason"]


@pytest.mark.asyncio
async def test_finance_batch_groups_all_report_expenses_into_one_policy(monkeypatch):
    informe = SimpleNamespace(
        id=uuid4(),
        tipo="INFORME",
        estado="aprobado",
        numero_referencia="I-26000001",
        cuenta_gastos_id=None,
    )
    expenses = [
        SimpleNamespace(
            id=uuid4(),
            fecha=datetime(2026, 9, day),
            numero_referencia=f"G-{day}",
            cuenta_gastos_id=None,
            informe_documento=informe,
            documento=None,
        )
        for day in (10, 11)
    ]

    class Session:
        calls = 0

        async def execute(self, _statement):
            self.calls += 1
            return _ScalarRows(expenses)

    async def ready(*_args, **_kwargs):
        return True, []

    async def build(_session, expense, **_kwargs):
        return ExpenseCFDI(
            fecha=expense.fecha,
            total=100,
            iva_amount=0,
            subtotal_amount=100,
            concepto=expense.numero_referencia,
            cuenta_contable="5000-001",
            cuenta_contrapartida="1170-001",
        )

    monkeypatch.setattr(admin_routes, "assess_expense_coi_cleanup_ready", ready)
    monkeypatch.setattr(admin_routes, "build_expense_cfdi_for_export", build)

    year, month, payloads = await admin_routes._build_finance_coi_batch_expenses(
        Session(), year=2026, month=9
    )

    assert (year, month) == (2026, 9)
    assert len(payloads) == 2
    assert {item.poliza_group_key for item in payloads} == {f"informe:{informe.id}"}
    assert {item.poliza_reference for item in payloads} == {"I-26000001"}


@pytest.mark.asyncio
async def test_finance_batch_blocks_unapproved_informe():
    informe = SimpleNamespace(
        id=uuid4(),
        tipo="INFORME",
        estado="enviado",
        numero_referencia="I-26000002",
        cuenta_gastos_id=None,
    )
    expense = SimpleNamespace(
        id=uuid4(),
        fecha=datetime(2026, 9, 10),
        numero_referencia="G-10",
        cuenta_gastos_id=None,
        informe_documento=informe,
        documento=None,
    )

    class Session:
        async def execute(self, _statement):
            return _ScalarRows([expense])

    with pytest.raises(ValueError, match="debe estar aprobado"):
        await admin_routes._build_finance_coi_batch_expenses(
            Session(), year=2026, month=9
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("relation", ["documento", "cuenta"])
@pytest.mark.parametrize("blocker", [None, "not_ready", "outside_period"])
async def test_finance_report_ownership_and_atomic_blockers(
    monkeypatch, relation, blocker
):
    account_id = uuid4() if relation == "cuenta" else None
    informe = SimpleNamespace(
        id=uuid4(),
        tipo="INFORME",
        estado="aprobado",
        numero_referencia="I-ATOMIC",
        cuenta_gastos_id=account_id,
    )
    expense = SimpleNamespace(
        id=uuid4(),
        fecha=datetime(2026, 9, 10),
        numero_referencia="G-READY",
        cuenta_gastos_id=account_id,
        informe_documento=None,
        documento=informe if relation == "documento" else None,
    )
    second = SimpleNamespace(
        **{**vars(expense), "id": uuid4(), "numero_referencia": "G-BLOCKED"}
    )
    if blocker == "outside_period":
        second.fecha = datetime(2026, 8, 31)
    responses = [[expense]]
    if account_id:
        responses.append([informe])
    responses.append([expense, second])

    class Session:
        async def execute(self, _statement):
            return _ScalarRows(responses.pop(0))

    async def ready(_session, item):
        if blocker == "not_ready" and item.id == second.id:
            return False, ["Falta contrapartida"]
        return True, []

    async def build(_session, item, **_kwargs):
        return ExpenseCFDI(
            fecha=item.fecha,
            total=100,
            iva_amount=0,
            subtotal_amount=100,
            concepto=item.numero_referencia,
            cuenta_contable="5000",
            cuenta_contrapartida="1170",
        )

    monkeypatch.setattr(admin_routes, "assess_expense_coi_cleanup_ready", ready)
    monkeypatch.setattr(admin_routes, "build_expense_cfdi_for_export", build)
    if blocker:
        with pytest.raises(ValueError, match="G-BLOCKED"):
            await admin_routes._build_finance_coi_batch_expenses(
                Session(), year=2026, month=9
            )
    else:
        _, _, payloads = await admin_routes._build_finance_coi_batch_expenses(
            Session(), year=2026, month=9
        )
        assert len(payloads) == 2
        assert {p.poliza_group_key for p in payloads} == {f"informe:{informe.id}"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "endpoint",
    [
        admin_routes.admin_finance_coi_batch_consolidated_xlsx,
        admin_routes.admin_finance_coi_batch_xlsx,
    ],
)
async def test_finance_download_returns_blocker_without_generating_file(
    monkeypatch, endpoint
):
    async def blocked(*_args, **_kwargs):
        raise ValueError("Informe incompleto: G-2")

    def forbidden(*_args, **_kwargs):
        pytest.fail("A blocked report must not generate a partial file")

    monkeypatch.setattr(admin_routes, "_build_finance_coi_batch_expenses", blocked)
    monkeypatch.setattr(admin_routes, "generate_coi_poliza_xlsx", forbidden)
    monkeypatch.setattr(admin_routes, "generate_coi_poliza_zip", forbidden)
    response = await endpoint(
        request=SimpleNamespace(),
        session=SimpleNamespace(),
        current_empleado=SimpleNamespace(),
        year=2026,
        month=9,
    )
    assert response.status_code == 303
    assert "Informe%20incompleto%3A%20G-2" in response.headers["location"]


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_second", [False, True])
async def test_selected_report_exports_all_items_or_no_status_updates(
    monkeypatch, fail_second
):
    document = SimpleNamespace(id=uuid4(), tipo="INFORME", numero_referencia="I-ATOMIC")
    expenses = [
        SimpleNamespace(
            id=uuid4(), numero_referencia=f"G-{index}", coi_estado="pendiente"
        )
        for index in (1, 2)
    ]
    commits = []

    class Session:
        async def commit(self):
            commits.append(True)

    async def visible(*_args, **_kwargs):
        return [
            {
                "documento": document,
                "tipo_lote": "INFORME",
                "expenses": expenses,
                "can_export": True,
            }
        ]

    async def build(_session, item, **_kwargs):
        if fail_second and item.id == expenses[1].id:
            raise ValueError("CFDI incompleto")
        return ExpenseCFDI(
            fecha=datetime(2026, 9, 10),
            total=100,
            iva_amount=0,
            subtotal_amount=100,
            concepto=item.numero_referencia,
            cuenta_contable="5000",
            cuenta_contrapartida="1170",
        )

    def generate(payloads, **_kwargs):
        assert len(payloads) == 2
        assert {p.poliza_group_key for p in payloads} == {f"informe:{document.id}"}
        return b"complete-report"

    monkeypatch.setattr(user_routes, "_build_coi_exportable_lote_rows", visible)
    monkeypatch.setattr(user_routes, "build_expense_cfdi_for_export", build)
    monkeypatch.setattr(user_routes, "generate_coi_poliza_xlsx", generate)
    actor = SimpleNamespace(id=uuid4())
    response = await user_routes.exportar_coi_gastos_lote_xlsx(
        session=Session(),
        current_empleado=actor,
        year=2026,
        month=9,
        q="",
        selected_documento_id=[document.id],
        confirmed_selection_count=1,
    )
    if fail_second:
        assert response.status_code == 303
        assert "CFDI%20incompleto" in response.headers["location"]
        assert commits == []
        assert {e.coi_estado for e in expenses} == {"pendiente"}
    else:
        assert response.body == b"complete-report"
        assert commits == [True]
        assert {e.coi_estado for e in expenses} == {"contabilizado"}
        assert {e.coi_exported_by_id for e in expenses} == {actor.id}


@pytest.mark.asyncio
@pytest.mark.parametrize("relation", ["documento", "cuenta"])
async def test_single_expense_resolves_legacy_report_links(monkeypatch, relation):
    actor_id = uuid4()
    informe = SimpleNamespace(id=uuid4(), tipo="INFORME")
    expense = SimpleNamespace(
        id=uuid4(),
        empleado_id=actor_id,
        informe_documento_id=None,
        documento_id=informe.id if relation == "documento" else None,
        cuenta_gastos_id=uuid4() if relation == "cuenta" else None,
    )

    async def load(*_args):
        return expense

    async def load_report(*_args):
        return informe

    class Session:
        async def get(self, _model, _entity_id):
            return informe

    monkeypatch.setattr(user_routes, "load_expense_for_coi_export", load)
    monkeypatch.setattr(user_routes, "_informe_documento_for_cuenta", load_report)
    response = await user_routes.exportar_coi_poliza_gasto_excel(
        expense.id, Session(), SimpleNamespace(id=actor_id, rol="usuario")
    )
    assert response.status_code == 303
    assert response.headers["location"] == f"/documentos/{informe.id}/exportar-coi.xlsx"


@pytest.mark.asyncio
async def test_document_bundle_rejects_late_failure_after_ready_item(monkeypatch):
    actor_id = uuid4()
    document = SimpleNamespace(
        id=uuid4(),
        empleado_id=actor_id,
        tipo="INFORME",
        estado="aprobado",
        cuenta_gastos_id=None,
        numero_referencia="I-ATOMIC",
    )
    expenses = [
        SimpleNamespace(
            id=uuid4(),
            numero_referencia=f"G-{i}",
            fecha=datetime(2026, 9, 10),
            coi_estado="pendiente",
        )
        for i in (1, 2)
    ]
    responses = [[document], expenses]

    class Session:
        async def execute(self, _statement):
            return _ScalarRows(responses.pop(0))

        async def commit(self):
            pytest.fail("An incomplete report must not commit status changes")

    async def ready(*_args):
        return True, []

    async def build(_session, item, **_kwargs):
        if item.id == expenses[1].id:
            raise ValueError("Falta contrapartida")
        return ExpenseCFDI(
            fecha=item.fecha,
            total=100,
            iva_amount=0,
            subtotal_amount=100,
            concepto=item.numero_referencia,
            cuenta_contable="5000",
            cuenta_contrapartida="1170",
        )

    monkeypatch.setattr(user_routes, "assess_expense_coi_cleanup_ready", ready)
    monkeypatch.setattr(user_routes, "build_expense_cfdi_for_export", build)
    with pytest.raises(HTTPException, match="G-2: Falta contrapartida"):
        await user_routes._build_documento_coi_bundle(
            document.id,
            Session(),
            SimpleNamespace(id=actor_id, rol="usuario"),
            require_complete_informe=True,
        )
    assert {e.coi_estado for e in expenses} == {"pendiente"}


@pytest.mark.asyncio
@pytest.mark.parametrize("other_ready", [False, True])
async def test_standalone_policies_remain_independently_selectable(
    monkeypatch, other_ready
):
    document = SimpleNamespace(
        id=uuid4(),
        tipo="SOLICITUD",
        estado="aprobado",
        numero_referencia="S-1",
        pagado_en=None,
        fecha_pago=None,
        aprobado_en=datetime(2026, 9, 10),
        creado_en=datetime(2026, 9, 1),
    )
    expenses = [
        SimpleNamespace(
            id=uuid4(),
            numero_referencia=f"G-{i}",
            concepto="Servicios",
            proyecto="Proyecto",
            fecha=datetime(2026, 9, 10),
            gasto_cantidad=100,
        )
        for i in range(2)
    ]

    async def informes(*args):
        return []

    async def terceros(*args):
        return [document]

    async def load(*args):
        return expenses

    async def ready(session, expense):
        ok = expense is expenses[0] or other_ready
        return ok, [] if ok else ["Falta cuenta"]

    monkeypatch.setattr(user_routes, "_load_coi_lote_informe_documentos", informes)
    monkeypatch.setattr(user_routes, "_load_coi_lote_terceros_documentos", terceros)
    monkeypatch.setattr(user_routes, "_load_documento_active_coi_expenses", load)
    monkeypatch.setattr(user_routes, "assess_expense_coi_cleanup_ready", ready)
    rows = await user_routes._build_coi_exportable_lote_rows(
        SimpleNamespace(),
        start_dt=datetime(2026, 9, 1),
        end_dt=datetime(2026, 10, 1),
        start_date=datetime(2026, 9, 1).date(),
        end_date=datetime(2026, 10, 1).date(),
    )
    assert len(rows) == (2 if other_ready else 1)
    assert rows[0]["expenses"] == [expenses[0]]
    assert all(row["can_export"] for row in rows)
    html = user_routes._render_coi_exportable_lote_rows_html(rows)
    assert f'name="selected_gasto_id" value="{expenses[0].id}"' in html
    assert f"/gastos/{expenses[0].id}/exportar-coi.xlsx" in html
    assert 'name="selected_documento_id"' not in html
    collected = []
    commits = []

    async def visible(*args, **kwargs):
        return rows

    async def collect(session, selected_rows):
        collected.extend(selected_rows)
        return [], [], len(selected_rows), {expenses[0].id}

    class Session:
        async def commit(self):
            commits.append(True)

    monkeypatch.setattr(user_routes, "_build_coi_exportable_lote_rows", visible)
    monkeypatch.setattr(user_routes, "_collect_coi_lote_expense_cfdis", collect)
    monkeypatch.setattr(
        user_routes, "generate_coi_poliza_xlsx", lambda *args, **kwargs: b"xlsx"
    )
    response = await user_routes.exportar_coi_gastos_lote_xlsx(
        session=Session(),
        current_empleado=SimpleNamespace(id=uuid4()),
        year=2026,
        month=9,
        q="",
        selected_documento_id=None,
        selected_gasto_id=[expenses[0].id],
        confirmed_selection_count=1,
    )
    assert response.status_code == 200
    assert collected == [rows[0]]
    assert commits == [True]
    assert expenses[0].coi_estado == "contabilizado"
    assert not hasattr(expenses[1], "coi_estado")
