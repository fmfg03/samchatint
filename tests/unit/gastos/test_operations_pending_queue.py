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
@pytest.mark.parametrize("role", ["superadmin", "super_admin"])
@pytest.mark.parametrize("operations_holder", [False, True])
async def test_superadmin_queue_requires_operations_authority_for_reference(
    monkeypatch, role, operations_holder, channel
):
    actor = str(uuid4())
    database = sqlite3.connect(":memory:")
    database.create_function(
        "btrim", 1, lambda value: value.strip() if value else value
    )
    database.executescript("""
        CREATE TABLE documentos (id TEXT, estado TEXT, referencia_operaciones TEXT,
                                 enviado_en TEXT, creado_en TEXT);
        CREATE TABLE aprobaciones (tipo_entidad TEXT, entidad_id TEXT,
                         aprobador_id TEXT, accion TEXT, fecha TEXT);
        CREATE TABLE documento_authorization_routes (documento_id TEXT,
                 eligible_position_keys TEXT, requires_operations_reference BOOLEAN,
                 eligible_empleado_ids TEXT);
        CREATE TABLE authorization_position_assignments (empleado_id TEXT,
                                       position_key TEXT, active BOOLEAN);
        CREATE TABLE authorization_positions (position_key TEXT, active BOOLEAN);
        CREATE TABLE empleados (id TEXT, activo BOOLEAN);
        INSERT INTO documentos VALUES
            ('operations', 'enviado', '177', NULL, '2026-01-01');
        INSERT INTO documentos VALUES ('natural', 'enviado', NULL, NULL, '2026-01-01');
        INSERT INTO documentos VALUES ('blank', 'enviado', '  ', NULL, '2026-01-01');
        INSERT INTO documentos VALUES ('paid', 'pagado', '178', NULL, '2026-01-01');
        INSERT INTO authorization_positions VALUES ('director_operaciones', TRUE);
        """)
    database.execute("INSERT INTO empleados VALUES (?, TRUE)", (actor,))
    if operations_holder:
        database.execute(
            "INSERT INTO authorization_position_assignments VALUES (?, ?, TRUE)",
            (actor, "director_operaciones"),
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
            assert "::VARCHAR" in str(compiled)
            predicate = re.sub(
                r"::(?:jsonb|text|uuid|varchar)(?:\(\d+\))?",
                "",
                str(compiled),
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
            visible.extend(
                row[0]
                for row in database.execute(
                    "SELECT id FROM documentos WHERE " + predicate, params
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
    assert set(visible) == (
        {"operations", "natural", "blank"}
        if operations_holder
        else {"natural", "blank"}
    )
    canonical_policy.assert_called_once_with()
