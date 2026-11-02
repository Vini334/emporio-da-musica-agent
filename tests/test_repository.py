"""Product search and name resolution over the processed data."""

import pytest

from emporio.data.repository import get_repository, match_key


@pytest.fixture(scope="module")
def repo():
    return get_repository()


def ids(products):
    return [p["product_id"] for p in products]


def test_match_key():
    assert match_key("Martin D-28 Elétrico") == "martin d28 eletrico"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Takamine GD20", 95),
        ("takamine gd 20", 95),
        ("gd20", 95),
        ("Martin D-28", 93),
        ("martin d28e", 108),
        ("Fender Strato", 145),
        ("kala ka-abp", 130),
        ("violão yamaha c40", 81),
        ("Ibanez Bass 2X", 136),
    ],
)
def test_match_name_resolves(repo, name, expected):
    match = repo.match_name(name)
    assert match.product is not None, match.candidates
    assert match.product["product_id"] == expected


def test_match_name_ambiguous_gives_candidates(repo):
    match = repo.match_name("Takamine")
    assert match.product is None
    assert {95, 100, 105, 115} <= set(ids(match.candidates))


def test_match_name_unknown(repo):
    assert repo.match_name("Stradivarius").product is None


def test_category_alias_in_query(repo):
    result = repo.search("violão elétrico nylon")
    assert result.category["category_id"] == 5
    assert set(ids(result.products)) == {98, 99, 100, 101, 102, 111, 115}


def test_search_by_spec_words(repo):
    assert set(ids(repo.search("ukulele tenor").products)) == {124, 125, 126, 127}
    assert 96 in ids(repo.search("violão aço").products)  # "aço" comes from specs


def test_search_empty_category(repo):
    result = repo.search("saxofone")
    assert result.category["category_id"] == 6 and result.products == []


def test_resolve_category(repo):
    assert repo.resolve_category("violões")["category_id"] == 5
    assert repo.resolve_category("9")["name"] == "Ukuleles"
    assert repo.resolve_category("pianos")["category_id"] == 4
    assert repo.resolve_category("acessórios") is None


def test_orders_by_phone_for_duplicated_email_customer(repo):
    first_name, orders = repo.orders_by_phone("(67) 98543-2109")
    assert first_name == "Bruno"
    assert [o["order_id"] for o in orders] == [10]
