"""Order rules: verified access (BR-08), late and stale orders (BR-09), status labels."""

from __future__ import annotations

from datetime import date

from emporio.text import digits

STALE_AFTER_DAYS = 7
LATE_CANDIDATE_STATUSES = frozenset({"shipped", "confirmed"})
UNSHIPPED_OPEN_STATUSES = frozenset({"pending", "confirmed"})

STATUS_LABELS = {
    "pending": "aguardando confirmação do pagamento",
    "confirmed": "pagamento confirmado, em separação",
    "shipped": "enviado",
    "delivered": "entregue",
    "cancelled": "cancelado",
}

_PAYMENT_LABELS = {"pix": "PIX", "debit": "cartão de débito", "boleto": "boleto bancário"}

# Brazilian phone: DDD (2 digits) + 8 or 9 digits; "+55" is optional.
_MIN_PHONE_DIGITS = 10
_COUNTRY_CODE = "55"


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


def recommended_action(late: bool, stale: bool) -> str | None:
    """Late or stale orders are offered a handoff to the human team (BR-09)."""
    return "handoff" if late or stale else None


def status_label(status: str) -> str:
    return STATUS_LABELS[status]


def payment_label(payment_method: str, installments: int | None = None) -> str:
    if installments:
        return f"cartão de crédito em {installments}x"
    return _PAYMENT_LABELS.get(payment_method, payment_method)


def normalize_phone(value: str) -> str | None:
    """Digits of a Brazilian phone without the country code, or None if it is not one."""
    number = digits(value)
    if len(number) in (12, 13) and number.startswith(_COUNTRY_CODE):
        number = number[len(_COUNTRY_CODE) :]
    if len(number) not in (_MIN_PHONE_DIGITS, _MIN_PHONE_DIGITS + 1):
        return None
    return number


def contact_matches(contact: str, email: str, phone: str) -> bool:
    """Whether ``contact`` (e-mail or phone) identifies the customer (BR-08).

    E-mail is compared case-insensitively; phone only by its digits (DDD required).
    """
    contact = contact.strip()
    if not contact:
        return False
    if "@" in contact:
        return contact.lower() == email.strip().lower()
    given = normalize_phone(contact)
    return given is not None and given == normalize_phone(phone)
