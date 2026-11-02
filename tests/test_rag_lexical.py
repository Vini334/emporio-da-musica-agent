"""BM25 half of the hybrid policy search."""

from emporio.rag.lexical import BM25, tokenize


def test_lexical_tokenizer_stems_and_drops_accents_and_stopwords():
    assert tokenize("Posso parcelar em quantas vezes?") == ["parce", "quant", "vezes"]
    assert tokenize("Parcelamento") == ["parce"]
    assert tokenize("Não há devolução") == ["nao", "ha", "devol"]


def test_bm25_ranks_exact_vocabulary_first():
    bm25 = BM25(["Boquilhas de sopro não são trocadas.", "Frete grátis acima de R$ 500,00."])
    scores = bm25.scores("vocês trocam boquilha?")
    assert scores[0] > 0 and scores[1] == 0
