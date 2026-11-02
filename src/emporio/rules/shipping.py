"""Shipping rules (SPEC BR-07, manual §5, DD-§6).

Only Campo Grande has a fee table. Elsewhere the fee depends on the destination ZIP code and is
calculated at checkout, so no value is ever invented. Large items always go to a human quote.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from emporio.text import normalize

LOCAL_CITY = "campo grande"
LOCAL_STATE = "ms"
LOCAL_FREE_ABOVE = 500.0  # strictly above
LOCAL_FEE = 35.0
LOCAL_DEADLINE = "1 a 3 dias úteis"

CARRIERS: tuple[tuple[str, str], ...] = (
    ("PAC (Correios)", "5 a 12 dias úteis"),
    ("SEDEX (Correios)", "2 a 5 dias úteis"),
    ("Jadlog", "3 a 8 dias úteis"),
)

LARGE_ITEM_CATEGORY_IDS = frozenset({3})  # Baterias e Percussão (acoustic kits)
_LARGE_ITEM_TERMS = ("bateria acustica", "piano digital", "contrabaixo acustico")
_ELECTRONIC_DRUM_TERMS = ("bateria eletronica",)

_STATE_SUFFIX = re.compile(r"^(?P<city>.+?)\s*(?:[-/,]\s*|\s+)(?P<uf>[a-z]{2})$")


def is_large_item(category_id: int, name: str) -> bool:
    """Acoustic drum kits, digital pianos and acoustic double basses need a special quote."""
    normalized = normalize(name)
    if any(term in normalized for term in _LARGE_ITEM_TERMS):
        return True
    return category_id in LARGE_ITEM_CATEGORY_IDS and not any(
        term in normalized for term in _ELECTRONIC_DRUM_TERMS
    )


def is_local(city: str) -> bool:
    """Whether ``city`` is Campo Grande/MS (accepts "Campo Grande", "campo grande - MS"…)."""
    normalized = normalize(city).strip(" .")
    match = _STATE_SUFFIX.match(normalized)
    if match and match.group("city") == LOCAL_CITY:
        return match.group("uf") == LOCAL_STATE
    return normalized == LOCAL_CITY


@dataclass(frozen=True)
class ShippingQuote:
    local: bool
    requires_human_quote: bool
    fee: float | None  # None when it cannot be calculated here
    deadline: str | None
    options: list[tuple[str, str]] = field(default_factory=list)  # (carrier, deadline)
    note: str = ""


def shipping_quote(city: str, amount: float, large_item: bool = False) -> ShippingQuote:
    if large_item:
        return ShippingQuote(
            local=is_local(city),
            requires_human_quote=True,
            fee=None,
            deadline=None,
            note="Item de grande porte: frete especial, com cotação individual feita pela equipe.",
        )
    if is_local(city):
        free = amount > LOCAL_FREE_ABOVE
        return ShippingQuote(
            local=True,
            requires_human_quote=False,
            fee=0.0 if free else LOCAL_FEE,
            deadline=LOCAL_DEADLINE,
            note="Entrega por motoboy próprio; a loja liga antes de entregar."
            + ("" if free else " Frete grátis para pedidos acima de R$ 500,00."),
        )
    return ShippingQuote(
        local=False,
        requires_human_quote=False,
        fee=None,
        deadline=None,
        options=list(CARRIERS),
        note="O valor é calculado pelo CEP de destino no fechamento do pedido. "
        "Todos os envios têm rastreio e seguro incluído.",
    )
