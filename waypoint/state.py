"""Access to state/. posted.json is append-only and its writers come in Phases 5-6.
skipped.json (Phase 4) is append-only too: candidates the writer couldn't turn into a
passing draft, so the next days' drafts don't spend calls on them again."""

from __future__ import annotations

import json
from pathlib import Path

from waypoint.config import REPO_ROOT
from waypoint.models import PostedEntry

STATE_DIR = REPO_ROOT / "state"
POSTED_PATH = STATE_DIR / "posted.json"
EMBEDDINGS_PATH = STATE_DIR / "embeddings.npy"
CACHE_DIR = REPO_ROOT / ".cache"
SKIPPED_PATH = STATE_DIR / "skipped.json"


def load_posted(path: Path = POSTED_PATH) -> list[PostedEntry]:
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8") or "[]")
    return [PostedEntry.model_validate(e) for e in data]


def load_skipped(path: Path = SKIPPED_PATH) -> list[dict]:
    """Entries {url, title, date, post_id, reason}, oldest first."""
    if not path.is_file():
        return []
    return json.loads(path.read_text(encoding="utf-8") or "[]")


def append_skipped(entries: list[dict], path: Path = SKIPPED_PATH) -> None:
    if not entries:
        return
    data = load_skipped(path) + entries
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def load_dotenv(path: Path = REPO_ROOT / ".env") -> None:
    """Set KEY=VALUE lines from a local, gitignored .env into os.environ (never overriding)."""
    import os

    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))
