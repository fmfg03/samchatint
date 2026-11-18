"""Reader startup is explicit and never probes a key or network during checks."""

import copy
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_ctt_batch_extraction import setup as _extraction_setup
from test_ctt_batch_extraction import source_fixture  # noqa: F401 - shared fixture

from devnous.copa_telmex.staff_batch_analysis_runtime import (
    create_analysis_producer,
    validate_analysis_profile,
)

setup = _extraction_setup


def configured(setup, tmp_path):
    profile = copy.deepcopy(setup.profile)
    profile["provider"]["timeout_seconds"] = 45
    cache = tmp_path / "sealed-cache"
    cache.mkdir(mode=0o700)
    return profile, Path("config/layout_ctt_2026.json").resolve(), cache


def test_profile_check_performs_no_credential_or_sdk_work(setup, tmp_path):
    profile, layout, cache = configured(setup, tmp_path)
    value = validate_analysis_profile(profile, layout_path=layout, cache_root=cache)
    assert value["pricing"]["unit"] == "micro_usd"
    assert list(cache.iterdir()) == []


def test_creation_pins_official_endpoint_disables_retries_and_preserves_existing_key(
    setup, tmp_path
):
    profile, layout, cache = configured(setup, tmp_path)
    calls = []
    sdk = SimpleNamespace()
    transport = object()

    def factory(**kwargs):
        calls.append(kwargs)
        return sdk

    analyzer, actual = create_analysis_producer(
        profile=profile,
        layout_path=layout,
        cache_root=cache,
        credential_reader=lambda: "synthetic-key-not-a-real-credential",
        sdk_factory=factory,
        http_client=transport,
    )
    assert actual is sdk and analyzer.provider.client is sdk
    assert calls == [
        {
            "api_key": "synthetic-key-not-a-real-credential",
            "base_url": "https://api.openai.com/v1",
            "max_retries": 0,
            "timeout": 45,
            "http_client": transport,
        }
    ]
    assert list(cache.iterdir()) == []


@pytest.mark.parametrize(
    "fault", ["price", "layout", "model", "timeout", "cache", "policy"]
)
def test_bad_configuration_is_denied_before_reading_credential(setup, tmp_path, fault):
    profile, layout, cache = configured(setup, tmp_path)
    if fault == "price":
        profile["provider"].pop("pricing")
    elif fault == "layout":
        profile["layout_sha256"] = "0" * 64
    elif fault == "model":
        profile["provider"]["model_revision"] = ""
    elif fault == "timeout":
        profile["provider"]["timeout_seconds"] = float("inf")
    elif fault == "cache":
        cache.chmod(0o755)
    else:
        profile["policy_artifacts"] = {}

    def forbidden():
        raise AssertionError("Credential accessed before configuration validation")

    with pytest.raises(Exception) as error:
        create_analysis_producer(
            profile=profile,
            layout_path=layout,
            cache_root=cache,
            credential_reader=forbidden,
            sdk_factory=forbidden,
            http_client=object(),
        )
    assert not isinstance(error.value, AssertionError)


@pytest.mark.parametrize("key", [None, "", "   "])
def test_missing_existing_key_fails_without_fallback_or_creation(setup, tmp_path, key):
    profile, layout, cache = configured(setup, tmp_path)
    with pytest.raises(
        ValueError, match="Existing OpenAI credential or client unavailable"
    ):
        create_analysis_producer(
            profile=profile,
            layout_path=layout,
            cache_root=cache,
            credential_reader=lambda: key,
            sdk_factory=lambda **kwargs: pytest.fail("SDK called without a key"),
            http_client=object(),
        )


def test_sdk_exception_never_exposes_credential(setup, tmp_path):
    profile, layout, cache = configured(setup, tmp_path)
    secret = "synthetic-sensitive-value"

    def failure(**kwargs):
        raise RuntimeError(secret)

    with pytest.raises(ValueError) as error:
        create_analysis_producer(
            profile=profile,
            layout_path=layout,
            cache_root=cache,
            credential_reader=lambda: secret,
            sdk_factory=failure,
            http_client=object(),
        )
    assert secret not in str(error.value) and error.value.__suppress_context__
