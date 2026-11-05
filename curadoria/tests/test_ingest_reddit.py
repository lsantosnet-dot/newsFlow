"""Testes do adaptador `reddit` — sem rede (requests.get é substituído)."""

from __future__ import annotations

import ingest
import pytest
import requests


def _post(**overrides):
    base = {
        "title": "Uma matéria técnica",
        "score": 300,
        "num_comments": 40,
        "created_utc": 1_760_000_000,
        "permalink": "/r/programming/comments/abc/uma_materia/",
        "url": "https://example.com/artigo",
        "is_self": False,
        "stickied": False,
        "over_18": False,
        "selftext": "",
    }
    base.update(overrides)
    return {"data": base}


class _FakeResponse:
    def __init__(self, children, status=200):
        self._children = children
        self.status = status

    def raise_for_status(self):
        if self.status >= 400:
            raise requests.HTTPError(f"{self.status} error")

    def json(self):
        return {"data": {"children": self._children}}


@pytest.fixture
def fake_get(monkeypatch):
    calls = []

    def install(children, status=200):
        def _get(url, params=None, headers=None, timeout=None):
            calls.append({"url": url, "params": params, "headers": headers})
            return _FakeResponse(children, status)

        monkeypatch.setattr(ingest.requests, "get", _get)
        return calls

    return install


def test_link_post_usa_a_url_externa(fake_get):
    fake_get([_post()])
    items = ingest.ingest_reddit({"type": "reddit", "subreddit": "programming", "name": "r/programming"})

    assert len(items) == 1
    assert items[0]["url"] == "https://example.com/artigo"
    assert items[0]["source"] == "r/programming"
    assert items[0]["published_at"].tzinfo is not None


def test_self_post_usa_permalink_e_texto(fake_get):
    fake_get([_post(is_self=True, url="https://www.reddit.com/r/x/y", selftext="Corpo do post")])
    items = ingest.ingest_reddit({"subreddit": "programming"})

    assert items[0]["url"] == "https://www.reddit.com/r/programming/comments/abc/uma_materia/"
    assert items[0]["raw_content"] == "Corpo do post"


def test_filtros_de_score_comentarios_fixado_nsfw_e_midia(fake_get):
    fake_get(
        [
            _post(title="ok"),
            _post(title="score baixo", score=3),
            _post(title="poucos comentarios", num_comments=0),
            _post(title="fixado", stickied=True),
            _post(title="nsfw", over_18=True),
            _post(title="imagem", url="https://i.redd.it/foo.png"),
            _post(title="video", is_video=True),
            _post(title="galeria", is_gallery=True, url="https://www.reddit.com/gallery/abc"),
        ]
    )
    items = ingest.ingest_reddit({"subreddit": "programming", "min_score": 100, "min_comments": 10})

    assert [i["title"] for i in items] == ["ok"]


def test_monta_a_requisicao_com_user_agent_proprio(fake_get):
    calls = fake_get([])
    ingest.ingest_reddit({"subreddit": "r/programming", "sort": "top", "time": "week", "limit": 500})

    call = calls[0]
    assert call["url"] == "https://www.reddit.com/r/programming/top.json"
    assert call["params"]["t"] == "week"
    assert call["params"]["limit"] == 100  # teto da API
    assert "newsflow" in call["headers"]["User-Agent"]


def test_sort_sem_top_nao_envia_janela_e_valores_invalidos_caem_no_default(fake_get):
    calls = fake_get([])
    ingest.ingest_reddit({"subreddit": "programming", "sort": "hot", "time": "day"})
    assert calls[0]["url"].endswith("/r/programming/hot.json")
    assert "t" not in calls[0]["params"]

    ingest.ingest_reddit({"subreddit": "programming", "sort": "bogus", "time": "bogus"})
    assert calls[1]["url"].endswith("/r/programming/top.json")
    assert calls[1]["params"]["t"] == "day"


@pytest.mark.parametrize("raw", [None, "", "a", "foo/bar", "../etc", "r/", "x y"])
def test_subreddit_invalido_e_ignorado_sem_rede(raw, fake_get):
    calls = fake_get([_post()])
    assert ingest.ingest_reddit({"subreddit": raw}) == []
    assert calls == []


@pytest.mark.parametrize("raw", ["programming", "r/programming", "/r/programming/", "rust+golang"])
def test_normaliza_nome_do_subreddit(raw):
    assert ingest._normalize_subreddit(raw) == raw.strip("/").removeprefix("r/")


def test_bloqueio_http_nao_derruba_a_rodada(fake_get):
    fake_get([], status=403)
    assert ingest.ingest_reddit({"subreddit": "programming"}) == []


def test_resposta_malformada_nao_derruba_a_rodada(monkeypatch):
    class _Bad:
        def raise_for_status(self):
            pass

        def json(self):
            return {"unexpected": True}

    monkeypatch.setattr(ingest.requests, "get", lambda *a, **k: _Bad())
    assert ingest.ingest_reddit({"subreddit": "programming"}) == []


def test_reddit_esta_desativado_no_registry(fake_get):
    """O adaptador existe, mas não está registrado: fontes reddit são ignoradas."""
    calls = fake_get([_post()])
    assert "reddit" not in ingest.SOURCE_ADAPTERS
    items = ingest.ingest_from_profile(
        {"name": "Reddit Dev", "sources": [{"type": "reddit", "subreddit": "programming"}]}
    )
    assert items == []
    assert calls == []  # nenhuma requisição ao Reddit


@pytest.mark.skipif("reddit" not in ingest.SOURCE_ADAPTERS, reason="Reddit desativado no registry")
def test_registrado_no_registry_e_roda_via_perfil(fake_get):
    assert ingest.SOURCE_ADAPTERS["reddit"] is ingest.ingest_reddit
    fake_get([_post()])
    items = ingest.ingest_from_profile(
        {"name": "Reddit Dev", "sources": [{"type": "reddit", "subreddit": "programming"}]}
    )
    assert len(items) == 1
