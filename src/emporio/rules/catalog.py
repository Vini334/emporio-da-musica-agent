"""Catalog rules: availability (BR-01) and alternatives (BR-02)."""

from __future__ import annotations

from collections.abc import Iterable

MAX_ALTERNATIVES = 3

AVAILABILITY_LABELS = {
    "disponivel": "disponível em estoque",
    "sem_estoque": "indisponível no momento",
    "descontinuado": "fora de catálogo (não é mais vendido)",
    "em_breve": "lançamento em breve, ainda sem venda e sem data prevista",
}


def availability(status: str, stock: int) -> str:
    """One of ``disponivel | sem_estoque | descontinuado | em_breve`` (BR-01).

    A discontinued product is never offered, even with stock left (e.g. product 136).
    """
    if status == "discontinued":
        return "descontinuado"
    if status == "coming_soon":
        return "em_breve"
    if status == "active":
        return "disponivel" if stock > 0 else "sem_estoque"
    raise ValueError(f"unknown product status: {status!r}")


def is_offerable(product: dict) -> bool:
    return availability(product["status"], product["stock_quantity"]) == "disponivel"


def pick_alternatives(
    reference: dict, candidates: Iterable[dict], limit: int = MAX_ALTERNATIVES
) -> list[dict]:
    """Offerable products of the same category, closest in price first (BR-02).

    Ties prefer the same string type and the same ``electronics`` spec as the reference.
    """
    ref_specs = reference.get("specs", {})

    def rank(product: dict) -> tuple[float, int, int, int]:
        specs = product.get("specs", {})
        return (
            round(abs(product["price_brl"] - reference["price_brl"]), 2),
            0 if specs.get("strings") == ref_specs.get("strings") else 1,
            0 if specs.get("electronics") == ref_specs.get("electronics") else 1,
            product["product_id"],
        )

    pool = [
        p
        for p in candidates
        if p["category_id"] == reference["category_id"]
        and p["product_id"] != reference["product_id"]
        and is_offerable(p)
    ]
    return sorted(pool, key=rank)[:limit]
