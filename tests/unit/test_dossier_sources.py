"""Source selection and classification for the Direction tournament dossier."""

from datetime import date
from decimal import Decimal
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from PIL import Image
from sqlalchemy.exc import SQLAlchemyError

from devnous.gastos.services import payment_run_service
from samchat.client_executive import dossier_sources as sources

PROJECT_ID = "6e0992d9-57b8-43e4-8ca9-614674a734b7"
OPERATIONS_ID = "10d27944-96e1-452f-8968-eba4af84eb72"
DOCUMENT_ID = "894807db-be49-4153-a2ed-6437f4ec95d4"
PROJECT = {"id": PROJECT_ID, "name": "Torneo con padrón"}
ORIGIN = "https://sports.example.test"


@pytest.mark.parametrize(
    ("declared", "expected"),
    [
        ("Nacional", "nacional"),
        ("Fase estatal - grupos", "estatal"),
        ("Nacional: semifinal", "nacional"),
        ("final", None),
        ("campeón", None),
        ("pre nacional", None),
        ("", None),
    ],
)
def test_phase_requires_declared_scope(declared, expected):
    assert sources._phase(declared) == expected


@pytest.mark.asyncio
async def test_configured_empty_roster_can_attest_zero(monkeypatch):
    rows = AsyncMock(
        side_effect=[
            [],
            [
                {
                    "scope_level": 7,
                    "entity": None,
                    "category": None,
                    "gender": None,
                    "teams": 0,
                    "registrations": 0,
                    "identified": 0,
                    "registrations_without_verified_identity": 0,
                    "updated_at": None,
                }
            ],
        ]
    )
    monkeypatch.setattr(sources, "_rows", rows)
    scope = {
        "status": "CONFIGURED",
        "tournament_id": PROJECT_ID,
        "edition_year": 2026,
        "roster_slug": "ctt-2026",
    }
    result = await sources._registration(object(), scope)
    assert result["status"] == "empty"
    assert result["summary"]["registrations"] == 0
    assert result["summary"]["unique_people_identified"] == 0
    assert result["age_cutoff"] is None
    assert "e.edition_year = :edition_year" in rows.await_args.args[1]


@pytest.mark.asyncio
async def test_scope_missing_does_not_look_like_empty_roster(monkeypatch):
    rows = AsyncMock()
    monkeypatch.setattr(sources, "_rows", rows)
    result = await sources._registration(object(), {"status": "SCOPE_MISSING"})
    assert result["status"] == "needs_link"
    assert result["rows"] == []
    assert "summary" not in result
    rows.assert_not_awaited()


def test_people_with_unverified_identity_remain_registrations():
    row = {
        "entity": "Puebla",
        "category": "Juvenil",
        "gender": "Varonil",
        "teams": 1,
        "registrations": 3,
        "identified": 1,
        "registrations_without_verified_identity": 2,
        "updated_at": None,
    }
    assert sources._registration_row(row)["unique_people_identified"] == 1
    row.update(identified=0, registrations_without_verified_identity=3)
    assert sources._registration_row(row)["unique_people_identified"] is None


@pytest.mark.asyncio
async def test_mapped_soul_with_different_year_cannot_fall_back(monkeypatch):
    rows = AsyncMock(return_value=[{"operations_tournament_id": OPERATIONS_ID}])
    snapshot = AsyncMock(
        return_value={
            "tournaments": [
                {
                    "id": OPERATIONS_ID,
                    "name": PROJECT["name"],
                    "start_date": "2027-01-01",
                }
            ]
        }
    )
    monkeypatch.setattr(sources, "_rows", rows)
    monkeypatch.setattr(
        sources,
        "_soul_edition_metadata",
        AsyncMock(return_value={"id": OPERATIONS_ID, "slug": "different"}),
    )
    monkeypatch.setattr(sources, "build_tournament_soul_snapshot", snapshot)
    result, status = await sources._soul_snapshot(
        object(), PROJECT, 2026, roster_slug="ctt-2026", include_registration=False
    )
    assert result is None
    assert status == "needs_link"
    snapshot.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_finance_or_photo_query_for_denied_viewer(monkeypatch):
    monkeypatch.setattr(
        sources,
        "probe_registration_scope",
        AsyncMock(return_value={"status": "SCOPE_MISSING"}),
    )
    monkeypatch.setattr(
        sources,
        "_soul_snapshot",
        AsyncMock(return_value=({"optional_sources": {}}, "available")),
    )
    monkeypatch.setattr(sources, "_project_categories", AsyncMock(return_value=set()))
    finance, gallery = AsyncMock(), AsyncMock()
    monkeypatch.setattr(sources, "_finance", finance)
    monkeypatch.setattr(sources, "_gallery", gallery)
    result = await sources.load_dossier_sources(
        object(),
        tournament=PROJECT,
        edition_year=2027,
        finance_allowed=False,
        contacts_allowed=False,
        media_allowed=False,
    )
    assert result["finance"]["status"] == "denied"
    assert result["marketing"]["status"] == "denied"
    assert result["operations"]["edition_verified"] is True
    finance.assert_not_awaited()
    gallery.assert_not_awaited()


