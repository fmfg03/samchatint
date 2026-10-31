"""Explicit assembly for the private review process, never the public app.

Pools, pinned policy, keys and sealed directories belong to deployment
configuration. Importing this module starts nothing and grants no SQL role.
The gate package and the approved FIDO2 environment must be provided by the
operator to this private process; there is no fallback authenticator.
"""

import logging

from cryptography.hazmat.primitives import serialization

from .staff_capture_transport import CaptureUnixClient
from .staff_photo_store import PrivateReviewPhotoStore
from .staff_review_issuer_client import ReviewIssuerClient
from .staff_review_proposal_store import PostgresReviewProposalStore
from .staff_review_workflow import ReviewWorkflowService, canonical


def assemble_review_issuer(
    *,
    issuer_pool,
    nonce_pool,
    signing_kid,
    signing_key,
    channel_key,
):
    """New ingress always retains its signed proof in the issuer transaction."""
    from staff_review_issuer_v2 import PostgresReviewAssertionIssuer
    from staff_review_http_v2 import create_review_issuer_app
    from .staff_capture_transport_store import PostgresTransportNonces

    if issuer_pool is nonce_pool:
        raise ValueError("separate issuer and nonce pools required")
    logging.getLogger("fido2.server").setLevel(logging.WARNING)
    issuer = PostgresReviewAssertionIssuer(
        issuer_pool,
        signing_kid=signing_kid,
        signing_key=signing_key,
        retain_proof=True,
    )
    issuer.require_proof_retention()
    return create_review_issuer_app(
        issuer=issuer,
        channel_key=channel_key,
        consume_nonce=PostgresTransportNonces(nonce_pool),
    )


def assemble_review_workflow(
    *,
    proposal_pool,
    enrollment_pool,
    backend_pin,
    issuer_client,
    business_gate,
    capture_client,
    private_source_root,
    private_photo_root,
    review_key,
    effect_key,
    gate_public_keys,
    policy_artifact,
    expected_policy_hash,
    max_source_bytes,
    max_asset_bytes,
    max_manifest_bytes,
):
    """Create the concrete proposal→identity→gate→writer workflow.

    Enrollment and proposal connections must use separately configured SQL
    services. Neither is the public app role or the trusted capture writer.
    Protected SQL independently verifies role registration on every call.
    """
    from staff_capture_gate_v2 import StaffCaptureGateV2
    from staff_review_enrollment_v2 import PasskeyEnrollmentService
    from .staff_review_plan_builder import ReviewPlanBuilder

    if (
        type(backend_pin) is not str
        or not backend_pin
        or not isinstance(issuer_client, ReviewIssuerClient)
        or not isinstance(capture_client, CaptureUnixClient)
        or not isinstance(business_gate, StaffCaptureGateV2)
        or canonical(policy_artifact) != canonical(business_gate._policy)
        or proposal_pool is enrollment_pool
    ):
        raise ValueError("separate protected review services required")
    # python-fido2 logs credential identifiers below WARNING.
    logging.getLogger("fido2.server").setLevel(logging.WARNING)
    photos = PrivateReviewPhotoStore(private_photo_root)
    builder = ReviewPlanBuilder(
        review_key=review_key,
        policy_artifact=policy_artifact,
        expected_policy_hash=expected_policy_hash,
        photo_store=photos,
        gate=business_gate,
    )
    enrollment = PasskeyEnrollmentService(enrollment_pool, backend_pin=backend_pin)
    store = PostgresReviewProposalStore(proposal_pool, backend_pin=backend_pin)
    return ReviewWorkflowService(
        store=store,
        builder=builder,
        enrollment=enrollment,
        issuer=issuer_client,
        business_gate=business_gate,
        capture_client=capture_client,
        private_source_root=private_source_root,
        review_key=review_key,
        effect_key=effect_key,
        gate_public_keys=gate_public_keys,
        policy_artifact=policy_artifact,
        expected_policy_hash=expected_policy_hash,
        max_source_bytes=max_source_bytes,
        max_asset_bytes=max_asset_bytes,
        max_manifest_bytes=max_manifest_bytes,
        reserved_channel_secrets=(
            business_gate._signer.private_bytes(
                serialization.Encoding.Raw,
                serialization.PrivateFormat.Raw,
                serialization.NoEncryption(),
            ),
        ),
    )
