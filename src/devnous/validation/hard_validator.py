"""
Hard Validation for Mexican Names - OCR Pipeline

Implements deterministic validation with routing:
- ACCEPT: Valid string, pass through
- RETRY: Suspicious string, try ensemble/preprocessing
- HUMAN: Invalid string or too suspicious, needs human review

Key principles:
- NO auto-corrections (only safe normalization)
- Detect invalid/suspicious strings
- Route to appropriate handling
- Reduce false accepts (even if false rejects increase slightly)

Author: Copa Telmex OCR System
"""

import re
import logging
from enum import Enum
from dataclasses import dataclass, field
from typing import List, Optional, Set

logger = logging.getLogger(__name__)


class ValidationStatus(Enum):
    """Validation result status for routing decisions"""
    ACCEPT = "ACCEPT"   # Valid, pass through
    RETRY = "RETRY"     # Suspicious, try ensemble/preprocessing
    HUMAN = "HUMAN"     # Invalid or too suspicious, needs human


class FieldType(Enum):
    """Type of name field being validated"""
    NOMBRES = "NOMBRES"
    APELLIDO_PATERNO = "APELLIDO_PATERNO"
    APELLIDO_MATERNO = "APELLIDO_MATERNO"
    NOMBRE_COMPLETO = "NOMBRE_COMPLETO"
    EQUIPO = "EQUIPO"


@dataclass
class ValidationMetrics:
    """Metrics captured during validation"""
    length: int = 0
    tokens: int = 0
    alpha_ratio: float = 0.0
    has_digits: bool = False
    has_invalid_chars: bool = False
    invalid_chars_found: str = ""


@dataclass
class ValidationResult:
    """Result of hard validation"""
    status: ValidationStatus
    cleaned: str
    reasons: List[str] = field(default_factory=list)
    metrics: ValidationMetrics = field(default_factory=ValidationMetrics)
    original: str = ""
    field_type: Optional[FieldType] = None

    @property
    def is_accept(self) -> bool:
        return self.status == ValidationStatus.ACCEPT

    @property
    def is_retry(self) -> bool:
        return self.status == ValidationStatus.RETRY

    @property
    def is_human(self) -> bool:
        return self.status == ValidationStatus.HUMAN


# Spanish/Mexican name connectors (particles that connect name parts)
CONNECTORS: Set[str] = {
    "DE", "DEL", "LA", "LAS", "LOS", "Y", "E",
    "DA", "DAS", "DO", "DOS",  # Portuguese origin
    "VAN", "VON",              # Germanic origin
    "MC", "MAC", "O'",         # Celtic origin
    "SAN", "SANTA",            # Saint names
}

# Valid characters for Mexican names
# Letters: A-Z, accented vowels, Ñ, Ü (for names like Güemes)
VALID_NAME_PATTERN = re.compile(r'^[A-ZÁÉÍÓÚÜÑ \-\'\.]+$')

# Pattern to detect digits
DIGIT_PATTERN = re.compile(r'[0-9]')

# Pattern to find invalid characters (everything not in valid set)
INVALID_CHAR_PATTERN = re.compile(r'[^A-ZÁÉÍÓÚÜÑ \-\'\.]')


def normalize_safe(raw: str) -> str:
    """
    Safe normalization that doesn't change meaning.

    Operations:
    - Trim whitespace
    - Collapse multiple spaces to single space
    - Convert to uppercase
    - Remove stray punctuation at start/end (OCR noise)

    Does NOT:
    - Change 0 to O (that's fuzzy correction)
    - Change 1 to I (that's fuzzy correction)
    - Remove accents (might be valid)
    """
    if not raw:
        return ""

    # Collapse multiple spaces, trim
    s = re.sub(r'\s+', ' ', raw).strip()

    # Remove stray punctuation at extremes (common OCR noise)
    s = re.sub(r'^[\.,;:\-_]+|[\.,;:\-_]+$', '', s).strip()

    # Convert to uppercase for consistent validation
    s = s.upper()

    return s


def calculate_alpha_ratio(s: str) -> float:
    """
    Calculate ratio of letter characters to total.

    Ignores spaces and valid punctuation (hyphen, apostrophe).
    Used to detect garbage text vs real names.
    """
    # Remove spaces and valid punctuation for ratio calculation
    core = re.sub(r"[ \-'\.]", "", s)

    if not core:
        return 0.0

    # Count letters (including accented)
    letters = len(re.findall(r'[A-ZÁÉÍÓÚÜÑ]', core))

    return letters / len(core)


