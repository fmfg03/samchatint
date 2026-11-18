"""Explicit, disabled-by-default public transport for protected batch review."""

import httpx

from .staff_batch_operations_channel import BatchOperationsClient
from .staff_review_operations_channel import StaffReviewClientSettings, _read_key


def configure_batch_review_client(app, settings: StaffReviewClientSettings):
    """Install a dedicated transport; configuration never grants capture rights."""
    if not isinstance(settings, StaffReviewClientSettings):
        raise ValueError("Invalid batch review configuration")
    if (
        type(settings.enabled) is not bool
        or getattr(app.state, "ctt_batch_review_client", None) is not None
    ):
        raise ValueError("Invalid batch review configuration")
    app.state.ctt_batch_review_client = None
    app.state.ctt_batch_review_transport = None
    if not settings.enabled:
        return None
    if settings.key_path is None:
        raise ValueError("Batch review channel key is required")
    key = _read_key(settings.key_path)
    transport = httpx.AsyncClient(trust_env=False, follow_redirects=False)
    client = BatchOperationsClient(
        base_url=settings.base_url,
        channel_key=key,
        http_client=transport,
        timeout_seconds=settings.timeout_seconds,
    )
    app.state.ctt_batch_review_transport = transport
    app.state.ctt_batch_review_client = client
    return client


async def close_batch_review_client(app):
    """Close only this feature's transport during application shutdown."""
    transport = getattr(app.state, "ctt_batch_review_transport", None)
    app.state.ctt_batch_review_client = None
    app.state.ctt_batch_review_transport = None
    if transport is not None:
        await transport.aclose()
