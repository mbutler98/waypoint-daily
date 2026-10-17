"""Gemini client wrapper: model names from config, a per-minute rate limiter, retries with
backoff, a per-run call budget, and a call counter that is logged at the end of each run.

The API key comes from the GEMINI_API_KEY environment variable only and is never logged.
Tests pass a fake `client` with the same `.models.generate_content` / `.models.embed_content`
surface as `google.genai.Client`.
"""

from __future__ import annotations

import collections
import logging
import os
import re
import time
from collections.abc import Callable, Sequence
from typing import Any, TypeVar

import numpy as np
from pydantic import BaseModel

from waypoint.config import Gemini as GeminiConfig

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

RETRY_STATUS = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = 5
EMBED_BATCH = 100  # max texts per embed_content request


class GeminiError(RuntimeError):
    pass


class GeminiBudgetExceeded(GeminiError):
    """The run hit limits.max_gemini_calls_per_run."""


RETRY_DELAY_RE = re.compile(r"retryDelay['\"]?\s*:\s*['\"]?(\d+(?:\.\d+)?)s")


def _retry_delay(exc: Exception, attempt: int) -> float:
    """The server's suggested delay on a 429 (RetryInfo), else exponential backoff."""
    m = RETRY_DELAY_RE.search(str(exc))
    if m:
        return min(90.0, float(m.group(1)) + 1)
    return min(60.0, 2.0 ** attempt)


def _status_of(exc: Exception) -> int | None:
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    return code if isinstance(code, int) else None


class RateLimiter:
    """Sliding one-minute window: at most `rpm` calls in any 60 seconds."""

    def __init__(self, rpm: int, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self.rpm, self.clock, self.sleep = rpm, clock, sleep
        self.stamps: collections.deque[float] = collections.deque()

    def wait(self) -> None:
        while True:
            now = self.clock()
            while self.stamps and now - self.stamps[0] >= 60:
                self.stamps.popleft()
            if len(self.stamps) < self.rpm:
                self.stamps.append(now)
                return
            self.sleep(60 - (now - self.stamps[0]) + 0.05)


class GeminiClient:
    def __init__(self, cfg: GeminiConfig, max_calls: int, *, client: Any = None,
                 api_key: str | None = None, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self.cfg = cfg
        self.max_calls = max_calls
        self.sleep = sleep
        self.limiter = RateLimiter(cfg.requests_per_minute, clock, sleep)
        self.calls: collections.Counter[str] = collections.Counter()
        if client is None:
            key = api_key or os.environ.get("GEMINI_API_KEY")
            if not key:
                raise GeminiError("GEMINI_API_KEY is not set (use --no-llm to skip Gemini)")
            from google import genai

            client = genai.Client(api_key=key)
        self.client = client

    @property
    def total_calls(self) -> int:
        return sum(self.calls.values())

    def _call(self, purpose: str, fn: Callable[[], Any]) -> Any:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            if self.total_calls >= self.max_calls:
                raise GeminiBudgetExceeded(
                    f"Gemini call budget of {self.max_calls} per run reached ({purpose})"
                )
            self.limiter.wait()
            self.calls[purpose] += 1
            try:
                return fn()
            except Exception as e:  # SDK errors carry .code; anything else is not retried
                status = _status_of(e)
                if status not in RETRY_STATUS or attempt == MAX_ATTEMPTS:
                    raise GeminiError(f"Gemini {purpose} failed: {type(e).__name__}: {e}") from e
                delay = _retry_delay(e, attempt)
                log.warning("Gemini %s got HTTP %s; retry %d in %.0fs",
                            purpose, status, attempt, delay)
                self.sleep(delay)
        raise AssertionError("unreachable")

    def generate_json(self, prompt: str, schema: type[T], *, purpose: str,
                      model: str | None = None, system: str | None = None,
                      temperature: float = 0.2) -> T:
        """One structured-output call; returns a validated instance of `schema`."""
        from google.genai import types

        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=schema,
            temperature=temperature,
            system_instruction=system,
        )
        resp = self._call(purpose, lambda: self.client.models.generate_content(
            model=model or self.cfg.fast_model, contents=prompt, config=config))
        parsed = getattr(resp, "parsed", None)
        if isinstance(parsed, schema):
            return parsed
        text = getattr(resp, "text", None)
        if not text:
            raise GeminiError(f"Gemini {purpose} returned no content")
        return schema.model_validate_json(text)

    def embed(self, texts: Sequence[str], *, purpose: str = "embed") -> np.ndarray:
        """Unit-normalised embeddings, one row per text."""
        from google.genai import types

        rows: list[list[float]] = []
        config = types.EmbedContentConfig(task_type="SEMANTIC_SIMILARITY")
        for i in range(0, len(texts), EMBED_BATCH):
            chunk = list(texts[i:i + EMBED_BATCH])
            resp = self._call(purpose, lambda c=chunk: self.client.models.embed_content(
                model=self.cfg.embedding_model, contents=c, config=config))
            rows.extend(e.values for e in resp.embeddings)
        if not rows:
            return np.zeros((0, 0), dtype=np.float32)
        arr = np.asarray(rows, dtype=np.float32)
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        return arr / np.where(norms == 0, 1, norms)

    def log_usage(self) -> None:
        detail = ", ".join(f"{k}={v}" for k, v in sorted(self.calls.items())) or "none"
        log.info("Gemini calls this run: %d of %d budget (%s)",
                 self.total_calls, self.max_calls, detail)
