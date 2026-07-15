"""Persistence-free CTT shadow observation for the registration intake bot."""

from __future__ import annotations

import asyncio
import inspect
import io
import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Optional, Sequence, Set, Tuple

from PIL import Image

from devnous.tournaments.core.ctt_canary import (
    CttCanaryExecution,
    CttCanaryMode,
    CttCanaryPolicy,
    CttCanaryReport,
    CttCanaryRunner,
    ctt_canary_mode_from_env,
    ctt_document_sha256,
)
from devnous.tournaments.core.ctt_extraction_cache import (
    CttCachedResponsesExtractor,
    CttDraftCache,
)
from devnous.tournaments.core.ctt_responses_extractor import (
    DEFAULT_CTT_RESPONSES_MODEL,
    CttResponsesExtractor,
)

logger = logging.getLogger(__name__)

MAX_SHADOW_PAGES = 3
MAX_PENDING_CHATS = 50
MAX_PAGE_BYTES = 12 * 1024 * 1024
MAX_TOTAL_BUFFER_BYTES = 128 * 1024 * 1024
MAX_BUFFER_AGE_SECONDS = 10 * 60

CanonicalReviewHandler = Callable[
    [str, CttCanaryExecution, Sequence[bytes], Dict[str, Any]], Awaitable[bool]
]


