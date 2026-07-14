"""Structured OpenAI Responses extraction for CTT registration forms."""

from __future__ import annotations

import base64
import hashlib
import io
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Type, TypeVar

from PIL import Image, ImageDraw
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .ctt_ocr_contract import (
    SCHEMA_VERSION,
    SHA256_PATTERN,
    CttFieldEvidence,
    CttFieldName,
    CttFieldObservation,
    CttPlayerFields,
    CttRegistrationDraft,
    CttSlotDraft,
    CttTeamDraft,
    CttTeamFields,
)
from .ctt_slot_montage import (
    CttSlotBatch,
    CttSlotCrop,
    build_ctt_slot_batches,
    extract_slots_from_normalized_page,
)
from .ocr_integrity import clamp_box, normalize_ctt_template_image

DEFAULT_CTT_RESPONSES_MODEL = "gpt-5.6-terra"
CTT_RESPONSES_PIPELINE_VERSION = "ctt.responses.v1"
EXPECTED_SLOT_NUMBERS = tuple(range(1, 21))
PAGE_SIDES = ("front", "back")

HEADER_LAYOUT_TO_FIELD = {
    "equipo_nombre": CttFieldName.TEAM_NAME,
    "categoria": CttFieldName.CATEGORY,
    "rama": CttFieldName.GENDER,
    "liga": CttFieldName.LEAGUE,
    "representante_nombre": CttFieldName.REPRESENTATIVE_NAME,
    "correo": CttFieldName.EMAIL,
    "estado": CttFieldName.STATE,
    "municipio": CttFieldName.MUNICIPALITY,
}

HEADER_PROMPT = """
Transcribe el encabezado de una cedula de inscripcion de Copa Telmex Telcel.
La primera imagen da contexto de pagina; la segunda amplia y etiqueta cada campo.
Devuelve exactamente los ocho campos del esquema. raw_text debe conservar lo
visible, sin completar ni corregir nombres. Usa null si el campo esta vacio o no
es legible. confidence mide solo la certeza de la transcripcion. candidates debe
contener alternativas visibles plausibles cuando haya ambiguedad; no inventes.
El campo gender corresponde a la casilla RAMA.
""".strip()

SLOT_PROMPT_TEMPLATE = """
Transcribe exactamente las casillas de jugadores {slots} de una cedula CTT.
La primera imagen da contexto de pagina; la segunda es el montaje ampliado con
cada casilla etiquetada. Devuelve un objeto por cada numero solicitado, incluso
si la casilla esta vacia. No agregues, omitas ni reordenes numeros. No deduzcas
nombres desde fotografias. Separa nombre(s), apellido paterno y materno solo por
lo escrito. Conserva la fecha tal como se ve y no inventes CURP. Usa null para
campos vacios o ilegibles. confidence mide la certeza de cada transcripcion y
candidates enumera alternativas visibles plausibles cuando exista ambiguedad.
""".strip()


class CttResponsesProtocolError(RuntimeError):
    """The model response did not satisfy the extraction protocol."""


class CttRawField(BaseModel):
    """One model-supplied field before deterministic canonical validation."""

    model_config = ConfigDict(extra="forbid")

    raw_text: Optional[str]
    confidence: float = Field(ge=0.0, le=1.0)
    candidates: List[str]


class CttHeaderExtraction(BaseModel):
    """Structured output requested for the first-page header."""

    model_config = ConfigDict(extra="forbid")

    team_name: CttRawField
    category: CttRawField
    gender: CttRawField
    league: CttRawField
    representative_name: CttRawField
    email: CttRawField
    state: CttRawField
    municipality: CttRawField


class CttPlayerExtraction(BaseModel):
    """Structured output for one labeled player slot."""

    model_config = ConfigDict(extra="forbid")

    slot: int = Field(ge=1, le=20)
    given_names: CttRawField
    paternal_surname: CttRawField
    maternal_surname: CttRawField
    birth_date: CttRawField
    curp: CttRawField


