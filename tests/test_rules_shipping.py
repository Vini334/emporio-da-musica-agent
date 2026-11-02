import pytest

from emporio.rules.shipping import is_large_item, is_local, shipping_quote


@pytest.mark.parametrize(
    ("city", "local"),
    [
        ("Campo Grande", True),
        ("campo grande - MS", True),
        ("Campo Grande/MS", True),
        ("CAMPO GRANDE, ms", True),
        ("Campo Grande - RJ", False),
        ("Dourados", False),
    ],
)
def test_is_local(city, local):
    assert is_local(city) is local


def test_local_free_only_above_500():
    assert shipping_quote("Campo Grande", 500.01).fee == 0.0
    assert shipping_quote("Campo Grande", 500.0).fee == 35.0
    assert shipping_quote("Campo Grande", 120.0).deadline == "1 a 3 dias úteis"


def test_other_cities_never_get_an_invented_fee():
    quote = shipping_quote("Dourados", 3000.0)
    assert quote.fee is None and not quote.local
    assert [name for name, _ in quote.options] == ["PAC (Correios)", "SEDEX (Correios)", "Jadlog"]


def test_large_items_need_human_quote_everywhere():
    assert is_large_item(3, "Bateria Acústica Yamaha Kit 1 Studio")
    assert is_large_item(4, "Piano Digital Yamaha P-145")
    assert not is_large_item(3, "Bateria Eletrônica Roland TD-07")
    assert not is_large_item(4, "Teclado Sintetizador Korg Synth 1 Pro")
    quote = shipping_quote("Campo Grande", 14922.0, large_item=True)
    assert quote.requires_human_quote and quote.fee is None
