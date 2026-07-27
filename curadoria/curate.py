"""Curadoria via Gemini: pontua relevância técnica e filtra clickbait/promocional.

Usa o SDK oficial `google-genai` com uma chave do Google AI Studio (tier gratuito).
"""

from __future__ import annotations

import os
import time

from google import genai
from google.genai import types
from pydantic import BaseModel

DEFAULT_MODEL = "gemini-3.1-flash-lite"
MAX_RETRIES = 3
INITIAL_BACKOFF_SECONDS = 2.0

SYSTEM_PROMPT = """\
Você é um editor técnico sênior especializado em engenharia de software.
Sua tarefa é avaliar notícias e decidir se elas merecem entrar em um feed pessoal \
de curadoria técnica prática, atribuindo um `relevance_score` de 0 a 100.

O público-alvo é um(a) engenheiro(a) de software do dia a dia — não um pesquisador \
acadêmico. Prefira conteúdo aplicável ao trabalho real de desenvolvimento em vez de \
pesquisa teórica ou papers densos.

CRITÉRIOS DE ELIMINAÇÃO (score deve ficar baixo, tipicamente abaixo de 40):
- Notícias especulativas sobre mercado financeiro, ações ou valuation de Big Techs.
- Artigos promocionais, releases de marketing ou títulos apelativos/clickbait \
  (ex: "X vai morrer?", "Isso vai mudar tudo", "Você não vai acreditar").
- Notícias repetidas, superficiais ou que só reagem a um anúncio sem profundidade técnica.
- Papers acadêmicos, resultados de pesquisa ou conteúdo excessivamente teórico/matemático \
  sem aplicação prática direta para quem programa no dia a dia.

CRITÉRIOS DE APROVAÇÃO (score > 75):
- Boas práticas de engenharia de software: arquitetura, padrões de projeto, testes, \
  code review, DevOps, observabilidade.
- Projetos, bibliotecas e ferramentas open source relevantes no GitHub (lançamentos, \
  novas versões, casos de uso interessantes).
- Discussões técnicas entre desenvolvedores (threads da Hacker News, posts do Dev.to) \
  sobre linguagens, frameworks, ferramentas e decisões de engenharia.
- Novidades concretas sobre linguagens de programação (releases, features, comparações \
  práticas) — não só as de alta performance, qualquer linguagem popular conta.
- Uso prático de IA/LLMs no dia a dia de desenvolvimento (ex: integração em produtos, \
  ferramentas de produtividade), evitando pesquisa teórica sobre os modelos em si.

Para cada item, retorne um objeto JSON com:
- title: título limpo, sem clickbait, em português.
- technical_summary: resumo objetivo com os 3 pontos técnicos principais (TL;DR), em português.
- relevance_score: inteiro de 0 a 100.
- tags: lista curta de tags temáticas em maiúsculas (ex: ["SYSTEM DESIGN", "RAG"]).
- is_quality_approved: true somente se relevance_score > 75 e o conteúdo passar nos \
  critérios de aprovação.
- tts_text: texto em português, otimizado para leitura em voz alta por um motor de TTS — \
  frases curtas, sem trechos de código, sem URLs, sem markdown e sem siglas não explicadas.
"""


class ArticleCuration(BaseModel):
    title: str
    technical_summary: str
    relevance_score: int
    tags: list[str]
    is_quality_approved: bool
    tts_text: str


_client: genai.Client | None = None


def get_client() -> genai.Client:
    global _client
    if _client is None:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY não definida no ambiente.")
        _client = genai.Client(api_key=api_key)
    return _client


def _model_name() -> str:
    return os.environ.get("GEMINI_MODEL", DEFAULT_MODEL)


def _request_delay_seconds() -> float:
    # Tier gratuito do Gemini: 15 req/min => ~4s entre chamadas para não estourar o limite.
    try:
        return float(os.environ.get("GEMINI_REQUEST_DELAY_SECONDS", 4.0))
    except ValueError:
        return 4.0


def _build_prompt(item: dict) -> str:
    return (
        f"Fonte: {item['source']}\n"
        f"Título original: {item['title']}\n"
        f"URL: {item['url']}\n"
        f"Conteúdo/resumo bruto:\n{item['raw_content'][:6000]}\n"
    )


def curate_item(item: dict) -> ArticleCuration | None:
    """Chama o Gemini para curar um item, com retry exponencial em caso de falha."""
    client = get_client()
    prompt = _build_prompt(item)

    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = client.models.generate_content(
                model=_model_name(),
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    response_mime_type="application/json",
                    response_schema=ArticleCuration,
                ),
            )
            parsed = response.parsed
            if parsed is None:
                raise ValueError(f"Resposta do Gemini sem JSON parseável: {response.text!r}")
            return parsed
        except Exception as exc:  # noqa: BLE001 - queremos capturar qualquer falha de API/parsing
            last_error = exc
            wait = INITIAL_BACKOFF_SECONDS * (2 ** (attempt - 1))
            print(
                f"[curate] Tentativa {attempt}/{MAX_RETRIES} falhou para {item['title']!r}: "
                f"{exc}. Aguardando {wait:.1f}s antes de retry."
            )
            if attempt < MAX_RETRIES:
                time.sleep(wait)

    print(f"[curate] Descartado após {MAX_RETRIES} tentativas: {item['title']!r} ({last_error})")
    return None


def curate_with_gemini(items: list[dict]) -> list[dict]:
    """Roda a curadoria do Gemini sobre uma lista de itens ingeridos.

    Retorna os itens originais enriquecidos com o resultado da curadoria em `curation`
    (ou `None` se a curadoria falhou após os retries).
    """
    delay = _request_delay_seconds()
    curated: list[dict] = []

    for idx, item in enumerate(items):
        result = curate_item(item)
        curated.append({**item, "curation": result})

        if idx < len(items) - 1:
            time.sleep(delay)

    return curated


def filter_approved(curated_items: list[dict]) -> list[dict]:
    """Filtra apenas os itens aprovados pela curadoria (is_quality_approved=True)."""
    return [item for item in curated_items if item.get("curation") and item["curation"].is_quality_approved]
