"""System prompt and its per-turn clock block (SPEC §9, BR-11, BR-12)."""

from datetime import datetime

from emporio import clock, prompts


def at(text):
    return datetime.fromisoformat(text).replace(tzinfo=clock.TZ)


def test_prompt_has_persona_contacts_and_hours():
    text = prompts.system_prompt(at("2026-11-03T10:00"))
    assert "Dó" in text and "assistente virtual" in text
    assert "(67) 3321-4500" in text and "(67) 3341-4444" in text
    assert "contato@emporiodamusica.com.br" in text
    assert "Sábado: 09:00 às 13:00" in text


def test_prompt_routes_policy_and_data_questions():
    text = prompts.SYSTEM_PROMPT
    for name in (
        "search_products",
        "get_product",
        "calculate_payment",
        "get_order",
        "list_my_orders",
        "check_return_eligibility",
        "search_policies",
        "handoff_to_human",
    ):
        assert name in text


def test_clock_block_open():
    block = prompts.clock_context(at("2026-11-03T10:00"))
    assert "terça-feira, 03/11/2026, 10:00" in block
    assert "ABERTA; fecha hoje às 18:00" in block


def test_clock_block_saturday_afternoon_is_closed_until_monday():
    block = prompts.clock_context(at("2026-11-07T15:00"))
    assert "FECHADA" in block and "segunda-feira, 09/11 às 09:00" in block


def test_clock_block_names_the_holiday():
    block = prompts.clock_context(at("2026-11-02T10:00"))  # Finados
    assert "feriado" in block and "FECHADA" in block
