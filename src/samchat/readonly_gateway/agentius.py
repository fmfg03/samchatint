"""Agentius Executor call/catalog adapter, not an installed server binding."""

from __future__ import annotations

import copy
from typing import Any, Callable, Protocol, cast

from .catalog import TOOLS, tools_list
from .contracts import Admission, AuditSink, Principal, ReadError
from .diagnosis import diagnose
from .gateway import Gateway, Sources
from .postgres import PostgresCanonicalAdapter
from .repository import Repository


class CanonicalGateway(Gateway):
    """Reuse the tested perimeter with the canonical PostgreSQL backend."""

    def __init__(
        self,
        admission: Admission,
        backend: PostgresCanonicalAdapter,
        audit: AuditSink,
        sources: Sources,
    ) -> None:
        if admission.mode == "production":
            admission.check()
            backend.binding.check()
            if (
                admission.activation_receipt_sha256
                != backend.binding.activation_receipt_sha256
                or admission.installation != backend.binding.installation
                or admission.schema_fingerprint != backend.binding.expected_schema
                or admission.policy_version != backend.binding.owner_version
            ):
                raise ReadError("ACTIVATION_REQUIRED")
        super().__init__(admission, cast(Repository, backend), audit, sources)
        self.backend = backend

    def _dispatch(
        self, operation: str, args: dict[str, Any], actor: Principal
    ) -> dict[str, Any]:
        if operation in {"samchat.document_status", "samchat.document_diagnose"}:
            document, authority = self.backend.observation(actor, args["document_id"])
            if authority["policy_versions"] != [self.admission.policy_version]:
                raise ReadError("SOURCE_VERSION_UNPROVEN")
            if operation.endswith("diagnose"):
                return {
                    "document": document,
                    "diagnosis": diagnose(document, authority),
                }
            return {"document": document, "authority": authority}
        if operation == "samchat.resolve_reference":
            return self.backend.reference(actor, args)
        if operation == "samchat.study":
            after_date, after_id, seen = self._cursor(operation, args, actor)
            page = args.get("page_size", 25)
            if seen + page > 1000:
                raise ReadError("OUTPUT_LIMIT")
            result = self.backend.study(
                actor, args, after_date=after_date, after_id=after_id
            )
            rows = result.get("rows", [])
            if result["truncated"] and rows and seen + len(rows) < 1000:
                result["next_cursor"] = self._next(
                    operation,
                    args,
                    actor,
                    rows[-1],
                    seen + len(rows),
                    "created_at",
                    "document_id",
                )
            return result
        if operation == "samchat.document_audit":
            after_date, after_id, seen = self._cursor(operation, args, actor)
            page = args.get("page_size", 25)
            if seen + page > 1000:
                raise ReadError("OUTPUT_LIMIT")
            rows = self.backend.audit(
                actor, args["document_id"], page, after_date, after_id
            )
            more, rows = len(rows) > page, rows[:page]
            return {
                "rows": rows,
                "truncated": more,
                "next_cursor": (
                    self._next(
                        operation,
                        args,
                        actor,
                        rows[-1],
                        seen + len(rows),
                        "occurred_at",
                        "event_id",
                    )
                    if more and seen + len(rows) < 1000
                    else None
                ),
            }
        return super()._dispatch(operation, args, actor)


class ExecutorPort(Protocol):
    tools: dict[str, Any]

    def call(self, name: str, args: dict[str, Any]) -> Any: ...


class AgentiusReadSlice:
    """Decorator matches inspected Executor.tools tuple and call interfaces.

    Register outside the legacy all-string validator; never mount by default.
    The real bridge's tools/list annotations must consume annotated_catalog().
    """

    def __init__(
        self,
        executor: ExecutorPort,
        gateway: Gateway | None = None,
        resolve_principal: Callable[[], Principal | None] | None = None,
    ) -> None:
        self.executor, self.gateway = executor, gateway
        self.allowed_tools = getattr(gateway, "allowed_tools", frozenset(TOOLS))
        self.resolve_principal = resolve_principal or (lambda: None)
        if gateway and set(executor.tools).intersection(TOOLS):
            raise ReadError("TOOL_COLLISION")
        self.tools = dict(executor.tools)
        if gateway:
            self.tools.update(
                {
                    name: (properties, required, "Governed readonly query.")
                    for name, (properties, required) in TOOLS.items()
                    if name in self.allowed_tools
                }
            )

    def call(self, name: str, args: dict[str, Any]) -> Any:
        if name in TOOLS:
            if not self.gateway:
                raise ReadError("ACTIVATION_REQUIRED")
            return self.gateway.call(name, args, self.resolve_principal())
        if (
            name.startswith("samchat.")
            or name == "transfer.create_draft"
            or name.endswith("create_draft_transfer")
        ):
            raise ReadError("UNKNOWN_TOOL")
        return self.executor.call(name, args)

    def annotated_catalog(self) -> list[dict[str, Any]]:
        readonly_legacy = {
            "server_info",
            "list_projects",
            "read_file",
            "job_status",
            "work_status",
            "work_list",
            "work_artifact",
        }
        destructive_legacy = {"write_file", "work_cancel"}
        catalog: list[dict[str, Any]] = []
        for name, value in self.executor.tools.items():
            if name in TOOLS:
                continue
            if not isinstance(value, tuple) or len(value) != 3:
                raise ReadError("SOURCE_VERSION_UNPROVEN")
            properties, required, description = value
            catalog.append(
                {
                    "name": name,
                    "description": description,
                    "inputSchema": {
                        "type": "object",
                        "properties": properties,
                        "required": required,
                        "additionalProperties": False,
                    },
                    "annotations": {
                        "readOnlyHint": name in readonly_legacy,
                        "destructiveHint": name in destructive_legacy,
                        "openWorldHint": False,
                    },
                }
            )
        if self.gateway:
            for item in tools_list():
                if item["name"] not in self.allowed_tools:
                    continue
                item = copy.deepcopy(item)
                if len(self.allowed_tools) == 5 and item["name"] == "samchat.study":
                    properties = item["inputSchema"]["properties"]
                    properties["dataset"]["enum"] = ["documents"]
                    properties.pop("version_id", None)
                    properties.pop("edition_year", None)
                catalog.append(item)
        return catalog


def install_read_slice(
    server: Any,
    gateway: Gateway,
    resolve_principal: Callable[[], Principal | None],
) -> AgentiusReadSlice:
    """Decorate an already constructed bridge server without starting it.

    The bridge's ``tools/list`` branch must use
    ``server.executor.annotated_catalog()``. This helper does not modify or
    restart the active service and is safe to exercise against a fixture.
    """
    executor = getattr(server, "executor", None)
    if executor is None:
        raise ReadError("SOURCE_VERSION_UNPROVEN")
    decorated = AgentiusReadSlice(executor, gateway, resolve_principal)
    server.executor = decorated
    return decorated
