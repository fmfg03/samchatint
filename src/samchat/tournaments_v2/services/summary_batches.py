"""Bounded transport for SOUL summaries using the canonical dataset readers.

Only core dataset reads are performed. Optional dossier detail is deliberately
empty; consumers must use this client for aggregate teams/players summaries only.
It never writes and never changes the canonical UUID/name/edition resolution.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from copy import deepcopy


class SummaryBatchClient:
    def __init__(self, client):
        self.client = client
        self.config = client.config
        self.pending = []
        self.task = None
        self.catalog_cache = {}
        self.waiters = set()

    async def select_rows(self, **kwargs):
        # These are optional managers/matches/media reads. None contributes to
        # the canonical entity dossier's teams_count or players_count.
        if kwargs.get("table") not in {
            "team_managers",
            "matches",
            "team_standings",
            "match_cedulas",
            "gallery_photos",
            "featured_videos",
            "live_streams",
            "email_inbox",
            "email_send_log",
            "scheduled_emails",
            "whatsapp_message_log",
            "whatsapp_templates",
        }:
            raise ValueError("Core summary reads must use bounded dataset batching")
        return []

    async def fetch_all_rows(self, **kwargs):
        future = asyncio.get_running_loop().create_future()
        self.waiters.add(future)
        future.add_done_callback(self.waiters.discard)
        self.pending.append((kwargs, future))
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
                pending, self.pending = self.pending, []
                groups = defaultdict(list)
                for kwargs, future in pending:
                    filters = kwargs.get("filters") or {}
                    field = next(iter(filters), None)
                    # Core datasets use exactly one UUID IN predicate. Reject
                    # another shape rather than broadening a query implicitly.
                    if filters and (
                        len(filters) != 1
                        or not filters[field].startswith("in.(")
                        or not filters[field].endswith(")")
                    ):
                        future.set_exception(ValueError("Unsupported summary scope"))
                        continue
                    common = {k: v for k, v in kwargs.items() if k != "filters"}
                    groups[(repr(sorted(common.items())), field)].append(
                        (kwargs, future)
                    )
                for (_, field), group in groups.items():
                    for offset in range(0, len(group), 25):
                        await self._dispatch(group[offset : offset + 25], field)
        except Exception as exc:
            for future in tuple(self.waiters):
                if not future.done():
                    future.set_exception(exc)
        finally:
            self.task = None

    async def _dispatch(self, requests, field):
        common = {k: v for k, v in requests[0][0].items() if k != "filters"}
        try:
            if field is None:
                # Canonical resolution reads the same capped catalog for each
                # UUID/name attempt; load it once per request, not per tournament.
                key = repr(sorted(common.items()))
                if key not in self.catalog_cache:
                    self.catalog_cache[key] = await self.client.fetch_all_rows(**common)
                for _, future in requests:
                    if not future.done():
                        future.set_result(deepcopy(self.catalog_cache[key]))
                return
            keys = [
                set(kwargs["filters"][field][4:-1].split(",")) for kwargs, _ in requests
            ]
            limit = max(1, int(common.pop("max_rows", None) or self.config.max_rows))
            page_size = max(
                1, int(common.pop("batch_size", None) or self.config.page_size)
            )
            # Track consumed rows by key so completed partitions can be removed
            # without resetting the offset of still-incomplete partitions.
            consumed = defaultdict(int)
            collected = [[] for _ in requests]
            while True:
                remaining = set().union(
                    *(k for k, rows in zip(keys, collected) if len(rows) < limit)
                )
                if not remaining:
                    break
                rows = await self.client.select_rows(
                    **common,
                    filters={field: "in.(" + ",".join(sorted(remaining)) + ")"},
                    offset=sum(consumed[k] for k in remaining),
                    limit=page_size,
                )
                for row in rows:
                    key = str(row.get(field) or "")
                    if key not in remaining:
                        raise ValueError("Summary source escaped its requested scope")
                    consumed[key] += 1
                    for selected, output in zip(keys, collected):
                        if key in selected and len(output) < limit:
                            output.append(row)
                if len(rows) < page_size:
                    break
            for (_, future), rows in zip(requests, collected):
                if not future.done():
                    future.set_result(deepcopy(rows))
        except Exception as exc:
            if len(requests) > 1:
                middle = len(requests) // 2
                await self._dispatch(requests[:middle], field)
                await self._dispatch(requests[middle:], field)
            else:
                if not requests[0][1].done():
                    requests[0][1].set_exception(exc)
