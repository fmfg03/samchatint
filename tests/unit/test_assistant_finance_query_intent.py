from samchat.assistant.finance_query_intent import detect_finance_comparison_intent


def test_detects_expense_year_over_year_by_concept() -> None:
    intent = detect_finance_comparison_intent(
        "Compara gasto 2026 vs 2025 por concepto"
    )

    assert intent is not None
    assert intent.metric == "gasto"
    assert intent.years == [2026, 2025]
    assert intent.group_by == "concepto"
    assert intent.comparison == "year_over_year"


def test_detects_expense_year_over_year_by_category() -> None:
    intent = detect_finance_comparison_intent(
        "compara gastos 2025 contra 2026 por categoría"
    )

    assert intent is not None
    assert intent.years == [2025, 2026]
    assert intent.group_by == "category"


def test_detects_variation_wording() -> None:
    intent = detect_finance_comparison_intent(
        "variación de gastos por concepto entre 2025 y 2026"
    )

    assert intent is not None
    assert intent.years == [2025, 2026]
    assert intent.group_by == "concepto"


def test_non_finance_message_is_not_detected() -> None:
    assert detect_finance_comparison_intent("Resume este roster sub-17") is None
