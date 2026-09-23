from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from devnous.gastos.routes import user_routes


class _Form(dict):
    def getlist(self, key):
        value = self.get(key, [])
        if isinstance(value, list):
            return value
        return [value]


@pytest.mark.asyncio
async def test_queue_single_reject_requires_reason_before_canonical_writer(monkeypatch):
    transition = AsyncMock()
    monkeypatch.setattr(user_routes, "transition_documento_workflow", transition)

    response = await user_routes.rechazar_documento(
        documento_id=uuid4(),
        request=SimpleNamespace(),
        session=AsyncMock(),
        current_empleado=SimpleNamespace(id=uuid4()),
        comentario="   ",
        next="/documentos/pendientes",
    )

    assert response.status_code == 303
    assert "reject_reason_required" in response.headers["location"]
    transition.assert_not_awaited()


@pytest.mark.asyncio
async def test_bulk_reject_requires_common_reason_before_canonical_writer(monkeypatch):
    monkeypatch.setattr(
        user_routes,
        "_can_review_pending_approvals",
        AsyncMock(return_value=True),
    )
    transition = AsyncMock()
    monkeypatch.setattr(user_routes, "transition_documento_workflow", transition)

    request = SimpleNamespace(
        form=AsyncMock(return_value=_Form({"bulk_comentario": ""}))
    )
    response = await user_routes.documentos_pendientes_accion_lote(
        request=request,
        session=AsyncMock(),
        current_empleado=SimpleNamespace(id=uuid4()),
        action="reject",
        next="/documentos/pendientes",
    )

    assert response.status_code == 303
    assert "reject_reason_required" in response.headers["location"]
    transition.assert_not_awaited()


@pytest.mark.asyncio
async def test_bulk_reject_passes_reason_to_canonical_writer(monkeypatch):
    monkeypatch.setattr(
        user_routes,
        "_can_review_pending_approvals",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(user_routes, "audit_context_from_request", lambda _request: {})
    transition = AsyncMock()
    monkeypatch.setattr(user_routes, "transition_documento_workflow", transition)

    documento_id = uuid4()
    request = SimpleNamespace(
        form=AsyncMock(
            return_value=_Form(
                {
                    "bulk_comentario": "Falta soporte contractual",
                    "documento_ids": [str(documento_id)],
                }
            )
        )
    )
    response = await user_routes.documentos_pendientes_accion_lote(
        request=request,
        session=AsyncMock(),
        current_empleado=SimpleNamespace(id=uuid4()),
        action="reject",
        next="/documentos/pendientes",
    )

    assert response.status_code == 303
    transition.assert_awaited_once()
    assert transition.await_args.kwargs["documento_id"] == documento_id
    assert transition.await_args.kwargs["action"] == "reject"
    assert transition.await_args.kwargs["comentario"] == "Falta soporte contractual"


def test_pending_approval_ui_collects_single_and_bulk_rejection_reasons():
    source = open("src/devnous/gastos/routes/user_routes.py", encoding="utf-8").read()
    start = source.index("async def documentos_pendientes")
    end = source.index('@router.post("/documentos/pendientes/accion-lote")', start)
    block = source[start:end]

    assert "Motivo si rechazas" in block
    assert "Motivo de rechazo" in block
    assert "data-reject-comment" in block
    assert 'name="bulk_comentario"' in block
    assert "Escribe el motivo del rechazo" in block
