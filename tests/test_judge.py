"""LLM as judge: prompt input, structured verdict handling and the report section (no LLM)."""

from datetime import datetime

from langchain_core.runnables import RunnableLambda

from eval.judge import ToneScore, ToneVerdict, judge_case, render_conversation
from eval.run import CaseResult, TurnResult, render_report


def case(case_id: str = "c1", category: str = "horario", error: str | None = None) -> CaseResult:
    turn = TurnResult(
        user="Vocês abrem amanhã?",
        reply="Oi! Sou a Dó 🎶 Amanhã abrimos às 09:00. Posso ajudar em algo mais?",
        tools=[{"name": "search_policies", "args": {"question": "horário"}}],
        failures=[],
        error=error,
    )
    return CaseResult(case_id, category, "2026-11-03T10:00:00", seconds=1.0, turns=[turn])


def verdict(**overrides) -> ToneVerdict:
    values = {"tom": 5, "cordialidade": 4, "concisao": 5, "encaminhamento": 4}
    return ToneVerdict(**(values | overrides), comentario="Faltou acolher melhor.")


def test_conversation_shows_messages_and_tool_names_but_not_raw_results():
    text = render_conversation(case())
    assert "**Cliente:** Vocês abrem amanhã?" in text
    assert "**Dó:** Oi! Sou a Dó" in text
    assert "search_policies" in text
    assert "horário" not in text.split("**Dó:**")[0].split("consultas")[1]  # no tool args


def test_judge_case_returns_scores_and_sends_rubric():
    seen = []

    def fake(messages):
        seen.append(messages)
        return verdict(cordialidade=3)

    score = judge_case(RunnableLambda(fake), case())
    assert score.scores == {"tom": 5, "cordialidade": 3, "concisao": 5, "encaminhamento": 4}
    assert score.mean == 4.25
    assert score.comment == "Faltou acolher melhor."
    system, human = seen[0]
    assert "Não** avalie se preços" in system.content
    assert "Vocês abrem amanhã?" in human.content


def test_judge_accepts_dict_output():
    score = judge_case(RunnableLambda(lambda _: verdict().model_dump()), case())
    assert score.scores["tom"] == 5


def test_judge_failure_is_recorded_not_raised():
    def boom(_):
        raise RuntimeError("quota")

    score = judge_case(RunnableLambda(boom), case())
    assert score.scores is None and "quota" in score.error


def test_infrastructure_error_case_is_not_judged():
    def never(_):
        raise AssertionError("should not be called")

    score = judge_case(RunnableLambda(never), case(error="rate limit"))
    assert score.scores is None and "não avaliado" in score.error


def test_report_tone_section_is_informative_only():
    good, low, skipped = case("bom"), case("baixo", category="pedido"), case("sem-nota")
    good.tone = ToneScore({"tom": 5, "cordialidade": 5, "concisao": 5, "encaminhamento": 5})
    low.tone = ToneScore(
        {"tom": 3, "cordialidade": 2, "concisao": 4, "encaminhamento": 3}, comment="Seca."
    )
    skipped.tone = ToneScore(error="quota")
    report = render_report(
        [good, low, skipped], "fake:model", datetime(2026, 11, 3, 10), judge_model="fake:judge"
    )

    assert "Taxa de aprovação: 100%" in report  # the judge does not change pass/fail
    assert "## Tom e cordialidade (LLM como juiz)" in report
    assert "Casos avaliados: 2 (1 sem nota" in report
    assert "**Média geral: 4,0**" in report
    assert "| Cordialidade | 3,5 | 1 |" in report
    assert "| Pedido e LGPD | 3,0 | 2,0 | 4,0 | 3,0 |" in report
    lowest = report.split("menores notas")[1]
    assert lowest.index("`baixo`") < lowest.index("`bom`")
    assert "Seca." in lowest


def test_report_without_judge_has_no_tone_section():
    report = render_report([case()], "fake:model", datetime(2026, 11, 3, 10))
    assert "LLM como juiz" not in report
