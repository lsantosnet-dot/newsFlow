"""Embeddings, agrupamento por similaridade e dedupe semântico no pipeline."""

from __future__ import annotations

import json
import math
import time
from datetime import datetime, timedelta, timezone

import pytest

import firestore_client
import main
from conftest import curation_entry, fake_embedding, make_item, prompt_items
from embeddings import cosine_similarity, embedding_text, normalize
from semantic import choose_representative, group_similar
from text_utils import title_hash

NOW = datetime.now(timezone.utc)


def profile(pid="tech", **extra):
    return {"id": pid, "name": "Tecnologia", "active": True, "config_version": 1, "sources": [], **extra}


def item(title, source="Fonte", minutes_ago=0, raw="conteúdo"):
    return {**make_item(title, minutes_ago, raw), "source": source, "url": f"https://{source}.example/{title}"}


def add_embedding(db, article_id, text, pid="tech", created_at=None, config_version=1):
    db.add(
        "article_embeddings", article_id,
        profile_id=pid, config_version=config_version,
        embedding=normalize(fake_embedding(text)), created_at=created_at or NOW,
    )


def run_profile(prof=None):
    stats = main.ProfileStats(name="t")
    main.curate_profile(prof or profile(), stats, deadline=time.monotonic() + 60)
    return stats


def sent_titles(gemini):
    return [t for c in gemini.calls for _, t in prompt_items(c)]


def seen_hashes(db):
    return {d["title_hash"] for d in db.data.get("seen", {}).values()}


# --- Matemática -------------------------------------------------------------- #


def test_normalize_and_cosine():
    v = normalize([3.0, 4.0])
    assert v == pytest.approx([0.6, 0.8])
    assert math.isclose(cosine_similarity(v, v), 1.0)
    assert cosine_similarity(normalize([1, 0]), normalize([0, 1])) == 0
    assert cosine_similarity(normalize([1, 0]), normalize([-1, 0])) == -1
    assert normalize([0.0, 0.0]) == [0.0, 0.0]


def test_fake_embeddings_behave_like_real_ones():
    same_a = normalize(fake_embedding("Selic sobe STORY:selic\nG1"))
    same_b = normalize(fake_embedding("Copom eleva juros STORY:selic\nFolha"))
    other = normalize(fake_embedding("Apple lança iPhone"))
    assert cosine_similarity(same_a, same_b) > 0.95
    assert abs(cosine_similarity(same_a, other)) < 0.3


def test_embedding_text_uses_title_and_lead():
    text = embedding_text({"title": "Título", "raw_content": "a  b\n" + "x" * 1000})
    assert text.startswith("Título\na b ")
    assert len(text) == len("Título\n") + 400


def test_group_similar_is_transitive_and_keeps_order():
    a, b, c, d = [1, 0, 0], normalize([1, 0.6, 0]), normalize([0.6, 1, 0]), [0, 0, 1]
    # a~b (0.86) e b~c (0.88), mas a e c (0.51) não passam do limiar sozinhos.
    assert group_similar([a, b, c, d], 0.85) == [[0, 1, 2], [3]]
    assert group_similar([a, b, c, d], 0.99) == [[0], [1], [2], [3]]


def test_representative_is_longest_then_earliest():
    short = item("curto", raw="x")
    long_new = item("longo novo", raw="x" * 50, minutes_ago=0)
    long_old = item("longo velho", raw="x" * 50, minutes_ago=30)
    assert choose_representative([short, long_new, long_old]) is long_old


# --- Pipeline: modo on ------------------------------------------------------- #


@pytest.fixture
def mode_on(monkeypatch):
    monkeypatch.setenv("SEMANTIC_DEDUPE_MODE", "on")


