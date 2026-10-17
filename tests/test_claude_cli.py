"""The Claude Code CLI stopgap backend, with a fake subprocess runner (no real claude)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from waypoint.write.claude_cli import ClaudeCodeModels
from waypoint.write.gemini import GeminiClient, GeminiError


class Out(BaseModel):
    answer: int


def runner_returning(payload: dict, seen: list):
    def run(cmd, **kw):
        seen.append((cmd, kw))
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")
    return run


@pytest.fixture
def claude_bin(monkeypatch):
    monkeypatch.setenv("WAYPOINT_CLAUDE_BIN", "claude-test")
    monkeypatch.delenv("WAYPOINT_CLAUDE_MODEL", raising=False)


def test_structured_output_flows_through_gemini_client(cfg, claude_bin):
    seen: list = []
    models = ClaudeCodeModels(cfg.gemini.model, runner=runner_returning(
        {"is_error": False, "structured_output": {"answer": 42}}, seen))
    g = GeminiClient(cfg.gemini, 10, client=models, sleep=lambda s: None)
    assert g.generate_json("q", Out, purpose="t", model=cfg.gemini.model).answer == 42
    cmd, kw = seen[0]
    assert cmd[0] == "claude-test" and kw["input"] == "q"
    assert cmd[cmd.index("--model") + 1] == "sonnet"
    assert json.loads(cmd[cmd.index("--json-schema") + 1])["required"] == ["answer"]
    g.generate_json("q", Out, purpose="t")  # the fast model maps to the cheap alias
    assert seen[1][0][seen[1][0].index("--model") + 1] == "haiku"


def test_cli_error_is_retried_then_reported(cfg, claude_bin):
    seen: list = []
    models = ClaudeCodeModels(cfg.gemini.model, runner=runner_returning(
        {"is_error": True, "subtype": "error_max_turns", "result": "no"}, seen))
    g = GeminiClient(cfg.gemini, 10, client=models, sleep=lambda s: None)
    with pytest.raises(GeminiError, match="error_max_turns"):
        g.generate_json("q", Out, purpose="t")
    assert len(seen) > 1  # treated as transient, like a 503


def test_embeddings_need_gemini(cfg, claude_bin):
    models = ClaudeCodeModels(cfg.gemini.model, runner=runner_returning({}, []))
    g = GeminiClient(cfg.gemini, 10, client=models, sleep=lambda s: None)
    with pytest.raises(GeminiError, match="GEMINI_API_KEY"):
        g.embed(["x"])
