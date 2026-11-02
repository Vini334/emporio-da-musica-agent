"""Streamlit chat (SPEC §10, phase 7): the same ``build_agent()`` as the CLI, with a sidebar panel
of the tool calls made in each turn.

    uv run emporio-web                      # or: uv run streamlit run src/emporio/web.py

One compiled agent (and its SQLite memory) is shared by the server; each browser session is its
own conversation (``session_id``). The simulated clock sets ``EMPORIO_NOW`` for the whole process:
fine for a local demo, not for a multi-user deployment.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

import streamlit as st

from emporio import agent as agent_module
from emporio import clock, config
from emporio.agent import Assistant, Reply
from emporio.cli import ASSISTANT_NAME, call_signature

ASSISTANT_AVATAR = "🎵"
USER_AVATAR = "👤"
SUGGESTIONS = (
    "Quais violões vocês têm até R$ 1000?",
    "Quanto custa o Takamine GD20? Tá com desconto de Black Friday?",
    "Cadê meu pedido 8? Já era pra ter chegado",
    "Vocês abrem no domingo?",
)


@st.cache_resource(show_spinner="Preparando a Dó (catálogo, manual de políticas e modelo)...")
def shared_agent():
    return agent_module.build_agent()


# --------------------------------------------------------------------------- state


def _state():
    if "assistant" not in st.session_state:
        st.session_state.assistant = Assistant(shared_agent())
        st.session_state.history = []  # [{"role", "text", "reply"}]
    return st.session_state


def _new_conversation() -> None:
    state = _state()
    state.assistant.reset()
    state.history = []


def _queue(message: str) -> None:
    st.session_state.pending = message


# --------------------------------------------------------------------------- sidebar


def clock_controls() -> None:
    """Optional simulated "now" (EMPORIO_NOW), to demo closed hours, holidays and deadlines."""
    pinned = config.now_override()
    start = clock.now()
    simulate = st.toggle("Simular data e hora", value=bool(pinned), key="simulate_clock")
    if simulate:
        col_day, col_time = st.columns(2)
        day = col_day.date_input("Data", value=start.date(), format="DD/MM/YYYY", key="sim_day")
        hour = col_time.time_input("Hora", value=start.time(), step=900, key="sim_time")
        os.environ["EMPORIO_NOW"] = datetime.combine(day, hour).isoformat(timespec="minutes")
        st.caption("Vale para o processo inteiro (demo local).")
    else:
        os.environ.pop("EMPORIO_NOW", None)


def store_status_text() -> str:
    status = clock.store_status()
    moment = status.now
    when = f"{clock.WEEKDAYS_PT[moment.weekday()]}, {moment:%d/%m/%Y %H:%M}"
    if status.is_open and status.closes_at is not None:
        line = f"🟢 Loja aberta · fecha às {status.closes_at:%H:%M}"
    elif status.next_opening is not None:
        line = f"🔴 Loja fechada · equipe volta {clock.describe_moment(status.next_opening)}"
    else:
        line = "🔴 Loja fechada"
    holiday = f" · feriado: {status.holiday}" if status.holiday else ""
    return f"{when}{holiday}  \n{line}"


def tool_panel(history: list[dict]) -> None:
    """Tool calls per turn, newest first; the latest turn starts expanded."""
    st.subheader("🔧 Chamadas de tool")
    turns = [
        (number, item["reply"])
        for number, item in enumerate((h for h in history if h["role"] == "assistant"), start=1)
        if item["reply"].tool_calls
    ]
    if not turns:
        st.caption("Nenhuma ainda. As consultas da Dó aparecem aqui, turno a turno.")
        return
    latest = turns[-1][0]
    for number, reply in reversed(turns):
        names = ", ".join(call.name for call in reply.tool_calls)
        with st.expander(f"Turno {number} · {names}", expanded=number == latest):
            for call in reply.tool_calls:
                st.code(call_signature(call), language=None, wrap_lines=True)
                if isinstance(call.result, dict | list):
                    st.json(call.result, expanded=False)
                else:
                    st.text(str(call.result))


# --------------------------------------------------------------------------- chat


def show_message(role: str, text: str, reply: Reply | None = None) -> None:
    avatar = ASSISTANT_AVATAR if role == "assistant" else USER_AVATAR
    with st.chat_message(role, avatar=avatar):
        st.markdown(text)
        if reply is not None and reply.tool_calls:
            st.caption("🔧 " + " · ".join(call.name for call in reply.tool_calls))


def main() -> None:
    st.set_page_config(page_title="Empório da Música · Dó", page_icon=ASSISTANT_AVATAR)
    state = _state()

    with st.sidebar:
        st.title(f"{ASSISTANT_AVATAR} Empório da Música")
        st.caption(f"Modelo `{config.llm_model()}` · sessão `{state.assistant.session_id}`")
        clock_controls()
        st.markdown(store_status_text())
        st.button("Nova conversa", on_click=_new_conversation, width="stretch")
        st.divider()

    st.title(f"Converse com a {ASSISTANT_NAME}")
    st.caption("Assistente virtual da Empório da Música · Sua música começa aqui.")

    for item in state.history:
        show_message(item["role"], item["text"], item.get("reply"))

    message = (st.chat_input("Escreva sua mensagem") or state.pop("pending", None) or "").strip()
    if not state.history and not message:
        st.caption("Sugestões:")
        for index, suggestion in enumerate(SUGGESTIONS):
            st.button(suggestion, key=f"suggestion-{index}", on_click=_queue, args=(suggestion,))

    if message:
        show_message("user", message)
        with st.spinner(f"{ASSISTANT_NAME} está digitando..."):
            try:
                reply = state.assistant.send(message)
            except Exception as exc:  # noqa: BLE001 - shown to the operator, the app goes on
                reply = None
                st.error(f"Não consegui falar com o modelo: {exc}")
        if reply is not None:
            state.history.append({"role": "user", "text": message, "reply": None})
            state.history.append({"role": "assistant", "text": reply.text, "reply": reply})
            show_message("assistant", reply.text, reply)

    with st.sidebar:
        tool_panel(state.history)


def run() -> None:
    """Console entry point ``emporio-web``: launches ``streamlit run`` on this file."""
    from streamlit.web import cli as stcli

    sys.argv = ["streamlit", "run", str(Path(__file__).resolve()), *sys.argv[1:]]
    sys.exit(stcli.main())


if __name__ == "__main__":
    main()
