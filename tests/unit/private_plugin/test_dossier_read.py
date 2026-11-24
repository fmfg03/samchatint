"""Dossier read uses canonical Direction authorization and an opt-in SDK tool."""

import json
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

import test_direction_read as direction_fixture
import test_oauth_identity as oauth_fixture
from mcp.shared.memory import create_connected_server_and_client_session

from samchat.private_plugin.contracts import Denied
from samchat.private_plugin.direction_read import DirectionContext
from samchat.private_plugin.dossier_read import (
    ACTION,
    CONTRACT,
    DossierReadAdapter,
    parse_dossier_query,
)
from samchat.private_plugin.durable_audit import SQLiteAudit, initialize_local_audit
from samchat.private_plugin.local_reads import create_local_read_server
from samchat.private_plugin.perimeter import digest

TOURNAMENT = direction_fixture.TOURNAMENT
ARGS = {"tournament_id": TOURNAMENT, "year": 2026}
PRIVATE_MARKER = "fixture-marker-must-not-leave-samchat"
VISIT = "30000000-0000-0000-0000-000000000138"


def _dossier():
    return {
        "edition_verified": True,
        "sources": {
            "queried_at": "2026-11-24T14:51:00Z",
            "directory": {
                "source": "Copa Telmex",
                "status": "available",
                "updated_at": "2026-10-04T15:00:00Z",
                "rows": [
                    {
                        "entity": "Oaxaca",
                        "state_slug": "oaxaca",
                        "zone": "Zona 1",
                        "representative_name": "Nombre publicado, fixture",
                        "representation_status": "confirmed",
                        "birth_date": PRIVATE_MARKER,
                        "email": PRIVATE_MARKER,
                        "spouse_name": PRIVATE_MARKER,
                    }
                ],
            },
            "team_roster": {
                "source": "Padrón CTT",
                "status": "partial",
                "observed_at": "2026-11-24T06:09:00Z",
                "rows": [
                    {
                        "name": "Equipo de prueba",
                        "category": "Juvenil",
                        "entity": "Oaxaca",
                        "player_registrations": 14,
                        "curp": PRIVATE_MARKER,
                    }
                ],
            },
            "registration": {
                "source": "Padrón CTT",
                "age_cutoff": "2026-10-01",
                "status": "available",
                "rows": [
                    {
                        "teams": 1,
                        "registrations": 14,
                        "unique_people_identified": None,
                        "ages": [
                            {"age": 14, "registrations": 4, "private": PRIVATE_MARKER}
                        ],
                    }
                ],
            },
            "finance": {
                "source": "Payment Run",
                "status": "available",
                "rows": [
                    {
                        "entity": "Oaxaca",
                        "amount_paid": "9000000",
                        "reference": PRIVATE_MARKER,
                    }
                ],
            },
            "finance_lines": {
                "source": "Partidas",
                "status": "available",
                "rows": [
                    {
                        "entity": "Oaxaca",
                        "phase": "estatal",
                        "amount": Decimal("12.90"),
                        "expense_date": date(2027, 1, 16),
                        "currency": "MXN",
                        "reference": "R-1",
                        "dossier_concept": "gasto_visita",
                        "visit_id": VISIT,
                    }
                ],
            },
            "marketing": {
                "source": "Galería",
                "status": "available",
                "rows": [{"photo_url": PRIVATE_MARKER}],
            },
        },
        "resolved_finance_documents": [
            {
                "entity": "Oaxaca",
                "phase": "nacional",
                "kind": "SOLICITUD",
                "categories": ["Juvenil", "Femenil"],
                "amount_paid": None,
                "amount_requested": "1150",
                "proof_available": False,
                "currency": "MXN",
                "dossier_concept": "hotel_anticipo",
                "service_type": "cenas",
                "status": "solicitado",
            }
        ],
        "captures": {
            "status": "available",
            "rows": [
                {
                    "domain": "operations",
                    "kind": "team_target",
                    "entity": "Oaxaca",
                    "phase": "estatal",
                    "category": "Juvenil",
                    "fields": {"gender": "mixto", "expected_teams": 3},
                    "evidence_reference": PRIVATE_MARKER,
                },
                {
                    "id": VISIT,
                    "domain": "operations",
                    "kind": "visit",
                    "entity": "Oaxaca",
                    "phase": "estatal",
                    "fields": {
                        "responsible": PRIVATE_MARKER,
                        "outcome": PRIVATE_MARKER,
                        "visit_date": "2026-10-01",
                    },
                },
                {
                    "domain": "operations",
                    "kind": "contact",
                    "fields": {"name": PRIVATE_MARKER},
                },
                {
                    "domain": "medical_transfers",
                    "kind": "medical_transfer",
                    "fields": {"diagnosis": PRIVATE_MARKER},
                },
                {
                    "domain": "sponsor_guests",
                    "kind": "sponsor_guest",
                    "fields": {"guest_name": PRIVATE_MARKER},
                },
                {
                    "domain": "media",
                    "kind": "activation_photo",
                    "fields": {"photo_url": PRIVATE_MARKER},
                },
            ],
        },
    }


class DossierReadTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture = direction_fixture.DirectionReadTests()
        self.fixture.setUp()
        self.identity = replace(
            self.fixture.identity, profile_id="fixture-dossier-account"
        )
        self.fixture.context.return_value = DirectionContext(
            self.identity, self.fixture.employee, self.fixture.session
        )
        self.grant = AsyncMock(return_value=True)
        self.domains = AsyncMock(
            return_value={
                "operations",
                "finance",
                "marketing",
                "contacts",
                "media",
                "medical_transfers",
                "sponsor_guests",
            }
        )
        self.view = AsyncMock(return_value=_dossier())
        self.adapter = DossierReadAdapter(
            direction=self.fixture.adapter,
            grant_for_query=self.grant,
            read_domains=self.domains,
            read_view=self.view,
            web_origin="https://direccion.example.test",
        )

    async def _read(self, **selectors):
        return (
            await self.adapter.read(
                identity=self.identity, query=parse_dossier_query({**ARGS, **selectors})
            )
        ).data

    def test_request_uses_closed_canonical_selectors(self):
        for change in (
            {"raw_sql": "SELECT 1"},
            {"grant": True},
            {"year": 2026.0},
            {"offset": 1.0},
            {"offset": -1},
            {"tournament_id": "invented"},
            {"entity": " Oaxaca"},
            {"category": "Juvenil\nFemenil"},
            {"cut_date": "2026-02-29"},
            {"cut_date": "1900-02-01"},
            {"phase": "colectiva"},
            {"section": "contacts"},
        ):
            with self.subTest(change=change), self.assertRaisesRegex(
                Denied, "INVALID_ARGUMENTS"
            ):
                parse_dossier_query({**ARGS, **change})
        with self.assertRaisesRegex(Denied, "INVALID_ARGUMENTS"):
            parse_dossier_query({"year": 2026})
        self.assertEqual(
            parse_dossier_query({**ARGS, "cut_date": "2027-01-08"}).cut_date,
            date(2027, 1, 8),
        )

    def test_web_origin_must_be_approved_shape(self):
        for origin in (
            "http://direccion.example.test",
            "https://user:secret@direccion.example.test",
            "https://direccion.example.test/somewhere",
            "https://direccion.example.test?foo=bar",
        ):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                DossierReadAdapter(
                    direction=self.fixture.adapter,
                    grant_for_query=self.grant,
                    read_domains=self.domains,
                    read_view=self.view,
                    web_origin=origin,
                )

    async def test_only_authorized_facts_and_redacted_link_from_same_projection(self):
        result = await self._read()
        self.assertEqual(result["schema"], CONTRACT)
        self.assertTrue(result["read_only"])
        self.assertEqual(result["tournament_ids"], [TOURNAMENT])
        self.assertEqual(result["consulted_at"], "2026-11-24T14:51:00Z")
        serialized = json.dumps(result)
        self.assertNotIn(PRIVATE_MARKER, serialized)
        self.assertNotIn("9000000", serialized)
        self.assertNotIn("resident_id", serialized)
        self.assertIn("Nombre publicado, fixture", serialized)
        self.assertIn("Equipo de prueba", serialized)
        self.assertEqual(
            self.view.await_args.kwargs["domains"],
            {"operations", "finance", "marketing"},
        )
        self.assertTrue(self.view.await_args.kwargs["directory_contacts_allowed"])
        self.assertEqual(
            set(result["protected_sections"]),
            {"contacts", "media", "medical_transfers", "sponsor_guests"},
        )
        facts = {fact["section"]: fact for fact in result["facts"]}
        self.assertIsNone(facts["finance"]["fields"]["amount_paid"])
        self.assertEqual(facts["finance"]["fields"]["service_type"], "cenas")
        self.assertEqual(facts["finance_lines"]["fields"]["expense_date"], "2027-01-16")
        self.assertEqual(facts["finance_lines"]["fields"]["amount"], "12.90")
        self.assertEqual(facts["finance_lines"]["fields"]["visit_id"], VISIT)
        visit = next(f for f in result["facts"] if f["type"] == "visit")
        self.assertEqual(visit["fields"]["visit_id"], VISIT)
        self.assertNotIn("responsible", visit["fields"])
        self.assertEqual(facts["registration"]["fields"]["age_cutoff"], "2026-10-01")
        self.assertEqual(
            facts["registration"]["fields"]["ages"], [{"age": 14, "registrations": 4}]
        )
        self.assertEqual(facts["team_roster"]["observed_at"], "2026-11-24T06:09:00Z")
        self.assertEqual(
            parse_qs(urlsplit(result["web_url"]).query),
            {"tournament_id": [TOURNAMENT], "edition_year": ["2026"]},
        )

    async def test_scope_selectors_match_categories_and_preserve_phase(
        self,
    ):
        result = await self._read(
            entity="Oaxaca", phase="nacional", category="Juvenil", section="finance"
        )
        self.assertEqual(set(result["sources"]), {"finance"})
        self.assertEqual(len(result["facts"]), 1)
        self.assertEqual(result["facts"][0]["fields"]["amount_requested"], "1150")
        query = parse_qs(urlsplit(result["web_url"]).query)
        self.assertEqual(query["category"], ["Juvenil"])
        self.assertEqual(query["phase"], ["nacional"])
        self.assertEqual(query["entity"], ["Oaxaca"])
        self.assertFalse(
            (await self._read(entity="Jalisco", category="Juvenil"))["facts"]
        )
        self.assertFalse(
            (await self._read(entity="Oaxaca", phase="estatal", section="finance"))[
                "facts"
            ]
        )
        self.assertTrue(
            (await self._read(entity="oaxaca", section="directory"))["facts"]
        )

    async def test_no_contacts_without_current_contact_entitlement(self):
        self.domains.return_value = {"operations"}
        result = await self._read(section="directory")
        self.assertFalse(self.view.await_args.kwargs["directory_contacts_allowed"])
        self.assertNotIn("representative_name", result["facts"][0]["fields"])
        self.assertEqual(result["protected_sections"], [])

    async def test_canonical_finance_deny_precedes_projection(self):
        self.fixture.decisions["admin.finanzas"] = False
        result = await self._read()
        self.assertNotIn("finance", self.view.await_args.kwargs["domains"])
        self.assertNotIn("finance", result["sources"])
        self.assertNotIn("finance_lines", result["sources"])

    async def test_explicit_grant_foreign_scope_and_changed_source_access_fail_closed(
        self,
    ):
        self.grant.return_value = False
        with self.assertRaisesRegex(Denied, "FORBIDDEN"):
            await self._read()
        self.view.assert_not_awaited()
        self.grant.return_value = True
        with self.assertRaisesRegex(Denied, "FORBIDDEN|SCOPE_CHANGED"):
            await self._read(tournament_id=direction_fixture.FOREIGN)
        self.view.assert_not_awaited()
        self.fixture.mapping.return_value = "other"
        with self.assertRaisesRegex(Denied, "ORGANIZATION_UNPROVEN"):
            await self._read()
        self.view.assert_not_awaited()
        self.fixture.mapping.return_value = "organization"
        self.domains.side_effect = [{"operations"}, {"operations", "contacts"}]
        with self.assertRaisesRegex(Denied, "SCOPE_CHANGED"):
            await self._read()
        self.view.assert_awaited_once()

    async def test_required_profile_and_scope_fail_without_business_read(self):
        raw = replace(self.identity, profile_id="")
        self.fixture.context.return_value = DirectionContext(
            raw, self.fixture.employee, self.fixture.session
        )
        with self.assertRaisesRegex(Denied, "FORBIDDEN"):
            await self.adapter.read(identity=raw, query=parse_dossier_query(ARGS))
        self.view.assert_not_awaited()

    async def test_historical_cut_keeps_only_roster_without_substituting_live_facts(
        self,
    ):
        result = await self._read(cut_date="2026-11-24")
        self.assertEqual(self.view.await_args.kwargs["cut_date"], date(2026, 11, 24))
        self.assertFalse(self.view.await_args.kwargs["directory_contacts_allowed"])
        self.assertEqual(set(result["sources"]), {"team_roster", "registration"})
        self.assertEqual(result["protected_sections"], [])
        self.assertEqual(result["scope"]["cut_date"], "2026-11-24")

    async def test_unverified_edition_keeps_gap_even_if_directory_has_data(self):
        self.view.return_value["edition_verified"] = False
        result = await self._read(year=2027)
        self.assertFalse(result["scope"]["edition_verified"])
        self.assertEqual(result["facts"], [])
        self.assertEqual(result["sources"]["directory"]["status"], "needs_link")

    async def test_page_is_bounded_and_order_keeps_exact_scope(self):
        self.view.return_value["sources"]["team_roster"]["rows"] = [
            {"name": f"Equipo {index}"} for index in range(24)
        ]
        first = await self._read(section="team_roster")
        second = await self._read(
            section="team_roster", offset=first["pagination"]["next_offset"]
        )
        self.assertEqual(len(first["facts"]), 20)
        self.assertEqual(len(second["facts"]), 4)
        self.assertIsNone(second["pagination"]["next_offset"])
        self.assertEqual(second["facts"][0]["fields"]["name"], "Equipo 20")

    async def test_sdk_is_opt_in_and_checks_signed_identity_and_durable_receipt(self):
        auth = oauth_fixture.OAuthIdentityTests()
        auth.setUpClass()
        auth.setUp()
        auth.records.link = replace(
            auth.records.link,
            employee_id=self.fixture.identity.actor_id,
            organization_id=self.fixture.identity.organization_id,
        )
        auth.records.grant = replace(
            auth.records.grant,
            employee_id=auth.records.link.employee_id,
            organization_id=auth.records.link.organization_id,
            profile_id=auth.records.link.profile_id,
        )
        auth.records.employee = replace(
            auth.records.employee, employee_id=self.fixture.identity.actor_id
        )
        identity = auth.provider.resolve_current("local-transport")
        self.fixture.context.return_value = DirectionContext(
            identity, self.fixture.employee, self.fixture.session
        )
        with tempfile.TemporaryDirectory() as directory:
            connection = sqlite3.connect(Path(directory) / "receipts.sqlite")
            try:
                initialize_local_audit(connection)
                audit = SQLiteAudit(connection)
                parameters = {
                    "identities": auth.provider,
                    "audit": audit,
                    "direction": self.fixture.adapter,
                    "current_connection": lambda: "local-transport",
                    "now": lambda: 100,
                    "issuer": auth.config.issuer,
                    "audience": auth.config.audience,
                    "installation_id": auth.config.installation_id,
                }
                inert = create_local_read_server(**parameters)
                async with create_connected_server_and_client_session(inert) as client:
                    self.assertNotIn(
                        ACTION,
                        {tool.name for tool in (await client.list_tools()).tools},
                    )
                    attempt = await client.call_tool(ACTION, ARGS)
                    self.assertTrue(attempt.isError)
                    self.assertEqual(attempt.content[0].text, "CAPABILITY_DISABLED")
                self.view.assert_not_awaited()
                server = create_local_read_server(**parameters, dossier=self.adapter)
                async with create_connected_server_and_client_session(server) as client:
                    tool = next(
                        tool
                        for tool in (await client.list_tools()).tools
                        if tool.name == ACTION
                    )
                    self.assertTrue(tool.annotations.readOnlyHint)
                    self.assertFalse(tool.inputSchema["additionalProperties"])
                    invalid = await client.call_tool(
                        ACTION, {**ARGS, "role": "superadmin"}
                    )
                    self.assertTrue(invalid.isError)
                    self.assertEqual(invalid.content[0].text, "INVALID_ARGUMENTS")
                    result = await client.call_tool(ACTION, ARGS)
                    self.assertFalse(result.isError, result)
                    self.assertEqual(result.structuredContent["schema"], CONTRACT)
                    stamp = audit.get_for_actor(
                        result.meta["samchat/receipt"], actor_id=identity.actor_id
                    )
                    self.assertEqual(stamp.action, ACTION)
                    self.assertEqual(
                        stamp.evidence_digest, digest(result.structuredContent)
                    )
                    self.assertNotIn(PRIVATE_MARKER, result.content[0].text)

                    append = audit.append_read

                    def revoke_permission(**record):
                        saved = append(**record)
                        self.domains.return_value = {"operations"}
                        return saved

                    audit.append_read = revoke_permission
                    denied = await client.call_tool(ACTION, ARGS)
                    self.assertTrue(denied.isError)
                    self.assertEqual(denied.content[0].text, "SCOPE_CHANGED")
                    self.assertIsNone(denied.structuredContent)
            finally:
                connection.close()
