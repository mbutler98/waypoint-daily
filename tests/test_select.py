import datetime as dt
import itertools

from helpers import make_candidate, make_posted

from waypoint.models import FACULTIES, PostedEntry
from waypoint.select import (
    blocked_faculties,
    freshness,
    rank_slot,
    score,
    select_day,
    topics_on_cooldown,
)

TODAY = dt.date(2026, 10, 16)


def pool(per_faculty: int = 2) -> list:
    """Candidates in every faculty, both kinds; Space is always the most surprising."""
    out = []
    for fac, i in itertools.product(FACULTIES, range(per_faculty)):
        kind = "news" if i % 2 == 0 else "evergreen"
        out.append(make_candidate(f"{fac} story {i}", faculty=fac, kind=kind,
                                  surprise=5 if fac == "Space" else 3, topic=f"{fac} topic {i}"))
    return out


def test_rotation_holds_over_20_posts(cfg):
    """Simulate 10 days (20 posts): no faculty may repeat within any 5 consecutive posts,
    even though Space always scores highest."""
    posted: list[PostedEntry] = []
    cands = pool(per_faculty=4)
    day = TODAY
    for _ in range(10):
        picks = select_day(cands, posted, cfg, day)
        for slot in ("am", "pm"):
            top = picks[slot][0]
            posted.append(make_posted(len(posted), date=day, slot=slot, faculty=top.faculty,
                                      topic=top.topic, url=top.url))
            cands = [c for c in cands if c.url != top.url]
        day += dt.timedelta(days=1)
    faculties = [p.faculty for p in posted]
    assert len(faculties) == 20
    k = cfg.limits.faculty_cooldown_posts
    for i in range(len(faculties)):
        window = faculties[max(0, i - k):i]
        assert faculties[i] not in window, (i, faculties)
    assert faculties.count("Space") == 4  # top scorer returns as soon as rotation allows


def test_pm_cannot_repeat_am_faculty(cfg):
    cands = [make_candidate("Space A", faculty="Space", surprise=5, kind="news"),
             make_candidate("Space B", faculty="Space", surprise=5, kind="evergreen"),
             make_candidate("Bees", faculty="Animals & Nature", kind="evergreen")]
    picks = select_day(cands, [], cfg, TODAY)
    assert picks["am"][0].title == "Space A"
    assert [c.title for c in picks["pm"]] == ["Bees"]


def test_rejected_posts_do_not_count_for_rotation(cfg):
    posted = [make_posted(1, date=TODAY, faculty="Space", status="rejected")]
    assert blocked_faculties(posted, [], 4) == set()


def test_topic_cooldown(cfg):
    posted = [make_posted(1, date=TODAY - dt.timedelta(days=10), faculty="History",
                          topic="Heat pumps")]
    assert "heat pumps" in topics_on_cooldown(posted, [], TODAY, 30)
    assert topics_on_cooldown(posted, [], TODAY + dt.timedelta(days=30), 30) == set()
    c = make_candidate("Heat pump news", faculty="Trades & Home", topic="Heat Pumps!")
    assert rank_slot("am", [c], posted, cfg, TODAY) == []


def test_fallback_chain_news_then_evergreen_then_wikipedia(cfg):
    news = make_candidate("News", faculty="Space", kind="news", surprise=2)
    ever = make_candidate("Ever", faculty="History", kind="evergreen", surprise=5)
    calls = []

    def fallback(allowed, n):
        calls.append((allowed, n))
        return [make_candidate("Wiki", faculty="Flight", kind="evergreen", feed="wiki-flight")]

    # AM prefers news even if evergreen scores higher, then evergreen, then the fallback.
    assert [c.title for c in rank_slot("am", [ever, news], [], cfg, TODAY, fallback=fallback)] \
        == ["News", "Ever", "Wiki"]
    # PM prefers evergreen.
    ranked = rank_slot("pm", [ever, news], [], cfg, TODAY, fallback=fallback)
    assert [c.title for c in ranked][:2] == ["Ever", "News"]

    # When rotation blocks everything, the fallback gets only the allowed faculties.
    posted = [make_posted(i, date=TODAY - dt.timedelta(days=i), faculty=f)
              for i, f in enumerate(["Space", "History", "Flight", "Money & Markets"], 1)]
    posted.reverse()
    calls.clear()
    assert rank_slot("am", [ever, news], posted, cfg, TODAY, fallback=fallback) == []
    allowed, need = calls[0]
    assert {"Space", "History", "Flight", "Money & Markets"}.isdisjoint(allowed)
    assert need == cfg.limits.candidates_per_slot


def test_unsafe_and_unlabelled_are_not_eligible(cfg):
    bad = make_candidate("Bad", faculty="Space")
    bad.safe = False
    none = make_candidate("None", faculty=None)
    assert rank_slot("am", [bad, none], [], cfg, TODAY) == []


def test_score_prefers_fresh_surprising_news(cfg):
    fresh = make_candidate("Fresh", published=TODAY, surprise=5)
    stale = make_candidate("Stale", published=TODAY - dt.timedelta(days=9), surprise=5)
    dull = make_candidate("Dull", published=TODAY, surprise=1)
    assert score(fresh, cfg, TODAY) > score(stale, cfg, TODAY) > score(dull, cfg, TODAY) - 0.5
    assert score(fresh, cfg, TODAY) > score(dull, cfg, TODAY)
    assert freshness(make_candidate("E", kind="evergreen", published=None), TODAY) == 0.6
    assert freshness(make_candidate("U", published=None), TODAY) == 0.3


def test_returns_at_most_n_per_slot(cfg):
    picks = select_day(pool(), [], cfg, TODAY)
    assert all(len(v) == cfg.limits.candidates_per_slot for v in picks.values())
    assert picks["am"][0].url != picks["pm"][0].url
