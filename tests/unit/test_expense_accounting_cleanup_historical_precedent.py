import pytest
from types import SimpleNamespace

from devnous.gastos.services import expense_accounting_cleanup_service as service


class _FakeHistoricalReport:
    def to_dict(self):
        return {
            "status": "precedents_found",
            "headline": "Precedentes encontrados",
            "summary": "Historico consultado",
            "query": "Alimentos DCC",
            "candidates": [
                {
                    "account_code": "5300-006-007",
                    "account_name": "CAFETERIA",
                    "match_count": 4,
                    "policy_count": 2,
                    "confidence": "medium",
                }
            ],
            "safety_summary": {"read_only": True},
        }


class _NestedTransaction:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        self.session.nested_enters += 1

    async def __aexit__(self, exc_type, exc, tb):
        self.session.nested_exits += 1
        return False


class _NestedSession:
    def __init__(self):
        self.nested_enters = 0
        self.nested_exits = 0

    def begin_nested(self):
        return _NestedTransaction(self)


def _expense(**overrides):
    base = {
        "concepto": "Consumo de alimentos",
        "proyecto": "De la Calle a la Cancha",
        "fase_torneo": "Nacional",
        "metodo_pago": "TARJETA CREDITO AMEX",
        "numero_factura": "F-123",
        "cfdi_report": SimpleNamespace(
            emisor_nombre="RESTAURANTE EJEMPLO",
            emisor_rfc="REJ010101AA1",
        ),
        "empleado": SimpleNamespace(nombre="ALICIA EDITH ZUNIGA SALAZAR"),
        "documento": SimpleNamespace(numero_referencia="I-123456"),
        "edicion": 2026,
        "cuenta_contable_id": None,
        "contra_cuenta_contable_id": None,
        "cfdi_report_id": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_cleanup_preview_includes_historical_precedent_without_assigning(monkeypatch):
    captured = {}

    async def fake_accounting_preview(session, expense):
        return {"taxes": {"iva_trasladado": 0.0, "retenciones": []}}

    async def fake_precedents(session, **kwargs):
        captured.update(kwargs)
        return _FakeHistoricalReport()

    monkeypatch.setattr(service, "build_expense_accounting_preview", fake_accounting_preview)
    monkeypatch.setattr(service, "query_historical_accounting_precedents", fake_precedents)

    expense = _expense()
    state = await service.build_cleanup_preview(
        object(),
        expense,
        include_historical_precedent=True,
    )

    evidence = state["historical_precedent_evidence"]
    assert evidence["status"] == "precedents_found"
    assert evidence["candidates"][0]["account_code"] == "5300-006-007"
    assert evidence["safety_summary"]["account_assignment_performed"] is False
    assert expense.cuenta_contable_id is None
    assert captured["company_code"] == "01"
    assert captured["fiscal_year"] == 2026
    assert captured["limit"] == 3
    assert "Consumo de alimentos" in captured["query"]
    assert "RESTAURANTE EJEMPLO" in captured["query"]


@pytest.mark.asyncio
async def test_historical_precedent_evidence_fails_closed(monkeypatch):
    async def fake_precedents(session, **kwargs):
        raise RuntimeError("historico caido")

    monkeypatch.setattr(service, "query_historical_accounting_precedents", fake_precedents)

    evidence = await service.build_historical_precedent_evidence(object(), _expense())

    assert evidence["status"] == "precedent_lookup_failed"
    assert evidence["candidates"] == []
    assert evidence["safety_summary"]["read_only"] is True
    assert evidence["safety_summary"]["account_assignment_performed"] is False


@pytest.mark.asyncio
async def test_historical_precedent_evidence_requires_visible_query(monkeypatch):
    async def fail_if_called(session, **kwargs):  # pragma: no cover - assertion guard
        raise AssertionError("historical lookup should not be called")

    monkeypatch.setattr(service, "query_historical_accounting_precedents", fail_if_called)

    evidence = await service.build_historical_precedent_evidence(
        object(),
        _expense(
            concepto="",
            proyecto="",
            fase_torneo="",
            metodo_pago="",
            numero_factura="",
            cfdi_report=None,
            empleado=None,
            documento=None,
        ),
    )

    assert evidence["status"] == "invalid_query"
    assert evidence["candidates"] == []


@pytest.mark.asyncio
async def test_historical_precedent_failure_isolated_in_nested_transaction(monkeypatch):
    async def fake_precedents(session, **kwargs):
        raise RuntimeError("optional precedent lookup failed after db error")

    monkeypatch.setattr(service, "query_historical_accounting_precedents", fake_precedents)

    session = _NestedSession()
    evidence = await service.build_historical_precedent_evidence(session, _expense())

    assert evidence["status"] == "precedent_lookup_failed"
    assert session.nested_enters == 1
    assert session.nested_exits == 1


@pytest.mark.asyncio
async def test_safe_cleanup_preview_degrades_failed_preview_in_nested_transaction(monkeypatch):
    async def fake_accounting_preview(session, expense):
        raise RuntimeError("preview query failed")

    monkeypatch.setattr(service, "build_expense_accounting_preview", fake_accounting_preview)

    session = _NestedSession()
    state = await service.safe_build_cleanup_preview(
        session,
        _expense(gasto_cantidad=123.45),
        include_historical_precedent=True,
    )

    assert state["status"] == "Pendiente"
    assert "Preview contable no disponible" in state["issues"]
    assert state["preview"]["taxes"]["base_gasto"] == 123.45
    assert state["preview"]["taxes"]["neto_contrapartida"] == 123.45
    assert state["historical_precedent_evidence"]["status"] == "preview_degraded"
    assert session.nested_enters == 1
    assert session.nested_exits == 1
