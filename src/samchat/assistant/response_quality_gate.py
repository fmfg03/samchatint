"""Output-quality guardrails for assistant responses.

This module is intentionally deterministic and provider-agnostic. It prevents
known bad fallback behavior (looped fragments, stylized unicode gibberish,
raw tool payloads) from reaching the user as if it were an executive answer.
It does not decide business truth; it only decides whether a rendered answer is
safe enough to display.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ResponseQualityVerdict:
    ok: bool
    reason: str = "ok"
    diagnostics: dict[str, Any] | None = None


def _tokens(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKD", text or "").lower()
    normalized = re.sub(r"[^\wáéíóúüñ]+", " ", normalized, flags=re.IGNORECASE)
    return [token for token in normalized.split() if token]


def _repeated_ngram_ratio(tokens: list[str], *, size: int = 4) -> float:
    if len(tokens) < size * 3:
        return 0.0
    grams = [tuple(tokens[index : index + size]) for index in range(len(tokens) - size + 1)]
    if not grams:
        return 0.0
    counts: dict[tuple[str, ...], int] = {}
    for gram in grams:
        counts[gram] = counts.get(gram, 0) + 1
    repeated = sum(count for count in counts.values() if count > 1)
    return repeated / max(len(grams), 1)


def _non_ascii_letter_ratio(text: str) -> float:
    letters = [char for char in text or "" if char.isalpha()]
    if not letters:
        return 0.0
    non_ascii = [char for char in letters if ord(char) > 127]
    return len(non_ascii) / max(len(letters), 1)


def _looks_like_small_caps_gibberish(text: str) -> bool:
    if len(text or "") < 80:
        return False
    non_ascii_ratio = _non_ascii_letter_ratio(text)
    # Legitimate Spanish has accents, but not 60%+ non-ASCII letters.
    if non_ascii_ratio < 0.55:
        return False
    ascii_words = re.findall(r"[A-Za-z]{3,}", text or "")
    return len(ascii_words) < 8


def _looks_like_raw_tool_payload(text: str) -> bool:
    stripped = (text or "").strip()
    if not stripped:
        return False
    if stripped.startswith("{") and stripped.endswith("}"):
        return True
    if re.search(r'"name"\s*:\s*"[a-zA-Z0-9_.-]+"\s*,\s*"arguments"\s*:', stripped):
        return True
    return False


def evaluate_response_quality(text: str) -> ResponseQualityVerdict:
    """Return whether an assistant answer is display-safe.

    The checks are intentionally conservative: normal Spanish executive answers,
    lists, markdown, and source citations should pass; repeated loops, raw JSON
    tool calls, and stylized unicode garbage should fail closed.
    """

    answer = text or ""
    stripped = answer.strip()
    if not stripped:
        return ResponseQualityVerdict(False, "empty_response")

    if _looks_like_raw_tool_payload(stripped):
        return ResponseQualityVerdict(False, "raw_tool_payload")

    tokens = _tokens(stripped)
    if len(tokens) >= 24:
        repeat_ratio = _repeated_ngram_ratio(tokens, size=4)
        if repeat_ratio >= 0.28:
            return ResponseQualityVerdict(
                False,
                "repeated_text_loop",
                {"repeated_ngram_ratio": round(repeat_ratio, 3)},
            )

    if _looks_like_small_caps_gibberish(stripped):
        return ResponseQualityVerdict(
            False,
            "unicode_gibberish",
            {"non_ascii_letter_ratio": round(_non_ascii_letter_ratio(stripped), 3)},
        )

    # Long answers with almost no business vocabulary and many repeated English
    # filler terms are a common symptom of fallback degeneration.
    if len(tokens) >= 40:
        business_terms = {
            "samchat",
            "contabilidad",
            "contable",
            "finanzas",
            "poliza",
            "póliza",
            "cfdi",
            "gasto",
            "gastos",
            "pago",
            "pagos",
            "torneo",
            "equipo",
            "jugador",
            "presupuesto",
            "evidencia",
            "fuente",
            "datos",
        }
        business_hits = sum(1 for token in tokens if token in business_terms)
        english_filler = sum(1 for token in tokens if token in {"the", "then", "to", "played"})
        if business_hits == 0 and english_filler >= 8:
            return ResponseQualityVerdict(False, "low_signal_fallback_loop")

    return ResponseQualityVerdict(True)


def render_quality_fallback(*, user_message: str, reason: str) -> str:
    normalized = unicodedata.normalize("NFKD", user_message or "").lower()
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    if any(token in normalized for token in ("contabilidad", "contable", "coi", "poliza", "polizas")):
        subject = "contabilidad"
        next_step = "Consultar el snapshot financiero/contable canónico y responder con bloqueos, pólizas, CFDI y pendientes reales."
    elif any(token in normalized for token in ("dueno", "dueño", "owner", "director general", "pack")):
        subject = "pack del dueño"
        next_step = "Consultar el Owner Pack readiness y responder con cobertura, faltantes y fuentes disponibles."
    else:
        subject = "esta solicitud"
        next_step = "Reintentar por la ruta read-only correspondiente o pedir una precisión mínima antes de responder."

    return "\n".join(
        [
            f"No voy a mostrar la respuesta generada para {subject} porque no pasó el control de calidad de salida.",
            f"Motivo técnico: {reason}.",
            "",
            "Lo seguro en este momento:",
            "- No ejecuté cambios; esto sigue en modo solo lectura.",
            "- La respuesta bloqueada no debe usarse como evidencia.",
            f"- Siguiente paso: {next_step}",
        ]
    )
