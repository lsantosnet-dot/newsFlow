"""Busca notícias tech em múltiplas fontes e normaliza no formato:

    {"source": str, "title": str, "url": str, "published_at": datetime, "raw_content": str}
"""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import requests

HN_BASE_URL = "https://hacker-news.firebaseio.com/v0"
DEVTO_API_URL = "https://dev.to/api/articles"
GITHUB_BLOG_RSS_URL = "https://github.blog/feed/"
ARXIV_API_URL = "http://export.arxiv.org/api/query"

HTTP_TIMEOUT = 15


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def ingest_hacker_news(min_score: int | None = None, min_comments: int | None = None, limit: int = 60) -> list[dict]:
    """Busca as top stories da Hacker News acima de um score/comentários mínimos."""
    min_score = min_score if min_score is not None else _env_int("HN_MIN_SCORE", 50)
    min_comments = min_comments if min_comments is not None else _env_int("HN_MIN_COMMENTS", 10)

    items: list[dict] = []
    try:
        resp = requests.get(f"{HN_BASE_URL}/topstories.json", timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        story_ids = resp.json()[:limit]
    except requests.RequestException as exc:
        print(f"[ingest] Hacker News: falha ao buscar topstories ({exc})")
        return items

    for story_id in story_ids:
        try:
            item_resp = requests.get(f"{HN_BASE_URL}/item/{story_id}.json", timeout=HTTP_TIMEOUT)
            item_resp.raise_for_status()
            story = item_resp.json()
        except requests.RequestException as exc:
            print(f"[ingest] Hacker News: falha ao buscar item {story_id} ({exc})")
            continue

        if not story or story.get("type") != "story":
            continue
        if story.get("score", 0) < min_score or story.get("descendants", 0) < min_comments:
            continue
        url = story.get("url")
        if not url:
            # Self-post da HN sem link externo (Ask HN, Show HN sem URL) — não é notícia técnica externa.
            continue

        items.append(
            {
                "source": "Hacker News",
                "title": story.get("title", "").strip(),
                "url": url,
                "published_at": datetime.fromtimestamp(story.get("time", 0), tz=timezone.utc),
                "raw_content": story.get("text", "") or story.get("title", ""),
            }
        )

    print(f"[ingest] Hacker News: {len(items)} itens após filtro (score>={min_score}, comments>={min_comments})")
    return items


def ingest_devto(tags: list[str] | None = None, per_tag: int = 15) -> list[dict]:
    """Busca artigos recentes do Dev.to filtrados por tags relevantes."""
    if tags is None:
        raw_tags = os.environ.get(
            "DEVTOO_TAGS", "ai,machinelearning,rust,golang,architecture,softwareengineering,llm"
        )
        tags = [t.strip() for t in raw_tags.split(",") if t.strip()]

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
            print(f"[ingest] Dev.to: falha ao buscar tag '{tag}' ({exc})")
            continue

        for article in articles:
            url = article.get("url")
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)

            published_raw = article.get("published_at")
            try:
                published_at = datetime.fromisoformat(published_raw.replace("Z", "+00:00")) if published_raw else _utcnow()
            except ValueError:
                published_at = _utcnow()

            items.append(
                {
                    "source": "Dev.to",
                    "title": (article.get("title") or "").strip(),
                    "url": url,
                    "published_at": published_at,
                    "raw_content": article.get("description", "") or "",
                }
            )

    print(f"[ingest] Dev.to: {len(items)} itens (tags={tags})")
    return items


def ingest_github_blog(limit: int = 20) -> list[dict]:
    """Busca posts recentes do RSS oficial do GitHub Blog."""
    items: list[dict] = []
    try:
        resp = requests.get(GITHUB_BLOG_RSS_URL, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"[ingest] GitHub Blog: falha ao buscar RSS ({exc})")
        return items

    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError as exc:
        print(f"[ingest] GitHub Blog: falha ao parsear RSS ({exc})")
        return items

    channel = root.find("channel")
    if channel is None:
        return items

    for entry in channel.findall("item")[:limit]:
        title = (entry.findtext("title") or "").strip()
        link = (entry.findtext("link") or "").strip()
        if not title or not link:
            continue

        pub_date_raw = entry.findtext("pubDate")
        try:
            published_at = parsedate_to_datetime(pub_date_raw) if pub_date_raw else _utcnow()
            if published_at.tzinfo is None:
                published_at = published_at.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            published_at = _utcnow()

        description = (entry.findtext("description") or "").strip()

        items.append(
            {
                "source": "GitHub Blog",
                "title": title,
                "url": link,
                "published_at": published_at,
                "raw_content": description,
            }
        )

    print(f"[ingest] GitHub Blog: {len(items)} itens")
    return items


def ingest_arxiv(categories: list[str] | None = None, max_results: int = 30) -> list[dict]:
    """Busca papers recentes do ArXiv nas categorias de interesse (cs.SE, cs.AI, cs.LG)."""
    if categories is None:
        categories = ["cs.SE", "cs.AI", "cs.LG"]

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
        print(f"[ingest] ArXiv: falha ao buscar feed ({exc})")
        return items

    try:
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        root = ET.fromstring(resp.content)
    except ET.ParseError as exc:
        print(f"[ingest] ArXiv: falha ao parsear resposta ({exc})")
        return items

    for entry in root.findall("atom:entry", ns):
        title = (entry.findtext("atom:title", default="", namespaces=ns) or "").strip().replace("\n", " ")
        summary = (entry.findtext("atom:summary", default="", namespaces=ns) or "").strip()
        url = (entry.findtext("atom:id", default="", namespaces=ns) or "").strip()
        published_raw = entry.findtext("atom:published", default="", namespaces=ns)

        if not title or not url:
            continue

        try:
            published_at = datetime.fromisoformat(published_raw.replace("Z", "+00:00")) if published_raw else _utcnow()
        except ValueError:
            published_at = _utcnow()

        items.append(
            {
                "source": "ArXiv",
                "title": title,
                "url": url,
                "published_at": published_at,
                "raw_content": summary,
            }
        )

    print(f"[ingest] ArXiv: {len(items)} itens (categorias={categories})")
    return items


def ingest_all_sources() -> list[dict]:
    """Roda todas as fontes e retorna a lista combinada de itens normalizados."""
    all_items: list[dict] = []
    all_items.extend(ingest_hacker_news())
    all_items.extend(ingest_devto())
    all_items.extend(ingest_github_blog())
    all_items.extend(ingest_arxiv())
    print(f"[ingest] Total combinado: {len(all_items)} itens")
    return all_items


if __name__ == "__main__":
    for item in ingest_all_sources():
        print(f"- [{item['source']}] {item['title']} ({item['url']})")
