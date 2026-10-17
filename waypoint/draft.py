"""`waypoint draft`: for each slot, take the ranked candidates and try them in order:

    write -> local checks (copy limits, reading grade, figure layout, numbers)
          -> render (text must fit) -> fact-check (Gemini + evidence quotes)

Any failure is fed back to the writer for a rewrite. A candidate gets at most
1 + limits.max_writer_retries writer calls and MAX_FACTCHECKS fact-checks; then the next
candidate is tried, if the writer model's budget still has room for a write and a
fact-check. A slot that can't be filled gets drafts/<id>/FAILED.md and no post.
Work happens in .cache/staging/<id>/ and only a complete draft is copied into drafts/.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import shutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from waypoint.config import REPO_ROOT, Config
from waypoint.dedupe import url_key
from waypoint.models import Candidate, Post, Slot
from waypoint.render.render import RenderError, RenderResult, SlideOverflowError
from waypoint.state import CACHE_DIR
from waypoint.write.factcheck import factcheck
from waypoint.write.gemini import GeminiBudgetExceeded, GeminiClient, GeminiError
from waypoint.write.writer import (
    HOOK_FIT_CHARS,
    HOOK_PROBLEM,
    WriterDraft,
    assemble,
    source_for_llm,
    write,
)

log = logging.getLogger(__name__)

DRAFTS_DIR = REPO_ROOT / "drafts"
STAGING_DIR = CACHE_DIR / "staging"
MAX_FACTCHECKS = 2  # the first check plus one re-check after a rewrite (PLAN.md Phase 4)
MIN_CALLS_PER_CANDIDATE = 2  # a write and a fact-check; don't start a candidate without them

Renderer = Callable[[Post, Path, Config], RenderResult]


@dataclass
class CandidateReport:
    """Everything that happened to one candidate; saved as factcheck.json."""

    url: str
    title: str
    passed: bool = False
    reason: str = ""
    attempts: list[dict[str, Any]] = field(default_factory=list)
    gemini_calls: int = 0

    def to_json(self) -> dict[str, Any]:
        return {"source_url": self.url, "title": self.title, "passed": self.passed,
                "reason": self.reason, "gemini_calls": self.gemini_calls,
                "attempts": self.attempts}


@dataclass
class SlotResult:
    slot: Slot
    post_id: str
    post: Post | None
    candidate: Candidate | None
    out_dir: Path
    reports: list[CandidateReport] = field(default_factory=list)
    skipped: bool = False

    @property
    def gemini_calls(self) -> int:
        return sum(r.gemini_calls for r in self.reports)


def default_renderer(post: Post, out_dir: Path, cfg: Config) -> RenderResult:
    from waypoint.render.render import render_post

    return render_post(post, out_dir, cfg)


def check_and_render(draft: WriterDraft, c: Candidate, post_id: str, slot: Slot, cfg: Config,
                     source: str, stage: Path, render: Renderer
                     ) -> tuple[Post | None, WriterDraft, str, list[str]]:
    """Assemble, check and render `draft` into `stage`. When the hook is the only problem
    (too long, or it overflows its 3 lines), try the writer's alternative hooks first, so a
    hook alone never costs a rewrite. Returns (post, draft used, failed stage, problems)."""
    variants = [draft, *(draft.model_copy(update={"hook": h.strip()})
                         for h in draft.alt_hooks if h.strip() and h.strip() != draft.hook)]
    for i, d in enumerate(variants):
        more = i < len(variants) - 1
        asm = assemble(d, c, post_id, slot, cfg, source)
        if asm.post is None:
            if more and all(p.startswith(HOOK_PROBLEM) for p in asm.problems):
                continue
            return None, d, "checks", asm.problems
        if stage.exists():
            shutil.rmtree(stage)
        stage.mkdir(parents=True)
        try:
            render(asm.post, stage, cfg)
        except SlideOverflowError as e:
            if e.slide_index == 1 and more:
                continue
            hint = (f" The hook is {len(d.hook)} characters and none of the alternatives "
                    f"fit either; write hooks under {HOOK_FIT_CHARS - 10} characters."
                    if e.slide_index == 1 else
                    " The figure is too cramped to read: use fewer items and shorter labels, "
                    "or drop the figure." if "figure" in e.detail else "")
            return None, d, "render", [f"slide {e.slide_index} does not fit on the slide "
                                       f"({e.detail}); shorten that slide's text.{hint}"]
        except RenderError as e:
            return None, d, "render", [f"a slide could not be rendered ({e}); "
                                       "simplify or drop the figure"]
        return asm.post, d, "", []
    raise AssertionError("unreachable: the last variant always returns")


def draft_candidate(c: Candidate, post_id: str, slot: Slot, cfg: Config, gemini: GeminiClient,
                    stage: Path, render: Renderer = default_renderer
                    ) -> tuple[Post | None, CandidateReport]:
    """Try to turn one candidate into a rendered, fact-checked post in `stage`."""
    report = CandidateReport(url=c.url, title=c.title)
    source = source_for_llm(c.text)
    start_calls = gemini.total_calls
    feedback: list[str] = []  # cumulative, so a rewrite can't bring an old problem back
    previous = None
    checks = 0
    max_writes = 1 + cfg.limits.max_writer_retries

    def note(step: dict[str, Any], stage_name: str, problems: list[str], **extra: Any) -> None:
        step.update(stage=stage_name, problems=problems, **extra)
        feedback.extend(p for p in problems if p not in feedback)

    try:
        for attempt in range(1, max_writes + 1):
            step: dict[str, Any] = {"attempt": attempt}
            report.attempts.append(step)
            draft = write(gemini, c, source, feedback, previous)
            post, previous, stage_name, problems = check_and_render(
                draft, c, post_id, slot, cfg, source, stage, render)
            if post is None:
                note(step, stage_name, problems)
                continue
            if previous.hook != draft.hook:
                step["hook_used"] = previous.hook
            fc = factcheck(gemini, post, source)
            checks += 1
            note(step, "factcheck", fc.failures, passed=fc.passed,
                 claims=[x.model_dump() for x in fc.claims])
            if fc.passed:
                report.passed = True
                return post, report
            if checks >= MAX_FACTCHECKS:
                report.reason = "fact-check failed after a rewrite"
                return None, report
        report.reason = f"no valid draft after {max_writes} writer calls"
        return None, report
    except GeminiBudgetExceeded:
        raise
    except GeminiError as e:
        report.reason = f"Gemini error: {e}"
        return None, report
    finally:
        report.gemini_calls = gemini.total_calls - start_calls
        log.info("draft %s: %s (%d Gemini calls) %s", post_id,
                 "passed" if report.passed else f"failed: {report.reason}",
                 report.gemini_calls, c.title[:60])


def _write_failed(out_dir: Path, post_id: str, reports: Sequence[CandidateReport],
                  why: str) -> None:
    if out_dir.exists():  # never leave an old post.json next to FAILED.md
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    lines = [f"# {post_id}: no post", "", why, ""]
    for r in reports:
        lines.append(f"- {r.title} ({r.url}): {r.reason}")
        last = r.attempts[-1] if r.attempts else {}
        for p in last.get("problems", [])[:8]:
            lines.append(f"  - {p}")
    (out_dir / "FAILED.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out_dir / "factcheck.json").write_text(
        json.dumps([r.to_json() for r in reports], indent=2, ensure_ascii=False),
        encoding="utf-8")


def publish_draft(stage: Path, out_dir: Path, post: Post, c: Candidate,
                  report: CandidateReport) -> None:
    """Move a finished draft from staging into drafts/<id>/ (replacing what was there)."""
    if out_dir.exists():
        shutil.rmtree(out_dir)
    shutil.copytree(stage, out_dir)
    (out_dir / "post.json").write_text(post.model_dump_json(indent=2), encoding="utf-8")
    (out_dir / "source.txt").write_text(source_for_llm(c.text), encoding="utf-8")
    (out_dir / "factcheck.json").write_text(
        json.dumps(report.to_json(), indent=2, ensure_ascii=False), encoding="utf-8")
    shutil.rmtree(stage, ignore_errors=True)


def draft_slot(slot: Slot, post_id: str, ranked: Sequence[Candidate], cfg: Config,
               gemini: GeminiClient, drafts_dir: Path = DRAFTS_DIR,
               staging_dir: Path = STAGING_DIR, render: Renderer = default_renderer
               ) -> SlotResult:
    out_dir = drafts_dir / post_id
    result = SlotResult(slot, post_id, None, None, out_dir)
    tries = list(ranked)[:cfg.limits.max_draft_candidates]
    if not tries:
        _write_failed(out_dir, post_id, [], "No eligible candidates for this slot.")
        return result
    for c in tries:
        left = gemini.calls_left(cfg.gemini.model)
        if left < MIN_CALLS_PER_CANDIDATE:
            result.reports.append(CandidateReport(
                c.url, c.title, reason=f"not tried: only {left} {cfg.gemini.model} call(s) "
                                       "left in this slot's budget"))
            break
        try:
            post, report = draft_candidate(c, post_id, slot, cfg, gemini,
                                           staging_dir / post_id, render)
        except GeminiBudgetExceeded as e:
            result.reports.append(CandidateReport(c.url, c.title, reason=str(e)))
            break
        result.reports.append(report)
        if post is not None:
            publish_draft(staging_dir / post_id, out_dir, post, c, report)
            result.post, result.candidate = post, c
            return result
    _write_failed(out_dir, post_id, result.reports,
                  f"Tried {len(result.reports)} candidate(s); none passed every check.")
    return result


CONTENT_FAILURES = ("no valid draft", "fact-check failed")  # not Gemini errors or budgets


def skipped_entries(res: SlotResult, date: str) -> list[dict]:
    """The candidates this slot gave up on for content reasons (state/skipped.json)."""
    return [{"url": r.url, "title": r.title, "date": date, "post_id": res.post_id,
             "reason": r.reason}
            for r in res.reports if not r.passed and r.reason.startswith(CONTENT_FAILURES)]


def recently_skipped(entries: Sequence[dict], today: dt.date, days: int) -> set[str]:
    """url_keys of candidates skipped in the last `days` days."""
    since = today - dt.timedelta(days=days)
    return {url_key(e["url"]) for e in entries if dt.date.fromisoformat(e["date"]) > since}


def candidate_from_post(post: Post) -> Candidate:
    """An already-written draft as a planned pick, so the other slot respects rotation."""
    return Candidate(url=post.source.url, title=post.source.title,
                     publisher=post.source.publisher, published=post.source.published,
                     kind=post.source.kind, feed="draft", faculty=post.faculty, topic=post.topic,
                     key_claim=post.key_claim)


def pending_drafts(drafts_dir: Path, posted_ids: set[str], before: str,
                   after: str | None = None) -> list[Candidate]:
    """Drafts that aren't in posted.json yet (awaiting approval or publishing), oldest
    first, as planned picks: their articles, topics and faculties count for rotation, so
    another day's draft can't pick the same thing again. Ids < `before`, or with `after`,
    ids > `after` (later days, when an earlier day is redone)."""
    out = []
    for path in sorted(drafts_dir.glob("*/post.json")):
        post_id = path.parent.name
        wanted = post_id > after if after is not None else post_id < before
        if wanted and post_id not in posted_ids:
            post = load_existing(path.parent)
            if post is not None and post.status in ("draft", "approved"):
                out.append(candidate_from_post(post))
    return out


def load_existing(out_dir: Path) -> Post | None:
    path = out_dir / "post.json"
    if not path.is_file():
        return None
    return Post.model_validate_json(path.read_text(encoding="utf-8"))
