"""Owner-run proposal, NOT executed during preparation.

The explicit receipt hash argument is the owner's approval attestation. This
provisions one fresh service password, never an employee password. It does not
install code, execute migrations, or start/restart any service.
"""

import argparse
import hashlib
import json
import os
import secrets
import subprocess
from pathlib import Path

RECEIPT = Path("/root/readonly-delivery-20261009/RECIBO-ACTIVACION-FMFG.json")
DIRECTORY = Path("/etc/samchat-readonly/fmfg")
ROOT = Path(__file__).resolve().parents[2]


def install_file(path: Path, data: bytes, mode: int, gid: int) -> None:
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode
    )
    try:
        os.fchown(descriptor, 0, gid)
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(descriptor)
    finally:
        os.close(descriptor)


def approved_inputs(receipt_bytes: bytes) -> tuple[bytes, dict]:
    """Load exact receipt-bound bytes before any credential or file mutation."""
    try:
        receipt = json.loads(receipt_bytes)
        files = receipt["artifacts"]["files"]
        inputs = {}
        for name in ("delegation-fmfg.proposed.json", "activation-fmfg.proposed.json"):
            relative = "deployment/readonly_gateway/" + name
            raw = (ROOT / relative).read_bytes()
            expected = files[relative]
            if (
                hashlib.sha256(raw).hexdigest() != expected["sha256"]
                or len(raw) != expected["bytes"]
            ):
                raise ValueError("Receipt-bound input mismatch")
            inputs[name] = raw
        config = json.loads(inputs["activation-fmfg.proposed.json"])
        if not isinstance(config, dict):
            raise ValueError("Invalid activation config")
        return inputs["delegation-fmfg.proposed.json"], config
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise SystemExit("APPROVED_INPUT_INTEGRITY_REQUIRED") from exc


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--approved-receipt-sha256", required=True)
    arguments = parser.parse_args()
    receipt_bytes = RECEIPT.read_bytes()
    if (
        os.getuid() != 0
        or hashlib.sha256(receipt_bytes).hexdigest()
        != arguments.approved_receipt_sha256
    ):
        raise SystemExit("OWNER_APPROVAL_REQUIRED")
    delegation, config = approved_inputs(receipt_bytes)
    if any(
        (DIRECTORY / name).exists()
        for name in (
            "database-password",
            "activation.json",
            "delegation.json",
            "admission.env",
        )
    ):
        raise SystemExit("EXISTING_ACCESS_MUST_NOT_BE_OVERWRITTEN")
    info = DIRECTORY.lstat()
    if (
        not DIRECTORY.is_dir()
        or DIRECTORY.is_symlink()
        or info.st_uid != 0
        or info.st_gid != 983
        or info.st_mode & 0o777 != 0o750
    ):
        raise SystemExit("PROTECTED_DIRECTORY_REQUIRED")
    # Separate random machine credential. Hex alphabet excludes SQL quotes.
    password = secrets.token_hex(48)
    result = subprocess.run(
        [
            "runuser",
            "-u",
            "postgres",
            "--",
            "psql",
            "-Xq",
            "-d",
            "devnous_db",
            "-v",
            "ON_ERROR_STOP=1",
        ],
        input="SET log_statement='none'; SET log_min_duration_statement=-1; SET password_encryption='scram-sha-256'; ALTER ROLE samchat_ro_fmfg PASSWORD '"
        + password
        + "';",
        text=True,
        capture_output=True,
    )
    if result.returncode:
        raise SystemExit("SERVICE_CREDENTIAL_PROVISION_FAILED")
    install_file(DIRECTORY / "database-password", password.encode(), 0o600, 0)
    config["activation_authorized"] = True
    config["approved_proposal_sha256"] = arguments.approved_receipt_sha256
    config["delegation_sha256"] = hashlib.sha256(delegation).hexdigest()
    raw = (json.dumps(config, indent=2, sort_keys=True) + "\n").encode()
    install_file(DIRECTORY / "delegation.json", delegation, 0o640, 983)
    install_file(DIRECTORY / "activation.json", raw, 0o640, 983)
    install_file(
        DIRECTORY / "admission.env",
        (
            "SAMCHAT_READONLY_ACTIVATION_SHA256="
            + hashlib.sha256(raw).hexdigest()
            + "\n"
        ).encode(),
        0o640,
        983,
    )
    print("SERVICE_ACCESS_PROVISIONED_NO_SERVICES_STARTED")


if __name__ == "__main__":
    main()
