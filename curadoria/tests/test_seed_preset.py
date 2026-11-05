"""Testes do seed_preset — Firestore substituído por um fake mínimo."""

from __future__ import annotations

import pytest
import seed_preset


class _Ref:
    def __init__(self, store, doc_id):
        self.store, self.doc_id = store, doc_id

    def get(self):
        return type("Snap", (), {"exists": self.doc_id in self.store})()

    def set(self, data):
        self.store[self.doc_id] = data


class _Client:
    def __init__(self, store):
        self.store = store

    def collection(self, name):
        assert name == "profiles"
        return type("Col", (), {"document": lambda _s, doc_id: _Ref(self.store, doc_id)})()


PRESETS = {
    "x": {
        "id": "x",
        "name": "Perfil X",
        "icon": "lightbulb",
        "active": True,  # o preset diz ativo, mas o perfil criado nunca pode nascer ativo
        "config_version": 7,
        "sources": [{"type": "github", "queries": ["topic:k"]}],
        "curation": {"persona": "p"},
    }
}


def test_cria_o_perfil_inativo_com_o_formato_do_app():
    store = {}
    assert seed_preset.seed_preset("x", client=_Client(store), presets=PRESETS) == "created"

    doc = store["x"]
    assert doc["active"] is False and doc["config_version"] == 1
    assert doc["pending_cleanup"] is None
    assert doc["sources"] == [{"type": "github", "queries": ["topic:k"]}]
    assert doc["max_items_per_run"] == 40  # default quando o preset não define
    assert "id" not in doc


def test_nunca_sobrescreve_um_perfil_existente():
    store = {"x": {"name": "Editado pelo usuário"}}
    assert seed_preset.seed_preset("x", client=_Client(store), presets=PRESETS) == "exists"
    assert store["x"] == {"name": "Editado pelo usuário"}


def test_dry_run_nao_toca_no_firestore():
    store = {}
    assert seed_preset.seed_preset("x", dry_run=True, client=_Client(store), presets=PRESETS) == "dry-run"
    assert store == {}


def test_preset_inexistente_aborta():
    with pytest.raises(SystemExit):
        seed_preset.seed_preset("nao-existe", presets=PRESETS)


def test_preset_real_github_ideias_e_valido():
    presets = seed_preset.load_presets()
    doc = seed_preset.build_profile_doc(presets["github_ideias"])
    assert doc["name"] == "Ideias para Construir"
    types = {s["type"] for s in doc["sources"]}
    assert types == {"github", "rss"}
    # todo tipo usado pelo preset tem adaptador registrado
    import ingest

    assert types <= set(ingest.SOURCE_ADAPTERS)
    assert doc["curation"]["summary_label"] == "Ideia de projeto"
