"""End-to-end gather on recorded fixtures: no live network, Gemini faked."""

import datetime as dt
import json
import re

from helpers import GATHER_FIXTURES, fake_gemini, make_candidate, make_posted, mock_transport
from test_fetch import ARTICLE

from waypoint.gather.fetch import Fetcher
from waypoint.gather.pipeline import Gatherer, round_robin, write_result
from waypoint.gather.sources import Feed
from waypoint.gather.triage import guess_faculty, keyword_unsafe

TODAY = dt.date(2026, 10, 16)
RSS = "https://www.sciencedaily.com/rss/plants_animals.xml"


def routes():
    rss = (GATHER_FIXTURES / "sciencedaily.xml").read_text(encoding="utf-8")
    return {
        RSS: (200, rss),
        "https://www.sciencedaily.com/robots.txt": (200, "User-agent: *\nAllow: /\n"),
        "https://www.sciencedaily.com/releases/*": (200, ARTICLE),
    }


FEEDS = [Feed(id="sd", type="rss", url=RSS, publisher="ScienceDaily", kind="news",
              faculties=["Animals & Nature", "Space"])]


def gatherer(cfg, gemini=None, posted=(), tmp_path=None):
    f = Fetcher(cfg.gather, transport=mock_transport(routes()), sleep=lambda s: None)
    return Gatherer(cfg, list(posted), FEEDS, f, gemini, TODAY,
                    cache_dir=tmp_path, embeddings_path=tmp_path / "e.npy" if tmp_path else None)


def test_no_llm_gather_end_to_end(cfg, tmp_path):
    res = gatherer(cfg).run()
    assert len(res.candidates) == 3
    assert all(c.word_count >= cfg.limits.min_source_words for c in res.candidates)
    assert res.slots["am"] and res.gemini_calls == 0
    out = write_result(res, tmp_path / "out")
    data = json.loads(out.read_text(encoding="utf-8"))
    first = data["slots"]["am"][0]
    src = tmp_path / "out" / "candidates" / first / "source.txt"
    assert "honeybees" in src.read_text(encoding="utf-8")
    assert "text" not in data["candidates"][first]  # the body lives in source.txt only


def test_llm_gather_labels_and_drops_unsafe(cfg, tmp_path):
    def respond(prompt):
        if "ARTICLE" not in prompt:
            return {"verdicts": []}
        titles = re.findall(r"^title: (.*)$", prompt, re.M)
        labels = [{"index": i, "title": titles[i - 1], "faculty": "Animals & Nature",
                   "topic": f"Bees {i}",
                   "key_claim": f"Bees fact number {i}.", "surprise": 4, "safe": i != 2,
                   "safety_reason": "war" if i == 2 else None} for i in range(1, len(titles) + 1)]
        return {"labels": labels}

    gemini, models = fake_gemini(cfg, respond)
    g = gatherer(cfg, gemini, tmp_path=tmp_path)
    res = g.run()
    assert len(res.candidates) == 2
    assert any(r == "safety: war" for _, r in res.rejected)
    assert {c.faculty for c in res.candidates} == {"Animals & Nature"}
    assert res.gemini_calls == 1  # one triage batch; no history, so no dedupe calls

    # Labels are cached: a second run spends no calls.
    gemini2, _ = fake_gemini(cfg, respond)
    gatherer(cfg, gemini2, tmp_path=tmp_path).run()
    assert gemini2.total_calls == 0


def test_seeded_posted_url_is_rejected_in_pipeline(cfg):
    url = "https://www.sciencedaily.com/releases/2026/10/261015073220.htm"
    posted = [make_posted(1, date=TODAY - dt.timedelta(days=1), url=url)]
    res = gatherer(cfg, posted=posted).run()
    assert url not in {c.url for c in res.candidates}
    assert any(c.url == url and r.startswith("dedupe-1") for c, r in res.rejected)


def test_round_robin_spreads_feeds():
    a = [make_candidate(f"A{i}", feed="a") for i in range(5)]
    b = [make_candidate(f"B{i}", feed="b") for i in range(2)]
    assert [c.feed for c in round_robin(a + b, 4)] == ["a", "b", "a", "b"]


def test_keyword_safety_filter():
    assert keyword_unsafe("Senate passes new tariff bill").startswith("keyword:politics")
    assert keyword_unsafe("Plane crash investigation").startswith("keyword:tragedy")
    assert keyword_unsafe("How bees count landmarks") is None
    assert keyword_unsafe("Heat pumps work in the cold") is None


def test_guess_faculty_heuristic():
    c = make_candidate("New telescope spots a comet", faculty=None,
                       hints=["Animals & Nature", "Space"])
    assert guess_faculty(c) == "Space"
    single = make_candidate("Anything", faculty=None, hints=["Flight"])
    assert guess_faculty(single) == "Flight"


def test_triage_labels_follow_titles_not_indexes():
    from waypoint.gather.triage import _Label, match_label

    batch = [make_candidate("Bees can count to three"),
             make_candidate("NASA names a new centre director")]

    def label(index, title):
        return _Label(index=index, title=title, faculty="Space", topic="t", key_claim="k",
                      surprise=3, safe=True)

    assert match_label(label(1, "Bees can count to three"), batch) is batch[0]
    # A shifted index is realigned by title; a title that matches nothing is dropped.
    assert match_label(label(1, "NASA names a new centre director"), batch) is batch[1]
    assert match_label(label(2, "Bird migration tracking"), batch) is None