def test_finance_requires_paid_state_and_proof_and_keeps_actual_date():
    document = {
        "id": DOCUMENT_ID,
        "tipo": "SOLICITUD",
        "estado": "aprobado",
        "numero_referencia": "Solicitud 1",
        "monto_solicitado": Decimal("125.00"),
        "amount_for_payment": Decimal("95.00"),
        "fecha_pago_efectiva": date(2027, 1, 22),
        "proof_exists": True,
    }
    requested = sources._finance_row(document)
    assert requested["status"] == "aprobado"
    assert requested["amount_requested"] == "125.00"
    assert requested["amount_paid"] is None
    assert requested["effective_payment_date"] is None
    document.update(estado="pagado", proof_exists=False)
    assert sources._finance_row(document)["status"] == "payment_without_proof"
    assert sources._finance_row(document)["amount_paid"] is None
    document.update(proof_exists=True)
    paid = sources._finance_row(document)
    assert paid["status"] == "payment_confirmed"
    assert paid["amount_paid"] == "95.00"
    assert paid["effective_payment_date"] == "2027-01-22"


@pytest.mark.asyncio
async def test_finance_null_edition_is_limited_to_valid_scoped_ids(monkeypatch):
    rows = AsyncMock(return_value=[])
    monkeypatch.setattr(sources, "_rows", rows)
    result = await sources._finance(
        object(), PROJECT_ID, 2026, (DOCUMENT_ID, "invalid")
    )
    assert result["status"] == "empty"
    _, statement, binds = rows.await_args.args
    assert binds["edition_year"] == 2026
    assert binds["linked_ids"] == [DOCUMENT_ID]
    assert statement.count("d.torneo_id = CAST(:tournament_id AS uuid)") == 2
    assert "d.edicion IS NULL AND d.id = ANY" in statement
    assert "d.fecha_pago_efectiva" in statement
    assert payment_run_service._PAYMENT_RUN_SOURCE_SQL in statement


@pytest.mark.parametrize(
    ("path", "approved"),
    [
        ("/storage/v1/object/public/gallery-media/activation.jpg", True),
        ("/storage/v1/object/authenticated/gallery-photos/a/photo.png", True),
        ("/storage/v1/object/public/gallery-media/../other/photo.png", False),
        ("/storage/v1/object/public/gallery-media/%2e%2e/other/photo.png", False),
        ("/storage/v1/object/public/registration-documents/passport.jpg", False),
        ("/storage/v1/object/public/gallery-media/a/evil.jpg?x=y", False),
    ],
)
def test_photo_paths_are_confined_to_gallery_storage(path, approved):
    assert bool(sources._approved_photo_url(ORIGIN + path, ORIGIN)) is approved


def test_rejects_external_media_and_non_raster_data():
    assert (
        sources._approved_photo_url(
            "https://other.example.test/storage/v1/object/public/gallery-media/a.jpg",
            ORIGIN,
        )
        is None
    )
    assert sources._verified_image(b"<svg>test</svg>") is None
    target = BytesIO()
    Image.new("RGB", (10, 10), "red").save(target, format="PNG")
    assert sources._verified_image(target.getvalue()) == "image/png"


