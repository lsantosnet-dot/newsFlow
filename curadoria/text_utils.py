"""Normalização de título e geração de hash para deduplicação."""

from __future__ import annotations

import hashlib
import re
import unicodedata

# Stopwords em PT e EN (títulos das fontes vêm nos dois idiomas).
_STOPWORDS = {
    "a", "o", "os", "as", "um", "uma", "uns", "umas", "de", "do", "da", "dos", "das",
    "em", "no", "na", "nos", "nas", "para", "por", "com", "sem", "e", "ou", "que",
    "the", "a", "an", "of", "in", "on", "for", "to", "and", "or", "is", "are", "with",
}

_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_title(title: str) -> str:
    """Lowercase, remove acentos, pontuação e stopwords."""
    text = unicodedata.normalize("NFKD", title.lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = _PUNCT_RE.sub(" ", text)
    tokens = [tok for tok in text.split() if tok not in _STOPWORDS]
    return _WHITESPACE_RE.sub(" ", " ".join(tokens)).strip()


def title_hash(title: str) -> str:
    """Hash SHA-256 (hex) do título normalizado, usado como chave de dedupe."""
    normalized = normalize_title(title)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
