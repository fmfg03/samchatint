"""Private catalog and retained-observation replay, without external API calls.

The analysis cache is evidence, not authority. Every reuse verifies original
CAS snapshots and still needs verifier admission, qualification and human UV.
"""

from __future__ import annotations

import base64
import hashlib
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid5

from devnous.tournaments.core.ctt_batch_qualification import (
    calculate_metrics,
    create_qualification_candidate,
    select_sample,
)

from .staff_batch_contracts import (
    canonical,
    digest,
    source_refs,
    strict_loads,
    validate_source_manifest,
)
from .staff_batch_corrections import editable_fields
from .staff_batch_observation_inputs import (
    verify_manual_observation_inputs,
    verify_observation_inputs,
)
from .staff_batch_store import BatchDenied
from .staff_batch_verification import normalize_observation
from .staff_source_admission import _open_root, _open_source, _publish_file


def analysis_identity(profile_hash, source):
    return {
        "schema": "ctt.batch.retained-analysis.v1",
        "profile_hash": profile_hash,
        "document_id": source["document_id"],
        "manifest_sha256": source["manifest_sha256"],
        "source_payload_hash": source["source_payload_hash"],
        "source_pages": source["source_pages"],
    }


class PrivateBatchAnalysisCache:
    """Sealed local receipts; a cache hit never consumes a provider budget."""

    def __init__(self, root: Path, *, maximum_bytes=8 * 1024 * 1024):
        if type(maximum_bytes) is not int or maximum_bytes < 1:
            raise ValueError("bounded analysis cache required")
        self.root, self.maximum_bytes = Path(root), maximum_bytes

    def read(self, identity):
        with ExitStack() as stack:
            directory = _open_root(self.root, stack)
            try:
                stream = _open_source(directory, digest(identity) + ".json", stack)
            except FileNotFoundError:
                return None
            envelope = strict_loads(
                stream.read(self.maximum_bytes + 1), self.maximum_bytes
            )
            if set(envelope) != {"identity", "analysis"} or canonical(
                envelope["identity"]
            ) != canonical(identity):
                raise BatchDenied("SOURCE_UNAVAILABLE")
            return envelope["analysis"]

    def retain(self, identity, analysis):
        raw = canonical({"identity": identity, "analysis": analysis})
        if len(raw) > self.maximum_bytes:
            raise BatchDenied("SOURCE_UNAVAILABLE")
        with ExitStack() as stack:
            directory = _open_root(self.root, stack)
            _publish_file(
                directory,
                digest(identity) + ".json",
                __import__("io").BytesIO(raw),
                hashlib.sha256(raw).hexdigest(),
                len(raw),
                uuid5(UUID("22222222-2222-4222-8222-222222222222"), digest(identity)),
            )


