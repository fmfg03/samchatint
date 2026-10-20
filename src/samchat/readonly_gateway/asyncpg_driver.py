"""Fresh asyncpg connection adapter for the synchronous gateway boundary.

Credential and URL resolution deliberately stay in trusted composition code.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Awaitable, Callable, Mapping, Sequence

from .contracts import ReadError

Connector = Callable[[], Awaitable[Any]]


def positional_sql(
    sql: str, parameters: Mapping[str, Any] | None
) -> tuple[str, list[Any]]:
    """Convert closed internal pyformat parameters to asyncpg positions."""
    values: list[Any] = []
    indexes: dict[str, int] = {}
    supplied = parameters or {}

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in supplied:
            raise ReadError("SOURCE_UNAVAILABLE")
        if name not in indexes:
            indexes[name] = len(values) + 1
            values.append(supplied[name])
        return f"${indexes[name]}"

    converted = re.sub(r"%\(([A-Za-z_][A-Za-z0-9_]*)\)s", replace, sql)
    # Closed canonical queries carry ISO strings, whereas asyncpg's binary
    # timestamptz codec requires datetime. Force a bound TEXT cast first;
    # the database still parses the value, never SQL string interpolation.
    for index, value in enumerate(values, 1):
        if isinstance(value, str):
            converted = re.sub(
                rf"\${index}::timestamptz\b", f"${index}::text::timestamptz", converted
            )
    if "%(" in converted or set(supplied) - set(indexes):
        raise ReadError("SOURCE_UNAVAILABLE")
    return converted, values


class AsyncpgCursor:
    def __init__(self, connection: "AsyncpgConnection") -> None:
        self.connection = connection
        self.description: Sequence[Sequence[Any]] | None = None
        self._rows: list[tuple[Any, ...]] = []

    def execute(self, sql: str, parameters: Mapping[str, Any] | None = None) -> None:
        converted, values = positional_sql(sql, parameters)
        if converted.lstrip().upper().startswith("SELECT"):
            records = self.connection._run(
                self.connection.raw.fetch(converted, *values, timeout=3.0)
            )
            keys = list(records[0].keys()) if records else []
            self.description = [(key,) for key in keys]
            self._rows = [tuple(record[key] for key in keys) for record in records]
        else:
            self.connection._run(
                self.connection.raw.execute(converted, *values, timeout=3.0)
            )
            self.description = []
            self._rows = []

    def fetchmany(self, size: int) -> list[tuple[Any, ...]]:
        rows, self._rows = self._rows[:size], self._rows[size:]
        return rows

    def close(self) -> None:
        self._rows = []


class AsyncpgConnection:
    """Own one event loop, one fresh connection and deterministic cleanup."""

    def __init__(self, connector: Connector, *, connect_timeout: float = 5.0) -> None:
        self.loop = asyncio.new_event_loop()
        self.closed = False
        try:
            self.raw = self._run(connector(), timeout=connect_timeout)
        except Exception:
            self.loop.close()
            raise

    def _run(self, awaitable: Awaitable[Any], *, timeout: float = 5.0) -> Any:
        try:
            return self.loop.run_until_complete(asyncio.wait_for(awaitable, timeout))
        except asyncio.TimeoutError as exc:
            raise ReadError("TIMEOUT") from exc

    def cursor(self) -> AsyncpgCursor:
        if self.closed:
            raise ReadError("SOURCE_UNAVAILABLE")
        return AsyncpgCursor(self)

    def rollback(self) -> None:
        if not self.closed:
            self._run(self.raw.execute("ROLLBACK", timeout=3.0))

    def cancel(self) -> None:
        if not self.closed:
            # asyncpg has no stable public per-query cancel method. Termination
            # is the supported safe boundary here: the pool never reuses it.
            if self.loop.is_running():
                self.loop.call_soon_threadsafe(self.raw.terminate)
            else:
                self.raw.terminate()
            self.closed = True

    def close(self) -> None:
        if self.closed:
            if not self.loop.is_closed():
                self.loop.close()
            return
        try:
            self._run(self.raw.close(timeout=3.0), timeout=5.0)
        finally:
            self.closed = True
            self.loop.close()


class AsyncpgConnectionFactory:
    """Return a new non-pooled connection for every gateway transaction."""

    def __init__(self, connector: Connector) -> None:
        self.connector = connector

    def __call__(self) -> AsyncpgConnection:
        return AsyncpgConnection(self.connector)
