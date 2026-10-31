"""Prepare and admit exact private sources under independent authorization.

This is an operator-side candidate, not a browser or team-capture endpoint.
An accredited source approver and pinned signing keys are deployment inputs.
Source admission grants no reviewer identity, field decision or eligibility.
"""

from __future__ import annotations

import base64
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
import fcntl
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import stat
import tempfile
import time
from types import MappingProxyType
from typing import BinaryIO, Mapping, NoReturn
from uuid import UUID

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from .batch_admission import validate_manifest
from .staff_capture_edition import TrustedEditionBinding

_DOMAIN = b"samchat.ctt.source-admission.v1\x00"
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_SLUG = re.compile(r"[a-z0-9][a-z0-9_-]{0,79}\Z")
_KID = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")


class SourceAdmissionDenied(ValueError):
    """A private source or its independent approval was not acceptable."""


def _deny() -> NoReturn:
    raise SourceAdmissionDenied("source admission denied") from None


def _canonical(value) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _json(raw: bytes, limit: int):
    if type(raw) is not bytes or not 0 < len(raw) <= limit:
        _deny()
    depth, quoted, escaped = 0, False, False
    for byte in raw:
        if quoted:
            if escaped:
                escaped = False
            elif byte == 92:
                escaped = True
            elif byte == 34:
                quoted = False
        elif byte == 34:
            quoted = True
        elif byte in (91, 123):
            depth += 1
            if depth > 64:
                _deny()
        elif byte in (93, 125):
            depth -= 1
            if depth < 0:
                _deny()
    if quoted or depth:
        _deny()

    def pairs(items):
        result = {}
        for name, value in items:
            if name in result:
                _deny()
            result[name] = value
        return result

    return json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=pairs,
        parse_constant=lambda _: _deny(),
    )


@dataclass(frozen=True)
class SourceAdmissionLimits:
    max_manifest_bytes: int
    max_pdf_bytes: int
    max_total_pdf_bytes: int
    max_page_bytes: int
    max_total_page_bytes: int
    max_page_pixels: int = 25_000_000

    def __post_init__(self):
        if any(type(value) is not int or value < 1 for value in vars(self).values()):
            _deny()


@dataclass(frozen=True, repr=False)
class PreparedSourceAdmission:
    """Unlinked snapshots, not admitted files or approved business data."""

    plan_bytes: bytes = field(repr=False)
    manifest_bytes: bytes = field(repr=False)
    pdfs: Mapping[str, BinaryIO] = field(repr=False)
    pages: Mapping[str, bytes] = field(repr=False)

    @property
    def plan_sha256(self) -> str:
        return "sha256:" + _digest(self.plan_bytes)


def _check_directory(fd: int) -> None:
    info = os.fstat(fd)
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        _deny()


def _open_root(root: Path, stack: ExitStack) -> int:
    root = Path(root)
    if not root.is_absolute() or ".." in root.parts:
        _deny()
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    stack.callback(os.close, fd)
    for name in root.parts[1:]:
        fd = os.open(
            name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=fd,
        )
        stack.callback(os.close, fd)
    _check_directory(fd)
    return fd


def _open_source(directory: int, name: str, stack: ExitStack):
    fd = os.open(
        name,
        os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
        dir_fd=directory,
    )
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) not in (0o400, 0o600)
            or info.st_nlink != 1
        ):
            _deny()
        stream = os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise
    return stack.enter_context(stream)


def _copy(source: BinaryIO, target: BinaryIO, limit: int) -> tuple[str, int]:
    source.seek(0)
    digest, size = hashlib.sha256(), 0
    while chunk := source.read(1024 * 1024):
        if type(chunk) is not bytes:
            _deny()
        size += len(chunk)
        if size > limit:
            _deny()
        digest.update(chunk)
        target.write(chunk)
    target.flush()
    target.seek(0)
    return digest.hexdigest(), size


