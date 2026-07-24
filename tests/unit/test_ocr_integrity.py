from PIL import Image, ImageDraw

from devnous.tournaments.core.ocr_integrity import (
    average_hash_hex,
    canonicalize_mexican_state,
    compute_sha256_hex,
    evaluate_player_identity_integrity,
    evaluate_player_name_integrity,
    hashes_look_duplicate,
    image_has_photo_like_content,
    normalize_ctt_template_image,
)
from devnous.validation import MexicanNamesValidator


def test_evaluate_player_name_integrity_flags_famous_player_name() -> None:
    validator = MexicanNamesValidator(min_confidence=0.80)

    result = evaluate_player_name_integrity(
        "Leo Messi",
        confidence=0.99,
        validator=validator,
    )

    assert result.needs_review is True
    assert "nombre_coincide_con_jugador_famoso" in result.reasons


def test_evaluate_player_name_integrity_accepts_common_mexican_name() -> None:
    validator = MexicanNamesValidator(min_confidence=0.80)

    result = evaluate_player_name_integrity(
        "Juan Garcia Lopez",
        confidence=0.99,
        validator=validator,
    )

    assert result.needs_review is False
    assert result.reasons == []


def test_evaluate_player_identity_integrity_flags_curp_name_mismatch() -> None:
    validator = MexicanNamesValidator(min_confidence=0.80)

    result = evaluate_player_identity_integrity(
        "Juan Garcia Lopez",
        birth_date="13/03/1992",
        curp="GALP920313HDFRPD08",
        confidence=0.99,
        validator=validator,
    )

    assert result.needs_review is True
    assert "curp_no_coincide_con_nombre_o_fecha" in result.reasons


def test_canonicalize_mexican_state_accepts_cdmx_alias() -> None:
    assert canonicalize_mexican_state("CDMX") == "Ciudad de Mexico"


def test_hashes_look_duplicate_for_identical_images() -> None:
    image = Image.new("RGB", (96, 96), "white")
    draw = ImageDraw.Draw(image)
    draw.ellipse((20, 10, 76, 66), fill="gray")
    draw.rectangle((30, 68, 66, 90), fill="navy")

    raw = image.tobytes()
    sha256_value = compute_sha256_hex(raw)
    ahash_value = average_hash_hex(image)

    assert hashes_look_duplicate(
        sha256_left=sha256_value,
        sha256_right=sha256_value,
        ahash_left=ahash_value,
        ahash_right=ahash_value,
        max_distance=4,
    )


def test_image_has_photo_like_content_rejects_flat_patch() -> None:
    image = Image.new("RGB", (80, 100), "white")
    assert image_has_photo_like_content(image) is False


def test_normalize_ctt_template_image_outputs_canonical_portrait_page() -> None:
    image = Image.new("RGB", (900, 1200), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((80, 70, 820, 1130), outline="black", width=6)
    draw.rectangle((150, 260, 300, 420), outline="black", width=5)
    draw.text((330, 280), "JUGADOR 1", fill="black")

    normalized, metadata = normalize_ctt_template_image(image)

    assert normalized.size == (2550, 3300)
    assert metadata["normalized"] is True
    assert metadata["method"] in {"quad_content_transform", "resize_only"}
