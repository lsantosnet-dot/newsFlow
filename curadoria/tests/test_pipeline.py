"""Dedupe via `seen`, regra de "falha não vira seen" e orquestração multi-perfil."""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

import pytest

import firestore_client
import main
from conftest import curation_entry, make_item, prompt_items
from text_utils import title_hash

NOW = datetime.now(timezone.utc)


def profile(pid="tech", name="Tecnologia", active=False, **extra):
    return {"id": pid, "name": name, "active": active, "config_version": 1, "sources": [], **extra}


def add_seen(db, pid, title, seen_at=None, config_version=1):
    h = title_hash(title)
    db.add("seen", f"{pid}_{h}", profile_id=pid, title_hash=h, seen_at=seen_at or NOW, config_version=config_version)


def add_article(db, doc_id, pid, title, **extra):
    data = dict(
        profile_id=pid,
        title_hash=title_hash(title),
        title=title,
        curated_at=NOW,
        read=False,
        favorite=False,
    )
    data.update(extra)
    db.add("articles", doc_id, **data)


# --- Dedupe ---------------------------------------------------------------- #


def test_dedupe_discards_seen_existing_and_in_run_duplicates(fake_db):
    add_seen(fake_db, "tech", "Já visto")
    add_seen(fake_db, "other", "Visto em outro perfil")
    add_article(fake_db, "a1", "tech", "Artigo salvo")
    items = [make_item(t) for t in ["Já visto", "Artigo salvo", "Novo", "Novo", "Visto em outro perfil"]]

    unique, discarded = main.dedupe(items, profile())

    assert [i["title"] for i in unique] == ["Novo", "Visto em outro perfil"]
    assert discarded == 3
    assert all(i["title_hash"] == title_hash(i["title"]) for i in unique)


def test_dedupe_uses_batched_reads(fake_db):
    items = [make_item(f"Item {n}") for n in range(40)]

    main.dedupe(items, profile())

    assert fake_db.get_all_calls == 1  # um get_all para os 40 IDs
    assert fake_db.queries == 2  # `in` em lotes de 30 => 2 queries, não 40


def test_seen_from_old_config_or_outside_window_is_ignored(fake_db):
    add_seen(fake_db, "tech", "Versão antiga", config_version=1)
    add_seen(fake_db, "tech", "Expirado", seen_at=NOW - timedelta(days=8), config_version=2)
    items = [make_item("Versão antiga"), make_item("Expirado")]

    unique, discarded = main.dedupe(items, profile(config_version=2))

    assert len(unique) == 2 and discarded == 0


