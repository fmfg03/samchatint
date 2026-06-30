"""
DevNous Validation Module

Data validation utilities for OCR and form processing.

Modules:
- mexican_names_validator: Mexican names and surnames validation (fuzzy matching)
- curp_validator: CURP (Clave Única de Registro de Población) validation
- hard_validator: Deterministic validation with ACCEPT/RETRY/HUMAN routing
"""

from .mexican_names_validator import (
    MexicanNamesValidator,
    validate_mexican_name,
    validate_mexican_full_name,
    NOMBRES_MEXICANOS,
    APELLIDOS_MEXICANOS
)

from .curp_validator import (
    CURPValidator,
    CURPData,
    ESTADOS_MEXICO,
    get_curp_validator
)

from .hard_validator import (
    ValidationStatus,
    FieldType,
    ValidationMetrics,
    ValidationResult,
    ExtractionValidation,
    normalize_safe,
    calculate_alpha_ratio,
    validate_name_field,
    validate_team_name,
    validate_ocr_extraction,
    CONNECTORS,
)


__all__ = [
    # Mexican names (fuzzy)
    'MexicanNamesValidator',
    'validate_mexican_name',
    'validate_mexican_full_name',
    'NOMBRES_MEXICANOS',
    'APELLIDOS_MEXICANOS',
    # CURP
    'CURPValidator',
    'CURPData',
    'ESTADOS_MEXICO',
    'get_curp_validator',
    # Hard validation (deterministic)
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
