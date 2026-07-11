import pytest

from samchat.assistant.finance_query_intent import (
    detect_finance_comparison_intent,
)
from samchat.assistant.finance_query_service import (
    render_finance_comparison_result,
    run_read_only_comparison,
)


async def _sample_rows(_intent):
    return [
        {"year": 2025, "concepto": "Hospedaje", "amount": 1000},
        {"year": 2026, "concepto": "Hospedaje", "amount": 1500},
        {"year": 2025, "concepto": "Transporte", "amount": 800},
        {"year": 2026, "concepto": "Transporte", "amount": 600},
    ]


async def _empty_rows(_intent):
    return []


async def _unavailable_rows(_intent):
    raise RuntimeError("source unavailable")


@pytest.mark.asyncio
async def test_successful_read_only_comparison_is_exportable():
    intent = detect_finance_comparison_intent(
        "Compara gasto 2026 vs 2025 por concepto"
    )

    result = await run_read_only_comparison(
        intent=intent,
        rows_provider=_sample_rows,
    )

    assert result.status == "success"
    assert result.exportable is True
    assert result.source == "mocked_read_only_provider"
    assert result.rows[0]["label"] == "Hospedaje"
    assert result.rows[0]["amount_base_year"] == 1000
    assert result.rows[0]["amount_compare_year"] == 1500
    assert result.rows[0]["difference"] == 500
    assert result.rows[0]["variation_pct"] == 50

    rendered = render_finance_comparison_result(result)
    assert "Comparación de gasto por concepto, 2026 vs 2025" in rendered
    assert "| Concepto | 2025 | 2026 | Dif. | Var. % |" in rendered
    assert (
        "| Hospedaje | $1,000.00 | $1,500.00 | $500.00 | 50.00% |"
        in rendered
    )


@pytest.mark.asyncio
async def test_empty_result_says_no_data_and_is_not_exportable():
    intent = detect_finance_comparison_intent(
        "Compara gasto 2026 vs 2025 por concepto"
    )

    result = await run_read_only_comparison(
        intent=intent,
        rows_provider=_empty_rows,
    )
    rendered = render_finance_comparison_result(result)

    assert result.status == "empty"
    assert result.exportable is False
    assert result.rows == []
    assert "No encontré datos suficientes" in rendered
    assert "¿Quieres que te lo exporte" not in rendered


@pytest.mark.asyncio
async def test_unavailable_source_is_clear_and_not_exportable():
    intent = detect_finance_comparison_intent(
        "Compara gasto 2026 vs 2025 por concepto"
    )

    result = await run_read_only_comparison(
        intent=intent,
        rows_provider=_unavailable_rows,
    )
    rendered = render_finance_comparison_result(result)

    assert result.status == "unavailable"
    assert result.exportable is False
    assert result.rows == []
    assert "fuente de datos financiera disponible" in rendered
    assert "¿Quieres que te lo exporte" not in rendered
