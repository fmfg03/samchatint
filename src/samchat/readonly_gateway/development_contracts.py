"""Pure development-work admission contracts, NOT an Agentius executor.

No start/resume implementation, filesystem writer, subprocess, git command or
network client is provided. These validators describe the pending integration.
This is an INTERNAL proposal contract, not the observed Agentius wire schema:
wire start/resume require expected_contract_hash + request_id, not revision.
See docs/private-plugin/expanded-read-integration-20261016.md for the verified
historical bundle, live advertised schema and explicit translation gap.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from .contracts import ReadError
from .expanded import digest


@dataclass(frozen=True)
class DevelopmentManifest:
    repository: str
    base_sha: str
    branch: str
    allowed_files: frozenset[str]
    checks: frozenset[str]
    manifest_sha256: str

    def validate(self) -> None:
        if (
            self.repository != "fmfg03/samchatint"
            or not re.fullmatch(r"[0-9a-f]{40}", self.base_sha)
            or not re.fullmatch(r"[0-9a-f]{64}", self.manifest_sha256)
            or not re.fullmatch(r"feat/connector-[a-z0-9-]{1,80}", self.branch)
            or not self.allowed_files
            or not self.checks
        ):
            raise ReadError("WORK_MANIFEST_INVALID")
        for path in self.allowed_files:
            parts = PurePosixPath(path).parts
            if (
                not parts
                or path.startswith("/")
                or "\\" in path
                or ":" in path
                or any(part in {".", ".."} or part.startswith(".") for part in parts)
                or path != str(PurePosixPath(path))
            ):
                raise ReadError("WORK_PATH_NOT_ADMITTED")


@dataclass(frozen=True)
class WorkReceipt:
    work_id: str
    owner_binding: str
    generation: str
    manifest_sha256: str
    revision: int
    state: str
    artifact_hashes: frozenset[str] = frozenset()


class DevelopmentContract:
    """Validate messages destined for a future separately admitted executor.

    `trusted_owner` and `current` must come from authenticated server storage,
    not tool arguments. The caller must atomically recheck revision/generation
    at start/resume and before returning status/artifact. This class does not
    persist a receipt or implement any transition.
    """

    def __init__(self, manifest: DevelopmentManifest) -> None:
        manifest.validate()
        self.manifest = manifest

    def create(
        self, arguments: dict[str, Any], *, trusted_owner: str
    ) -> dict[str, Any]:
        if (
            type(arguments) is not dict
            or set(arguments) != {"project", "objective", "files", "checks"}
            or arguments["project"] != "samchat-dev"
            or not trusted_owner
        ):
            raise ReadError("WORK_ARGUMENTS_INVALID")
        objective, files, checks = (
            arguments["objective"],
            arguments["files"],
            arguments["checks"],
        )
        if (
            type(objective) is not str
            or not 1 <= len(objective) <= 4000
            or type(files) is not list
            or type(checks) is not list
            or not files
            or not checks
            or any(type(x) is not str for x in files + checks)
            or not set(files) <= self.manifest.allowed_files
            or not set(checks) <= self.manifest.checks
        ):
            raise ReadError("WORK_SCOPE_NOT_ADMITTED")
        return {
            "request_hash": digest(arguments),
            "manifest": self.manifest.manifest_sha256,
            "owner_digest": digest(trusted_owner),
            "state": "validated_contract_only",
            "executor_bound": False,
            "publish": False,
            "deploy": False,
        }

    def validate_operation(
        self,
        operation: str,
        arguments: dict[str, Any],
        *,
        current: WorkReceipt,
        trusted_owner: str,
        current_generation: str,
    ) -> None:
        if (
            current.owner_binding != trusted_owner
            or not trusted_owner
            or current.generation != current_generation
            or not current_generation
            or current.manifest_sha256 != self.manifest.manifest_sha256
        ):
            raise ReadError("WORK_NOT_FOUND_OR_NOT_VISIBLE")
        required = {"work_id", "expected_revision"}
        if operation == "work_artifact":
            required.add("sha256")
        if (
            type(arguments) is not dict
            or set(arguments) != required
            or arguments["work_id"] != current.work_id
            or type(arguments["expected_revision"]) is not int
            or arguments["expected_revision"] != current.revision
        ):
            raise ReadError("WORK_STALE_OR_INVALID")
        allowed = {
            "work_start": {"prepared"},
            "work_resume": {"paused"},
            "work_cancel": {"prepared", "running", "paused"},
            "work_status": {"prepared", "running", "paused", "cancelled", "completed"},
            "work_artifact": {"completed", "paused", "cancelled"},
        }
        if operation not in allowed or current.state not in allowed[operation]:
            raise ReadError("WORK_OPERATION_NOT_ADMITTED")
        if (
            operation == "work_artifact"
            and arguments["sha256"] not in current.artifact_hashes
        ):
            raise ReadError("WORK_ARTIFACT_NOT_ADMITTED")

    @staticmethod
    def prepare_publication(
        *,
        base_sha: str,
        diff_sha256: str,
        checks_passed: bool,
        independent_review_passed: bool,
    ) -> dict[str, Any]:
        if (
            not re.fullmatch(r"[0-9a-f]{40}", base_sha)
            or not re.fullmatch(r"[0-9a-f]{64}", diff_sha256)
            or checks_passed is not True
            or independent_review_passed is not True
        ):
            raise ReadError("PUBLICATION_PREPARATION_BLOCKED")
        return {
            "base_sha": base_sha,
            "diff_sha256": diff_sha256,
            "state": "ready_for_human_review",
            "commit_executed": False,
            "push_executed": False,
            "pr_created": False,
            "deploy_executed": False,
        }
