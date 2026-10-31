"""Assemble the private capture runtime from trusted deployment configuration.

This factory does not create pools, credentials, roles, enrollment, admissions,
files, sockets, or a running service. It is never an HTTP request factory.
Gate accreditation and source/payload admission must be established before use.
"""

from pathlib import Path

from .staff_capture_executor import CaptureDenied, StaffCaptureExecutor
from .staff_capture_postgres import PostgresCaptureStore
from .staff_capture_private_sources import PrivateAttemptSourceProvider
from .staff_capture_source_adapter import SourceCheckAdapter
from .staff_capture_transport import CaptureUnixTransport
from .staff_capture_transport_store import PostgresTransportNonces
from .staff_signed_assertion import SignedAssertionVerifier
from .staff_photo_store import PrivateReviewPhotoStore


def assemble_capture_runtime(
    *,
    capture_pool,
    nonce_pool,
    private_source_root: Path,
    gate_public_keys,
    assertion_public_keys,
    channel_key: bytes,
    review_key: bytes,
    effect_key: bytes,
    allowed_uids: frozenset[int],
    max_source_bytes: int,
    max_asset_bytes: int,
    max_manifest_bytes: int,
    client_gid: int | None = None,
    private_photo_root: Path | None = None,
    finality_service=None,
    receipt_public_keys=None,
    database_identity: str | None = None,
) -> CaptureUnixTransport:
    """Wire the same trusted writer for all internal authenticated requests."""
    try:
        keys = (channel_key, review_key, effect_key)
        if any(type(key) is not bytes or len(key) < 32 for key in keys):
            raise ValueError("invalid private keys")
        if len(set(keys)) != 3 or not gate_public_keys or not assertion_public_keys:
            raise ValueError("separate key domains required")
        if (
            finality_service is None
            or not receipt_public_keys
            or database_identity is None
        ):
            raise ValueError("complete finality configuration required")
        for registry in (gate_public_keys, assertion_public_keys):
            if any(
                type(kid) is not str
                or not kid
                or type(key) is not bytes
                or len(key) != 32
                for kid, key in registry.items()
            ):
                raise ValueError("invalid pinned public keys")
        root = Path(private_source_root)
        if not root.is_absolute() or ".." in root.parts:
            raise ValueError("invalid private root")
        if any(
            type(value) is not int or value < 1
            for value in (
                max_source_bytes,
                max_asset_bytes,
                max_manifest_bytes,
            )
        ):
            raise ValueError("invalid resource limits")

        def source_factory(locked):
            provider = PrivateAttemptSourceProvider(
                locked, root, max_manifest_bytes=max_manifest_bytes
            )
            return SourceCheckAdapter(
                provider,
                max_source_bytes=max_source_bytes,
                max_asset_bytes=max_asset_bytes,
                max_manifest_bytes=max_manifest_bytes,
            )

        store = PostgresCaptureStore(
            capture_pool,
            source_factory,
            public_keys=gate_public_keys,
            review_key=review_key,
            effect_key=effect_key,
            photo_store=(
                PrivateReviewPhotoStore(private_photo_root)
                if private_photo_root is not None
                else None
            ),
            finality_service=finality_service,
            receipt_public_keys=receipt_public_keys,
            assertion_public_keys=assertion_public_keys,
            database_identity=database_identity,
        )
        executor = StaffCaptureExecutor(
            store, SignedAssertionVerifier(assertion_public_keys)
        )
        return CaptureUnixTransport(
            key=channel_key,
            allowed_uids=allowed_uids,
            consume_nonce=PostgresTransportNonces(nonce_pool),
            handler=executor.execute,
            client_gid=client_gid,
        )
    except Exception:
        raise CaptureDenied("capture denied") from None