class CttRegistrationShadowObserver:
    """Buffer one CTT document and evaluate it without affecting intake results."""

    def __init__(
        self,
        *,
        enabled: bool,
        api_key: Optional[str] = None,
        layout_path: Optional[Path] = None,
        model: str = DEFAULT_CTT_RESPONSES_MODEL,
        minimum_players: int = 16,
        handoff_enabled: bool = False,
        result_handler: Optional[CanonicalReviewHandler] = None,
    ) -> None:
        self.enabled = bool(enabled)
        self.api_key = (api_key or "").strip()
        self.layout_path = Path(layout_path) if layout_path else None
        self.model = model.strip() or DEFAULT_CTT_RESPONSES_MODEL
        self.minimum_players = max(1, min(25, int(minimum_players)))
        self.handoff_enabled = bool(handoff_enabled)
        self._result_handler = result_handler
        self._pages_by_chat: Dict[int, list[bytes]] = {}
        self._touched_at: Dict[int, float] = {}
        self._tasks: Set[asyncio.Task[CttCanaryReport]] = set()

        if self.enabled and (not self.api_key or self.layout_path is None):
            raise ValueError(
                "enabled CTT shadow observation requires API key and layout"
            )

    @classmethod
    def from_environment(cls) -> "CttRegistrationShadowObserver":
        """Create a fail-closed observer from the registration runtime environment."""
        mode = ctt_canary_mode_from_env()
        if mode == CttCanaryMode.ACTIVE:
            logger.error(
                "CTT active rollout is not permitted by the registration shadow bridge; "
                "observation disabled"
            )
            return cls(enabled=False)
        if mode != CttCanaryMode.SHADOW:
            return cls(enabled=False)

        api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
        default_layout = (
            Path(__file__).resolve().parents[5] / "config" / "layout_ctt_2026.json"
        )
        layout_path = Path(os.getenv("CTT_LAYOUT_PATH") or default_layout)
        if not api_key:
            logger.error(
                "CTT shadow observation disabled because OPENAI_API_KEY is missing"
            )
            return cls(enabled=False)
        if not layout_path.is_file():
            logger.error(
                "CTT shadow observation disabled because its layout is unavailable"
            )
            return cls(enabled=False)

        raw_minimum = (os.getenv("CTT_SHADOW_MINIMUM_PLAYERS") or "16").strip()
        try:
            minimum_players = int(raw_minimum)
        except ValueError:
            minimum_players = 16
        return cls(
            enabled=True,
            api_key=api_key,
            layout_path=layout_path,
            model=os.getenv("CTT_RESPONSES_MODEL") or DEFAULT_CTT_RESPONSES_MODEL,
            minimum_players=minimum_players,
            handoff_enabled=(
                (os.getenv("CTT_SHADOW_REVIEW_HANDOFF") or "off").strip().lower()
                in {"1", "true", "on", "yes"}
            ),
        )

    def bind_result_handler(self, handler: CanonicalReviewHandler) -> None:
        """Attach the quarantined review sink owned by the intake runtime."""
        self._result_handler = handler

    @property
    def pending_chat_count(self) -> int:
        """Return the bounded number of documents held only in memory."""
        return len(self._pages_by_chat)

    @property
    def buffered_bytes(self) -> int:
        """Return the total page bytes currently retained in memory."""
        return sum(
            len(payload) for pages in self._pages_by_chat.values() for payload in pages
        )

    async def capture_page(
        self,
        chat_id: int,
        payload: bytes,
        *,
        review_session_id: Optional[str] = None,
    ) -> bool:
        """Buffer a validated page and auto-finalize a complete three-page document."""
        if not self.enabled:
            return False
        if not payload or len(payload) > MAX_PAGE_BYTES:
            logger.warning("CTT shadow page rejected by byte-size boundary")
            return False

        self._purge_stale()
        normalized_chat_id = int(chat_id)
        pages = self._pages_by_chat.get(normalized_chat_id)
        if pages is None:
            if len(self._pages_by_chat) >= MAX_PENDING_CHATS:
                logger.warning("CTT shadow buffer is full; page ignored")
                return False
            pages = []
            self._pages_by_chat[normalized_chat_id] = pages
        if len(pages) >= MAX_SHADOW_PAGES:
            logger.warning("CTT shadow page limit reached; page ignored")
            return False
        if self.buffered_bytes + len(payload) > MAX_TOTAL_BUFFER_BYTES:
            logger.warning("CTT shadow global byte boundary reached; page ignored")
            return False

        pages.append(bytes(payload))
        self._touched_at[normalized_chat_id] = time.monotonic()
        if len(pages) == MAX_SHADOW_PAGES:
            await self.finalize(
                normalized_chat_id,
                review_session_id=review_session_id,
            )
        return True

    async def finalize(
        self, chat_id: int, *, review_session_id: Optional[str] = None
    ) -> bool:
        """Detach a two- or three-page buffer and evaluate it in the background."""
        self._purge_stale()
        normalized_chat_id = int(chat_id)
        pages = tuple(self._pages_by_chat.pop(normalized_chat_id, []))
        self._touched_at.pop(normalized_chat_id, None)
        if not self.enabled or len(pages) not in (2, 3):
            return False

        task = asyncio.create_task(
            self._execute_and_log(
                pages,
                review_session_id=review_session_id,
            ),
            name="ctt-shadow",
        )
        self._tasks.add(task)
        task.add_done_callback(self._consume_task)
        return True

    async def discard(self, chat_id: int) -> None:
        """Forget an unfinished document without calling the provider."""
        normalized_chat_id = int(chat_id)
        self._pages_by_chat.pop(normalized_chat_id, None)
        self._touched_at.pop(normalized_chat_id, None)

    async def drain(self) -> None:
        """Wait for currently scheduled observations, primarily for tests and shutdown."""
        tasks = tuple(self._tasks)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def close(self) -> None:
        """Clear buffered pages and cancel any in-flight observations."""
        self._pages_by_chat.clear()
        self._touched_at.clear()
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _consume_task(self, task: asyncio.Task[CttCanaryReport]) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        try:
            task.result()
        except Exception:
            logger.exception("CTT shadow observation failed")

    def _purge_stale(self) -> None:
        cutoff = time.monotonic() - MAX_BUFFER_AGE_SECONDS
        stale_chat_ids = [
            chat_id
            for chat_id, touched_at in self._touched_at.items()
            if touched_at < cutoff
        ]
        for chat_id in stale_chat_ids:
            self._pages_by_chat.pop(chat_id, None)
            self._touched_at.pop(chat_id, None)
        if stale_chat_ids:
            logger.info("Expired %s stale CTT shadow buffer(s)", len(stale_chat_ids))

    async def _execute_and_log(
        self,
        payloads: Tuple[bytes, ...],
        *,
        review_session_id: Optional[str],
    ) -> CttCanaryReport:
        execution, layout = await self._execute(payloads)
        report = execution.report
        logger.info("CTT shadow report: %s", report.model_dump_json())
        if (
            self.handoff_enabled
            and self._result_handler is not None
            and review_session_id
            and report.accepted
            and execution.draft is not None
        ):
            persisted = await self._result_handler(
                str(review_session_id),
                execution,
                payloads,
                layout,
            )
            logger.info(
                "CTT canonical review handoff: persisted=%s",
                bool(persisted),
            )
        return report

    async def _execute(
        self, payloads: Sequence[bytes]
    ) -> Tuple[CttCanaryExecution, Dict[str, Any]]:
        if not self.layout_path:
            raise RuntimeError("CTT shadow layout is unavailable")
        layout = json.loads(self.layout_path.read_text(encoding="utf-8"))
        images = []
        for payload in payloads:
            with Image.open(io.BytesIO(payload)) as image:
                images.append(image.convert("RGB"))

        extractor = CttResponsesExtractor.from_api_key(
            self.api_key,
            model=self.model,
        )
        try:
            with tempfile.TemporaryDirectory(prefix="samchat-ctt-shadow-") as cache_dir:
                cached = CttCachedResponsesExtractor(
                    extractor,
                    CttDraftCache(Path(cache_dir)),
                    attempts=1,
                )
                execution = await CttCanaryRunner(
                    cached,
                    mode=CttCanaryMode.SHADOW,
                    policy=CttCanaryPolicy(minimum_players=self.minimum_players),
                ).run(
                    images,
                    layout,
                    document_sha256=ctt_document_sha256(payloads),
                )
                return execution, layout
        finally:
            for image in images:
                image.close()
            close = getattr(extractor.client, "close", None)
            if callable(close):
                close_result: Any = close()
                if inspect.isawaitable(close_result):
                    await close_result
