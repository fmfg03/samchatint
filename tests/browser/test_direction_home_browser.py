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
    screenshot_dir = Path("/tmp/samchat-431-evidence")
    screenshot_dir.mkdir(exist_ok=True)
    page.screenshot(path=str(screenshot_dir / "desktop.png"), full_page=True)
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(path=str(screenshot_dir / "mobile.png"), full_page=True)
    page.locator("[name=tournament_id]").select_option(scope()["tournaments"][0]["id"])
    page.locator("[name=portfolio_id]").select_option(scope()["portfolios"][0]["id"])
    assert page.locator("[name=tournament_id]").input_value() == ""
    page.locator("[name=edition_year]").fill("2025")
    page.locator("[name=edition_year]").dispatch_event("change")
    assert page.locator("[name=date_from]").input_value() == "2025-01-01"
    with page.expect_navigation():
        page.locator(".home-filters button").click()
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
