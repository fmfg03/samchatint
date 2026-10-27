"""Execute existing boundary functions without booting the production web app."""

import ast
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable, Optional, Set
from unittest.mock import AsyncMock
from urllib.parse import quote, unquote

import pytest
from fastapi import Depends, HTTPException
from starlette.responses import RedirectResponse


def functions(path, names, namespace):
    """Load exact function ASTs; imports/application startup are intentionally absent."""
    tree = ast.parse(Path(path).read_text())
    nodes = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in names
    ]
    assert {node.name for node in nodes} == set(names)
    for node in nodes:
        node.decorator_list = []
    exec(compile(ast.Module(body=nodes, type_ignores=[]), path, "exec"), namespace)
    return namespace


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role,allowed",
    [
        ("finanzas", True),
        ("empleado", False),
        ("contabilidad", False),
        ("superadmin", True),
    ],
)
async def test_existing_finance_dependency_requires_authority(role, allowed):
    ns = functions(
        "src/devnous/gastos/routes/dependencies.py",
        [
            "_normalize_role",
            "has_permission",
            "require_permission_factory",
            "require_admin_finanzas",
        ],
        {
            "Any": Any,
            "Iterable": Iterable,
            "Optional": Optional,
            "Set": Set,
            "Empleado": object,
            "Depends": Depends,
            "get_current_empleado": lambda: None,
            "HTTPException": HTTPException,
        },
    )
    gate = ns["require_admin_finanzas"]().dependency
    actor = SimpleNamespace(rol=role, permissions=set())
    if allowed:
        assert await gate(actor) is actor
    else:
        with pytest.raises(HTTPException) as error:
            await gate(actor)
        assert error.value.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("closed", [True, False])
async def test_existing_accounting_period_gate(closed):
    ns = functions(
        "src/devnous/gastos/routes/user_routes.py",
        ["_ensure_accounting_period_open"],
        {
            "AsyncSession": object,
            "_is_accounting_period_closed": AsyncMock(return_value=closed),
            "_safe_redirect_base": lambda target, fallback: target,
            "RedirectResponse": RedirectResponse,
            "quote": quote,
        },
    )
    result = await ns["_ensure_accounting_period_open"](
        object(),
        fiscal_year=2026,
        fiscal_month=1,
        redirect_to="/admin/contabilidad/cierres",
    )
    if closed:
        assert "está cerrado" in unquote(result.headers["location"])
    else:
        assert result is None


@pytest.mark.asyncio
async def test_accounting_manual_cxc_branch_rejects_before_any_write():
    form = {
        "year": "2026",
        "month": "1",
        "fecha_cobro": "2026-01-02",
        "factura_folio": "SYNTHETIC",
        "cliente": "TEST",
        "concepto": "TEST",
        "monto_cobrado": "100",
        "iva_trasladado": "0",
    }
    ns = functions(
        "src/devnous/gastos/routes/user_routes.py",
        ["contabilidad_ingresos_save"],
        {
            "Request": object,
            "AsyncSession": object,
            "Empleado": object,
            "Depends": Depends,
            "get_db_session": lambda: None,
            "require_admin_finanzas": lambda: None,
            "RedirectResponse": RedirectResponse,
            "datetime": datetime,
            "quote": quote,
            "_ensure_accounting_period_open": AsyncMock(return_value=None),
            "_clean_optional_text": lambda value: value or None,
            "_parse_money_decimal": lambda value, label: float(value),
            "CuentaContable": object,
            "_resolve_cxc_account_for_collected_cfdi": AsyncMock(
                return_value=SimpleNamespace(codigo="1150-001")
            ),
        },
    )
    session = AsyncMock()
    response = await ns["contabilidad_ingresos_save"](
        SimpleNamespace(form=AsyncMock(return_value=form)),
        session,
        SimpleNamespace(id="actor"),
    )
    assert "bank_reconciliation_required" in response.headers["location"]
    session.execute.assert_not_awaited()
    session.add.assert_not_called()
    session.flush.assert_not_awaited()
    session.commit.assert_not_awaited()