class BatchCatalogSourceProvider:
    """Account context comes exclusively from the protected SQL service."""

    def __init__(
        self,
        *,
        store,
        source_adapter,
        profiles,
        builders,
        analysis_cache,
        analysis_producer=None,
        analysis_budget_store=None,
    ):
        if (
            not profiles
            or not builders
            or any(digest(v) != k for k, v in profiles.items())
        ):
            raise ValueError("pinned batch profiles required")
        self.store, self.adapter = store, source_adapter
        self.profiles, self.builders, self.cache = profiles, builders, analysis_cache
        self.analysis_producer = analysis_producer
        self.analysis_budget_store = analysis_budget_store
        if analysis_producer is not None and analysis_budget_store is None:
            raise ValueError("separate analysis budget store required")

    @staticmethod
    def _source_scope(context, source):
        return canonical(
            {
                "schema": "ctt.batch.source-snapshot.v2",
                "tenant_id": context["tenant_id"],
                "edition_id": str(context["edition_id"]),
                "tournament_slug": source["manifest"]["edition"]["roster_slug"],
                "manifest_sha256": source["manifest_sha256"],
                "document_id": source["document_id"],
                "source_pages": source["source_pages"],
            }
        )

    async def prepare_run(self, context, manifests):
        """Metadata only: no guessed rows, model invocation or executable plan."""
        if context["profile_hash"] not in self.profiles:
            raise BatchDenied("REVIEW_BLOCKED")
        docs, all_pages, members = [], {}, []
        profile = self.profiles[context["profile_hash"]]
        run_id = UUID(str(context["run_id"]))
        for source in context["documents"]:
            manifest = validate_source_manifest(
                source["manifest"], str(context["edition_id"])
            )
            if (
                manifest["manifest_sha256"] not in manifests
                or manifest["edition"]["tenant_id"] != context["tenant_id"]
            ):
                raise BatchDenied()
            for pdf in manifest["pdfs"]:
                for number in range(1, pdf["page_count"] + 1):
                    all_pages[(pdf["sha256"], number)] = {
                        "pdf_sha256": pdf["sha256"],
                        "pdf_page": number,
                        "kind": "unknown",
                    }
            doc = next(
                d
                for d in manifest["documents"]
                if d["document_id"] == source["document_id"]
            )
            legacy = source.get("legacy", {})
            status, issues = "analysis_pending", []
            structure = doc["extraction"].get("structural_observation", {})
            expected_refs = [
                {"pdf_sha256": sha, "pdf_page": number}
                for sha, number in source_refs(doc["source_slices"])
            ]
            templates = profile.get("template_ids", [profile.get("template_id")])
            valid_structure = (
                type(structure) is dict
                and set(structure)
                == {"template_id", "category", "source_refs", "physical_slots"}
                and structure["template_id"] in templates
                and type(structure["category"]) is str
                and bool(structure["category"].strip())
                and structure["source_refs"] == expected_refs
                and type(structure["physical_slots"]) is list
                and structure["physical_slots"]
                == [s.get("slot_ref") for s in profile.get("physical_slots", [])]
            )
            if not valid_structure:
                status, issues = "exception", [
                    {"code": "STRUCTURAL_OBSERVATION_REQUIRED"}
                ]
            if legacy.get("status") == "committed" and legacy.get("result_id"):
                status = "already_captured"
            elif legacy.get("status") == "ambiguous" or (
                source.get("committed_team_id") and status != "already_captured"
            ):
                status, issues = "exception", [{"code": "LEGACY_IDENTITY_UNRESOLVED"}]
            member = {
                "member_id": str(uuid5(run_id, source["document_id"])),
                "document_id": source["document_id"],
                "manifest_sha256": manifest["manifest_sha256"],
                "source_payload_hash": source["source_payload_hash"],
                "policy_hash": next(
                    c["policy_hash"]
                    for c in context["capabilities"]
                    if c["manifest_sha256"] == manifest["manifest_sha256"]
                ),
                "session_id": source["session_id"],
                "source_slices": doc["source_slices"],
                "source_pages": source["source_pages"],
                "status": status,
                "issues": issues,
                "label": "Expediente " + source["document_id"][:8],
                "snapshot": {"source": source},
                "payload_hash": None,
                "scope_hash": None,
                "attempt_id": None,
                "auto_ready": False,
            }
            if status == "already_captured":
                member["exclusion_proof"] = {
                    "kind": "already_captured",
                    "evidence_sha256": legacy["proof_hash"],
                    "effective_at": datetime.now(timezone.utc).isoformat(),
                }
            members.append(member)
            docs.append(
                {
                    **doc,
                    "template_id": (
                        structure["template_id"] if valid_structure else "unclassified"
                    ),
                    "category": (
                        structure["category"] if valid_structure else "unclassified"
                    ),
                }
            )
        corpus_hash = digest(
            {"edition": str(context["edition_id"]), "manifests": sorted(manifests)}
        )
        return {
            "members": members,
            "documents": docs,
            "source_pages": list(all_pages.values()),
            "profile_hash": context["profile_hash"],
            "corpus_hash": corpus_hash,
            "sample_documents": list(select_sample(docs, corpus_hash=corpus_hash)),
            "source_manifest_hashes": sorted(manifests),
        }

    async def analyze(self, run):
        """Replay already retained observations. Never silently call a model.

        A separately configured, budget-admitted producer must retain missing
        receipts first; missing data is an explicit pending preparation state.
        """
        context = await self.store.preparation_context(
            UUID(run["account_id"]), UUID(run["edition_id"]), run["manifest_hashes"]
        )
        context["run_id"] = run["run_id"]
        initial = await self.prepare_run(context, run["manifest_hashes"])
        previous = {m["document_id"]: m for m in run.get("members", [])}
        analyzed, documents = [], initial["documents"]
        strata = {d["document_id"]: d for d in documents}
        for member in initial["members"]:
            source = member["snapshot"]["source"]
            old = previous.get(member["document_id"], {})
            if old.get("original_auto_ready") is True:
                retained = dict(old)
                retained["snapshot"] = old.get("frozen_snapshot", old.get("snapshot"))
                retained["source_slices"] = member["source_slices"]
                analyzed.append(retained)
                continue
            if member["status"] in {"already_captured", "exception"}:
                analyzed.append(member)
                continue
            identity = analysis_identity(run["profile_hash"], source)
            with self.adapter.open_verified_snapshot(
                self._source_scope(context, source)
            ) as snapshot:
                analysis = self.cache.read(identity)
                if analysis is None:
                    if self.analysis_producer is None:
                        raise BatchDenied("ANALYSIS_MISSING")
                    analysis = await self.analysis_producer.analyze(
                        snapshot,
                        {**source, "policy_hash": member["policy_hash"]},
                        profile=self.profiles[run["profile_hash"]],
                        run=run,
                        store=self.analysis_budget_store,
                    )
                    snapshot.verify_unchanged()
                    if (
                        type(analysis) is not dict
                        or analysis.get("document_id") != source["document_id"]
                        or analysis.get("source_binding_hash")
                        != digest(source["source_pages"])
                    ):
                        raise BatchDenied("SOURCE_UNAVAILABLE")
                    self.cache.retain(identity, analysis)
                if analysis.get("document_id") != source["document_id"] or analysis.get(
                    "source_binding_hash"
                ) != digest(source["source_pages"]):
                    raise BatchDenied("SOURCE_UNAVAILABLE")
                source_document = next(
                    d
                    for d in source["manifest"]["documents"]
                    if d["document_id"] == source["document_id"]
                )
                analysis = {
                    **analysis,
                    "source_physical_slots": source_document["extraction"][
                        "structural_observation"
                    ]["physical_slots"],
                }
                # Human proposals come only from the protected correction
                # ledger, never from a retained machine receipt.
                analysis = {
                    k: v
                    for k, v in analysis.items()
                    if k not in {"human_edits", "human_edits_hash"}
                }
                stratum = strata[source["document_id"]]
                if (
                    analysis.get(
                        "template_id",
                        self.profiles[run["profile_hash"]].get("template_id"),
                    )
                    != stratum["template_id"]
                    or analysis.get("team", {}).get("category") != stratum["category"]
                ):
                    analysis = {
                        **analysis,
                        "auto_ready": False,
                        "issues": [{"code": "SOURCE_STRATUM_MISMATCH"}],
                    }
                manual_original = strict_loads(canonical(analysis))
                if old.get("human_edits"):
                    try:
                        analysis, replay = verify_manual_observation_inputs(
                            snapshot,
                            source["source_pages"],
                            self.profiles[run["profile_hash"]],
                            manual_original,
                            records=old["human_edits"],
                            principal_id=run["principal_id"],
                        )
                    except BatchDenied:
                        analysis = {**analysis, "auto_ready": False}
                elif analysis.get("auto_ready") is True and not analysis.get("issues"):
                    try:
                        replay = verify_observation_inputs(
                            snapshot,
                            source["source_pages"],
                            self.profiles[run["profile_hash"]],
                            analysis,
                        )
                        for field in replay["expected_fields"]:
                            actual = normalize_observation(
                                replay["proposed_fields"][field], field=field
                            )
                            if any(
                                normalize_observation(r["fields"][field], field=field)
                                != actual
                                for r in replay["readings"]
                            ):
                                raise BatchDenied("REVIEW_BLOCKED")
                    except BatchDenied:
                        analysis = {
                            **analysis,
                            "auto_ready": False,
                            "issues": [{"code": "OBSERVATION_INPUTS_INVALID"}],
                        }
                if analysis.get("auto_ready") is not True or analysis.get("issues"):
                    result = {
                        **member,
                        "status": "exception",
                        "issues": analysis.get("issues")
                        or [{"code": "READER_DISAGREEMENT"}],
                        "snapshot": {
                            **member["snapshot"],
                            "analysis": analysis,
                            "editable_fields": editable_fields(
                                analysis,
                                profile=self.profiles[run["profile_hash"]],
                                source_pages=source["source_pages"],
                            ),
                        },
                    }
                else:
                    capability = next(
                        c
                        for c in context["capabilities"]
                        if c["manifest_sha256"] == source["manifest_sha256"]
                    )
                    builder_context = {
                        **context,
                        "manifest_sha256": source["manifest_sha256"],
                        "policy_hash": capability["policy_hash"],
                        "capability": capability,
                    }
                    try:
                        result = self.builders[capability["policy_hash"]].build_batch(
                            builder_context, source, analysis, snapshot
                        )
                    except ValueError:
                        result = {
                            **member,
                            "status": "exception",
                            "auto_ready": False,
                            "issues": [{"code": "BATCH_PLAN_REJECTED"}],
                            "snapshot": {
                                "editable_fields": editable_fields(
                                    analysis,
                                    profile=self.profiles[run["profile_hash"]],
                                    source_pages=source["source_pages"],
                                )
                            },
                        }
                    result["snapshot"]["source"] = source
                    result["snapshot"]["analysis"] = analysis
                    result["source_slices"] = member["source_slices"]
                snapshot.verify_unchanged()
            if old.get("human_edits"):
                result["auto_ready"] = False
                result["verification_origin"] = "human_corrected"
                result["snapshot"]["manual_original_analysis"] = manual_original
            result["snapshot"]["profile_hash"] = run["profile_hash"]
            result.update(
                template_id=strata[source["document_id"]]["template_id"],
                category=strata[source["document_id"]]["category"],
            )
            analyzed.append(result)
        cutoff = datetime.now(timezone.utc).isoformat()
        metrics = calculate_metrics(
            analyzed,
            source_pages=initial["source_pages"],
            cutoff=cutoff,
            profile_hash=run["profile_hash"],
            corpus_hash=initial["corpus_hash"],
            input_versions={},
        )
        return {
            **initial,
            "members": analyzed,
            "documents": documents,
            "sample_documents": initial["sample_documents"],
            "metrics": metrics,
        }

    async def qualify(self, run, observations):
        data = run["input_snapshot"]
        if run.get("cohort_metrics"):
            data = {**data, "metrics": run["cohort_metrics"]}
        if not data.get("metrics") or not data.get("documents"):
            raise BatchDenied("REVIEW_BLOCKED")
        candidate = create_qualification_candidate(
            data["documents"],
            corpus_hash=run["corpus_hash"],
            profile_hash=run["profile_hash"],
            cutoff=data["metrics"]["cutoff"],
            metrics=data["metrics"],
            sample_results=observations,
            input_versions=data["metrics"].get("input_versions", {}),
        )
        # The protected store hashes the retained bytes, not a self-declared
        # hash. Keep the pure helper's diagnostic hash out of the signed wire.
        candidate.pop("candidate_hash")
        candidate["observations"] = candidate["sample_results"]
        candidate["critical_false_admissions"] = candidate["sample_report"][
            "critical_false_admissions"
        ]
        return candidate

    async def verification_input(self, member):
        value = member.get("frozen_snapshot", member.get("snapshot", {}))
        scope_bytes = base64.b64decode(value["scope_bytes"], validate=True)
        with self.adapter.open_verified_snapshot(scope_bytes) as snapshot:
            analysis = value["analysis"]
            result = analysis.get("verification_input")
            if type(result) is not dict:
                raise BatchDenied("REVIEW_BLOCKED")
            if result.get("source_binding_hash") != digest(member["source_pages"]):
                raise BatchDenied("SOURCE_UNAVAILABLE")
            profile_hash = member.get("verification_profile_hash")
            if (
                type(profile_hash) is not str
                or profile_hash not in self.profiles
                or value.get("profile_hash") != profile_hash
            ):
                raise BatchDenied("REVIEW_BLOCKED")
            source_manifest = strict_loads(snapshot.manifest_json)
            source_document = next(
                d
                for d in source_manifest["documents"]
                if d["document_id"] == member["document_id"]
            )
            structure = source_document["extraction"].get("structural_observation", {})
            if structure.get("template_id") != analysis.get(
                "template_id", self.profiles[profile_hash].get("template_id")
            ) or structure.get("category") != analysis.get("team", {}).get("category"):
                raise BatchDenied("SOURCE_UNAVAILABLE")
            analysis = {
                **analysis,
                "source_physical_slots": structure.get("physical_slots"),
            }
            if member.get("human_edits"):
                original = value.get("manual_original_analysis")
                if type(original) is not dict:
                    raise BatchDenied("REVIEW_BLOCKED")
                corrected, result = verify_manual_observation_inputs(
                    snapshot,
                    member["source_pages"],
                    self.profiles[profile_hash],
                    original,
                    records=member["human_edits"],
                    principal_id=strict_loads(scope_bytes)["actor_id"],
                )
                if canonical(corrected) != canonical(analysis):
                    raise BatchDenied("REVIEW_BLOCKED")
            else:
                result = verify_observation_inputs(
                    snapshot,
                    member["source_pages"],
                    self.profiles[profile_hash],
                    analysis,
                )
            snapshot.verify_unchanged()
            return strict_loads(canonical(result))

    async def source_page(self, run, member, page_index):
        value = member.get("frozen_snapshot", member.get("snapshot", {}))
        source = value["source"]
        context = {"tenant_id": run["tenant_id"], "edition_id": run["edition_id"]}
        if type(page_index) is not int or not 1 <= page_index <= len(
            source["source_pages"]
        ):
            raise BatchDenied()
        with self.adapter.open_verified_snapshot(
            self._source_scope(context, source)
        ) as snapshot:
            page = source["source_pages"][page_index - 1]
            raw = snapshot.page_assets[UUID(page["asset_id"])]
            snapshot.verify_unchanged()
            return raw
