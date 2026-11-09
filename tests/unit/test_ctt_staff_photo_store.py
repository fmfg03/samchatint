"""Private synthetic photo objects are verified bytes, never publication."""

from dataclasses import replace
import hashlib
from io import BytesIO
import os
from types import SimpleNamespace

from PIL import Image
import pytest

from devnous.copa_telmex.registration_governance import _derive_geometry
from devnous.copa_telmex.staff_capture_executor import CaptureDenied
from devnous.copa_telmex.staff_photo_store import (
    PrivateReviewPhotoStore,
    derive_batch_review_photo,
    derive_rectangular_review_photo,
    encode_review_photo,
)


@pytest.fixture
def photo():
    image = Image.new("RGB", (100, 120), "white")
    image.paste("red", (0, 0, 50, 120))
    return encode_review_photo(image)


@pytest.fixture
def objects(tmp_path):
    root = tmp_path / "objects"
    root.mkdir(mode=0o700)
    return PrivateReviewPhotoStore(root)


@pytest.mark.parametrize("mode,file_mode", [(0o700, 0o400), (0o750, 0o440)])
def test_stage_is_private_content_addressed_and_idempotent(
    objects, photo, mode, file_mode
):
    objects.root.chmod(mode)
    assert objects.stage(photo) == "ctt-photo:" + photo.sha256
    filename = photo.sha256 + ".jpg"
    path = objects.root / filename
    before = path.stat()
    assert before.st_mode & 0o777 == file_mode
    assert before.st_nlink == 1
    assert before.st_uid == os.geteuid()
    assert before.st_gid == objects.root.stat().st_gid
    assert objects.read(filename) == photo.data
    assert objects.stage(photo) == photo.photo_path
    assert path.stat().st_ino == before.st_ino
    assert list(objects.root.iterdir()) == [path]
    assert hashlib.sha256(photo.data).hexdigest() == photo.sha256
    assert len(photo.ahash) == 16
    assert photo.data.hex() not in repr(photo)


@pytest.mark.parametrize(
    "name", ["../a.jpg", "/a.jpg", "a.jpg", "A" * 64 + ".jpg", "0" * 64 + ".png"]
)
def test_names_cannot_escape_content_address(objects, name):
    with pytest.raises(CaptureDenied):
        objects.read(name)


@pytest.mark.parametrize("change", ["sha", "not_image", "oversize", "empty"])
def test_invalid_candidate_never_creates_an_object(objects, photo, change):
    if change == "sha":
        photo = replace(photo, sha256="f" * 64)
    elif change in {"not_image", "empty"}:
        raw = b"invalid" if change == "not_image" else b""
        photo = replace(photo, data=raw, sha256=hashlib.sha256(raw).hexdigest())
    else:
        objects.max_photo_bytes = len(photo.data) - 1
    with pytest.raises(CaptureDenied):
        objects.stage(photo)
    assert list(objects.root.iterdir()) == []


@pytest.mark.parametrize(
    "change",
    ["writable", "root_mode", "symlink", "hardlink", "tamper", "fifo", "limit"],
)
def test_read_rejects_unsealed_or_substituted_objects(objects, photo, tmp_path, change):
    objects.stage(photo)
    path = objects.root / (photo.sha256 + ".jpg")
    if change == "writable":
        path.chmod(0o600)
    elif change == "root_mode":
        objects.root.chmod(0o755)
    elif change == "hardlink":
        os.link(path, tmp_path / "second-link")
    elif change in {"symlink", "fifo"}:
        path.unlink()
        if change == "symlink":
            target = tmp_path / "target"
            target.write_bytes(photo.data)
            target.chmod(0o400)
            path.symlink_to(target)
        else:
            os.mkfifo(path, 0o400)
    elif change == "limit":
        objects.max_photo_bytes = len(photo.data) - 1
    else:
        path.chmod(0o600)
        path.write_bytes(photo.data[:-1] + b"x")
        path.chmod(0o400)
    with pytest.raises(CaptureDenied):
        objects.read(path.name)
    if change != "limit":
        with pytest.raises(CaptureDenied):
            objects.stage(photo)
    assert not list(objects.root.glob(".pending-*"))


def test_symlink_in_root_ancestry_is_rejected(objects, photo, tmp_path):
    link = tmp_path / "redirect"
    link.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(CaptureDenied):
        redirected = PrivateReviewPhotoStore(link / "objects")
        redirected.stage(photo)
    assert list(objects.root.iterdir()) == []


