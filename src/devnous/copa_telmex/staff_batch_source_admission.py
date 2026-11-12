"""Independent operator admission of exact multipart originals.

Preparing, signing and applying are distinct. No call here creates a team or
grants reviewer faculty. Applying requires a separately owner-granted SQL role.
"""

from __future__ import annotations

import base64
import fcntl
import hashlib
import hmac
import os
import re
import tempfile
import time
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from types import MappingProxyType
from typing import BinaryIO, Mapping
from uuid import UUID, uuid5

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from .staff_batch_contracts import (
    canonical,
    digest,
    source_refs,
    strict_loads,
    validate_source_manifest,
)
from .staff_source_admission import (
    SourceAdmissionDenied,
    SourceAdmissionLimits,
    _check_directory,
    _copy,
    _open_root,
    _open_source,
    _publish_file,
)

DOMAIN = b"samchat.ctt.source-admission.v2\x00"


@dataclass(frozen=True, repr=False)
class PreparedBatchSources:
    plan_bytes: bytes = field(repr=False)
    manifest_bytes: bytes = field(repr=False)
    pdfs: Mapping[str, BinaryIO] = field(repr=False)
    pages: Mapping[str, bytes] = field(repr=False)

    @property
    def plan_sha256(self) -> str:
        return hashlib.sha256(self.plan_bytes).hexdigest()


@contextmanager
def prepare_source_admission_v2(
    manifest_bytes: bytes,
    source_root: Path,
    *,
    edition: Mapping,
    limits: SourceAdmissionLimits,
):
    """Snapshot private originals and derive pages without publishing anything."""
    import fitz

    with ExitStack() as stack:
        try:
            if type(limits) is not SourceAdmissionLimits:
                raise ValueError
            manifest = validate_source_manifest(
                strict_loads(manifest_bytes, limits.max_manifest_bytes)
            )
            if canonical(manifest["edition"]) != canonical(dict(edition)):
                raise ValueError
            root = _open_root(source_root, stack)
            pdfs, pdf_lengths, images, docs, pages = {}, {}, {}, [], []
            total_pdf = total_page = 0
            for pdf in manifest["pdfs"]:
                original = _open_source(root, pdf["filename"], stack)
                target = stack.enter_context(tempfile.TemporaryFile("w+b"))
                actual, length = _copy(original, target, limits.max_pdf_bytes)
                if actual != pdf["sha256"]:
                    raise ValueError
                total_pdf += length
                if total_pdf > limits.max_total_pdf_bytes:
                    raise ValueError
                with fitz.open(
                    f"/proc/self/fd/{target.fileno()}", filetype="pdf"
                ) as document:
                    if (
                        document.is_encrypted
                        or not document.is_pdf
                        or len(document) != pdf["page_count"]
                    ):
                        raise ValueError
                pdfs[actual], pdf_lengths[actual] = target, length
            for doc in manifest["documents"]:
                refs = source_refs(doc["source_slices"])
                docs.append(
                    {
                        "document_id": doc["document_id"],
                        "payload_hash": digest(doc["extraction"]),
                        "payload_bytes_hex": canonical(doc["extraction"]).hex(),
                        "extraction": doc["extraction"],
                        "source_refs": [
                            {"pdf_sha256": sha, "pdf_page": number}
                            for sha, number in refs
                        ],
                    }
                )
                for index, (sha, number) in enumerate(refs, 1):
                    with fitz.open(
                        f"/proc/self/fd/{pdfs[sha].fileno()}", filetype="pdf"
                    ) as document:
                        page = document.load_page(number - 1)
                        if (
                            min(page.rect.width, page.rect.height) < 1
                            or max(page.rect.width, page.rect.height) > 8000
                            or page.rect.width * page.rect.height
                            > limits.max_page_pixels
                        ):
                            raise ValueError
                        pix = page.get_pixmap(alpha=False)
                        if pix.width * pix.height > limits.max_page_pixels:
                            raise ValueError
                        raw = pix.tobytes("png")
                        if len(raw) > limits.max_page_bytes:
                            raise ValueError
                        image_hash = hashlib.sha256(raw).hexdigest()
                        if image_hash not in images:
                            total_page += len(raw)
                            if total_page > limits.max_total_page_bytes:
                                raise ValueError
                            images[image_hash] = raw
                        asset = uuid5(
                            UUID(manifest["edition"]["edition_id"]),
                            f"ctt.source-page.v2:{doc['document_id']}:{index}:"
                            f"{sha}:{number}:{image_hash}",
                        )
                        pages.append(
                            {
                                "document_id": doc["document_id"],
                                "page_index": index,
                                "pdf_sha256": sha,
                                "pdf_page": number,
                                "image_sha256": image_hash,
                                "width": pix.width,
                                "height": pix.height,
                                "asset_id": str(asset),
                            }
                        )
            plan = {
                "schema": "ctt.source-admission.plan.v2",
                "manifest_sha256": manifest["manifest_sha256"],
                "edition": manifest["edition"],
                "documents": docs,
                "pages": pages,
                "pdfs": [
                    {"pdf_sha256": sha, "byte_length": pdf_lengths[sha]}
                    for sha in sorted(pdfs)
                ],
            }
            raw_plan, raw_manifest = canonical(plan), canonical(manifest)
            if (
                len(raw_plan) > 8 * 1024 * 1024
                or len(raw_manifest) > limits.max_manifest_bytes
            ):
                raise ValueError
            prepared = PreparedBatchSources(
                raw_plan, raw_manifest, MappingProxyType(pdfs), MappingProxyType(images)
            )
        except Exception:
            raise SourceAdmissionDenied("batch source admission denied") from None
        yield prepared


