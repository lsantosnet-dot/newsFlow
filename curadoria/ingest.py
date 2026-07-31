"""Busca notícias em múltiplas fontes e normaliza no formato:

    {"source": str, "title": str, "url": str, "published_at": datetime, "raw_content": str}

As fontes não são mais fixas em código: cada perfil de curadoria traz sua própria
lista de fontes (ver `profiles` no Firestore), e cada fonte declara um `type` que
é resolvido no `SOURCE_ADAPTERS` para a função de ingestão correspondente.

Adicionar um feed novo (RSS/Atom) é só dado — não exige código Python.
"""

from __future__ import annotations

import gzip
import xml.etree.ElementTree as ET
import zlib
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import requests

HN_BASE_URL = "https://hacker-news.firebaseio.com/v0"
DEVTO_API_URL = "https://dev.to/api/articles"
ARXIV_API_URL = "http://export.arxiv.org/api/query"

HTTP_TIMEOUT = 15

# Vários portais (UOL, Carta Capital, g1) devolvem 403 para clientes sem
# User-Agent de browser. Accept-Encoding explícito é necessário porque o g1
# responde gzipado — ver `_decode_feed_bytes`.
FEED_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.8",
    "Accept-Encoding": "gzip, deflate",
}

# Namespaces usados por feeds Atom (ex: Martin Fowler) e RDF (ex: DW Brasil).
_ATOM_NS = "http://www.w3.org/2005/Atom"
_RDF_NS = "http://purl.org/rss/1.0/"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _decode_feed_bytes(content: bytes) -> bytes:
    """Descomprime o corpo do feed quando o servidor mente sobre o encoding.

    O RSS do g1 (`g1.globo.com/rss/...`) responde `content-type: application/xml`
    sem `Content-Encoding`, mas o corpo vem gzipado — o requests entrega os bytes
    crus e o parse XML falha. Detectamos o magic number do gzip/zlib e
    descomprimimos manualmente. Feeds normais passam intactos.
    """
    if content[:2] == b"\x1f\x8b":  # magic number do gzip
        try:
            return gzip.decompress(content)
        except (OSError, EOFError, zlib.error):
            return content
    return content


def _parse_feed_datetime(raw: str | None) -> datetime:
    """Converte data de feed (RFC 822 do RSS ou ISO 8601 do Atom) para datetime UTC."""
    if not raw:
        return _utcnow()

    raw = raw.strip()
    # RSS usa RFC 822 ("Mon, 21 Jul 2025 10:00:00 -0300").
    try:
        parsed = parsedate_to_datetime(raw)
        if parsed is not None:
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        pass

    # Atom usa ISO 8601 ("2025-07-21T10:00:00Z").
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return _utcnow()


def _strip_ns(tag: str) -> str:
    """Remove o namespace de um tag XML ('{http://...}entry' -> 'entry')."""
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _findtext_any_ns(element: ET.Element, name: str) -> str:
    """Busca o texto do primeiro filho com o nome dado, ignorando namespace."""
    for child in element:
        if _strip_ns(child.tag) == name and child.text:
            return child.text.strip()
    return ""


def _atom_link(entry: ET.Element) -> str:
    """Extrai a URL de um <entry> Atom, onde o link vem no atributo `href`."""
    for child in entry:
        if _strip_ns(child.tag) != "link":
            continue
        rel = child.get("rel")
        if rel in (None, "alternate") and child.get("href"):
            return child.get("href", "").strip()
    return ""


# --------------------------------------------------------------------------- #
# Adaptadores de fonte
# --------------------------------------------------------------------------- #


