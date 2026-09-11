from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
import asyncio
import json

import pytest

from devnous.gastos.services.cfdi_expense_link_service import (
    ExpenseCFDIDuplicateError,
    _enforce_expense_cfdi_uniqueness,
)
from devnous.gastos.models import Adjunto
from devnous.gastos.services.expense_non_deductible_service import (
    logically_delete_non_deductible_proof,
    replace_non_deductible_proof,
)
from devnous.gastos.routes.user_routes import _cfdi_link_transition_audit


REPO_ROOT = Path(__file__).resolve().parents[3]
ROUTES = (REPO_ROOT / "src/devnous/gastos/routes/user_routes.py").read_text(encoding="utf-8")
SERVICE = (
    REPO_ROOT / "src/devnous/gastos/services/expense_non_deductible_service.py"
).read_text(encoding="utf-8")
CFDI_SERVICE = (
    REPO_ROOT / "src/devnous/gastos/services/cfdi_expense_link_service.py"
).read_text(encoding="utf-8")
SCHEMA_GUARD = (REPO_ROOT / "src/devnous/gastos/schema_guard.py").read_text(encoding="utf-8")


def test_non_deductible_proof_is_dedicated_and_auditable() -> None:
    assert 'NON_DEDUCTIBLE_PROOF_CATEGORY = "comprobante_no_deducible"' in SERVICE
    assert "sustituido_por_adjunto_id" in SERVICE
    assert "previous.sustituido_por_empleado_id = actor_id" in SERVICE
    assert "accion=\"reemplazar_comprobante_no_deducible\"" in SERVICE
    assert "accion=\"eliminar_comprobante_no_deducible\"" in SERVICE
    assert "session.delete" not in SERVICE


def test_schema_enforces_one_active_non_deductible_proof_per_expense() -> None:
    assert "adjuntos_activo_column" in SCHEMA_GUARD
    assert "adjuntos_eliminado_por_id_column" in SCHEMA_GUARD
    assert "ux_adjuntos_active_no_deducible_per_expense" in SCHEMA_GUARD
    assert "WHERE categoria = 'comprobante_no_deducible' AND activo = TRUE" in SCHEMA_GUARD


def test_edit_flow_previews_replaces_and_logically_removes_proof() -> None:
    start = ROUTES.index("async def editar_gasto_form")
    end = ROUTES.index('@router.post("/gastos/{gasto_id}/editar")', start)
    form = ROUTES[start:end]
    assert 'name="comprobante_no_deducible"' in form
    assert "Vista previa del comprobante no deducible" in form
    assert "comprobante-no-deducible/eliminar" in form
    assert "Factura compartida" in form

    delete_start = ROUTES.index("async def eliminar_comprobante_no_deducible")
    delete_end = ROUTES.index('@router.get("/documentos/mis-documentos"', delete_start)
    delete_handler = ROUTES[delete_start:delete_end]
    assert "logically_delete_non_deductible_proof" in delete_handler
    assert "await session.commit()" in delete_handler


def test_quick_capture_requires_proof_and_clears_cfdi_for_no_deductible() -> None:
    start = ROUTES.index("async def crear_gasto_rapido_en_informe")
    end = ROUTES.index('@router.post("/informes-de-gastos/{cuenta_id}/gastos/amex")', start)
    handler = ROUTES[start:end]
    assert "comprobante_no_deducible: Optional[UploadFile] = File(None)" in handler
    assert "Adjunte el comprobante no deducible" in handler
    assert "Un comprobante no deducible no puede cargarse junto con CFDI PDF o XML" in handler
    assert 'values["numero_factura"] = "no facturable"' in handler
    assert "replace_non_deductible_proof" in handler
    assert "manual_no_deducible" in handler
    assert "_form_checkbox_checked(es_no_deducible) or manual_no_deducible" in handler


def test_non_deductible_transition_clears_legacy_tocino_cfdi_link() -> None:
    start = ROUTES.index("async def editar_gasto")
    end = ROUTES.index("async def eliminar_comprobante_no_deducible", start)
    handler = ROUTES[start:end]
    assert "or expense.nova_request_id" in handler
    assert 'old_values["nova_request_id"] = expense.nova_request_id' in handler
    assert "expense.nova_request_id = None" in handler
    assert 'new_values["nova_request_id"] = None' in handler
    assert 'if "nova_request_id" in old_values:' in handler
    assert '"Enlaces CFDI/Tocino antes/después: "' in handler
    assert "_cfdi_link_transition_audit(old_values, new_values)" in handler


