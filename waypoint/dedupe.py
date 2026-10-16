"""Dedupe gate (PLAN.md 4.5). A candidate must pass all four checks:

1. canonical URL not already in posted.json
2. normalised-title Jaccard similarity < limits.title_jaccard_max with every past title
3. key_claim embedding cosine < limits.embedding_cosine_max with every past key_claim
4. an LLM "same core idea?" check against the last limits.llm_dedupe_window key_claims

Checks 1-2 are pure Python (`--no-llm` runs only these). Checks 3-4 need Gemini.
Rejected drafts are in posted.json too, so they are never proposed again.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import numpy as np
from pydantic import BaseModel, Field

from waypoint.config import Limits
from waypoint.models import Candidate, PostedEntry
from waypoint.write.gemini import GeminiClient

log = logging.getLogger(__name__)

# --- URLs -----------------------------------------------------------------------------------

TRACKING_PARAMS = {
    "fbclid", "gclid", "dclid", "msclkid", "mc_cid", "mc_eid", "igshid", "ref", "ref_src",
    "cmpid", "ito", "_ga", "yclid", "spm", "ncid", "sr_share",
}


def canonical_url(url: str) -> str:
    """The URL we store: https where given, lower-case host, no fragment, no tracking
    parameters, sorted query, no trailing slash on non-root paths."""
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower() or "https"
    host = parts.netloc.lower()
    if host.endswith(":80") and scheme == "http":
        host = host[:-3]
    if host.endswith(":443") and scheme == "https":
        host = host[:-4]
    query = sorted(
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in TRACKING_PARAMS
    )
    path = parts.path or "/"
    if len(path) > 1:
        path = path.rstrip("/")
    return urlunsplit((scheme, host, path, urlencode(query), ""))


def url_key(url: str) -> str:
    """Comparison key: canonical URL without scheme, `www.` or `m.` prefixes."""
    parts = urlsplit(canonical_url(url))
    host = re.sub(r"^(www\d?|m|mobile)\.", "", parts.netloc)
    host = host.replace(".m.wikipedia.org", ".wikipedia.org")
    return f"{host}{parts.path}" + (f"?{parts.query}" if parts.query else "")


# --- Titles ---------------------------------------------------------------------------------

STOPWORDS = frozenset(" ".join((
    "a an and are as at be been but by can could did do does for from had has have how i if in",
    "into is it its may might more most new not of on or our out over so study than that the",
    "their them then there these they this those through to up was we were what when where",
    "which who why will with would you your about after again all also just like make makes",
    "made now only some such says said here s t",
)).split())


def title_tokens(title: str) -> list[str]:
    text = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode().lower()
    return [w for w in re.findall(r"[a-z0-9]+", text) if w not in STOPWORDS]


def title_norm(title: str) -> str:
    """Normalised title as stored in posted.json (`title_norm`)."""
    return " ".join(title_tokens(title))


def jaccard(a: str, b: str) -> float:
    sa, sb = set(a.split()), set(b.split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


# --- Embedding cache ------------------------------------------------------------------------


class EmbeddingCache:
    """state/embeddings.npy: one unit vector per posted.json entry, same order.

    posted.json is append-only, so rows only ever need adding at the end. If the row count
    no longer fits (e.g. the file was lost or the model changed), it is rebuilt.
    """

    def __init__(self, path: Path, gemini: GeminiClient):
        self.path, self.gemini = path, gemini

    def vectors(self, posted: Sequence[PostedEntry]) -> np.ndarray:
        rows = np.load(self.path) if self.path.is_file() else np.zeros((0, 0), np.float32)
        if len(rows) > len(posted):
            log.warning("embeddings.npy has more rows than posted.json; rebuilding")
            rows = np.zeros((0, 0), np.float32)
        missing = [p.key_claim for p in posted[len(rows):]]
        if missing:
            new = self.gemini.embed(missing, purpose="embed_posted")
            rows = new if len(rows) == 0 else np.vstack([rows, new])
            self.path.parent.mkdir(parents=True, exist_ok=True)
            np.save(self.path, rows.astype(np.float32))
        return rows


# --- Gate -----------------------------------------------------------------------------------


class _DupVerdict(BaseModel):
    index: int = Field(description="candidate number from the CANDIDATES list")
    duplicate_of: int | None = Field(
        description="number of the PAST idea it repeats, or null if it is a new idea")


class _DupVerdicts(BaseModel):
    verdicts: list[_DupVerdict]


LLM_DEDUPE_PROMPT = """You check an educational Instagram account for repeats.
PAST lists core ideas already posted. CANDIDATES lists new ideas.
For each candidate, decide whether it is the SAME CORE IDEA as any past idea: the same
surprising fact or explanation, even in different words or from a different source.
A different fact about the same broad subject is NOT a repeat.

