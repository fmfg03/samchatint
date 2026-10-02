"""Synthetic browser evidence; never represents production or owners' UAT."""

import json
import sys
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import Page

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "unit"))
from test_direction_home import scope, snapshot  # noqa: E402

from samchat.client_executive.conversation import answer_snapshot  # noqa: E402
from samchat.client_executive.home import build_snapshot  # noqa: E402
from samchat.client_executive.home_ui import render_home  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


def test_report_tabs_multiselect_and_dialog_keyboard_access(page: Page):
    data = snapshot()

    def serve(route):
        if route.request.url.endswith(".js"):
            route.fulfill(
                content_type="application/javascript",
                body=(ROOT / "static/direction_home.js").read_text(),
            )
        elif route.request.url.endswith(".css"):
            route.fulfill(
                content_type="text/css",
                body=(ROOT / "static/direction_reports.css").read_text(),
            )
        else:
            route.fulfill(
                content_type="text/html",
                body=render_home(data, scope(), token="synthetic", csrf="synthetic"),
            )

    page.route("http://direction.test/**", serve)
    page.goto("http://direction.test/direccion/inicio")
    assert not page.locator("#sam-dialog").evaluate("el => el.open")
    page.locator("#summary-tab").focus()
    page.keyboard.press("ArrowRight")
    assert page.locator("#budget-tab").get_attribute("aria-selected") == "true"
    assert page.locator("#budget-panel").is_visible()
    page.locator("[data-group=group-8]").click()
    assert page.locator("[data-parent=group-8]").first.is_visible()
    assert page.locator(".financial-table tbody tr").count() == 26
    page.locator("#budget-tab").focus()
    page.keyboard.press("End")
    assert page.locator("#cashflow-panel").is_visible()
    page.locator("#open-sam").click()
    assert page.locator("#sam-dialog").evaluate("el => el.open")
    page.keyboard.press("Escape")
    assert not page.locator("#sam-dialog").evaluate("el => el.open")
    assert page.locator("#open-sam").evaluate("el => el === document.activeElement")
    page.locator(".tournament-select > summary").click()
    page.locator("[name=tournament_ids]").nth(0).check()
    page.locator("[name=tournament_ids]").nth(1).check()
    assert "2" in page.locator(".tournament-select > summary").inner_text()
    with page.expect_navigation():
        page.locator(".home-filters > button").click()
    assert parse_qs(urlparse(page.url).query)["tournament_ids"] == [
        r["id"] for r in scope()["tournaments"]
    ]
    page.set_viewport_size({"width": 390, "height": 844})
    first_value = page.locator(".home-metric strong").first
    assert first_value.bounding_box()["y"] < 800
    page.locator(".metric-details summary").first.click()
    assert page.locator(".metric-details").first.get_attribute("open") is not None
    for tab in ("summary", "budget", "cashflow"):
        page.locator(f"#{tab}-tab").click()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        if tab == "budget":
            assert page.locator(".scroll-hint").is_visible()
            assert page.locator("#budget-legend").evaluate(
                "el => el.scrollWidth <= el.clientWidth"
            )


