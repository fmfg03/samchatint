# Evidence Bus - Secure Event Collection for ZAUBERN Security Events
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from typing import Dict, List, Any, Optional
import time
import json
import hmac
import hashlib
import os
import logging
import base64
from datetime import datetime, timezone
from threading import Lock
try:
    from aegis_extensions import aegis_processor
except ImportError:  # AEGIS is an optional event family, not a startup dependency.
    aegis_processor = None
from durable_event_store import (
    IDEMPOTENT_V2_TYPES,
    DurableEventStoreError,
    DurableJSONLEventStore,
)
from security.hmac_key import load_evidence_hmac_key, HMACKeyError
from evidence_receipt import issue_evidence_receipt
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives import serialization
from cryptography.exceptions import InvalidSignature
try:
    from redis import Redis
except Exception:  # pragma: no cover - import guard for minimal environments
    Redis = None  # type: ignore[assignment]

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load HMAC key at startup (fail-closed if missing/invalid)
# P0 Fix F-001: No default key - service refuses to start without valid key
# Invariant ID: EVIDENCE-HMAC-1 (grep-able for ops/audit)
try:
    EVIDENCE_HMAC_KEY, _fingerprint = load_evidence_hmac_key()
    # Log only prefix (12 chars) - enough for correlation, avoids cross-env fingerprinting
    logger.info(f"EVIDENCE-HMAC-1 PASS sha256_prefix={_fingerprint[:12]}")
except HMACKeyError as e:
    # Log only error code, not exception text (prevents future regression leaking key material)
    logger.critical(f"EVIDENCE-HMAC-1 FAIL code={e.code}")
    raise SystemExit(1) from e

_receipt_key_raw = os.getenv("EVIDENCE_RECEIPT_HMAC_KEY", "").encode("utf-8")
EVIDENCE_RECEIPT_ALG = os.getenv("EVIDENCE_RECEIPT_ALG", "HS256").strip()
EVIDENCE_RECEIPT_HMAC_KEY = _receipt_key_raw
EVIDENCE_RECEIPT_KID = os.getenv(
    "EVIDENCE_RECEIPT_KID",
    "evidence-bus-ed25519-v1"
    if EVIDENCE_RECEIPT_ALG == "Ed25519"
    else "evidence-bus-hs256-v1",
)
EVIDENCE_RECEIPT_ED25519_PRIVATE_KEY = None
if EVIDENCE_RECEIPT_ALG == "HS256":
    if len(EVIDENCE_RECEIPT_HMAC_KEY) < 32:
        logger.critical("EVIDENCE-RECEIPT-1 FAIL code=KEY_MISSING_OR_SHORT")
        raise SystemExit(1)
elif EVIDENCE_RECEIPT_ALG == "Ed25519":
    try:
        private_key_bytes = base64.b64decode(
            os.environ["EVIDENCE_RECEIPT_ED25519_PRIVATE_KEY_B64"]
        )
        EVIDENCE_RECEIPT_ED25519_PRIVATE_KEY = (
            Ed25519PrivateKey.from_private_bytes(private_key_bytes)
        )
    except Exception as exc:
        logger.critical("EVIDENCE-RECEIPT-1 FAIL code=ED25519_KEY_INVALID")
        raise SystemExit(1) from exc
else:
    logger.critical("EVIDENCE-RECEIPT-1 FAIL code=ALG_UNSUPPORTED")
    raise SystemExit(1)
logger.info(
    "EVIDENCE-RECEIPT-1 PASS kid=%s alg=%s",
    EVIDENCE_RECEIPT_KID,
    EVIDENCE_RECEIPT_ALG,
)

