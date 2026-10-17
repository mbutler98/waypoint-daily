"""Command-line entry point: `python -m waypoint <command>`."""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import sys
from pathlib import Path

from waypoint.config import DEFAULT_CONFIG_PATH, REPO_ROOT, load_config

NOT_IMPLEMENTED_EXIT = 2
RENDER_FAILED_EXIT = 1
GATHER_FAILED_EXIT = 1
DRAFT_FAILED_EXIT = 1
SAMPLES_DIR = REPO_ROOT / "samples"
CACHE_DIR_DEFAULT = REPO_ROOT / ".cache"


def _not_implemented(args: argparse.Namespace) -> int:
    print(f"waypoint {args.command}: not implemented", file=sys.stderr)
    return NOT_IMPLEMENTED_EXIT


def _render(args: argparse.Namespace) -> int:
    from waypoint.render.render import RenderError, render_draft_dir

    draft_dir = Path(args.draft_dir)
    if not (draft_dir / "post.json").is_file():
        print(f"render failed: no post.json in {draft_dir}", file=sys.stderr)
        return RENDER_FAILED_EXIT
    config = load_config(args.config or DEFAULT_CONFIG_PATH)
    try:
        res = render_draft_dir(draft_dir, config)
    except RenderError as e:
        print(f"render failed: {e}", file=sys.stderr)
        return RENDER_FAILED_EXIT
    print(f"rendered {len(res.slides)} slides and {res.preview}")
    return 0


def _sample(args: argparse.Namespace) -> int:
    """Render every samples/<slug>/post.json (hand-mapped Waypoint lessons) in place."""
    from waypoint.render.render import RenderError, render_draft_dir

    config = load_config(args.config or DEFAULT_CONFIG_PATH)
    dirs = sorted(p.parent for p in SAMPLES_DIR.glob("*/post.json"))
    if not dirs:
        print(f"no samples/*/post.json found in {SAMPLES_DIR}", file=sys.stderr)
        return RENDER_FAILED_EXIT
    failed = 0
    for d in dirs:
        try:
            res = render_draft_dir(d, config)
            print(f"{d.name}: {len(res.slides)} slides, {res.preview.name}")
        except RenderError as e:
            print(f"{d.name}: render failed: {e}", file=sys.stderr)
            failed += 1
    return RENDER_FAILED_EXIT if failed else 0


def _parse_date(value: str, tz) -> dt.date:
    today = dt.datetime.now(tz).date()
    if value == "today":
        return today
    if value == "tomorrow":
        return today + dt.timedelta(days=1)
    return dt.date.fromisoformat(value)


def _gather(args: argparse.Namespace) -> int:
    from waypoint.gather.fetch import Fetcher
    from waypoint.gather.pipeline import Gatherer, write_result
    from waypoint.gather.sources import load_feeds
    from waypoint.state import CACHE_DIR, EMBEDDINGS_PATH, load_posted
    from waypoint.write.gemini import GeminiError

    config = load_config(args.config or DEFAULT_CONFIG_PATH)
    today = _parse_date(args.date, config.tz)
    gemini = None
    if not args.no_llm:
        gemini = _gemini_or_exit(config, "gather", args.llm)
        if gemini is None:
            return GATHER_FAILED_EXIT
    fetcher = Fetcher(config.gather, CACHE_DIR / "http")
    try:
        res = Gatherer(config, load_posted(), load_feeds(), fetcher, gemini, today,
                       cache_dir=CACHE_DIR, embeddings_path=EMBEDDINGS_PATH).run()
    except GeminiError as e:
        print(f"gather failed: {e}", file=sys.stderr)
        return GATHER_FAILED_EXIT
    finally:
        fetcher.close()
    out = write_result(res, Path(args.out) if args.out else CACHE_DIR / "gather" / str(today))

    for slot, cands in res.slots.items():
        print(f"\n{slot.upper()} ({', '.join(getattr(config.slot_kinds, slot))} first)")
        for i, c in enumerate(cands, 1):
            print(f"  {i}. {c.score:.2f}  s{c.surprise}  {c.faculty:<28} {c.kind:<9} "
                  f"{c.word_count:>5}w  {c.title[:70]}")
    print(f"\n{len(res.candidates)} candidates across {len(res.faculties)} faculties; "
          f"{len(res.rejected)} rejected; {res.fetch_requests} HTTP requests; "
          f"{res.gemini_calls} Gemini calls" + (" (--no-llm)" if args.no_llm else ""))
    print(f"wrote {out}")
    return 0 if all(res.slots.values()) else GATHER_FAILED_EXIT


