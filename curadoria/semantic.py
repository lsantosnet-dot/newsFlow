"""Dedupe semântico: reconhecer a mesma notícia contada por fontes diferentes.

O dedupe de `main.py` só pega o mesmo título (depois de normalizado). Mas
"Copom eleva a Selic para 11%" (g1) e "BC sobe juros básicos em meio ponto"
(Folha) têm hashes diferentes e iriam os dois ao Gemini e ao seu feed. Aqui
cada item vira um embedding (ver `embeddings.py`) e é comparado:

1. **Com os artigos já salvos do perfil** (dentro da janela de dedupe), via
   busca vetorial do Firestore (`find_nearest_article`). Se um item novo é a
   mesma notícia de um artigo existente, ele não vai ao Gemini: a fonte dele é
   acrescentada em `also_covered_by` do artigo existente.
2. **Com os outros itens da mesma rodada.** Itens parecidos formam um grupo
   (union-find, como em `dedupe_articles.py`); só um representante vai ao
   Gemini, levando as outras fontes em `also_covered_by`.

"Parecido" = similaridade de cosseno >= SEMANTIC_DEDUPE_THRESHOLD.

Modos (variável SEMANTIC_DEDUPE_MODE):
  off — não calcula nada; o pipeline se comporta como antes.
  log — (padrão) calcula, grava os embeddings dos artigos salvos e imprime no
        log o que *seria* agrupado, com a similaridade — mas não descarta
        nada. Serve para você calibrar o limiar com dados reais antes de ligar.
  on  — agrupa de verdade.

Se a API de embeddings ou o Firestore falharem, os itens seguem sem dedupe
semântico (o mesmo espírito do dedupe por hash: na dúvida, não perde notícia).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from embeddings import cosine_similarity, embed_texts, embedding_text
from firestore_client import find_nearest_article

DEFAULT_THRESHOLD = 0.86
DEFAULT_MODE = "log"
MODES = ("off", "log", "on")


@dataclass
class Coverage:
    """Item novo que é a mesma notícia de um artigo já salvo."""

    article_id: str
    item: dict
    similarity: float


@dataclass
class SemanticResult:
    items: list[dict]  # o que segue para o Gemini (representantes + itens sem par)
    covered: list[Coverage] = field(default_factory=list)  # absorvidos por artigos salvos
    merged: int = 0  # absorvidos por outro item da mesma rodada


def semantic_mode() -> str:
    mode = os.environ.get("SEMANTIC_DEDUPE_MODE", "").strip().lower() or DEFAULT_MODE
    if mode not in MODES:
        print(f"[semantic] Aviso: SEMANTIC_DEDUPE_MODE={mode!r} inválido; usando {DEFAULT_MODE!r}.")
        return DEFAULT_MODE
    return mode


def similarity_threshold() -> float:
    try:
        return float(os.environ.get("SEMANTIC_DEDUPE_THRESHOLD") or DEFAULT_THRESHOLD)
    except ValueError:
        return DEFAULT_THRESHOLD


def group_similar(vectors: list[list[float]], threshold: float) -> list[list[int]]:
    """Agrupa índices de vetores parecidos (similaridade >= threshold).

    Compara todos os pares (n²/2 comparações — com 40~100 itens é nada) e une
    os parecidos com union-find. A união é transitiva: se A~B e B~C, os três
    ficam juntos mesmo que A e C não passem do limiar entre si (o "efeito
    corrente" do single-linkage clustering). Com um limiar alto isso é raro e
    aceitável para notícias. Grupos e membros saem na ordem original.
    """
    parent = list(range(len(vectors)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]  # path halving: encurta o caminho até a raiz
            i = parent[i]
        return i

    for i in range(len(vectors)):
        for j in range(i + 1, len(vectors)):
            if cosine_similarity(vectors[i], vectors[j]) >= threshold:
                parent[find(j)] = find(i)

    groups: dict[int, list[int]] = {}
    for i in range(len(vectors)):
        groups.setdefault(find(i), []).append(i)
    return list(groups.values())


def choose_representative(members: list[dict]) -> dict:
    """Quem vai ao Gemini pelo grupo: o de conteúdo mais longo (mais material
    para o resumo); no empate, o publicado primeiro (quem deu a notícia)."""
    return max(members, key=lambda m: (len(m.get("raw_content") or ""), -m["published_at"].timestamp()))


def source_ref(item: dict) -> dict:
    """Como uma fonte extra aparece em `also_covered_by`."""
    return {"source_name": item["source"], "url": item["url"]}


def find_semantic_duplicates(items: list[dict], profile: dict) -> SemanticResult:
    """Embeda os itens, compara com o que o perfil já tem e entre si, e agrupa.

    Os itens devolvidos carregam `embedding` (para ser gravado junto do artigo,
    se aprovado). Os representantes de grupo carregam também `also_covered_by`
    e `absorbed_hashes` (title_hash dos absorvidos, que viram `seen` junto).
    """
    mode = semantic_mode()
    if mode == "off" or not items:
        return SemanticResult(items=items)

    try:
        vectors = embed_texts([embedding_text(item) for item in items])
    except Exception as exc:  # noqa: BLE001
        print(f"[semantic] Aviso: falha ao gerar embeddings ({exc}). Seguindo sem dedupe semântico.")
        return SemanticResult(items=items)
    items = [{**item, "embedding": vector} for item, vector in zip(items, vectors)]

    threshold = similarity_threshold()
    acting = mode == "on"
    verb = "absorvido" if acting else "seria absorvido (modo log)"

    # 1) Contra os artigos já salvos.
    covered: list[Coverage] = []
    fresh: list[dict] = []
    search_ok = True
    for item in items:
        match = None
        if search_ok:
            try:
                match = find_nearest_article(profile, item["embedding"])
            except Exception as exc:  # noqa: BLE001
                # Ex.: índice vetorial ainda não criado (o erro traz o comando para criar).
                print(f"[semantic] Aviso: falha na busca vetorial ({exc}). Comparando só dentro da rodada.")
                search_ok = False
        if match and match[1] >= threshold:
            print(f"[semantic] {match[1]:.3f} {item['title']!r} ({item['source']}) {verb} pelo artigo {match[0]}")
            if acting:
                covered.append(Coverage(article_id=match[0], item=item, similarity=match[1]))
                continue
        fresh.append(item)

    # 2) Entre os itens da rodada.
    result = SemanticResult(items=[], covered=covered)
    for group in group_similar([item["embedding"] for item in fresh], threshold):
        members = [fresh[i] for i in group]
        if len(members) == 1:
            result.items.append(members[0])
            continue

        rep = choose_representative(members)
        others = [m for m in members if m is not rep]
        for other in others:
            sim = cosine_similarity(rep["embedding"], other["embedding"])
            print(f"[semantic] {sim:.3f} {other['title']!r} ({other['source']}) {verb} "
                  f"por {rep['title']!r} ({rep['source']})")
        if acting:
            result.items.append({
                **rep,
                "also_covered_by": [source_ref(o) for o in others],
                "absorbed_hashes": [o["title_hash"] for o in others],
            })
            result.merged += len(others)
        else:
            result.items.extend(members)

    return result