@pytest.mark.asyncio
async def test_soul_attestation_uses_local_mapping_and_exact_returned_id(monkeypatch):
    monkeypatch.setattr(
        sources,
        "_rows",
        AsyncMock(return_value=[{"operations_tournament_id": OPERATIONS_ID}]),
    )
    monkeypatch.setattr(
        sources,
        "_soul_edition_metadata",
        AsyncMock(return_value={"id": OPERATIONS_ID, "slug": "no-edition-in-slug"}),
    )
    snapshot = AsyncMock(
        return_value={
            "tournaments": [{"id": OPERATIONS_ID, "start_date": "2027-01-01"}]
        }
    )
    monkeypatch.setattr(sources, "build_tournament_soul_snapshot", snapshot)
    result, status = await sources._soul_snapshot(
        object(),
        PROJECT,
        2026,
        include_registration=False,
        operations_edition_id=OPERATIONS_ID,
    )
    assert status == "available"
    assert result["tournaments"][0]["id"] == OPERATIONS_ID
    assert snapshot.await_args.kwargs["tournament_slug"] == OPERATIONS_ID
    result, status = await sources._soul_snapshot(
        object(),
        PROJECT,
        2026,
        include_registration=False,
        operations_edition_id=DOCUMENT_ID,
    )
    assert (result, status) == (None, "needs_link")
    snapshot.assert_awaited_once()


@pytest.mark.asyncio
async def test_conflicting_explicit_soul_year_blocks_stale_attestation(monkeypatch):
    monkeypatch.setattr(
        sources,
        "_rows",
        AsyncMock(return_value=[{"operations_tournament_id": OPERATIONS_ID}]),
    )
    monkeypatch.setattr(
        sources,
        "_soul_edition_metadata",
        AsyncMock(return_value={"id": OPERATIONS_ID, "edition_year": 2027}),
    )
    snapshot = AsyncMock()
    monkeypatch.setattr(sources, "build_tournament_soul_snapshot", snapshot)
    result, status = await sources._soul_snapshot(
        object(),
        PROJECT,
        2026,
        include_registration=False,
        operations_edition_id=OPERATIONS_ID,
    )
    assert (result, status) == (None, "needs_link")
    snapshot.assert_not_awaited()


@pytest.mark.asyncio
async def test_match_participant_from_another_soul_tournament_is_hidden(monkeypatch):
    category_id = "aa97b086-bfc4-4c7b-9707-741770c8f807"
    home = "0a2121b6-2991-4b49-a85b-44bb0b333f49"
    away = "c213edf2-a72e-449d-89c3-900718f977e3"
    match_id = str(DOCUMENT_ID)
    snapshot = {
        "tournaments": [{"id": OPERATIONS_ID}],
        "operations": {
            "matches": [
                {"match_id": match_id, "phase": "Fase estatal - semifinal"},
            ]
        },
    }
    remote = AsyncMock()
    remote.fetch_all_rows.side_effect = [
        [{"id": category_id, "name": "Juvenil", "tournament_id": OPERATIONS_ID}],
        [
            {
                "id": match_id,
                "category_id": category_id,
                "home_team_id": home,
                "away_team_id": away,
            }
        ],
        [
            {
                "id": home,
                "team_name": "Equipo registrado",
                "state": "Puebla",
                "tournament_id": OPERATIONS_ID,
            },
            {
                "id": away,
                "team_name": "Otro torneo",
                "state": "Jalisco",
                "tournament_id": PROJECT_ID,
            },
        ],
    ]
    monkeypatch.setattr(sources, "SupabaseRestClient", lambda _config: remote)
    categories, matches, teams = await sources._sport_relationships(snapshot)
    assert categories == {category_id: "Juvenil"}
    assert set(teams) == {home}
    (projected,) = sources._sport_rows(snapshot, matches, teams)
    assert projected["phase"] == "estatal"
    assert projected["home_team"] == "Equipo registrado"
    assert projected["entities"] == ["Puebla"]
    assert projected["away_team"] is None


@pytest.mark.asyncio
async def test_domain_denials_skip_roster_and_gallery(monkeypatch):
    probe, soul, gallery = AsyncMock(), AsyncMock(), AsyncMock()
    monkeypatch.setattr(sources, "probe_registration_scope", probe)
    monkeypatch.setattr(sources, "_soul_snapshot", soul)
    monkeypatch.setattr(sources, "_gallery", gallery)
    result = await sources.load_dossier_sources(
        object(),
        tournament=PROJECT,
        edition_year=2026,
        finance_allowed=False,
        contacts_allowed=True,
        media_allowed=True,
        operations_allowed=False,
        marketing_allowed=False,
    )
    assert result["registration"]["status"] == "denied"
    assert result["operations"]["status"] == "denied"
    assert result["marketing"]["status"] == "denied"
    assert result["entities"] == result["categories"] == []
    probe.assert_not_awaited()
    soul.assert_not_awaited()
    gallery.assert_not_awaited()


