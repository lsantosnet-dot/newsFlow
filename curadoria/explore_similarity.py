"""Laboratório de embeddings: veja as similaridades reais entre as notícias de um perfil.

Busca as notícias das fontes do perfil (como o pipeline faz), gera os
embeddings e imprime:

  - um histograma de todas as similaridades entre pares;
  - os pares mais parecidos, com a similaridade de cada um.

Não grava nada no Firestore e não chama o Gemini de curadoria — só a API de
embeddings. Use para escolher SEMANTIC_DEDUPE_THRESHOLD: procure o ponto da
lista em que os pares deixam de ser "a mesma notícia" e passam a ser "o mesmo
assunto". O limiar fica um pouco acima disso.

Uso:
    python explore_similarity.py                    # perfil ativo
    python explore_similarity.py --profile <id>     # outro perfil
    python explore_similarity.py --top 40 --limit 80
"""

from __future__ import annotations

import argparse

from dotenv import load_dotenv

load_dotenv()

from embeddings import cosine_similarity, embed_texts, embedding_text  # noqa: E402
from firestore_client import list_profiles  # noqa: E402
from ingest import ingest_from_profile  # noqa: E402
from semantic import similarity_threshold  # noqa: E402


def histogram(values: list[float], width: int = 40) -> str:
    """Histograma em texto, em faixas de 0.05 entre o menor e o maior valor."""
    if not values:
        return "(sem pares)"
    buckets: dict[float, int] = {}
    for v in values:
        b = int(v * 20) / 20
        buckets[b] = buckets.get(b, 0) + 1
    biggest = max(buckets.values())
    lines = []
    for b in sorted(buckets):
        bar = "#" * max(1, round(buckets[b] / biggest * width))
        lines.append(f"  {b:.2f}–{b + 0.05:.2f} {bar} {buckets[b]}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--profile", help="ID do perfil (padrão: o ativo)")
    parser.add_argument("--top", type=int, default=25, help="quantos pares mostrar")
    parser.add_argument("--limit", type=int, default=60, help="máximo de notícias a comparar")
    args = parser.parse_args()

    profiles = list_profiles()
    chosen = next(
        (p for p in profiles if (p["id"] == args.profile if args.profile else p.get("active"))),
        None,
    )
    if chosen is None:
        raise SystemExit("Perfil não encontrado. IDs: " + ", ".join(p["id"] for p in profiles))

    items = sorted(ingest_from_profile(chosen), key=lambda i: i["published_at"], reverse=True)[: args.limit]
    print(f"Perfil {chosen.get('name')!r}: {len(items)} notícias. Gerando embeddings...")
    vectors = embed_texts([embedding_text(i) for i in items])

    pairs = [
        (cosine_similarity(vectors[i], vectors[j]), i, j)
        for i in range(len(items))
        for j in range(i + 1, len(items))
    ]
    pairs.sort(reverse=True)

    print(f"\nDistribuição das similaridades ({len(pairs)} pares):")
    print(histogram([p[0] for p in pairs]))

    threshold = similarity_threshold()
    print(f"\nPares mais parecidos (limiar atual: {threshold}):")
    for sim, i, j in pairs[: args.top]:
        mark = "<- agruparia" if sim >= threshold else ""
        print(f"\n{sim:.3f} {mark}")
        print(f"   [{items[i]['source']}] {items[i]['title']}")
        print(f"   [{items[j]['source']}] {items[j]['title']}")


if __name__ == "__main__":
    main()
