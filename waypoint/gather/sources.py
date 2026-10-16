"""Source adapters: turn each feed in feeds.yaml into Candidates (PLAN.md Phase 3, task 1).

RSS/HN/arXiv adapters return metadata only; the article text is fetched later by the
pipeline. Wikipedia adapters fill `text` straight from the MediaWiki API (plain text is
better than scraping the HTML).
"""

from __future__ import annotations

import calendar
import datetime as dt
import html
import logging
import random
import re
from pathlib import Path
from typing import Literal
from urllib.parse import quote, unquote

import feedparser
import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from waypoint.dedupe import canonical_url
from waypoint.gather.fetch import Fetcher, FetchError
from waypoint.models import Candidate, SourceKind

log = logging.getLogger(__name__)

FEEDS_PATH = Path(__file__).with_name("feeds.yaml")
WIKI_API = "https://en.wikipedia.org/w/api.php"
WIKI_SKIP_TITLE = re.compile(r"^(List|Lists|Outline|Index|Glossary|Timeline) of ", re.I)
WIKI_END_SECTIONS = re.compile(
    r"^==+\s*(See also|References|Notes|External links|Further reading|Bibliography|"
    r"Sources|Citations|Footnotes)\s*==+\s*$", re.M | re.I)
WIKI_HEADING = re.compile(r"^==+\s*(.*?)\s*==+\s*$", re.M)
DYK_BOLD_LINK = re.compile(r'<b[^>]*>\s*(?:<i[^>]*>\s*)?<a[^>]*href="[^"]*/wiki/([^"#?]+)"')


class Feed(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    type: Literal["rss", "hn", "arxiv", "wikipedia_featured", "wikipedia_category"]
    publisher: str
    kind: SourceKind
    faculties: list[str] = Field(default_factory=list)
    url: str | None = None
    max_items: int | None = Field(default=None, ge=1)
    # type-specific
    categories: list[str] = Field(default_factory=list)  # wikipedia_category
    query: str | None = None                             # arxiv
    min_score: int = 0                                   # hn
    scan: int = 15                                       # hn


class FeedsFile(BaseModel):
    feeds: list[Feed]


def load_feeds(path: Path = FEEDS_PATH) -> list[Feed]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    feeds = FeedsFile.model_validate(data).feeds
    ids = [f.id for f in feeds]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate feed ids in feeds.yaml")
    return feeds


def strip_html(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text or ""))).strip()


def _entry_date(entry) -> dt.date | None:
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            return dt.datetime.fromtimestamp(calendar.timegm(t), dt.UTC).date()
    return None


def _candidate(feed: Feed, **kw) -> Candidate | None:
    try:
        return Candidate(feed=feed.id, publisher=feed.publisher, kind=feed.kind,
                         faculty_hints=feed.faculties, **kw)
    except ValidationError as e:
        log.info("%s: skipping item %r: %s", feed.id, kw.get("url"), e.errors()[0]["msg"])
        return None


# --- RSS / Atom -----------------------------------------------------------------------------


def parse_rss(feed: Feed, body: str, limit: int) -> list[Candidate]:
    parsed = feedparser.parse(body)
    out: list[Candidate] = []
    for entry in parsed.entries:
        link, title = entry.get("link"), strip_html(entry.get("title", ""))
        if not link or not title:
            continue
        c = _candidate(feed, url=canonical_url(link), title=title, published=_entry_date(entry),
                       summary=strip_html(entry.get("summary", ""))[:600])
        if c:
            out.append(c)
        if len(out) >= limit:
            break
    return out


# --- Hacker News ----------------------------------------------------------------------------


def collect_hn(feed: Feed, fetcher: Fetcher, limit: int) -> list[Candidate]:
    base = feed.url or "https://hacker-news.firebaseio.com/v0/"
    ids = fetcher.get_json(base + "topstories.json")
    out: list[Candidate] = []
    for item_id in list(ids)[: feed.scan]:
        item = fetcher.get_json(f"{base}item/{item_id}.json") or {}
        url = item.get("url")
        if item.get("type") != "story" or not url or item.get("score", 0) < feed.min_score:
            continue
        title = re.sub(r"^(Show|Ask|Tell|Launch) HN:\s*", "", item.get("title", ""))
        published = dt.datetime.fromtimestamp(item.get("time", 0), dt.UTC).date()
        c = _candidate(feed, url=canonical_url(url), title=title, published=published)
        if c:
            out.append(c)
        if len(out) >= limit:
            break
    return out


# --- arXiv ----------------------------------------------------------------------------------


def parse_arxiv(feed: Feed, body: str, limit: int) -> list[Candidate]:
    """arXiv API Atom. Links point at the HTML full text (arxiv.org/html/<id>), which most
    recent papers have; papers without it fail the word floor and drop out."""
    out: list[Candidate] = []
    for entry in feedparser.parse(body).entries:
        m = re.search(r"arxiv\.org/abs/([^v\s]+)", entry.get("id", ""))
        if not m:
            continue
        c = _candidate(feed, url=f"https://arxiv.org/html/{m.group(1)}",
                       title=strip_html(entry.get("title", "")), published=_entry_date(entry),
                       summary=strip_html(entry.get("summary", ""))[:600])
        if c:
            out.append(c)
        if len(out) >= limit:
            break
    return out


