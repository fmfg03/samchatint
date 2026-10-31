# services/evidence-bus/security/hmac_key.py
"""
Secure HMAC key loader for Evidence Bus.

P0 Fix for F-001: Default HMAC Key Vulnerability
- No default key in code (fail-closed)
- Reject banned/weak keys
- Support file-based secrets (K8s mounts)
- Log only fingerprint prefix, never raw key
- Structured error codes for safe logging
"""
from __future__ import annotations

import hashlib
import os
from typing import Tuple


class HMACKeyError(RuntimeError):
    """
    Structured error for HMAC key validation failures.

    Attributes:
        code: Machine-readable error code (safe to log)
        message: Human-readable message (may contain sensitive context - do NOT log directly)
    """

    # Error codes (safe to log, no sensitive data)
    MISSING = "MISSING"
    BANNED = "BANNED"
    TOO_SHORT = "TOO_SHORT"
    FILE_NOT_FOUND = "FILE_NOT_FOUND"
    FILE_PERMISSION = "FILE_PERMISSION"
    FILE_READ_ERROR = "FILE_READ_ERROR"

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


BANNED_KEYS = frozenset({
    "dev-evidence-key",
    "dev-evidence-key-production",
    "test-key",
    "changeme",
    "default",
    "password",
    "secret",
    "example",
    "placeholder",
})

# Minimum key length (32 bytes = 256 bits)
MIN_KEY_LENGTH = 32


def load_evidence_hmac_key() -> Tuple[bytes, str]:
    """
    Load and validate EVIDENCE_HMAC_KEY from environment.

    Returns:
        Tuple of (key_bytes, sha256_hex_fingerprint)

    Raises:
        RuntimeError: If key is missing, banned, or too short.

    Supports:
        - EVIDENCE_HMAC_KEY: Direct key value
        - EVIDENCE_HMAC_KEY_FILE: Path to file containing key (preferred for K8s)
    """
    key = (os.getenv("EVIDENCE_HMAC_KEY") or "").strip()

    # Support file-based secrets (K8s secret mounts)
    key_file = (os.getenv("EVIDENCE_HMAC_KEY_FILE") or "").strip()
    if not key and key_file:
        try:
            with open(key_file, "r", encoding="utf-8") as f:
                key = f.read().strip()
        except FileNotFoundError:
            raise HMACKeyError(
                HMACKeyError.FILE_NOT_FOUND,
                f"EVIDENCE_HMAC_KEY_FILE not found: {key_file}"
            )
        except PermissionError:
            raise HMACKeyError(
                HMACKeyError.FILE_PERMISSION,
                f"EVIDENCE_HMAC_KEY_FILE not readable (permission denied): {key_file}"
            )
        except Exception as e:
            raise HMACKeyError(
                HMACKeyError.FILE_READ_ERROR,
                f"EVIDENCE_HMAC_KEY_FILE read error: {e}"
            ) from e

    # Fail-closed: no default key
    if not key:
        raise HMACKeyError(
            HMACKeyError.MISSING,
            "EVIDENCE_HMAC_KEY not set. "
            "Set EVIDENCE_HMAC_KEY or EVIDENCE_HMAC_KEY_FILE environment variable. "
            "Evidence Bus cannot start without a valid HMAC key (fail-closed)."
        )

    # Compute fingerprint for logging (never log raw key)
    fingerprint = hashlib.sha256(key.encode("utf-8")).hexdigest()

    # Check against banned keys
    key_lower = key.lower()
    if key in BANNED_KEYS or any(banned in key_lower for banned in ("dev", "test", "example")):
        raise HMACKeyError(
            HMACKeyError.BANNED,
            f"EVIDENCE_HMAC_KEY is banned/unsafe (sha256_prefix={fingerprint[:12]}). "
            "Use a cryptographically strong random key."
        )

    # Check minimum length
    if len(key) < MIN_KEY_LENGTH:
        raise HMACKeyError(
            HMACKeyError.TOO_SHORT,
            f"EVIDENCE_HMAC_KEY too short ({len(key)} chars, minimum {MIN_KEY_LENGTH}). "
            "Use a cryptographically strong random key of at least 32 characters."
        )

    return key.encode("utf-8"), fingerprint


def generate_secure_key(length: int = 64) -> str:
    """
    Generate a cryptographically secure random key.

    Utility function for operators to generate valid keys.

    Args:
        length: Key length in characters (default 64)

    Returns:
        Hex-encoded random key
    """
    import secrets
    return secrets.token_hex(length // 2)


if __name__ == "__main__":
    # CLI helper for key generation
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "generate":
        key = generate_secure_key()
        print(f"Generated key: {key}")
        print(f"SHA256 fingerprint: {hashlib.sha256(key.encode()).hexdigest()}")
        print("\nSet in environment:")
        print(f"  export EVIDENCE_HMAC_KEY='{key}'")
    else:
        print("Usage: python -m security.hmac_key generate")
