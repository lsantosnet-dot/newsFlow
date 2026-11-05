"""Cria no Firestore um perfil a partir de um preset de `assets/presets/profiles.json`.

Serve para adicionar um perfil novo sem passar pelo app: o preset vira o documento
`profiles/{id}` — o mesmo id do preset, para que seja fácil localizar e apagar.

Seguro por construção:
  - NUNCA sobrescreve: se `profiles/{id}` já existe, não faz nada (e diz isso).
  - O perfil nasce inativo (`active: false`); o app continua exibindo o seu perfil atual.
  - `--dry-run` só mostra o que seria criado.

Uso:
    python seed_preset.py github_ideias            # cria o perfil
    python seed_preset.py github_ideias --dry-run  # só mostra
    python seed_preset.py --list                   # lista os presets disponíveis
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PRESETS_PATH = Path(__file__).resolve().parent.parent / "assets" / "presets" / "profiles.json"


def load_presets(path: Path = PRESETS_PATH) -> dict[str, dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {preset["id"]: preset for preset in data["presets"]}


def build_profile_doc(preset: dict) -> dict:
    """Documento do Firestore no mesmo formato que o app grava (`Profile.toMap`)."""
    return {
        "name": preset["name"],
        "icon": preset.get("icon", "newspaper"),
        "active": False,  # um perfil novo nunca nasce ativo (invariante do app)
        "config_version": 1,
        "sources": preset.get("sources", []),
        "curation": preset.get("curation", {}),
        "max_items_per_run": preset.get("max_items_per_run", 40),
        "dedupe_window_days": preset.get("dedupe_window_days", 7),
        "cleanup_grace_hours": preset.get("cleanup_grace_hours", 48),
        "inactive_retention_days": preset.get("inactive_retention_days", 30),
        "pending_cleanup": None,
    }


def seed_preset(preset_id: str, *, dry_run: bool = False, client=None, presets: dict | None = None) -> str:
    """Cria o perfil. Retorna 'created', 'exists' ou 'dry-run'."""
    presets = presets if presets is not None else load_presets()
    if preset_id not in presets:
        raise SystemExit(f"Preset {preset_id!r} não existe. Disponíveis: {', '.join(sorted(presets))}")

    doc = build_profile_doc(presets[preset_id])

    if dry_run:
        print(f"[seed] (dry-run) criaria profiles/{preset_id}: {doc['name']!r}, {len(doc['sources'])} fontes")
        return "dry-run"

    if client is None:
        from firestore_client import PROFILES_COLLECTION, get_client

        client = get_client()
        collection = PROFILES_COLLECTION
    else:
        collection = "profiles"

    ref = client.collection(collection).document(preset_id)
    if ref.get().exists:
        print(f"[seed] profiles/{preset_id} já existe — nada foi alterado.")
        return "exists"

    ref.set(doc)
    print(f"[seed] Criado profiles/{preset_id}: {doc['name']!r}, {len(doc['sources'])} fontes (inativo).")
    return "created"


def main(argv: list[str]) -> int:
    if "--list" in argv:
        for preset_id, preset in load_presets().items():
            print(f"{preset_id}: {preset['name']} ({len(preset.get('sources', []))} fontes)")
        return 0

    ids = [a for a in argv if not a.startswith("--")]
    if len(ids) != 1:
        print(__doc__)
        return 2

    seed_preset(ids[0], dry_run="--dry-run" in argv)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
