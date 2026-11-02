"""Streamlit app driven by AppTest with the scripted fake model (no real LLM, no browser)."""

from pathlib import Path

import pytest
import streamlit as st
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from streamlit.testing.v1 import AppTest

from emporio import agent as agent_module
from emporio import config
from tests.test_agent import call, scripted

APP = Path(__file__).resolve().parents[1] / "src" / "emporio" / "web.py"
build_agent = agent_module.build_agent


@pytest.fixture
def app(tmp_path, monkeypatch):
    """App whose agent replays: get_order (wrong contact) → generic error reply → a greeting."""
    monkeypatch.setenv("EMPORIO_NOW", "2026-11-07T15:00:00")  # Saturday, store closed
    monkeypatch.setattr(config, "HANDOFFS_FILE", tmp_path / "handoffs.jsonl")
    model = scripted(
        AIMessage("", tool_calls=[call("get_order", "c1", order_id=8, contact="errado@x.com")]),
        AIMessage("Não encontrei pedido com esses dados."),
        AIMessage("Oi de novo!"),
    )
    monkeypatch.setattr(agent_module, "build_agent", lambda: build_agent(model, InMemorySaver()))
    st.cache_resource.clear()
    at = AppTest.from_file(str(APP), default_timeout=30)
    yield at.run()
    st.cache_resource.clear()


def sidebar_text(at: AppTest) -> str:
    return " ".join(m.value for m in at.sidebar.markdown)


def test_first_screen_shows_suggestions_and_closed_store(app):
    assert not app.exception
    assert app.toggle(key="simulate_clock").value is True  # EMPORIO_NOW is pinned
    assert any("Cadê meu pedido 8" in b.label for b in app.button)
    assert "Loja fechada" in sidebar_text(app) and "segunda" in sidebar_text(app)


def test_turn_shows_reply_and_tool_panel(app):
    app.chat_input[0].set_value("Cadê meu pedido 8? errado@x.com").run()

    assert not app.exception
    user, assistant = app.chat_message
    assert user.markdown[0].value == "Cadê meu pedido 8? errado@x.com"
    assert assistant.markdown[0].value == "Não encontrei pedido com esses dados."
    assert "get_order" in assistant.caption[0].value
    assert not any(b.key and b.key.startswith("suggestion") for b in app.button)

    [panel] = app.sidebar.expander
    assert panel.label == "Turno 1 · get_order"
    assert 'get_order(order_id=8, contact="errado@x.com")' in panel.code[0].value


def test_suggestion_button_sends_the_message(app):
    next(b for b in app.button if "Cadê meu pedido 8" in b.label).click().run()
    assert [m.markdown[0].value for m in app.chat_message][0].startswith("Cadê meu pedido 8")


def test_new_conversation_clears_history_and_changes_session(app):
    app.chat_input[0].set_value("oi").run()
    session = app.session_state.assistant.session_id
    next(b for b in app.button if b.label == "Nova conversa").click().run()

    assert not app.chat_message
    assert app.session_state.assistant.session_id != session


def test_simulated_clock_drives_the_status(app):
    import datetime as dt

    app.date_input(key="sim_day").set_value(dt.date(2026, 11, 3)).run()  # Tuesday
    app.time_input(key="sim_time").set_value(dt.time(10, 0)).run()
    assert "Loja aberta" in sidebar_text(app)
    app.toggle(key="simulate_clock").set_value(False).run()
    assert config.now_override() is None