def validate_name_field(
    raw: str,
    field_type: FieldType = FieldType.NOMBRE_COMPLETO
) -> ValidationResult:
    """
    Validate a name field with hard rules.

    Args:
        raw: Raw OCR text
        field_type: Type of field (affects some thresholds)

    Returns:
        ValidationResult with status, cleaned text, reasons, and metrics
    """
    original = raw
    cleaned = normalize_safe(raw)
    reasons: List[str] = []

    # Calculate metrics
    has_digits = bool(DIGIT_PATTERN.search(cleaned))
    invalid_matches = INVALID_CHAR_PATTERN.findall(cleaned)
    has_invalid_chars = len(invalid_matches) > 0
    invalid_chars_found = ''.join(set(invalid_matches))

    tokens_arr = cleaned.split() if cleaned else []
    tokens = len(tokens_arr)

    alpha_ratio = calculate_alpha_ratio(cleaned)

    metrics = ValidationMetrics(
        length=len(cleaned),
        tokens=tokens,
        alpha_ratio=round(alpha_ratio, 3),
        has_digits=has_digits,
        has_invalid_chars=has_invalid_chars,
        invalid_chars_found=invalid_chars_found,
    )

    # === VALIDATION RULES ===

    # Rule 1: Empty or too short
    if len(cleaned) < 2:
        reasons.append("too_short")

    # Rule 2: Too long (probably captured extra text)
    if len(cleaned) > 60:
        reasons.append("too_long")

    # Rule 3: Contains digits (names NEVER have digits)
    if has_digits:
        reasons.append("has_digits")

    # Rule 4: Contains invalid characters
    if has_invalid_chars:
        reasons.append("invalid_chars")

    # Rule 5: Low alpha ratio (too much garbage)
    if alpha_ratio < 0.85 and len(cleaned) > 0:
        reasons.append("low_alpha_ratio")

    # Rule 6: Token count validation
    # Check if name has connectors
    has_connector = any(t in CONNECTORS for t in tokens_arr)

    # Without connectors: max 6 tokens (Nombre Nombre ApPat ApPat ApMat ApMat)
    # With connectors: max 8 tokens (María de los Ángeles de la Cruz...)
    if not has_connector and tokens > 6:
        reasons.append("too_many_tokens_no_connectors")
    if has_connector and tokens > 8:
        reasons.append("too_many_tokens_even_with_connectors")

    # Rule 7: Single letter tokens (except connectors)
    for t in tokens_arr:
        if len(t) == 1 and t not in {'Y', 'E', 'O'}:  # Y, E are valid connectors
            reasons.append("single_letter_token")
            break

    # Rule 8: Repeated characters (OCR artifact)
    if re.search(r'(.)\1{3,}', cleaned):  # Same char 4+ times
        reasons.append("repeated_chars")

    # === DECISION LOGIC ===
    # Conservative: reduce false accepts

    status = ValidationStatus.ACCEPT

    # RETRY triggers (salvable with preprocessing/ensemble)
    retry_reasons = {
        "has_digits",
        "invalid_chars",
        "low_alpha_ratio",
        "single_letter_token",
        "repeated_chars",
        "too_short",
    }

    # HUMAN triggers (not salvable, need human eyes)
    human_reasons = {
        "too_long",
        "too_many_tokens_no_connectors",
        "too_many_tokens_even_with_connectors",
    }

    # Check for HUMAN triggers first
    if any(r in human_reasons for r in reasons):
        status = ValidationStatus.HUMAN
    # Then check for RETRY triggers
    elif any(r in retry_reasons for r in reasons):
        status = ValidationStatus.RETRY

    result = ValidationResult(
        status=status,
        cleaned=cleaned,
        reasons=reasons,
        metrics=metrics,
        original=original,
        field_type=field_type,
    )

    # Log result
    _log_validation_result(result)

    return result


def validate_team_name(raw: str) -> ValidationResult:
    """
    Validate team/club name with more lenient rules.

    Team names can have:
    - Numbers (e.g., "Club Deportivo 2000")
    - More tokens
    - Special characters like & or +
    """
    original = raw
    cleaned = normalize_safe(raw)
    reasons: List[str] = []

    # For team names, allow digits
    has_digits = bool(DIGIT_PATTERN.search(cleaned))  # Just track, don't reject

    # Invalid chars for team: very limited set
    team_invalid = re.compile(r'[^A-ZÁÉÍÓÚÜÑ0-9 \-\'\.&\+]')
    invalid_matches = team_invalid.findall(cleaned)
    has_invalid_chars = len(invalid_matches) > 0

    tokens_arr = cleaned.split() if cleaned else []
    tokens = len(tokens_arr)
    alpha_ratio = calculate_alpha_ratio(cleaned)

    metrics = ValidationMetrics(
        length=len(cleaned),
        tokens=tokens,
        alpha_ratio=round(alpha_ratio, 3),
        has_digits=has_digits,
        has_invalid_chars=has_invalid_chars,
        invalid_chars_found=''.join(set(invalid_matches)),
    )

    # Rules for team names (more lenient)
    if len(cleaned) < 2:
        reasons.append("too_short")

    if len(cleaned) > 80:  # Teams can have longer names
        reasons.append("too_long")

    if has_invalid_chars:
        reasons.append("invalid_chars")

    if alpha_ratio < 0.70:  # More lenient for teams with numbers
        reasons.append("low_alpha_ratio")

    if tokens > 10:
        reasons.append("too_many_tokens")

    # Decision
    status = ValidationStatus.ACCEPT

    if "too_long" in reasons or "too_many_tokens" in reasons:
        status = ValidationStatus.HUMAN
    elif reasons:
        status = ValidationStatus.RETRY

    result = ValidationResult(
        status=status,
        cleaned=cleaned,
        reasons=reasons,
        metrics=metrics,
        original=original,
        field_type=FieldType.EQUIPO,
    )

    _log_validation_result(result)
    return result


