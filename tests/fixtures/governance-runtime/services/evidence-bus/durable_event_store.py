"""Single-node, append-only durable journal for verified Evidence Bus events."""

from __future__ import annotations

import base64
import hashlib
import copy
import fcntl
import json
import os
import re
import time
from pathlib import Path
from threading import Lock
from typing import Any, Callable, Dict, Iterator, List, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from staff_v2_event_validation import validate_staff_v2_event


class DurableEventStoreError(RuntimeError):
    """The evidence journal cannot safely accept or replay events."""


IDEMPOTENT_V2_TYPES = frozenset(
    {
        "samchat_registration_roster_decision_v2",
        "samchat_registration_staff_finality_attestation_v2",
        "samchat_registration_player_finality_attestation_v2",
    }
)


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def verify_stored_receipt(
    receipt: Mapping[str, Any], trusted_keys: Mapping[str, bytes]
) -> bool:
    """Authenticate a stored receipt independently of its expiry."""
    try:
        if (
            receipt.get("alg") != "Ed25519"
            or receipt.get("issuer") != "zaubern-evidence-bus"
        ):
            return False
        key = Ed25519PublicKey.from_public_bytes(trusted_keys[receipt["kid"]])
        signature = base64.b64decode(receipt["signature"], validate=True)
        unsigned = {
            k: v
            for k, v in receipt.items()
            if k not in {"signature", "receipt_id"}
        }
        key.verify(signature, _canonical(unsigned))
        return True
    except (KeyError, TypeError, ValueError, InvalidSignature):
        return False


def _operation_id(semantic: Mapping[str, Any]) -> str:
    """Bind the operation to the caller's exact, opaque attempt and subject."""
    required = (
        "event_type",
        "tenant_id",
        "session_id",
        "draft_id",
        "team_id",
        "tournament_edition_id",
        "attempt_id",
    )
    if any(
        not isinstance(semantic.get(k), str) or not semantic[k]
        for k in required
    ):
        raise DurableEventStoreError("invalid v2 operation scope")
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", semantic["attempt_id"]):
        raise DurableEventStoreError("invalid v2 attempt id")
    if (
        type(semantic.get("draft_version")) is not int
        or semantic["draft_version"] < 1
    ):
        raise DurableEventStoreError("invalid v2 draft version")
    scope = {key: semantic[key] for key in required}
    scope["draft_version"] = semantic["draft_version"]
    if (
        semantic["event_type"]
        == "samchat_registration_staff_finality_attestation_v2"
    ):
        staff_id = semantic.get("staff_id")
        if not isinstance(staff_id, str) or not staff_id:
            raise DurableEventStoreError("invalid v2 staff subject")
        scope["staff_id"] = staff_id
    if semantic["event_type"] == "samchat_registration_player_finality_attestation_v2":
        player_id = semantic.get("player_id")
        if not isinstance(player_id, str) or not player_id:
            raise DurableEventStoreError("invalid v2 player subject")
        scope["player_id"] = player_id
    return _hash(scope)


