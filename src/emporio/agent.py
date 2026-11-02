"""The agent (SPEC §8): LangChain ``create_agent`` (a LangGraph ReAct loop) over the store tools.

- The system prompt is rebuilt on every model call, so the clock block is always current.
- Memory is a LangGraph checkpointer keyed by ``thread_id = session_id`` (SQLite by default).
- Rate-limit and transient model errors are retried with exponential backoff; tool calls are
  capped per turn so a confused model cannot loop.

``Assistant`` wraps the graph for the interfaces (CLI, evaluation): one call per customer
message, returning the reply text plus the tool calls made in that turn.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import (
    ModelRequest,
    ModelRetryMiddleware,
    ToolCallLimitMiddleware,
    dynamic_prompt,
)
from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.sqlite import SqliteSaver

from emporio import config, prompts
from emporio.tools import ALL_TOOLS

TEMPERATURE = 0.2
MAX_TOOL_CALLS_PER_TURN = 8
RECURSION_LIMIT = 2 * MAX_TOOL_CALLS_PER_TURN + 5  # model + tool steps, with slack
MODEL_ERROR_REPLY = (
    "Ops, tive uma instabilidade técnica agora e não consegui concluir a consulta. 😕 "
    "Pode me mandar a mensagem de novo em instantes? Se preferir, fale com a equipe no "
    "WhatsApp (67) 3321-4500."
)


@dynamic_prompt
def store_prompt(request: ModelRequest) -> str:
    return prompts.system_prompt()


def build_model(model: str | None = None) -> BaseChatModel:
    return init_chat_model(model or config.llm_model(), temperature=TEMPERATURE)


def sqlite_checkpointer(path: Path | None = None) -> SqliteSaver:
    path = path or config.MEMORY_DB
    path.parent.mkdir(parents=True, exist_ok=True)
    return SqliteSaver(sqlite3.connect(path, check_same_thread=False))


def build_agent(
    model: BaseChatModel | str | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
):
    """Compiled agent graph. Defaults: ``LLM_MODEL`` from the env and SQLite memory."""
    if not isinstance(model, BaseChatModel):
        model = build_model(model)
    return create_agent(
        model,
        tools=ALL_TOOLS,
        middleware=[
            store_prompt,
            ModelRetryMiddleware(
                max_retries=3,
                initial_delay=5.0,
                backoff_factor=2.0,
                max_delay=40.0,
                on_failure=lambda exc: MODEL_ERROR_REPLY,
            ),
            ToolCallLimitMiddleware(run_limit=MAX_TOOL_CALLS_PER_TURN, exit_behavior="continue"),
        ],
        checkpointer=checkpointer if checkpointer is not None else sqlite_checkpointer(),
        name="emporio_assistant",
    )


# --------------------------------------------------------------------------- turn interface


@dataclass
class ToolCall:
    name: str
    args: dict[str, Any]
    result: Any = None  # parsed JSON when possible


@dataclass
class Reply:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)


def new_session_id() -> str:
    return uuid.uuid4().hex[:12]


class Assistant:
    """One customer conversation (``session_id`` = LangGraph thread) over a compiled agent."""

    def __init__(self, agent=None, session_id: str | None = None) -> None:
        self.agent = agent if agent is not None else build_agent()
        self.session_id = session_id or new_session_id()

    def reset(self, session_id: str | None = None) -> None:
        self.session_id = session_id or new_session_id()

    def send(self, message: str) -> Reply:
        result = self.agent.invoke(
            {"messages": [HumanMessage(message)]},
            config={
                "configurable": {"thread_id": self.session_id},
                "recursion_limit": RECURSION_LIMIT,
            },
        )
        return turn_reply(result["messages"])


def turn_reply(messages: list[BaseMessage]) -> Reply:
    """Reply text and tool calls of the last turn (everything after the last human message)."""
    start = max((i for i, m in enumerate(messages) if isinstance(m, HumanMessage)), default=-1)
    turn = messages[start + 1 :]
    results = {m.tool_call_id: _parse(m.content) for m in turn if isinstance(m, ToolMessage)}
    calls = [
        ToolCall(call["name"], call["args"], results.get(call["id"]))
        for m in turn
        if isinstance(m, AIMessage)
        for call in m.tool_calls
    ]
    final = next((m for m in reversed(turn) if isinstance(m, AIMessage) and m.text), None)
    return Reply(text=final.text.strip() if final else MODEL_ERROR_REPLY, tool_calls=calls)


def _parse(content: Any) -> Any:
    if isinstance(content, str):
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return content
    return content