def ingest_rss(config: dict) -> list[dict]:
    """Adaptador genérico de RSS/Atom/RDF — cobre a maioria das fontes.

    Config: {"type": "rss", "name": str, "url": str, "limit": int (opcional)}
    """
    url = config.get("url")
    name = config.get("name") or url or "RSS"
    limit = int(config.get("limit") or 20)

    if not url:
        print(f"[ingest] {name}: fonte RSS sem 'url' — ignorando.")
        return []

    try:
        resp = requests.get(url, headers=FEED_HEADERS, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"[ingest] {name}: falha ao buscar feed ({exc})")
        return []

    try:
        root = ET.fromstring(_decode_feed_bytes(resp.content))
    except ET.ParseError as exc:
        print(f"[ingest] {name}: falha ao parsear feed ({exc})")
        return []

    # RSS 2.0 aninha os itens em <channel>; Atom e RDF os deixam na raiz.
    entries: list[ET.Element] = []
    channel = root.find("channel")
    if channel is not None:
        entries = channel.findall("item")
    if not entries:
        entries = root.findall(f"{{{_ATOM_NS}}}entry") or root.findall(f"{{{_RDF_NS}}}item")
    if not entries:
        entries = [child for child in root if _strip_ns(child.tag) in ("item", "entry")]

    items: list[dict] = []
    for entry in entries[:limit]:
        title = _findtext_any_ns(entry, "title").replace("\n", " ").strip()
        link = _findtext_any_ns(entry, "link") or _atom_link(entry)
        if not title or not link:
            continue

        published_raw = (
            _findtext_any_ns(entry, "pubDate")
            or _findtext_any_ns(entry, "published")
            or _findtext_any_ns(entry, "updated")
            or _findtext_any_ns(entry, "date")
        )
        summary = (
            _findtext_any_ns(entry, "description")
            or _findtext_any_ns(entry, "summary")
            or _findtext_any_ns(entry, "content")
        )

        items.append(
            {
                "source": name,
                "title": title,
                "url": link,
                "published_at": _parse_feed_datetime(published_raw),
                "raw_content": summary,
            }
        )

    print(f"[ingest] {name}: {len(items)} itens")
    return items


def ingest_hacker_news(config: dict | None = None) -> list[dict]:
    """Busca as top stories da Hacker News acima de um score/comentários mínimos.

    Config: {"type": "hackernews", "min_score": int, "min_comments": int, "limit": int}
    """
    config = config or {}
    name = config.get("name") or "Hacker News"
    min_score = int(config.get("min_score", 50))
    min_comments = int(config.get("min_comments", 10))
    limit = int(config.get("limit") or 60)

    items: list[dict] = []
    try:
        resp = requests.get(f"{HN_BASE_URL}/topstories.json", timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        story_ids = resp.json()[:limit]
    except requests.RequestException as exc:
        print(f"[ingest] {name}: falha ao buscar topstories ({exc})")
        return items

    for story_id in story_ids:
        try:
            item_resp = requests.get(f"{HN_BASE_URL}/item/{story_id}.json", timeout=HTTP_TIMEOUT)
            item_resp.raise_for_status()
            story = item_resp.json()
        except requests.RequestException as exc:
            print(f"[ingest] {name}: falha ao buscar item {story_id} ({exc})")
            continue

        if not story or story.get("type") != "story":
            continue
        if story.get("score", 0) < min_score or story.get("descendants", 0) < min_comments:
            continue
        url = story.get("url")
        if not url:
            # Self-post da HN sem link externo (Ask HN, Show HN sem URL) — não é notícia externa.
            continue

        items.append(
            {
                "source": name,
                "title": story.get("title", "").strip(),
                "url": url,
                "published_at": datetime.fromtimestamp(story.get("time", 0), tz=timezone.utc),
                "raw_content": story.get("text", "") or story.get("title", ""),
            }
        )

    print(f"[ingest] {name}: {len(items)} itens após filtro (score>={min_score}, comments>={min_comments})")
    return items


def ingest_devto(config: dict | None = None) -> list[dict]:
    """Busca artigos recentes do Dev.to filtrados por tags.

    Config: {"type": "devto", "tags": list[str], "per_tag": int}
    """
    config = config or {}
    name = config.get("name") or "Dev.to"
    per_tag = int(config.get("per_tag") or 15)

    tags = config.get("tags") or [
        "ai", "machinelearning", "rust", "golang", "architecture", "softwareengineering", "llm",
    ]
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]

    items: list[dict] = []
    seen_urls: set[str] = set()

    for tag in tags:
        try:
            resp = requests.get(
                DEVTO_API_URL,
                params={"tag": tag, "per_page": per_tag, "top": 7},
                timeout=HTTP_TIMEOUT,
            )
            resp.raise_for_status()
            articles = resp.json()
        except requests.RequestException as exc:
            print(f"[ingest] {name}: falha ao buscar tag '{tag}' ({exc})")
            continue

        for article in articles:
            url = article.get("url")
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)

            items.append(
                {
                    "source": name,
                    "title": (article.get("title") or "").strip(),
                    "url": url,
                    "published_at": _parse_feed_datetime(article.get("published_at")),
                    "raw_content": article.get("description", "") or "",
                }
            )

    print(f"[ingest] {name}: {len(items)} itens (tags={tags})")
    return items