class DurableJSONLEventStore:
    """Fsync each hash-chained record before returning to the receipt issuer."""

    def __init__(
        self, path: str, *, v2_public_keys: Mapping[str, bytes] | None = None
    ):
        self.path = Path(path).expanduser().resolve()
        self._lock = Lock()
        self._records: List[Dict[str, Any]] = []
        self._idempotent: Dict[str, Dict[str, Any]] = {}
        self._failed = False
        self._lock_fd = None
        self._v2_public_keys = dict(v2_public_keys or {})
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            lock_path = str(self.path) + ".lock"
            self._lock_fd = os.open(
                lock_path, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600
            )
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise DurableEventStoreError(
                    "evidence journal already owned"
                ) from exc
            if not self.path.exists():
                self.path.touch(mode=0o600)
            os.chmod(self.path, 0o600)
            self._records = self._read_and_verify()
            # A previous append may have failed after writing but before fsync.
            # Sync the verified journal before any stored receipt can be reused.
            with self.path.open("rb") as stream:
                os.fsync(stream.fileno())
            directory_fd = os.open(
                str(self.path.parent), os.O_RDONLY | os.O_DIRECTORY
            )
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            self._restore_idempotent()
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            self.close()
            raise DurableEventStoreError(
                "evidence journal initialization failed"
            ) from exc
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        """Release the process-exclusive journal lock."""
        fd = self._lock_fd
        if fd is not None:
            self._lock_fd = None
            os.close(fd)
        self._failed = True

    def __del__(self) -> None:
        if getattr(self, "_lock_fd", None) is not None:
            self.close()

    def _read_and_verify(self) -> List[Dict[str, Any]]:
        records: List[Dict[str, Any]] = []
        previous_hash = None
        with self.path.open("r", encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                record = json.loads(line)
                expected = {
                    "sequence": len(records) + 1,
                    "previous_record_hash": previous_hash,
                    "entry": record.get("entry"),
                }
                if (
                    record.get("sequence") != expected["sequence"]
                    or record.get("previous_record_hash") != previous_hash
                    or record.get("record_hash") != _hash(expected)
                    or not isinstance(record.get("entry"), dict)
                ):
                    raise DurableEventStoreError(
                        f"evidence journal verification failed at line {line_number}"
                    )
                records.append(record)
                previous_hash = record["record_hash"]
        return records

    def _restore_idempotent(self) -> None:
        for record in self._records:
            entry = record["entry"]
            item = entry.get("idempotent_v2")
            if item is None:
                continue
            if not isinstance(item, dict):
                raise DurableEventStoreError(
                    "invalid idempotent receipt entry"
                )
            operation_id = item.get("operation_id")
            semantic = item.get("semantic_event")
            receipt = item.get("receipt")
            if (
                not isinstance(operation_id, str)
                or not re.fullmatch(r"sha256:[a-f0-9]{64}", operation_id)
                or not isinstance(semantic, dict)
                or semantic.get("operation_id") != operation_id
                or semantic.get("event_type") not in IDEMPOTENT_V2_TYPES
                or not validate_staff_v2_event(semantic)
                or _operation_id(semantic) != operation_id
                or not isinstance(receipt, dict)
                or receipt.get("event_hash") != _hash(semantic)
                or receipt.get("event_type") != semantic.get("event_type")
                or receipt.get("tenant_id") != semantic.get("tenant_id")
                or receipt.get("alg") != "Ed25519"
                or receipt.get("verified") is not True
                or receipt.get("receipt_id")
                != _hash(
                    {k: v for k, v in receipt.items() if k != "receipt_id"}
                )
                or not verify_stored_receipt(receipt, self._v2_public_keys)
                or operation_id in self._idempotent
                or entry.get("verified") is not True
                or not isinstance(entry.get("event"), dict)
                or entry["event"].get("type") != semantic.get("event_type")
                or entry["event"].get("execution_receipt_ref")
                != "zaubern:registration-gate:precommit"
                or {
                    k: v
                    for k, v in entry["event"].items()
                    if k not in {"eis_sig", "execution_receipt_ref", "type"}
                }
                != semantic
            ):
                raise DurableEventStoreError(
                    "invalid idempotent receipt entry"
                )
            self._idempotent[operation_id] = item

    def _append_locked(self, entry: Dict[str, Any]) -> None:
        if self._failed:
            raise DurableEventStoreError("evidence journal requires restart")
        unsigned = {
            "sequence": len(self._records) + 1,
            "previous_record_hash": (
                self._records[-1]["record_hash"] if self._records else None
            ),
            "entry": entry,
        }
        record = {**unsigned, "record_hash": _hash(unsigned)}
        try:
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(_canonical(record).decode("utf-8") + "\n")
                stream.flush()
                os.fsync(stream.fileno())
        except OSError as exc:
            # The write may have reached disk; never continue with a stale head.
            self._failed = True
            raise DurableEventStoreError(
                "evidence journal append failed"
            ) from exc
        self._records.append(record)

    def append(self, entry: Dict[str, Any]) -> None:
        if not isinstance(entry, dict):
            raise DurableEventStoreError("evidence entry must be an object")
        with self._lock:
            self._append_locked(entry)

    def append_idempotent_receipt(
        self,
        event: Mapping[str, Any],
        receipt_factory: Callable[[], Mapping[str, Any]],
    ) -> Dict[str, Any]:
        """Persist one v2 event and receipt atomically, or recover it on retry."""
        if (
            event.get("type") != event.get("event_type")
            or event.get("execution_receipt_ref")
            != "zaubern:registration-gate:precommit"
        ):
            raise DurableEventStoreError("invalid v2 transport wrapper")
        semantic = {
            k: v
            for k, v in event.items()
            if k not in {"eis_sig", "execution_receipt_ref", "type"}
        }
        operation_id = semantic.get("operation_id")
        if (
            not isinstance(operation_id, str)
            or not re.fullmatch(r"sha256:[a-f0-9]{64}", operation_id)
            or semantic.get("event_type") not in IDEMPOTENT_V2_TYPES
            or not validate_staff_v2_event(semantic)
            or not isinstance(semantic.get("tenant_id"), str)
            or not semantic["tenant_id"]
            or _operation_id(semantic) != operation_id
        ):
            raise DurableEventStoreError("invalid v2 operation binding")
        with self._lock:
            if self._failed:
                raise DurableEventStoreError(
                    "evidence journal requires restart"
                )
            existing = self._idempotent.get(operation_id)
            if existing is not None:
                if existing["semantic_event"] != semantic:
                    raise DurableEventStoreError(
                        "v2 operation binding mismatch"
                    )
                return copy.deepcopy(existing["receipt"])
            receipt = dict(receipt_factory())
            if (
                receipt.get("event_hash") != _hash(semantic)
                or receipt.get("event_type") != semantic.get("event_type")
                or receipt.get("tenant_id") != semantic.get("tenant_id")
                or receipt.get("alg") != "Ed25519"
                or receipt.get("verified") is not True
                or receipt.get("receipt_id")
                != _hash(
                    {k: v for k, v in receipt.items() if k != "receipt_id"}
                )
                or not verify_stored_receipt(receipt, self._v2_public_keys)
            ):
                raise DurableEventStoreError("invalid v2 receipt")
            item = {
                "operation_id": operation_id,
                "semantic_event": copy.deepcopy(semantic),
                "receipt": copy.deepcopy(receipt),
            }
            self._append_locked(
                {
                    "received_at": time.time(),
                    "verified": True,
                    "event": copy.deepcopy(dict(event)),
                    "idempotent_v2": item,
                }
            )
            self._idempotent[operation_id] = item
            return copy.deepcopy(receipt)

    def __iter__(self) -> Iterator[Dict[str, Any]]:
        with self._lock:
            snapshot = [record["entry"] for record in reversed(self._records)]
        return iter(snapshot)

    def __len__(self) -> int:
        with self._lock:
            return len(self._records)

    def health(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "status": "failed" if self._failed else "verified",
                "path": str(self.path),
                "records": len(self._records),
                "head_record_hash": (
                    self._records[-1]["record_hash"] if self._records else None
                ),
            }
