"""Hand-off queue and the handoff_to_human tool (SPEC §11)."""

import json
from datetime import datetime

import pytest

from emporio import clock, config, handoff
from emporio.tools import handoff_to_human


@pytest.fixture
def queue(tmp_path, monkeypatch):
    path = tmp_path / "handoffs.jsonl"
    monkeypatch.setattr(config, "HANDOFFS_FILE", path)
    return path


def lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_protocol_sequence_is_per_day(queue):
    day1 = datetime(2026, 11, 7, 15, tzinfo=clock.TZ)
    day2 = datetime(2026, 11, 9, 9, tzinfo=clock.TZ)
    first = handoff.create_handoff("pedido_atrasado", "Pedido 8 atrasado", at=day1)
    second = handoff.create_handoff("reclamacao", "Cliente insatisfeito", at=day1)
    third = handoff.create_handoff("outro", "Dúvida", at=day2)
    assert first.protocol == "EMP-20261107-0001"
    assert second.protocol == "EMP-20261107-0002"
    assert third.protocol == "EMP-20261109-0001"
    assert [row["protocol"] for row in lines(queue)] == [
        first.protocol,
        second.protocol,
        third.protocol,
    ]


def test_unknown_reason_falls_back_to_outro(queue):
    created = handoff.create_handoff("qualquer", "x", at=datetime(2026, 11, 2, 10, tzinfo=clock.TZ))
    assert created.reason == "outro"


def test_tool_records_session_and_next_window_when_closed(queue, monkeypatch):
    monkeypatch.setenv("EMPORIO_NOW", "2026-11-07T15:00:00")  # Saturday after 13h
    result = handoff_to_human.invoke(
        {"reason": "pedido_atrasado", "summary": "Pedido 8 atrasado", "contact": "(67) 99999-0000"},
        config={"configurable": {"thread_id": "sessao-1"}},
    )
    assert result["ok"] and result["protocol"] == "EMP-20261107-0001"
    assert result["sla"] == "retorno em até 24h úteis"
    assert result["store_open_now"] is False
    assert result["next_human_window"] == "segunda-feira, 09/11 às 09:00"
    row = lines(queue)[0]
    assert row["session_id"] == "sessao-1" and row["contact"] == "(67) 99999-0000"


def test_tool_omits_next_window_when_open(queue, monkeypatch):
    monkeypatch.setenv("EMPORIO_NOW", "2026-11-03T10:00:00")  # Tuesday morning
    result = handoff_to_human.invoke({"reason": "outro", "summary": "Quer falar com alguém"})
    assert result["store_open_now"] is True and "next_human_window" not in result


def test_tool_requires_summary(queue):
    result = handoff_to_human.invoke({"reason": "outro", "summary": "  "})
    assert not result["ok"] and not queue.exists()
