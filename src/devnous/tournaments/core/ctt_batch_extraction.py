"""Pinned two-view crop reader; proposals never grant capture authority.

Only an injected SDK client is used. Every actual SDK attempt reserves budget;
unknown usage leaves that reservation held. Cache hits consume no reservation.
"""

from __future__ import annotations

import base64
import hashlib
import inspect
import re
from collections.abc import Mapping
from datetime import date
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from devnous.copa_telmex.staff_batch_contracts import (
    BatchContractError,
    canonical,
    digest,
    source_refs,
    strict_loads,
)
from devnous.copa_telmex.staff_batch_observation_inputs import (
    derive_input_hashes,
    slot_presence_fields,
    verify_observation_inputs,
)
from devnous.copa_telmex.staff_batch_plan_builder import semantic_fields
from devnous.copa_telmex.staff_batch_verification import normalize_observation
from devnous.copa_telmex.staff_capture_geometry import verify_staff_geometry
from devnous.copa_telmex.staff_photo_store import derive_rectangular_review_photo

from .ctt_ocr_provider import (
    CttOcrMode,
    CttProviderCacheIdentity,
    CttProviderFieldCache,
    CttProviderRawField,
)
from .ctt_openai_crop_client import OpenAICttCropClient

PIPELINE = "openai.ctt.batch_pinned_crops.v1"


async def _await(value):
    return await value if inspect.isawaitable(value) else value


def _error(code):
    raise BatchContractError(code)


def _png(image):
    output = BytesIO()
    image.save(output, format="PNG", optimize=False)
    return output.getvalue()


def _rectangle(box, page):
    return dict(
        page_index=page,
        x=box[0],
        y=box[1],
        width=box[2] - box[0],
        height=box[3] - box[1],
    )


def _box(spec, image):
    return (
        round(spec["x"] * image.width),
        round(spec["y"] * image.height),
        round((spec["x"] + spec["width"]) * image.width),
        round((spec["y"] + spec["height"]) * image.height),
    )


def _geometry(raw, image, box):
    source_hash = "sha256:" + hashlib.sha256(raw).hexdigest()
    with image.crop(box) as crop:
        crop_hash = digest_bytes(
            canonical({"mode": "RGB", "size": list(crop.size)}) + crop.tobytes()
        )
    version = "samchat-identity-normalization-v1"
    coordinates = _rectangle(box, 1)
    coordinates.pop("page_index")
    geometry = {
        "original_image_hash": source_hash,
        "normalized_image_hash": source_hash,
        "coordinate_frame_image_hash": source_hash,
        "coordinate_frame": "ORIGINAL",
        "normalization_version": version,
        "normalization_transform_hash": "sha256:"
        + digest(dict(operation="identity", source_hash=source_hash, version=version)),
        "field_coordinates": coordinates,
        "crop_derivation_version": "samchat-rgb-field-crop-v1",
        "crop_hash": "sha256:" + crop_hash,
        "effective_pixels": coordinates["width"] * coordinates["height"],
    }
    if not verify_staff_geometry(
        {"source_page_hash": source_hash}, geometry, raw, max_page_bytes=16 * 1024**2
    ):
        _error("BATCH_GEOMETRY_INVALID")
    return geometry


def digest_bytes(raw):
    return hashlib.sha256(raw).hexdigest()


def _blank(image, box, profile):
    """Pixel proof is mandatory; OCR null never establishes absence."""
    threshold = profile.get("white_threshold")
    maximum = profile.get("max_nonwhite_fraction")
    if (
        type(threshold) is not int
        or not 245 <= threshold <= 255
        or type(maximum) not in (int, float)
        or not 0 <= maximum <= 0.001
    ):
        _error("BATCH_BLANK_PROFILE_REQUIRED")
    with image.crop(box) as crop, crop.convert("L") as gray:
        return sum(gray.histogram()[:threshold]) <= gray.width * gray.height * maximum


def _get(value, key):
    return value.get(key) if isinstance(value, Mapping) else getattr(value, key, None)


def _ceil_million(value):
    return (value + 999999) // 1000000


