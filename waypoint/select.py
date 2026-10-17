"""Selection: rotation rules (PLAN.md 4.4), scoring and the per-slot fallback chain.

Rotation:
- a faculty may not appear within the last `faculty_cooldown_posts` posts. "Posts" are the
  published entries in posted.json followed by any picks already planned for the day
  (so the PM pick can't share the AM pick's faculty).
- a topic may not appear within the last `topic_cooldown_days` days.

Fallback chain per slot: the slot's preferred kinds (AM news/paper, PM evergreen) -> the
other kinds -> Wikipedia articles in a faculty that rotation allows.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Callable, Sequence

from waypoint.config import Config
from waypoint.dedupe import title_norm
from waypoint.models import FACULTIES, Candidate, PostedEntry, Slot

# Score weights (sum to 1).
W_FRESH, W_WEIGHT, W_SURPRISE, W_LENGTH = 0.25, 0.15, 0.45, 0.15
FRESH_HALF_LIFE_DAYS = 3.0
EVERGREEN_FRESHNESS = 0.6
UNKNOWN_DATE_FRESHNESS = 0.3

Fallback = Callable[[list[str], int], list[Candidate]]
"""(allowed faculties, how many) -> labelled, deduped candidates (Wikipedia in-faculty)."""


def published_history(posted: Sequence[PostedEntry]) -> list[PostedEntry]:
    return [p for p in posted if p.status == "published"]


def blocked_faculties(posted: Sequence[PostedEntry], planned: Sequence[Candidate],
                      cooldown: int) -> set[str]:
    history = [p.faculty for p in published_history(posted)] + [c.faculty for c in planned]
    return set(history[-cooldown:]) if cooldown else set()


def _topic_key(topic: str) -> str:
    return title_norm(topic)


def topics_on_cooldown(posted: Sequence[PostedEntry], planned: Sequence[Candidate],
                       today: dt.date, days: int) -> set[str]:
    since = today - dt.timedelta(days=days)
    keys = {_topic_key(p.topic) for p in published_history(posted) if p.date > since}
    keys |= {_topic_key(c.topic) for c in planned if c.topic}
    return keys


def freshness(c: Candidate, today: dt.date) -> float:
    if c.kind == "evergreen":
        return EVERGREEN_FRESHNESS
    if c.published is None:
        return UNKNOWN_DATE_FRESHNESS
    age = max(0, (today - c.published).days)
    return math.pow(0.5, age / FRESH_HALF_LIFE_DAYS)


def length_score(words: int) -> float:
    if words < 300:
        return 0.0
    if words < 500:
        return 0.6
    if words <= 4000:
        return 1.0
    return 0.8  # very long: more to sift through, more risk of a muddled story


def score(c: Candidate, cfg: Config, today: dt.date) -> float:
    weights = cfg.faculties
    top = max(weights.values())
    w = weights.get(c.faculty or "", 0.0) / top
    surprise = ((c.surprise or 3) - 1) / 4
    return round(W_FRESH * freshness(c, today) + W_WEIGHT * w + W_SURPRISE * surprise
                 + W_LENGTH * length_score(c.word_count), 4)


def eligible(c: Candidate, blocked: set[str], cooldown_topics: set[str],
             taken_urls: set[str], min_surprise: int = 1) -> bool:
    return (c.safe and c.faculty is not None and c.faculty not in blocked
            and (c.surprise or 3) >= min_surprise
            and _topic_key(c.topic or c.title) not in cooldown_topics
            and c.url not in taken_urls)


def rank_slot(slot: Slot, cands: Sequence[Candidate], posted: Sequence[PostedEntry],
              cfg: Config, today: dt.date, planned: Sequence[Candidate] = (),
              fallback: Fallback | None = None) -> list[Candidate]:
    """Top `candidates_per_slot` candidates for one slot, best first."""
    n = cfg.limits.candidates_per_slot
    blocked = blocked_faculties(posted, planned, cfg.limits.faculty_cooldown_posts)
    cooldown = topics_on_cooldown(posted, planned, today, cfg.limits.topic_cooldown_days)
    taken = {c.url for c in planned}
    ok = [c for c in cands
          if eligible(c, blocked, cooldown, taken, cfg.limits.min_surprise)]
    for c in ok:
        c.score = score(c, cfg, today)
    preferred = set(getattr(cfg.slot_kinds, slot))

    def by_score(xs: list[Candidate]) -> list[Candidate]:
        return sorted(xs, key=lambda c: (-c.score, c.url))

    ranked = by_score([c for c in ok if c.kind in preferred])
    ranked += by_score([c for c in ok if c.kind not in preferred])
    if len(ranked) < n and fallback is not None:
        allowed = [f for f in FACULTIES if f not in blocked and cfg.faculties[f] > 0]
        seen = {c.url for c in ranked} | taken
        extra = [c for c in fallback(allowed, n - len(ranked))
                 if c.url not in seen
                 and eligible(c, blocked, cooldown, taken, cfg.limits.min_surprise)]
        for c in extra:
            c.score = score(c, cfg, today)
        ranked += by_score(extra)
    return ranked[:n]


def select_day(cands: Sequence[Candidate], posted: Sequence[PostedEntry], cfg: Config,
               today: dt.date, fallback: Fallback | None = None) -> dict[Slot, list[Candidate]]:
    """Rank both slots. The AM top pick counts as planned when ranking PM."""
    out: dict[Slot, list[Candidate]] = {}
    planned: list[Candidate] = []
    for slot in ("am", "pm"):
        ranked = rank_slot(slot, cands, posted, cfg, today, planned, fallback)
        out[slot] = ranked
        if ranked:
            planned.append(ranked[0])
    return out
