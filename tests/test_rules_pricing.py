import pytest

from emporio.rules.pricing import (
    apply_discount,
    best_active_discount,
    installment_allowed,
    max_installments,
    min_installment_value,
    quote,
    split_payment_allowed,
)


@pytest.mark.parametrize(
    ("installments", "minimum"),
    [(1, 0.0), (2, 50.0), (3, 50.0), (4, 80.0), (6, 80.0), (7, 100.0), (12, 100.0)],
)
def test_min_installment_value_tiers(installments, minimum):
    assert min_installment_value(installments) == minimum


@pytest.mark.parametrize("installments", [0, 13])
def test_min_installment_value_out_of_range(installments):
    with pytest.raises(ValueError):
        min_installment_value(installments)


@pytest.mark.parametrize(
    ("amount", "installments", "allowed"),
    [
        (10.0, 1, True),  # 1x is always allowed
        (150.0, 3, True),
        (149.99, 3, False),
        (480.0, 6, True),
        (349.9, 6, False),  # order 9
        (459.9, 12, False),  # order 18
        (2199.0, 12, True),  # Takamine GD20: 12x of R$ 183,25
    ],
)
def test_installment_allowed(amount, installments, allowed):
    assert installment_allowed(amount, installments) is allowed


@pytest.mark.parametrize(
    ("amount", "expected"),
    [
        (49.0, 1),  # 2x would be below R$ 50
        (100.0, 2),
        (150.0, 3),
        (319.99, 3),  # 4x needs R$ 320
        (320.0, 4),
        (480.0, 6),
        (699.99, 6),  # 7x needs R$ 700
        (700.0, 7),
        (1199.99, 11),
        (1200.0, 12),
        (11499.0, 12),
    ],
)
def test_max_installments(amount, expected):
    assert max_installments(amount) == expected


def test_split_payment_only_above_2000():
    assert not split_payment_allowed(2000.0)
    assert split_payment_allowed(2000.01)


def test_apply_discount():
    assert apply_discount(2399.0, 18) == 1967.18  # Crafter HT-100, promotion 19
    assert apply_discount(2199.0, 5) == 2089.05  # Takamine GD20 on PIX


def test_best_active_discount_ignores_inactive_and_picks_largest():
    promos = [
        {"discount_percent": 40.0, "is_active": False},
        {"discount_percent": 10.0, "is_active": True},
        {"discount_percent": 15.0, "is_active": True},
    ]
    assert best_active_discount(promos)["discount_percent"] == 15.0
    assert best_active_discount([{"discount_percent": 12.0, "is_active": False}]) is None
    assert best_active_discount([]) is None


def test_quote_without_promotion_has_pix():
    q = quote(2199.0)  # Takamine GD20
    assert q.final_price == 2199.0
    assert q.pix_price == 2089.05
    assert (q.max_installments, q.installment_value) == (12, 183.25)
    assert q.split_payment_allowed


def test_quote_with_promotion_has_no_extra_pix():
    q = quote(2399.0, {"discount_percent": 18.0, "is_active": True, "description": "x"})
    assert q.final_price == 1967.18
    assert q.pix_price is None  # BR-04: not cumulative
    assert q.lowest_price == 1967.18
    assert q.max_installments == 12


def test_quote_refuses_inactive_promotion():
    with pytest.raises(ValueError):
        quote(2199.0, {"discount_percent": 12.0, "is_active": False})
