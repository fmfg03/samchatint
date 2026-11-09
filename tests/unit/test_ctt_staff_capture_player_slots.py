"""Physical source slots remain distinct from selected roster positions."""

import pytest

from devnous.copa_telmex.staff_capture_executor import CaptureDenied
from devnous.copa_telmex.staff_capture_player_slots import resolve_player_slots
from test_ctt_staff_capture_source_check import sample


def test_gaps_do_not_shift_player_photo_source():
    _, scope, _, _ = sample()
    rows = [
        {"slot_ref": "page:1:jugador_3"},
        {"slot_ref": "page:2:jugador_12"},
    ]
    resolved = resolve_player_slots(rows, scope)
    assert [(r.roster_index, r.source_slot, r.pdf_page) for r in resolved] == [
        (1, 3, 1),
        (2, 12, 2),
    ]
    assert resolved[1].asset_id == scope.source_pages[1].asset_id


@pytest.mark.parametrize(
    "rows",
    [
        [{}],
        [{"slot_ref": "page:3:jugador_3"}],
        [{"slot_ref": "page:1:jugador_9"}],
        [{"slot_ref": "page:1:jugador_3"}, {"slot_ref": "page:1:jugador_3"}],
    ],
)
def test_missing_invalid_or_duplicate_source_is_denied(rows):
    _, scope, _, _ = sample()
    with pytest.raises(CaptureDenied):
        resolve_player_slots(rows, scope)


def test_alternate_reference_spelling_cannot_duplicate_physical_slot():
    _, scope, _, _ = sample()
    with pytest.raises(CaptureDenied):
        resolve_player_slots(
            [
                {"slot_ref": "page:1:jugador_3"},
                {"slot_ref": "page:01:jugador_03"},
            ],
            scope,
        )
