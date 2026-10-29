"""Async policy contract tests with synthetic identity; no live binding is claimed."""

from __future__ import annotations

import asyncio
import json
import unittest
from dataclasses import replace
from unittest.mock import patch

import test_expanded_reads as fixtures
from test_expanded_reads import A, T

from samchat.readonly_gateway.contracts import ReadError
from samchat.readonly_gateway.expanded import DomainScope


class AsyncTechnicalTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        fixtures.TechnicalTests.setUp(self)
        self.resolutions = 0

        async def resolve(query):
            self.assertIsNone(query)
            self.resolutions += 1
            await asyncio.sleep(0)
            return self.actor

        self.perimeter.resolve_async = resolve

    async def test_async_source_and_both_checks_with_fresh_authority(self):
        for operation, arguments in (
            ("source_info", {}),
            ("source_read", {"source_id": self.sources.source_id, "path": "safe.py"}),
            ("diagnose", {"check": "source-manifest"}),
            ("diagnose", {"check": "adapter-bindings"}),
        ):
            with self.subTest(operation=operation, arguments=arguments):
                before = self.resolutions
                result = await self.technical.call_async(operation, arguments)
                self.assertEqual(self.resolutions - before, 3)
                self.assertIn(
                    "reviewed_source_not_runtime_or_deployment_evidence", result["gaps"]
                )
                self.assertNotIn("1234567890123456", json.dumps(result))
        self.assertNotIn("safe.py", json.dumps(self.perimeter.audit.events))

    async def test_financial_scope_does_not_grant_source_access(self):
        self.actor = replace(
            self.actor, scopes=frozenset({DomainScope("solicitud", T, frozenset({A}))})
        )
        with patch.object(self.sources, "info") as read:
            with self.assertRaisesRegex(ReadError, "NOT_FOUND_OR_NOT_VISIBLE"):
                await self.technical.call_async("source_info", {})
            read.assert_not_called()
        self.assertEqual(self.perimeter.audit.events[-1]["decision"], "deny")

    async def test_revocation_after_source_read_blocks_delivery(self):
        original = self.sources.info

        def read():
            result = original()
            self.actor = replace(self.actor, generation="revoked")
            return result

        with patch.object(self.sources, "info", read):
            with self.assertRaisesRegex(ReadError, "AUTH_CHANGED"):
                await self.technical.call_async("source_info", {})
        self.assertEqual(self.resolutions, 2)
        self.assertNotIn("allow", [e["decision"] for e in self.perimeter.audit.events])

    async def test_revocation_after_allow_audit_blocks_delivery(self):
        original = self.perimeter.audit.append

        def audit(event):
            original(event)
            if event["decision"] == "allow":
                self.actor = replace(self.actor, scopes=frozenset())

        self.perimeter.audit.append = audit
        with self.assertRaisesRegex(ReadError, "AUTH_CHANGED"):
            await self.technical.call_async("source_info", {})
        self.assertEqual(self.resolutions, 3)
        self.assertEqual(self.perimeter.audit.events[-1]["decision"], "deny")

    async def test_cancellation_during_final_authority_does_not_release(self):
        entered = asyncio.Event()
        original = self.perimeter.resolve_async

        async def resolve(query):
            if self.resolutions == 2:
                entered.set()
                await asyncio.Future()
            return await original(query)

        self.perimeter.resolve_async = resolve
        task = asyncio.create_task(self.technical.call_async("source_info", {}))
        await asyncio.wait_for(entered.wait(), timeout=1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.perimeter.audit.events[-1]["decision"], "cancelled")

    async def test_initial_timeout_and_failed_audit_fail_closed(self):
        async def unavailable(query):
            await asyncio.Future()

        self.perimeter.resolve_async = unavailable
        self.perimeter.timeout = 0.01
        with patch.object(self.sources, "info") as read:
            with self.assertRaisesRegex(ReadError, "AUTH_REQUIRED"):
                await self.technical.call_async("source_info", {})
            read.assert_not_called()
        self.assertNotIn("actor_digest", self.perimeter.audit.events[-1])
        self.setUp()
        self.perimeter.audit.append = lambda event: (_ for _ in ()).throw(
            OSError("private")
        )
        with patch.object(self.sources, "info") as read:
            with self.assertRaisesRegex(ReadError, "AUDIT_UNAVAILABLE"):
                await self.technical.call_async("source_info", {})
            read.assert_not_called()

    async def test_source_errors_are_redacted_and_invalid_inputs_stay_closed(self):
        with patch.object(
            self.sources, "info", side_effect=ValueError("private backend")
        ):
            with self.assertRaisesRegex(ReadError, "SOURCE_UNAVAILABLE"):
                await self.technical.call_async("source_info", {})
        self.assertNotIn("private backend", json.dumps(self.perimeter.audit.events))
        for operation, arguments in (
            (
                "source_read",
                {"source_id": self.sources.source_id, "path": "../private"},
            ),
            ("diagnose", {"check": "shell"}),
            ("source_info", {"actor": "other"}),
        ):
            with self.assertRaises(ReadError):
                await self.technical.call_async(operation, arguments)
