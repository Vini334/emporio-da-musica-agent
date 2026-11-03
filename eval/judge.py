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
    "tone": "Tone",
    "courtesy": "Courtesy",
    "concision": "Concision",
    "next_step": "Next step",
}


class ToneVerdict(BaseModel):
    """Structured output the judge model must return."""

    tone: int = Field(
        ge=1,
        le=5,
        description="Informal but professional, like a friend who knows music; addresses the "
        "customer as 'você'; occasional emoji, not in every sentence; sounds like Dó, the "
        "store's virtual assistant.",
    )
    courtesy: int = Field(
        ge=1,
        le=5,
        description="Welcomes the customer; shows empathy on problems (delay, defect, missed "
        "deadline); declines out-of-scope or improper requests kindly, without lecturing.",
    )
    concision: int = Field(
        ge=1,
        le=5,
        description="WhatsApp style: short messages, lists of up to about 6 items, no walls of "
        "text, no internal jargon (tool names, JSON fields, long manual excerpts).",
    )
    next_step: int = Field(
        ge=1,
        le=5,
        description="Ends with a clear next step or a useful question; when something is "
        "missing, asks for one thing at a time; never leaves the customer unsure what to do.",
    )
    comment: str = Field(
        description="One or two sentences in English justifying the lowest scores."
    )


JUDGE_PROMPT = """\
You evaluate the customer service of Dó, the virtual assistant of Empório da Música (a musical \
instrument store in Campo Grande/MS, Brazil), who talks to customers by message, WhatsApp style. \
The conversation is in Brazilian Portuguese, as it should be.

Score **only the form** of Dó's replies: tone, courtesy, concision and next step. Do **not** \
judge whether prices, deadlines, stock or policies are correct: another process checks that. \
Do not penalize Dó for declining discounts, third-party data or topics outside the store either: \
declining is part of the rules; judge only whether the refusal was kind.

Scale for each criterion:
5 = exemplary · 4 = good, with one detail to improve · 3 = acceptable, with visible flaws · \
2 = poor · 1 = unacceptable for a customer.

Consider the whole conversation; with more than one reply, the score reflects all of them. Be \
demanding: a 5 should be genuinely hard to improve on. Write the comment in English."""


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
        parts.append(f"### Turn {index}\n**Customer:** {turn.user}")
        if turn.tools:
            names = ", ".join(tool["name"] for tool in turn.tools)
            parts.append(f"_(lookups made by Dó: {names})_")
        parts.append(f"**Dó:** {turn.reply}")
    return "\n\n".join(parts)


def judge_case(judge: Runnable, result: CaseResult) -> ToneScore:
    """Score one case. Cases with an infrastructure error are not judged; judge failures are
    recorded on the score and never raised."""
    if result.status == "error" or not result.turns:
        return ToneScore(error="case not judged (infrastructure error)")
    messages = [
        SystemMessage(JUDGE_PROMPT),
        HumanMessage(f"Conversation to evaluate:\n\n{render_conversation(result)}"),
    ]
    try:
        verdict: Any = judge.invoke(messages)
    except Exception as exc:  # noqa: BLE001 - a judge failure must not stop the run
        return ToneScore(error=repr(exc))
    if isinstance(verdict, dict):
        verdict = ToneVerdict.model_validate(verdict)
    return ToneScore(
        scores={key: getattr(verdict, key) for key in CRITERIA},
        comment=verdict.comment.strip(),
    )
