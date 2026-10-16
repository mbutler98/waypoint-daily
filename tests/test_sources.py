import datetime as dt
import json
import random

from helpers import GATHER_FIXTURES, mock_transport

from waypoint.gather.fetch import Fetcher
from waypoint.gather.sources import (
    Feed,
    clean_wiki_extract,
    collect,
    featured_titles,
    load_feeds,
    parse_arxiv,
    parse_rss,
)
from waypoint.models import FACULTIES


def feed(**kw) -> Feed:
    base = dict(id="t", type="rss", publisher="Pub", kind="news", faculties=["Space"])
    return Feed(**(base | kw))


def read(name: str) -> str:
    return (GATHER_FIXTURES / name).read_text(encoding="utf-8")


def test_feeds_yaml_is_valid_and_covers_every_faculty():
    feeds = load_feeds()
    covered = {f for fd in feeds for f in fd.faculties}
    assert covered == set(FACULTIES)
    for fd in feeds:
        assert set(fd.faculties) <= set(FACULTIES), fd.id
        if fd.type == "wikipedia_category":
            assert fd.categories and fd.kind == "evergreen"
        else:
            assert fd.url and fd.url.startswith("https://"), fd.id


def test_parse_rss_fixture():
    items = parse_rss(feed(publisher="ScienceDaily"), read("sciencedaily.xml"), limit=2)
    assert len(items) == 2
    c = items[0]
    assert c.url.startswith("https://www.sciencedaily.com/releases/2026/10/")
    assert c.published == dt.date(2026, 10, 16)  # 23:45 EDT on the 15th is the 16th in UTC
    assert c.title and c.summary and c.faculty_hints == ["Space"] and c.kind == "news"


def test_parse_atom_fixture():
    items = parse_rss(feed(publisher="The Conversation"), read("conversation.atom"), limit=5)
    assert len(items) == 2
    assert all(c.url.startswith("https://theconversation.com/") for c in items)
    assert all(c.published for c in items)


def test_parse_arxiv_points_at_html_full_text():
    items = parse_arxiv(feed(type="arxiv", kind="paper"), read("arxiv.atom"), limit=5)
    assert len(items) == 2
    assert all(c.url.startswith("https://arxiv.org/html/2610.") for c in items)
    assert "v1" not in items[0].url


def test_featured_titles_reads_tfa_and_bold_dyk_links():
    data = json.loads(read("wiki_featured.json"))
    titles = [t for t, _ in featured_titles(data)]
    assert titles[0] == "The Hunger (collection)"
    assert "Mary Jane Kekulani Fayerweather" in titles
    assert "Mele (Hawaiian term)" not in titles  # only the bold article, not other links


def test_clean_wiki_extract_cuts_reference_sections():
    raw = "Intro text.\n\n== History ==\nOld times.\n\n== See also ==\nOther\n== References ==\nx"
    assert clean_wiki_extract(raw) == "Intro text.\n\nHistory.\nOld times."


def test_collect_hn_filters_by_score_and_type(cfg):
    base = "https://hacker-news.firebaseio.com/v0/"
    routes = {
        base + "topstories.json": (200, "[1, 2, 3]"),
        base + "item/1.json": (200, json.dumps({"type": "story", "score": 500, "time": 1792108800,
                                                "title": "Show HN: A tiny CPU",
                                                "url": "https://cpu.example/?utm_source=hn"})),
        base + "item/2.json": (200, json.dumps({"type": "story", "score": 5, "time": 1,
                                                "title": "Low", "url": "https://low.example/"})),
        base + "item/3.json": (200, json.dumps({"type": "job", "score": 900, "time": 1,
                                                "title": "Job", "url": "https://job.example/"})),
    }
    f = Fetcher(cfg.gather, transport=mock_transport(routes), sleep=lambda s: None)
    hn = feed(type="hn", url=base, min_score=200, scan=3)
    items = collect(hn, f, 3, dt.date(2026, 10, 16), random.Random(0))
    assert [(c.title, c.url) for c in items] == [("A tiny CPU", "https://cpu.example/")]


def test_collect_wikipedia_category_uses_api_text(cfg):
    api = "https://en.wikipedia.org/w/api.php*"
    members = {"query": {"categorymembers": [{"title": "Heat pump"}, {"title": "List of fans"}]}}
    page = {"query": {"pages": [{
        "title": "Heat pump", "fullurl": "https://en.wikipedia.org/wiki/Heat_pump",
        "extract": "A heat pump moves heat. " * 100 + "\n== References ==\nx"}]}}

    def handler_routes():
        return {"https://en.wikipedia.org/w/api.php?action=query&list=categorymembers*":
                (200, json.dumps(members)), api: (200, json.dumps(page))}

    f = Fetcher(cfg.gather, transport=mock_transport(handler_routes()), sleep=lambda s: None)
    wiki = feed(type="wikipedia_category", publisher="Wikipedia", kind="evergreen",
                faculties=["Trades & Home"], categories=["HVAC"], url=None)
    items = collect(wiki, f, 1, dt.date(2026, 10, 16), random.Random(0))
    assert len(items) == 1
    c = items[0]
    assert c.url == "https://en.wikipedia.org/wiki/Heat_pump" and c.word_count == 500
    assert "References" not in c.text


def test_feed_failure_gives_empty_list(cfg):
    f = Fetcher(cfg.gather, transport=mock_transport({}), sleep=lambda s: None)
    assert collect(feed(url="https://down.example/rss"), f, 3, dt.date.today(),
                   random.Random(0)) == []
