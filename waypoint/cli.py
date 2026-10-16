"""Command-line entry point: `python -m waypoint <command>`."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from waypoint.config import DEFAULT_CONFIG_PATH, REPO_ROOT, load_config

NOT_IMPLEMENTED_EXIT = 2
RENDER_FAILED_EXIT = 1
SAMPLES_DIR = REPO_ROOT / "samples"


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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="waypoint",
        description="Waypoint Daily: educational Instagram carousels, twice a day.",
    )
    parser.add_argument("--config", default=None, help="path to config.yaml")
    sub = parser.add_subparsers(dest="command", required=True, metavar="<command>")

    p = sub.add_parser("gather", help="fetch, dedupe and rank source candidates (Phase 3)")
    p.add_argument("--no-llm", action="store_true", help="run only the non-LLM dedupe checks")

    p = sub.add_parser("draft", help="write, fact-check and render a day's two posts (Phase 4)")
    p.add_argument("--date", default="tomorrow", help="YYYY-MM-DD or 'tomorrow'")

    p = sub.add_parser("render", help="render a draft's post.json to slide JPEGs (Phase 2)")
    p.add_argument("draft_dir", help="path to drafts/<date>-<slot>/")

    p = sub.add_parser("publish", help="publish the draft for a slot to Instagram (Phase 5)")
    p.add_argument("slot", help="'am', 'pm' or a draft directory")

    sub.add_parser("refresh-token", help="refresh the long-lived Instagram token (Phase 5)")
    sub.add_parser("sample", help="render sample carousels from Waypoint lessons (Phase 2)")

    for action in sub.choices.values():
        action.set_defaults(func=_not_implemented)
    sub.choices["render"].set_defaults(func=_render)
    sub.choices["sample"].set_defaults(func=_sample)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    return args.func(args)
