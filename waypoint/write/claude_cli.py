"""Stopgap LLM backend: route text generation through the local Claude Code CLI
(`claude -p`, the user's subscription) while Gemini's free tier is too small to tune the
writer (20 requests/day on the writer model, 2026-10-16). Local use only, never in CI.

`ClaudeCodeModels` has the `.models.generate_content` / `.models.embed_content` surface
that `GeminiClient` expects, so budgets, retries and call logging are unchanged. Gemini
model names are mapped to Claude aliases: `gemini.model` -> WAYPOINT_CLAUDE_MODEL
(default "sonnet"), anything else -> WAYPOINT_CLAUDE_FAST_MODEL (default "haiku").
Embeddings (dedupe check 3) still go to Gemini, whose embedding quota is separate.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

TIMEOUT_S = 600
NPM_EXE = Path("node_modules") / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"


class ClaudeCLIError(RuntimeError):
    """A failed `claude -p` run. `code` is 503 so GeminiClient retries it with backoff."""

    code = 503


def find_claude() -> str:
    """The real claude binary. On Windows the npm `claude.cmd` shim would send the JSON
    schema through cmd.exe, which treats `<` and `>` as redirection, so call claude.exe."""
    if os.environ.get("WAYPOINT_CLAUDE_BIN"):
        return os.environ["WAYPOINT_CLAUDE_BIN"]
    found = shutil.which("claude")
    if not found:
        raise ClaudeCLIError("the claude CLI is not on PATH (set WAYPOINT_CLAUDE_BIN)")
    exe = Path(found).parent / NPM_EXE
    return str(exe) if exe.is_file() else found


class ClaudeCodeModels:
    def __init__(self, main_model: str, embed_client: Any = None, runner=subprocess.run):
        self.main_model = main_model
        self.embed_client = embed_client
        self.runner = runner
        self.bin = find_claude()
        self.models = self  # so an instance can stand in for google.genai.Client

    def alias(self, gemini_model: str) -> str:
        if gemini_model == self.main_model:
            return os.environ.get("WAYPOINT_CLAUDE_MODEL", "sonnet")
        return os.environ.get("WAYPOINT_CLAUDE_FAST_MODEL", "haiku")

    def generate_content(self, *, model: str, contents: str, config: Any) -> SimpleNamespace:
        schema = config.response_schema.model_json_schema()
        system = (config.system_instruction or "You return JSON that matches the schema.")
        system += ("\n\nReply only through the structured output. Use only the text in this "
                   "message; you have no tools.")
        # A clean, empty working dir so no project CLAUDE.md or settings leak into the prompt.
        with tempfile.TemporaryDirectory(prefix="waypoint-claude-") as tmp:
            sys_file = Path(tmp) / "system.md"
            sys_file.write_text(system, encoding="utf-8")
            cmd = [self.bin, "-p", "--output-format", "json", "--json-schema",
                   json.dumps(schema), "--tools", "", "--no-session-persistence",
                   "--model", self.alias(model), "--system-prompt-file", str(sys_file)]
            try:
                proc = self.runner(cmd, input=contents, capture_output=True, text=True,
                                   encoding="utf-8", timeout=TIMEOUT_S, cwd=tmp)
            except subprocess.TimeoutExpired as e:
                raise ClaudeCLIError(f"claude -p timed out after {TIMEOUT_S}s") from e
        try:
            out = json.loads(proc.stdout)
        except ValueError as e:
            raise ClaudeCLIError(f"claude -p exited {proc.returncode} without JSON: "
                                 f"{(proc.stderr or proc.stdout)[:300]}") from e
        data = out.get("structured_output")
        if out.get("is_error") or data is None:
            raise ClaudeCLIError(f"claude -p failed: {out.get('subtype')} "
                                 f"{str(out.get('result'))[:300]}")
        return SimpleNamespace(parsed=None, text=json.dumps(data))

    def embed_content(self, *, model: str, contents: Any, config: Any) -> Any:
        if self.embed_client is None:
            raise ClaudeCLIError("embeddings need GEMINI_API_KEY (Claude has no embeddings)")
        return self.embed_client.models.embed_content(model=model, contents=contents,
                                                      config=config)
