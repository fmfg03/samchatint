import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from devnous.copa_telmex.batch_admission import (
    BatchAdmissionError,
    admission_receipt,
    sha256_value,
    validate_manifest,
)
from devnous.gastos.amex_schema_policy import (
    CTT_OWNER_MIGRATION_TABLES,
    runtime_managed_tables,
)

os.environ.setdefault(
    "SESSION_SECRET_KEY", "test-only-session-secret-key-0123456789abcdef"
)

import copa_telmex_dashboard as dashboard  # noqa: E402


class _Upload:
    def __init__(self, filename, payload):
        self.filename = filename
        self.payload = payload
        self.offset = 0

    async def read(self, size=-1):
        if size < 0:
            size = len(self.payload) - self.offset
        chunk = self.payload[self.offset : self.offset + size]
        self.offset += len(chunk)
        return chunk

    async def seek(self, offset):
        self.offset = offset


class _Form(dict):
    def getlist(self, key):
        return list(self.get(key, []))


class _Request:
    def __init__(self, form):
        self._form = form

    async def form(self):
        return self._form


class _Result:
    def __init__(self, *, mapping=None, scalar=None, rows=None):
        self.mapping = mapping
        self.scalar = scalar
        self.rows = list(rows or [])

    def mappings(self):
        return self

    def one_or_none(self):
        return self.mapping

    def scalar_one(self):
        return self.scalar

    def scalar_one_or_none(self):
        return self.scalar

    def scalars(self):
        return self

    def all(self):
        return self.rows


class _AdmissionSession:
    def __init__(self, state, phase):
        self.state = state
        self.phase = phase
        self.calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def execute(self, _statement, params=None):
        self.calls += 1
        if self.phase == "batch":
            if self.calls == 1:
                return _Result(mapping={"id": str(self.state.edition_id)})
            if self.calls == 2:
                return _Result(rows=[self.state.binding] if self.state.binding else [])
            if self.calls == 3:
                if self.state.batch is None:
                    self.state.batch = SimpleNamespace(
                        id=params["id"],
                        tournament_edition_id=params["edition_id"],
                        manifest_sha256=params["manifest_sha256"],
                        document_count=params["document_count"],
                    )
                return _Result()
            return _Result(scalar=self.state.batch)
        if self.calls == 1:
            return _Result(scalar=self.state.batch)
        return _Result(scalar=self.state.binding)

    def add(self, value):
        self.state.added_types.append(type(value).__name__)
        if isinstance(value, dashboard.RegistrationBatchDocument):
            self.state.binding = value

    async def flush(self):
        return None

    async def commit(self):
        return None


class _AdmissionState:
    def __init__(self):
        self.edition_id = UUID("47bcc601-929a-4a8a-9005-99f10ee1aa58")
        self.batch = None
        self.binding = None
        self.added_types = []
        self.session_count = 0

    def session(self):
        phase = "batch" if self.session_count % 2 == 0 else "document"
        self.session_count += 1
        return _AdmissionSession(self, phase)


def _staff(slot, role):
    evidence = {"first_name": {"value": None, "status": "missing"}}
    identity = {"role": role, "slot": slot, "slot_ref": role, "evidence": evidence}
    return {
        "staff_entry_id": sha256_value(identity),
        "slot": slot,
        "role": role,
        "slot_ref": role,
        "first_name": None,
        "evidence": evidence,
    }


def manifest():
    return {
        "schema_version": "ctt.registration.batch-admission.v1",
        "edition": {
            "tournament_id": "67c556df-f953-4f99-8cab-87ed4d111b05",
            "edition_year": 2026,
            "roster_slug": "copa-telmex-2026",
        },
        "documents": [
            {
                "document_id": "a" * 64,
                "pdf": "Oaxaca.pdf",
                "pdf_sha256": "b" * 64,
                "pages": [1, 2],
                "extraction": {
                    "team": {"name": "Equipo"},
                    "players": [],
                    "staff": [
                        _staff(1, "director_tecnico"),
                        _staff(2, "auxiliar"),
                    ],
                },
            }
        ],
    }


