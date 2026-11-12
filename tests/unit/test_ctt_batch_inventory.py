"""Original-byte and page-census acceptance for the authority-free inventory."""

import copy
import hashlib
import json
import os
import socket
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import fitz
import pytest
from PIL import Image

from devnous.copa_telmex import staff_batch_inventory as module
from devnous.copa_telmex.staff_batch_contracts import canonical, digest
from devnous.copa_telmex.staff_batch_inventory import (
    INVENTORY_SCHEMA,
    RENDER_VERSION,
    InventorySourceAdapter,
    prepare_inventory,
    validate_inventory,
)


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "sources"
    root.mkdir()
    original = {}
    for filename, texts, dimensions in (
        (
            "Oaxaca Varonil 103 PT1.pdf",
            ["first source page", "second original"],
            (612, 792),
        ),
        ("Second source.pdf", ["landscape page"], (792, 612)),
    ):
        with fitz.open() as doc:
            for text in texts:
                page = doc.new_page(width=dimensions[0], height=dimensions[1])
                page.insert_text((30, 50), text)
            raw = doc.tobytes()
        path = root / filename
        path.write_bytes(raw)
        path.chmod(0o644)
        original[path] = (raw, path.stat().st_mode, path.stat().st_mtime_ns)
    return SimpleNamespace(
        root=root,
        output=tmp_path / "inventory",
        edition={
            "tenant_id": "synthetic",
            "edition_id": str(uuid4()),
            "roster_slug": "ctt-synthetic",
        },
        profile=dict(
            version=RENDER_VERSION,
            dpi=72,
            color="RGB",
            max_page_pixels=25_000_000,
            max_render_bytes=32 * 1024**2,
        ),
        limit=sum(len(raw[0]) for raw in original.values()),
        originals=original,
    )


def prepare(source, **changes):
    args = dict(
        pdf_root=source.root,
        output_root=source.output,
        edition=source.edition,
        render_profile=source.profile,
        max_total_bytes=source.limit,
    )
    return prepare_inventory(**{**args, **changes})


def unchanged(source):
    for path, (raw, mode, mtime) in source.originals.items():
        assert path.read_bytes() == raw
        assert path.stat().st_mode == mode
        assert path.stat().st_mtime_ns == mtime


def test_offline_inventory_retains_all_originals_pages_and_geometry(
    source, monkeypatch
):
    monkeypatch.setattr(socket, "socket", lambda *a, **k: pytest.fail("network"))
    inventory = prepare(source)
    assert set(inventory) == {"schema", "edition", "render_profile", "pdfs", "pages"}
    assert inventory["schema"] == INVENTORY_SCHEMA
    assert len(inventory["pdfs"]) == 2 and len(inventory["pages"]) == 3
    assert "inventory_sha256" not in inventory
    assert (
        json.loads((source.output / "source-inventory.json").read_bytes()) == inventory
    )
    assert digest(inventory) == hashlib.sha256(canonical(inventory)).hexdigest()
    assert inventory["edition"] == source.edition
    assert [(p["width"], p["height"]) for p in inventory["pages"]] == [
        (612, 792),
        (612, 792),
        (792, 612),
    ]
    for pdf in inventory["pdfs"]:
        raw = (source.output / "originals" / pdf["filename"]).read_bytes()
        assert len(raw) == pdf["bytes"]
        assert hashlib.sha256(raw).hexdigest() == pdf["sha256"]
    for page in inventory["pages"]:
        path = source.output / "renders" / (page["render_sha256"] + ".png")
        assert hashlib.sha256(path.read_bytes()).hexdigest() == page["render_sha256"]
        with Image.open(path) as image:
            assert image.mode == "RGB" and image.size == (page["width"], page["height"])
    for path in [source.output, *source.output.rglob("*")]:
        assert path.stat().st_mode & 0o077 == 0
    unchanged(source)


def test_verified_snapshot_replays_original_pages_and_is_frozen(source):
    inventory = prepare(source)
    with InventorySourceAdapter(source.output).open_verified_inventory(
        inventory
    ) as snap:
        assert snap.inventory_sha256 == digest(inventory)
        assert isinstance(snap.pages, tuple)
        page = snap.pages[0]
        with pytest.raises(TypeError):
            page["width"] = 1
        raw = snap.page_bytes(page["pdf_sha256"], page["pdf_page"])
        assert hashlib.sha256(raw).hexdigest() == page["render_sha256"]
        assert "first source page" in snap.native_text(page["pdf_sha256"], 1)
        snap.verify_unchanged()
    unchanged(source)


