"""Admitted source reads and pure checks, not a shell/log/process interface."""

from __future__ import annotations

import asyncio
import hashlib
import re
from typing import Any

from .contracts import ReadError
from .expanded import Authority, ExpandedReads, digest
from .gateway import Sources


def redact_source(text: str) -> str:
    """Defence in depth after exact source-manifest admission; not a secret scanner."""
    text = re.sub(
        r"-----BEGIN .*?PRIVATE KEY-----.*?-----END .*?PRIVATE KEY-----",
        "[REDACTED_PRIVATE_KEY]",
        text,
        flags=re.S,
    )
    text = re.sub(
        r"(?i)\b(?:Bearer\s+|(?:postgres(?:ql)?|mysql)://)[^\s'\"]+",
        "[REDACTED_CREDENTIAL]",
        text,
    )
    text = re.sub(
        r"(?i)((?:password|secret|api[_-]?key|token|credential)\s*[:=]\s*)[^\s,;]+",
        r"\1[REDACTED]",
        text,
    )
    return re.sub(r"(?<![\w-])(?:\d[ -]?){12,19}(?![\w-])", "[REDACTED_ACCOUNT]", text)


class TechnicalReads:
    """Uses the existing immutable Sources manifest, under separate source scope.

    Async callers must use call_async; both entries require technical.source
    explicitly. CanonicalAuthorityResolver grants financial scope only, so it
    still cannot authorize these reads. No financial-to-source scope fallback.
    Only source-manifest and adapter-bindings checks are admitted. No executable
    checks, path discovery, free grep, environment, process or live log access.
    """

    def __init__(self, perimeter: ExpandedReads, sources: Sources) -> None:
        if sources.commit != perimeter.commit:
            raise ReadError("SOURCE_VERSION_UNPROVEN")
        self.perimeter, self.sources = perimeter, sources

    def _event(self, operation: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if type(operation) is not str:
            raise ReadError("INVALID_ARGUMENTS")
        event = {
            "operation": (
                operation
                if operation in {"source_read", "source_info", "diagnose"}
                else "unknown"
            ),
            "source_id": self.sources.source_id,
        }
        if isinstance(arguments, dict):
            if isinstance(arguments.get("path"), str):
                event["path_digest"] = digest(arguments["path"])
            if isinstance(arguments.get("check"), str) and arguments["check"] in {
                "source-manifest",
                "adapter-bindings",
            }:
                event["check"] = arguments["check"]
        return event

    def call(self, operation: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Synchronous compatibility entry; never bypass an async policy resolver."""
        p = self.perimeter
        event = self._event(operation, arguments)
        try:
            actor = p._authority()
            event.update(
                actor_digest=digest(actor.actor_binding),
                policy_version=actor.policy_version,
            )
            p._audit({**event, "decision": "attempt"})
            result = self._dispatch(operation, arguments, actor)
            p._revalidate(actor)
            p._audit({**event, "decision": "allow"})
            p._revalidate(actor)
            return result
        except ReadError as exc:
            p._audit({**event, "decision": "deny", "code": exc.code})
            raise
        except Exception as exc:
            p._audit({**event, "decision": "deny", "code": "SOURCE_UNAVAILABLE"})
            raise ReadError("SOURCE_UNAVAILABLE") from exc

    async def call_async(
        self, operation: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """Await fresh authority before access and again before/after allow audit.

        The supplied resolver remains responsible for authenticated technical
        scope. Supporting async execution does not create that scope or bind a
        transport. Cancellation and failed revalidation never return source data.
        """
        p = self.perimeter
        event = self._event(operation, arguments)
        try:
            actor = await p._authority_for(None)
            event.update(
                actor_digest=digest(actor.actor_binding),
                policy_version=actor.policy_version,
            )
            p._audit({**event, "decision": "attempt"})
            result = self._dispatch(operation, arguments, actor)
            await p._revalidate_for(actor, None)
            p._audit({**event, "decision": "allow"})
            await p._revalidate_for(actor, None)
            return result
        except asyncio.CancelledError:
            p._audit({**event, "decision": "cancelled"})
            raise
        except ReadError as exc:
            p._audit({**event, "decision": "deny", "code": exc.code})
            raise
        except Exception as exc:
            p._audit({**event, "decision": "deny", "code": "SOURCE_UNAVAILABLE"})
            raise ReadError("SOURCE_UNAVAILABLE") from exc

    def _dispatch(
        self, operation: str, arguments: dict[str, Any], actor: Authority
    ) -> dict[str, Any]:
        p = self.perimeter
        if not any(
            s.dataset == "technical.source"
            and self.sources.source_id in s.object_ids
            and s.version_id == self.sources.commit
            for s in actor.scopes
        ):
            raise ReadError("NOT_FOUND_OR_NOT_VISIBLE")
        if operation == "source_info" and arguments == {}:
            result = self.sources.info()
        elif operation == "source_read":
            if (
                type(arguments) is not dict
                or set(arguments) - {"source_id", "path", "start_line", "line_count"}
                or not {"source_id", "path"} <= set(arguments)
            ):
                raise ReadError("INVALID_ARGUMENTS")
            for key, maximum in (("start_line", 100000), ("line_count", 100)):
                value = arguments.get(key, 1 if key == "start_line" else 25)
                if type(value) is not int or not 1 <= value <= maximum:
                    raise ReadError("INVALID_ARGUMENTS")
            if (
                type(arguments["path"]) is not str
                or type(arguments["source_id"]) is not str
            ):
                raise ReadError("INVALID_ARGUMENTS")
            result = self.sources.read(arguments)
            result["text"] = redact_source(result["text"])
            result["returned_text_sha256"] = hashlib.sha256(
                result["text"].encode()
            ).hexdigest()
            result["redaction"] = (
                "credential_and_account_patterns_after_manifest_admission"
            )
        elif (
            operation == "diagnose"
            and type(arguments) is dict
            and set(arguments) == {"check"}
        ):
            if arguments["check"] == "source-manifest":
                result = {
                    "check": "source-manifest",
                    "source_id": self.sources.source_id,
                    "commit": self.sources.commit,
                    "file_count": len(self.sources.files),
                }
            elif arguments["check"] == "adapter-bindings":
                result = {
                    "check": "adapter-bindings",
                    "datasets": p._capabilities(actor)["datasets"],
                }
            else:
                raise ReadError("CHECK_NOT_ADMITTED")
        else:
            raise ReadError("ACTION_NOT_REGISTERED")
        return {
            "data": result,
            "source_version": self.sources.source_id,
            "observed_at_utc": p.clock().isoformat(),
            "gaps": ["reviewed_source_not_runtime_or_deployment_evidence"],
        }
