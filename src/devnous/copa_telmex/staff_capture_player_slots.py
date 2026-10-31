"""Resolve player source slots explicitly before building a capture effect."""

from dataclasses import dataclass
from typing import Mapping, Sequence
from uuid import UUID

from devnous.tournaments.core.ctt_batch_photo_alignment import parse_batch_slot

from .staff_capture_executor import CaptureDenied
from .staff_review_challenge import ReviewScope


@dataclass(frozen=True)
class ResolvedPlayerSlot:
    roster_index: int
    source_slot: int
    pdf_page: int
    asset_id: UUID


def resolve_player_slots(
    players: Sequence[Mapping[str, object]], scope: ReviewScope
) -> tuple[ResolvedPlayerSlot, ...]:
    """Bind selected rows to printed slots; never infer a missing reference.

    The selected player rows and scope must already come from protected review
    state. This helper does not approve their identity or payload.
    """
    try:
        if type(scope) is not ReviewScope or not isinstance(players, (list, tuple)):
            raise ValueError("invalid slots")
        pages = [page.pdf_page for page in scope.source_pages]
        seen = set()
        result = []
        for index, player in enumerate(players, 1):
            if not isinstance(player, Mapping):
                raise ValueError("invalid player")
            ref = player.get("slot_ref")
            parsed = parse_batch_slot(ref, pages)
            if parsed is None or parsed in seen:
                raise ValueError("ambiguous source slot")
            seen.add(parsed)
            page_index, slot = parsed
            asset = scope.source_pages[page_index - 1]
            if asset.page_index != page_index:
                raise ValueError("page mismatch")
            result.append(
                ResolvedPlayerSlot(index, slot, asset.pdf_page, asset.asset_id)
            )
        return tuple(result)
    except Exception:
        raise CaptureDenied("capture denied") from None
