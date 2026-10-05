"""Request-local batching of the existing canonical textual SELECT readers.

Statements and bound parameters come from domain owners, never from a model.
Batching changes transport only: each result keeps its original columns/types,
ORDER BY, LIMIT and scope predicate. There is no cache across requests.
"""

from __future__ import annotations

import asyncio
import re
from contextlib import asynccontextmanager
from copy import deepcopy

from sqlalchemy import text
from sqlalchemy.sql.elements import TextClause


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def all(self):
        return deepcopy(self.rows)

    def first(self):
        return deepcopy(self.rows[0]) if self.rows else None


class ReadBatch:
    """Coalesce a bounded group of canonical readers on one read-only session.

    Callers must bound the number of readers (25 in Direction). Each SQL round
    sends at most that many parameterized subqueries in one UNION ALL statement.
    Identical reads, such as the edition's version list, execute only once.
    A failing union is rolled back and split to isolate the failing source.
    """

    def __init__(self, session):
        self.session = session
        self.pending = []
        self.cache = {}
        self.task = None
        self.waiters = set()

    @asynccontextmanager
    async def begin_nested(self):
        # Real savepoints wrap each dispatched SQL batch below. A caller must
        # not open an overlapping savepoint on the shared underlying session.
        yield

    async def execute(self, statement, params=None):
        if not isinstance(statement, TextClause):
            raise TypeError("Direction batches accept canonical textual SELECTs only")
        sql = str(statement).strip()
        if not sql.upper().startswith(("SELECT", "WITH")) or ";" in sql:
            raise ValueError("Direction batches accept one read-only SELECT")
        params = params or {}
        key = (sql, repr(sorted(params.items())))
        if key in self.cache:
            return _Rows(self.cache[key])
        future = asyncio.get_running_loop().create_future()
        self.waiters.add(future)
        future.add_done_callback(self.waiters.discard)
        self.pending.append((sql, params, key, future))
        if self.task is None:
            self.task = asyncio.create_task(self._drain())
        return await future

    async def close(self):
        if self.task is not None:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        for future in tuple(self.waiters):
            future.cancel()
        self.pending.clear()

    async def _drain(self):
        try:
            while self.pending:
                await asyncio.sleep(0)
                requests, self.pending = self.pending, []
                groups = {}
                for request in requests:
                    groups.setdefault(request[2], []).append(request)
                unique = list(groups.values())
                for offset in range(0, len(unique), 25):
                    await self._dispatch(unique[offset : offset + 25])
        except Exception as exc:
            for future in tuple(self.waiters):
                if not future.done():
                    future.set_exception(exc)
        finally:
            self.task = None

    async def _dispatch(self, groups):
        branches, parameters = [], {}
        for index, group in enumerate(groups):
            sql, params, _, _ = group[0]
            # Rename actual bind tokens only; PostgreSQL ::casts are untouched.
            names = {name: f"batch_{index}_{name}" for name in params}
            sql = re.sub(
                r"(?<!:):([A-Za-z_][A-Za-z_0-9]*)\b",
                lambda match: ":" + names.get(match[1], match[1]),
                sql,
            )
            branches.append(
                f"SELECT {index} AS __direction_batch, row_number() OVER () AS __direction_order, read_result.* FROM ({sql}) read_result"
            )
            parameters.update({names[k]: v for k, v in params.items()})
        try:
            # Different canonical queries can have different output columns.
            # Only equal SQL shapes can share a UNION (see the split below).
            shapes = {group[0][0] for group in groups}
            if len(shapes) > 1:
                by_shape = {}
                for group in groups:
                    by_shape.setdefault(group[0][0], []).append(group)
                for same_shape in by_shape.values():
                    await self._dispatch(same_shape)
                return
            async with self.session.begin_nested():
                result = await self.session.execute(
                    text(
                        " UNION ALL ".join(branches)
                        + " ORDER BY __direction_batch, __direction_order"
                    ),
                    parameters,
                )
                rows = result.mappings().all()
        except Exception as exc:
            if len(groups) > 1:
                middle = len(groups) // 2
                await self._dispatch(groups[:middle])
                await self._dispatch(groups[middle:])
            else:
                for *_, future in groups[0]:
                    if not future.done():
                        future.set_exception(exc)
            return
        partitioned = [[] for _ in groups]
        for row in rows:
            value = dict(row)
            index = value.pop("__direction_batch")
            value.pop("__direction_order")
            partitioned[index].append(value)
        for group, values in zip(groups, partitioned):
            self.cache[group[0][2]] = values
            for *_, future in group:
                if not future.done():
                    future.set_result(_Rows(values))