def test_manifest_binds_exact_scope_payload_and_two_staff_members():
    result = validate_manifest(
        manifest(),
        expected_tournament_id="67c556df-f953-4f99-8cab-87ed4d111b05",
        expected_edition_year=2026,
    )
    document = result["documents"][0]
    assert len(document["extraction"]["staff"]) == 2
    assert document["payload_sha256"] == sha256_value(document["extraction"])
    assert document["source_pages_sha256"] == sha256_value([1, 2])
    assert len(result["manifest_sha256"]) == 64


def test_manifest_identity_is_stable_when_document_order_changes():
    first = manifest()["documents"][0]
    second = {
        **first,
        "document_id": "f" * 64,
        "pages": [3, 4],
    }
    forward = manifest()
    forward["documents"] = [first, second]
    reverse = manifest()
    reverse["documents"] = [second, first]
    assert (
        validate_manifest(forward)["manifest_sha256"]
        == validate_manifest(reverse)["manifest_sha256"]
    )


@pytest.mark.parametrize(
    "mutate,code",
    [
        (
            lambda value: value["edition"].update(edition_year=2025),
            "TOURNAMENT_EDITION_SCOPE_MISMATCH",
        ),
        (
            lambda value: value["edition"].update(tournament_id="not-a-uuid"),
            "TOURNAMENT_ID_INVALID",
        ),
        (
            lambda value: value["documents"][0].update(pdf="../private.pdf"),
            "INVALID_SOURCE_FILENAME",
        ),
        (
            lambda value: value["documents"][0].update(pdf="..\\private.pdf"),
            "INVALID_SOURCE_FILENAME",
        ),
        (
            lambda value: value["documents"][0].update(pages=[2, 1]),
            "INVALID_SOURCE_PAGES",
        ),
        (
            lambda value: value["documents"][0]["extraction"].update(staff=[]),
            "TECHNICAL_STAFF_INCOMPLETE",
        ),
        (
            lambda value: value["documents"][0]["extraction"]["staff"][0].update(
                slot_ref="changed"
            ),
            "TECHNICAL_STAFF_ID_MISMATCH",
        ),
        (
            lambda value: value["documents"][0]["extraction"]["staff"][1].update(
                role="director_tecnico"
            ),
            "TECHNICAL_STAFF_SCOPE_MISMATCH",
        ),
    ],
)
def test_manifest_fails_closed(mutate, code):
    value = manifest()
    mutate(value)
    with pytest.raises(BatchAdmissionError, match=code):
        validate_manifest(
            value,
            expected_tournament_id="67c556df-f953-4f99-8cab-87ed4d111b05",
            expected_edition_year=2026,
        )


@pytest.mark.parametrize("invalid_slot", ["1", 1.5, True])
def test_manifest_rejects_non_integer_staff_slots(invalid_slot):
    value = manifest()
    value["documents"][0]["extraction"]["staff"][0]["slot"] = invalid_slot
    with pytest.raises(BatchAdmissionError, match="TECHNICAL_STAFF_SCOPE_MISMATCH"):
        validate_manifest(value)


def test_declared_payload_hash_cannot_hide_changed_personal_evidence():
    value = manifest()
    value["documents"][0]["payload_sha256"] = sha256_value(
        value["documents"][0]["extraction"]
    )
    value["documents"][0]["extraction"]["staff"][0]["first_name"] = "changed"
    with pytest.raises(BatchAdmissionError, match="EXTRACTION_HASH_MISMATCH"):
        validate_manifest(value)


def test_manifest_enforces_bounded_documents_pdfs_and_pages():
    too_many_pages = manifest()
    too_many_pages["documents"][0]["pages"] = [1, 2, 3, 4, 5]
    with pytest.raises(BatchAdmissionError, match="INVALID_SOURCE_PAGES"):
        validate_manifest(too_many_pages)

    too_many_pdfs = manifest()
    template = too_many_pdfs["documents"][0]
    too_many_pdfs["documents"] = []
    for index in range(17):
        document = copy.deepcopy(template)
        document["document_id"] = f"{index + 1:064x}"
        document["pdf"] = f"source-{index + 1}.pdf"
        document["pdf_sha256"] = f"{index + 1:064x}"
        too_many_pdfs["documents"].append(document)
    with pytest.raises(BatchAdmissionError, match="BATCH_PDF_LIMIT_EXCEEDED"):
        validate_manifest(too_many_pdfs)