def _log_validation_result(result: ValidationResult) -> None:
    """Log validation result for debugging/auditing"""
    status_emoji = {
        ValidationStatus.ACCEPT: "✅",
        ValidationStatus.RETRY: "🔄",
        ValidationStatus.HUMAN: "👤",
    }

    emoji = status_emoji.get(result.status, "❓")
    reasons_str = ", ".join(result.reasons) if result.reasons else "none"

    logger.info(
        f"{emoji} Validation [{result.status.value}] "
        f"'{result.cleaned}' "
        f"(len={result.metrics.length}, "
        f"tokens={result.metrics.tokens}, "
        f"alpha={result.metrics.alpha_ratio:.2f}, "
        f"digits={result.metrics.has_digits}, "
        f"reasons=[{reasons_str}])"
    )


# === Batch validation for full extraction ===

@dataclass
class ExtractionValidation:
    """Validation results for a full OCR extraction"""
    team: ValidationResult
    responsables: List[ValidationResult]
    players: List[ValidationResult]

    @property
    def overall_status(self) -> ValidationStatus:
        """
        Overall status based on all fields.
        Most restrictive wins: HUMAN > RETRY > ACCEPT
        """
        all_results = [self.team] + self.responsables + self.players

        if any(r.status == ValidationStatus.HUMAN for r in all_results):
            return ValidationStatus.HUMAN
        if any(r.status == ValidationStatus.RETRY for r in all_results):
            return ValidationStatus.RETRY
        return ValidationStatus.ACCEPT

    @property
    def needs_human_review(self) -> bool:
        return self.overall_status == ValidationStatus.HUMAN

    @property
    def needs_retry(self) -> bool:
        return self.overall_status == ValidationStatus.RETRY

    def get_problem_fields(self) -> List[str]:
        """Get list of fields that have issues"""
        problems = []

        if not self.team.is_accept:
            problems.append(f"team: {self.team.cleaned} ({self.team.reasons})")

        for i, r in enumerate(self.responsables):
            if not r.is_accept:
                problems.append(f"responsable_{i+1}: {r.cleaned} ({r.reasons})")

        for i, p in enumerate(self.players):
            if not p.is_accept:
                problems.append(f"player_{i+1}: {p.cleaned} ({p.reasons})")

        return problems


def validate_ocr_extraction(
    team_name: str,
    responsables: List[str],
    player_names: List[str]
) -> ExtractionValidation:
    """
    Validate all names from an OCR extraction.

    Args:
        team_name: Team/club name
        responsables: List of responsable names
        player_names: List of player names

    Returns:
        ExtractionValidation with all results and overall status
    """
    team_result = validate_team_name(team_name)

    responsable_results = [
        validate_name_field(name, FieldType.NOMBRE_COMPLETO)
        for name in responsables
    ]

    player_results = [
        validate_name_field(name, FieldType.NOMBRE_COMPLETO)
        for name in player_names
    ]

    validation = ExtractionValidation(
        team=team_result,
        responsables=responsable_results,
        players=player_results,
    )

    # Log overall result
    logger.info(
        f"📋 Extraction validation: {validation.overall_status.value} "
        f"(team={team_result.status.value}, "
        f"responsables={len([r for r in responsable_results if r.is_accept])}/{len(responsable_results)} OK, "
        f"players={len([p for p in player_results if p.is_accept])}/{len(player_results)} OK)"
    )

    if validation.get_problem_fields():
        logger.info(f"⚠️  Problems: {validation.get_problem_fields()}")

    return validation


# === Exports ===

__all__ = [
    'ValidationStatus',
    'FieldType',
    'ValidationMetrics',
    'ValidationResult',
    'ExtractionValidation',
    'normalize_safe',
    'calculate_alpha_ratio',
    'validate_name_field',
    'validate_team_name',
    'validate_ocr_extraction',
    'CONNECTORS',
]