def test_same_story_in_run_goes_once_with_extra_sources(fake_db, fake_gemini, mode_on, monkeypatch):
    items = [
        item("Copom sobe a Selic STORY:selic", "G1", raw="curto"),
        item("BC eleva juros STORY:selic", "Folha", raw="texto bem mais longo"),
        item("Outra coisa", "G1"),
    ]
    monkeypatch.setattr(main, "ingest_from_profile", lambda _p: items)

    stats = run_profile()

    assert sorted(sent_titles(fake_gemini)) == ["BC eleva juros STORY:selic", "Outra coisa"]
    assert stats.semantic_merged == 1
    saved = {a["title"]: a for a in fake_db.data["articles"].values()}
    assert saved["Curado: BC eleva juros STORY:selic"]["also_covered_by"] == [
        {"source_name": "G1", "url": items[0]["url"]}
    ]
    assert saved["Curado: Outra coisa"]["also_covered_by"] == []
    # O absorvido vira `seen` junto com o representante.
    assert seen_hashes(fake_db) == {title_hash(i["title"]) for i in items}
    # Cada artigo salvo ganha o seu embedding, com o mesmo ID.
    assert set(fake_db.data["article_embeddings"]) == set(fake_db.data["articles"])


def test_story_already_saved_is_attached_not_resent(fake_db, fake_gemini, mode_on, monkeypatch):
    fake_db.add("articles", "a1", profile_id="tech", title="Selic", also_covered_by=[], curated_at=NOW)
    add_embedding(fake_db, "a1", "Selic sobe STORY:selic\nconteúdo")
    new = item("Juros sobem STORY:selic", "Estadão")
    monkeypatch.setattr(main, "ingest_from_profile", lambda _p: [new])

    stats = run_profile()

    assert fake_gemini.calls == []
    assert stats.semantic_covered == 1
    assert fake_db.data["articles"]["a1"]["also_covered_by"] == [{"source_name": "Estadão", "url": new["url"]}]
    assert seen_hashes(fake_db) == {title_hash(new["title"])}

    # A mesma fonte de novo (ex.: seen expirou) não duplica a entrada.
    fake_db.data["seen"].clear()
    run_profile()
    assert len(fake_db.data["articles"]["a1"]["also_covered_by"]) == 1


def test_story_of_deleted_article_counts_as_already_read(fake_db, fake_gemini, mode_on, monkeypatch):
    add_embedding(fake_db, "lido-e-apagado", "Selic sobe STORY:selic\nconteúdo")
    monkeypatch.setattr(main, "ingest_from_profile", lambda _p: [item("Juros sobem STORY:selic")])

    stats = run_profile()

    assert fake_gemini.calls == [] and stats.semantic_covered == 1
    assert seen_hashes(fake_db) == {title_hash("Juros sobem STORY:selic")}


def test_old_or_other_config_embeddings_are_ignored(fake_db, fake_gemini, mode_on, monkeypatch):
    add_embedding(fake_db, "velho", "A STORY:velho", created_at=NOW - timedelta(days=8))
    add_embedding(fake_db, "config", "B STORY:config", config_version=1)
    add_embedding(fake_db, "outro", "C STORY:outro", pid="pol")
    items = [item("A2 STORY:velho"), item("B2 STORY:config"), item("C2 STORY:outro")]
    monkeypatch.setattr(main, "ingest_from_profile", lambda _p: items)

    stats = run_profile(profile(config_version=2))

    assert stats.semantic_covered == 0
    assert len(sent_titles(fake_gemini)) == 3


def test_rejected_representative_takes_absorbed_to_seen_but_failure_does_not(
    fake_db, fake_gemini, mode_on, monkeypatch
):
    items = [item("A STORY:x", "G1", raw="longo"), item("B STORY:x", "Folha")]
    monkeypatch.setattr(main, "ingest_from_profile", lambda _p: items)

    fake_gemini.handler = lambda c, _n: json.dumps([curation_entry(n, t, 10) for n, t in prompt_items(c)])
    run_profile()
    assert seen_hashes(fake_db) == {title_hash("A STORY:x"), title_hash("B STORY:x")}

    fake_db.data["seen"].clear()
    fake_gemini.handler = lambda _c, _n: "[]"  # Gemini nunca responde => falha
    run_profile()
    assert seen_hashes(fake_db) == set()