@pytest.mark.asyncio
async def test_resolver_rejects_changed_photo_category_before_fetch(monkeypatch):
    monkeypatch.setattr(
        sources,
        "probe_registration_scope",
        AsyncMock(return_value={"status": "NOT_CONFIGURED"}),
    )
    monkeypatch.setattr(
        sources,
        "_soul_snapshot",
        AsyncMock(return_value=({"tournaments": [{"id": OPERATIONS_ID}]}, "available")),
    )
    monkeypatch.setattr(
        sources,
        "_gallery_rows",
        AsyncMock(
            return_value=(
                [
                    {
                        "id": DOCUMENT_ID,
                        "category": "Femenil",
                        "category_id": OPERATIONS_ID,
                    }
                ],
                False,
            )
        ),
    )
    transport = AsyncMock()
    monkeypatch.setattr(sources.httpx, "AsyncClient", transport)
    result = await sources.resolve_dossier_photo(
        object(), PROJECT, 2026, DOCUMENT_ID, expected_category="Juvenil"
    )
    assert result is None
    transport.assert_not_called()


@pytest.mark.asyncio
async def test_exact_ctt_edition_slug_can_resolve_older_operations_id(monkeypatch):
    monkeypatch.setattr(
        sources,
        "_rows",
        AsyncMock(return_value=[{"operations_tournament_id": OPERATIONS_ID}]),
    )
    catalog = AsyncMock()
    catalog.fetch_all_rows.return_value = [
        {
            "id": DOCUMENT_ID,
            "slug": "ctt-2026",
            "edition_year": 2026,
            "edition": 2026,
        }
    ]
    monkeypatch.setattr(sources, "SupabaseRestClient", lambda _config: catalog)
    snapshot = AsyncMock(
        return_value={"tournaments": [{"id": DOCUMENT_ID, "slug": "ctt-2026"}]}
    )
    monkeypatch.setattr(sources, "build_tournament_soul_snapshot", snapshot)

    result, status = await sources._soul_snapshot(
        object(), PROJECT, 2026, roster_slug="ctt-2026", include_registration=False
    )
    assert status == "available"
    assert sources._source_tournament_id(result) == DOCUMENT_ID
    assert catalog.fetch_all_rows.await_args.kwargs["filters"] == {
        "slug": "eq.ctt-2026"
    }
    assert snapshot.await_args.kwargs["tournament_slug"] == DOCUMENT_ID


CATEGORY_ID = "aa97b086-bfc4-4c7b-9707-741770c8f807"
PHOTO_URL = ORIGIN + "/storage/v1/object/authenticated/gallery-media/activation.png"
SOUL_TOURNAMENT = {"tournaments": [{"id": OPERATIONS_ID}]}


def _catalog_entry():
    return {"id": CATEGORY_ID, "name": "Juvenil", "tournament_id": OPERATIONS_ID}


@pytest.mark.asyncio
async def test_gallery_lists_only_scoped_metadata_without_a_phase_guess(monkeypatch):
    remote = AsyncMock()
    remote.fetch_all_rows.side_effect = [
        [_catalog_entry()],
        [
            {
                "id": DOCUMENT_ID,
                "category_id": CATEGORY_ID,
                "title": "Activación",
                "photo_date": "2026-11-18",
                "tournament_id": OPERATIONS_ID,
                "image_url": PHOTO_URL,
            },
            {"id": PROJECT_ID, "tournament_id": PROJECT_ID},
            {
                "id": CATEGORY_ID,
                "category_id": PROJECT_ID,
                "tournament_id": OPERATIONS_ID,
            },
        ],
    ]
    monkeypatch.setattr(sources, "SupabaseRestClient", lambda _config: remote)
    result = await sources._gallery(SOUL_TOURNAMENT)
    assert result["rows"] == []
    assert result["status"] == "needs_link"
    assert result["edition_verified"] is True
    assert result["candidates"] == [
        {
            "id": DOCUMENT_ID,
            "category_id": CATEGORY_ID,
            "category": "Juvenil",
            "label": "Activación",
            "date": "2026-11-18",
            "updated_at": None,
        }
    ]
    source_query = remote.fetch_all_rows.await_args.kwargs
    assert source_query["filters"] == {"tournament_id": f"eq.{OPERATIONS_ID}"}
    assert "image_url" not in source_query["select_expr"]


