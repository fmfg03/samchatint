"""Runtime composition pins distinct channel/authority keys; no deployment."""

import os
from types import SimpleNamespace

import pytest

from devnous.copa_telmex.staff_capture_executor import CaptureDenied
from devnous.copa_telmex.staff_capture_runtime import assemble_capture_runtime


def configuration(tmp_path):
    return dict(
        capture_pool=object(),
        nonce_pool=object(),
        private_source_root=tmp_path,
        gate_public_keys={"gate": b"g" * 32},
        assertion_public_keys={"assertion": b"a" * 32},
        channel_key=b"c" * 32,
        review_key=b"r" * 32,
        effect_key=b"e" * 32,
        allowed_uids=frozenset({os.getuid()}),
        max_source_bytes=1000000,
        max_asset_bytes=1000000,
        max_manifest_bytes=1000000,
        finality_service=SimpleNamespace(
            finalize_rows=lambda **_: None,
            authorize_finality_effect=lambda **_: None,
        ),
        receipt_public_keys={"bus": b"b" * 32},
        database_identity="isolated-copa",
    )


def test_factory_only_assembles_without_database_or_socket_side_effects(
    tmp_path,
):
    runtime = assemble_capture_runtime(**configuration(tmp_path))
    assert runtime is not None
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "change", ["keys", "public", "root", "limits", "finality", "receipt", "database"]
)
def test_untrusted_or_ambiguous_configuration_fails_closed(tmp_path, change):
    args = configuration(tmp_path)
    if change == "keys":
        args["effect_key"] = args["channel_key"]
    elif change == "public":
        args["gate_public_keys"] = {}
    elif change == "root":
        args["private_source_root"] = "relative"
    elif change == "limits":
        args["max_asset_bytes"] = 0
    elif change == "finality":
        args["finality_service"] = None
    elif change == "receipt":
        args["receipt_public_keys"] = {}
    else:
        args["database_identity"] = None
    with pytest.raises(CaptureDenied):
        assemble_capture_runtime(**args)
