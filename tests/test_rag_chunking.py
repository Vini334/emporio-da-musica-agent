"""Policies PDF → chunks (SPEC §7), over the real manual. No embeddings involved."""

import pytest

from emporio.rag.ingest import PolicyChunk, clean_body, load_chunks

EXPECTED_SECTIONS = [
    "1", "1.1", "1.2", "2", "3", "3.1", "4", "4.1", "4.2", "4.3", "4.4",
    "5.1", "5.2", "5.3", "6.1", "6.2", "7", "7.1", "7.2", "7.3",
    "8.1", "8.2", "8.3", "9", "10",
]  # fmt: skip


@pytest.fixture(scope="module")
def chunks() -> dict[str, PolicyChunk]:
    return {c.section: c for c in load_chunks()}


def test_one_chunk_per_subsection_in_order(chunks):
    # Sections 5, 6 and 8 have no intro text before their first subsection, so no chunk.
    assert list(chunks) == EXPECTED_SECTIONS


def test_numbered_list_items_are_not_headings(chunks):
    flow = chunks["7.2"].text
    assert "1. Saudação" in flow and "5. Fechamento" in flow  # list spans pages 6–7
    assert chunks["7.3"].title == "Situações Especiais"


def test_titles_and_parents(chunks):
    assert chunks["4.1"].title == "Direito de Arrependimento (Compras Online)"
    assert chunks["4.1"].parent_title == "Política de Trocas e Devoluções"
    assert chunks["9"].parent_title == ""
    assert chunks["5.2"].heading == (
        "Política de Frete e Entregas — 5.2 Entregas para Outras Cidades"
    )


def test_running_header_and_footer_removed(chunks):
    for chunk in chunks.values():
        assert "Manual de Políticas e Procedimentos" not in chunk.text
        assert "Página " not in chunk.text


def test_cover_page_is_not_indexed(chunks):
    # The cover only repeats contacts, which live in the prompt (BR-12).
    assert all(c.page >= 2 for c in chunks.values())


def test_hours_table_survives_extraction(chunks):
    text = chunks["2"].text
    assert "Sábado | 09:00 às 13:00" in text
    assert "Domingo e Feriados | Fechado" in text


def test_payment_table_and_installment_tiers(chunks):
    assert "PIX | Pagamento à vista | 5% de desconto sobre o preço de tabela" in chunks["3"].text
    tiers = chunks["3.1"].text
    assert "(exceto abaixo de R$ 50,00)" in tiers  # wrapped line rejoined
    assert "4x a 6x: sem juros, parcela mínima de R$ 80,00" in tiers
    assert "acima de R$ 2.000,00" in tiers
    assert tiers.count("• ") == 4 and " | " not in tiers  # bullets are not table cells


def test_other_cities_shipping_spans_two_pages(chunks):
    text = chunks["5.2"].text
    assert chunks["5.2"].page == 4
    assert "PAC (Correios) | 5 a 12 dias úteis | Sim | Incluído" in text
    assert "SEDEX (Correios) | 2 a 5 dias úteis" in text
    assert "frete especial com cotação individual" in text


def test_embedding_text_prefixes_heading(chunks):
    first_line = chunks["4.4"].embedding_text().splitlines()[0]
    assert first_line == "Política de Trocas e Devoluções — 4.4 Itens Não Elegíveis para Troca"


def test_clean_body_rejoins_wrapped_table_cell_and_paragraph():
    raw = [
        " PIX          Pagamento à vista          5% de desconto sobre o preço de",
        "                                         tabela",
        "",
        "Primeira linha de um parágrafo que",
        "continua aqui.",
        "    •    item",
    ]
    assert clean_body(raw) == (
        "PIX | Pagamento à vista | 5% de desconto sobre o preço de tabela\n"
        "Primeira linha de um parágrafo que continua aqui.\n"
        "• item"
    )