def sign_source_admission_v2(
    plan_bytes: bytes,
    *,
    admission_id: UUID,
    issued_at: int,
    expires_at: int,
    kid: str,
    private_key: Ed25519PrivateKey,
) -> bytes:
    if (
        type(plan_bytes) is not bytes
        or not isinstance(admission_id, UUID)
        or admission_id.int == 0
        or any(type(n) is not int for n in (issued_at, expires_at))
        or issued_at < 0
        or not 0 < expires_at - issued_at <= 300
        or type(kid) is not str
        or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", kid)
    ):
        raise SourceAdmissionDenied("batch source admission denied")
    plan = strict_loads(plan_bytes)
    if (
        canonical(plan) != plan_bytes
        or plan.get("schema") != "ctt.source-admission.plan.v2"
    ):
        raise SourceAdmissionDenied("batch source admission denied")
    body = {
        "schema": "ctt.source-admission.authorization.v2",
        "kid": kid,
        "admission_id": str(admission_id),
        "plan_sha256": hashlib.sha256(plan_bytes).hexdigest(),
        "issued_at": issued_at,
        "expires_at": expires_at,
    }
    return canonical(
        {
            "body": body,
            "signature": base64.b64encode(
                private_key.sign(DOMAIN + canonical(body))
            ).decode("ascii"),
        }
    )


def verify_source_admission_v2(
    plan_bytes: bytes,
    envelope: bytes,
    *,
    public_keys: Mapping[str, bytes],
    now_seconds: int,
) -> dict:
    try:
        value = strict_loads(envelope, 8192)
        if (
            type(value) is not dict
            or set(value) != {"body", "signature"}
            or canonical(value) != envelope
        ):
            raise ValueError
        body = value["body"]
        if (
            type(body) is not dict
            or set(body)
            != {
                "schema",
                "kid",
                "admission_id",
                "plan_sha256",
                "issued_at",
                "expires_at",
            }
            or body["schema"] != "ctt.source-admission.authorization.v2"
        ):
            raise ValueError
        if (
            any(
                type(n) is not int
                for n in (now_seconds, body["issued_at"], body["expires_at"])
            )
            or not 0 <= body["issued_at"] <= now_seconds < body["expires_at"]
            or not 0 < body["expires_at"] - body["issued_at"] <= 300
        ):
            raise ValueError
        if (
            str(UUID(body["admission_id"])) != body["admission_id"]
            or UUID(body["admission_id"]).int == 0
            or not hmac.compare_digest(
                body["plan_sha256"], hashlib.sha256(plan_bytes).hexdigest()
            )
        ):
            raise ValueError
        signature = base64.b64decode(value["signature"], validate=True)
        if (
            len(signature) != 64
            or base64.b64encode(signature).decode("ascii") != value["signature"]
        ):
            raise ValueError
        Ed25519PublicKey.from_public_bytes(public_keys[body["kid"]]).verify(
            signature, DOMAIN + canonical(body)
        )
        return body
    except Exception:
        raise SourceAdmissionDenied("batch source admission denied") from None


