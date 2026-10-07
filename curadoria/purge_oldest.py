"""Limpeza manual da coleção `articles`, em dois modos:

  - por quantidade (padrão): apaga os N artigos mais antigos (padrão: 50);
  - por idade (`--older-than-days D`): apaga TODOS os artigos não lidos com
    `curated_at` mais antigo que D dias. Nesse modo `--limit` é ignorado.

Para quando o feed acumula artigos antigos que nunca serão lidos. "Mais antigo"
é pelo `curated_at` (quando o artigo entrou no Firestore), em ordem crescente.

Regras:
  - favoritos nunca são apagados (e não contam para o limite);
  - `--profile all` (padrão) considera todos os perfis; `--profile active`
    restringe ao perfil que o app está exibindo;
  - `--only-unread` ignora artigos já lidos (normalmente já são apagados pelo
    pipeline, mas pode sobrar algum entre uma execução e outra). No modo por
    idade só não lidos são considerados, sempre.

Os filtros de favorito/perfil/lido são aplicados no cliente enquanto a query
ordenada por `curated_at` é lida em stream — assim não é preciso nenhum índice
composto novo, e a leitura para assim que N artigos elegíveis são encontrados.
Artigos sem `curated_at` ficam de fora (o Firestore exclui da ordenação
documentos que não têm o campo).

Uso:
    python purge_oldest.py                          # 50 mais antigos, todos os perfis
    python purge_oldest.py --limit 100 --profile active
    python purge_oldest.py --only-unread --dry-run  # só relata o que apagaria
    python purge_oldest.py --older-than-days 14 --dry-run  # não lidos com mais de 14 dias
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

load_dotenv()

from google.cloud.firestore import Query  # noqa: E402
from firestore_client import (  # noqa: E402
    ARTICLES_COLLECTION,
    BATCH_SIZE,
    get_client,
    load_active_profile,
)

DEFAULT_LIMIT = 50


def find_oldest(limit: int, profile_id: str | None, only_unread: bool) -> list:
    """Retorna os `limit` documentos elegíveis mais antigos por `curated_at`."""
    client = get_client()
    query = client.collection(ARTICLES_COLLECTION).order_by("curated_at", direction=Query.ASCENDING)

    selected = []
    for doc in query.stream():
        data = doc.to_dict()
        if data.get("favorite", False):
            continue
        if profile_id is not None and data.get("profile_id") != profile_id:
            continue
        if only_unread and data.get("read", False):
            continue

        selected.append(doc)
        if len(selected) >= limit:
            break

    return selected


def find_older_than(days: int, profile_id: str | None) -> list:
    """Retorna todos os artigos não lidos e não favoritos com `curated_at` > `days` dias."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    client = get_client()
    query = (
        client.collection(ARTICLES_COLLECTION)
        .where("curated_at", "<", cutoff)
        .order_by("curated_at", direction=Query.ASCENDING)
    )

    selected = []
    for doc in query.stream():
        data = doc.to_dict()
        if data.get("favorite", False):
            continue
        if data.get("read", False):
            continue
        if profile_id is not None and data.get("profile_id") != profile_id:
            continue
        selected.append(doc)

    return selected


def _delete_in_batches(docs: list) -> int:
    client = get_client()
    deleted = 0
    for start in range(0, len(docs), BATCH_SIZE):
        batch = client.batch()
        chunk = docs[start : start + BATCH_SIZE]
        for doc in chunk:
            batch.delete(doc.reference)
        batch.commit()
        deleted += len(chunk)
    return deleted


def run(
    limit: int,
    profile_scope: str,
    only_unread: bool,
    dry_run: bool,
    older_than_days: int | None = None,
) -> None:
    print("=" * 60)
    if older_than_days is not None:
        title = f"Purge dos não lidos com mais de {older_than_days} dias"
    else:
        title = f"Purge dos {limit} artigos mais antigos"
    print(title + (" (DRY RUN)" if dry_run else ""))
    print("=" * 60)

    profile_id = None
    if profile_scope == "active":
        profile = load_active_profile()
        if profile is None:
            print("[purge_oldest] Nenhum perfil ativo no Firestore — nada a fazer.")
            print("[purge_oldest] Use --profile all (padrão) para considerar todos os perfis.")
            return
        profile_id = profile["id"]
        print(f"[purge_oldest] Perfil: {profile.get('name')!r} (id={profile_id})")
    else:
        print("[purge_oldest] Perfil: todos")

    if older_than_days is not None:
        if limit != DEFAULT_LIMIT:
            print(f"[purge_oldest] --limit {limit} ignorado no modo por idade.")
        docs = find_older_than(older_than_days, profile_id)
    else:
        docs = find_oldest(limit, profile_id, only_unread)
    print(f"[purge_oldest] {len(docs)} artigos selecionados:")
    for doc in docs:
        data = doc.to_dict()
        print(
            f"    apaga: curated_at={data.get('curated_at')} lido={data.get('read', False)} "
            f"fonte={data.get('source_name')!r} — {data.get('title')!r}"
        )

    deleted = 0 if dry_run else _delete_in_batches(docs)

    print()
    print("=" * 60)
    print("Resumo")
    print("=" * 60)
    if older_than_days is not None:
        print(f"Selecionados:  {len(docs)} (não lidos com mais de {older_than_days} dias)")
    else:
        print(f"Selecionados:  {len(docs)} (limite {limit})")
    if docs:
        print(f"Intervalo:     {docs[0].to_dict().get('curated_at')} → {docs[-1].to_dict().get('curated_at')}")
    if dry_run:
        print("Apagados:      0 (--dry-run, nada foi apagado)")
    else:
        print(f"Apagados:      {deleted}")
    print("=" * 60)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Apaga os N artigos mais antigos, ou os não lidos mais velhos que D dias (exceto favoritos)."
    )
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help=f"quantos apagar (padrão {DEFAULT_LIMIT})")
    parser.add_argument(
        "--profile",
        choices=("active", "all"),
        default="all",
        help="considerar todos os perfis (padrão) ou só o perfil ativo",
    )
    parser.add_argument(
        "--older-than-days",
        type=int,
        default=None,
        help="apagar todos os não lidos com curated_at mais antigo que isso (ignora --limit)",
    )
    parser.add_argument("--only-unread", action="store_true", help="considerar só artigos não lidos")
    parser.add_argument("--dry-run", action="store_true", help="só relatar, sem apagar")
    args = parser.parse_args(argv)
    if args.limit < 1:
        parser.error("--limit precisa ser >= 1")
    if args.older_than_days is not None and args.older_than_days < 1:
        parser.error("--older-than-days precisa ser >= 1")
    return args


if __name__ == "__main__":
    args = _parse_args(sys.argv[1:])
    try:
        run(args.limit, args.profile, args.only_unread, args.dry_run, args.older_than_days)
    except Exception as exc:  # noqa: BLE001
        print(f"[purge_oldest] Erro fatal: {exc}", file=sys.stderr)
        raise
