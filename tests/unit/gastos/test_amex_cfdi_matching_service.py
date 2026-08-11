from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

from devnous.gastos.services.amex_cfdi_matching_service import (
    rank_amex_cfdi_candidates,
    score_amex_cfdi_candidate,
)


def _expense(**kwargs):
    base = dict(
        id=uuid4(),
        origen="amex_batch",
        fecha=datetime(2026, 8, 5),
        concepto="HOTEL MONTERREY",
        gasto_cantidad=1000.0,
        cfdi_report_id=None,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def _cfdi(**kwargs):
    base = dict(
        id=uuid4(),
        cfdi_uuid=str(uuid4()).upper(),
        fecha=datetime(2026, 8, 5),
        total=1000.0,
        tipo_de_comprobante="I",
        emisor_nombre="HOTEL MONTERREY SA DE CV",
        emisor_rfc="HMO010101AA1",
        descripcion_concepto_principal="HOSPEDAJE",
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_score_amex_cfdi_candidate_accepts_exact_amount_same_day():
    suggestion = score_amex_cfdi_candidate(_expense(), _cfdi())

    assert suggestion is not None
    assert suggestion.confidence == "Alta"
    assert suggestion.amount_delta == 0
    assert "monto exacto" in suggestion.reason


def test_score_amex_cfdi_candidate_accepts_restaurant_tip_delta():
    suggestion = score_amex_cfdi_candidate(
        _expense(concepto="Consumo restaurante", gasto_cantidad=116.0),
        _cfdi(total=100.0, emisor_nombre="RESTAURANTE LA BUENA MESA"),
    )

    assert suggestion is not None
    assert suggestion.amount_delta == 16.0
    assert "propina" in suggestion.reason


def test_score_amex_cfdi_candidate_rejects_unrelated_amount():
    suggestion = score_amex_cfdi_candidate(
        _expense(concepto="Papelería", gasto_cantidad=500.0),
        _cfdi(total=100.0, emisor_nombre="PAPELERIA CENTRO"),
    )

    assert suggestion is None


def test_rank_amex_cfdi_candidates_prefers_best_score():
    expense = _expense(concepto="HOTEL MONTERREY", gasto_cantidad=1000.0)
    weaker = _cfdi(fecha=datetime(2026, 8, 9), emisor_nombre="SERVICIOS VARIOS")
    stronger = _cfdi(fecha=datetime(2026, 8, 5), emisor_nombre="HOTEL MONTERREY")

    suggestions = rank_amex_cfdi_candidates(expense, [weaker, stronger])

    assert suggestions[0].cfdi_report_id == str(stronger.id)
