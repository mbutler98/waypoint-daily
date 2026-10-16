import datetime as dt

import numpy as np
import pytest
from helpers import fake_gemini, make_candidate, make_posted

from waypoint.dedupe import DedupeGate, canonical_url, jaccard, title_norm, url_key

D = dt.date(2026, 10, 10)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://Example.com/a/?utm_source=rss&utm_medium=x#top", "https://example.com/a"),
        ("https://example.com/a?b=2&a=1&fbclid=z", "https://example.com/a?a=1&b=2"),
        ("https://example.com/", "https://example.com/"),
        ("https://example.com:443/x", "https://example.com/x"),
        ("https://example.com/p?id=7&ref=hn", "https://example.com/p?id=7"),
    ],
)
def test_canonical_url(raw, expected):
    assert canonical_url(raw) == expected


def test_url_key_ignores_scheme_www_and_mobile():
    a = url_key("http://www.example.com/story/")
    assert a == url_key("https://example.com/story?utm_campaign=x")
    assert url_key("https://en.m.wikipedia.org/wiki/Heat_pump") == url_key(
        "https://en.wikipedia.org/wiki/Heat_pump")


def test_title_norm_and_jaccard():
    assert title_norm("Your Heat Pump is a Fridge — Running Backwards!") == (
        "heat pump fridge running backwards")
    assert jaccard("a b c", "a b c") == 1.0
    assert jaccard("a b", "c d") == 0.0
    assert jaccard("", "a") == 0.0


# --- the four checks: one seeded duplicate each ----------------------------------------------

SEED = make_posted(
    1, date=D, url="https://www.sciencedaily.com/releases/2026/10/heat.htm",
    title_norm="heat pumps fridge running backwards",
    key_claim="A heat pump moves heat from cold outdoor air into a warm house.",
)


def test_check_1_url(cfg):
    gate = DedupeGate([SEED], cfg.limits)
    dup = make_candidate("Totally different words", url="http://sciencedaily.com/releases/2026/10/heat.htm/")
    assert gate.check_url(dup).startswith("dedupe-1")
    assert gate.check_url(make_candidate("Other")) is None
    res = gate.cheap([dup])
    assert not res.kept and res.rejected[0][1].startswith("dedupe-1")


def test_check_2_title(cfg):
    gate = DedupeGate([SEED], cfg.limits)
    dup = make_candidate("Your heat pump is a fridge running backwards")
    assert gate.check_title(dup).startswith("dedupe-2")
    assert gate.check_title(make_candidate("Octopuses taste with their arms")) is None


def test_check_3_embedding(cfg, tmp_path):
    gemini, models = fake_gemini(cfg)
    emb_path = tmp_path / "embeddings.npy"
    gate = DedupeGate([SEED], cfg.limits, gemini, emb_path)
    dup = make_candidate("Winter warmth from cold air", key_claim=SEED.key_claim)
    fresh = make_candidate("Octopus arms", key_claim="Octopus arms can taste what they touch.")
    reasons = gate.check_embeddings([dup, fresh])
    assert set(reasons) == {0} and reasons[0].startswith("dedupe-3")
    # The posted vectors are cached in embeddings.npy, aligned with posted.json.
    assert np.load(emb_path).shape[0] == 1
    gate.check_embeddings([fresh])
    assert [SEED.key_claim] in models.embedded and models.embedded.count([SEED.key_claim]) == 1


def test_embedding_cache_appends_new_posted_rows(cfg, tmp_path):
    from waypoint.dedupe import EmbeddingCache

    gemini, models = fake_gemini(cfg)
    cache = EmbeddingCache(tmp_path / "e.npy", gemini)
    second = make_posted(2, date=D, slot="pm", key_claim="Bees can count to four.")
    assert cache.vectors([SEED]).shape[0] == 1
    assert cache.vectors([SEED, second]).shape[0] == 2
    assert models.embedded[-1] == ["Bees can count to four."]


def test_check_4_llm(cfg):
    def respond(prompt):
        assert SEED.key_claim in prompt  # the past window is in the prompt
        return {"verdicts": [{"index": 1, "duplicate_of": 1},
                             {"index": 2, "duplicate_of": None}]}

    gemini, _ = fake_gemini(cfg, respond)
    gate = DedupeGate([SEED], cfg.limits, gemini)
    dup = make_candidate("Cold air heats homes",
                         key_claim="Heat pumps pull warmth from winter air.")
    fresh = make_candidate("Octopus arms", key_claim="Octopus arms can taste what they touch.")
    reasons = gate.check_llm([dup, fresh])
    assert set(reasons) == {0} and reasons[0].startswith("dedupe-4")
    res = gate.semantic([dup, fresh])
    assert res.kept == [fresh]


def test_semantic_checks_skip_without_history(cfg):
    gemini, models = fake_gemini(cfg)
    res = DedupeGate([], cfg.limits, gemini).semantic([make_candidate("Anything")])
    assert len(res.kept) == 1 and gemini.total_calls == 0 and not models.prompts


def test_cheap_dedupes_within_batch(cfg):
    a = make_candidate("Bees can count to four", url="https://a.example/bees")
    b = make_candidate("Bees can count to four!", url="https://b.example/bees")
    c = make_candidate("Other story", url="https://www.a.example/bees/")
    res = DedupeGate([], cfg.limits).cheap([a, b, c])
    assert res.kept == [a]
    assert [r for _, r in res.rejected] == [
        "dedupe-2: same title as another candidate", "dedupe-1: same URL as another candidate"]


def test_rejected_history_also_blocks(cfg):
    rejected = make_posted(1, date=D, url="https://example.com/old", status="rejected")
    gate = DedupeGate([rejected], cfg.limits)
    assert gate.check_url(make_candidate("x", url="https://example.com/old"))