@pytest.mark.asyncio
async def test_legacy_gallery_requires_exact_category_scope(monkeypatch):
    remote = AsyncMock()
    remote.fetch_all_rows.side_effect = [
        [_catalog_entry()],
        sources.TournamentsV2Error("42703 missing gallery_photos.tournament_id"),
        [
            {"id": DOCUMENT_ID, "category_id": CATEGORY_ID},
            {"id": PROJECT_ID, "category_id": PROJECT_ID},
        ],
    ]
    monkeypatch.setattr(sources, "SupabaseRestClient", lambda _config: remote)
    result = await sources._gallery(SOUL_TOURNAMENT)
    assert [(card["id"], card["category"]) for card in result["candidates"]] == [
        (DOCUMENT_ID, "Juvenil")
    ]
    assert remote.fetch_all_rows.await_args.kwargs["filters"] == {
        "category_id": f"in.({CATEGORY_ID})",
    }


@pytest.mark.asyncio
async def test_gallery_denial_from_source_does_not_try_legacy_read(monkeypatch):
    remote = AsyncMock()
    remote.fetch_all_rows.side_effect = [
        [_catalog_entry()],
        sources.TournamentsV2Error("403 insufficient permission"),
    ]
    monkeypatch.setattr(sources, "SupabaseRestClient", lambda _config: remote)
    result = await sources._gallery(SOUL_TOURNAMENT)
    assert result["status"] == "unavailable"
    assert result["rows"] == []
    assert remote.fetch_all_rows.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response_status", "size_limit", "valid_image", "delivered"),
    [
        (200, 1024, True, True),
        (302, 1024, True, False),
        (200, 2, True, False),
        (200, 1024, False, False),
    ],
)
async def test_protected_gallery_download_has_no_redirect_and_validates_payload(
    monkeypatch, response_status, size_limit, valid_image, delivered
):
    config = SimpleNamespace(
        supabase_url=ORIGIN,
        service_role_key="fixture-key",
        anon_key="",
        request_timeout_sec=5,
    )
    monkeypatch.setattr(sources, "load_tournaments_v2_config", lambda: config)
    monkeypatch.setattr(sources, "PHOTO_MAX_BYTES", size_limit)
    monkeypatch.setattr(
        sources,
        "probe_registration_scope",
        AsyncMock(return_value={"status": "CONFIGURED", "roster_slug": "ctt-2026"}),
    )
    soul = AsyncMock(return_value=(SOUL_TOURNAMENT, "available"))
    monkeypatch.setattr(sources, "_soul_snapshot", soul)
    gallery = AsyncMock(
        return_value=(
            [
                {
                    "id": DOCUMENT_ID,
                    "category": "Juvenil",
                    "category_id": CATEGORY_ID,
                    "image_url": PHOTO_URL,
                }
            ],
            False,
        )
    )
    monkeypatch.setattr(sources, "_gallery_rows", gallery)

    data = BytesIO()
    Image.new("RGB", (10, 10), "red").save(data, format="PNG")
    payload = data.getvalue() if valid_image else b"<svg>fixture</svg>"
    requests = []

    def fetch(request):
        requests.append(request)
        return httpx.Response(
            response_status, content=payload, headers={"location": ORIGIN + "/other"}
        )

    transport = httpx.MockTransport(fetch)
    create_client = httpx.AsyncClient

    def build_client(**kwargs):
        assert kwargs["follow_redirects"] is False
        assert kwargs["trust_env"] is False
        return create_client(transport=transport, **kwargs)

    monkeypatch.setattr(sources.httpx, "AsyncClient", build_client)
    result = await sources.resolve_dossier_photo(
        object(),
        PROJECT,
        2026,
        DOCUMENT_ID,
        expected_category="Juvenil",
        operations_edition_id=OPERATIONS_ID,
    )
    assert result == ((payload, "image/png") if delivered else None)
    assert soul.await_args.kwargs["operations_edition_id"] == OPERATIONS_ID
    assert soul.await_args.kwargs["roster_slug"] == "ctt-2026"
    assert len(requests) == 1
    assert str(requests[0].url) == PHOTO_URL


