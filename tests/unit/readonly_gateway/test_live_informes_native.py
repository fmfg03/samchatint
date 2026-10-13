"""Acceptance against a disposable native PostgreSQL cluster with synthetic rows."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from samchat.readonly_gateway.asyncpg_driver import AsyncpgConnectionFactory
from samchat.readonly_gateway.contracts import Admission, ReadError
from samchat.readonly_gateway.daily_audit import DailyAudit
from samchat.readonly_gateway.gateway import Sources
from samchat.readonly_gateway.live_informes import (
    ACTOR,
    LiveInformeAdapter,
    LiveInformeOwner,
    ReportDelegation,
    ReportOnlyGateway,
)
from samchat.readonly_gateway.postgres import PostgresBinding
from samchat.readonly_gateway.unix_bridge import UnixReadServer, UnixReportProxy

ROOT = Path(__file__).resolve().parents[3]
FIRST = "20000000-0000-4000-8000-000000000001"
SECOND = "20000000-0000-4000-8000-000000000002"
REQUEST = "20000000-0000-4000-8000-000000000003"


@unittest.skipUnless(
    os.environ.get("SAMCHAT_TEST_SOCKET"), "disposable native socket not configured"
)
class LiveInformeNativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.socket = Path(os.environ["SAMCHAT_TEST_SOCKET"])
        if (
            not str(cls.socket).startswith("/tmp/samchat-ro-native-")
            or not cls.socket.is_dir()
        ):
            raise RuntimeError("Only an isolated test socket under /tmp is admitted")
        exists = cls.admin(
            "SELECT count(*) FROM pg_namespace WHERE nspname='samchat_ro_fmfg_v1'"
        )
        if exists == "1":
            cls.admin(
                (
                    ROOT / "deployment/readonly_gateway/live-informes-rollback.sql"
                ).read_text()
            )
            cls.admin(
                "DROP TABLE documentos,aprobaciones,expense_reports,anticipos,reembolsos,empleados,authorization_positions,authorization_position_assignments,documento_authorization_routes,access_control_rules; DROP ROLE devnous_user; DROP ROLE outside_actor"
            )
        cls.admin((Path(__file__).with_name("native_fixture.sql")).read_text())
        cls.admin(
            (ROOT / "deployment/readonly_gateway/live-informes-install.sql").read_text()
        )
        definitions = json.loads(
            cls.admin(
                "SET TIME ZONE 'UTC'; SET search_path=samchat_ro_fmfg_v1,pg_catalog; "
                "SELECT json_agg(x ORDER BY name) FROM (SELECT c.relname AS name,"
                "pg_get_viewdef(c.oid,true) AS definition FROM pg_class c "
                "JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname='samchat_ro_fmfg_v1') x"
            )
        )
        cls.fingerprint = hashlib.sha256(
            json.dumps(definitions, sort_keys=True).encode()
        ).hexdigest()

    @classmethod
    def admin(cls, sql: str) -> str:
        return subprocess.check_output(
            [
                "psql",
                "-XqAt",
                "-h",
                str(cls.socket),
                "-U",
                "postgres",
                "-d",
                "devnous_db",
                "-v",
                "ON_ERROR_STOP=1",
                "-c",
                sql,
            ],
            text=True,
        ).strip()

    def setUp(self) -> None:
        self.admin(
            "UPDATE empleados SET activo=true,rol='superadmin',departamento='Dirección',correo='fmfg@pm.me' WHERE id='"
            + ACTOR
            + "'; DELETE FROM access_control_rules"
        )
        self.delegation = ReportDelegation(
            ACTOR,
            "fmfg@pm.me",
            "samchat-production",
            "agentius:fmfg:readonly:v1",
            "20261009-v1",
            datetime(2026, 11, 8, 23, 59, 59, tzinfo=timezone.utc),
            "live-informes-7d857ad32-v1",
        )
        self.actor = self.delegation.principal()

        async def connect():
            import asyncpg

            return await asyncpg.connect(
                host=str(self.socket),
                database="devnous_db",
                user="samchat_ro_fmfg",
                timeout=3,
            )

        self.backend = LiveInformeAdapter(
            PostgresBinding(
                "production",
                "samchat-production",
                "devnous_db",
                "samchat_ro_fmfg",
                self.fingerprint,
                self.delegation.owner_version,
                "f" * 64,
            ),
            AsyncpgConnectionFactory(connect),
            LiveInformeOwner(lambda: self.delegation),
        )
        self.audit_directory = tempfile.TemporaryDirectory(prefix="samchat-ro-audit-")
        self.addCleanup(self.audit_directory.cleanup)
        self.audit = DailyAudit(
            Path(self.audit_directory.name), os.getuid(), os.getgid()
        )
        self.gateway = ReportOnlyGateway(
            Admission(
                "production",
                "samchat-production",
                self.fingerprint,
                "7d857ad326a48e0c2a8315caf81f792f3279ce51",
                self.delegation.owner_version,
                "f" * 64,
            ),
            self.backend,
            self.audit,
            Sources(
                {"src/review.py": b"# synthetic admitted source\n"},
                "7d857ad326a48e0c2a8315caf81f792f3279ce51",
                "reviewed_manifest",
            ),
        )

    def test_legacy_authority_is_unresolved_without_new_permission_reads(self) -> None:
        from samchat.readonly_gateway.diagnosis import diagnose

        self.admin(
            "UPDATE documentos SET referencia_operaciones=NULL "
            "WHERE id='" + FIRST + "'; "
            "DELETE FROM documento_authorization_routes WHERE documento_id='"
            + FIRST
            + "'"
        )
        try:
            document, authority = self.backend.observation(self.actor, FIRST)
            self.assertEqual(authority["resolution"], "unresolved")
            self.assertIsNone(authority["actor_can_decide"])
            self.assertIn("legacy_approval_authority_not_projected", authority["gaps"])
            observations = diagnose(document, authority)["observations"]
            self.assertIn("authority_unresolved", observations)
            self.assertNotIn("actor_is_observer_not_approver", observations)
        finally:
            self.admin(
                "UPDATE documentos SET referencia_operaciones='RO-001' "
                "WHERE id='" + FIRST + "'"
            )

    def test_reviewed_sources_do_not_claim_verified_deployment(self) -> None:
        result = self.gateway.call(
            "samchat.document_status", {"document_id": FIRST}, self.actor
        )
        self.assertEqual(result["source_kind"], "reviewed_manifest")
        self.assertEqual(
            result["reviewed_commit"], self.gateway.admission.source_commit
        )
        self.assertIsNone(result["deployed_commit"])

    def test_native_unix_bridge_durable_audit_and_peer_denial(self) -> None:
        with tempfile.TemporaryDirectory(prefix="samchat-ro-bridge-") as directory:
            path = Path(directory) / "readonly.sock"
            server = UnixReadServer(
                path, self.gateway, lambda: self.delegation.principal(), os.getuid()
            )
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                listener.bind(str(path))
                listener.listen(1)

                def accept_one():
                    connection, _ = listener.accept()
                    with connection:
                        server.handle(connection)

                thread = threading.Thread(target=accept_one)
                thread.start()
                result = UnixReportProxy(path, os.getuid()).call(
                    "samchat.document_status", {"document_id": FIRST}
                )
                thread.join(5)
                self.assertFalse(thread.is_alive())
                self.assertEqual(result["data"]["document"]["document_id"], FIRST)
                server.peer_uid = os.getuid() + 1
                thread = threading.Thread(target=accept_one)
                thread.start()
                self.error(
                    "AUTH_REQUIRED",
                    lambda: UnixReportProxy(path, os.getuid()).call(
                        "samchat.document_status", {"document_id": FIRST}
                    ),
                )
                thread.join(5)
                self.assertFalse(thread.is_alive())
        logs = list(Path(self.audit_directory.name).glob("audit-*.jsonl"))
        self.assertEqual(len(logs), 1)
        self.assertNotIn("monto_total", logs[0].read_text())
        self.assertNotIn("password", logs[0].read_text())

    def error(self, code, callback) -> None:
        with self.assertRaises(ReadError) as raised:
            callback()
        self.assertEqual(raised.exception.code, code)

    def test_native_dynamic_scope_new_reports_and_no_requests(self) -> None:
        self.assertEqual(self.backend.document(self.actor, FIRST)["kind"], "INFORME")
        self.assertEqual(self.backend.document(self.actor, SECOND)["kind"], "INFORME")
        self.error(
            "NOT_FOUND_OR_NOT_VISIBLE",
            lambda: self.backend.document(self.actor, REQUEST),
        )
        new = "20000000-0000-4000-8000-000000000004"
        self.admin(
            "INSERT INTO documentos SELECT '"
            + new
            + "',torneo_id,tipo,estado,numero_referencia,referencia_operaciones,referencia_base,creado_en,enviado_en,currency,monto_total,budget_concept_id,pagado_en,gasto_generado_id,cuenta_gastos_id,concepto_pago,informe_origen_id,motivo_comprobacion_parcial,empleado_id FROM documentos WHERE id='"
            + FIRST
            + "'"
        )
        self.assertEqual(self.backend.document(self.actor, new)["document_id"], new)
        self.admin("DELETE FROM documentos WHERE id='" + new + "'")

    def test_gateway_reference_diagnosis_study_and_audit(self) -> None:
        result = self.gateway.call(
            "samchat.resolve_reference",
            {"reference_kind": "operations", "reference": "SYNTH-1"},
            self.actor,
        )["data"]
        self.assertEqual(result["resolution"], "unique")
        self.error(
            "NOT_FOUND_OR_NOT_VISIBLE",
            lambda: self.gateway.call(
                "samchat.resolve_reference",
                {"reference_kind": "operations", "reference": "' OR TRUE --"},
                self.actor,
            ),
        )
        result = self.gateway.call(
            "samchat.document_diagnose",
            {"document_id": FIRST, "intent": "approve"},
            self.actor,
        )["data"]
        self.assertEqual(result["diagnosis"]["approval_eligibility"], "unknown")
        result = self.gateway.call(
            "samchat.study",
            {
                "date_from": "2026-10-01",
                "date_to": "2026-10-09",
                "dataset": "documents",
                "page_size": 1,
            },
            self.actor,
        )["data"]
        self.assertEqual(len(result["rows"]), 1)
        result = self.gateway.call(
            "samchat.document_audit", {"document_id": FIRST}, self.actor
        )["data"]
        self.assertEqual(result["rows"], [])

    def test_real_driver_statement_timeout_and_inflight_interrupt(self) -> None:
        started = time.monotonic()
        with self.assertRaises(ReadError) as rejected:
            self.backend._query(
                self.actor, lambda grant: ("SELECT pg_sleep(10)", {}), 1
            )
        self.assertIn(rejected.exception.code, {"TIMEOUT", "SOURCE_UNAVAILABLE"})
        self.assertLess(time.monotonic() - started, 5)
        errors = []
        finished = threading.Event()

        def slow():
            try:
                self.backend._query(
                    self.actor, lambda grant: ("SELECT pg_sleep(10)", {}), 1
                )
            except ReadError as error:
                errors.append(error.code)
            finally:
                finished.set()

        thread = threading.Thread(target=slow)
        thread.start()
        self.assertFalse(finished.wait(0.3))
        started = time.monotonic()
        self.backend.interrupt()
        self.assertTrue(finished.wait(2))
        thread.join(2)
        self.assertLess(time.monotonic() - started, 2)
        self.assertEqual(errors, ["SOURCE_UNAVAILABLE"])

    def test_role_and_department_changes_narrow_next_query(self) -> None:
        self.backend.document(self.actor, SECOND)
        self.admin(
            "UPDATE empleados SET rol='admin',departamento='Operaciones' WHERE id='"
            + ACTOR
            + "'"
        )
        self.error(
            "NOT_FOUND_OR_NOT_VISIBLE",
            lambda: self.backend.document(self.actor, SECOND),
        )
        self.backend.document(self.actor, FIRST)
        self.admin("UPDATE empleados SET rol='empleado' WHERE id='" + ACTOR + "'")
        self.error(
            "NOT_FOUND_OR_NOT_VISIBLE", lambda: self.backend.document(self.actor, FIRST)
        )

    def test_explicit_deny_and_inactive_account_deny_before_data(self) -> None:
        self.admin(
            "INSERT INTO access_control_rules VALUES ('gastos.informes','ver','superadmin','dirección',false,true,now())"
        )
        self.error("AUTH_REQUIRED", lambda: self.backend.document(self.actor, FIRST))
        self.admin(
            "DELETE FROM access_control_rules; UPDATE empleados SET activo=false WHERE id='"
            + ACTOR
            + "'"
        )
        self.error("AUTH_REQUIRED", lambda: self.backend.document(self.actor, FIRST))

    def test_revoke_during_audit_denies_release(self) -> None:
        original = self.audit.append

        def revoke(event):
            original(event)
            if event.get("decision") == "allow":
                self.admin("UPDATE empleados SET activo=false WHERE id='" + ACTOR + "'")

        self.audit.append = revoke
        self.error(
            "AUTH_REQUIRED",
            lambda: self.gateway.call(
                "samchat.document_status", {"document_id": FIRST}, self.actor
            ),
        )

    def test_identity_payload_and_foreign_scope_are_rejected(self) -> None:
        self.error(
            "INVALID_ARGUMENTS",
            lambda: self.gateway.call(
                "samchat.document_status",
                {"document_id": FIRST, "actor_uuid": ACTOR},
                self.actor,
            ),
        )
        self.error(
            "AUTH_REQUIRED",
            lambda: self.backend.document(
                replace(self.actor, generation="forged"), FIRST
            ),
        )
        self.error(
            "ACTION_NOT_REGISTERED",
            lambda: self.gateway.call("transfer.create_draft", {}, self.actor),
        )
        self.error(
            "ACTION_NOT_REGISTERED",
            lambda: self.backend.study(self.actor, {"dataset": "budget"}),
        )

    def test_native_role_no_base_secret_writes_temp_or_identity_forgery(self) -> None:
        denied = [
            "SELECT * FROM public.documentos",
            "SELECT * FROM public.empleados",
            "UPDATE samchat_ro_fmfg_v1.documentos SET estado='aprobado'",
            "DELETE FROM samchat_ro_fmfg_v1.documentos",
            "CREATE TEMP TABLE denied(id int)",
            "CREATE TABLE public.denied(id int)",
            "SET ROLE samchat_ro_fmfg_owner",
        ]
        for sql in denied:
            completed = subprocess.run(
                [
                    "psql",
                    "-XAt",
                    "-h",
                    str(self.socket),
                    "-U",
                    "samchat_ro_fmfg",
                    "-d",
                    "devnous_db",
                    "-v",
                    "ON_ERROR_STOP=1",
                    "-c",
                    sql,
                ],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(completed.returncode, 0, sql)
        # Granting SELECT to another login does not forge session_user in views.
        self.admin(
            "GRANT USAGE ON SCHEMA samchat_ro_fmfg_v1 TO outside_actor; GRANT SELECT ON samchat_ro_fmfg_v1.documentos TO outside_actor"
        )
        result = subprocess.check_output(
            [
                "psql",
                "-XAt",
                "-h",
                str(self.socket),
                "-U",
                "outside_actor",
                "-d",
                "devnous_db",
                "-c",
                "SELECT count(*) FROM samchat_ro_fmfg_v1.documentos",
            ],
            text=True,
        ).strip()
        self.assertEqual(result, "0")
        self.admin(
            "REVOKE SELECT ON samchat_ro_fmfg_v1.documentos FROM outside_actor; REVOKE USAGE ON SCHEMA samchat_ro_fmfg_v1 FROM outside_actor"
        )

    def test_privilege_drift_other_schema_create_and_definer_deny(self) -> None:
        self.admin(
            "CREATE SCHEMA readonly_drift_case; CREATE TABLE readonly_drift_case.exposed(id int); GRANT SELECT ON readonly_drift_case.exposed TO PUBLIC"
        )
        try:
            self.error(
                "ROLE_UNPROVEN", lambda: self.backend.document(self.actor, FIRST)
            )
            self.admin(
                "REVOKE SELECT ON readonly_drift_case.exposed FROM PUBLIC; GRANT CREATE ON SCHEMA samchat_ro_fmfg_v1 TO samchat_ro_fmfg"
            )
            try:
                self.error(
                    "ROLE_UNPROVEN", lambda: self.backend.document(self.actor, FIRST)
                )
            finally:
                self.admin(
                    "REVOKE CREATE ON SCHEMA samchat_ro_fmfg_v1 FROM samchat_ro_fmfg"
                )
            self.admin(
                "CREATE FUNCTION readonly_drift_case.exposed_function() RETURNS int LANGUAGE sql SECURITY DEFINER AS 'SELECT 1'"
            )
            self.error(
                "ROLE_UNPROVEN", lambda: self.backend.document(self.actor, FIRST)
            )
        finally:
            self.admin(
                "DROP FUNCTION IF EXISTS readonly_drift_case.exposed_function(); DROP TABLE readonly_drift_case.exposed; DROP SCHEMA readonly_drift_case"
            )


if __name__ == "__main__":
    unittest.main()
