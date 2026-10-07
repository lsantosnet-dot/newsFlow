"""Curadoria via Gemini: pontua relevância e filtra clickbait/conteúdo promocional.

O prompt não é mais fixo: o esqueleto (formato de saída, regras de TTS, idioma)
é constante, e as partes variáveis — persona, critérios de aprovação/rejeição,
score mínimo — vêm do perfil de curadoria no Firestore.

Os itens vão em lote (CURATION_BATCH_SIZE por chamada) para economizar a cota
diária de requisições; cada item continua sendo avaliado e devolvido no mesmo
formato de antes.

Usa o SDK oficial `google-genai` com uma chave do Google AI Studio (tier gratuito).
"""

from __future__ import annotations

import json
import os
import time

from google import genai
from google.genai import types
from pydantic import BaseModel, ValidationError

DEFAULT_MODEL = "gemini-3.1-flash-lite"
MAX_RETRIES = 3
INITIAL_BACKOFF_SECONDS = 2.0

# Teto de tempo para a curadoria via Gemini. O job do GitHub Actions tem um
# timeout de 15 min (ver .github/workflows/curadoria.yml); quando o Gemini
# está com alta demanda (503 UNAVAILABLE), os retries com backoff por item
# podem inflar o tempo total muito além do normal (~4s/item). Esse orçamento
# interrompe a curadoria antes do timeout do job, para que o pipeline ainda
# consiga salvar o que já foi aprovado em vez de ser morto sem aviso.
#
# Com a curadoria multi-perfil, esse orçamento é compartilhado por todos os
# perfis de uma execução (ver main.py).
DEFAULT_CURATION_TIME_BUDGET_SECONDS = 600.0

# Itens por chamada ao Gemini. Cada chamada leva o system prompt do perfil uma
# vez só, então agrupar itens corta o número de requisições/dia (a cota que
# realmente aperta no tier gratuito) sem mudar os critérios.
DEFAULT_BATCH_SIZE = 8

# Quanto do conteúdo bruto de cada item vai no prompt. A maioria dos feeds traz
# só um resumo bem menor que isso; o corte limita os itens com corpo inteiro.
DEFAULT_RAW_CONTENT_MAX_CHARS = 1500

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


class BatchItemCuration(BaseModel):
    """Saída por item na curadoria em lote: `index` (1-based) + os campos de ArticleCuration.

    `index` vem primeiro para o modelo ancorar cada objeto ao item antes de escrever o resto.
    """

    index: int
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


def curation_time_budget_seconds() -> float:
    try:
        return float(os.environ.get("CURATION_TIME_BUDGET_SECONDS", DEFAULT_CURATION_TIME_BUDGET_SECONDS))
    except ValueError:
        return DEFAULT_CURATION_TIME_BUDGET_SECONDS


def _raw_content_max_chars() -> int:
    try:
        return max(200, int(os.environ.get("CURATION_RAW_CONTENT_MAX_CHARS", DEFAULT_RAW_CONTENT_MAX_CHARS)))
    except ValueError:
        return DEFAULT_RAW_CONTENT_MAX_CHARS


def _batch_size() -> int:
    try:
        return max(1, int(os.environ.get("CURATION_BATCH_SIZE", DEFAULT_BATCH_SIZE)))
    except ValueError:
        return DEFAULT_BATCH_SIZE


def build_batch_system_prompt(profile: dict) -> str:
    """System prompt do perfil + o contrato de saída em lote.

    O texto do perfil (persona, critérios, min_score, formato de cada campo) é
    exatamente o de `build_system_prompt`; só é acrescentado como os vários
    itens chegam e voltam numa única chamada.
    """
    return build_system_prompt(profile) + """
FORMATO EM LOTE:
Você receberá vários itens numerados (ITEM 1, ITEM 2, ...). Avalie cada item de \
forma independente, com os mesmos critérios, como se fosse o único.
Retorne uma lista JSON com exatamente um objeto por item, incluindo o campo \
`index` com o número do item correspondente.
Para itens NÃO aprovados (is_quality_approved false), deixe technical_summary e \
tts_text como string vazia — eles não serão publicados.
"""


