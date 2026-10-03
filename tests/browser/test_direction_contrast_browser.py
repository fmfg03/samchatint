"""Synthetic Direction contrast with the runtime's exact theme injection.

The AST loads only the existing HTML transformer and its literal assets, avoiding
runtime startup, environment configuration, authentication and database access.
"""

import ast
import json
import os
import sys
from pathlib import Path

import pytest
from playwright.sync_api import Page

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from direction_report_visual_qa import synthetic_snapshot  # noqa: E402

from samchat.client_executive.home_ui import render_home  # noqa: E402
from samchat.client_executive.report_layouts import (  # noqa: E402
    GROUPS,
    budget_layout,
    cashflow_layout,
)

OUT = Path(os.environ.get("SAMCHAT_BROWSER_ARTIFACT_DIR", "/tmp/direction-contrast"))


def runtime_theme(html):
    tree = ast.parse((ROOT / "copa_telmex_dashboard.py").read_text())
    nodes = [
        node
        for node in tree.body
        if (
            isinstance(node, ast.Assign)
            and any(
                isinstance(t, ast.Name) and t.id == "MODERN_UI_HEAD_INJECTION"
                for t in node.targets
            )
        )
        or (isinstance(node, ast.FunctionDef) and node.name == "_inject_modern_theme")
    ]
    assert len(nodes) == 2
    namespace = {}
    exec(
        compile(ast.Module(body=nodes, type_ignores=[]), "runtime-theme", "exec"),
        namespace,
    )
    return namespace["_inject_modern_theme"](html)


CONTRAST = """root => {
  const rgb = text => (text.match(/[\\d.]+/g) || []).map(Number);
  const blend = (front, back) => front.slice(0,3).map((n,i) => n*(front[3]??1)+back[i]*(1-(front[3]??1)));
  const luminance = color => color.slice(0,3).map(n => {n/=255; return n<=.04045?n/12.92:((n+.055)/1.055)**2.4;}).reduce((n,c,i)=>n+c*[.2126,.7152,.0722][i],0);
  const results = [];
  for (const el of root.querySelectorAll('*')) {
    if (!Array.from(el.childNodes).some(n=>n.nodeType===3 && n.textContent.trim())) continue;
    if (el.closest('[hidden],.sr-only,script,style') || !el.checkVisibility({checkOpacity:true,checkVisibilityCSS:true})) continue;
    const style = getComputedStyle(el);
    if (style.visibility!=='visible') continue;
    let ancestors=[], node=el;
    while(node) { ancestors.unshift(node); node=node.parentElement; }
    let bg=[255,255,255], gradient=false;
    for (const parent of ancestors) {
      const s=getComputedStyle(parent);
      bg=blend(rgb(s.backgroundColor),bg);
      if(s.backgroundImage!=='none') gradient=true;
    }
    const fg=blend(rgb(style.color),bg), a=luminance(fg), b=luminance(bg);
    const ratio=(Math.max(a,b)+.05)/(Math.min(a,b)+.05);
    const large=parseFloat(style.fontSize)>=24 || (parseFloat(style.fontSize)>=18.66 && parseInt(style.fontWeight)>=700);
    results.push({tag:el.tagName, text:el.textContent.trim().slice(0,70), ratio, required:large?3:4.5, gradient});
  }
  return results;
}"""


def populated_snapshot():
    data, selected = synthetic_snapshot()
    cells = dict(
        budget_month="1000", budget_ytd="6000", actual_month="800", actual_ytd="4800"
    )
    source = {f"group-{r}/part-{i}": cells for r, _ in GROUPS for i in (1, 2)}
    source.update(income=cells, broadcast=cells, sponsorship=cells)
    data["reports"]["budget"] = budget_layout(source_rows=source, period=data["period"])
    data["reports"]["cashflow"] = cashflow_layout(
        periods=[
            dict(
                label="Junio 2026 · sintético",
                start="2026-06-01",
                end="2026-06-30",
                opening="100",
                origins=["10"] * 6,
                applications=["5"] * 6,
            )
        ],
        period=data["period"],
    )
    return data, selected


@pytest.mark.parametrize("width", [1440, 390])
def test_direction_contrast_under_runtime_theme(page: Page, width):
    OUT.mkdir(parents=True, exist_ok=True)
    data, selected = populated_snapshot()
    html = render_home(data, selected, token="synthetic", csrf="synthetic")
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    # Removing only the new page marker reproduces the pre-fix runtime cascade.
    before = True

    def serve(route):
        url = route.request.url
        if url.endswith(".css"):
            route.fulfill(
                content_type="text/css",
                body=(ROOT / "static/direction_reports.css").read_text(),
            )
        elif url.endswith(".js"):
            route.fulfill(
                content_type="application/javascript",
                body=(ROOT / "static/direction_home.js").read_text(),
            )
        else:
            source = html.replace(' class="direction-reports"', "") if before else html
            route.fulfill(content_type="text/html", body=runtime_theme(source))

    page.route("http://direction.test/**", serve)
    page.set_viewport_size(dict(width=width, height=1000 if width == 1440 else 844))
    page.goto("http://direction.test/direccion/inicio")
    assert "linear-gradient" in page.locator("body").evaluate(
        "el=>getComputedStyle(el).backgroundImage"
    )
    assert (
        page.locator("h1").evaluate("el=>getComputedStyle(el).color")
        == "rgb(15, 23, 42)"
    )
    page.screenshot(path=str(OUT / f"before-{width}.png"), full_page=True)
    before = False
    page.reload()
    assert page.locator("#samchat-modern-theme").count() == 1
    assert page.locator(".sam-table-wrap").count() > 0  # Global JS really ran.
    assert (
        page.locator("body").evaluate("el=>getComputedStyle(el).backgroundImage")
        == "none"
    )
    assert (
        page.locator(".home-metric").first.evaluate(
            "el=>getComputedStyle(el).backgroundColor"
        )
        == "rgb(255, 255, 255)"
    )
    checks = {}

    def inspect(name, selector="main"):
        rows = page.locator(selector).evaluate(CONTRAST)
        assert rows
        checks[name] = rows
        failures = [
            r for r in rows if r["gradient"] or r["ratio"] + 0.01 < r["required"]
        ]
        assert not failures, failures
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=str(OUT / f"after-{name}-{width}.png"), full_page=True)

    inspect("summary")
    page.locator(".tournament-select > summary").click()
    assert page.locator(".tournament-select").evaluate("el=>el.open")
    inspect("selector")
    page.locator(".tournament-select > summary").click()
    page.locator("#budget-tab").click()
    page.locator("[data-group=group-8]").click()
    inspect("budget")
    page.locator("#cashflow-tab").click()
    page.locator(".cash-detail > summary").first.click()
    page.locator(".cash-detail > summary").last.click()
    inspect("cashflow")
    page.locator("#open-sam").click()
    page.locator("#home-scenario").evaluate("el=>el.parentElement.open=true")
    inspect("sam", "#sam-dialog")
    page.locator("#close-sam").focus()
    page.keyboard.press("Tab")
    page.keyboard.press("Shift+Tab")
    assert (
        page.locator("#close-sam").evaluate("el=>getComputedStyle(el).outlineStyle")
        != "none"
    )
    page.keyboard.press("Escape")
    assert not page.locator("#sam-dialog").evaluate("el=>el.open")
    assert not errors
    (OUT / f"contrast-{width}.json").write_text(
        json.dumps(checks, indent=2, ensure_ascii=False)
    )
