"""Agent graph wiring with a scripted fake model (no real LLM, no network)."""

from collections.abc import Iterator
from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, SystemMessage
from langgraph.checkpoint.memory import InMemorySaver

from emporio import config
from emporio.agent import Assistant, build_agent


class ScriptedModel(GenericFakeChatModel):
    """Replays scripted AI messages and records the system prompt of each call."""

    seen_prompts: list[str] = []

    def bind_tools(self, tools: Any, **kwargs: Any):
        return self

    def _generate(self, messages, *args, **kwargs):
        system = next((m for m in messages if isinstance(m, SystemMessage)), None)
        self.seen_prompts.append(system.text if system else "")
        return super()._generate(messages, *args, **kwargs)


def scripted(*messages: AIMessage) -> ScriptedModel:
    iterator: Iterator[AIMessage] = iter(messages)
    return ScriptedModel(messages=iterator, seen_prompts=[])


def call(name: str, call_id: str, **args) -> dict:
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("EMPORIO_NOW", "2026-11-07T15:00:00")  # Saturday, store closed
    monkeypatch.setattr(config, "HANDOFFS_FILE", tmp_path / "handoffs.jsonl")


def test_turn_runs_tools_and_returns_trace():
    model = scripted(
        AIMessage("", tool_calls=[call("get_order", "c1", order_id=8, contact="errado@x.com")]),
        AIMessage("Não encontrei pedido com esses dados."),
    )
    assistant = Assistant(build_agent(model, InMemorySaver()), session_id="s1")
    reply = assistant.send("Cadê meu pedido 8? Meu e-mail é errado@x.com")

    assert reply.text == "Não encontrei pedido com esses dados."
    [trace] = reply.tool_calls
    assert trace.name == "get_order" and trace.args == {"order_id": 8, "contact": "errado@x.com"}
    assert trace.result["ok"] is False  # contact does not match: generic error (BR-08)


def test_system_prompt_is_injected_with_clock_on_every_call():
    model = scripted(AIMessage("Oi! Sou a Dó."), AIMessage("Até mais!"))
    assistant = Assistant(build_agent(model, InMemorySaver()), session_id="s2")
    assistant.send("Oi")
    assistant.send("Tchau")
    assert len(model.seen_prompts) == 2
    assert all("sábado, 07/11/2026, 15:00" in p for p in model.seen_prompts)
    assert all("FECHADA" in p for p in model.seen_prompts)


def test_memory_is_per_session():
    model = scripted(AIMessage("a"), AIMessage("b"), AIMessage("c"))
    agent = build_agent(model, InMemorySaver())
    first = Assistant(agent, session_id="x")
    first.send("primeira")
    first.send("segunda")
    Assistant(agent, session_id="y").send("outra conversa")

    history = agent.get_state({"configurable": {"thread_id": "x"}}).values["messages"]
    assert [m.text for m in history] == ["primeira", "a", "segunda", "b"]
    other = agent.get_state({"configurable": {"thread_id": "y"}}).values["messages"]
    assert len(other) == 2


def test_handoff_tool_receives_session_id(tmp_path):
    model = scripted(
        AIMessage(
            "",
            tool_calls=[
                call("handoff_to_human", "h1", reason="pedido_atrasado", summary="Pedido 8")
            ],
        ),
        AIMessage("Encaminhei! Protocolo EMP-20261107-0001."),
    )
    reply = Assistant(build_agent(model, InMemorySaver()), session_id="sess-9").send("Encaminha")
    result = reply.tool_calls[0].result
    assert result["protocol"] == "EMP-20261107-0001"
    assert result["next_human_window"] == "segunda-feira, 09/11 às 09:00"
    assert '"session_id": "sess-9"' in (tmp_path / "handoffs.jsonl").read_text()