def build_batch_prompt(items: list[dict], max_chars: int | None = None) -> str:
    """Monta o conteúdo da chamada: os itens numerados a partir de 1."""
    max_chars = max_chars if max_chars is not None else _raw_content_max_chars()
    parts = []
    for number, item in enumerate(items, start=1):
        parts.append(
            f"### ITEM {number}\n"
            f"Fonte: {item['source']}\n"
            f"Título original: {item['title']}\n"
            f"URL: {item['url']}\n"
            f"Conteúdo/resumo bruto:\n{(item.get('raw_content') or '')[:max_chars]}\n"
        )
    return "\n".join(parts)


class InvalidBatchResponse(ValueError):
    """A resposta veio sem JSON utilizável (vazia, truncada ou fora do formato)."""


def parse_batch_response(text: str | None, item_count: int) -> dict[int, ArticleCuration]:
    """Converte a resposta do lote em {posição 0-based: ArticleCuration}.

    Cada objeto é validado isoladamente: um objeto ruim (índice fora da faixa,
    campo faltando, aprovado sem resumo/TTS) é descartado sem invalidar os
    outros — os itens sem resposta válida são reprocessados por quem chamou.
    Levanta `InvalidBatchResponse` só quando não há uma lista JSON utilizável.
    """
    if not text:
        raise InvalidBatchResponse("resposta vazia")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise InvalidBatchResponse(f"JSON inválido ({exc})") from exc

    if isinstance(data, dict):
        data = data.get("items") or data.get("results")
    if not isinstance(data, list):
        raise InvalidBatchResponse(f"esperava uma lista JSON, veio {type(data).__name__}")

    results: dict[int, ArticleCuration] = {}
    for raw in data:
        try:
            entry = BatchItemCuration.model_validate(raw)
        except ValidationError:
            continue
        position = entry.index - 1
        if not 0 <= position < item_count or position in results:
            continue
        if entry.is_quality_approved and not (entry.technical_summary.strip() and entry.tts_text.strip()):
            # Aprovado precisa do formato completo de hoje; reprocessa.
            continue
        results[position] = ArticleCuration(**entry.model_dump(exclude={"index"}))
    return results


class _RateLimiter:
    """Garante o intervalo mínimo entre chamadas ao Gemini (GEMINI_REQUEST_DELAY_SECONDS).

    Compartilhado entre todos os perfis da execução: o limite por minuto é da
    chave, não do perfil.
    """

    def __init__(self) -> None:
        self._last_call: float | None = None

    def wait(self) -> None:
        delay = _request_delay_seconds()
        if self._last_call is not None:
            remaining = delay - (time.monotonic() - self._last_call)
            if remaining > 0:
                _sleep(remaining)
        self._last_call = time.monotonic()


_sleep = time.sleep
_rate_limiter = _RateLimiter()


def _request_batch(items: list[dict], system_prompt: str) -> dict[int, ArticleCuration]:
    """Uma chamada ao Gemini para um lote. Levanta exceção em erro de API/JSON."""
    _rate_limiter.wait()
    response = get_client().models.generate_content(
        model=_model_name(),
        contents=build_batch_prompt(items),
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            response_mime_type="application/json",
            response_schema=list[BatchItemCuration],
        ),
    )
    return parse_batch_response(response.text, len(items))


def _deadline_passed(deadline: float | None) -> bool:
    return deadline is not None and time.monotonic() > deadline