def _gemini_or_exit(config, what: str, llm: str = "gemini"):
    """The LLM client. `llm="claude"` is the local stopgap (waypoint.write.claude_cli): text
    goes through `claude -p`, embeddings still use Gemini, and model budgets are lifted."""
    from waypoint.state import load_dotenv
    from waypoint.write.gemini import GeminiClient, GeminiError

    load_dotenv()
    try:
        if llm == "claude":
            from waypoint.write.claude_cli import ClaudeCLIError, ClaudeCodeModels

            embed = None
            if os.environ.get("GEMINI_API_KEY"):
                from google import genai

                embed = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
            try:
                client = ClaudeCodeModels(config.gemini.model, embed)
            except ClaudeCLIError as e:
                raise GeminiError(str(e)) from e
            config.gemini.model_budgets = {}  # the Gemini caps don't apply to Claude
            print(f"{what}: using the Claude Code CLI as the LLM (stopgap)", file=sys.stderr)
            return GeminiClient(config.gemini, config.limits.max_gemini_calls_per_run,
                                client=client)
        return GeminiClient(config.gemini, config.limits.max_gemini_calls_per_run)
    except GeminiError as e:
        print(f"{what} failed: {e}", file=sys.stderr)
        return None


def _print_slot(res) -> None:
    if res.skipped:
        print(f"{res.post_id}: already drafted, skipped (use --force to redo)")
    elif res.post:
        hook = res.post.slides[0].headline
        print(f"{res.post_id}: OK  [{res.post.faculty}] {hook}  "
              f"({res.gemini_calls} Gemini calls, {len(res.reports)} candidate(s))")
    else:
        print(f"{res.post_id}: FAILED, see {res.out_dir / 'FAILED.md'}")


def _draft(args: argparse.Namespace) -> int:
    from waypoint.dedupe import url_key
    from waypoint.draft import (
        DRAFTS_DIR,
        SlotResult,
        candidate_from_post,
        draft_slot,
        load_existing,
        pending_drafts,
        recently_skipped,
        skipped_entries,
    )
    from waypoint.gather.fetch import Fetcher
    from waypoint.gather.pipeline import Gatherer
    from waypoint.gather.sources import load_feeds
    from waypoint.select import rank_slot
    from waypoint.state import (
        CACHE_DIR,
        EMBEDDINGS_PATH,
        append_skipped,
        load_posted,
        load_skipped,
    )
    from waypoint.write.gemini import GeminiError

    config = load_config(args.config or DEFAULT_CONFIG_PATH)
    date = _parse_date(args.date, config.tz)
    today = dt.datetime.now(config.tz).date()
    gemini = _gemini_or_exit(config, "draft", args.llm)
    if gemini is None:
        return DRAFT_FAILED_EXIT
    drafts_dir = Path(args.out) if args.out else DRAFTS_DIR
    posted = load_posted()
    fetcher = Fetcher(config.gather, CACHE_DIR / "http")
    results: list[SlotResult] = []
    try:
        gatherer = Gatherer(config, posted, load_feeds(), fetcher, gemini, today,
                            cache_dir=CACHE_DIR, embeddings_path=EMBEDDINGS_PATH)
        cands: list | None = None
        planned = pending_drafts(drafts_dir, {p.id for p in posted}, date.isoformat())
        existing = {slot: None if args.force else load_existing(drafts_dir / f"{date}-{slot}")
                    for slot in ("am", "pm")}
        todo = [slot for slot, post in existing.items() if post is None]
        # the day's kept drafts are planned before either slot is ranked (a kept PM must
        # block its faculty for a redone AM, not only the other way round)
        planned += [candidate_from_post(p) for p in existing.values() if p is not None]
        for slot in ("am", "pm"):
            post_id = f"{date.isoformat()}-{slot}"
            if existing[slot] is not None:
                results.append(SlotResult(slot, post_id, existing[slot], None,
                                          drafts_dir / post_id, skipped=True))
                continue
            if cands is None:
                skip = recently_skipped(load_skipped(), today, config.limits.skip_failed_days)
                # later days' pending drafts (when redoing a past day) are off-limits too
                later = pending_drafts(drafts_dir, {p.id for p in posted}, "",
                                       after=f"{date.isoformat()}-zz")
                skip |= {url_key(c.url) for c in later}
                later_topics = {c.topic.lower() for c in later if c.topic}
                cands = [c for c in gatherer.process(gatherer.collect_all())
                         if url_key(c.url) not in skip
                         and (c.topic or "").lower() not in later_topics]
            ranked = rank_slot(slot, cands, posted, config, today, planned, gatherer.fallback)
            # each slot left gets an equal share of the writer model's remaining budget
            share = gemini.calls_left(config.gemini.model) // (len(todo) - todo.index(slot))
            with gemini.capped(config.gemini.model, share):
                res = draft_slot(slot, post_id, ranked, config, gemini, drafts_dir)
            results.append(res)
            append_skipped(skipped_entries(res, today.isoformat()))
            if res.candidate is not None:
                planned.append(res.candidate)
    except GeminiError as e:
        print(f"draft failed: {e}", file=sys.stderr)
        return DRAFT_FAILED_EXIT
    finally:
        fetcher.close()
        gemini.log_usage()
    for res in results:
        _print_slot(res)
    print(f"Gemini calls this run: {gemini.total_calls} of "
          f"{config.limits.max_gemini_calls_per_run}")
    return 0 if all(r.post for r in results) else DRAFT_FAILED_EXIT


