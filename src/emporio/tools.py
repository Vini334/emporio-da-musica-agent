"""LangChain tools over ``rules/``, the repository and the policies RAG (SPEC §6).

Tools only translate I/O: validate arguments, call the rules, and serialize a short pt-BR JSON
with money and dates already formatted (BR-15). Expected errors become
``{"ok": False, "error": ...}``; nothing raises to the LLM.

The docstrings are part of the prompt: they tell the model when to use each tool and when not.
"""

from __future__ import annotations

import functools
import logging
from collections.abc import Callable
from typing import Any, Literal

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, tool

from emporio import clock, handoff
from emporio.data.repository import Repository, get_repository
from emporio.formatting import brl, date_br
from emporio.rag import retriever as policy_retriever
from emporio.rules import catalog, orders, pricing, returns, shipping

logger = logging.getLogger(__name__)

MAX_LIST_ITEMS = 6
NOT_FOUND_ORDER = (
    "Não encontrei pedido com esses dados. Confira o número do pedido e o e-mail ou telefone "
    "usado na compra."
)
TECHNICAL_ERROR = "Tive um problema técnico para consultar o sistema agora."

_SPEC_LABELS = {
    "top": "tampo",
    "back_sides": "fundo e laterais",
    "neck": "braço",
    "scale": "escala",
    "color": "cor",
    "body": "corpo",
    "shells": "cascos",
    "polyphony": "polifonia",
    "type": "tipo",
}


def _safe(func: Callable[..., dict]) -> Callable[..., dict]:
    """Unexpected failures become a friendly error instead of an exception for the LLM."""

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> dict:
        try:
            return func(*args, **kwargs)
        except Exception:
            logger.exception("tool %s failed", func.__name__)
            return {"ok": False, "error": TECHNICAL_ERROR}

    return wrapper


def _repo() -> Repository:
    return get_repository()


def _error(message: str, **extra: Any) -> dict:
    return {"ok": False, "error": message, **extra}


# --------------------------------------------------------------------------- serialization


def specs_summary(product: dict) -> str:
    specs = product["specs"]
    parts: list[str] = []
    if "strings" in specs:
        strings = specs["strings"]
        parts.append(
            {"nylon": "cordas de nylon", "steel": "cordas de aço"}.get(strings, f"{strings} cordas")
        )
    if "electronics" in specs:
        parts.append("com captação" if specs["electronics"] == "yes" else "sem captação")
    if "pieces" in specs:
        parts.append(f"{specs['pieces']} peças")
    if "keys" in specs:
        parts.append(f"{specs['keys']} teclas")
    if specs.get("hardware") == "included":
        parts.append("ferragens inclusas")
    parts += [f"{label} {specs[key]}" for key, label in _SPEC_LABELS.items() if key in specs]
    return " · ".join(parts)


def price_quote(repo: Repository, product: dict) -> pricing.PriceQuote:
    promotion = pricing.best_active_discount(repo.promotions_for(product["product_id"]))
    return pricing.quote(product["price_brl"], promotion)


def _price_fields(quote: pricing.PriceQuote) -> dict:
    fields: dict[str, Any] = {"price": brl(quote.list_price)}
    if quote.promo_percent:
        fields["promo"] = {
            "percent": f"{quote.promo_percent:g}%",
            "final_price": brl(quote.final_price),
            "name": quote.promo_description,
        }
    else:
        fields["promo"] = None
        fields["pix_price"] = brl(quote.pix_price)
    fields["installments"] = _installments_text(quote.max_installments, quote.installment_value)
    return fields


def _installments_text(count: int, value: float) -> str:
    return "à vista" if count == 1 else f"até {count}x de {brl(value)} sem juros"


def product_payload(repo: Repository, product: dict, detailed: bool = False) -> dict:
    """Product as the customer may see it: prices only when it can be sold (BR-01, BR-03)."""
    status = catalog.availability(product["status"], product["stock_quantity"])
    payload: dict[str, Any] = {
        "id": product["product_id"],
        "name": product["name"],
        "category": product["category_name"],
        "availability": catalog.AVAILABILITY_LABELS[status],
    }
    if status in ("disponivel", "sem_estoque"):
        payload |= _price_fields(price_quote(repo, product))
    payload["specs_summary"] = specs_summary(product)
    if detailed and not product["description_conflict"]:  # BR-16
        payload["description"] = product["description"]
    return payload


# --------------------------------------------------------------------------- catalog tools


