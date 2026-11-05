"""Testes do adaptador `github` — sem rede (requests.get é substituído)."""

from __future__ import annotations

import ingest
import pytest
import requests


def _repo(**overrides):
    base = {
        "full_name": "dono/projeto",
        "description": "Um painel para Kubernetes",
        "html_url": "https://github.com/dono/projeto",
        "created_at": "2026-11-01T10:00:00Z",
        "stargazers_count": 420,
        "language": "Go",
        "topics": ["kubernetes", "cli"],
        "homepage": "",
        "archived": False,
        "fork": False,
    }
    base.update(overrides)
    return base


class _Resp:
    def __init__(self, payload=None, text="", status=200):
        self._payload, self.text, self.status_code = payload, text, status

    def raise_for_status(self):
        if self.status_code >= 400:
            err = requests.HTTPError(f"{self.status_code} error")
            err.response = self
            raise err

    def json(self):
        return self._payload


@pytest.fixture
def fake_get(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    calls = []

    def install(search_items=None, readme="", status=200, per_query=None):
        def _get(url, params=None, headers=None, timeout=None):
            calls.append({"url": url, "params": params, "headers": headers})
            if url.endswith("/readme"):
                return _Resp(text=readme)
            if per_query is not None:
                return _Resp({"items": per_query.pop(0)})
            return _Resp({"items": search_items or []}, status=status)

        monkeypatch.setattr(ingest.requests, "get", _get)
        return calls

    return install


def test_busca_repos_novos_e_monta_o_item(fake_get):
    calls = fake_get([_repo()])
    items = ingest.ingest_github(
        {"type": "github", "name": "GitHub: K8s", "queries": ["topic:kubernetes"], "days": 14, "min_stars": 30}
    )

    assert len(items) == 1
    item = items[0]
    assert item["source"] == "GitHub: K8s"
    assert item["title"] == "dono/projeto: Um painel para Kubernetes"
    assert item["url"] == "https://github.com/dono/projeto"
    assert "Linguagem: Go" in item["raw_content"] and "Tópicos: kubernetes, cli" in item["raw_content"]
    assert item["published_at"].year == 2026

    q = calls[0]["params"]["q"]
    assert "created:>" in q and "stars:>=30" in q and q.endswith("topic:kubernetes")
    assert calls[0]["params"]["sort"] == "stars"
    assert "newsflow" in calls[0]["headers"]["User-Agent"]


def test_descarta_arquivados_forks_e_duplicados_entre_buscas(fake_get):
    fake_get(
        per_query=[
            [_repo(), _repo(full_name="a/arquivado", archived=True), _repo(full_name="a/fork", fork=True)],
            [_repo(), _repo(full_name="b/novo", html_url="https://github.com/b/novo")],
        ]
    )
    items = ingest.ingest_github({"queries": ["topic:a", "topic:b"]})
    assert [i["url"] for i in items] == ["https://github.com/dono/projeto", "https://github.com/b/novo"]


def test_sem_queries_faz_uma_busca_geral(fake_get):
    calls = fake_get([_repo()])
    ingest.ingest_github({"min_stars": 300})
    assert len(calls) == 1
    assert calls[0]["params"]["q"].endswith("stars:>=300")


def test_readme_so_com_token_e_sem_ruido(fake_get, monkeypatch):
    readme = "[![CI](x.svg)](http://ci) # Titulo <img src='a'> Texto  útil\n\n![logo](l.png) fim"
    calls = fake_get([_repo()], readme=readme)

    sem_token = ingest.ingest_github({"queries": ["topic:k"]})
    assert "README" not in sem_token[0]["raw_content"]
    assert not any(c["url"].endswith("/readme") for c in calls)

    monkeypatch.setenv("GITHUB_TOKEN", "t0k3n")
    com_token = ingest.ingest_github({"queries": ["topic:k"], "readme_chars": 100})
    raw = com_token[0]["raw_content"]
    assert "README: # Titulo Texto útil fim" in raw
    assert "CI" not in raw and "<img" not in raw and "logo" not in raw
    assert calls[-1]["headers"]["Accept"] == "application/vnd.github.raw+json"
    assert calls[-1]["headers"]["Authorization"] == "Bearer t0k3n"
    assert "Authorization" not in calls[0]["headers"]  # a primeira busca rodou sem token


def test_readme_respeita_o_limite_de_caracteres(fake_get, monkeypatch):
    fake_get([_repo()], readme="x" * 5000)
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    raw = ingest.ingest_github({"queries": ["topic:k"], "readme_chars": 50})[0]["raw_content"]
    assert raw.split("README: ")[1] == "x" * 50


def test_rate_limit_interrompe_as_buscas_seguintes(fake_get):
    calls = fake_get(status=403)
    assert ingest.ingest_github({"queries": ["topic:a", "topic:b", "topic:c"]}) == []
    assert len(calls) == 1


def test_resposta_malformada_nao_derruba_a_rodada(monkeypatch):
    monkeypatch.setattr(ingest.requests, "get", lambda *a, **k: _Resp({"inesperado": True}))
    assert ingest.ingest_github({"queries": ["topic:a"]}) == []


def test_registrado_no_registry_e_roda_via_perfil(fake_get):
    assert ingest.SOURCE_ADAPTERS["github"] is ingest.ingest_github
    fake_get([_repo()])
    items = ingest.ingest_from_profile(
        {"name": "Ideias", "sources": [{"type": "github", "queries": ["topic:kubernetes"]}]}
    )
    assert len(items) == 1
