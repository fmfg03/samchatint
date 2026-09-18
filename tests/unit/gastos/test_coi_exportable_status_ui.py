from types import SimpleNamespace
from uuid import uuid4
from datetime import datetime

import pytest
from sqlalchemy.dialects import postgresql

from devnous.gastos.routes import user_routes


def test_coi_exportable_rows_include_selection_and_status_controls():
    expense_id = uuid4()
    documento_id = uuid4()
    html = user_routes._render_coi_exportable_lote_rows_html([
        {
            "tipo_lote": "INFORME",
            "documento": SimpleNamespace(
                id=documento_id,
                numero_referencia="I-123456",
                estado="aprobado",
            ),
            "expense": SimpleNamespace(
                id=expense_id,
                numero_referencia="O-26000001",
                concepto="Gasolina",
                gasto_cantidad=123.45,
                fecha=datetime(2026, 8, 20),
                coi_estado="reversar",
            ),
            "period_label": "2026-08-20",
        }
    ])

    assert 'name="selected_gasto_id"' in html
    assert f'value="{expense_id}"' in html
    assert (
        f'class="coi-selection-checkbox" type="checkbox" '
        f'form="coi-export-form" name="selected_gasto_id" value="{expense_id}"'
    ) in html
    assert f'value="{expense_id}" checked' not in html
    assert 'name="coi_estado"' in html
    assert 'value="reversar" selected' in html
    assert f'/admin/contabilidad/coi/gastos/{expense_id}/estado' in html
    assert "Clasificación: No registrado" in html
    assert "Exportación: No registrado" in html


def test_coi_export_page_requires_explicit_visible_selection():
    from pathlib import Path

    source = Path("src/devnous/gastos/routes/user_routes.py").read_text()

    assert '@router.post("/admin/contabilidad/coi/exportar-gastos-lote.xlsx"' in source
    assert 'id="coi-select-all"' in source
    assert 'id="coi-selected-count"' in source
    assert 'id="coi-confirmed-selection-count"' in source
    assert "selected_gasto_id" in source
    assert "Exportar todo" not in source
    assert "Confirma nuevamente el número exacto de pólizas" in source
    assert 'coi_estado = "contabilizado"' in source


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
    visible_expense = SimpleNamespace(id=uuid4())

    async def build_visible_rows(*_args, **_kwargs):
        return [{"expense": visible_expense}]

    monkeypatch.setattr(user_routes, "_build_coi_exportable_lote_rows", build_visible_rows)
    common = {
        "session": SimpleNamespace(),
        "current_empleado": SimpleNamespace(id=uuid4()),
        "year": 2026,
        "month": 9,
        "q": "",
    }

    empty = await user_routes.exportar_coi_gastos_lote_xlsx(
        **common, selected_gasto_id=None, confirmed_selection_count=0
    )
    hidden = await user_routes.exportar_coi_gastos_lote_xlsx(
        **common, selected_gasto_id=[uuid4()], confirmed_selection_count=1
    )

    assert empty.status_code == 303
    assert "Selecciona%20al%20menos%20una" in empty.headers["location"]
    assert hidden.status_code == 303
    assert "selecci%C3%B3n%20ya%20no%20coincide" in hidden.headers["location"]


@pytest.mark.asyncio
async def test_coi_batch_export_commits_only_the_confirmed_visible_selection(monkeypatch):
    visible_expense = SimpleNamespace(id=uuid4())
    hidden_expense = SimpleNamespace(id=uuid4())
    commits = []

    class Session:
        async def commit(self):
            commits.append(True)

    async def build_visible_rows(*_args, **_kwargs):
        return [{"expense": visible_expense}]

    async def collect_selected_rows(_session, rows):
        assert [row["expense"].id for row in rows] == [visible_expense.id]
        return [], [], 1, {visible_expense.id}

    monkeypatch.setattr(user_routes, "_build_coi_exportable_lote_rows", build_visible_rows)
    monkeypatch.setattr(user_routes, "_collect_coi_lote_expense_cfdis", collect_selected_rows)
    monkeypatch.setattr(user_routes, "generate_coi_poliza_xlsx", lambda *_args, **_kwargs: b"xlsx")

    response = await user_routes.exportar_coi_gastos_lote_xlsx(
        session=Session(),
        current_empleado=SimpleNamespace(id=uuid4()),
        year=2026,
        month=9,
        q="",
        selected_gasto_id=[visible_expense.id],
        confirmed_selection_count=1,
    )

    assert hidden_expense.id != visible_expense.id
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
