from pathlib import Path
from types import SimpleNamespace

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "templates"


def _environment() -> Environment:
    return Environment(
        loader=FileSystemLoader(TEMPLATES_DIR),
        autoescape=select_autoescape(("html",)),
    )


def _request(path: str) -> SimpleNamespace:
    return SimpleNamespace(url=SimpleNamespace(path=path))


@pytest.mark.parametrize(
    "template_name",
    sorted(path.name for path in TEMPLATES_DIR.glob("*.html")),
)
def test_versioned_dashboard_templates_compile(template_name: str) -> None:
    _environment().get_template(template_name)


def test_home_exposes_registration_review_inbox() -> None:
    template = _environment().get_template("home.html")
    stats = SimpleNamespace(
        total_teams=1,
        total_players=16,
        total_ocr_registrations=1,
        average_ocr_confidence=0.9,
        players_needing_review=2,
        review_rate=12.5,
    )

    html = template.render(
        request=_request("/dashboard"),
        stats=stats,
        pending_reviews=2,
        review_operations={
            "pending_count": 3,
            "ready_count": 1,
            "blocked_count": 1,
            "processing_count": 0,
            "rejected_count": 1,
            "recent": [
                {
                    "reference": "REG-2026-ABC12345",
                    "tournament_slug": "copa_telmex",
                    "player_count": 16,
                    "issue_count": 2,
                    "updated_at_iso": "2026-07-15T19:55:00+00:00",
                    "updated_at_display": "15/07/2026 19:55 UTC",
                    "recency": "Hace 5 min",
                    "state": "ready",
                    "state_label": "Lista para capturar",
                    "review_url": "/registration-review/session-123",
                    "action_label": "Continuar captura",
                }
            ],
        },
    )

    assert 'href="/registration-review"' in html
    assert "Bandeja de precaptura" in html
    assert "Operación de precaptura" in html
    assert "Listas para capturar" in html
    assert "REG-2026-ABC12345" in html
    assert "Hace 5 min" in html
    assert 'href="/registration-review/session-123"' in html
    assert "Continuar captura" in html


def test_detail_renders_read_only_canonical_comparison() -> None:
    template = _environment().get_template("registration_review_detail.html")
    canonical_review = {
        "canonical_hash": "canonical-123",
        "player_count": 1,
        "legacy_player_count": 1,
        "review_count": 1,
        "difference_count": 1,
        "difference_player_count": 1,
        "matches_legacy": False,
        "team": {
            "name": "Deportivo Estrellas",
            "category": "Libre",
            "gender": "Femenil",
        },
        "team_difference_labels": ["nombre"],
        "manager_differences": [],
        "team_differences": [
            {
                "label": "nombre",
                "legacy_value": "Deportivo Estellas",
                "canonical_value": "Deportivo Estrellas",
            }
        ],
        "players": [
            {
                "slot": 1,
                "name": "María López",
                "birth_date": "01/01/2000",
                "confidence_pct": "94%",
                "source_page": 1,
                "requires_review": True,
                "matches_legacy": False,
                "roster_difference": False,
                "missing_from_legacy": False,
                "difference_labels": ["nombre"],
                "differences": [
                    {
                        "label": "nombre",
                        "legacy_value": "Maria Lopes",
                        "canonical_value": "María López",
                    }
                ],
                "photo_url": (
                    "/photos/review_sessions/session/canonical_shadow/" "player_01.jpg"
                ),
            }
        ],
    }

    html = template.render(
        request=_request("/registration-review/session"),
        review_session=SimpleNamespace(
            id="session",
            status="review",
            provider="openai",
            tournament_slug="copa_telmex",
        ),
        assets=[],
        team={},
        manager={},
        players=[],
        notes="",
        validation={
            "blockers": [],
            "issues": [],
            "ready_to_commit": False,
        },
        layout_regions={},
        overall_confidence="0%",
        canonical_review=canonical_review,
        canonical_promotion_enabled=False,
        tournament_options=[],
    )

    assert "Comparación canónica" in html
    assert "no autoritativa" in html
    assert "Esta lectura histórica no modifica el borrador" in html
    assert canonical_review["players"][0]["photo_url"] in html
    assert "Sólo diferencias" in html
    assert "Borrador actual" in html
    assert "Deportivo Estellas" in html
    assert "Lectura canónica" in html
    assert 'data-action="reject"' in html
    assert "/api/registration-review/session/reject" in html
    assert 'name="canonical_fields"' not in html
    assert 'name="canonical_value"' not in html