def test_ancestor_replaced_after_configuration_is_rejected(objects, photo, tmp_path):
    parent = tmp_path / "parent"
    parent.mkdir()
    root = parent / "objects"
    root.mkdir(mode=0o700)
    store = PrivateReviewPhotoStore(root)
    parent.rename(tmp_path / "moved")
    parent.symlink_to(tmp_path / "moved", target_is_directory=True)
    with pytest.raises(CaptureDenied):
        store.stage(photo)
    assert not list((tmp_path / "moved" / "objects").iterdir())


def test_repository_cannot_become_a_private_photo_root():
    from pathlib import Path
    from devnous.copa_telmex import staff_photo_store

    repo = Path(staff_photo_store.__file__).resolve().parents[3]
    for path in (repo, repo / "static" / "private-photos", repo / "uploads"):
        with pytest.raises(CaptureDenied):
            PrivateReviewPhotoStore(path)


def test_effect_reads_player_and_staff_exact_hashes(objects, photo):
    objects.stage(photo)
    player = {"photo_path": photo.photo_path, "photo_sha256": photo.sha256}
    staff = {"photo_path": photo.photo_path}
    effect = {"players": [player], "staff": [{"row": staff}, {"row": None}]}
    objects.verify_effect_photos(effect)
    player["photo_sha256"] = "f" * 64
    with pytest.raises(CaptureDenied):
        objects.verify_effect_photos(effect)
    player["photo_path"] = None
    staff["photo_path"] = "https://example.invalid/photo.jpg"
    with pytest.raises(CaptureDenied):
        objects.verify_effect_photos(effect)


@pytest.mark.parametrize("tamper", [None, "crop", "page", "limit", "coordinates"])
def test_rectangle_is_rederived_from_verified_source(tmp_path, tamper):
    image = Image.new("RGB", (200, 240), "white")
    image.paste("red", (40, 60, 140, 180))
    stream = BytesIO()
    image.save(stream, format="PNG")
    raw = stream.getvalue()
    path = tmp_path / "page.png"
    path.write_bytes(raw)
    geometry = _derive_geometry(
        SimpleNamespace(image_path=path, sha256=hashlib.sha256(raw).hexdigest()),
        {"x": 40, "y": 60, "width": 100, "height": 120},
    )
    limit = len(raw)
    if tamper == "crop":
        geometry["crop_hash"] = "sha256:" + "f" * 64
    elif tamper == "page":
        raw += b"substituted"
    elif tamper == "limit":
        limit -= 1
    elif tamper == "coordinates":
        geometry["field_coordinates"]["x"] += 1
    if tamper:
        with pytest.raises(CaptureDenied):
            derive_rectangular_review_photo(raw, geometry, max_page_bytes=limit)
    else:
        photo = derive_rectangular_review_photo(raw, geometry, max_page_bytes=limit)
        with Image.open(BytesIO(photo.data)) as decoded:
            assert decoded.size == (100, 120)
            assert decoded.getpixel((50, 60))[0] > 240
            assert decoded.getpixel((50, 60))[1] < 10


def test_small_image_is_rejected():
    with pytest.raises(CaptureDenied):
        encode_review_photo(Image.new("RGB", (39, 80)))


def test_batch_crop_requires_real_grid_and_source_slot():
    from test_ctt_staff_capture_source_adapter import candidate, Provider
    from devnous.copa_telmex.staff_capture_source_adapter import SourceCheckAdapter

    locked, _, manifest, pdf, images = candidate()
    adapter = SourceCheckAdapter(
        Provider(manifest, pdf, images),
        max_source_bytes=1_000_000,
        max_asset_bytes=1_000_000,
        max_manifest_bytes=1_000_000,
    )
    with adapter.open_verified_snapshot(locked) as snapshot:
        for reference in ("page:1:jugador_3", "page:1:jugador_21", "../page"):
            with pytest.raises(CaptureDenied):
                derive_batch_review_photo(snapshot, reference)


def test_partial_write_failure_removes_unpublished_scratch(objects, photo, monkeypatch):
    original_write = os.write
    writes = 0

    def fail_after_partial_write(fd, data):
        nonlocal writes
        writes += 1
        if writes == 1:
            return original_write(fd, data[:20])
        raise OSError("synthetic disk failure")

    monkeypatch.setattr(os, "write", fail_after_partial_write)
    with pytest.raises(CaptureDenied):
        objects.stage(photo)
    assert writes == 2
    assert list(objects.root.iterdir()) == []


@pytest.mark.skipif(os.geteuid() != 0, reason="ownership fixture requires local root")
def test_object_owner_must_match_private_directory(objects, photo):
    objects.stage(photo)
    path = objects.root / (photo.sha256 + ".jpg")
    os.chown(path, 65534, -1)
    with pytest.raises(CaptureDenied):
        objects.read(path.name)
