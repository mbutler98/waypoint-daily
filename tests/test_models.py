import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from waypoint.models import Post, PostedEntry

FIXTURE = Path(__file__).parent / "fixtures" / "post.json"


@pytest.fixture
def post_data() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_round_trip(post_data):
    post = Post.model_validate(post_data)
    assert post.model_dump(mode="json") == post_data
    assert Post.model_validate_json(post.model_dump_json()) == post


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("slides", 0, "headline"), " ".join(["word"] * 13)),
        (("slides", 1, "headline"), " ".join(["word"] * 9)),
        (("slides", 1, "body"), " ".join(["word"] * 31)),
        (("caption",), "x" * 2201),
        (("hashtags",), [f"#tag{i}" for i in range(6)]),
        (("slides", 4, "headline"), " ".join(["word"] * 9)),
        (("slides", 4, "body"), " ".join(["word"] * 15)),
    ],
    ids=[
        "hook>12", "insight-headline>8", "insight-body>30", "caption>2200", "hashtags>5",
        "takeaway>8", "takeaway-support>14",
    ],
)
def test_over_length_copy_fails(post_data, path, value):
    data = copy.deepcopy(post_data)
    target = data
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValidationError):
        Post.model_validate(data)


def test_limits_are_inclusive(post_data):
    data = copy.deepcopy(post_data)
    data["slides"][0]["headline"] = " ".join(["word"] * 12)
    data["slides"][1]["headline"] = " ".join(["word"] * 8)
    data["slides"][1]["body"] = " ".join(["word"] * 30)
    data["caption"] = "x" * 2200
    Post.model_validate(data)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d["slides"].reverse(),
        lambda d: d["slides"].pop(),
        lambda d: d["alt_text"].pop(),
        lambda d: d.update(id="2026-10-17-pm"),
        lambda d: d.update(faculty="Astrology"),
        lambda d: d["source"].update(url="https://example.com/a?utm_source=x"),
        lambda d: d["source"].update(url="https://example.com/a#top"),
        lambda d: d["hashtags"].append("no-hash"),
        lambda d: d["slides"][0].update(headline="Hot take 🔥"),
        lambda d: d["slides"][2].update(figure={"type": "manim"}),
        lambda d: d.update(extra_field=1),
    ],
    ids=[
        "slide-order", "four-slides", "alt-text-count", "id-slot-mismatch", "unknown-faculty",
        "utm-url", "fragment-url", "bad-hashtag", "emoji-in-headline", "manim-no-template",
        "unknown-field",
    ],
)
def test_structural_rules(post_data, mutate):
    data = copy.deepcopy(post_data)
    mutate(data)
    with pytest.raises(ValidationError):
        Post.model_validate(data)


def test_too_many_figures(post_data):
    data = copy.deepcopy(post_data)
    fig = {"type": "diagram", "nodes": []}
    for i in (1, 2, 3):
        data["slides"][i]["figure"] = fig
    with pytest.raises(ValidationError, match="figures"):
        Post.model_validate(data)


def test_manim_figure_accepted(post_data):
    data = copy.deepcopy(post_data)
    data["slides"][1]["figure"] = {"type": "manim", "template": "cycle", "params": {"n": 4}}
    Post.model_validate(data)


POSTED = {
    "id": "2026-10-17-am",
    "date": "2026-10-17",
    "faculty": "Trades & Home",
    "topic": "Heat pumps",
    "source_url": "https://www.energy.gov/energysaver/heat-pump-systems",
    "title_norm": "heat pumps fridge backwards",
    "key_claim": "A heat pump moves heat instead of making it.",
    "ig_media_id": "17900000000000000",
    "ig_permalink": "https://www.instagram.com/p/abc123/",
}


def test_posted_entry_round_trip():
    entry = PostedEntry.model_validate(POSTED)
    assert entry.status == "published"
    assert entry.model_dump(mode="json") == {**POSTED, "status": "published"}


def test_posted_entry_rejected_has_no_media():
    entry = PostedEntry.model_validate(
        {**POSTED, "ig_media_id": None, "ig_permalink": None, "status": "rejected"}
    )
    assert entry.status == "rejected"


def test_posted_entry_id_must_match_date():
    with pytest.raises(ValidationError):
        PostedEntry.model_validate({**POSTED, "date": "2026-10-18"})


def test_state_file_is_valid():
    state = Path(__file__).parent.parent / "state" / "posted.json"
    for row in json.loads(state.read_text(encoding="utf-8")):
        PostedEntry.model_validate(row)
