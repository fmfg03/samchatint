"""Source-loaded canonical functions with synthetic dependencies, no runtime import.

These tests execute the repository guards, scope resolver and build_home body.
They do not establish production DB mappings, authentication or composition.
AST loading here only avoids import-time live runtime/env side effects; it is
not an adapter facility and accepts no user-supplied code.
"""

import ast
import hashlib
import importlib.util
import json
import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

from samchat.private_plugin.contracts import Denied, Identity
from samchat.private_plugin.direction_read import (
    DirectionContext,
    DirectionOwners,
    DirectionReadAdapter,
)

ROOT = Path(__file__).resolve().parents[3]
_layout_spec = importlib.util.spec_from_file_location(
    "direction_report_layouts_fixture",
    ROOT / "src/samchat/client_executive/report_layouts.py",
)
_layout_module = importlib.util.module_from_spec(_layout_spec)
_layout_spec.loader.exec_module(_layout_module)
report_layouts = _layout_module.report_layouts

ACTOR = "10000000-0000-0000-0000-000000000001"
PORTFOLIO = "20000000-0000-0000-0000-000000000001"
TOURNAMENT = "30000000-0000-0000-0000-000000000001"
FOREIGN = "40000000-0000-0000-0000-000000000001"


def source_functions(path, names, namespace, *, constants=False):
    tree = ast.parse((ROOT / path).read_text())
    nodes = [
        ast.ImportFrom(
            module="__future__", names=[ast.alias(name="annotations")], level=0
        )
    ]
    nodes += [
        node
        for node in tree.body
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and (names is None or node.name in names)
        )
        or (constants and isinstance(node, ast.Assign))
    ]
    exec(
        compile(
            ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])),
            str(path),
            "exec",
        ),
        namespace,
    )
    return namespace


class AccessError(PermissionError):
    pass


class HTTPError(Exception):
    def __init__(self, *, status_code, detail):
        super().__init__(detail)
        self.status_code = status_code


class Result:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def all(self):
        return self.rows


