"""Offline stdio JSON-RPC MCP adapter. Default startup does not admit reads.

Synthetic mode is an explicit command-line selection, not production enablement.
Trusted principal resolution is injected outside the MCP argument parser.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Callable, TextIO

from .catalog import tools_list
from .contracts import Principal, ReadError
from .gateway import Gateway


class Connector:
    def __init__(
        self,
        gateway: Gateway | None,
        resolve_principal: Callable[[], Principal | None],
    ) -> None:
        self.gateway, self.resolve_principal = gateway, resolve_principal

    def handle(self, request: dict[str, Any]) -> dict[str, Any] | None:
        request_id = request.get("id")
        if request.get("method") == "notifications/initialized":
            return None
        response: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id}
        try:
            if request.get("jsonrpc") != "2.0" or set(request) - {
                "jsonrpc",
                "id",
                "method",
                "params",
            }:
                raise ReadError("INVALID_REQUEST")
            method, params = request.get("method"), request.get("params", {})
            if not isinstance(params, dict):
                raise ReadError("INVALID_REQUEST")
            if method == "initialize":
                requested = params.get("protocolVersion")
                if requested not in {"2024-11-05", "2025-03-26", "2025-06-18"}:
                    raise ReadError("UNSUPPORTED_PROTOCOL")
                result: dict[str, Any] = {
                    "protocolVersion": requested,
                    "capabilities": {"tools": {}},
                    "serverInfo": {
                        "name": "samchat-readonly-candidate",
                        "version": "0.1.0",
                    },
                }
            elif method == "tools/list":
                if params:
                    raise ReadError("INVALID_ARGUMENTS")
                result = {"tools": tools_list() if self.gateway else []}
            elif method == "ping":
                result = {}
            elif method == "tools/call":
                if self.gateway is None:
                    raise ReadError("ACTIVATION_REQUIRED")
                if (
                    set(params) != {"name", "arguments"}
                    or not isinstance(params["arguments"], dict)
                    or not isinstance(params["name"], str)
                ):
                    raise ReadError("INVALID_ARGUMENTS")
                data = self.gateway.call(
                    params["name"], params["arguments"], self.resolve_principal()
                )
                result = {
                    "content": [{"type": "text", "text": json.dumps(data)}],
                    "structuredContent": data,
                    "isError": False,
                }
            else:
                raise ReadError("METHOD_NOT_FOUND")
            response["result"] = result
        except ReadError as exc:
            response["error"] = {"code": -32000, "message": exc.code}
        except Exception:
            response["error"] = {"code": -32000, "message": "SOURCE_UNAVAILABLE"}
        return response


def serve(connector: Connector, input_stream: TextIO, output_stream: TextIO) -> None:
    while True:
        line = input_stream.readline(65538)
        if not line:
            return
        if len(line.encode()) > 65536 or not line.endswith("\n"):
            # Do not parse a prefix of an oversized request as a second request.
            while line and not line.endswith("\n"):
                line = input_stream.readline(65538)
            output_stream.write(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32700, "message": "INPUT_LIMIT"},
                    }
                )
                + "\n"
            )
            output_stream.flush()
            continue
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("object required")
            response = connector.handle(request)
        except (ValueError, UnicodeError):
            response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "INVALID_JSON"},
            }
        if response is not None:
            encoded = json.dumps(response)
            if len(encoded.encode()) > 65536:
                encoded = json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": request.get("id"),
                        "error": {"code": -32000, "message": "OUTPUT_LIMIT"},
                    }
                )
            output_stream.write(encoded + "\n")
            output_stream.flush()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--synthetic-demo", action="store_true")
    args = parser.parse_args()
    if args.synthetic_demo:
        from .fixtures import fixture

        gateway, actor, _ = fixture()
        connector = Connector(gateway, lambda: actor)
    else:
        connector = Connector(None, lambda: None)
    serve(connector, sys.stdin, sys.stdout)


if __name__ == "__main__":
    main()
