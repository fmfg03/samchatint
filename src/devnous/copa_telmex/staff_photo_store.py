"""Content-addressed review photos; preparation alone never publishes them.

The HTTP reader must additionally require a committed protected capture binding.
Paths are fixed before the review plan is signed; no postcommit renaming is used.
"""

from dataclasses import dataclass, field
import hashlib
from io import BytesIO
import os
from pathlib import Path
import re
import secrets
import stat
from typing import NoReturn

from PIL import Image

from devnous.tournaments.core.ocr_integrity import average_hash_hex
from devnous.tournaments.core.ctt_batch_photo_alignment import (
    extract_batch_photo,
    parse_batch_slot,
    prepare_batch_page,
)

from .staff_capture_executor import CaptureDenied
from .staff_capture_geometry import verify_staff_geometry
from .staff_review_challenge import parse_canonical_scope

_NAME = re.compile(r"([0-9a-f]{64})\.jpg\Z")
_PATH = re.compile(r"ctt-photo:([0-9a-f]{64})\Z")


def _deny() -> NoReturn:
    raise CaptureDenied("photo source unavailable") from None


@dataclass(frozen=True, repr=False)
class PreparedReviewPhoto:
    data: bytes = field(repr=False)
    sha256: str
    ahash: str

    @property
    def photo_path(self) -> str:
        return "ctt-photo:" + self.sha256


def encode_review_photo(image: Image.Image) -> PreparedReviewPhoto:
    """Use the existing review JPEG and average-hash algorithms."""
    try:
        if (
            image.width < 40
            or image.height < 40
            or image.width * image.height > 25_000_000
        ):
            _deny()
        rgb = image.convert("RGB")
        output = BytesIO()
        rgb.save(output, format="JPEG", quality=90)
        raw = output.getvalue()
        return PreparedReviewPhoto(
            raw, hashlib.sha256(raw).hexdigest(), average_hash_hex(rgb)
        )
    except Exception:
        _deny()


def derive_rectangular_review_photo(page_bytes, geometry, *, max_page_bytes):
    """Derive a candidate from its exact source rectangle; not identity proof."""
    try:
        source_hash = "sha256:" + hashlib.sha256(page_bytes).hexdigest()
        if not verify_staff_geometry(
            {"source_page_hash": source_hash},
            geometry,
            page_bytes,
            max_page_bytes=max_page_bytes,
        ):
            _deny()
        coords = geometry["field_coordinates"]
        x, y, width, height = (coords[key] for key in ("x", "y", "width", "height"))
        with Image.open(BytesIO(page_bytes)) as image:
            return encode_review_photo(image.crop((x, y, x + width, y + height)))
    except Exception:
        _deny()


def derive_batch_review_photo(snapshot, slot_ref: str) -> PreparedReviewPhoto:
    """Keep the already used, conservative printed-slot crop algorithm."""
    try:
        snapshot.verify_unchanged()
        scope = parse_canonical_scope(snapshot.scope_bytes)
        parsed = parse_batch_slot(
            slot_ref, [page.pdf_page for page in scope.source_pages]
        )
        if parsed is None:
            _deny()
        page_index, slot = parsed
        page = next(
            page for page in scope.source_pages if page.page_index == page_index
        )
        with Image.open(BytesIO(snapshot.page_assets[page.asset_id])) as source:
            image = source.convert("RGB")
        grid = prepare_batch_page(image, page_index)
        crop, status = extract_batch_photo(
            image, page_index=page_index, slot=slot, prepared=grid
        )
        if status != "photo" or crop is None:
            _deny()
        return encode_review_photo(crop)
    except Exception:
        _deny()