PAST:
{past}

CANDIDATES:
{cands}

Return one verdict per candidate."""


@dataclass
class GateResult:
    kept: list[Candidate] = field(default_factory=list)
    rejected: list[tuple[Candidate, str]] = field(default_factory=list)


class DedupeGate:
    def __init__(self, posted: Sequence[PostedEntry], limits: Limits,
                 gemini: GeminiClient | None = None, embeddings_path: Path | None = None):
        self.posted = list(posted)
        self.limits = limits
        self.gemini = gemini
        self.embeddings_path = embeddings_path
        self.posted_urls = {url_key(p.source_url) for p in self.posted}

    # Check 1
    def check_url(self, c: Candidate) -> str | None:
        if url_key(c.url) in self.posted_urls:
            return "dedupe-1: URL already in posted.json"
        return None

    # Check 2
    def check_title(self, c: Candidate) -> str | None:
        norm = title_norm(c.title)
        for p in self.posted:
            sim = jaccard(norm, p.title_norm)
            if sim >= self.limits.title_jaccard_max:
                return f"dedupe-2: title {sim:.2f} similar to {p.id} ({p.title_norm!r})"
        return None

    def cheap(self, cands: Sequence[Candidate]) -> GateResult:
        """Checks 1-2, plus duplicates within this batch (same URL or similar title)."""
        res = GateResult()
        seen_urls: set[str] = set()
        seen_titles: list[str] = []
        for c in cands:
            reason = self.check_url(c) or self.check_title(c)
            key, norm = url_key(c.url), title_norm(c.title)
            if not reason and key in seen_urls:
                reason = "dedupe-1: same URL as another candidate"
            if not reason and any(jaccard(norm, t) >= self.limits.title_jaccard_max
                                  for t in seen_titles):
                reason = "dedupe-2: same title as another candidate"
            if reason:
                res.rejected.append((c, reason))
            else:
                seen_urls.add(key)
                seen_titles.append(norm)
                res.kept.append(c)
        return res

    # Check 3
    def check_embeddings(self, cands: Sequence[Candidate]) -> dict[int, str]:
        if not self.posted or not cands or self.gemini is None:
            return {}
        if self.embeddings_path is None:
            past = self.gemini.embed([p.key_claim for p in self.posted], purpose="embed_posted")
        else:
            past = EmbeddingCache(self.embeddings_path, self.gemini).vectors(self.posted)
        new = self.gemini.embed([c.key_claim or c.title for c in cands], purpose="embed_cands")
        sims = new @ past.T
        out: dict[int, str] = {}
        for i, row in enumerate(sims):
            j = int(np.argmax(row))
            if row[j] >= self.limits.embedding_cosine_max:
                out[i] = f"dedupe-3: key claim cosine {row[j]:.2f} with {self.posted[j].id}"
        return out

    # Check 4
    def check_llm(self, cands: Sequence[Candidate]) -> dict[int, str]:
        n = self.limits.llm_dedupe_window
        window = self.posted[-n:] if n else []
        if not window or not cands or self.gemini is None:
            return {}
        past = "\n".join(f"{i}. {p.key_claim}" for i, p in enumerate(window, 1))
        lines = "\n".join(f"{i}. {c.key_claim or c.title}" for i, c in enumerate(cands, 1))
        res = self.gemini.generate_json(
            LLM_DEDUPE_PROMPT.format(past=past, cands=lines), _DupVerdicts, purpose="dedupe_llm")
        out: dict[int, str] = {}
        for v in res.verdicts:
            if v.duplicate_of and 1 <= v.index <= len(cands) and 1 <= v.duplicate_of <= len(window):
                out[v.index - 1] = f"dedupe-4: same idea as {window[v.duplicate_of - 1].id}"
        return out

    def semantic(self, cands: Sequence[Candidate]) -> GateResult:
        """Checks 3-4. Needs key_claims, so it runs after triage."""
        cands = list(cands)
        reasons = self.check_embeddings(cands)
        rest = [i for i in range(len(cands)) if i not in reasons]
        llm = self.check_llm([cands[i] for i in rest])
        reasons.update({rest[k]: r for k, r in llm.items()})
        res = GateResult()
        for i, c in enumerate(cands):
            if i in reasons:
                res.rejected.append((c, reasons[i]))
            else:
                res.kept.append(c)
        return res
