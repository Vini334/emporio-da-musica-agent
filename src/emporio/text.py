"""Text normalization shared by data preparation and search."""

from __future__ import annotations

import re
import unicodedata

_WHITESPACE = re.compile(r"\s+")
_NON_DIGITS = re.compile(r"\D")


def strip_accents(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def normalize(value: str) -> str:
    """Lowercase, accent-free, single-spaced version of ``value`` (used for matching)."""
    return _WHITESPACE.sub(" ", strip_accents(value).lower()).strip()


def digits(value: str) -> str:
    return _NON_DIGITS.sub("", value)