class CttSlotBatchExtraction(BaseModel):
    """Structured output for one bounded slot montage."""

    model_config = ConfigDict(extra="forbid")

    slots: List[CttPlayerExtraction]

    @model_validator(mode="after")
    def reject_duplicate_slots(self) -> "CttSlotBatchExtraction":
        numbers = [player.slot for player in self.slots]
        if len(numbers) != len(set(numbers)):
            raise ValueError("duplicate slot numbers in model response")
        return self


@dataclass(frozen=True)
class CttResponsesExtractionResult:
    """Canonical draft and non-sensitive provider audit metadata."""

    draft: CttRegistrationDraft
    model: str
    response_ids: Tuple[str, ...]


ParsedModel = TypeVar("ParsedModel", bound=BaseModel)


def _image_sha256(image: Image.Image) -> str:
    canonical = image.convert("RGB")
    size_prefix = f"{canonical.width}x{canonical.height}:RGB:".encode("ascii")
    return hashlib.sha256(size_prefix + canonical.tobytes()).hexdigest()


def _image_data_url(image: Image.Image, *, max_dimension: int = 2048) -> str:
    prepared = image.convert("RGB")
    prepared.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    prepared.save(buffer, format="JPEG", quality=90, optimize=True)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def _relative_crop(
    image: Image.Image,
    field: Mapping[str, Any],
    *,
    margin_px: int = 14,
) -> Image.Image:
    width, height = image.size
    left = int(float(field.get("x") or 0.0) * width) - margin_px
    top = int(float(field.get("y") or 0.0) * height) - margin_px
    right = (
        int((float(field.get("x") or 0.0) + float(field.get("w") or 0.0)) * width)
        + margin_px
    )
    bottom = (
        int((float(field.get("y") or 0.0) + float(field.get("h") or 0.0)) * height)
        + margin_px
    )
    return image.crop(clamp_box((left, top, right, bottom), image.size))


def _header_crops(
    normalized_front: Image.Image,
    page_layout: Mapping[str, Any],
) -> Dict[CttFieldName, Image.Image]:
    configured = page_layout.get("header_fields") or {}
    crops: Dict[CttFieldName, Image.Image] = {}
    for layout_name, field_name in HEADER_LAYOUT_TO_FIELD.items():
        field = configured.get(layout_name)
        if not isinstance(field, Mapping):
            raise ValueError(f"CTT layout missing header field {layout_name}")
        crops[field_name] = _relative_crop(normalized_front, field)
    return crops


def _build_header_montage(crops: Mapping[CttFieldName, Image.Image]) -> Image.Image:
    label_width = 330
    content_width = 1800
    gap = 16
    prepared: List[Tuple[CttFieldName, Image.Image]] = []
    for field_name in CttFieldName:
        if field_name not in crops:
            continue
        crop = crops[field_name].convert("RGB")
        target_height = max(96, min(180, crop.height * 2))
        target_width = max(1, round(crop.width * (target_height / crop.height)))
        resized = crop.resize((target_width, target_height), Image.Resampling.LANCZOS)
        resized.thumbnail((content_width, 180), Image.Resampling.LANCZOS)
        prepared.append((field_name, resized))

    if not prepared:
        raise ValueError("Cannot build CTT header montage without crops")
    row_height = max(image.height for _, image in prepared) + gap
    canvas = Image.new(
        "RGB",
        (label_width + content_width + (gap * 3), (row_height * len(prepared)) + gap),
        "white",
    )
    draw = ImageDraw.Draw(canvas)
    for index, (field_name, image) in enumerate(prepared):
        top = gap + (index * row_height)
        draw.text((gap, top + 25), field_name.value, fill="black")
        image_left = label_width + (gap * 2)
        canvas.paste(image, (image_left, top))
        draw.rectangle(
            (image_left, top, image_left + image.width - 1, top + image.height - 1),
            outline="gray",
        )
    return canvas


def _raw_observation(
    raw: CttRawField,
    *,
    field_name: CttFieldName,
    evidence: CttFieldEvidence,
) -> CttFieldObservation:
    return CttFieldObservation(
        field_name=field_name,
        raw_text=raw.raw_text,
        confidence=raw.confidence,
        candidates=raw.candidates,
        evidence=evidence,
    )


