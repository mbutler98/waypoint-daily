"""Fact-check (PLAN.md 4.3, Phase 4): a second Gemini call lists every claim in the draft
with a verdict and a verbatim evidence quote from SOURCE. Then two programmatic checks:
each "supported" verdict's quote must really be in SOURCE (so the model can't vouch with
an invented quote). The number check (waypoint.write.numbers) runs before this, in
waypoint.write.writer.assemble, so a draft with an unsourced number never gets this far.
"""

from __future__ import annotations

import re
from functools import cache
from pathlib import Path

from pydantic import BaseModel, Field

from waypoint.models import CtaSlide, HookSlide, InsightSlide, Post
from waypoint.write.gemini import GeminiClient

PROMPTS = Path(__file__).resolve().parent / "prompts"
QUOTE_MIN_OVERLAP = 0.6  # share of the quote's word trigrams that must appear in SOURCE


class ClaimCheck(BaseModel):
    where: str
    claim: str
    supported: bool
    evidence_quote: str
    problem: str = Field(description="empty when supported")


class _Claims(BaseModel):
    claims: list[ClaimCheck]


class FactCheckResult(BaseModel):
    passed: bool
    claims: list[ClaimCheck]
    quote_problems: list[str]

    @property
    def failures(self) -> list[str]:
        """Writer-facing feedback, one line per problem."""
        out = [f"{c.where}: \"{c.claim}\" is not supported by SOURCE. {c.problem}".strip()
               for c in self.claims if not c.supported]
        return out + self.quote_problems


@cache
def system_prompt() -> str:
    return (PROMPTS / "factcheck.md").read_text(encoding="utf-8")


def _figure_text(fig) -> str:
    if fig is None:
        return ""
    d = fig.model_dump(mode="json")
    keep = {k: d[k] for k in ("title", "kind", "data", "x", "series") if k in d}
    if d.get("type") == "diagram":
        keep["items"] = [" ".join(filter(None, (n.get("label"), n.get("sub"))))
                         for n in d.get("nodes", []) if n.get("label")]
    return f"\n  figure: {keep}"


def render_draft(post: Post) -> str:
    lines = []
    for i, s in enumerate(post.slides, 1):
        if isinstance(s, HookSlide):
            lines.append(f"slide {i} (hook): [{s.kicker}] {s.headline}")
        elif isinstance(s, InsightSlide):
            lines.append(f"slide {i}: {s.headline}. {s.body}{_figure_text(s.figure)}")
        elif isinstance(s, CtaSlide):
            lines.append(f"slide {i} (takeaway): {s.headline} {s.body}")
    return "\n".join(lines) + f"\n\ncaption:\n{post.caption}"


def _words(text: str) -> list[str]:
    text = text.lower().replace("’", "'").replace("‘", "'")
    return re.findall(r"[a-z0-9']+", text)


def quote_in_source(quote: str, source_trigrams: set[tuple[str, ...]],
                    source_words: str) -> bool:
    words = _words(quote)
    if not words:
        return False
    if len(words) < 3:
        return f" {' '.join(words)} " in source_words
    grams = [tuple(words[i:i + 3]) for i in range(len(words) - 2)]
    return sum(g in source_trigrams for g in grams) / len(grams) >= QUOTE_MIN_OVERLAP


def check_quotes(claims: list[ClaimCheck], source: str) -> list[str]:
    words = _words(source)
    trigrams = {tuple(words[i:i + 3]) for i in range(len(words) - 2)}
    joined = f" {' '.join(words)} "
    return [f"{c.where}: the evidence for \"{c.claim}\" could not be found in SOURCE; "
            "make sure this claim is really stated there"
            for c in claims if c.supported and not quote_in_source(c.evidence_quote, trigrams,
                                                                    joined)]


def factcheck(gemini: GeminiClient, post: Post, source: str) -> FactCheckResult:
    prompt = f"SOURCE:\n{source}\n\nDRAFT:\n{render_draft(post)}\n"
    res = gemini.generate_json(prompt, _Claims, purpose="factcheck", model=gemini.cfg.model,
                               system=system_prompt())
    quote_problems = check_quotes(res.claims, source)
    passed = bool(res.claims) and all(c.supported for c in res.claims) and not quote_problems
    return FactCheckResult(passed=passed, claims=res.claims, quote_problems=quote_problems)
