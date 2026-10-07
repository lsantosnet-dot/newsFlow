"""Fakes em memória do Firestore e do Gemini — os testes não usam rede."""

from __future__ import annotations

import itertools
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import curate  # noqa: E402
import firestore_client  # noqa: E402


# --------------------------------------------------------------------------- #
# Firestore fake
# --------------------------------------------------------------------------- #

_OPS = {
    "==": lambda a, b: a == b,
    "<": lambda a, b: a is not None and a < b,
    "<=": lambda a, b: a is not None and a <= b,
    ">": lambda a, b: a is not None and a > b,
    ">=": lambda a, b: a is not None and a >= b,
    "in": lambda a, b: a in b,
}


class FakeSnapshot:
    def __init__(self, ref, data):
        self.reference = ref
        self.id = ref.id
        self._data = data

    @property
    def exists(self):
        return self._data is not None

    def to_dict(self):
        return dict(self._data) if self._data is not None else None


class FakeDocRef:
    def __init__(self, store, collection, doc_id):
        self._store = store
        self.collection_name = collection
        self.id = doc_id

    def get(self):
        self._store.reads += 1
        return FakeSnapshot(self, self._store.data[self.collection_name].get(self.id))

    def set(self, data):
        self._store.writes += 1
        self._store.data[self.collection_name][self.id] = dict(data)

    def update(self, data):
        self._store.writes += 1
        self._store.data[self.collection_name][self.id].update(data)

    def delete(self):
        self._store.writes += 1
        self._store.data[self.collection_name].pop(self.id, None)


class FakeQuery:
    def __init__(self, store, collection, filters=(), order=None, limit=None):
        self._store = store
        self._collection = collection
        self._filters = list(filters)
        self._order = order
        self._limit = limit

    def _copy(self, **kw):
        args = dict(filters=self._filters, order=self._order, limit=self._limit)
        args.update(kw)
        return FakeQuery(self._store, self._collection, **args)

    def where(self, field, op, value):
        if op == "in":
            assert len(value) <= 30, "Firestore limita `in` a 30 valores"
        return self._copy(filters=self._filters + [(field, op, value)])

    def select(self, _fields):
        return self._copy()

    def order_by(self, field, direction="ASCENDING"):
        return self._copy(order=(field, direction))

    def limit(self, n):
        return self._copy(limit=n)

    def stream(self):
        self._store.queries += 1
        docs = []
        for doc_id, data in list(self._store.data[self._collection].items()):
            if all(f in data and _OPS[op](data[f], v) for f, op, v in self._filters):
                docs.append((doc_id, data))
        if self._order:
            field, direction = self._order
            docs = [d for d in docs if field in d[1]]
            docs.sort(key=lambda d: d[1][field], reverse=direction == "DESCENDING")
        if self._limit is not None:
            docs = docs[: self._limit]
        self._store.reads += max(1, len(docs))
        for doc_id, data in docs:
            yield FakeSnapshot(FakeDocRef(self._store, self._collection, doc_id), data)


class FakeCollection(FakeQuery):
    _ids = itertools.count(1)

    def document(self, doc_id=None):
        return FakeDocRef(self._store, self._collection, doc_id or f"auto{next(self._ids)}")


class FakeBatch:
    def __init__(self, store):
        self._store = store
        self._ops = []

    def set(self, ref, data):
        self._ops.append(("set", ref, data))

    def delete(self, ref):
        self._ops.append(("delete", ref, None))

    def commit(self):
        for op, ref, data in self._ops:
            ref.set(data) if op == "set" else ref.delete()
        self._ops = []


class FakeFirestore:
    def __init__(self):
        self.data: dict[str, dict[str, dict]] = {}
        self.reads = 0
        self.writes = 0
        self.queries = 0
        self.get_all_calls = 0

    def collection(self, name):
        self.data.setdefault(name, {})
        return FakeCollection(self, name)

    def batch(self):
        return FakeBatch(self)

    def get_all(self, refs):
        self.get_all_calls += 1
        for ref in refs:
            yield ref.get()

    def add(self, collection, doc_id, **data):
        self.collection(collection)
        self.data[collection][doc_id] = data


@pytest.fixture
def fake_db(monkeypatch):
    db = FakeFirestore()
    monkeypatch.setattr(firestore_client, "_client", db)
    return db


# --------------------------------------------------------------------------- #
# Gemini fake
# --------------------------------------------------------------------------- #

_ITEM_RE = re.compile(r"### ITEM (\d+)\nFonte: .*\nTítulo original: (.*)\n")


def prompt_items(contents: str) -> list[tuple[int, str]]:
    """[(número, título original)] presentes no prompt de um lote."""
    return [(int(n), title) for n, title in _ITEM_RE.findall(contents)]


def curation_entry(index: int, title: str, score: int = 90, approved: bool | None = None) -> dict:
    approved = score > 75 if approved is None else approved
    return {
        "index": index,
        "title": f"Curado: {title}",
        "technical_summary": f"Resumo de {title}" if approved else "",
        "relevance_score": score,
        "tags": ["TESTE"],
        "is_quality_approved": approved,
        "tts_text": f"Texto falado de {title}" if approved else "",
    }


class FakeGemini:
    """Responde com `handler(contents, call_number)`; registra cada prompt enviado.

    O handler retorna o texto da resposta (str) ou levanta uma exceção.
    """

    def __init__(self, handler=None):
        self.calls: list[str] = []
        self.handler = handler or self.approve_all
        self.models = SimpleNamespace(generate_content=self._generate)

    @staticmethod
    def approve_all(contents, _call):
        return json.dumps([curation_entry(n, t) for n, t in prompt_items(contents)])

    def _generate(self, model, contents, config):
        self.calls.append(contents)
        return SimpleNamespace(text=self.handler(contents, len(self.calls)))


@pytest.fixture
def fake_gemini(monkeypatch):
    gemini = FakeGemini()
    monkeypatch.setattr(curate, "_client", gemini)
    monkeypatch.setattr(curate, "_sleep", lambda _s: None)
    monkeypatch.setattr(curate, "_rate_limiter", curate._RateLimiter())
    monkeypatch.setenv("GEMINI_REQUEST_DELAY_SECONDS", "0")
    return gemini


def make_item(title: str, minutes_ago: int = 0, raw: str = "conteúdo") -> dict:
    return {
        "source": "Fonte",
        "title": title,
        "url": f"https://example.com/{abs(hash(title))}",
        "published_at": datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc) - timedelta(minutes=minutes_ago),
        "raw_content": raw,
    }
