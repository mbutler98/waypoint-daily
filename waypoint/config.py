"""Load and validate config.yaml (PLAN.md 4.6)."""

from __future__ import annotations

import re
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from waypoint.models import FACULTIES, SourceKind

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = REPO_ROOT / "config.yaml"

HHMM_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _check_hhmm(v: str) -> str:
    if not HHMM_RE.match(v):
        raise ValueError(f"time {v!r} must be HH:MM (24h)")
    return v


class Slots(_Model):
    am: str
    pm: str

    @field_validator("am", "pm")
    @classmethod
    def _hhmm(cls, v: str) -> str:
        return _check_hhmm(v)

    @model_validator(mode="after")
    def _order(self) -> Slots:
        if self.am >= self.pm:
            raise ValueError("the am slot must be earlier than the pm slot")
        return self


class SlotKinds(_Model):
    am: list[SourceKind] = Field(min_length=1)
    pm: list[SourceKind] = Field(min_length=1)


class Limits(_Model):
    faculty_cooldown_posts: int = Field(ge=0)
    topic_cooldown_days: int = Field(ge=0)
    title_jaccard_max: float = Field(gt=0, le=1)
    embedding_cosine_max: float = Field(gt=0, le=1)
    llm_dedupe_window: int = Field(ge=0)
    min_source_words: int = Field(ge=0)
    max_reading_grade: float = Field(gt=0)
    candidates_per_slot: int = Field(ge=1)
    max_gemini_calls_per_run: int = Field(ge=1)
    max_writer_retries: int = Field(ge=0)


class Gemini(_Model):
    model: str = Field(min_length=1)
    fast_model: str = Field(min_length=1)
    embedding_model: str = Field(min_length=1)
    requests_per_minute: int = Field(ge=1)


class Gather(_Model):
    user_agent: str = Field(min_length=1)
    per_host_delay_s: float = Field(ge=0)
    timeout_s: float = Field(gt=0)
    cache_ttl_hours: float = Field(ge=0)
    max_items_per_feed: int = Field(ge=1)
    max_triage: int = Field(ge=1)
    triage_batch_size: int = Field(ge=1)
    fallback_items: int = Field(ge=0)


class Instagram(_Model):
    api_version: str = Field(pattern=r"^v\d+\.\d+$")


class Brand(_Model):
    name: str = Field(min_length=1)
    handle: str = Field(pattern=r"^@[A-Za-z0-9._]{1,30}$")
    tagline: str = Field(min_length=1)
    signoff_kicker: str = Field(min_length=1)       # small line on the last slide
    signoff_lines: list[str] = Field(min_length=1)  # fixed brand promise on the last slide
    caption_cta: list[str] = Field(min_length=1)    # closing lines of every caption


class Config(_Model):
    timezone: str
    slots: Slots
    draft_time: str
    require_approval: bool
    dry_run: bool
    slot_kinds: SlotKinds
    faculties: dict[str, float]
    limits: Limits
    gemini: Gemini
    gather: Gather
    instagram: Instagram
    brand: Brand
    pages_base_url: str = Field(pattern=r"^https://.+/$")
    media_retention_days: int = Field(ge=1)

    @field_validator("timezone")
    @classmethod
    def _timezone(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as e:
            raise ValueError(f"unknown timezone {v!r}") from e
        return v

    @field_validator("draft_time")
    @classmethod
    def _draft_time(cls, v: str) -> str:
        return _check_hhmm(v)

    @field_validator("faculties")
    @classmethod
    def _faculties(cls, v: dict[str, float]) -> dict[str, float]:
        missing = set(FACULTIES) - v.keys()
        unknown = v.keys() - set(FACULTIES)
        if missing or unknown:
            raise ValueError(
                f"faculties mismatch: missing {sorted(missing)}, unknown {sorted(unknown)}"
            )
        if any(w < 0 for w in v.values()) or not any(w > 0 for w in v.values()):
            raise ValueError("faculty weights must be >= 0 with at least one > 0")
        return v

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


def load_config(path: str | Path = DEFAULT_CONFIG_PATH) -> Config:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return Config.model_validate(data)
