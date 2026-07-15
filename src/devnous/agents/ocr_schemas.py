"""
Pydantic schemas for structured OCR extraction.

These models enforce schema validation at the LLM level using Claude's tool_use,
eliminating JSON parsing errors and providing type-safe extraction.
"""

import re
from enum import Enum
from typing import List, Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator, model_validator


class TournamentCategory(str, Enum):
    """Valid tournament categories for Copa Telmex."""

    U8 = "U8"
    U10 = "U10"
    U12 = "U12"
    U14 = "U14"
    U16 = "U16"
    U18 = "U18"
    OPEN = "Open"
    JUVENIL = "Juvenil"
    LIBRE = "Libre"


class Gender(str, Enum):
    """Player/team gender."""

    MALE = "varonil"
    FEMALE = "femenil"
    MIXED = "mixto"


class PhotoRegion(BaseModel):
    """
    Bounding box coordinates for a player's photo in the registration form.

    Coordinates are in pixels relative to the original image dimensions.
    Used to crop individual player photos from the full form image.
    """

    x: int = Field(
        ..., ge=0, description="X coordinate of top-left corner (pixels from left edge)"
    )

    y: int = Field(
        ..., ge=0, description="Y coordinate of top-left corner (pixels from top edge)"
    )

    width: int = Field(..., ge=10, description="Width of the photo region in pixels")

    height: int = Field(..., ge=10, description="Height of the photo region in pixels")

    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Confidence that this region contains a player photo",
    )


class PlayerExtraction(BaseModel):
    """
    Extracted player information from registration form.

    Each player must have at least a name. Other fields are optional
    but will be validated if provided.
    """

    name: str = Field(
        ...,
        min_length=3,
        max_length=100,
        description="Player's full name (nombre completo) - first name + paternal surname + maternal surname",
    )

    paternal_surname: Optional[str] = Field(
        None, max_length=50, description="Apellido paterno (father's surname)"
    )

    maternal_surname: Optional[str] = Field(
        None, max_length=50, description="Apellido materno (mother's surname)"
    )

    first_name: Optional[str] = Field(
        None, max_length=50, description="Nombre(s) de pila (given name)"
    )

    birth_date: Optional[str] = Field(
        None, description="Date of birth in DD/MM/YYYY format"
    )

    curp: Optional[str] = Field(
        None,
        description="CURP - Mexican unique population identifier (18 characters). May need review if OCR is unclear.",
    )

    jersey_number: Optional[int] = Field(
        None, ge=0, le=99, description="Player's jersey/shirt number"
    )

    position: Optional[str] = Field(
        None,
        max_length=30,
        description="Player position (portero, defensa, mediocampista, delantero)",
    )

    photo_region: Optional[PhotoRegion] = Field(
        None,
        description="Bounding box coordinates of this player's photo in the form image",
    )

    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Confidence score for this player's extraction (0.0-1.0)",
    )

    needs_review: bool = Field(
        default=False, description="Flag if this player's data needs human review"
    )

    @field_validator("curp")
    @classmethod
    def validate_curp_format(cls, v: Optional[str]) -> Optional[str]:
        """
        Validate and normalize CURP format.

        Accepts CURPs that are slightly off (17-19 chars) due to OCR errors,
        and flags them for human review rather than rejecting outright.
        """
        if v is None or v.lower() in ("no visible", "n/a", "", "no"):
            return None

        v = v.upper().strip()

        # Remove common OCR artifacts
        v = v.replace(" ", "").replace("-", "").replace("_", "")

        # If length is close to 18, try to normalize
        if len(v) == 19:
            # Try removing common duplicate characters at end
            if v[-1] == v[-2]:  # Duplicate last char
                v = v[:-1]
            elif v[0] == v[1] and v[0].isalpha():  # Duplicate first char
                v = v[1:]
        elif len(v) == 17:
            # CURP is missing a character - keep as-is for review
            pass

        # CURP pattern: 4 letters + 6 digits + 1 gender + 2 state + 3 consonants + 2 homoclave
        pattern = r"^[A-Z]{4}\d{6}[HM][A-Z]{2}[A-Z]{3}[A-Z0-9]{2}$"
        if not re.match(pattern, v):
            # Return as-is for human review (will be flagged by needs_review)
            return v
        return v

    @field_validator("birth_date")
    @classmethod
    def validate_birth_date(cls, v: Optional[str]) -> Optional[str]:
        """Validate and normalize birth date format."""
        if v is None or v.lower() in ("no visible", "n/a", ""):
            return None

        # Try to parse common date formats
        # DD/MM/YYYY, DD-MM-YYYY, DD.MM.YYYY
        date_patterns = [
            (r"^(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})$", "{:02d}/{:02d}/{}"),
            (r"^(\d{4})[/\-.](\d{1,2})[/\-.](\d{1,2})$", "{2:02d}/{1:02d}/{0}"),
        ]

        for pattern, fmt in date_patterns:
            match = re.match(pattern, v.strip())
            if match:
                groups = match.groups()
                try:
                    if len(groups[0]) == 4:  # YYYY-MM-DD format
                        return fmt.format(
                            int(groups[0]), int(groups[1]), int(groups[2])
                        )
                    else:  # DD/MM/YYYY format
                        return fmt.format(int(groups[0]), int(groups[1]), groups[2])
                except (ValueError, IndexError):
                    pass

        return v  # Return as-is if can't parse

    @model_validator(mode="after")
    def check_curp_needs_review(self) -> "PlayerExtraction":
        """Automatically flag for review if CURP is invalid."""
        if self.curp:
            # Check if CURP is valid (exactly 18 chars and matches pattern)
            pattern = r"^[A-Z]{4}\d{6}[HM][A-Z]{2}[A-Z]{3}[A-Z0-9]{2}$"
            if len(self.curp) != 18 or not re.match(pattern, self.curp):
                self.needs_review = True
        return self


