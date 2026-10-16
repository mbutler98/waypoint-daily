"""Renderer tests (PLAN.md Phase 2). These launch headless Chromium:
run `uv run playwright install chromium` once (CI uses --with-deps).

To refresh the golden image after an intended design change:
    WAYPOINT_UPDATE_GOLDEN=1 uv run pytest tests/test_render.py
then look at tests/golden/fixture_preview.png before committing it.
"""

import json
import os
from pathlib import Path

import pytest
from PIL import Image, ImageChops, ImageStat

from waypoint.models import Post
from waypoint.render.render import (
    HEIGHT,
    MAX_JPEG_BYTES,
    WIDTH,
    RenderError,
    SlideOverflowError,
    render_post,
)

FIXTURE = Path(__file__).parent / "fixtures" / "post.json"
OVERLONG = Path(__file__).parent / "fixtures" / "post_overlong.json"
GOLDEN = Path(__file__).parent / "golden" / "fixture_preview.png"

# Thumbnails smooth out font antialiasing differences between Windows and Linux Chromium;
# a moved line break or a restyle shifts far more than this.
MAX_MEAN_DIFF = 2.5  # mean absolute difference per channel, 0-255
MAX_CHANGED_FRACTION = 0.01  # share of pixels that differ by more than 40


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    out = tmp_path_factory.mktemp("render")
    return render_post(Post.model_validate(_fixture()), out)


def test_slides_meet_export_spec(rendered):
    assert len(rendered.slides) == 5
    for path in rendered.slides:
        with Image.open(path) as im:
            assert im.format == "JPEG"
            assert im.size == (WIDTH, HEIGHT)
            assert im.mode == "RGB"
            assert im.info.get("icc_profile"), "JPEG must carry an sRGB profile"
        assert path.stat().st_size < MAX_JPEG_BYTES
    assert rendered.preview.is_file()


def test_bottom_band_is_clear(rendered):
    """Nothing but background in the bottom 140 px (the Instagram UI covers it)."""
    for path in rendered.slides:
        with Image.open(path) as im:
            band = im.convert("L").crop((0, HEIGHT - 140, WIDTH, HEIGHT))
            assert ImageStat.Stat(band).extrema[0][1] < 40, f"{path.name} has content in the band"


def test_matches_golden(rendered):
    with Image.open(rendered.preview) as im:
        got = im.convert("RGB")
    if os.environ.get("WAYPOINT_UPDATE_GOLDEN"):
        GOLDEN.parent.mkdir(exist_ok=True)
        got.save(GOLDEN)
        pytest.skip("golden image updated")
    with Image.open(GOLDEN) as im:
        want = im.convert("RGB")
    assert got.size == want.size
    diff = ImageChops.difference(got, want)
    mean = sum(ImageStat.Stat(diff).mean) / 3
    hist = diff.convert("L").histogram()
    changed = sum(hist[41:]) / (got.width * got.height)
    assert mean <= MAX_MEAN_DIFF and changed <= MAX_CHANGED_FRACTION, (
        f"slides drifted from the golden image (mean diff {mean:.2f}, "
        f"{changed:.2%} of pixels changed); see the module docstring to update it"
    )


def test_text_fits_at_largest_sizes(rendered):
    # The fixture's copy is short, so nothing should need to shrink.
    assert rendered.sizes[0] == [("hook", 112.0)]
    assert rendered.sizes[1] == [("ihead", 72.0), ("body", 44.0)]
    assert rendered.sizes[4] == [("takeaway", 96.0), ("body", 44.0)]


def test_swipe_hint_only_on_first_slide(rendered):
    """The hook shows 'Swipe ->' beside the counter; later slides only show the counter."""
    def footer_ink(path):
        with Image.open(path) as im:
            band = im.convert("L").crop((560, HEIGHT - 190, 860, HEIGHT - 140))
            return sum(1 for v in band.histogram()[100:] if v)
    assert footer_ink(rendered.slides[0]) > 0
    assert footer_ink(rendered.slides[1]) == 0


def test_unbreakable_word_fails_loudly(tmp_path):
    data = _fixture()
    data["slides"][0]["headline"] = "Pneumonoultramicroscopicsilicovolcanoconiosis is a real word."
    with pytest.raises(SlideOverflowError) as e:
        render_post(Post.model_validate(data), tmp_path)
    assert e.value.slide_index == 1
    assert "too wide" in e.value.detail
    assert not (tmp_path / "slides").exists(), "no partial carousel on failure"


def test_overlong_copy_with_figure_fails_loudly(tmp_path):
    """Legal copy (8-word headline, 30-word body) leaves the tall diagram too small to read."""
    data = json.loads(OVERLONG.read_text(encoding="utf-8"))
    with pytest.raises(SlideOverflowError) as e:
        render_post(Post.model_validate(data), tmp_path)
    assert e.value.slide_index == 3
    assert "figure" in e.value.detail


def test_hook_over_three_lines_fails(tmp_path):
    data = _fixture()
    data["slides"][0]["headline"] = (
        "Every winter your heat pump quietly steals warmth from freezing outdoor air."
    )
    with pytest.raises(SlideOverflowError, match="lines"):
        render_post(Post.model_validate(data), tmp_path)


def test_bad_figure_spec_is_an_error(tmp_path):
    data = _fixture()
    data["slides"][2]["figure"] = {"type": "chart", "kind": "column", "data": "not a list"}
    with pytest.raises(RenderError, match="figure"):
        render_post(Post.model_validate(data), tmp_path)
