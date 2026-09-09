"""Limpeza retroativa de artigos duplicados na coleção `articles`.

Diferente do dedupe em `main.py` (que só evita duplicata *nova* dentro de uma
janela de dias e por perfil), este script varre a coleção inteira e apaga
duplicatas já existentes, sem limite de tempo e ignorando `profile_id`.

Dois artigos são duplicata um do outro se compartilham `title_hash` OU
`source_url`. A relação é transitiva (union-find): se A e B batem por
title_hash, e B e C batem por source_url, os três formam um único grupo —
mesmo que A e C não tenham nada em comum diretamente.

Dentro de cada grupo:
  - qualquer cópia marcada como `favorite` é sempre mantida, independente de
    estar lida ou não;
  - dos não-favoritos: se pelo menos um estiver marcado como não lido
    (`read == False`), mantém a versão mais recente (maior `curated_at`)
    entre os não lidos, e apaga todo o resto do grupo (lidas e não lidas
    mais antigas);
  - se todos os não-favoritos já estiverem lidos, apaga todos eles — não há
    razão para manter uma duplicata que já foi lida.

Uso:
    python dedupe_articles.py              # aplica e apaga
    python dedupe_articles.py --dry-run     # só relata o que apagaria
"""

from __future__ import annotations

import sys
from collections import defaultdict
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

from firestore_client import ARTICLES_COLLECTION, BATCH_SIZE, get_client  # noqa: E402

_EPOCH = datetime.min.replace(tzinfo=timezone.utc)


class _UnionFind:
    """Union-find simples sobre IDs de artigo (string), para agrupar duplicatas."""

    def __init__(self) -> None:
        self._parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        self._parent.setdefault(x, x)
        root = x
        while self._parent[root] != root:
            root = self._parent[root]
        self._parent[x] = root
        return root

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[ra] = rb


def load_articles() -> list[dict]:
    """Carrega todos os artigos da coleção, com `id` e `_ref` anexados."""
    client = get_client()
    articles = []
    for doc in client.collection(ARTICLES_COLLECTION).stream():
        data = doc.to_dict()
        data["id"] = doc.id
        data["_ref"] = doc.reference
        articles.append(data)
    return articles


def find_duplicate_groups(articles: list[dict]) -> list[list[dict]]:
    """Agrupa artigos por title_hash OU source_url (transitivamente) e retorna
    apenas os grupos com mais de um artigo."""
    uf = _UnionFind()
    by_title_hash: dict[str, list[str]] = defaultdict(list)
    by_url: dict[str, list[str]] = defaultdict(list)

    for art in articles:
        uf.find(art["id"])
        title_hash = art.get("title_hash")
        if title_hash:
            by_title_hash[title_hash].append(art["id"])
        source_url = art.get("source_url")
        if source_url:
            by_url[source_url].append(art["id"])

    for ids in (*by_title_hash.values(), *by_url.values()):
        for other_id in ids[1:]:
            uf.union(ids[0], other_id)

    groups: dict[str, list[dict]] = defaultdict(list)
    for art in articles:
        groups[uf.find(art["id"])].append(art)

    return [group for group in groups.values() if len(group) > 1]


def articles_to_delete(group: list[dict]) -> list[dict]:
    """Dentro de um grupo de duplicatas, decide quais apagar.

    Favoritos nunca são apagados. Dos não-favoritos: se houver pelo menos um
    não lido, mantém o mais recente (`curated_at`) entre os não lidos e apaga
    o resto; se todos já estiverem lidos, apaga todos (nenhum motivo para
    manter uma duplicata já lida).
    """
    non_favorites = [art for art in group if not art.get("favorite", False)]
    if not non_favorites:
        return []

    unread = [art for art in non_favorites if not art.get("read", False)]
    if not unread:
        return non_favorites

    keep = max(unread, key=lambda a: a.get("curated_at") or _EPOCH)
    return [art for art in non_favorites if art["id"] != keep["id"]]


def _delete_in_batches(client, articles: list[dict]) -> int:
    batch = client.batch()
    pending = 0
    deleted = 0

    for art in articles:
        batch.delete(art["_ref"])
        pending += 1
        deleted += 1

        if pending >= BATCH_SIZE:
            batch.commit()
            batch = client.batch()
            pending = 0

    if pending > 0:
        batch.commit()

    return deleted


def run(dry_run: bool = False) -> None:
    print("=" * 60)
    print("Dedupe de artigos" + (" (DRY RUN)" if dry_run else ""))
    print("=" * 60)

    client = get_client()
    articles = load_articles()
    print(f"[dedupe_articles] {len(articles)} artigos carregados.")

    groups = find_duplicate_groups(articles)
    print(f"[dedupe_articles] {len(groups)} grupos de duplicatas encontrados.")

    to_delete: list[dict] = []
    for group in groups:
        group_to_delete = articles_to_delete(group)
        to_delete.extend(group_to_delete)

        delete_ids = {art["id"] for art in group_to_delete}
        kept = [art for art in group if art["id"] not in delete_ids]

        print(f"[dedupe_articles] Grupo com {len(group)} artigos — {group[0].get('title')!r}:")
        if not kept:
            print("    todos lidos e nenhum favorito — grupo inteiro apagado")
        for art in kept:
            reason = "favorito" if art.get("favorite") else "mais recente (não lido)"
            print(f"    mantém ({reason}): id={art['id']} curated_at={art.get('curated_at')}")
        for art in group_to_delete:
            print(
                f"    apaga: id={art['id']} curated_at={art.get('curated_at')} "
                f"lido={art.get('read', False)}"
            )

    deleted = 0 if dry_run else _delete_in_batches(client, to_delete)

    print()
    print("=" * 60)
    print("Resumo")
    print("=" * 60)
    print(f"Artigos carregados:     {len(articles)}")
    print(f"Grupos de duplicatas:   {len(groups)}")
    print(f"Artigos a apagar:       {len(to_delete)}")
    if dry_run:
        print("Apagados:               0 (--dry-run, nada foi apagado)")
    else:
        print(f"Apagados:               {deleted}")
    print("=" * 60)


if __name__ == "__main__":
    try:
        run(dry_run="--dry-run" in sys.argv)
    except Exception as exc:  # noqa: BLE001
        print(f"[dedupe_articles] Erro fatal: {exc}", file=sys.stderr)
        raise
