from types import SimpleNamespace

from devnous.gastos.services.expense_accounting_service import (
    summarize_cfdi_tax_components,
)


def test_missing_tax_detail_derivation_includes_cfdi_discount():
    cfdi = SimpleNamespace(
        impuestos_detalle={"traslados": [], "retenciones": []},
        total_impuestos_trasladados=0.0,
        subtotal=1000.0,
        descuento=100.0,
        total=1044.0,
    )

    summary = summarize_cfdi_tax_components(cfdi, fallback_iva=None)

    assert summary["iva_trasladado"] == 144.0
