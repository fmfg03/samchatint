"""Private source-to-batch orchestration with durable SQL state and recovery.

Requests propose preparation and genuine WebAuthn input. They cannot choose a
signer, policy, pool, verifier implementation, or an executable child proof.
"""

from __future__ import annotations

import asyncio
import base64
import copy
import hmac
import inspect
import re
import secrets
from contextlib import suppress
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from .staff_batch_contracts import canonical, digest
from .staff_batch_store import BatchConflict, BatchDenied

_SHA = re.compile(r"[0-9a-f]{64}\Z")


def _uuid(value):
    try:
        result = UUID(value) if type(value) is str else value
        if (
            type(result) is not UUID
            or result.int == 0
            or (type(value) is str and str(result) != value)
        ):
            raise ValueError
        return result
    except (TypeError, ValueError, AttributeError) as exc:
        raise BatchDenied() from exc


def _keys(value, keys):
    if type(value) is not dict or set(value) != set(keys):
        raise BatchDenied()


def _version(value):
    if type(value) is not int or value < 1:
        raise BatchDenied()
    return value


async def _await(value):
    return await value if inspect.isawaitable(value) else value


def _canonical_snapshot(member):
    value = copy.deepcopy(member)
    snapshot = value.get("snapshot")
    if type(snapshot) is dict and type(snapshot.get("raw_snapshot")) is dict:
        raw = canonical(snapshot["raw_snapshot"])
        snapshot["raw_snapshot_bytes"] = base64.b64encode(raw).decode("ascii")
    return value


