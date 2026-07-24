from devnous.gastos.routes.admin_budget_routes import (
    _render_budget_status_message,
    _select_requested_budget_version,
)
from devnous.gastos.routes.admin_budget_ui import render_budget_matrix_filters


def test_budget_status_message_escapes_query_html() -> None:
    rendered = _render_budget_status_message(
        '<img src=x onerror="alert(1)">',
        is_error=True,
    )

    assert "<img" not in rendered
    assert "&lt;img" in rendered
    assert "&quot;alert(1)&quot;" in rendered


def test_requested_budget_version_must_match_id_and_year() -> None:
    versions = [
        {"id": "draft-2026", "edition_year": 2026, "status": "draft"},
        {"id": "draft-2025", "edition_year": 2025, "status": "draft"},
    ]

    assert _select_requested_budget_version(
        versions,
        requested_version_id="draft-2026",
        edition_year=2026,
    ) == versions[0]
    assert (
        _select_requested_budget_version(
            versions,
            requested_version_id="draft-2025",
            edition_year=2026,
        )
        is None
    )


def test_budget_matrix_filters_preserve_selected_version() -> None:
    rendered = render_budget_matrix_filters(
        tournament_key="torneo-1",
        edition_year=2026,
        version_id='draft-2026"><script>',
        all_versions=[{"edition_year": 2026}],
        phase_options=[],
        visible_count=0,
        total_count=0,
    )

    assert 'name="version_id"' in rendered
    assert 'value="draft-2026&quot;&gt;&lt;script&gt;"' in rendered
    assert "<script>" not in rendered