@contextmanager
def prepare_source_admission(
    manifest_bytes: bytes,
    source_root: Path,
    binding: TrustedEditionBinding,
    limits: SourceAdmissionLimits,
):
    """Render declared pages from exact originals without publishing sources."""
    stack = ExitStack()
    try:
        try:
            import fitz

            if (
                type(binding) is not TrustedEditionBinding
                or type(limits) is not SourceAdmissionLimits
                or not _SLUG.fullmatch(binding.tenant_id)
                or not isinstance(binding.edition_id, UUID)
                or not isinstance(binding.tournament_id, UUID)
                or type(binding.edition_year) is not int
                or not 1 <= binding.edition_year <= 9999
                or not _SLUG.fullmatch(binding.roster_slug)
            ):
                _deny()
            raw = _json(manifest_bytes, limits.max_manifest_bytes)
            manifest = validate_manifest(
                raw,
                expected_tournament_id=str(binding.tournament_id),
                expected_edition_year=binding.edition_year,
            )
            if (
                manifest["edition"]["roster_slug"] != binding.roster_slug
                or raw.get("manifest_sha256", manifest["manifest_sha256"])
                != manifest["manifest_sha256"]
            ):
                _deny()
            root = _open_root(source_root, stack)
            pdfs, pdf_info, images, documents = {}, {}, {}, []
            filenames: dict[str, str] = {}
            total_pdf = total_pages = 0
            for document in manifest["documents"]:
                sha, name = document["pdf_sha256"], document["pdf"]
                if name in filenames and filenames[name] != sha:
                    _deny()
                if name not in filenames:
                    # Verify each distinct declared basename, including aliases.
                    original = _open_source(root, name, stack)
                    candidate = stack.enter_context(tempfile.TemporaryFile(mode="w+b"))
                    actual, size = _copy(original, candidate, limits.max_pdf_bytes)
                    if not hmac.compare_digest(actual, sha):
                        _deny()
                    if sha not in pdfs:
                        total_pdf += size
                        if total_pdf > limits.max_total_pdf_bytes:
                            _deny()
                        pdfs[sha], pdf_info[sha] = candidate, size
                    else:
                        candidate.close()
                    filenames[name] = sha
                snapshot = pdfs[sha]
                pages = []
                with fitz.open(
                    f"/proc/self/fd/{snapshot.fileno()}", filetype="pdf"
                ) as pdf:
                    if not pdf.is_pdf or pdf.is_encrypted:
                        _deny()
                    for index, number in enumerate(document["pages"], 1):
                        if number > pdf.page_count:
                            _deny()
                        page = pdf.load_page(number - 1)
                        width, height = math.ceil(page.rect.width), math.ceil(
                            page.rect.height
                        )
                        if (
                            min(width, height) < 1
                            or max(width, height) > 8000
                            or width * height > limits.max_page_pixels
                        ):
                            _deny()
                        pix = page.get_pixmap(alpha=False)
                        if pix.width * pix.height > limits.max_page_pixels:
                            _deny()
                        image = pix.tobytes("png")
                        if len(image) > limits.max_page_bytes:
                            _deny()
                        image_hash = _digest(image)
                        if image_hash not in images:
                            total_pages += len(image)
                            if total_pages > limits.max_total_page_bytes:
                                _deny()
                            images[image_hash] = image
                        pages.append(
                            {
                                "page_index": index,
                                "pdf_page": number,
                                "image_sha256": "sha256:" + image_hash,
                            }
                        )
                documents.append(
                    {
                        "document_id": document["document_id"],
                        "pdf_sha256": sha,
                        "payload_sha256": document["payload_sha256"],
                        "source_pages_sha256": document["source_pages_sha256"],
                        "pages": pages,
                    }
                )
            plan = {
                "schema": "ctt.source-admission.plan.v1",
                "tenant_id": binding.tenant_id,
                "edition_id": str(binding.edition_id),
                "tournament_id": str(binding.tournament_id),
                "edition_year": binding.edition_year,
                "roster_slug": binding.roster_slug,
                "manifest_sha256": manifest["manifest_sha256"],
                "documents": documents,
                "pdfs": [
                    {"pdf_sha256": sha, "byte_length": pdf_info[sha]}
                    for sha in sorted(pdf_info)
                ],
            }
            normalized_bytes = _canonical(manifest)
            if len(normalized_bytes) > limits.max_manifest_bytes:
                _deny()
            prepared = PreparedSourceAdmission(
                _canonical(plan),
                normalized_bytes,
                MappingProxyType(pdfs),
                MappingProxyType(images),
            )
        except Exception:
            _deny()
        yield prepared
    finally:
        stack.close()


def sign_source_admission(
    plan_bytes: bytes,
    *,
    admission_id: UUID,
    issued_at: int,
    expires_at: int,
    kid: str,
    private_key: Ed25519PrivateKey,
) -> bytes:
    """Operator-side signer; caller must establish independent accreditation."""
    try:
        if (
            not isinstance(admission_id, UUID)
            or type(issued_at) is not int
            or type(expires_at) is not int
            or issued_at < 0
            or not 0 < expires_at - issued_at <= 300
            or type(plan_bytes) is not bytes
            or not 2 <= len(plan_bytes) <= 1_048_576
            or type(kid) is not str
            or not _KID.fullmatch(kid)
        ):
            _deny()
        body = {
            "schema": "ctt.source-admission.authorization.v1",
            "kid": kid,
            "admission_id": str(admission_id),
            "plan_sha256": "sha256:" + _digest(plan_bytes),
            "issued_at": issued_at,
            "expires_at": expires_at,
        }
        return _canonical(
            {
                "body": body,
                "signature": base64.b64encode(
                    private_key.sign(_DOMAIN + _canonical(body))
                ).decode("ascii"),
            }
        )
    except Exception:
        _deny()


