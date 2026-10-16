"""Polite HTTP fetching for gather: a User-Agent naming the repo, at most one request per
second per host, timeouts, robots.txt, and a small file cache under .cache/http/ so re-runs
in the same day don't refetch. Article text is extracted with trafilatura.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx
import trafilatura

from waypoint.config import Gather

log = logging.getLogger(__name__)

# Sites whose articles are (mostly) behind a paywall: never worth fetching.
PAYWALL_HOSTS = frozenset({
    "nytimes.com", "wsj.com", "ft.com", "economist.com", "bloomberg.com", "washingtonpost.com",
    "theatlantic.com", "newyorker.com", "wired.com", "thetimes.co.uk", "telegraph.co.uk",
    "barrons.com", "businessinsider.com", "hbr.org", "nature.com", "science.org",
    "scientificamerican.com", "newscientist.com", "theinformation.com", "medium.com",
})
# Hosts that ask for a slower pace than per_host_delay_s (arXiv API terms: one call per 3 s).
HOST_MIN_DELAY_S = {"export.arxiv.org": 3.0}
PAYWALL_MARKERS = ("subscribe to continue", "subscribers only", "to continue reading",
                   "already a subscriber", "this article is for subscribers")


class FetchError(RuntimeError):
    pass


def host_of(url: str) -> str:
    return urlsplit(url).netloc.lower()


def is_paywalled_host(url: str) -> bool:
    host = host_of(url)
    return any(host == h or host.endswith("." + h) for h in PAYWALL_HOSTS)


class Fetcher:
    def __init__(self, cfg: Gather, cache_dir: Path | None = None, *,
                 transport: httpx.BaseTransport | None = None,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep,
                 wall: Callable[[], float] = time.time):
        self.cfg = cfg
        self.cache_dir = cache_dir
        self.clock, self.sleep, self.wall = clock, sleep, wall
        self.client = httpx.Client(
            headers={"User-Agent": cfg.user_agent}, timeout=cfg.timeout_s,
            follow_redirects=True, transport=transport,
        )
        self._lock = threading.Lock()
        self._host_locks: dict[str, threading.Lock] = {}
        self._last: dict[str, float] = {}
        self._robots: dict[str, RobotFileParser | None] = {}
        self.requests = 0

    def close(self) -> None:
        self.client.close()

    # --- politeness -------------------------------------------------------------------------

    def _host_lock(self, host: str) -> threading.Lock:
        with self._lock:
            return self._host_locks.setdefault(host, threading.Lock())

    def _request(self, url: str) -> httpx.Response:
        host = host_of(url)
        with self._host_lock(host):
            last = self._last.get(host)
            if last is not None:
                delay = max(self.cfg.per_host_delay_s, HOST_MIN_DELAY_S.get(host, 0.0))
                wait = delay - (self.clock() - last)
                if wait > 0:
                    self.sleep(wait)
            try:
                resp = self.client.get(url)
            except httpx.HTTPError as e:
                raise FetchError(f"{url}: {type(e).__name__}: {e}") from e
            finally:
                self._last[host] = self.clock()
                self.requests += 1
        return resp

    def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        host = parts.netloc.lower()
        if host not in self._robots:
            rp: RobotFileParser | None = RobotFileParser()
            try:
                resp = self._request(f"{parts.scheme}://{host}/robots.txt")
                if resp.status_code >= 500:
                    rp.disallow_all = True  # server trouble: stay away (RFC 9309)
                elif resp.status_code >= 400:
                    rp = None  # no robots.txt: everything allowed
                else:
                    rp.parse(resp.text.splitlines())
            except FetchError:
                rp.disallow_all = True
            self._robots[host] = rp
        rp = self._robots[host]
        return rp is None or rp.can_fetch(self.cfg.user_agent, url)

    # --- cache ------------------------------------------------------------------------------

    def _cache_path(self, url: str) -> Path | None:
        if self.cache_dir is None:
            return None
        return self.cache_dir / (hashlib.sha1(url.encode()).hexdigest() + ".json")

    def _cache_get(self, url: str) -> tuple[str, str] | None:
        p = self._cache_path(url)
        if p is None or not p.is_file():
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if self.wall() - data.get("fetched_at", 0) > self.cfg.cache_ttl_hours * 3600:
            return None
        return data["text"], data["final_url"]

    def _cache_put(self, url: str, text: str, final_url: str) -> None:
        p = self._cache_path(url)
        if p is None:
            return
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"url": url, "final_url": final_url, "text": text,
                                 "fetched_at": self.wall()}), encoding="utf-8")

    # --- public API -------------------------------------------------------------------------

    def get_text(self, url: str, *, check_robots: bool = True) -> tuple[str, str]:
        """GET a URL; returns (body, final URL after redirects). Raises FetchError."""
        cached = self._cache_get(url)
        if cached is not None:
            return cached
        if check_robots and not self.allowed(url):
            raise FetchError(f"{url}: disallowed by robots.txt")
        resp = self._request(url)
        if resp.status_code != 200:
            raise FetchError(f"{url}: HTTP {resp.status_code}")
        final = str(resp.url)
        self._cache_put(url, resp.text, final)
        return resp.text, final

    def get_json(self, url: str, params: dict | None = None) -> object:
        """GET a JSON API (APIs are not robots-checked: they are documented for programs)."""
        full = str(httpx.URL(url, params=params)) if params else url
        text, _ = self.get_text(full, check_robots=False)
        try:
            return json.loads(text)
        except ValueError as e:
            raise FetchError(f"{full}: not JSON") from e

    def article(self, url: str) -> tuple[str, str]:
        """Fetch a web article and extract its main text. Returns (text, final URL)."""
        if is_paywalled_host(url):
            raise FetchError(f"{url}: paywalled site")
        html, final = self.get_text(url)
        if is_paywalled_host(final):
            raise FetchError(f"{final}: paywalled site")
        text = trafilatura.extract(html, url=final, include_comments=False,
                                   include_tables=False, favor_precision=True) or ""
        if len(text.split()) < 600 and any(m in text.lower() for m in PAYWALL_MARKERS):
            raise FetchError(f"{url}: paywall text detected")
        return text.strip(), final
