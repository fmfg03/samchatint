"""
CURP Validator - Validador de Clave Única de Registro de Población

Validates Mexican CURP (Clave Única de Registro de Población) identifiers.

CURP Format: AAAA######HHHHHH## (18 characters)
Example: BEML920313HDFRRS09

Structure:
- Positions 1-4: 4 letters from names (apellido paterno, apellido materno, nombre)
- Positions 5-10: 6 digits for birth date (YYMMDD)
- Position 11: 1 letter for sex (H=Hombre, M=Mujer)
- Positions 12-13: 2 letters for birth state
- Positions 14-16: 3 letters (internal consonants)
- Positions 17-18: 2 digits (homoclave + verification digit)

Author: Copa Telmex Development Team
Date: 2025-11-11
"""

import re
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Any, Optional, Tuple

logger = logging.getLogger(__name__)


# Estados de México (códigos de 2 letras para CURP)
ESTADOS_MEXICO = {
    'AS': 'Aguascalientes',
    'BC': 'Baja California',
    'BS': 'Baja California Sur',
    'CC': 'Campeche',
    'CS': 'Chiapas',
    'CH': 'Chihuahua',
    'CL': 'Coahuila',
    'CM': 'Colima',
    'DF': 'Ciudad de México',  # También acepta CDMX
    'DG': 'Durango',
    'GT': 'Guanajuato',
    'GR': 'Guerrero',
    'HG': 'Hidalgo',
    'JC': 'Jalisco',
    'MC': 'Estado de México',
    'MN': 'Michoacán',
    'MS': 'Morelos',
    'NT': 'Nayarit',
    'NL': 'Nuevo León',
    'OC': 'Oaxaca',
    'PL': 'Puebla',
    'QT': 'Querétaro',
    'QR': 'Quintana Roo',
    'SP': 'San Luis Potosí',
    'SL': 'Sinaloa',
    'SR': 'Sonora',
    'TC': 'Tabasco',
    'TS': 'Tamaulipas',
    'TL': 'Tlaxcala',
    'VZ': 'Veracruz',
    'YN': 'Yucatán',
    'ZS': 'Zacatecas',
    'NE': 'Nacido en el Extranjero'
}

# Palabras inconvenientes que se reemplazan por 'X'
PALABRAS_INCONVENIENTES = {
    'BACA', 'BAKA', 'BUEI', 'BUEY', 'CACA', 'CACO', 'CAGA', 'CAGO',
    'CAKA', 'CAKO', 'COGE', 'COGI', 'COJA', 'COJE', 'COJI', 'COJO',
    'COLA', 'CULO', 'FALO', 'FETO', 'GETA', 'GUEI', 'GUEY', 'JETA',
    'JOTO', 'KACA', 'KACO', 'KAGA', 'KAGO', 'KAKA', 'KAKO', 'KOGE',
    'KOGI', 'KOJA', 'KOJE', 'KOJI', 'KOJO', 'KOLA', 'KULO', 'LILO',
    'LOCA', 'LOCO', 'LOKA', 'LOKO', 'MAME', 'MAMO', 'MEAR', 'MEAS',
    'MEON', 'MIAR', 'MION', 'MOCO', 'MOKO', 'MULA', 'MULO', 'NACA',
    'NACO', 'PEDA', 'PEDO', 'PENE', 'PIPI', 'PITO', 'POPO', 'PUTA',
    'PUTO', 'QULO', 'RATA', 'ROBA', 'ROBE', 'ROBO', 'RUIN', 'SENO',
    'TETA', 'VACA', 'VAGA', 'VAGO', 'VAKA', 'VUEI', 'VUEY', 'WUEI',
    'WUEY'
}


@dataclass
class CURPData:
    """Extracted data from CURP."""

    curp: str
    apellido_paterno_inicial: str
    apellido_materno_inicial: str
    nombre_inicial: str
    fecha_nacimiento: datetime
    sexo: str
    estado_nacimiento: str
    estado_nombre: str
    homoclave: str
    digito_verificador: str
    valid: bool
    errors: list


