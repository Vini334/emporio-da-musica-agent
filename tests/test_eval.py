"""Evaluation harness: case parsing, deterministic checks and the report (no LLM)."""

from datetime import datetime
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

from emporio import config
from emporio.agent import MODEL_ERROR_REPLY, Reply, ToolCall, build_agent
from eval.checks import Expect, check_turn
from eval.run import CATEGORY_LABELS, load_cases, render_report, run_case
from tests.test_agent import call, scripted


def reply(text: str, *calls: tuple[str, dict]) -> Reply:
    return Reply(text=text, tool_calls=[ToolCall(name, args) for name, args in calls])


def test_text_checks_ignore_case_accents_and_bold():
    expect = Expect.from_dict(
        {"must_contain": ["2.199", "nao encontrei"], "must_not_contain": ["1.935"]}
    )
    assert check_turn(expect, reply("**Não encontrei** nada; o preço é **R$ 2.199,00**")) == []
    failures = check_turn(expect, reply("Sai por R$ 1.935,12"))
    assert "texto proibido presente: '1.935'" in failures
    assert any("2.199" in failure for failure in failures)


def test_contain_any_accepts_flat_list_or_groups():
    flat = Expect.from_dict({"must_contain_any": ["e-mail", "telefone"]})
    assert check_turn(flat, reply("Me passa seu telefone?")) == []
    groups = Expect.from_dict({"must_contain_any": [["fechad"], ["sábado", "amanhã"]]})
    assert check_turn(groups, reply("Hoje estamos fechados, voltamos amanhã")) == []
    assert len(check_turn(groups, reply("Estamos fechados"))) == 1


def test_tool_checks():
    turn = reply("ok", ("get_order", {"order_id": 13, "contact": "(67) 99456-7890"}))
    assert check_turn(Expect.from_dict({"tools_called": ["get_order"]}), turn) == []
    assert check_turn(Expect.from_dict({"tool_args": {"get_order": {"order_id": 13}}}), turn) == []
    assert check_turn(Expect.from_dict({"tool_args": {"get_order": {"order_id": 8}}}), turn)
    assert check_turn(Expect.from_dict({"tools_not_called": ["get_order"]}), turn)
    assert check_turn(Expect.from_dict({"no_tools": True}), turn)
    assert check_turn(Expect.from_dict({"tools_any": ["get_product", "search_products"]}), turn)


def test_unknown_expect_key_is_rejected():
    with pytest.raises(ValueError, match="desconhecidas"):
        Expect.from_dict({"must_contains": ["x"]})


def test_bundled_cases_are_valid():
    cases = load_cases()
    assert len(cases) >= 25
    assert {case.category for case in cases} == set(CATEGORY_LABELS)
    assert len({case.id for case in cases}) == len(cases)


def test_case_level_expect_goes_to_last_turn(tmp_path: Path):
    path = tmp_path / "cases.yaml"
    path.write_text(
        "- id: x\n  category: pedido\n  turns: ['oi', 'pedido 8']\n"
        "  expect: {must_contain: ['e-mail']}\n",
        encoding="utf-8",
    )
    [case] = load_cases(path)
    assert case.turns[0].expect.is_empty
    assert case.turns[1].expect.must_contain == ["e-mail"]


def test_run_case_with_scripted_model(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "HANDOFFS_FILE", tmp_path / "handoffs.jsonl")
    monkeypatch.setenv("EMPORIO_NOW", "2026-11-03T10:00:00")
    path = tmp_path / "cases.yaml"
    path.write_text(
        "- id: lgpd\n  category: pedido\n"
        "  turns: ['Pedido 8, e-mail errado@x.com']\n"
        "  expect: {tools_called: [get_order], must_contain: ['não encontrei']}\n",
        encoding="utf-8",
    )
    [case] = load_cases(path)
    model = scripted(
        AIMessage("", tool_calls=[call("get_order", "c1", order_id=8, contact="errado@x.com")]),
        AIMessage("Não encontrei pedido com esses dados."),
    )
    result = run_case(case, build_agent(model, InMemorySaver()))

    assert result.status == "passou"
    assert result.tools_used == ["get_order"]
    report = render_report([result], "fake:model", datetime(2026, 11, 3, 10))
    assert "Taxa de aprovação: 100%" in report
    assert "| `lgpd` | Pedido e LGPD | ✅ passou | `get_order` |" in report


def test_model_error_is_reported_as_error_not_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("EMPORIO_NOW", "2026-11-03T10:00:00")
    path = tmp_path / "cases.yaml"
    path.write_text(
        "- id: e\n  category: horario\n  turns: ['Abertos?']\n  expect: {must_contain: ['x']}\n",
        encoding="utf-8",
    )
    [case] = load_cases(path)
    result = run_case(case, build_agent(scripted(AIMessage(MODEL_ERROR_REPLY)), InMemorySaver()))
    assert result.status == "erro"
    assert result.turns[0].failures == []
