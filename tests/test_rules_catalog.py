import pytest

from emporio.rules.catalog import availability, pick_alternatives


@pytest.mark.parametrize(
    ("status", "stock", "expected"),
    [
        ("active", 3, "disponivel"),
        ("active", 0, "sem_estoque"),
        ("discontinued", 13, "descontinuado"),  # product 136: never offered
        ("coming_soon", 2, "em_breve"),
    ],
)
def test_availability(status, stock, expected):
    assert availability(status, stock) == expected


def _product(pid, price, category=5, status="active", stock=5, strings="steel", electronics="no"):
    return {
        "product_id": pid,
        "price_brl": price,
        "category_id": category,
        "status": status,
        "stock_quantity": stock,
        "specs": {"strings": strings, "electronics": electronics},
    }


def test_alternatives_same_category_offerable_closest_price():
    ref = _product(1, 800.0, stock=0)
    candidates = [
        ref,
        _product(2, 790.0),
        _product(3, 1500.0),
        _product(4, 805.0, status="discontinued", stock=9),
        _product(5, 801.0, stock=0),
        _product(6, 800.0, category=9),
        _product(7, 700.0),
        _product(8, 950.0),
    ]
    assert [p["product_id"] for p in pick_alternatives(ref, candidates)] == [2, 7, 8]


def test_alternatives_tie_prefers_same_strings_then_electronics():
    ref = _product(1, 800.0, stock=0)
    candidates = [
        _product(2, 900.0, strings="nylon"),
        _product(3, 700.0, electronics="yes"),
        _product(4, 900.0),
    ]
    assert [p["product_id"] for p in pick_alternatives(ref, candidates)] == [4, 3, 2]
