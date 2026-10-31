"""Internal gate finality producer; no HTTP surface or operational authority."""

import base64
import copy
from datetime import datetime
import time
from typing import Callable, Mapping

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from devnous.copa_telmex.staff_capture_finality_authorization import (
    DOMAIN,
    check_finality_effect,
    finality_authorization_body,
)
from devnous.copa_telmex.staff_capture_finality_effect import (
    FinalityContext,
    VerifiedFinalityBundle,
    canonical,
    deny,
    finality_event,
    members,
    parse,
    pending_rows,
    verify_finality_bundle,
)
from devnous.copa_telmex.staff_governance_receipt import verify_staff_evidence_receipt


class StaffCaptureFinalityMixin:
    """Use independently configured issuer/Bus keys and retained source bytes."""

    _effect_key: bytes
    _review_key: bytes
    _receipt_keys: Mapping[str, bytes]
    _assertion_keys: Mapping[str, bytes]
    _kid: str
    _signer: Ed25519PrivateKey
    _publish: Callable[[dict], Mapping]

    def _validate_finality_inputs(
        self,
        *,
        protected_context,
        scope,
        review_attempt_id,
        review_bytes,
        pending_effect_bytes,
        signed_review,
        preauthorization
    ):
        if type(protected_context) is not FinalityContext:
            deny()
        protected_context.verify(scope, review_attempt_id)
        original_time = int(
            datetime.fromisoformat(
                preauthorization["roster_decision"]["timestamp"].replace("Z", "+00:00")
            ).timestamp()
        )
        if original_time > protected_context.issued_at:
            deny()
        # Reuse the complete roster HMAC, field-decision, signature and exact
        # plan->pending comparison. This does not publish or consume anything.
        self.authorize_effect(
            scope=scope,
            attempt_id=review_attempt_id,
            nonce=protected_context.nonce,
            review_bytes=review_bytes,
            effect_bytes=pending_effect_bytes,
            signed_review=signed_review,
            preauthorization=preauthorization,
            issued_at=original_time,
            expires_at=protected_context.expires_at,
        )

    def finalize_rows(
        self,
        *,
        protected_context,
        scope,
        review_attempt_id,
        review_bytes,
        pending_effect_bytes,
        signed_review,
        preauthorization,
        observed_pending_rows
    ) -> VerifiedFinalityBundle:
        """Publish exact postexecution observations, not a claim of SQL commit."""
        try:
            kwargs = dict(
                protected_context=protected_context,
                scope=scope,
                review_attempt_id=review_attempt_id,
                review_bytes=review_bytes,
                pending_effect_bytes=pending_effect_bytes,
                signed_review=signed_review,
                preauthorization=preauthorization,
            )
            self._validate_finality_inputs(**kwargs)
            pending = parse(pending_effect_bytes)
            if canonical(observed_pending_rows) != canonical(pending_rows(pending)):
                deny()
            records = []
            for kind, slot, role, row in members(pending):
                self._validate_finality_inputs(**kwargs)
                event = finality_event(
                    scope=scope,
                    context=protected_context,
                    pending=pending,
                    kind=kind,
                    slot=slot,
                    role=role,
                    row=row,
                    preauthorization=preauthorization,
                    effect_key=self._effect_key,
                )
                try:
                    receipt = self._publish(copy.deepcopy(event))
                except (TimeoutError, ConnectionError, OSError):
                    # A lost response may follow fsync. Retry exactly once with
                    # the identical event/operation/timestamp, never regenerate.
                    receipt = self._publish(copy.deepcopy(event))
                if not verify_staff_evidence_receipt(
                    receipt, event, public_keys=self._receipt_keys, max_age_seconds=300
                ):
                    deny()
                self._validate_finality_inputs(**kwargs)
                records.append(
                    dict(
                        kind=kind,
                        row_id=row["id"],
                        slot=slot,
                        event=event,
                        receipt=copy.deepcopy(receipt),
                    )
                )
            bundle = VerifiedFinalityBundle(
                canonical(
                    {"schema": "ctt.capture.finality-bundle.v1", "records": records}
                )
            )
            verify_finality_bundle(
                bundle=bundle,
                scope=scope,
                context=protected_context,
                pending_effect_bytes=pending_effect_bytes,
                preauthorization=preauthorization,
                effect_key=self._effect_key,
                receipt_public_keys=self._receipt_keys,
            )
            self._validate_finality_inputs(**kwargs)
            return bundle
        except Exception:
            deny()

    def authorize_finality_effect(
        self,
        *,
        protected_context,
        scope,
        review_attempt_id,
        review_bytes,
        pending_effect_bytes,
        finality_effect_bytes,
        signed_review,
        preauthorization,
        verified_finalities,
        nonce=None,
        issued_at=None,
        expires_at=None
    ) -> bytes:
        """Sign a separate, complete activation capability after every receipt."""
        try:
            if (
                nonce is not None
                and nonce != protected_context.nonce
                or issued_at is not None
                and (
                    type(issued_at) is not int
                    or issued_at != protected_context.issued_at
                )
                or expires_at is not None
                and (
                    type(expires_at) is not int
                    or expires_at != protected_context.expires_at
                )
            ):
                deny()
            self._validate_finality_inputs(
                protected_context=protected_context,
                scope=scope,
                review_attempt_id=review_attempt_id,
                review_bytes=review_bytes,
                pending_effect_bytes=pending_effect_bytes,
                signed_review=signed_review,
                preauthorization=preauthorization,
            )
            records = check_finality_effect(
                scope=scope,
                protected_context=protected_context,
                review_bytes=review_bytes,
                pending_effect_bytes=pending_effect_bytes,
                finality_effect_bytes=finality_effect_bytes,
                signed_review=signed_review,
                preauthorization=preauthorization,
                verified_finalities=verified_finalities,
                review_key=self._review_key,
                effect_key=self._effect_key,
                receipt_public_keys=self._receipt_keys,
                assertion_public_keys=self._assertion_keys,
                now_seconds=int(time.time()),
            )
            body = finality_authorization_body(
                kid=self._kid,
                scope=scope,
                protected_context=protected_context,
                review_bytes=review_bytes,
                pending_effect_bytes=pending_effect_bytes,
                finality_effect_bytes=finality_effect_bytes,
                signed_review=signed_review,
                preauthorization=preauthorization,
                records=records,
                review_key=self._review_key,
                effect_key=self._effect_key,
            )
            signature = self._signer.sign(DOMAIN + canonical(body))
            protected_context.verify(scope, review_attempt_id)
            return canonical(
                {"body": body, "signature": base64.b64encode(signature).decode("ascii")}
            )
        except Exception:
            deny()
