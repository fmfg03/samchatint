from types import SimpleNamespace
from uuid import uuid4

from devnous.gastos.services.documento_workflow_service import documento_requires_budget_control


def test_solicitud_without_budget_concept_requires_budget_control():
    documento = SimpleNamespace(tipo="SOLICITUD", budget_concept_id=None)
    assert documento_requires_budget_control(documento) is True


def test_informe_without_budget_concept_requires_budget_control():
    documento = SimpleNamespace(tipo="INFORME", budget_concept_id=None)
    assert documento_requires_budget_control(documento) is True


def test_document_with_budget_concept_goes_to_regular_approval():
    documento = SimpleNamespace(tipo="SOLICITUD", budget_concept_id=uuid4())
    assert documento_requires_budget_control(documento) is False