def _usage_cost(usage, config):
    """Reject incomplete usage before reconciliation; units are integer microUSD."""
    inputs, outputs = _get(usage, "input_tokens"), _get(usage, "output_tokens")
    cached = _get(_get(usage, "input_tokens_details"), "cached_tokens")
    quote = config["pricing"]
    if (
        any(type(v) is not int for v in (inputs, outputs, cached))
        or not 0 <= cached <= inputs <= quote["model_input_token_limit"]
        or not 0 <= outputs <= config["max_output_tokens"]
    ):
        _error("BATCH_PROVIDER_USAGE_UNCERTAIN")
    total = _get(usage, "total_tokens")
    if total is not None and (type(total) is not int or total != inputs + outputs):
        _error("BATCH_PROVIDER_USAGE_UNCERTAIN")
    cost = _ceil_million(
        (inputs - cached) * quote["input_per_million_micro_usd"]
        + cached * quote["cached_input_per_million_micro_usd"]
        + outputs * quote["output_per_million_micro_usd"]
    )
    if cost > config["maximum_call_units"]:
        _error("BATCH_PROVIDER_USAGE_UNCERTAIN")
    return cost


def _source_structure(snapshot, scope, profile):
    """Frozen CAS structure selects a compatible template, never OCR output."""
    manifest = strict_loads(snapshot.manifest_json)
    documents = [
        d for d in manifest["documents"] if d["document_id"] == scope["document_id"]
    ]
    if len(documents) != 1:
        _error("BATCH_SOURCE_STRUCTURE_REQUIRED")
    document = documents[0]
    structure = document["extraction"].get("structural_observation", {})
    templates = profile.get("template_ids", [profile.get("template_id")])
    expected_refs = [
        {"pdf_sha256": pdf, "pdf_page": page}
        for pdf, page in source_refs(document["source_slices"])
    ]
    if (
        type(structure) is not dict
        or set(structure)
        != {"template_id", "category", "source_refs", "physical_slots"}
        or type(templates) is not list
        or structure["template_id"] not in templates
        or (
            "template_id" in profile
            and structure["template_id"] != profile["template_id"]
        )
        or type(structure["category"]) is not str
        or not structure["category"].strip()
        or structure["source_refs"] != expected_refs
        or [
            {k: p[k] for k in ("pdf_sha256", "pdf_page")} for p in scope["source_pages"]
        ]
        != expected_refs
        or structure["physical_slots"]
        != [slot["slot_ref"] for slot in profile["physical_slots"]]
    ):
        _error("BATCH_SOURCE_STRUCTURE_REQUIRED")
    return structure


class _CropBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    results: list[CttProviderRawField] = Field(min_length=1, max_length=16)


