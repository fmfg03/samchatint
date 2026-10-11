"""Inactive composition: requires a protected owner-approved activation file.

Reads only an independently provisioned service credential via LoadCredential.
Never reads SamChat's environment, user's password, or arbitrary request paths.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
from typing import Any

from .asyncpg_driver import AsyncpgConnectionFactory
from .contracts import Admission, ReadError
from .daily_audit import DailyAudit
from .delegation import DelegationFile, DelegationFileBinding
from .gateway import Sources
from .live_informes import (
    LiveInformeAdapter,
    LiveInformeOwner,
    ReportDelegation,
    ReportOnlyGateway,
    load_report_delegation,
)
from .postgres import PostgresBinding
from .unix_bridge import UnixReadServer

CONFIG = Path("/etc/samchat-readonly/fmfg/activation.json")
DELEGATION = Path("/etc/samchat-readonly/fmfg/delegation.json")
AUDIT = Path("/var/lib/agentius-samchat/readonly-audit/fmfg")
SOCKET = Path("/run/agentius-samchat-readonly/fmfg.sock")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prune", action="store_true")
    parser.add_argument("--verify-metadata", action="store_true")
    arguments = parser.parse_args()
    if os.getuid() != 988 or os.getgid() != 983:
        raise ReadError("ACTIVATION_REQUIRED")
    digest = os.environ.get("SAMCHAT_READONLY_ACTIVATION_SHA256", "")
    raw = DelegationFile(DelegationFileBinding(CONFIG, digest, 0, 983))._read()
    try:
        config = json.loads(raw)
        if config["activation_authorized"] is not True:
            raise ReadError("ACTIVATION_REQUIRED")
        audit = DailyAudit(AUDIT, 988, 983)
        if arguments.prune:
            audit.prune()
            return
        source = DelegationFile(
            DelegationFileBinding(DELEGATION, config["delegation_sha256"], 0, 983)
        )

        def load() -> ReportDelegation:
            return load_report_delegation(source)

        delegation = load()
        admission = Admission(
            "production",
            delegation.installation,
            config["schema_fingerprint"],
            config["source_commit"],
            delegation.owner_version,
            hashlib.sha256(raw).hexdigest(),
        )
        admission.check()
        sources = Sources.from_manifest(
            Path(config["source_root"]), config["sources"], admission.source_commit
        )
        password_path = Path(os.environ["CREDENTIALS_DIRECTORY"]) / "database-password"

        async def connect() -> Any:
            asyncpg = importlib.import_module("asyncpg")

            return await asyncpg.connect(
                host="127.0.0.1",
                port=5432,
                database="devnous_db",
                user="samchat_ro_fmfg",
                password=password_path.read_text().strip(),
                timeout=3,
            )

        backend = LiveInformeAdapter(
            PostgresBinding(
                "production",
                delegation.installation,
                "devnous_db",
                "samchat_ro_fmfg",
                admission.schema_fingerprint,
                delegation.owner_version,
                admission.activation_receipt_sha256,
            ),
            AsyncpgConnectionFactory(connect),
            LiveInformeOwner(load),
        )
        if arguments.verify_metadata:
            backend._query(
                load().principal(), lambda grant: ("SELECT 1 AS verified", {}), 1
            )
            print("ROLE_SCHEMA_DELEGATION_VERIFIED_BUSINESS_ROWS_READ_0")
            return
        gateway = ReportOnlyGateway(admission, backend, audit, sources)
        UnixReadServer(SOCKET, gateway, lambda: load().principal(), 993).serve()
    except ReadError:
        raise
    except Exception as exc:
        raise ReadError("ACTIVATION_REQUIRED") from exc


if __name__ == "__main__":
    try:
        main()
    except ReadError as error:
        raise SystemExit(error.code) from None