@tool(parse_docstring=True)
@_safe
def search_products(
    query: str | None = None,
    category: str | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    only_available: bool = True,
    cheapest_first: bool = False,
    limit: int = MAX_LIST_ITEMS,
) -> dict:
    """Busca instrumentos no catálogo com filtros. Use para perguntas abertas: "quais violões
    até R$ 1000?", "tem guitarra Fender?", "ukulele elétrico". Devolve preço, promoção ativa,
    preço no PIX e parcelamento de cada item. A lista é limitada: compare `total_found` com os
    itens devolvidos.

    Não use para um produto específico já nomeado (use get_product) nem para regras de
    pagamento ou frete (use calculate_payment ou search_policies).

    Args:
        query: Palavras do cliente (marca, modelo, tipo, "aço", "nylon", "elétrico"...).
        category: Nome ou id da categoria (ex.: "Violões", "Guitarras", "Ukuleles").
        min_price: Preço mínimo em reais.
        max_price: Orçamento máximo em reais; considera o menor preço possível (PIX ou
            promoção). Com orçamento, os itens mais próximos dele vêm primeiro.
        only_available: True (padrão) traz só o que pode ser vendido agora.
        cheapest_first: True ordena do mais barato para o mais caro. Use para iniciante,
            "o mais em conta" ou para ver as opções mais baratas que ficaram de fora.
        limit: Máximo de itens na resposta (padrão 6).
    """
    repo = _repo()
    category_id = None
    if category:
        resolved = repo.resolve_category(category)
        if resolved is None:
            names = ", ".join(c["name"] for c in repo.categories.values())
            return _error(f"Categoria não encontrada. Categorias da loja: {names}.")
        category_id = resolved["category_id"]

    result = repo.search(query, category_id)
    if result.category is not None and not any(
        p["category_id"] == result.category["category_id"] for p in repo.products.values()
    ):
        return {
            "ok": True,
            "items": [],
            "total_found": 0,
            "note": f"A loja não tem produtos de {result.category['name']} no momento.",
        }

    # (sort key, price shown to the customer, payload) for every product that passes the filters.
    entries: list[tuple[tuple, float, dict]] = []
    hidden = []
    for product in result.products:
        offerable = catalog.is_offerable(product)
        if only_available and not offerable:
            hidden.append(product)
            continue
        pix_only = False
        if offerable:
            quote = price_quote(repo, product)
            lowest, shown = quote.lowest_price, quote.final_price
            if max_price is not None and lowest > max_price:
                continue
            if min_price is not None and quote.final_price < min_price:
                continue
            payload = product_payload(repo, product)
            if max_price is not None and quote.final_price > max_price:
                pix_only = True
                payload["price_note"] = "Dentro do orçamento só pagando no PIX."
        else:
            lowest = shown = product["price_brl"]
            if max_price is not None and lowest > max_price:
                continue
            payload = product_payload(repo, product)
        if cheapest_first:
            key: tuple = (lowest,)
        elif max_price is not None:
            # Closest to the budget first; items that only fit paying by PIX go after.
            key = (pix_only, -lowest)
        else:
            key = ()  # keep the repository's relevance order
        entries.append((key, shown, payload))
    entries.sort(key=lambda entry: entry[0])
    items = [payload for _, _, payload in entries]

    limit = max(1, min(limit, MAX_LIST_ITEMS))
    response: dict[str, Any] = {"ok": True, "items": items[:limit], "total_found": len(items)}
    if len(items) > limit:
        prices = [shown for _, shown, _ in entries]
        response["price_range"] = f"{brl(min(prices))} a {brl(max(prices))}"
        order = (
            "os mais em conta"
            if cheapest_first
            else "os mais próximos do orçamento"
            if max_price is not None
            else "os mais relevantes"
        )
        response["note"] = (
            f"Mostrando {limit} de {len(items)} ({order}). Diga ao cliente quantas opções "
            f"existem e a faixa de preço de tabela ({response['price_range']}) e ofereça mostrar "
            "as outras."
        )
    if query and hidden and not items:
        response["not_available"] = [
            {
                "name": p["name"],
                "availability": catalog.AVAILABILITY_LABELS[
                    catalog.availability(p["status"], p["stock_quantity"])
                ],
            }
            for p in hidden[:3]
        ]
    return response


