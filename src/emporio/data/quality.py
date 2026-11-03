"""Data quality checks: each one returns findings for the report (SPEC §16).

A finding never changes the data; it records what was seen and the treatment adopted.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date

from emporio.formatting import brl, date_br
from emporio.rules.orders import is_late, is_stale
from emporio.rules.pricing import PIX_DISCOUNT_PERCENT, apply_discount
from emporio.text import normalize

SHIPPED_STATUSES = frozenset({"shipped", "delivered"})
TRACKING_PATTERN = re.compile(r"^BR[A-Z0-9]{9}BR$")  # manual §5.3
PHONE_DIGITS_PATTERN = re.compile(r"^\d{2}9\d{8}$")  # DDD + mobile number
EMAIL_PATTERN = re.compile(r"^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$")
# Instrument brands and the model names that identify them. Pickup/electronics brands
# (LR Baggs, Fishman…) are intentionally absent: citing them does not change the maker.
BRAND_ALIASES: dict[str, tuple[str, ...]] = {
    "Crafter": ("crafter",),
    "Fender": ("fender", "stratocaster", "telecaster", "jazz bass", "precision"),
    "Giannini": ("giannini",),
    "Gibson": ("gibson", "les paul"),
    "Ibanez": ("ibanez",),
    "Kala": ("kala",),
    "Kalani": ("kalani",),
    "Korg": ("korg",),
    "Martin": ("martin",),
    "Music Man": ("music man", "stingray"),
    "Nord": ("nord",),
    "Ohana": ("ohana",),
    "Pearl": ("pearl",),
    "PRS": ("prs",),
    "Roland": ("roland",),
    "Rozini": ("rozini",),
    "Shelby": ("shelby",),
    "Tagima": ("tagima",),
    "Takamine": ("takamine",),
    "Tama": ("tama",),
    "Taylor": ("taylor",),
    "Yamaha": ("yamaha", "stage custom"),
}
# Woods checked against ``specs.body`` (electric) and ``specs.shells`` (drums).
WOOD_ALIASES: dict[str, tuple[str, ...]] = {
    "Alder": ("alder",),
    "Ash": ("ash",),
    "Basswood": ("basswood",),
    "Birch": ("birch",),
    "Mahogany": ("mahogany", "mogno"),
    "Maple": ("maple",),
    "Poplar": ("poplar",),
    "Walnut": ("walnut",),
}
SPECS_SCHEMAS: dict[str, frozenset[str]] = {
    "acustico": frozenset(
        {"top", "back_sides", "neck", "strings", "scale", "electronics", "color"}
    ),
    "eletrico": frozenset({"body", "neck", "strings", "color"}),
    "bateria": frozenset({"shells", "pieces", "hardware", "color"}),
    "teclado": frozenset({"keys", "type", "polyphony", "color"}),
}
NAME_COLORS = {"natural": "natural", "sunburst": "sunburst", "preto": "preto"}

SECTIONS = {
    "products": "Products",
    "categories": "Categories",
    "promotions": "Promotions",
    "orders": "Orders",
    "customers": "Customers",
    "manual": "Policy manual (PDF)",
}


@dataclass(frozen=True)
class Finding:
    code: str
    section: str
    severity: str  # "conflict" | "rule" | "warning" | "info"
    title: str
    detail: str
    treatment: str
    ids: tuple[int, ...] = ()
    related: tuple[int, ...] = ()
    table_header: tuple[str, ...] = ()
    table_rows: tuple[tuple[str, ...], ...] = ()


def mentions(text: str, aliases: dict[str, tuple[str, ...]]) -> list[str]:
    """Canonical names whose aliases appear in ``text``, ordered by first position."""
    normalized = normalize(text)
    positions: dict[str, int] = {}
    for canonical, words in aliases.items():
        for word in words:
            match = re.search(rf"\b{re.escape(word)}\b", normalized)
            if match:
                positions[canonical] = min(positions.get(canonical, match.start()), match.start())
    return sorted(positions, key=positions.__getitem__)


def _counts(text: str, unit: str) -> set[int]:
    return {int(n) for n in re.findall(rf"\b(\d+)\s*{unit}\b", normalize(text))}


def description_conflicts(name: str, description: str, specs: dict[str, str]) -> list[str]:
    """Reasons why a product description contradicts its name or specs (BR-16)."""
    reasons: list[str] = []

    name_brands = set(mentions(name, BRAND_ALIASES))
    foreign = [b for b in mentions(description, BRAND_ALIASES) if b not in name_brands]
    if foreign and name_brands:
        reasons.append(
            f"description cites {', '.join(foreign)}; the name is {', '.join(sorted(name_brands))}"
        )

    # Units are matched in Portuguese (the data's language) and reported in English.
    for unit, spec_key, label in (("cordas", "strings", "strings"), ("teclas", "keys", "keys")):
        described = _counts(description, unit)
        reference = _counts(name, unit) or (
            {int(specs[spec_key])} if specs.get(spec_key, "").isdigit() else set()
        )
        if described and reference and described != reference:
            reasons.append(
                f"description says {join_ints(described)} {label}; "
                f"name/specs say {join_ints(reference)}"
            )

    for spec_key in ("body", "shells"):
        if spec_key not in specs:
            continue
        described = set(mentions(description, WOOD_ALIASES))
        declared = set(mentions(specs[spec_key], WOOD_ALIASES))
        if described - declared:
            reasons.append(
                f"description cites {'/'.join(sorted(described))}; "
                f"specs.{spec_key} = {specs[spec_key]}"
            )
    return reasons


def name_specs_mismatches(name: str, specs: dict[str, str]) -> list[str]:
    """Attributes stated in the product name that disagree with its specs."""
    normalized = normalize(name)
    words = set(normalized.split())
    reasons: list[str] = []
    strings = specs.get("strings", "")
    if "nylon" in words and strings and strings != "nylon":
        reasons.append(f"name says nylon; specs.strings = {strings}")
    if "aco" in words and strings and strings != "steel":
        reasons.append(f"name says steel (aço); specs.strings = {strings}")
    if "eletrico" in words and specs.get("electronics", "yes") != "yes":
        reasons.append("name says electric (elétrico); specs.electronics = no")
    for word, color in NAME_COLORS.items():
        if word in words and "color" in specs and normalize(specs["color"]) != color:
            reasons.append(f"name says {word}; specs.color = {specs['color']}")
    return reasons


def specs_schema(specs: dict[str, str]) -> str:
    keys = frozenset(specs)
    return next((name for name, schema in SPECS_SCHEMAS.items() if schema == keys), "outro")


def join_ints(values: Iterable[int]) -> str:
    return ", ".join(str(v) for v in sorted(values))


# --------------------------------------------------------------------------- checks


@dataclass
class CheckContext:
    tables: dict[str, list[dict]]
    policy_text: str
    as_of: date

    def by_id(self, table: str, key: str) -> dict[int, dict]:
        return {row[key]: row for row in self.tables[table]}


Check = Callable[[CheckContext], list[Finding]]


def _ids(rows: Iterable[dict], key: str) -> tuple[int, ...]:
    return tuple(sorted(row[key] for row in rows))


def check_description_conflicts(ctx: CheckContext) -> list[Finding]:
    conflicted = [p for p in ctx.tables["products"] if p["description_conflict"]]
    if not conflicted:
        return []
    return [
        Finding(
            code="product.description_conflict",
            section="products",
            severity="conflict",
            title="Description contradicts the name or the specs",
            detail=(
                "The description mentions another brand/model, another number of strings or "
                "keys, or another wood."
            ),
            treatment=(
                "Flag `description_conflict = true`. The description is **not** shown to the "
                "customer; the agent uses name + specs + price (BR-16)."
            ),
            ids=_ids(conflicted, "product_id"),
            table_header=("id", "product", "mismatch"),
            table_rows=tuple(
                (str(p["product_id"]), p["name"], "; ".join(p["description_conflict_reasons"]))
                for p in sorted(conflicted, key=lambda p: p["product_id"])
            ),
        )
    ]


def check_name_specs(ctx: CheckContext) -> list[Finding]:
    rows = []
    for product in ctx.tables["products"]:
        reasons = name_specs_mismatches(product["name"], product["specs"])
        if reasons:
            rows.append((str(product["product_id"]), product["name"], "; ".join(reasons)))
    if not rows:
        return []
    return [
        Finding(
            code="product.name_specs_mismatch",
            section="products",
            severity="conflict",
            title="Name contradicts the specs",
            detail="The product name states an attribute that differs from the specs.",
            treatment="Specs prevail; the record is kept as is.",
            ids=tuple(int(r[0]) for r in rows),
            table_header=("id", "product", "mismatch"),
            table_rows=tuple(rows),
        )
    ]


def check_product_status(ctx: CheckContext) -> list[Finding]:
    products = ctx.tables["products"]
    findings = []
    discontinued = [p for p in products if p["status"] == "discontinued"]
    if discontinued:
        findings.append(
            Finding(
                code="product.discontinued",
                section="products",
                severity="warning",
                title="Discontinued products",
                detail="; ".join(
                    f"{p['product_id']} {p['name']} (stock {p['stock_quantity']})"
                    for p in discontinued
                ),
                treatment=(
                    "Never offered, even with stock; the agent says they left the catalog and "
                    "suggests equivalents (BR-01, BR-02; manual §7.3)."
                ),
                ids=_ids(discontinued, "product_id"),
            )
        )

    out_of_stock = [p for p in products if p["status"] == "active" and p["stock_quantity"] == 0]
    if out_of_stock:
        findings.append(
            Finding(
                code="product.active_out_of_stock",
                section="products",
                severity="warning",
                title="Active products with no stock",
                detail="; ".join(f"{p['product_id']} {p['name']}" for p in out_of_stock),
                treatment="Unavailable for now + up to 3 sellable alternatives (BR-01, BR-02).",
                ids=_ids(out_of_stock, "product_id"),
            )
        )

    coming_soon = [p for p in products if p["status"] == "coming_soon"]
    if coming_soon:
        coming_ids = {p["product_id"] for p in coming_soon}
        orders = ctx.by_id("orders", "order_id")
        related = sorted(
            {i["order_id"] for i in ctx.tables["order_items"] if i["product_id"] in coming_ids}
        )
        orders_text = (
            "; orders that already include it: "
            + ", ".join(f"{oid} ({orders[oid]['status']})" for oid in related)
            if related
            else ""
        )
        findings.append(
            Finding(
                code="product.coming_soon",
                section="products",
                severity="conflict" if related else "warning",
                title="Upcoming releases (`coming_soon`)",
                detail="; ".join(
                    f"{p['product_id']} {p['name']} (stock {p['stock_quantity']})"
                    for p in coming_soon
                )
                + orders_text
                + ". A product not yet released should not have orders.",
                treatment=(
                    "Presented as coming soon, not for sale and with no promised date (BR-01). "
                    "Existing orders can still be looked up normally."
                ),
                ids=_ids(coming_soon, "product_id"),
                related=tuple(related),
            )
        )
    return findings


def check_specs_schemas(ctx: CheckContext) -> list[Finding]:
    by_schema: dict[str, set[str]] = defaultdict(set)
    ids_by_schema: dict[str, list[int]] = defaultdict(list)
    for p in ctx.tables["products"]:
        by_schema[p["specs_schema"]].add(p["category_name"])
        ids_by_schema[p["specs_schema"]].append(p["product_id"])
    rows = tuple(
        (
            schema,
            ", ".join(sorted(by_schema[schema])),
            ", ".join(sorted(SPECS_SCHEMAS.get(schema, ()))) or "—",
            str(len(ids_by_schema[schema])),
        )
        for schema in sorted(by_schema)
    )
    unknown = tuple(sorted(ids_by_schema.get("outro", [])))
    return [
        Finding(
            code="product.specs_schemas",
            section="products",
            severity="conflict" if unknown else "info",
            title="Specs follow different schemas per instrument type",
            detail=(
                "The `specs` JSON has no single schema: acoustic guitars/ukuleles, "
                "electric guitars/basses, drums and keyboards use different keys."
            ),
            treatment=(
                "Specs are parsed into a dict and get a `specs_schema` field; the summary shown "
                "to the customer is built per schema."
            ),
            ids=unknown,
            table_header=("schema", "categories", "keys", "products"),
            table_rows=rows,
        )
    ]


def check_specs_serialization(ctx: CheckContext) -> list[Finding]:
    """Products whose specs JSON was serialized differently (a hint of another source)."""
    odd = [p for p in ctx.tables["products"] if '": "' in p["_specs_raw"]]
    if not odd or len(odd) == len(ctx.tables["products"]):
        return []
    conflicted = {p["product_id"] for p in ctx.tables["products"] if p["description_conflict"]}
    odd_ids = _ids(odd, "product_id")
    overlap = len(conflicted & set(odd_ids))
    return [
        Finding(
            code="product.different_origin",
            section="products",
            severity="info",
            title="A batch of products from another source",
            detail=(
                f"{len(odd_ids)} products have their specs JSON serialized differently "
                f'(`": "` with a space). {overlap} of the {len(conflicted)} products with a '
                "conflicting description are in this batch, whose names are also generic "
                '("Bass 1X", "Kit 1 Studio", "Synth 1 Pro").'
            ),
            treatment=(
                "No correction; it reinforces the decision not to trust these descriptions."
            ),
            ids=odd_ids,
        )
    ]


def check_empty_categories(ctx: CheckContext) -> list[Finding]:
    used = {p["category_id"] for p in ctx.tables["products"]}
    empty = [c for c in ctx.tables["categories"] if c["category_id"] not in used]
    if not empty:
        return []
    policy = normalize(ctx.policy_text)
    promised_terms = {
        "instrumentos de sopro": "wind instruments",
        "cordas orquestrais": "orchestral strings",
    }
    promised = [english for term, english in promised_terms.items() if term in policy]
    manual_note = (
        f" The manual (§1) says the store sells {' and '.join(promised)}." if promised else ""
    )
    return [
        Finding(
            code="category.empty",
            section="categories",
            severity="warning",
            title="Categories without any product",
            detail="; ".join(f"{c['category_id']} {c['name']}" for c in empty) + "." + manual_note,
            treatment=(
                "The system prevails: the agent never claims to have items in these categories "
                "and says there are none for now (BR-13, DD-§6)."
            ),
            ids=_ids(empty, "category_id"),
        )
    ]


def check_promotions(ctx: CheckContext) -> list[Finding]:
    products = ctx.by_id("products", "product_id")
    promotions = ctx.tables["promotions"]
    findings = []

    unknown = [p for p in promotions if p["product_id"] not in products]
    if unknown:
        findings.append(
            Finding(
                code="promotion.unknown_product",
                section="promotions",
                severity="conflict",
                title="Promotions for products that do not exist",
                detail="Promotions pointing to a `product_id` outside the catalog.",
                treatment="Ignored.",
                ids=_ids(unknown, "promotion_id"),
            )
        )

    active = [p for p in promotions if p["is_active"]]
    findings.append(
        Finding(
            code="promotion.active",
            section="promotions",
            severity="info",
            title=f"Only {len(active)} of {len(promotions)} promotions are active",
            detail="No promotion has a start/end date; `is_active` is the only signal.",
            treatment=(
                "Only promotions with `is_active = 1` exist for the agent; inactive ones never "
                "reach the LLM (BR-03)."
            ),
            ids=_ids(active, "promotion_id"),
            related=tuple(sorted({p["product_id"] for p in active})),
            table_header=("promotion", "product", "discount", "price", "final price"),
            table_rows=tuple(
                (
                    str(p["promotion_id"]),
                    f"{p['product_id']} {products[p['product_id']]['name']}",
                    f"{p['discount_percent']:g}%",
                    brl(products[p["product_id"]]["price_brl"]),
                    brl(
                        apply_discount(
                            products[p["product_id"]]["price_brl"], p["discount_percent"]
                        )
                    ),
                )
                for p in sorted(active, key=lambda p: p["promotion_id"])
                if p["product_id"] in products
            ),
        )
    )

    by_product: dict[int, list[dict]] = defaultdict(list)
    for promo in promotions:
        by_product[promo["product_id"]].append(promo)
    multiple = {pid: promos for pid, promos in by_product.items() if len(promos) > 1}
    if multiple:
        any_active = any(p["is_active"] for promos in multiple.values() for p in promos)
        findings.append(
            Finding(
                code="promotion.multiple_per_product",
                section="promotions",
                severity="warning",
                title="Products with more than one promotion",
                detail="; ".join(
                    f"product {pid}: "
                    + ", ".join(
                        f"#{p['promotion_id']} {p['discount_percent']:g}%"
                        f"{' (active)' if p['is_active'] else ''}"
                        for p in promos
                    )
                    for pid, promos in sorted(multiple.items())
                )
                + ("." if any_active else ". All inactive."),
                treatment="If more than one is active, the largest discount applies (BR-03).",
                ids=tuple(sorted(multiple)),
            )
        )

    unsellable = [
        p
        for p in active
        if p["product_id"] in products
        and (
            products[p["product_id"]]["status"] != "active"
            or products[p["product_id"]]["stock_quantity"] == 0
        )
    ]
    if unsellable:
        findings.append(
            Finding(
                code="promotion.active_on_unavailable",
                section="promotions",
                severity="warning",
                title="Active promotion on an unavailable product",
                detail="Active promotions for products that cannot be sold.",
                treatment="The product is not offered; the promotion is not announced (BR-01).",
                ids=_ids(unsellable, "promotion_id"),
            )
        )
    return findings


def _order_rows(orders: Iterable[dict], *extra: Callable[[dict], str]) -> tuple:
    return tuple(
        (
            str(o["order_id"]),
            date_br(o["order_date"]),
            o["status"],
            o["payment_method"],
            brl(o["total_brl"]),
            *(fn(o) for fn in extra),
        )
        for o in sorted(orders, key=lambda o: o["order_id"])
    )


_ORDER_HEADER = ("order", "date", "status", "payment", "total")


def check_order_totals(ctx: CheckContext) -> list[Finding]:
    orders = ctx.tables["orders"]
    findings = []
    mismatched = [o for o in orders if o["total_mismatch"]]
    if mismatched:
        findings.append(
            Finding(
                code="order.total_mismatch",
                section="orders",
                severity="conflict",
                title="Order total differs from the sum of its items",
                detail=(
                    "The sum uses the **current** catalog price (there is no historical price "
                    "per item), so it is a hint, not proof."
                ),
                treatment="The recorded `total_brl` prevails: it is what the customer paid "
                "(BR-16).",
                ids=_ids(mismatched, "order_id"),
                table_header=(*_ORDER_HEADER, "sum of items"),
                table_rows=_order_rows(mismatched, lambda o: brl(o["items_total_brl"])),
            )
        )

    pix = [o for o in orders if o["pix_without_discount"]]
    if pix:
        findings.append(
            Finding(
                code="order.pix_without_discount",
                section="orders",
                severity="rule",
                title="PIX payment without the 5% discount",
                detail=(
                    "The total equals the items' list price, without the 5% PIX discount "
                    "(manual §3), and no item has an active promotion that would justify it."
                ),
                treatment=(
                    "History is not recalculated. For new purchases, the agent applies the PIX "
                    "discount correctly (BR-04)."
                ),
                ids=_ids(pix, "order_id"),
                table_header=(*_ORDER_HEADER, "with PIX it would be"),
                table_rows=_order_rows(
                    pix,
                    lambda o: brl(apply_discount(o["items_total_brl"], PIX_DISCOUNT_PERCENT)),
                ),
            )
        )

    below = [o for o in orders if o["installment_below_minimum"]]
    if below:
        findings.append(
            Finding(
                code="order.installment_below_minimum",
                section="orders",
                severity="rule",
                title="Installment below the minimum of manual §3.1",
                detail="Tiers: up to 3x, R$ 50,00; 4–6x, R$ 80,00; 7–12x, R$ 100,00.",
                treatment=(
                    "History is not recalculated. For new purchases, the tool only offers the "
                    "allowed installments (BR-05)."
                ),
                ids=_ids(below, "order_id"),
                table_header=(*_ORDER_HEADER, "installment"),
                table_rows=_order_rows(
                    below,
                    lambda o: f"{o['installments']}x of {brl(o['total_brl'] / o['installments'])}",
                ),
            )
        )
    return findings


def check_order_integrity(ctx: CheckContext) -> list[Finding]:
    orders = ctx.tables["orders"]
    order_ids = {o["order_id"] for o in orders}
    with_items = {i["order_id"] for i in ctx.tables["order_items"]}
    problems: list[tuple[str, str]] = []
    for oid in sorted(with_items - order_ids):
        problems.append((str(oid), "items of an order that does not exist"))
    for order in sorted(orders, key=lambda o: o["order_id"]):
        oid = str(order["order_id"])
        if order["order_id"] not in with_items:
            problems.append((oid, "order without items"))
        tracking = order["tracking_code"]
        if order["status"] in SHIPPED_STATUSES:
            if not tracking:
                problems.append((oid, "shipped/delivered without a tracking code"))
            elif not TRACKING_PATTERN.match(tracking):
                problems.append((oid, f"tracking code {tracking} outside the manual §5.3 format"))
            if order["estimated_delivery"] is None:
                problems.append((oid, "shipped/delivered without an estimated delivery"))
        elif tracking:
            problems.append((oid, f"status {order['status']} with a tracking code"))
        delivery = order["estimated_delivery"]
        if delivery is not None and delivery < order["order_date"]:
            problems.append((oid, "estimated delivery before the order date"))
        if order["status"] == "cancelled" and not order["notes"]:
            problems.append((oid, "cancelled without a reason in `notes`"))
    if not problems:
        return []
    return [
        Finding(
            code="order.integrity",
            section="orders",
            severity="conflict",
            title="Structural inconsistencies in orders",
            detail="Orders whose fields do not agree with each other.",
            treatment="Record kept; the agent shows only the fields that are present.",
            ids=tuple(sorted({int(p[0]) for p in problems})),
            table_header=("order", "problem"),
            table_rows=tuple(problems),
        )
    ]


def check_order_timeliness(ctx: CheckContext) -> list[Finding]:
    orders = ctx.tables["orders"]
    today = ctx.as_of
    findings = []
    late = [o for o in orders if is_late(o["status"], o["estimated_delivery"], today)]
    if late:
        findings.append(
            Finding(
                code="order.late",
                section="orders",
                severity="warning",
                title=f"Late orders on {date_br(today)}",
                detail="Status `shipped`/`confirmed` with the estimated delivery already past.",
                treatment="The tool flags `is_late` and recommends a human hand-off (BR-09).",
                ids=_ids(late, "order_id"),
                table_header=(*_ORDER_HEADER, "estimated delivery"),
                table_rows=_order_rows(late, lambda o: date_br(o["estimated_delivery"])),
            )
        )
    stale = [o for o in orders if is_stale(o["status"], o["order_date"], today)]
    if stale:
        findings.append(
            Finding(
                code="order.stale",
                section="orders",
                severity="warning",
                title=f"Stalled orders on {date_br(today)}",
                detail="Status `pending`/`confirmed` for more than 7 days without shipping.",
                treatment="The tool flags `is_stale` and recommends a human hand-off (BR-09).",
                ids=_ids(stale, "order_id"),
                table_header=(*_ORDER_HEADER, "days stalled"),
                table_rows=_order_rows(stale, lambda o: str((today - o["order_date"]).days)),
            )
        )
    return findings


def check_customers(ctx: CheckContext) -> list[Finding]:
    customers = ctx.tables["customers"]
    findings = []
    for key, label, code in (
        ("email_normalized", "e-mail", "customer.duplicate_email"),
        ("phone_digits", "phone", "customer.duplicate_phone"),
    ):
        groups: dict[str, list[dict]] = defaultdict(list)
        for customer in customers:
            groups[customer[key]].append(customer)
        duplicated = {value: group for value, group in groups.items() if len(group) > 1}
        if duplicated:
            findings.append(
                Finding(
                    code=code,
                    section="customers",
                    severity="conflict",
                    title=f"{label.capitalize()} shared by more than one customer",
                    detail="; ".join(
                        f"`{value}`: customers {join_ints(c['customer_id'] for c in group)}"
                        for value, group in sorted(duplicated.items())
                    ),
                    treatment=(
                        "The contact alone does not identify the customer: `get_order` requires "
                        "order number + contact, and `list_my_orders` looks up by phone only "
                        "(BR-08)."
                    ),
                    ids=tuple(sorted(c["customer_id"] for g in duplicated.values() for c in g)),
                )
            )

    invalid = [
        (str(c["customer_id"]), problem)
        for c in customers
        for problem in (
            None if PHONE_DIGITS_PATTERN.match(c["phone_digits"]) else f"phone {c['phone']}",
            None if EMAIL_PATTERN.match(c["email_normalized"]) else f"e-mail {c['email']}",
        )
        if problem
    ]
    if invalid:
        findings.append(
            Finding(
                code="customer.invalid_contact",
                section="customers",
                severity="conflict",
                title="Contacts in an invalid format",
                detail="Phone or e-mail outside the expected format.",
                treatment="Record kept; that contact cannot be used for verification (BR-08).",
                ids=tuple(sorted({int(i[0]) for i in invalid})),
                table_header=("customer", "problem"),
                table_rows=tuple(invalid),
            )
        )
    return findings


_PHONE_IN_TEXT = re.compile(r"\(\d{2}\)\s*\d{4,5}\s*-\s*\d{4}")
_EMAIL_IN_TEXT = re.compile(r"[\w.+-]+@[\w.-]+\.\w+")
_MIN_INSTALLMENT_IN_TEXT = re.compile(r"(?:parcela minima de|exceto abaixo de) r\$ ?([\d.]+,\d{2})")


def check_manual(ctx: CheckContext) -> list[Finding]:
    text = ctx.policy_text
    normalized = normalize(text)
    findings = []

    phones = sorted(
        {re.sub(r"\s+", "", p).replace(")", ") ") for p in _PHONE_IN_TEXT.findall(text)}
    )
    if len(phones) > 1:
        findings.append(
            Finding(
                code="manual.multiple_whatsapp",
                section="manual",
                severity="conflict",
                title="Two numbers presented as WhatsApp",
                detail=(
                    f'Numbers in the manual: {", ".join(phones)}. §1.2 lists "Telefone / '
                    'WhatsApp" and §7 gives another number as the WhatsApp channel.'
                ),
                treatment=(
                    "WhatsApp = (67) 3321-4500 (the channel's own section); phone = "
                    "(67) 3341-4444 (BR-12, DD-§6)."
                ),
            )
        )

    emails = sorted(set(_EMAIL_IN_TEXT.findall(text)))
    accented = [e for e in emails if not e.isascii()]
    if accented:
        findings.append(
            Finding(
                code="manual.email_with_accent",
                section="manual",
                severity="conflict",
                title="E-mail with an accent in the domain",
                detail=f"E-mails in the manual: {', '.join(f'`{e}`' for e in emails)}.",
                treatment=(
                    "Uses `contato@emporiodamusica.com.br` (cover page); the accent is treated as "
                    "a typo (BR-12, DD-§6)."
                ),
            )
        )

    minimums = sorted(
        set(_MIN_INSTALLMENT_IN_TEXT.findall(normalized)),
        key=lambda m: float(m.replace(".", "").replace(",", ".")),
    )
    if len(minimums) > 1:
        cited = ", ".join(f"R$ {m}" for m in minimums)
        findings.append(
            Finding(
                code="manual.installment_conflict",
                section="manual",
                severity="conflict",
                title="Contradictory minimum installment between §3 and §3.1",
                detail=(
                    'The §3 table says "parcela mínima de R$ 100,00" (minimum installment) for '
                    f"all credit purchases; §3.1 defines tiers. Minimums cited: {cited}."
                ),
                treatment=(
                    "§3.1 prevails (more specific): 1–3x R$ 50,00 · 4–6x R$ 80,00 · "
                    "7–12x R$ 100,00 (BR-05, DD-§6)."
                ),
            )
        )

    claimed = re.search(r"mais de (\d+) instrumentos", normalized)
    actual = len(ctx.tables["products"])
    if claimed and int(claimed.group(1)) > actual:
        findings.append(
            Finding(
                code="manual.catalog_size",
                section="manual",
                severity="conflict",
                title="Catalog size",
                detail=(
                    f'The manual (§1) says "mais de {claimed.group(1)} instrumentos" (more than '
                    f"{claimed.group(1)} instruments); the system has {actual} products."
                ),
                treatment="The system prevails; the agent never claims to have what is not in it.",
            )
        )

    if "acima de r$ 500,00" in normalized and "abaixo de r$ 500,00" in normalized:
        findings.append(
            Finding(
                code="manual.shipping_threshold_gap",
                section="manual",
                severity="warning",
                title="Shipping for exactly R$ 500,00 is not covered",
                detail=(
                    '§5.1: free "acima de R$ 500,00" (above) and R$ 35,00 "abaixo de R$ 500,00" '
                    "(below)."
                ),
                treatment="Up to R$ 500,00 inclusive pays R$ 35,00; free only above (BR-07).",
            )
        )

    active = sum(1 for p in ctx.tables["promotions"] if p["is_active"])
    if "black friday (novembro)" in normalized:
        findings.append(
            Finding(
                code="manual.black_friday",
                section="manual",
                severity="warning",
                title="Black Friday announced for November × promotions in the system",
                detail=(
                    "The manual (§6.1) promises a November Black Friday with 15% to 30% off "
                    f'"em todo o catálogo" (the whole catalog); the system has only {active} '
                    "active promotions, and every Black Friday one is inactive."
                ),
                treatment=(
                    "The system prevails: only promotions with `is_active = 1` "
                    "(manual §7.3, DD-§6)."
                ),
            )
        )
    return findings


CHECKS: tuple[tuple[str, str, Check], ...] = (
    (
        "products",
        "Description × name/specs (brand, strings, keys, wood)",
        check_description_conflicts,
    ),
    ("products", "Name × specs (nylon/steel, electric, color)", check_name_specs),
    ("products", "Status and stock", check_product_status),
    ("products", "Specs schemas", check_specs_schemas),
    ("products", "Record origin", check_specs_serialization),
    ("categories", "Categories with products", check_empty_categories),
    ("promotions", "Promotions", check_promotions),
    ("orders", "Totals, PIX and installments", check_order_totals),
    (
        "orders",
        "Integrity (items, manual §5.3 tracking format, dates, cancellation reason)",
        check_order_integrity,
    ),
    ("orders", "Late and stalled orders", check_order_timeliness),
    ("customers", "Duplicate contacts and phone/e-mail format", check_customers),
    ("manual", "Internal contradictions in the manual", check_manual),
)


def run_checks(
    tables: dict[str, list[dict]], policy_text: str, as_of: date
) -> tuple[list[Finding], list[tuple[str, str]]]:
    """Run every check; returns (findings, passed checks as (section, label))."""
    ctx = CheckContext(tables=tables, policy_text=policy_text, as_of=as_of)
    findings: list[Finding] = []
    passed: list[tuple[str, str]] = []
    for section, label, check in CHECKS:
        found = check(ctx)
        if found:
            findings.extend(found)
        else:
            passed.append((section, label))
    return findings, passed