def verify_source_admission(
    plan_bytes: bytes,
    envelope: bytes,
    *,
    public_keys: Mapping[str, bytes],
    now_seconds: int,
) -> dict:
    """Verify a pinned independent signature over this exact source plan."""
    try:
        value = _json(envelope, 8192)
        if type(value) is not dict or set(value) != {"body", "signature"}:
            _deny()
        body = value["body"]
        if type(body) is not dict or set(body) != {
            "schema",
            "kid",
            "admission_id",
            "plan_sha256",
            "issued_at",
            "expires_at",
        }:
            _deny()
        if (
            body["schema"] != "ctt.source-admission.authorization.v1"
            or type(body["issued_at"]) is not int
            or type(body["expires_at"]) is not int
            or type(now_seconds) is not int
            or not 0 <= body["issued_at"] <= now_seconds < body["expires_at"]
            or not 0 < body["expires_at"] - body["issued_at"] <= 300
            or type(body["kid"]) is not str
            or not _KID.fullmatch(body["kid"])
            or body["plan_sha256"] != "sha256:" + _digest(plan_bytes)
            or str(UUID(body["admission_id"])) != body["admission_id"]
            or _canonical(value) != envelope
        ):
            _deny()
        signature = base64.b64decode(value["signature"], validate=True)
        if (
            len(signature) != 64
            or base64.b64encode(signature).decode("ascii") != value["signature"]
        ):
            _deny()
        Ed25519PublicKey.from_public_bytes(public_keys[body["kid"]]).verify(
            signature, _DOMAIN + _canonical(body)
        )
        return dict(body, proof_sha256="sha256:" + _digest(envelope))
    except Exception:
        _deny()


def _matches_file(directory: int, name: str, digest: str, size: int) -> bool:
    with ExitStack() as stack:
        source = _open_source(directory, name, stack)
        actual, total = hashlib.sha256(), 0
        while chunk := source.read(1024 * 1024):
            total += len(chunk)
            if total > size:
                _deny()
            actual.update(chunk)
        return total == size and hmac.compare_digest(actual.hexdigest(), digest)


