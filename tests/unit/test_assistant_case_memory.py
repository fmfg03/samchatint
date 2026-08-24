from types import SimpleNamespace
from uuid import uuid4

from samchat.assistant.case_memory import (
    CASE_MEMORY_ARTIFACT_TYPE,
    build_case_memory_summary,
    detect_case_memory_command,
    render_case_memory_resume_markdown,
    score_case_memory_artifacts,
)


def _message(role: str, content: str):
    return SimpleNamespace(id=uuid4(), role=role, content=content)


def _run(status: str, *, tool_trace=None, pending_tool_name=None):
    return SimpleNamespace(
        id=uuid4(),
        status=status,
        tool_trace=tool_trace or [],
        pending_tool_name=pending_tool_name,
    )


def test_case_memory_artifact_type_is_stable() -> None:
    assert CASE_MEMORY_ARTIFACT_TYPE == "case_memory_summary"


def test_build_case_memory_summary_extracts_business_state() -> None:
    conversation_id = uuid4()
    conversation = SimpleNamespace(
        id=conversation_id,
        title="Gastos comprobantes julio",
        tournament_key="beisbol",
        metadata_={"module_key": "expenses", "module_label": "Informes"},
    )
    messages = [
        _message(
            "user",
            "Aqu? est?n factura.xml y ticket.pdf. Prepara mi cuenta de gastos y deja lista la solicitud.",
        ),
        _message(
            "assistant",
            "Encontr? error: el total no coincide. Falta materialidad. No ejecut? cambios; canary read-only.",
        ),
        _message(
            "user", "Correcto, queda como regla revisar no deducibles por proyecto."
        ),
        _message("user", "Apruebo la vista previa."),
    ]
    runs = [
        _run(
            "completed",
            tool_trace=[
                {"assistant_plan": {"steps": ["leer", "validar", "preparar preview"]}},
                {
                    "retrieval": {
                        "sources": [
                            {
                                "label": "doc:/docs/assistant/product-canon.md",
                                "score": 0.9,
                            }
                        ]
                    }
                },
            ],
        ),
        _run("provider_timeout"),
        _run("pending_confirmation", pending_tool_name="expenses.create_request"),
    ]

    summary = build_case_memory_summary(
        conversation=conversation,
        messages=messages,
        runs=runs,
    )

    assert summary.conversation_id == str(conversation_id)
    assert summary.objective.startswith("Aqu? est?n factura.xml")
    assert "beisbol" in summary.scope.values()
    assert any("factura.xml" in item for item in summary.documents)
    assert any("ticket.pdf" in item for item in summary.documents)
    assert any("total no coincide" in item for item in summary.findings)
    assert any("no deducibles" in item for item in summary.decisions)
    assert any("Falta materialidad" in item for item in summary.open_questions)
    assert any("Pending confirmation" in item for item in summary.previews)
    assert any("Apruebo" in item for item in summary.approvals)
    assert any("Provider timeout" in item for item in summary.limitations)
    assert summary.case_status == "waiting_context"
    assert any("product-canon" in item for item in summary.artifacts_consulted)
    assert summary.last_action.startswith("user: Apruebo")
    assert summary.next_step and "Resolver pendiente" in summary.next_step
    assert any("read-only" in item for item in summary.non_claims)
    assert summary.message_count == 4
    assert summary.run_count == 3


def test_case_memory_summary_markdown_is_retrievable_context() -> None:
    conversation = SimpleNamespace(
        id=uuid4(), title=None, tournament_key=None, metadata_={}
    )
    summary = build_case_memory_summary(
        conversation=conversation,
        messages=[_message("user", "Crear torneo 2027 tomando como base el anterior")],
        runs=[],
    )

    markdown = summary.to_markdown()

    assert "# Assistant Case Memory Summary" in markdown
    assert "## Objective" in markdown
    assert "Crear torneo 2027" in markdown
    assert "## Case status" in markdown
    assert "## Last action" in markdown
    assert "## Next step" in markdown
    assert "## Resume hint" in markdown
    assert "## Source counts" in markdown


def test_score_case_memory_artifacts_prefers_compact_summary() -> None:
    artifact = SimpleNamespace(
        id=uuid4(),
        conversation_id=uuid4(),
        content=(
            "# Assistant Case Memory Summary\n\n"
            "Objective: preparar cuenta de gastos con CFDI y materialidades.\n"
            "Decisions: revisar no deducibles por proyecto."
        ),
        metadata_={
            "case_memory": {
                "scope": {"module_key": "expenses", "tournament_key": "beisbol"}
            }
        },
    )

    results = score_case_memory_artifacts(
        artifacts=[artifact],
        tokens=["gastos", "cfdi", "deducibles"],
        module_key="expenses",
        tournament_key="beisbol",
        memory_weight=1.0,
    )

    assert len(results) == 1
    assert results[0]["label"].startswith("memory:case_summary:")
    assert results[0]["module_key"] == "expenses"
    assert results[0]["tournament_key"] == "beisbol"
    assert results[0]["score"] > 1.0


def test_case_memory_command_detection_is_explicit() -> None:
    assert detect_case_memory_command("Guarda este caso para retomarlo") == "save"
    assert detect_case_memory_command("retoma este caso") == "resume"
    assert detect_case_memory_command("sigamos") is None


def test_case_memory_resume_without_summary_fails_closed() -> None:
    rendered = render_case_memory_resume_markdown(
        {"status": "no_case_memory", "matched": False}
    )

    assert "No encontre memoria de caso" in rendered
    assert "no inventar continuidad" in rendered
    assert "no ejecute acciones" in rendered
