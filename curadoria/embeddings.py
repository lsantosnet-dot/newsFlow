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


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embeddings normalizados para cada texto, na mesma ordem, em lotes de 100."""
    vectors: list[list[float]] = []
    for start in range(0, len(texts), EMBED_BATCH_SIZE):
        vectors.extend(normalize(v) for v in _embed_batch(texts[start : start + EMBED_BATCH_SIZE]))
    return vectors