def _recover_stage(directory: int, temporary: str, name: str, digest: str, size: int):
    """Remove only this admission's owned scratch link, preserving its CAS file."""
    try:
        staged = os.stat(temporary, dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return
    if (
        not stat.S_ISREG(staged.st_mode)
        or staged.st_uid != os.geteuid()
        or stat.S_IMODE(staged.st_mode) not in (0o400, 0o600)
    ):
        _deny()
    if staged.st_nlink == 2:
        try:
            final = os.stat(name, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            _deny()
        if (staged.st_dev, staged.st_ino) != (final.st_dev, final.st_ino):
            _deny()
        fd = os.open(
            temporary, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory
        )
        actual, total = hashlib.sha256(), 0
        with os.fdopen(fd, "rb") as stream:
            while chunk := stream.read(1024 * 1024):
                total += len(chunk)
                if total > size:
                    _deny()
                actual.update(chunk)
        if total != size or actual.hexdigest() != digest:
            _deny()
    elif staged.st_nlink != 1:
        _deny()
    os.unlink(temporary, dir_fd=directory)
    os.fsync(directory)


def _publish_file(
    directory: int,
    name: str,
    source: BinaryIO,
    digest: str,
    size: int,
    admission_id: UUID,
):
    temporary = (
        ".source-admission-" + admission_id.hex + "-" + _digest(name.encode())[:16]
    )
    _recover_stage(directory, temporary, name, digest, size)
    try:
        if _matches_file(directory, name, digest, size):
            return
        _deny()
    except FileNotFoundError:
        pass
    fd = os.open(
        temporary,
        os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
        0o600,
        dir_fd=directory,
    )
    try:
        with os.fdopen(fd, "wb") as output:
            actual, actual_size = _copy(source, output, size)
            if actual != digest or actual_size != size:
                _deny()
            os.fchmod(output.fileno(), 0o400)
            os.fsync(output.fileno())
        try:
            os.link(
                temporary,
                name,
                src_dir_fd=directory,
                dst_dir_fd=directory,
                follow_symlinks=False,
            )
        except FileExistsError:
            if not _matches_file(directory, name, digest, size):
                _deny()
    finally:
        os.unlink(temporary, dir_fd=directory)
        os.fsync(directory)


def _publish(
    prepared: PreparedSourceAdmission, private_root: Path, admission_id: UUID
) -> None:
    from io import BytesIO

    plan = _json(prepared.plan_bytes, 1_048_576)
    if _canonical(plan) != prepared.plan_bytes:
        _deny()
    manifest = validate_manifest(
        _json(prepared.manifest_bytes, len(prepared.manifest_bytes))
    )
    if (
        manifest["manifest_sha256"] != plan["manifest_sha256"]
        or _canonical(manifest) != prepared.manifest_bytes
        or set(prepared.pdfs) != {item["pdf_sha256"] for item in plan["pdfs"]}
        or set(prepared.pages)
        != {
            page["image_sha256"].removeprefix("sha256:")
            for document in plan["documents"]
            for page in document["pages"]
        }
        or any(_digest(image) != sha for sha, image in prepared.pages.items())
    ):
        _deny()
    with ExitStack() as stack:
        root = _open_root(private_root, stack)
        fcntl.flock(root, fcntl.LOCK_EX)
        directories = {}
        for name in ("manifests", "pdfs", "pages"):
            try:
                os.mkdir(name, mode=0o700, dir_fd=root)
            except FileExistsError:
                pass
            child = os.open(
                name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=root,
            )
            stack.callback(os.close, child)
            _check_directory(child)
            directories[name] = child
        os.fsync(root)
        _publish_file(
            directories["manifests"],
            plan["manifest_sha256"] + ".json",
            BytesIO(prepared.manifest_bytes),
            _digest(prepared.manifest_bytes),
            len(prepared.manifest_bytes),
            admission_id,
        )
        for item in plan["pdfs"]:
            sha = item["pdf_sha256"]
            _publish_file(
                directories["pdfs"],
                sha + ".pdf",
                prepared.pdfs[sha],
                sha,
                item["byte_length"],
                admission_id,
            )
        for sha, image in prepared.pages.items():
            _publish_file(
                directories["pages"],
                sha + ".png",
                BytesIO(image),
                sha,
                len(image),
                admission_id,
            )


async def admit_prepared_sources(
    prepared: PreparedSourceAdmission,
    envelope: bytes,
    *,
    public_keys,
    private_root: Path,
    pool,
) -> str:
    """Publish private CAS and commit protected anchors after exact approval.

    A database failure can leave private, content-addressed files without an
    admission. It does not create capture authority; shared files are never
    deleted by rollback. Operational orphan cleanup is a separate action.
    """
    try:
        if type(prepared) is not PreparedSourceAdmission:
            _deny()
        authorization = verify_source_admission(
            prepared.plan_bytes,
            envelope,
            public_keys=public_keys,
            now_seconds=int(time.time()),
        )
        _publish(prepared, private_root, UUID(authorization["admission_id"]))
        async with pool.acquire() as conn:
            if conn.is_in_transaction():
                _deny()
            async with conn.transaction():
                role = await conn.fetchrow(
                    "SELECT current_user AS name,session_user AS session_name,"
                    "rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls,"
                    "pg_catalog.pg_has_role(current_user,'samchat_ctt_staff_owner',"
                    "'MEMBER') AS owner_member,"
                    "(pg_catalog.pg_has_role('devnous_user',current_user,'MEMBER') OR "
                    "pg_catalog.pg_has_role(current_user,'devnous_user','MEMBER')) "
                    "AS app_related,"
                    "EXISTS(SELECT 1 FROM pg_catalog.pg_roles r WHERE r.rolname IN "
                    "('pg_read_all_data','pg_write_all_data','pg_read_server_files',"
                    "'pg_write_server_files','pg_execute_server_program') AND "
                    "pg_catalog.pg_has_role(current_user,r.oid,'MEMBER')) "
                    "AS broad_privileges "
                    "FROM pg_catalog.pg_roles "
                    "WHERE rolname=current_user"
                )
                if (
                    role is None
                    or role["name"] == "devnous_user"
                    or role["name"] != role["session_name"]
                    or any(
                        role[key] is not False
                        for key in (
                            "rolsuper",
                            "rolcreatedb",
                            "rolcreaterole",
                            "rolreplication",
                            "rolbypassrls",
                            "owner_member",
                            "app_related",
                            "broad_privileges",
                        )
                    )
                    or await conn.fetchval("SHOW max_prepared_transactions") != "0"
                ):
                    _deny()
                accepted = await conn.fetchval(
                    "SELECT ctt_staff_private.register_source_admission("
                    "$1,$2,$3,$4,$5,$6)",
                    UUID(authorization["admission_id"]),
                    prepared.plan_bytes,
                    authorization["proof_sha256"],
                    authorization["issued_at"],
                    authorization["expires_at"],
                    envelope,
                )
                if accepted is not True:
                    _deny()
        return prepared.plan_sha256
    except Exception:
        _deny()
