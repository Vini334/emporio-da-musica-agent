"""Return, exchange and warranty eligibility (SPEC BR-10, manual §4 and §8, DD-§6).

Assumptions: every order is an online purchase, and ``estimated_delivery`` is the date the
customer received it (the data has no real delivery date).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from emporio.formatting import date_br

REASONS = ("arrependimento", "defeito", "preferencia")

REGRET_DAYS = 7  # after receipt (§4.1)
PREFERENCE_DAYS = 7  # after receipt (§4.3)
DEFECT_EXCHANGE_DAYS = 30  # after purchase (§4.2)
LEGAL_WARRANTY_DAYS = 90  # after receipt (§8.1)

CAVEATS = {
    "arrependimento": [
        "Produto na embalagem original, sem sinais de uso, com acessórios e manuais.",
        "Reembolso na mesma forma de pagamento em até 10 dias úteis; frete de volta por conta "
        "da loja.",
    ],
    "preferencia": [
        "Depende da disponibilidade do produto desejado.",
        "Produto em perfeito estado e na embalagem original; diferença de valor cobrada ou "
        "reembolsada.",
    ],
    "defeito": [
        "Não cobre mau uso, queda, umidade excessiva ou modificações não autorizadas.",
    ],
}
INELIGIBLE_ITEMS = (
    "Não são elegíveis: instrumentos personalizados ou com setup sob encomenda, itens de "
    'liquidação com aviso de "venda final" e boquilhas de sopro.'
)


@dataclass(frozen=True)
class ReturnAssessment:
    eligible: bool
    path: (
        str | None
    )  # devolucao|troca|garantia_legal|garantia_fabricante|cancelamento|aguardar_entrega
    deadline: date | None
    reasoning: str
    recommended_action: str | None
    deadlines: dict[str, date] = field(default_factory=dict)
    caveats: list[str] = field(default_factory=list)


def assess_return(
    *,
    status: str,
    order_date: date,
    received_date: date | None,
    reason: str,
    today: date,
) -> ReturnAssessment:
    if reason not in REASONS:
        raise ValueError(f"reason must be one of {REASONS}: {reason!r}")

    if status == "cancelled":
        return ReturnAssessment(
            eligible=False,
            path=None,
            deadline=None,
            reasoning="O pedido já está cancelado; não há o que devolver.",
            recommended_action=None,
        )
    if status in ("pending", "confirmed"):
        return ReturnAssessment(
            eligible=True,
            path="cancelamento",
            deadline=None,
            reasoning="O pedido ainda não foi enviado: o caminho é pedir o cancelamento, que é "
            "feito pela equipe.",
            recommended_action="handoff",
        )
    if status == "shipped":
        return ReturnAssessment(
            eligible=False,
            path="aguardar_entrega",
            deadline=None,
            reasoning="O pedido está a caminho. Os prazos de troca e devolução contam a partir do "
            "recebimento. Se o pacote chegar avariado, recuse o recebimento e fale com a loja.",
            recommended_action=None,
        )
    if status != "delivered":
        raise ValueError(f"unknown order status: {status!r}")

    received = received_date or order_date
    deadlines = {
        "arrependimento": received + timedelta(days=REGRET_DAYS),
        "preferencia": received + timedelta(days=PREFERENCE_DAYS),
        "troca_defeito": order_date + timedelta(days=DEFECT_EXCHANGE_DAYS),
        "garantia_legal": received + timedelta(days=LEGAL_WARRANTY_DAYS),
    }
    basis = (
        f"Compra em {date_br(order_date)}; recebimento considerado em {date_br(received)}"
        + (" (data estimada de entrega)" if received_date else " (sem data de entrega registrada)")
        + "."
    )
    caveats = [*CAVEATS[reason], INELIGIBLE_ITEMS]

    if reason in ("arrependimento", "preferencia"):
        deadline = deadlines[reason]
        path = "devolucao" if reason == "arrependimento" else "troca"
        label = "arrependimento" if reason == "arrependimento" else "troca por preferência"
        if today <= deadline:
            return ReturnAssessment(
                eligible=True,
                path=path,
                deadline=deadline,
                reasoning=f"{basis} O prazo de {label} (7 dias após o recebimento) vai até "
                f"{date_br(deadline)}.",
                recommended_action="handoff",
                deadlines=deadlines,
                caveats=caveats,
            )
        defect = _defect_path(deadlines, today)
        return ReturnAssessment(
            eligible=False,
            path=defect.path,
            deadline=defect.deadline,
            reasoning=f"{basis} O prazo de {label} terminou em {date_br(deadline)}. "
            f"Só resta o caminho de defeito de fabricação: {defect.summary}",
            recommended_action="handoff" if defect.path == "garantia_legal" else None,
            deadlines=deadlines,
            caveats=[*caveats, *CAVEATS["defeito"]],
        )

    defect = _defect_path(deadlines, today)
    return ReturnAssessment(
        eligible=defect.path in ("troca", "garantia_legal"),
        path=defect.path,
        deadline=defect.deadline,
        reasoning=f"{basis} {defect.summary}",
        recommended_action="handoff",
        deadlines=deadlines,
        caveats=caveats,
    )


@dataclass(frozen=True)
class _DefectPath:
    path: str
    deadline: date | None
    summary: str


def _defect_path(deadlines: dict[str, date], today: date) -> _DefectPath:
    exchange, legal = deadlines["troca_defeito"], deadlines["garantia_legal"]
    if today <= exchange:
        return _DefectPath(
            "troca",
            exchange,
            f"Troca por defeito direto na loja até {date_br(exchange)} (30 dias após a compra).",
        )
    if today <= legal:
        return _DefectPath(
            "garantia_legal",
            legal,
            f"A troca na loja venceu em {date_br(exchange)}, mas a garantia legal vale até "
            f"{date_br(legal)} (90 dias após o recebimento); a loja intermedia.",
        )
    return _DefectPath(
        "garantia_fabricante",
        None,
        f"A troca na loja venceu em {date_br(exchange)} e a garantia legal em {date_br(legal)}. "
        "Resta a garantia do fabricante (de 6 meses a 2 anos, conforme o certificado do "
        "produto), e a loja pode intermediar se o cliente pedir.",
    )
