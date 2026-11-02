"""Hybrid retrieval and the search_policies tool, with the real local embedding model.

The index is built in a temporary Chroma directory. The model (~220 MB) is downloaded once
into ``storage/models``; if it cannot be loaded (offline, first run), these tests are skipped.
"""

import pytest

from emporio.rag import ingest
from emporio.rag import retriever as policy_retriever
from emporio.tools import POLICY_TOOLS, search_policies


@pytest.fixture(scope="module")
def chroma_dir(tmp_path_factory):
    try:
        ingest.get_embedder()
    except Exception as exc:  # pragma: no cover - depends on network/cache
        pytest.skip(f"modelo de embeddings indisponível: {exc}")
    return tmp_path_factory.mktemp("chroma")


@pytest.fixture(scope="module")
def retriever(chroma_dir):
    return policy_retriever.PolicyRetriever(chroma_dir=chroma_dir)


# Section that must be in the top-3. Several of these fail with dense-only search
# (parcelar, boquilha, trocar a cor, boleto, frete para outra cidade): see DD-§4.
CASES = {
    "me arrependi da compra, posso devolver?": "4.1",
    "quanto tempo demora o reembolso?": "4.1",
    "meu violão chegou com defeito": "4.2",
    "posso trocar a cor da guitarra?": "4.3",
    "vocês trocam boquilha de saxofone?": "4.4",
    "posso parcelar em quantas vezes?": "3.1",
    "posso pagar metade no pix e metade no cartão?": "3.1",
    "aceita boleto?": "3",
    "quanto custa o frete para Dourados?": "5.2",
    "entregam bateria acústica em casa?": "5.2",
    "frete grátis em Campo Grande?": "5.1",
    "como rastreio meu pedido?": "5.3",
    "tem Black Friday?": "6.1",
    "o pix tem desconto em produto em promoção?": "6.2",
    "qual o prazo da garantia legal?": "8.1",
    "a garantia cobre queda?": "8.3",
    "quero apagar meus dados": "9",
    "vocês vendem cordas e amplificador?": "1",
}


@pytest.mark.parametrize(("question", "section"), CASES.items())
def test_expected_section_in_top3(retriever, question, section):
    assert section in [c.section for c in retriever.search(question)]


def test_returns_three_chunks_with_text(retriever):
    found = retriever.search("política de trocas")
    assert len(found) == 3
    assert all(c.text and c.title for c in found)
    assert found[0].score >= found[1].score >= found[2].score


def test_blank_question_returns_nothing(retriever):
    assert retriever.search("   ") == []


def test_index_build_is_idempotent(chroma_dir, retriever):
    chunks, rebuilt = ingest.build_index(chroma_dir=chroma_dir)
    assert rebuilt is False
    assert len(chunks) == 25
    _, rebuilt = ingest.build_index(chroma_dir=chroma_dir, force=True)
    assert rebuilt is True


def test_retriever_survives_index_rebuild(chroma_dir, retriever):
    ingest.build_index(chroma_dir=chroma_dir, force=True)  # old collection handle is gone
    assert retriever.search("boquilha")[0].section == "4.4"


def test_search_policies_tool(retriever, monkeypatch):
    monkeypatch.setattr(policy_retriever, "get_retriever", lambda: retriever)
    result = search_policies.invoke({"question": "prazo para devolver por arrependimento"})
    assert result["ok"] is True
    assert len(result["chunks"]) == 3
    assert result["chunks"][0]["section"] == "4.1"
    assert set(result["chunks"][0]) == {"section", "title", "text"}


def test_search_policies_blank_question_is_friendly_error(retriever, monkeypatch):
    monkeypatch.setattr(policy_retriever, "get_retriever", lambda: retriever)
    result = search_policies.invoke({"question": " "})
    assert result["ok"] is False and "dúvida" in result["error"]


def test_policy_tool_docstring_routes_away_from_data_questions():
    description = POLICY_TOOLS[0].description
    assert "NÃO use para preço" in description
    assert "calculate_payment" in description and "get_order" in description
