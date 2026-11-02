"""Tools end to end over the real processed data (no LLM)."""

import pytest

from emporio.tools import (
    DATA_TOOLS,
    calculate_payment,
    check_return_eligibility,
    get_order,
    get_product,
    list_my_orders,
    search_products,
)


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setenv("EMPORIO_NOW", "2026-11-02T10:00:00")


def run(tool, **args):
    return tool.invoke(args)


def names(result):
    return [item["name"] for item in result["items"]]


# --------------------------------------------------------------------- catalog


def test_violoes_ate_1000_only_offerable_closest_to_budget_first():
    result = run(search_products, category="Violões", max_price=1000)
    # Offerable violões whose lowest price fits R$ 1000 (GF-3D 96 and SN-7C 113 excluded).
    assert result["total_found"] == 13
    assert len(result["items"]) == 6
    gnf3 = result["items"][0]
    assert gnf3["id"] == 101
    assert gnf3["price"] == "R$ 1.049,00" and gnf3["pix_price"] == "R$ 996,55"
    assert "PIX" in gnf3["price_note"]


def test_violoes_ate_1000_never_lists_unsellable():
    result = run(search_products, category="Violões", max_price=1000, only_available=True)
    names_found = " ".join(names(result))
    assert "GF-3D" not in names_found and "SN-7C" not in names_found


def test_search_limit_is_capped_with_note():
    result = run(search_products, query="violão")
    assert len(result["items"]) == 6 and result["total_found"] > 6
    assert "note" in result


def test_search_unknown_category():
    result = run(search_products, category="Acessórios")
    assert not result["ok"] and "Violões" in result["error"]


def test_search_empty_category_says_so():
    result = run(search_products, query="violino")
    assert result["ok"] and result["items"] == [] and "Cordas Orquestrais" in result["note"]


def test_search_reports_unavailable_matches():
    result = run(search_products, query="Giannini GF-3D")
    assert result["items"] == []
    assert result["not_available"][0]["availability"] == "indisponível no momento"


def test_inactive_promotion_never_reaches_the_llm():
    result = run(get_product, name_or_id="Takamine GD20")  # promotion 4 (12%) is inactive
    assert result["promo"] is None
    assert result["price"] == "R$ 2.199,00"
    assert result["pix_price"] == "R$ 2.089,05"
    assert result["installments"] == "até 12x de R$ 183,25 sem juros"
    assert "1.935" not in str(result)


def test_active_promotion_without_extra_pix():
    result = run(get_product, name_or_id="90")  # Crafter HT-100, promotion 19
    assert result["promo"]["percent"] == "18%"
    assert result["promo"]["final_price"] == "R$ 1.967,18"
    assert "pix_price" not in result


def test_discontinued_with_stock_is_not_offered():
    result = run(get_product, name_or_id="136")
    assert "fora de catálogo" in result["availability"]
    assert "price" not in result
    assert result["alternatives"] and all(
        alt["availability"] == "disponível em estoque" for alt in result["alternatives"]
    )
    assert "description" not in result  # description conflicts with the name (BR-16)


def test_coming_soon_has_no_price():
    result = run(get_product, name_or_id="Kala KA-ABP-CTG")
    assert "lançamento em breve" in result["availability"] and "price" not in result


def test_out_of_stock_gets_alternatives():
    result = run(get_product, name_or_id="Giannini GF-3D")
    assert result["availability"] == "indisponível no momento"
    assert len(result["alternatives"]) == 3


def test_ambiguous_product():
    result = run(get_product, name_or_id="Takamine")
    assert result["ambiguous"] and len(result["candidates"]) >= 4


def test_unknown_product():
    assert not run(get_product, name_or_id="Stradivarius")["ok"]


# --------------------------------------------------------------------- payment


def test_payment_for_product_with_local_shipping():
    result = run(calculate_payment, product_id=95, city="Campo Grande")
    assert result["pix_price"] == "R$ 2.089,05"
    assert result["split_payment_allowed"] is True
    assert result["shipping"]["fee"] == "grátis"


def test_payment_for_small_amount_pays_local_fee():
    result = run(calculate_payment, amount=349.9, city="Campo Grande")
    assert result["shipping"]["fee"] == "R$ 35,00"
    assert result["installments"] == "até 4x de R$ 87,48 sem juros"
    assert result["split_payment_allowed"] is False


def test_payment_other_city_has_no_invented_fee():
    result = run(calculate_payment, product_id=95, city="Dourados")
    assert result["shipping"]["fee"] == "calculado pelo CEP no fechamento"


def test_payment_large_item_needs_quote():
    result = run(calculate_payment, product_id=141, city="Campo Grande")
    assert result["shipping"]["recommended_action"] == "handoff"


def test_payment_requires_exactly_one_input():
    assert not run(calculate_payment)["ok"]
    assert not run(calculate_payment, product_id=95, amount=10)["ok"]


def test_payment_refuses_unsellable_product():
    assert not run(calculate_payment, product_id=113)["ok"]


# --------------------------------------------------------------------- orders (BR-08, BR-09)


def test_order_requires_matching_contact_and_is_generic():
    wrong = run(get_order, order_id=8, contact="lucas.mendes@jmail.com")  # another customer
    missing = run(get_order, order_id=999, contact="lucas.mendes@jmail.com")
    assert not wrong["ok"] and wrong == missing


def test_order_8_is_late():
    result = run(get_order, order_id=8, contact="(67) 99654-7821")
    assert result["ok"] and result["customer_first_name"] == "Ana"
    assert result["is_late"] and result["recommended_action"] == "handoff"
    assert result["estimated_delivery"] == "28/02/2026"
    assert result["status_label"] == "enviado"
    assert "Ferreira" not in str(result) and "@" not in str(result)


def test_order_13_is_stale():
    result = run(get_order, order_id=13, contact="FERNANDA.RIBEIRO@coldmail.com")
    assert result["is_stale"] and not result["is_late"]
    assert result["recommended_action"] == "handoff"


def test_cancelled_order_shows_reason():
    result = run(get_order, order_id=17, contact=_phone_of_order(17))
    assert result["status_label"] == "cancelado" and "cancelado pelo cliente" in result["notes"]


def test_order_total_is_the_registered_one():
    result = run(get_order, order_id=3, contact=_phone_of_order(3))
    assert result["total"] == "R$ 3.450,00"


def test_list_my_orders():
    result = run(list_my_orders, phone="(67) 98432-1098")  # customer 3: orders 1 and 19
    assert [o["order_id"] for o in result["orders"]] == [19, 1]
    assert not run(list_my_orders, phone="(67) 90000-0000")["ok"]


def test_return_order_4_points_to_manufacturer_warranty():
    result = run(
        check_return_eligibility,
        order_id=4,
        contact="lucas.mendes@jmail.com",
        reason="arrependimento",
    )
    assert result["ok"] and not result["eligible"]
    assert result["path"] == "garantia_fabricante"
    assert result["all_deadlines"]["arrependimento"] == "29/12/2025"


def test_return_requires_contact():
    result = run(check_return_eligibility, order_id=4, contact="x@y.com", reason="defeito")
    assert not result["ok"]


def test_every_tool_has_a_portuguese_description():
    for tool in DATA_TOOLS:
        assert len(tool.description) > 80, tool.name


def _phone_of_order(order_id: int) -> str:
    from emporio.data.repository import get_repository

    repo = get_repository()
    customer = repo._customers[repo.orders[order_id]["customer_id"]]
    return customer["phone"]