def curate_batch(
    items: list[dict],
    system_prompt: str,
    deadline: float | None = None,
) -> list[ArticleCuration | None]:
    """Cura um lote, reprocessando só os itens que ficaram sem resposta válida.

    - Erro de API (ex.: 503): espera com backoff exponencial e repete o lote pendente.
    - Resposta sem JSON utilizável (ex.: truncada): divide o lote pendente ao meio.
    - Índices faltando/inválidos: repete só os itens faltantes, num lote menor.

    Cada grupo de itens tem até MAX_RETRIES tentativas. Retorna uma curadoria por
    item, na mesma ordem; `None` = falhou (o item não vira `seen` e volta no
    próximo ciclo).
    """
    results: dict[int, ArticleCuration] = {}
    # Fila de (posições pendentes, tentativa).
    queue: list[tuple[list[int], int]] = [(list(range(len(items))), 1)]

    while queue:
        positions, attempt = queue.pop(0)
        if _deadline_passed(deadline):
            print(f"[curate] Orçamento de tempo estourado — {len(positions)} itens do lote ficam para depois.")
            continue

        subset = [items[i] for i in positions]
        try:
            got = _request_batch(subset, system_prompt)
        except Exception as exc:  # noqa: BLE001 - queremos capturar qualquer falha de API/parsing
            if attempt >= MAX_RETRIES:
                print(
                    f"[curate] Descartados após {MAX_RETRIES} tentativas: {len(positions)} itens "
                    f"({exc}): {[items[i]['title'] for i in positions]!r}"
                )
                continue
            if isinstance(exc, InvalidBatchResponse) and len(positions) > 1:
                half = len(positions) // 2
                print(
                    f"[curate] Tentativa {attempt}/{MAX_RETRIES}: resposta inválida para lote de "
                    f"{len(positions)} itens ({exc}). Dividindo em lotes menores."
                )
                queue[:0] = [(positions[:half], attempt + 1), (positions[half:], attempt + 1)]
                continue
            wait = INITIAL_BACKOFF_SECONDS * (2 ** (attempt - 1))
            print(
                f"[curate] Tentativa {attempt}/{MAX_RETRIES} falhou para lote de {len(positions)} itens: "
                f"{exc}. Aguardando {wait:.1f}s antes de retry."
            )
            _sleep(wait)
            queue.insert(0, (positions, attempt + 1))
            continue

        for local, curation in got.items():
            results[positions[local]] = curation

        missing = [pos for pos in positions if pos not in results]
        if not missing:
            continue
        if attempt >= MAX_RETRIES:
            print(
                f"[curate] Sem resposta válida após {MAX_RETRIES} tentativas para "
                f"{[items[i]['title'] for i in missing]!r}"
            )
            continue
        print(
            f"[curate] Tentativa {attempt}/{MAX_RETRIES}: {len(missing)} de {len(positions)} itens "
            f"sem resposta válida. Reprocessando só esses."
        )
        queue.insert(0, (missing, attempt + 1))

    return [results.get(i) for i in range(len(items))]


def curate_with_gemini(items: list[dict], profile: dict, deadline: float | None = None) -> list[dict]:
    """Roda a curadoria do Gemini sobre os itens, em lotes, com o prompt do perfil.

    Retorna os itens enriquecidos com `curation` (ou `None` se a curadoria falhou
    após os retries). `deadline` (time.monotonic) é o orçamento de tempo
    compartilhado entre todos os perfis da execução; sem ele, usa
    CURATION_TIME_BUDGET_SECONDS a partir de agora. Quando estoura, os lotes
    restantes ficam de fora (não aparecem na lista retornada) e voltam a ser
    candidatos no próximo ciclo, em vez de arriscar o job inteiro ser morto pelo
    timeout do GitHub Actions.
    """
    if deadline is None:
        deadline = time.monotonic() + curation_time_budget_seconds()

    system_prompt = build_batch_system_prompt(profile)
    size = _batch_size()
    curated: list[dict] = []

    for start in range(0, len(items), size):
        if _deadline_passed(deadline):
            print(
                f"[curate] Orçamento de tempo estourado — {len(items) - start} itens ficam para a próxima rodada."
            )
            break
        chunk = items[start : start + size]
        for item, curation in zip(chunk, curate_batch(chunk, system_prompt, deadline)):
            curated.append({**item, "curation": curation})

    return curated


def filter_approved(curated_items: list[dict]) -> list[dict]:
    """Filtra apenas os itens aprovados pela curadoria (is_quality_approved=True)."""
    return [item for item in curated_items if item.get("curation") and item["curation"].is_quality_approved]
