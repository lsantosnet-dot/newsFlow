"""Orquestra o pipeline de curadoria para o perfil ativo:

    load_active_profile() -> purge pendente -> limpezas (lidos/inativos) ->
    ingest_from_profile() -> dedupe() -> curate_with_gemini() ->
    filter_approved() -> save_to_firestore()

As limpezas rodam logo no início, antes de ingestão/curadoria, porque essas
etapas são as mais lentas do pipeline (rede + chamadas ao Gemini) e sujeitas
ao timeout do job no GitHub Actions — se o job for encerrado à força ali, a
limpeza já terá rodado nesse ciclo.

As fontes e os critérios de curadoria vêm do perfil ativo no Firestore (editável
pelo app Flutter), não mais de constantes no código.

Rodável localmente com `python main.py` (usando um .env) e também é o entrypoint
usado pelo workflow do GitHub Actions.
"""

from __future__ import annotations

import sys

from dotenv import load_dotenv

load_dotenv()

from curate import curate_with_gemini, filter_approved  # noqa: E402
from firestore_client import (  # noqa: E402
    clear_pending_cleanup,
    delete_inactive_profile_articles,
    delete_stale_read_articles,
    dedupe_window_days,
    load_active_profile,
    purge_profile_articles,
    save_article,
    title_hash_exists_recently,
)
from ingest import ingest_from_profile  # noqa: E402
from text_utils import title_hash  # noqa: E402


def dedupe(items: list[dict], profile: dict) -> tuple[list[dict], int]:
    """Remove itens cujo title_hash já existe no Firestore recentemente.

    Isso evita gastar tokens do Gemini com notícias já curadas. O dedupe é por
    perfil. Retorna a lista de itens únicos (com `title_hash` anexado) e a
    contagem de descartados.
    """
    profile_id = profile["id"]
    window = dedupe_window_days(profile)

    unique_items: list[dict] = []
    discarded = 0

    for item in items:
        h = title_hash(item["title"])
        try:
            exists = title_hash_exists_recently(h, profile_id, window)
        except Exception as exc:  # noqa: BLE001
            print(f"[dedupe] Aviso: falha ao consultar Firestore para {item['title']!r} ({exc}). Mantendo item.")
            exists = False

        if exists:
            discarded += 1
            continue

        unique_items.append({**item, "title_hash": h})

    return unique_items, discarded


def save_to_firestore(approved_items: list[dict], profile: dict) -> int:
    """Grava os artigos aprovados no Firestore. Retorna quantos foram efetivamente salvos."""
    saved = 0
    for item in approved_items:
        curation = item["curation"]
        payload = {
            "title": curation.title,
            "title_hash": item["title_hash"],
            "source_url": item["url"],
            "source_name": item["source"],
            "technical_summary": curation.technical_summary,
            "relevance_score": curation.relevance_score,
            "tags": curation.tags,
            "tts_text": curation.tts_text,
            "published_at": item["published_at"],
        }
        if save_article(payload, profile):
            saved += 1
    return saved


def run_pending_purge(profile: dict) -> int:
    """Executa o purge que o app sinalizou ao editar as fontes/critérios do perfil.

    O app não pode apagar artigos (as regras do Firestore proíbem `delete` no
    cliente), então ele grava `pending_cleanup` no perfil e o pipeline executa
    aqui, na próxima rodada.
    """
    mode = profile.get("pending_cleanup")
    if not mode:
        return 0

    print(f"[main] Purge pendente no perfil {profile.get('name')!r}: {mode}")
    deleted = purge_profile_articles(profile["id"], mode)

    try:
        clear_pending_cleanup(profile["id"])
    except Exception as exc:  # noqa: BLE001
        print(f"[main] Aviso: purge executado mas falhou ao limpar a flag ({exc}).")

    return deleted


def run() -> None:
    print("=" * 60)
    print("Pipeline de curadoria — início")
    print("=" * 60)

    profile = load_active_profile()
    if profile is None:
        print("[main] Nenhum perfil ativo no Firestore — nada a fazer.")
        print("[main] Abra o app e ative um perfil de curadoria.")
        return

    print(f"[main] Perfil ativo: {profile.get('name')!r} (id={profile['id']}, "
          f"config_version={profile.get('config_version', 1)})")

    purged = run_pending_purge(profile)

    # Limpezas rodam antes de ingestão/curadoria de propósito: são rápidas e não
    # dependem do Gemini, então saem executadas mesmo se o job for encerrado pelo
    # timeout do GitHub Actions durante as etapas lentas mais adiante.
    try:
        deleted_read = delete_stale_read_articles()
    except Exception as exc:  # noqa: BLE001
        print(f"[main] Aviso: falha na limpeza de artigos lidos ({exc}). Pulando desta execução.")
        deleted_read = 0

    try:
        deleted_inactive = delete_inactive_profile_articles(profile["id"])
    except Exception as exc:  # noqa: BLE001
        print(f"[main] Aviso: falha na limpeza de perfis inativos ({exc}). Pulando desta execução.")
        deleted_inactive = 0

    ingested = ingest_from_profile(profile)
    total_ingested = len(ingested)

    unique_items, discarded_dedupe = dedupe(ingested, profile)
    print(f"[main] {len(unique_items)} itens únicos após dedupe ({discarded_dedupe} descartados por duplicidade)")

    curated = curate_with_gemini(unique_items, profile)
    curation_failures = sum(1 for item in curated if item["curation"] is None)

    approved = filter_approved(curated)
    rejected_by_score = len(curated) - curation_failures - len(approved)

    saved_count = save_to_firestore(approved, profile)
    already_existed = len(approved) - saved_count

    print()
    print("=" * 60)
    print("Resumo da execução")
    print("=" * 60)
    print(f"Perfil:                    {profile.get('name')!r}")
    print(f"Ingeridos:                 {total_ingested}")
    print(f"Descartados (duplicados):  {discarded_dedupe}")
    print(f"Enviados ao Gemini:        {len(unique_items)}")
    print(f"Falhas de curadoria:       {curation_failures}")
    print(f"Reprovados (score/regras): {rejected_by_score}")
    print(f"Aprovados:                 {len(approved)}")
    print(f"Salvos no Firestore:       {saved_count}")
    print(f"Já existiam (race dedupe): {already_existed}")
    print(f"Apagados (purge pendente): {purged}")
    print(f"Apagados (lidos, carência vencida): {deleted_read}")
    print(f"Apagados (perfis inativos): {deleted_inactive}")
    print("=" * 60)


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print(f"[main] Erro fatal no pipeline: {exc}", file=sys.stderr)
        raise
