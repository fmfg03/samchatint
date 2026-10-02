"""Render synthetic Direction evidence locally; no credentials or database reads.

Run with PYTHONPATH=src and a local Chromium executable. The baseline renderer
is read from the already-present PR452 commit, never fetched from production.
"""

import hashlib
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests/unit"))
from test_direction_home import T1, T2, row, scope

from samchat.client_executive.home import build_snapshot
from samchat.client_executive.home_ui import render_home
from samchat.client_executive.report_layouts import (
    GROUPS,
    budget_layout,
    cashflow_layout,
)
from samchat.client_executive.reports import build_report
from samchat.executive.exporter import (
    generate_direction_report_pdf,
    generate_direction_report_xlsx,
)

OUT = ROOT / "docs/evidence/direction-report-redesign"
BASE = "11ff4c4b828bec7df4e8b0b07e87d9184739e3bc"


def synthetic_snapshot():
    rows = [row(T1, "240000"), row(T2, "160000")]
    for item, name in zip(rows, ("Torneo sintético Norte", "Torneo sintético Sur")):
        item["name"] = name
        item["values"].update(
            budget=None,
            committed="180000",
            paid="95000",
            forecast=None,
            deviation=None,
            obligations="12000",
            receivables=None,
        )
        item["gaps"] = {
            "budget": ["No existe versión aprobada en este ejemplo sintético."],
            "receivables": ["Falta atribución y cobro aceptado."],
            "forecast": ["Requiere presupuesto aprobado y base comparable."],
            "deviation": ["Requiere presupuesto aprobado."],
        }
        item["monthly_execution"] = {
            "status": "available",
            "rows": [{"month": 6, "value": item["values"]["actual"]}],
        }
    selected = scope()
    selected["tournaments"] = [{"id": r["id"], "name": r["name"]} for r in rows]
    selected["selected"] = selected["tournaments"]
    data = build_snapshot(
        selected,
        rows,
        year=2026,
        start=date(2026, 1, 1),
        end=date(2026, 6, 30),
        observed_at="2026-06-30T18:00:00+00:00",
    )
    return data, selected