class CURPValidator:
    """
    Validator for Mexican CURP (Clave Única de Registro de Población).

    Features:
    - Format validation (18 characters, alphanumeric)
    - Birth date validation
    - Birth state validation
    - Sex validation (H/M)
    - Homoclave and verification digit
    - Data extraction from CURP
    - Age calculation
    """

    # CURP pattern: AAAA######HHHHHH##
    CURP_PATTERN = re.compile(
        r'^[A-Z]{4}\d{6}[HM][A-Z]{2}[A-Z]{3}[A-Z0-9]\d$'
    )

    def __init__(self):
        """Initialize CURP validator."""
        self.logger = logging.getLogger(__name__)

    def validate(self, curp: str) -> Dict[str, Any]:
        """
        Validate CURP and extract data.

        Args:
            curp: CURP string (18 characters)

        Returns:
            Dictionary with validation results:
            {
                "valid": bool,
                "curp": str,
                "errors": list,
                "data": CURPData or None
            }
        """

        errors = []

        # Normalize CURP (uppercase, strip whitespace)
        if not curp:
            return {
                "valid": False,
                "curp": "",
                "errors": ["CURP vacío"],
                "data": None
            }

        curp = curp.upper().strip()

        # 1. Length validation
        if len(curp) != 18:
            errors.append(f"Longitud incorrecta: {len(curp)} caracteres (debe ser 18)")

        # 2. Format validation (pattern)
        if not self.CURP_PATTERN.match(curp):
            errors.append("Formato inválido (debe ser AAAA######HHHHHH##)")

        # If basic validation fails, return early
        if errors:
            return {
                "valid": False,
                "curp": curp,
                "errors": errors,
                "data": None
            }

        # 3. Extract components
        apellido_p = curp[0]
        apellido_m = curp[1]
        nombre = curp[2]
        fecha_str = curp[4:10]  # YYMMDD
        sexo = curp[10]
        estado = curp[11:13]
        homoclave = curp[16]
        digito_verificador = curp[17]

        # 4. Validate birth date
        fecha_nacimiento = None
        try:
            fecha_nacimiento = self._parse_fecha_nacimiento(fecha_str)

            # Check if date is in the future
            if fecha_nacimiento > datetime.now():
                errors.append("Fecha de nacimiento en el futuro")

            # Check minimum age (e.g., 5 years for player registration)
            edad = self._calcular_edad(fecha_nacimiento)
            if edad < 5:
                errors.append(f"Edad muy baja: {edad} años (mínimo 5)")
            elif edad > 120:
                errors.append(f"Edad muy alta: {edad} años (máximo 120)")

        except ValueError as e:
            errors.append(f"Fecha de nacimiento inválida: {str(e)}")

        # 5. Validate sex
        if sexo not in ['H', 'M']:
            errors.append(f"Sexo inválido: '{sexo}' (debe ser H o M)")

        # 6. Validate birth state
        if estado not in ESTADOS_MEXICO:
            errors.append(f"Estado de nacimiento inválido: '{estado}'")

        estado_nombre = ESTADOS_MEXICO.get(estado, "Desconocido")

        # 7. Validate verification digit (official RENAPO algorithm)
        try:
            expected_digit = self._calcular_digito_verificador(curp[:17])
            if str(expected_digit) != digito_verificador:
                errors.append(
                    f"Dígito verificador inválido: esperado '{expected_digit}', "
                    f"recibido '{digito_verificador}'"
                )
        except Exception as e:
            errors.append(f"Error validando dígito verificador: {str(e)}")

        # 8. Create result
        valid = len(errors) == 0

        data = CURPData(
            curp=curp,
            apellido_paterno_inicial=apellido_p,
            apellido_materno_inicial=apellido_m,
            nombre_inicial=nombre,
            fecha_nacimiento=fecha_nacimiento,
            sexo="Hombre" if sexo == 'H' else "Mujer",
            estado_nacimiento=estado,
            estado_nombre=estado_nombre,
            homoclave=homoclave,
            digito_verificador=digito_verificador,
            valid=valid,
            errors=errors
        )

        return {
            "valid": valid,
            "curp": curp,
            "errors": errors,
            "data": data
        }

    def _parse_fecha_nacimiento(self, fecha_str: str) -> datetime:
        """
        Parse birth date from CURP format (YYMMDD).

        Args:
            fecha_str: Date string (6 digits: YYMMDD)

        Returns:
            datetime object

        Raises:
            ValueError: If date is invalid
        """

        if len(fecha_str) != 6:
            raise ValueError("Fecha debe tener 6 dígitos (YYMMDD)")

        yy = int(fecha_str[0:2])
        mm = int(fecha_str[2:4])
        dd = int(fecha_str[4:6])

        # Determine century (assumption: 00-25 = 2000s, 26-99 = 1900s)
        if yy <= 25:
            year = 2000 + yy
        else:
            year = 1900 + yy

        # Validate date
        try:
            fecha = datetime(year, mm, dd)
        except ValueError as e:
            raise ValueError(f"Fecha inválida: {year}-{mm:02d}-{dd:02d}")

        return fecha

    def _calcular_edad(self, fecha_nacimiento: datetime) -> int:
        """
        Calculate age from birth date.

        Args:
            fecha_nacimiento: Birth date

        Returns:
            Age in years
        """

        hoy = datetime.now()
        edad = hoy.year - fecha_nacimiento.year

        # Adjust if birthday hasn't occurred this year
        if (hoy.month, hoy.day) < (fecha_nacimiento.month, fecha_nacimiento.day):
            edad -= 1

        return edad

    def _calcular_digito_verificador(self, curp_17: str) -> int:
        """
        Calculate CURP verification digit using official RENAPO algorithm.

        Algorithm:
        1. Convert each character to numeric value:
           - Digits 0-9 = their value
           - Letters A-Z = 10-35 (A=10, B=11, ..., Z=35)
           - Ñ = 36
        2. Multiply each value by (18 - position)
        3. Sum all products
        4. Calculate modulo 10
        5. Verification digit = (10 - modulo) % 10

        Args:
            curp_17: First 17 characters of CURP

        Returns:
            Expected verification digit (0-9)

        Raises:
            ValueError: If invalid characters in CURP
        """

        if len(curp_17) != 17:
            raise ValueError(f"CURP debe tener 17 caracteres, recibió {len(curp_17)}")

        # Character to numeric value mapping
        def char_to_value(char: str) -> int:
            """Convert CURP character to numeric value."""
            if char.isdigit():
                return int(char)
            elif char == 'Ñ':
                return 36
            elif char.isalpha():
                # A=10, B=11, ..., Z=35
                return ord(char) - ord('A') + 10
            else:
                raise ValueError(f"Carácter inválido en CURP: '{char}'")

        # Calculate weighted sum
        suma = 0
        for i, char in enumerate(curp_17):
            valor = char_to_value(char)
            peso = 18 - i  # Weight decreases from 18 to 2
            suma += valor * peso

        # Calculate verification digit
        modulo = suma % 10
        digito = (10 - modulo) % 10

        return digito

    def extract_data(self, curp: str) -> Optional[CURPData]:
        """
        Extract data from CURP.

        Args:
            curp: CURP string

        Returns:
            CURPData if valid, None otherwise
        """

        result = self.validate(curp)
        return result['data'] if result['valid'] else None

    def calcular_edad(self, curp: str) -> Optional[int]:
        """
        Calculate age from CURP.

        Args:
            curp: CURP string

        Returns:
            Age in years, or None if invalid
        """

        data = self.extract_data(curp)
        if data and data.fecha_nacimiento:
            return self._calcular_edad(data.fecha_nacimiento)
        return None

    def es_mayor_edad(self, curp: str) -> Optional[bool]:
        """
        Check if person is of legal age (18+).

        Args:
            curp: CURP string

        Returns:
            True if 18+, False if <18, None if invalid
        """

        edad = self.calcular_edad(curp)
        return edad >= 18 if edad is not None else None

    def validar_contra_datos(
        self,
        curp: str,
        nombre: str,
        apellido_paterno: str,
        apellido_materno: Optional[str] = None,
        fecha_nacimiento: Optional[datetime] = None,
        sexo: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Validate CURP against provided personal data.

        Args:
            curp: CURP string
            nombre: First name
            apellido_paterno: Paternal surname
            apellido_materno: Optional maternal surname
            fecha_nacimiento: Optional birth date
            sexo: Optional sex (H/M or Hombre/Mujer)

        Returns:
            Dictionary with validation results
        """

        # First validate CURP format
        result = self.validate(curp)

        if not result['valid']:
            return result

        data = result['data']
        mismatches = []

        # Check name initials
        nombre_inicial = self._obtener_inicial(nombre)
        if data.nombre_inicial != nombre_inicial:
            mismatches.append(
                f"Inicial de nombre no coincide: CURP='{data.nombre_inicial}', "
                f"Dato='{nombre_inicial}' (de '{nombre}')"
            )

        # Check paternal surname initial
        apellido_p_inicial = self._obtener_inicial(apellido_paterno)
        if data.apellido_paterno_inicial != apellido_p_inicial:
            mismatches.append(
                f"Inicial de apellido paterno no coincide: CURP='{data.apellido_paterno_inicial}', "
                f"Dato='{apellido_p_inicial}' (de '{apellido_paterno}')"
            )

        # Check maternal surname initial (if provided)
        if apellido_materno:
            apellido_m_inicial = self._obtener_inicial(apellido_materno)
            if data.apellido_materno_inicial != apellido_m_inicial:
                mismatches.append(
                    f"Inicial de apellido materno no coincide: CURP='{data.apellido_materno_inicial}', "
                    f"Dato='{apellido_m_inicial}' (de '{apellido_materno}')"
                )

        # Check birth date (if provided)
        if fecha_nacimiento:
            if data.fecha_nacimiento.date() != fecha_nacimiento.date():
                mismatches.append(
                    f"Fecha de nacimiento no coincide: "
                    f"CURP={data.fecha_nacimiento.strftime('%Y-%m-%d')}, "
                    f"Dato={fecha_nacimiento.strftime('%Y-%m-%d')}"
                )

        # Check sex (if provided)
        if sexo:
            sexo_normalized = self._normalizar_sexo(sexo)
            if data.sexo != sexo_normalized:
                mismatches.append(
                    f"Sexo no coincide: CURP='{data.sexo}', Dato='{sexo_normalized}'"
                )

        # Update result
        result['data_match'] = len(mismatches) == 0
        result['mismatches'] = mismatches

        return result

    def _obtener_inicial(self, texto: str) -> str:
        """
        Get first letter of text (first non-special character).

        Args:
            texto: Text string

        Returns:
            First letter (uppercase)
        """

        if not texto:
            return 'X'

        texto = texto.upper().strip()

        # Skip special prefixes (DE, DEL, LA, LAS, MC, VON, etc.)
        prefijos = ['DE', 'DEL', 'LA', 'LAS', 'LOS', 'MC', 'VON', 'VAN']
        palabras = texto.split()

        for palabra in palabras:
            if palabra not in prefijos and len(palabra) > 0:
                return palabra[0]

        # Fallback to first character
        return texto[0] if texto else 'X'

    def _normalizar_sexo(self, sexo: str) -> str:
        """
        Normalize sex value.

        Args:
            sexo: Sex string (H/M or Hombre/Mujer)

        Returns:
            Normalized sex (Hombre/Mujer)
        """

        sexo = sexo.upper().strip()

        if sexo in ['H', 'HOMBRE', 'M', 'MALE']:
            return 'Hombre'
        elif sexo in ['M', 'MUJER', 'F', 'FEMALE']:
            return 'Mujer'
        else:
            return sexo

    @staticmethod
    def generar_curp_template(
        nombre: str,
        apellido_paterno: str,
        apellido_materno: str,
        fecha_nacimiento: datetime,
        sexo: str,
        estado: str
    ) -> str:
        """
        Generate CURP template (first 13 characters).

        This is a simplified template generator. The full CURP
        requires homoclave and verification digit which require
        official algorithms.

        Args:
            nombre: First name
            apellido_paterno: Paternal surname
            apellido_materno: Maternal surname
            fecha_nacimiento: Birth date
            sexo: Sex (H/M)
            estado: Birth state code (2 letters)

        Returns:
            CURP template (13 characters + XXXXX)
        """

        # Extract initials
        ap_inicial = apellido_paterno[0].upper() if apellido_paterno else 'X'
        am_inicial = apellido_materno[0].upper() if apellido_materno else 'X'
        n_inicial = nombre[0].upper() if nombre else 'X'

        # Get first vowel of paternal surname
        ap_vocal = 'X'
        for c in apellido_paterno[1:].upper():
            if c in 'AEIOU':
                ap_vocal = c
                break

        # Build first 4 characters
        primeras_4 = f"{ap_inicial}{ap_vocal}{am_inicial}{n_inicial}"

        # Check for inconvenient words
        if primeras_4 in PALABRAS_INCONVENIENTES:
            primeras_4 = primeras_4[:3] + 'X'

        # Birth date (YYMMDD)
        yy = fecha_nacimiento.year % 100
        fecha_str = f"{yy:02d}{fecha_nacimiento.month:02d}{fecha_nacimiento.day:02d}"

        # Sex
        sexo_char = sexo.upper()[0] if sexo else 'H'

        # State
        estado_code = estado.upper() if estado in ESTADOS_MEXICO else 'NE'

        # Template (without consonants, homoclave, verification digit)
        template = f"{primeras_4}{fecha_str}{sexo_char}{estado_code}XXX00"

        return template


# Singleton instance
_curp_validator = None

def get_curp_validator() -> CURPValidator:
    """Get singleton CURP validator instance."""
    global _curp_validator
    if _curp_validator is None:
        _curp_validator = CURPValidator()
    return _curp_validator


# Example usage
if __name__ == "__main__":
    """
    Example usage of CURP validator.
    """

    # Set up logging
    logging.basicConfig(level=logging.INFO)

    validator = CURPValidator()

    # Example 1: Valid CURP
    print("=" * 60)
    print("Example 1: Valid CURP")
    print("=" * 60)

    curp = "BEML920313HDFRRS09"
    result = validator.validate(curp)

    print(f"CURP: {curp}")
    print(f"Valid: {result['valid']}")

    if result['valid']:
        data = result['data']
        print(f"Nombre inicial: {data.nombre_inicial}")
        print(f"Fecha nacimiento: {data.fecha_nacimiento.strftime('%Y-%m-%d')}")
        print(f"Edad: {validator._calcular_edad(data.fecha_nacimiento)} años")
        print(f"Sexo: {data.sexo}")
        print(f"Estado: {data.estado_nombre}")

    # Example 2: Invalid CURP (wrong length)
    print("\n" + "=" * 60)
    print("Example 2: Invalid CURP (wrong length)")
    print("=" * 60)

    curp = "BEML920313"
    result = validator.validate(curp)

    print(f"CURP: {curp}")
    print(f"Valid: {result['valid']}")
    print(f"Errors: {result['errors']}")

    # Example 3: Validate against data
    print("\n" + "=" * 60)
    print("Example 3: Validate against personal data")
    print("=" * 60)

    curp = "BEML920313HDFRRS09"
    result = validator.validar_contra_datos(
        curp=curp,
        nombre="Luis",
        apellido_paterno="Bermúdez",
        apellido_materno="Martínez",
        fecha_nacimiento=datetime(1992, 3, 13),
        sexo="H"
    )

    print(f"CURP: {curp}")
    print(f"Valid: {result['valid']}")
    print(f"Data match: {result.get('data_match', False)}")

    if result.get('mismatches'):
        print("Mismatches:")
        for mismatch in result['mismatches']:
            print(f"  - {mismatch}")

    # Example 4: Generate CURP template
    print("\n" + "=" * 60)
    print("Example 4: Generate CURP template")
    print("=" * 60)

    template = validator.generar_curp_template(
        nombre="Luis",
        apellido_paterno="Bermúdez",
        apellido_materno="Martínez",
        fecha_nacimiento=datetime(1992, 3, 13),
        sexo="H",
        estado="DF"
    )

    print(f"Template: {template}")
    print("Note: Last 5 characters (XXX00) need official algorithm")
