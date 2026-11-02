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
    "products": "Produtos",
    "categories": "Categorias",
    "promotions": "Promoções",
    "orders": "Pedidos",
    "customers": "Clientes",
    "manual": "Manual de políticas (PDF)",
}


@dataclass(frozen=True)
class Finding:
    code: str
    section: str
    severity: str  # "conflito" | "regra" | "atenção" | "info"
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
            f"descrição cita {', '.join(foreign)}; o nome é {', '.join(sorted(name_brands))}"
        )

    for unit, spec_key in (("cordas", "strings"), ("teclas", "keys")):
        described = _counts(description, unit)
        reference = _counts(name, unit) or (
            {int(specs[spec_key])} if specs.get(spec_key, "").isdigit() else set()
        )
        if described and reference and described != reference:
            reasons.append(
                f"descrição diz {join_ints(described)} {unit}; "
                f"nome/specs dizem {join_ints(reference)}"
            )

    for spec_key in ("body", "shells"):
        if spec_key not in specs:
            continue
        described = set(mentions(description, WOOD_ALIASES))
        declared = set(mentions(specs[spec_key], WOOD_ALIASES))
        if described - declared:
            reasons.append(
                f"descrição cita {'/'.join(sorted(described))}; "
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
        reasons.append(f"nome diz nylon; specs.strings = {strings}")
    if "aco" in words and strings and strings != "steel":
        reasons.append(f"nome diz aço; specs.strings = {strings}")
    if "eletrico" in words and specs.get("electronics", "yes") != "yes":
        reasons.append("nome diz elétrico; specs.electronics = no")
    for word, color in NAME_COLORS.items():
        if word in words and "color" in specs and normalize(specs["color"]) != color:
            reasons.append(f"nome diz {word}; specs.color = {specs['color']}")
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
            severity="conflito",
            title="Descrição contradiz o nome ou as specs",
            detail=(
                "A descrição fala de outra marca/modelo, de outra quantidade de cordas ou "
                "teclas, ou de outra madeira."
            ),
            treatment=(
                "Flag `description_conflict = true`. A descrição **não** é exposta ao cliente; "
                "o agente usa nome + specs + preço (BR-16)."
            ),
            ids=_ids(conflicted, "product_id"),
            table_header=("id", "produto", "divergência"),
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
            severity="conflito",
            title="Nome contradiz as specs",
            detail="O nome do produto cita um atributo diferente do registrado nas specs.",
            treatment="Specs prevalecem; registro mantido como está.",
            ids=tuple(int(r[0]) for r in rows),
            table_header=("id", "produto", "divergência"),
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
                severity="atenção",
                title="Produtos descontinuados",
                detail="; ".join(
                    f"{p['product_id']} {p['name']} (estoque {p['stock_quantity']})"
                    for p in discontinued
                ),
                treatment=(
                    "Nunca são oferecidos, mesmo com estoque; o agente informa que saíram do "
                    "catálogo e sugere equivalentes (BR-01, BR-02; manual §7.3)."
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
                severity="atenção",
                title="Ativos sem estoque",
                detail="; ".join(f"{p['product_id']} {p['name']}" for p in out_of_stock),
                treatment="Indisponível no momento + até 3 alternativas ofertáveis (BR-01, BR-02).",
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
            "; pedidos que já o incluem: "
            + ", ".join(f"{oid} ({orders[oid]['status']})" for oid in related)
            if related
            else ""
        )
        findings.append(
            Finding(
                code="product.coming_soon",
                section="products",
                severity="conflito" if related else "atenção",
                title="Lançamentos (`coming_soon`)",
                detail="; ".join(
                    f"{p['product_id']} {p['name']} (estoque {p['stock_quantity']})"
                    for p in coming_soon
                )
                + orders_text
                + ". Um produto ainda não lançado não deveria ter pedidos.",
                treatment=(
                    "Apresentado como lançamento em breve, sem venda e sem prazo prometido "
                    "(BR-01). Os pedidos existentes continuam consultáveis normalmente."
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
            severity="conflito" if unknown else "info",
            title="Specs com esquemas diferentes por tipo de instrumento",
            detail=(
                "O JSON de `specs` não tem um esquema único: violões/ukuleles, "
                "guitarras/baixos, baterias e teclados usam chaves diferentes."
            ),
            treatment=(
                "Specs são parseadas como dicionário e ganham o campo `specs_schema`; "
                "o resumo exibido ao cliente é montado por esquema."
            ),
            ids=unknown,
            table_header=("esquema", "categorias", "chaves", "produtos"),
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
            title="Lote de produtos com outra origem",
            detail=(
                f"{len(odd_ids)} produtos têm o JSON de specs serializado em outro formato "
                f'(`": "` com espaço). {overlap} dos {len(conflicted)} produtos com descrição '
                "conflitante estão nesse lote, cujos nomes também são genéricos "
                '("Bass 1X", "Kit 1 Studio", "Synth 1 Pro").'
            ),
            treatment=(
                "Nenhuma correção; reforça a decisão de não confiar na descrição desses itens."
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
    promised = [term for term in ("instrumentos de sopro", "cordas orquestrais") if term in policy]
    manual_note = f" O manual (§1) diz que a loja vende {' e '.join(promised)}." if promised else ""
    return [
        Finding(
            code="category.empty",
            section="categories",
            severity="atenção",
            title="Categorias sem nenhum produto",
            detail="; ".join(f"{c['category_id']} {c['name']}" for c in empty) + "." + manual_note,
            treatment=(
                "Vale o sistema: o agente nunca afirma ter itens dessas categorias e diz que "
                "não há no momento (BR-13, DD-§6)."
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
                severity="conflito",
                title="Promoções de produtos inexistentes",
                detail="Promoções que apontam para um `product_id` fora do catálogo.",
                treatment="Ignoradas.",
                ids=_ids(unknown, "promotion_id"),
            )
        )

    active = [p for p in promotions if p["is_active"]]
    findings.append(
        Finding(
            code="promotion.active",
            section="promotions",
            severity="info",
            title=f"Só {len(active)} de {len(promotions)} promoções estão ativas",
            detail="Nenhuma promoção tem data de início/fim; `is_active` é o único sinal.",
            treatment=(
                "Só promoções com `is_active = 1` existem para o agente; as inativas nem chegam "
                "ao LLM (BR-03)."
            ),
            ids=_ids(active, "promotion_id"),
            related=tuple(sorted({p["product_id"] for p in active})),
            table_header=("promoção", "produto", "desconto", "preço", "preço final"),
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
                severity="atenção",
                title="Produtos com mais de uma promoção",
                detail="; ".join(
                    f"produto {pid}: "
                    + ", ".join(
                        f"#{p['promotion_id']} {p['discount_percent']:g}%"
                        f"{' (ativa)' if p['is_active'] else ''}"
                        for p in promos
                    )
                    for pid, promos in sorted(multiple.items())
                )
                + ("." if any_active else ". Todas inativas."),
                treatment="Se houver mais de uma ativa, vale o maior desconto (BR-03).",
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
                severity="atenção",
                title="Promoção ativa em produto indisponível",
                detail="Promoções ativas de produtos não ofertáveis.",
                treatment="O produto não é ofertado; a promoção não é anunciada (BR-01).",
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


_ORDER_HEADER = ("pedido", "data", "status", "pagamento", "total")


def check_order_totals(ctx: CheckContext) -> list[Finding]:
    orders = ctx.tables["orders"]
    findings = []
    mismatched = [o for o in orders if o["total_mismatch"]]
    if mismatched:
        findings.append(
            Finding(
                code="order.total_mismatch",
                section="orders",
                severity="conflito",
                title="Total do pedido diferente da soma dos itens",
                detail=(
                    "A soma usa o preço **atual** do catálogo (não há preço histórico por item), "
                    "então é um indício, não uma prova."
                ),
                treatment="Vale o `total_brl` registrado: é o que o cliente pagou (BR-16).",
                ids=_ids(mismatched, "order_id"),
                table_header=(*_ORDER_HEADER, "soma dos itens"),
                table_rows=_order_rows(mismatched, lambda o: brl(o["items_total_brl"])),
            )
        )

    pix = [o for o in orders if o["pix_without_discount"]]
    if pix:
        findings.append(
            Finding(
                code="order.pix_without_discount",
                section="orders",
                severity="regra",
                title="Pagamento PIX sem o desconto de 5%",
                detail=(
                    "O total é igual ao preço de tabela dos itens, sem os 5% do PIX (manual §3), "
                    "e nenhum item tem promoção ativa que justifique não aplicar o desconto."
                ),
                treatment=(
                    "Histórico não é recalculado. Para compras novas, o agente aplica o PIX "
                    "corretamente (BR-04)."
                ),
                ids=_ids(pix, "order_id"),
                table_header=(*_ORDER_HEADER, "com PIX seria"),
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
                severity="regra",
                title="Parcela abaixo do mínimo do manual §3.1",
                detail="Faixas: até 3x, R$ 50,00; 4–6x, R$ 80,00; 7–12x, R$ 100,00.",
                treatment=(
                    "Histórico não é recalculado. Para compras novas, a tool só oferece o "
                    "parcelamento permitido (BR-05)."
                ),
                ids=_ids(below, "order_id"),
                table_header=(*_ORDER_HEADER, "parcela"),
                table_rows=_order_rows(
                    below,
                    lambda o: f"{o['installments']}x de {brl(o['total_brl'] / o['installments'])}",
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
        problems.append((str(oid), "itens de um pedido inexistente"))
    for order in sorted(orders, key=lambda o: o["order_id"]):
        oid = str(order["order_id"])
        if order["order_id"] not in with_items:
            problems.append((oid, "pedido sem itens"))
        tracking = order["tracking_code"]
        if order["status"] in SHIPPED_STATUSES:
            if not tracking:
                problems.append((oid, "enviado/entregue sem código de rastreio"))
            elif not TRACKING_PATTERN.match(tracking):
                problems.append((oid, f"rastreio {tracking} fora do formato do manual §5.3"))
            if order["estimated_delivery"] is None:
                problems.append((oid, "enviado/entregue sem previsão de entrega"))
        elif tracking:
            problems.append((oid, f"status {order['status']} com código de rastreio"))
        delivery = order["estimated_delivery"]
        if delivery is not None and delivery < order["order_date"]:
            problems.append((oid, "previsão de entrega anterior à data do pedido"))
        if order["status"] == "cancelled" and not order["notes"]:
            problems.append((oid, "cancelado sem motivo em `notes`"))
    if not problems:
        return []
    return [
        Finding(
            code="order.integrity",
            section="orders",
            severity="conflito",
            title="Inconsistências estruturais em pedidos",
            detail="Pedidos cujos campos não batem entre si.",
            treatment="Registro mantido; o agente mostra só os campos presentes.",
            ids=tuple(sorted({int(p[0]) for p in problems})),
            table_header=("pedido", "problema"),
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
                severity="atenção",
                title=f"Pedidos atrasados em {date_br(today)}",
                detail="Status `shipped`/`confirmed` com previsão de entrega já vencida.",
                treatment="A tool marca `is_late` e recomenda passagem para humano (BR-09).",
                ids=_ids(late, "order_id"),
                table_header=(*_ORDER_HEADER, "previsão"),
                table_rows=_order_rows(late, lambda o: date_br(o["estimated_delivery"])),
            )
        )
    stale = [o for o in orders if is_stale(o["status"], o["order_date"], today)]
    if stale:
        findings.append(
            Finding(
                code="order.stale",
                section="orders",
                severity="atenção",
                title=f"Pedidos parados em {date_br(today)}",
                detail="Status `pending`/`confirmed` há mais de 7 dias sem envio.",
                treatment="A tool marca `is_stale` e recomenda passagem para humano (BR-09).",
                ids=_ids(stale, "order_id"),
                table_header=(*_ORDER_HEADER, "dias parado"),
                table_rows=_order_rows(stale, lambda o: str((today - o["order_date"]).days)),
            )
        )
    return findings


def check_customers(ctx: CheckContext) -> list[Finding]:
    customers = ctx.tables["customers"]
    findings = []
    for key, label, code in (
        ("email_normalized", "e-mail", "customer.duplicate_email"),
        ("phone_digits", "telefone", "customer.duplicate_phone"),
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
                    severity="conflito",
                    title=f"{label.capitalize()} compartilhado por mais de um cliente",
                    detail="; ".join(
                        f"`{value}`: clientes {join_ints(c['customer_id'] for c in group)}"
                        for value, group in sorted(duplicated.items())
                    ),
                    treatment=(
                        "O contato sozinho não identifica o cliente: `get_order` exige número do "
                        "pedido + contato, e `list_my_orders` busca só por telefone (BR-08)."
                    ),
                    ids=tuple(sorted(c["customer_id"] for g in duplicated.values() for c in g)),
                )
            )

    invalid = [
        (str(c["customer_id"]), problem)
        for c in customers
        for problem in (
            None if PHONE_DIGITS_PATTERN.match(c["phone_digits"]) else f"telefone {c['phone']}",
            None if EMAIL_PATTERN.match(c["email_normalized"]) else f"e-mail {c['email']}",
        )
        if problem
    ]
    if invalid:
        findings.append(
            Finding(
                code="customer.invalid_contact",
                section="customers",
                severity="conflito",
                title="Contatos em formato inválido",
                detail="Telefone ou e-mail fora do formato esperado.",
                treatment="Registro mantido; esse contato não serve para verificação (BR-08).",
                ids=tuple(sorted({int(i[0]) for i in invalid})),
                table_header=("cliente", "problema"),
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
                severity="conflito",
                title="Dois números apresentados como WhatsApp",
                detail=(
                    f'Números no manual: {", ".join(phones)}. A §1.2 traz "Telefone / WhatsApp" '
                    "e a §7 traz outro número como o canal de WhatsApp."
                ),
                treatment=(
                    "WhatsApp = (67) 3321-4500 (seção específica do canal); telefone = "
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
                severity="conflito",
                title="E-mail com acento no domínio",
                detail=f"E-mails no manual: {', '.join(f'`{e}`' for e in emails)}.",
                treatment=(
                    "Usado `contato@emporiodamusica.com.br` (capa); o acento é tratado como erro "
                    "de digitação (BR-12, DD-§6)."
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
                severity="conflito",
                title="Parcela mínima contraditória entre §3 e §3.1",
                detail=(
                    'A tabela da §3 diz "parcela mínima de R$ 100,00" para todo o crédito; a '
                    f"§3.1 define faixas. Mínimos citados: {cited}."
                ),
                treatment=(
                    "Vale a §3.1 (mais específica): 1–3x R$ 50,00 · 4–6x R$ 80,00 · "
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
                severity="conflito",
                title="Tamanho do catálogo",
                detail=(
                    f"O manual (§1) fala em mais de {claimed.group(1)} instrumentos; o sistema "
                    f"tem {actual} produtos."
                ),
                treatment="Vale o sistema; o agente nunca afirma ter o que não está na base.",
            )
        )

    if "acima de r$ 500,00" in normalized and "abaixo de r$ 500,00" in normalized:
        findings.append(
            Finding(
                code="manual.shipping_threshold_gap",
                section="manual",
                severity="atenção",
                title="Frete de exatamente R$ 500,00 não está coberto",
                detail='§5.1: grátis "acima de R$ 500,00" e R$ 35,00 "abaixo de R$ 500,00".',
                treatment="Até R$ 500,00 inclusive paga R$ 35,00; grátis só acima (BR-07).",
            )
        )

    active = sum(1 for p in ctx.tables["promotions"] if p["is_active"])
    if "black friday (novembro)" in normalized:
        findings.append(
            Finding(
                code="manual.black_friday",
                section="manual",
                severity="atenção",
                title="Black Friday prevista em novembro × promoções do sistema",
                detail=(
                    'O manual (§6.1) promete Black Friday em novembro com 15% a 30% "em todo o '
                    f'catálogo"; o sistema tem só {active} promoções ativas, e todas as de '
                    "Black Friday estão inativas."
                ),
                treatment="Vale o sistema: só promoção com `is_active = 1` (manual §7.3, DD-§6).",
            )
        )
    return findings


CHECKS: tuple[tuple[str, str, Check], ...] = (
    (
        "products",
        "Descrição × nome/specs (marca, cordas, teclas, madeira)",
        check_description_conflicts,
    ),
    ("products", "Nome × specs (nylon/aço, elétrico, cor)", check_name_specs),
    ("products", "Status e estoque", check_product_status),
    ("products", "Esquemas de specs", check_specs_schemas),
    ("products", "Origem dos registros", check_specs_serialization),
    ("categories", "Categorias com produtos", check_empty_categories),
    ("promotions", "Promoções", check_promotions),
    ("orders", "Totais, PIX e parcelamento", check_order_totals),
    (
        "orders",
        "Integridade (itens, rastreio no formato do manual §5.3, datas, motivo de cancelamento)",
        check_order_integrity,
    ),
    ("orders", "Atraso e pedidos parados", check_order_timeliness),
    ("customers", "Contatos duplicados e formato de telefone/e-mail", check_customers),
    ("manual", "Contradições internas do manual", check_manual),
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