class Session:
    """Metadata query fixture; any attempted non-SELECT fails immediately."""

    def __init__(self):
        self.queries = []

    async def execute(self, query, params):
        assert query.strip().startswith("SELECT"), "Business write attempted"
        self.queries.append((query, params))
        if "portfolio_tournaments" in query:
            return Result([{"id": TOURNAMENT}])
        return Result([{"id": PORTFOLIO, "label": "Fixture portfolio"}])

    def begin_nested(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class DirectionReadTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.identity = Identity(
            ACTOR,
            "installation",
            "organization",
            "grant",
            200,
            "issuer",
            "audience",
            frozenset({"direction:read"}),
            True,
            False,
        )
        self.employee = SimpleNamespace(id=ACTOR, activo=True, rol="direccion")
        self.session = Session()
        self.context = AsyncMock(
            return_value=DirectionContext(self.identity, self.employee, self.session)
        )
        self.mapping = AsyncMock(return_value="organization")
        self.decisions = {}

        async def decision(_session, employee, tool, action):
            value = self.decisions.get(tool)
            if isinstance(value, Exception):
                raise value
            return value

        # Lower-level policy/database responses are fixtures; the route guards
        # and home resolver/build functions themselves run without substitution.
        self.portfolios = AsyncMock(return_value=[PORTFOLIO])
        self.budget = AsyncMock(
            return_value={
                "source": "budget_db",
                "version": {"status": "approved", "id": "fixture-version"},
                "summary": {
                    "budget_total": "100",
                    "actual_total": "40",
                    "committed_total": "50",
                    "paid_total": "20",
                },
            }
        )
        service = SimpleNamespace(
            authorized_direction_portfolio_ids=self.portfolios,
            _authorized_tournaments=AsyncMock(
                return_value=[{"id": TOURNAMENT, "name": "Fixture tournament"}]
            ),
            ClientExecutiveAccessError=AccessError,
            _build_direction_budget_snapshot=self.budget,
            _build_operational_dossier=AsyncMock(return_value={}),
        )
        routes = source_functions(
            "src/devnous/gastos/routes/client_executive_routes.py",
            {
                "_assigned_direction_portfolios",
                "_direction_source_access",
                "_is_superadmin",
            },
            {
                "HTTPException": HTTPError,
                "AccessControlLookupError": AccessError,
                "explicit_tool_decision": decision,
                "authorized_direction_portfolio_ids": self.portfolios,
                "is_superadmin_role": lambda value: value
                in {"superadmin", "super_admin"},
                "DIRECTION_EXECUTIVE_TOOL": "direccion.tableros_ejecutivos",
            },
        )

        async def documentary_facts(
            _session, *, include_expenses, include_documents, **kwargs
        ):
            return {
                "by_tournament": {
                    TOURNAMENT: {
                        "values": {
                            "actual": "40" if include_expenses else None,
                            "committed": "50" if include_documents else None,
                            "paid": "20" if include_documents else None,
                        },
                        "gaps": {},
                    }
                }
            }

        home = source_functions(
            "src/samchat/client_executive/home.py",
            None,
            {
                "service": service,
                "FACT_SOURCE": "samchat.budgets.executive_facts.build_executive_facts",
                "report_layouts": report_layouts,
                "build_executive_facts": AsyncMock(side_effect=documentary_facts),
                "text": lambda value: value,
                "date": date,
                "datetime": datetime,
                "timedelta": timedelta,
                "timezone": timezone,
                "ZoneInfo": ZoneInfo,
                "Decimal": Decimal,
                "InvalidOperation": InvalidOperation,
                "hashlib": hashlib,
                "json": json,
            },
            constants=True,
        )
        self.payments = AsyncMock(
            return_value={
                "by_tournament": {TOURNAMENT: {"value": Decimal("7"), "gaps": []}}
            }
        )
        self.receivables = AsyncMock(
            return_value={"value": None, "gaps": ["Fixture missing collections"]}
        )
        home["payment_values"] = self.payments
        home["receivable_values"] = self.receivables
        self.build = AsyncMock(wraps=home["build_home"])
        self.adapter = DirectionReadAdapter(
            current_context=self.context,
            organization_for_scope=self.mapping,
            owners=DirectionOwners(
                routes["_assigned_direction_portfolios"],
                routes["_direction_source_access"],
                routes["_is_superadmin"],
                home["resolve_scope"],
                self.build,
                access_errors=(HTTPError, AccessError),
            ),
            now=lambda: 100,
        )

    async def read(self, **kwargs):
        return await self.adapter.read(identity=self.identity, **kwargs)

    async def test_actual_canonical_home_aggregates_and_evidence(self):
        result = await self.read(tournament_id=TOURNAMENT)
        values = {row["id"]: row for row in result["indicators"]}
        self.assertEqual(values["actual"]["value"], "40.00")
        self.assertEqual(
            values["actual"]["source"],
            "samchat.budgets.executive_facts.build_executive_facts",
        )
        self.assertIsNone(values["receivables"]["value"])
        self.assertIn("Fixture missing collections", values["receivables"]["gaps"])
        self.assertTrue(result["read_only"])
        self.assertEqual(result["tournament_ids"], [TOURNAMENT])
        self.assertNotIn("tournaments", result)
        self.assertNotIn("payment_evidence", json.dumps(result))
        self.assertEqual(result["business_acceptance"], "pending")
        self.assertTrue(
            all(q.strip().startswith("SELECT") for q, _ in self.session.queries)
        )

    async def test_scope_listing_reuses_guards_without_report_reads(self):
        result = await self.adapter.list_scopes(identity=self.identity)
        self.assertEqual(
            result["portfolios"], [{"id": PORTFOLIO, "label": "Fixture portfolio"}]
        )
        self.assertEqual(
            result["tournaments"], [{"id": TOURNAMENT, "label": "Fixture tournament"}]
        )
        self.assertEqual(self.context.await_count, 2)
        self.build.assert_not_awaited()
        self.budget.assert_not_awaited()
        self.payments.assert_not_awaited()
        self.receivables.assert_not_awaited()

    async def test_scope_listing_denies_role_company_and_current_revocation(self):
        self.portfolios.return_value = []
        with self.assertRaisesRegex(Denied, "FORBIDDEN"):
            await self.adapter.list_scopes(identity=self.identity)
        self.portfolios.return_value = [PORTFOLIO]
        self.mapping.return_value = "foreign-organization"
        with self.assertRaisesRegex(Denied, "ORGANIZATION_UNPROVEN"):
            await self.adapter.list_scopes(identity=self.identity)
        self.mapping.return_value = "organization"

        async def revoke(*_):
            self.decisions["direccion.tableros_ejecutivos"] = False
            return "organization"

        self.mapping.side_effect = revoke
        with self.assertRaisesRegex(Denied, "FORBIDDEN"):
            await self.adapter.list_scopes(identity=self.identity)
        self.build.assert_not_awaited()

    async def test_scope_listing_bounds_duplicates_and_foreign_ids_fail_closed(self):
        for malformed in (
            "duplicate_portfolio",
            "duplicate_tournament",
            "long_label",
            "foreign_portfolio",
            "too_many",
        ):
            with self.subTest(malformed=malformed):
                self.setUp()
                canonical = self.adapter._owners.resolve_scope

                async def bad_scope(*args, **kwargs):
                    scope = await canonical(*args, **kwargs)
                    if malformed == "duplicate_portfolio":
                        scope["portfolios"].append(dict(scope["portfolios"][0]))
                    elif malformed == "duplicate_tournament":
                        scope["selected"].append(dict(scope["selected"][0]))
                    elif malformed == "long_label":
                        scope["portfolios"][0]["label"] = "x" * 201
                    elif malformed == "foreign_portfolio":
                        scope["portfolios"][0]["id"] = FOREIGN
                    else:
                        scope["portfolios"] *= 1001
                    return scope

                self.adapter._owners = replace(
                    self.adapter._owners, resolve_scope=bad_scope
                )
                with self.assertRaisesRegex(Denied, "SOURCE_UNAVAILABLE"):
                    await self.adapter.list_scopes(identity=self.identity)
                self.build.assert_not_awaited()

    async def test_specific_denials_skip_sources_and_keep_missing_values(self):
        for denied_tool in ("admin.presupuestos", "admin.finanzas"):
            with self.subTest(tool=denied_tool):
                self.decisions = {denied_tool: False}
                self.budget.reset_mock()
                self.payments.reset_mock()
                self.receivables.reset_mock()
                result = await self.read()
                values = {row["id"]: row for row in result["indicators"]}
                if denied_tool == "admin.presupuestos":
                    self.budget.assert_not_awaited()
                    self.assertIsNone(values["actual"]["value"])
                    self.assertEqual(values["paid"]["value"], "20.00")
                else:
                    self.payments.assert_not_awaited()
                    self.receivables.assert_not_awaited()
                    self.assertIsNone(values["obligations"]["value"])
                    self.assertIsNone(values["paid"]["value"])
                    self.assertEqual(values["actual"]["value"], "40.00")
                    self.assertTrue(values["obligations"]["gaps"])

    async def test_source_lookup_failure_is_denial_not_global_fallback(self):
        self.decisions["admin.finanzas"] = AccessError("private-fixture")
        result = await self.read()
        self.assertFalse(result["source_access"]["finance"])
        self.payments.assert_not_awaited()
        self.assertNotIn("private-fixture", json.dumps(result))

    async def test_no_position_and_explicit_direction_denial_precede_read(self):
        self.portfolios.return_value = []
        with self.assertRaisesRegex(Denied, "FORBIDDEN"):
            await self.read()
        self.portfolios.return_value = [PORTFOLIO]
        self.decisions["direccion.tableros_ejecutivos"] = False
        self.employee.rol = "superadmin"
        with self.assertRaisesRegex(Denied, "FORBIDDEN"):
            await self.read()
        self.build.assert_not_awaited()

    async def test_foreign_selectors_denied_by_actual_scope_resolver(self):
        for selectors in ({"portfolio_id": FOREIGN}, {"tournament_id": FOREIGN}):
            with self.assertRaisesRegex(Denied, "FORBIDDEN"):
                await self.read(**selectors)
        self.mapping.assert_not_awaited()
        self.build.assert_not_awaited()

    async def test_missing_or_foreign_organization_denies_before_sources(self):
        for organization in (None, "foreign-organization", ""):
            self.mapping.return_value = organization
            with self.assertRaisesRegex(Denied, "ORGANIZATION_UNPROVEN"):
                await self.read()
        self.build.assert_not_awaited()
        self.budget.assert_not_awaited()

    async def test_inactive_mismatched_employee_and_gate_race_denied(self):
        for employee in (
            SimpleNamespace(id=ACTOR),
            SimpleNamespace(id=ACTOR, activo="true"),
            SimpleNamespace(id=FOREIGN, activo=True),
        ):
            self.context.return_value = DirectionContext(
                self.identity, employee, self.session
            )
            with self.assertRaisesRegex(Denied, "UNAUTHENTICATED"):
                await self.read()
        self.context.return_value = DirectionContext(
            replace(self.identity, grant_id="different-grant"),
            self.employee,
            self.session,
        )
        with self.assertRaisesRegex(Denied, "UNAUTHENTICATED"):
            await self.read()
        self.build.assert_not_awaited()

    async def test_revocation_expiry_and_oauth_restriction_rechecked(self):
        for changes in (
            {"revoked": True},
            {"expires_at": 100},
            {"active": False},
            {"oauth_scopes": frozenset()},
            {"expires_at": 200.5},
        ):
            identity = replace(self.identity, **changes)
            self.context.return_value = DirectionContext(
                identity, self.employee, self.session
            )
            with self.assertRaisesRegex(Denied, "UNAUTHENTICATED"):
                await self.adapter.read(identity=identity)
        self.build.assert_not_awaited()

    async def test_no_actor_or_organization_model_arguments(self):
        with self.assertRaises(TypeError):
            await self.read(actor_id=FOREIGN)
        with self.assertRaises(TypeError):
            await self.read(organization_id="foreign")
        for kwargs in ({"portfolio_id": "invalid"}, {"year": True}, {"year": 1900}):
            with self.assertRaisesRegex(Denied, "INVALID_SELECTOR"):
                await self.read(**kwargs)
        self.context.assert_not_awaited()

    async def test_scope_change_and_source_error_do_not_release_data(self):
        # Obtain a legitimate canonical response first, then emulate a source
        # changing its resolved scope while the read was in progress.
        await self.read()
        canonical = self.build._mock_wraps

        async def changed(*args, **kwargs):
            snapshot, scope = await canonical(*args, **kwargs)
            scope["selected"] = []
            return snapshot, scope

        self.build.side_effect = changed
        with self.assertRaisesRegex(Denied, "SCOPE_CHANGED"):
            await self.read()
        self.build.side_effect = RuntimeError("private-source-detail")
        with self.assertRaisesRegex(Denied, "^SOURCE_UNAVAILABLE$"):
            await self.read()

    async def test_authority_changes_during_read_suppress_result(self):
        for change in (
            "finance",
            "budget",
            "direction",
            "position",
            "employee",
            "organization",
            "superadmin",
            "identity",
        ):
            with self.subTest(change=change):
                self.setUp()
                canonical = self.build._mock_wraps

                async def changed(*args, **kwargs):
                    result = await canonical(*args, **kwargs)
                    if change in {"finance", "budget", "direction"}:
                        key = {
                            "finance": "admin.finanzas",
                            "budget": "admin.presupuestos",
                            "direction": "direccion.tableros_ejecutivos",
                        }[change]
                        self.decisions[key] = False
                    elif change == "position":
                        self.portfolios.return_value = []
                    elif change == "employee":
                        self.employee.activo = False
                    elif change == "organization":
                        self.mapping.return_value = "other-organization"
                    elif change == "superadmin":
                        self.employee.rol = "superadmin"
                    else:
                        self.context.return_value = DirectionContext(
                            replace(self.identity, grant_id="different"),
                            self.employee,
                            self.session,
                        )
                    return result

                self.build.side_effect = changed
                with self.assertRaises(Denied):
                    await self.read()

    async def test_malformed_nested_evidence_is_rejected(self):
        canonical = self.build._mock_wraps
        for bad_field in ("coverage", "gaps", "value", "too_long"):

            async def malformed(*args, **kwargs):
                snapshot, scope = await canonical(*args, **kwargs)
                first = snapshot["indicators"][0]
                if bad_field == "coverage":
                    first["coverage"]["covered"] = {"contact": "synthetic"}
                elif bad_field == "gaps":
                    first["gaps"] = [{"contact": "synthetic"}]
                elif bad_field == "value":
                    first["value"] = "NaN"
                else:
                    first["definition"] = "x" * 2001
                return snapshot, scope

            self.build.side_effect = malformed
            with self.assertRaisesRegex(Denied, "^SOURCE_UNAVAILABLE$"):
                await self.read()

    async def test_source_extras_not_forwarded(self):
        canonical = self.build._mock_wraps

        async def extras(*args, **kwargs):
            snapshot, scope = await canonical(*args, **kwargs)
            snapshot["private_contact"] = "synthetic-private"
            snapshot["indicators"][0]["private_contact"] = "synthetic-private"
            return snapshot, scope

        self.build.side_effect = extras
        result = await self.read()
        self.assertNotIn("synthetic-private", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
