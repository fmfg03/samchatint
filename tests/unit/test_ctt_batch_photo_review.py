"""Batch photo provenance, previews and capture share one safe crop path."""

import hashlib
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from PIL import Image, ImageDraw

os.environ.setdefault(
    "SESSION_SECRET_KEY", "test-only-session-secret-key-0123456789abcdef"
)

import copa_telmex_dashboard as dashboard  # noqa: E402

from devnous.tournaments.core import ctt_batch_photo_alignment as align  # noqa: E402


def _asset(tmp_path: Path):
    image = Image.new("RGB", (612, 792), "white")
    draw = ImageDraw.Draw(image)
    for x in (46, 122, 311, 385, 565):
        draw.line((x, 238, x, 723), fill="black", width=2)
    for row in range(6):
        y = 238 + row * 97
        draw.line((46, y, 565, y), fill="black", width=2)
    # Printed slot 1 is blank. Printed slot 3 has a distinguishable image.
    draw.rectangle((51, 437, 115, 522), fill=(35, 35, 35))
    path = tmp_path / "source.png"
    image.save(path)
    return SimpleNamespace(
        page_index=1,
        image_path=str(path),
        width=612,
        height=792,
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        source_ocr_run_ref="batch:" + "a" * 64 + ":source-page:5",
    )


def test_slots_survive_filtered_players_without_reassigning_photos(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(align, "_face_present", lambda _image: True)
    asset = _asset(tmp_path)
    current = [{"name": "First Player"}, {"name": "Third Player"}]
    draft = SimpleNamespace(
        mutation_type="batch_manifest_admitted",
        extraction={
            "players": [
                {"slot_ref": "page:5:jugador_1"},
                {"slot_ref": "page:5:jugador_3"},
            ]
        },
    )
    enriched = dashboard._batch_photo_players(current, draft)
    assert all("_batch_source_slot" not in player for player in current)
    results = dashboard._batch_review_photo_crops(
        enriched, [asset], {"source_pages": [5, 6]}
    )
    assert results[1][1] in {"missing", "uncertain"}
    assert results[2][1] == "photo"
    assert results[2][0].getpixel((30, 30))[0] < 60


def test_preview_and_capture_share_resolver_and_remove_stale_preview(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(align, "_face_present", lambda _image: True)
    monkeypatch.setattr(dashboard, "review_uploads_dir", tmp_path / "reviews")
    monkeypatch.setattr(dashboard, "__file__", str(tmp_path / "dashboard.py"))
    asset = _asset(tmp_path)
    session_id = uuid4()
    stale = tmp_path / "reviews" / str(session_id) / "player_previews" / "player_01.jpg"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"old incorrect preview")
    players = [
        {"name": "First Player", "_batch_source_slot": "page:5:jugador_1"},
        {"name": "Third Player", "_batch_source_slot": "page:5:jugador_3"},
    ]
    layout = {"source_pages": [5, 6]}
    previews = dashboard._build_review_player_photo_previews(
        session_id=session_id,
        players_payload=players,
        assets=[asset],
        layout_regions=layout,
        batch_source=True,
    )
    artifacts = dashboard._build_review_photo_artifacts(
        team_id=session_id,
        players_payload=players,
        assets=[asset],
        layout_regions=layout,
        batch_source=True,
    )
    assert not stale.exists()
    assert previews[1]["preview_status"] in {"missing", "uncertain"}
    assert previews[2]["preview_status"] == "photo"
    assert "?v=" in previews[2]["preview_url"]
    assert set(artifacts) == {2}
    with Image.open(previews[2]["preview_path"]) as a, Image.open(
        artifacts[2]["photo_path"]
    ) as b:
        assert a.size == b.size


def test_tampered_or_duplicate_page_and_ambiguous_refs_fail_closed(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(align, "_face_present", lambda _image: True)
    asset = _asset(tmp_path)
    player = [{"name": "Player", "_batch_source_slot": "page:5:jugador_3"}]
    layout = {"source_pages": [5, 6]}
    assert (
        dashboard._batch_review_photo_crops(player, [asset, asset, asset], layout)[1][1]
        == "uncertain"
    )
    asset.sha256 = "0" * 64
    assert (
        dashboard._batch_review_photo_crops(player, [asset], layout)[1][1]
        == "uncertain"
    )
    asset.sha256 = hashlib.sha256(Path(asset.image_path).read_bytes()).hexdigest()
    assert (
        dashboard._batch_review_photo_crops(player * 2, [asset], layout)[1][1]
        == "uncertain"
    )
    draft = SimpleNamespace(
        mutation_type="ocr_reprocessed",
        extraction={"players": [{"slot_ref": "page:5:jugador_3"}]},
    )
    assert (
        dashboard._batch_photo_players([{"name": "Player"}], draft)[0][
            "_batch_source_slot"
        ]
        is None
    )


def test_preview_versions_are_immutable_across_concurrent_requests(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(dashboard, "review_uploads_dir", tmp_path / "reviews")
    session_id = uuid4()
    slots = [{"name": "Player", "_batch_source_slot": "page:5:jugador_1"}]
    source = {1: (Image.new("RGB", (60, 80), "red"), "photo", 1)}
    monkeypatch.setattr(dashboard, "_batch_review_photo_crops", lambda *_: source)

    def preview():
        return dashboard._build_review_player_photo_previews(
            session_id=session_id,
            players_payload=slots,
            assets=[],
            layout_regions={"source_pages": [5, 6]},
            batch_source=True,
        )[1]

    first = preview()
    first_bytes = Path(first["preview_path"]).read_bytes()
    source[1] = (Image.new("RGB", (60, 80), "blue"), "photo", 1)
    second = preview()
    assert first["preview_path"] != second["preview_path"]
    assert first["preview_url"] != second["preview_url"]
    assert Path(first["preview_path"]).read_bytes() == first_bytes
    assert Path(second["preview_path"]).read_bytes() != first_bytes


def test_review_page_from_prior_release_uses_active_copy(tmp_path, monkeypatch):
    session_id = uuid4()
    releases = tmp_path / "releases"
    old = releases / "gastos-prod-old" / "photos" / "review_sessions" / str(session_id)
    current_photos = releases / "gastos-prod-current" / "photos"
    current = current_photos / "review_sessions" / str(session_id)
    old.mkdir(parents=True)
    current.mkdir(parents=True)
    (old / "page-01.png").write_bytes(b"original")
    (current / "page-01.png").write_bytes(b"original")
    monkeypatch.setattr(dashboard, "photos_dir", current_photos)
    old_page = old / "page-01.png"
    asset = SimpleNamespace(
        image_path=str(old_page), sha256=hashlib.sha256(b"original").hexdigest()
    )
    assert (
        dashboard._current_review_asset_path(str(old_page)) == current / "page-01.png"
    )
    assert dashboard._review_public_path(str(old_page)) == (
        f"/photos/review_sessions/{session_id}/page-01.png"
    )
    assert dashboard._review_asset_public_url(asset) is not None
    (current / "page-01.png").write_bytes(b"changed")
    assert dashboard._review_asset_public_url(asset) is None
    (current / "page-01.png").write_bytes(b"original")
    old_page.unlink()
    assert (
        dashboard._current_review_asset_path(str(old_page)) == current / "page-01.png"
    )
    outside = releases / "gastos-prod-old" / "photos" / "other" / "page.png"
    assert dashboard._current_review_asset_path(str(outside)) == outside
