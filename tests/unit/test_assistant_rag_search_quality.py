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

def test_rag_search_owner_folder_terms_beat_generic_authorization_neighbor(
    tmp_path: Path,
) -> None:
    index_path = tmp_path / "assistant_rag_index.json"
    payload = {
        "created_at": "2026-07-30T00:00:00",
        "updated_at": "2026-07-30T00:00:00",
        "chunks": [
            {
                "chunk_id": "auth::#0",
                "source": "/repo/docs/sprints/rqf-057g-authorization-strategy-matrix.md",
                "text": "Authorization strategy for operations, finance, budgets, transfers, approvals, and tournament expense reports.",
                "embedding": [1.0, 0.0],
                "metadata": {},
            },
            {
                "chunk_id": "owner::#0",
                "source": "/repo/docs/assistant/owner-ai-needs.md",
                "text": "Crear carpeta por entidad para cada torneo. La carpeta contiene operaciones y finanzas, equipos esperados, equipos reales participantes, jugadores, pagos, uniformes, visitas y evidencia.",
                "embedding": [0.0, 1.0],
                "metadata": {},
            },
        ],
    }
    index_path.write_text(json.dumps(payload), encoding="utf-8")
    store = LocalRAGStore(index_path=str(index_path))

    def fake_embed_texts(_client, texts):
        assert texts
        # Query vector intentionally points at the authorization chunk.  Exact
        # owner-folder vocabulary must still rescue the owner requirements.
        return [[1.0, 0.0] for _ in texts]

    store._embed_texts = fake_embed_texts  # type: ignore[method-assign]

    results = store.search(
        query="carpeta por entidad torneo operaciones finanzas",
        top_k=2,
        min_score=0.15,
    )

    assert results
    assert results[0]["source"].endswith("owner-ai-needs.md")
    assert results[0]["lexical_score"] > results[1]["lexical_score"]

def test_owner_ai_conceptual_retrieval_can_be_canon_only():
    from samchat.assistant.request_intent import is_owner_ai_conceptual_request

    assert is_owner_ai_conceptual_request(
        "Que debe contener una carpeta por entidad para cualquier torneo?"
    )
