#!/usr/bin/env python3
"""
Local OCR pipeline for Copa Telmex registration forms.

This script is designed to run inside the repo virtualenv, not necessarily
under the same interpreter as the Telegram bot service.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import logging
import math
import os
import re
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))

import cv2
import numpy as np
import torch
from PIL import Image, ImageEnhance, ImageFilter
from transformers import AutoModelForCausalLM, TrOCRProcessor, VisionEncoderDecoderModel

from devnous.document_parsing import (
    adjudicate_registration_extraction,
    parse_document_bytes,
)
from devnous.tournaments.core.ocr_integrity import (
    canonicalize_mexican_state,
    evaluate_player_identity_integrity,
)
from devnous.validation import MexicanNamesValidator, validate_name_field, validate_team_name
from devnous.validation.hard_validator import ValidationStatus


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("local_registration_ocr")


def _env_flag(name: str, default: bool) -> bool:
    value = (os.getenv(name) or "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


def _json_from_text(value: str) -> Optional[Dict[str, Any]]:
    text = (value or "").strip()
    if not text:
        return None
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < 0 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def _clean_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    if not text or text.lower() in {"null", "none", "n/a", "no visible", "unknown"}:
        return None
    return text


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except Exception:
        return default


def _preferred_torch_device() -> str:
    if not torch.cuda.is_available():
        return "cpu"
    try:
        major, minor = torch.cuda.get_device_capability(0)
        if major < 7:
            logger.warning(
                "CUDA device capability %s.%s is too old for the installed PyTorch build; using CPU",
                major,
                minor,
            )
            return "cpu"
    except Exception:
        logger.warning("Could not inspect CUDA capability; using CPU", exc_info=True)
        return "cpu"
    return "cuda"


def _extract_date(text: str) -> Optional[str]:
    match = re.search(r"\b(\d{1,2})[\/\-.](\d{1,2})[\/\-.](\d{2,4})\b", text or "")
    if not match:
        return None
    dd, mm, yy = match.groups()
    if len(yy) == 2:
        yy = f"20{yy}" if int(yy) < 40 else f"19{yy}"
    return f"{int(dd):02d}/{int(mm):02d}/{yy}"


def _extract_curp(text: str) -> Optional[str]:
    cleaned = re.sub(r"[^A-Z0-9]", "", (text or "").upper())
    match = re.search(r"[A-Z]{4}\d{6}[HM][A-Z]{2}[A-Z]{3}[A-Z0-9]{2}", cleaned)
    if match:
        return match.group(0)
    if 16 <= len(cleaned) <= 20:
        return cleaned
    return None


def _render_pdf_to_images(pdf_path: Path, *, dpi: int = 200) -> List[Image.Image]:
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError("PyMuPDF (fitz) is required for --pdf ingestion") from exc

    doc = fitz.open(str(pdf_path))
    zoom = dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)
    images: List[Image.Image] = []
    try:
        for page in doc:
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            images.append(image)
    finally:
        doc.close()
    return images


def _image_to_data_url(image: Image.Image, *, quality: int = 90) -> str:
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", quality=quality)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def _openai_vision_json(
    *,
    image: Image.Image,
    prompt: str,
    model: Optional[str] = None,
    timeout_seconds: float = 90.0,
) -> Dict[str, Any]:
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY missing")

    payload = {
        "model": model or os.getenv("OPENAI_OCR_MODEL", "gpt-4.1-mini"),
        "temperature": 0,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": _image_to_data_url(image)},
                    },
                ],
            }
        ],
    }
    request = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI HTTP {exc.code}: {error_body[:500]}") from exc

    data = json.loads(body)
    content = data["choices"][0]["message"]["content"]
    parsed = _json_from_text(content)
    if not parsed:
        raise RuntimeError("OpenAI OCR returned non-JSON content")
    return parsed


def _ctt_openai_prompt(page_number: int) -> str:
    return (
        "Analiza esta pagina escaneada de una cedula de inscripcion de "
        "Copa Telmex-Telcel de futbol. No aplica para otros torneos.\n"
        f"Numero de pagina del PDF: {page_number}.\n\n"
        "Devuelve SOLO JSON estricto con esta estructura:\n"
        "{\n"
        '  "page_type": "front"|"back",\n'
        '  "team": {"name": string|null, "gender": string|null, '
        '"category": string|null, "representative_name": string|null, '
        '"league": string|null, "email": string|null, "state": string|null, '
        '"municipality": string|null, "folio": string|null, "confidence": number},\n'
        '  "responsables": [{"role": "director_tecnico"|"auxiliar", '
        '"name": string|null, "birth_date": string|null, "curp": string|null, '
        '"confidence": number, "needs_review": boolean}],\n'
        '  "players": [{"visible_player_number": number|null, "name": string|null, '
        '"birth_date": string|null, "curp": string|null, "confidence": number, '
        '"needs_review": boolean}],\n'
        '  "overall_confidence": number,\n'
        '  "notes": string|null\n'
        "}\n\n"
        "Reglas:\n"
        "- page_type=front si aparecen los datos del equipo antes del Director Tecnico.\n"
        "- page_type=back si solo aparecen jugadores 9-20.\n"
        "- En front extrae todos los datos antes del Director Tecnico: equipo, rama, "
        "categoria, representante, liga, correo, estado y municipio.\n"
        "- Director Tecnico y Auxiliar son responsables; Auxiliar puede estar vacio.\n"
        "- No inventes jugadores ni responsables vacios.\n"
        "- Si un jugador aparece parcialmente, incluyelo con needs_review=true.\n"
        "- Transcribe CURP solo si se ve; si dudas marca needs_review=true.\n"
        "- Fechas en DD/MM/YYYY cuando sea posible; si el formato visible tiene dos "
        "digitos de ano, normalizalo a 2000s para jugadores juveniles y conserva "
        "1900s para adultos.\n"
        "- La categoria esperada del lote puede ser varonil juvenil, pero no la "
        "inventes si no se ve.\n"
    )


def _openai_page_payload(image: Image.Image, *, page_number: int) -> Dict[str, Any]:
    raw_result = _openai_vision_json(
        image=image,
        prompt=_ctt_openai_prompt(page_number),
    )
    page_type = (raw_result.get("page_type") or "front").strip().lower()
    is_front = page_type != "back"
    raw_team = raw_result.get("team") if isinstance(raw_result.get("team"), dict) else {}
    representative = _clean_text(raw_team.get("representative_name"))
    email = _clean_text(raw_team.get("email"))
    team = {
        "name": _clean_text(raw_team.get("name")) or "Unknown Team",
        "category": _clean_text(raw_team.get("category")),
        "gender": _clean_text(raw_team.get("gender")),
        "league": _clean_text(raw_team.get("league")),
        "municipality": _clean_text(raw_team.get("municipality")),
        "state": _clean_text(raw_team.get("state")),
        "confidence": _coerce_float(raw_team.get("confidence"), 0.0),
    }
    manager = (
        {
            "name": representative,
            "role": "representante",
            "phone": None,
            "email": email,
            "confidence": team["confidence"],
        }
        if representative
        else None
    )

    responsables = []
    for item in raw_result.get("responsables") or []:
        if not isinstance(item, dict):
            continue
        name = _clean_text(item.get("name"))
        birth_date = _extract_date(str(item.get("birth_date") or "")) or _clean_text(
            item.get("birth_date")
        )
        curp = _extract_curp(str(item.get("curp") or "")) or _clean_text(item.get("curp"))
        if not name and not birth_date and not curp:
            continue
        responsables.append(
            {
                "name": name,
                "role": _clean_text(item.get("role")),
                "birth_date": birth_date,
                "curp": curp,
                "confidence": _coerce_float(item.get("confidence"), 0.0),
                "needs_review": bool(item.get("needs_review")) or not name,
            }
        )

    players = []
    for index, item in enumerate(raw_result.get("players") or [], 1):
        if not isinstance(item, dict):
            continue
        name = _clean_text(item.get("name"))
        birth_date = _extract_date(str(item.get("birth_date") or "")) or _clean_text(
            item.get("birth_date")
        )
        curp = _extract_curp(str(item.get("curp") or "")) or _clean_text(item.get("curp"))
        if not name and not birth_date and not curp:
            continue
        visible = item.get("visible_player_number")
        if visible is None:
            visible = (8 + index) if not is_front else index
        try:
            visible_number = int(visible)
        except Exception:
            visible_number = (8 + index) if not is_front else index
        players.append(
            {
                "name": name,
                "first_name": None,
                "paternal_surname": None,
                "maternal_surname": None,
                "birth_date": birth_date,
                "curp": curp,
                "jersey_number": None,
                "position": None,
                "photo_region": None,
                "confidence": _coerce_float(item.get("confidence"), 0.0),
                "needs_review": bool(item.get("needs_review")) or not name,
                "source_page_number": 1 if is_front else 2,
                "visible_slot_label": f"Jugador {visible_number}",
                "visible_player_number": visible_number,
                "continuous_player_number": visible_number,
            }
        )

    extraction = {
        "team": team,
        "responsables": responsables,
        "manager": manager,
        "players": players,
        "overall_confidence": _coerce_float(raw_result.get("overall_confidence"), 0.0),
        "notes": _clean_text(raw_result.get("notes")),
        "form_type": "copa_telmex_telcel_futbol_2026",
        "is_front": is_front,
    }
    return {
        "extraction": extraction,
        "raw": {
            "provider": "openai",
            "openai_result": raw_result,
            "ctt_template": {
                "template_id": "copa_telmex_telcel_futbol_2026",
                "tournament": "Copa Telmex-Telcel",
                "sport": "futbol",
                "scope": "ctt_futbol_only",
                "side": "front" if is_front else "back",
                "page_type": "front" if is_front else "back",
                "back_page_repeatable": not is_front,
            },
        },
    }


def _looks_like_name(text: str) -> bool:
    value = _clean_text(text)
    if not value:
        return False
    if len(value.split()) < 2:
        return False
    if any(ch.isdigit() for ch in value):
        return False
    return True


def _extract_name_candidate(text: str) -> Optional[str]:
    if not text:
        return None
    candidate = re.split(r"\b\d{1,2}[\/\-.]\d{1,2}[\/\-.]\d{2,4}\b", text, maxsplit=1)[0]
    candidate = re.sub(r"\b[A-Z]{4}\d{6}[HM][A-Z]{2}[A-Z]{3}[A-Z0-9]{2}\b", "", candidate, flags=re.I)
    candidate = re.sub(r"[^A-Za-zÁÉÍÓÚÑÜáéíóúñü\s]", " ", candidate)
    candidate = re.sub(r"\s+", " ", candidate).strip()
    return candidate if _looks_like_name(candidate) else None


def _find_labeled_text(text: str, labels: List[str]) -> Optional[str]:
    for label in labels:
        pattern = rf"(?im)^\s*(?:{label})\s*[:\-]\s*(.+?)\s*$"
        match = re.search(pattern, text or "")
        if match:
            return _clean_text(match.group(1))
    return None


def _preprocess_for_trocr(image: Image.Image) -> Image.Image:
    gray = image.convert("L")
    np_img = np.array(gray)
    np_img = cv2.fastNlMeansDenoising(np_img, None, 12, 7, 21)
    np_img = cv2.adaptiveThreshold(
        np_img,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        31,
        11,
    )
    pil = Image.fromarray(np_img).convert("RGB")
    pil = ImageEnhance.Contrast(pil).enhance(1.4)
    pil = ImageEnhance.Sharpness(pil).enhance(1.2)
    return pil.filter(ImageFilter.MedianFilter(size=3))


@dataclass
class OCRTextResult:
    text: str
    confidence: float


@dataclass
class ModelLoadStatus:
    model_name: str
    configured: bool = True
    available: bool = False
    source: Optional[str] = None
    error: Optional[str] = None
    attempt_count: int = 0


def _model_load_attempts() -> List[Tuple[str, bool]]:
    """Prefer local cache, but fall back to download unless explicitly disabled."""
    env_value = os.getenv("LOCAL_OCR_ALLOW_DOWNLOAD")
    if env_value is None:
        return [("cache", True), ("download", False)]
    if _env_flag("LOCAL_OCR_ALLOW_DOWNLOAD", False):
        return [("download", False)]
    return [("cache", True)]


def _short_error(exc: Exception) -> str:
    text = re.sub(r"\s+", " ", str(exc or "")).strip()
    return text[:240] if len(text) > 240 else text


class TrOCRHelper:
    def __init__(self) -> None:
        self.model_name = os.getenv("LOCAL_TROCR_MODEL", "microsoft/trocr-base-handwritten")
        self.num_beams = max(1, int(os.getenv("LOCAL_TROCR_NUM_BEAMS", "4")))
        self.max_length = max(8, int(os.getenv("LOCAL_TROCR_MAX_LENGTH", "96")))
        self.device = _preferred_torch_device()
        self.processor: Optional[TrOCRProcessor] = None
        self.model: Optional[VisionEncoderDecoderModel] = None
        self.status = ModelLoadStatus(model_name=self.model_name)

    def initialize(self) -> bool:
        if self.processor is not None and self.model is not None:
            return True
        errors: List[str] = []
        for source, local_files_only in _model_load_attempts():
            self.status.attempt_count += 1
            try:
                self.processor = TrOCRProcessor.from_pretrained(
                    self.model_name,
                    local_files_only=local_files_only,
                )
                self.model = VisionEncoderDecoderModel.from_pretrained(
                    self.model_name,
                    local_files_only=local_files_only,
                )
                self.model.to(self.device)
                self.model.eval()
                self.status.available = True
                self.status.source = source
                self.status.error = None
                return True
            except Exception as exc:
                errors.append(f"{source}: {_short_error(exc)}")
                self.processor = None
                self.model = None
        self.status.available = False
        self.status.source = None
        self.status.error = " | ".join(errors) if errors else "unknown_error"
        logger.warning("TrOCR unavailable: %s", self.status.error)
        return False

    def read(self, image: Image.Image) -> OCRTextResult:
        if not self.initialize() or self.processor is None or self.model is None:
            return OCRTextResult(text="", confidence=0.0)

        prepared = _preprocess_for_trocr(image)
        pixel_values = self.processor(prepared, return_tensors="pt").pixel_values.to(self.device)
        with torch.no_grad():
            generated = self.model.generate(
                pixel_values,
                num_beams=self.num_beams,
                max_length=self.max_length,
                return_dict_in_generate=True,
                output_scores=True,
            )
        text = self.processor.batch_decode(generated.sequences, skip_special_tokens=True)[0].strip()
        confidence = 0.5
        if generated.scores:
            probs = []
            for score_tensor in generated.scores:
                score_probs = torch.softmax(score_tensor, dim=-1)
                probs.extend(torch.max(score_probs, dim=-1)[0].detach().cpu().numpy().tolist())
            if probs:
                confidence = float(sum(probs) / len(probs))
        return OCRTextResult(text=text, confidence=max(0.0, min(1.0, confidence)))


class MoondreamHelper:
    def __init__(self) -> None:
        self.device = os.getenv("LOCAL_MOONDREAM_DEVICE", _preferred_torch_device())
        self.model_name = os.getenv("LOCAL_MOONDREAM_MODEL", "vikhyatk/moondream2")
        self.model = None
        self.status = ModelLoadStatus(model_name=self.model_name)

    def initialize(self) -> bool:
        if self.model is not None:
            return True
        errors: List[str] = []
        for source, local_files_only in _model_load_attempts():
            self.status.attempt_count += 1
            try:
                self.model = AutoModelForCausalLM.from_pretrained(
                    self.model_name,
                    trust_remote_code=True,
                    device_map=self.device,
                    local_files_only=local_files_only,
                )
                self.status.available = True
                self.status.source = source
                self.status.error = None
                return True
            except Exception as exc:
                errors.append(f"{source}: {_short_error(exc)}")
                self.model = None
        self.status.available = False
        self.status.source = None
        self.status.error = " | ".join(errors) if errors else "unknown_error"
        logger.warning("Moondream unavailable: %s", self.status.error)
        return False

    def query_json(self, image: Image.Image, prompt: str) -> Optional[Dict[str, Any]]:
        if not self.initialize() or self.model is None:
            return None
        try:
            encoded = self.model.encode_image(image)
            response = self.model.query(
                encoded,
                prompt,
                stream=False,
                settings={
                    "temperature": 0.0,
                    "top_p": 0.2,
                    "max_tokens": 900,
                },
            )
            answer = response["answer"] if isinstance(response, dict) else str(response)
            return _json_from_text(answer)
        except Exception as exc:
            logger.warning("Moondream query failed: %s", exc)
            return None


class QianfanLayoutHelper:
    """Layout-only helper for Qianfan-OCR.

    The goal is to recover regions for header fields and player rows/photos
    while leaving the actual handwriting recognition to the existing local OCR
    logic.
    """

    def __init__(self) -> None:
        self.device = os.getenv("LOCAL_QIANFAN_DEVICE", _preferred_torch_device())
        self.model_name = os.getenv("LOCAL_QIANFAN_MODEL", "baidu/Qianfan-OCR")
        self.use_thinking = _env_flag("LOCAL_QIANFAN_USE_THINKING", False)
        self.max_tiles = max(1, int(os.getenv("LOCAL_QIANFAN_MAX_TILES", "12")))
        self.model = None
        self.tokenizer = None
        self.status = ModelLoadStatus(model_name=self.model_name)

    def initialize(self) -> bool:
        if self.model is not None and self.tokenizer is not None:
            return True
        errors: List[str] = []
        for source, local_files_only in _model_load_attempts():
            self.status.attempt_count += 1
            try:
                from transformers import AutoModel, AutoTokenizer

                dtype = torch.bfloat16 if self.device != "cpu" else torch.float32
                self.model = AutoModel.from_pretrained(
                    self.model_name,
                    torch_dtype=dtype,
                    trust_remote_code=True,
                    device_map="auto",
                    local_files_only=local_files_only,
                ).eval()
                self.tokenizer = AutoTokenizer.from_pretrained(
                    self.model_name,
                    trust_remote_code=True,
                    local_files_only=local_files_only,
                )
                self.status.available = True
                self.status.source = source
                self.status.error = None
                return True
            except Exception as exc:
                errors.append(f"{source}: {_short_error(exc)}")
                self.model = None
                self.tokenizer = None
        self.status.available = False
        self.status.source = None
        self.status.error = " | ".join(errors) if errors else "unknown_error"
        logger.warning("Qianfan-OCR unavailable: %s", self.status.error)
        return False

    def query_layout(self, image: Image.Image) -> Optional[Dict[str, Any]]:
        if not self.initialize() or self.model is None or self.tokenizer is None:
            return None

        try:
            pixel_values = self._load_image(image).to(self._dtype()).to(self._model_device())
            prompt = self._build_layout_prompt()
            with torch.no_grad():
                response = self.model.chat(
                    self.tokenizer,
                    pixel_values=pixel_values,
                    question=prompt,
                    generation_config={
                        "max_new_tokens": 4096,
                        "do_sample": False,
                    },
                )
            payload = _json_from_text(str(response))
            return payload if isinstance(payload, dict) else None
        except Exception as exc:
            logger.warning("Qianfan-OCR layout query failed: %s", exc)
            return None

    def _dtype(self) -> torch.dtype:
        return torch.bfloat16 if self.device != "cpu" else torch.float32

    def _model_device(self) -> torch.device:
        model_device = getattr(self.model, "device", None)
        if isinstance(model_device, torch.device):
            return model_device
        try:
            return next(self.model.parameters()).device
        except Exception:
            return torch.device(self.device)

    def _build_layout_prompt(self) -> str:
        prompt = (
            "Analyze this Mexican football team registration roster form. "
            "Return JSON only. "
            "Do not transcribe handwriting. "
            "Detect the team header and each player row in top-to-bottom order. "
            "For the header, locate team_name, category, league, municipality, state, and manager_name if visible. "
            "For each player row, locate row_bbox, name_bbox, birth_date_bbox, curp_bbox, and photo_bbox if visible. "
            "Use null instead of guessing. "
            "Coordinates must be normalized to a 0-1000 range relative to the original image width/height. "
            "Strictly keep each field within the player row it belongs to. "
            "Use this schema: "
            "{\"document_type\":\"team_roster|other\","
            "\"page_confidence\":number,"
            "\"header\":{\"bbox\":{\"x\":number,\"y\":number,\"width\":number,\"height\":number}|null,"
            "\"fields\":{\"team_name\":{\"bbox\":object|null},\"category\":{\"bbox\":object|null},"
            "\"league\":{\"bbox\":object|null},\"municipality\":{\"bbox\":object|null},"
            "\"state\":{\"bbox\":object|null},\"manager_name\":{\"bbox\":object|null}}},"
            "\"players\":[{\"row_index\":number,\"row_bbox\":object|null,"
            "\"name_bbox\":object|null,\"birth_date_bbox\":object|null,"
            "\"curp_bbox\":object|null,\"photo_bbox\":object|null,\"confidence\":number}]}"
        )
        if self.use_thinking:
            prompt += "<think>"
        return prompt

    def _load_image(self, image: Image.Image, input_size: int = 448) -> torch.Tensor:
        import torchvision.transforms as T
        from torchvision.transforms.functional import InterpolationMode

        mean = (0.485, 0.456, 0.406)
        std = (0.229, 0.224, 0.225)
        transform = T.Compose(
            [
                T.Lambda(lambda img: img.convert("RGB") if img.mode != "RGB" else img),
                T.Resize((input_size, input_size), interpolation=InterpolationMode.BICUBIC),
                T.ToTensor(),
                T.Normalize(mean=mean, std=std),
            ]
        )

        images = self._dynamic_preprocess(
            image.convert("RGB"),
            image_size=input_size,
            use_thumbnail=True,
            max_num=self.max_tiles,
        )
        pixel_values = [transform(chunk) for chunk in images]
        return torch.stack(pixel_values)

    def _dynamic_preprocess(
        self,
        image: Image.Image,
        *,
        min_num: int = 1,
        max_num: int = 12,
        image_size: int = 448,
        use_thumbnail: bool = False,
    ) -> List[Image.Image]:
        orig_width, orig_height = image.size
        aspect_ratio = orig_width / max(orig_height, 1)

        target_ratios = set()
        for n in range(min_num, max_num + 1):
            for i in range(1, n + 1):
                for j in range(1, n + 1):
                    if min_num <= i * j <= max_num:
                        target_ratios.add((i, j))
        sorted_ratios = sorted(target_ratios, key=lambda x: x[0] * x[1])
        target_aspect_ratio = self._find_closest_aspect_ratio(
            aspect_ratio,
            sorted_ratios,
            width=orig_width,
            height=orig_height,
            image_size=image_size,
        )

        target_width = image_size * target_aspect_ratio[0]
        target_height = image_size * target_aspect_ratio[1]
        blocks = target_aspect_ratio[0] * target_aspect_ratio[1]
        resized_img = image.resize((target_width, target_height))

        processed_images: List[Image.Image] = []
        tiles_per_row = max(1, target_width // image_size)
        for i in range(blocks):
            box = (
                (i % tiles_per_row) * image_size,
                (i // tiles_per_row) * image_size,
                ((i % tiles_per_row) + 1) * image_size,
                ((i // tiles_per_row) + 1) * image_size,
            )
            processed_images.append(resized_img.crop(box))

        if use_thumbnail and len(processed_images) != 1:
            processed_images.append(image.resize((image_size, image_size)))
        return processed_images

    def _find_closest_aspect_ratio(
        self,
        aspect_ratio: float,
        target_ratios: List[Tuple[int, int]],
        *,
        width: int,
        height: int,
        image_size: int,
    ) -> Tuple[int, int]:
        best_ratio = (1, 1)
        best_ratio_diff = float("inf")
        area = width * height
        for ratio in target_ratios:
            target_aspect_ratio = ratio[0] / ratio[1]
            ratio_diff = abs(aspect_ratio - target_aspect_ratio)
            if ratio_diff < best_ratio_diff:
                best_ratio_diff = ratio_diff
                best_ratio = ratio
            elif ratio_diff == best_ratio_diff:
                threshold = 0.5 * image_size * image_size * ratio[0] * ratio[1]
                if area > threshold:
                    best_ratio = ratio
        return best_ratio


class CTTTemplateOCRHelper:
    """Template-aligned extractor for fixed Copa Telmex registration sheets."""

    def __init__(self) -> None:
        self.template_pdf_path = os.getenv(
            "LOCAL_CTT_TEMPLATE_PDF",
            str(REPO_ROOT / "config" / "CedulaInscripcion_CTT_2026.pdf"),
        )
        self.layout_json_path = os.getenv(
            "LOCAL_CTT_LAYOUT_JSON",
            str(REPO_ROOT / "config" / "layout_ctt_2026.json"),
        )
        self.extractor = None
        self.status = ModelLoadStatus(model_name="ctt_template_extractor")

    def initialize(self) -> bool:
        if self.extractor is not None:
            return True

        template_path = Path(self.template_pdf_path)
        layout_path = Path(self.layout_json_path)
        if not template_path.exists() or not layout_path.exists():
            missing = []
            if not template_path.exists():
                missing.append(f"template={template_path}")
            if not layout_path.exists():
                missing.append(f"layout={layout_path}")
            self.status.available = False
            self.status.source = None
            self.status.error = ", ".join(missing)
            logger.warning("CTT template extractor unavailable: %s", self.status.error)
            return False

        self.status.attempt_count += 1
        try:
            from devnous.vision.ctt_form_extractor import CTTFormExtractor

            self.extractor = CTTFormExtractor(
                template_pdf_path=str(template_path),
                layout_json_path=str(layout_path),
            )
            self.status.available = True
            self.status.source = "local"
            self.status.error = None
            return True
        except Exception as exc:
            self.extractor = None
            self.status.available = False
            self.status.source = None
            self.status.error = _short_error(exc)
            logger.warning("CTT template extractor unavailable: %s", self.status.error)
            return False

    def process_image(self, image: Image.Image, *, out_dir: str, prefix: str) -> Optional[Dict[str, Any]]:
        if not self.initialize() or self.extractor is None:
            return None

        try:
            bgr = cv2.cvtColor(np.array(image.convert("RGB")), cv2.COLOR_RGB2BGR)
            return self.extractor.process_photo_array(bgr, out_dir, prefix)
        except Exception as exc:
            self.status.available = False
            self.status.source = None
            self.status.error = _short_error(exc)
            logger.warning("CTT template OCR failed: %s", self.status.error)
            return None


class LocalRegistrationOCR:
    def __init__(self) -> None:
        layout_provider = (os.getenv("LOCAL_OCR_LAYOUT_PROVIDER") or "").strip().lower()
        self.layout_provider = layout_provider
        self.template_aligner = (
            CTTTemplateOCRHelper() if _env_flag("LOCAL_OCR_USE_TEMPLATE_ALIGNER", True) else None
        )
        self.template_trocr_max_variants = max(
            0,
            int(os.getenv("LOCAL_TEMPLATE_TROCR_MAX_VARIANTS", "0")),
        )
        self.qianfan_layout = QianfanLayoutHelper() if layout_provider == "qianfan" else None
        self.moondream = MoondreamHelper() if _env_flag("LOCAL_OCR_USE_MOONDREAM", True) else None
        self.trocr = TrOCRHelper() if _env_flag("LOCAL_OCR_USE_TROCR", True) else None
        self.names_validator = MexicanNamesValidator(min_confidence=0.82)

    def extract(self, image: Image.Image) -> Dict[str, Any]:
        image = image.convert("RGB")
        raw: Dict[str, Any] = {
            "backend": {
                "provider": "local",
                "layout_provider": self.layout_provider or None,
                "template_aligner_enabled": bool(self.template_aligner),
                "qianfan_layout_enabled": bool(self.qianfan_layout),
                "moondream_enabled": bool(self.moondream),
                "trocr_enabled": bool(self.trocr),
                "mineru_enabled": _env_flag("MINERU_ENABLED", False),
                "adjudicator_enabled": _env_flag("REGISTRATION_ADJUDICATOR_ENABLED", False),
            }
        }

        extraction: Optional[Dict[str, Any]] = None
        mineru_payload = self._extract_with_mineru(image, raw)
        template_payload = self._extract_with_template_alignment(image, raw) if self.template_aligner and self.trocr else None
        if template_payload:
            extraction = template_payload
        elif self.template_aligner:
            raw["ctt_template_status"] = "unavailable"

        if extraction is None and mineru_payload:
            extraction = mineru_payload

        layout_payload = self.qianfan_layout.query_layout(image) if self.qianfan_layout else None
        if extraction is None and layout_payload:
            raw["layout_qianfan"] = layout_payload
            extraction = self._build_initial_extraction_from_layout(layout_payload, image)
            if not self._layout_extraction_has_signal(extraction):
                raw["layout_qianfan_status"] = "insufficient_signal"
                extraction = None
        elif self.qianfan_layout:
            raw["layout_qianfan_status"] = "unavailable"

        if extraction is None:
            header_payload = self._extract_header_with_moondream(image) if self.moondream else None
            players_payload = self._extract_players_with_moondream(image) if self.moondream else None
            if header_payload:
                raw["header_moondream"] = header_payload
            if players_payload:
                raw["players_moondream"] = players_payload
            extraction = self._build_initial_extraction(header_payload, players_payload)

        if self.trocr:
            self._rescue_header_with_trocr(image, extraction, raw)
            self._refine_players_with_trocr(image, extraction, raw)

        if mineru_payload:
            adjudication = adjudicate_registration_extraction(
                extraction,
                mineru_payload,
                mineru_text=str((raw.get("mineru") or {}).get("text_excerpt") or ""),
            )
            raw["adjudication"] = {
                "applied": adjudication.applied,
                "error": adjudication.error,
                **(adjudication.raw or {}),
            }
            extraction = adjudication.extraction

        self._enforce_mexico_domain(extraction, raw)
        self._annotate_backend_status(raw)

        extraction["overall_confidence"] = self._compute_overall_confidence(extraction)
        extraction["notes"] = self._build_notes(extraction, raw)
        return {"extraction": extraction, "raw": raw}

    def extract_pdf_pages(
        self,
        pages: Iterable[Image.Image],
        *,
        source_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        payloads = [self.extract(page_image) for page_image in pages]
        return self.aggregate_page_payloads(payloads, source_path=source_path)

    def aggregate_page_payloads(
        self,
        payloads: Iterable[Dict[str, Any]],
        *,
        source_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        page_results: List[Dict[str, Any]] = []
        teams: List[Dict[str, Any]] = []
        current_team: Optional[Dict[str, Any]] = None
        current_back_count = 0

        for page_number, payload in enumerate(payloads, 1):
            extraction = payload.get("extraction") or {}
            raw = payload.get("raw") or {}
            template = raw.get("ctt_template") if isinstance(raw.get("ctt_template"), dict) else {}
            side = template.get("side") or ("front" if extraction.get("is_front", True) else "back")
            page_type = "front" if side == "front" else "back"

            if page_type == "front" or current_team is None:
                current_team = {
                    "team": extraction.get("team") or {},
                    "manager": extraction.get("manager"),
                    "responsables": list(extraction.get("responsables") or []),
                    "players": [],
                    "pages": [],
                    "front_page_number": page_number,
                    "back_page_count": 0,
                    "form_type": extraction.get("form_type"),
                    "tournament": template.get("tournament"),
                    "sport": template.get("sport"),
                }
                teams.append(current_team)
                current_back_count = 0

            if page_type == "back":
                current_back_count += 1
                current_team["back_page_count"] = current_back_count

            players = []
            for player in extraction.get("players") or []:
                player_copy = dict(player)
                visible_number = player_copy.get("visible_player_number")
                if visible_number is None:
                    visible_number = player_copy.get("continuous_player_number")
                try:
                    visible_int = int(visible_number)
                except Exception:
                    visible_int = len(current_team["players"]) + 1

                if page_type == "back":
                    continuous_number = visible_int + max(0, current_back_count - 1) * 12
                else:
                    continuous_number = visible_int

                player_copy["document_page_number"] = page_number
                player_copy["page_type"] = page_type
                player_copy["visible_player_number"] = visible_int
                player_copy["continuous_player_number"] = continuous_number
                players.append(player_copy)

            current_team["players"].extend(players)
            current_team["pages"].append(
                {
                    "page_number": page_number,
                    "page_type": page_type,
                    "player_count": len(players),
                    "template_id": template.get("template_id"),
                    "back_page_repeatable": bool(template.get("back_page_repeatable")),
                }
            )
            page_results.append(
                {
                    "page_number": page_number,
                    "page_type": page_type,
                    "team_index": len(teams),
                    "player_count": len(players),
                    "team_name": (current_team.get("team") or {}).get("name"),
                    "raw": raw,
                }
            )

        return {
            "document": {
                "source_path": source_path,
                "page_count": len(page_results),
                "team_count": len(teams),
                "form_type": "copa_telmex_telcel_futbol_2026",
                "tournament": "Copa Telmex-Telcel",
                "sport": "futbol",
            },
            "teams": teams,
            "pages": page_results,
        }

    def _extract_with_template_alignment(
        self,
        image: Image.Image,
        raw: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        if not self.template_aligner or not self.trocr:
            return None

        with tempfile.TemporaryDirectory(prefix="ctt_template_ocr_") as out_dir:
            result = self.template_aligner.process_image(image, out_dir=out_dir, prefix="scan")
            if not isinstance(result, dict):
                return None

            raw["ctt_template"] = {
                "template_id": "copa_telmex_telcel_futbol_2026",
                "tournament": "Copa Telmex-Telcel",
                "sport": "futbol",
                "scope": "ctt_futbol_only",
                "side": result.get("side"),
                "page_type": "front" if result.get("side") == "front" else "back",
                "back_page_repeatable": result.get("side") == "back",
                "align_info": result.get("align_info"),
                "degraded_mode": bool(result.get("degraded_mode")),
                "field_count": len(result.get("fields") or {}),
            }

            fields = result.get("fields") if isinstance(result.get("fields"), dict) else {}
            if not fields:
                return None

            field_reads: Dict[str, Dict[str, Any]] = {}
            team = {
                "name": "Unknown Team",
                "category": None,
                "gender": None,
                "league": None,
                "municipality": None,
                "state": None,
                "confidence": 0.0,
            }
            manager: Optional[Dict[str, Any]] = None
            responsables: List[Dict[str, Any]] = []
            players: List[Dict[str, Any]] = []

            header_mapping = {
                "equipo_nombre": "name",
                "categoria": "category",
                "rama": "gender",
                "liga": "league",
                "municipio": "municipality",
                "estado": "state",
            }
            header_confidences: List[float] = []
            header_reads: Dict[str, OCRTextResult] = {}

            header_targets = {**header_mapping, "correo": "email"}
            for source_key, target_key in header_targets.items():
                read = self._read_template_field(fields.get(f"header.{source_key}"))
                if read.text:
                    header_reads[source_key] = read
                    if target_key == "email":
                        field_reads[f"header.{source_key}"] = {
                            "text": read.text,
                            "confidence": read.confidence,
                        }
                        continue
                    team[target_key] = _clean_text(read.text)
                    header_confidences.append(read.confidence)
                    field_reads[f"header.{source_key}"] = {
                        "text": read.text,
                        "confidence": read.confidence,
                    }

            representative = self._read_template_field(
                fields.get("header.representante_nombre")
            )
            if representative.text:
                manager = {
                    "name": _clean_text(representative.text),
                    "role": "delegado",
                    "phone": None,
                    "email": (
                        _clean_text(header_reads["correo"].text)
                        if header_reads.get("correo")
                        else None
                    ),
                    "confidence": representative.confidence,
                }
                field_reads["header.representante_nombre"] = {
                    "text": representative.text,
                    "confidence": representative.confidence,
                }

            for card_name, role in (
                ("director_tecnico", "director_tecnico"),
                ("auxiliar", "auxiliar"),
            ):
                responsable = self._build_template_person(
                    fields=fields,
                    card_name=card_name,
                    role=role,
                    source_page_number=1,
                    visible_slot_label=(
                        "Director Técnico"
                        if card_name == "director_tecnico"
                        else "Auxiliar"
                    ),
                    field_reads=field_reads,
                )
                if responsable:
                    responsables.append(responsable)

            card_names = sorted(
                {
                    key.split(".")[1]
                    for key in fields.keys()
                    if key.startswith("cards.")
                },
                key=self._template_card_sort_key,
            )

            for card_name in card_names:
                if not card_name.startswith("jugador_"):
                    continue
                visible_number = self._template_card_sort_key(card_name)[0]
                if visible_number == 10_000:
                    continue
                player = self._build_template_person(
                    fields=fields,
                    card_name=card_name,
                    role=None,
                    source_page_number=1 if result.get("side") == "front" else 2,
                    visible_slot_label=f"Jugador {visible_number}",
                    field_reads=field_reads,
                )
                if not player:
                    continue

                full_name = player.pop("name", None)
                players.append(
                    {
                        "name": full_name or None,
                        "first_name": None,
                        "paternal_surname": None,
                        "maternal_surname": None,
                        "birth_date": player.get("birth_date"),
                        "curp": player.get("curp"),
                        "jersey_number": None,
                        "position": None,
                        "photo_region": None,
                        "confidence": player.get("confidence", 0.0),
                        "needs_review": player.get("needs_review", True),
                        "source_page_number": player.get("source_page_number"),
                        "visible_slot_label": player.get("visible_slot_label"),
                        "visible_player_number": visible_number,
                        "continuous_player_number": visible_number,
                    }
                )

            if header_confidences:
                team["confidence"] = float(
                    sum(header_confidences) / len(header_confidences)
                )
            if field_reads:
                raw["ctt_template_field_reads"] = field_reads

            extraction = {
                "team": team,
                "responsables": responsables,
                "manager": manager,
                "players": players,
                "overall_confidence": 0.0,
                "notes": None,
                "form_type": "copa_telmex_telcel_futbol_2026",
                "is_front": result.get("side") == "front",
            }
            if self._template_extraction_has_signal(extraction):
                return extraction
            return None

    def _build_template_person(
        self,
        *,
        fields: Dict[str, Any],
        card_name: str,
        role: Optional[str],
        source_page_number: int,
        visible_slot_label: str,
        field_reads: Dict[str, Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        nombre = self._read_template_field(fields.get(f"cards.{card_name}.nombre"))
        apellidos = self._read_template_field(
            fields.get(f"cards.{card_name}.apellidos")
        )
        nacimiento = self._read_template_field(
            fields.get(f"cards.{card_name}.nacimiento")
        )
        curp = self._read_template_field(fields.get(f"cards.{card_name}.curp"))

        for field_key, read in (
            (f"cards.{card_name}.nombre", nombre),
            (f"cards.{card_name}.apellidos", apellidos),
            (f"cards.{card_name}.nacimiento", nacimiento),
            (f"cards.{card_name}.curp", curp),
        ):
            if read.text:
                field_reads[field_key] = {
                    "text": read.text,
                    "confidence": read.confidence,
                }

        full_name = " ".join(
            part
            for part in [_clean_text(nombre.text), _clean_text(apellidos.text)]
            if part
        ).strip()
        birth_date = _extract_date(nacimiento.text) or _clean_text(nacimiento.text)
        curp_value = _extract_curp(curp.text) or _clean_text(curp.text)
        confidence_values = [
            read.confidence
            for read in (nombre, apellidos, nacimiento, curp)
            if read.text
        ]
        if not full_name and not birth_date and not curp_value:
            return None

        needs_review = not full_name or bool(
            curp_value and not _extract_curp(curp_value)
        )
        payload = {
            "name": full_name or None,
            "birth_date": birth_date,
            "curp": curp_value,
            "confidence": (
                float(sum(confidence_values) / len(confidence_values))
                if confidence_values
                else 0.0
            ),
            "needs_review": needs_review,
            "source_page_number": source_page_number,
            "visible_slot_label": visible_slot_label,
        }
        if role:
            payload["role"] = role
        return payload

    def _extract_with_mineru(
        self,
        image: Image.Image,
        raw: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp_path = Path(tmp.name)
            image.save(tmp_path, format="JPEG", quality=95)
        try:
            result = parse_document_bytes(tmp_path.read_bytes(), suffix=".jpg")
        finally:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                logger.debug("Could not delete MinerU registration temp image", exc_info=True)

        if not result.enabled:
            return None

        raw["mineru"] = {
            "error": result.error,
            "content_blocks": len(result.content_list),
            "text_length": len(result.text or ""),
            "text_excerpt": (result.text or "")[:4000],
            "returncode": result.raw.get("returncode"),
        }
        if not result.has_text:
            return None

        extraction = self._build_initial_extraction_from_mineru_text(result.text)
        return extraction if self._template_extraction_has_signal(extraction) else None

    def _build_initial_extraction_from_mineru_text(self, text: str) -> Dict[str, Any]:
        team_name = _find_labeled_text(text, ["equipo", "nombre del equipo", "team"])
        category = _find_labeled_text(text, ["categoria", "categoría", "category"])
        gender = _find_labeled_text(text, ["rama", "genero", "género", "gender"])
        league = _find_labeled_text(text, ["liga", "league"])
        municipality = _find_labeled_text(text, ["municipio", "municipality"])
        state = _find_labeled_text(text, ["estado", "state"])
        manager_name = _find_labeled_text(
            text,
            ["delegado", "representante", "manager", "responsable"],
        )

        players: List[Dict[str, Any]] = []
        seen = set()
        for line in (text or "").splitlines():
            clean = re.sub(r"\s+", " ", line).strip(" |")
            if not clean:
                continue
            birth_date = _extract_date(clean)
            curp = _extract_curp(clean)
            name = _extract_name_candidate(clean)
            if not (name and birth_date):
                continue
            key = (name.upper(), birth_date or "", curp or "")
            if key in seen:
                continue
            seen.add(key)
            players.append(
                {
                    "name": name,
                    "first_name": None,
                    "paternal_surname": None,
                    "maternal_surname": None,
                    "birth_date": birth_date,
                    "curp": curp,
                    "jersey_number": None,
                    "position": None,
                    "photo_region": None,
                    "confidence": 0.72,
                    "needs_review": True,
                }
            )

        return {
            "team": {
                "name": team_name or "Unknown Team",
                "category": category,
                "gender": gender,
                "league": league,
                "municipality": municipality,
                "state": state,
                "confidence": 0.72 if team_name else 0.0,
            },
            "manager": (
                {
                    "name": manager_name,
                    "role": "delegado",
                    "phone": None,
                    "email": None,
                    "confidence": 0.72,
                }
                if manager_name
                else None
            ),
            "players": players,
            "overall_confidence": 0.72 if team_name or players else 0.0,
            "notes": "mineru",
        }

    def _read_template_field(self, field_info: Any) -> OCRTextResult:
        if not self.trocr or not isinstance(field_info, dict) or field_info.get("empty"):
            return OCRTextResult(text="", confidence=0.0)

        candidates: List[OCRTextResult] = []
        primary_path = field_info.get("path")
        variant_paths = list(field_info.get("variants") or [])[: self.template_trocr_max_variants]
        candidate_paths: List[str] = []
        if primary_path:
            candidate_paths.append(primary_path)
        candidate_paths.extend(path for path in variant_paths if path != primary_path)

        for path in candidate_paths:
            try:
                if not path or not Path(path).exists():
                    continue
                crop = Image.open(path).convert("RGB")
                result = self.trocr.read(crop)
                if result.text:
                    candidates.append(result)
                    if result.confidence >= 0.9:
                        break
            except Exception:
                logger.debug("Failed to OCR template crop %s", path, exc_info=True)

        if not candidates:
            return OCRTextResult(text="", confidence=0.0)
        return max(candidates, key=lambda item: (item.confidence, len(item.text or "")))

    def _template_card_sort_key(self, value: str) -> Tuple[int, str]:
        match = re.search(r"(\d+)$", value or "")
        if match:
            return (int(match.group(1)), value)
        return (10_000, value)

    def _template_extraction_has_signal(self, extraction: Dict[str, Any]) -> bool:
        team_name = ((extraction.get("team") or {}).get("name") or "").strip()
        if team_name and team_name.lower() != "unknown team":
            return True
        return bool(extraction.get("players"))

    def _annotate_backend_status(self, raw: Dict[str, Any]) -> None:
        backend = raw.setdefault("backend", {})
        if self.template_aligner:
            status = getattr(self.template_aligner, "status", None)
            if status is not None:
                backend["ctt_template"] = asdict(status)
        if self.qianfan_layout:
            status = getattr(self.qianfan_layout, "status", None)
            if status is not None:
                backend["qianfan"] = asdict(status)
        if self.moondream:
            status = getattr(self.moondream, "status", None)
            if status is not None:
                backend["moondream"] = asdict(status)
        if self.trocr:
            status = getattr(self.trocr, "status", None)
            if status is not None:
                backend["trocr"] = asdict(status)

        unavailable = []
        for key in ("ctt_template", "qianfan", "moondream", "trocr"):
            status = backend.get(key)
            if isinstance(status, dict) and status.get("configured") and not status.get("available"):
                unavailable.append(key)
        if unavailable:
            raw["backend_unavailable"] = unavailable

    def _build_initial_extraction_from_layout(
        self,
        layout_payload: Dict[str, Any],
        image: Image.Image,
    ) -> Dict[str, Any]:
        page_confidence = _coerce_float(layout_payload.get("page_confidence"), default=0.0)
        image_size = image.size
        header = layout_payload.get("header") if isinstance(layout_payload.get("header"), dict) else {}
        header_fields_payload = header.get("fields") if isinstance(header.get("fields"), dict) else {}
        team_field_regions = self._normalize_layout_field_regions(
            header_fields_payload,
            image_size=image_size,
            field_mapping={
                "team_name": "name",
                "category": "category",
                "league": "league",
                "municipality": "municipality",
                "state": "state",
                "manager_name": "manager_name",
            },
        )

        players: List[Dict[str, Any]] = []
        for player_payload in layout_payload.get("players") or []:
            if not isinstance(player_payload, dict):
                continue
            row_region = self._normalize_layout_bbox(player_payload.get("row_bbox"), image_size=image_size)
            photo_region = self._normalize_layout_bbox(player_payload.get("photo_bbox"), image_size=image_size)
            field_regions = self._normalize_layout_field_regions(
                player_payload,
                image_size=image_size,
                field_mapping={
                    "name_bbox": "name",
                    "birth_date_bbox": "birth_date",
                    "curp_bbox": "curp",
                },
            )
            if not row_region and not photo_region and not field_regions:
                continue
            players.append(
                {
                    "name": None,
                    "first_name": None,
                    "paternal_surname": None,
                    "maternal_surname": None,
                    "birth_date": None,
                    "curp": None,
                    "jersey_number": None,
                    "position": None,
                    "photo_region": photo_region,
                    "row_region": row_region,
                    "field_regions": field_regions,
                    "confidence": _coerce_float(player_payload.get("confidence"), default=page_confidence * 0.5),
                    "needs_review": True,
                }
            )

        return {
            "team": {
                "name": "Unknown Team",
                "category": None,
                "gender": None,
                "league": None,
                "municipality": None,
                "state": None,
                "confidence": page_confidence,
                "field_regions": team_field_regions,
            },
            "manager": None,
            "players": players,
            "overall_confidence": page_confidence,
            "notes": None,
        }

    def _extract_header_with_moondream(self, image: Image.Image) -> Optional[Dict[str, Any]]:
        prompt = (
            "Analiza esta cedula de registro de un equipo de futbol y devuelve SOLO JSON con "
            "{\"team\":{\"name\":string|null,\"category\":string|null,\"gender\":string|null,"
            "\"league\":string|null,\"municipality\":string|null,\"state\":string|null,\"confidence\":number},"
            "\"manager\":{\"name\":string|null,\"role\":string|null,\"phone\":string|null,"
            "\"email\":string|null,\"confidence\":number},\"notes\":string|null}. "
            "Contexto obligatorio: formulario mexicano en español. "
            "El estado debe ser una entidad federativa de México y el municipio debe estar en México. "
            "No inventes; si no se ve usa null; no uses conocimiento global."
        )
        return self.moondream.query_json(image, prompt) if self.moondream else None

    def _extract_players_with_moondream(self, image: Image.Image) -> Optional[Dict[str, Any]]:
        prompt = (
            "Analiza esta cedula de jugadores y devuelve SOLO JSON con "
            "{\"players\":[{\"name\":string|null,\"first_name\":string|null,"
            "\"paternal_surname\":string|null,\"maternal_surname\":string|null,"
            "\"birth_date\":string|null,\"curp\":string|null,\"jersey_number\":number|null,"
            "\"position\":string|null,\"photo_region\":{\"x\":number,\"y\":number,"
            "\"width\":number,\"height\":number,\"confidence\":number}|null,"
            "\"confidence\":number,\"needs_review\":boolean}],"
            "\"overall_confidence\":number,\"notes\":string|null}. "
            "Contexto obligatorio: jugadores mexicanos o hispanos en una cedula mexicana en español. "
            "No inventes nombres; no uses futbolistas famosos; incluye solo jugadores visibles."
        )
        return self.moondream.query_json(image, prompt) if self.moondream else None

    def _build_initial_extraction(
        self,
        header_payload: Optional[Dict[str, Any]],
        players_payload: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        team = ((header_payload or {}).get("team") or {}) if isinstance(header_payload, dict) else {}
        manager = ((header_payload or {}).get("manager") or {}) if isinstance(header_payload, dict) else {}
        players_in = ((players_payload or {}).get("players") or []) if isinstance(players_payload, dict) else []

        players: List[Dict[str, Any]] = []
        for player in players_in:
            if not isinstance(player, dict):
                continue
            normalized = {
                "name": _clean_text(player.get("name")),
                "first_name": _clean_text(player.get("first_name")),
                "paternal_surname": _clean_text(player.get("paternal_surname")),
                "maternal_surname": _clean_text(player.get("maternal_surname")),
                "birth_date": _clean_text(player.get("birth_date")),
                "curp": _clean_text(player.get("curp")),
                "jersey_number": player.get("jersey_number"),
                "position": _clean_text(player.get("position")),
                "photo_region": self._normalize_photo_region(player.get("photo_region")),
                "confidence": _coerce_float(player.get("confidence"), default=0.0),
                "needs_review": bool(player.get("needs_review")),
            }
            if normalized["name"] or normalized["birth_date"] or normalized["curp"]:
                players.append(normalized)

        return {
            "team": {
                "name": _clean_text(team.get("name")) or "Unknown Team",
                "category": _clean_text(team.get("category")),
                "gender": _clean_text(team.get("gender")),
                "league": _clean_text(team.get("league")),
                "municipality": _clean_text(team.get("municipality")),
                "state": _clean_text(team.get("state")),
                "confidence": _coerce_float(team.get("confidence"), default=0.0),
            },
            "manager": self._normalize_manager(manager),
            "players": players,
            "overall_confidence": _coerce_float((players_payload or {}).get("overall_confidence"), default=0.0),
            "notes": _clean_text((players_payload or {}).get("notes")) or _clean_text((header_payload or {}).get("notes")),
        }

    def _normalize_manager(self, manager: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(manager, dict):
            return None
        name = _clean_text(manager.get("name"))
        if not name:
            return None
        return {
            "name": name,
            "role": _clean_text(manager.get("role")),
            "phone": _clean_text(manager.get("phone")),
            "email": _clean_text(manager.get("email")),
            "confidence": _coerce_float(manager.get("confidence"), default=0.0),
        }

    def _normalize_photo_region(self, value: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(value, dict):
            return None
        try:
            x = max(0, int(value.get("x", 0)))
            y = max(0, int(value.get("y", 0)))
            width = max(10, int(value.get("width", 0)))
            height = max(10, int(value.get("height", 0)))
        except Exception:
            return None
        return {
            "x": x,
            "y": y,
            "width": width,
            "height": height,
            "confidence": _coerce_float(value.get("confidence"), default=0.0),
        }

    def _normalize_layout_field_regions(
        self,
        payload: Dict[str, Any],
        *,
        image_size: Tuple[int, int],
        field_mapping: Dict[str, str],
    ) -> Dict[str, Dict[str, Any]]:
        regions: Dict[str, Dict[str, Any]] = {}
        for source_key, target_key in field_mapping.items():
            value = payload.get(source_key)
            if isinstance(value, dict) and "bbox" in value:
                value = value.get("bbox")
            region = self._normalize_layout_bbox(value, image_size=image_size)
            if region:
                regions[target_key] = region
        return regions

    def _normalize_layout_bbox(
        self,
        value: Any,
        *,
        image_size: Tuple[int, int],
    ) -> Optional[Dict[str, Any]]:
        if value is None:
            return None

        if isinstance(value, dict):
            if "bbox" in value and isinstance(value.get("bbox"), (dict, list, tuple)):
                return self._normalize_layout_bbox(value["bbox"], image_size=image_size)
            x = value.get("x")
            y = value.get("y")
            width = value.get("width")
            height = value.get("height")
            if None not in (x, y, width, height):
                return self._scale_bbox(float(x), float(y), float(width), float(height), image_size=image_size)
            x1 = value.get("x1")
            y1 = value.get("y1")
            x2 = value.get("x2")
            y2 = value.get("y2")
            if None not in (x1, y1, x2, y2):
                return self._scale_bbox(
                    float(x1),
                    float(y1),
                    float(x2) - float(x1),
                    float(y2) - float(y1),
                    image_size=image_size,
                )

        if isinstance(value, (list, tuple)) and len(value) >= 4:
            x1, y1, x2, y2 = value[:4]
            return self._scale_bbox(
                float(x1),
                float(y1),
                float(x2) - float(x1),
                float(y2) - float(y1),
                image_size=image_size,
            )
        return None

    def _scale_bbox(
        self,
        x: float,
        y: float,
        width: float,
        height: float,
        *,
        image_size: Tuple[int, int],
    ) -> Optional[Dict[str, Any]]:
        image_width, image_height = image_size
        if width <= 0 or height <= 0:
            return None

        scale = 1.0
        max_value = max(abs(x), abs(y), abs(width), abs(height))
        if max_value <= 1.5:
            scale = 1.0
            px = int(round(x * image_width))
            py = int(round(y * image_height))
            pw = int(round(width * image_width))
            ph = int(round(height * image_height))
        elif max_value <= 1000.0:
            scale = 1000.0
            px = int(round((x / scale) * image_width))
            py = int(round((y / scale) * image_height))
            pw = int(round((width / scale) * image_width))
            ph = int(round((height / scale) * image_height))
        else:
            px = int(round(x))
            py = int(round(y))
            pw = int(round(width))
            ph = int(round(height))

        px = max(0, min(image_width - 1, px))
        py = max(0, min(image_height - 1, py))
        pw = max(10, min(image_width - px, pw))
        ph = max(10, min(image_height - py, ph))
        return {"x": px, "y": py, "width": pw, "height": ph, "confidence": 0.0}

    def _layout_extraction_has_signal(self, extraction: Dict[str, Any]) -> bool:
        team_regions = (extraction.get("team") or {}).get("field_regions") or {}
        if team_regions:
            return True
        for player in extraction.get("players") or []:
            if player.get("row_region") or player.get("photo_region") or player.get("field_regions"):
                return True
        return False

    def _enforce_mexico_domain(self, extraction: Dict[str, Any], raw: Dict[str, Any]) -> None:
        team = extraction.get("team") or {}
        notes: List[str] = []

        team_name = team.get("name")
        if team_name:
            team_validation = validate_team_name(team_name)
            if team_validation.status != ValidationStatus.ACCEPT:
                team["confidence"] = min(team.get("confidence") or 0.0, 0.45)
                notes.append("team_name_suspicious")

        canonical_state = canonicalize_mexican_state(team.get("state"))
        if canonical_state:
            team["state"] = canonical_state
        elif team.get("state"):
            notes.append("state_not_mexican")
            team["state"] = None
            team["confidence"] = min(team.get("confidence") or 0.0, 0.30)

        municipality = team.get("municipality")
        if municipality:
            municipality_validation = validate_name_field(municipality)
            if municipality_validation.status != ValidationStatus.ACCEPT:
                notes.append("municipality_suspicious")
                team["municipality"] = None
                team["confidence"] = min(team.get("confidence") or 0.0, 0.35)

        manager = extraction.get("manager")
        if isinstance(manager, dict) and manager.get("name"):
            manager_validation = validate_name_field(manager["name"])
            if manager_validation.status != ValidationStatus.ACCEPT:
                manager["confidence"] = min(manager.get("confidence") or 0.0, 0.40)
                notes.append("manager_name_suspicious")

        for player in extraction.get("players") or []:
            identity = evaluate_player_identity_integrity(
                player.get("name"),
                birth_date=player.get("birth_date"),
                curp=player.get("curp"),
                confidence=player.get("confidence"),
                validator=self.names_validator,
            )
            if identity.reasons:
                player["needs_review"] = True
                player["confidence"] = min(player.get("confidence") or 0.0, 0.45)
                player["integrity_reasons"] = identity.reasons
                notes.extend(identity.reasons)

            if player.get("first_name"):
                first_name_validation = self.names_validator.validate_name(
                    player["first_name"],
                    player.get("confidence"),
                    is_surname=False,
                )
                if first_name_validation.get("needs_human_review"):
                    player["needs_review"] = True
                    player.setdefault("integrity_reasons", []).append("nombre_no_confirmado_en_catalogo_mexicano")

        raw["domain_enforcement"] = sorted(set(notes))

    def _rescue_header_with_trocr(self, image: Image.Image, extraction: Dict[str, Any], raw: Dict[str, Any]) -> None:
        if not self.trocr:
            return
        self._rescue_header_with_trocr_regions(image, extraction, raw)
        width, height = image.size
        header_crop = image.crop((0, 0, width, int(height * 0.30)))
        result = self.trocr.read(header_crop)
        if not result.text:
            return
        raw["header_trocr"] = {"text": result.text, "confidence": result.confidence}
        text = result.text
        team = extraction["team"]

        for label, key in (
            ("equipo", "name"),
            ("estado", "state"),
            ("municipio", "municipality"),
            ("liga", "league"),
            ("categoria", "category"),
        ):
            if team.get(key):
                continue
            match = re.search(
                rf"{label}\s*[:\-]?\s*([A-Za-zÁÉÍÓÚÑÜáéíóúñü0-9 .,'/-]+)",
                text,
                flags=re.I,
            )
            if match:
                candidate = _clean_text(match.group(1))
                if candidate:
                    team[key] = candidate
                    team["confidence"] = max(team.get("confidence") or 0.0, min(0.70, result.confidence))

    def _rescue_header_with_trocr_regions(
        self,
        image: Image.Image,
        extraction: Dict[str, Any],
        raw: Dict[str, Any],
    ) -> None:
        team = extraction.get("team") or {}
        field_regions = team.get("field_regions") or {}
        if not isinstance(field_regions, dict) or not field_regions:
            return

        team_field_map = {
            "name": "name",
            "category": "category",
            "league": "league",
            "municipality": "municipality",
            "state": "state",
        }
        field_reads: Dict[str, Dict[str, Any]] = {}
        confidences: List[float] = []

        for source_key, target_key in team_field_map.items():
            region = field_regions.get(source_key)
            if not region:
                continue
            crop = self._crop_region(image, region, pad_ratio=0.05)
            if crop is None:
                continue
            result = self.trocr.read(crop)
            field_reads[source_key] = {"text": result.text, "confidence": result.confidence}
            candidate = _clean_text(result.text)
            existing_value = _clean_text(team.get(target_key))
            if candidate and (not existing_value or (target_key == "name" and existing_value == "Unknown Team")):
                team[target_key] = candidate
                confidences.append(result.confidence)

        manager_region = field_regions.get("manager_name")
        if manager_region:
            crop = self._crop_region(image, manager_region, pad_ratio=0.05)
            if crop is not None:
                result = self.trocr.read(crop)
                field_reads["manager_name"] = {"text": result.text, "confidence": result.confidence}
                manager_name = _clean_text(result.text)
                if manager_name and not extraction.get("manager"):
                    extraction["manager"] = {
                        "name": manager_name,
                        "role": "delegado",
                        "phone": None,
                        "email": None,
                        "confidence": result.confidence,
                    }
                    confidences.append(result.confidence)

        if confidences:
            team["confidence"] = max(team.get("confidence") or 0.0, float(sum(confidences) / len(confidences)))
        if field_reads:
            raw["header_trocr_regions"] = field_reads

    def _refine_players_with_trocr(self, image: Image.Image, extraction: Dict[str, Any], raw: Dict[str, Any]) -> None:
        if not self.trocr:
            return
        width, height = image.size
        refinements: List[Dict[str, Any]] = []
        for index, player in enumerate(extraction.get("players") or []):
            field_confidences: List[float] = []
            field_regions = player.get("field_regions") or {}
            if isinstance(field_regions, dict) and field_regions:
                region_reads: Dict[str, Dict[str, Any]] = {}
                for field_name in ("name", "birth_date", "curp"):
                    region = field_regions.get(field_name)
                    if not region:
                        continue
                    crop = self._crop_region(image, region, pad_ratio=0.04)
                    if crop is None:
                        continue
                    result = self.trocr.read(crop)
                    if not result.text:
                        continue
                    region_reads[field_name] = {"text": result.text, "confidence": result.confidence}
                    field_confidences.append(result.confidence)
                    if field_name == "name":
                        candidate = _extract_name_candidate(result.text) or _clean_text(result.text)
                        if candidate and (not player.get("name") or player.get("needs_review")):
                            player["name"] = candidate
                            player["needs_review"] = False
                    elif field_name == "birth_date":
                        birth_date = _extract_date(result.text) or _clean_text(result.text)
                        if birth_date and not player.get("birth_date"):
                            player["birth_date"] = birth_date
                    elif field_name == "curp":
                        curp = _extract_curp(result.text) or _clean_text(result.text)
                        if curp and not player.get("curp"):
                            player["curp"] = curp
                if region_reads:
                    refinements.append({"index": index, "regions": region_reads})

            row_region = player.get("row_region")
            if row_region and (not player.get("name") or not player.get("birth_date") or not player.get("curp")):
                crop = self._crop_region(image, row_region, pad_ratio=0.03)
                if crop is not None:
                    result = self.trocr.read(crop)
                    if result.text:
                        refinements.append({"index": index, "row_text": result.text, "confidence": result.confidence})
                        field_confidences.append(result.confidence)
                        if not player.get("name"):
                            name_candidate = _extract_name_candidate(result.text)
                            if name_candidate:
                                player["name"] = name_candidate
                                player["needs_review"] = False
                        if not player.get("birth_date"):
                            birth_date = _extract_date(result.text)
                            if birth_date:
                                player["birth_date"] = birth_date
                        if not player.get("curp"):
                            curp = _extract_curp(result.text)
                            if curp:
                                player["curp"] = curp

            region = player.get("photo_region")
            if region:
                x1 = min(width, max(0, region["x"] + region["width"]))
                y0 = max(0, region["y"] - int(region["height"] * 0.15))
                y1 = min(height, region["y"] + region["height"] + int(region["height"] * 0.15))
                if x1 < width and y1 > y0:
                    crop = image.crop((x1, y0, width, y1))
                    result = self.trocr.read(crop)
                    if result.text:
                        refinements.append({"index": index, "text": result.text, "confidence": result.confidence})
                        field_confidences.append(result.confidence)

                        if (not player.get("name") or player.get("needs_review")):
                            name_candidate = _extract_name_candidate(result.text)
                            if name_candidate:
                                player["name"] = player.get("name") or name_candidate
                                player["needs_review"] = False

                        if not player.get("birth_date"):
                            birth_date = _extract_date(result.text)
                            if birth_date:
                                player["birth_date"] = birth_date

                        if not player.get("curp"):
                            curp = _extract_curp(result.text)
                            if curp:
                                player["curp"] = curp

            if not _looks_like_name(player.get("name") or ""):
                player["needs_review"] = True
            if field_confidences:
                player["confidence"] = max(
                    player.get("confidence") or 0.0,
                    float(sum(field_confidences) / len(field_confidences)),
                )

        if refinements:
            raw["player_trocr_refinements"] = refinements

    def _crop_region(
        self,
        image: Image.Image,
        region: Dict[str, Any],
        *,
        pad_ratio: float = 0.0,
    ) -> Optional[Image.Image]:
        if not isinstance(region, dict):
            return None
        width, height = image.size
        x = int(region.get("x", 0))
        y = int(region.get("y", 0))
        w = int(region.get("width", 0))
        h = int(region.get("height", 0))
        if w <= 0 or h <= 0:
            return None
        pad_x = int(math.ceil(w * pad_ratio))
        pad_y = int(math.ceil(h * pad_ratio))
        x0 = max(0, x - pad_x)
        y0 = max(0, y - pad_y)
        x1 = min(width, x + w + pad_x)
        y1 = min(height, y + h + pad_y)
        if x1 <= x0 or y1 <= y0:
            return None
        return image.crop((x0, y0, x1, y1))

    def _compute_overall_confidence(self, extraction: Dict[str, Any]) -> float:
        values: List[float] = []
        team_conf = _coerce_float(extraction.get("team", {}).get("confidence"), 0.0)
        if team_conf:
            values.append(team_conf)
        for player in extraction.get("players") or []:
            conf = _coerce_float(player.get("confidence"), 0.0)
            if conf:
                values.append(conf)
        if not values:
            return 0.0
        return round(float(sum(values) / len(values)), 4)

    def _build_notes(self, extraction: Dict[str, Any], raw: Dict[str, Any]) -> str:
        notes: List[str] = []
        players = extraction.get("players") or []
        review_count = sum(1 for p in players if p.get("needs_review"))
        unavailable = raw.get("backend_unavailable") or []
        if raw.get("header_moondream"):
            notes.append("header=moondream")
        if raw.get("players_moondream"):
            notes.append("players=moondream")
        if raw.get("layout_qianfan"):
            notes.append("layout=qianfan")
        if raw.get("ctt_template"):
            notes.append("layout=ctt_template")
        if raw.get("mineru"):
            notes.append("parse=mineru")
        if raw.get("header_trocr"):
            notes.append("header_refined=trocr")
        if raw.get("header_trocr_regions"):
            notes.append("header_regions_refined=trocr")
        if raw.get("player_trocr_refinements"):
            notes.append("player_rows_refined=trocr")
        if raw.get("domain_enforcement"):
            notes.append("domain=" + ",".join(sorted(set(raw["domain_enforcement"]))))
        if unavailable:
            notes.append("backend_unavailable=" + ",".join(unavailable))
        notes.append(f"players={len(players)}")
        if review_count:
            notes.append(f"needs_review={review_count}")
        return "; ".join(notes)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run local OCR on a registration form image")
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument("--image", help="Path to the JPEG/PNG image")
    input_group.add_argument("--pdf", help="Path to a multipage registration PDF")
    parser.add_argument(
        "--pdf-provider",
        choices=("local", "openai"),
        default="local",
        help="OCR provider for --pdf pages",
    )
    parser.add_argument("--pdf-dpi", type=int, default=200, help="DPI for --pdf rendering")
    parser.add_argument(
        "--pdf-max-pages",
        type=int,
        default=0,
        help="Optional maximum number of PDF pages to process",
    )
    args = parser.parse_args()

    input_path = Path(args.image or args.pdf).resolve()
    if not input_path.exists():
        print(json.dumps({"error": "input_not_found", "path": str(input_path)}))
        return 2

    try:
        if args.pdf:
            pages = _render_pdf_to_images(input_path, dpi=max(72, int(args.pdf_dpi)))
            if args.pdf_max_pages and args.pdf_max_pages > 0:
                pages = pages[: int(args.pdf_max_pages)]
            if args.pdf_provider == "openai":
                extractor = LocalRegistrationOCR()
                page_payloads = [
                    _openai_page_payload(page, page_number=page_number)
                    for page_number, page in enumerate(pages, 1)
                ]
                payload = extractor.aggregate_page_payloads(
                    page_payloads,
                    source_path=str(input_path),
                )
            else:
                os.environ.setdefault("LOCAL_TROCR_NUM_BEAMS", "1")
                os.environ.setdefault("LOCAL_TROCR_MAX_LENGTH", "48")
                extractor = LocalRegistrationOCR()
                payload = extractor.extract_pdf_pages(pages, source_path=str(input_path))
        else:
            extractor = LocalRegistrationOCR()
            image = Image.open(input_path).convert("RGB")
            payload = extractor.extract(image)
        print(json.dumps(payload, ensure_ascii=True))
        return 0
    except Exception as exc:
        logger.exception("Local OCR execution failed")
        print(json.dumps({"error": "local_ocr_exception", "message": str(exc)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