class _GroupedReader:
    """Strict IDs/hash matching, bounded multimodal requests and private cache."""

    def __init__(self, provider, cache, config, *, reserve, reconcile, context):
        client = provider.client.with_options(max_retries=0)
        if getattr(client, "max_retries", None) != 0:
            _error("BATCH_PROVIDER_RETRY_POLICY_REQUIRED")
        self.responses = client.responses
        self.provider, self.cache, self.config = provider, cache, config
        self.reserve, self.reconcile, self.context = reserve, reconcile, context

    async def read(self, crops):
        output, missing = {}, []
        for crop in crops:
            identity = CttProviderCacheIdentity(
                document_sha256=self.context["document_id"],
                normalized_page_sha256=crop["page_hash"],
                crop_id=crop["id"],
                crop_sha256=crop["sha"],
                context_sha256=digest(self.context),
                provider="openai",
                mode=CttOcrMode.OPENAI,
                model_revision=self.provider.model_revision,
                pipeline_version=PIPELINE,
            )
            cached = self.cache.load(identity)
            if cached is None:
                missing.append((crop, identity))
            elif cached.crop_id != crop["id"] or cached.crop_sha256 != crop["sha"]:
                _error("BATCH_CACHE_BINDING_INVALID")
            else:
                output[crop["id"]] = cached
        size = self.config["batch_size"]
        for start in range(0, len(missing), size):
            chunk = missing[start : start + size]
            content: list[dict[str, Any]] = []
            for crop, _ in chunk:
                content.extend(
                    [
                        {
                            "type": "input_text",
                            "text": canonical(
                                {"crop_id": crop["id"], "crop_sha256": crop["sha"]}
                            ).decode(),
                        },
                        {
                            "type": "input_image",
                            "image_url": "data:image/png;base64,"
                            + base64.b64encode(crop["png"]).decode(),
                            "detail": "high",
                        },
                    ]
                )
            arguments = dict(
                model=self.provider.model_revision,
                instructions=(
                    "Transcribe cada recorte independientemente, conservando crop_id y "
                    "crop_sha256 exactos. No deduzcas identidades ni completes campos. "
                    "null significa vacío o ilegible, nunca ausencia acreditada. "
                    "En campos presence devuelve PRESENT sólo si hay datos visibles, "
                    "ABSENT sólo si está vacío, UNCERTAIN si es dudoso. "
                    "Fechas: conserva lo impreso, sin adivinar año. "
                    "candidates contiene únicamente alternativas visibles."
                ),
                input=[{"role": "user", "content": content}],
                text_format=_CropBatch,
                store=False,
                service_tier="default",
                max_output_tokens=self.config["max_output_tokens"],
                timeout=self.provider.timeout_seconds,
            )
            safe_request = {k: v for k, v in arguments.items() if k != "text_format"}
            if len(canonical(safe_request)) > self.config["max_request_bytes"]:
                _error("BATCH_PROVIDER_REQUEST_TOO_LARGE")
            fingerprint = digest(
                dict(context=self.context, request=safe_request, attempt=str(uuid4()))
            )
            reservation = await _await(
                self.reserve(self.config["maximum_call_units"], fingerprint)
            )
            if reservation is None:
                _error("BATCH_BUDGET_RESERVATION_REQUIRED")
            response = await self.responses.parse(**arguments)
            if getattr(response, "model", None) != self.provider.model_revision:
                _error("BATCH_PROVIDER_MODEL_UNCERTAIN")
            # Actual processing mode can differ from the requested mode. Only
            # an explicit Standard response supports this admitted price quote.
            if getattr(response, "service_tier", None) != "default":
                _error("BATCH_PROVIDER_SERVICE_TIER_UNCERTAIN")
            actual = _usage_cost(getattr(response, "usage", None), self.config)
            await _await(self.reconcile(reservation, actual))
            parsed = _CropBatch.model_validate(response.output_parsed)
            requested = {crop["id"]: crop["sha"] for crop, _ in chunk}
            results = {r.crop_id: r for r in parsed.results}
            if len(results) != len(parsed.results) or set(results) != set(requested):
                _error("BATCH_PROVIDER_RESULT_SET_INVALID")
            if any(r.crop_sha256 != requested[key] for key, r in results.items()):
                _error("BATCH_PROVIDER_RESULT_HASH_INVALID")
            for crop, identity in chunk:
                output[crop["id"]] = self.cache.save(identity, results[crop["id"]])
        return output