def test_receipt_binds_actor_session_and_source_without_field_values():
    result = validate_manifest(manifest())
    receipt = admission_receipt(
        manifest_sha256=result["manifest_sha256"],
        document=result["documents"][0],
        actor_id="employee-1",
        review_session_id="session-1",
    )
    assert receipt["actor_id"] == "employee-1"
    assert receipt["review_session_id"] == "session-1"
    assert "extraction" not in receipt
    assert len(receipt["event_sha256"]) == 64


def test_script_does_not_offer_database_or_apply_flags():
    source = (
        Path(__file__).parents[2] / "scripts/prepare_ctt_pilot_admission.py"
    ).read_text()
    assert "--apply" not in source
    assert "DATABASE_URL" not in source


def test_manifest_preparer_rejects_extra_technical_staff(tmp_path):
    script = Path(__file__).parents[2] / "scripts/prepare_ctt_pilot_admission.py"
    spec = importlib.util.spec_from_file_location("prepare_ctt_pilot", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    pdf = tmp_path / "source.pdf"
    pdf.write_bytes(b"reviewed source")
    people = [
        {"role": "director_tecnico"},
        {"role": "auxiliar"},
        {"role": "auxiliar"},
    ]
    reconciliation = {
        "documents": [
            {
                "document_id": "a" * 64,
                "source": {
                    "pdf": pdf.name,
                    "pdf_sha256": hashlib.sha256(pdf.read_bytes()).hexdigest(),
                    "pages": [1],
                },
                "visual_reference": {
                    "people": people,
                    "source_reviewed_extraction_candidate": {},
                },
            }
        ]
    }
    with pytest.raises(ValueError, match="Technical staff evidence missing"):
        module.prepare(reconciliation, tmp_path)


def test_batch_tables_require_owner_migration_instead_of_runtime_create_all():
    managed = {table.name for table in runtime_managed_tables(dashboard.Base.metadata)}
    assert not (CTT_OWNER_MIGRATION_TABLES & managed)


def test_batch_endpoint_serializes_concurrent_idempotent_retries():
    source = Path(dashboard.__file__).read_text()
    route = source.split("async def admit_registration_review_batch", 1)[1].split(
        '@app.get("/api/registration-review/{session_id}"', 1
    )[0]
    assert "ON CONFLICT (id) DO NOTHING" in route
    assert route.count(".with_for_update()") >= 2


def test_rollout_requires_owner_migration_before_release_activation():
    roadmap = (
        Path(__file__).parents[2] / "docs/roadmap/ctt-pilot-batch-admission.md"
    ).read_text()
    assert roadmap.index("apply the backward-compatible owner migration") < (
        roadmap.index("Activate the prepared release")
    )


def test_normalization_preserves_staff_and_commit_fails_closed():
    value = manifest()["documents"][0]["extraction"]
    normalized = dashboard._normalize_review_extraction(value)
    assert normalized["staff"] == value["staff"]
    validation = dashboard._build_review_commit_validation(normalized, {})
    assert "STAFF_GOVERNANCE_CONTRACT_REQUIRED" in {
        item["code"] for item in validation["blockers"]
    }
    assert validation["ready_to_commit"] is False


def test_batch_binding_blocks_commit_even_when_latest_extraction_lacks_staff():
    validation = dashboard._build_review_commit_validation(
        {"team": {"name": "Equipo"}, "players": []}, {}
    )
    validation = dashboard._attach_batch_staff_governance_blocker(validation)
    assert "STAFF_GOVERNANCE_CONTRACT_REQUIRED" in {
        item["code"] for item in validation["blockers"]
    }
    assert validation["ready_to_commit"] is False


def test_batch_bound_reprocess_rejects_staff_removal():
    current = manifest()["documents"][0]["extraction"]
    proposed = {**current, "staff": []}
    with pytest.raises(HTTPException) as exc_info:
        dashboard._ensure_batch_reprocess_staff_preserved(
            batch_binding_exists=True,
            current_extraction=current,
            proposed_extraction=proposed,
        )
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["error"] == "batch_staff_evidence_immutable"


def test_batch_bound_reprocess_is_rejected_before_ocr_processing():
    with pytest.raises(HTTPException) as exc_info:
        dashboard._ensure_batch_reprocess_supported(batch_binding_exists=True)
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["error"] == "batch_reprocess_not_supported"

    route = Path(dashboard.__file__).read_text().split(
        "async def reprocess_registration_review_session", 1
    )[1]
    assert route.index("_ensure_batch_reprocess_supported") < route.index(
        "_process_review_assets"
    )


def test_batch_player_page_map_translates_physical_pages_to_asset_indices():
    document = manifest()["documents"][0]
    document["pages"] = [3, 7]
    document["extraction"]["players"] = [
        {"slot_ref": "page:3:jugador_1"},
        {"slot_ref": "page:7:jugador_2"},
    ]
    assert dashboard._batch_player_page_map(document) == {"1": 1, "2": 2}

    document["extraction"]["players"][1]["slot_ref"] = "page:8:jugador_2"
    with pytest.raises(HTTPException) as exc_info:
        dashboard._batch_player_page_map(document)
    assert exc_info.value.detail["error"] == "player_source_page_mismatch"


def test_new_review_session_can_prepare_its_first_batch_draft():
    review_session = dashboard.RegistrationReviewSession(
        source="batch_admission", provider="reviewed_manifest"
    )
    values = dashboard._prepare_review_draft_values(
        review_session,
        manifest()["documents"][0]["extraction"],
        {"provider": "reviewed_manifest"},
    )
    assert values["extraction"]["staff"][0]["role"] == "director_tecnico"
    assert values["review_edits"] == values["extraction"]
    assert values["validation"]["ready_to_commit"] is False


def test_selected_pdf_pages_keep_source_order_and_omit_other_pages(tmp_path):
    if dashboard.fitz is None:
        pytest.skip("PyMuPDF unavailable")
    document = dashboard.fitz.open()
    for label in ("first", "second", "third"):
        page = document.new_page(width=200, height=200)
        page.insert_text((20, 30), label)
    payload = document.tobytes()
    document.close()
    assets = dashboard._render_pdf_review_asset_pages(tmp_path, payload, pages=[1, 3])
    assert [item["source_pdf_page"] for item in assets] == [1, 3]
    assert [item["page_index"] for item in assets] == [1, 2]
    assert len(list(tmp_path.glob("*.png"))) == 2


@pytest.mark.asyncio
async def test_batch_endpoint_rejects_pdf_hash_mismatch_before_database(monkeypatch):
    value = manifest()
    pdf_payload = b"%PDF-1.7\nwrong source"
    value["documents"][0]["pdf_sha256"] = "b" * 64
    request = _Request(
        _Form(
            {
                "manifest": _Upload("manifest.json", json.dumps(value).encode("utf-8")),
                "files": [_Upload("Oaxaca.pdf", pdf_payload)],
            }
        )
    )
    monkeypatch.setattr(
        dashboard, "_ensure_registration_review_access", lambda _request: None
    )
    monkeypatch.setattr(
        dashboard,
        "_review_session_actor",
        lambda _request: {"user_id": "operator-1", "role": "admin"},
    )

    with pytest.raises(HTTPException) as exc_info:
        await dashboard.admit_registration_review_batch(request)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["error"] == "source_pdf_hash_mismatch"
    assert hashlib.sha256(pdf_payload).hexdigest() != "b" * 64


@pytest.mark.asyncio
async def test_batch_endpoint_admits_draft_then_recovers_exact_retry(
    monkeypatch, tmp_path
):
    pdf_payload = b"%PDF-1.7\nreviewed source"
    value = manifest()
    value["documents"][0]["pdf_sha256"] = hashlib.sha256(pdf_payload).hexdigest()
    state = _AdmissionState()

    def request():
        return _Request(
            _Form(
                {
                    "manifest": _Upload(
                        "manifest.json", json.dumps(value).encode("utf-8")
                    ),
                    "files": [_Upload("Oaxaca.pdf", pdf_payload)],
                }
            )
        )

    async def upsert(_session, _review_session, extraction, *_args, **kwargs):
        assert extraction["staff"][1]["role"] == "auxiliar"
        assert kwargs["layout_regions"]["player_page_map"] == {}
        return SimpleNamespace(id=uuid4(), content_hash="sha256:" + "9" * 64)

    monkeypatch.setattr(
        dashboard, "_ensure_registration_review_access", lambda _request: None
    )
    monkeypatch.setattr(
        dashboard,
        "_review_session_actor",
        lambda _request: {"user_id": "operator-1", "role": "admin"},
    )
    monkeypatch.setattr(dashboard, "async_session_maker", state.session)
    monkeypatch.setattr(dashboard, "_upsert_review_draft", upsert)
    monkeypatch.setattr(dashboard, "review_uploads_dir", tmp_path)
    monkeypatch.setattr(
        dashboard,
        "_render_pdf_review_asset_pages",
        lambda *_args, **_kwargs: [
            {
                "page_index": 1,
                "source_pdf_page": 1,
                "image_path": str(tmp_path / "page-01.png"),
                "sha256": "8" * 64,
                "width": 100,
                "height": 100,
            }
        ],
    )

    admitted = await dashboard.admit_registration_review_batch(request())
    recovered = await dashboard.admit_registration_review_batch(request())
    admitted_payload = json.loads(admitted.body)
    recovered_payload = json.loads(recovered.body)

    assert admitted_payload["documents"][0]["status"] == "ADMITTED_FOR_REVIEW"
    assert recovered_payload["documents"][0]["status"] == "RECOVERED"
    assert admitted_payload["documents"][0]["review_session_id"] == (
        recovered_payload["documents"][0]["review_session_id"]
    )
    assert admitted_payload["committed_teams"] == 0
    assert admitted_payload["grants_eligibility"] is False
    assert "Team" not in state.added_types
    assert "Player" not in state.added_types
    assert "TeamStaff" not in state.added_types


@pytest.mark.asyncio
async def test_batch_endpoint_removes_private_assets_when_draft_write_fails(
    monkeypatch, tmp_path
):
    pdf_payload = b"%PDF-1.7\nreviewed source"
    value = manifest()
    value["documents"][0]["pdf_sha256"] = hashlib.sha256(pdf_payload).hexdigest()
    request = _Request(
        _Form(
            {
                "manifest": _Upload("manifest.json", json.dumps(value).encode("utf-8")),
                "files": [_Upload("Oaxaca.pdf", pdf_payload)],
            }
        )
    )
    state = _AdmissionState()

    def render(session_dir, *_args, **_kwargs):
        session_dir.mkdir(parents=True)
        private_asset = session_dir / "page-01.png"
        private_asset.write_bytes(b"private rendered evidence")
        return [
            {
                "page_index": 1,
                "source_pdf_page": 1,
                "image_path": str(private_asset),
                "sha256": "8" * 64,
                "width": 100,
                "height": 100,
            }
        ]

    async def fail_upsert(*_args, **_kwargs):
        raise RuntimeError("synthetic draft failure")

    monkeypatch.setattr(
        dashboard, "_ensure_registration_review_access", lambda _request: None
    )
    monkeypatch.setattr(
        dashboard,
        "_review_session_actor",
        lambda _request: {"user_id": "operator-1", "role": "admin"},
    )
    monkeypatch.setattr(dashboard, "async_session_maker", state.session)
    monkeypatch.setattr(dashboard, "_render_pdf_review_asset_pages", render)
    monkeypatch.setattr(dashboard, "_upsert_review_draft", fail_upsert)
    monkeypatch.setattr(dashboard, "review_uploads_dir", tmp_path)

    with pytest.raises(RuntimeError, match="synthetic draft failure"):
        await dashboard.admit_registration_review_batch(request)

    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_batch_endpoint_checks_access_before_reading_private_form(monkeypatch):
    request = _Request(None)

    def deny(_request):
        raise HTTPException(status_code=403, detail="denied")

    monkeypatch.setattr(dashboard, "_ensure_registration_review_access", deny)
    with pytest.raises(HTTPException) as exc_info:
        await dashboard.admit_registration_review_batch(request)
    assert exc_info.value.status_code == 403
