"""`waypoint gather`: collect -> cheap dedupe (1-2) -> keyword safety -> fetch text ->
word floor -> triage (Gemini or heuristics) -> safety label -> semantic dedupe (3-4) ->
select per slot, with the Wikipedia-in-faculty fallback.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import random
import re
from collections import defaultdict
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from waypoint.config import Config
from waypoint.dedupe import DedupeGate
from waypoint.gather.fetch import Fetcher, FetchError
from waypoint.gather.sources import Feed, collect, collect_wikipedia_category
from waypoint.gather.triage import TriageCache, heuristic_label, keyword_unsafe, llm_label
from waypoint.models import Candidate, PostedEntry, Slot
from waypoint.select import select_day
from waypoint.write.gemini import GeminiClient

log = logging.getLogger(__name__)

FETCH_WORKERS = 8


@dataclass
class GatherResult:
    slots: dict[Slot, list[Candidate]]
    candidates: list[Candidate] = field(default_factory=list)
    rejected: list[tuple[Candidate, str]] = field(default_factory=list)
    gemini_calls: int = 0
    fetch_requests: int = 0

    @property
    def faculties(self) -> set[str]:
        return {c.faculty for c in self.candidates if c.faculty}


def round_robin(cands: Sequence[Candidate], limit: int) -> list[Candidate]:
    """Up to `limit` candidates, taking one from each feed in turn (newest first per feed)."""
    by_feed: dict[str, list[Candidate]] = defaultdict(list)
    for c in cands:
        by_feed[c.feed].append(c)
    for xs in by_feed.values():
        xs.sort(key=lambda c: c.published or dt.date.min, reverse=True)
    out: list[Candidate] = []
    while len(out) < limit and any(by_feed.values()):
        for feed in list(by_feed):
            if by_feed[feed] and len(out) < limit:
                out.append(by_feed[feed].pop(0))
    return out


class Gatherer:
    def __init__(self, cfg: Config, posted: Sequence[PostedEntry], feeds: Sequence[Feed],
                 fetcher: Fetcher, gemini: GeminiClient | None, today: dt.date,
                 cache_dir: Path | None = None, embeddings_path: Path | None = None):
        self.cfg, self.posted, self.feeds = cfg, list(posted), list(feeds)
        self.fetcher, self.gemini, self.today = fetcher, gemini, today
        self.rng = random.Random(today.isoformat())
        self.gate = DedupeGate(self.posted, cfg.limits, gemini, embeddings_path)
        self.triage_cache = TriageCache(cache_dir / "triage.json" if cache_dir else None)
        self.rejected: list[tuple[Candidate, str]] = []
        self.seen_urls: set[str] = set()

    def _reject(self, c: Candidate, reason: str) -> None:
        log.debug("reject %s: %s", c.url, reason)
        self.rejected.append((c, reason))

    def _fetch_text(self, c: Candidate) -> Candidate | None:
        if c.text:
            return c
        try:
            text, _ = self.fetcher.article(c.url)
        except FetchError as e:
            self._reject(c, f"fetch: {e}")
            return None
        c.text, c.word_count = text, len(text.split())
        return c

    def process(self, raw: Sequence[Candidate]) -> list[Candidate]:
        """Everything between collecting and selecting. Returns labelled, deduped, safe ones."""
        raw = [c for c in raw if c.url not in self.seen_urls]
        self.seen_urls |= {c.url for c in raw}

        gated = self.gate.cheap(raw)
        for c, r in gated.rejected:
            self._reject(c, r)
        cands = []
        for c in gated.kept:
            hit = keyword_unsafe(f"{c.title} {c.summary}")
            if hit:
                c.safe, c.safety_reason = False, hit
                self._reject(c, f"safety: {hit}")
            else:
                cands.append(c)

        with ThreadPoolExecutor(FETCH_WORKERS) as pool:
            fetched = [c for c in pool.map(self._fetch_text, cands) if c]
        long_enough = []
        for c in fetched:
            if c.word_count < self.cfg.limits.min_source_words:
                self._reject(c, f"too short: {c.word_count} words")
            else:
                long_enough.append(c)

        picked = round_robin(long_enough, self.cfg.gather.max_triage)
        for c in long_enough:
            if c not in picked:
                self._reject(c, "over max_triage")
        if self.gemini is None:
            for c in picked:
                heuristic_label(c)
            labelled = picked
        else:
            labelled = llm_label(picked, self.gemini, self.cfg.gather.triage_batch_size,
                                 self.triage_cache)
        kept = []
        for c in labelled:
            if c.faculty is None:
                self._reject(c, "no faculty fits")
            elif not c.safe:
                self._reject(c, f"safety: {c.safety_reason}")
            else:
                kept.append(c)

        if self.gemini is not None:
            sem = self.gate.semantic(kept)
            for c, r in sem.rejected:
                self._reject(c, r)
            kept = sem.kept
        return kept

    def collect_all(self) -> list[Candidate]:
        limit = self.cfg.gather.max_items_per_feed
        feeds = [f for f in self.feeds]
        rngs = [random.Random(f"{self.today}|{f.id}") for f in feeds]
        with ThreadPoolExecutor(FETCH_WORKERS) as pool:
            results = pool.map(lambda fr: collect(fr[0], self.fetcher, limit, self.today, fr[1]),
                               zip(feeds, rngs, strict=True))
            out = []
            for feed, items in zip(feeds, results, strict=True):
                log.info("feed %-24s %d items", feed.id, len(items))
                out.extend(items)
        return out

    def fallback(self, allowed: list[str], n: int) -> list[Candidate]:
        """Wikipedia articles from category feeds of allowed faculties (last resort)."""
        n = min(max(n, 1), self.cfg.gather.fallback_items) if self.cfg.gather.fallback_items else 0
        feeds = [f for f in self.feeds if f.type == "wikipedia_category"
                 and any(fac in allowed for fac in f.faculties)]
        if not n or not feeds:
            return []
        self.rng.shuffle(feeds)
        log.info("fallback: %d Wikipedia article(s) from %s", n, [f.id for f in feeds[:n]])
        exclude = {c.title for c, _ in self.rejected}
        raw: list[Candidate] = []
        for feed in feeds[:n]:
            raw += collect_wikipedia_category(feed, self.fetcher, 1, self.rng, exclude)
        return self.process(raw)

    def run(self) -> GatherResult:
        candidates = self.process(self.collect_all())
        slots = select_day(candidates, self.posted, self.cfg, self.today, self.fallback)
        extra = [c for s in slots.values() for c in s if c not in candidates]
        if self.gemini is not None:
            self.gemini.log_usage()
        return GatherResult(
            slots=slots, candidates=candidates + extra, rejected=self.rejected,
            gemini_calls=self.gemini.total_calls if self.gemini else 0,
            fetch_requests=self.fetcher.requests,
        )


# --- output ---------------------------------------------------------------------------------


def slug(c: Candidate) -> str:
    h = hashlib.sha1(c.url.encode()).hexdigest()[:8]
    words = re.sub(r"[^a-z0-9]+", "-", c.title.lower()).strip("-")[:40].strip("-")
    return f"{h}-{words}"


def write_result(res: GatherResult, out_dir: Path) -> Path:
    """out_dir/candidates.json (ranked slots + all candidates) and
    out_dir/candidates/<slug>/{candidate.json,source.txt}."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for c in res.candidates:
        d = out_dir / "candidates" / slug(c)
        d.mkdir(parents=True, exist_ok=True)
        (d / "source.txt").write_text(c.text, encoding="utf-8")
        (d / "candidate.json").write_text(c.model_dump_json(indent=2), encoding="utf-8")
    summary = {
        "slots": {s: [slug(c) for c in cs] for s, cs in res.slots.items()},
        "candidates": {slug(c): c.model_dump(mode="json") for c in res.candidates},
        "rejected": [{"url": c.url, "title": c.title, "reason": r} for c, r in res.rejected],
        "gemini_calls": res.gemini_calls,
        "fetch_requests": res.fetch_requests,
    }
    path = out_dir / "candidates.json"
    path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return path
