"""HTTP boundary for the SamChat registration governance gate."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from typing import Any, Dict, Mapping
from urllib import error, request as urlrequest

from fastapi import FastAPI, HTTPException

from registration_gate import (
    GateClosedError,
    adjudicate_human_field_edit,
    adjudicate_page_composition,
    adjudicate_postcommit_mutation,
    adjudicate_reprocess,
    authorize_draft_version,
    finalize,
    finalize_postcommit_mutation,
    preauthorize,
)


app = FastAPI(title="Zaubern SamChat Registration Gate", version="1.0.0")


def _required_secret(name: str) -> bytes:
    value = os.getenv(name, "").encode("utf-8")
    if len(value) < 32:
        raise RuntimeError(f"{name} must contain at least 32 bytes")
    return value


def _publish(event: Mapping[str, Any]) -> Mapping[str, Any]:
    evidence_url = os.environ["EVIDENCE_BUS_URL"].rstrip("/")
    evidence_key = _required_secret("EVIDENCE_HMAC_KEY")
    envelope = dict(event)
    envelope.setdefault("type", envelope["event_type"])
    envelope["execution_receipt_ref"] = "zaubern:registration-gate:precommit"
    canonical = json.dumps(envelope, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )
    envelope["eis_sig"] = hmac.new(evidence_key, canonical, hashlib.sha256).hexdigest()
    body = json.dumps(envelope).encode("utf-8")
    req = urlrequest.Request(
        f"{evidence_url}/events",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlrequest.urlopen(
            req, timeout=float(os.getenv("EVIDENCE_BUS_TIMEOUT_SECONDS", "5"))
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (error.URLError, ValueError) as exc:
        raise RuntimeError("Evidence Bus publication failed") from exc
    if payload.get("verified") is not True or not isinstance(
        payload.get("evidence_receipt"), dict
    ):
        raise RuntimeError("Evidence Bus did not return a verified receipt")
    return payload["evidence_receipt"]


def _run(operation, payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return operation(
            payload,
            identity_key=_required_secret("SAMCHAT_IDENTITY_BINDING_KEY"),
            receipt_key=_required_secret("EVIDENCE_RECEIPT_HMAC_KEY"),
            publish=_publish,
        )
    except GateClosedError as exc:
        raise HTTPException(
            status_code=409,
            detail={"reason_code": exc.reason_code, "message": exc.detail},
        ) from exc
    except (KeyError, TypeError, ValueError, RuntimeError) as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "reason_code": "EVIDENCE_WRITE_FAILED_FAIL_CLOSED",
                "message": str(exc),
            },
        ) from exc


@app.get("/health")
def health() -> Dict[str, Any]:
    configured = all(
        len(os.getenv(name, "").encode("utf-8")) >= 32
        for name in (
            "SAMCHAT_IDENTITY_BINDING_KEY",
            "EVIDENCE_HMAC_KEY",
            "EVIDENCE_RECEIPT_HMAC_KEY",
        )
    ) and bool(
        os.getenv("EVIDENCE_BUS_URL")
    ) and bool(
        os.getenv("EVIDENCE_RECEIPT_ED25519_PUBLIC_KEYS_JSON")
    )
    return {"status": "ready" if configured else "not_ready", "fail_closed": True}


@app.post("/v1/preauthorize")
def preauthorize_endpoint(payload: Dict[str, Any]) -> Dict[str, Any]:
    return _run(preauthorize, payload)


@app.post("/v1/finalize")
def finalize_endpoint(payload: Dict[str, Any]) -> Dict[str, Any]:
    return _run(finalize, payload)


@app.post("/v1/draft-version")
def draft_version_endpoint(payload: Dict[str, Any]) -> Dict[str, Any]:
    return _run(authorize_draft_version, payload)


@app.post("/v1/reprocess")
def reprocess_endpoint(payload: Dict[str, Any]) -> Dict[str, Any]:
    return _run(adjudicate_reprocess, payload)


@app.post("/v1/page-composition")
def page_composition_endpoint(payload: Dict[str, Any]) -> Dict[str, Any]:
    return _run(adjudicate_page_composition, payload)


@app.post("/v1/human-field-edit")
def human_field_edit_endpoint(payload: Dict[str, Any]) -> Dict[str, Any]:
    return _run(adjudicate_human_field_edit, payload)


@app.post("/v1/postcommit-mutation")
def postcommit_mutation_endpoint(payload: Dict[str, Any]) -> Dict[str, Any]:
    return _run(adjudicate_postcommit_mutation, payload)


@app.post("/v1/postcommit-mutation/finalize")
def postcommit_mutation_finality_endpoint(
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    return _run(finalize_postcommit_mutation, payload)
