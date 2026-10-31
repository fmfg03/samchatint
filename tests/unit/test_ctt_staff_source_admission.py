"""Synthetic exact-source admission; no enrolled operator or business rows."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
import hashlib
import json
import os
import time
from uuid import uuid4

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import fitz
import pytest

from devnous.copa_telmex.batch_admission import sha256_value, validate_manifest
from devnous.copa_telmex.staff_capture_edition import TrustedEditionBinding
from devnous.copa_telmex.staff_source_admission import (
    SourceAdmissionDenied,
    SourceAdmissionLimits,
    admit_prepared_sources,
    prepare_source_admission,
    sign_source_admission,
)


@pytest.fixture
def sources(tmp_path):
    originals, private = tmp_path / "originals", tmp_path / "private"
    originals.mkdir(mode=0o700)
    private.mkdir(mode=0o700)
    with fitz.open() as doc:
        for label in ("synthetic one", "synthetic two"):
            doc.new_page().insert_text((72, 72), label)
        pdf = doc.tobytes()
    path = originals / "source.pdf"
    path.write_bytes(pdf)
    path.chmod(0o600)
    binding = TrustedEditionBinding("synthetic", uuid4(), uuid4(), 2026, "sample")
    staff = []
    for slot, role in ((1, "director_tecnico"), (2, "auxiliar")):
        identity = {
            "slot": slot,
            "role": role,
            "slot_ref": "staff:" + str(slot),
            "evidence": {"first_name": {"value": None, "status": "missing"}},
        }
        staff.append(identity | {"staff_entry_id": sha256_value(identity)})
    manifest = {
        "schema_version": "ctt.registration.batch-admission.v1",
        "edition": {
            "tournament_id": str(binding.tournament_id),
            "edition_year": 2026,
            "roster_slug": "sample",
        },
        "documents": [
            {
                "document_id": "a" * 64,
                "pdf": "source.pdf",
                "pdf_sha256": hashlib.sha256(pdf).hexdigest(),
                "pages": [1, 2],
                "extraction": {
                    "team": {"name": "Synthetic"},
                    "players": [],
                    "staff": staff,
                },
            }
        ],
    }
    limits = SourceAdmissionLimits(
        1_000_000, 1_000_000, 2_000_000, 1_000_000, 2_000_000
    )
    return manifest, originals, private, binding, limits


def raw(manifest):
    return json.dumps(manifest, indent=2).encode()


def approval(prepared):
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    now = int(time.time())
    envelope = sign_source_admission(
        prepared.plan_bytes,
        admission_id=uuid4(),
        issued_at=now,
        expires_at=now + 180,
        kid="test",
        private_key=key,
    )
    return envelope, {"test": public}


class Pool:
    def __init__(self):
        self.active = False
        self.fail_commit = False
        self.calls = []

    @asynccontextmanager
    async def acquire(self):
        yield self

    def is_in_transaction(self):
        return self.active

    @asynccontextmanager
    async def transaction(self):
        self.active = True
        try:
            yield
            if self.fail_commit:
                raise RuntimeError("synthetic commit failure")
        finally:
            self.active = False

    async def fetchrow(self, _query):
        return dict(
            name="synthetic_source_admitter",
            session_name="synthetic_source_admitter",
            rolsuper=False,
            rolcreatedb=False,
            rolcreaterole=False,
            rolreplication=False,
            rolbypassrls=False,
            owner_member=False,
            app_related=False,
            broad_privileges=False,
        )

    async def fetchval(self, query, *args):
        if query.startswith("SHOW"):
            return "0"
        self.calls.append(args)
        return True


def test_prepares_normalized_manifest_and_actual_page_hashes_without_publishing(
    sources,
):
    manifest, originals, private, binding, limits = sources
    with prepare_source_admission(
        raw(manifest), originals, binding, limits
    ) as prepared:
        plan = json.loads(prepared.plan_bytes)
        assert plan["manifest_sha256"] == validate_manifest(manifest)["manifest_sha256"]
        assert plan["manifest_sha256"] != hashlib.sha256(raw(manifest)).hexdigest()
        assert len(plan["documents"][0]["pages"]) == 2
        assert not list(private.iterdir())
        assert "Synthetic" not in prepared.plan_bytes.decode()
        handles = list(prepared.pdfs.values())
        assert all(not stream.closed for stream in handles)
    assert all(stream.closed for stream in handles)


def test_signed_admission_publishes_private_exact_files_and_commits(sources):
    manifest, originals, private, binding, limits = sources
    with prepare_source_admission(
        raw(manifest), originals, binding, limits
    ) as prepared:
        envelope, keys = approval(prepared)
        pool = Pool()
        result = asyncio.run(
            admit_prepared_sources(
                prepared, envelope, public_keys=keys, private_root=private, pool=pool
            )
        )
        assert result == prepared.plan_sha256
        assert len(pool.calls) == 1
        assert pool.calls[0][1] == prepared.plan_bytes
        assert pool.calls[0][-1] == envelope
        for directory in private.iterdir():
            assert directory.stat().st_mode & 0o777 == 0o700
            for path in directory.iterdir():
                assert path.stat().st_mode & 0o777 == 0o400
                assert path.stat().st_nlink == 1
        assert len(list((private / "pages").iterdir())) == 2


@pytest.mark.parametrize(
    "attack",
    ["pdf", "filename", "declared_raw_hash", "payload", "duplicate_json", "deep_json"],
)
def test_invalid_or_changed_originals_are_rejected(sources, attack):
    manifest, originals, private, binding, limits = sources
    if attack == "pdf":
        with (originals / "source.pdf").open("ab") as stream:
            stream.write(b"changed")
    elif attack == "filename":
        manifest["documents"][0]["pdf"] = "../source.pdf"
    elif attack == "declared_raw_hash":
        manifest["manifest_sha256"] = hashlib.sha256(raw(manifest)).hexdigest()
    elif attack == "payload":
        manifest["documents"][0]["payload_sha256"] = "f" * 64
    data = raw(manifest)
    if attack == "duplicate_json":
        data = b'{"documents":[],"documents":[]}'
    elif attack == "deep_json":
        data = b"[" * 70 + b"]" * 70
    with pytest.raises(SourceAdmissionDenied):
        with prepare_source_admission(data, originals, binding, limits):
            pytest.fail("changed sources accepted")
    assert not list(private.iterdir())


@pytest.mark.parametrize(
    "attack", ["symlink", "hardlink", "mode", "root_mode", "root_symlink"]
)
def test_source_paths_require_private_owned_regular_single_link_files(sources, attack):
    manifest, originals, _, binding, limits = sources
    source = originals / "source.pdf"
    if attack == "symlink":
        source.rename(originals / "target.pdf")
        source.symlink_to(originals / "target.pdf")
    elif attack == "hardlink":
        os.link(source, originals / "second.pdf")
    elif attack == "mode":
        source.chmod(0o644)
    elif attack == "root_mode":
        originals.chmod(0o755)
    elif attack == "root_symlink":
        alias = originals.parent / "alias"
        alias.symlink_to(originals, target_is_directory=True)
        originals = alias
    with pytest.raises(SourceAdmissionDenied):
        with prepare_source_admission(raw(manifest), originals, binding, limits):
            pytest.fail("unsafe source accepted")


@pytest.mark.parametrize(
    "field",
    [
        "max_manifest_bytes",
        "max_pdf_bytes",
        "max_total_pdf_bytes",
        "max_page_bytes",
        "max_total_page_bytes",
        "max_page_pixels",
    ],
)
def test_configured_resource_limits_are_enforced(sources, field):
    manifest, originals, _, binding, limits = sources
    with pytest.raises(SourceAdmissionDenied):
        with prepare_source_admission(
            raw(manifest), originals, binding, replace(limits, **{field: 1})
        ):
            pytest.fail("resource limit ignored")


@pytest.mark.parametrize("attack", ["key", "signature", "plan", "expiry", "kid"])
def test_invalid_approval_rejected_before_publishing(sources, attack):
    manifest, originals, private, binding, limits = sources
    with prepare_source_admission(
        raw(manifest), originals, binding, limits
    ) as prepared:
        envelope, keys = approval(prepared)
        if attack == "key":
            keys = {"test": b"x" * 32}
        elif attack == "plan":
            prepared = replace(prepared, plan_bytes=prepared.plan_bytes + b" ")
        else:
            value = json.loads(envelope)
            if attack == "signature":
                value["signature"] = "A" * 88
            elif attack == "expiry":
                value["body"]["expires_at"] = 1
            elif attack == "kid":
                value["body"]["kid"] = "unknown"
            envelope = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        pool = Pool()
        with pytest.raises(SourceAdmissionDenied):
            asyncio.run(
                admit_prepared_sources(
                    prepared,
                    envelope,
                    public_keys=keys,
                    private_root=private,
                    pool=pool,
                )
            )
        assert not list(private.iterdir()) and not pool.calls


def test_database_failure_keeps_only_private_cas_and_retry_is_exact(sources):
    manifest, originals, private, binding, limits = sources
    with prepare_source_admission(
        raw(manifest), originals, binding, limits
    ) as prepared:
        envelope, keys = approval(prepared)
        pool = Pool()
        pool.fail_commit = True
        with pytest.raises(SourceAdmissionDenied):
            asyncio.run(
                admit_prepared_sources(
                    prepared,
                    envelope,
                    public_keys=keys,
                    private_root=private,
                    pool=pool,
                )
            )
        files = {
            path: path.read_bytes()
            for directory in private.iterdir()
            for path in directory.iterdir()
        }
        pool.fail_commit = False
        assert (
            asyncio.run(
                admit_prepared_sources(
                    prepared,
                    envelope,
                    public_keys=keys,
                    private_root=private,
                    pool=pool,
                )
            )
            == prepared.plan_sha256
        )
        assert all(path.read_bytes() == data for path, data in files.items())


def test_existing_conflicting_cas_and_mutated_snapshot_are_rejected(sources):
    manifest, originals, private, binding, limits = sources
    with prepare_source_admission(
        raw(manifest), originals, binding, limits
    ) as prepared:
        envelope, keys = approval(prepared)
        pdf = next(iter(prepared.pdfs.values()))
        pdf.seek(0)
        pdf.write(b"changed")
        pool = Pool()
        with pytest.raises(SourceAdmissionDenied):
            asyncio.run(
                admit_prepared_sources(
                    prepared,
                    envelope,
                    public_keys=keys,
                    private_root=private,
                    pool=pool,
                )
            )
        assert not pool.calls


def test_dirty_connection_never_returns_an_admission(sources):
    manifest, originals, private, binding, limits = sources
    with prepare_source_admission(
        raw(manifest), originals, binding, limits
    ) as prepared:
        envelope, keys = approval(prepared)
        pool = Pool()
        pool.active = True
        with pytest.raises(SourceAdmissionDenied):
            asyncio.run(
                admit_prepared_sources(
                    prepared,
                    envelope,
                    public_keys=keys,
                    private_root=private,
                    pool=pool,
                )
            )
        assert not pool.calls


@pytest.mark.parametrize("stage", ["partial", "linked_complete"])
def test_retry_recovers_only_its_owned_scratch_after_interruption(sources, stage):
    manifest, originals, private, binding, limits = sources
    with prepare_source_admission(
        raw(manifest), originals, binding, limits
    ) as prepared:
        envelope, keys = approval(prepared)
        admission = json.loads(envelope)["body"]["admission_id"].replace("-", "")
        sha = next(iter(prepared.pdfs))
        directory = private / "pdfs"
        directory.mkdir(mode=0o700)
        name = sha + ".pdf"
        temporary = directory / (
            ".source-admission-"
            + admission
            + "-"
            + hashlib.sha256(name.encode()).hexdigest()[:16]
        )
        final = directory / name
        if stage == "partial":
            temporary.write_bytes(b"partial scratch")
            temporary.chmod(0o600)
        else:
            data = prepared.pdfs[sha].read()
            final.write_bytes(data)
            final.chmod(0o400)
            os.link(final, temporary)
            assert final.stat().st_nlink == 2
        assert (
            asyncio.run(
                admit_prepared_sources(
                    prepared,
                    envelope,
                    public_keys=keys,
                    private_root=private,
                    pool=Pool(),
                )
            )
            == prepared.plan_sha256
        )
        assert not temporary.exists()
        assert final.stat().st_nlink == 1
        assert hashlib.sha256(final.read_bytes()).hexdigest() == sha


def test_retry_never_deletes_an_unrelated_hardlink_or_overwrites_conflict(sources):
    manifest, originals, private, binding, limits = sources
    with prepare_source_admission(
        raw(manifest), originals, binding, limits
    ) as prepared:
        envelope, keys = approval(prepared)
        directory = private / "pdfs"
        directory.mkdir(mode=0o700)
        sha = next(iter(prepared.pdfs))
        final = directory / (sha + ".pdf")
        final.write_bytes(b"unrelated content")
        final.chmod(0o400)
        other = private.parent / "unrelated-link"
        os.link(final, other)
        with pytest.raises(SourceAdmissionDenied):
            asyncio.run(
                admit_prepared_sources(
                    prepared,
                    envelope,
                    public_keys=keys,
                    private_root=private,
                    pool=Pool(),
                )
            )
        assert final.read_bytes() == other.read_bytes() == b"unrelated content"
        assert final.stat().st_nlink == 2


@pytest.mark.parametrize(
    "change",
    [
        {"rolsuper": True},
        {"rolcreatedb": True},
        {"rolcreaterole": True},
        {"rolreplication": True},
        {"rolbypassrls": True},
        {"owner_member": True},
        {"app_related": True},
        {"broad_privileges": True},
        {"session_name": "other"},
    ],
)
def test_admitter_requires_an_isolated_database_identity(sources, change):
    manifest, originals, private, binding, limits = sources
    with prepare_source_admission(
        raw(manifest), originals, binding, limits
    ) as prepared:
        envelope, keys = approval(prepared)
        pool = Pool()
        original = pool.fetchrow

        async def changed(query):
            return (await original(query)) | change

        pool.fetchrow = changed
        with pytest.raises(SourceAdmissionDenied):
            asyncio.run(
                admit_prepared_sources(
                    prepared,
                    envelope,
                    public_keys=keys,
                    private_root=private,
                    pool=pool,
                )
            )
        assert not pool.calls
