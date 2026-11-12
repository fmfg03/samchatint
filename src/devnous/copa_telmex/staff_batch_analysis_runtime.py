"""Explicit OpenAI reader composition; validation never reads a credential."""

import math
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace

from devnous.tournaments.core.ctt_batch_extraction import BatchDocumentAnalyzer
from devnous.tournaments.core.ctt_ocr_provider import CttProviderFieldCache
from devnous.tournaments.core.ctt_openai_crop_client import OpenAICttCropClient

from .staff_source_admission import _open_root


def validate_analysis_profile(profile, *, layout_path, cache_root):
    """Check exact quote/policies/layout and sealed cache before SDK creation."""
    config = profile.get("provider", {})
    model = config.get("model_revision")
    timeout = config.get("timeout_seconds")
    if (
        type(model) is not str
        or not model.strip()
        or type(timeout) not in (int, float)
        or not math.isfinite(timeout)
        or not 0 < timeout <= 120
    ):
        raise ValueError("Explicit model and bounded timeout required")
    with ExitStack() as stack:
        _open_root(Path(cache_root), stack)
    provider = OpenAICttCropClient(
        SimpleNamespace(), model_revision=model, timeout_seconds=timeout
    )
    analyzer = BatchDocumentAnalyzer(
        provider=provider,
        cache=CttProviderFieldCache(Path(cache_root)),
        layout_path=Path(layout_path),
    )
    policies = profile.get("policy_artifacts", {})
    if type(policies) is not dict or not policies:
        raise ValueError("Exact policy artifacts required")
    for policy_hash, policy in policies.items():
        phase = policy.get("phase_binding", {})
        scope = {
            key: phase.get(key)
            for key in ("tenant_id", "edition_id", "manifest_sha256")
        }
        if any(type(value) is not str or not value for value in scope.values()):
            raise ValueError("Complete source-bound policy required")
        analyzer._configuration(profile, scope, {"policy_hash": policy_hash})
    return config


def create_analysis_producer(
    *, profile, layout_path, cache_root, credential_reader, sdk_factory, http_client
):
    """Read the existing key only for an explicitly enabled, validated reader.

    The caller owns the client lifecycle. No request, cache write, new key or
    persistent secret write occurs here. Tests inject a non-network SDK factory.
    """
    config = validate_analysis_profile(
        profile, layout_path=layout_path, cache_root=cache_root
    )
    try:
        key = credential_reader()
        if type(key) is not str or not key.strip():
            raise ValueError
        sdk = sdk_factory(
            api_key=key,
            base_url="https://api.openai.com/v1",
            max_retries=0,
            timeout=config["timeout_seconds"],
            http_client=http_client,
        )
    except Exception:
        raise ValueError("Existing OpenAI credential or client unavailable") from None
    provider = OpenAICttCropClient(
        sdk,
        model_revision=config["model_revision"],
        timeout_seconds=config["timeout_seconds"],
    )
    analyzer = BatchDocumentAnalyzer(
        provider=provider,
        cache=CttProviderFieldCache(Path(cache_root)),
        layout_path=Path(layout_path),
    )
    return analyzer, sdk


def create_structural_producer(
    *, profile, cache_root, credential_reader, sdk_factory, http_client
):
    """Compose only the separately admitted pre-document observation reader."""
    from devnous.tournaments.core.ctt_batch_structural_extraction import (
        StructuralPageAnalyzer,
        validate_structural_profile,
    )

    from .staff_batch_source_service import PrivateBatchAnalysisCache

    config = validate_structural_profile(profile)
    with ExitStack() as stack:
        _open_root(Path(cache_root), stack)
    try:
        key = credential_reader()
        if type(key) is not str or not key.strip():
            raise ValueError
        sdk = sdk_factory(
            api_key=key,
            base_url="https://api.openai.com/v1",
            max_retries=0,
            timeout=config["timeout_seconds"],
            http_client=http_client,
        )
    except Exception:
        raise ValueError("Existing OpenAI credential or client unavailable") from None
    return (
        StructuralPageAnalyzer(
            provider=OpenAICttCropClient(
                sdk,
                model_revision=config["model_revision"],
                timeout_seconds=config["timeout_seconds"],
            ),
            cache=PrivateBatchAnalysisCache(
                Path(cache_root), maximum_bytes=config["max_analysis_bytes"]
            ),
        ),
        sdk,
    )
