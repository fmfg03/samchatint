from __future__ import annotations

import json
from pathlib import Path

from samchat.assistant.rag import LocalRAGStore


def _store(tmp_path: Path, monkeypatch):
    base_dir = tmp_path / "repo"
    index_path = tmp_path / "index.json"
    base_dir.mkdir()
    monkeypatch.setenv("ASSISTANT_RAG_BASE_DIR", str(base_dir))
    store = LocalRAGStore(index_path=str(index_path))
    monkeypatch.setattr(
        store,
        "_embed_texts",
        lambda _client, texts: [[] for _ in texts],
    )
    return base_dir, index_path, store


def _indexed_text(index_path: Path) -> str:
    payload = json.loads(index_path.read_text(encoding="utf-8"))
    return "\n".join(str(chunk.get("text") or "") for chunk in payload["chunks"])


def test_rag_ingest_skips_secret_directories_and_secret_like_files(
    tmp_path, monkeypatch
):
    base_dir, index_path, store = _store(tmp_path, monkeypatch)

    (base_dir / "docs").mkdir()
    (base_dir / "docs" / "public.md").write_text(
        "safe public documentation", encoding="utf-8"
    )
    (base_dir / ".secrets").mkdir()
    (base_dir / ".secrets" / "token.json").write_text(
        '{"token": "SUPER_SECRET_FROM_DOT_SECRETS"}', encoding="utf-8"
    )
    (base_dir / "credentials").mkdir()
    (base_dir / "credentials" / "service.yaml").write_text(
        "password: SUPER_SECRET_FROM_CREDENTIALS", encoding="utf-8"
    )
    (base_dir / "test_env").mkdir()
    (base_dir / "test_env" / "debug.log").write_text(
        "api_key=SUPER_SECRET_FROM_TEST_ENV", encoding="utf-8"
    )
    (base_dir / "docs" / "api_token.json").write_text(
        '{"api_token": "SUPER_SECRET_FROM_FILENAME"}', encoding="utf-8"
    )

    result = store.ingest(paths=["."], reset=True)

    assert result["indexed_files"] == 1
    assert result["sources"] == [str(base_dir / "docs" / "public.md")]
    indexed_text = _indexed_text(index_path)
    assert "safe public documentation" in indexed_text
    assert "SUPER_SECRET" not in indexed_text


def test_rag_ingest_skips_symlinked_files(tmp_path, monkeypatch):
    base_dir, index_path, store = _store(tmp_path, monkeypatch)

    docs_dir = base_dir / "docs"
    docs_dir.mkdir()
    (docs_dir / "public.md").write_text("safe public docs", encoding="utf-8")
    outside_secret = tmp_path / "outside-secret.json"
    outside_secret.write_text(
        '{"token": "SUPER_SECRET_FROM_SYMLINK"}', encoding="utf-8"
    )
    (docs_dir / "linked-secret.json").symlink_to(outside_secret)

    result = store.ingest(paths=["docs"], reset=True)

    assert result["indexed_files"] == 1
    assert result["sources"] == [str(docs_dir / "public.md")]
    indexed_text = _indexed_text(index_path)
    assert "safe public docs" in indexed_text
    assert "SUPER_SECRET_FROM_SYMLINK" not in indexed_text
