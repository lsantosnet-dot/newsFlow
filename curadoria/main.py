"""Orquestra o pipeline de curadoria:

    ingest_all_sources() -> dedupe() -> curate_with_gemini() -> filter_approved() -> save_to_firestore()

Rodável localmente com `python main.py` (usando um .env) e também é o entrypoint
usado pelo workflow do GitHub Actions.
"""

from __future__ import annotations

import sys

from dotenv import load_dotenv

load_dotenv()

from curate import curate_with_gemini, filter_approved  # noqa: E402
from firestore_client import save_article, title_hash_exists_recently  # noqa: E402
from ingest import ingest_all_sources  # noqa: E402
from text_utils import title_hash  # noqa: E402


def dedupe(items: list[dict]) -> tuple[list[dict], int]:
    """Remove itens cujo title_hash já existe no Firestore recentemente.

    Isso evita gastar tokens do Gemini com notícias já curadas. Retorna a lista
    de itens únicos (com `title_hash` anexado) e a contagem de descartados.
    """
    unique_items: list[dict] = []
    discarded = 0

    for item in items:
        h = title_hash(item["title"])
        try:
            exists = title_hash_exists_recently(h)
        except Exception as exc:  # noqa: BLE001
            print(f"[dedupe] Aviso: falha ao consultar Firestore para {item['title']!r} ({exc}). Mantendo item.")
            exists = False

        if exists:
            discarded += 1
            continue

        unique_items.append({**item, "title_hash": h})

    return unique_items, discarded


def save_to_firestore(approved_items: list[dict]) -> int:
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
        doc_id = save_article(payload)
        if doc_id:
            saved += 1
    return saved


def run() -> None:
    print("=" * 60)
    print("Pipeline de curadoria — início")
    print("=" * 60)

    ingested = ingest_all_sources()
    total_ingested = len(ingested)

    unique_items, discarded_dedupe = dedupe(ingested)
    print(f"[main] {len(unique_items)} itens únicos após dedupe ({discarded_dedupe} descartados por duplicidade)")

    curated = curate_with_gemini(unique_items)
    curation_failures = sum(1 for item in curated if item["curation"] is None)

    approved = filter_approved(curated)
    rejected_by_score = len(curated) - curation_failures - len(approved)

    saved_count = save_to_firestore(approved)
    already_existed = len(approved) - saved_count

    print()
    print("=" * 60)
    print("Resumo da execução")
    print("=" * 60)
    print(f"Ingeridos:                 {total_ingested}")
    print(f"Descartados (duplicados):  {discarded_dedupe}")
    print(f"Enviados ao Gemini:        {len(unique_items)}")
    print(f"Falhas de curadoria:       {curation_failures}")
    print(f"Reprovados (score/regras): {rejected_by_score}")
    print(f"Aprovados:                 {len(approved)}")
    print(f"Salvos no Firestore:       {saved_count}")
    print(f"Já existiam (race dedupe): {already_existed}")
    print("=" * 60)


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print(f"[main] Erro fatal no pipeline: {exc}", file=sys.stderr)
        raise
