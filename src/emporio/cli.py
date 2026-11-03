"""Terminal chat (SPEC §10): ``emporio-chat [--session ID] [--now ISO] [--verbose]
[--transcript caminho.md]``.

Interactive by default. When stdin is not a terminal, each non-empty line is sent as one customer
message, which makes the example conversations reproducible::

    printf 'Oi!\\nCadê meu pedido 8?\\n' | emporio-chat --now 2026-11-07T15:00 --transcript x.md
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from emporio import clock, config
from emporio.agent import Assistant, Reply, ToolCall

ASSISTANT_NAME = "Dó"
EXIT_COMMANDS = {"/sair", "/exit", "/quit"}
NEW_SESSION_COMMAND = "/nova"
SUMMARY_CHARS = 160


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="emporio-chat", description="Chat with Dó, the Empório da Música assistant."
    )
    parser.add_argument("--session", help="session id (resumes a conversation's memory)")
    parser.add_argument(
        "--now",
        type=_iso_datetime,
        help="pins 'now' (ISO 8601, Campo Grande time), e.g. 2026-11-07T15:00",
    )
    parser.add_argument("--verbose", action="store_true", help="show tool calls")
    parser.add_argument(
        "--transcript", type=Path, help="save the conversation as Markdown (with tool calls)"
    )
    return parser.parse_args(argv)


def _iso_datetime(value: str) -> str:
    try:
        datetime.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid date/time: {value!r}") from exc
    return value


# --------------------------------------------------------------------------- formatting


def call_signature(call: ToolCall) -> str:
    args = ", ".join(
        f"{key}={json.dumps(value, ensure_ascii=False)}" for key, value in call.args.items()
    )
    return f"{call.name}({args})"


def result_summary(result: object, limit: int = SUMMARY_CHARS) -> str:
    text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
    return text if len(text) <= limit else text[: limit - 1] + "…"


class Transcript:
    """Markdown log of the conversation, written turn by turn (survives Ctrl+C)."""

    def __init__(self, path: Path, session_id: str) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        now = clock.now()
        pinned = " (pinned with `--now`)" if config.now_override() else ""
        path.write_text(
            "# Conversation with Dó — Empório da Música\n\n"
            f"- Simulated time: {now:%A, %Y-%m-%d %H:%M}{pinned}\n"
            f"- Model: `{config.llm_model()}`\n"
            f"- Session: `{session_id}`\n"
            "- Generated with `emporio-chat --verbose --transcript`; tool calls appear before "
            "each reply. The conversation is in Brazilian Portuguese, the store's language.\n"
            "\n---\n",
            encoding="utf-8",
        )

    def _append(self, text: str) -> None:
        with self.path.open("a", encoding="utf-8") as file:
            file.write(text)

    def turn(self, message: str, reply: Reply) -> None:
        parts = [f"\n**👤 Customer:** {message}\n"]
        for call in reply.tool_calls:
            body = json.dumps(call.result, ensure_ascii=False, indent=2)
            parts.append(
                f"\n<details><summary>🔧 <code>{call_signature(call)}</code></summary>\n\n"
                f"```json\n{body}\n```\n</details>\n"
            )
        parts.append(f"\n**🎵 {ASSISTANT_NAME}:** {reply.text}\n")
        self._append("".join(parts))

    def new_session(self, session_id: str) -> None:
        self._append(f"\n---\n\n*New session: `{session_id}`*\n")


# --------------------------------------------------------------------------- loop


def _messages(console: Console, interactive: bool) -> Iterator[str]:
    if interactive:
        while True:
            try:
                yield console.input("[bold cyan]Você:[/] ")
            except (EOFError, KeyboardInterrupt):
                console.print()
                return
    else:
        for line in sys.stdin:
            if line.strip():
                console.print(f"[bold cyan]Você:[/] {line.strip()}")
                yield line


def show_reply(console: Console, reply: Reply, verbose: bool) -> None:
    if verbose:
        for call in reply.tool_calls:
            console.print(
                f"  🔧 {call_signature(call)}", style="dim", markup=False, highlight=False
            )
            console.print(
                f"     ↳ {result_summary(call.result)}", style="dim", markup=False, highlight=False
            )
    console.print(
        Panel(
            Markdown(reply.text),
            title=f"🎵 {ASSISTANT_NAME}",
            title_align="left",
            border_style="magenta",
        )
    )


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    if args.now:
        os.environ["EMPORIO_NOW"] = args.now

    console = Console()
    interactive = sys.stdin.isatty()
    with console.status("Preparando a Dó (catálogo, manual de políticas e modelo)..."):
        assistant = Assistant(session_id=args.session)
    transcript = Transcript(args.transcript, assistant.session_id) if args.transcript else None

    now = clock.now()
    console.print(
        f"[bold magenta]Empório da Música[/] · [dim]modelo {config.llm_model()} · sessão "
        f"{assistant.session_id} · {clock.WEEKDAYS_PT[now.weekday()]}, {now:%d/%m/%Y %H:%M}[/]"
    )
    if interactive:
        console.print("[dim]Comandos: /nova (nova conversa) · /sair[/]\n")

    for raw in _messages(console, interactive):
        message = raw.strip()
        if not message:
            continue
        if message.lower() in EXIT_COMMANDS:
            break
        if message.lower() == NEW_SESSION_COMMAND:
            assistant.reset()
            console.print(f"[dim]Nova conversa: sessão {assistant.session_id}[/]\n")
            if transcript:
                transcript.new_session(assistant.session_id)
            continue
        with console.status(f"{ASSISTANT_NAME} está digitando..."):
            reply = assistant.send(message)
        show_reply(console, reply, args.verbose)
        if transcript:
            transcript.turn(message, reply)

    if transcript:
        console.print(f"[dim]Transcrição salva em {transcript.path}[/]")


if __name__ == "__main__":
    main()