def test_identical_render_bytes_do_not_drop_physical_pages(tmp_path, source):
    for p in source.root.iterdir():
        p.unlink()
    with fitz.open() as doc:
        doc.new_page()
        doc.new_page()
        (source.root / "blank.pdf").write_bytes(doc.tobytes())
    inventory = prepare(source, max_total_bytes=100_000)
    assert len(inventory["pages"]) == 2
    assert len({p["render_sha256"] for p in inventory["pages"]}) == 1
    assert len(list((source.output / "renders").iterdir())) == 1
    with InventorySourceAdapter(source.output).open_verified_inventory(
        inventory
    ) as snap:
        assert len(snap.pages) == 2


@pytest.mark.parametrize(
    "fault",
    [
        "missing_page",
        "duplicate_page",
        "foreign_page",
        "page_order",
        "pdf_order",
        "unknown_field",
        "extra_page",
        "duplicate_pdf",
        "extra_pdf",
        "negative_bytes",
        "boolean_bytes",
        "invalid_hash",
        "bad_name",
        "bad_uuid",
        "unknown_edition",
        "bad_slug",
        "huge_page",
        "boolean_page",
        "bad_schema",
        "missing_page_key",
    ],
)
def test_inventory_contract_rejects_changed_census_and_untrusted_fields(source, fault):
    value = prepare(source)
    if fault == "missing_page":
        value["pages"].pop()
    elif fault == "duplicate_page":
        value["pages"][1] = copy.deepcopy(value["pages"][0])
    elif fault == "foreign_page":
        value["pages"][0]["pdf_sha256"] = "f" * 64
    elif fault == "page_order":
        value["pages"].reverse()
    elif fault == "pdf_order":
        value["pdfs"].reverse()
    elif fault == "unknown_field":
        value["teams"] = []
    elif fault == "extra_page":
        value["pages"].append(value["pages"][0])
    elif fault == "duplicate_pdf":
        value["pdfs"][1] = copy.deepcopy(value["pdfs"][0])
    elif fault == "extra_pdf":
        value["pdfs"] *= 17
    elif fault == "negative_bytes":
        value["pdfs"][0]["bytes"] = -1
    elif fault == "boolean_bytes":
        value["pdfs"][0]["bytes"] = True
    elif fault == "invalid_hash":
        value["pages"][0]["render_sha256"] = "invalid"
    elif fault == "bad_name":
        value["pdfs"][0]["filename"] = "../outside.pdf"
    elif fault == "bad_uuid":
        value["edition"]["edition_id"] = "invalid"
    elif fault == "unknown_edition":
        value["edition"]["phase"] = "NACIONAL"
    elif fault == "bad_slug":
        value["edition"]["tenant_id"] = "../tenant"
    elif fault == "huge_page":
        value["pages"][0]["width"] = 9000
    elif fault == "boolean_page":
        value["pages"][0]["pdf_page"] = True
    elif fault == "bad_schema":
        value["schema"] = "ctt.registration.source-manifest.v2"
    else:
        value["pages"][0].pop("width")
    with pytest.raises(ValueError):
        validate_inventory(value)


@pytest.mark.parametrize(
    "fault",
    [
        "version",
        "dpi_low",
        "dpi_high",
        "boolean_dpi",
        "color",
        "pixels",
        "bytes",
        "unknown",
        "missing",
    ],
)
def test_render_profile_must_be_explicit_bounded_and_versioned_before_output(
    source, fault
):
    profile = copy.deepcopy(source.profile)
    if fault == "version":
        profile["version"] = "unpinned"
    elif fault == "dpi_low":
        profile["dpi"] = 71
    elif fault == "dpi_high":
        profile["dpi"] = 301
    elif fault == "boolean_dpi":
        profile["dpi"] = True
    elif fault == "color":
        profile["color"] = "RGBA"
    elif fault == "pixels":
        profile["max_page_pixels"] = 25_000_001
    elif fault == "bytes":
        profile["max_render_bytes"] = 0
    elif fault == "unknown":
        profile["accept_any_size"] = True
    else:
        profile.pop("dpi")
    with pytest.raises(ValueError):
        prepare(source, render_profile=profile)
    assert not source.output.exists()


