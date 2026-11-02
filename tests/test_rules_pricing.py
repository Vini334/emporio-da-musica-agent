import pytest

from emporio.rules.pricing import apply_discount, installment_allowed, min_installment_value


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


def test_apply_discount():
    assert apply_discount(2399.0, 18) == 1967.18  # Crafter HT-100, promotion 19
    assert apply_discount(2199.0, 5) == 2089.05  # Takamine GD20 on PIX
