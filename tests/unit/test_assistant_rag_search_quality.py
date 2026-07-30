import json
from pathlib import Path

from samchat.assistant.rag import LocalRAGStore


def _write_index(path: Path) -> None:
    payload = {
        "created_at": "2026-07-30T00:00:00",
        "updated_at": "2026-07-30T00:00:00",
        "chunks": [
            {
                "chunk_id": "canon::#0",
                "source": "/repo/docs/assistant/product-canon.md",
                "text": "SamChat is not a dashboard with chat. The assistant is the primary work interface and executes only with explicit authority.",
                "embedding": [0.0, 1.0],
                "metadata": {},
            },
            {
                "chunk_id": "dashboard::#0",
                "source": "/repo/docs/sprints/warnings-dashboard.md",
                "text": "Authorization warnings dashboard for finance review and operational visibility.",
                "embedding": [1.0, 0.0],
                "metadata": {},
            },
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_rag_search_keeps_exact_policy_phrase_above_vector_neighbor(
    tmp_path: Path,
) -> None:
    index_path = tmp_path / "assistant_rag_index.json"
    _write_index(index_path)
    store = LocalRAGStore(index_path=str(index_path))

    def fake_embed_texts(_client, texts):
        assert texts
        # Query vector intentionally points at the dashboard chunk.  The lexical
        # policy phrase must still rescue the product canon chunk.
        return [[1.0, 0.0] for _ in texts]

    store._embed_texts = fake_embed_texts  # type: ignore[method-assign]

    results = store.search(
        query="not a dashboard with chat primary work interface explicit authority",
        top_k=2,
        min_score=0.15,
    )

    assert results
    assert results[0]["source"].endswith("product-canon.md")
    assert results[0]["lexical_score"] > 0
    assert results[0]["vector_score"] is not None


def test_rag_search_exposes_component_scores(tmp_path: Path) -> None:
    index_path = tmp_path / "assistant_rag_index.json"
    _write_index(index_path)
    store = LocalRAGStore(index_path=str(index_path))
    store._embed_texts = lambda _client, texts: [[0.0, 1.0] for _ in texts]  # type: ignore[method-assign]
