from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from devnous.gastos.services import documento_telegram


def test_telegram_approval_guard_allows_assigned_approver_without_admin_role():
    approver_id = uuid4()
    empleado = SimpleNamespace(id=approver_id, rol="empleado")
    documento = SimpleNamespace(
        estado="enviado",
        empleado=SimpleNamespace(id=uuid4(), aprobador_id=uuid4()),
        beneficiario_empleado=SimpleNamespace(id=uuid4(), aprobador_id=approver_id),
    )

    assert documento_telegram.approver_can_see_document_in_queue(empleado, documento) is True


def test_telegram_approval_guard_blocks_stale_buttons_after_state_moves():
    approver_id = uuid4()
    empleado = SimpleNamespace(id=approver_id, rol="empleado")
    documento = SimpleNamespace(
        estado="aprobado",
        empleado=SimpleNamespace(id=uuid4(), aprobador_id=uuid4()),
        beneficiario_empleado=SimpleNamespace(id=uuid4(), aprobador_id=approver_id),
    )

    assert documento_telegram.approver_can_see_document_in_queue(empleado, documento) is False



def test_telegram_pending_command_does_not_require_admin_role():
    source = Path("src/devnous/gastos/services/telegram_document_runtime.py").read_text(encoding="utf-8")
    start = source.index("async def send_pendientes")
    end = source.index("async def send_mis_solicitudes", start)
    send_pendientes = source[start:end]

    assert "APPROVER_QUEUE_ROLES" not in send_pendientes
    assert "query_pending_documentos_for_approver" in send_pendientes
