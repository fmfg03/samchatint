from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from devnous.gastos.routes import admin_routes


def _upload(filename: str, content: bytes):
    return SimpleNamespace(filename=filename, read=AsyncMock(return_value=content))


@pytest.mark.asyncio
async def test_finance_training_routes_hide_unexpected_errors(monkeypatch):
    logged = []
    monkeypatch.setattr(
        admin_routes.logger,
        "exception",
        lambda *args, **kwargs: logged.append((args, kwargs)),
    )
    monkeypatch.setattr(admin_routes, "_repo_root", lambda: "/tmp/repo")
    monkeypatch.setattr(
        admin_routes,
        "generate_finance_training_dataset",
        AsyncMock(side_effect=RuntimeError("training secret")),
    )
    monkeypatch.setattr(
        admin_routes,
        "cleanup_finance_training_dataset",
        AsyncMock(side_effect=RuntimeError("cleanup secret")),
    )
    monkeypatch.setattr(
        admin_routes,
        "reset_finance_training_dataset",
        AsyncMock(side_effect=RuntimeError("reset secret")),
    )
    current = SimpleNamespace(id=uuid4())

    responses = [
        await admin_routes.finance_training_generate(
            session=AsyncMock(),
            current_empleado=current,
            batch_key="demo",
            modo="apply",
            force=False,
            seed=42,
        ),
        await admin_routes.finance_training_cleanup(
            session=AsyncMock(),
            current_empleado=current,
            batch_key="demo",
            modo="apply",
        ),
        await admin_routes.finance_training_reset(
            session=AsyncMock(),
            current_empleado=current,
            batch_key="demo",
            seed=42,
        ),
    ]

    for response in responses:
        assert response.status_code == 303
        assert "secret" not in response.headers["location"]
        assert "Ocurri" in response.headers["location"]

    assert logged


@pytest.mark.asyncio
async def test_budget_routes_hide_unexpected_errors_and_keep_value_error(monkeypatch):
    logged = []
    monkeypatch.setattr(
        admin_routes.logger,
        "exception",
        lambda *args, **kwargs: logged.append((args, kwargs)),
    )
    monkeypatch.setattr(
        admin_routes,
        "_require_budget_access",
        lambda *args, **kwargs: None,
    )
    current = SimpleNamespace(id=uuid4())

    session = AsyncMock()
    monkeypatch.setattr(
        admin_routes,
        "import_budget_artifact",
        AsyncMock(side_effect=RuntimeError("budget secret")),
    )
    monkeypatch.setattr(
        admin_routes,
        "bulk_save_budget_concepts",
        AsyncMock(side_effect=RuntimeError("concept secret")),
    )
    monkeypatch.setattr(
        admin_routes,
        "hide_budget_concept",
        AsyncMock(side_effect=RuntimeError("hide secret")),
    )
    monkeypatch.setattr(
        admin_routes,
        "import_budget_lines_upload",
        AsyncMock(side_effect=ValueError("archivo inválido")),
    )

    import_response = await admin_routes.admin_presupuestos_import_default(
        session=session,
        current_empleado=current,
    )
    bulk_response = await admin_routes.admin_presupuestos_bulk_save_concepts(
        concept_ids=[],
        concept_names=["Demo"],
        tournament_ids=[""],
        sub_proyectos=[""],
        version_id="v1",
        session=AsyncMock(),
        current_empleado=current,
    )
    hide_response = await admin_routes.admin_presupuestos_hide_concept(
        concept_id=uuid4(),
        version_id="v1",
        drill_dimension=None,
        drill_value=None,
        drill_tournament=None,
        drill_document=None,
        session=AsyncMock(),
        current_empleado=current,
    )
    value_error_response = await admin_routes.admin_presupuestos_import_lines(
        version_id=uuid4(),
        archivo_presupuesto=_upload("presupuesto.xlsx", b"x"),
        session=AsyncMock(),
        current_empleado=current,
    )

    for response in [import_response, bulk_response, hide_response]:
        assert response.status_code == 303
        assert "secret" not in response.headers["location"]
        assert "Ocurri" in response.headers["location"]

    assert "archivo%20inv%C3%A1lido" in value_error_response.headers["location"]
    assert logged
