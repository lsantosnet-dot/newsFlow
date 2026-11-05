"""Embeddings: transformar texto em vetores e medir o quanto dois textos se parecem.

Um *embedding* é uma lista de números (aqui, 256 floats) que representa o
significado de um texto. O modelo de embedding foi treinado para que textos
com significado parecido virem vetores que apontam para a mesma direção, mesmo
quando não compartilham nenhuma palavra:

    "Banco Central sobe a Selic para 11%"       ┐ vetores quase paralelos
    "Copom eleva juros básicos em 0,5 ponto"    ┘ (similaridade ~0.9)
    "Apple lança novo iPhone"                     vetor em outra direção (~0.5)

A medida usada é a *similaridade de cosseno*: o cosseno do ângulo entre os dois
vetores. 1.0 = mesma direção (mesmo assunto), perto de 0 = nada a ver. Como os
vetores são normalizados (comprimento 1) logo depois de chegarem da API, o
cosseno vira só o produto escalar — soma de a[i] * b[i].

Tudo aqui é Python puro de propósito (sem numpy): com algumas centenas de
vetores por perfil a conta é instantânea e dá para ler cada passo. Com milhões
de vetores, aí sim você trocaria por numpy ou por busca vetorial no próprio
banco (ver `curadoria/EMBEDDINGS.md`).
"""

from __future__ import annotations

import math
import os
import re
import time

from google.genai import types

import curate

DEFAULT_EMBEDDING_MODEL = "gemini-embedding-001"

# O modelo gera 3072 dimensões, mas aceita truncar (Matryoshka representation
# learning: as primeiras dimensões carregam a maior parte do significado).
# 256 é bem mais barato de guardar no Firestore e sobra para "é a mesma notícia?".
EMBEDDING_DIMENSIONS = 256

# Limite da API: até 100 textos por chamada de embed_content.
EMBED_BATCH_SIZE = 100

# Quanto do conteúdo entra no texto embeddado. Título + começo da matéria (o
# lide) identificam o fato; o resto do texto só diluiria o vetor.
EMBEDDING_CONTENT_CHARS = 400


