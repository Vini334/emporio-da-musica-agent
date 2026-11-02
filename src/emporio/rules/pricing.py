"""Pricing rules: PIX discount and installments (SPEC BR-04, BR-05)."""

from __future__ import annotations

PIX_DISCOUNT_PERCENT = 5.0
MAX_INSTALLMENTS = 12

# (last installment count of the tier, minimum installment value) — manual §3.1, DD-§6.
INSTALLMENT_TIERS: tuple[tuple[int, float], ...] = ((3, 50.0), (6, 80.0), (12, 100.0))


def apply_discount(price: float, percent: float) -> float:
    """``round(price × (1 − percent/100), 2)`` (SPEC BR-03)."""
    return round(price * (1 - percent / 100), 2)


def min_installment_value(installments: int) -> float:
    """Minimum value of each installment for ``installments`` payments (1x has no minimum)."""
    if not 1 <= installments <= MAX_INSTALLMENTS:
        raise ValueError(f"installments must be between 1 and {MAX_INSTALLMENTS}: {installments}")
    if installments == 1:
        return 0.0
    for last_of_tier, minimum in INSTALLMENT_TIERS:
        if installments <= last_of_tier:
            return minimum
    raise AssertionError("unreachable: tiers cover 1..MAX_INSTALLMENTS")


def installment_allowed(amount: float, installments: int) -> bool:
    """Whether ``amount`` can be split into ``installments`` respecting the tier minimum."""
    return amount >= min_installment_value(installments) * installments
