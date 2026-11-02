"""LLM as judge for tone and courtesy (SPEC §12, optional): ``uv run python -m eval.run --judge``.

The deterministic checks in ``eval/checks.py`` decide pass/fail on business rules. The judge
scores what they cannot see, the way the reply is written, on four 1–5 criteria taken from the
persona in the system prompt (manual §7.1). It never judges whether numbers or policies are
correct, and its scores are reported on their own: they do not change a case's status or the exit
code.

The judge model defaults to ``JUDGE_MODEL`` or, when unset, ``LLM_MODEL``. Judging with the same
model that answered tends to be lenient (self-preference), so a different model is preferable when
a key is available.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import Runnable
from pydantic import BaseModel, Field

from emporio import config

if TYPE_CHECKING:
    from eval.run import CaseResult

JUDGE_TEMPERATURE = 0.0
CRITERIA = {
    "tom": "Tom",
    "cordialidade": "Cordialidade",
    "concisao": "Concisão",
    "encaminhamento": "Encaminhamento",
}


class ToneVerdict(BaseModel):
    """Structured output the judge model must return."""

    tom: int = Field(
        ge=1,
        le=5,
        description="Informal mas profissional, como um amigo que entende de música; trata por "
        "'você'; emoji pontual, não em toda frase; soa como a Dó, assistente virtual da loja.",
    )
    cordialidade: int = Field(
        ge=1,
        le=5,
        description="Acolhe o cliente; mostra empatia em problemas (atraso, defeito, prazo "
        "vencido); recusa pedidos fora do escopo ou indevidos com simpatia, sem sermão.",
    )
    concisao: int = Field(
        ge=1,
        le=5,
        description="Estilo WhatsApp: mensagens curtas, listas de até uns 6 itens, sem textão, "
        "sem jargão interno (nomes de tools, campos JSON, trechos longos do manual).",
    )
    encaminhamento: int = Field(
        ge=1,
        le=5,
        description="Fecha com um próximo passo claro ou uma pergunta útil; quando falta algo, "
        "pede uma coisa por vez; não deixa o cliente sem saber o que fazer.",
    )
    comentario: str = Field(
        description="Uma ou duas frases em português justificando as notas mais baixas."
    )


JUDGE_PROMPT = """\
Você avalia o atendimento da Dó, assistente virtual da Empório da Música (loja de instrumentos \
musicais em Campo Grande/MS), que conversa com clientes por mensagem, no estilo WhatsApp.

Avalie **só a forma** das respostas da Dó: tom, cordialidade, concisão e encaminhamento. **Não** \
avalie se preços, prazos, estoque ou políticas estão corretos: isso já é checado por outro \
processo. Também não penalize a Dó por recusar descontos, dados de terceiros ou assuntos fora da \
loja: recusar faz parte das regras; avalie apenas se a recusa foi simpática.

Escala de cada critério:
5 = exemplar · 4 = bom, com um detalhe a melhorar · 3 = aceitável, com falhas visíveis · \
2 = ruim · 1 = inadequado para um cliente.

Considere a conversa inteira; se houver mais de uma resposta, a nota reflete o conjunto. Seja \
exigente: um 5 deve ser realmente difícil de melhorar."""


@dataclass
class ToneScore:
    scores: dict[str, int] | None = None
    comment: str = ""
    error: str | None = None

    @property
    def mean(self) -> float | None:
        return sum(self.scores.values()) / len(self.scores) if self.scores else None


def judge_model_name() -> str:
    return os.getenv("JUDGE_MODEL") or config.llm_model()


def build_judge(model: str | None = None) -> Runnable:
    """Structured-output runnable over the judge model, with retries for rate limits."""
    from langchain.chat_models import init_chat_model

    llm = init_chat_model(model or judge_model_name(), temperature=JUDGE_TEMPERATURE)
    return llm.with_structured_output(ToneVerdict).with_retry(
        stop_after_attempt=4, wait_exponential_jitter=True
    )


def render_conversation(result: CaseResult) -> str:
    """The conversation as the judge sees it: customer messages, replies and the tool names
    (so it knows the reply was grounded, without the raw JSON)."""
    parts = []
    for index, turn in enumerate(result.turns, start=1):
        parts.append(f"### Turno {index}\n**Cliente:** {turn.user}")
        if turn.tools:
            names = ", ".join(tool["name"] for tool in turn.tools)
            parts.append(f"_(consultas feitas pela Dó: {names})_")
        parts.append(f"**Dó:** {turn.reply}")
    return "\n\n".join(parts)


def judge_case(judge: Runnable, result: CaseResult) -> ToneScore:
    """Score one case. Cases with an infrastructure error are not judged; judge failures are
    recorded on the score and never raised."""
    if result.status == "erro" or not result.turns:
        return ToneScore(error="caso não avaliado (erro de infraestrutura)")
    messages = [
        SystemMessage(JUDGE_PROMPT),
        HumanMessage(f"Conversa a avaliar:\n\n{render_conversation(result)}"),
    ]
    try:
        verdict: Any = judge.invoke(messages)
    except Exception as exc:  # noqa: BLE001 - a judge failure must not stop the run
        return ToneScore(error=repr(exc))
    if isinstance(verdict, dict):
        verdict = ToneVerdict.model_validate(verdict)
    return ToneScore(
        scores={key: getattr(verdict, key) for key in CRITERIA},
        comment=verdict.comentario.strip(),
    )