def test_tocino_unlink_audit_preserves_before_and_after_values() -> None:
    payload = json.loads(
        _cfdi_link_transition_audit(
            {
                "cfdi_uuid_manual": "UUID-1",
                "cfdi_report_id": uuid4(),
                "nova_request_id": "tocino-legacy",
            },
            {
                "cfdi_uuid_manual": None,
                "cfdi_report_id": None,
                "nova_request_id": None,
            },
        )
    )
    assert payload["nova_request_id"] == {
        "antes": "tocino-legacy",
        "despues": None,
    }
    assert payload["cfdi_uuid_manual"] == {"antes": "UUID-1", "despues": None}


def test_duplicate_cfdi_requires_explicit_shared_confirmation_and_audit() -> None:
    assert "class ExpenseCFDIDuplicateError" in CFDI_SERVICE
    assert "require_unique: bool = False" in CFDI_SERVICE
    assert "if not allow_shared:" in CFDI_SERVICE
    assert "pg_advisory_xact_lock" in CFDI_SERVICE
    assert "Indique el motivo de la factura compartida." in CFDI_SERVICE
    assert 'accion="confirmar_cfdi_compartido"' in CFDI_SERVICE
    assert "require_unique=True" in ROUTES


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _SessionStub:
    def __init__(self, duplicate_id=None):
        self.duplicate_id = duplicate_id
        self.added = []

    async def execute(self, _statement, _params=None):
        return _ScalarResult(self.duplicate_id)

    def add(self, value):
        self.added.append(value)


class _ProofSessionStub(_SessionStub):
    async def flush(self):
        for value in self.added:
            if isinstance(value, Adjunto) and value.id is None:
                value.id = uuid4()


def _expense_stub():
    return SimpleNamespace(
        id=uuid4(),
        cfdi_uuid_manual="0C568D02-1111-2222-3333-444444444444",
        cfdi_compartido_confirmado=False,
        cfdi_compartido_motivo=None,
    )


def test_manual_uuid_without_cfdi_report_is_blocked_when_already_active() -> None:
    session = _SessionStub(duplicate_id=uuid4())
    with pytest.raises(ExpenseCFDIDuplicateError):
        asyncio.run(
            _enforce_expense_cfdi_uniqueness(
                session,
                expense=_expense_stub(),
                report=None,
                allow_shared=False,
                shared_reason=None,
                actor_id=uuid4(),
            )
        )


def test_shared_manual_uuid_requires_reason_and_records_audit() -> None:
    session = _SessionStub(duplicate_id=uuid4())
    expense = _expense_stub()
    asyncio.run(
        _enforce_expense_cfdi_uniqueness(
            session,
            expense=expense,
            report=None,
            allow_shared=True,
            shared_reason="División de consumo de dos pasajeros",
            actor_id=uuid4(),
        )
    )
    assert expense.cfdi_compartido_confirmado is True
    assert expense.cfdi_compartido_motivo == "División de consumo de dos pasajeros"
    assert len(session.added) == 1
    assert session.added[0].accion == "confirmar_cfdi_compartido"


def test_replacement_and_logical_deletion_preserve_prior_proof() -> None:
    gasto_id, actor_id = uuid4(), uuid4()
    prior = Adjunto(
        id=uuid4(), gasto_id=gasto_id, ruta_archivo="old", categoria="comprobante_no_deducible", activo=True
    )
    session = _ProofSessionStub(duplicate_id=prior)
    replacement = asyncio.run(
        replace_non_deductible_proof(
            session,
            gasto_id=gasto_id,
            actor_id=actor_id,
            ruta_archivo="new",
            mime_type="application/pdf",
            nombre_archivo="nuevo.pdf",
        )
    )
    assert prior.activo is False
    assert prior.sustituido_por_adjunto_id == replacement.id
    assert replacement.activo is True

    session.duplicate_id = replacement
    asyncio.run(
        logically_delete_non_deductible_proof(
            session, gasto_id=gasto_id, actor_id=actor_id, motivo="Archivo equivocado"
        )
    )
    assert replacement.activo is False
    assert replacement.eliminado_por_id == actor_id
    assert replacement.motivo_eliminacion == "Archivo equivocado"
