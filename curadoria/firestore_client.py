"""Cliente Firestore: perfis de curadoria, dedupe por title_hash, gravação e limpeza.

Autenticação via service account: defina a variável de ambiente
GOOGLE_APPLICATION_CREDENTIALS apontando para o arquivo JSON da service account
(local) ou deixe o workflow do GitHub Actions escrevê-lo em disco a partir do
secret FIRESTORE_SERVICE_ACCOUNT_JSON antes de rodar este módulo.

Modelo de dados:
  profiles/{id}   — o que buscar (sources) e como filtrar (curation). O pipeline
                    cura TODOS os perfis; `active: true` marca só o perfil que o
                    app está exibindo (usado para ordem e retenção).
  articles/{id}   — artigos curados, marcados com `profile_id` e `config_version`.
  seen/{profile_id}_{title_hash}
                  — itens que o Gemini já avaliou (aprovados ou reprovados), para
                    não reenviar a mesma notícia a cada ciclo. Só o pipeline usa.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from google.cloud import firestore

ARTICLES_COLLECTION = "articles"
PROFILES_COLLECTION = "profiles"
SEEN_COLLECTION = "seen"

# Limite do Firestore é 500 operações por batch; 400 dá folga para segurança.
BATCH_SIZE = 400

DEFAULT_INACTIVE_RETENTION_DAYS = 30

# Limite do operador `in` do Firestore (valores por query).
IN_QUERY_MAX_VALUES = 30

# Quantas referências por chamada de `get_all` (leitura em lote por ID).
GET_ALL_CHUNK_SIZE = 100

_client: firestore.Client | None = None


def get_client() -> firestore.Client:
    """Retorna um client Firestore singleton, autenticado via service account."""
    global _client
    if _client is None:
        project_id = os.environ.get("FIRESTORE_PROJECT_ID")
        _client = firestore.Client(project=project_id) if project_id else firestore.Client()
    return _client


# --------------------------------------------------------------------------- #
# Perfis de curadoria
# --------------------------------------------------------------------------- #


def load_active_profile() -> dict | None:
    """Carrega o perfil com `active == true` (o que o app está exibindo).

    Retorna `None` se nenhum perfil estiver ativo. Se mais de um estiver ativo
    (estado inconsistente), usa o primeiro e avisa. Usado pelo purge manual
    (`purge_oldest.py --profile active`); a curadoria percorre todos os perfis.
    """
    client = get_client()
    docs = list(client.collection(PROFILES_COLLECTION).where("active", "==", True).stream())

    if not docs:
        return None

    if len(docs) > 1:
        names = ", ".join(repr(d.to_dict().get("name")) for d in docs)
        print(f"[firestore] Aviso: {len(docs)} perfis ativos ({names}). Usando o primeiro.")

    profile = docs[0].to_dict()
    profile["id"] = docs[0].id
    return profile


def list_profiles() -> list[dict]:
    """Lista todos os perfis cadastrados (ativos e inativos)."""
    client = get_client()
    profiles = []
    for doc in client.collection(PROFILES_COLLECTION).stream():
        data = doc.to_dict()
        data["id"] = doc.id
        profiles.append(data)
    return profiles


def clear_pending_cleanup(profile_id: str) -> None:
    """Zera a flag `pending_cleanup` depois que o purge foi executado."""
    client = get_client()
    client.collection(PROFILES_COLLECTION).document(profile_id).update({"pending_cleanup": None})


# --------------------------------------------------------------------------- #
# Dedupe e gravação
# --------------------------------------------------------------------------- #


def dedupe_window_days(profile: dict | None = None) -> int:
    """Janela de dedupe: preferência para o valor do perfil, senão o ambiente."""
    if profile and profile.get("dedupe_window_days"):
        try:
            return int(profile["dedupe_window_days"])
        except (TypeError, ValueError):
            pass
    try:
        return int(os.environ.get("DEDUPE_WINDOW_DAYS", 7))
    except ValueError:
        return 7


def title_hash_exists_recently(
    title_hash: str,
    profile_id: str,
    window_days: int | None = None,
) -> bool:
    """Verifica se já existe artigo com o mesmo title_hash no perfil, na janela recente.

    O dedupe é por perfil: a mesma notícia pode ser legitimamente curada em dois
    perfis diferentes (ex: uma notícia de economia que também é política).
    """
    window_days = window_days if window_days is not None else dedupe_window_days()
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)

    client = get_client()
    query = (
        client.collection(ARTICLES_COLLECTION)
        .where("profile_id", "==", profile_id)
        .where("title_hash", "==", title_hash)
        .where("curated_at", ">=", cutoff)
        .limit(1)
    )
    return len(list(query.stream())) > 0


def _chunks(values: list, size: int):
    for start in range(0, len(values), size):
        yield values[start : start + size]


def seen_doc_id(profile_id: str, title_hash: str) -> str:
    """ID determinístico do documento em `seen`: permite leitura em lote por ID."""
    return f"{profile_id}_{title_hash}"


def fetch_seen_hashes(
    profile_id: str,
    title_hashes: list[str],
    window_days: int,
    config_version: int,
) -> set[str]:
    """Retorna quais `title_hashes` o Gemini já avaliou para o perfil.

    Uma leitura em lote (`get_all`) por até 100 IDs, em vez de uma query por
    item. Uma marca só vale se estiver dentro da janela de dedupe e tiver sido
    gravada com a `config_version` atual do perfil: ao editar fontes/critérios
    no app a versão sobe, e os itens voltam a ser avaliados com as regras novas
    (como acontecia antes da coleção `seen` existir).
    """
    if not title_hashes:
        return set()

    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    client = get_client()
    collection = client.collection(SEEN_COLLECTION)
    seen: set[str] = set()

    unique_hashes = list(dict.fromkeys(title_hashes))
    for chunk in _chunks(unique_hashes, GET_ALL_CHUNK_SIZE):
        refs = [collection.document(seen_doc_id(profile_id, h)) for h in chunk]
        for snapshot in client.get_all(refs):
            if not snapshot.exists:
                continue
            data = snapshot.to_dict() or {}
            seen_at = data.get("seen_at")
            if seen_at is not None and seen_at < cutoff:
                continue
            if int(data.get("config_version") or 1) != config_version:
                continue
            if data.get("title_hash"):
                seen.add(data["title_hash"])

    return seen


def recent_article_hashes(profile_id: str, title_hashes: list[str], window_days: int) -> set[str]:
    """Retorna quais `title_hashes` já têm artigo salvo no perfil, na janela de dedupe.

    Cobre artigos gravados antes da coleção `seen` existir. Usa `in` em lotes de
    30 (uma query por lote, não por item) sobre o índice composto
    (profile_id, title_hash, curated_at) que já existe.
    """
    if not title_hashes:
        return set()

    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    client = get_client()
    found: set[str] = set()

    unique_hashes = list(dict.fromkeys(title_hashes))
    for chunk in _chunks(unique_hashes, IN_QUERY_MAX_VALUES):
        query = (
            client.collection(ARTICLES_COLLECTION)
            .where("profile_id", "==", profile_id)
            .where("title_hash", "in", chunk)
            .where("curated_at", ">=", cutoff)
            .select(["title_hash"])
        )
        for doc in query.stream():
            h = (doc.to_dict() or {}).get("title_hash")
            if h:
                found.add(h)

    return found


def mark_seen(profile_id: str, title_hashes: list[str], config_version: int) -> int:
    """Grava em `seen` os itens que o Gemini avaliou com sucesso. Retorna quantos.

    Só deve receber itens com curadoria bem-sucedida (aprovados ou reprovados):
    itens que falharam ou ficaram de fora por orçamento de tempo não entram,
    para serem tentados de novo no próximo ciclo.
    """
    unique_hashes = list(dict.fromkeys(title_hashes))
    if not unique_hashes:
        return 0

    client = get_client()
    collection = client.collection(SEEN_COLLECTION)
    now = datetime.now(timezone.utc)

    for chunk in _chunks(unique_hashes, BATCH_SIZE):
        batch = client.batch()
        for h in chunk:
            batch.set(
                collection.document(seen_doc_id(profile_id, h)),
                {
                    "profile_id": profile_id,
                    "title_hash": h,
                    "seen_at": now,
                    "config_version": config_version,
                },
            )
        batch.commit()

    return len(unique_hashes)


def save_article(article: dict, profile: dict) -> str:
    """Grava um artigo aprovado na coleção `articles`.

    Reconfirma idempotência (title_hash ainda não existe) imediatamente antes de gravar,
    para reduzir a janela de corrida entre o dedupe inicial e a gravação.
    Retorna o ID do documento criado, ou string vazia se descartado por já existir.
    """
    profile_id = profile["id"]
    window = dedupe_window_days(profile)

    if title_hash_exists_recently(article["title_hash"], profile_id, window):
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
        "profile_id": profile_id,
        "config_version": int(profile.get("config_version") or 1),
        "read": False,
        "favorite": False,
    }
    doc_ref.set(payload)
    print(f"[firestore] Salvo: {article['title']!r} (id={doc_ref.id}, score={article['relevance_score']})")
    return doc_ref.id


# --------------------------------------------------------------------------- #
# Limpeza
# --------------------------------------------------------------------------- #


def _delete_docs(docs_iter, should_delete=None) -> int:
    """Apaga documentos em batches, aplicando um filtro opcional por documento.

    `should_delete` recebe o dict do documento e retorna bool — usado quando o
    critério não pode ser expresso na query sem exigir um índice composto novo.
    """
    client = get_client()
    batch = client.batch()
    pending = 0
    deleted = 0

    for doc in docs_iter:
        if should_delete is not None and not should_delete(doc.to_dict()):
            continue

        batch.delete(doc.reference)
        pending += 1
        deleted += 1

        if pending >= BATCH_SIZE:
            batch.commit()
            batch = client.batch()
            pending = 0

    if pending > 0:
        batch.commit()

    return deleted


def delete_stale_read_articles() -> int:
    """Apaga todos os artigos já lidos (e não favoritados), sem carência.

    Vale para todos os perfis, não só o ativo. Roda a cada execução do
    pipeline: qualquer artigo lido e não favoritado é apagado, independente
    de quando foi lido. Retorna quantos documentos foram apagados.

    O filtro de `favorite` é aplicado no cliente, não na query: artigos
    salvos antes do campo `favorite` existir não têm esse campo, e uma
    igualdade `== False` no Firestore não casa com campo ausente — o que
    fazia esses artigos legados nunca serem apagados, mesmo lidos.
    """
    client = get_client()
    query = client.collection(ARTICLES_COLLECTION).where("read", "==", True)
    return _delete_docs(query.stream(), lambda data: not data.get("favorite", False))


def delete_inactive_profile_articles(active_profile_id: str | None) -> int:
    """Apaga artigos antigos dos perfis que não estão sendo exibidos no app.

    Com a curadoria multi-perfil, todos os perfis recebem artigos novos, mas só
    o ativo (o que o app exibe) é lido no dia a dia. A retenção
    (`inactive_retention_days`, padrão 30) é lida de cada perfil não ativo.
    Favoritos nunca são apagados, e o perfil ativo é ignorado.

    A query já filtra `curated_at <= cutoff` (índice composto profile_id +
    curated_at), para ler só os candidatos em vez de todos os artigos de cada
    perfil a cada ciclo. Se o índice ainda não foi publicado, cai no modo antigo
    (lê o perfil inteiro e filtra no cliente).
    """
    client = get_client()
    now = datetime.now(timezone.utc)
    total_deleted = 0

    for profile in list_profiles():
        profile_id = profile["id"]
        if profile_id == active_profile_id:
            continue

        try:
            retention_days = int(profile.get("inactive_retention_days") or DEFAULT_INACTIVE_RETENTION_DAYS)
        except (TypeError, ValueError):
            retention_days = DEFAULT_INACTIVE_RETENTION_DAYS

        cutoff = now - timedelta(days=retention_days)
        base_query = client.collection(ARTICLES_COLLECTION).where("profile_id", "==", profile_id)

        def is_expired_and_not_favorite(data: dict) -> bool:
            if data.get("favorite", False):
                return False
            curated_at = data.get("curated_at")
            return curated_at is None or curated_at <= cutoff

        try:
            deleted = _delete_docs(
                base_query.where("curated_at", "<=", cutoff).stream(),
                is_expired_and_not_favorite,
            )
        except Exception as exc:  # noqa: BLE001 - tipicamente índice composto ainda não publicado
            print(
                f"[firestore] Aviso: query com filtro de data falhou para o perfil "
                f"{profile.get('name')!r} ({exc}). Usando leitura completa do perfil — "
                f"publique os índices com `firebase deploy --only firestore:indexes`."
            )
            deleted = _delete_docs(base_query.stream(), is_expired_and_not_favorite)

        if deleted:
            print(
                f"[firestore] Perfil não ativo {profile.get('name')!r}: {deleted} artigos "
                f"apagados (retenção de {retention_days} dias)"
            )
        total_deleted += deleted

    return total_deleted


def delete_expired_seen(profiles: list[dict]) -> int:
    """Apaga marcas de `seen` mais antigas que a janela de dedupe do perfil.

    Roda uma vez por execução, para todos os perfis. A query usa a menor janela
    entre os perfis (índice simples em `seen_at`, criado automaticamente) e a
    janela exata de cada perfil é aplicada no cliente. Marcas de perfis que não
    existem mais usam a janela padrão.
    """
    default_window = dedupe_window_days()
    windows = {p["id"]: dedupe_window_days(p) for p in profiles}
    min_window = min([default_window, *windows.values()])

    now = datetime.now(timezone.utc)
    client = get_client()
    query = client.collection(SEEN_COLLECTION).where("seen_at", "<", now - timedelta(days=min_window))

    def is_expired(data: dict) -> bool:
        window = windows.get(data.get("profile_id"), default_window)
        seen_at = data.get("seen_at")
        return seen_at is None or seen_at < now - timedelta(days=window)

    return _delete_docs(query.stream(), is_expired)


def purge_profile_articles(profile_id: str, mode: str) -> int:
    """Executa o purge pedido pelo app ao editar as fontes/critérios de um perfil.

    Modos:
      - `purge_unread`: apaga não lidos e não favoritados (preserva o histórico lido).
      - `purge_all`:    apaga tudo do perfil, exceto favoritos.

    Favoritos sobrevivem em ambos os casos. Roda no pipeline (Admin SDK) porque as
    regras do Firestore proíbem `delete` no cliente.
    """
    if mode not in ("purge_unread", "purge_all"):
        print(f"[firestore] Modo de purge desconhecido: {mode!r} — ignorando.")
        return 0

    client = get_client()
    query = client.collection(ARTICLES_COLLECTION).where("profile_id", "==", profile_id)

    def should_delete(data: dict) -> bool:
        if data.get("favorite", False):
            return False
        if mode == "purge_unread":
            return not data.get("read", False)
        return True

    deleted = _delete_docs(query.stream(), should_delete)
    print(f"[firestore] Purge '{mode}' no perfil {profile_id}: {deleted} artigos apagados.")
    return deleted
