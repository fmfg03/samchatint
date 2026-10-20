"""Minimized projection of an already authorized canonical review result.

No roster extraction, defaults, classification or commit logic is implemented.
The server-side adapter must authorize the review before calling this function.
"""

from collections.abc import Mapping

from .contracts import Denied


def review_summary(validation: Mapping, *, version: str) -> dict:
    """Expose canonical readiness and issue counts without player PII/messages."""
    if (
        not version
        or type(validation.get("ready_to_commit")) is not bool
        or not isinstance(validation.get("blockers"), list)
        or not isinstance(validation.get("warnings"), list)
    ):
        raise Denied("CANONICAL_REVIEW_INCOMPLETE")
    return {
        "version": version,
        "ready_to_commit": validation["ready_to_commit"] and not validation["blockers"],
        "blocking_issues": len(validation["blockers"]),
        "warnings": len(validation["warnings"]),
        "commit_enabled": False,
    }


def validate_file_envelope(content: bytes, mime: str, *, maximum_bytes: int) -> None:
    """Cheap intake rejection only, NOT malware, image or document validation.

    Canonical scanning, decoding, asset ownership and extraction remain required.
    Content is never logged, stored, uploaded or sent to a model here.
    """
    if maximum_bytes <= 0 or not content or len(content) > maximum_bytes:
        raise Denied("INVALID_FILE_SIZE")
    signatures = {
        "application/pdf": b"%PDF-",
        "image/png": b"\x89PNG\r\n\x1a\n",
        "image/jpeg": b"\xff\xd8\xff",
    }
    if mime not in signatures or not content.startswith(signatures[mime]):
        raise Denied("INVALID_FILE_TYPE")