@pytest.mark.parametrize(
    "fault",
    [
        "original",
        "render",
        "inventory",
        "extra_original",
        "extra_render",
        "source_swap",
        "render_symlink",
        "private_mode",
    ],
)
def test_adapter_rejects_changed_or_extra_private_files_before_yield(
    source, tmp_path, fault
):
    inventory = prepare(source)
    pdf = inventory["pdfs"][0]
    original = source.output / "originals" / pdf["filename"]
    render = (
        source.output / "renders" / (inventory["pages"][0]["render_sha256"] + ".png")
    )
    if fault == "original":
        original.write_bytes(b"changed")
    elif fault == "render":
        render.write_bytes(b"changed")
    elif fault == "inventory":
        (source.output / "source-inventory.json").write_bytes(b"{}")
    elif fault == "extra_original":
        (source.output / "originals" / "extra.pdf").write_bytes(b"extra")
    elif fault == "extra_render":
        (source.output / "renders" / "extra.png").write_bytes(b"extra")
    elif fault == "source_swap":
        raw = original.read_bytes()
        original.unlink()
        original.write_bytes(raw + b" ")
    elif fault == "render_symlink":
        target = tmp_path / "render.png"
        target.write_bytes(render.read_bytes())
        render.unlink()
        render.symlink_to(target)
    else:
        original.chmod(0o644)
    with pytest.raises((ValueError, OSError)):
        with InventorySourceAdapter(source.output).open_verified_inventory(inventory):
            pytest.fail("changed snapshot yielded")


@pytest.mark.parametrize(
    "fault", ["original", "render", "inventory", "replace", "extra"]
)
def test_snapshot_detects_mutation_during_body_even_when_caller_raises(source, fault):
    inventory = prepare(source)
    original = source.output / "originals" / inventory["pdfs"][0]["filename"]
    render = (
        source.output / "renders" / (inventory["pages"][0]["render_sha256"] + ".png")
    )
    with pytest.raises(ValueError, match="BATCH_INVENTORY_CHANGED"):
        with InventorySourceAdapter(source.output).open_verified_inventory(inventory):
            if fault == "original":
                original.write_bytes(original.read_bytes() + b" ")
            elif fault == "render":
                render.write_bytes(render.read_bytes() + b" ")
            elif fault == "inventory":
                (source.output / "source-inventory.json").write_bytes(b"{}")
            elif fault == "replace":
                raw = original.read_bytes()
                original.unlink()
                original.write_bytes(raw)
            else:
                (source.output / "renders" / "extra.png").write_bytes(b" ")
            raise RuntimeError("caller failed")


@pytest.mark.parametrize("method", ["page_bytes", "native_text"])
def test_unknown_source_ref_is_rejected(source, method):
    inventory = prepare(source)
    with InventorySourceAdapter(source.output).open_verified_inventory(
        inventory
    ) as snap:
        for page in (0, True, 5000):
            with pytest.raises(ValueError):
                getattr(snap, method)(inventory["pdfs"][0]["sha256"], page)
        with pytest.raises(ValueError):
            getattr(snap, method)("f" * 64, 1)


@pytest.mark.parametrize(
    "fault",
    [
        "source_symlink",
        "source_hardlink",
        "input_directory_symlink",
        "output_parent_symlink",
        "relative_output",
        "dotdot_output",
        "not_pdf",
        "duplicate_pdf",
        "byte_limit",
        "pixel_limit",
        "render_limit",
    ],
)
def test_preparation_failures_never_publish_inventory_or_mutate_originals(
    source, tmp_path, fault
):
    args = {}
    first = next(iter(source.originals))
    if fault == "source_symlink":
        target = tmp_path / "outside.pdf"
        target.write_bytes(first.read_bytes())
        first.unlink()
        first.symlink_to(target)
    elif fault == "source_hardlink":
        os.link(first, tmp_path / "outside.pdf")
    elif fault == "input_directory_symlink":
        link = tmp_path / "link"
        link.symlink_to(source.root, target_is_directory=True)
        args["pdf_root"] = link
    elif fault == "output_parent_symlink":
        link = tmp_path / "link"
        link.symlink_to(tmp_path, target_is_directory=True)
        args["output_root"] = link / "escaped"
    elif fault == "relative_output":
        args["output_root"] = Path("relative")
    elif fault == "dotdot_output":
        args["output_root"] = tmp_path / ".." / "escaped"
    elif fault == "not_pdf":
        first.write_bytes(b"not a pdf")
    elif fault == "duplicate_pdf":
        (source.root / "duplicate.pdf").write_bytes(first.read_bytes())
        args["max_total_bytes"] = source.limit * 2
    elif fault == "byte_limit":
        args["max_total_bytes"] = source.limit - 1
    elif fault == "pixel_limit":
        args["render_profile"] = {**source.profile, "max_page_pixels": 10}
    else:
        args["render_profile"] = {**source.profile, "max_render_bytes": 10}
    with pytest.raises((ValueError, OSError, fitz.FileDataError)):
        prepare(source, **args)
    assert not (source.output / "source-inventory.json").exists()
    assert not (tmp_path / "escaped").exists()
    if fault not in ("not_pdf", "source_symlink"):
        unchanged(source)