class BatchDocumentAnalyzer:
    """Produce JSON-only proposals from immutable source and an admitted profile.

    ``provider`` is the existing configured OpenAICttCropClient. Profile provider
    budget units are integer microUSD under an independently admitted price quote.
    Missing, inconsistent or excessive usage leaves the full reservation held.
    """

    def __init__(self, *, provider, cache: CttProviderFieldCache, layout_path: Path):
        self.provider, self.cache, self.layout_path = provider, cache, Path(layout_path)

    def _configuration(self, profile, scope, source):
        if not isinstance(self.provider, OpenAICttCropClient) or not isinstance(
            self.cache, CttProviderFieldCache
        ):
            _error("BATCH_PROVIDER_NOT_CONFIGURED")
        config = profile.get("provider", {})
        revision = self.provider.model_revision
        if not re.fullmatch(r".+-\d{4}-\d{2}-\d{2}", revision):
            _error("BATCH_PROVIDER_SNAPSHOT_REQUIRED")
        try:
            date.fromisoformat(revision[-10:])
        except ValueError:
            _error("BATCH_PROVIDER_SNAPSHOT_REQUIRED")
        if (
            config.get("name") != "openai"
            or config.get("model_revision") != self.provider.model_revision
            or config.get("pipeline_version") != PIPELINE
        ):
            _error("BATCH_PROVIDER_REVISION_MISMATCH")
        limits = {
            "batch_size": (8, 16),
            "max_output_tokens": (256, 16000),
            "max_request_bytes": (1024, 16 * 1024**2),
            "max_analysis_bytes": (1024, 64 * 1024**2),
            "maximum_call_units": (1, 100_000_000),
        }
        if any(
            type(config.get(k)) is not int or not low <= config[k] <= high
            for k, (low, high) in limits.items()
        ) or not isinstance(config.get("pricing_revision"), str):
            _error("BATCH_PROVIDER_BUDGET_REQUIRED")
        if not config["pricing_revision"].strip():
            _error("BATCH_PROVIDER_BUDGET_REQUIRED")
        quote = config.get("pricing", {})
        if (
            quote.get("revision") != config["pricing_revision"]
            or quote.get("model_revision") != self.provider.model_revision
            or quote.get("currency") != "USD"
            or quote.get("unit") != "micro_usd"
            or any(
                type(quote.get(k)) is not int or quote[k] <= 0
                for k in (
                    "input_per_million_micro_usd",
                    "output_per_million_micro_usd",
                    "model_input_token_limit",
                )
            )
            or type(quote.get("cached_input_per_million_micro_usd")) is not int
            or not 0
            <= quote["cached_input_per_million_micro_usd"]
            <= quote["input_per_million_micro_usd"]
        ):
            _error("BATCH_PROVIDER_PRICING_REQUIRED")
        worst = _ceil_million(
            quote["model_input_token_limit"] * quote["input_per_million_micro_usd"]
            + config["max_output_tokens"] * quote["output_per_million_micro_usd"]
        )
        if config["maximum_call_units"] < worst:
            _error("BATCH_PROVIDER_BUDGET_BOUND_INSUFFICIENT")
        if digest_bytes(self.layout_path.read_bytes()) != profile.get("layout_sha256"):
            _error("BATCH_LAYOUT_REVISION_MISMATCH")
        if not source.get("policy_hash"):
            _error("BATCH_POLICY_REQUIRED")
        policies = profile.get("policy_artifacts", {})
        if not policies and "policy_artifact" in profile:
            artifact = profile["policy_artifact"]
            policies = {
                digest(
                    {k: v for k, v in artifact.items() if k != "policy_hash"}
                ): artifact
            }
        matches = []
        for policy_hash, artifact in policies.items():
            if source["policy_hash"] != policy_hash:
                continue
            phase = artifact.get("phase_binding", {})
            if all(
                phase.get(k) == scope[k]
                for k in ("tenant_id", "edition_id", "manifest_sha256")
            ):
                if (
                    digest({k: v for k, v in artifact.items() if k != "policy_hash"})
                    != policy_hash
                    or artifact.get("policy_hash") != "sha256:" + policy_hash
                ):
                    _error("BATCH_POLICY_HASH_MISMATCH")
                matches.append((policy_hash, artifact))
        if len(matches) != 1:
            _error("BATCH_POLICY_SCOPE_MISMATCH")
        policy_hash, policy = matches[0]
        if (
            type(policy.get("minimum_roster_size")) is not int
            or type(policy.get("maximum_roster_size")) is not int
            or not 1
            <= policy["minimum_roster_size"]
            <= policy["maximum_roster_size"]
            <= 35
            or not isinstance(policy.get("required_staff_slots"), list)
            or any(
                type(slot) is not int or slot not in (1, 2)
                for slot in policy["required_staff_slots"]
            )
        ):
            _error("BATCH_POLICY_INVALID")
        for key in ("optional_curp_kinds", "required_photo_kinds"):
            if type(policy.get(key)) is not list or any(
                k not in {"PLAYER", "STAFF"} for k in policy[key]
            ):
                _error("BATCH_POLICY_INVALID")
        return config, policy_hash, policy

    async def analyze(
        self,
        snapshot,
        source,
        *,
        profile,
        run=None,
        store=None,
        reserve=None,
        reconcile=None,
    ):
        """Read two pinned views; missing configuration fails before any call."""
        snapshot.verify_unchanged()
        scope = strict_loads(snapshot.scope_bytes)
        if (
            scope["document_id"] != source["document_id"]
            or scope["source_pages"] != source["source_pages"]
        ):
            _error("BATCH_ANALYSIS_SOURCE_MISMATCH")
        structure = _source_structure(snapshot, scope, profile)
        config, policy_hash, policy = self._configuration(profile, scope, source)
        if reserve is None or reconcile is None:
            if store is None or run is None or run["profile_hash"] != digest(profile):
                _error("BATCH_BUDGET_RESERVATION_REQUIRED")

            async def reserve(units, fingerprint):
                reservation_id = uuid4()
                receipt = await store.budget_reserve(
                    UUID(run["run_id"]), reservation_id, units
                )
                if (
                    type(receipt) is not dict
                    or receipt.get("reservation_id") != str(reservation_id)
                    or receipt.get("maximum_units") != units
                ):
                    _error("BATCH_BUDGET_RESERVATION_REQUIRED")
                return (reservation_id, fingerprint)

            async def reconcile(reservation, actual):
                await store.budget_reconcile(reservation[0], actual, reservation[1])

        pages = scope["source_pages"]
        inventory = list(profile["observation_views"][0]["fields"])
        identities = derive_input_hashes(snapshot, pages, profile, inventory)
        images, raw_pages = {}, {}
        try:
            for page in pages:
                raw = snapshot.page_assets[UUID(page["asset_id"])]
                with Image.open(BytesIO(raw)) as image:
                    images[page["page_index"]] = image.convert("RGB")
                raw_pages[page["page_index"]] = raw
            reader = _GroupedReader(
                self.provider,
                self.cache,
                config,
                reserve=reserve,
                reconcile=reconcile,
                context=dict(
                    document_id=source["document_id"],
                    profile_hash=digest(profile),
                    policy_hash=policy_hash,
                ),
            )
            readings, raw_readings, issues = [], [], []
            for view, identity in zip(profile["observation_views"], identities):
                crops, image_bytes = [], 0
                for field, spec in sorted(view["fields"].items()):
                    image = images[spec["page_index"]]
                    with image.crop(_box(spec, image)) as crop:
                        png = _png(crop)
                    image_bytes += len(png)
                    if image_bytes > config["max_analysis_bytes"]:
                        _error("BATCH_ANALYSIS_MEMORY_LIMIT")
                    crops.append(
                        dict(
                            id=view["method"] + ":" + field,
                            sha=digest_bytes(png),
                            png=png,
                            page_hash=pages[spec["page_index"] - 1]["image_sha256"],
                        )
                    )
                results = await reader.read(crops)
                fields = {}
                for field in view["fields"]:
                    result = results[view["method"] + ":" + field]
                    try:
                        fields[field] = normalize_observation(
                            result.raw_text, field=field
                        )
                    except Exception:
                        fields[field] = result.raw_text
                        issues.append("INVALID_FIELD:" + field)
                    if result.candidates:
                        issues.append("AMBIGUOUS_FIELD:" + field)
                readings.append({**identity, "fields": fields})
                raw_readings.append(
                    dict(
                        method=view["method"],
                        input_hash=identity["input_hash"],
                        fields={
                            key: value.model_dump(mode="json")
                            for key, value in results.items()
                        },
                    )
                )
            analysis = self._assemble(
                snapshot, source, profile, policy, images, raw_pages, readings, issues
            )
            analysis["readings"] = raw_readings
            analysis["policy_hash"] = policy_hash
            analysis["template_id"] = structure["template_id"]
            if analysis["team"].get("category") != structure["category"]:
                issues.append("STRUCTURAL_CATEGORY_MISMATCH")
            expected = semantic_fields(analysis)
            for field in slot_presence_fields(profile):
                expected[field] = readings[0]["fields"][field]
            retained = [
                {**r, "fields": {k: r["fields"].get(k) for k in expected}}
                for r in readings
            ]
            analysis["verification_input"] = dict(
                expected_fields=list(expected),
                required_fields=[k for k, v in expected.items() if v is not None],
                proposed_fields=expected,
                source_binding_hash=analysis["source_binding_hash"],
                readings=retained,
            )
            if any(r["fields"][k] != expected[k] for r in retained for k in expected):
                issues.append("READINGS_DISAGREE")
            try:
                verify_observation_inputs(snapshot, pages, profile, analysis)
            except Exception:
                issues.append("SOURCE_OBSERVATION_REPLAY_FAILED")
            analysis["auto_ready"] = not issues
            snapshot.verify_unchanged()
            return strict_loads(canonical(analysis))
        finally:
            for image in images.values():
                image.close()

    def _assemble(
        self, snapshot, source, profile, policy, images, raw_pages, readings, issues
    ):
        first = profile["observation_views"][0]["fields"]
        values = readings[0]["fields"]
        team = {k[5:]: v for k, v in values.items() if k.startswith("team.")}
        analysis = dict(
            document_id=source["document_id"],
            team=team,
            players=[],
            staff=[],
            source_binding_hash=digest(source["source_pages"]),
            source_physical_slots=[s["slot_ref"] for s in profile["physical_slots"]],
            issues=issues,
        )

        def person(prefix, presence_field, kind, slot_ref=None):
            spec = first[presence_field]
            page = spec["page_index"]
            image = images[page]
            present = values.get(presence_field)
            blank = all(
                _blank(
                    images[v["fields"][presence_field]["page_index"]],
                    _box(
                        v["fields"][presence_field],
                        images[v["fields"][presence_field]["page_index"]],
                    ),
                    profile["blank_profile"],
                )
                for v in profile["observation_views"]
            )
            if present == "ABSENT" and not blank:
                present = "UNCERTAIN"
            if present not in {"PRESENT", "ABSENT"}:
                issues.append("PRESENCE_UNCERTAIN:" + presence_field)
            result = {
                key: values.get(prefix + key)
                for key in ("first_name", "last_name", "birth_date", "curp")
            }
            result.update(presence=present, source_page_index=page, photo=None)
            if slot_ref:
                result["slot_ref"] = slot_ref
            if present == "ABSENT":
                for key in ("first_name", "last_name", "birth_date", "curp"):
                    if result[key] is not None:
                        issues.append("ABSENT_WITH_IDENTITY:" + prefix + key)
                    result[key] = None
                result["absence_box"] = _rectangle(_box(spec, image), page)
                return result
            boxes = {
                key: _box(first[prefix + key], image)
                for key in ("first_name", "last_name", "birth_date", "curp")
            }
            a, b = boxes["first_name"], boxes["last_name"]
            union = min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])
            result["boxes"] = dict(
                full_name=_rectangle(union, page),
                birth_date=_rectangle(boxes["birth_date"], page),
                curp=_rectangle(boxes["curp"], page),
            )
            for key in ("first_name", "last_name", "birth_date"):
                if not result[key]:
                    issues.append("MISSING_FIELD:" + prefix + key)
            if not result["curp"] and kind not in policy["optional_curp_kinds"]:
                issues.append("MISSING_FIELD:" + prefix + "curp")
            photo_spec = first[prefix + "photo_sha256"]
            photo_box = _box(photo_spec, image)
            if not _blank(image, photo_box, profile["blank_profile"]):
                try:
                    if kind == "PLAYER":
                        photo = snapshot.derive_player_photo(slot_ref)
                        evidence = dict(mode="BATCH_PHYSICAL_SLOT", slot_ref=slot_ref)
                    else:
                        geometry = _geometry(raw_pages[page], image, photo_box)
                        photo = derive_rectangular_review_photo(
                            raw_pages[page], geometry, max_page_bytes=16 * 1024**2
                        )
                        evidence = dict(mode="SOURCE_RECTANGLE", geometry=geometry)
                    result["photo"] = {
                        k: getattr(photo, k) for k in ("sha256", "ahash", "photo_path")
                    }
                    result["photo_evidence"] = evidence
                except Exception:
                    issues.append("PHOTO_UNCERTAIN:" + prefix)
            if result["photo"] is None and kind in policy["required_photo_kinds"]:
                issues.append("PHOTO_REQUIRED:" + prefix)
            # Photo digest is pixel-derived, never predicted by an OCR model.
            for reading in readings:
                reading["fields"][prefix + "photo_sha256"] = (
                    result["photo"] or {}
                ).get("sha256")
            return result

        for slot in profile["physical_slots"]:
            index = slot.get("person_index")
            if index is None:
                # No person index may be invented from predictions in a frozen profile.
                if values[slot["presence_field"]] != "ABSENT":
                    issues.append("UNMAPPED_OCCUPIED_SLOT")
                continue
            player = person(
                f"players.{index}.", slot["presence_field"], "PLAYER", slot["slot_ref"]
            )
            if index != len(analysis["players"]) + 1:
                issues.append("NONCONTIGUOUS_PERSON_MAPPING")
            analysis["players"].append(player)
        for index, role in ((1, "director_tecnico"), (2, "auxiliar")):
            prefix = f"staff.{index}."
            staff = person(prefix, prefix + "presence", "STAFF")
            staff["role"] = role
            if values.get(prefix + "role") != role:
                issues.append("STAFF_ROLE_MISMATCH")
            if (
                staff["presence"] != "PRESENT"
                and index in policy["required_staff_slots"]
            ):
                issues.append("STAFF_REQUIRED")
            analysis["staff"].append(staff)
        if (
            not policy["minimum_roster_size"]
            <= sum(p["presence"] == "PRESENT" for p in analysis["players"])
            <= policy["maximum_roster_size"]
        ):
            issues.append("ROSTER_SIZE_INVALID")
        return analysis
