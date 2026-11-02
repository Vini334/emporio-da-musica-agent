"""Read-only access to ``data/processed`` (no LLM, no business decisions beyond lookups).

Customer records never leave this module except through ``verified_order`` and
``orders_by_phone``, which enforce the contact check of BR-08.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path

from rapidfuzz import fuzz, process

from emporio import config
from emporio.rules.orders import contact_matches, normalize_phone
from emporio.text import normalize

_DATE_FIELDS = {"order_date", "estimated_delivery", "created_at"}
_NON_ALNUM = re.compile(r"[^a-z0-9 ]+")

# Words that point to a category (normalized, singular and plural).
CATEGORY_ALIASES: dict[str, int] = {
    **dict.fromkeys(["guitarra", "guitarras"], 1),
    **dict.fromkeys(["baixo", "baixos", "contrabaixo", "contrabaixos"], 2),
    **dict.fromkeys(["bateria", "baterias", "percussao"], 3),
    **dict.fromkeys(
        ["teclado", "teclados", "piano", "pianos", "sintetizador", "sintetizadores", "synth"], 4
    ),
    **dict.fromkeys(["violao", "violoes"], 5),
    **dict.fromkeys(
        ["sopro", "flauta", "flautas", "clarinete", "clarinetes", "sax", "saxofone", "oboe"], 6
    ),
    **dict.fromkeys(["trompete", "trompetes", "trombone", "trombones", "tuba", "trompa"], 7),
    **dict.fromkeys(["violino", "violinos", "viola", "violoncelo", "cello", "orquestral"], 8),
    **dict.fromkeys(["ukulele", "ukuleles", "uke"], 9),
}

# Extra search terms derived from specs, so customers can use everyday words.
_SPEC_TERMS = {
    ("strings", "steel"): "aco",
    ("strings", "nylon"): "nylon",
    ("electronics", "yes"): "eletrico eletroacustico captacao",
}

STOPWORDS = frozenset(
    [
        "a",
        "o",
        "as",
        "os",
        "de",
        "do",
        "da",
        "dos",
        "das",
        "um",
        "uma",
        "uns",
        "umas",
        "e",
        "ou",
        "com",
        "sem",
        "para",
        "pra",
        "pro",
        "por",
        "em",
        "no",
        "na",
        "nos",
        "nas",
        "ate",
        "ate",
        "r",
        "rs",
        "reais",
        "mil",
        "quero",
        "queria",
        "tem",
        "tens",
        "voces",
        "vcs",
        "algum",
        "alguma",
        "alguns",
        "algumas",
        "qual",
        "quais",
        "que",
        "me",
        "mostra",
        "mostre",
        "preco",
        "precos",
        "barato",
        "barata",
        "baratos",
        "baratas",
        "modelo",
        "modelos",
        "instrumento",
        "instrumentos",
        "loja",
        "venda",
        "vende",
        "vendem",
    ]
)

FUZZY_TOKEN_THRESHOLD = 85
FUZZY_QUERY_THRESHOLD = 70
NAME_MATCH_THRESHOLD = 85
NAME_CANDIDATE_THRESHOLD = 60
NAME_UNAMBIGUOUS_GAP = 8


def match_key(value: str) -> str:
    """Normalized text without punctuation: ``"Martin D-28"`` -> ``"martin d28"``."""
    return " ".join(_NON_ALNUM.sub("", normalize(value)).split())


def query_tokens(query: str) -> list[str]:
    return [t for t in match_key(query).split() if t not in STOPWORDS]


@dataclass(frozen=True)
class SearchResult:
    products: list[dict]
    category: dict | None  # category filter applied (explicit or inferred from the query)


@dataclass(frozen=True)
class NameMatch:
    product: dict | None  # set when the match is unambiguous
    candidates: list[dict]  # set when ambiguous


class Repository:
    def __init__(self, processed_dir: Path = config.PROCESSED_DIR) -> None:
        tables = {
            name: _load(processed_dir / f"{name}.json")
            for name in ("categories", "products", "promotions", "customers", "orders")
        }
        tables["order_items"] = _load(processed_dir / "order_items.json")

        self.categories: dict[int, dict] = {c["category_id"]: c for c in tables["categories"]}
        self.products: dict[int, dict] = {p["product_id"]: p for p in tables["products"]}
        self.orders: dict[int, dict] = {o["order_id"]: o for o in tables["orders"]}
        self._customers: dict[int, dict] = {c["customer_id"]: c for c in tables["customers"]}

        self._promotions: dict[int, list[dict]] = defaultdict(list)
        for promo in tables["promotions"]:
            self._promotions[promo["product_id"]].append(promo)
        self._items: dict[int, list[dict]] = defaultdict(list)
        for item in tables["order_items"]:
            self._items[item["order_id"]].append(item)

        self._search_tokens = {pid: _search_tokens(p) for pid, p in self.products.items()}
        self._name_keys = {pid: match_key(p["name"]) for pid, p in self.products.items()}

    # ------------------------------------------------------------------ catalog

    def promotions_for(self, product_id: int) -> list[dict]:
        return list(self._promotions.get(product_id, []))

    def resolve_category(self, value: str | int) -> dict | None:
        """Category by id, name or alias (``"violão"`` -> Violões)."""
        text = str(value).strip()
        if text.isdigit():
            return self.categories.get(int(text))
        key = match_key(text)
        for category in self.categories.values():
            if match_key(category["name"]) == key:
                return category
        for token in key.split():
            if token in CATEGORY_ALIASES:
                return self.categories[CATEGORY_ALIASES[token]]
        best = process.extractOne(
            key,
            {cid: match_key(c["name"]) for cid, c in self.categories.items()},
            scorer=fuzz.WRatio,
            score_cutoff=FUZZY_TOKEN_THRESHOLD,
        )
        return self.categories[best[2]] if best else None

    def search(self, query: str | None = None, category_id: int | None = None) -> SearchResult:
        """Products matching ``query`` within ``category_id``, best matches first.

        Category words in the query (``violão``, ``guitarra``…) become a category filter. Every
        remaining word must match the product (fuzzy, per token); if nothing matches that way,
        the whole query is compared fuzzily as a fallback.
        """
        tokens = query_tokens(query or "")
        if category_id is None:
            alias_ids = {CATEGORY_ALIASES[t] for t in tokens if t in CATEGORY_ALIASES}
            if len(alias_ids) == 1:
                category_id = alias_ids.pop()
        tokens = [t for t in tokens if t not in CATEGORY_ALIASES]
        category = self.categories.get(category_id) if category_id is not None else None
        pool = [
            p
            for p in self.products.values()
            if category_id is None or p["category_id"] == category_id
        ]
        return SearchResult(self._rank(pool, tokens), category)

    def _rank(self, pool: list[dict], tokens: list[str]) -> list[dict]:
        if not tokens:
            return sorted(pool, key=lambda p: p["price_brl"])
        strict = [p for p in pool if self._matches_all(p["product_id"], tokens)]
        if strict:
            return sorted(strict, key=lambda p: p["price_brl"])
        text = " ".join(tokens)
        scored = [
            (fuzz.token_set_ratio(text, " ".join(self._search_tokens[p["product_id"]])), p)
            for p in pool
        ]
        scored.sort(key=lambda s: -s[0])
        return [p for score, p in scored if score >= FUZZY_QUERY_THRESHOLD]

    def match_name(self, name: str) -> NameMatch:
        """Resolve a product the customer named (fuzzy, accent-insensitive)."""
        key = match_key(name)
        if not key:
            return NameMatch(None, [])
        if key in self._name_keys.values():
            pid = next(pid for pid, k in self._name_keys.items() if k == key)
            return NameMatch(self.products[pid], [])
        # Every typed word found in the name (prefixes allowed: "strato" -> "stratocaster").
        # Category words ("violão yamaha c40") rarely appear in names, so they are optional.
        words = [w for w in key.split() if w not in CATEGORY_ALIASES] or key.split()
        by_words = [
            pid
            for pid, name_key in self._name_keys.items()
            if all(_token_matches(w, set(name_key.split())) for w in words)
        ]
        if len(by_words) == 1:
            return NameMatch(self.products[by_words[0]], [])
        # Spacing differences: "gd 20" vs "GD20".
        compact = "".join(words)
        by_compact = [
            pid for pid, name_key in self._name_keys.items() if compact in name_key.replace(" ", "")
        ]
        if len(by_compact) == 1:
            return NameMatch(self.products[by_compact[0]], [])
        scored = process.extract(
            key,
            self._name_keys,
            scorer=fuzz.token_set_ratio,
            limit=6,
            score_cutoff=NAME_CANDIDATE_THRESHOLD,
        )
        if not scored:
            return NameMatch(None, [])
        # Prefer names that contain every word typed (e.g. "gd20" beats "gc1ce").
        scored.sort(key=lambda s: (-s[1], -fuzz.ratio(key, s[0])))
        _, best_score, best_id = scored[0]
        runner_up = scored[1][1] if len(scored) > 1 else 0
        if best_score >= NAME_MATCH_THRESHOLD and best_score - runner_up >= NAME_UNAMBIGUOUS_GAP:
            return NameMatch(self.products[best_id], [])
        return NameMatch(None, [self.products[s[2]] for s in scored])

    def _matches_all(self, product_id: int, tokens: list[str]) -> bool:
        product_tokens = self._search_tokens[product_id]
        return all(_token_matches(token, product_tokens) for token in tokens)

    # ------------------------------------------------------------------ orders (BR-08)

    def verified_order(self, order_id: int, contact: str) -> tuple[dict, dict] | None:
        """(order, customer) when ``contact`` belongs to the order's customer; else None.

        Callers must answer "not found" in both cases, without revealing which one happened.
        """
        order = self.orders.get(order_id)
        if order is None:
            return None
        customer = self._customers[order["customer_id"]]
        if not contact_matches(contact, customer["email"], customer["phone"]):
            return None
        return order, customer

    def orders_by_phone(self, phone: str) -> tuple[str | None, list[dict]]:
        """(first name, orders newest first) of the customer(s) with this phone number."""
        number = normalize_phone(phone)
        if number is None:
            return None, []
        customers = [c for c in self._customers.values() if normalize_phone(c["phone"]) == number]
        ids = {c["customer_id"] for c in customers}
        orders = [o for o in self.orders.values() if o["customer_id"] in ids]
        first_name = customers[0]["first_name"] if len(customers) == 1 else None
        return first_name, sorted(orders, key=lambda o: o["order_date"], reverse=True)

    def items_of(self, order_id: int) -> list[dict]:
        return list(self._items.get(order_id, []))


@lru_cache(maxsize=1)
def get_repository() -> Repository:
    return Repository()


def _load(path: Path) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(
            f"{path} não existe. Rode `uv run python -m emporio.data.prepare` primeiro."
        )
    rows = json.loads(path.read_text(encoding="utf-8"))
    for row in rows:
        for key in _DATE_FIELDS & row.keys():
            if row[key]:
                row[key] = date.fromisoformat(row[key])
    return rows


def _search_tokens(product: dict) -> set[str]:
    words = [product["name"], product["category_name"], *product["specs"].values()]
    for (spec, value), terms in _SPEC_TERMS.items():
        if product["specs"].get(spec) == value:
            words.append(terms)
    return set(match_key(" ".join(words)).split()) - {"yes", "no"}


def _token_matches(token: str, product_tokens: set[str]) -> bool:
    if token in product_tokens:
        return True
    if token.isdigit():  # "7" must not fuzzy-match "70"
        return False
    return any(
        fuzz.ratio(token, candidate) >= FUZZY_TOKEN_THRESHOLD
        or (len(token) >= 4 and candidate.startswith(token))
        for candidate in product_tokens
    )
