"""Reuse the real pure canonical service with synthetic input, without runtime."""

import copy
import importlib.util
import json
import sys
import unittest
from pathlib import Path

from samchat.private_plugin.adoption_preview import adoption_preview
from samchat.private_plugin.contracts import Denied

ROOT = Path(__file__).resolve().parents[3]
OWNER = (
    ROOT / "src/devnous/tournaments/instances/copa_telmex/ctt_canonical_promotion.py"
)
SPEC = importlib.util.spec_from_file_location("fixture_canonical_promotion", OWNER)
CANONICAL = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = CANONICAL
SPEC.loader.exec_module(CANONICAL)


class AdoptionPreviewTest(unittest.TestCase):
    def setUp(self):
        self.raw = {
            "canonical_shadow": {
                "schema_version": "ctt.canonical_review.v1",
                "accepted": True,
                "authoritative": False,
                "canonical_hash": "synthetic-canonical-hash",
                "document_sha256": "synthetic-source-hash",
                "team": {
                    "name": "Synthetic Team B",
                    "field_evidence": {"team_name": {"page": 1}},
                },
                "players": [
                    {
                        "slot": 1,
                        "name": "Synthetic Player B",
                        "field_evidence": {"given_names": {"page": 1}},
                    }
                ],
            }
        }
        self.extraction = {
            "team": {"name": "Synthetic Team A"},
            "players": [{"name": "Synthetic Player A"}],
        }

    def preview(self, selections=("team.name",), **overrides):
        args = dict(
            raw_payload=self.raw,
            extraction=self.extraction,
            selections=selections,
            expected_hash="synthetic-canonical-hash",
            draft_version="7",
            actor_id="synthetic-operator",
            observed_at="2026-10-01T00:00:00Z",
        )
        args.update(overrides)
        return adoption_preview(CANONICAL.promote_canonical_fields, **args)

    def test_real_owner_reused_read_only_minimized_and_no_duplicate_capture(self):
        snapshot = copy.deepcopy((self.raw, self.extraction))
        results = [self.preview(("team.name", "player.1.name")) for _ in range(3)]
        self.assertTrue(all(r == results[0] for r in results))
        self.assertEqual(results[0]["changed_fields"], 2)
        self.assertFalse(results[0]["persisted"])
        self.assertFalse(results[0]["commit_enabled"])
        self.assertEqual((self.raw, self.extraction), snapshot)
        for pii in ("Synthetic Player", "Synthetic Team", "birth_date", "phone"):
            self.assertNotIn(pii, json.dumps(results))

    def test_real_owner_rejects_stale_missing_evidence_and_implicit_player(self):
        with self.assertRaises(CANONICAL.CanonicalPromotionError) as stale:
            self.preview(expected_hash="old")
        self.assertEqual(stale.exception.code, "canonical_sidecar_changed")
        with self.assertRaises(CANONICAL.CanonicalPromotionError) as missing:
            self.preview(("player.2.name",))
        self.assertEqual(missing.exception.code, "canonical_player_slot_missing")
        self.raw["canonical_shadow"]["team"].pop("field_evidence")
        with self.assertRaises(CANONICAL.CanonicalPromotionError) as evidence:
            self.preview()
        self.assertEqual(evidence.exception.code, "canonical_evidence_missing")

    def test_canonical_allowlist_not_reimplemented_or_bypassed(self):
        for path in ("player.1.phone", "team.arbitrary", "sql", "__dict__"):
            with self.assertRaises(CANONICAL.CanonicalPromotionError) as error:
                self.preview((path,))
            self.assertEqual(error.exception.code, "canonical_field_invalid")

    def test_no_context_or_invented_defaults(self):
        with self.assertRaisesRegex(Denied, "PREVIEW_CONTEXT_REQUIRED"):
            self.preview(actor_id="")
        snapshot = copy.deepcopy(self.extraction)
        self.preview()
        self.assertEqual(self.extraction, snapshot)
        self.assertNotIn("category", self.extraction["team"])
        self.assertNotIn("birth_date", self.extraction["players"][0])


if __name__ == "__main__":
    unittest.main()
