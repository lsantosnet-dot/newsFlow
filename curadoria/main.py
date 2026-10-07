"""Orquestra o pipeline de curadoria para TODOS os perfis:

    list_profiles() -> purge pendente (por perfil) ->
    limpezas globais (lidos / perfis não ativos / seen expirado) ->
    para cada perfil (o ativo primeiro):
        ingest_from_profile() -> dedupe() -> teto por perfil ->
        curate_with_gemini() -> filter_approved() -> save_to_firestore() -> mark_seen()

`active` marca só o perfil que o app está exibindo: ele é processado primeiro
(para receber notícias novas mesmo se o orçamento de tempo acabar) e é poupado
da retenção de perfis não ativos. Não decide mais o que é curado.

As limpezas rodam logo no início, antes de ingestão/curadoria, porque essas
etapas são as mais lentas do pipeline (rede + chamadas ao Gemini) e sujeitas
ao timeout do job no GitHub Actions — se o job for encerrado à força ali, a
limpeza já terá rodado nesse ciclo.

Cada perfil roda isolado: uma falha (ingestão, Gemini, Firestore) é registrada
no log e no resumo, e os outros perfis seguem normalmente.

Rodável localmente com `python main.py` (usando um .env) e também é o entrypoint
usado pelo workflow do GitHub Actions.
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass, fields

from dotenv import load_dotenv

load_dotenv()

from curate import curate_with_gemini, curation_time_budget_seconds, filter_approved  # noqa: E402
from firestore_client import (  # noqa: E402
    clear_pending_cleanup,
    delete_expired_seen,
    delete_inactive_profile_articles,
    delete_stale_read_articles,
    dedupe_window_days,
    fetch_seen_hashes,
    list_profiles,
    mark_seen,
    purge_profile_articles,
    recent_article_hashes,
    save_article,
)
from ingest import ingest_from_profile  # noqa: E402
from text_utils import title_hash  # noqa: E402

DEFAULT_MAX_ITEMS_PER_PROFILE_PER_RUN = 40


@dataclass
class ProfileStats:
    name: str
    ingested: int = 0
    duplicates: int = 0
    over_cap: int = 0
    sent: int = 0
    skipped_budget: int = 0
    failures: int = 0
    rejected: int = 0
    approved: int = 0
    saved: int = 0
    already_existed: int = 0
    marked_seen: int = 0
    purged: int = 0
    error: str = ""


def _max_items_per_profile() -> int:
    try:
        return max(1, int(os.environ.get("MAX_ITEMS_PER_PROFILE_PER_RUN", DEFAULT_MAX_ITEMS_PER_PROFILE_PER_RUN)))
    except ValueError:
        return DEFAULT_MAX_ITEMS_PER_PROFILE_PER_RUN


def _config_version(profile: dict) -> int:
    try:
        return int(profile.get("config_version") or 1)
    except (TypeError, ValueError):
        return 1


def order_profiles(profiles: list[dict]) -> tuple[list[dict], str | None]:
    """Ordena os perfis com o ativo primeiro e os demais por nome.

    Retorna também o id do ativo (ou `None`). Se houver mais de um ativo
    (estado inconsistente), usa o primeiro por nome e avisa.
    """
    by_name = sorted(profiles, key=lambda p: (str(p.get("name") or ""), p["id"]))
    actives = [p for p in by_name if p.get("active")]
    if len(actives) > 1:
        names = ", ".join(repr(p.get("name")) for p in actives)
        print(f"[main] Aviso: {len(actives)} perfis ativos ({names}). Usando o primeiro como ativo.")
    active_id = actives[0]["id"] if actives else None
    ordered = sorted(by_name, key=lambda p: p["id"] != active_id)
    return ordered, active_id


def dedupe(items: list[dict], profile: dict) -> tuple[list[dict], int]:
    """Remove itens que o perfil já avaliou ou já tem salvos, e repetidos na rodada.

    Consulta primeiro a coleção `seen` (leitura em lote por ID) e, só para o que
    sobrar, os artigos já salvos (query `in` em lotes de 30). Isso evita gastar
    cota do Gemini com notícias já curadas ou já reprovadas. O dedupe é por
    perfil. Retorna os itens únicos (com `title_hash` anexado) e a contagem de
    descartados. Se o Firestore falhar, mantém os itens (como antes).
    """
    profile_id = profile["id"]
    window = dedupe_window_days(profile)

    candidates: list[dict] = []
    in_run: set[str] = set()
    discarded = 0
    for item in items:
        h = title_hash(item["title"])
        if h in in_run:
            discarded += 1
            continue
        in_run.add(h)
        candidates.append({**item, "title_hash": h})

    hashes = [item["title_hash"] for item in candidates]
    try:
        known = fetch_seen_hashes(profile_id, hashes, window, _config_version(profile))
    except Exception as exc:  # noqa: BLE001
        print(f"[dedupe] Aviso: falha ao consultar `seen` ({exc}). Seguindo sem essa checagem.")
        known = set()

    remaining = [h for h in hashes if h not in known]
    try:
        known |= recent_article_hashes(profile_id, remaining, window)
    except Exception as exc:  # noqa: BLE001
        print(f"[dedupe] Aviso: falha ao consultar artigos existentes ({exc}). Mantendo itens.")

    unique_items = [item for item in candidates if item["title_hash"] not in known]
    discarded += len(candidates) - len(unique_items)
    return unique_items, discarded


def apply_profile_cap(items: list[dict], cap: int) -> tuple[list[dict], int]:
    """Limita quantos itens um perfil manda ao Gemini por execução (mais recentes primeiro).

    O excedente não vira `seen`: volta a ser candidato no próximo ciclo.
    """
    if len(items) <= cap:
        return items, 0
    ordered = sorted(items, key=lambda item: item["published_at"], reverse=True)
    return ordered[:cap], len(items) - cap


def save_to_firestore(approved_items: list[dict], profile: dict) -> tuple[int, int, set[str]]:
    """Grava os artigos aprovados no Firestore.

    Retorna (salvos, já existiam, title_hashes que falharam ao gravar). Um item
    cuja gravação falhou não deve virar `seen`, para ser tentado de novo.
    """
    saved = 0
    already_existed = 0
    failed: set[str] = set()
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
        try:
            if save_article(payload, profile):
                saved += 1
            else:
                already_existed += 1
        except Exception as exc:  # noqa: BLE001
            print(f"[main] Aviso: falha ao gravar {curation.title!r} ({exc}). Será tentado de novo.")
            failed.add(item["title_hash"])
    return saved, already_existed, failed


def evaluated_hashes(curated: list[dict], failed_saves: set[str]) -> list[str]:
    """Hashes que podem virar `seen`: avaliados com sucesso e, se aprovados, gravados."""
    return [
        item["title_hash"]
        for item in curated
        if item.get("curation") is not None and item["title_hash"] not in failed_saves
    ]


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


def curate_profile(profile: dict, stats: ProfileStats, deadline: float) -> None:
    """Ingestão -> dedupe -> teto -> Gemini -> gravação -> `seen`, para um perfil."""
    ingested = ingest_from_profile(profile)
    stats.ingested = len(ingested)

    unique_items, stats.duplicates = dedupe(ingested, profile)
    to_curate, stats.over_cap = apply_profile_cap(unique_items, _max_items_per_profile())
    print(
        f"[main] {len(unique_items)} itens únicos após dedupe ({stats.duplicates} descartados); "
        f"{len(to_curate)} vão ao Gemini ({stats.over_cap} acima do teto ficam para depois)"
    )

    curated = curate_with_gemini(to_curate, profile, deadline=deadline)
    stats.sent = len(curated)
    stats.skipped_budget = len(to_curate) - len(curated)
    stats.failures = sum(1 for item in curated if item["curation"] is None)

    approved = filter_approved(curated)
    stats.approved = len(approved)
    stats.rejected = len(curated) - stats.failures - len(approved)

    stats.saved, stats.already_existed, failed_saves = save_to_firestore(approved, profile)

    try:
        stats.marked_seen = mark_seen(profile["id"], evaluated_hashes(curated, failed_saves), _config_version(profile))
    except Exception as exc:  # noqa: BLE001
        # Não é fatal: aprovados já estão em `articles` (o dedupe os pega); os
        # reprovados só seriam reavaliados no próximo ciclo.
        print(f"[main] Aviso: falha ao gravar `seen` ({exc}).")


def _print_summary(all_stats: list[ProfileStats], globals_: dict[str, int]) -> None:
    labels = {
        "ingested": "Ingeridos",
        "duplicates": "Descartados (duplicados/já vistos)",
        "over_cap": "Acima do teto por perfil",
        "sent": "Enviados ao Gemini",
        "skipped_budget": "Pulados (orçamento de tempo)",
        "failures": "Falhas de curadoria",
        "rejected": "Reprovados (score/regras)",
        "approved": "Aprovados",
        "saved": "Salvos no Firestore",
        "already_existed": "Já existiam (race dedupe)",
        "marked_seen": "Marcados como vistos",
        "purged": "Apagados (purge pendente)",
    }

    print()
    print("=" * 60)
    print("Resumo da execução")
    print("=" * 60)
    totals = ProfileStats(name="TOTAL")
    for stats in all_stats:
        print(f"Perfil {stats.name!r}" + (f" — {stats.error}" if stats.error else ""))
        for f in fields(ProfileStats):
            if f.name in labels:
                value = getattr(stats, f.name)
                setattr(totals, f.name, getattr(totals, f.name) + value)
                print(f"  {labels[f.name] + ':':38} {value}")
        print("-" * 60)

    failed = [s.name for s in all_stats if s.error.startswith("ERRO")]
    print(f"TOTAL ({len(all_stats)} perfis, {len(failed)} com erro)")
    for f in fields(ProfileStats):
        if f.name in labels:
            print(f"  {labels[f.name] + ':':38} {getattr(totals, f.name)}")
    print(f"  {'Apagados (lidos):':38} {globals_['read']}")
    print(f"  {'Apagados (perfis não ativos):':38} {globals_['inactive']}")
    print(f"  {'Marcas `seen` expiradas apagadas:':38} {globals_['seen']}")
    print("=" * 60)


def run() -> None:
    print("=" * 60)
    print("Pipeline de curadoria — início")
    print("=" * 60)

    profiles = list_profiles()
    if not profiles:
        print("[main] Nenhum perfil no Firestore — nada a fazer.")
        print("[main] Abra o app (ou rode `python migrate.py`) para semear os perfis.")
        return

    ordered, active_id = order_profiles(profiles)
    print(f"[main] {len(ordered)} perfis a curar: " + ", ".join(
        f"{p.get('name')!r}" + (" (ativo)" if p["id"] == active_id else "") for p in ordered
    ))

    all_stats = [ProfileStats(name=str(p.get("name") or p["id"])) for p in ordered]

    # Purges pedidos pelo app, por perfil — rápidos, então rodam antes de tudo.
    for profile, stats in zip(ordered, all_stats):
        try:
            stats.purged = run_pending_purge(profile)
        except Exception as exc:  # noqa: BLE001
            print(f"[main] Aviso: falha no purge pendente do perfil {stats.name!r} ({exc}).")

    # Limpezas globais rodam antes de ingestão/curadoria de propósito: são rápidas
    # e não dependem do Gemini, então saem executadas mesmo se o job for encerrado
    # pelo timeout do GitHub Actions durante as etapas lentas mais adiante.
    globals_ = {"read": 0, "inactive": 0, "seen": 0}
    try:
        globals_["read"] = delete_stale_read_articles()
    except Exception as exc:  # noqa: BLE001
        print(f"[main] Aviso: falha na limpeza de artigos lidos ({exc}). Pulando desta execução.")

    if active_id is None:
        print("[main] Nenhum perfil ativo — pulando a limpeza de perfis não ativos.")
    else:
        try:
            globals_["inactive"] = delete_inactive_profile_articles(active_id)
        except Exception as exc:  # noqa: BLE001
            print(f"[main] Aviso: falha na limpeza de perfis não ativos ({exc}). Pulando desta execução.")

    try:
        globals_["seen"] = delete_expired_seen(profiles)
    except Exception as exc:  # noqa: BLE001
        print(f"[main] Aviso: falha na limpeza de `seen` ({exc}). Pulando desta execução.")

    # Orçamento de tempo único para todos os perfis (ver CURATION_TIME_BUDGET_SECONDS).
    deadline = time.monotonic() + curation_time_budget_seconds()

    for profile, stats in zip(ordered, all_stats):
        print()
        print(f"[main] --- Perfil {stats.name!r} (id={profile['id']}, "
              f"config_version={_config_version(profile)}) ---")
        if time.monotonic() > deadline:
            stats.error = "pulado: orçamento de tempo esgotado antes de começar (fica para o próximo ciclo)"
            print(f"[main] {stats.error}")
            continue
        try:
            curate_profile(profile, stats, deadline)
        except Exception as exc:  # noqa: BLE001 - um perfil com problema não derruba os outros
            stats.error = f"ERRO {type(exc).__name__}: {exc}"
            print(f"[main] {stats.error} no perfil {stats.name!r}. Seguindo para o próximo.")

    _print_summary(all_stats, globals_)


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print(f"[main] Erro fatal no pipeline: {exc}", file=sys.stderr)
        raise
