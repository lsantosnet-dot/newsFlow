"""Migração one-off para o modelo de perfis de curadoria.

Faz duas coisas, ambas idempotentes (pode rodar mais de uma vez sem estragar nada):

1. Semeia os perfis dos presets (`assets/presets/profiles.json`) no Firestore,
   caso a coleção `profiles` ainda esteja vazia. O app também semeia no primeiro
   launch — rodar aqui garante que o pipeline funcione mesmo antes disso.
2. Marca os artigos existentes (que não têm `profile_id`) com o perfil de
   tecnologia, que era o comportamento único antes desta mudança. Sem isso, eles
   sumiriam do feed, já que o app passa a filtrar por `profile_id`.

Uso:
    python migrate.py            # aplica a migração
    python migrate.py --dry-run  # só mostra o que faria
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from firestore_client import (  # noqa: E402
    ARTICLES_COLLECTION,
    BATCH_SIZE,
    PROFILES_COLLECTION,
    get_client,
)

PRESETS_PATH = Path(__file__).resolve().parent.parent / "assets" / "presets" / "profiles.json"

# Perfil que os artigos pré-existentes recebem: antes da mudança, o pipeline só
# curava conteúdo de engenharia de software.
LEGACY_PROFILE_ID = "tech"


def load_presets() -> list[dict]:
    with PRESETS_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)["presets"]


def seed_profiles(dry_run: bool = False) -> int:
    """Cria os perfis dos presets se a coleção `profiles` estiver vazia."""
    client = get_client()
    existing = list(client.collection(PROFILES_COLLECTION).limit(1).stream())

    if existing:
        print("[migrate] Coleção 'profiles' já tem documentos — pulando o seed.")
        return 0

    presets = load_presets()
    print(f"[migrate] Coleção 'profiles' vazia — semeando {len(presets)} perfis dos presets.")

    if dry_run:
        for preset in presets:
            flag = " (ativo)" if preset.get("active") else ""
            print(f"[migrate]   [dry-run] criaria perfil {preset['id']!r} — {preset['name']}{flag}")
        return len(presets)

    for preset in presets:
        profile = {k: v for k, v in preset.items() if k != "id"}
        profile["pending_cleanup"] = None
        client.collection(PROFILES_COLLECTION).document(preset["id"]).set(profile)
        flag = " (ativo)" if preset.get("active") else ""
        print(f"[migrate]   perfil criado: {preset['id']} — {preset['name']}{flag}")

    return len(presets)


def migrate_articles(dry_run: bool = False) -> int:
    """Marca artigos sem `profile_id` com o perfil legado (tech)."""
    client = get_client()
    updated = 0
    batch = client.batch()
    pending = 0

    for doc in client.collection(ARTICLES_COLLECTION).stream():
        data = doc.to_dict()
        if data.get("profile_id"):
            continue

        if dry_run:
            updated += 1
            continue

        batch.update(doc.reference, {"profile_id": LEGACY_PROFILE_ID, "config_version": 1})
        pending += 1
        updated += 1

        if pending >= BATCH_SIZE:
            batch.commit()
            batch = client.batch()
            pending = 0

    if pending > 0 and not dry_run:
        batch.commit()

    prefix = "[dry-run] " if dry_run else ""
    print(f"[migrate] {prefix}{updated} artigos marcados com profile_id={LEGACY_PROFILE_ID!r}.")
    return updated


def run(dry_run: bool = False) -> None:
    print("=" * 60)
    print("Migração para perfis de curadoria" + (" (DRY RUN)" if dry_run else ""))
    print("=" * 60)

    seeded = seed_profiles(dry_run)
    migrated = migrate_articles(dry_run)

    print()
    print(f"Perfis semeados:    {seeded}")
    print(f"Artigos migrados:   {migrated}")
    if dry_run:
        print("\nNada foi gravado (--dry-run). Rode sem a flag para aplicar.")
    print("=" * 60)


if __name__ == "__main__":
    try:
        run(dry_run="--dry-run" in sys.argv)
    except Exception as exc:  # noqa: BLE001
        print(f"[migrate] Erro fatal: {exc}", file=sys.stderr)
        raise
