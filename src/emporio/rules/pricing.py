"""Pricing rules: promotions, PIX discount, installments and split payment (SPEC BR-03..06)."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

PIX_DISCOUNT_PERCENT = 5.0
MAX_INSTALLMENTS = 12
SPLIT_PAYMENT_MIN_EXCLUSIVE = 2000.0

# (last installment count of the tier, minimum installment value) — manual §3.1, DD-§6.
INSTALLMENT_TIERS: tuple[tuple[int, float], ...] = ((3, 50.0), (6, 80.0), (12, 100.0))


def round_money(value: float) -> float:
    """Round to cents, half up (as receipts do; plain ``round`` gives 87.475 -> 87.47)."""
    return float(Decimal(repr(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def apply_discount(price: float, percent: float) -> float:
    """``price × (1 − percent/100)`` rounded to cents (SPEC BR-03)."""
    return round_money(price * (100 - percent) / 100)


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


def max_installments(amount: float) -> int:
    """Largest number of interest-free installments allowed for ``amount`` (BR-05)."""
    if amount <= 0:
        raise ValueError(f"amount must be positive: {amount}")
    return max(n for n in range(1, MAX_INSTALLMENTS + 1) if installment_allowed(amount, n))


def installment_value(amount: float, installments: int) -> float:
    return round_money(amount / installments)


def split_payment_allowed(amount: float) -> bool:
    """Combining payment methods (e.g. PIX + card) is allowed above R$ 2.000,00 (BR-06)."""
    return amount > SPLIT_PAYMENT_MIN_EXCLUSIVE


def best_active_discount(promotions: Iterable[dict]) -> dict | None:
    """The active promotion with the largest discount, or None (BR-03).

    Inactive promotions are ignored: they must never reach the customer.
    """
    active = [p for p in promotions if p.get("is_active")]
    return max(active, key=lambda p: p["discount_percent"], default=None)


@dataclass(frozen=True)
class PriceQuote:
    list_price: float
    promo_percent: float | None
    promo_description: str | None
    final_price: float  # with the promotion, if any; this is the card price
    pix_price: float | None  # None when a promotion applies (not cumulative, BR-04)
    max_installments: int
    installment_value: float
    split_payment_allowed: bool

    @property
    def lowest_price(self) -> float:
        return self.pix_price if self.pix_price is not None else self.final_price


def quote(list_price: float, promotion: dict | None = None) -> PriceQuote:
    """Every price condition for one item at ``list_price`` (BR-03..06).

    ``promotion`` must already be the best active one (see ``best_active_discount``).
    """
    if list_price <= 0:
        raise ValueError(f"list_price must be positive: {list_price}")
    if promotion is not None and not promotion.get("is_active"):
        raise ValueError("inactive promotions must never be applied")

    percent = promotion["discount_percent"] if promotion else None
    final_price = apply_discount(list_price, percent) if percent else list_price
    pix_price = None if percent else apply_discount(list_price, PIX_DISCOUNT_PERCENT)
    installments = max_installments(final_price)
    return PriceQuote(
        list_price=list_price,
        promo_percent=percent,
        promo_description=promotion.get("description") if promotion else None,
        final_price=final_price,
        pix_price=pix_price,
        max_installments=installments,
        installment_value=installment_value(final_price, installments),
        split_payment_allowed=split_payment_allowed(final_price),
    )