@tool(parse_docstring=True)
@_safe
def get_product(name_or_id: str) -> dict:
    """Detalhes de UM produto: preço de tabela, promoção ativa (se houver), preço no PIX,
    parcelamento, disponibilidade, especificações e alternativas quando não está disponível.
    Use quando o cliente cita um modelo ("Takamine GD20", "o Yamaha C40") ou pergunta se um
    produto está em promoção. Se o nome for ambíguo, devolve candidatos para o cliente escolher.

    Não use para listar opções por faixa de preço ou tipo (use search_products).

    Args:
        name_or_id: Nome (mesmo parcial ou sem acento) ou id numérico do produto.
    """
    repo = _repo()
    text = name_or_id.strip()
    if text.isdigit():
        product = repo.products.get(int(text))
        if product is None:
            return _error("Não encontrei produto com esse código.")
    else:
        match = repo.match_name(text)
        if match.product is None:
            if not match.candidates:
                return _error(
                    "Não encontrei esse produto no catálogo. A loja só vende o que está no "
                    "sistema; tente search_products com outras palavras."
                )
            return {
                "ok": True,
                "ambiguous": True,
                "candidates": [
                    {
                        "id": p["product_id"],
                        "name": p["name"],
                        "availability": catalog.AVAILABILITY_LABELS[
                            catalog.availability(p["status"], p["stock_quantity"])
                        ],
                    }
                    for p in match.candidates
                ],
            }
        product = match.product

    payload = {"ok": True, **product_payload(repo, product, detailed=True)}
    if not catalog.is_offerable(product):
        alternatives = catalog.pick_alternatives(product, repo.products.values())
        payload["alternatives"] = [product_payload(repo, p) for p in alternatives]
    return payload


@tool(parse_docstring=True)
@_safe
def calculate_payment(
    product_id: int | None = None,
    amount: float | None = None,
    city: str | None = None,
) -> dict:
    """Condições de pagamento e frete: preço final com promoção, preço no PIX (5%, não acumula
    com promoção), máximo de parcelas sem juros e valor da parcela, se pode combinar formas de
    pagamento e, se a cidade for informada, o frete. Use para "em quantas vezes posso pagar?",
    "quanto fica no PIX?", "quanto é o frete para Campo Grande?".

    Informe product_id (preferível, aplica a promoção certa) OU amount (valor total de um
    carrinho). Não use para explicar a política em si (use search_policies).

    Args:
        product_id: Id do produto (veja get_product ou search_products).
        amount: Valor em reais, quando não for um produto só.
        city: Cidade de entrega, para calcular o frete.
    """
    repo = _repo()
    if (product_id is None) == (amount is None):
        return _error("Informe product_id ou amount (só um dos dois).")

    large_item = False
    response: dict[str, Any] = {"ok": True}
    if product_id is not None:
        product = repo.products.get(product_id)
        if product is None:
            return _error("Não encontrei produto com esse código.")
        status = catalog.availability(product["status"], product["stock_quantity"])
        if status != "disponivel":
            return _error(
                f"{product['name']} está {catalog.AVAILABILITY_LABELS[status]}; não há condição "
                "de pagamento para ele agora."
            )
        quote = price_quote(repo, product)
        large_item = shipping.is_large_item(product["category_id"], product["name"])
        response["product"] = product["name"]
    else:
        if amount <= 0:
            return _error("O valor precisa ser maior que zero.")
        quote = pricing.quote(round(amount, 2))
        response["note"] = (
            "Valor avulso: o desconto do PIX não vale para itens que já estão em promoção."
        )

    response |= _price_fields(quote)
    response["split_payment_allowed"] = quote.split_payment_allowed
    if not quote.split_payment_allowed:
        response["split_payment_rule"] = "Combinar formas de pagamento só acima de R$ 2.000,00."
    if city:
        response["shipping"] = _shipping_payload(
            shipping.shipping_quote(city, quote.final_price, large_item)
        )
    return response


def _shipping_payload(quote: shipping.ShippingQuote) -> dict:
    payload: dict[str, Any] = {"note": quote.note}
    if quote.requires_human_quote:
        payload["fee"] = "cotação com a equipe"
        payload["recommended_action"] = "handoff"
    elif quote.local:
        payload["fee"] = "grátis" if quote.fee == 0 else brl(quote.fee)
        payload["deadline"] = quote.deadline
    else:
        payload["fee"] = "calculado pelo CEP no fechamento"
        payload["options"] = [f"{name}: {deadline}" for name, deadline in quote.options]
    return payload