class CttResponsesExtractor:
    """Extract a two-page CTT form using bounded structured Responses calls."""

    def __init__(
        self,
        client: Any,
        *,
        model: str = DEFAULT_CTT_RESPONSES_MODEL,
        timeout_seconds: float = 90.0,
    ) -> None:
        if not model.strip():
            raise ValueError("model cannot be empty")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.client = client
        self.model = model.strip()
        self.timeout_seconds = timeout_seconds

    @classmethod
    def from_api_key(
        cls,
        api_key: str,
        *,
        model: str = DEFAULT_CTT_RESPONSES_MODEL,
        timeout_seconds: float = 90.0,
    ) -> "CttResponsesExtractor":
        """Build an extractor without persisting or logging the credential."""
        if not api_key.strip():
            raise ValueError("api_key cannot be empty")
        from openai import AsyncOpenAI

        return cls(
            AsyncOpenAI(api_key=api_key),
            model=model,
            timeout_seconds=timeout_seconds,
        )

    async def _parse(
        self,
        *,
        prompt: str,
        page_image: Image.Image,
        montage: Image.Image,
        text_format: Type[ParsedModel],
    ) -> Tuple[ParsedModel, str]:
        response = await self.client.responses.parse(
            model=self.model,
            instructions=(
                "Eres un transcriptor de formularios. Obedece el esquema y no "
                "completes datos que no sean visibles."
            ),
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt},
                        {
                            "type": "input_image",
                            "image_url": _image_data_url(page_image),
                            "detail": "low",
                        },
                        {
                            "type": "input_image",
                            "image_url": _image_data_url(montage),
                            "detail": "high",
                        },
                    ],
                }
            ],
            text_format=text_format,
            store=False,
            max_output_tokens=5000,
            metadata={
                "component": "ctt_registration_ocr",
                "schema_version": SCHEMA_VERSION,
            },
            timeout=self.timeout_seconds,
        )
        parsed = getattr(response, "output_parsed", None)
        if parsed is None:
            raise CttResponsesProtocolError(
                "OpenAI response did not contain parsed structured output"
            )
        response_id = str(getattr(response, "id", "") or "")
        if not response_id:
            raise CttResponsesProtocolError("OpenAI response did not contain an id")
        return text_format.model_validate(parsed), response_id

    def _team_draft(
        self,
        header: CttHeaderExtraction,
        crops: Mapping[CttFieldName, Image.Image],
    ) -> CttTeamDraft:
        raw_by_name = {
            CttFieldName.TEAM_NAME: header.team_name,
            CttFieldName.CATEGORY: header.category,
            CttFieldName.GENDER: header.gender,
            CttFieldName.LEAGUE: header.league,
            CttFieldName.REPRESENTATIVE_NAME: header.representative_name,
            CttFieldName.EMAIL: header.email,
            CttFieldName.STATE: header.state,
            CttFieldName.MUNICIPALITY: header.municipality,
        }

        def observation(field_name: CttFieldName) -> CttFieldObservation:
            return _raw_observation(
                raw_by_name[field_name],
                field_name=field_name,
                evidence=CttFieldEvidence(
                    page=1,
                    crop_id=f"p1:header:{field_name.value}",
                    crop_sha256=_image_sha256(crops[field_name]),
                ),
            )

        return CttTeamDraft(
            fields=CttTeamFields(
                name=observation(CttFieldName.TEAM_NAME),
                category=observation(CttFieldName.CATEGORY),
                gender=observation(CttFieldName.GENDER),
                league=observation(CttFieldName.LEAGUE),
                representative_name=observation(CttFieldName.REPRESENTATIVE_NAME),
                email=observation(CttFieldName.EMAIL),
                state=observation(CttFieldName.STATE),
                municipality=observation(CttFieldName.MUNICIPALITY),
            )
        )

    def _slot_drafts(
        self,
        batch: CttSlotBatch,
        extraction: CttSlotBatchExtraction,
    ) -> List[CttSlotDraft]:
        expected = [slot.slot for slot in batch.slots]
        actual = [player.slot for player in extraction.slots]
        if actual != expected:
            raise CttResponsesProtocolError(
                f"slot response mismatch: expected {expected}, received {actual}"
            )
        crops = {slot.slot: slot for slot in batch.slots}
        drafts: List[CttSlotDraft] = []
        for player in extraction.slots:
            crop = crops[player.slot]
            crop_sha256 = _image_sha256(crop.image)

            def observation(
                raw: CttRawField,
                field_name: CttFieldName,
            ) -> CttFieldObservation:
                return _raw_observation(
                    raw,
                    field_name=field_name,
                    evidence=CttFieldEvidence(
                        page=crop.page,
                        slot=crop.slot,
                        crop_id=f"p{crop.page}:slot-{crop.slot}:{field_name.value}",
                        crop_sha256=crop_sha256,
                    ),
                )

            drafts.append(
                CttSlotDraft(
                    page=crop.page,
                    slot=crop.slot,
                    fields=CttPlayerFields(
                        given_names=observation(
                            player.given_names, CttFieldName.GIVEN_NAMES
                        ),
                        paternal_surname=observation(
                            player.paternal_surname,
                            CttFieldName.PATERNAL_SURNAME,
                        ),
                        maternal_surname=observation(
                            player.maternal_surname,
                            CttFieldName.MATERNAL_SURNAME,
                        ),
                        birth_date=observation(
                            player.birth_date, CttFieldName.BIRTH_DATE
                        ),
                        curp=observation(player.curp, CttFieldName.CURP),
                    ),
                )
            )
        return drafts

    async def extract(
        self,
        page_images: Sequence[Image.Image],
        layout: Mapping[str, Any],
        *,
        document_sha256: str,
    ) -> CttResponsesExtractionResult:
        """Return a fail-closed canonical draft for exactly two template pages."""
        if len(page_images) != 2:
            raise ValueError("CTT Responses extraction requires exactly two pages")
        if re.fullmatch(SHA256_PATTERN, document_sha256) is None:
            raise ValueError("document_sha256 must be a lowercase SHA-256 hex digest")

        pages_layout = layout.get("pages") or {}
        normalized_pages: List[Image.Image] = []
        slots: List[CttSlotCrop] = []
        for index, image in enumerate(page_images):
            side = PAGE_SIDES[index]
            page_layout = pages_layout.get(side)
            if not isinstance(page_layout, Mapping):
                raise ValueError(f"CTT layout missing page {side}")
            normalized, _metadata = normalize_ctt_template_image(image)
            normalized_pages.append(normalized)
            slots.extend(
                extract_slots_from_normalized_page(
                    normalized,
                    page=index + 1,
                    page_layout=page_layout,
                )
            )

        actual_slots = tuple(slot.slot for slot in slots)
        if actual_slots != EXPECTED_SLOT_NUMBERS:
            raise ValueError(
                "CTT layout must materialize slots 1 through 20 in document order"
            )

        header_crops = _header_crops(normalized_pages[0], pages_layout["front"])
        header, header_response_id = await self._parse(
            prompt=HEADER_PROMPT,
            page_image=normalized_pages[0],
            montage=_build_header_montage(header_crops),
            text_format=CttHeaderExtraction,
        )

        slot_drafts: List[CttSlotDraft] = []
        response_ids = [header_response_id]
        for batch in build_ctt_slot_batches(slots, max_slots=4):
            page_number = batch.slots[0].page
            expected = ", ".join(str(slot.slot) for slot in batch.slots)
            parsed, response_id = await self._parse(
                prompt=SLOT_PROMPT_TEMPLATE.format(slots=expected),
                page_image=normalized_pages[page_number - 1],
                montage=batch.montage,
                text_format=CttSlotBatchExtraction,
            )
            slot_drafts.extend(self._slot_drafts(batch, parsed))
            response_ids.append(response_id)

        draft = CttRegistrationDraft(
            document_sha256=document_sha256,
            team=self._team_draft(header, header_crops),
            slots=slot_drafts,
        )
        return CttResponsesExtractionResult(
            draft=draft,
            model=self.model,
            response_ids=tuple(response_ids),
        )
