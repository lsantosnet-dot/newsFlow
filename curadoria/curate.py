"""Curadoria via Gemini: pontua relevância e filtra clickbait/conteúdo promocional.

O prompt não é mais fixo: o esqueleto (formato de saída, regras de TTS, idioma)
é constante, e as partes variáveis — persona, critérios de aprovação/rejeição,
score mínimo — vêm do perfil de curadoria ativo no Firestore.

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

# Teto de tempo para a curadoria via Gemini. O job do GitHub Actions tem um
# timeout de 15 min (ver .github/workflows/curadoria.yml); quando o Gemini
# está com alta demanda (503 UNAVAILABLE), os retries com backoff por item
# podem inflar o tempo total muito além do normal (~4s/item). Esse orçamento
# interrompe a curadoria antes do timeout do job, para que o pipeline ainda
# consiga salvar o que já foi aprovado em vez de ser morto sem aviso.
DEFAULT_CURATION_TIME_BUDGET_SECONDS = 600.0

DEFAULT_MIN_SCORE = 75

# Fallbacks usados quando o perfil não define persona/critérios — mantêm o
# comportamento original (curadoria de engenharia de software).
DEFAULT_PERSONA = (
    "Você é um editor técnico sênior especializado em engenharia de software. "
    "O público-alvo é um(a) engenheiro(a) de software do dia a dia."
)
DEFAULT_APPROVE_CRITERIA = [
    "Boas práticas de engenharia: arquitetura, padrões de projeto, testes, DevOps, observabilidade.",
    "Projetos, bibliotecas e ferramentas open source relevantes.",
    "Discussões técnicas entre desenvolvedores sobre linguagens, frameworks e decisões de engenharia.",
    "Uso prático de IA/LLMs no dia a dia de desenvolvimento.",
]
DEFAULT_REJECT_CRITERIA = [
    "Notícias especulativas sobre mercado financeiro, ações ou valuation de Big Techs.",
    "Artigos promocionais, releases de marketing ou títulos apelativos/clickbait.",
    "Notícias repetidas, superficiais ou que só reagem a um anúncio sem profundidade técnica.",
    "Papers acadêmicos ou conteúdo excessivamente teórico sem aplicação prática.",
]


class ArticleCuration(BaseModel):
    title: str
    technical_summary: str
    relevance_score: int
    tags: list[str]
    is_quality_approved: bool
    tts_text: str


def _format_criteria(criteria: list[str] | None, fallback: list[str]) -> str:
    items = criteria if criteria else fallback
    return "\n".join(f"- {item}" for item in items)


def build_system_prompt(profile: dict) -> str:
    """Monta o system prompt a partir da config de curadoria do perfil.

    O esqueleto (contrato de saída JSON e regras de TTS) é fixo de propósito:
    é o que garante que a resposta continue parseável, independentemente do que
    o usuário editar no app.
    """
    curation = profile.get("curation") or {}

    persona = (curation.get("persona") or DEFAULT_PERSONA).strip()
    min_score = int(curation.get("min_score") or DEFAULT_MIN_SCORE)
    approve = _format_criteria(curation.get("approve_criteria"), DEFAULT_APPROVE_CRITERIA)
    reject = _format_criteria(curation.get("reject_criteria"), DEFAULT_REJECT_CRITERIA)
    extra = (curation.get("extra_instructions") or "").strip()

    # Rejeição usa uma margem abaixo do corte para dar ao modelo uma faixa clara
    # de "claramente ruim", em vez de empilhar tudo logo abaixo do min_score.
    reject_ceiling = max(10, min_score - 35)

    prompt = f"""\
{persona}

Sua tarefa é avaliar notícias e decidir se elas merecem entrar em um feed pessoal \
de curadoria, atribuindo um `relevance_score` de 0 a 100.

CRITÉRIOS DE ELIMINAÇÃO (score deve ficar baixo, tipicamente abaixo de {reject_ceiling}):
{reject}

CRITÉRIOS DE APROVAÇÃO (score > {min_score}):
{approve}
"""

    if extra:
        prompt += f"\nINSTRUÇÕES ADICIONAIS:\n{extra}\n"

    prompt += f"""
Para cada item, retorne um objeto JSON com:
- title: título limpo, sem clickbait, em português.
- technical_summary: resumo objetivo com os 3 pontos principais (TL;DR), em português.
- relevance_score: inteiro de 0 a 100.
- tags: lista curta de tags temáticas em maiúsculas (ex: ["SYSTEM DESIGN", "RAG"]).
- is_quality_approved: true somente se relevance_score > {min_score} e o conteúdo \
passar nos critérios de aprovação.
- tts_text: texto em português, otimizado para leitura em voz alta por um motor de TTS — \
frases curtas, sem trechos de código, sem URLs, sem markdown e sem siglas não explicadas.
"""
    return prompt


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


def _curation_time_budget_seconds() -> float:
    try:
        return float(os.environ.get("CURATION_TIME_BUDGET_SECONDS", DEFAULT_CURATION_TIME_BUDGET_SECONDS))
    except ValueError:
        return DEFAULT_CURATION_TIME_BUDGET_SECONDS


def _build_prompt(item: dict) -> str:
    return (
        f"Fonte: {item['source']}\n"
        f"Título original: {item['title']}\n"
        f"URL: {item['url']}\n"
        f"Conteúdo/resumo bruto:\n{item['raw_content'][:6000]}\n"
    )


def curate_item(item: dict, system_prompt: str) -> ArticleCuration | None:
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
                    system_instruction=system_prompt,
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


def curate_with_gemini(items: list[dict], profile: dict) -> list[dict]:
    """Roda a curadoria do Gemini sobre os itens ingeridos, usando o prompt do perfil.

    Retorna os itens originais enriquecidos com o resultado da curadoria em `curation`
    (ou `None` se a curadoria falhou após os retries). Para de processar itens novos
    se o orçamento de tempo estourar (ver `DEFAULT_CURATION_TIME_BUDGET_SECONDS`) — os
    itens restantes ficam de fora desta rodada e voltam a ser candidatos na próxima
    ingestão, em vez de arriscar o job inteiro ser morto pelo timeout do GitHub Actions.
    """
    system_prompt = build_system_prompt(profile)
    delay = _request_delay_seconds()
    budget = _curation_time_budget_seconds()
    started_at = time.monotonic()
    curated: list[dict] = []

    for idx, item in enumerate(items):
        elapsed = time.monotonic() - started_at
        if elapsed > budget:
            skipped = len(items) - idx
            print(
                f"[curate] Orçamento de tempo estourado ({elapsed:.0f}s > {budget:.0f}s) — "
                f"{skipped} itens ficam para a próxima rodada."
            )
            break

        result = curate_item(item, system_prompt)
        curated.append({**item, "curation": result})

        if idx < len(items) - 1:
            time.sleep(delay)

    return curated


def filter_approved(curated_items: list[dict]) -> list[dict]:
    """Filtra apenas os itens aprovados pela curadoria (is_quality_approved=True)."""
    return [item for item in curated_items if item.get("curation") and item["curation"].is_quality_approved]