def test_detail_routes_governed_conflicts_through_regs05_controls() -> None:
    template = _environment().get_template("registration_review_detail.html")
    canonical_review = {
        "canonical_hash": "canonical-123",
        "governed": True,
        "decision_id": "sha256:" + "d" * 64,
        "player_count": 0,
        "legacy_player_count": 0,
        "review_count": 0,
        "difference_count": 1,
        "difference_player_count": 0,
        "matches_legacy": False,
        "team_differences": [
            {
                "field": "name",
                "label": "nombre",
                "legacy_value": "Equipo anterior",
                "canonical_value": "Equipo canónico",
                "input_name": "team_name",
                "classification": "MATERIAL_CHANGE",
                "field_path": "team.name",
            }
        ],
        "manager_differences": [
            {
                "field": "email",
                "label": "email",
                "legacy_value": "anterior@example.test",
                "canonical_value": "canonico@example.test",
                "input_name": "manager_email",
                "classification": "MATERIAL_CHANGE",
                "field_path": "manager.email",
            }
        ],
        "players": [
            {
                "slot": 1,
                "name": "María López",
                "confidence_pct": "94%",
                "source_page": 1,
                "matches_legacy": False,
                "requires_review": False,
                "roster_difference": False,
                "missing_from_legacy": False,
                "photo_url": None,
                "differences": [
                    {
                        "field": "name",
                        "label": "nombre",
                        "legacy_value": "Maria Lopes",
                        "canonical_value": "María López",
                        "input_name": "player_0_name",
                        "classification": "MATERIAL_CHANGE",
                        "field_path": "players.1.name",
                    }
                ],
            }
        ],
    }

    html = template.render(
        request=_request("/registration-review/session"),
        review_session=SimpleNamespace(
            id="session",
            status="review",
            provider="openai",
            tournament_slug="copa_telmex",
        ),
        assets=[],
        team={},
        manager={},
        players=[],
        notes="",
        validation={"blockers": [], "issues": [], "ready_to_commit": False},
        layout_regions={},
        overall_confidence="0%",
        canonical_review=canonical_review,
        canonical_promotion_enabled=True,
        tournament_options=[],
    )

    assert "/api/registration-review/session/canonical-adopt" not in html
    assert "REG-S03 · pendiente REG-S05" in html
    assert "Aceptar candidato" in html
    assert "Conservar actual" in html
    assert "Corregir manualmente" in html
    assert "Vaciar campo" in html
    assert 'data-regs05-target="team_name"' in html
    assert 'data-regs05-target="manager_email"' in html
    assert 'data-regs05-target="player_0_name"' in html
    assert "Guardar correcciones" in html
    assert 'name="canonical_fields"' not in html
    assert 'name="canonical_value"' not in html


def test_committed_detail_disables_decisions_and_omits_reject_dialog() -> None:
    template = _environment().get_template("registration_review_detail.html")

    html = template.render(
        request=_request("/registration-review/session"),
        review_session=SimpleNamespace(
            id="session",
            status="committed",
            provider="openai",
            tournament_slug="copa_telmex",
        ),
        assets=[],
        team={},
        manager={},
        players=[],
        notes="",
        validation={"blockers": [], "issues": [], "ready_to_commit": True},
        layout_regions={},
        overall_confidence="100%",
        canonical_review=None,
        tournament_options=[],
    )

    assert "Revisión capturada." in html
    assert 'data-action="modify" disabled' in html
    assert 'data-action="reject" data-open-reject disabled' in html
    assert "La revisión ya fue capturada" in html
    assert 'id="reject-review-dialog"' not in html


def test_inbox_renders_mobile_cards_and_operational_filters() -> None:
    template = _environment().get_template("registration_review_list.html")

    html = template.render(
        request=_request("/registration-review"),
        sessions=[
            {
                "id": "session-12345678",
                "status": "rejected",
                "provider": "openai",
                "source": "telegram",
                "tournament_slug": "copa_telmex",
                "started_at": "2026-07-15 10:00",
                "issue_count": 2,
                "player_count": 16,
                "needs_review": True,
                "committed_team_id": None,
                "cover_url": None,
            }
        ],
    )

    assert "Bandeja de precaptura" in html
    assert 'data-inbox-filter="pending"' in html
    assert 'data-inbox-filter="rejected"' in html
    assert "Rechazado" in html
    assert "Continuar revisión" in html
