"""Private filesystem primitives for resumable Copa Telmex batch uploads."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any, Dict, Tuple
from uuid import UUID, uuid4

from .batch_admission import validate_manifest

MAX_BATCH_PDF_BYTES = 64 * 1024 * 1024
UPLOAD_CHUNK_BYTES = 1024 * 1024


class BatchUploadStorageError(ValueError):
    """One staged file is unsafe, incomplete, or inconsistent."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def staging_root(*, production: bool = False) -> Path:
    """Return the configured private root, failing closed in production."""
    configured = str(os.getenv("CTT_BATCH_STAGING_ROOT") or "").strip()
    if not configured:
        if production:
            raise BatchUploadStorageError(
                "batch_staging_not_configured",
                "El almacenamiento temporal del lote no está configurado.",
            )
        configured = "/tmp/samchat-ctt-batch-uploads"
    return Path(configured)


def ensure_private_root(root: Path) -> None:
    """Create a non-symlink private root and enforce owner-only permissions."""
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if root.is_symlink() or not root.is_dir():
        raise BatchUploadStorageError(
            "batch_staging_unsafe", "El almacenamiento temporal no es seguro."
        )
    root.chmod(0o700)


def upload_directory(root: Path, upload_id: UUID) -> Path:
    return root / str(upload_id)


def initialize_upload_directory(
    root: Path, upload_id: UUID, manifest: Dict[str, Any]
) -> Path:
    """Persist one normalized manifest atomically with mode 0600."""
    ensure_private_root(root)
    directory = upload_directory(root, upload_id)
    directory.mkdir(mode=0o700, exist_ok=True)
    if directory.is_symlink() or not directory.is_dir():
        raise BatchUploadStorageError(
            "batch_staging_unsafe", "El directorio temporal no es seguro."
        )
    directory.chmod(0o700)
    manifest_path = directory / "manifest.json"
    temporary = directory / f".manifest-{uuid4().hex}.tmp"
    payload = json.dumps(
        manifest, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, manifest_path)
        manifest_path.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)
    return directory


def load_manifest(root: Path, upload_id: UUID) -> Dict[str, Any]:
    """Reload and revalidate the normalized private manifest."""
    path = upload_directory(root, upload_id) / "manifest.json"
    if path.is_symlink() or not path.is_file():
        raise BatchUploadStorageError(
            "batch_manifest_missing", "El manifiesto temporal ya no está disponible."
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return validate_manifest(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise BatchUploadStorageError(
            "batch_manifest_invalid", "El manifiesto temporal no es válido."
        ) from exc


def staged_pdf_path(root: Path, upload_id: UUID, expected_sha256: str) -> Path:
    return upload_directory(root, upload_id) / "files" / f"{expected_sha256}.pdf"


async def store_pdf(
    *,
    root: Path,
    upload_id: UUID,
    upload: Any,
    expected_sha256: str,
) -> Tuple[Path, int]:
    """Stream, hash, bound, and atomically store one expected PDF."""
    directory = upload_directory(root, upload_id)
    files_dir = directory / "files"
    if directory.is_symlink() or not directory.is_dir():
        raise BatchUploadStorageError(
            "batch_upload_missing", "La carga temporal ya no está disponible."
        )
    files_dir.mkdir(mode=0o700, exist_ok=True)
    if files_dir.is_symlink() or not files_dir.is_dir():
        raise BatchUploadStorageError(
            "batch_staging_unsafe", "El almacenamiento temporal no es seguro."
        )
    files_dir.chmod(0o700)
    final_path = staged_pdf_path(root, upload_id, expected_sha256)
    temporary = files_dir / f".{uuid4().hex}.part"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    digest = hashlib.sha256()
    total = 0
    try:
        with os.fdopen(descriptor, "wb") as stream:
            while True:
                chunk = await upload.read(UPLOAD_CHUNK_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_BATCH_PDF_BYTES:
                    raise BatchUploadStorageError(
                        "file_too_large", "Un PDF del lote excede 64 MB."
                    )
                digest.update(chunk)
                stream.write(chunk)
            stream.flush()
            os.fsync(stream.fileno())
        if total == 0:
            raise BatchUploadStorageError(
                "source_file_empty", "El PDF seleccionado está vacío."
            )
        if digest.hexdigest() != expected_sha256:
            raise BatchUploadStorageError(
                "source_pdf_hash_mismatch",
                "El PDF no coincide con el manifiesto.",
            )
        with temporary.open("rb") as candidate:
            if candidate.read(5) != b"%PDF-":
                raise BatchUploadStorageError(
                    "invalid_file_type", "El archivo seleccionado no es un PDF válido."
                )
        os.replace(temporary, final_path)
        final_path.chmod(0o600)
        return final_path, total
    finally:
        temporary.unlink(missing_ok=True)


def purge_upload(root: Path, upload_id: UUID) -> None:
    """Delete one bounded staging directory without following symlinks."""
    directory = upload_directory(root, upload_id)
    if directory.is_symlink():
        directory.unlink(missing_ok=True)
        return
    if directory.exists():
        shutil.rmtree(directory)


class StagedUploadFile:
    """Minimal async UploadFile-compatible reader for existing admission code."""

    def __init__(self, path: Path, filename: str):
        if path.is_symlink() or not path.is_file():
            raise BatchUploadStorageError(
                "source_file_missing", "Falta un PDF temporal del manifiesto."
            )
        self.filename = filename
        self._stream = path.open("rb")

    async def read(self, size: int = -1) -> bytes:
        return await asyncio.to_thread(self._stream.read, size)

    async def seek(self, offset: int) -> None:
        await asyncio.to_thread(self._stream.seek, offset)

    async def close(self) -> None:
        await asyncio.to_thread(self._stream.close)
