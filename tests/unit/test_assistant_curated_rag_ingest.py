from pathlib import Path

from scripts.assistant_ingest_curated_rag import (
    CURATED_RAG_PATHS,
    curated_paths,
)


def test_curated_rag_paths_include_assistant_canon() -> None:
    assert "docs/assistant/product-canon.md" in CURATED_RAG_PATHS
    assert "docs/assistant/owner-ai-needs.md" in CURATED_RAG_PATHS
    assert "docs/sprints/rqf-058-sprint-close-candidate.md" in CURATED_RAG_PATHS
    assert "artifacts/rqf-samchat-assistant-008-readonly-canary.md" in CURATED_RAG_PATHS
    assert "docs/assistant/rqf-assistant-009e-evaluation-set.md" not in CURATED_RAG_PATHS


def test_curated_rag_paths_do_not_include_broad_repo_or_secrets() -> None:
    forbidden = {".", "src", "tests", ".env", "uploads", "downloads"}
    assert forbidden.isdisjoint(set(CURATED_RAG_PATHS))

    for item in CURATED_RAG_PATHS:
        lowered = item.lower()
        assert ".env" not in lowered
        assert "secret" not in lowered
        assert "uploads" not in lowered
        assert "downloads" not in lowered


def test_curated_paths_resolves_existing_safe_files(tmp_path: Path) -> None:
    root = tmp_path
    (root / "docs" / "assistant").mkdir(parents=True)
    (root / "docs" / "assistant" / "product-canon.md").write_text(
        "canon", encoding="utf-8"
    )
    (root / "docs" / "assistant" / "rqf-assistant-009e-evaluation-set.md").write_text(
        "eval", encoding="utf-8"
    )
    (root / "docs" / "sprints").mkdir(parents=True)
    (root / "docs" / "sprints" / "rqf-058-sprint-close-candidate.md").write_text(
        "sprint", encoding="utf-8"
    )
    (root / "private").mkdir()
    (root / "private" / "secret.md").write_text("secret", encoding="utf-8")
    (root / "2.1. Cat?logo Contable.xls").write_text("binary-ish", encoding="utf-8")

    paths = curated_paths(
        root=root,
        requested=(
            "docs/assistant",
            "docs/assistant/product-canon.md",
            "docs/assistant/rqf-assistant-009e-evaluation-set.md",
            "docs/sprints/rqf-058-sprint-close-candidate.md",
            "private/secret.md",
            "2.1. Cat?logo Contable.xls",
            "missing.md",
        ),
    )

    assert str((root / "docs" / "assistant").resolve()) not in paths
    assert str((root / "docs" / "assistant" / "product-canon.md").resolve()) in paths
    assert (
        str((root / "docs" / "sprints" / "rqf-058-sprint-close-candidate.md").resolve())
        in paths
    )
    assert not any("private" in path for path in paths)
    assert not any("evaluation-set" in path for path in paths)
    assert not any(path.endswith(".xls") for path in paths)