class TeamExtraction(BaseModel):
    """
    Extracted team information from registration form.
    """

    name: str = Field(
        ...,
        min_length=2,
        max_length=100,
        description="Team/club name (nombre del equipo)",
    )

    category: Optional[str] = Field(
        None, description="Tournament category (U10, U12, U14, U16, U18, Open, Juvenil)"
    )

    gender: Optional[str] = Field(
        None, description="Team gender: varonil, femenil, or mixto"
    )

    league: Optional[str] = Field(
        None, max_length=100, description="League name (nombre de la liga)"
    )

    municipality: Optional[str] = Field(
        None, max_length=100, description="Municipality/city (municipio)"
    )

    state: Optional[str] = Field(
        None,
        max_length=50,
        description="State (estado) - e.g., Jalisco, CDMX, Nuevo León",
    )

    confidence: float = Field(
        default=0.0, ge=0.0, le=1.0, description="Confidence score for team extraction"
    )


class ManagerExtraction(BaseModel):
    """
    Extracted manager/coach information.
    """

    name: str = Field(
        ..., min_length=3, max_length=100, description="Manager/coach full name"
    )

    role: Optional[str] = Field(
        None, description="Role: manager, entrenador, delegado, representante"
    )

    phone: Optional[str] = Field(None, description="Contact phone number (10 digits)")

    email: Optional[str] = Field(None, description="Contact email address")

    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Confidence score for manager extraction",
    )

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: Optional[str]) -> Optional[str]:
        """Normalize phone number."""
        if v is None or v.lower() in ("no visible", "n/a", ""):
            return None

        # Extract digits only
        digits = re.sub(r"\D", "", v)

        # Mexican phone numbers are 10 digits
        if len(digits) == 10:
            return digits
        elif len(digits) == 12 and digits.startswith("52"):
            return digits[2:]  # Remove country code

        return v  # Return as-is


class ResponsableExtraction(BaseModel):
    """
    Extracted team responsable (delegado/representante) information.

    Responsables are in slots 1-2 of the form and do NOT have:
    - CURP field
    - Jersey number field

    This distinguishes them from players.
    """

    name: str = Field(
        ...,
        min_length=3,
        max_length=100,
        description="Responsable full name (nombre completo)",
    )

    role: Optional[str] = Field(
        None, description="Role: delegado, representante, entrenador, manager"
    )

    phone: Optional[str] = Field(None, description="Contact phone number (10 digits)")

    email: Optional[str] = Field(None, description="Contact email address")

    birth_date: Optional[str] = Field(
        None, description="Date of birth if visible (DD/MM/YYYY)"
    )

    confidence: float = Field(
        default=0.0, ge=0.0, le=1.0, description="Confidence score for this extraction"
    )

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: Optional[str]) -> Optional[str]:
        """Normalize phone number."""
        if v is None or v.lower() in ("no visible", "n/a", ""):
            return None
        digits = re.sub(r"\D", "", v)
        if len(digits) == 10:
            return digits
        elif len(digits) == 12 and digits.startswith("52"):
            return digits[2:]
        return v


