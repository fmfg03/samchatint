from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _read_doc(relative_path: str) -> str:
    path = ROOT / relative_path
    assert path.exists(), f"missing assistant context document: {relative_path}"
    return path.read_text(encoding="utf-8")


def test_product_canon_defines_samchat_as_operational_assistant() -> None:
    text = _read_doc("docs/assistant/product-canon.md")

    assert "SamChat is an operational assistant" in text
    assert "not a dashboard with chat" in text
    assert "executes only with explicit authority" in text
    assert "assistant is the primary work interface" in text


def test_product_canon_preserves_authority_boundary() -> None:
    text = _read_doc("docs/assistant/product-canon.md")

    required_phrases = [
        "authority remains with people",
        "separate read-only investigation from write-capable execution",
        "Every write-capable action requires explicit preview and approval",
        "Memory must not silently override live data",
    ]
    for phrase in required_phrases:
        assert phrase in text


def test_context_corpus_defines_context_layers_and_gates() -> None:
    text = _read_doc("docs/assistant/context-corpus.md")

    for layer in ("Stable canon", "Live business context", "Case memory"):
        assert layer in text

    for source_type in ("canon", "sql", "memory", "tool", "none"):
        assert f"`{source_type}`" in text

    assert "RAG index exists" in text
    assert "Live facts are fetched from SQL/tools" in text


def test_quality_roadmap_keeps_canary_readonly() -> None:
    text = _read_doc("docs/assistant/rqf-assistant-009-quality-roadmap.md")