def test_desktop_mobile_context_and_filter_navigation(page: Page):
    data = snapshot()
    for row in data["tournaments"]:
        row["monthly_execution"] = {
            "status": "available",
            "rows": [{"month": 1, "value": row["values"]["actual"]}],
        }
    requests = []
    errors = []
    html = render_home(data, scope(), token="synthetic-receipt", csrf="synthetic-csrf")
    page.set_viewport_size({"width": 1440, "height": 1000})
    page.on("pageerror", lambda error: errors.append(str(error)))

    def serve(route):
        nonlocal data
        if route.request.url.endswith("/static/direction_home.js"):
            route.fulfill(
                content_type="application/javascript",
                body=(ROOT / "static/direction_home.js").read_text(),
            )
        elif route.request.url.endswith("/static/direction_reports.css"):
            route.fulfill(
                content_type="text/css",
                body=(ROOT / "static/direction_reports.css").read_text(),
            )
        elif route.request.method == "POST":
            body = route.request.post_data_json
            requests.append(body)
            assert route.request.headers["x-direction-csrf"] == "synthetic-csrf"
            assert body["context_token"] == "synthetic-receipt"
            answer = answer_snapshot(data, body["metric_id"], body["question"])
            answer["conversation_id"] = "synthetic-conversation"
            route.fulfill(content_type="application/json", body=json.dumps(answer))
        else:
            query = parse_qs(urlparse(route.request.url).query)
            if "edition_year" in query:
                selected_scope = scope()
                selected_scope["portfolio_id"] = query.get("portfolio_id", [None])[0]
                data = build_snapshot(
                    selected_scope,
                    data["tournaments"],
                    year=int(query["edition_year"][0]),
                    start=date.fromisoformat(query["date_from"][0]),
                    end=date.fromisoformat(query["date_to"][0]),
                    observed_at=data["as_of"],
                )
                body = render_home(
                    data,
                    selected_scope,
                    token="synthetic-receipt",
                    csrf="synthetic-csrf",
                )
            else:
                body = html
            route.fulfill(content_type="text/html", body=body)

    page.route("http://direction.test/**", serve)
    page.goto("http://direction.test/direccion/inicio")
    page.locator("[data-metric=actual]").click()
    page.locator("#home-explain").click()
    page.wait_for_function(
        "document.getElementById('home-answer').textContent.includes('Hechos')"
    )
    assert requests[-1]["metric_id"] == "actual"
    assert "$100.00 MXN" in page.locator("#home-answer").inner_text()
    page.locator("#home-source").click()
    assert (
        data["indicators"][1]["source"] in page.locator("#home-evidence").inner_text()
    )
    page.locator("#close-sam").click()
    page.locator(".source-gaps").evaluate("el => el.open = true")
    page.locator("[data-metric=liquidity]").click()
    page.locator("#home-compare").click()
    page.wait_for_function(
        "document.querySelectorAll('#home-history article').length === 2"
    )
    assert "Sin dato" in page.locator("#home-answer").inner_text()
    assert requests[-1]["conversation_id"] == "synthetic-conversation"
    assert (
        page.locator("[data-metric=liquidity]").get_attribute("aria-pressed") == "true"
    )
    page.locator("#close-sam").click()
    screenshot_dir = Path("/tmp/samchat-431-evidence")
    screenshot_dir.mkdir(exist_ok=True)
    page.screenshot(path=str(screenshot_dir / "desktop.png"), full_page=True)
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(path=str(screenshot_dir / "mobile.png"), full_page=True)
    page.locator(".tournament-select").evaluate("el => el.open = true")
    page.locator("[name=tournament_ids]").first.check()
    page.locator("[name=portfolio_id]").select_option(scope()["portfolios"][0]["id"])
    assert not page.locator("[name=tournament_ids]").first.is_checked()
    page.locator("[name=edition_year]").fill("2025")
    page.locator("[name=edition_year]").dispatch_event("change")
    assert page.locator("[name=date_from]").input_value() == "2025-01-01"
    with page.expect_navigation():
        page.locator(".home-filters > button").click()
    query = parse_qs(urlparse(page.url).query)
    assert query["portfolio_id"] == [
        scope()["portfolio_id"] or scope()["portfolios"][0]["id"]
    ]
    assert "tournament_id" not in query
    assert "2025" in page.locator("#home-context").inner_text()
    assert page.locator("#home-history article").count() == 0
    page.locator("[data-metric=actual]").click()
    page.locator("#home-explain").click()
    page.wait_for_function(
        "document.getElementById('home-answer').textContent.includes('Hechos')"
    )
    assert "2025-01-01 a 2025-12-31" in page.locator("#home-answer").inner_text()
    assert requests[-1]["conversation_id"] is None
    assert not errors


def test_scenario_download_uses_same_context_and_signed_analysis(page: Page):
    from samchat.client_executive.reports import build_report
    from samchat.executive.exporter import generate_direction_report_xlsx

    data = snapshot()
    latest = None
    captured = []
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))

    def serve(route):
        nonlocal latest
        if route.request.url.endswith("/static/direction_home.js"):
            route.fulfill(
                content_type="application/javascript",
                body=(ROOT / "static/direction_home.js").read_text(),
            )
        elif "/exportar/" in route.request.url:
            payload = route.request.post_data_json
            assert payload["context_token"] == "same-cut"
            assert payload["analysis_token"] == "signed-analysis"
            captured.append(payload)
            route.fulfill(
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                headers={"X-Direction-Snapshot": data["snapshot_id"]},
                body=generate_direction_report_xlsx(build_report(data, latest)),
            )
        elif route.request.url.endswith("/static/direction_reports.css"):
            route.fulfill(
                content_type="text/css",
                body=(ROOT / "static/direction_reports.css").read_text(),
            )
        elif route.request.method == "POST":
            payload = route.request.post_data_json
            latest = answer_snapshot(
                data,
                payload["metric_id"],
                payload["question"],
                scenario=payload.get("scenario"),
            )
            latest.update(
                analysis_token="signed-analysis", conversation_id="same-conversation"
            )
            route.fulfill(content_type="application/json", body=json.dumps(latest))
        else:
            route.fulfill(
                content_type="text/html",
                body=render_home(data, scope(), token="same-cut", csrf="same-csrf"),
            )

    page.route("http://direction.test/**", serve)
    page.goto("http://direction.test/direccion/inicio")
    page.locator("#open-sam").click()
    page.locator("#home-scenario").evaluate("el => el.parentElement.open = true")
    page.locator("#scenario-basis").select_option("observed_expense")
    page.locator("#scenario-percent").fill("10")
    page.locator("#home-scenario button[type=submit]").click()
    page.wait_for_function(
        "document.getElementById('home-answer').textContent.includes('90.00')"
    )
    with page.expect_download() as download:
        page.locator("#home-report [data-export=xlsx]").click()
    assert download.value.suggested_filename.endswith(".xlsx")
    assert captured and not errors
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    Path("/tmp/samchat-432-433-evidence").mkdir(exist_ok=True)
    page.screenshot(
        path="/tmp/samchat-432-433-evidence/mobile-scenario.png", full_page=True
    )
