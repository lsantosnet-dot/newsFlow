"""Modo por idade do purge manual."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import purge_oldest

NOW = datetime.now(timezone.utc)


@pytest.fixture
def articles(fake_db):
    def add(doc_id, pid, days_old, **extra):
        fake_db.add(
            "articles",
            doc_id,
            profile_id=pid,
            title=doc_id,
            curated_at=NOW - timedelta(days=days_old),
            read=extra.get("read", False),
            favorite=extra.get("favorite", False),
        )

    add("velho-tech", "tech", 20)
    add("velho-pol", "pol", 30)
    add("velho-fav", "tech", 30, favorite=True)
    add("velho-lido", "tech", 30, read=True)
    add("novo-tech", "tech", 3)
    fake_db.add("profiles", "tech", name="Tecnologia", active=True)
    fake_db.add("profiles", "pol", name="Política", active=False)
    return fake_db


def test_older_than_all_scope(articles):
    docs = purge_oldest.find_older_than(14, None)
    assert [d.id for d in docs] == ["velho-pol", "velho-tech"]


def test_older_than_active_scope(articles):
    assert [d.id for d in purge_oldest.find_older_than(14, "tech")] == ["velho-tech"]


def test_run_older_than_deletes_and_dry_run_keeps(articles):
    purge_oldest.run(limit=50, profile_scope="all", only_unread=False, dry_run=True, older_than_days=14)
    assert len(articles.data["articles"]) == 5

    purge_oldest.run(limit=50, profile_scope="all", only_unread=False, dry_run=False, older_than_days=14)
    assert set(articles.data["articles"]) == {"velho-fav", "velho-lido", "novo-tech"}


def test_default_scope_is_all_and_count_mode_unchanged(articles):
    args = purge_oldest._parse_args([])
    assert args.profile == "all" and args.older_than_days is None and args.limit == 50

    purge_oldest.run(limit=2, profile_scope=args.profile, only_unread=False, dry_run=False)
    # Os 2 mais antigos não favoritos (o favorito é preservado e não conta).
    assert "velho-fav" in articles.data["articles"]
    assert len(articles.data["articles"]) == 3


def test_older_than_days_must_be_positive():
    with pytest.raises(SystemExit):
        purge_oldest._parse_args(["--older-than-days", "0"])
