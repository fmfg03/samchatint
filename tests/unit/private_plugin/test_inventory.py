"""Coverage of inspected source, not a claim of active frontend/runtime parity."""

import importlib.util
import unittest
from pathlib import Path

from samchat.private_plugin.catalog import READS, WRITES, operations

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location(
    "inventory", ROOT / "scripts/private_plugin/inventory.py"
)
inventory = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inventory)


class InventoryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = inventory.scan()

    def test_inventory_is_reproducible_and_no_parse_gaps(self):
        expected_json, expected_csv = inventory.render(self.data)
        self.assertEqual(
            (ROOT / "docs/private-plugin/route-inventory.json").read_text(),
            expected_json,
        )
        self.assertEqual(
            (ROOT / "docs/private-plugin/route-matrix.csv").read_text(), expected_csv
        )
        self.assertEqual(self.data["parse_gaps"], [])

    def test_every_current_action_router_entry_is_accounted_for(self):
        self.assertEqual(
            {a["action"] for a in self.data["actions"]}, set(READS + WRITES)
        )
        self.assertEqual(len(operations()), len(set(o.action for o in operations())))

    def test_product_lanes_and_aliases_are_not_silently_omitted(self):
        paths = {p for r in self.data["routes"] for p in r["paths"]}
        for path in (
            "/api/registration-review/{session_id}/commit",
            "/documentos/{documento_id}/aprobar",
            "/documentos/{documento_id}/rechazar",
            "/admin/finanzas/payment-run",
            "/admin/finanzas/cuentas-por-cobrar",
            "/admin/finanzas/cashflow",
            "/admin/nomina/prenomina",
            "/direccion/tableros",
            "/direccion/reportes/gestion",
            "/admin/sam-inbox",
            "/admin/artifacts",
            "/soporte",
            "/api/assistant/conversations",
            "/copa-america/api/assistant/conversations",
        ):
            self.assertIn(path, paths)
        for row in self.data["routes"]:
            for column in (
                "profile_evidence",
                "canonical_owner",
                "proposed_tool",
                "scope_proposal",
                "effect",
                "confirmation",
                "evidence_idempotency",
                "tests_required",
                "status",
            ):
                self.assertTrue(row[column], (row["id"], column))
            self.assertIn("DISABLED", row["status"])

    def test_baseline_canons_match_register(self):
        import hashlib

        register = (ROOT / "docs/roadmap/samchat-convergence-register.md").read_text()
        for name in (
            "SAMCHAT_CANON_FOR_CHATGPT_2026-09-10.md",
            "SAMCHAT_ENGINEERING_CANON_FOR_CHATGPT_2026-09-10.md",
            "SAMCHAT_CODEBASE_SWEEP_REPORT_2026-09-10.md",
        ):
            self.assertIn(
                hashlib.sha256((ROOT / name).read_bytes()).hexdigest(), register
            )


class MountEvidenceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = inventory.scan()

    def test_every_mounted_path_has_source_chain_evidence(self):
        for row in self.data["routes"]:
            self.assertEqual(len(row["paths"]), len(row["mount_chain"]))
            for mount in row["mount_chain"]:
                self.assertTrue(mount["evidence"])
                self.assertIn("static", mount["status"])
            self.assertIn("UNPROVEN", row["profile_mapping_status"])

    def test_webhooks_have_ingress_prefix_and_budget_registration_resolves(self):
        webhook = [
            r for r in self.data["routes"] if r["source"].endswith("webhook_handler.py")
        ]
        budget = [
            r
            for r in self.data["routes"]
            if r["source"].endswith("admin_budget_routes.py")
        ]
        self.assertTrue(webhook)
        self.assertTrue(budget)
        self.assertTrue(
            all(p.startswith("/ingress/") for r in webhook for p in r["paths"])
        )
        self.assertTrue(
            all(
                "registration_function_static" in m["status"]
                for r in budget
                for m in r["mount_chain"]
            )
        )

    def test_action_services_have_exact_source_and_effect_evidence(self):
        for action in self.data["actions"]:
            self.assertNotEqual(action["adapter_source"], "UNRESOLVED")
            self.assertTrue(action["service_candidates"])
            self.assertIn("DISABLED", action["status"])
        actions = {a["action"]: a for a in self.data["actions"]}
        for name in (
            "receipts.send_document",
            "receipts.approve_document",
            "receipts.reject_document",
        ):
            self.assertIn(
                "_transition_document_adapter", actions[name]["service_candidates"]
            )
            self.assertTrue(actions[name]["transitive_local_helpers"])
            self.assertEqual(actions[name]["effect"], "declared_write")

    def test_profile_defaults_are_evidence_and_direction_not_role_grant(self):
        catalog = {entry["key"]: entry for entry in self.data["access_profile_catalog"]}
        self.assertEqual(len(catalog), 36)
        self.assertEqual(catalog["direccion.tableros_ejecutivos"]["default_roles"], [])
        self.assertTrue(
            all("DEFAULT_POLICY_ONLY" in t["status"] for t in catalog.values())
        )
        self.assertTrue(
            any(row["default_profile_candidates"] for row in self.data["routes"])
        )

    def test_exclusions_keep_ui_and_transitive_authority_open(self):
        exclusions = " ".join(self.data["exclusions"])
        self.assertIn("frontend", exclusions)
        self.assertIn("transitive authority", exclusions)
        self.assertEqual(
            self.data["baseline"], "d83cd104ae6106978e40d61de33c98139de145bb"
        )


if __name__ == "__main__":
    unittest.main()
