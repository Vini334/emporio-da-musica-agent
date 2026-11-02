"""Return / exchange / warranty paths (BR-10)."""

from datetime import date

import pytest

from emporio.rules.returns import assess_return

# Order 4: bought 10/12/2025, received (estimated) 22/12/2025.
ORDER_4 = {
    "status": "delivered",
    "order_date": date(2025, 12, 10),
    "received_date": date(2025, 12, 22),
}


def assess(reason: str, today: date, **overrides):
    return assess_return(**{**ORDER_4, **overrides}, reason=reason, today=today)


def test_order_4_is_past_every_deadline():
    result = assess("arrependimento", date(2026, 11, 2))
    assert not result.eligible
    assert result.path == "garantia_fabricante"
    assert result.deadlines["arrependimento"] == date(2025, 12, 29)
    assert result.deadlines["troca_defeito"] == date(2026, 1, 9)
    assert result.deadlines["garantia_legal"] == date(2026, 3, 22)
    for text in ("29/12/2025", "09/01/2026", "22/03/2026"):
        assert text in result.reasoning


@pytest.mark.parametrize(
    ("today", "eligible"), [(date(2025, 12, 29), True), (date(2025, 12, 30), False)]
)
def test_regret_window_is_seven_days_after_receipt(today, eligible):
    result = assess("arrependimento", today)
    assert result.eligible is eligible
    if eligible:
        assert result.path == "devolucao" and result.recommended_action == "handoff"


def test_preference_inside_window_is_exchange():
    result = assess("preferencia", date(2025, 12, 25))
    assert result.eligible and result.path == "troca"


@pytest.mark.parametrize(
    ("today", "path", "deadline"),
    [
        (date(2026, 1, 9), "troca", date(2026, 1, 9)),  # 30 days after purchase
        (date(2026, 1, 10), "garantia_legal", date(2026, 3, 22)),
        (date(2026, 3, 22), "garantia_legal", date(2026, 3, 22)),  # 90 days after receipt
        (date(2026, 3, 23), "garantia_fabricante", None),
    ],
)
def test_defect_paths(today, path, deadline):
    result = assess("defeito", today)
    assert (result.path, result.deadline) == (path, deadline)
    assert result.eligible is (path != "garantia_fabricante")
    assert result.recommended_action == "handoff"


def test_unshipped_order_is_cancellation():
    result = assess("arrependimento", date(2026, 11, 2), status="pending", received_date=None)
    assert result.path == "cancelamento" and result.recommended_action == "handoff"


def test_shipped_order_waits_for_delivery():
    result = assess("defeito", date(2026, 11, 2), status="shipped")
    assert result.path == "aguardar_entrega" and not result.eligible


def test_cancelled_order_has_nothing_to_return():
    result = assess("arrependimento", date(2026, 11, 2), status="cancelled")
    assert result.path is None and not result.eligible


def test_unknown_reason():
    with pytest.raises(ValueError):
        assess("nao_gostei", date(2026, 11, 2))
