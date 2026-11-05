"""Rate limit (429) do embedding: espera o tempo que a API pede e tenta de novo."""

from __future__ import annotations

import embeddings
import pytest
import semantic
from conftest import make_item

# Mensagem real do 429 por minuto do tier gratuito (trecho do log do Actions).
REAL_429 = (
    "429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded your current quota. "
    "Please retry in 11.677415039s.', 'status': 'RESOURCE_EXHAUSTED', 'details': [{'@type': "
    "'type.googleapis.com/google.rpc.RetryInfo', 'retryDelay': '11s'}]}}"
)


class RateLimitError(Exception):
    code = 429


@pytest.fixture(autouse=True)
def sleeps(monkeypatch):
    waited: list[float] = []
    monkeypatch.setattr(embeddings, "_sleep", waited.append)
    monkeypatch.delenv("EMBED_MAX_WAIT_SECONDS", raising=False)
    embeddings.reset_wait_budget()
    yield waited
    embeddings.reset_wait_budget()


def fail_first(n, exc, fake_gemini):
    """Faz as `n` primeiras chamadas de embedding levantarem `exc`."""
    original = fake_gemini.embed_handler

    def handler(texts):
        if len(fake_gemini.embed_calls) <= n:
            raise exc
        return original(texts)

    fake_gemini.embed_handler = handler


def test_tenta_de_novo_esperando_o_tempo_que_a_api_pede(fake_gemini, sleeps):
    fail_first(1, RateLimitError(REAL_429), fake_gemini)

    vectors = embeddings.embed_texts(["a", "b"])

    assert len(vectors) == 2
    assert len(fake_gemini.embed_calls) == 2  # uma falha + uma que deu certo
    assert sleeps == [pytest.approx(11.677415039 + embeddings.RETRY_MARGIN_SECONDS)]


def test_erro_que_nao_e_rate_limit_nao_espera_nem_repete(fake_gemini, sleeps):
    fail_first(5, RuntimeError("500 INTERNAL"), fake_gemini)

    with pytest.raises(RuntimeError):
        embeddings.embed_texts(["a"])

    assert len(fake_gemini.embed_calls) == 1
    assert sleeps == []


def test_desiste_depois_do_maximo_de_tentativas(fake_gemini, sleeps):
    fail_first(99, RateLimitError(REAL_429), fake_gemini)

    with pytest.raises(RateLimitError):
        embeddings.embed_texts(["a"])

    assert len(fake_gemini.embed_calls) == embeddings.EMBED_MAX_ATTEMPTS
    assert len(sleeps) == embeddings.EMBED_MAX_ATTEMPTS - 1


def test_sem_dica_na_mensagem_usa_backoff_exponencial(fake_gemini, sleeps):
    fail_first(2, RateLimitError("429 RESOURCE_EXHAUSTED"), fake_gemini)

    embeddings.embed_texts(["a"])

    margin = embeddings.RETRY_MARGIN_SECONDS
    assert sleeps == [15.0 + margin, 30.0 + margin]


def test_uma_espera_nunca_passa_do_teto(fake_gemini, sleeps):
    fail_first(1, RateLimitError("Please retry in 600s. RESOURCE_EXHAUSTED"), fake_gemini)

    embeddings.embed_texts(["a"])

    assert sleeps == [embeddings.MAX_SINGLE_WAIT_SECONDS]


def test_orcamento_de_espera_vale_para_a_rodada_inteira(fake_gemini, sleeps, monkeypatch):
    monkeypatch.setenv("EMBED_MAX_WAIT_SECONDS", "20")
    fail_first(99, RateLimitError(REAL_429), fake_gemini)  # cada espera: ~13.2s

    with pytest.raises(RateLimitError):
        embeddings.embed_texts(["a"])  # 1ª espera cabe (13.2s); a 2ª estouraria os 20s

    assert len(sleeps) == 1
    assert len(fake_gemini.embed_calls) == 2

    # Outro "perfil" na mesma rodada: o orçamento já foi gasto, falha sem esperar.
    with pytest.raises(RateLimitError):
        embeddings.embed_texts(["b"])
    assert len(sleeps) == 1


def test_dedupe_semantico_segue_depois_de_um_429_recuperado(fake_gemini, sleeps, monkeypatch):
    monkeypatch.setenv("SEMANTIC_DEDUPE_MODE", "on")
    monkeypatch.setattr(semantic, "find_nearest_article", lambda _p, _v: None)
    fail_first(1, RateLimitError(REAL_429), fake_gemini)
    items = [
        {**make_item("Selic sobe STORY:selic"), "title_hash": "h1"},
        {**make_item("Copom eleva juros STORY:selic"), "title_hash": "h2"},
    ]

    result = semantic.find_semantic_duplicates(items, {"id": "tech"})

    assert len(sleeps) == 1
    assert len(result.items) == 1 and result.merged == 1  # as duas viraram uma notícia


def test_dedupe_semantico_cai_sem_embeddings_se_o_orcamento_acabou(fake_gemini, sleeps, monkeypatch):
    monkeypatch.setenv("SEMANTIC_DEDUPE_MODE", "on")
    monkeypatch.setenv("EMBED_MAX_WAIT_SECONDS", "0")
    fail_first(99, RateLimitError(REAL_429), fake_gemini)
    items = [{**make_item("A STORY:x"), "title_hash": "h1"}, {**make_item("B STORY:x"), "title_hash": "h2"}]

    result = semantic.find_semantic_duplicates(items, {"id": "tech"})

    assert sleeps == []
    assert len(result.items) == 2  # segue sem dedupe, como antes da mudança