def model_name() -> str:
    return os.environ.get("EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL)


def embedding_text(item: dict) -> str:
    """Texto que representa um item de notícia para o embedding: título + lide."""
    content = " ".join(str(item.get("raw_content") or "").split())
    return f"{item['title']}\n{content[:EMBEDDING_CONTENT_CHARS]}".strip()


def normalize(vector: list[float]) -> list[float]:
    """Divide o vetor pelo próprio comprimento, para ele passar a ter norma 1.

    Necessário porque, ao pedir menos de 3072 dimensões, a API devolve vetores
    truncados que não estão mais normalizados.
    """
    norm = math.sqrt(sum(x * x for x in vector))
    if norm == 0:
        return list(vector)
    return [x / norm for x in vector]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosseno do ângulo entre `a` e `b` (os dois já normalizados): o produto escalar."""
    return sum(x * y for x, y in zip(a, b))


def _embed_batch(texts: list[str]) -> list[list[float]]:
    """Uma chamada à API de embeddings do Gemini. Levanta exceção em erro de API."""
    response = curate.get_client().models.embed_content(
        model=model_name(),
        contents=texts,
        config=types.EmbedContentConfig(
            # Pede vetores otimizados para comparar textos entre si (e não para
            # busca pergunta → documento, que seria RETRIEVAL_QUERY/DOCUMENT).
            task_type="SEMANTIC_SIMILARITY",
            output_dimensionality=EMBEDDING_DIMENSIONS,
        ),
    )
    embeddings = response.embeddings or []
    if len(embeddings) != len(texts):
        raise RuntimeError(f"API devolveu {len(embeddings)} embeddings para {len(texts)} textos")
    return [list(e.values or []) for e in embeddings]


# --------------------------------------------------------------------------- #
# Limite de taxa (429) do tier gratuito
# --------------------------------------------------------------------------- #
#
# O tier gratuito limita as chamadas de embedding POR MINUTO (100). Com vários
# perfis rodando em sequência, a rajada estoura o limite e o erro traz o tempo
# de espera ("Please retry in 11.6s"). Em vez de desistir do dedupe semântico na
# primeira falha, esperamos o tempo que a própria API pede e tentamos de novo.

EMBED_MAX_ATTEMPTS = 4

# Teto de espera por rate limit em UMA execução do pipeline (todos os perfis
# somados). Passou disso, desiste e segue sem dedupe semântico (como antes), para
# a rodada não estourar o timeout do job.
DEFAULT_MAX_WAIT_SECONDS = 180.0

# Uma espera isolada nunca passa disso (a janela do limite é de 1 minuto).
MAX_SINGLE_WAIT_SECONDS = 65.0

# Folga somada ao tempo que a API sugere, porque a contagem é por janela móvel.
RETRY_MARGIN_SECONDS = 1.5

_RETRY_IN_RE = re.compile(r"retry in ([0-9]+(?:\.[0-9]+)?)s", re.IGNORECASE)
_RETRY_DELAY_RE = re.compile(r"'retryDelay':\s*'([0-9]+(?:\.[0-9]+)?)s'")

_sleep = time.sleep
_waited_seconds = 0.0


def _max_wait_seconds() -> float:
    try:
        return float(os.environ.get("EMBED_MAX_WAIT_SECONDS", DEFAULT_MAX_WAIT_SECONDS))
    except ValueError:
        return DEFAULT_MAX_WAIT_SECONDS


def is_rate_limited(exc: BaseException) -> bool:
    """429 / RESOURCE_EXHAUSTED do Gemini."""
    if getattr(exc, "code", None) == 429:
        return True
    text = str(exc)
    return "RESOURCE_EXHAUSTED" in text or text.lstrip().startswith("429")


def retry_delay_seconds(exc: BaseException, attempt: int) -> float:
    """Quanto esperar: o que a API pediu; se não disser, backoff exponencial."""
    text = str(exc)
    match = _RETRY_IN_RE.search(text) or _RETRY_DELAY_RE.search(text)
    suggested = float(match.group(1)) if match else 15.0 * (2 ** (attempt - 1))
    return min(suggested + RETRY_MARGIN_SECONDS, MAX_SINGLE_WAIT_SECONDS)


def reset_wait_budget() -> None:
    """Zera o tempo já gasto esperando rate limit (usado nos testes)."""
    global _waited_seconds
    _waited_seconds = 0.0


def _embed_batch_with_retry(texts: list[str]) -> list[list[float]]:
    """`_embed_batch` esperando e repetindo quando o limite por minuto estoura."""
    global _waited_seconds
    attempt = 1
    while True:
        try:
            return _embed_batch(texts)
        except Exception as exc:  # noqa: BLE001
            if not is_rate_limited(exc) or attempt >= EMBED_MAX_ATTEMPTS:
                raise
            wait = retry_delay_seconds(exc, attempt)
            if _waited_seconds + wait > _max_wait_seconds():
                print(
                    f"[embeddings] Limite de taxa e o tempo máximo de espera "
                    f"({_max_wait_seconds():.0f}s) já foi gasto — desistindo desta rodada."
                )
                raise
            print(
                f"[embeddings] Limite por minuto atingido (tentativa {attempt}/{EMBED_MAX_ATTEMPTS}). "
                f"Aguardando {wait:.1f}s."
            )
            _sleep(wait)
            _waited_seconds += wait
            attempt += 1


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embeddings normalizados para cada texto, na mesma ordem, em lotes de 100."""
    vectors: list[list[float]] = []
    for start in range(0, len(texts), EMBED_BATCH_SIZE):
        vectors.extend(
            normalize(v) for v in _embed_batch_with_retry(texts[start : start + EMBED_BATCH_SIZE])
        )
    return vectors
