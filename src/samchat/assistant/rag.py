from __future__ import annotations

import json
import math
import os
import re
from urllib import request as urllib_request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Optional


ALLOWED_EXTENSIONS = {
    ".txt",
    ".md",
    ".markdown",
    ".rst",
    ".json",
    ".csv",
    ".log",
    ".yaml",
    ".yml",
    ".py",
    ".sql",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".html",
}


SENSITIVE_PATH_PARTS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".secrets",
    "secrets",
    "secret",
    "credentials",
    "credential",
    "test_env",
    "private",
}

SENSITIVE_FILENAME_MARKERS = {
    "api_key",
    "apikey",
    "auth_token",
    "credential",
    "credentials",
    "passwd",
    "password",
    "private_key",
    "secret",
    "service_account",
    "token",
}

SENSITIVE_PREFIXES = (".env",)

SENSITIVE_SUFFIXES = {
    ".crt",
    ".csr",
    ".der",
    ".key",
    ".p12",
    ".pem",
    ".pfx",
}


@dataclass
class RAGChunk:
    chunk_id: str
    source: str
    text: str
    embedding: List[float]
    metadata: Dict[str, Any]


class LocalRAGStore:
    def __init__(self, *, index_path: Optional[str] = None) -> None:
        base_dir = Path(os.getenv("ASSISTANT_RAG_BASE_DIR", "/root/samchat"))
        default_path = base_dir / "data" / "assistant_rag_index.json"
        self.index_path = Path(
            index_path or os.getenv("ASSISTANT_RAG_INDEX_PATH", str(default_path))
        )
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._index: Dict[str, Any] = {
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat(),
            "chunks": [],
        }
        self._load()

    def _load(self) -> None:
        if not self.index_path.exists():
            return
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("chunks"), list):
                self._index = data
        except Exception:
            # Keep previous index if file is corrupt.
            pass

    def _save(self) -> None:
        self._index["updated_at"] = datetime.utcnow().isoformat()
        self.index_path.write_text(
            json.dumps(self._index, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )

    def _chunk_text(
        self, text: str, *, chunk_size: int = 1200, overlap: int = 180
    ) -> List[str]:
        t = (text or "").strip()
        if not t:
            return []
        if len(t) <= chunk_size:
            return [t]
        chunks: List[str] = []
        start = 0
        while start < len(t):
            end = min(len(t), start + chunk_size)
            chunk = t[start:end].strip()
            if chunk:
                chunks.append(chunk)
            if end >= len(t):
                break
            start = max(start + 1, end - overlap)
        return chunks

    def _is_safe_ingest_file(self, path: Path, *, base_dir: Path) -> bool:
        if path.is_symlink():
            return False
        try:
            base_resolved = base_dir.resolve()
            resolved = path.resolve()
            relative = resolved.relative_to(base_resolved)
        except (OSError, ValueError):
            return False
        if path.suffix.lower() not in ALLOWED_EXTENSIONS:
            return False
        relative_parts = [part.lower() for part in relative.parts]
        if any(part in SENSITIVE_PATH_PARTS for part in relative_parts[:-1]):
            return False
        name = path.name.lower()
        stem = path.stem.lower()
        if name.startswith(SENSITIVE_PREFIXES):
            return False
        if path.suffix.lower() in SENSITIVE_SUFFIXES:
            return False
        normalized_stem = re.sub(r"[^a-z0-9]+", "_", stem).strip("_")
        normalized_for_match = f"_{normalized_stem}_"
        return not any(
            f"_{marker}_" in normalized_for_match
            for marker in SENSITIVE_FILENAME_MARKERS
        )

    def _iter_files(
        self,
        paths: List[str],
        *,
        max_files: int,
    ) -> List[Path]:
        base_dir = Path(os.getenv("ASSISTANT_RAG_BASE_DIR", "/root/samchat"))
        collected: List[Path] = []
        for item in paths:
            p = Path(item)
            if not p.is_absolute():
                p = base_dir / p
            if not p.exists():
                continue
            if p.is_file():
                if self._is_safe_ingest_file(p, base_dir=base_dir):
                    collected.append(p)
            else:
                for f in p.rglob("*"):
                    if f.is_file() and self._is_safe_ingest_file(f, base_dir=base_dir):
                        collected.append(f)
                        if len(collected) >= max_files:
                            return collected
            if len(collected) >= max_files:
                return collected
        return collected

    def _embedding_backend(self) -> Dict[str, str]:
        provider = (os.getenv("ASSISTANT_RAG_EMBED_PROVIDER") or "auto").strip().lower()
        model = (
            os.getenv("ASSISTANT_RAG_EMBED_MODEL")
            or os.getenv("OLLAMA_EMBED_MODEL")
            or "nomic-embed-text"
        ).strip()
        base_url = (
            (os.getenv("OLLAMA_BASE_URL") or "http://127.0.0.1:11434")
            .strip()
            .rstrip("/")
        )
        return {
            "provider": provider or "auto",
            "model": model or "nomic-embed-text",
            "base_url": base_url or "http://127.0.0.1:11434",
        }

    def _embed_texts_openai(self, client: Any, texts: List[str]) -> List[List[float]]:
        model = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
        embeddings: List[List[float]] = []
        batch_size = int(os.getenv("ASSISTANT_RAG_EMBED_BATCH_SIZE", "64"))
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            resp = client.embeddings.create(model=model, input=batch)
            for row in resp.data:
                embeddings.append([float(x) for x in row.embedding])
        return embeddings

    def _embed_texts_ollama(self, texts: List[str]) -> List[List[float]]:
        cfg = self._embedding_backend()
        payload = json.dumps({"model": cfg["model"], "input": texts}).encode("utf-8")
        req = urllib_request.Request(
            f"{cfg['base_url']}/api/embed",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        timeout_sec = max(5, int(os.getenv("ASSISTANT_RAG_EMBED_TIMEOUT_SEC", "60")))
        with urllib_request.urlopen(req, timeout=timeout_sec) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        data = json.loads(raw)
        embeddings = data.get("embeddings")
        if not isinstance(embeddings, list):
            raise RuntimeError("Ollama /api/embed returned no embeddings")
        out: List[List[float]] = []
        for row in embeddings:
            if not isinstance(row, list):
                out.append([])
                continue
            out.append([float(x) for x in row])
        return out

    def _embed_texts(self, client: Any, texts: List[str]) -> List[List[float]]:
        cfg = self._embedding_backend()
        provider = cfg["provider"]
        errors: List[str] = []

        if provider in {"auto", "ollama", "local"}:
            try:
                return self._embed_texts_ollama(texts)
            except Exception as exc:
                errors.append(f"ollama:{exc}")
                if provider in {"ollama", "local"}:
                    raise

        if provider in {"auto", "openai"}:
            if client is None:
                errors.append("openai:no_client")
            else:
                try:
                    return self._embed_texts_openai(client, texts)
                except Exception as exc:
                    errors.append(f"openai:{exc}")
                    if provider == "openai":
                        raise

        if errors:
            raise RuntimeError("; ".join(errors))
        raise RuntimeError(f"Unsupported embedding provider: {provider}")

    def ingest(
        self,
        *,
        client: Any = None,
        paths: List[str],
        reset: bool = False,
        max_files: int = 200,
        max_chars_per_file: int = 300_000,
    ) -> Dict[str, Any]:
        with self._lock:
            files = self._iter_files(paths, max_files=max_files)
            if reset:
                self._index = {
                    "created_at": datetime.utcnow().isoformat(),
                    "updated_at": datetime.utcnow().isoformat(),
                    "chunks": [],
                }

            new_chunks_payload: List[Dict[str, Any]] = []
            chunk_texts: List[str] = []
            chunk_meta: List[Dict[str, Any]] = []
            skipped: List[str] = []
            for f in files:
                try:
                    text = f.read_text(encoding="utf-8", errors="ignore")
                    text = text[:max_chars_per_file]
                    chunks = self._chunk_text(text)
                    for idx, c in enumerate(chunks):
                        source = str(f)
                        chunk_id = f"{source}::#{idx}"
                        chunk_texts.append(c)
                        chunk_meta.append(
                            {
                                "chunk_id": chunk_id,
                                "source": source,
                                "metadata": {
                                    "filename": f.name,
                                    "extension": f.suffix.lower(),
                                    "chunk_index": idx,
                                },
                            }
                        )
                except Exception:
                    skipped.append(str(f))

            embedding_error = None
            vectors: List[List[float]] = []
            if chunk_texts:
                try:
                    vectors = self._embed_texts(client, chunk_texts)
                except Exception as exc:
                    embedding_error = str(exc)
                    vectors = [[] for _ in chunk_texts]
                for meta, text, emb in zip(chunk_meta, chunk_texts, vectors):
                    new_chunks_payload.append(
                        {
                            "chunk_id": meta["chunk_id"],
                            "source": meta["source"],
                            "text": text,
                            "embedding": emb,
                            "metadata": meta["metadata"],
                        }
                    )

            existing = self._index.get("chunks", [])
            by_id: Dict[str, Dict[str, Any]] = {
                str(c.get("chunk_id")): c for c in existing
            }
            for c in new_chunks_payload:
                by_id[str(c["chunk_id"])] = c
            self._index["chunks"] = list(by_id.values())
            self._save()

            sources = sorted({c["source"] for c in new_chunks_payload})
            return {
                "indexed_files": len(files),
                "indexed_chunks": len(new_chunks_payload),
                "total_chunks": len(self._index["chunks"]),
                "sources": sources,
                "skipped_files": skipped,
                "embedding_error": embedding_error,
                "index_path": str(self.index_path),
            }

    def status(self) -> Dict[str, Any]:
        with self._lock:
            chunks = self._index.get("chunks", [])
            sources = sorted({str(c.get("source")) for c in chunks})
            cfg = self._embedding_backend()
            return {
                "index_path": str(self.index_path),
                "created_at": self._index.get("created_at"),
                "updated_at": self._index.get("updated_at"),
                "total_chunks": len(chunks),
                "total_sources": len(sources),
                "sources_sample": sources[:50],
                "embed_provider": cfg["provider"],
                "embed_model": cfg["model"],
            }

    def search(
        self,
        *,
        client: Any = None,
        query: str,
        top_k: int = 6,
        min_score: float = 0.15,
    ) -> List[Dict[str, Any]]:
        q = (query or "").strip()
        if not q:
            return []
        with self._lock:
            chunks = self._index.get("chunks", [])
            if not chunks:
                return []
            q_vec: Optional[List[float]] = None
            try:
                q_vec = self._embed_texts(client, [q])[0]
            except Exception:
                q_vec = None
            lexical_min_score = float(
                os.getenv("ASSISTANT_RAG_LEXICAL_MIN_SCORE", "0.02")
            )
            scored: List[Dict[str, Any]] = []
            for c in chunks:
                emb = c.get("embedding")
                if q_vec and isinstance(emb, list) and emb:
                    score = _cosine_similarity(q_vec, emb)
                    threshold = min_score
                else:
                    score = _lexical_similarity(q, str(c.get("text") or ""))
                    threshold = lexical_min_score
                if score >= threshold:
                    scored.append(
                        {
                            "score": round(score, 4),
                            "chunk_id": c.get("chunk_id"),
                            "source": c.get("source"),
                            "text": c.get("text"),
                            "metadata": c.get("metadata", {}),
                        }
                    )
            scored.sort(key=lambda x: x["score"], reverse=True)
            return scored[: max(1, min(top_k, 20))]


def _cosine_similarity(v1: List[float], v2: List[float]) -> float:
    if not v1 or not v2 or len(v1) != len(v2):
        return 0.0
    dot = 0.0
    n1 = 0.0
    n2 = 0.0
    for a, b in zip(v1, v2):
        dot += a * b
        n1 += a * a
        n2 += b * b
    if n1 <= 0 or n2 <= 0:
        return 0.0
    return float(dot / (math.sqrt(n1) * math.sqrt(n2)))


def _lexical_similarity(q: str, text: str) -> float:
    q_tokens = set(_tokenize(q))
    t_tokens = set(_tokenize(text))
    if not q_tokens or not t_tokens:
        return 0.0
    inter = len(q_tokens.intersection(t_tokens))
    denom = len(q_tokens.union(t_tokens))
    if denom <= 0:
        return 0.0
    return float(inter / denom)


def _tokenize(s: str) -> List[str]:
    parts = re.split(r"[^a-zA-Z0-9_]+", (s or "").lower())
    return [p for p in parts if len(p) > 1]


_RAG_STORE: Optional[LocalRAGStore] = None
_RAG_STORE_LOCK = Lock()


def get_rag_store() -> LocalRAGStore:
    global _RAG_STORE
    with _RAG_STORE_LOCK:
        if _RAG_STORE is None:
            _RAG_STORE = LocalRAGStore()
        return _RAG_STORE
