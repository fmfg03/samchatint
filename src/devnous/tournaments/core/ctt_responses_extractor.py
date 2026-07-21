"""Structured OpenAI Responses extraction for CTT registration forms."""

from __future__ import annotations

import base64
import hashlib
import io
import re
from dataclasses import dataclass
from typing import (
    Any,
    Dict,
    List,
    Literal,
    Mapping,
    Optional,
    Sequence,
    Tuple,
    Type,
    TypeVar,
)

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
CTT_RESPONSES_PIPELINE_VERSION = "ctt.responses.v4"
EXPECTED_FRONT_SLOTS = tuple(range(1, 9))
EXPECTED_BACK_SLOTS = tuple(range(9, 21))

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
occupied debe ser true cuando la casilla contiene escritura o una fotografia de
jugador, aunque algun texto sea ilegible; debe ser false solo si esta vacia.
""".strip()

PAGE_SEQUENCE_PROMPT_TEMPLATE = """
Clasifica las {page_count} imagenes fisicas de una cedula de inscripcion CTT.
No uses el orden de las imagenes para decidir. front es la portada que contiene
el encabezado del equipo, las tarjetas de director tecnico y auxiliar, y ocho
casillas de jugadores. back es el reverso de continuacion que contiene doce
casillas de jugadores y no contiene el encabezado del equipo. Usa unknown si la
imagen no corresponde claramente a una de esas dos caras o no pertenece a esta
plantilla. Devuelve exactamente un objeto por imagen, en el mismo orden, con
physical_page numerado desde 1. template_match debe ser true solo cuando la
plantilla CTT y la cara indicada sean visibles con claridad.
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
    occupied: bool
    given_names: CttRawField
    paternal_surname: CttRawField
    maternal_surname: CttRawField
    birth_date: CttRawField
    curp: CttRawField

    @model_validator(mode="after")
    def content_implies_occupancy(self) -> "CttPlayerExtraction":
        fields = (
            self.given_names,
            self.paternal_surname,
            self.maternal_surname,
            self.birth_date,
            self.curp,
        )
        if any(
            (field.raw_text and field.raw_text.strip()) or field.candidates
            for field in fields
        ):
            self.occupied = True
        return self


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


class CttPageIdentity(BaseModel):
    """Model-supplied identity for one physical document page."""

    model_config = ConfigDict(extra="forbid")

    physical_page: int = Field(ge=1, le=3)
    side: Literal["front", "back", "unknown"]
    template_match: bool


