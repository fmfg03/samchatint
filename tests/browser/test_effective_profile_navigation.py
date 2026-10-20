from __future__ import annotations

import os
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from navigation_inventory import (
    BLOCKED_NAVIGATION_ENTRIES,
    VISIBLE_NAVIGATION_ENTRIES,
)


PROFILE_CASES = [
    pytest.param(
        "employee",
        "/gastos-terceros",
        ["/admin/finanzas", "/admin/contabilidad"],
        id="employee-transfer",
    ),
    pytest.param(
        "finance",
        "/admin/finanzas",
        [],
        id="finance-payment-entry",
    ),
    pytest.param(
        "accounting",
        "/admin/contabilidad/estado",
        [],
        id="accounting-entry",
    ),
]

PANEL_REQUIRED_CASES = [
    pytest.param(
        "approver",
        "/documentos/pendientes",
        id="approver-approval-panel-required",
    ),
    pytest.param(
        "budget_control",
        "/documentos/control-presupuestal",
        id="budget-control-panel-required",
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


@pytest.mark.parametrize("entry", VISIBLE_NAVIGATION_ENTRIES)
def test_navigation_inventory_renders_canonical_target(
    page: Page, browser_server: str, entry
) -> None:
    response = page.goto(f"{browser_server}{entry.test_path}")
    assert response is not None
    assert response.status == 200

    scope = page
    if entry.section_label:
        scope = page.locator(f'section[aria-label="{entry.section_label}"]')
        expect(scope).to_have_count(1)

    target = scope.locator(f'a[href="{entry.href}"]').filter(
        has_text=entry.label
    )
    assert target.count() >= 1
    expect(target.first).to_be_visible()


@pytest.mark.parametrize("entry", BLOCKED_NAVIGATION_ENTRIES)
@pytest.mark.parametrize(
    "profile",
    [
        "employee",
        "approver",
        "budget_control",
        "finance",
        "accounting",
        "direction",
    ],
)
def test_navigation_inventory_hides_legacy_target_for_every_profile(
    page: Page, browser_server: str, entry, profile: str
) -> None:
    response = page.goto(f"{browser_server}/_test/profile/{profile}")
    assert response is not None
    assert response.status == 200

    expect(page.locator(f'a[href="{entry.href}"]')).to_have_count(0)


@pytest.mark.parametrize(
    "profile,expected_href,forbidden_hrefs", PROFILE_CASES
)
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

    expect(
        page.get_by_role("heading", name=f"Perfil simulado: {profile}")
    ).to_be_visible()
    expect(page.get_by_test_id("task-prompt")).to_be_visible()

    entry = page.locator(f'a[href="{expected_href}"]')
    assert entry.count() >= 1
    expect(entry.first).to_be_visible()

    for href in forbidden_hrefs:
        expect(page.locator(f'a[href="{href}"]')).to_have_count(0)

    assert _focus_reaches_href(page, expected_href), (
        f"{profile}: intended task entry {expected_href} is not keyboard "
        "reachable"
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


@pytest.mark.parametrize("profile,target_href", PANEL_REQUIRED_CASES)
def test_nav_only_baseline_records_tasks_that_require_panel_entry(
    page: Page,
    browser_server: str,
    profile: str,
    target_href: str,
) -> None:
    response = page.goto(f"{browser_server}/_test/profile/{profile}")
    assert response is not None
    assert response.status == 200

    # Evidence boundary: these task queues are not exposed by the navigation
    # helpers themselves. #357 must simulate the real /panel cards next before
    # this is treated as a product defect.
    expect(page.locator(f'a[href="{target_href}"]')).to_have_count(0)
    _capture_profile(page, profile)


@pytest.mark.parametrize(
    "profile,target_href,label",
    [
        pytest.param(
            "approver",
            "/documentos/pendientes",
            "Aprobaciones pendientes",
            id="approver-panel-card",
        ),
        pytest.param(
            "budget_control",
            "/documentos/control-presupuestal",
            "Control Presupuestal",
            id="budget-control-panel-card",
        ),
    ],
)
def test_real_panel_exposes_assigned_task_card(
    page: Page,
    browser_server: str,
    profile: str,
    target_href: str,
    label: str,
) -> None:
    response = page.goto(f"{browser_server}/_test/panel/{profile}")
    assert response is not None
    assert response.status == 200

    entry = page.locator(f'a[href="{target_href}"]')
    expect(entry).to_have_count(1)
    expect(entry).to_be_visible()
    expect(entry.get_by_text(label, exact=True)).to_be_visible()

    assert _focus_reaches_href(page, target_href, max_tabs=120), (
        f"{profile}: panel task entry {target_href} is not keyboard reachable"
    )
    _capture_profile(page, f"{profile}-panel")


def test_direction_panel_exposes_direction_task_entry(
    page: Page, browser_server: str
) -> None:
    response = page.goto(f"{browser_server}/_test/panel/direction")
    assert response is not None
    assert response.status == 200

    entry = page.locator('a[href="/direccion/inicio"]')
    assert entry.count() >= 1
    expect(entry.first).to_be_visible()
    assert _focus_reaches_href(page, "/direccion/inicio", max_tabs=120)
    _capture_profile(page, "direction-panel")
