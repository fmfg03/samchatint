from samchat.assistant.finance_accounting_qa import (
    detect_finance_accounting_qa_intent,
    render_finance_accounting_qa_answer,
)


def test_detects_target_finance_accounting_questions() -> None:
    cases = {
        "¿Por qué no puedo cerrar contabilidad?": "accounting_close",
        "¿Qué pólizas no cuadran?": "unbalanced_policies",
        "¿Qué CFDIs faltan vincular?": "missing_cfdi",
        "¿Qué está en payment run?": "payment_run",
        "¿Qué gastos AMEX están sin CFDI?": "amex_missing_cfdi",
        "¿Qué falta para COI?": "coi_missing",
        "¿Qué bloquea DIOT?": "diot_blockers",
        "tenemos contabilidad cargada?": "accounting_loaded",
    }

    for question, expected in cases.items():
        intent = detect_finance_accounting_qa_intent(question)

        assert intent is not None, question
        assert intent.question_type == expected


def _snapshot_result() -> dict:
    return {
        "ok": True,
        "read_only": True,
        "intent": "finance.platform",
        "source_function": "test.finance.snapshot",
        "payload": {
            "period": {"year": 2026, "month": 8},
            "summary": {"documents": 11, "expenses": 7, "polizas": 4},
            "accounting_close_center": {
                "unbalanced_count": 1,
                "unbalanced_polizas": [
                    {
                        "tipo_poliza": "Diario",
                        "numero_poliza": "P-001",
                        "debe": 100,
                        "haber": 90,
                    }
                ],
                "coi_ready_expenses_count": 3,
                "pending_coi_expenses_count": 2,
                "pending_coi_expenses": [
                    {
                        "numero_referencia": "O-1",
                        "empleado_nombre": "Alicia",
                        "gasto_cantidad": 250,
                    }
                ],
            },
            "tax_readiness": {
                "status": "blocked",
                "cfdi_missing_count": 2,
                "diot_blockers_count": 1,
                "amex_rows_count": 3,
                "blockers": [
                    {
                        "numero_referencia": "O-2",
                        "empleado_nombre": "Odilon",
                        "metodo_pago": "TARJETA CREDITO AMEX",
                        "gasto_cantidad": 500,
                    }
                ],
            },
            "payment_run": {
                "payable_count": 2,
                "payable_total": 750,
                "next_step": "Cerrar corte operativo",
                "items": [
                    {
                        "numero_referencia": "S-1",
                        "beneficiario_nombre": "Proveedor Demo",
                        "monto_total": 750,
                    }
                ],
            },
        },
        "source_notes": ["snapshot de prueba"],
        "safety_labels": ["finance_platform_read_only"],
    }


def test_render_payment_run_answer_is_executive_evidenced_and_read_only() -> None:
    intent = detect_finance_accounting_qa_intent("¿Qué está en payment run?")
    assert intent is not None

    answer = render_finance_accounting_qa_answer(result=_snapshot_result(), intent=intent)

    assert "Payment Run" in answer
    assert "2 solicitudes" in answer
    assert "Proveedor Demo" in answer
    assert "/admin/finanzas/payment-run" in answer
    assert "Fuentes y rutas" in answer
    assert "No ejecuté cambios" in answer


def test_render_accounting_close_names_blockers_and_routes() -> None:
    intent = detect_finance_accounting_qa_intent("¿Por qué no puedo cerrar contabilidad?")
    assert intent is not None

    answer = render_finance_accounting_qa_answer(result=_snapshot_result(), intent=intent)

    assert "no se debería cerrar" in answer
    assert "pólizas descuadradas" in answer
    assert "gastos pendientes para COI" in answer
    assert "bloqueos DIOT/CFDI" in answer
    assert "/admin/contabilidad/cierres" in answer
    assert "No ejecuté cambios" in answer
