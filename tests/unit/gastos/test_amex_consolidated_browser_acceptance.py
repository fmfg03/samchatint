"""Execute the shipped batch script: explicit choices survive exception regrouping."""

import json
import shutil
import subprocess

import pytest

from devnous.gastos.routes.admin_amex_accounting_routes import _batch_decision_script


def run_browser_actions(actions):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for batch browser acceptance")
    script = _batch_decision_script().removeprefix("<script>")
    script = script.removesuffix("</script>")
    harness = """
    const elements = {};
    const rows = ['first', 'second', 'hidden'].map(value => ({
      value, checked: false, label: {textContent: ''},
      closest() { return {querySelector: () => this.label}; }
    }));
    for (const id of ['amex-cut-confirmation', 'amex-decisions',
      'amex-batch-error', 'amex-batch-treatment', 'amex-batch-debtor',
      'amex-batch-reason', 'amex-apply-batch', 'amex-batch-summary']) {
      elements[id] = {value: '', textContent: '',
        addEventListener(event, callback) { this[event] = callback; }};
    }
    elements['amex-decisions'].value = '[]';
    global.document = {
      getElementById: id => elements[id],
      querySelectorAll: () => rows.filter(row => row.checked)
    };
    """
    assertions = """
    const observations = [];
    for (const action of ACTIONS) {
      for (const row of rows) row.checked = action.ids.includes(row.value);
      elements['amex-batch-treatment'].value = action.treatment;
      elements['amex-batch-debtor'].value = action.debtor || '';
      elements['amex-batch-reason'].value = action.reason || '';
      elements['amex-apply-batch'].click();
      observations.push({
        decisions: JSON.parse(elements['amex-decisions'].value),
        error: elements['amex-batch-error'].textContent,
        checked: rows.filter(row => row.checked).map(row => row.value)
      });
    }
    process.stdout.write(JSON.stringify(observations));
    """.replace(
        "ACTIONS", json.dumps(actions)
    )
    result = subprocess.run(
        [node, "-e", harness + script + assertions],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    return json.loads(result.stdout)


def test_batch_requires_explicit_choice_and_keeps_invalid_partner_pending():
    result = run_browser_actions(
        [
            {"ids": ["first"], "treatment": ""},
            {"ids": [], "treatment": "expense"},
            {"ids": ["first"], "treatment": "partner_receivable"},
            {
                "ids": ["first"],
                "treatment": "partner_receivable",
                "debtor": "explicit-account",
                "reason": "   ",
            },
        ]
    )
    assert all(item["decisions"] == [] and item["error"] for item in result)
    assert result[-1]["checked"] == ["first"]


def test_explicit_group_and_exception_replacement_never_duplicate_ids():
    result = run_browser_actions(
        [
            {"ids": ["first", "second"], "treatment": "expense"},
            {
                "ids": ["second"],
                "treatment": "partner_receivable",
                "debtor": "explicit-account",
                "reason": "  excepción confirmada  ",
            },
        ]
    )
    assert result[0]["decisions"] == [
        {
            "treatment": "expense",
            "debtor_account_id": None,
            "reason": "",
            "expense_ids": ["first", "second"],
        }
    ]
    final = result[1]
    assert not final["error"] and final["checked"] == []
    groups = final["decisions"]
    assert groups[0]["expense_ids"] == ["first"]
    assert groups[1] == {
        "treatment": "partner_receivable",
        "debtor_account_id": "explicit-account",
        "reason": "excepción confirmada",
        "expense_ids": ["second"],
    }
    assert [item for group in groups for item in group["expense_ids"]] == [
        "first",
        "second",
    ]
