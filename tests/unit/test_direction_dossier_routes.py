"""Direction session and scoped capture contracts through the mounted router."""

from html.parser import HTMLParser
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from test_direction_route_middleware import _direction_client, _employee

from devnous.gastos.routes import direction_dossier_routes as routes

PATH = "/direccion/expediente-vivo"


class _Inputs(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.values = {}
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "input" and attributes.get("type") == "hidden":
            self.values[attributes.get("name")] = attributes.get("value", "")


@pytest.fixture
def dossier_client(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET_KEY", "direction-dossier-tests")
    client = _direction_client(monkeypatch, _employee())
    tournament_id = str(uuid4())
    monkeypatch.setattr(
        routes,
        "_authorized_tournaments",
        AsyncMock(return_value=[{"id": tournament_id, "name": "Copa verificada"}]),
    )

    async def load_sources(_session, **params):
        assert params["finance_allowed"]
        assert not params["contacts_allowed"] and not params["media_allowed"]
        return {
            "entities": ["Morelos"],
            "options": {"categories": ["Juvenil"]},
            "operations": {
                "source": "tournament_soul_snapshot",
                "status": "available",
                "rows": [],
            },
            "finance": {"source": "documentos", "status": "unavailable", "rows": []},
        }

    async def permissions(_session, _employee, action="editar"):
        return {"operations"} if action == "editar" else set()

    monkeypatch.setattr(routes, "known_editions", AsyncMock(return_value=[2027, 2026]))
    monkeypatch.setattr(
        routes, "load_dossier_sources", AsyncMock(side_effect=load_sources)
    )
    monkeypatch.setattr(
        routes,
        "read_records",
        AsyncMock(return_value={"status": "available", "rows": []}),
    )
    monkeypatch.setattr(routes, "capture_permissions", permissions)
    monkeypatch.setattr(
        routes,
        "read_domains",
        AsyncMock(return_value={"operations", "finance", "marketing"}),
    )
    return client, tournament_id


def test_dossier_uses_existing_direction_guard_and_exact_edition(dossier_client):
    client, tid = dossier_client
    assert client.get("/direccion").status_code == 401
    assert client.get(PATH).status_code == 401
    client.get("/_test/login")
    assert client.get("/direccion").headers["location"] == "/direccion/inicio"
    assert client.get(PATH, params={"tournament_id": str(uuid4())}).status_code == 403
    assert routes.load_dossier_sources.await_count == 0
    result = client.get(
        PATH,
        params={"tournament_id": tid, "edition_year": 2026, "entity": "Morelos"},
    )
    assert result.status_code == 200
    assert routes.load_dossier_sources.await_args.kwargs["edition_year"] == 2026
    assert "Morelos" in result.text
    assert "no-store" in result.headers["cache-control"]
    assert result.headers["referrer-policy"] == "no-referrer"
    assert (
        client.get(
            PATH, params={"tournament_id": tid, "entity": "No existe"}
        ).status_code
        == 422
    )


def test_dossier_confirm_binds_review_to_exact_data_and_revalidates_actor(
    dossier_client, monkeypatch
):
    from devnous.gastos.routes import dependencies

    client, tid = dossier_client
    client.get("/_test/login")
    page = client.get(PATH, params={"tournament_id": tid, "edition_year": 2026})
    csrf = _Inputs(page.text).values["csrf"]
    session = AsyncMock()

    async def session_override():
        yield session

    client.app.dependency_overrides[dependencies.get_db_session] = session_override
    save = AsyncMock(return_value={"id": str(uuid4()), "revision": 1})
    monkeypatch.setattr(routes, "save_record", save)
    payload = {
        "csrf": csrf,
        "tournament_id": tid,
        "edition_year": "2026",
        "kind": "team_target",
        "entity": "Morelos",
        "phase": "estatal",
        "category": "Juvenil",
        "field_expected_teams": "10",
        "field_gender": "varonil",
        "intent": "preview",
        "evidence_reference": "Acta autorizada",
    }
    reject = client.post(f"{PATH}/captura", data={**payload, "csrf": "incorrecto"})
    assert reject.status_code == 403
    preview = client.post(f"{PATH}/captura", data=payload)
    assert preview.status_code == 200
    assert "Confirmar y guardar" in preview.text and "Acta autorizada" in preview.text
    assert save.await_count == 0
    confirmed = {**_Inputs(preview.text).values, "intent": "confirm"}
    assert (
        client.post(
            f"{PATH}/captura",
            data={**confirmed, "field_expected_teams": "11"},
        ).status_code
        == 403
    )
    monkeypatch.setattr(routes, "capture_permissions", AsyncMock(return_value=set()))
    assert client.post(f"{PATH}/captura", data=confirmed).status_code == 403
    assert save.await_count == 0
    monkeypatch.setattr(
        routes,
        "capture_permissions",
        AsyncMock(
            side_effect=lambda *_args, action="editar": (
                {"operations"} if action == "editar" else set()
            )
        ),
    )
    assert client.post(f"{PATH}/captura", data=confirmed).status_code == 303
    assert save.await_args.kwargs["edition_year"] == 2026
    assert save.await_args.kwargs["request_id"] == confirmed["request_id"]
    session.commit.assert_awaited_once()


def test_dossier_photo_permission_precedes_file_resolution(dossier_client, monkeypatch):
    client, tid = dossier_client
    client.get("/_test/login")
    resolve = AsyncMock()
    monkeypatch.setattr(routes, "resolve_dossier_photo", resolve)
    response = client.get(
        f"{PATH}/fotografias/{uuid4()}",
        params={"tournament_id": tid, "edition_year": 2026},
    )
    assert response.status_code == 403
    resolve.assert_not_awaited()


def test_dossier_finance_denial_is_forwarded_before_read(dossier_client, monkeypatch):
    client, tid = dossier_client
    client.get("/_test/login")
    monkeypatch.setattr(
        routes, "read_domains", AsyncMock(return_value={"operations", "marketing"})
    )
    monkeypatch.setattr(routes, "load_dossier_sources", AsyncMock(return_value={}))
    response = client.get(PATH, params={"tournament_id": tid, "edition_year": 2026})
    assert response.status_code == 200
    assert not routes.load_dossier_sources.await_args.kwargs["finance_allowed"]
    assert not routes.known_editions.await_args.kwargs["finance_allowed"]
    assert "finance" not in routes.read_records.await_args.kwargs["allowed_domains"]


def test_photo_serves_protected_bytes_only_for_edition_and_canonical_category(
    dossier_client, monkeypatch
):
    client, tid = dossier_client
    client.get("/_test/login")
    pid, aid, mid, sid = (str(uuid4()) for _ in range(4))

    async def records(_session, **kw):
        return {
            "status": "available",
            "rows": (
                [
                    {
                        "id": pid,
                        "kind": "activation_photo",
                        "phase": "nacional",
                        "entity": "",
                        "category": "Juvenil",
                        "fields": {"photo_id": mid, "activation_id": aid},
                    },
                    {
                        "id": aid,
                        "kind": "activation",
                        "phase": "nacional",
                        "entity": "",
                        "category": "Juvenil",
                        "fields": {},
                    },
                    {
                        "id": sid,
                        "kind": "operations_edition",
                        "phase": "nacional",
                        "entity": "",
                        "category": "",
                        "fields": {"operations_tournament_id": tid},
                    },
                ]
                if kw["edition_year"] == 2026
                else []
            ),
        }

    monkeypatch.setattr(routes, "read_records", records)
    monkeypatch.setattr(
        routes,
        "read_domains",
        AsyncMock(return_value={"media", "marketing", "operations"}),
    )
    resolve = AsyncMock(return_value=(b"protected-image-bytes", "image/jpeg"))
    monkeypatch.setattr(routes, "resolve_dossier_photo", resolve)
    route = f"{PATH}/fotografias/{pid}"
    assert (
        client.get(
            route, params={"tournament_id": tid, "edition_year": 2027}
        ).status_code
        == 404
    )
    resolve.assert_not_awaited()
    response = client.get(route, params={"tournament_id": tid, "edition_year": 2026})
    assert response.status_code == 200 and response.content == b"protected-image-bytes"
    assert response.headers["content-type"] == "image/jpeg"
    assert "no-store" in response.headers["cache-control"]
    assert resolve.await_args.kwargs["expected_category"] == "Juvenil"
    assert resolve.await_args.kwargs["operations_edition_id"] == tid


def test_source_failure_and_unknown_edition_leave_capture_unavailable(
    dossier_client, monkeypatch
):
    client, tid = dossier_client
    client.get("/_test/login")
    monkeypatch.setattr(routes, "known_editions", AsyncMock(return_value=[]))
    monkeypatch.setattr(routes, "load_dossier_sources", AsyncMock(return_value={}))
    monkeypatch.setattr(
        routes,
        "read_records",
        AsyncMock(return_value={"status": "unavailable", "rows": []}),
    )
    assert client.get(PATH, params={"edition_year": "26"}).status_code == 422
    assert client.get(PATH).status_code == 200
    result = client.get(PATH, params={"tournament_id": tid, "edition_year": 2027})
    assert result.status_code == 200
    assert "Confirmar y guardar" not in result.text


def _valid_preview(client, tid):
    page = client.get(PATH, params={"tournament_id": tid, "edition_year": 2026})
    return {
        "csrf": _Inputs(page.text).values["csrf"],
        "tournament_id": tid,
        "edition_year": "2026",
        "kind": "team_target",
        "phase": "estatal",
        "entity": "Morelos",
        "category": "Juvenil",
        "field_gender": "varonil",
        "field_expected_teams": "10",
        "evidence_reference": "Acta autorizada",
        "intent": "preview",
    }


@pytest.mark.parametrize(
    "raw,content_type,code",
    [
        (b"{}", "application/json", 415),
        (b"x=" + b"y" * 16384, "application/x-www-form-urlencoded", 413),
        (b"x=%FF", "application/x-www-form-urlencoded", 422),
        (b"x=1&x=2", "application/x-www-form-urlencoded", 422),
    ],
)
def test_capture_rejects_unrecognized_form_encoding(
    dossier_client, raw, content_type, code
):
    client, _ = dossier_client
    client.get("/_test/login")
    response = client.post(
        f"{PATH}/captura", content=raw, headers={"content-type": content_type}
    )
    assert response.status_code == code
    assert routes.read_records.await_count == 0


@pytest.mark.parametrize(
    "overlay,code",
    [
        ({"tournament_id": "invalid"}, 422),
        ({"edition_year": ""}, 422),
        ({"edition_year": "2099"}, 422),
        ({"edition_year": "２０２６"}, 422),
        ({"kind": "undefined"}, 422),
        ({"unexpected": "x"}, 422),
        ({"entity": "Entidad ajena"}, 422),
        ({"field_expected_teams": "NaN"}, 422),
        (
            {
                "record_id": "8aa3d6b0-53c7-4353-be01-c204a1293e01",
                "expected_revision": "-1",
            },
            422,
        ),
        (
            {
                "record_id": "8aa3d6b0-53c7-4353-be01-c204a1293e01",
                "expected_revision": "2",
            },
            404,
        ),
        ({"intent": "unknown"}, 422),
    ],
)
def test_capture_rejects_out_of_scope_or_invalid_record(dossier_client, overlay, code):
    client, tid = dossier_client
    client.get("/_test/login")
    response = client.post(
        f"{PATH}/captura", data={**_valid_preview(client, tid), **overlay}
    )
    assert response.status_code == code


def test_canonical_source_denial_blocks_capture_even_with_write_grant(
    dossier_client,
    monkeypatch,
):
    client, tid = dossier_client
    client.get("/_test/login")
    form = _valid_preview(client, tid)
    monkeypatch.setattr(routes, "read_domains", AsyncMock(return_value={"finance"}))
    assert client.post(f"{PATH}/captura", data=form).status_code == 403


def test_capture_bootstraps_explicit_operations_edition_and_fails_if_storage_missing(
    dossier_client,
    monkeypatch,
):
    client, tid = dossier_client
    client.get("/_test/login")
    form = _valid_preview(client, tid)
    binding = {
        key: value for key, value in form.items() if not key.startswith("field_")
    }
    binding.update(
        kind="operations_edition",
        edition_year="2031",
        entity="",
        phase="nacional",
        category="",
        field_operations_tournament_id=tid,
    )
    preview = client.post(f"{PATH}/captura", data=binding)
    assert preview.status_code == 200
    assert "Vínculo de la edición con Operaciones SOUL" in preview.text
    monkeypatch.setattr(
        routes,
        "read_records",
        AsyncMock(return_value={"status": "unavailable", "rows": []}),
    )
    assert client.post(f"{PATH}/captura", data=binding).status_code == 503


def test_capture_conflict_rolls_back_without_committing(dossier_client, monkeypatch):
    from devnous.gastos.routes import dependencies
    from samchat.client_executive.dossier_records import DossierRecordError

    client, tid = dossier_client
    client.get("/_test/login")
    form = _valid_preview(client, tid)
    session = AsyncMock()

    async def session_override():
        yield session

    client.app.dependency_overrides[dependencies.get_db_session] = session_override
    review = client.post(f"{PATH}/captura", data=form)
    assert review.status_code == 200
    monkeypatch.setattr(
        routes,
        "save_record",
        AsyncMock(side_effect=DossierRecordError("Vínculo duplicado")),
    )
    confirmed = {**_Inputs(review.text).values, "intent": "confirm"}
    assert client.post(f"{PATH}/captura", data=confirmed).status_code == 409
    session.commit.assert_not_awaited()
    session.rollback.assert_awaited_once()
