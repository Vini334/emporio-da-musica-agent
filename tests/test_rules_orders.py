from datetime import date

import pytest

from emporio.rules.orders import (
    contact_matches,
    is_late,
    is_stale,
    normalize_phone,
    payment_label,
    recommended_action,
)

TODAY = date(2026, 11, 2)
EMAIL, PHONE = "Lucas.Mendes@jmail.com", "(67) 99812-3456"


def test_late_requires_past_estimated_delivery():
    assert is_late("shipped", date(2026, 2, 28), TODAY)
    assert not is_late("shipped", TODAY, TODAY)
    assert not is_late("shipped", None, TODAY)
    assert not is_late("delivered", date(2026, 2, 28), TODAY)


def test_stale_after_seven_days_without_shipping():
    assert is_stale("pending", date(2026, 10, 25), TODAY)  # 8 days
    assert not is_stale("pending", date(2026, 10, 26), TODAY)  # exactly 7 days
    assert is_stale("confirmed", date(2026, 2, 20), TODAY)
    assert not is_stale("shipped", date(2026, 2, 20), TODAY)
    assert not is_stale("cancelled", date(2026, 2, 20), TODAY)


def test_recommended_action():
    assert recommended_action(True, False) == "handoff"
    assert recommended_action(False, True) == "handoff"
    assert recommended_action(False, False) is None


@pytest.mark.parametrize(
    "contact",
    ["lucas.mendes@jmail.com", " LUCAS.MENDES@JMAIL.COM ", "67998123456", "+55 (67) 99812-3456"],
)
def test_contact_matches(contact):
    assert contact_matches(contact, EMAIL, PHONE)


@pytest.mark.parametrize(
    "contact",
    ["", "lucas@jmail.com", "99812-3456", "(67) 99812-3457", "lucas.mendes@jmail.com.br"],
)
def test_contact_does_not_match(contact):
    assert not contact_matches(contact, EMAIL, PHONE)


def test_normalize_phone():
    assert normalize_phone("+55 67 99812-3456") == "67998123456"
    assert normalize_phone("(67) 3341-4444") == "6733414444"
    assert normalize_phone("123") is None


def test_payment_label():
    assert payment_label("credit_6x", 6) == "cartão de crédito em 6x"
    assert payment_label("pix") == "PIX"