class RegistrationFormExtraction(BaseModel):
    """
    Complete extraction from a Copa Telmex registration form.

    Structure of the form:
    - Rows 1-6: Team data (name, category, league, etc.)
    - Slots 1-2: Responsables (NO CURP, NO jersey number)
    - Slots 3-10: Players on FRONT (8 players)
    - Back side: Slots for 12 more players
    - Total: 2 responsables + up to 20 players
    """

    team: TeamExtraction = Field(
        ..., description="Team/club information from the first 6 rows of the form"
    )

    responsables: List[ResponsableExtraction] = Field(
        default_factory=list,
        min_length=0,
        max_length=2,
        description="Team responsables/delegados (slots 1-2, no CURP/jersey fields)",
    )

    manager: Optional[ManagerExtraction] = Field(
        None,
        description="Manager/coach information (legacy field, use responsables instead)",
    )

    players: List[PlayerExtraction] = Field(
        default_factory=list,
        min_length=0,
        max_length=20,
        description="List of players (slots 3-10 on front + up to 12 on back)",
    )

    is_front: bool = Field(
        default=True, description="Whether this is the front side of the form"
    )

    overall_confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Overall confidence score for the entire extraction",
    )

    form_type: Optional[str] = Field(
        None, description="Type of form detected: roster, individual, credential"
    )

    notes: Optional[str] = Field(
        None,
        max_length=500,
        description="Any additional notes or observations about the form",
    )

    @model_validator(mode="after")
    def calculate_needs_review(self) -> "RegistrationFormExtraction":
        """Flag extraction for review if confidence is low."""
        # Count players needing review
        low_confidence_players = sum(
            1 for p in self.players if p.confidence < 0.7 or p.needs_review
        )

        # If more than 30% of players need review, flag the whole form
        if self.players and low_confidence_players / len(self.players) > 0.3:
            self.overall_confidence = min(self.overall_confidence, 0.6)

        return self


class SinglePlayerExtraction(BaseModel):
    """
    Extraction schema for single-player forms or credentials.
    """

    player: PlayerExtraction = Field(..., description="Player information")

    team_name: Optional[str] = Field(None, description="Team name if visible")

    category: Optional[str] = Field(None, description="Tournament category if visible")

    photo_detected: bool = Field(
        default=False, description="Whether a player photo was detected in the form"
    )

    overall_confidence: float = Field(
        default=0.0, ge=0.0, le=1.0, description="Overall confidence score"
    )


class OCRExtractionResult(BaseModel):
    """
    Generic OCR extraction result with metadata.
    """

    success: bool = Field(default=True, description="Whether extraction was successful")

    extraction_type: Literal[
        "registration_form", "single_player", "roster", "unknown"
    ] = Field(default="unknown", description="Type of document that was extracted")

    data: Optional[Union[RegistrationFormExtraction, SinglePlayerExtraction]] = Field(
        None, description="Extracted data based on extraction_type"
    )

    raw_text: Optional[str] = Field(
        None, description="Raw transcribed text from the image"
    )

    processing_time_ms: Optional[float] = Field(
        None, ge=0, description="Time taken to process the image in milliseconds"
    )

    model_used: Optional[str] = Field(None, description="Model used for extraction")

    error_message: Optional[str] = Field(
        None, description="Error message if extraction failed"
    )


def get_registration_form_tool_schema() -> dict:
    """
    Get the tool schema for Claude's tool_use feature.

    This schema is passed to the Claude API to enforce structured output.
    """
    return {
        "name": "extract_registration_form",
        "description": (
            "Extract team registration form data from an image. "
            "This tool extracts team name, manager info, and all player details "
            "from Copa Telmex registration forms. Returns structured data with "
            "confidence scores for each field."
        ),
        "input_schema": RegistrationFormExtraction.model_json_schema(),
    }


def get_single_player_tool_schema() -> dict:
    """
    Get the tool schema for single player extraction.
    """
    return {
        "name": "extract_single_player",
        "description": (
            "Extract single player information from a credential or individual form. "
            "Returns player name, CURP, birth date, and other visible details."
        ),
        "input_schema": SinglePlayerExtraction.model_json_schema(),
    }


# Export schemas for external use
__all__ = [
    "TournamentCategory",
    "Gender",
    "PhotoRegion",
    "PlayerExtraction",
    "TeamExtraction",
    "ManagerExtraction",
    "ResponsableExtraction",
    "RegistrationFormExtraction",
    "SinglePlayerExtraction",
    "OCRExtractionResult",
    "get_registration_form_tool_schema",
    "get_single_player_tool_schema",
]
