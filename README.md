# Waypoint Daily

*1% smarter, twice a day.*

An automated Instagram account that posts a 5-slide educational carousel twice a day, on topics from heat pumps to honeybees. Each post is a short story: a hook built on tension, three slides (discovery, complication, payoff) and a branded sign-off with a memorable takeaway. It runs entirely on free tiers: GitHub Actions, GitHub Pages and the Gemini API free tier.

> **Status:** Phase 4 (writer + fact-check: full offline drafts). The look was signed off in Phase 2 ([`samples/`](samples/)). Publishing and scheduling are still stubs. See [PLAN.md](PLAN.md) for the build plan.

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

Gathering candidates (Phase 3):

```
uv run python -m waypoint gather --no-llm   # sources + dedupe checks 1-2 only, no Gemini key needed
uv run python -m waypoint gather            # adds Gemini triage, safety label and dedupe checks 3-4
```

It reads the feeds in `waypoint/gather/feeds.yaml`, fetches politely (1 request/s per host, robots.txt), and writes a ranked shortlist per slot to `.cache/gather/<date>/candidates.json`, with each article's text in `candidates/<slug>/source.txt`. The run prints how many Gemini calls it used.

Drafting (Phase 4, needs `GEMINI_API_KEY`):

```
uv run python -m waypoint draft --date tomorrow     # gather -> write -> check -> render both slots
uv run python -m waypoint draft --date 2026-10-17 --force   # redo slots that already have a draft
uv run python -m waypoint write .cache/gather/<date>/candidates/<slug>   # draft one candidate
```

`draft` writes `drafts/<date>-<am|pm>/` with `post.json`, `source.txt` (what the writer saw), `factcheck.json` (every attempt, every claim with its evidence quote), the slides and `preview.png`. Each candidate goes through: write → local checks (copy limits, reading grade ≤ 7, figure layout, every number found in the source) → render (the text must fit) → Gemini fact-check, with each evidence quote verified against the source. Any failure is fed back to the writer for a rewrite (at most 3 writer calls and 2 fact-checks per candidate), then the next candidate is tried. A slot that can't be filled gets `FAILED.md` instead of a post. The prompts live in `waypoint/write/prompts/`.

Each render writes `slides/01.jpg … 05.jpg` (1080×1350, JPEG q90, sRGB) and a `preview.png` contact sheet. If a slide's copy doesn't fit at the minimum type sizes, or a figure would be too small to read, the render fails and writes nothing.

Secrets (`GEMINI_API_KEY`, `IG_USER_ID`, `IG_ACCESS_TOKEN`, `GH_PAT`) live in GitHub Actions secrets. For local runs, put them in a `.env` file. It is gitignored, so never commit it.

## Layout

```
config.yaml        slots, timezone, approval and dry-run flags, topic weights, limits
waypoint/          Python package (python -m waypoint <command>)
  models.py        post.json and posted.json contracts
  config.py        config loader and validation
  gather/          feeds.yaml, polite fetcher, source adapters, triage, pipeline
  dedupe.py        the four duplicate checks (PLAN.md 4.5)
  select.py        faculty rotation, scoring, per-slot fallback chain
  write/           writer, figures, number check, fact-check, prompts/, Gemini client
  draft.py         the per-slot write -> check -> render -> fact-check loop
  render/          Playwright renderer + templates/ (slide.html, tokens.css, Inter, vendored visuals.js)
state/posted.json  append-only record of every published or rejected post
drafts/            one folder per draft: drafts/<date>-<am|pm>/
samples/           design samples (Phase 2)
```

## Credits

The visual identity, chart and diagram renderer (`visuals.js`) and the sample lessons come from **Waypoint** ([mbutler98/Daily_quiz](https://github.com/mbutler98/Daily_quiz)), by the same author. Inter is by Rasmus Andersson (SIL Open Font License).

## Licence

MIT. See [LICENSE](LICENSE).
