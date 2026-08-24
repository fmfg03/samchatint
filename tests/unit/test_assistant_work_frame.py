from samchat.assistant.work_frame import build_work_frame


def test_payment_evidence_is_not_pending_payment_queue():
    frame = build_work_frame("Que evidencia tenemos de pagos hechos en agosto?")

    assert frame.domain in {"owner", "mixed", "finance"}
    assert frame.task_kind == "evidence"
    assert frame.temporal_scope["month"] == "08"
    assert "payment_receipts" in frame.required_evidence
    assert "pending_payment_queue" in frame.forbidden_interpretations
    assert "zero_pending_payments_as_evidence" in frame.forbidden_interpretations


def test_pending_payments_are_status_not_historical_evidence():
    frame = build_work_frame("Que pagos estan pendientes esta semana?")

    assert frame.domain == "finance"
    assert frame.task_kind == "status"
    assert "pending_payment_queue" in frame.required_evidence
    assert "historical_payment_evidence" in frame.forbidden_interpretations


def test_owner_readiness_question_is_readiness_not_variable_guess():
    frame = build_work_frame("ya tenemos datos para el dueno?")

    assert frame.audience == "owner"
    assert frame.domain == "owner"
    assert frame.task_kind == "readiness"
    assert "owner_pack_inventory" in frame.required_evidence
    assert "single_variable_answer" in frame.forbidden_interpretations


def test_owner_specific_variable_requires_supported_evidence():
    frame = build_work_frame("Cuantos equipos reales tiene Copa Telmex para el dueno?")

    assert frame.audience == "owner"
    assert frame.domain == "owner"
    assert frame.task_kind == "evidence"
    assert "Copa Telmex" in frame.explicit_entities
    assert "owner_variable_source" in frame.required_evidence
    assert "invented_amount" in frame.forbidden_interpretations


def test_finance_accounting_closeout_question_is_diagnostic():
    frame = build_work_frame("Por que no puedo cerrar contabilidad?")

    assert frame.audience == "finance"
    assert frame.domain == "finance"
    assert frame.task_kind == "diagnostic"
    assert "closeout_diagnostics" in frame.required_evidence
    assert "owner_pack_readiness" in frame.forbidden_interpretations


def test_broad_accounting_loaded_question_is_status():
    frame = build_work_frame("tenemos contabilidad cargada?")

    assert frame.audience == "finance"
    assert frame.domain == "finance"
    assert frame.task_kind == "status"
    assert "finance_platform_snapshot" in frame.required_evidence


def test_soul_coverage_frame_tracks_phase_dates_and_activities():
    frame = build_work_frame("Que torneos tienen SOUL completo con fases, fechas y actividades por fase?")

    assert frame.domain in {"owner", "operations"}
    assert frame.task_kind in {"data_coverage", "evidence"}
    assert "soul_snapshot" in frame.required_evidence or "owner_variable_source" in frame.required_evidence
    assert not frame.needs_clarification


def test_unknown_request_fails_into_clarification_frame():
    frame = build_work_frame("hazme magia con esto")

    assert frame.domain == "unknown"
    assert frame.task_kind == "unknown"
    assert frame.needs_clarification is True
    assert frame.clarification_reason == "no_stable_business_work_frame"
    assert "guessing" in frame.forbidden_interpretations


def test_answer_contract_is_executive_and_safe_for_owner():
    frame = build_work_frame("Que falta para la carpeta de Jalisco del Director General?")

    assert frame.answer_contract["style"] == "executive"
    assert "direct_answer" in frame.answer_contract["must_include"]
    assert "raw_tool_payload" in frame.answer_contract["must_not_include"]
    assert "Jalisco" in frame.explicit_entities