def _publish(prepared: PreparedBatchSources, root: Path, admission_id: UUID) -> None:
    plan = strict_loads(prepared.plan_bytes)
    manifest = validate_source_manifest(strict_loads(prepared.manifest_bytes))
    if (
        canonical(plan) != prepared.plan_bytes
        or canonical(manifest) != prepared.manifest_bytes
        or manifest["manifest_sha256"] != plan["manifest_sha256"]
    ):
        raise SourceAdmissionDenied("batch source admission denied")
    with ExitStack() as stack:
        fd = _open_root(root, stack)
        fcntl.flock(fd, fcntl.LOCK_EX)
        folders = {}
        for name in ("manifests", "pdfs", "pages"):
            try:
                os.mkdir(name, mode=0o700, dir_fd=fd)
            except FileExistsError:
                pass
            child = os.open(
                name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=fd,
            )
            stack.callback(os.close, child)
            _check_directory(child)
            folders[name] = child
        _publish_file(
            folders["manifests"],
            plan["manifest_sha256"] + ".json",
            BytesIO(prepared.manifest_bytes),
            hashlib.sha256(prepared.manifest_bytes).hexdigest(),
            len(prepared.manifest_bytes),
            admission_id,
        )
        if set(prepared.pdfs) != {p["pdf_sha256"] for p in plan["pdfs"]} or set(
            prepared.pages
        ) != {p["image_sha256"] for p in plan["pages"]}:
            raise SourceAdmissionDenied("batch source admission denied")
        for pdf in plan["pdfs"]:
            sha = pdf["pdf_sha256"]
            _publish_file(
                folders["pdfs"],
                sha + ".pdf",
                prepared.pdfs[sha],
                sha,
                pdf["byte_length"],
                admission_id,
            )
        for sha, raw in prepared.pages.items():
            _publish_file(
                folders["pages"],
                sha + ".png",
                BytesIO(raw),
                sha,
                len(raw),
                admission_id,
            )


async def admit_prepared_sources_v2(
    prepared: PreparedBatchSources,
    envelope: bytes,
    *,
    public_keys,
    private_root: Path,
    pool,
) -> str:
    """Retain exact sources; a SQL failure leaves only unadmitted private CAS."""
    if type(prepared) is not PreparedBatchSources:
        raise SourceAdmissionDenied("batch source admission denied")
    approval = verify_source_admission_v2(
        prepared.plan_bytes,
        envelope,
        public_keys=public_keys,
        now_seconds=int(time.time()),
    )
    _publish(prepared, private_root, UUID(approval["admission_id"]))
    plan = strict_loads(prepared.plan_bytes)
    manifest = strict_loads(prepared.manifest_bytes)
    identity = dict(manifest)
    identity.pop("manifest_sha256")
    async with pool.acquire() as conn:
        if conn.is_in_transaction():
            raise SourceAdmissionDenied("batch source admission denied")
        async with conn.transaction():
            if (
                await conn.fetchval(
                    "SELECT ctt_staff_private.register_batch_source_admission("
                    "$1,$2,$3,$4,$5,$6::jsonb,$7::jsonb)",
                    UUID(approval["admission_id"]),
                    prepared.manifest_bytes,
                    canonical(identity),
                    prepared.plan_bytes,
                    envelope,
                    canonical(plan["documents"]).decode(),
                    canonical(plan["pages"]).decode(),
                )
                is not True
            ):
                raise SourceAdmissionDenied("batch source admission denied")
    return prepared.plan_sha256
