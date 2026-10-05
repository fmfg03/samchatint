"""Exercise the actionable queue predicate against isolated in-memory rows."""

import re
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.dialects.postgresql.asyncpg import dialect as asyncpg_dialect

from devnous.gastos.routes import user_routes
from devnous.gastos.services import documento_telegram, project_authorization_service


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["web", "telegram"])
@pytest.mark.parametrize(
    "role", ["superadmin", "super_admin", "admin", "finanzas", "usuario"]
)
@pytest.mark.parametrize("operations_holder", [False, True])
@pytest.mark.parametrize("route_snapshot", [False, True])
@pytest.mark.parametrize("crowded", [False, True])
async def test_pending_queue_superadmin_visibility_preserves_other_roles_scope(
    monkeypatch, role, operations_holder, channel, route_snapshot, crowded
):
    """Broader superadmin visibility preserves every other role's queue scope."""
    actor = str(uuid4())
    database = sqlite3.connect(":memory:")
    database.create_function(
        "btrim", 1, lambda value: value.strip() if value else value
    )
    database.executescript("""
        CREATE TABLE documentos (id TEXT, estado TEXT, referencia_operaciones TEXT,
                                 enviado_en TEXT, creado_en TEXT,
                                 empleado_id TEXT, beneficiario_empleado_id TEXT);
        CREATE TABLE aprobaciones (tipo_entidad TEXT, entidad_id TEXT,
                         aprobador_id TEXT, accion TEXT, fecha TEXT);
        CREATE TABLE documento_authorization_routes (documento_id TEXT,
                 eligible_position_keys TEXT, requires_operations_reference BOOLEAN,
                 eligible_empleado_ids TEXT);
        CREATE TABLE authorization_position_assignments (empleado_id TEXT,
                                       position_key TEXT, active BOOLEAN);
        CREATE TABLE authorization_positions (position_key TEXT, active BOOLEAN);
        CREATE TABLE empleados (id TEXT, activo BOOLEAN, aprobador_id TEXT);
        INSERT INTO documentos (id, estado, referencia_operaciones, enviado_en, creado_en) VALUES
            ('operations', 'enviado', '177', NULL, '2026-01-01');
        INSERT INTO documentos (id, estado, referencia_operaciones, enviado_en, creado_en) VALUES
            ('natural', 'enviado', NULL, NULL, '2026-01-01'),
            ('blank', 'enviado', '  ', NULL, '2026-01-01'),
            ('other-route', 'enviado', NULL, NULL, '2026-01-01'),
            ('paid', 'pagado', '178', NULL, '2026-01-01'),
            ('budget', 'control_presupuestal', '179', NULL, '2026-01-01');
        INSERT INTO authorization_positions VALUES ('director_operaciones', TRUE);
        """)
    database.execute("INSERT INTO empleados VALUES (?, TRUE, ?)", (actor, actor))
    database.execute("UPDATE documentos SET empleado_id = ?", (actor,))
    database.execute(
        "INSERT INTO documento_authorization_routes VALUES (?, ?, FALSE, ?)",
        ("other-route", '["other-position"]', '["other-actor"]'),
    )
    if route_snapshot:
        database.execute(
            "INSERT INTO documento_authorization_routes VALUES (?, ?, TRUE, ?)",
            ("operations", '["director_operaciones"]', '["other-actor"]'),
        )
    if operations_holder:
        database.execute(
            "INSERT INTO authorization_position_assignments VALUES (?, ?, TRUE)",
            (actor, "director_operaciones"),
        )
    if crowded:
        for i in range(35):
            database.execute(
                "INSERT INTO documentos VALUES (?, 'enviado', '300', NULL, '2026-02-01', ?, NULL)",
                (f"observer-{i}", actor),
            )
            database.execute(
                "INSERT INTO documento_authorization_routes VALUES (?, ?, TRUE, ?)",
                (f"observer-{i}", '["director_operaciones"]', '["other-actor"]'),
            )
    canonical_policy = Mock(wraps=user_routes.document_route_approver_sql)
    monkeypatch.setattr(user_routes, "document_route_approver_sql", canonical_policy)
    monkeypatch.setattr(
        project_authorization_service, "document_route_approver_sql", canonical_policy
    )
    monkeypatch.setattr(
        user_routes, "_can_review_pending_approvals", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        user_routes,
        "fetch_documento_aprobador_display_batch",
        AsyncMock(return_value={}),
    )
    for name in (
        "render_top_navigation",
        "_gastos_workspace_nav_html",
        "_gastos_breadcrumb_html",
    ):
        monkeypatch.setattr(user_routes, name, lambda *_args: "")
    monkeypatch.setattr(user_routes, "_render_workspace_hero", lambda **_kwargs: "")

    visible = []

    class Session:
        async def execute(self, query):
            compiled = query._whereclause.compile(
                dialect=asyncpg_dialect(paramstyle="named")
            )
            if channel == "web" or role not in {"superadmin", "super_admin"}:
                assert "::VARCHAR" in str(compiled)
            predicate = re.sub(
                r"::(?:jsonb|text|uuid|varchar)(?:\(\d+\))?",
                "",
                str(compiled),
                flags=re.IGNORECASE,
            )
            if crowded and channel == "telegram" and role in {"superadmin", "super_admin"}:
                ordering = str(query._order_by_clause.compile(
                    dialect=asyncpg_dialect(paramstyle="named")
                ))
                predicate += " ORDER BY " + ordering
                predicate = re.sub(
                    r"::(?:jsonb|text|uuid|varchar)(?:\(\d+\))?", "", predicate,
                    flags=re.IGNORECASE,
                )
            predicate = predicate.replace(
                "SELECT jsonb_array_elements_text(route.eligible_empleado_ids)",
                "SELECT value FROM json_each(route.eligible_empleado_ids)",
            )
            # SQLite executes the same canonical policy; only PostgreSQL casts,
            # bind markers and the JSON set-returning function are adapted.
            predicate = re.sub(r"%\(([^)]+)\)s", r":\1", predicate)
            params = dict(compiled.params)
            params["route_employee_id"] = actor
            for key, value in list(params.items()):
                if isinstance(value, list):
                    markers = ", ".join(f":{key}_{i}" for i in range(len(value)))
                    predicate = predicate.replace(f"__[POSTCOMPILE_{key}]", markers)
                    params.update({f"{key}_{i}": item for i, item in enumerate(value)})
            params.update({"nullif_1": "", "estado_1": "enviado"})
            if crowded and channel == "telegram" and role in {"superadmin", "super_admin"}:
                predicate += " LIMIT 20"
            visible.extend(
                row[0]
                for row in database.execute(
                    "SELECT documentos.id FROM documentos "
                    "LEFT JOIN empleados AS empleados_1 "
                    "ON documentos.empleado_id = empleados_1.id "
                    "LEFT JOIN empleados AS empleados_2 "
                    "ON documentos.beneficiario_empleado_id = empleados_2.id "
                    "WHERE " + predicate,
                    params,
                )
            )
            return SimpleNamespace(
                scalars=lambda: SimpleNamespace(
                    unique=lambda: SimpleNamespace(all=lambda: []), all=lambda: []
                )
            )

    try:
        employee = SimpleNamespace(id=actor, rol=role)
        if channel == "web":
            await user_routes.documentos_pendientes(
                SimpleNamespace(query_params={}), Session(), employee
            )
        else:
            await documento_telegram.query_pending_documentos_for_approver(
                Session(), employee
            )
    finally:
        database.close()
    if role in {"superadmin", "super_admin"}:
        if crowded and channel == "telegram":
            assert len(visible) == 20
            assert {"natural", "blank"} <= set(visible)
            if operations_holder and not route_snapshot:
                assert "operations" in visible
        else:
            assert set(visible) == ({"operations", "natural", "blank", "other-route"}
                                   | ({f"observer-{i}" for i in range(35)} if crowded else set()))
        if channel == "telegram":
            canonical_policy.assert_called_once_with()
        else:
            canonical_policy.assert_not_called()
    else:
        assert set(visible) == (
            {"operations", "natural", "blank"}
            if operations_holder and not route_snapshot
            else {"natural", "blank"}
        )
        canonical_policy.assert_called_once_with()
