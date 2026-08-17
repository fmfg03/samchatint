import pytest

import samchat.assistant.router as assistant_router
from samchat.assistant.closeout_diagnostics import (
    CLOSEOUT_BLOCKED,
    CLOSEOUT_DIAGNOSTICS_ONLY,
    CLOSEOUT_READY,
    build_closeout_diagnostics_from_platform,
)


def _platform(**overrides):
    base = {
        "period": {"year": 2026, "month": 8},
        "accounting_close_center": {
            "polizas_count": 3,
            "unbalanced_count": 0,
            "unbalanced_polizas": [],
            "pending_coi_expenses_count": 0,
            "pending_coi_expenses": [],
        },
        "tax_readiness": {
            "diot_blockers_count": 0,
            "blockers": [],
        },
    }
    base.update(overrides)
    return base


def test_closeout_diagnostics_blocks_on_unbalanced_polizas() -> None:
    report = build_closeout_diagnostics_from_platform(
        _platform(
            accounting_close_center={
                "polizas_count": 2,
                "unbalanced_count": 2,
                "unbalanced_polizas": [
                    {
                        "id": "p1",
                        "tipo_poliza": "Eg",
                        "numero_poliza": "26000123",
                        "debe": 1000.0,
                        "haber": 651.8,
                        "beneficiario_nombre": "Proveedor Uno",
                        "origen": "COI",
                    },
                    {
                        "id": "p2",
                        "tipo_poliza": "Ig",
                        "numero_poliza": "26000131",
                        "debe": 0.0,
                        "haber": 1250.0,
                    },
                ],
                "pending_coi_expenses_count": 0,
                "pending_coi_expenses": [],
            }
        )
    )

    assert report.status == CLOSEOUT_BLOCKED
    assert "bloqueado" in report.headline.lower()
    assert "2 póliza" in report.summary
    assert report.high_priority_count == 2
    assert report.blocker_count == 2
    assert report.blockers[0].blocker_type == "unbalanced_poliza"
    assert report.blockers[0].reference == "Eg-26000123"
    assert report.blockers[0].amount == 348.2
    assert report.blockers[0].evidence["difference"] == 348.2
    assert report.execution_status == "not_executed"
    assert report.writes_attempted == 0
    assert report.side_effects_detected == 0
    assert report.audit_language == CLOSEOUT_DIAGNOSTICS_ONLY


def test_closeout_diagnostics_can_report_ready_when_no_blockers() -> None:
    report = build_closeout_diagnostics_from_platform(_platform())

    assert report.status == CLOSEOUT_READY
    assert report.blocker_count == 0
    assert "no tiene bloqueos" in report.headline.lower()
    assert report.safety_summary["writes_enabled"] is False
    assert report.safety_summary["approval_required_for_close"] is True


def test_closeout_diagnostics_lists_coi_and_tax_blockers() -> None:
    report = build_closeout_diagnostics_from_platform(
        _platform(
            accounting_close_center={
                "polizas_count": 1,
                "unbalanced_count": 0,
                "unbalanced_polizas": [],
                "pending_coi_expenses_count": 1,
                "pending_coi_expenses": [
                    {
                        "id": "g1",
                        "numero_referencia": "O-26000024",
                        "concepto": "Hospedaje",
                        "gasto_cantidad": 5800,
                        "cuenta_contable_id": "",
                        "contra_cuenta_contable_id": "cc2",
                        "cfdi_uuid_manual": "",
                        "cfdi_report_id": "",
                    }
                ],
            },
            tax_readiness={
                "diot_blockers_count": 1,
                "blockers": [
                    {
                        "entity_type": "documento",
                        "id": "d1",
                        "numero_referencia": "S-26000106",
                        "estado": "aprobado",
                        "monto_total": 25000,
                    }
                ],
            },
        )
    )

    assert report.status == CLOSEOUT_BLOCKED
    assert report.high_priority_count == 0
    assert {item.blocker_type for item in report.blockers} == {
        "pending_coi_expense",
        "missing_cfdi",
    }
    coi = [item for item in report.blockers if item.blocker_type == "pending_coi_expense"][0]
    assert "cuenta contable" in coi.detail
    assert "CFDI" in coi.detail
    assert report.source_summary["pending_coi_expenses_count"] == 1
    assert report.source_summary["diot_blockers_count"] == 1


def test_closeout_diagnostics_can_focus_only_high_priority() -> None:
    report = build_closeout_diagnostics_from_platform(
        _platform(
            accounting_close_center={
                "polizas_count": 1,
                "unbalanced_count": 0,
                "unbalanced_polizas": [],
                "pending_coi_expenses_count": 1,
                "pending_coi_expenses": [{"id": "g1", "gasto_cantidad": 1}],
            }
        ),
        include_medium=False,
    )

    assert report.status == CLOSEOUT_READY
    assert report.blocker_count == 0


@pytest.mark.asyncio
async def test_closeout_diagnostics_router_tool_executes_builder(monkeypatch) -> None:
    calls = {}

    class FakeReport:
        def to_dict(self):
            return {"status": "blocked", "writes_attempted": 0}

    async def fake_builder(session, **kwargs):
        calls["session"] = session
        calls["kwargs"] = kwargs
        return FakeReport()

    monkeypatch.setattr(
        assistant_router,
        "build_finance_closeout_diagnostics",
        fake_builder,
    )

    result = await assistant_router._run_read_tool(
        "finance_closeout_diagnostics",
        {
            "year": 2026,
            "month": 8,
            "scope": "accounting",
            "include_medium": "false",
        },
        gastos_session=object(),
        tournament_key_default=None,
        current_role="admin",
    )

    assert result == {"status": "blocked", "writes_attempted": 0}
    assert calls["kwargs"] == {
        "year": 2026,
        "month": 8,
        "scope": "accounting",
        "include_medium": False,
    }