class BatchReviewWorkflowService:
    """Concrete private workflow; assembly provides admitted real dependencies."""

    def __init__(
        self,
        *,
        store,
        source_provider,
        verifier,
        issuer=None,
        capture_adapter=None,
        worker_store=None,
        capture_enabled=False,
        worker_id=None,
        reserved_channel_secrets=(),
    ):
        if any(
            not callable(getattr(source_provider, name, None))
            for name in ("prepare_run", "analyze", "qualify", "source_page")
        ):
            raise ValueError("concrete batch source provider required")
        if capture_enabled and (
            issuer is None or capture_adapter is None or worker_store is None
        ):
            raise ValueError("complete private batch capture assembly required")
        self.store, self.sources, self.verifier = store, source_provider, verifier
        self.issuer, self.capture = issuer, capture_adapter
        self.worker_store, self.capture_enabled = worker_store, capture_enabled
        self.worker_id = _uuid(worker_id or uuid4())
        self._reserved = tuple(reserved_channel_secrets)
        self._tasks = []
        self._stopping = False
        self.last_dispatch_error = None

    def validate_channel_key(self, key):
        if (
            type(key) is not bytes
            or not 32 <= len(key) <= 64
            or any(hmac.compare_digest(key, item) for item in self._reserved)
        ):
            raise ValueError("separate private batch channel key required")
        self.verifier.validate_channel_key(key)

    async def catalog(self, account_id):
        return await self.store.catalog(_uuid(account_id))

    async def create_run(self, account_id, payload):
        _keys(payload, {"edition_id", "source_manifest_hashes", "idempotency_key"})
        account, edition = _uuid(account_id), _uuid(payload["edition_id"])
        key = _uuid(payload["idempotency_key"])
        hashes = payload["source_manifest_hashes"]
        if (
            type(hashes) is not list
            or not 1 <= len(hashes) <= 16
            or any(type(v) is not str or not _SHA.fullmatch(v) for v in hashes)
            or len(set(hashes)) != len(hashes)
        ):
            raise BatchDenied()
        context = await self.store.preparation_context(account, edition, hashes)
        run_id = uuid5(
            NAMESPACE_URL, f"ctt-batch-run:{context['backend_pin']}:{account}:{key}"
        )
        context = {**context, "run_id": str(run_id)}
        # Metadata only. A durable run must exist before any provider call can
        # reserve its budget; source_provider.analyze runs in the private queue.
        initial = await _await(self.sources.prepare_run(context, hashes))
        initial = {
            **initial,
            "edition_id": str(edition),
            "source_manifest_hashes": hashes,
            "idempotency_key": str(key),
            "request_hash": digest(payload),
        }
        initial["members"] = [
            {
                **m,
                "member_id": str(uuid5(run_id, m["document_id"])),
                "status": "analysis_pending",
                "payload_hash": None,
                "scope_hash": None,
                "snapshot": None,
                "attempt_id": None,
            }
            for m in initial["members"]
        ]
        initial["input_hash"] = digest(
            {k: v for k, v in initial.items() if k != "input_hash"}
        )
        return await self.store.create_run(account, run_id, initial)

    async def view(self, account_id, run_id, offset=0, limit=50):
        if (
            type(offset) is not int
            or offset < 0
            or type(limit) is not int
            or not 1 <= limit <= 100
        ):
            raise BatchDenied()
        return await self.store.view(_uuid(account_id), _uuid(run_id), offset, limit)

    async def sample(self, account_id, run_id, payload):
        _keys(payload, {"expected_run_version", "observations"})
        version = _version(payload["expected_run_version"])
        observations = payload["observations"]
        if type(observations) is not list or not observations:
            raise BatchDenied()
        seen = set()
        for item in observations:
            _keys(item, {"document_id", "outcome", "notes"})
            if (
                type(item["document_id"]) is not str
                or not _SHA.fullmatch(item["document_id"])
                or item["document_id"] in seen
                or item["outcome"]
                not in {"matches", "critical_error", "exception_confirmed"}
                or type(item["notes"]) is not str
                or len(item["notes"]) > 2000
            ):
                raise BatchDenied()
            seen.add(item["document_id"])
        account, run = _uuid(account_id), _uuid(run_id)
        frozen = await self.store.private_run(account, run)
        if frozen["version"] != version or frozen["status"] != "prepared":
            raise BatchConflict()
        candidate = await _await(self.sources.qualify(frozen, observations))
        # Only a configured private verifier can produce these exact signatures.
        proofs = await self.verifier.attest_members(frozen, candidate)
        return await self.store.register_sample(
            account, run, version, canonical(candidate), proofs
        )

    async def prepare(self, account_id, run_id, payload):
        _keys(payload, {"expected_run_version", "member_ids", "idempotency_key"})
        version = _version(payload["expected_run_version"])
        key = _uuid(payload["idempotency_key"])
        ids = payload["member_ids"]
        if type(ids) is not list or not 1 <= len(ids) <= 50:
            raise BatchDenied()
        selected = [_uuid(value) for value in ids]
        if len(set(selected)) != len(selected):
            raise BatchDenied()
        account, run = _uuid(account_id), _uuid(run_id)
        frozen = await self.store.private_run(account, run)
        if frozen["version"] != version or not frozen.get("qualification"):
            raise BatchConflict()
        # Revalidate retained observations and current source/key admission;
        # preserve the original qualification/cohort and human sample.
        proofs = await self.verifier.attest_members(
            frozen, frozen["qualification"]["candidate"]
        )
        await self.store.refresh_verifications(account, run, proofs)
        frozen = await self.store.private_run(account, run)
        members = {m["member_id"]: m for m in frozen["members"]}
        chosen = [members.get(str(value)) for value in selected]
        if any(m is None or m["status"] != "ready" for m in chosen):
            raise BatchDenied("REVIEW_BLOCKED")
        chosen_members = [m for m in chosen if m is not None]
        manifests = {m["manifest_sha256"] for m in chosen_members}
        policies = {m["policy_hash"] for m in chosen_members}
        if len(manifests) != 1 or len(policies) != 1:
            raise BatchDenied()
        block = uuid5(run, f"ctt-batch-block:{key}")
        qualification = frozen["qualification"]
        root = {
            "schema": "ctt.batch.review-root.v1",
            "run_id": str(run),
            "block_id": str(block),
            "principal_id": frozen["principal_id"],
            "tenant_id": frozen["tenant_id"],
            "edition_id": frozen["edition_id"],
            "corpus_hash": frozen["corpus_hash"],
            "profile_hash": frozen["profile_hash"],
            "manifest_sha256": next(iter(manifests)),
            "policy_hash": next(iter(policies)),
            "qualification_hash": qualification["candidate_hash"],
            "max_execution_seconds": 3600,
            "members": [
                {
                    k: m[k]
                    for k in (
                        "member_id",
                        "document_id",
                        "payload_hash",
                        "scope_hash",
                        "verification_hash",
                        "human_edits_hash",
                    )
                }
                for m in chosen_members
            ],
            "exclusions_hash": digest(
                [
                    {
                        "member_id": m["member_id"],
                        "status": m["status"],
                        "issues": m["issues"],
                    }
                    for m in frozen["members"]
                    if m["member_id"] not in set(map(str, selected))
                ]
            ),
        }
        return await self.store.prepare_block(
            account, run, block, version, selected, canonical(root)
        )

    async def member(self, account_id, run_id, member_id):
        return await self.store.member(
            _uuid(account_id), _uuid(run_id), _uuid(member_id)
        )

    async def propose_corrections(self, account_id, run_id, member_id, payload):
        _keys(payload, {"expected_version", "edits"})
        edits = payload["edits"]
        if type(edits) is not list or not 1 <= len(edits) <= 200:
            raise BatchDenied()
        fields = set()
        for edit in edits:
            _keys(edit, {"field_id", "value"})
            from .staff_batch_corrections import validate_edit_value

            if type(edit["field_id"]) is not str or edit["field_id"] in fields:
                raise BatchDenied()
            validate_edit_value(edit["field_id"], edit["value"])
            fields.add(edit["field_id"])
        return await self.store.propose_corrections(
            _uuid(account_id),
            _uuid(run_id),
            _uuid(member_id),
            _version(payload["expected_version"]),
            edits,
        )

    async def report_quality_issue(self, account_id, run_id, member_id, payload):
        _keys(payload, {"expected_version", "reason"})
        if type(payload["reason"]) is not str or not 1 <= len(payload["reason"]) <= 500:
            raise BatchDenied()
        return await self.store.report_quality_issue(
            _uuid(account_id),
            _uuid(run_id),
            _uuid(member_id),
            _version(payload["expected_version"]),
            payload["reason"],
        )

    async def challenge(self, account_id, block_id, payload):
        _keys(payload, {"expected_block_version", "confirmed"})
        if not self.capture_enabled or payload["confirmed"] is not True:
            raise BatchDenied("REVIEW_BLOCKED")
        version = _version(payload["expected_block_version"])
        account, block = _uuid(account_id), _uuid(block_id)
        value = await self.store.create_challenge(
            account, block, version, uuid4(), secrets.token_bytes(32)
        )

        def url(value):
            return (
                base64.urlsafe_b64encode(base64.b64decode(value, validate=True))
                .rstrip(b"=")
                .decode()
            )

        value["options"] = {
            "challenge": url(value.pop("challenge")),
            "rpId": "sam.chat",
            "allowCredentials": [
                {"type": "public-key", "id": url(value.pop("credential_id"))}
            ],
            "timeout": 300000,
            "userVerification": "required",
        }
        return value

    async def assertion(self, account_id, block_id, payload):
        _keys(payload, {"attempt_id", "assertion"})
        if not self.capture_enabled or type(payload["assertion"]) is not dict:
            raise BatchDenied("REVIEW_BLOCKED")
        account, block, challenge = (
            _uuid(account_id),
            _uuid(block_id),
            _uuid(payload["attempt_id"]),
        )
        context = await self.store.private_block(account, block)
        if context.get("attempt_id") != str(challenge):
            raise BatchDenied()
        await self.issuer.issue_consent(challenge, canonical(payload["assertion"]))
        return await self.store.recover_consent(account, block)

    async def _transition(self, account_id, block_id, payload, action):
        _keys(payload, {"expected_version", "idempotency_key"})
        _uuid(payload["idempotency_key"])
        return await self.store.transition(
            _uuid(account_id),
            _uuid(block_id),
            _version(payload["expected_version"]),
            action,
        )

    async def pause(self, account_id, block_id, payload):
        return await self._transition(account_id, block_id, payload, "pause")

    async def resume(self, account_id, block_id, payload):
        return await self._transition(account_id, block_id, payload, "resume")

    async def revoke(self, account_id, block_id, payload):
        return await self._transition(account_id, block_id, payload, "revoke")

    async def attempt(self, account_id, block_id, attempt_id):
        return await self.store.attempt(
            _uuid(account_id), _uuid(block_id), _uuid(attempt_id)
        )

    async def source_page(self, account_id, run_id, document_id, page_index):
        if (
            type(document_id) is not str
            or not _SHA.fullmatch(document_id)
            or type(page_index) is not int
            or not 1 <= page_index <= 4
        ):
            raise BatchDenied()
        run = await self.store.private_run(_uuid(account_id), _uuid(run_id))
        matches = [m for m in run["members"] if m["document_id"] == document_id]
        if len(matches) != 1:
            raise BatchDenied()
        return await _await(self.sources.source_page(run, matches[0], page_index))

    async def source_asset(self, account_id, session_id, asset_id):
        context = await self.store.source_asset_context(
            _uuid(account_id), _uuid(session_id), _uuid(asset_id)
        )
        return await self.source_page(
            account_id, context["run_id"], context["document_id"], context["page_index"]
        )

    async def analysis_once(self):
        job = await self.store.claim_analysis(self.worker_id)
        if job["status"] != "leased":
            return job
        run_id, account = _uuid(job["run_id"]), _uuid(job["account_id"])
        stop = asyncio.Event()

        async def heartbeat():
            while not stop.is_set():
                await asyncio.sleep(30)
                if not stop.is_set():
                    await self.store.analysis_heartbeat(
                        run_id, self.worker_id, job["lease_epoch"]
                    )

        pulse = asyncio.create_task(heartbeat())
        try:
            run = await self.store.private_run(account, run_id)
            analyzed = await _await(self.sources.analyze(run))
            analyzed = {**run["input_snapshot"], **analyzed}
            analyzed["members"] = [_canonical_snapshot(m) for m in analyzed["members"]]
            analyzed["input_hash"] = digest(
                {k: v for k, v in analyzed.items() if k != "input_hash"}
            )
            return await self.store.complete_preparation(
                account,
                run_id,
                run["version"],
                self.worker_id,
                job["lease_epoch"],
                analyzed,
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            code = getattr(error, "code", "REVIEW_BLOCKED")
            if code not in {"SOURCE_UNAVAILABLE", "SCOPE_DENIED", "BUDGET_UNAVAILABLE"}:
                code = "REVIEW_BLOCKED"
            self.last_dispatch_error = code
            return await self.store.block_analysis(
                run_id, self.worker_id, job["lease_epoch"], code
            )
        finally:
            stop.set()
            pulse.cancel()
            with suppress(asyncio.CancelledError):
                await pulse

    async def dispatch_once(self):
        if not self.capture_enabled:
            raise BatchDenied("REVIEW_BLOCKED")
        job = await self.worker_store.claim(self.worker_id)
        if job["status"] == "recovery_required":
            job = await self.worker_store.recover_job(
                self.worker_id, _uuid(job["attempt_id"]), job["lease_epoch"]
            )
        if job["status"] != "leased":
            return job
        try:

            async def issue_proof():
                return await self.issuer.issue_child(
                    _uuid(job["consent_id"]),
                    _uuid(job["member_id"]),
                    self.worker_id,
                    job["lease_epoch"],
                )

            result = await self.capture.prepare_and_capture(
                job, proof_factory=issue_proof
            )
            if type(result) is not dict or result.get("status") != "committed":
                raise BatchDenied("REVIEW_BLOCKED")
            return result
        except asyncio.CancelledError:
            raise
        except Exception:
            # Never create another operation or claim success after a lost reply.
            return await self.worker_store.mark_uncertain(
                self.worker_id, _uuid(job["attempt_id"]), job["lease_epoch"]
            )

    def start(self):
        if self._tasks:
            raise ValueError("batch dispatcher already started")
        self._stopping = False

        async def loop(call):
            while not self._stopping:
                try:
                    await call()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self.last_dispatch_error = "REVIEW_BLOCKED"
                await asyncio.sleep(1)

        self._tasks.append(asyncio.create_task(loop(self.analysis_once)))
        if self.capture_enabled:
            self._tasks.append(asyncio.create_task(loop(self.dispatch_once)))

    async def aclose(self):
        self._stopping = True
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with suppress(asyncio.CancelledError):
                await task
        self._tasks.clear()
