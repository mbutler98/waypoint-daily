"""Data contracts shared by every pipeline stage (PLAN.md sections 3.2, 3.3 and 4.2).

Change these only together with PLAN.md section 3.2/3.3.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Annotated, Any, Literal
from urllib.parse import parse_qsl, urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# --- Copy limits (PLAN.md 4.2) -------------------------------------------------------------

HOOK_MAX_WORDS = 12
INSIGHT_HEADLINE_MAX_WORDS = 8
INSIGHT_BODY_MAX_WORDS = 30
TAKEAWAY_MAX_WORDS = 8  # last slide headline: the post's memorable takeaway
TAKEAWAY_SUPPORT_MAX_WORDS = 14
CAPTION_MAX_CHARS = 2200
MAX_HASHTAGS = 5
MAX_EMOJI_PER_POST = 1
MAX_FIGURES_PER_POST = 2
MAX_MANIM_FIGURES_PER_POST = 1
SLIDE_COUNT = 5

Slot = Literal["am", "pm"]
SourceKind = Literal["news", "paper", "evergreen"]
PostStatus = Literal["draft", "rejected", "approved", "published", "failed"]
PostedStatus = Literal["published", "rejected", "failed"]

FACULTIES: tuple[str, ...] = (
    "Animals & Nature",
    "Space",
    "Human Body & Health",
    "Mind & Behaviour",
    "Everyday Physics & Chemistry",
    "Trades & Home",
    "Engineering & Tech",
    "AI & Computing",
    "Money & Markets",
    "History",
    "Geography & Earth",
    "Food & Cooking",
    "Sport & Strength",
    "Flight",
)

POST_ID_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-(am|pm)$")
HASHTAG_RE = re.compile(r"^#[^\s#]+$")
EMOJI_RE = re.compile(
    "[\U0001f300-\U0001faff\U00002600-\U000027bf\U0001f000-\U0001f2ff\U0001f900-\U0001f9ff]"
)


def word_count(text: str) -> int:
    return len(text.split())


def emoji_count(text: str) -> int:
    return len(EMOJI_RE.findall(text))


def _max_words(text: str, limit: int, what: str) -> str:
    n = word_count(text)
    if n > limit:
        raise ValueError(f"{what} has {n} words; the limit is {limit}")
    return text


def _no_emoji(text: str, what: str) -> str:
    if emoji_count(text):
        raise ValueError(f"{what} must not contain emoji")
    return text


def _check_faculty(value: str) -> str:
    if value not in FACULTIES:
        raise ValueError(f"unknown faculty {value!r}; expected one of {FACULTIES}")
    return value


def _check_canonical_url(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError(f"not an http(s) URL: {url!r}")
    if parts.fragment:
        raise ValueError(f"canonical URL must not have a fragment: {url!r}")
    if any(k.lower().startswith("utm_") for k, _ in parse_qsl(parts.query)):
        raise ValueError(f"canonical URL must not have utm_* parameters: {url!r}")
    return url


NonEmptyStr = Annotated[str, Field(min_length=1)]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# --- Figures --------------------------------------------------------------------------------


class VisualsFigure(BaseModel):
    """A Waypoint visuals.js spec. Only `type` is checked here; the renderer validates the rest."""

    model_config = ConfigDict(extra="allow")

    type: Literal["chart", "diagram"]


class ManimFigure(_Model):
    """A call to one of the curated Manim templates (Phase 7)."""

    type: Literal["manim"]
    template: NonEmptyStr
    params: dict[str, Any] = Field(default_factory=dict)


Figure = Annotated[VisualsFigure | ManimFigure, Field(discriminator="type")]


# --- Slides ---------------------------------------------------------------------------------


class HookSlide(_Model):
    type: Literal["hook"]
    kicker: NonEmptyStr
    headline: NonEmptyStr

    @field_validator("kicker", "headline")
    @classmethod
    def _limits(cls, v: str, info) -> str:
        _no_emoji(v, f"hook {info.field_name}")
        if info.field_name == "headline":
            _max_words(v, HOOK_MAX_WORDS, "hook headline")
        return v


class InsightSlide(_Model):
    type: Literal["insight"]
    n: Literal[1, 2, 3]
    headline: NonEmptyStr
    body: NonEmptyStr
    figure: Figure | None = None

    @field_validator("headline")
    @classmethod
    def _headline(cls, v: str) -> str:
        _no_emoji(v, "insight headline")
        return _max_words(v, INSIGHT_HEADLINE_MAX_WORDS, "insight headline")

    @field_validator("body")
    @classmethod
    def _body(cls, v: str) -> str:
        return _max_words(v, INSIGHT_BODY_MAX_WORDS, "insight body")


class CtaSlide(_Model):
    """The sign-off slide: the post's takeaway (headline) and one supporting line (body).
    The fixed brand promise and handle come from config, not from here."""

    type: Literal["cta"]
    headline: NonEmptyStr
    body: NonEmptyStr

    @field_validator("headline")
    @classmethod
    def _headline(cls, v: str) -> str:
        _no_emoji(v, "takeaway")
        return _max_words(v, TAKEAWAY_MAX_WORDS, "takeaway")

    @field_validator("body")
    @classmethod
    def _body(cls, v: str) -> str:
        _no_emoji(v, "takeaway support line")
        return _max_words(v, TAKEAWAY_SUPPORT_MAX_WORDS, "takeaway support line")


Slide = Annotated[HookSlide | InsightSlide | CtaSlide, Field(discriminator="type")]


# --- Post (post.json) -----------------------------------------------------------------------


class Source(_Model):
    url: str
    title: NonEmptyStr
    publisher: NonEmptyStr
    published: dt.date | None = None
    kind: SourceKind

    @field_validator("url")
    @classmethod
    def _url(cls, v: str) -> str:
        return _check_canonical_url(v)


class Post(_Model):
    id: str
    slot: Slot
    faculty: str
    topic: NonEmptyStr
    source: Source
    key_claim: NonEmptyStr
    slides: list[Slide]
    caption: str = Field(min_length=1, max_length=CAPTION_MAX_CHARS)
    hashtags: list[str] = Field(default_factory=list, max_length=MAX_HASHTAGS)
    alt_text: list[NonEmptyStr]
    status: PostStatus = "draft"
    approval_issue: int | None = None
    ig_media_id: str | None = None
    ig_permalink: str | None = None

    @field_validator("faculty")
    @classmethod
    def _faculty(cls, v: str) -> str:
        return _check_faculty(v)

    @field_validator("hashtags")
    @classmethod
    def _hashtags(cls, v: list[str]) -> list[str]:
        for tag in v:
            if not HASHTAG_RE.match(tag):
                raise ValueError(f"bad hashtag {tag!r}; expected '#word'")
        if len({t.lower() for t in v}) != len(v):
            raise ValueError("duplicate hashtags")
        return v

    @model_validator(mode="after")
    def _structure(self) -> Post:
        m = POST_ID_RE.match(self.id)
        if not m:
            raise ValueError(f"id {self.id!r} must look like YYYY-MM-DD-am|pm")
        dt.date.fromisoformat(m.group(1))
        if m.group(2) != self.slot:
            raise ValueError(f"id {self.id!r} does not match slot {self.slot!r}")

        types = [s.type for s in self.slides]
        expected = ["hook", "insight", "insight", "insight", "cta"]
        if types != expected:
            raise ValueError(f"slides must be {expected}, got {types}")
        if [s.n for s in self.slides[1:4]] != [1, 2, 3]:
            raise ValueError("insight slides must be numbered 1, 2, 3 in order")

        if len(self.alt_text) != SLIDE_COUNT:
            raise ValueError(f"alt_text needs {SLIDE_COUNT} entries, got {len(self.alt_text)}")

        figures = [s.figure for s in self.slides if isinstance(s, InsightSlide) and s.figure]
        if len(figures) > MAX_FIGURES_PER_POST:
            raise ValueError(f"at most {MAX_FIGURES_PER_POST} figures per post")
        if sum(isinstance(f, ManimFigure) for f in figures) > MAX_MANIM_FIGURES_PER_POST:
            raise ValueError(f"at most {MAX_MANIM_FIGURES_PER_POST} manim figure per post")

        texts = [self.caption] + [
            getattr(s, field) for s in self.slides for field in ("kicker", "headline", "body")
            if hasattr(s, field)
        ]
        if sum(emoji_count(t) for t in texts) > MAX_EMOJI_PER_POST:
            raise ValueError(f"at most {MAX_EMOJI_PER_POST} emoji per post")
        return self


# --- Gather candidates ----------------------------------------------------------------------


class Candidate(_Model):
    """A source article that may become a post (Phase 3 output, Phase 4 input).

    `text` is the extracted article body; it is saved as source.txt, not in the JSON.
    The triage fields (faculty, topic, key_claim, surprise, safe) are filled by
    waypoint.gather.triage, by Gemini or, with --no-llm, by simple heuristics.
    """

    url: str
    title: NonEmptyStr
    publisher: NonEmptyStr
    published: dt.date | None = None
    kind: SourceKind
    feed: NonEmptyStr
    faculty_hints: list[str] = Field(default_factory=list)
    summary: str = ""
    text: str = Field(default="", exclude=True, repr=False)
    word_count: int = 0
    faculty: str | None = None
    topic: str | None = None
    key_claim: str | None = None
    surprise: int | None = Field(default=None, ge=1, le=5)
    safe: bool = True
    safety_reason: str | None = None
    score: float = 0.0

    @field_validator("url")
    @classmethod
    def _url(cls, v: str) -> str:
        return _check_canonical_url(v)

    @field_validator("faculty_hints")
    @classmethod
    def _hints(cls, v: list[str]) -> list[str]:
        return [_check_faculty(f) for f in v]

    @field_validator("faculty")
    @classmethod
    def _faculty(cls, v: str | None) -> str | None:
        return None if v is None else _check_faculty(v)

    def to_source(self) -> Source:
        return Source(url=self.url, title=self.title, publisher=self.publisher,
                      published=self.published, kind=self.kind)



# --- state/posted.json ----------------------------------------------------------------------


class PostedEntry(_Model):
    """One append-only record in state/posted.json."""

    id: str
    date: dt.date
    faculty: str
    topic: NonEmptyStr
    source_url: str
    title_norm: str
    key_claim: NonEmptyStr
    ig_media_id: str | None = None
    ig_permalink: str | None = None
    status: PostedStatus = "published"

    @field_validator("faculty")
    @classmethod
    def _faculty(cls, v: str) -> str:
        return _check_faculty(v)

    @field_validator("source_url")
    @classmethod
    def _url(cls, v: str) -> str:
        return _check_canonical_url(v)

    @model_validator(mode="after")
    def _id_matches_date(self) -> PostedEntry:
        if not POST_ID_RE.match(self.id) or self.id[:10] != self.date.isoformat():
            raise ValueError(f"id {self.id!r} must be '<date>-am|pm' matching date {self.date}")
        return self