def ingest_arxiv(config: dict | None = None) -> list[dict]:
    """Busca papers recentes do ArXiv nas categorias configuradas.

    Config: {"type": "arxiv", "categories": list[str], "limit": int}
    """
    config = config or {}
    name = config.get("name") or "ArXiv"
    categories = config.get("categories") or ["cs.SE", "cs.AI", "cs.LG"]
    max_results = int(config.get("limit") or 30)

    search_query = " OR ".join(f"cat:{cat}" for cat in categories)
    items: list[dict] = []

    try:
        resp = requests.get(
            ARXIV_API_URL,
            params={
                "search_query": search_query,
                "sortBy": "submittedDate",
                "sortOrder": "descending",
                "max_results": max_results,
            },
            timeout=HTTP_TIMEOUT,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"[ingest] {name}: falha ao buscar feed ({exc})")
        return items

    try:
        root = ET.fromstring(_decode_feed_bytes(resp.content))
    except ET.ParseError as exc:
        print(f"[ingest] {name}: falha ao parsear resposta ({exc})")
        return items

    for entry in root.findall(f"{{{_ATOM_NS}}}entry"):
        title = _findtext_any_ns(entry, "title").replace("\n", " ").strip()
        url = _findtext_any_ns(entry, "id")
        if not title or not url:
            continue

        items.append(
            {
                "source": name,
                "title": title,
                "url": url,
                "published_at": _parse_feed_datetime(_findtext_any_ns(entry, "published")),
                "raw_content": _findtext_any_ns(entry, "summary"),
            }
        )

    print(f"[ingest] {name}: {len(items)} itens (categorias={categories})")
    return items


# Registry de adaptadores: o campo `type` de cada fonte do perfil resolve aqui.
SOURCE_ADAPTERS = {
    "rss": ingest_rss,
    "hackernews": ingest_hacker_news,
    "devto": ingest_devto,
    "arxiv": ingest_arxiv,
}


def ingest_from_profile(profile: dict) -> list[dict]:
    """Roda todas as fontes declaradas no perfil e retorna a lista combinada.

    Falhas são isoladas por fonte: um feed fora do ar (ou um `type` desconhecido)
    não derruba a rodada inteira. Aplica `max_items_per_run` no fim para proteger
    a cota do Gemini e o timeout do job no GitHub Actions.
    """
    sources = profile.get("sources") or []
    profile_name = profile.get("name", "?")

    if not sources:
        print(f"[ingest] Perfil {profile_name!r} não tem fontes configuradas.")
        return []

    all_items: list[dict] = []
    for source in sources:
        source_type = source.get("type")
        adapter = SOURCE_ADAPTERS.get(source_type)

        if adapter is None:
            print(f"[ingest] Tipo de fonte desconhecido: {source_type!r} — ignorando.")
            continue

        try:
            all_items.extend(adapter(source))
        except Exception as exc:  # noqa: BLE001 - uma fonte quebrada não pode parar as outras
            name = source.get("name") or source_type
            print(f"[ingest] {name}: erro inesperado ({exc}) — pulando fonte.")

    print(f"[ingest] Total combinado do perfil {profile_name!r}: {len(all_items)} itens")

    max_items = int(profile.get("max_items_per_run") or 40)
    if len(all_items) > max_items:
        # Prioriza o mais recente: com muitas fontes, o teto é atingido rápido e
        # queremos gastar a cota do Gemini com o que acabou de sair.
        all_items.sort(key=lambda item: item["published_at"], reverse=True)
        all_items = all_items[:max_items]
        print(f"[ingest] Truncado para {max_items} itens (max_items_per_run)")

    return all_items


if __name__ == "__main__":
    demo_profile = {
        "name": "Demo",
        "max_items_per_run": 20,
        "sources": [
            {"type": "rss", "name": "GitHub Blog", "url": "https://github.blog/feed/", "limit": 10},
            {"type": "hackernews", "min_score": 100, "min_comments": 30, "limit": 30},
        ],
    }
    for item in ingest_from_profile(demo_profile):
        print(f"- [{item['source']}] {item['title']} ({item['url']})")
