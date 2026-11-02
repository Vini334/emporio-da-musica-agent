from datetime import date

from emporio.rules.orders import is_late, is_stale

TODAY = date(2026, 11, 2)


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
