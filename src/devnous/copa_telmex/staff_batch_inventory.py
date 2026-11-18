"""Private original-page inventory for observation, without capture authority.

The full canonical inventory bytes are its external identity. Originals and
renders remain in an owner-private snapshot; no model output can admit them.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
import stat
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from types import MappingProxyType
from typing import Any, BinaryIO, Iterator, Mapping, NoReturn, cast

import fitz
from PIL import Image

from .staff_batch_contracts import (
    BatchContractError,
    canonical,
    canonical_uuid,
    sha256_hex,
    strict_loads,
)
from .staff_source_admission import _check_directory, _open_root, _open_source

INVENTORY_SCHEMA = "ctt.batch.source-inventory.v1"
RENDER_VERSION = f"pymupdf-{fitz.VersionBind}-rgb-png.v1"
MAX_TOTAL_BYTES = 1024**3
_SLUG = re.compile(r"[a-z0-9][a-z0-9_-]{0,79}\Z")


def _deny(code: str = "BATCH_INVENTORY_INVALID") -> NoReturn:
    raise BatchContractError(code)


def _filename(value):
    if (
        type(value) is not str
        or not 1 <= len(value.encode("utf-8")) <= 255
        or "/" in value
        or "\\" in value
        or any(ord(c) < 32 or ord(c) == 127 for c in value)
        or Path(value).suffix.lower() != ".pdf"
    ):
        _deny()
    return value


def _edition(value):
    if type(value) is not dict or set(value) != {
        "tenant_id",
        "edition_id",
        "roster_slug",
    }:
        _deny()
    canonical_uuid(value["edition_id"])
    if any(
        type(value[k]) is not str or not _SLUG.fullmatch(value[k])
        for k in ("tenant_id", "roster_slug")
    ):
        _deny()
    return dict(value)


def _render_profile(value):
    if type(value) is not dict or set(value) != {
        "version",
        "dpi",
        "color",
        "max_page_pixels",
        "max_render_bytes",
    }:
        _deny()
    if (
        value["version"] != RENDER_VERSION
        or value["color"] != "RGB"
        or type(value["dpi"]) is not int
        or not 72 <= value["dpi"] <= 300
        or type(value["max_page_pixels"]) is not int
        or not 1 <= value["max_page_pixels"] <= 25_000_000
        or type(value["max_render_bytes"]) is not int
        or not 1 <= value["max_render_bytes"] <= 32 * 1024**2
    ):
        _deny()
    return dict(value)


def validate_inventory(value: dict) -> dict:
    """Validate the exact, authority-free census; return an independent copy."""
    if (
        type(value) is not dict
        or set(value) != {"schema", "edition", "render_profile", "pdfs", "pages"}
        or value["schema"] != INVENTORY_SCHEMA
    ):
        _deny()
    _edition(value["edition"])
    profile = _render_profile(value["render_profile"])
    pdfs, pages = value["pdfs"], value["pages"]
    if (
        type(pdfs) is not list
        or not 1 <= len(pdfs) <= 16
        or type(pages) is not list
        or not 1 <= len(pages) <= 1200
    ):
        _deny()
    names: set[str] = set()
    hashes: set[str] = set()
    expected: list[tuple[str, int]] = []
    total = 0
    for pdf in pdfs:
        if type(pdf) is not dict or set(pdf) != {
            "filename",
            "sha256",
            "bytes",
            "page_count",
        }:
            _deny()
        name, sha = _filename(pdf["filename"]), sha256_hex(pdf["sha256"])
        if (
            name in names
            or sha in hashes
            or type(pdf["bytes"]) is not int
            or not 1 <= pdf["bytes"] <= MAX_TOTAL_BYTES
            or type(pdf["page_count"]) is not int
            or not 1 <= pdf["page_count"] <= 1200
        ):
            _deny()
        total += pdf["bytes"]
        names.add(name)
        hashes.add(sha)
        expected.extend((sha, n) for n in range(1, pdf["page_count"] + 1))
    if total > MAX_TOTAL_BYTES or len(expected) != len(pages):
        _deny()
    for ref, page in zip(expected, pages):
        if type(page) is not dict or set(page) != {
            "pdf_sha256",
            "pdf_page",
            "render_sha256",
            "width",
            "height",
        }:
            _deny()
        sha256_hex(page["render_sha256"])
        if (
            (page["pdf_sha256"], page["pdf_page"]) != ref
            or type(page["pdf_page"]) is not int
            or any(
                type(page[k]) is not int or not 1 <= page[k] <= 8000
                for k in ("width", "height")
            )
            or page["width"] * page["height"] > profile["max_page_pixels"]
        ):
            _deny()
    return cast(dict, strict_loads(canonical(value), 2 * 1024**2))


def _walk(path, stack):
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts:
        _deny()
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    stack.callback(os.close, fd)
    for name in path.parts[1:]:
        fd = os.open(
            name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd
        )
        stack.callback(os.close, fd)
    return fd


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _stream_hash(stream, limit):
    previous = stream.tell()
    try:
        stream.seek(0)
        hasher, total = hashlib.sha256(), 0
        while data := stream.read(1024 * 1024):
            total += len(data)
            if total > limit:
                _deny("BATCH_INVENTORY_LIMIT")
            hasher.update(data)
        return hasher.hexdigest(), total
    finally:
        stream.seek(previous)


def _write_blob(folder, name, raw):
    fd = os.open(
        name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
        0o600,
        dir_fd=folder,
    )
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def _open_pdf(stream):
    # Reopen the pinned inode, never a mutable caller-chosen path.
    document = fitz.open(f"/proc/self/fd/{stream.fileno()}")
    if not document.is_pdf or document.is_encrypted or not 1 <= len(document) <= 1200:
        document.close()
        _deny()
    return document


def _render(document, number, profile):
    page = document[number - 1]
    scale = profile["dpi"] / 72
    width, height = math.ceil(page.rect.width * scale), math.ceil(
        page.rect.height * scale
    )
    if (
        width < 1
        or height < 1
        or max(width, height) > 8000
        or width * height > profile["max_page_pixels"]
    ):
        _deny("BATCH_INVENTORY_LIMIT")
    pix = page.get_pixmap(dpi=profile["dpi"], colorspace=fitz.csRGB, alpha=False)
    raw = pix.tobytes("png")
    if (
        len(raw) > profile["max_render_bytes"]
        or pix.width * pix.height > profile["max_page_pixels"]
    ):
        _deny("BATCH_INVENTORY_LIMIT")
    return raw, pix.width, pix.height


def prepare_inventory(
    *, pdf_root, output_root, edition, render_profile, max_total_bytes
):
    """Copy exact originals and retain every rendered page, entirely offline."""
    edition, profile = _edition(edition), _render_profile(render_profile)
    if type(max_total_bytes) is not int or not 1 <= max_total_bytes <= MAX_TOTAL_BYTES:
        _deny("BATCH_INVENTORY_LIMIT")
    with ExitStack() as stack:
        source = _walk(pdf_root, stack)
        names = sorted(
            n for n in os.listdir(source) if Path(n).suffix.lower() == ".pdf"
        )
        if not 1 <= len(names) <= 16:
            _deny()
        for name in names:
            _filename(name)
        output = Path(output_root)
        parent = _walk(output.parent, stack)
        if output.name in ("", ".", ".."):
            _deny()
        os.mkdir(output.name, 0o700, dir_fd=parent)
        root = _open_root(output, stack)
        folders = {}
        for name in ("originals", "renders"):
            os.mkdir(name, 0o700, dir_fd=root)
            fd = os.open(
                name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root
            )
            stack.callback(os.close, fd)
            folders[name] = fd
        inventory: dict[str, Any] = dict(
            schema=INVENTORY_SCHEMA,
            edition=edition,
            render_profile=profile,
            pdfs=[],
            pages=[],
        )
        originals, copies, total = {}, {}, 0
        for name in names:
            fd = os.open(
                name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=source
            )
            stream = stack.enter_context(os.fdopen(fd, "rb"))
            before = os.fstat(fd)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_size < 1
                or before.st_nlink != 1
            ):
                _deny()
            total += before.st_size
            if total > max_total_bytes:
                _deny("BATCH_INVENTORY_LIMIT")
            originals[name] = (stream, _identity(before))
            out_fd = os.open(
                name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=folders["originals"],
            )
            hasher, copied = hashlib.sha256(), 0
            with os.fdopen(out_fd, "wb") as target:
                while data := stream.read(1024 * 1024):
                    copied += len(data)
                    if copied > before.st_size:
                        _deny("BATCH_INVENTORY_CHANGED")
                    hasher.update(data)
                    target.write(data)
                target.flush()
                os.fsync(target.fileno())
            if copied != before.st_size or _identity(os.fstat(fd)) != _identity(before):
                _deny("BATCH_INVENTORY_CHANGED")
            sha = hasher.hexdigest()
            if sha in {p["sha256"] for p in inventory["pdfs"]}:
                _deny("BATCH_INVENTORY_DUPLICATE_PDF")
            copied_stream = _open_source(folders["originals"], name, stack)
            copies[sha] = copied_stream
            with _open_pdf(copied_stream) as document:
                inventory["pdfs"].append(
                    dict(
                        filename=name,
                        sha256=sha,
                        bytes=copied,
                        page_count=len(document),
                    )
                )
                for number in range(1, len(document) + 1):
                    if len(inventory["pages"]) == 1200:
                        _deny("BATCH_INVENTORY_LIMIT")
                    raw, width, height = _render(document, number, profile)
                    render_hash = hashlib.sha256(raw).hexdigest()
                    try:
                        _write_blob(folders["renders"], render_hash + ".png", raw)
                    except FileExistsError:
                        with ExitStack() as temporary:
                            existing = _open_source(
                                folders["renders"], render_hash + ".png", temporary
                            )
                            if existing.read(profile["max_render_bytes"] + 1) != raw:
                                _deny("BATCH_INVENTORY_CHANGED")
                    inventory["pages"].append(
                        dict(
                            pdf_sha256=sha,
                            pdf_page=number,
                            render_sha256=render_hash,
                            width=width,
                            height=height,
                        )
                    )
        if names != sorted(
            n for n in os.listdir(source) if Path(n).suffix.lower() == ".pdf"
        ):
            _deny("BATCH_INVENTORY_CHANGED")
        for name, (stream, identity) in originals.items():
            if (
                _identity(os.fstat(stream.fileno())) != identity
                or _identity(os.stat(name, dir_fd=source, follow_symlinks=False))
                != identity
            ):
                _deny("BATCH_INVENTORY_CHANGED")
            expected = next(p for p in inventory["pdfs"] if p["filename"] == name)
            if _stream_hash(stream, expected["bytes"]) != (
                expected["sha256"],
                expected["bytes"],
            ):
                _deny("BATCH_INVENTORY_CHANGED")
        for pdf in inventory["pdfs"]:
            if _stream_hash(copies[pdf["sha256"]], pdf["bytes"]) != (
                pdf["sha256"],
                pdf["bytes"],
            ):
                _deny("BATCH_INVENTORY_CHANGED")
        for sha in {p["render_sha256"] for p in inventory["pages"]}:
            with ExitStack() as temporary:
                stream = _open_source(folders["renders"], sha + ".png", temporary)
                if _stream_hash(stream, profile["max_render_bytes"])[0] != sha:
                    _deny("BATCH_INVENTORY_CHANGED")
        inventory = validate_inventory(inventory)
        _write_blob(root, "source-inventory.json", canonical(inventory))
        return inventory


@dataclass(frozen=True, repr=False)
class InventorySnapshot:
    """Pinned read-only originals and renders; no admission or authority flags."""

    inventory_bytes: bytes = field(repr=False)
    pages: tuple[Mapping, ...]
    _originals: Mapping[str, BinaryIO] = field(repr=False)
    _renders: Mapping[str, str] = field(repr=False)
    _documents: Mapping[str, fitz.Document] = field(repr=False)
    _folders: Mapping[str, int] = field(repr=False)
    _identities: Mapping[tuple[str, str], tuple[int, ...]] = field(repr=False)

    @property
    def inventory_sha256(self) -> str:
        return hashlib.sha256(self.inventory_bytes).hexdigest()

    def _check_stats(self):
        for folder_fd in self._folders.values():
            _check_directory(folder_fd)
        inventory = strict_loads(self.inventory_bytes, 2 * 1024**2)
        expected_names = {p["filename"] for p in inventory["pdfs"]}
        expected_renders = {p["render_sha256"] + ".png" for p in inventory["pages"]}
        if (
            set(os.listdir(self._folders["originals"])) != expected_names
            or set(os.listdir(self._folders["renders"])) != expected_renders
        ):
            _deny("BATCH_INVENTORY_CHANGED")
        for (folder, name), identity in self._identities.items():
            current = os.stat(name, dir_fd=self._folders[folder], follow_symlinks=False)
            if _identity(current) != identity or not stat.S_ISREG(current.st_mode):
                _deny("BATCH_INVENTORY_CHANGED")

    def verify_unchanged(self) -> None:
        self._check_stats()
        inventory = strict_loads(self.inventory_bytes, 2 * 1024**2)
        for pdf in inventory["pdfs"]:
            if _stream_hash(self._originals[pdf["sha256"]], pdf["bytes"]) != (
                pdf["sha256"],
                pdf["bytes"],
            ):
                _deny("BATCH_INVENTORY_CHANGED")
        for sha in self._renders:
            self._read_render(sha, inventory["render_profile"]["max_render_bytes"])
        self._check_stats()

    def _read_render(self, sha: str, limit: int) -> bytes:
        # Hash and inode are checked for every bounded read without retaining
        # up to 1,200 open render descriptors throughout an API request.
        name = self._renders[sha]
        with ExitStack() as temporary:
            stream = _open_source(self._folders["renders"], name, temporary)
            identity = self._identities[("renders", name)]
            if _identity(os.fstat(stream.fileno())) != identity:
                _deny("BATCH_INVENTORY_CHANGED")
            raw = stream.read(limit + 1)
            if type(raw) is not bytes:
                _deny()
            if (
                len(raw) > limit
                or hashlib.sha256(raw).hexdigest() != sha
                or _identity(os.fstat(stream.fileno())) != identity
            ):
                _deny("BATCH_INVENTORY_CHANGED")
            return raw

    def page_bytes(self, pdf_sha256: str, pdf_page: int) -> bytes:
        self._check_stats()
        page = next(
            (
                p
                for p in self.pages
                if (p["pdf_sha256"], p["pdf_page"]) == (pdf_sha256, pdf_page)
            ),
            None,
        )
        if page is None or type(pdf_page) is not int:
            _deny()
        inventory = strict_loads(self.inventory_bytes, 2 * 1024**2)
        raw = self._read_render(
            page["render_sha256"], inventory["render_profile"]["max_render_bytes"]
        )
        self._check_stats()
        return raw

    def native_text(self, pdf_sha256: str, pdf_page: int) -> str:
        self._check_stats()
        if type(pdf_page) is not int or not any(
            p["pdf_sha256"] == pdf_sha256 and p["pdf_page"] == pdf_page
            for p in self.pages
        ):
            _deny()
        result = self._documents[pdf_sha256][pdf_page - 1].get_text("text")
        if type(result) is not str:
            _deny()
        self._check_stats()
        return result


class InventorySourceAdapter:
    """Open a pinned private inventory and reproduce its renders from originals."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    @contextmanager
    def open_verified_inventory(self, inventory: dict) -> Iterator[InventorySnapshot]:
        inventory = validate_inventory(inventory)
        raw_inventory = canonical(inventory)
        with ExitStack() as stack:
            root = _open_root(self.root, stack)
            folders = {}
            for name in ("originals", "renders"):
                fd = os.open(
                    name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root
                )
                stack.callback(os.close, fd)
                _check_directory(fd)
                folders[name] = fd
            inventory_file = _open_source(root, "source-inventory.json", stack)
            if inventory_file.read(2 * 1024**2 + 1) != raw_inventory:
                _deny("BATCH_INVENTORY_CHANGED")
            originals, renders, documents = {}, {}, {}
            identities = {
                ("root", "source-inventory.json"): _identity(
                    os.fstat(inventory_file.fileno())
                )
            }
            folders["root"] = root
            for pdf in inventory["pdfs"]:
                stream = _open_source(folders["originals"], pdf["filename"], stack)
                originals[pdf["sha256"]] = stream
                identities[("originals", pdf["filename"])] = _identity(
                    os.fstat(stream.fileno())
                )
                if _stream_hash(stream, pdf["bytes"]) != (pdf["sha256"], pdf["bytes"]):
                    _deny("BATCH_INVENTORY_CHANGED")
                document = stack.enter_context(_open_pdf(stream))
                if len(document) != pdf["page_count"]:
                    _deny("BATCH_INVENTORY_CHANGED")
                documents[pdf["sha256"]] = document
            for page in inventory["pages"]:
                sha = page["render_sha256"]
                if sha not in renders:
                    with ExitStack() as temporary:
                        stream = _open_source(
                            folders["renders"], sha + ".png", temporary
                        )
                        renders[sha] = sha + ".png"
                        identities[("renders", sha + ".png")] = _identity(
                            os.fstat(stream.fileno())
                        )
            snapshot = InventorySnapshot(
                raw_inventory,
                tuple(MappingProxyType(dict(p)) for p in inventory["pages"]),
                MappingProxyType(originals),
                MappingProxyType(renders),
                MappingProxyType(documents),
                MappingProxyType(folders),
                MappingProxyType(identities),
            )
            snapshot.verify_unchanged()
            for page in inventory["pages"]:
                raw, width, height = _render(
                    documents[page["pdf_sha256"]],
                    page["pdf_page"],
                    inventory["render_profile"],
                )
                if hashlib.sha256(raw).hexdigest() != page["render_sha256"] or (
                    width,
                    height,
                ) != (page["width"], page["height"]):
                    _deny("BATCH_INVENTORY_CHANGED")
                with Image.open(
                    BytesIO(snapshot.page_bytes(page["pdf_sha256"], page["pdf_page"]))
                ) as image:
                    if (
                        image.format != "PNG"
                        or image.mode != "RGB"
                        or image.size != (width, height)
                    ):
                        _deny("BATCH_INVENTORY_CHANGED")
            snapshot.verify_unchanged()
            try:
                yield snapshot
            finally:
                snapshot.verify_unchanged()
