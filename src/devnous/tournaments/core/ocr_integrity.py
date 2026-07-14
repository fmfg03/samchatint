"""
Integrity helpers for tournament OCR imports.

These utilities harden the OCR pipeline before player rows are accepted:
- suspicious name detection
- lightweight photo fingerprinting
- duplicate photo comparison
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Sequence, Tuple

from PIL import Image, ImageOps

from devnous.validation import (
    ESTADOS_MEXICO,
    MexicanNamesValidator,
    get_curp_validator,
    validate_name_field,
)
from devnous.validation.hard_validator import ValidationStatus


DISALLOWED_FAMOUS_PLAYER_NAMES = {
    "CRISTIANO RONALDO",
    "CRISTIANO ROLANDO",
    "LIONEL MESSI",
    "LEO MESSI",
}

MEXICAN_STATE_ALIASES = {
    "AGUASCALIENTES": "Aguascalientes",
    "BAJA CALIFORNIA": "Baja California",
    "BAJA CALIFORNIA SUR": "Baja California Sur",
    "CAMPECHE": "Campeche",
    "CHIAPAS": "Chiapas",
    "CHIHUAHUA": "Chihuahua",
    "COAHUILA": "Coahuila",
    "COAHUILA DE ZARAGOZA": "Coahuila",
    "COLIMA": "Colima",
    "CIUDAD DE MEXICO": "Ciudad de Mexico",
    "CIUDAD DE MEXICO CDMX": "Ciudad de Mexico",
    "CDMX": "Ciudad de Mexico",
    "DISTRITO FEDERAL": "Ciudad de Mexico",
    "DF": "Ciudad de Mexico",
    "DURANGO": "Durango",
    "GUANAJUATO": "Guanajuato",
    "GUERRERO": "Guerrero",
    "HIDALGO": "Hidalgo",
    "JALISCO": "Jalisco",
    "ESTADO DE MEXICO": "Estado de Mexico",
    "EDO MEX": "Estado de Mexico",
    "EDOMEX": "Estado de Mexico",
    "MEXICO": "Estado de Mexico",
    "MICHOACAN": "Michoacan",
    "MICHOACAN DE OCAMPO": "Michoacan",
    "MORELOS": "Morelos",
    "NAYARIT": "Nayarit",
    "NUEVO LEON": "Nuevo Leon",
    "OAXACA": "Oaxaca",
    "PUEBLA": "Puebla",
    "QUERETARO": "Queretaro",
    "QUINTANA ROO": "Quintana Roo",
    "SAN LUIS POTOSI": "San Luis Potosi",
    "SINALOA": "Sinaloa",
    "SONORA": "Sonora",
    "TABASCO": "Tabasco",
    "TAMAULIPAS": "Tamaulipas",
    "TLAXCALA": "Tlaxcala",
    "VERACRUZ": "Veracruz",
    "VERACRUZ DE IGNACIO DE LA LLAVE": "Veracruz",
    "YUCATAN": "Yucatan",
    "ZACATECAS": "Zacatecas",
}


@dataclass
class NameIntegrityResult:
    normalized_name: str
    needs_review: bool
    reasons: List[str] = field(default_factory=list)


def canonicalize_mexican_state(value: Optional[str]) -> Optional[str]:
    normalized = normalize_name_key(value)
    if not normalized:
        return None
    return MEXICAN_STATE_ALIASES.get(normalized)


def parse_birth_date_text(value: Optional[str]) -> Optional[datetime]:
    text = (value or "").strip()
    if not text:
        return None
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def normalize_name_key(value: Optional[str]) -> str:
    raw = (value or "").strip().upper()
    if not raw:
        return ""
    raw = unicodedata.normalize("NFD", raw)
    raw = "".join(ch for ch in raw if unicodedata.category(ch) != "Mn")
    raw = re.sub(r"[^A-Z ]+", " ", raw)
    return re.sub(r"\s+", " ", raw).strip()


for _state_name in ESTADOS_MEXICO.values():
    _key = normalize_name_key(_state_name)
    if _key:
        MEXICAN_STATE_ALIASES.setdefault(_key, _state_name)


def slugify_filename(value: Optional[str], fallback: str = "player") -> str:
    text = normalize_name_key(value).lower().replace(" ", "_")
    text = re.sub(r"[^a-z0-9_]+", "", text).strip("_")
    return text or fallback


def evaluate_player_name_integrity(
    full_name: Optional[str],
    *,
    confidence: Optional[float],
    validator: Optional[MexicanNamesValidator],
) -> NameIntegrityResult:
    normalized = normalize_name_key(full_name)
    reasons: List[str] = []

    if not normalized:
        reasons.append("nombre_vacio")
        return NameIntegrityResult(normalized_name=normalized, needs_review=True, reasons=reasons)

    if normalized in DISALLOWED_FAMOUS_PLAYER_NAMES:
        reasons.append("nombre_coincide_con_jugador_famoso")

    hard = validate_name_field(full_name or "")
    if hard.status == ValidationStatus.RETRY:
        reasons.append("nombre_sospechoso_por_reglas_duras")
    elif hard.status == ValidationStatus.HUMAN:
        reasons.append("nombre_invalido_por_reglas_duras")

    if validator is not None:
        fuzzy = validator.validate_full_name(full_name or "", confidence=confidence)
        if fuzzy.get("needs_human_review"):
            reasons.append("nombre_no_confirmado_en_catalogo_mexicano")

    return NameIntegrityResult(
        normalized_name=normalized,
        needs_review=bool(reasons),
        reasons=sorted(set(reasons)),
    )


def evaluate_player_identity_integrity(
    full_name: Optional[str],
    *,
    birth_date: Optional[str],
    curp: Optional[str],
    confidence: Optional[float],
    validator: Optional[MexicanNamesValidator],
) -> NameIntegrityResult:
    result = evaluate_player_name_integrity(
        full_name,
        confidence=confidence,
        validator=validator,
    )
    reasons = list(result.reasons)
    curp_value = (curp or "").strip().upper()

    if curp_value:
        curp_validator = get_curp_validator()
        curp_result = curp_validator.validate(curp_value)
        if not curp_result.get("valid"):
            reasons.append("curp_invalido")
        else:
            parts = (full_name or "").split()
            first_name = parts[0] if parts else ""
            paternal = parts[1] if len(parts) > 1 else ""
            maternal = parts[2] if len(parts) > 2 else None
            birth_dt = parse_birth_date_text(birth_date)
            if first_name and paternal:
                validate_fn = getattr(curp_validator, "validate_against_personal_data", None)
                if validate_fn is None:
                    validate_fn = getattr(curp_validator, "validar_contra_datos", None)
                match_result = (
                    validate_fn(
                        curp=curp_value,
                        nombre=first_name,
                        apellido_paterno=paternal,
                        apellido_materno=maternal,
                        fecha_nacimiento=birth_dt,
                    )
                    if validate_fn is not None
                    else {}
                )
                mismatches = match_result.get("mismatches") or []
                if mismatches:
                    reasons.append("curp_no_coincide_con_nombre_o_fecha")
            elif birth_date:
                birth_dt = parse_birth_date_text(birth_date)
                if birth_dt and curp_result.get("data"):
                    curp_birth = curp_result["data"].fecha_nacimiento.date()
                    if curp_birth != birth_dt.date():
                        reasons.append("curp_no_coincide_con_nombre_o_fecha")

    return NameIntegrityResult(
        normalized_name=result.normalized_name,
        needs_review=bool(reasons),
        reasons=sorted(set(reasons)),
    )


def compute_sha256_hex(raw_bytes: bytes) -> str:
    return hashlib.sha256(raw_bytes).hexdigest()


def average_hash_hex(image: Image.Image, hash_size: int = 8) -> str:
    img = ImageOps.exif_transpose(image).convert("L").resize(
        (hash_size, hash_size),
        Image.Resampling.LANCZOS,
    )
    pixels = list(img.getdata())
    avg = sum(pixels) / max(len(pixels), 1)
    bits = "".join("1" if px >= avg else "0" for px in pixels)
    width = (hash_size * hash_size) // 4
    return f"{int(bits, 2):0{width}x}"


def hamming_distance_hex(left: Optional[str], right: Optional[str]) -> int:
    if not left or not right:
        return 10**9
    return (int(left, 16) ^ int(right, 16)).bit_count()


def hashes_look_duplicate(
    *,
    sha256_left: Optional[str],
    sha256_right: Optional[str],
    ahash_left: Optional[str],
    ahash_right: Optional[str],
    max_distance: int = 4,
) -> bool:
    if sha256_left and sha256_right and sha256_left == sha256_right:
        return True
    return hamming_distance_hex(ahash_left, ahash_right) <= max_distance


def image_has_photo_like_content(image: Image.Image, min_stddev: float = 8.0) -> bool:
    grayscale = ImageOps.exif_transpose(image).convert("L")
    values = list(grayscale.getdata())
    if not values:
        return False
    mean = sum(values) / len(values)
    variance = sum((px - mean) ** 2 for px in values) / len(values)
    return variance ** 0.5 >= min_stddev


def clamp_box(box: Tuple[int, int, int, int], image_size: Tuple[int, int]) -> Tuple[int, int, int, int]:
    width, height = image_size
    left, top, right, bottom = box
    left = max(0, min(left, width))
    right = max(0, min(right, width))
    top = max(0, min(top, height))
    bottom = max(0, min(bottom, height))
    if right <= left:
        right = min(width, left + 1)
    if bottom <= top:
        bottom = min(height, top + 1)
    return left, top, right, bottom


def _ctt_target_size_for(image: Image.Image) -> Tuple[int, int]:
    width, height = image.size
    return (3300, 2550) if width > height else (2550, 3300)


def _dark_content_points(
    image: Image.Image,
    *,
    max_samples: int = 180000,
) -> List[Tuple[float, float]]:
    grayscale = ImageOps.exif_transpose(image).convert("L")
    width, height = grayscale.size
    step = max(1, int(((width * height) / max(max_samples, 1)) ** 0.5))
    pixels = grayscale.load()
    points: List[Tuple[float, float]] = []
    for y in range(0, height, step):
        for x in range(0, width, step):
            if int(pixels[x, y]) < 210:
                points.append((float(x), float(y)))
    return points


def _expand_quad(
    quad: List[Tuple[float, float]],
    *,
    image_size: Tuple[int, int],
    expansion: float = 0.035,
) -> List[Tuple[float, float]]:
    width, height = image_size
    center_x = sum(point[0] for point in quad) / 4.0
    center_y = sum(point[1] for point in quad) / 4.0
    expanded: List[Tuple[float, float]] = []
    for x, y in quad:
        new_x = x + ((x - center_x) * expansion)
        new_y = y + ((y - center_y) * expansion)
        expanded.append(
            (
                max(0.0, min(float(width - 1), new_x)),
                max(0.0, min(float(height - 1), new_y)),
            )
        )
    return expanded


def _quad_area(quad: List[Tuple[float, float]]) -> float:
    area = 0.0
    for index, (x1, y1) in enumerate(quad):
        x2, y2 = quad[(index + 1) % len(quad)]
        area += (x1 * y2) - (x2 * y1)
    return abs(area) / 2.0


def _estimate_document_quad(
    image: Image.Image,
) -> Optional[List[Tuple[float, float]]]:
    points = _dark_content_points(image)
    if len(points) < 200:
        return None

    top_left = min(points, key=lambda point: point[0] + point[1])
    top_right = max(points, key=lambda point: point[0] - point[1])
    bottom_right = max(points, key=lambda point: point[0] + point[1])
    bottom_left = min(points, key=lambda point: point[0] - point[1])
    quad = _expand_quad(
        [top_left, bottom_left, bottom_right, top_right],
        image_size=image.size,
    )

    image_area = float(max(image.size[0] * image.size[1], 1))
    if _quad_area(quad) < image_area * 0.35:
        return None
    return quad


def normalize_ctt_template_image(
    image: Image.Image,
    *,
    already_canonical: bool = False,
) -> Tuple[Image.Image, Dict[str, object]]:
    """Normalize a photographed CTT page for fixed template coordinates."""
    oriented = ImageOps.exif_transpose(image).convert("RGB")
    if oriented.width > oriented.height:
        oriented = oriented.rotate(90, expand=True)

    target_size = _ctt_target_size_for(oriented)
    if already_canonical:
        if oriented.size != target_size:
            raise ValueError(
                "already-canonical CTT page must use the canonical target size"
            )
        return oriented, {
            "normalized": True,
            "method": "already_canonical",
            "source_size": image.size,
            "target_size": target_size,
        }
    quad = _estimate_document_quad(oriented)
    if quad:
        normalized = oriented.transform(
            target_size,
            Image.Transform.QUAD,
            data=(
                quad[0][0],
                quad[0][1],
                quad[1][0],
                quad[1][1],
                quad[2][0],
                quad[2][1],
                quad[3][0],
                quad[3][1],
            ),
            resample=Image.Resampling.BICUBIC,
        )
        return normalized, {
            "normalized": True,
            "method": "quad_content_transform",
            "source_size": image.size,
            "target_size": target_size,
        }

    normalized = oriented.resize(target_size, Image.Resampling.BICUBIC)
    return normalized, {
        "normalized": True,
        "method": "resize_only",
        "source_size": image.size,
        "target_size": target_size,
    }


def heuristic_photo_box(
    *,
    image_size: Tuple[int, int],
    player_index: int,
    total_players: int,
    side: str,
) -> Tuple[int, int, int, int]:
    width, height = image_size
    header_ratio = 0.18 if side == "front" else 0.10
    footer_ratio = 0.04
    table_top = int(height * header_ratio)
    table_height = max(1, int(height * (1.0 - header_ratio - footer_ratio)))
    rows = max(1, total_players)
    row_height = table_height / rows

    photo_width = max(48, int(min(width * 0.18, row_height * 0.95)))
    photo_height = max(56, int(min(row_height * 0.88, photo_width * 1.20)))

    left = int(width * 0.015)
    top = int(table_top + (player_index * row_height) + max(0, (row_height - photo_height) / 2))
    return clamp_box((left, top, left + photo_width, top + photo_height), image_size)


def crop_player_photo(
    *,
    image: Image.Image,
    photo_region: Optional[object],
    player_index: int,
    total_players: int,
    side: str,
) -> Image.Image:
    if photo_region is not None:
        if isinstance(photo_region, dict):
            x = int(photo_region.get("x", 0) or 0)
            y = int(photo_region.get("y", 0) or 0)
            width = int(photo_region.get("width", 0) or 0)
            height = int(photo_region.get("height", 0) or 0)
        else:
            x = int(getattr(photo_region, "x", 0))
            y = int(getattr(photo_region, "y", 0))
            width = int(getattr(photo_region, "width", 0))
            height = int(getattr(photo_region, "height", 0))
        box = clamp_box(
            (
                x,
                y,
                x + width,
                y + height,
            ),
            image.size,
        )
    else:
        box = heuristic_photo_box(
            image_size=image.size,
            player_index=player_index,
            total_players=total_players,
            side=side,
        )
    return image.crop(box)


def describe_integrity_reasons(reasons: Sequence[str]) -> str:
    labels = {
        "nombre_vacio": "nombre vacio",
        "nombre_coincide_con_jugador_famoso": "nombre coincide con jugador famoso",
        "nombre_sospechoso_por_reglas_duras": "nombre sospechoso por formato",
        "nombre_invalido_por_reglas_duras": "nombre invalido por formato",
        "nombre_no_confirmado_en_catalogo_mexicano": "nombre no confirmado en catalogo mexicano",
        "curp_invalido": "curp invalido",
        "curp_no_coincide_con_nombre_o_fecha": "curp no coincide con nombre o fecha",
        "foto_no_detectada_con_claridad": "foto no detectada con claridad",
        "foto_repetida_en_misma_cedula": "foto repetida en misma cedula",
        "foto_repetida_contra_jugador_existente": "foto repetida contra jugador existente",
    }
    return ", ".join(labels.get(reason, reason) for reason in reasons)