class PrivateReviewPhotoStore:
    """A sealed objects directory; no role, directory or reference is created."""

    def __init__(self, root: Path, *, max_photo_bytes=4 * 1024 * 1024):
        self.root = Path(root)
        if (
            not self.root.is_absolute()
            or ".." in self.root.parts
            or type(max_photo_bytes) is not int
            or max_photo_bytes < 1
            or self.root.resolve() != self.root
            or self.root.is_relative_to(Path(__file__).resolve().parents[3])
        ):
            _deny()
        self.max_photo_bytes = max_photo_bytes

    def _open_root(self):
        # Pin every directory component. NOFOLLOW on just the last component
        # would still permit replacing an ancestor with a symlink after init.
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        fd = os.open(self.root.anchor, flags)
        try:
            for component in self.root.parts[1:]:
                child = os.open(component, flags, dir_fd=fd)
                os.close(fd)
                fd = child
            info = os.fstat(fd)
            if stat.S_IMODE(info.st_mode) not in (0o700, 0o750):
                _deny()
            return fd, info
        except Exception:
            os.close(fd)
            raise

    def read(self, filename: str) -> bytes:
        match = _NAME.fullmatch(filename)
        if match is None:
            _deny()
        directory = target = None
        try:
            directory, owner = self._open_root()
            target = os.open(
                filename,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=directory,
            )
            info = os.fstat(target)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_uid != owner.st_uid
                or stat.S_IMODE(info.st_mode) not in (0o400, 0o440)
                or not 0 < info.st_size <= self.max_photo_bytes
            ):
                _deny()
            chunks, total = [], 0
            while True:
                chunk = os.read(target, min(65536, self.max_photo_bytes + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > self.max_photo_bytes:
                    _deny()
            raw = b"".join(chunks)
            if hashlib.sha256(raw).hexdigest() != match[1]:
                _deny()
            return raw
        except Exception:
            _deny()
        finally:
            if target is not None:
                os.close(target)
            if directory is not None:
                os.close(directory)

    def stage(self, photo: PreparedReviewPhoto) -> str:
        directory = target = None
        scratch = None
        owned_scratch = False
        try:
            if (
                type(photo) is not PreparedReviewPhoto
                or type(photo.data) is not bytes
                or not 0 < len(photo.data) <= self.max_photo_bytes
                or hashlib.sha256(photo.data).hexdigest() != photo.sha256
                or _PATH.fullmatch(photo.photo_path) is None
            ):
                _deny()
            with Image.open(BytesIO(photo.data)) as decoded:
                if (
                    decoded.format != "JPEG"
                    or decoded.width < 40
                    or decoded.height < 40
                    or decoded.width * decoded.height > 25_000_000
                ):
                    _deny()
                decoded.verify()
            directory, owner = self._open_root()
            if owner.st_uid != os.geteuid():
                _deny()
            filename = photo.sha256 + ".jpg"
            try:
                self.read(filename)
                return photo.photo_path
            except CaptureDenied:
                try:
                    os.stat(filename, dir_fd=directory, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    _deny()
            scratch = ".pending-" + secrets.token_hex(16)
            target = os.open(
                scratch,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=directory,
            )
            owned_scratch = True
            view = memoryview(photo.data)
            while view:
                count = os.write(target, view)
                if count < 1:
                    _deny()
                view = view[count:]
            if stat.S_IMODE(owner.st_mode) == 0o750:
                os.fchown(target, -1, owner.st_gid)
                os.fchmod(target, 0o440)
            else:
                os.fchmod(target, 0o400)
            os.fsync(target)
            os.close(target)
            target = None
            try:
                os.link(
                    scratch,
                    filename,
                    src_dir_fd=directory,
                    dst_dir_fd=directory,
                    follow_symlinks=False,
                )
            except FileExistsError:
                self.read(filename)
            os.unlink(scratch, dir_fd=directory)
            owned_scratch = False
            scratch = None
            os.fsync(directory)
            self.read(filename)
            return photo.photo_path
        except Exception:
            _deny()
        finally:
            if target is not None:
                os.close(target)
            if owned_scratch and scratch is not None and directory is not None:
                try:
                    os.unlink(scratch, dir_fd=directory)
                except FileNotFoundError:
                    pass
            if directory is not None:
                os.close(directory)

    def verify_effect_photos(self, effect):
        rows = list(effect["players"]) + [
            item["row"] for item in effect["staff"] if item["row"] is not None
        ]
        for row in rows:
            value = row["photo_path"]
            if value is None:
                continue
            match = _PATH.fullmatch(value)
            if match is None:
                _deny()
            raw = self.read(match[1] + ".jpg")
            if (
                "photo_sha256" in row
                and row["photo_sha256"] != hashlib.sha256(raw).hexdigest()
            ):
                _deny()
