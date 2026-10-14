"""Rendered cleanup selectors preserve explicit accounts and keep heuristics advisory."""

from datetime import datetime
from html.parser import HTMLParser
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from starlette.requests import Request

from devnous.gastos.models import CuentaContable, ExpenseReport
from devnous.gastos.routes import admin_routes
from devnous.gastos.services.cuenta_contable_suggester import (
    CuentaContableSuggestion,
    CuentaContableSuggester,
)


class Selectors(HTMLParser):
    def __init__(self):
        super().__init__()
        self.current = None
        self.main = None
        self.hidden = None

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if tag == "div" and attrs.get("class") == "cuenta-selector":
            self.current = attrs
        if tag == "input" and attrs.get("data-target") == "main":
            self.main = self.current
        if tag == "input" and attrs.get("class") == "main-cuenta-id":
            self.hidden = attrs


class Result:
    def scalars(self):
        return self

    def all(self):
        return []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tier,partial,assigned,preselect",
    [
        ("torneo", False, False, True),
        ("partida", False, False, True),
        ("torneo", True, False, False),
        ("learned", False, False, False),
        ("rules", False, False, False),
        ("torneo", False, True, False),
        ("learned", False, True, False),
    ],
)
async def test_rendered_main_selector_preselects_only_unambiguous_unassigned_account(
    monkeypatch, tier, partial, assigned, preselect
):
    suggested_id = uuid4()
    manual = CuentaContable(
        id=uuid4(), codigo="SYNTH-MANUAL", nombre="Selección manual", activo=True
    )
    expense = ExpenseReport(
        id=uuid4(),
        numero_referencia="SYNTH-EXPENSE",
        concepto="Apoyo asociación",
        fecha=datetime(2026, 10, 14),
        gasto_cantidad=100,
        proyecto="Torneo sintético",
        cuenta_contable_id=manual.id if assigned else None,
        hospedaje_impuesto_confirmado=False,
    )
    if assigned:
        expense.cuenta_contable = manual
    suggestion = CuentaContableSuggestion(
        suggested_id,
        "SYNTH-A",
        "Cuotas Asociaciones",
        0.95,
        "Synthetic",
        tier,
        requires_explicit_selection=partial,
    )
    monkeypatch.setattr(
        admin_routes, "load_cleanup_expenses", AsyncMock(return_value=[expense])
    )
    monkeypatch.setattr(
        admin_routes, "_expense_operational_references", AsyncMock(return_value={})
    )
    monkeypatch.setattr(
        admin_routes, "list_unassigned_cfdi_options", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        admin_routes,
        "safe_build_cleanup_preview",
        AsyncMock(return_value={"preview": {}, "issues": []}),
    )
    monkeypatch.setattr(admin_routes, "render_admin_navigation", lambda *_a, **_k: "")
    monkeypatch.setattr(
        CuentaContableSuggester,
        "get_suggestions_batch",
        AsyncMock(return_value={expense.id: suggestion}),
    )
    session = SimpleNamespace(
        execute=AsyncMock(return_value=Result()), commit=AsyncMock()
    )
    html = await admin_routes.gastos_sin_cuenta_contable(
        Request({"type": "http", "query_string": b""}),
        session,
        period="2026-10",
        bi_year=None,
        bi_scope=None,
        q="",
        document_type="all",
        issue="all",
        focus_expense_id=None,
        document_id=None,
        current_empleado=SimpleNamespace(),
    )
    selectors = Selectors()
    selectors.feed(html)
    assert ("data-preselect-id" in selectors.main) is preselect
    if preselect:
        assert selectors.main["data-preselect-id"] == str(suggested_id)
    assert selectors.hidden["value"] == (str(manual.id) if assigned else "")
    assert expense.cuenta_contable_id == (manual.id if assigned else None)
    assert "btn-accept-suggestion" in html  # explicit acceptance remains available
    session.commit.assert_not_awaited()


@pytest.mark.parametrize(
    "current,expected_count", [("suggested", 1), ("manual-change", 0), ("", 0)]
)
def test_bulk_collector_skips_manually_changed_charge(current, expected_count):
    import json
    from pathlib import Path
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:
        pytest.skip("Node required to execute the actual bulk collector")
    source = Path("src/devnous/gastos/routes/admin_routes.py").read_text()
    start = source.index("                const highConfidenceRows = [];")
    end = source.index("                if (highConfidenceRows.length === 0)", start)
    collector = source[start:end].replace("{{", "{").replace("}}", "}")
    script = """
const current = JSON.parse(process.argv[1]);
const selector = {
  querySelector: name => name === '.account-search'
    ? { getAttribute: name => name === 'data-target' ? 'main' : 'synthetic-expense' }
    : { value: current },
  getAttribute: () => 'suggested',
};
const document = {
  querySelectorAll: () => [selector],
  querySelector: () => ({ value: 'synthetic-counterpart' }),
};
""" + collector + "console.log(JSON.stringify(highConfidenceRows));"
    result = subprocess.run(
        [node, "-e", script, json.dumps(current)],
        check=True,
        capture_output=True,
        text=True,
    )
    rows = json.loads(result.stdout)
    assert len(rows) == expected_count
    if rows:
        assert rows[0]["cuentaId"] == "suggested"
