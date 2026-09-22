from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect


VIEWPORTS = [
    pytest.param(
        {"name": "desktop-1440", "width": 1440, "height": 900},
        id="desktop-1440",
    ),
    pytest.param(
        {"name": "desktop-1280", "width": 1280, "height": 800},
        id="desktop-1280",
    ),
    pytest.param(
        {"name": "tablet-1024", "width": 1024, "height": 768},
        id="tablet-1024",
    ),
    pytest.param(
        {"name": "tablet-768", "width": 768, "height": 1024},
        id="tablet-768",
    ),
    pytest.param(
        {"name": "mobile-390", "width": 390, "height": 844},
        id="mobile-390",
    ),
]


def _login(page: Page, base_url: str) -> None:
    response = page.goto(f"{base_url}/_test/login")
    assert response is not None
    assert response.status == 200


def test_direction_dashboard_requires_authenticated_session(
    page: Page, browser_server: str
) -> None:
    response = page.goto(f"{browser_server}/direccion/tableros?edition_year=2026")
    assert response is not None
    assert response.status == 401
    expect(page.get_by_text("No has iniciado sesión", exact=False)).to_be_visible()


def test_direction_dashboard_authenticated_keyboard_navigation(
    page: Page, browser_server: str
) -> None:
    _login(page, browser_server)
    response = page.goto(f"{browser_server}/direccion/tableros?edition_year=2026")
    assert response is not None
    assert response.status == 200

    expect(page).to_have_title("Tableros de Dirección · SamChat")
    expect(
        page.get_by_role("heading", name="Tablero ejecutivo de Dirección")
    ).to_be_visible()
    expect(page.get_by_text("Torneo Browser UX", exact=True)).to_be_visible()
    expect(page.get_by_role("heading", name="Presupuesto vs. real")).to_be_visible()
    expect(
        page.get_by_role(
            "heading", name="Responsables, equipos, jugadores y avance"
        )
    ).to_be_visible()
    expect(page.get_by_text("Sólo lectura.", exact=False)).to_be_visible()
    expect(page.get_by_role("link", name="Reportes publicados")).to_be_visible()

    page.evaluate("document.activeElement && document.activeElement.blur()")
    page.keyboard.press("Tab")
    expect(page.locator(":focus")).to_have_attribute("name", "edition_year")
    page.keyboard.press("Tab")
    expect(page.locator(":focus")).to_have_text("Actualizar")

    for _ in range(12):
        page.keyboard.press("Tab")
        if page.locator(":focus").get_attribute("href") == "/direccion/reportes":
            break
    else:
        pytest.fail("Reportes publicados is not keyboard reachable from the hero.")

    expect(page.locator(":focus")).to_have_text("Reportes publicados")
    page.keyboard.press("Enter")
    expect(page).to_have_url(f"{browser_server}/direccion/reportes")
    expect(page.get_by_role("heading", name="Reportes de Dirección")).to_be_visible()


@pytest.mark.parametrize(
    "page",
    VIEWPORTS,
    indirect=True,
)
def test_direction_dashboard_has_no_body_horizontal_overflow(
    page: Page, browser_server: str
) -> None:
    _login(page, browser_server)
    response = page.goto(f"{browser_server}/direccion/tableros?edition_year=2026")
    assert response is not None
    assert response.status == 200

    dimensions = page.evaluate(
        """() => ({
            scrollWidth: document.documentElement.scrollWidth,
            clientWidth: document.documentElement.clientWidth
        })"""
    )
    overflowing = page.evaluate(
        """() => Array.from(document.querySelectorAll("*"))
            .map((el) => {
                const rect = el.getBoundingClientRect();
                return {
                    tag: el.tagName,
                    cls: el.className || "",
                    text: (el.textContent || "").trim().slice(0, 80),
                    left: Math.round(rect.left),
                    right: Math.round(rect.right),
                    width: Math.round(rect.width)
                };
            })
            .filter((item) => item.right > document.documentElement.clientWidth + 1)
            .slice(0, 20)"""
    )
    assert dimensions["scrollWidth"] <= dimensions["clientWidth"] + 1, overflowing
    expect(
        page.get_by_role("heading", name="Tablero ejecutivo de Dirección")
    ).to_be_visible()