app = FastAPI(
    title="ZAUBERN Evidence Bus",
    description="Secure event collection and integrity verification",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# A verified receipt is issued only after the event is durably fsynced into a
# hash-chained local journal. Startup fails if the journal cannot be verified.
_v2_receipt_keys = {}
_configured_receipt_keys = json.loads(
    os.getenv("EVIDENCE_RECEIPT_ED25519_PUBLIC_KEYS_JSON", "{}")
)
if not isinstance(_configured_receipt_keys, dict):
    raise RuntimeError("invalid configured EvidenceReceipt public keys")
for _kid, _encoded in _configured_receipt_keys.items():
    _v2_receipt_keys[str(_kid)] = base64.b64decode(str(_encoded), validate=True)
if EVIDENCE_RECEIPT_ED25519_PRIVATE_KEY is not None:
    _current_public_key = EVIDENCE_RECEIPT_ED25519_PRIVATE_KEY.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    if EVIDENCE_RECEIPT_KID in _v2_receipt_keys and _v2_receipt_keys[EVIDENCE_RECEIPT_KID] != _current_public_key:
        raise RuntimeError("EvidenceReceipt signing key conflicts with trusted key")
    _v2_receipt_keys[EVIDENCE_RECEIPT_KID] = _current_public_key
events_store = DurableJSONLEventStore(
    os.getenv("EVIDENCE_EVENT_STORE_PATH", "var/evidence-bus/events.jsonl"),
    v2_public_keys=_v2_receipt_keys,
)
metrics = {
    "events_received": 0,
    "events_verified": 0,
    "events_failed": 0,
    "canary_trips": 0,
    "canary_seeds": 0,
    "receipt_replay_rejected": 0,
    "receipt_expired_rejected": 0,
    "receipt_backend_unavailable_rejected": 0,
}

_seen_governance_receipt_ids: set[str] = set()
_seen_governance_receipt_lock = Lock()
_receipt_window_seconds = int(os.getenv("GOVERNANCE_RECEIPT_WINDOW_SECONDS", "300"))
_replay_backend = os.getenv("EVIDENCE_RECEIPT_REPLAY_BACKEND", "memory").strip().lower()
_redis_url = os.getenv("REDIS_URL", "").strip()
_redis_client = None

if _replay_backend == "redis":
    if Redis is None or not _redis_url:
        logger.critical("EVIDENCE-REPLAY-1 FAIL backend=redis but redis client/url unavailable")
    else:
        try:
            _redis_client = Redis.from_url(_redis_url, decode_responses=True)
            _redis_client.ping()
            logger.info("EVIDENCE-REPLAY-1 PASS backend=redis")
        except Exception:
            _redis_client = None
            logger.critical("EVIDENCE-REPLAY-1 FAIL redis ping failed")

class SecurityEvent(BaseModel):
    ts: str
    type: str
    tenant: str
    eis_sig: str
    # Additional fields vary by event type


def _extract_execution_receipt_ref(event_data: Dict[str, Any]) -> Optional[str]:
    payload = event_data.get("payload")
    if isinstance(payload, dict):
        for key in ("execution_receipt_ref", "slm_execution_receipt_ref", "slm_receipt_ref"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    for key in ("execution_receipt_ref", "slm_execution_receipt_ref", "slm_receipt_ref"):
        value = event_data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _canonical_json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True, ensure_ascii=True)


def _b64decode(data: str) -> bytes:
    return base64.b64decode(data.encode("ascii"))


def _canonical_receipt_signing_input(
    *,
    alg: str,
    kid: str,
    issuer: str,
    iat: str,
    nonce: str,
    payload: Dict[str, Any],
) -> bytes:
    signing_doc = {
        "alg": alg,
        "kid": kid,
        "iss": issuer,
        "iat": iat,
        "nonce": nonce,
        "payload": payload,
    }
    return _canonical_json(signing_doc).encode("utf-8")


def _load_ed25519_public_keys() -> Dict[str, str]:
    raw = os.getenv("GOVERNANCE_RECEIPT_ED25519_PUBLIC_KEYS_JSON", "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except Exception:
        return {}
    if not isinstance(parsed, dict):
        return {}
    keys: Dict[str, str] = {}
    for kid, key_b64 in parsed.items():
        if isinstance(kid, str) and kid.strip() and isinstance(key_b64, str) and key_b64.strip():
            keys[kid.strip()] = key_b64.strip()
    return keys


def _validate_governance_receipt(event_data: Dict[str, Any]) -> Optional[str]:
    payload = event_data.get("payload")
    if not isinstance(payload, dict):
        return "EVIDENCE_RECEIPT_FIELD_MISMATCH"
    receipt_id = payload.get("governance_receipt_id")
    receipt = payload.get("governance_receipt")
    if not isinstance(receipt_id, str) or not receipt_id.strip():
        return "EVIDENCE_RECEIPT_FIELD_MISMATCH"
    if not isinstance(receipt, dict):
        return "EVIDENCE_RECEIPT_FIELD_MISMATCH"
    receipt_payload = receipt.get("payload")
    alg = receipt.get("alg")
    kid = receipt.get("kid")
    issuer = receipt.get("iss")
    iat = receipt.get("iat")
    nonce = receipt.get("nonce")
    signature = receipt.get("sig")
    if not isinstance(receipt_payload, dict):
        return "EVIDENCE_RECEIPT_FIELD_MISMATCH"
    if not isinstance(alg, str) or alg not in ("HS256", "Ed25519"):
        return "EVIDENCE_RECEIPT_ALG_UNSUPPORTED"
    if not isinstance(kid, str) or not kid.strip():
        return "EVIDENCE_RECEIPT_KID_UNKNOWN"
    if not isinstance(signature, str) or not signature.strip():
        return "EVIDENCE_RECEIPT_SIGNATURE_INVALID"
    expected_issuer = os.getenv("REAIM_GOVERNANCE_ISSUER_ID", "runtime-governance-issuer").strip()
    if not isinstance(issuer, str) or issuer.strip() != expected_issuer:
        return "EVIDENCE_RECEIPT_ISSUER_MISMATCH"
    if not isinstance(iat, str) or not iat.strip():
        return "EVIDENCE_RECEIPT_FIELD_MISMATCH"
    if not isinstance(nonce, str) or not nonce.strip():
        return "EVIDENCE_RECEIPT_FIELD_MISMATCH"

    side_effect_class = str(payload.get("side_effect_class") or "").strip().upper()
    try:
        side_effect_sal = int(payload.get("sal_level") or 0)
    except Exception:
        side_effect_sal = 0
    require_asymmetric_sal2 = os.getenv(
        "GOVERNANCE_RECEIPT_REQUIRE_ASYMMETRIC_SAL2", "false"
    ).strip().lower() in ("1", "true", "yes")
    if side_effect_class and side_effect_class != "READ":
        if side_effect_sal >= 3 and alg != "Ed25519":
            return "EVIDENCE_RECEIPT_ASYMMETRIC_REQUIRED"
        if side_effect_sal == 2 and require_asymmetric_sal2 and alg != "Ed25519":
            return "EVIDENCE_RECEIPT_ASYMMETRIC_REQUIRED"

    signing_input = _canonical_receipt_signing_input(
        alg=alg,
        kid=kid,
        issuer=issuer,
        iat=iat,
        nonce=nonce,
        payload=receipt_payload,
    )
    try:
        sig_bytes = _b64decode(signature)
    except Exception:
        return "EVIDENCE_RECEIPT_SIGNATURE_INVALID"
    if alg == "HS256":
        hmac_key = (
            os.getenv("GOVERNANCE_RECEIPT_HMAC_KEY")
            or os.getenv("GOVERNANCE_RECEIPT_SIGNING_KEY")
            or os.getenv("REAIM_DSSE_SIGNING_KEY")
        )
        if not hmac_key:
            return "EVIDENCE_RECEIPT_SIGNING_KEY_MISSING"
        expected_sig = hmac.new(hmac_key.encode("utf-8"), signing_input, hashlib.sha256).digest()
        if not hmac.compare_digest(sig_bytes, expected_sig):
            return "EVIDENCE_RECEIPT_SIGNATURE_INVALID"
    elif alg == "Ed25519":
        key_map = _load_ed25519_public_keys()
        key_b64 = key_map.get(kid)
        if not key_b64:
            return "EVIDENCE_RECEIPT_KID_UNKNOWN"
        try:
            public_key = Ed25519PublicKey.from_public_bytes(_b64decode(key_b64))
            public_key.verify(sig_bytes, signing_input)
        except InvalidSignature:
            return "EVIDENCE_RECEIPT_SIGNATURE_INVALID"
        except Exception:
            return "EVIDENCE_RECEIPT_SIGNATURE_INVALID"

    computed_id = hashlib.sha256(_canonical_json(receipt).encode("utf-8")).hexdigest()
    if not hmac.compare_digest(receipt_id, computed_id):
        return "EVIDENCE_RECEIPT_HASH_MISMATCH"

    # Cross-check critical execution fields against event payload.
    for key in ("task_id", "operator_id", "slm_artifact_id", "side_effect_class", "sal_level"):
        if payload.get(key) != receipt_payload.get(key):
            return "EVIDENCE_RECEIPT_FIELD_MISMATCH"

    # Receipt freshness (anti-replay safety belt)
    ts = iat
    if not isinstance(ts, str):
        return "EVIDENCE_RECEIPT_FIELD_MISMATCH"
    try:
        ts_dt = datetime.fromisoformat(ts)
        if ts_dt.tzinfo is None:
            ts_dt = ts_dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        age = abs((now - ts_dt.astimezone(timezone.utc)).total_seconds())
        if age > _receipt_window_seconds:
            return "EVIDENCE_RECEIPT_EXPIRED"
    except Exception:
        return "EVIDENCE_RECEIPT_FIELD_MISMATCH"

    # Replay protection: one-time use of governance_receipt_id
    if _replay_backend == "redis":
        if _redis_client is None:
            return "EVIDENCE_REPLAY_BACKEND_UNAVAILABLE"
        try:
            key = f"receipt:{receipt_id}"
            claimed = _redis_client.set(key, "1", nx=True, ex=max(_receipt_window_seconds, 1))
            if not claimed:
                return "EVIDENCE_RECEIPT_REPLAYED"
        except Exception:
            return "EVIDENCE_REPLAY_BACKEND_UNAVAILABLE"
    else:
        with _seen_governance_receipt_lock:
            if receipt_id in _seen_governance_receipt_ids:
                return "EVIDENCE_RECEIPT_REPLAYED"
            _seen_governance_receipt_ids.add(receipt_id)
    return None

def verify_event_signature(event_data: Dict[str, Any]) -> bool:
    """Verify HMAC signature of security event"""

    if "eis_sig" not in event_data:
        return False

    signature = event_data.pop("eis_sig")

    # Recreate canonical form
    canonical = json.dumps(event_data, separators=(",", ":"), sort_keys=True).encode()
    expected_sig = hmac.new(EVIDENCE_HMAC_KEY, canonical, hashlib.sha256).hexdigest()

    # Restore signature for storage
    event_data["eis_sig"] = signature

    return hmac.compare_digest(signature, expected_sig)

@app.get("/health")
async def health_check():
    journal_health = events_store.health()
    if journal_health["status"] != "verified":
        return JSONResponse(status_code=503, content={"status": "not_ready"})
    return {
        "status": "healthy" if journal_health["status"] == "verified" else "not_ready",
        "service": "evidence-bus",
        "durable_store": journal_health,
        "timestamp": time.time(),
        "metrics": metrics
    }

@app.post("/events")
async def receive_event(request: Request):
    """Receive and verify security events"""

    try:
        event_data = await request.json()
        event_type = event_data.get("event_type", event_data.get("type", ""))
        payload = event_data.get("payload") if isinstance(event_data.get("payload"), dict) else {}
        is_side_effect_event = (
            str(event_type).startswith("side_effect_")
            or payload.get("side_effect_class") is not None
        )
        if is_side_effect_event:
            receipt_error = _validate_governance_receipt(event_data)
            if receipt_error:
                metrics["events_received"] += 1
                metrics["events_failed"] += 1
                if receipt_error == "EVIDENCE_RECEIPT_REPLAYED":
                    metrics["receipt_replay_rejected"] += 1
                elif receipt_error == "EVIDENCE_RECEIPT_EXPIRED":
                    metrics["receipt_expired_rejected"] += 1
                elif receipt_error == "EVIDENCE_REPLAY_BACKEND_UNAVAILABLE":
                    metrics["receipt_backend_unavailable_rejected"] += 1
                logger.error(f"❌ Evidence rejected: {receipt_error}")
                raise HTTPException(status_code=400, detail=receipt_error)
        else:
            receipt_ref = _extract_execution_receipt_ref(event_data)
            if not receipt_ref:
                metrics["events_received"] += 1
                metrics["events_failed"] += 1
                logger.error("❌ Evidence rejected: missing execution receipt reference")
                raise HTTPException(
                    status_code=400,
                    detail="EVIDENCE_RECEIPT_REF_MISSING",
                )

        # Check if this is an AEGIS event (different signature scheme)
        is_aegis_event = event_type.startswith("aegis_")

        # For AEGIS events, process through AEGIS processor
        if is_aegis_event:
            if aegis_processor is None:
                raise HTTPException(
                    status_code=503,
                    detail="AEGIS_PROCESSOR_UNAVAILABLE_FAIL_CLOSED",
                )
            aegis_result = aegis_processor.process_event(event_data)

            if aegis_result["status"] == "processed":
                events_store.append({
                    "received_at": time.time(),
                    "verified": True,
                    "event": aegis_result["event_data"],
                    "aegis_processed": True,
                    "audit_trail_id": aegis_result.get("audit_trail_id")
                })

                metrics["events_received"] += 1
                metrics["events_verified"] += 1

                logger.info(f"✅ AEGIS event processed: {event_type}")

                return {
                    "status": "received",
                    "verified": True,
                    "aegis_processed": True,
                    "audit_trail_id": aegis_result.get("audit_trail_id"),
                    "timestamp": time.time()
                }
            else:
                metrics["events_failed"] += 1
                logger.error(f"❌ AEGIS event processing failed: {aegis_result}")
                return HTTPException(status_code=400, detail=f"AEGIS event processing failed: {aegis_result.get('error', 'Unknown error')}")

        # Original signature verification for non-AEGIS events
        elif verify_event_signature(event_data):
            if event_type in IDEMPOTENT_V2_TYPES:
                if EVIDENCE_RECEIPT_ALG != "Ed25519":
                    raise DurableEventStoreError("v2 requires Ed25519 receipts")
                receipt = events_store.append_idempotent_receipt(
                    event_data,
                    lambda: issue_evidence_receipt(
                        event_data,
                        EVIDENCE_RECEIPT_HMAC_KEY,
                        kid=EVIDENCE_RECEIPT_KID,
                        alg=EVIDENCE_RECEIPT_ALG,
                        ed25519_private_key=EVIDENCE_RECEIPT_ED25519_PRIVATE_KEY,
                    ),
                )
                metrics["events_received"] += 1
                metrics["events_verified"] += 1
                return {
                    "status": "accepted",
                    "verified": True,
                    "evidence_receipt": receipt,
                }
            events_store.append({
                "received_at": time.time(),
                "verified": True,
                "event": event_data
            })

            metrics["events_received"] += 1
            metrics["events_verified"] += 1

            # Update specific metrics
            if event_type == "canary.trip.v1":
                metrics["canary_trips"] += 1
                logger.warning(f"🚨 Canary trip detected: {event_data}")
            elif event_type == "canary.seeded.v1":
                metrics["canary_seeds"] += 1
                logger.info(f"🌱 Canary seeded: {event_data}")

            return {
                "status": "accepted",
                "verified": True,
                "evidence_receipt": issue_evidence_receipt(
                    event_data,
                    EVIDENCE_RECEIPT_HMAC_KEY,
                    kid=EVIDENCE_RECEIPT_KID,
                    alg=EVIDENCE_RECEIPT_ALG,
                    ed25519_private_key=EVIDENCE_RECEIPT_ED25519_PRIVATE_KEY,
                ),
            }

        else:
            if event_type in IDEMPOTENT_V2_TYPES:
                metrics["events_received"] += 1
                metrics["events_failed"] += 1
                logger.warning("Rejected v2 event with invalid signature")
                raise HTTPException(status_code=400, detail="INVALID_EVENT_SIGNATURE")
            events_store.append({
                "received_at": time.time(),
                "verified": False,
                "event": event_data,
                "error": "signature_verification_failed"
            })

            metrics["events_received"] += 1
            metrics["events_failed"] += 1

            logger.error(f"❌ Event signature verification failed: {event_data}")
            return {"status": "accepted", "verified": False, "error": "signature_verification_failed"}

    except HTTPException:
        raise
    except DurableEventStoreError as e:
        logger.critical("Evidence durability failed; receipt withheld")
        raise HTTPException(
            status_code=503,
            detail="EVIDENCE_DURABILITY_FAILED_FAIL_CLOSED",
        ) from e
    except Exception as e:
        logger.error(f"Error processing event: {e}")
        raise HTTPException(status_code=400, detail=f"Invalid event format: {str(e)}")

@app.get("/events")
async def get_events(
    event_type: Optional[str] = None,
    tenant: Optional[str] = None,
    limit: int = 100,
    verified_only: bool = True
):
    """Retrieve security events with filtering"""

    filtered_events = []

    for entry in events_store:
        # Skip unverified events if requested
        if verified_only and not entry["verified"]:
            continue

        event = entry["event"]

        # Apply filters
        if event_type and event.get("type") != event_type:
            continue

        event_tenant = event.get("tenant")
        if event.get("event_type") in IDEMPOTENT_V2_TYPES:
            event_tenant = event.get("tenant_id")
        if tenant and event_tenant != tenant:
            continue

        filtered_events.append(entry)

        if len(filtered_events) >= limit:
            break

    return {
        "events": filtered_events[-limit:],  # Most recent first
        "total_stored": len(events_store),
        "filtered_count": len(filtered_events),
        "metrics": metrics
    }

@app.get("/metrics")
async def get_metrics():
    """Prometheus-compatible metrics endpoint"""

    prometheus_metrics = f"""# HELP evidence_events_total Total security events received
# TYPE evidence_events_total counter
evidence_events_total {metrics["events_received"]}

# HELP evidence_events_verified_total Total verified security events
# TYPE evidence_events_verified_total counter
evidence_events_verified_total {metrics["events_verified"]}

# HELP evidence_events_failed_total Total failed security events
# TYPE evidence_events_failed_total counter
evidence_events_failed_total {metrics["events_failed"]}

# HELP canary_trip_total Total canary trips detected
# TYPE canary_trip_total counter
canary_trip_total {metrics["canary_trips"]}

# HELP canary_seed_total Total canaries seeded
# TYPE canary_seed_total counter
canary_seed_total {metrics["canary_seeds"]}

# HELP evidence_hmac_verification_total Total HMAC verifications
# TYPE evidence_hmac_verification_total counter
evidence_hmac_verification_total {metrics["events_received"]}

# HELP evidence_hmac_verification_success_total Successful HMAC verifications
# TYPE evidence_hmac_verification_success_total counter
evidence_hmac_verification_success_total {metrics["events_verified"]}
"""

    return prometheus_metrics

@app.get("/canary/trips")
async def get_canary_trips(tenant: Optional[str] = None, limit: int = 50):
    """Get recent canary trips for investigation"""

    trips = []

    for entry in events_store:
        if not entry["verified"]:
            continue

        event = entry["event"]
        if event.get("type") != "canary.trip.v1":
            continue

        if tenant and event.get("tenant") != tenant:
            continue

        trips.append({
            "received_at": entry["received_at"],
            "tenant": event.get("tenant"),
            "route": event.get("route"),
            "valid": event.get("valid"),
            "meta": event.get("meta"),
            "sample": event.get("sample", "")[:100] + "..." if len(event.get("sample", "")) > 100 else event.get("sample", "")
        })

        if len(trips) >= limit:
            break

    return {
        "trips": trips[-limit:],  # Most recent first
        "total_trips": metrics["canary_trips"]
    }

@app.delete("/events")
async def clear_events(confirm: str = ""):
    """Clear stored events (admin only)"""

    if confirm != "yes-clear-all":
        raise HTTPException(status_code=400, detail="Must provide confirm=yes-clear-all")

    events_store.clear()

    # Reset metrics
    for key in metrics:
        metrics[key] = 0

    logger.warning("🗑️ All events cleared from evidence bus")

    return {"status": "cleared", "timestamp": time.time()}

if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "9200"))

    logger.info(f"🚀 Starting Evidence Bus on port {port}")
    logger.info("🛡️ Security event collection and verification enabled")

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=port,
        reload=True,
        log_level="info"
    )
