from types import SimpleNamespace
from uuid import uuid4
from datetime import datetime

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
    assert 'name="coi_estado"' in html
    assert 'value="reversar" selected' in html
    assert f'/admin/contabilidad/coi/gastos/{expense_id}/estado' in html


def test_coi_export_endpoint_supports_selected_batch_mode():
    from pathlib import Path

    source = Path("src/devnous/gastos/routes/user_routes.py").read_text()

    assert "selection_mode" in source
    assert "selected_gasto_id" in source
    assert "Exportar seleccionados" in source
    assert 'coi_estado = "contabilizado"' in source
