"""Montagem, parsing e reprocessamento da curadoria em lote."""

from __future__ import annotations

import json
import time

import pytest

import curate
from conftest import curation_entry, make_item, prompt_items

PROFILE = {"id": "tech", "name": "Tecnologia", "curation": {"persona": "Persona X", "min_score": 75}}


# --- Montagem -------------------------------------------------------------- #


def test_batch_prompt_numbers_items_and_truncates_raw_content(monkeypatch):
    monkeypatch.delenv("CURATION_RAW_CONTENT_MAX_CHARS", raising=False)
    items = [make_item("Primeiro", raw="a" * 5000), make_item("Segundo", raw="curto")]

    prompt = curate.build_batch_prompt(items)

    assert prompt_items(prompt) == [(1, "Primeiro"), (2, "Segundo")]
    assert "a" * 1500 in prompt and "a" * 1501 not in prompt
    assert "curto" in prompt


def test_raw_content_limit_is_configurable(monkeypatch):
    monkeypatch.setenv("CURATION_RAW_CONTENT_MAX_CHARS", "300")
    prompt = curate.build_batch_prompt([make_item("X", raw="b" * 1000)])
    assert "b" * 300 in prompt and "b" * 301 not in prompt


def test_batch_system_prompt_keeps_profile_prompt():
    system = curate.build_batch_system_prompt(PROFILE)
    assert system.startswith(curate.build_system_prompt(PROFILE))
    assert "Persona X" in system
    assert "`index`" in system


def test_batch_schema_is_list_with_index_first():
    fields = list(curate.BatchItemCuration.model_fields)
    assert fields[0] == "index"
    assert fields[1:] == list(curate.ArticleCuration.model_fields)


# --- Parsing --------------------------------------------------------------- #


def test_parse_maps_index_to_position():
    text = json.dumps([curation_entry(2, "B", 90), curation_entry(1, "A", 20)])
    result = curate.parse_batch_response(text, 2)

    assert set(result) == {0, 1}
    assert result[0].title == "Curado: A" and not result[0].is_quality_approved
    assert result[1].title == "Curado: B" and result[1].is_quality_approved
    assert isinstance(result[1], curate.ArticleCuration)


def test_parse_allows_empty_summary_only_for_rejected():
    rejected = curation_entry(1, "A", 10)
    approved_empty = {**curation_entry(2, "B", 95), "tts_text": ""}
    result = curate.parse_batch_response(json.dumps([rejected, approved_empty]), 2)

    assert result[0].technical_summary == "" and result[0].tts_text == ""
    assert 1 not in result  # aprovado sem TTS => reprocessar


def test_parse_drops_invalid_out_of_range_and_duplicate_entries():
    entries = [
        curation_entry(1, "A"),
        {**curation_entry(1, "A-dup"), "title": "duplicado"},
        curation_entry(7, "fora"),
        {"index": 2, "title": "sem campos"},
        "lixo",
    ]
    result = curate.parse_batch_response(json.dumps(entries), 3)

    assert list(result) == [0]
    assert result[0].title == "Curado: A"


def test_parse_accepts_items_wrapper():
    text = json.dumps({"items": [curation_entry(1, "A")]})
    assert list(curate.parse_batch_response(text, 1)) == [0]


@pytest.mark.parametrize("text", [None, "", "{truncado", '{"x": 1}', "42"])
def test_parse_rejects_unusable_response(text):
    with pytest.raises(curate.InvalidBatchResponse):
        curate.parse_batch_response(text, 2)


# --- Reprocessamento ------------------------------------------------------- #


def test_missing_indices_are_retried_alone(fake_gemini):
    def handler(contents, call):
        items = prompt_items(contents)
        if call == 1:  # esquece o item 2
            items = [i for i in items if i[0] != 2]
        return json.dumps([curation_entry(n, t) for n, t in items])

    fake_gemini.handler = handler
    items = [make_item("A"), make_item("B"), make_item("C")]

    results = curate.curate_batch(items, "sys")

    assert [r.title for r in results] == ["Curado: A", "Curado: B", "Curado: C"]
    assert len(fake_gemini.calls) == 2
    assert prompt_items(fake_gemini.calls[1]) == [(1, "B")]  # só o faltante, renumerado


def test_invalid_json_splits_batch(fake_gemini):
    def handler(contents, call):
        if call == 1:
            return '[{"index": 1, "title": "trunc'
        return fake_gemini.approve_all(contents, call)

    fake_gemini.handler = handler
    items = [make_item(t) for t in "ABCD"]

    results = curate.curate_batch(items, "sys")

    assert all(r is not None for r in results)
    assert [len(prompt_items(c)) for c in fake_gemini.calls] == [4, 2, 2]


def test_api_error_retries_with_backoff_then_gives_up(fake_gemini, monkeypatch):
    sleeps = []
    monkeypatch.setattr(curate, "_sleep", sleeps.append)

    def handler(contents, call):
        raise RuntimeError("503 UNAVAILABLE")

    fake_gemini.handler = handler
    results = curate.curate_batch([make_item("A"), make_item("B")], "sys")

    assert results == [None, None]
    assert len(fake_gemini.calls) == curate.MAX_RETRIES
    assert sleeps == [2.0, 4.0]


def test_api_error_then_success(fake_gemini):
    def handler(contents, call):
        if call == 1:
            raise RuntimeError("503")
        return fake_gemini.approve_all(contents, call)

    fake_gemini.handler = handler
    results = curate.curate_batch([make_item("A"), make_item("B")], "sys")
    assert all(results)
    assert len(fake_gemini.calls) == 2


def test_curate_with_gemini_uses_batch_size(fake_gemini, monkeypatch):
    monkeypatch.setenv("CURATION_BATCH_SIZE", "4")
    items = [make_item(f"T{i}") for i in range(10)]

    curated = curate.curate_with_gemini(items, PROFILE)

    assert len(fake_gemini.calls) == 3
    assert [len(prompt_items(c)) for c in fake_gemini.calls] == [4, 4, 2]
    assert [c["title"] for c in curated] == [f"T{i}" for i in range(10)]
    assert all(c["curation"].is_quality_approved for c in curated)
    assert len(curate.filter_approved(curated)) == 10


def test_expired_deadline_skips_items(fake_gemini):
    curated = curate.curate_with_gemini([make_item("A")], PROFILE, deadline=time.monotonic() - 1)
    assert curated == []
    assert fake_gemini.calls == []


def test_rate_limiter_waits_between_calls(monkeypatch):
    sleeps = []
    monkeypatch.setattr(curate, "_sleep", sleeps.append)
    monkeypatch.setenv("GEMINI_REQUEST_DELAY_SECONDS", "4")
    limiter = curate._RateLimiter()

    limiter.wait()
    limiter.wait()

    assert len(sleeps) == 1 and 3.5 < sleeps[0] <= 4
