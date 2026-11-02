"""Order rules: late and stale orders (SPEC BR-09)."""

from __future__ import annotations

from datetime import date

STALE_AFTER_DAYS = 7
LATE_CANDIDATE_STATUSES = frozenset({"shipped", "confirmed"})
UNSHIPPED_OPEN_STATUSES = frozenset({"pending", "confirmed"})


def is_late(status: str, estimated_delivery: date | None, today: date) -> bool:
    """Shipped/confirmed order whose estimated delivery date has already passed."""
    return (
        status in LATE_CANDIDATE_STATUSES
        and estimated_delivery is not None
        and estimated_delivery < today
    )


def is_stale(status: str, order_date: date, today: date) -> bool:
    """Pending/confirmed order still not shipped more than 7 days after it was placed."""
    return status in UNSHIPPED_OPEN_STATUSES and (today - order_date).days > STALE_AFTER_DAYS
