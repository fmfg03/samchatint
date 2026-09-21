from __future__ import annotations

import os
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect


PROFILE_CASES = [
    pytest.param(
        "employee",
        "/gastos-terceros",
        ["/admin/finanzas", "/admin/contabilidad"],
        id="employee-transfer",
    ),
    pytest.param(
        "approver",
        "/documentos/pendientes",
        ["/documentos/pendientes-pago"],
        id="approver-approval",
    ),
    pytest.param(
        "budget_control",
        "/documentos/control-presupuestal",
        [],
        id="budget-control-assignment",
    ),
    pytest.param(
        "finance",
        "/admin/finanzas",
        [],
        id="finance-payment-entry",
    ),
    pytest.param(
        "accounting",
        "/admin/contabilidad",
        [],
        id="accounting-entry",
    ),
    pytest.param(
        "direction",
        "/direccion/tableros",
        ["/admin/finanzas"],
        id="direction-entry",
    ),
]


def _capture_profile(page: Page, profile: str) -> None:
    root = Path(
        os.environ.get(
            "SAMCHAT_BROWSER_ARTIFACT_DIR",
            Path(__file__).resolve().parents[2] / "artifacts" / "browser",
        )
    )
    target = root / "profiles"
    target.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(target / f"{profile}.png"), full_page=True)


def _focus_reaches_href(page: Page, href: str, *, max_tabs: int = 80) -> bool:
    page.evaluate("document.activeElement && document.activeElement.blur()")
    for _ in range(max_tabs):
        page.keyboard.press("Tab")
        if page.locator(":focus").get_attribute("href") == href:
            return True
    return False


@pytest.mark.parametrize("profile,expected_href,forbidden_hrefs", PROFILE_CASES)
def test_effective_profile_exposes_task_entry(
    page: Page,
    browser_server: str,
    profile: str,
    expected_href: str,
    forbidden_hrefs: list[str],
) -> None:
    response = page.goto(f"{browser_server}/_test/profile/{profile}")
    assert response is not None
    assert response.status == 200

    expect(page.get_by_role("heading", name=f"Perfil simulado: {profile}")).to_be_visible()
    expect(page.get_by_test_id("task-prompt")).to_be_visible()

    entry = page.locator(f'a[href="{expected_href}"]')
    expect(entry).to_have_count(1)
    expect(entry).to_be_visible()

    for href in forbidden_hrefs:
        expect(page.locator(f'a[href="{href}"]')).to_have_count(0)

    assert _focus_reaches_href(page, expected_href), (
        f"{profile}: intended task entry {expected_href} is not keyboard reachable"
    )
    _capture_profile(page, profile)


@pytest.mark.parametrize("profile,_,__", PROFILE_CASES)
def test_effective_profile_navigation_has_no_body_horizontal_overflow(
    page: Page,
    browser_server: str,
    profile: str,
    _: str,
    __: list[str],
) -> None:
    page.set_viewport_size({"width": 390, "height": 844})
    response = page.goto(f"{browser_server}/_test/profile/{profile}")
    assert response is not None
    assert response.status == 200

    dimensions = page.evaluate(
        """() => ({
            scrollWidth: document.documentElement.scrollWidth,
            clientWidth: document.documentElement.clientWidth
        })"""
    )
    assert dimensions["scrollWidth"] <= dimensions["clientWidth"] + 1