def test_duplicate_output_cannot_overwrite_retained_files(source):
    prepare(source)
    before = {p: p.read_bytes() for p in source.output.rglob("*") if p.is_file()}
    with pytest.raises(FileExistsError):
        prepare(source)
    assert before == {
        p: p.read_bytes() for p in source.output.rglob("*") if p.is_file()
    }


def test_pdf_and_total_page_limits_are_complete_census_constraints(source, monkeypatch):
    monkeypatch.setattr(
        module, "_open_pdf", lambda *_: pytest.fail("must reject before parse")
    )
    for n in range(17):
        (source.root / f"extra{n}.pdf").write_bytes(b"pdf")
    with pytest.raises(ValueError):
        prepare(source)
    assert not source.output.exists()


def test_adapter_rejects_consistent_fake_render_hash_even_when_manifest_matches(source):
    inventory = prepare(source)
    page = inventory["pages"][0]
    old = source.output / "renders" / (page["render_sha256"] + ".png")
    raw = old.read_bytes() + b"appended"
    sha = hashlib.sha256(raw).hexdigest()
    old.unlink()
    replacement = source.output / "renders" / (sha + ".png")
    replacement.write_bytes(raw)
    replacement.chmod(0o600)
    page["render_sha256"] = sha
    (source.output / "source-inventory.json").write_bytes(canonical(inventory))
    with pytest.raises(ValueError, match="BATCH_INVENTORY_CHANGED"):
        with InventorySourceAdapter(source.output).open_verified_inventory(inventory):
            pass


@pytest.mark.parametrize("fault", ["original", "extra_pdf", "copied_pdf", "render"])
def test_mutation_during_preparation_prevents_inventory_publication(
    source, monkeypatch, fault
):
    original_render = module._render
    changed = False

    def render(*args):
        nonlocal changed
        result = original_render(*args)
        if not changed:
            changed = True
            if fault == "original":
                path = next(iter(source.originals))
                path.write_bytes(path.read_bytes() + b" ")
            elif fault == "extra_pdf":
                (source.root / "late.pdf").write_bytes(b"late")
            elif fault == "copied_pdf":
                path = source.output / "originals" / next(iter(source.originals)).name
                path.write_bytes(path.read_bytes()[:-1] + b"X")
        return result

    monkeypatch.setattr(module, "_render", render)
    if fault == "render":
        original_write = module._write_blob

        def write(folder, name, raw):
            original_write(folder, name, raw)
            if name.endswith(".png"):
                path = source.output / "renders" / name
                path.write_bytes(raw + b" ")

        monkeypatch.setattr(module, "_write_blob", write)
    with pytest.raises(ValueError, match="BATCH_INVENTORY_CHANGED"):
        prepare(source)
    assert not (source.output / "source-inventory.json").exists()


def test_directory_privacy_is_rechecked_before_each_read(source):
    inventory = prepare(source)
    with pytest.raises(ValueError):
        with InventorySourceAdapter(source.output).open_verified_inventory(
            inventory
        ) as snap:
            (source.output / "renders").chmod(0o755)
            page = snap.pages[0]
            snap.page_bytes(page["pdf_sha256"], page["pdf_page"])


def test_render_descriptor_use_stays_constant_during_prepare_and_reads(
    source, monkeypatch
):
    original_open = module._open_source
    renders = []

    def tracked_open(folder, name, stack):
        stream = original_open(folder, name, stack)
        if name.endswith(".png"):
            renders.append(stream)
            assert sum(not opened.closed for opened in renders) == 1
        return stream

    monkeypatch.setattr(module, "_open_source", tracked_open)
    inventory = prepare(source)
    assert len(renders) >= 3 and all(stream.closed for stream in renders)
    with InventorySourceAdapter(source.output).open_verified_inventory(
        inventory
    ) as snap:
        assert all(stream.closed for stream in renders)
        for page in snap.pages:
            snap.page_bytes(page["pdf_sha256"], page["pdf_page"])
        assert all(stream.closed for stream in renders)
