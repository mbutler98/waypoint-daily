"""Render a post.json into 1080x1350 slide JPEGs plus a contact sheet (PLAN.md 4.1, Phase 2).

Playwright Chromium loads templates/slide.html from disk (no network), injects each slide as
JSON, auto-fits the text and screenshots it. Pillow converts the PNG to an sRGB JPEG.
"""

from __future__ import annotations

import io
import json
import logging
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageCms
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from waypoint.config import Config, load_config
from waypoint.models import Post

log = logging.getLogger(__name__)

TEMPLATES = Path(__file__).resolve().parent / "templates"
SLIDE_HTML = TEMPLATES / "slide.html"

WIDTH, HEIGHT = 1080, 1350
JPEG_QUALITY = 90
MAX_JPEG_BYTES = 1_000_000

SHEET_THUMB_W = 216  # contact sheet: 5 thumbnails of 216x270 in a row
SHEET_GAP = 12
SHEET_BG = (5, 12, 28)


class RenderError(RuntimeError):
    """A slide could not be rendered. Never publish a partial carousel."""


class SlideOverflowError(RenderError):
    """Copy does not fit at the minimum sizes; the writer must shorten it."""

    def __init__(self, slide_index: int, detail: str):
        self.slide_index = slide_index
        self.detail = detail
        super().__init__(f"slide {slide_index}: {detail}")


@dataclass
class RenderResult:
    slides: list[Path]
    preview: Path
    sizes: list[list[tuple[str, float]]]  # fitted font sizes per slide, for logs and tests


def _to_jpeg(png: bytes, dest: Path) -> None:
    img = Image.open(io.BytesIO(png)).convert("RGB")
    if img.size != (WIDTH, HEIGHT):
        raise RenderError(f"screenshot is {img.size}, expected {(WIDTH, HEIGHT)}")
    srgb = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    img.save(dest, "JPEG", quality=JPEG_QUALITY, optimize=True, icc_profile=srgb, subsampling=0)
    if dest.stat().st_size > MAX_JPEG_BYTES:
        raise RenderError(f"{dest.name} is {dest.stat().st_size} bytes (max {MAX_JPEG_BYTES})")


def contact_sheet(slides: list[Path], dest: Path) -> None:
    """All slides in a row at thumbnail size, for the approval Issue."""
    th = round(SHEET_THUMB_W * HEIGHT / WIDTH)
    w = len(slides) * SHEET_THUMB_W + (len(slides) + 1) * SHEET_GAP
    sheet = Image.new("RGB", (w, th + 2 * SHEET_GAP), SHEET_BG)
    for i, path in enumerate(slides):
        with Image.open(path) as im:
            thumb = im.convert("RGB").resize((SHEET_THUMB_W, th), Image.Resampling.LANCZOS)
        sheet.paste(thumb, (SHEET_GAP + i * (SHEET_THUMB_W + SHEET_GAP), SHEET_GAP))
    sheet.save(dest, "PNG", optimize=True)


def render_post(
    post: Post,
    out_dir: Path,
    config: Config | None = None,
    *,
    animate: bool = False,
) -> RenderResult:
    """Render every slide of `post` into out_dir/slides/NN.jpg and out_dir/preview.png.

    Raises SlideOverflowError if any slide's copy does not fit, RenderError on other failures.
    Nothing is written unless every slide renders.
    """
    config = config or load_config()
    brand = {
        "name": config.brand.name,
        "handle": config.brand.handle,
        "signoff_kicker": config.brand.signoff_kicker,
        "signoff_lines": config.brand.signoff_lines,
    }
    total = len(post.slides)
    pngs: list[bytes] = []
    sizes: list[list[tuple[str, float]]] = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(
                viewport={"width": WIDTH, "height": HEIGHT}, device_scale_factor=1
            )
            page.on("console", lambda m: log.debug("browser %s: %s", m.type, m.text))
            page.goto(SLIDE_HTML.as_uri())
            page.evaluate(
                "async () => { await document.fonts.load('400 40px Inter');"
                " await document.fonts.load('700 40px Inter'); await document.fonts.ready; }"
            )
            if not page.evaluate("() => document.fonts.check('700 40px Inter')"):
                raise RenderError("Inter did not load from templates/fonts/")
            for i, slide in enumerate(post.slides, start=1):
                data = {
                    "slide": slide.model_dump(mode="json"),
                    "index": i,
                    "total": total,
                    "brand": brand,
                    "animate": animate,
                }
                try:
                    res = page.evaluate("d => WPS.render(d)", data)
                except PlaywrightError as e:
                    msg = str(e)
                    if "OVERFLOW:" in msg:
                        detail = msg.split("OVERFLOW:", 1)[1].strip().splitlines()[0]
                        raise SlideOverflowError(i, detail) from None
                    raise RenderError(f"slide {i}: {msg.splitlines()[0]}") from None
                page.evaluate("() => document.fonts.ready")
                sizes.append([tuple(x) for x in res["sizes"]])
                pngs.append(page.screenshot(type="png", full_page=False))
        finally:
            browser.close()

    slides_dir = out_dir / "slides"
    slides_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, png in enumerate(pngs, start=1):
        dest = slides_dir / f"{i:02d}.jpg"
        _to_jpeg(png, dest)
        paths.append(dest)
    preview = out_dir / "preview.png"
    contact_sheet(paths, preview)
    log.info("rendered %s: %d slides -> %s", post.id, len(paths), out_dir)
    return RenderResult(slides=paths, preview=preview, sizes=sizes)


def render_draft_dir(draft_dir: Path, config: Config | None = None) -> RenderResult:
    """Render drafts/<id>/post.json in place."""
    post = Post.model_validate(json.loads((draft_dir / "post.json").read_text(encoding="utf-8")))
    return render_post(post, draft_dir, config)