# --------------------------------------------------------------------------- order tools


@tool(parse_docstring=True)
@_safe
def get_order(order_id: int, contact: str) -> dict:
    """Status de um pedido: situação, itens, total, pagamento, rastreio, previsão de entrega e se
    está atrasado ou parado. Exige o número do pedido E o e-mail ou telefone da compra (LGPD):
    peça os dois ao cliente antes de chamar. Nunca tente adivinhar o contato.

    Se a resposta trouxer recommended_action = "handoff", ofereça encaminhar para a equipe.

    Args:
        order_id: Número do pedido.
        contact: E-mail ou telefone (com DDD) informado pelo cliente.
    """
    repo = _repo()
    found = repo.verified_order(order_id, contact)
    if found is None:
        return _error(NOT_FOUND_ORDER)
    order, customer = found
    today = clock.today()
    late = orders.is_late(order["status"], order["estimated_delivery"], today)
    stale = orders.is_stale(order["status"], order["order_date"], today)

    payload: dict[str, Any] = {
        "ok": True,
        "order_id": order["order_id"],
        "customer_first_name": customer["first_name"],
        "status_label": orders.status_label(order["status"]),
        "order_date": date_br(order["order_date"]),
        "items": _order_items(repo, order["order_id"]),
        "total": brl(order["total_brl"]),  # registered total wins (BR-16)
        "payment_label": orders.payment_label(order["payment_method"], order["installments"]),
        "is_late": late,
        "is_stale": stale,
    }
    if order["tracking_code"]:
        payload["tracking_code"] = order["tracking_code"]
    if order["estimated_delivery"]:
        payload["estimated_delivery"] = date_br(order["estimated_delivery"])
    if order["notes"]:
        payload["notes"] = order["notes"]
    action = orders.recommended_action(late, stale)
    if action:
        payload["recommended_action"] = action
        payload["reason"] = (
            "Entrega prevista já passou." if late else "Mais de 7 dias sem envio desde a compra."
        )
    return payload


def _order_items(repo: Repository, order_id: int) -> list[str]:
    return [
        f"{item['quantity']}x {repo.products[item['product_id']]['name']}"
        for item in repo.items_of(order_id)
    ]


@tool(parse_docstring=True)
@_safe
def list_my_orders(phone: str) -> dict:
    """Lista os pedidos do cliente pelo telefone cadastrado (com DDD). Use quando o cliente
    não sabe o número do pedido. Depois, para detalhes, use get_order com o número e o mesmo
    telefone.

    Args:
        phone: Telefone com DDD informado pelo cliente.
    """
    first_name, found = _repo().orders_by_phone(phone)
    if not found:
        return _error(
            "Não encontrei pedidos para esse telefone. Confira o número com DDD ou informe o "
            "número do pedido e o e-mail da compra."
        )
    return {
        "ok": True,
        "customer_first_name": first_name,
        "orders": [
            {
                "order_id": o["order_id"],
                "order_date": date_br(o["order_date"]),
                "status_label": orders.status_label(o["status"]),
                "total": brl(o["total_brl"]),
            }
            for o in found[:MAX_LIST_ITEMS]
        ],
    }


@tool(parse_docstring=True)
@_safe
def check_return_eligibility(
    order_id: int,
    contact: str,
    reason: Literal["arrependimento", "defeito", "preferencia"],
) -> dict:
    """Verifica se um pedido pode ser devolvido, trocado ou coberto pela garantia, aplicando os
    prazos da loja às datas do pedido (7 dias para arrependimento e preferência, 30 dias da
    compra para troca por defeito, 90 dias de garantia legal). Use sempre que o cliente quiser
    devolver, trocar ou reclamar de defeito em um pedido. Exige número do pedido e e-mail ou
    telefone da compra.

    Para dúvidas gerais sobre a política, sem um pedido, use search_policies.

    Args:
        order_id: Número do pedido.
        contact: E-mail ou telefone (com DDD) informado pelo cliente.
        reason: "arrependimento" (desistiu), "defeito" (problema de fabricação) ou
            "preferencia" (trocar cor/modelo).
    """
    repo = _repo()
    found = repo.verified_order(order_id, contact)
    if found is None:
        return _error(NOT_FOUND_ORDER)
    order, _ = found
    assessment = returns.assess_return(
        status=order["status"],
        order_date=order["order_date"],
        received_date=order["estimated_delivery"],
        reason=reason,
        today=clock.today(),
    )
    payload: dict[str, Any] = {
        "ok": True,
        "order_id": order["order_id"],
        "items": _order_items(repo, order["order_id"]),
        "eligible": assessment.eligible,
        "path": assessment.path,
        "deadline": date_br(assessment.deadline) if assessment.deadline else None,
        "reasoning": assessment.reasoning,
        "recommended_action": assessment.recommended_action,
    }
    if assessment.deadlines:
        payload["all_deadlines"] = {k: date_br(v) for k, v in assessment.deadlines.items()}
    if assessment.caveats:
        payload["caveats"] = assessment.caveats
    return payload


