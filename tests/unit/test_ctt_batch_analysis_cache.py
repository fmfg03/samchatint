import os
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest

from devnous.copa_telmex.staff_batch_contracts import digest
from devnous.copa_telmex.staff_batch_source_service import (
    BatchCatalogSourceProvider,
    PrivateBatchAnalysisCache,
)
from devnous.copa_telmex.staff_batch_store import BatchDenied


def test_retained_analysis_is_private_immutable_and_source_bound(tmp_path):
    root = tmp_path / "cache"
    root.mkdir(mode=0o700)
    cache = PrivateBatchAnalysisCache(root)
    identity = {"profile": "a", "source": "exact"}
    assert cache.read(identity) is None
    cache.retain(identity, {"readings": ["original"]})
    assert cache.read(identity) == {"readings": ["original"]}
    assert cache.read({**identity, "source": "other"}) is None
    with pytest.raises(Exception):
        cache.retain(identity, {"readings": ["replacement"]})
    assert cache.read(identity) == {"readings": ["original"]}
    path = next(root.iterdir())
    assert os.stat(path).st_mode & 0o077 == 0


def test_unsafe_cache_directory_and_oversize_receipt_are_rejected(tmp_path):
    root = tmp_path / "cache"
    root.mkdir(mode=0o755)
    cache = PrivateBatchAnalysisCache(root, maximum_bytes=32)
    with pytest.raises(Exception):
        cache.read({"source": "exact"})
    root.chmod(0o700)
    with pytest.raises(Exception):
        cache.retain({}, {"data": "x" * 100})


@pytest.mark.asyncio
async def test_source_provider_keeps_rejected_plan_as_honest_exception(monkeypatch):
    profile = {"template_id": "letter", "observation_views": [{"fields": {}}]}
    profile_hash = digest(profile)
    page = {
        "pdf_sha256": "a" * 64,
        "pdf_page": 1,
        "page_index": 1,
        "width": 612,
        "height": 792,
    }
    source = {
        "document_id": "b" * 64,
        "manifest_sha256": "c" * 64,
        "source_payload_hash": "d" * 64,
        "source_pages": [page],
        "manifest": {
            "edition": {"roster_slug": "synthetic"},
            "documents": [
                {
                    "document_id": "b" * 64,
                    "extraction": {"structural_observation": {"physical_slots": []}},
                }
            ],
        },
    }
    member = {
        "document_id": source["document_id"],
        "status": "analysis_pending",
        "auto_ready": False,
        "source_slices": [{"pdf_sha256": "a" * 64, "pages": [1]}],
        "source_pages": [page],
        "snapshot": {"source": source},
    }
    analysis = {
        "document_id": source["document_id"],
        "source_binding_hash": digest([page]),
        "auto_ready": True,
        "issues": [],
        "template_id": "letter",
        "team": {"category": "UNICA"},
        "players": [],
        "staff": [],
    }
    checked = []

    @contextmanager
    def snapshot(_scope):
        yield SimpleNamespace(verify_unchanged=lambda: checked.append(True))

    async def context(*args):
        return {
            "tenant_id": "synthetic",
            "edition_id": str(uuid4()),
            "capabilities": [{"manifest_sha256": "c" * 64, "policy_hash": "e" * 64}],
        }

    async def prepare(*args):
        return {
            "members": [member],
            "source_pages": [page],
            "corpus_hash": "f" * 64,
            "documents": [
                {
                    "document_id": "b" * 64,
                    "template_id": "letter",
                    "category": "UNICA",
                    "source_slices": member["source_slices"],
                }
            ],
            "sample_documents": ["b" * 64],
        }

    def rejected(*args):
        raise ValueError("private policy diagnostic")

    provider = BatchCatalogSourceProvider(
        store=SimpleNamespace(preparation_context=context),
        source_adapter=SimpleNamespace(open_verified_snapshot=snapshot),
        profiles={profile_hash: profile},
        builders={"e" * 64: SimpleNamespace(build_batch=rejected)},
        analysis_cache=SimpleNamespace(read=lambda identity: analysis),
    )
    provider.prepare_run = prepare
    # This dispatch test isolates policy rejection. Normative replay uses real
    # CAS pages in test_ctt_batch_source_provider and observation_inputs tests.
    monkeypatch.setattr(
        "devnous.copa_telmex.staff_batch_source_service.verify_observation_inputs",
        lambda *args: {"expected_fields": [], "readings": []},
    )
    run = {
        "account_id": str(uuid4()),
        "edition_id": str(uuid4()),
        "run_id": str(uuid4()),
        "manifest_hashes": ["c" * 64],
        "profile_hash": profile_hash,
    }
    result = await provider.analyze(run)
    assert result["members"][0]["status"] == "exception"
    assert result["members"][0]["auto_ready"] is False
    assert result["members"][0]["issues"] == [{"code": "BATCH_PLAN_REJECTED"}]
    assert checked == [True]
    provider.cache = SimpleNamespace(read=lambda identity: None)
    with pytest.raises(BatchDenied, match="ANALYSIS_MISSING"):
        await provider.analyze(run)
