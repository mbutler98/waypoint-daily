"""Writer (PLAN.md Phase 4): one Gemini call turns a candidate + SOURCE into slide copy,
which is assembled into a `Post` and checked locally (copy limits, reading grade, hashtags,
figure layout, numbers). Problems come back as plain-English feedback for a rewrite.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

import textstat
from pydantic import BaseModel, Field, ValidationError

from waypoint.config import REPO_ROOT, Config
from waypoint.models import (
    CAPTION_MAX_CHARS,
    MAX_HASHTAGS,
    Candidate,
    Post,
    Slot,
)
from waypoint.write.figures import (
    FigureDraft,
    FigureError,
    build_figure,
    figure_data_numbers,
    figure_texts,
)
from waypoint.write.gemini import GeminiClient
from waypoint.write.numbers import check_numbers

PROMPTS = Path(__file__).resolve().parent / "prompts"
SAMPLES_DIR = REPO_ROOT / "samples"
BRAND_HASHTAG = "#WaypointDaily"
KICKER_MAX_WORDS = 3
SOURCE_MAX_CHARS = 40_000  # what the writer and fact-check see (and are checked against)
CAPTION_BODY_MAX_CHARS = 800
WRITER_TEMPERATURE = 0.4
HOOK_FIT_CHARS = 50  # what 3 hook lines hold, roughly (approved samples: 43-54 characters)
HOOK_MAX_CHARS = 56  # longer than this never fits, so don't spend a render finding out
HOOK_PROBLEM = "the hook is"  # how a hook-only problem starts (see draft.check_and_render)


class InsightDraft(BaseModel):
    headline: str = Field(description="<= 8 words")
    body: str = Field(description="<= 30 words; <= 20 words if this slide has a figure")
    figure: FigureDraft | None = Field(description="optional figure, or null")


class WriterDraft(BaseModel):
    evidence: list[str] = Field(
        description="exactly 5: for slides 1-5 in order, the SOURCE sentence(s) the slide "
                    "relies on, copied word for word")
    kicker: str = Field(description="topic in 1-3 words")
    hook: str = Field(description="<= 12 words and <= 50 characters, tension-led")
    alt_hooks: list[str] = Field(
        default_factory=list,
        description="2 shorter versions of the same hook (<= 40 characters each), used if "
                    "the hook doesn't fit on 3 lines")
    insights: list[InsightDraft] = Field(description="exactly 3: discovery, complication, payoff")
    takeaway: str = Field(description="<= 8 words, memorable, standalone")
    takeaway_support: str = Field(description="<= 14 words")
    caption_body: str = Field(description="3-5 short paragraphs; no hook, source or CTA lines")
    hashtags: list[str] = Field(description="3-4 specific hashtags, without #WaypointDaily")
    alt_text: list[str] = Field(description="exactly 5, one per slide")
    key_claim: str = Field(description="the post's core idea, one sentence")


@dataclass
class Assembled:
    post: Post | None
    problems: list[str] = field(default_factory=list)


def source_for_llm(text: str) -> str:
    return text[:SOURCE_MAX_CHARS]


@cache
def system_prompt() -> str:
    """writer.md with the approved sample posts inlined as house-style references."""
    blocks = []
    for path in sorted(SAMPLES_DIR.glob("*/post.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        slides = [{**s, "figure": f"({s['figure'].get('kind', 'diagram')} figure)"}
                  if s.get("figure") else s for s in data["slides"]]
        blocks.append(json.dumps({"topic": data["topic"], "slides": slides},
                                 ensure_ascii=False, indent=1))
    return (PROMPTS / "writer.md").read_text(encoding="utf-8").replace(
        "{samples}", "\n\n".join(blocks))


def user_prompt(c: Candidate, source: str, feedback: list[str],
                previous: WriterDraft | None = None) -> str:
    meta = (f"TOPIC: {c.topic}\nFACULTY: {c.faculty}\nTITLE: {c.title}\n"
            f"PUBLISHER: {c.publisher}\nKIND: {c.kind}\n"
            f"TRIAGE'S GUESS AT THE KEY CLAIM: {c.key_claim}\n")
    fb = ""
    if previous is not None:
        fb += ("\nYOUR PREVIOUS DRAFT (edit it; keep what the feedback doesn't mention):\n"
               + previous.model_dump_json(indent=1) + "\n")
    if feedback:
        fb += ("\nFEEDBACK on your previous attempts (fix all of these, and don't bring back "
               "a problem that was fixed before):\n"
               + "\n".join(f"- {f}" for f in feedback) + "\n")
    return f"{meta}{fb}\nSOURCE:\n{source}\n"


def write(gemini: GeminiClient, c: Candidate, source: str, feedback: list[str],
          previous: WriterDraft | None = None) -> WriterDraft:
    return gemini.generate_json(
        user_prompt(c, source, feedback, previous), WriterDraft, purpose="write",
        model=gemini.cfg.model, system=system_prompt(), temperature=WRITER_TEMPERATURE)


# --- assembly --------------------------------------------------------------------------------


def _tag(t: str) -> str:
    t = re.sub(r"[^\w]", "", t.strip().lstrip("#"))
    return f"#{t}" if t else ""


def hashtags(draft: WriterDraft) -> list[str]:
    out: list[str] = []
    for t in [*draft.hashtags, BRAND_HASHTAG]:
        t = _tag(t)
        if t and t.lower() not in {x.lower() for x in out}:
            out.append(t)
    own = [t for t in out if t.lower() != BRAND_HASHTAG.lower()]
    return own[:MAX_HASHTAGS - 1] + [BRAND_HASHTAG]


def caption_body(draft: WriterDraft) -> str:
    """The writer's paragraphs; some models return an escaped "\\n" instead of newlines."""
    return draft.caption_body.replace("\\n", "\n").strip()