# --------------------------------------------------------------------------- policies (RAG)


@tool(parse_docstring=True)
@_safe
def search_policies(question: str) -> dict:
    """Busca no manual de políticas da loja os trechos que respondem a uma dúvida sobre regras:
    trocas e devoluções, garantia, frete e prazos de entrega em geral, formas de pagamento e
    regras de parcelamento, regras de promoções (cumulatividade, Black Friday), rastreamento,
    privacidade/LGPD e o que a loja vende ou não vende. Toda afirmação sobre política precisa
    vir desta tool, nunca da memória.

    NÃO use para preço, estoque ou promoção de um produto (use get_product/search_products),
    para valores de parcela, PIX ou frete de uma compra (use calculate_payment), nem para um
    pedido específico (use get_order ou check_return_eligibility). Pergunta mista ("o violão X
    tem garantia e quanto custa?") pede as duas consultas. Contatos e horário já estão no seu
    contexto: prefira-os ao que aparecer aqui.

    Resuma e aplique os trechos para o cliente; não cole o texto do manual (é documento
    interno).

    Args:
        question: A dúvida do cliente reescrita como pergunta autossuficiente, em português
            (ex.: "prazo para devolver por arrependimento").
    """
    if not question.strip():
        return _error("Preciso da dúvida do cliente para consultar o manual.")
    chunks = policy_retriever.get_retriever().search(question)
    return {
        "ok": True,
        "chunks": [{"section": c.section, "title": c.title, "text": c.text} for c in chunks],
    }


# --------------------------------------------------------------------------- hand-off


@tool(parse_docstring=True)
@_safe
def handoff_to_human(
    reason: handoff.HandoffReason,
    summary: str,
    config: RunnableConfig,
    contact: str | None = None,
) -> dict:
    """Encaminha o atendimento para a equipe humana e gera um protocolo. Use quando: o pedido
    está atrasado ou parado (recommended_action = "handoff"), o cliente quer cancelar, o frete
    precisa de cotação (item de grande porte), há garantia ou defeito a intermediar, o cliente
    reclama ou pede para falar com uma pessoa, ou há pedido sobre dados pessoais (LGPD).

    Confirme com o cliente antes de encaminhar (exceto se ele mesmo pediu um humano). Depois,
    informe o protocolo, o prazo de retorno e, se a loja estiver fechada, quando a equipe volta.
    Não use para dúvidas que as outras tools respondem.

    Args:
        reason: "reclamacao", "pedido_atrasado", "cotacao_frete", "garantia", "cancelamento",
            "lgpd" ou "outro".
        summary: Resumo curto para a equipe: o que o cliente quer, número do pedido e o que já
            foi verificado.
        contact: E-mail ou telefone para retorno, se o cliente informou.
    """
    if not summary.strip():
        return _error("Preciso de um resumo do caso para encaminhar.")
    session_id = (config.get("configurable") or {}).get("thread_id")
    created = handoff.create_handoff(reason, summary, contact, session_id)
    status = clock.store_status()
    payload: dict[str, Any] = {
        "ok": True,
        "protocol": created.protocol,
        "sla": handoff.SLA,
        "store_open_now": status.is_open,
    }
    if not status.is_open and status.next_opening is not None:
        payload["next_human_window"] = clock.describe_moment(status.next_opening)
    return payload


DATA_TOOLS: list[BaseTool] = [
    search_products,
    get_product,
    calculate_payment,
    get_order,
    list_my_orders,
    check_return_eligibility,
]

POLICY_TOOLS: list[BaseTool] = [search_policies]

HANDOFF_TOOLS: list[BaseTool] = [handoff_to_human]

ALL_TOOLS: list[BaseTool] = DATA_TOOLS + POLICY_TOOLS + HANDOFF_TOOLS
