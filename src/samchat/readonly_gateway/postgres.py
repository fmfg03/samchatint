"""Canonical physical-column PostgreSQL adapter; no driver or credential loader.

Default binding cannot open a connection. Only disposable-test bindings are
admitted in this candidate; server admission requires the exact future receipt.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Callable, Mapping, Protocol, Sequence
from uuid import UUID

from .canonical_policy import document_route_approver_sql
from .contracts import Principal, ReadError


class Cursor(Protocol):
    description: Sequence[Sequence[Any]] | None

    def execute(self, sql: str, parameters: Mapping[str, Any] | None = None) -> Any: ...
    def fetchmany(self, size: int) -> Sequence[Sequence[Any]]: ...
    def close(self) -> None: ...


class Connection(Protocol):
    def cursor(self) -> Cursor: ...
    def rollback(self) -> None: ...
    def close(self) -> None: ...
    def cancel(self) -> None: ...


@dataclass(frozen=True)
class ScopeGrant:
    """Owner-resolved document visibility, independent of approval authority."""

    actor_uuid: str
    document_ids: frozenset[str]
    installation: str
    generation: str
    owner_version: str
    budget_version_ids: frozenset[str] = frozenset()


class ScopeOwner(Protocol):
    def resolve(self, principal: Principal) -> ScopeGrant: ...


class BudgetOwner(Protocol):
    def snapshot(
        self, principal: Principal, grant: ScopeGrant, request: Any
    ) -> dict[str, Any]: ...


class DenyScopeOwner:
    def resolve(self, principal: Principal) -> ScopeGrant:
        raise ReadError("SCOPE_UNPROVEN")


@dataclass(frozen=True)
class PostgresBinding:
    kind: str = "disabled"
    installation: str = ""
    database: str = ""
    role: str = ""
    expected_schema: str = ""
    owner_version: str = ""
    activation_receipt_sha256: str = ""

    def check(self) -> None:
        if self.kind not in {"disposable_test", "production"}:
            raise ReadError("ACTIVATION_REQUIRED")
        if not all(
            (
                self.installation,
                self.database,
                self.role,
                self.expected_schema,
                self.owner_version,
            )
        ):
            raise ReadError("SCOPE_UNPROVEN")
        if (
            self.kind == "production"
            and re.fullmatch(r"[0-9a-f]{64}", self.activation_receipt_sha256) is None
        ):
            raise ReadError("ACTIVATION_REQUIRED")


# Only columns actually inspected in the base; no imaginary tenant columns.
EXPECTED_COLUMNS = {
    "documentos": {
        "id",
        "torneo_id",
        "tipo",
        "estado",
        "numero_referencia",
        "referencia_operaciones",
        "referencia_base",
        "creado_en",
        "enviado_en",
        "currency",
        "monto_total",
        "budget_concept_id",
        "pagado_en",
        "gasto_generado_id",
        "cuenta_gastos_id",
        "concepto_pago",
        "informe_origen_id",
        "motivo_comprobacion_parcial",
    },
    "aprobaciones": {"id", "entidad_id", "tipo_entidad", "accion", "fecha"},
    "expense_reports": {
        "id",
        "documento_id",
        "informe_documento_id",
        "cuenta_gastos_id",
        "estado_gasto",
    },
    "anticipos": {"id", "documento_id", "estado"},
    "reembolsos": {"id", "documento_id", "cuenta_gastos_id", "estado", "tipo"},
    "documento_authorization_routes": {
        "documento_id",
        "eligible_empleado_ids",
        "eligible_position_keys",
        "requires_operations_reference",
    },
    "authorization_position_assignments": {"position_key", "empleado_id", "active"},
    "authorization_positions": {"position_key", "active"},
    "empleados": {"id", "activo"},
}


def normalise(value: Any) -> Any:
    if isinstance(value, (date, datetime, UUID, Decimal)):
        return str(value)
    return value


class PostgresCanonicalAdapter:
    def __init__(
        self,
        binding: PostgresBinding,
        connect: Callable[[], Connection],
        owner: ScopeOwner | None = None,
        budget_owner: BudgetOwner | None = None,
    ) -> None:
        self.binding, self.connect = binding, connect
        self.owner = owner or DenyScopeOwner()
        self.budget_owner = budget_owner
        self._active: Connection | None = None
        self._lock = threading.RLock()
        self.query_count = 0
        self.schema_fingerprint = binding.expected_schema

    def interrupt(self) -> None:
        with self._lock:
            if self._active:
                self._active.cancel()

    def _grant(self, principal: Principal) -> ScopeGrant:
        self.binding.check()
        if not principal.valid(datetime.now().astimezone()):
            raise ReadError("AUTH_REQUIRED")
        grant = self.owner.resolve(principal)
        if (
            grant.installation != principal.installation
            or grant.installation != self.binding.installation
            or grant.generation != principal.generation
            or grant.owner_version != self.binding.owner_version
            or not 1 <= len(grant.document_ids) <= 1000
        ):
            raise ReadError("SCOPE_UNPROVEN")
        try:
            UUID(grant.actor_uuid)
            for identifier in (*grant.document_ids, *principal.tournaments):
                UUID(identifier)
        except (ValueError, TypeError) as exc:
            raise ReadError("SCOPE_UNPROVEN") from exc
        return grant

    @staticmethod
    def _scope(principal: Principal, grant: ScopeGrant) -> tuple[str, dict[str, Any]]:
        return (
            "d.id = ANY(%(documents)s::uuid[]) AND d.torneo_id = ANY(%(tournaments)s::uuid[])",
            {
                "documents": sorted(grant.document_ids),
                "tournaments": sorted(principal.tournaments),
            },
        )

    @staticmethod
    def _rows(
        cursor: Cursor, sql: str, args: Mapping[str, Any], cap: int
    ) -> list[dict[str, Any]]:
        cursor.execute(sql, args)
        columns = [str(field[0]) for field in cursor.description or ()]
        rows = cursor.fetchmany(cap + 1)
        if len(rows) > cap:
            raise ReadError("OUTPUT_LIMIT")
        return [dict(zip(columns, map(normalise, row))) for row in rows]

    def _query(
        self,
        principal: Principal,
        build: Callable[[ScopeGrant], tuple[str, dict[str, Any]]],
        cap: int,
    ) -> list[dict[str, Any]]:
        grant = self._grant(principal)  # Before opening any database connection.
        sql, args = build(grant)
        connection: Connection | None = None
        cursor: Cursor | None = None
        try:
            connection = self.connect()
            with self._lock:
                self._active = connection
            cursor = connection.cursor()
            cursor.execute("BEGIN READ ONLY")
            cursor.execute("SET LOCAL statement_timeout = '3000ms'")
            cursor.execute("SET LOCAL lock_timeout = '500ms'")
            readonly = self._rows(
                cursor,
                "SELECT current_setting('transaction_read_only') AS readonly",
                {},
                1,
            )
            if len(readonly) != 1 or readonly[0]["readonly"] != "on":
                raise ReadError("READONLY_UNPROVEN")
            role = self._rows(
                cursor,
                "SELECT current_database() AS db,current_user AS role,session_user AS session_role,rolsuper,rolcreaterole,rolcreatedb,rolreplication,rolbypassrls,has_database_privilege(current_user,current_database(),'CREATE') AS db_create,has_database_privilege(current_user,current_database(),'TEMP') AS db_temp,has_schema_privilege(current_user,'public','CREATE') AS schema_create FROM pg_roles WHERE rolname=current_user",
                {},
                1,
            )
            elevated = (
                "rolsuper",
                "rolcreaterole",
                "rolcreatedb",
                "rolreplication",
                "rolbypassrls",
            )
            if (
                len(role) != 1
                or role[0]["db"] != self.binding.database
                or role[0]["role"] != self.binding.role
                or role[0]["session_role"] != self.binding.role
                or any(role[0][key] for key in elevated)
                or (
                    self.binding.kind == "production"
                    and any(
                        role[0][key]
                        for key in ("db_create", "db_temp", "schema_create")
                    )
                )
            ):
                raise ReadError("ROLE_UNPROVEN")
            inherited = self._rows(
                cursor,
                "SELECT roleid FROM pg_auth_members WHERE member=(SELECT oid FROM pg_roles WHERE rolname=current_user)",
                {},
                1,
            )
            if inherited:
                raise ReadError("ROLE_UNPROVEN")
            if self.binding.kind == "production":
                privileges = self._rows(
                    cursor,
                    "SELECT c.relname AS table_name,has_table_privilege(current_user,c.oid,'SELECT') AS table_select,has_table_privilege(current_user,c.oid,'INSERT') AS can_insert,has_table_privilege(current_user,c.oid,'UPDATE') AS can_update,has_table_privilege(current_user,c.oid,'DELETE') AS can_delete,has_table_privilege(current_user,c.oid,'TRUNCATE') AS can_truncate,has_table_privilege(current_user,c.oid,'REFERENCES') AS can_reference,has_table_privilege(current_user,c.oid,'TRIGGER') AS can_trigger FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relname=ANY(%(tables)s::text[]) ORDER BY c.relname",
                    {"tables": sorted(EXPECTED_COLUMNS)},
                    100,
                )
                if len(privileges) != len(EXPECTED_COLUMNS) or any(
                    row["table_select"]
                    or any(
                        row[key]
                        for key in (
                            "can_insert",
                            "can_update",
                            "can_delete",
                            "can_truncate",
                            "can_reference",
                            "can_trigger",
                        )
                    )
                    for row in privileges
                ):
                    raise ReadError("ROLE_UNPROVEN")
                selected_columns = self._rows(
                    cursor,
                    "SELECT cols.table_name,cols.column_name FROM information_schema.columns cols WHERE cols.table_schema='public' AND cols.table_name=ANY(%(tables)s::text[]) AND has_column_privilege(current_user,format('%I.%I',cols.table_schema,cols.table_name),cols.column_name,'SELECT') ORDER BY cols.table_name,cols.column_name",
                    {"tables": sorted(EXPECTED_COLUMNS)},
                    500,
                )
                selected = {
                    (row["table_name"], row["column_name"]) for row in selected_columns
                }
                expected_selected = {
                    (table, column)
                    for table, columns in EXPECTED_COLUMNS.items()
                    for column in columns
                }
                public_privileges = self._rows(
                    cursor,
                    "SELECT table_name,column_name,privilege_type FROM information_schema.column_privileges WHERE table_schema='public' AND table_name=ANY(%(tables)s::text[]) AND grantee='PUBLIC' UNION ALL SELECT table_name,NULL AS column_name,privilege_type FROM information_schema.table_privileges WHERE table_schema='public' AND table_name=ANY(%(tables)s::text[]) AND grantee='PUBLIC'",
                    {"tables": sorted(EXPECTED_COLUMNS)},
                    500,
                )
                exposed_functions = self._rows(
                    cursor,
                    "SELECT p.oid FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='agentius_read_v1' AND has_function_privilege(current_user,p.oid,'EXECUTE')",
                    {},
                    1,
                )
                if (
                    selected != expected_selected
                    or public_privileges
                    or exposed_functions
                ):
                    raise ReadError("ROLE_UNPROVEN")
            schema = self._rows(
                cursor,
                "SELECT table_name,column_name,data_type,is_nullable FROM information_schema.columns WHERE table_schema='public' AND table_name=ANY(%(tables)s::text[]) ORDER BY table_name,column_name",
                {"tables": sorted(EXPECTED_COLUMNS)},
                500,
            )
            present: dict[str, set[str]] = {}
            for row in schema:
                present.setdefault(row["table_name"], set()).add(row["column_name"])
            fingerprint = hashlib.sha256(
                json.dumps(schema, sort_keys=True).encode()
            ).hexdigest()
            if fingerprint != self.binding.expected_schema or any(
                not columns <= present.get(table, set())
                for table, columns in EXPECTED_COLUMNS.items()
            ):
                raise ReadError("SCHEMA_MISMATCH")
            self.query_count += 1
            return self._rows(cursor, sql, args, cap)
        except ReadError:
            raise
        except Exception as exc:
            raise ReadError("SOURCE_UNAVAILABLE") from exc
        finally:
            with self._lock:
                self._active = None
            if connection:
                cleanup_failed = False
                for cleanup in (
                    connection.rollback,
                    cursor.close if cursor else lambda: None,
                    connection.close,
                ):
                    try:
                        cleanup()
                    except Exception:
                        cleanup_failed = True
                if cleanup_failed:
                    raise ReadError("SOURCE_UNAVAILABLE")

    def observation(
        self, principal: Principal, document_id: str
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        predicate = re.sub(
            r":route_actor\b",
            "%(actor)s",
            document_route_approver_sql("d", "route_actor"),
        )

        def build(grant: ScopeGrant) -> tuple[str, dict[str, Any]]:
            scope, args = self._scope(principal, grant)
            args["id"] = document_id
            args["actor"] = grant.actor_uuid
            return (
                "SELECT d.id AS document_id,d.torneo_id AS tournament_id,d.tipo AS kind,d.estado AS state,d.referencia_operaciones AS reference_operations,d.numero_referencia AS reference_samchat,d.referencia_base AS reference_base,d.creado_en AS created_at,d.enviado_en AS submitted_at,d.currency,d.monto_total AS amount,(d.budget_concept_id IS NOT NULL) AS classification_present,"
                "d.informe_origen_id IS NOT NULL AS partial_report,"
                "NULLIF(BTRIM(d.motivo_comprobacion_parcial),'') IS NOT NULL AS partial_reason_present,"
                "(LOWER(COALESCE(d.estado,'')) IN ('pagado','cerrado','reembolsado','aplicado','liquidado') OR d.pagado_en IS NOT NULL OR d.gasto_generado_id IS NOT NULL OR EXISTS (SELECT 1 FROM anticipos ant WHERE ant.documento_id=d.id AND LOWER(COALESCE(ant.estado,'')) NOT IN ('cancelado','cancelada','rechazado','rechazada','anulado','anulada')) OR EXISTS (SELECT 1 FROM reembolsos rem WHERE (rem.documento_id=d.id OR (d.cuenta_gastos_id IS NOT NULL AND rem.cuenta_gastos_id=d.cuenta_gastos_id)) AND NOT (d.tipo='INFORME' AND LOWER(COALESCE(d.estado,'')) IN ('borrador','control_presupuestal','enviado') AND LOWER(COALESCE(rem.tipo,''))='devolucion') AND LOWER(COALESCE(rem.estado,'')) NOT IN ('cancelado','cancelada','rechazado','rechazada','anulado','anulada'))) AS financial_terminal,"
                "(SELECT COUNT(*) FROM expense_reports e WHERE (e.documento_id=d.id OR e.informe_documento_id=d.id OR (d.cuenta_gastos_id IS NOT NULL AND e.cuenta_gastos_id=d.cuenta_gastos_id)) AND e.estado_gasto<>'cancelado') AS active_expenses,"
                "EXISTS (SELECT 1 FROM aprobaciones prior WHERE prior.tipo_entidad='documento' AND prior.entidad_id=d.id AND prior.accion='aprobar') AS recorded_approval,"
                "(d.tipo='SOLICITUD' AND d.cuenta_gastos_id IS NOT NULL AND COALESCE(d.concepto_pago,'') LIKE 'Reembolso de saldo a favor%') AS employee_reimbursement,"
                "EXISTS (SELECT 1 FROM documentos informe WHERE informe.cuenta_gastos_id=d.cuenta_gastos_id AND informe.tipo='INFORME' AND informe.estado='aprobado') AS linked_informe_approved,"
                f"({predicate}) AS actor_can_decide FROM documentos d WHERE {scope} AND d.id=%(id)s::uuid",
                args,
            )

        rows = self._query(principal, build, 2)
        if len(rows) != 1:
            raise ReadError("NOT_FOUND_OR_NOT_VISIBLE")
        actor_can_decide = rows[0].pop("actor_can_decide")
        authority = {
            "resolution": "resolved",
            "actor_can_decide": actor_can_decide,
            "approver_aliases": [],
            "policy_versions": [self.binding.owner_version],
            "gaps": ["effective_approver_alias_list_not_projected"],
        }
        document = {
            **rows[0],
            "authority_coverage": "route_predicate_and_ordered_workflow_facts",
        }
        return document, authority

    def document(self, principal: Principal, document_id: str) -> dict[str, Any]:
        return self.observation(principal, document_id)[0]

    def reference(self, principal: Principal, args: dict[str, Any]) -> dict[str, Any]:
        column = {
            "operations": "referencia_operaciones",
            "samchat": "numero_referencia",
            "base": "referencia_base",
        }[args["reference_kind"]]

        def build(grant: ScopeGrant) -> tuple[str, dict[str, Any]]:
            scope, params = self._scope(principal, grant)
            params["ref"] = args["reference"]
            if args.get("tournament_id"):
                scope += " AND d.torneo_id=%(tournament)s::uuid"
                params["tournament"] = args["tournament_id"]
            return (
                f"SELECT d.id AS document_id,d.torneo_id AS tournament_id,d.tipo AS kind,d.estado AS state FROM documentos d WHERE {scope} AND d.{column}=%(ref)s ORDER BY d.id LIMIT 21",
                params,
            )

        rows = self._query(principal, build, 21)
        if not rows:
            raise ReadError("NOT_FOUND_OR_NOT_VISIBLE")
        if len(rows) > 20:
            raise ReadError("OUTPUT_LIMIT")
        return {
            "candidates": rows,
            "resolution": "unique" if len(rows) == 1 else "ambiguous",
            "selector_required": len(rows) != 1,
        }

    def authority(self, principal: Principal, document_id: str) -> dict[str, Any]:
        return self.observation(principal, document_id)[1]

    def audit(
        self,
        principal: Principal,
        document_id: str,
        page: int,
        after_date: str,
        after_id: str,
    ) -> list[dict[str, Any]]:
        self.document(principal, document_id)

        def build(grant: ScopeGrant) -> tuple[str, dict[str, Any]]:
            scope, args = self._scope(principal, grant)
            args.update(
                id=document_id,
                page=page + 1,
                after_date=after_date or "0001-01-01",
                after_id=after_id or "00000000-0000-0000-0000-000000000000",
            )
            return (
                "SELECT a.id AS event_id,a.fecha AS occurred_at,a.accion AS action "
                f"FROM aprobaciones a JOIN documentos d ON d.id=a.entidad_id WHERE {scope} AND d.id=%(id)s::uuid AND a.tipo_entidad='documento' AND (a.fecha,a.id)>(%(after_date)s::timestamptz,%(after_id)s::uuid) ORDER BY a.fecha,a.id LIMIT %(page)s",
                args,
            )

        return self._query(principal, build, 101)

    def select(
        self, sql: str, parameters: dict[str, Any], *, cap: int = 1001
    ) -> list[dict[str, Any]]:
        raise ReadError("UNREGISTERED_QUERY")

    def study(
        self,
        principal: Principal,
        args: dict[str, Any],
        *,
        after_date: str = "",
        after_id: str = "",
    ) -> dict[str, Any]:
        if args.get("dataset") == "budget" or "version_id" in args:
            if self.budget_owner is None:
                raise ReadError("CANONICAL_BUDGET_OWNER_UNBOUND")
            if not args.get("tournament_id") or not args.get("version_id"):
                raise ReadError("INVALID_ARGUMENTS")
            try:
                start = date.fromisoformat(args["date_from"])
                end = date.fromisoformat(args["date_to"])
                edition_year = int(args["edition_year"])
            except (ValueError, TypeError, KeyError) as exc:
                raise ReadError("INVALID_ARGUMENTS") from exc
            if start >= end or (end - start).days > 366:
                raise ReadError("INVALID_ARGUMENTS")
            from .owners import BudgetRequest

            grant = self._grant(principal)
            snapshot = self.budget_owner.snapshot(
                principal,
                grant,
                BudgetRequest(
                    tournament_id=args["tournament_id"],
                    version_id=args["version_id"],
                    edition_year=edition_year,
                    date_from=start,
                    date_to=end,
                ),
            )
            return {
                "rows": [snapshot],
                "truncated": False,
                "amount_basis": "canonical_budget_owner_projection",
            }
        try:
            start, end = date.fromisoformat(args["date_from"]), date.fromisoformat(
                args["date_to"]
            )
        except ValueError as exc:
            raise ReadError("INVALID_ARGUMENTS") from exc
        if start >= end or (end - start).days > 366:
            raise ReadError("INVALID_ARGUMENTS")

        def build(grant: ScopeGrant) -> tuple[str, dict[str, Any]]:
            scope, params = self._scope(principal, grant)
            scope += " AND d.creado_en >= %(start)s::timestamptz AND d.creado_en < %(end)s::timestamptz"
            params.update(start=start.isoformat(), end=end.isoformat())
            for key, column in (
                ("state", "estado"),
                ("kind", "tipo"),
                ("tournament_id", "torneo_id"),
            ):
                if key in args:
                    scope += f" AND d.{column}=%({key})s"
                    params[key] = args[key]
            if "group_by" in args:
                group = {
                    "state": "d.estado",
                    "kind": "d.tipo",
                    "month": "to_char(d.creado_en,'YYYY-MM')",
                    "tournament_id": "d.torneo_id",
                }[args["group_by"]]
                return (
                    f"SELECT {group} AS group_key,d.currency,COUNT(*) AS documents,SUM(d.monto_total) AS document_amount,COUNT(d.monto_total) AS amount_coverage FROM documentos d WHERE {scope} GROUP BY {group},d.currency ORDER BY group_key,d.currency LIMIT 101",
                    params,
                )
            params["page"] = args.get("page_size", 25) + 1
            if after_date and after_id:
                scope += " AND (d.creado_en,d.id)>(%(after_date)s::timestamptz,%(after_id)s::uuid)"
                params.update(after_date=after_date, after_id=after_id)
            return (
                f"SELECT d.id AS document_id,d.torneo_id AS tournament_id,d.tipo AS kind,d.estado AS state,d.creado_en AS created_at,d.currency,d.monto_total AS amount FROM documentos d WHERE {scope} ORDER BY d.creado_en,d.id LIMIT %(page)s",
                params,
            )

        rows = self._query(principal, build, 101)
        if "group_by" in args:
            if len(rows) > 100:
                raise ReadError("OUTPUT_LIMIT")
            return {
                "groups": rows,
                "truncated": False,
                "amount_basis": "persisted_document_amount_not_canonical_financial_actuals",
            }
        page = args.get("page_size", 25)
        return {"rows": rows[:page], "truncated": len(rows) > page, "next_cursor": None}
