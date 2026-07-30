"""Regression checks for punctuation-insensitive account search widgets."""

from pathlib import Path


def test_budget_matrix_account_search_normalizes_punctuation_and_accents() -> None:
    source = Path("src/devnous/gastos/routes/admin_budget_ui.py").read_text()

    assert "function normalizeCuentaSearchText" in source
    assert '.normalize("NFD")' in source
    assert '.replace(/[^a-z0-9]+/g, " ")' in source
    assert "searchableText.includes(query)" in source
    assert '(c.codigo || "").toLowerCase().includes(query)' not in source


def test_account_cleanup_search_normalizes_punctuation_and_accents() -> None:
    source = Path("src/devnous/gastos/routes/admin_routes.py").read_text()

    assert "function normalizeCuentaSearchText" in source
    assert ".normalize('NFD')" in source
    assert ".replace(/[^a-z0-9]+/g, ' ')" in source
    assert "searchableText.includes(query)" in source
    assert "c.codigo.toLowerCase().includes(query)" not in source