def caption(draft: WriterDraft, c: Candidate, cfg: Config) -> str:
    parts = [draft.hook.strip(), caption_body(draft),
             f"Source: {c.publisher} — {c.title}", *cfg.brand.caption_cta]
    return "\n\n".join(p for p in parts if p)


def _pydantic_problems(e: ValidationError) -> list[str]:
    out = []
    for err in e.errors():
        loc = ".".join(str(x) for x in err["loc"])
        out.append(f"{loc}: {err['msg'].removeprefix('Value error, ')}")
    return out


def slide_copy(draft: WriterDraft) -> list[str]:
    return [draft.hook, *(t for i in draft.insights for t in (i.headline, i.body)),
            draft.takeaway, draft.takeaway_support]


def reading_grade(draft: WriterDraft) -> float:
    sentences = [t if t.rstrip()[-1:] in ".?!" else f"{t}." for t in slide_copy(draft) if t]
    return textstat.flesch_kincaid_grade(" ".join(sentences))


def assemble(draft: WriterDraft, c: Candidate, post_id: str, slot: Slot, cfg: Config,
             source: str) -> Assembled:
    """Build the Post and run every local check. Returns all problems found."""
    problems: list[str] = []
    if len(draft.insights) != 3:
        return Assembled(None, [f"write exactly 3 insights, not {len(draft.insights)}"])
    if len(draft.hook) > HOOK_MAX_CHARS:
        problems.append(f"{HOOK_PROBLEM} {len(draft.hook)} characters; keep it under "
                        f"{HOOK_FIT_CHARS} so it fits on 3 lines")
    if len(draft.kicker.split()) > KICKER_MAX_WORDS:
        problems.append(f"kicker must be at most {KICKER_MAX_WORDS} words")
    if len(draft.caption_body) > CAPTION_BODY_MAX_CHARS:
        problems.append(f"caption_body is {len(draft.caption_body)} characters; "
                        f"keep it under {CAPTION_BODY_MAX_CHARS}")
    if len(draft.alt_text) != 5:
        problems.append(f"alt_text needs exactly 5 entries, got {len(draft.alt_text)}")

    figures: list[dict | None] = []
    for i, ins in enumerate(draft.insights, 1):
        if ins.figure is None:
            figures.append(None)
            continue
        try:
            figures.append(build_figure(ins.figure, c.publisher))
        except FigureError as e:
            problems.append(f"insight {i} figure: {e}")
            figures.append(None)
        n_words = len(ins.body.split())
        if n_words > 20:
            problems.append(f"insight {i} has a figure, so its body must be <= 20 words; it "
                            f"has {n_words}. Cut {n_words - 20}+ words or drop the figure")

    grade = reading_grade(draft)
    if grade > cfg.limits.max_reading_grade:
        problems.append(f"reading grade is {grade:.1f}; it must be <= "
                        f"{cfg.limits.max_reading_grade:g}. Use shorter sentences and words")

    figs = [i.figure for i in draft.insights if i.figure]
    problems += check_numbers(
        source,
        figure_data=[v for f in figs for v in figure_data_numbers(f)],
        figure_text=[t for f in figs for t in figure_texts(f)],
        copy_text=[*slide_copy(draft), draft.caption_body],
    )

    data = {
        "id": post_id, "slot": slot, "faculty": c.faculty, "topic": c.topic or c.title,
        "source": c.to_source().model_dump(mode="json"), "key_claim": draft.key_claim,
        "slides": [
            {"type": "hook", "kicker": draft.kicker, "headline": draft.hook},
            *({"type": "insight", "n": n, "headline": ins.headline, "body": ins.body,
               "figure": fig}
              for n, (ins, fig) in enumerate(zip(draft.insights, figures, strict=True), 1)),
            {"type": "cta", "headline": draft.takeaway, "body": draft.takeaway_support},
        ],
        "caption": caption(draft, c, cfg), "hashtags": hashtags(draft),
        "alt_text": draft.alt_text,
    }
    if len(data["caption"]) > CAPTION_MAX_CHARS:
        problems.append("caption is too long; shorten caption_body")
    try:
        post = Post.model_validate(data)
    except ValidationError as e:
        return Assembled(None, problems + _pydantic_problems(e))
    return Assembled(post if not problems else None, problems)
