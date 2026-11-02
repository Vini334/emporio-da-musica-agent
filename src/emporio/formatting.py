"""Customer-facing formatting in pt-BR (SPEC BR-15)."""

from __future__ import annotations

from datetime import date


def brl(value: float) -> str:
    """Format a value as Brazilian currency: ``1234.5`` -> ``R$ 1.234,50``."""
    sign = "-" if value < 0 else ""
    formatted = f"{abs(value):,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
    return f"{sign}R$ {formatted}"


def date_br(value: date) -> str:
    return value.strftime("%d/%m/%Y")