def _write(args: argparse.Namespace) -> int:
    """Draft one gathered candidate (for prompt tuning): .cache/gather/<d>/candidates/<slug>/."""
    from waypoint.draft import draft_slot
    from waypoint.models import Candidate

    config = load_config(args.config or DEFAULT_CONFIG_PATH)
    cdir = Path(args.candidate_dir)
    c = Candidate.model_validate_json((cdir / "candidate.json").read_text(encoding="utf-8"))
    c.text = (cdir / "source.txt").read_text(encoding="utf-8")
    gemini = _gemini_or_exit(config, "write", args.llm)
    if gemini is None:
        return DRAFT_FAILED_EXIT
    post_id = args.id or f"{dt.datetime.now(config.tz).date().isoformat()}-{args.slot}"
    out = Path(args.out) if args.out else CACHE_DIR_DEFAULT / "write"
    try:
        res = draft_slot(post_id[-2:], post_id, [c], config, gemini, out)
    finally:
        gemini.log_usage()
    _print_slot(res)
    return 0 if res.post else DRAFT_FAILED_EXIT


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="waypoint",
        description="Waypoint Daily: educational Instagram carousels, twice a day.",
    )
    parser.add_argument("--config", default=None, help="path to config.yaml")
    parser.add_argument("--llm", choices=["gemini", "claude"],
                        default=os.environ.get("WAYPOINT_LLM", "gemini"),
                        help="LLM backend; 'claude' is a local stopgap via the claude CLI")
    sub = parser.add_subparsers(dest="command", required=True, metavar="<command>")

    p = sub.add_parser("gather", help="fetch, dedupe and rank source candidates (Phase 3)")
    p.add_argument("--no-llm", action="store_true", help="run only the non-LLM dedupe checks")
    p.add_argument("--date", default="today", help="YYYY-MM-DD, 'today' or 'tomorrow'")
    p.add_argument("--out", default=None, help="output dir (default .cache/gather/<date>)")

    p = sub.add_parser("draft", help="write, fact-check and render a day's two posts (Phase 4)")
    p.add_argument("--date", default="tomorrow", help="YYYY-MM-DD, 'today' or 'tomorrow'")
    p.add_argument("--force", action="store_true", help="redo slots that already have a draft")
    p.add_argument("--out", default=None, help="drafts dir (default drafts/)")

    p = sub.add_parser("write", help="draft one gathered candidate (prompt tuning, Phase 4)")
    p.add_argument("candidate_dir", help=".cache/gather/<date>/candidates/<slug>/")
    p.add_argument("--slot", default="am", choices=["am", "pm"])
    p.add_argument("--id", default=None, help="post id (default <today>-<slot>)")
    p.add_argument("--out", default=None, help="output dir (default .cache/write/)")

    p = sub.add_parser("render", help="render a draft's post.json to slide JPEGs (Phase 2)")
    p.add_argument("draft_dir", help="path to drafts/<date>-<slot>/")

    p = sub.add_parser("publish", help="publish the draft for a slot to Instagram (Phase 5)")
    p.add_argument("slot", help="'am', 'pm' or a draft directory")

    sub.add_parser("refresh-token", help="refresh the long-lived Instagram token (Phase 5)")
    sub.add_parser("sample", help="render sample carousels from Waypoint lessons (Phase 2)")

    for action in sub.choices.values():
        action.set_defaults(func=_not_implemented)
    sub.choices["gather"].set_defaults(func=_gather)
    sub.choices["draft"].set_defaults(func=_draft)
    sub.choices["write"].set_defaults(func=_write)
    sub.choices["render"].set_defaults(func=_render)
    sub.choices["sample"].set_defaults(func=_sample)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    for stream in (sys.stdout, sys.stderr):  # titles and logs are UTF-8 (Windows consoles)
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    for noisy in ("httpx", "httpcore", "trafilatura", "google_genai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return args.func(args)
