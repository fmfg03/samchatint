from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, Optional
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

from .config import TournamentsV2Config


class TournamentsV2Error(RuntimeError):
    pass


class SupabaseRestClient:
    def __init__(self, config: TournamentsV2Config):
        self.config = config

    def _api_key(self) -> str:
        return self.config.service_role_key or self.config.anon_key

    def _headers(self, *, include_json: bool = True, prefer_return: bool = False) -> Dict[str, str]:
        api_key = self._api_key()
        if not self.config.supabase_url:
            raise TournamentsV2Error("SUPABASE_URL is not configured")
        if not api_key:
            raise TournamentsV2Error("Supabase API key is not configured")
        headers = {
            "apikey": api_key,
            "Authorization": f"Bearer {api_key}",
        }
        if include_json:
            headers["Content-Type"] = "application/json"
        if prefer_return:
            headers["Prefer"] = "return=representation"
        return headers

    def _request_sync(
        self,
        *,
        method: str,
        path: str,
        query: Optional[Dict[str, str]] = None,
        payload: Optional[Any] = None,
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> Any:
        base = self.config.supabase_url.rstrip("/")
        qs = f"?{urllib_parse.urlencode(query)}" if query else ""
        url = f"{base}/rest/v1/{path.lstrip('/')}{qs}"
        data = None
        headers = self._headers(
            include_json=payload is not None or method.upper() in {"POST", "PATCH", "PUT"},
            prefer_return=method.upper() in {"POST", "PATCH", "PUT"},
        )
        if extra_headers:
            headers.update(extra_headers)
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib_request.Request(
            url=url,
            headers=headers,
            data=data,
            method=method.upper(),
        )
        try:
            with urllib_request.urlopen(req, timeout=self.config.request_timeout_sec) as res:
                body = res.read().decode("utf-8", errors="replace")
                if not body:
                    return []
                return json.loads(body)
        except urllib_error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise TournamentsV2Error(f"Supabase REST error ({exc.code}): {detail}") from exc
        except urllib_error.URLError as exc:
            raise TournamentsV2Error(f"Supabase REST unreachable: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise TournamentsV2Error("Supabase returned invalid JSON") from exc

    async def request(
        self,
        *,
        method: str,
        path: str,
        query: Optional[Dict[str, str]] = None,
        payload: Optional[Any] = None,
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> Any:
        return await asyncio.to_thread(
            self._request_sync,
            method=method,
            path=path,
            query=query,
            payload=payload,
            extra_headers=extra_headers,
        )

    async def select_rows(
        self,
        *,
        table: str,
        select_expr: str = "*",
        filters: Optional[Dict[str, str]] = None,
        order: Optional[str] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        query: Dict[str, str] = {"select": select_expr}
        if filters:
            query.update(filters)
        if order:
            query["order"] = order
        if limit is not None:
            query["limit"] = str(int(limit))
        if offset is not None:
            query["offset"] = str(int(offset))
        rows = await self.request(method="GET", path=table, query=query)
        return list(rows or [])

    async def fetch_all_rows(
        self,
        *,
        table: str,
        select_expr: str = "*",
        filters: Optional[Dict[str, str]] = None,
        order: Optional[str] = None,
        batch_size: Optional[int] = None,
        max_rows: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        batch_size = max(1, int(batch_size or self.config.page_size))
        max_rows = max(1, int(max_rows or self.config.max_rows))
        offset = 0
        rows: list[dict[str, Any]] = []
        while offset < max_rows:
            chunk = await self.select_rows(
                table=table,
                select_expr=select_expr,
                filters=filters,
                order=order,
                limit=min(batch_size, max_rows - offset),
                offset=offset,
            )
            if not chunk:
                break
            rows.extend(chunk)
            if len(chunk) < batch_size:
                break
            offset += len(chunk)
        return rows

    async def insert_rows(
        self,
        *,
        table: str,
        payload: Any,
        on_conflict: Optional[str] = None,
        merge_duplicates: bool = False,
    ) -> list[dict[str, Any]]:
        query: Dict[str, str] = {}
        headers: Dict[str, str] = {}
        if on_conflict:
            query["on_conflict"] = on_conflict
        if merge_duplicates:
            headers["Prefer"] = "resolution=merge-duplicates,return=representation"
        rows = await self.request(
            method="POST",
            path=table,
            query=query or None,
            payload=payload,
            extra_headers=headers or None,
        )
        return list(rows or [])