# --- Wikipedia ------------------------------------------------------------------------------


def clean_wiki_extract(text: str) -> str:
    """Cut a plain-text extract at the reference sections and drop heading markers."""
    m = WIKI_END_SECTIONS.search(text)
    if m:
        text = text[: m.start()]
    text = WIKI_HEADING.sub(r"\1.", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def wiki_page(fetcher: Fetcher, title: str) -> tuple[str, str, str] | None:
    """(canonical title, URL, plain text) for an article, or None if it doesn't exist."""
    data = fetcher.get_json(WIKI_API, {
        "action": "query", "prop": "extracts|info", "inprop": "url", "explaintext": 1,
        "exsectionformat": "wiki", "redirects": 1, "titles": title, "format": "json",
        "formatversion": 2,
    })
    pages = (data or {}).get("query", {}).get("pages", [])
    if not pages or pages[0].get("missing") or "extract" not in pages[0]:
        return None
    p = pages[0]
    return p["title"], p["fullurl"], clean_wiki_extract(p["extract"])


def _wiki_candidate(feed: Feed, fetcher: Fetcher, title: str,
                    summary: str = "") -> Candidate | None:
    try:
        page = wiki_page(fetcher, title)
    except FetchError as e:
        log.info("%s: %s", feed.id, e)
        return None
    if page is None:
        return None
    name, url, text = page
    c = _candidate(feed, url=canonical_url(url), title=name, summary=summary)
    if c:
        c.text, c.word_count = text, len(text.split())
    return c


def featured_titles(data: dict) -> list[tuple[str, str]]:
    """(title, summary) for today's featured article and the bold "Did you know" articles."""
    out: list[tuple[str, str]] = []
    tfa = data.get("tfa") or {}
    if tfa.get("title"):
        out.append((unquote(tfa["title"]).replace("_", " "), tfa.get("extract", "")[:600]))
    for item in data.get("dyk") or []:
        m = DYK_BOLD_LINK.search(item.get("html", ""))
        if m:
            out.append((unquote(m.group(1)).replace("_", " "), item.get("text", "")))
    return out


def collect_wikipedia_featured(feed: Feed, fetcher: Fetcher, limit: int,
                               today: dt.date) -> list[Candidate]:
    base = feed.url or "https://en.wikipedia.org/api/rest_v1/feed/featured/"
    data = fetcher.get_json(f"{base}{today:%Y/%m/%d}")
    out: list[Candidate] = []
    for title, summary in featured_titles(data or {}):
        if WIKI_SKIP_TITLE.match(title):
            continue
        c = _wiki_candidate(feed, fetcher, title, summary)
        if c:
            out.append(c)
        if len(out) >= limit:
            break
    return out


def category_members(fetcher: Fetcher, category: str) -> list[str]:
    data = fetcher.get_json(WIKI_API, {
        "action": "query", "list": "categorymembers", "cmtitle": f"Category:{category}",
        "cmtype": "page", "cmnamespace": 0, "cmlimit": 500, "format": "json",
        "formatversion": 2,
    })
    titles = [m["title"] for m in (data or {}).get("query", {}).get("categorymembers", [])]
    return [t for t in titles if not WIKI_SKIP_TITLE.match(t)
            and t.lower() != category.lower()]


def collect_wikipedia_category(feed: Feed, fetcher: Fetcher, limit: int, rng: random.Random,
                               exclude: set[str] | None = None) -> list[Candidate]:
    """`limit` random articles from the feed's categories. `rng` is seeded per run date so a
    re-run on the same day picks the same articles (and hits the cache)."""
    pool: list[str] = []
    for cat in feed.categories:
        try:
            pool.extend(category_members(fetcher, cat))
        except FetchError as e:
            log.info("%s: %s", feed.id, e)
    pool = sorted(set(pool) - (exclude or set()))
    rng.shuffle(pool)
    out: list[Candidate] = []
    for title in pool[: limit * 3]:  # some picks are stubs; try a few extra
        c = _wiki_candidate(feed, fetcher, title)
        if c and c.word_count:
            out.append(c)
        if len(out) >= limit:
            break
    return out


# --- dispatcher -----------------------------------------------------------------------------


def collect(feed: Feed, fetcher: Fetcher, default_limit: int, today: dt.date,
            rng: random.Random) -> list[Candidate]:
    """All candidates from one feed. Network errors are logged and give an empty list."""
    limit = feed.max_items or default_limit
    try:
        if feed.type == "rss":
            body, _ = fetcher.get_text(feed.url or "", check_robots=False)
            return parse_rss(feed, body, limit)
        if feed.type == "hn":
            return collect_hn(feed, fetcher, limit)
        if feed.type == "arxiv":
            url = (f"{feed.url}?search_query={quote(feed.query or '')}"
                   f"&sortBy=submittedDate&sortOrder=descending&max_results={limit * 3}")
            body, _ = fetcher.get_text(url, check_robots=False)
            return parse_arxiv(feed, body, limit)
        if feed.type == "wikipedia_featured":
            return collect_wikipedia_featured(feed, fetcher, limit, today)
        if feed.type == "wikipedia_category":
            return collect_wikipedia_category(feed, fetcher, limit, rng)
    except FetchError as e:
        log.warning("feed %s failed: %s", feed.id, e)
        return []
    raise AssertionError(feed.type)
