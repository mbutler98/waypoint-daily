"""Shared helpers for the offline gather/dedupe/select tests (no live network, no Gemini).

Imported as `helpers` (pytest puts tests/ on sys.path); `tests` itself is shadowed by a
top-level `tests` package that a dependency installs into site-packages."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from pathlib import Path
from types import SimpleNamespace

import httpx
import numpy as np

from waypoint.models import Candidate, PostedEntry

FIXTURES = Path(__file__).parent / "fixtures"
GATHER_FIXTURES = FIXTURES / "gather"


def make_candidate(title: str = "A surprising fact", *, url: str | None = None,
                   faculty: str | None = "Space", kind: str = "news",
                   published: dt.date | None = dt.date(2026, 10, 16), surprise: int = 3,
                   words: int = 800, topic: str | None = None, key_claim: str | None = None,
                   feed: str = "test", hints: list[str] | None = None) -> Candidate:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    c = Candidate(
        url=url or f"https://example.com/{slug}", title=title, publisher="Example", kind=kind,
        published=published, feed=feed, faculty_hints=hints or [], faculty=faculty,
        topic=topic or title, key_claim=key_claim or title, surprise=surprise, word_count=words,
    )
    c.text = "word " * words
    return c


def make_posted(n: int, *, date: dt.date, slot: str = "am", faculty: str = "Space",
                topic: str = "Topic", url: str | None = None, title_norm: str = "some title",
                key_claim: str = "A claim.", status: str = "published") -> PostedEntry:
    return PostedEntry(
        id=f"{date.isoformat()}-{slot}", date=date, faculty=faculty, topic=topic,
        source_url=url or f"https://posted.example/{n}", title_norm=title_norm,
        key_claim=key_claim, status=status,
    )


# --- fake Gemini ----------------------------------------------------------------------------


def bag_of_words_vector(text: str, dim: int = 256) -> list[float]:
    v = np.zeros(dim)
    for w in re.findall(r"[a-z0-9]+", text.lower()):
        v[int(hashlib.md5(w.encode()).hexdigest(), 16) % dim] += 1
    return v.tolist()


class FakeModels:
    """Stands in for google.genai Client.models. `respond(prompt) -> dict` builds the JSON
    reply for generate_content; embeddings are hashed bags of words."""

    def __init__(self, respond=None, errors: list[Exception] | None = None):
        self.respond = respond or (lambda prompt: {})
        self.errors = list(errors or [])
        self.prompts: list[str] = []
        self.embedded: list[list[str]] = []

    def _maybe_fail(self):
        if self.errors:
            raise self.errors.pop(0)

    def generate_content(self, *, model, contents, config):
        self._maybe_fail()
        self.prompts.append(contents)
        return SimpleNamespace(parsed=None, text=json.dumps(self.respond(contents)))

    def embed_content(self, *, model, contents, config):
        self._maybe_fail()
        self.embedded.append(list(contents))
        return SimpleNamespace(
            embeddings=[SimpleNamespace(values=bag_of_words_vector(t)) for t in contents])


def fake_gemini(cfg, respond=None, errors=None, max_calls: int = 100):
    from waypoint.write.gemini import GeminiClient

    models = FakeModels(respond, errors)
    client = GeminiClient(cfg.gemini, max_calls, client=SimpleNamespace(models=models),
                          sleep=lambda s: None)
    return client, models


class ApiError(Exception):
    """Mimics google.genai.errors.APIError (has an int `.code`)."""

    def __init__(self, code: int):
        super().__init__(f"HTTP {code}")
        self.code = code


# --- fake HTTP ------------------------------------------------------------------------------


def mock_transport(routes: dict[str, tuple[int, str]], log: list[str] | None = None):
    """httpx transport serving exact URLs (or URL prefixes ending in '*'); 404 otherwise."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if log is not None:
            log.append(url)
        for pattern, (status, body) in routes.items():
            if url == pattern or (pattern.endswith("*") and url.startswith(pattern[:-1])):
                return httpx.Response(status, text=body)
        return httpx.Response(404, text="not found")

    return httpx.MockTransport(handler)