@pytest.mark.asyncio
async def test_legacy_roster_keeps_unverified_identity_and_withholds_contacts(
    monkeypatch,
):
    snapshot = {
        **SOUL_TOURNAMENT,
        "summary": {"registrations_count": 3},
        "optional_sources": {"matches": {"available": False}},
        "breakdowns": {
            "entities": [
                {
                    "entity_name": "Puebla",
                    "teams_count": 1,
                    "players_count": 3,
                    "teams": [
                        {
                            "category": "Juvenil",
                            "team_name": "Equipo",
                            "primary_manager": {
                                "name": "Contacto",
                                "email": "fixture@example.test",
                                "phone": None,
                            },
                        }
                    ],
                },
                {"entity_name": "Sin entidad", "teams_count": 1},
            ]
        },
    }
    monkeypatch.setattr(
        sources,
        "probe_registration_scope",
        AsyncMock(return_value={"status": "NOT_CONFIGURED"}),
    )
    soul = AsyncMock(return_value=(snapshot, "available"))
    monkeypatch.setattr(sources, "_soul_snapshot", soul)
    monkeypatch.setattr(
        sources,
        "_sport_relationships",
        AsyncMock(return_value=({CATEGORY_ID: "Juvenil"}, {}, {})),
    )
    finance = AsyncMock(return_value={"status": "empty", "rows": []})
    gallery = AsyncMock(return_value={"status": "needs_link", "rows": []})
    monkeypatch.setattr(sources, "_finance", finance)
    monkeypatch.setattr(sources, "_gallery", gallery)
    result = await sources.load_dossier_sources(
        object(),
        tournament=PROJECT,
        edition_year=2026,
        finance_allowed=True,
        contacts_allowed=False,
        media_allowed=True,
        linked_document_ids=(DOCUMENT_ID,),
    )
    assert soul.await_args.kwargs["include_registration"] is True
    assert result["registration"]["status"] == "partial"
    assert result["entities"] == ["Puebla"]
    assert result["categories"] == ["Juvenil"]
    assert result["operations"]["status"] == "partial"
    assert [row.get("email") for row in result["registration"]["rows"]] == [None, None]
    assert result["registration"]["rows"][0]["unique_people_identified"] is None
    assert finance.await_args.args[-1] == (DOCUMENT_ID,)
    gallery.assert_awaited_once()

    contact_enabled = sources._legacy_registration(snapshot, contacts_allowed=True)
    (declared,) = [row for row in contact_enabled["rows"] if row.get("email")]
    assert declared["team"] == "Equipo"
    assert declared["email"] == "fixture@example.test"


@pytest.mark.asyncio
async def test_document_and_registration_db_failures_remain_unavailable(monkeypatch):
    monkeypatch.setattr(sources, "_rows", AsyncMock(side_effect=SQLAlchemyError()))
    scope = {
        "status": "CONFIGURED",
        "tournament_id": PROJECT_ID,
        "edition_year": 2026,
        "roster_slug": "ctt-2026",
    }
    registration = await sources._registration(object(), scope)
    finance = await sources._finance(object(), PROJECT_ID, 2026, ())
    assert registration["status"] == finance["status"] == "unavailable"
    assert registration["rows"] == finance["rows"] == []
    assert "summary" not in registration


@pytest.mark.asyncio
async def test_soul_catalog_can_return_exact_identity_when_edition_columns_are_absent():
    catalog = AsyncMock()
    catalog.fetch_all_rows.side_effect = [
        sources.TournamentsV2Error("42703 missing column edition_year"),
        sources.TournamentsV2Error("42703 missing column edition_year"),
        sources.TournamentsV2Error("42703 missing column edition"),
        [{"id": OPERATIONS_ID, "slug": "copa"}],
    ]
    row = await sources._soul_edition_metadata(catalog, OPERATIONS_ID)
    assert row == {"id": OPERATIONS_ID, "slug": "copa"}
    assert catalog.fetch_all_rows.await_args.kwargs["select_expr"] == "id,slug"
    assert catalog.fetch_all_rows.await_args.kwargs["filters"] == {
        "id": f"eq.{OPERATIONS_ID}",
    }
