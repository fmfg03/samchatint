"""Closed read dispatch, policy envelope, audit and bounded keyset pagination."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import date, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping
from uuid import UUID, uuid4

from .catalog import TOOLS
from .contracts import Admission, AuditSink, Principal, ReadError, utc_now
from .diagnosis import diagnose
from .repository import Repository


class Sources:
    """Pre-admitted immutable bytes, never a caller-selected filesystem path."""

    def __init__(
        self,
        files: Mapping[str, bytes],
        commit: str,
        source_kind: str = "synthetic_reviewed",
    ) -> None:
        self.files = MappingProxyType(dict(files))
        self.commit = commit
        self.source_kind = source_kind
        if source_kind not in {"synthetic_reviewed", "reviewed_manifest"}:
            raise ReadError("SOURCE_NOT_ADMITTED")
        if not files or re.fullmatch(r"[0-9a-f]{40}", commit) is None:
            raise ReadError("SOURCE_VERSION_UNPROVEN")
        for path, content in self.files.items():
            if (
                type(content) is not bytes
                or not isinstance(path, str)
                or path.startswith(("/", "\\"))
                or ".." in path.split("/")
                or "\\" in path
                or ":" in path
                or not path.endswith((".py", ".sql", ".md"))
                or any(part.startswith(".") for part in path.split("/"))
                or len(content) > 65536
                or b"\x00" in content
            ):
                raise ReadError("SOURCE_NOT_ADMITTED")
            try:
                decoded = content.decode("utf-8")
                if (
                    re.search(
                        r"(?i)(?:password|secret|api[_-]?key|token|credential)\s*[:=]\s*['\"][^'\"]+['\"]",
                        decoded,
                    )
                    or "PRIVATE KEY-----" in decoded
                ):
                    raise ReadError("SOURCE_NOT_ADMITTED")
            except UnicodeError as exc:
                raise ReadError("SOURCE_NOT_ADMITTED") from exc
        manifest = {p: hashlib.sha256(b).hexdigest() for p, b in self.files.items()}
        self.source_id = hashlib.sha256(
            json.dumps([commit, manifest], sort_keys=True).encode()
        ).hexdigest()

    @classmethod
    def from_manifest(
        cls,
        root: Path,
        manifest: Mapping[str, str],
        commit: str,
    ) -> Sources:
        """Read only owner-admitted exact files with hashes; reject symlinks.

        The caller here is trusted composition code, not a tool argument. This
        method neither accepts nor discovers arbitrary source roots via MCP.
        """
        if root.is_symlink() or root.is_junction() or not root.is_dir():
            raise ReadError("SOURCE_NOT_ADMITTED")
        root = root.resolve()
        admitted: dict[str, bytes] = {}
        for relative, expected in manifest.items():
            # Validate path and extension before touching the filesystem.
            cls({relative: b""}, commit)
            path = root / relative
            for part in (path, *path.parents):
                if part.is_symlink() or part.is_junction():
                    raise ReadError("SOURCE_NOT_ADMITTED")
                if part == root:
                    break
            try:
                if (
                    not path.resolve().is_relative_to(root)
                    or path.stat().st_size > 65536
                ):
                    raise ReadError("SOURCE_NOT_ADMITTED")
                content = path.read_bytes()
            except OSError as exc:
                raise ReadError("SOURCE_NOT_ADMITTED") from exc
            if hashlib.sha256(content).hexdigest() != expected:
                raise ReadError("SOURCE_VERSION_UNPROVEN")
            admitted[relative] = content
        return cls(admitted, commit, source_kind="reviewed_manifest")

    def info(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "commit": self.commit,
            "source_kind": self.source_kind,
            "files": {
                p: {"sha256": hashlib.sha256(b).hexdigest(), "bytes": len(b)}
                for p, b in self.files.items()
            },
        }

    def read(self, args: dict[str, Any]) -> dict[str, Any]:
        if args["source_id"] != self.source_id:
            raise ReadError("SOURCE_VERSION_UNPROVEN")
        content = self.files.get(args["path"])
        if content is None:
            raise ReadError("SOURCE_NOT_ADMITTED")
        lines = content.decode().splitlines()
        start, count = args.get("start_line", 1), args.get("line_count", 25)
        return {
            "path": args["path"],
            "commit": self.commit,
            "source_kind": self.source_kind,
            "source_id": self.source_id,
            "sha256": hashlib.sha256(content).hexdigest(),
            "start_line": start,
            "total_lines": len(lines),
            "text": "\n".join(lines[start - 1 : start - 1 + count]),
            "truncated": start - 1 + count < len(lines),
        }


class Gateway:
    def __init__(
        self,
        admission: Admission,
        repository: Repository,
        audit: AuditSink,
        sources: Sources,
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.admission, self.repository = admission, repository
        self.audit, self.sources, self.clock = audit, sources, clock
        self._cursor_key = secrets.token_bytes(32)
        self._revoked: set[str] = set()
        self._active_binding: str | None = None
        self._lock = threading.RLock()
        self._revocation_lock = threading.RLock()
        self._calls: dict[str, deque[float]] = defaultdict(deque)
        self._global_calls: deque[float] = deque()

    def revoke(self, binding: str) -> None:
        with self._revocation_lock:
            self._revoked.add(binding)
            if self._active_binding == binding:
                self.repository.interrupt()

    def _audit(self, event: dict[str, Any]) -> None:
        try:
            self.audit.append(event)
        except Exception as exc:
            raise ReadError("AUDIT_UNAVAILABLE") from exc

    @staticmethod
    def _validate(operation: str, args: dict[str, Any]) -> None:
        if operation not in TOOLS:
            raise ReadError("ACTION_NOT_REGISTERED")
        props, required = TOOLS[operation]
        if set(args) - set(props) or any(key not in args for key in required):
            raise ReadError("INVALID_ARGUMENTS")
        for key, value in args.items():
            spec = props[key]
            if spec["type"] == "integer":
                if (
                    type(value) is not int
                    or not spec["minimum"] <= value <= spec["maximum"]
                ):
                    raise ReadError("INVALID_ARGUMENTS")
            elif (
                not isinstance(value, str)
                or not spec["minLength"] <= len(value) <= spec["maxLength"]
                or ("enum" in spec and value not in spec["enum"])
            ):
                raise ReadError("INVALID_ARGUMENTS")
        for key in ("document_id", "tournament_id", "version_id"):
            if key in args:
                try:
                    if str(UUID(args[key])) != args[key]:
                        raise ValueError("noncanonical")
                except ValueError as exc:
                    raise ReadError("INVALID_ARGUMENTS") from exc

    def _principal(self, principal: Principal | None) -> Principal:
        if principal is None or not principal.valid(self.clock()):
            raise ReadError("AUTH_REQUIRED")
        with self._revocation_lock:
            if principal.actor_binding in self._revoked:
                raise ReadError("AUTH_REQUIRED")
        if principal.installation != self.admission.installation:
            raise ReadError("SCOPE_UNPROVEN")
        return principal

    def _limit(self, binding: str) -> None:
        now = time.monotonic()
        for queue in (self._calls[binding], self._global_calls):
            while queue and now - queue[0] >= 60:
                queue.popleft()
        if len(self._calls[binding]) >= 20 or len(self._global_calls) >= 60:
            raise ReadError("RATE_LIMIT")
        self._calls[binding].append(now)
        self._global_calls.append(now)

    def call(
        self,
        operation: str,
        args: dict[str, Any],
        principal: Principal | None,
    ) -> dict[str, Any]:
        started = time.monotonic()
        correlation = str(uuid4())
        event: dict[str, Any] = {
            "correlation_id": correlation,
            "operation": operation if operation in TOOLS else "unknown",
            "policy_version": self.admission.policy_version,
            "at_utc": self.clock().isoformat(),
            "source_kind": self.sources.source_kind,
        }
        self._audit({**event, "phase": "attempt"})
        if not self._lock.acquire(timeout=5):
            raise ReadError("TIMEOUT")
        try:
            self.admission.check()
            actor = self._principal(principal)
            with self._revocation_lock:
                self._active_binding = actor.actor_binding
            event["actor_binding_digest"] = hashlib.sha256(
                actor.actor_binding.encode()
            ).hexdigest()
            self._validate(operation, args)
            if (
                args.get("tournament_id")
                and actor.scope_policy != "live_informes_v1"
                and args["tournament_id"] not in actor.tournaments
            ):
                raise ReadError("NOT_FOUND_OR_NOT_VISIBLE")
            if self.repository.schema_fingerprint != self.admission.schema_fingerprint:
                raise ReadError("SCHEMA_MISMATCH")
            if self.sources.commit != self.admission.source_commit:
                raise ReadError("SOURCE_VERSION_UNPROVEN")
            self._limit(actor.actor_binding)
            result = self._dispatch(operation, args, actor)
            self._principal(actor)
            if time.monotonic() - started > 5:
                raise ReadError("TIMEOUT")
            envelope = {
                "correlation_id": correlation,
                "observed_at_utc": self.clock().isoformat(),
                "source_kind": self.sources.source_kind,
                "reviewed_commit": self.admission.source_commit,
                "deployed_commit": (
                    self.admission.source_commit
                    if self.admission.mode == "production"
                    and self.sources.source_kind == "reviewed_manifest"
                    else None
                ),
                "schema_fingerprint": self.admission.schema_fingerprint,
                "policy_version": self.admission.policy_version,
                "scope_binding_id": hashlib.sha256(
                    actor.generation.encode()
                ).hexdigest(),
                "coverage": (
                    "scoped_production_read"
                    if self.admission.mode == "production"
                    else "scoped_synthetic_projection"
                ),
                "data": result,
                "gaps": (
                    []
                    if self.admission.mode == "production"
                    else ["production_source_and_scope_not_admitted"]
                ),
            }
            encoded = json.dumps(envelope).encode()
            if len(encoded) > 65536:
                raise ReadError("OUTPUT_LIMIT")
            self._audit(
                {
                    **event,
                    "phase": "complete",
                    "decision": "allow",
                    "bytes": len(encoded),
                    "duration_ms": round((time.monotonic() - started) * 1000),
                }
            )
            revalidate = getattr(self.repository, "revalidate", None)
            if revalidate is not None:
                revalidate(actor)
            with self._revocation_lock:
                self._principal(actor)
                return envelope
        except ReadError as exc:
            self._audit(
                {**event, "phase": "complete", "decision": "deny", "reason": exc.code}
            )
            raise
        except Exception as exc:
            self._audit(
                {
                    **event,
                    "phase": "complete",
                    "decision": "deny",
                    "reason": "SOURCE_UNAVAILABLE",
                }
            )
            raise ReadError("SOURCE_UNAVAILABLE") from exc
        finally:
            with self._revocation_lock:
                self._active_binding = None
            self._lock.release()

    def _context(self, operation: str, args: dict[str, Any], actor: Principal) -> str:
        value = [
            operation,
            {k: v for k, v in args.items() if k != "cursor"},
            actor.actor_binding,
            actor.installation,
            sorted(actor.tournaments),
            actor.generation,
            self.admission.schema_fingerprint,
            self.admission.policy_version,
            self.sources.source_id,
        ]
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()

    def _cursor(
        self, operation: str, args: dict[str, Any], actor: Principal
    ) -> tuple[str, str, int]:
        if "cursor" not in args:
            return "", "", 0
        try:
            decoded = base64.urlsafe_b64decode(args["cursor"].encode())
            body, signature = decoded[:-32], decoded[-32:]
            if not hmac.compare_digest(
                signature, hmac.digest(self._cursor_key, body, "sha256")
            ):
                raise ValueError("signature")
            value = json.loads(body)
            if value["context"] != self._context(operation, args, actor):
                raise ValueError("binding")
            if value["expires"] <= self.clock().timestamp():
                raise ValueError("expired")
            return str(value["date"]), str(value["id"]), int(value["seen"])
        except (ValueError, KeyError, TypeError) as exc:
            raise ReadError("INVALID_CURSOR") from exc

    def _next(
        self,
        operation: str,
        args: dict[str, Any],
        actor: Principal,
        row: dict[str, Any],
        seen: int,
        date_key: str,
        id_key: str,
    ) -> str:
        body = json.dumps(
            {
                "context": self._context(operation, args, actor),
                "expires": self.clock().timestamp() + 600,
                "date": row[date_key],
                "id": row[id_key],
                "seen": seen,
            },
            sort_keys=True,
        ).encode()
        return base64.urlsafe_b64encode(
            body + hmac.digest(self._cursor_key, body, "sha256")
        ).decode()

    def _dispatch(
        self, operation: str, args: dict[str, Any], actor: Principal
    ) -> dict[str, Any]:
        if operation == "samchat.source_info":
            return self.sources.info()
        if operation == "samchat.source_read":
            return self.sources.read(args)
        if operation in {"samchat.document_status", "samchat.document_diagnose"}:
            document = self.repository.document(actor, args["document_id"])
            authority = self.repository.authority(actor, args["document_id"])
            if any(
                v != self.admission.policy_version for v in authority["policy_versions"]
            ):
                raise ReadError("SOURCE_VERSION_UNPROVEN")
            if operation.endswith("diagnose"):
                return {
                    "document": document,
                    "diagnosis": diagnose(document, authority),
                }
            return {"document": document, "authority": authority}
        scope, params = self.repository.scope(actor)
        if operation == "samchat.resolve_reference":
            column = {
                "operations": "reference_operations",
                "samchat": "reference_samchat",
                "base": "reference_base",
            }[args["reference_kind"]]
            params["ref"] = args["reference"]
            if args.get("tournament_id"):
                scope += " AND tournament_id=:tournament"
                params["tournament"] = args["tournament_id"]
            rows = self.repository.select(
                f"SELECT document_id,tournament_id,kind,state FROM document_status WHERE {scope} AND {column}=:ref ORDER BY document_id LIMIT 21",
                params,
                cap=21,
            )
            if not rows:
                raise ReadError("NOT_FOUND_OR_NOT_VISIBLE")
            if len(rows) > 20:
                raise ReadError("OUTPUT_LIMIT")
            return {
                "candidates": rows,
                "resolution": "unique" if len(rows) == 1 else "ambiguous",
                "selector_required": len(rows) != 1,
            }
        if operation == "samchat.document_audit":
            self.repository.document(actor, args["document_id"])
            params["document"] = args["document_id"]
            table, date_key, id_key = "document_audit", "occurred_at", "event_id"
            columns = "event_id,occurred_at,action,actor_alias"
            scope += " AND document_id=:document"
        else:
            try:
                start, end = date.fromisoformat(args["date_from"]), date.fromisoformat(
                    args["date_to"]
                )
            except ValueError as exc:
                raise ReadError("INVALID_ARGUMENTS") from exc
            if start >= end or (end - start).days > 366:
                raise ReadError("INVALID_ARGUMENTS")
            params.update(start=start.isoformat(), end=end.isoformat())
            if args.get("dataset") == "budget":
                if (
                    not args.get("version_id")
                    or not args.get("edition_year")
                    or any(
                        key in args for key in ("cursor", "group_by", "state", "kind")
                    )
                ):
                    raise ReadError("INVALID_ARGUMENTS")
                params["version"] = args["version_id"]
                if args.get("tournament_id"):
                    scope += " AND tournament_id=:tournament"
                    params["tournament"] = args["tournament_id"]
                rows = self.repository.select(
                    "SELECT tournament_id,version_id,period_start,period_end,"
                    "currency,approved_amount,consumed_amount,committed_amount,"
                    "available_amount,coverage FROM budget_availability WHERE "
                    f"{scope} AND version_id=:version AND period_start>=:start "
                    "AND period_end<=:end ORDER BY period_start,tournament_id LIMIT 101",
                    params,
                    cap=101,
                )
                if len(rows) > 100:
                    raise ReadError("OUTPUT_LIMIT")
                if not rows:
                    raise ReadError("NOT_FOUND_OR_NOT_VISIBLE")
                return {
                    "rows": rows,
                    "truncated": False,
                    "amount_basis": "synthetic_owner_projection_not_live_availability",
                }
            if "version_id" in args:
                raise ReadError("INVALID_ARGUMENTS")
            scope += " AND created_at >= :start AND created_at < :end"
            for key in ("state", "kind", "tournament_id"):
                if key in args:
                    scope += f" AND {key}=:{key}"
                    params[key] = args[key]
            if "group_by" in args:
                if "cursor" in args:
                    raise ReadError("INVALID_ARGUMENTS")
                group = {
                    "state": "state",
                    "kind": "kind",
                    "month": "substr(created_at,1,7)",
                    "tournament_id": "tournament_id",
                }[args["group_by"]]
                rows = self.repository.select(
                    f"SELECT {group} AS group_key,currency,COUNT(*) AS documents,SUM(amount) AS known_amount,COUNT(amount) AS amount_coverage FROM document_status WHERE {scope} GROUP BY {group},currency ORDER BY group_key,currency LIMIT 101",
                    params,
                    cap=101,
                )
                if len(rows) > 100:
                    raise ReadError("OUTPUT_LIMIT")
                return {
                    "groups": rows,
                    "truncated": False,
                    "amount_basis": "synthetic_document_amount_not_canonical_financial_actuals",
                }
            table, date_key, id_key = "document_status", "created_at", "document_id"
            columns = "document_id,tournament_id,kind,state,created_at,currency,amount"
        after_date, after_id, seen = self._cursor(operation, args, actor)
        page = args.get("page_size", 25)
        if seen + page > 1000:
            raise ReadError("OUTPUT_LIMIT")
        params.update(after_date=after_date, after_id=after_id, page=page + 1)
        rows = self.repository.select(
            f"SELECT {columns} FROM {table} WHERE {scope} AND ({date_key} > :after_date OR ({date_key}=:after_date AND {id_key}>:after_id)) ORDER BY {date_key},{id_key} LIMIT :page",
            params,
            cap=101,
        )
        more, rows = len(rows) > page, rows[:page]
        return {
            "rows": rows,
            "truncated": more,
            "next_cursor": (
                self._next(
                    operation, args, actor, rows[-1], seen + len(rows), date_key, id_key
                )
                if more and seen + len(rows) < 1000
                else None
            ),
            "coverage": (
                "row_cap_reached" if more and seen + len(rows) == 1000 else "page"
            ),
        }