# --- Modos log/off e falhas ---------------------------------------------------- #


def test_log_mode_reports_but_keeps_everything(fake_db, fake_gemini, monkeypatch, capsys):
    add_embedding(fake_db, "a1", "Selic STORY:selic")
    items = [item("A STORY:selic", "G1"), item("B STORY:juros", "G1"), item("C STORY:juros", "Folha")]
    monkeypatch.setattr(main, "ingest_from_profile", lambda _p: items)

    stats = run_profile()

    assert len(sent_titles(fake_gemini)) == 3
    assert (stats.semantic_merged, stats.semantic_covered) == (0, 0)
    out = capsys.readouterr().out
    assert "seria absorvido (modo log) pelo artigo a1" in out
    assert "seria absorvido (modo log) por" in out
    # Mesmo em modo log os embeddings são gravados, para o modo on já ter base.
    assert len(fake_db.data["article_embeddings"]) == 1 + 3


def test_off_mode_does_not_call_embeddings(fake_db, fake_gemini, monkeypatch):
    monkeypatch.setenv("SEMANTIC_DEDUPE_MODE", "off")
    monkeypatch.setattr(main, "ingest_from_profile", lambda _p: [item("A STORY:x"), item("B STORY:x")])

    run_profile()

    assert fake_gemini.embed_calls == []
    assert len(sent_titles(fake_gemini)) == 2
    assert "article_embeddings" not in fake_db.data


def test_embedding_failure_falls_back_to_plain_pipeline(fake_db, fake_gemini, mode_on, monkeypatch):
    def boom(_texts):
        raise RuntimeError("quota")

    fake_gemini.embed_handler = boom
    monkeypatch.setattr(main, "ingest_from_profile", lambda _p: [item("A STORY:x"), item("B STORY:x")])

    stats = run_profile()

    assert len(sent_titles(fake_gemini)) == 2 and stats.saved == 2


def test_delete_expired_embeddings_respects_profile_window(fake_db):
    add_embedding(fake_db, "velho", "a", created_at=NOW - timedelta(days=8))
    add_embedding(fake_db, "recente", "b", created_at=NOW - timedelta(days=2))
    add_embedding(fake_db, "longo", "c", pid="long", created_at=NOW - timedelta(days=8))

    deleted = firestore_client.delete_expired_embeddings([profile(), profile("long", dedupe_window_days=14)])

    assert deleted == 1
    assert set(fake_db.data["article_embeddings"]) == {"recente", "longo"}


def test_vector_search_failure_still_groups_within_run(fake_db, fake_gemini, mode_on, monkeypatch, capsys):
    calls = []

    def no_index(_profile, _vector):
        calls.append(1)
        raise RuntimeError("400 Missing vector index configuration")

    monkeypatch.setattr("semantic.find_nearest_article", no_index)
    monkeypatch.setattr(main, "ingest_from_profile", lambda _p: [item("A STORY:x"), item("B STORY:x"), item("C")])

    stats = run_profile()

    assert len(calls) == 1  # desiste da busca depois da primeira falha
    assert stats.semantic_merged == 1 and len(sent_titles(fake_gemini)) == 2
    assert "Comparando só dentro da rodada" in capsys.readouterr().out


def test_saved_embedding_is_a_firestore_vector_with_config_version(fake_db, fake_gemini, monkeypatch):
    from google.cloud.firestore_v1.vector import Vector

    monkeypatch.setattr(main, "ingest_from_profile", lambda _p: [item("A")])

    run_profile(profile(config_version=3))

    (emb,) = fake_db.data["article_embeddings"].values()
    assert isinstance(emb["embedding"], Vector) and len(emb["embedding"]) == 256
    assert emb["config_version"] == 3 and emb["profile_id"] == "tech"
    assert math.isclose(sum(x * x for x in emb["embedding"]), 1.0)