def main():
    OUT.mkdir(exist_ok=True, parents=True)
    data, selected = synthetic_snapshot()
    namespace = {"__package__": "samchat.client_executive"}
    original = subprocess.check_output(
        ["git", "show", f"{BASE}:src/samchat/client_executive/home_ui.py"],
        cwd=ROOT,
        text=True,
    )
    exec(compile(original, "baseline_home_ui.py", "exec"), namespace)
    baseline_js = subprocess.check_output(
        ["git", "show", f"{BASE}:static/direction_home.js"], cwd=ROOT, text=True
    )
    errors = []
    checks = {}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path="/usr/bin/chromium", headless=True, args=["--no-sandbox"]
        )
        for phase, renderer in (
            ("before", namespace["render_home"]),
            ("after", render_home),
        ):
            page = browser.new_page(
                viewport={"width": 1440, "height": 1000}, device_scale_factor=1
            )
            page.on("pageerror", lambda error: errors.append(str(error)))
            html = renderer(
                data,
                selected,
                token="synthetic-no-authority",
                csrf="synthetic-no-authority",
            )
            html = html.replace(
                "<header>", "<header><strong>DATOS SINTÉTICOS · QA</strong>", 1
            )

            def serve(route):
                if route.request.url.endswith(".js"):
                    route.fulfill(
                        content_type="application/javascript",
                        body=(
                            baseline_js
                            if phase == "before"
                            else (ROOT / "static/direction_home.js").read_text()
                        ),
                    )
                elif route.request.url.endswith(".css"):
                    route.fulfill(
                        content_type="text/css",
                        body=(ROOT / "static/direction_reports.css").read_text(),
                    )
                else:
                    route.fulfill(content_type="text/html", body=html)

            page.route("http://direction.test/**", serve)
            page.goto("http://direction.test/direccion/inicio")
            page.screenshot(path=str(OUT / f"{phase}-desktop.png"), full_page=True)
            if phase == "after":
                checks["no_permanent_aside"] = page.locator("aside").count() == 0
                checks["sam_initially_closed"] = not page.locator(
                    "#sam-dialog"
                ).evaluate("el => el.open")
                page.locator("[data-metric=actual]").click()
                checks["sam_contextual"] = page.locator("#sam-dialog").evaluate(
                    "el => el.open"
                )
                page.screenshot(path=str(OUT / "after-sam.png"), full_page=True)
                page.keyboard.press("Escape")
                for tab in ("budget", "cashflow"):
                    page.locator(f"#{tab}-tab").click()
                    page.screenshot(
                        path=str(OUT / f"after-{tab}-desktop.png"), full_page=True
                    )
                page.locator("#summary-tab").click()
            page.set_viewport_size({"width": 390, "height": 844})
            page.screenshot(path=str(OUT / f"{phase}-mobile.png"), full_page=True)
            checks[f"{phase}_mobile_no_overflow"] = page.evaluate(
                "document.documentElement.scrollWidth <= innerWidth"
            )
            if phase == "after":
                for tab in ("budget", "cashflow"):
                    page.locator(f"#{tab}-tab").click()
                    checks[f"{tab}_mobile_no_overflow"] = page.evaluate(
                        "document.documentElement.scrollWidth <= innerWidth"
                    )
                    page.screenshot(
                        path=str(OUT / f"after-{tab}-mobile.png"), full_page=True
                    )
            page.close()
        # Independently declared synthetic mapping exercises populated contracts.
        cells = {
            "budget_month": "30000",
            "budget_ytd": "30000",
            "actual_month": "25000",
            "actual_ytd": "25000",
        }
        sources = {f"group-{r}/part-{i}": cells for r, _ in GROUPS for i in (1, 2)}
        sources.update(
            broadcast=cells,
            sponsorship=cells,
            income={
                "budget_month": "550000",
                "budget_ytd": "550000",
                "actual_month": "600000",
                "actual_ytd": "600000",
            },
        )
        populated_rows = json.loads(json.dumps(data["tournaments"]))
        for item in populated_rows:
            item["values"]["budget"] = "240000"
            item["gaps"]["budget"] = []
            item["gaps"]["forecast"] = [
                "Proyección no incluida en este ejemplo sintético."
            ]
            item["gaps"]["deviation"] = [
                "Proyección no incluida en este ejemplo sintético."
            ]
        populated = build_snapshot(
            selected,
            populated_rows,
            year=2026,
            start=date(2026, 1, 1),
            end=date(2026, 6, 30),
            observed_at=data["as_of"],
        )
        populated["reports"]["budget"] = budget_layout(
            period=data["period"], source_rows=sources
        )
        populated["reports"]["cashflow"] = cashflow_layout(
            period=data["period"],
            periods=[
                {
                    "start": "2026-06-01",
                    "end": "2026-06-30",
                    "opening": "100000",
                    "origins": ["100000"] * 6,
                    "applications": ["60000"] * 6,
                }
            ],
        )
        for layout in populated["reports"].values():
            layout["source"] = (
                "Mapeo sintético de QA; no representa datos ni configuración productivos"
            )
        # This fixture represents a different snapshot, never reuses its receipt.
        populated.pop("snapshot_id")
        populated["snapshot_id"] = hashlib.sha256(
            json.dumps(populated, sort_keys=True).encode()
        ).hexdigest()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("pageerror", lambda error: errors.append(str(error)))
        html = render_home(
            populated, selected, token="synthetic-populated", csrf="synthetic"
        )
        html = html.replace(
            "<header>",
            "<header><strong>MAPEO SINTÉTICO · QA · NO PRODUCTIVO</strong>",
            1,
        )
        page.route("http://direction.test/**", serve)
        page.goto("http://direction.test/direccion/inicio")
        for width, suffix in ((1440, "desktop"), (390, "mobile")):
            page.set_viewport_size(
                {"width": width, "height": 1000 if width == 1440 else 844}
            )
            for tab in ("budget", "cashflow"):
                page.locator(f"#{tab}-tab").click()
                page.screenshot(
                    path=str(OUT / f"populated-{tab}-{suffix}.png"), full_page=True
                )
                checks[f"populated_{tab}_{suffix}_no_overflow"] = page.evaluate(
                    "document.documentElement.scrollWidth <= innerWidth"
                )
        page.close()
        browser.close()
    populated_report = build_report(populated)
    populated_report["title"] = "Informe de QA · datos sintéticos"
    (OUT / "synthetic-populated-report.pdf").write_bytes(
        generate_direction_report_pdf(populated_report)
    )
    (OUT / "synthetic-populated-report.xlsx").write_bytes(
        generate_direction_report_xlsx(populated_report)
    )
    report = build_report(data)
    report["title"] = "Informe de QA · datos sintéticos"
    (OUT / "synthetic-report.pdf").write_bytes(generate_direction_report_pdf(report))
    (OUT / "synthetic-report.xlsx").write_bytes(generate_direction_report_xlsx(report))
    (OUT / "visual-checks.json").write_text(
        json.dumps(
            {
                "checks": checks,
                "javascript_errors": errors,
                "data": "synthetic only",
                "baseline": BASE,
            },
            indent=2,
        )
    )
    print(json.dumps({"checks": checks, "javascript_errors": errors}))
    assert all(checks.values()) and not errors


if __name__ == "__main__":
    main()