def test_dedupe_keeps_items_when_firestore_fails(fake_db, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("firestore fora")

    monkeypatch.setattr(main, "fetch_seen_hashes", boom)
    monkeypatch.setattr(main, "recent_article_hashes", boom)

    unique, discarded = main.dedupe([make_item("A")], profile())
    assert len(unique) == 1 and discarded == 0


def test_delete_expired_seen_respects_profile_window(fake_db):
    add_seen(fake_db, "tech", "velho", seen_at=NOW - timedelta(days=8))
    add_seen(fake_db, "tech", "recente", seen_at=NOW - timedelta(days=2))
    add_seen(fake_db, "long", "dentro da janela longa", seen_at=NOW - timedelta(days=8))
    add_seen(fake_db, "apagado", "perfil removido", seen_at=NOW - timedelta(days=8))

    deleted = firestore_client.delete_expired_seen([profile(), profile("long", dedupe_window_days=14)])

    assert deleted == 2
    remaining = {d["title_hash"] for d in fake_db.data["seen"].values()}
    assert remaining == {title_hash("recente"), title_hash("dentro da janela longa")}


# --- Falha não vira seen ---------------------------------------------------- #


def _seen_hashes(db, pid):
    return {d["title_hash"] for d in db.data.get("seen", {}).values() if d["profile_id"] == pid}


def test_only_successful_evaluations_become_seen(fake_db, fake_gemini, monkeypatch):
    titles = ["Aprovado", "Reprovado", "Falha"]
    monkeypatch.setattr(main, "ingest_from_profile", lambda _p: [make_item(t) for t in titles])

    def handler(contents, _call):
        out = []
        for n, t in prompt_items(contents):
            if t == "Aprovado":
                out.append(curation_entry(n, t, 90))
            elif t == "Reprovado":
                out.append(curation_entry(n, t, 20))
            # "Falha" nunca volta => falha após os retries
        return json.dumps(out)

    fake_gemini.handler = handler
    stats = main.ProfileStats(name="Tecnologia")

    main.curate_profile(profile(), stats, deadline=time.monotonic() + 60)

    assert _seen_hashes(fake_db, "tech") == {title_hash("Aprovado"), title_hash("Reprovado")}
    assert (stats.approved, stats.rejected, stats.failures, stats.saved) == (1, 1, 1, 1)
    saved = list(fake_db.data["articles"].values())
    assert saved[0]["title"] == "Curado: Aprovado"
    assert saved[0]["tts_text"] and saved[0]["technical_summary"]
    assert saved[0]["profile_id"] == "tech"

    # Próximo ciclo: só o item que falhou volta ao Gemini.
    fake_gemini.calls.clear()
    main.curate_profile(profile(), main.ProfileStats(name="x"), deadline=time.monotonic() + 60)
    sent = [t for c in fake_gemini.calls for _, t in prompt_items(c)]
    assert set(sent) == {"Falha"}


def test_items_skipped_by_budget_or_cap_do_not_become_seen(fake_db, fake_gemini, monkeypatch):
    monkeypatch.setenv("MAX_ITEMS_PER_PROFILE_PER_RUN", "2")
    monkeypatch.setattr(
        main, "ingest_from_profile", lambda _p: [make_item("Novo", 0), make_item("Médio", 5), make_item("Velho", 10)]
    )

    stats = main.ProfileStats(name="t")
    main.curate_profile(profile(), stats, deadline=time.monotonic() + 60)
    assert stats.over_cap == 1
    assert _seen_hashes(fake_db, "tech") == {title_hash("Novo"), title_hash("Médio")}

    fake_db.data["seen"].clear()
    fake_db.data["articles"].clear()
    stats = main.ProfileStats(name="t")
    main.curate_profile(profile(), stats, deadline=time.monotonic() - 1)  # orçamento esgotado
    assert stats.skipped_budget == 2 and stats.sent == 0
    assert _seen_hashes(fake_db, "tech") == set()


def test_save_failure_does_not_become_seen(fake_db, fake_gemini, monkeypatch):
    monkeypatch.setattr(main, "ingest_from_profile", lambda _p: [make_item("A"), make_item("B")])

    real_save = main.save_article

    def flaky_save(payload, prof):
        if payload["title"] == "Curado: A":
            raise RuntimeError("deadline exceeded")
        return real_save(payload, prof)

    monkeypatch.setattr(main, "save_article", flaky_save)
    main.curate_profile(profile(), main.ProfileStats(name="t"), deadline=time.monotonic() + 60)

    assert _seen_hashes(fake_db, "tech") == {title_hash("B")}


# --- Orquestração ----------------------------------------------------------- #


def test_order_profiles_puts_active_first():
    ordered, active = main.order_profiles(
        [profile("a", "Alfa"), profile("z", "Zeta", active=True), profile("m", "Meio")]
    )
    assert active == "z"
    assert [p["id"] for p in ordered] == ["z", "a", "m"]


def test_run_curates_all_profiles_and_isolates_failures(fake_db, fake_gemini, monkeypatch, capsys):
    for p in [profile("pol", "Política"), profile("tech", "Tecnologia", active=True), profile("eco", "Economia")]:
        fake_db.add("profiles", p.pop("id"), **p)
    fake_db.data["profiles"]["pol"]["pending_cleanup"] = "purge_unread"
    add_article(fake_db, "old-pol", "pol", "Antigo de política")
    add_article(fake_db, "fav-pol", "pol", "Favorito", favorite=True)

    order = []

    def ingest(prof):
        order.append(prof["id"])
        if prof["id"] == "eco":
            raise RuntimeError("feed quebrado")
        return [make_item(f"{prof['id']} notícia")]

    monkeypatch.setattr(main, "ingest_from_profile", ingest)

    main.run()

    assert order == ["tech", "eco", "pol"]
    by_profile = {}
    for art in fake_db.data["articles"].values():
        by_profile.setdefault(art["profile_id"], []).append(art["title"])
    assert by_profile["tech"] == ["Curado: tech notícia"]
    assert sorted(by_profile["pol"]) == ["Curado: pol notícia", "Favorito"]  # purge pendente rodou
    assert fake_db.data["profiles"]["pol"]["pending_cleanup"] is None
    assert "eco" not in by_profile

    out = capsys.readouterr().out
    assert "ERRO RuntimeError: feed quebrado" in out
    assert "TOTAL (3 perfis, 1 com erro)" in out


def test_inactive_cleanup_spares_active_and_favorites(fake_db):
    for p in [profile("tech", "Tecnologia", active=True), profile("pol", "Política")]:
        fake_db.add("profiles", p.pop("id"), **p)
    old = NOW - timedelta(days=40)
    add_article(fake_db, "t-old", "tech", "t", curated_at=old)
    add_article(fake_db, "p-old", "pol", "p", curated_at=old)
    add_article(fake_db, "p-fav", "pol", "pf", curated_at=old, favorite=True)
    add_article(fake_db, "p-new", "pol", "pn")

    assert firestore_client.delete_inactive_profile_articles("tech") == 1
    assert set(fake_db.data["articles"]) == {"t-old", "p-fav", "p-new"}
