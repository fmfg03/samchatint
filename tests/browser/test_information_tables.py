"""Offline browser acceptance for the live entrypoint's table enhancement."""
import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _fixture_html(rows=50):
    tree = ast.parse((ROOT / "copa_telmex_dashboard.py").read_text())
    theme = next(
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "MODERN_UI_HEAD_INJECTION" for target in node.targets)
    )
    body = "".join("<tr>" + "".join(f"<td>Row {i} Col {j}</td>" for j in range(12)) + "</tr>" for i in range(rows))
    table = '<table aria-label="Test information" style="min-width:1800px"><thead><tr>' + "".join(f"<th>Column {j}</th>" for j in range(12)) + "</tr></thead><tbody>" + body + "</tbody></table>"
    return '<html><head>' + theme + '<style>body{padding:20px}td,th{padding:12px;white-space:nowrap}</style></head><body><h1>Fixture</h1><div class="table-shell">' + table + '</div><h2>Second</h2><div class="ar-table-wrap">' + table + '</div></body></html>'


@pytest.mark.parametrize("width", [1440, 1024, 768, 390])
def test_long_multiple_tables_keep_headings_and_horizontal_access(page, width):
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.set_viewport_size({"width": width, "height": 900})
    page.set_content(_fixture_html())
    page.wait_for_timeout(100)
    assert page.locator(".sam-information-table").count() == 2
    assert page.locator(".table-shell .sam-table-wrap").count() == 0
    first = page.locator(".sam-information-table").first
    first.evaluate("el => {el.scrollTop=600;el.scrollLeft=300}")
    page.wait_for_timeout(100)
    assert abs(first.locator("thead th").first.bounding_box()["y"] - first.bounding_box()["y"]) < 4
    first_bar = page.locator(".sam-table-scrollbar").first
    assert first_bar.is_visible()
    first_bar.evaluate("el => el.scrollLeft=250")
    page.wait_for_timeout(100)
    assert abs(first.evaluate("el => el.scrollLeft") - 250) < 2
    second = page.locator(".sam-information-table").nth(1)
    second.scroll_into_view_if_needed()
    page.wait_for_timeout(100)
    second_bar = page.locator(".sam-table-scrollbar").nth(1)
    assert second_bar.is_visible()
    second_bar.evaluate("el => el.scrollLeft=200")
    page.wait_for_timeout(100)
    assert abs(second.evaluate("el => el.scrollLeft") - 200) < 2
    assert abs(first.evaluate("el => el.scrollLeft") - 250) < 2
    box = second_bar.bounding_box()
    assert box["y"] + box["height"] <= 901
    page.set_viewport_size({"width": 1280, "height": 800})
    page.wait_for_timeout(100)
    assert second_bar.is_visible()
    # New content and native horizontal scrolling update the accessible dock.
    second.evaluate("el => {el.querySelector('tbody').insertAdjacentHTML('beforeend', '<tr><td colspan=12>New row</td></tr>'); el.scrollLeft=120}")
    page.wait_for_timeout(100)
    assert abs(second_bar.evaluate("el => el.scrollLeft") - 120) < 2
    assert errors == []


def test_simultaneously_visible_table_scrollbars_do_not_overlap(page):
    page.set_viewport_size({"width": 1024, "height": 900})
    page.set_content(_fixture_html(rows=3))
    page.wait_for_timeout(100)
    bars = page.locator(".sam-table-scrollbar")
    assert bars.nth(0).is_visible() and bars.nth(1).is_visible()
    first, second = bars.nth(0).bounding_box(), bars.nth(1).bounding_box()
    assert first["y"] + first["height"] <= second["y"]
    bars.nth(0).focus()
    page.keyboard.press("ArrowRight")
    page.wait_for_timeout(150)
    assert page.locator(".sam-information-table").first.evaluate("el => el.scrollLeft") > 0


def test_fetched_tables_initialize_and_discarded_scrollbars_are_removed(page):
    page.set_content(_fixture_html(rows=3))
    page.wait_for_timeout(100)
    page.evaluate("""() => {
        const host = document.createElement('section');
        host.id = 'fetched-report';
        document.body.appendChild(host);
        host.innerHTML = '<table style="min-width:1800px"><thead><tr><th>Fetched heading</th></tr></thead><tbody><tr><td>Fetched data</td></tr></tbody></table>';
    }""")
    page.wait_for_timeout(100)
    assert page.locator("#fetched-report .sam-information-table").count() == 1
    assert page.locator(".sam-table-scrollbar").count() == 3
    page.evaluate("document.querySelector('#fetched-report').innerHTML = '<p>Replaced report</p>'")
    page.wait_for_timeout(100)
    assert page.locator(".sam-table-scrollbar").count() == 2
