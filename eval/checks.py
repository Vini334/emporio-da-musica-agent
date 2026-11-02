"""Deterministic checks of one agent turn against the expectations of an evaluation case.

Text checks compare a normalized version of the reply (lowercase, no accents, no Markdown
emphasis, single spaces), so ``**R$ 2.199,00**`` contains ``2.199`` and ``Não encontrei``
matches ``nao encontrei``. Tool checks look only at the calls made in that turn.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from emporio.agent import Reply
from emporio.text import normalize

KNOWN_KEYS = {
    "tools_called",
    "tools_any",
    "tools_not_called",
    "no_tools",
    "tool_args",
    "must_contain",
    "must_contain_any",
    "must_not_contain",
}


@dataclass(frozen=True)
class Expect:
    tools_called: list[str] = field(default_factory=list)  # every one of them
    tools_any: list[str] = field(default_factory=list)  # at least one of them
    tools_not_called: list[str] = field(default_factory=list)
    no_tools: bool = False
    tool_args: dict[str, dict[str, Any]] = field(default_factory=dict)  # subset of some call
    must_contain: list[str] = field(default_factory=list)  # every one of them
    must_contain_any: list[list[str]] = field(default_factory=list)  # one per group
    must_not_contain: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> Expect:
        raw = dict(raw or {})
        unknown = set(raw) - KNOWN_KEYS
        if unknown:
            raise ValueError(f"chaves desconhecidas em expect: {sorted(unknown)}")
        groups = raw.pop("must_contain_any", [])
        if groups and all(isinstance(item, str) for item in groups):
            groups = [groups]  # a flat list is a single group
        return cls(must_contain_any=[list(group) for group in groups], **raw)

    @property
    def is_empty(self) -> bool:
        return self == Expect()


def text_key(value: str) -> str:
    return normalize(value.replace("*", "").replace(" ", " "))


def check_turn(expect: Expect, reply: Reply) -> list[str]:
    """Human-readable failures (empty when the turn meets every expectation)."""
    failures: list[str] = []
    called = [call.name for call in reply.tool_calls]
    text = text_key(reply.text)

    for name in expect.tools_called:
        if name not in called:
            failures.append(f"tool esperada não chamada: {name}")
    if expect.tools_any and not set(expect.tools_any) & set(called):
        failures.append(f"nenhuma destas tools foi chamada: {', '.join(expect.tools_any)}")
    for name in expect.tools_not_called:
        if name in called:
            failures.append(f"tool proibida chamada: {name}")
    if expect.no_tools and called:
        failures.append(f"não devia chamar tools, chamou: {', '.join(called)}")
    for name, wanted in expect.tool_args.items():
        calls = [call for call in reply.tool_calls if call.name == name]
        if not any(_args_match(call.args, wanted) for call in calls):
            failures.append(f"nenhuma chamada de {name} com {wanted}")

    for needle in expect.must_contain:
        if text_key(needle) not in text:
            failures.append(f"faltou no texto: {needle!r}")
    for group in expect.must_contain_any:
        if not any(text_key(needle) in text for needle in group):
            failures.append(f"faltou no texto um destes: {group}")
    for needle in expect.must_not_contain:
        if text_key(needle) in text:
            failures.append(f"texto proibido presente: {needle!r}")
    return failures


def _args_match(actual: dict[str, Any], wanted: dict[str, Any]) -> bool:
    return all(_same(actual.get(key), value) for key, value in wanted.items())


def _same(actual: Any, wanted: Any) -> bool:
    if isinstance(wanted, str) or isinstance(actual, str):
        return text_key(str(actual)) == text_key(str(wanted))
    if isinstance(wanted, int | float) and isinstance(actual, int | float):
        return float(actual) == float(wanted)
    return actual == wanted
