"""Cliente Firestore: dedupe por title_hash e gravação de artigos aprovados.

Autenticação via service account: defina a variável de ambiente
GOOGLE_APPLICATION_CREDENTIALS apontando para o arquivo JSON da service account
(local) ou deixe o workflow do GitHub Actions escrevê-lo em disco a partir do
secret FIRESTORE_SERVICE_ACCOUNT_JSON antes de rodar este módulo.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from google.cloud import firestore

ARTICLES_COLLECTION = "articles"

_client: firestore.Client | None = None


def get_client() -> firestore.Client:
    """Retorna um client Firestore singleton, autenticado via service account."""
    global _client
    if _client is None:
        project_id = os.environ.get("FIRESTORE_PROJECT_ID")
        _client = firestore.Client(project=project_id) if project_id else firestore.Client()
    return _client


def dedupe_window_days() -> int:
    try:
        return int(os.environ.get("DEDUPE_WINDOW_DAYS", 7))
    except ValueError:
        return 7


def title_hash_exists_recently(title_hash: str, window_days: int | None = None) -> bool:
    """Verifica se já existe um artigo com o mesmo title_hash publicado nos últimos N dias."""
    window_days = window_days if window_days is not None else dedupe_window_days()
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)

    client = get_client()
    query = (
        client.collection(ARTICLES_COLLECTION)
        .where("title_hash", "==", title_hash)
        .where("curated_at", ">=", cutoff)
        .limit(1)
    )
    docs = list(query.stream())
    return len(docs) > 0


def cleanup_grace_hours() -> int:
    try:
        return int(os.environ.get("CLEANUP_GRACE_HOURS", 48))
    except ValueError:
        return 48


def delete_stale_read_articles(grace_hours: int | None = None) -> int:
    """Apaga artigos já lidos (e não favoritados) após um período de carência.

    Carência baseada em `read_at` (quando o app marcou o artigo como lido).
    Artigos lidos antes dessa funcionalidade existir (sem `read_at`) são
    tratados como elegíveis, já que certamente passaram da carência.
    Retorna quantos documentos foram apagados.
    """
    grace_hours = grace_hours if grace_hours is not None else cleanup_grace_hours()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=grace_hours)

    client = get_client()
    # Só filtra por igualdade (read/favorite): evita exigir índice composto e
    # ainda pega artigos sem `read_at` (que uma consulta com intervalo excluiria).
    query = client.collection(ARTICLES_COLLECTION).where("read", "==", True).where("favorite", "==", False)

    deleted = 0
    batch = client.batch()
    pending = 0
    for doc in query.stream():
        read_at = doc.to_dict().get("read_at")
        if read_at is not None and read_at > cutoff:
            continue  # ainda dentro da carência

        batch.delete(doc.reference)
        pending += 1
        deleted += 1

        if pending >= 400:  # limite de 500 operações por batch no Firestore
            batch.commit()
            batch = client.batch()
            pending = 0

    if pending > 0:
        batch.commit()

    return deleted


def save_article(article: dict) -> str:
    """Grava um artigo aprovado na coleção `articles`.

    Reconfirma idempotência (title_hash ainda não existe) imediatamente antes de gravar,
    para reduzir a janela de corrida entre o dedupe inicial e a gravação.
    Retorna o ID do documento criado, ou string vazia se descartado por já existir.
    """
    if title_hash_exists_recently(article["title_hash"]):
        print(f"[firestore] Ignorado (já existe): {article['title']!r}")
        return ""

    client = get_client()
    doc_ref = client.collection(ARTICLES_COLLECTION).document()
    payload = {
        "title": article["title"],
        "title_hash": article["title_hash"],
        "source_url": article["source_url"],
        "source_name": article["source_name"],
        "technical_summary": article["technical_summary"],
        "relevance_score": article["relevance_score"],
        "tags": article["tags"],
        "tts_text": article["tts_text"],
        "published_at": article["published_at"],
        "curated_at": article.get("curated_at") or datetime.now(timezone.utc),
        "read": False,
        "favorite": False,
    }
    doc_ref.set(payload)
    print(f"[firestore] Salvo: {article['title']!r} (id={doc_ref.id}, score={article['relevance_score']})")
    return doc_ref.id
