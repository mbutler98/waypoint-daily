# Waypoint Daily

*1% smarter, twice a day.*

An automated Instagram account that posts a 5-slide educational carousel twice a day, on topics from heat pumps to honeybees. Each post is a short story: a hook built on tension, three slides (discovery, complication, payoff) and a branded sign-off with a memorable takeaway. It runs entirely on free tiers: GitHub Actions, GitHub Pages and the Gemini API free tier.

> **Status:** Phase 2 (design system and slide renderer), awaiting sign-off on the samples in [`samples/`](samples/). Other CLI commands are still stubs. See [PLAN.md](PLAN.md) for the build plan.

## How it works

1. **Draft** (every evening, Toronto time): gather fresh articles and evergreen topics → pick two that aren't repeats → write the slides with Gemini → fact-check every claim against the source → render the slides as 1080×1350 JPEGs.
2. **Approve:** each draft opens a GitHub Issue with the slides inline. Comment `/reject` to skip it or `/approve` to post right away. If you do nothing, it posts at its slot time.
3. **Publish** (08:00 and 18:00): post the carousel through the Instagram Graph API and record it in `state/posted.json`.

`dry_run: true` in `config.yaml` (the default) turns every network write into a logged no-op. Setting `require_approval: false` posts drafts without review.

## Local setup

You need [uv](https://docs.astral.sh/uv/). uv installs Python 3.12 for you.

**Windows (PowerShell)**

```powershell
winget install --id=astral-sh.uv -e
git clone https://github.com/mbutler98/waypoint-daily.git
cd waypoint-daily
uv sync
uv run python -m waypoint --help
uv run pytest
```

**Linux / macOS**

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
git clone https://github.com/mbutler98/waypoint-daily.git
cd waypoint-daily
uv sync
uv run python -m waypoint --help
uv run pytest
```

Rendering also needs a headless browser, installed once: `uv run playwright install chromium` (on Linux: `--with-deps chromium`). Then:

```
uv run python -m waypoint sample                    # re-render samples/*/post.json in place
uv run python -m waypoint render drafts/<id>        # render one draft's post.json
```

Each render writes `slides/01.jpg … 05.jpg` (1080×1350, JPEG q90, sRGB) and a `preview.png` contact sheet. If a slide's copy doesn't fit at the minimum type sizes, or a figure would be too small to read, the render fails and writes nothing.

Secrets (`GEMINI_API_KEY`, `IG_USER_ID`, `IG_ACCESS_TOKEN`, `GH_PAT`) live in GitHub Actions secrets. For local runs, put them in a `.env` file. It is gitignored, so never commit it.

## Layout

```
config.yaml        slots, timezone, approval and dry-run flags, topic weights, limits
waypoint/          Python package (python -m waypoint <command>)
  models.py        post.json and posted.json contracts
  config.py        config loader and validation
  render/          Playwright renderer + templates/ (slide.html, tokens.css, Inter, vendored visuals.js)
state/posted.json  append-only record of every published or rejected post
drafts/            one folder per draft: drafts/<date>-<am|pm>/
samples/           design samples (Phase 2)
```

## Credits

The visual identity, chart and diagram renderer (`visuals.js`) and the sample lessons come from **Waypoint** ([mbutler98/Daily_quiz](https://github.com/mbutler98/Daily_quiz)), by the same author. Inter is by Rasmus Andersson (SIL Open Font License).

## Licence

MIT. See [LICENSE](LICENSE).
