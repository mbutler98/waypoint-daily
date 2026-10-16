import pytest
from helpers import ApiError, fake_gemini
from pydantic import BaseModel

from waypoint.write.gemini import GeminiBudgetExceeded, GeminiClient, GeminiError, RateLimiter


class Out(BaseModel):
    answer: int


def test_rate_limiter_sliding_window():
    t = [0.0]
    sleeps = []

    def sleep(s):
        sleeps.append(s)
        t[0] += s

    rl = RateLimiter(2, clock=lambda: t[0], sleep=sleep)
    rl.wait()
    rl.wait()
    assert sleeps == []
    rl.wait()  # third call within the minute must wait for the first to age out
    assert len(sleeps) == 1 and 59 < sleeps[0] <= 61


def test_generate_json_parses_and_counts(cfg):
    g, models = fake_gemini(cfg, lambda p: {"answer": 42})
    assert g.generate_json("q", Out, purpose="test").answer == 42
    assert g.total_calls == 1 and g.calls["test"] == 1


def test_retries_on_rate_limit_then_succeeds(cfg):
    g, _ = fake_gemini(cfg, lambda p: {"answer": 1}, errors=[ApiError(429), ApiError(503)])
    assert g.generate_json("q", Out, purpose="t").answer == 1
    assert g.total_calls == 3  # every attempt counts against the budget


def test_client_error_is_not_retried(cfg):
    g, _ = fake_gemini(cfg, errors=[ApiError(400)])
    with pytest.raises(GeminiError):
        g.generate_json("q", Out, purpose="t")
    assert g.total_calls == 1


def test_budget_is_enforced(cfg):
    g, _ = fake_gemini(cfg, lambda p: {"answer": 1}, max_calls=1)
    g.generate_json("q", Out, purpose="t")
    with pytest.raises(GeminiBudgetExceeded):
        g.generate_json("q", Out, purpose="t")


def test_embed_returns_unit_rows(cfg):
    g, _ = fake_gemini(cfg)
    v = g.embed(["heat pumps move heat", "bees count"])
    assert v.shape[0] == 2
    assert abs(float((v[0] ** 2).sum()) - 1) < 1e-5


def test_missing_key_is_a_clear_error(cfg, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(GeminiError, match="GEMINI_API_KEY"):
        GeminiClient(cfg.gemini, 10)


def test_usage_is_logged(cfg, caplog):
    g, _ = fake_gemini(cfg, lambda p: {"answer": 1})
    g.generate_json("q", Out, purpose="triage")
    with caplog.at_level("INFO"):
        g.log_usage()
    assert "Gemini calls this run: 1" in caplog.text and "triage=1" in caplog.text