class CttPageSequenceExtraction(BaseModel):
    """Structured classification of the supplied physical page sequence."""

    model_config = ConfigDict(extra="forbid")

    pages: List[CttPageIdentity]

    @model_validator(mode="after")
    def reject_duplicate_pages(self) -> "CttPageSequenceExtraction":
        numbers = [page.physical_page for page in self.pages]
        if len(numbers) != len(set(numbers)):
            raise ValueError("duplicate physical page numbers in model response")
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
        input_images_are_canonical: bool = False,
    ) -> None:
        if not model.strip():
            raise ValueError("model cannot be empty")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.client = client
        self.model = model.strip()
        self.timeout_seconds = timeout_seconds
        self.input_images_are_canonical = bool(input_images_are_canonical)
        self.pipeline_version = CTT_RESPONSES_PIPELINE_VERSION + (
            ".canonical_input" if self.input_images_are_canonical else ""
        )

    @classmethod
    def from_api_key(
        cls,
        api_key: str,
        *,
        model: str = DEFAULT_CTT_RESPONSES_MODEL,
        timeout_seconds: float = 90.0,
        input_images_are_canonical: bool = False,
    ) -> "CttResponsesExtractor":
        """Build an extractor without persisting or logging the credential."""
        if not api_key.strip():
            raise ValueError("api_key cannot be empty")
        from openai import AsyncOpenAI

        return cls(
            AsyncOpenAI(api_key=api_key),
            model=model,
            timeout_seconds=timeout_seconds,
            input_images_are_canonical=input_images_are_canonical,
        )

    async def _parse(
        self,
        *,
        prompt: str,
        page_image: Image.Image,
        montage: Image.Image,
        text_format: Type[ParsedModel],
        page_detail: Literal["low", "high"] = "low",
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
                            "detail": page_detail,
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

    async def _classify_pages(
        self,
        page_images: Sequence[Image.Image],
    ) -> Tuple[CttPageSequenceExtraction, str]:
        content: List[Dict[str, Any]] = [
            {
                "type": "input_text",
                "text": PAGE_SEQUENCE_PROMPT_TEMPLATE.format(
                    page_count=len(page_images),
                ),
            }
        ]
        content.extend(
            {
                "type": "input_image",
                "image_url": _image_data_url(image),
                "detail": "high",
            }
            for image in page_images
        )
        response = await self.client.responses.parse(
            model=self.model,
            instructions=(
                "Eres un clasificador de paginas de formularios. Clasifica "
                "solo por la estructura impresa visible y obedece el esquema."
            ),
            input=[{"role": "user", "content": content}],
            text_format=CttPageSequenceExtraction,
            store=False,
            max_output_tokens=1000,
            metadata={
                "component": "ctt_registration_page_identity",
                "schema_version": SCHEMA_VERSION,
            },
            timeout=self.timeout_seconds,
        )
        parsed = getattr(response, "output_parsed", None)
        if parsed is None:
            raise CttResponsesProtocolError(
                "OpenAI page identity response did not contain parsed "
                "structured output"
            )
        response_id = str(getattr(response, "id", "") or "")
        if not response_id:
            raise CttResponsesProtocolError(
                "OpenAI page identity response did not contain an id"
            )
        return CttPageSequenceExtraction.model_validate(parsed), response_id

    @staticmethod
    def _validate_page_sequence(
        extraction: CttPageSequenceExtraction,
        *,
        page_count: int,
    ) -> None:
        expected_numbers = list(range(1, page_count + 1))
        actual_numbers = [page.physical_page for page in extraction.pages]
        if actual_numbers != expected_numbers:
            raise CttResponsesProtocolError(
                "CTT page identity mismatch: expected physical pages "
                f"{expected_numbers}, received {actual_numbers}"
            )

        expected_sides = ["front"] + ["back"] * (page_count - 1)
        actual_sides = [page.side for page in extraction.pages]
        invalid_templates = [
            page.physical_page for page in extraction.pages if not page.template_match
        ]
        if actual_sides != expected_sides or invalid_templates:
            raise CttResponsesProtocolError(
                "CTT page sequence must be front followed by back pages from "
                "the expected template; received sides "
                f"{actual_sides} and template mismatches {invalid_templates}"
            )

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

    def _batch_pairs(
        self,
        batch: CttSlotBatch,
        extraction: CttSlotBatchExtraction,
    ) -> List[Tuple[CttSlotCrop, CttPlayerExtraction]]:
        expected = [slot.slot for slot in batch.slots]
        actual = [player.slot for player in extraction.slots]
        if actual != expected:
            raise CttResponsesProtocolError(
                f"slot response mismatch: expected {expected}, received {actual}"
            )
        crops = {slot.slot: slot for slot in batch.slots}
        return [(crops[player.slot], player) for player in extraction.slots]

    @staticmethod
    def _canonical_crop(
        crop: CttSlotCrop,
        *,
        page: int,
        slot: int,
    ) -> CttSlotCrop:
        return CttSlotCrop(
            page=page,
            slot=slot,
            box=crop.box,
            image=crop.image,
            source_page=crop.physical_page,
            source_slot=crop.physical_slot,
        )

    def _slot_draft(
        self,
        crop: CttSlotCrop,
        player: CttPlayerExtraction,
    ) -> CttSlotDraft:
        crop_sha256 = _image_sha256(crop.image)
        source_page = crop.physical_page
        source_slot = crop.physical_slot

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
                    source_page=source_page,
                    source_slot=source_slot,
                    crop_id=(
                        f"p{crop.page}:slot-{crop.slot}:source-p{source_page}-"
                        f"slot-{source_slot}:{field_name.value}"
                    ),
                    crop_sha256=crop_sha256,
                ),
            )

        return CttSlotDraft(
            page=crop.page,
            slot=crop.slot,
            occupied=player.occupied,
            fields=CttPlayerFields(
                given_names=observation(player.given_names, CttFieldName.GIVEN_NAMES),
                paternal_surname=observation(
                    player.paternal_surname,
                    CttFieldName.PATERNAL_SURNAME,
                ),
                maternal_surname=observation(
                    player.maternal_surname,
                    CttFieldName.MATERNAL_SURNAME,
                ),
                birth_date=observation(player.birth_date, CttFieldName.BIRTH_DATE),
                curp=observation(player.curp, CttFieldName.CURP),
            ),
        )

    def _canonicalize_slot_pairs(
        self,
        pairs: Sequence[Tuple[CttSlotCrop, CttPlayerExtraction]],
        *,
        page_count: int,
    ) -> List[Tuple[CttSlotCrop, CttPlayerExtraction]]:
        by_source_page: Dict[int, List[Tuple[CttSlotCrop, CttPlayerExtraction]]] = {}
        for crop, player in pairs:
            by_source_page.setdefault(crop.page, []).append((crop, player))
        for source_pairs in by_source_page.values():
            source_pairs.sort(key=lambda item: item[0].slot)

        front = by_source_page.get(1) or []
        canonical = [
            (self._canonical_crop(crop, page=1, slot=crop.slot), player)
            for crop, player in front
        ]
        if page_count == 2:
            primary_back = by_source_page.get(2) or []
            occupied_count = sum(1 for _crop, player in primary_back if player.occupied)
            if occupied_count < 8:
                raise CttResponsesProtocolError(
                    "two-page CTT document requires a primary page 2 with at "
                    f"least eight players; received {occupied_count}"
                )
            canonical.extend(
                (
                    self._canonical_crop(crop, page=2, slot=crop.slot),
                    player,
                )
                for crop, player in primary_back
            )
            return canonical

        back_pages = {page: by_source_page.get(page) or [] for page in (2, 3)}
        occupied_counts = {
            page: sum(1 for _crop, player in source_pairs if player.occupied)
            for page, source_pairs in back_pages.items()
        }
        primary_pages = [page for page, count in occupied_counts.items() if count == 12]
        extension_pages = [
            page for page, count in occupied_counts.items() if 1 <= count <= 5
        ]
        if (
            len(primary_pages) != 1
            or len(extension_pages) != 1
            or primary_pages[0] == extension_pages[0]
        ):
            raise CttResponsesProtocolError(
                "three-page CTT document requires one full page 2 and one "
                "extension copy containing between one and five players; "
                f"received occupied counts {occupied_counts}"
            )

        for crop, player in back_pages[primary_pages[0]]:
            canonical.append(
                (self._canonical_crop(crop, page=2, slot=crop.slot), player)
            )

        extension_pairs = back_pages[extension_pages[0]]
        occupied = [pair for pair in extension_pairs if pair[1].occupied]
        empty = [pair for pair in extension_pairs if not pair[1].occupied]
        selected = occupied + empty[: 5 - len(occupied)]
        for canonical_slot, (crop, player) in enumerate(selected, start=21):
            canonical.append(
                (
                    self._canonical_crop(crop, page=3, slot=canonical_slot),
                    player,
                )
            )
        return canonical

    async def extract(
        self,
        page_images: Sequence[Image.Image],
        layout: Mapping[str, Any],
        *,
        document_sha256: str,
    ) -> CttResponsesExtractionResult:
        """Return a fail-closed canonical draft for two or three physical pages."""
        if len(page_images) not in (2, 3):
            raise ValueError("CTT Responses extraction requires two or three pages")
        if re.fullmatch(SHA256_PATTERN, document_sha256) is None:
            raise ValueError("document_sha256 must be a lowercase SHA-256 hex digest")

        pages_layout = layout.get("pages") or {}
        normalized_pages: List[Image.Image] = []
        physical_slots: List[CttSlotCrop] = []
        for index, image in enumerate(page_images):
            side = "front" if index == 0 else "back"
            page_layout = pages_layout.get(side)
            if not isinstance(page_layout, Mapping):
                raise ValueError(f"CTT layout missing page {side}")
            normalized, _metadata = normalize_ctt_template_image(
                image,
                already_canonical=self.input_images_are_canonical,
            )
            normalized_pages.append(normalized)
            physical_slots.extend(
                extract_slots_from_normalized_page(
                    normalized,
                    page=index + 1,
                    page_layout=page_layout,
                )
            )

        for page_number in range(1, len(page_images) + 1):
            actual = tuple(
                slot.slot for slot in physical_slots if slot.page == page_number
            )
            expected_slots = (
                EXPECTED_FRONT_SLOTS if page_number == 1 else EXPECTED_BACK_SLOTS
            )
            if actual != expected_slots:
                side = "front" if page_number == 1 else "back"
                raise ValueError(
                    f"CTT layout page {side} must materialize printed slots "
                    f"{expected_slots[0]} through {expected_slots[-1]}"
                )

        page_sequence, page_sequence_response_id = await self._classify_pages(
            normalized_pages
        )
        self._validate_page_sequence(
            page_sequence,
            page_count=len(normalized_pages),
        )

        header_crops = _header_crops(normalized_pages[0], pages_layout["front"])
        header, header_response_id = await self._parse(
            prompt=HEADER_PROMPT,
            page_image=normalized_pages[0],
            montage=_build_header_montage(header_crops),
            text_format=CttHeaderExtraction,
            page_detail="high",
        )

        raw_pairs: List[Tuple[CttSlotCrop, CttPlayerExtraction]] = []
        response_ids = [page_sequence_response_id, header_response_id]
        for batch in build_ctt_slot_batches(physical_slots, max_slots=4):
            source_page = batch.slots[0].page
            if any(slot.page != source_page for slot in batch.slots):
                raise ValueError("CTT slot batch cannot cross physical pages")
            requested_slots = ", ".join(str(slot.slot) for slot in batch.slots)
            parsed, response_id = await self._parse(
                prompt=SLOT_PROMPT_TEMPLATE.format(slots=requested_slots),
                page_image=normalized_pages[source_page - 1],
                montage=batch.montage,
                text_format=CttSlotBatchExtraction,
            )
            raw_pairs.extend(self._batch_pairs(batch, parsed))
            response_ids.append(response_id)

        canonical_pairs = self._canonicalize_slot_pairs(
            raw_pairs,
            page_count=len(page_images),
        )
        slot_drafts = [
            self._slot_draft(crop, player) for crop, player in canonical_pairs
        ]
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
