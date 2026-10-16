"""Command-line entry point: `python -m waypoint <command>`."""

from __future__ import annotations

import argparse
import sys

NOT_IMPLEMENTED_EXIT = 2


def _not_implemented(args: argparse.Namespace) -> int:
    print(f"waypoint {args.command}: not implemented", file=sys.stderr)
    return NOT_IMPLEMENTED_EXIT


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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)
