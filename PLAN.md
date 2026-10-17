# Waypoint Daily — Build Plan

An automated Instagram account that posts a 5-slide educational carousel twice a day on a wide range of topics (HVAC to animals). Each post is a short story with a surprising payoff: a hook built on tension, three slides that each do a different job, and a branded sign-off with a memorable takeaway that earns the follow. Free to run. Visual identity and rendering engine derived from the Waypoint repo.

This file is the hand-off document for a sequence of build agents. **Each agent: read sections 1–4 in full, then do only your phase in section 5.** Do not start a later phase. When you finish, tick your phase's acceptance checklist in this file and add a short entry to section 7 (Build log).

---

## 1. Decisions already made (do not re-open)

| Decision | Choice |
|---|---|
| Brand | **Waypoint** (working handle `@waypoint.daily`, confirm with user before creating). Tagline: "1% smarter, twice a day." Do **not** use "Smarter Every Day" anywhere (it is an existing YouTube brand). |
| Repo | **Public** GitHub repo `mbutler98/waypoint-daily`. GitHub Actions + GitHub Pages, both free on public repos. |
| LLM | **Gemini API, free tier** (Google AI Studio key). No paid APIs anywhere. |
| Approval | **Approval step on** at launch. Drafts are previewed in a GitHub Issue; the user can reject before the publish time. A config flag turns approval off later. |
| Posting | **Instagram Graph API, "Instagram API with Instagram Login"** (graph.instagram.com). Needs an Instagram Professional (Creator or Business) account. No Facebook Page needed. |
| Style | Apple-style design, simple language. Keep Waypoint's palette, logo and chart rules. Drop Waypoint's sci-fi HUD styling (Michroma caps, cyan glow, hex texture). |
| Visual engines | (1) HTML/CSS slide templates rendered with Playwright, reusing Waypoint's `js/visuals.js` for charts and diagrams. (2) Later: a **curated** set of Manim scene templates. The LLM picks a template and fills in parameters; it never writes Manim code. |
| Language | Python 3.12 for the pipeline. JS only inside the slide templates. |

## 2. Reference: the Waypoint repo

- GitHub: https://github.com/mbutler98/Daily_quiz (same owner, so reusing its code is fine; keep a credit line in the README)
- Live: https://mbutler98.github.io/Daily_quiz/
- Reuse:
  - `css/style.css` lines 1–110: colour tokens (use the **dark** set only)
  - `js/visuals.js`: renderers for `chart` (column/bar/line/waterfall), `diagram` (boxes, pipes, flows), `scene` (animated curves) and `map`. The spec format is documented at the top of the file and in the README.
  - `content/*.js`: 173 lessons in `WP.addTopic({...})` format, with fields `hook`, `bluf`, `story`, `points`, `example`, `pitfall`, `terms`. Phase 2 uses these as sample input.
  - `content/diagrams.js`: ready-made diagram specs (heat pump cycle, furnace, jet engine, RAG, and more). Good for testing.
  - `assets/icon.svg`: the diamond waypoint logo.
- Vendor (copy) `visuals.js` into this repo at `render/templates/vendor/visuals.js` with a header comment that gives its origin and commit SHA. Do not load it from the network at render time.

## 3. System architecture

```
                         GitHub Actions (public repo, free)
 ┌────────────────────────────────────────────────────────────────────────────┐
 │ draft.yml  (daily, ~20:00 Toronto)  → produces TOMORROW's two posts        │
 │   gather → select → write (Gemini) → fact-check (Gemini) → render          │
 │   → commit drafts/<date>-<slot>/ → deploy media to Pages                   │
 │   → open one approval Issue per draft (slides inline)                      │
 │                                                                            │
 │ publish.yml (08:00 and 18:00 Toronto)                                      │
 │   find draft for this slot → check Issue not rejected → IG carousel        │
 │   publish → append to state/posted.json → comment permalink, close Issue   │
 │                                                                            │
 │ refresh-token.yml (weekly) → refresh IG long-lived token → update secret   │
 └────────────────────────────────────────────────────────────────────────────┘
        │ media (JPEG/MP4) served from GitHub Pages (gh-pages branch)
        ▼
  Instagram Graph API (fetches image_url / video_url at container creation)
```

Why drafting runs once a day: the user approves both of tomorrow's posts in one sitting, instead of checking two 2-hour windows a day. Slot times are set in `config.yaml`.

**Cron notes:** GitHub cron is in UTC and can run 5–30+ minutes late. Toronto is UTC−4 (EDT) or UTC−5 (EST). Schedule in UTC with the EST/EDT offset documented in a comment, and make publish idempotent so a late or doubled run never double-posts.

### 3.1 Repo layout (Phase 1 creates this)

```
waypoint-daily/
  PLAN.md                     ← this file (moved in from the Claude Instagram folder)
  CLAUDE.md                   ← short: points agents to PLAN.md and the conventions
  README.md
  config.yaml                 ← slots, timezone, approval flag, topic weights, limits
  pyproject.toml              ← managed with uv
  .github/workflows/
    ci.yml  draft.yml  publish.yml  refresh-token.yml
  waypoint/                   ← Python package
    __init__.py
    cli.py                    ← `python -m waypoint <command>` entry point
    config.py                 ← loads + validates config.yaml (pydantic)
    models.py                 ← pydantic models for Candidate, Post, Slide, Figure, PostedEntry
    state.py                  ← load posted.json, local .env
    gather/                   ← sources.py, feeds.yaml, fetch.py (article text), triage.py, pipeline.py
    select.py                 ← topic rotation + dedupe gate
    dedupe.py
    write/                    ← prompts/, writer.py, factcheck.py, gemini.py (client + retry + rate limit)
    render/
      render.py               ← Playwright: post.json → slides/*.jpg
      templates/              ← slide.html, slide.css, tokens.css, fonts/, vendor/visuals.js, logo.svg
      manim/                  ← Phase 7: scene templates
    publish/                  ← instagram.py, pages.py, approval.py (GitHub Issues)
  state/
    posted.json               ← permanent record of every published post (source of truth for dedupe)
    embeddings.npy            ← optional cache aligned with posted.json
  drafts/<YYYY-MM-DD>-<am|pm>/
    post.json  source.txt  factcheck.json  slides/01.jpg … 05.jpg  preview.png
  samples/                    ← Phase 2 output
  tests/
```

### 3.2 Core data contract: `post.json`

Every stage reads or writes this. Phase 1 defines it as pydantic models; later phases must not change it without updating this section.

```jsonc
{
  "id": "2026-10-17-am",
  "slot": "am",
  "faculty": "Trades & Home",           // see 4.4
  "topic": "Heat pumps",
  "source": {
    "url": "https://…",                  // canonical (no utm_*, no fragments)
    "title": "…",
    "publisher": "ScienceDaily",
    "published": "2026-10-15",
    "kind": "news | paper | evergreen"
  },
  "key_claim": "One-sentence core idea, used for dedupe.",
  "slides": [
    { "type": "hook",    "kicker": "HVAC", "headline": "Your heat pump is a fridge running backwards." },
    { "type": "insight", "n": 1, "headline": "≤ 8 words", "body": "≤ 30 words", "figure": null },
    { "type": "insight", "n": 2, "headline": "…", "body": "…", "figure": { /* visuals.js spec or manim template call */ } },
    { "type": "insight", "n": 3, "headline": "…", "body": "…", "figure": null },
    { "type": "cta",     "headline": "Civilisation started with a trade-off.",          // the takeaway, ≤ 8 words
                         "body": "More food. Bigger societies. A new set of problems." } // support line, ≤ 14 words
  ],
  "caption": "…",                        // ≤ 2,200 chars; restates the 3 points + source credit
  "hashtags": ["#science", "#hvac"],     // max 5
  "alt_text": ["…", "…"],                // one per slide
  "status": "draft | rejected | approved | published | failed",
  "approval_issue": 123,
  "ig_media_id": null,
  "ig_permalink": null
}
```

The `cta` slide is the **sign-off** (renamed in Phase 2 in spirit; the type name stays `cta`). Its `headline` and `body` are written per post. The fixed brand lines around them (`<name> · Today's takeaway`, "Stay curious.", "Two ideas worth knowing. Every day.", the handle) come from `config.yaml` → `brand`.

`figure` is either a Waypoint `visuals.js` spec (`{"type":"chart",...}` or `{"type":"diagram",...}`) or `{"type":"manim","template":"cycle","params":{...}}` (Phase 7).

The Phase 4 writer never writes these specs directly. It fills a flat `FigureDraft` (`column`, `bar` or `line` chart, `timeline`, `steps`), and `waypoint/write/figures.py` turns that into the `visuals.js` spec stored here (it lays out diagram coordinates, so the LLM never writes them).

### 3.2.1 Gather output: `Candidate` (Phase 3)

`waypoint gather` writes `.cache/gather/<date>/candidates.json` (`slots: {am: [...], pm: [...]}` ranked best first, every candidate, every rejection with its reason, call counts) and `candidates/<slug>/{candidate.json, source.txt}`. A candidate is:

```jsonc
{ "url": "https://…", "title": "…", "publisher": "ScienceDaily", "published": "2026-10-16",
  "kind": "news | paper | evergreen", "feed": "sciencedaily-animals",
  "faculty_hints": ["Animals & Nature"], "summary": "feed blurb", "word_count": 912,
  "faculty": "Animals & Nature", "topic": "Bee counting", "key_claim": "One sentence.",
  "surprise": 4, "safe": true, "safety_reason": null, "score": 0.81 }
```

`key_claim` here is triage's best guess; Phase 4's writer produces the post's real `key_claim`. `Candidate.to_source()` gives the `source` block of `post.json`.

### 3.3 `state/posted.json` entry

```json
{ "id": "2026-10-17-am", "date": "2026-10-17", "faculty": "Trades & Home", "topic": "Heat pumps",
  "source_url": "https://…", "title_norm": "heat pumps fridge backwards", "key_claim": "…",
  "ig_media_id": "…", "ig_permalink": "…", "status": "published" }
```

Append-only. Never rewrite history. Rejected drafts are also recorded, with `"status":"rejected"`, so they are not proposed again. `status` is one of `published | rejected | failed` (default `published`); `ig_media_id`/`ig_permalink` are `null` when nothing was posted.

---

## 4. Brand, content and quality rules (all phases)

### 4.1 Visual identity: "Waypoint, made by Apple"

- **Canvas:** 1080 × 1350 px (4:5). Export JPEG at quality 90, sRGB, under 1 MB. Safe margin 96 px on all sides. Nothing important within 140 px of the bottom (the Instagram UI covers it).
- **Background:** Waypoint dark gradient `linear-gradient(170deg, #0b1c3a 0%, #050c1c 100%)` with one soft radial light at the top right, `rgba(64,140,255,0.14)`. No hex texture, no glow outlines, no star dust.
- **Colour tokens** (from Waypoint dark):
  - text `#eef4fb`, secondary `#c9d6e6`, muted `#8ea3bd`, hairline `rgba(160,200,255,0.14)`
  - **one accent, `#f4d64a`** (commendation yellow). Use it for the kicker, the slide counter dot and the single highlighted data mark. Never use it for body text.
  - charts: highlight `#5A82F5`, context `#62728A`, ink `#dfe7f1` (Waypoint's colour-blind-safe pair)
- **Type:** Inter, **bundled locally** under `templates/fonts/` (SIL OFL licence, include it). No network fonts at render time.
  - Hook headline: 96–112 px, weight 700, letter-spacing −0.03em, line-height 1.05, max 3 lines
  - Insight headline: 64–72 px, weight 700, letter-spacing −0.02em
  - Body: 40–44 px, weight 400, line-height 1.35, colour secondary
  - Kicker (topic): 28 px, weight 600, uppercase, letter-spacing +0.08em, accent colour
  - Counter (e.g. `2 / 5`): 26 px, weight 500, muted
  - Build hierarchy from size and weight, not colour.
- **Layout:** left-aligned text, generous negative space, one idea per slide. Figures sit in a card with rounded corners (radius 36 px), fill `rgba(255,255,255,0.04)` and a 1 px hairline border. Depth comes from subtle layering, never glow.
- **Brand marks:** diamond logo (from `assets/icon.svg`, stroke `#f4d64a`) plus "Waypoint" in Inter 600 at the bottom-left of every slide. Counter at the bottom-right. Slide 1 adds a muted "Swipe →" beside the counter. Insight slides carry no "01/02/03" numbering (it read like a textbook).
- **Sign-off slide** (Phase 2, from user feedback): an editorial, left-aligned closing card that is the same on every topic. Accent kicker `WAYPOINT · TODAY'S TAKEAWAY`, the post's takeaway (96–80 px, 700), one support line (body style), a hairline, then "Stay curious." / "Two ideas worth knowing. Every day." / `@waypoint.daily`. It ends the story instead of asking for a follow; "Save this" lives in the caption only.
- **Consistency:** every post uses the same 5-slide skeleton. Text auto-fits by stepping down through the allowed sizes; if it still overflows at the minimum, rendering **fails** (the writer must shorten). Never shrink below the minimums.
- **Charts and diagrams:** follow Waypoint's rules. The title states the takeaway; grey context with one highlighted mark; direct labels; no gridlines; a source line. Re-skin through CSS variables only; do not fork the renderer logic.
- **Figures must stay legible** (added in Phase 2): on a figure slide, rendering fails if the figure card is under 360 px tall or any figure text renders under 18 px after scaling. Tall figures (e.g. the portrait refrigeration-cycle diagram) need short copy: about a one-line headline and a body of ≤ 20 words. The Phase 4 writer prompt must say so.
- **Diagram colours** (Phase 2): `visuals.js` colours diagrams with a Manim-style rainbow. CSS folds it into the brand palette: cold/blue/teal/green → chart blue `#5A82F5`, hot/warm/gold/yellow → accent `#f4d64a`, everything else → context grey. Line charts hide the legend because every series is labelled at its end.

### 4.2 Voice and copy rules

- Plain English at a reading age of about 12 (Flesch-Kincaid grade ≤ 7, checked with `textstat` in the writer's validation).
- Hook ≤ 12 words. Insight headline ≤ 8 words. Insight body ≤ 30 words. No jargon unless it's explained in the same slide.
- **Tell a story, not a textbook.** Each swipe has a different job: hook (tension) → discovery → complication → payoff → sign-off (takeaway). The payoff should resolve the tension the hook set up.
- **Lead with tension, not a category label.** The kicker gives context; the headline does the work. Hook patterns: a contradiction ("Farming changed everything. Not always for the better."); surprising consequence; myth vs reality; big-number contrast; a "why does…?" question with an answer worth swiping for. The hook must be **true and supported by the source**. No "you won't believe", no outrage bait.
- **One simple visual** on an information-heavy slide (chart, diagram or a small timeline) so the five slides don't look alike. Optional, never decorative.
- **Takeaway** (sign-off headline): ≤ 8 words, memorable, true to the source, works as a standalone line. Support line ≤ 14 words. No "follow"/"save" asks on any slide.
- At most 1 emoji per post, and never in headlines.
- Sign-off brand lines are fixed in config (`brand.signoff_kicker`, `brand.signoff_lines`); only the takeaway changes per post. The tagline "1% smarter, twice a day." stays the account tagline (bio, README) but is no longer on the slides.
- Caption: line 1 repeats the hook, then the story in short paragraphs (discovery, "The catch?", "The payoff?"), then `Source: <publisher> — <title>`, then the `brand.caption_cta` lines ("Save this for your next curiosity rabbit hole." / "Follow @waypoint.daily for two ideas worth knowing, every day."), then ≤ 5 hashtags, one of them `#WaypointDaily` (Instagram now caps hashtags; check the current limit).

### 4.3 Accuracy and rights

- Every insight must be supported by the fetched source text. The fact-check (Phase 4) verifies this claim by claim.
- **Every number in a figure's data must appear in the source text.** This is a programmatic check, not an LLM one.
- Never reuse images from sources. Only our own rendered text, charts, diagrams and Manim scenes.
- Never quote more than a short phrase from a source. Always credit the source in the caption.
- Skip medical, legal and financial *advice*. Explaining how something works is fine.
- Skip politics, tragedies, graphic content, and anything about named private individuals.
- If anything fails, **post nothing** for that slot and record the reason. Never publish a partial carousel.

### 4.4 Topic areas ("faculties") and rotation

Start with Waypoint's faculties plus general-interest ones:

`Animals & Nature`, `Space`, `Human Body & Health`, `Mind & Behaviour`, `Everyday Physics & Chemistry`, `Trades & Home` (HVAC, carpentry), `Engineering & Tech`, `AI & Computing`, `Money & Markets`, `History`, `Geography & Earth`, `Food & Cooking`, `Sport & Strength`, `Flight`.

Rules (in `config.yaml`, with weights):
- The same faculty may not appear within the last 4 posts.
- The same topic may not appear within the last 30 days.
- AM slot leans "news and research"; PM slot leans "evergreen and how things work" (configurable).

### 4.5 Dedupe gate (a candidate must pass all checks)

1. The canonical URL is not in `posted.json`.
2. The normalised title's Jaccard similarity with every past title is < 0.6.
3. The `key_claim` embedding's cosine similarity with every past `key_claim` is < 0.85 (Gemini embeddings model, free tier; cache vectors in `state/embeddings.npy`).
4. An LLM check against the last 60 `key_claim`s: "Is this the same core idea as any of these?" If yes, reject.

### 4.6 Secrets and config

GitHub Actions secrets. Never commit them; never print them in logs.

| Secret | Purpose |
|---|---|
| `GEMINI_API_KEY` | Google AI Studio key (free tier) |
| `IG_USER_ID` | Instagram professional account ID |
| `IG_ACCESS_TOKEN` | long-lived Instagram User access token (60-day life) |
| `GH_PAT` | fine-grained PAT, this repo only, **Secrets: read/write**, used to save the refreshed IG token. The default `GITHUB_TOKEN` cannot write secrets. |

`config.yaml` keys (minimum): `timezone: America/Toronto`, `slots: {am: "08:00", pm: "18:00"}`, `draft_time: "20:00"`, `require_approval: true`, `dry_run: true`, `faculties: {...weights}`, `limits: {...}`, `gemini: {model, fast_model, embedding_model, requests_per_minute}`, `gather: {user_agent, per_host_delay_s, timeout_s, cache_ttl_hours, max_items_per_feed, max_triage, triage_batch_size, fallback_items}`, `brand: {name, handle, tagline, signoff_kicker, signoff_lines, caption_cta}`, `pages_base_url`, `media_retention_days: 14`.

**Gemini free-tier caveats:** model names and rate limits change. Verify the current free model (a "Flash" model) and its limits in Google's docs at implementation time, and keep the model name in config. Use the `google-genai` SDK with structured JSON output (`response_schema`). Free-tier prompts may be used by Google to improve its products; that is acceptable because all inputs are public articles. Never send secrets or personal data.

---

## 5. Phases (one agent each)

Each phase lists: **Goal → User actions needed → Tasks → Acceptance criteria → Out of scope.**

### Phase 1: Scaffold the repo

**Goal:** an empty-but-runnable project skeleton with the data contracts, config and CI in place.

**User actions:** none required. If `gh` is not installed (it was not on 2026-10-16), either install it (`winget install GitHub.cli`, then `gh auth login`) or ask the user to create the empty public repo `waypoint-daily` on github.com.

**Tasks:**
1. Create the folder layout in 3.1 (in this `Claude Instagram` folder or a new `waypoint-daily` folder; ask the user which). `git init`, `.gitignore` (Python, `.venv`, `.env`, `drafts/**/tmp`), MIT licence.
2. `pyproject.toml` managed with `uv`. Dependencies: `pydantic`, `pyyaml`, `httpx`, `feedparser`, `trafilatura`, `google-genai`, `playwright`, `pillow`, `textstat`, `numpy`, `python-dateutil`, `tzdata`. Dev: `pytest`, `ruff`. `manim` stays out until Phase 7.
3. `waypoint/models.py`: pydantic models matching 3.2 and 3.3 exactly, with validators for the copy-length limits in 4.2.
4. `waypoint/config.py` and `config.yaml` with all keys in 4.6, validated on load. `dry_run: true` by default.
5. `waypoint/cli.py`: subcommands as stubs (`gather`, `draft`, `render <draft_dir>`, `publish <slot>`, `refresh-token`, `sample`), each printing "not implemented" and exiting with code 2.
6. Copy this PLAN.md into the repo root. Write `CLAUDE.md` (≤ 30 lines): purpose, "read PLAN.md", how to run, conventions (ruff, pytest, no secrets in logs, `dry_run` respected everywhere).
7. `README.md`: what it is, how it works (short), credit to the Waypoint repo, local setup on Windows and Linux.
8. `.github/workflows/ci.yml`: on push and PR: install uv, `ruff check`, `pytest`.
9. `tests/test_models.py`: round-trip a sample `post.json` fixture; check that over-length copy fails validation.
10. Create `state/posted.json` as `[]`.
11. Push to the public GitHub repo. Enable Pages (source: `gh-pages` branch; the branch is created in Phase 6).

**Acceptance:**
- [x] `uv run python -m waypoint --help` lists all subcommands
- [x] `uv run pytest` passes; CI is green on GitHub
- [x] The repo is public; no secrets in the repo
- [x] Models match 3.2/3.3 field for field

**Out of scope:** any real gathering, rendering or posting.

---

### Phase 2: Design system + render samples (user approval gate)

**Goal:** a pixel-perfect slide renderer and **3 sample carousels** for the user to approve the look. **No later phase starts until the user approves the samples.**

**User actions:** review `samples/` and give feedback. Iterate until the user signs off.

**Tasks:**
1. `render/templates/tokens.css`: the tokens from 4.1 as CSS variables, plus the variables `visuals.js` expects (`--viz-hi`, `--viz-ctx`, `--viz-ink`, `--text`, `--muted`, `--line`, `--font`, etc.), so the vendored renderer picks up the new style without code changes.
2. Bundle Inter (variable font, `.woff2`, plus the OFL licence) in `templates/fonts/`.
3. Vendor `visuals.js` from Daily_quiz (see section 2). Add the minimal CSS its charts and diagrams need, taken from Waypoint's `style.css` (`.v-*` classes) and restyled to 4.1. Disable animation for static renders (render the final state). Keep a flag so Phase 7 or a video export can animate later.
4. `templates/slide.html` + `slide.css`: one template with variants `hook`, `insight` (with and without a figure), `cta`. Data is injected as JSON via `page.evaluate`. Include the auto-fit routine from 4.1, which **throws** on overflow at the minimum size.
5. Convert `logo.svg` from Waypoint's `assets/icon.svg` (yellow stroke on a transparent background).
6. `waypoint/render/render.py`: Playwright Chromium, viewport 1080×1350, `device_scale_factor: 1`. Wait for fonts (`document.fonts.ready`), screenshot each slide, then convert to JPEG q90 sRGB with Pillow. Also produce `preview.png`, a contact sheet of all 5 slides in a row at small size, for the approval Issue.
7. `python -m waypoint sample`: convert 3 existing Waypoint lessons into `post.json` (by hand-mapping or a small converter, **no LLM needed**), then render them to `samples/<slug>/`. Pick lessons that exercise every layout:
   - an HVAC lesson using the heat-pump `diagram` from `content/diagrams.js`
   - a finance lesson with a `chart` (e.g. Compounding or Inflation)
   - a text-only lesson (e.g. Philosophy or History) to test pure typography
8. Golden-image test: render a fixed fixture and compare it to a committed reference image within a pixel tolerance, so later changes cannot silently break the look.
9. Render one deliberately over-long copy fixture and check that it fails loudly.

**Acceptance:**
- [x] `samples/` holds 3 × 5 JPEGs at 1080×1350, each under 1 MB, plus contact sheets
- [x] Checked visually (open the images): margins, bottom 140 px clear, logo and counter on every slide, one accent colour, no HUD styling
- [x] The diagram and chart render crisply in the new style
- [x] Rendering works on Windows locally and in CI (Ubuntu, using `playwright install --with-deps chromium`)
- [x] **The user has explicitly approved the look** (record the date in the Build log)

**Out of scope:** LLM, sources, Instagram.

---

### Phase 3: Gather + select + dedupe

**Goal:** `python -m waypoint gather` returns a ranked list of fresh, non-duplicate candidates with full source text.

**User actions:** none (optionally, suggest favourite sources).

**Tasks:**
1. `gather/feeds.yaml`: a curated list of free sources, each tagged with faculties and `kind`. **Verify every URL works when you implement it.** Starting set:
   - Research and news: arXiv API (selected categories), EurekAlert! RSS, ScienceDaily RSS (by section), phys.org RSS, Quanta Magazine RSS, NASA news RSS, The Conversation RSS (science/tech), Smithsonian Magazine RSS, Hacker News API (top stories, filtered by score)
   - Evergreen: Wikipedia REST API (featured article, "Did you know"), Wikipedia articles by category per faculty (for "how things work" and HVAC/trades topics), NASA APOD API (free `DEMO_KEY` or a free key)
2. `gather/fetch.py`: polite fetching (custom User-Agent naming the repo, 1 request per second per host, timeouts, respect robots.txt). Extract main text with `trafilatura`. Drop items under 300 words or behind a paywall.
3. `dedupe.py`: implement all 4 checks in 4.5. Checks 3–4 need Gemini; add `waypoint/write/gemini.py` here (client, model from config, retries with backoff, a simple rate limiter that respects free-tier RPM). Provide a `--no-llm` mode that runs only checks 1–2, for tests.
4. `select.py`: apply the faculty rotation rules (4.4) using `posted.json`. Score candidates by freshness, faculty weight, "surprise potential" (a cheap Gemini classification, 1–5) and text length. Return the top N per slot, with a fallback chain: news → evergreen → Wikipedia random-in-faculty.
5. Exclude topics from 4.3 (keyword filter plus a Gemini safety label).
6. Cache: `.cache/` (gitignored) for fetched pages during a run.
7. Tests with recorded fixtures (no live network in CI): URL canonicalisation, title Jaccard, rotation rules, fallback chain.

**Acceptance:**
- [x] A live `gather` run locally returns at least 10 candidates across at least 5 faculties, each with `source.txt` content (`--no-llm`, 2026-10-16: 60 candidates, 13 faculties)
- [x] A seeded `posted.json` duplicate is rejected by each of the 4 checks (one test per check)
- [x] Faculty rotation holds over a simulated 20-post sequence
- [ ] Stays within Gemini free-tier limits (log the number of calls per run)

**Out of scope:** writing slide copy.

---

### Phase 4: Writer + fact-check → full offline draft

**Goal:** `python -m waypoint draft --date <d>` produces two complete, rendered, fact-checked drafts under `drafts/`, with no Instagram involved.

**User actions:** read 5–10 generated drafts and give feedback on voice. Tune the prompts until they're good.

**Tasks:**
1. `write/prompts/writer.md`: system prompt carrying all of 4.2 and 4.3, the story arc (each slide's job) and the takeaway rules, the slide skeleton, hook patterns, the 3 rewritten `samples/*/post.json` as reference, two good and two bad examples, and the instruction "only use facts present in SOURCE". Output uses structured JSON matching the `slides`/`caption`/`hashtags`/`alt_text`/`key_claim` parts of 3.2.
2. Figure choice: the writer may add **at most 2** figures, each a valid `visuals.js` `chart` or `diagram` spec. Give it the spec docs (summarised from the top of `visuals.js`) and 3 examples taken from `content/visuals.js` and `content/diagrams.js`. Figures are optional; text-only is fine.
3. `write/factcheck.py`: a second Gemini call with SOURCE and the draft. It returns, for each claim, `{claim, supported: bool, evidence_quote}`. Then the programmatic number check (4.3).
   - Any failure → one rewrite with the failures attached → re-check → if it still fails, discard the candidate and try the next one.
   - Save `factcheck.json` next to the draft.
4. Validation: pydantic copy limits, `textstat` grade ≤ 7, and the hashtag limit. A render overflow error → ask the writer to shorten that slide (max 2 retries).
5. `draft` command: select (Phase 3) → write → fact-check → render (Phase 2) → write `drafts/<date>-<slot>/`. If a slot can't be filled, write `drafts/<date>-<slot>/FAILED.md` with the reason.
6. Tests: mock the Gemini client and use fixture responses (good, unsupported claim, over-long copy, bad figure spec).

**Acceptance:**
- [ ] 10 consecutive live drafts: all render, all pass fact-check, all within copy limits
- [x] A planted unsupported claim and a planted invented number are both caught (live 2026-10-16 on the 10-17 AM draft: the fact-check flagged a planted magnetic-field claim; the number check flagged a planted 92%)
- [ ] Gemini calls per draft are logged, and the daily total fits the free tier with headroom (aim for ≤ 50% of the limit)
- [ ] The user approves the voice and quality of a sample batch

**Out of scope:** GitHub Issues, Pages, Instagram.

---

### Phase 5: Instagram account + publisher

**Goal:** `python -m waypoint publish <draft_dir>` posts a carousel to Instagram (with `dry_run` respected) and records it.

**User actions (give the user a numbered checklist, and pause until each step is done):**
1. Create the Instagram account (handle confirmed) and switch it to a **Professional account (Creator or Business)**.
2. Create a Meta developer account and an app at developers.facebook.com, then add the **Instagram** product → "API setup with Instagram login".
3. Add the Instagram account as an Instagram Tester / app role and accept the invite in Instagram settings. The app can stay in development mode for the owner's own account; no App Review is needed. **Verify this against current Meta docs.**
4. Generate a token with scopes `instagram_business_basic` and `instagram_business_content_publish`, exchange it for a **long-lived** token (60 days), and get the IG user ID.
5. Add the `IG_USER_ID`, `IG_ACCESS_TOKEN`, `GEMINI_API_KEY` and `GH_PAT` secrets to the GitHub repo.

**Tasks:**
1. `publish/instagram.py` (graph.instagram.com; verify the current API version and endpoints in Meta docs before coding):
   - For each slide: `POST /{ig-user-id}/media` with `image_url` (a **public JPEG URL**), `is_carousel_item=true`, and `alt_text` if supported. For a video slide (Phase 7): `media_type=VIDEO` + `video_url`, then poll the container `status_code` until `FINISHED`.
   - Carousel container: `POST /{ig-user-id}/media` with `media_type=CAROUSEL`, `children=<ids>`, `caption`.
   - Publish: `POST /{ig-user-id}/media_publish` with `creation_id`. Then fetch the `permalink`.
   - Check `GET /{ig-user-id}/content_publishing_limit` before publishing (there is a rolling 24-hour cap far above 2 a day).
   - Retries with backoff on 5xx and rate limits; **no retry after `media_publish` returns success** (avoids double posts).
2. Idempotency: before publishing, if `posted.json` already has this `id`, exit 0 without doing anything.
3. `refresh-token`: `GET https://graph.instagram.com/refresh_access_token?grant_type=ig_refresh_token&access_token=…` (the token must be at least 24 hours old). Write the new token back to the repo secret with the GitHub API, using `GH_PAT` (libsodium sealed box via `pynacl`, or `gh secret set` in the workflow).
4. `publish/pages.py`: a helper that turns a draft's slide paths into public Pages URLs (`pages_base_url` + path). Phase 6 handles the actual deployment.
5. Manual end-to-end test: host one sample carousel's JPEGs at public URLs (the Pages branch, or temporarily `raw.githubusercontent.com` if Pages isn't ready), then publish **one real test post with the user watching**. The user decides whether to keep or delete it.

**Acceptance:**
- [ ] `dry_run: true` makes every network write a logged no-op
- [ ] One real carousel published successfully; permalink recorded in `posted.json`
- [ ] Running publish twice for the same id posts once
- [ ] Token refresh works and updates the secret (test with a manual run)

**Out of scope:** scheduling and approval.

---

### Phase 6: Automation, approval flow and media hosting

**Goal:** the system runs by itself on schedule, with the user's approval step.

**User actions:** watch notifications for the approval Issues (GitHub mobile app recommended). Take part in a 3-day dry run, then a 3-day live run.

**Tasks:**
1. **Media hosting (`gh-pages` branch):** after rendering, a step rebuilds the `gh-pages` branch as a **single orphan commit** holding only media from the last `media_retention_days` (14), then force-pushes. This stops repo history growing by about 1 GB a year. Instagram copies media when the container is created, so pruning is safe. JPEGs stay in `drafts/` on `main` **only** as `preview.png` (small); full slides live only on `gh-pages`. Keep the `.nojekyll` file.
2. **`draft.yml`** (cron about 20:00 Toronto; for an evening run Toronto time is the previous UTC calendar day's late hours, so document the UTC conversion and the EST/EDT offset): uv setup → Playwright install (cached) → `waypoint draft --date tomorrow` → commit `drafts/` and the state changes → deploy `gh-pages` → for each draft, open an Issue titled `[Approve] <date> <slot>: <hook>` with the label `pending`. The Issue body holds: slide images inline (Pages URLs), the caption, source link, fact-check summary, and instructions: *"Comment `/reject` or add label `rejected` to skip. Comment `/approve` to post right away. Do nothing and it posts at 08:00."* If a draft failed, open an Issue labelled `failed` with the reason.
3. **`approval.py`:** read the Issue state, labels and comments **by the repo owner only** (ignore comments from anyone else; the repo is public). Rejected → record a `rejected` status in `posted.json` (so it isn't proposed again) and close the Issue.
4. **`publish.yml`:** cron at each slot, plus `workflow_dispatch`, plus a trigger on `issue_comment` for `/approve` from the owner. Look up the draft for the current slot → if `require_approval`, honour reject; when time is up, "no response" means approve → publish → comment the permalink → close the Issue → commit `posted.json`. Use `concurrency: publish` so runs can't overlap.
5. **`refresh-token.yml`:** weekly plus `workflow_dispatch`.
6. **Failure alerts:** any workflow failure opens or updates a single `pipeline-failure` Issue (notifies the user). Never post on partial failure.
7. **Workflow permissions:** least privilege (`contents: write`, `issues: write`, `pages: write` only where needed). Pin third-party actions to a commit SHA. Secrets only in the steps that need them.
8. **Rollout:**
   1. 3 days with `dry_run: true` (Issues created, nothing posted)
   2. 3 days live with approval
   3. Hand back to the user. Turning `require_approval` off later is a one-line config change; document it in the README.

**Acceptance:**
- [ ] The dry-run days produce 2 approval Issues each evening with correct images
- [ ] `/reject` from the owner blocks the post; `/reject` from another account is ignored
- [ ] `/approve` posts immediately; silence posts at slot time
- [ ] A late or doubled cron run never double-posts
- [ ] `gh-pages` stays small (only the last 14 days)
- [ ] Monthly Actions usage is estimated and logged (it's unlimited on public repos, but keep runs under 15 minutes)

**Out of scope:** Manim and analytics.

---

### Phase 7 (later): curated Manim scene templates

**Goal:** optional animated slides (MP4) for insights that are better shown moving, while keeping one consistent visual identity.

**Tasks:**
1. Add `manim` (Community Edition) with ffmpeg. **Avoid LaTeX** (use `Text`/`MarkupText` with the bundled Inter) to keep CI installs light.
2. A shared `WaypointScene` base class: 1080×1350 frame, background, tokens from 4.1, logo and counter, and the same text-fitting rules. Clips of 6–12 seconds at 30 fps, H.264, AAC silent track (Instagram requires an audio track for some video uploads; verify).
3. Start with 6 parameterised templates, each with a JSON schema:
   - `curve` (a function draws itself plus a highlighted point)
   - `bars_compare` (before/after or A vs B grow-in)
   - `cycle` (a loop of 3–6 labelled stages with flowing dots, e.g. the refrigeration cycle or water cycle)
   - `scale` (orders-of-magnitude zoom: atom → cell → human)
   - `count_up` (a big number counting up with a comparison)
   - `timeline` (3–6 events)
4. The writer may pick a `{"type":"manim","template":…,"params":…}` figure (max 1 per post). Validate the params against the template's schema; on failure, fall back to a static figure.
5. Render the poster frame as a JPEG for previews. The approval Issue links the MP4.
6. Publisher: carousel children with `media_type=VIDEO` (Phase 5 already planned for this).

**Acceptance:** each template has a golden render, renders in CI in under 3 minutes, and matches the static style side by side.

### Phase 8 (later): learn from performance

**Tasks:** fetch insights (reach, saves, shares, follows) for each post through the API (verify the available metrics). Store them in `state/metrics.json`. A/B test the sign-off: the takeaway-led ending (default) against a direct "Follow for two ideas a day" ending, measured as follows per reach; add a config flag for the variant. Weekly, adjust faculty weights and hook-pattern weights within bounds. Write a weekly summary Issue. Never change the visual identity automatically.

---

## 6. Risks and mitigations

| Risk | Mitigation |
|---|---|
| A wrong fact gets posted | Claim-by-claim fact-check, the programmatic number check, the approval step, and skipping advice and politics topics |
| Gemini free-tier limits or model changes | Model name in config, call budget per run, retries, Wikipedia evergreen fallback, logging |
| Meta API or policy changes | Keep all IG calls in `instagram.py`; check the API version in config; failure alerts |
| Token expiry (60 days) | Weekly refresh plus a failure Issue if refresh fails |
| Repo bloat from media | Orphan `gh-pages` with 14-day retention |
| Public repo abuse via comments | Only the owner's `/approve` and `/reject` are honoured |
| Copyright | Only our own visuals, short paraphrase, source credit |
| Account flagged as spam | 2 posts a day, varied hashtags (max 5), original visuals, no follow/like automation |

## 7. Build log

| Date | Phase | Agent notes |
|---|---|---|
| 2026-10-16 | Plan | Plan written. Decisions: public repo, approval at launch, Gemini free, Waypoint brand. |
| 2026-10-16 | 1 | Scaffold pushed to github.com/mbutler98/waypoint-daily (public); CI green (ruff + 38 tests). Repo lives in the `waypoint-daily/` subfolder; this copy of PLAN.md is canonical. Added `status` (published/rejected/failed) to PostedEntry and documented it in 3.3. `.gitattributes` forces LF. Actions pinned: checkout v7.0.1, setup-uv v9.0.0. Gemini model and IG API version in config are placeholders for Phases 3/5 to verify. Pages not enabled yet: GitHub needs the `gh-pages` branch to exist, so do it in Phase 6. CI warns `ubuntu-latest` moves to Ubuntu 26 from 2026-10-19; recheck Playwright deps in Phase 2. |
| 2026-10-16 | 2 | Renderer + 3 samples on branch `phase-2` (CI green on Ubuntu, incl. golden image made on Windows). Templates in `waypoint/render/templates/`; visuals.js vendored at Daily_quiz `dcb5a35`, re-skinned by CSS only. New 4.1 rules: figure legibility floor (card >= 360 px, figure text >= 18 px) and diagram colour folding (cold -> blue, hot -> accent yellow). Diagram text sizes raised slightly for phones. Fixed the Phase 1 fixture chart to real visuals.js format. Daily_quiz has no licence file; credit kept in README. **Awaiting user approval of the look; merge `phase-2` to `main` only after sign-off.** |
| 2026-10-16 | 2 | User feedback round 1 ("less mini textbook, more story with a payoff"): swipe hint on slide 1; insight numbering dropped; last slide is now an editorial sign-off (takeaway + fixed brand promise from config, `brand.cta_lines` replaced by `signoff_kicker`/`signoff_lines`/`caption_cta`); "Save this" moved to the caption; takeaway limits ≤ 8 / ≤ 14 words in models; 4.2 gains the story arc and tension-led hooks; Phase 8 gains the sign-off A/B test. Short figures (e.g. timelines) now get a snug card instead of a stretched one. Samples rewritten as stories; history hook shortened to "Not always for the better" because "Not necessarily…" broke the 3-line hook limit. Golden image regenerated. Still awaiting look approval. |
| 2026-10-16 | 2 | **Look approved**: the user merged PR #1 (`phase-2` → `main`). |
| 2026-10-16 | 3 | Gather/select/dedupe on branch `phase-3`. Feeds verified live: ScienceDaily (9 sections), phys.org (3), Quanta, NASA, The Conversation (tech/environment/health/business), Smithsonian (3), HN, arXiv (HTML full text), Wikipedia featured + DYK, Wikipedia categories per faculty (also the fallback). Dropped: EurekAlert! (all RSS URLs 404), NASA APOD (DEMO_KEY 500, texts < 300 words), Conversation /science (404). Gemini: 2.5 Flash is closed to new projects, so config now uses `gemini-3.8-flash` (writer) and `gemini-3.5-flash-lite` (`fast_model`: triage, safety, dedupe-4); per-model free RPM/RPD are only shown in AI Studio, so `requests_per_minute: 8` stays conservative. One batched triage call labels 10 candidates (faculty, topic, key claim, surprise, safety); labels are cached in `.cache/triage.json`. Estimated calls per run: about 6 triage + 2 embed + 1 dedupe-4 (+1–2 if the fallback runs), against a budget of 100. `Candidate` added to models (3.2.1). Live `--no-llm` run: 60 candidates, 13 faculties, 162 HTTP requests, ~2 min. **Open:** a live run with Gemini needs `GEMINI_API_KEY` (in `.env` locally) to confirm the call count; then tick the last box. |
| 2026-10-16 | 4 | **In progress** on branch `phase-4`. Writer, fact-check (with programmatic evidence-quote and number checks), `FigureDraft` → visuals.js layout, `draft` and `write` commands, mocked tests. First live run: AM drafted in 5 calls; PM failed on a 429, because **`gemini-3.8-flash` free tier allows only 20 requests/day**. That AM draft was a NASA personnel announcement with someone else's topic label. Cause: batch triage trusted the model's `index`, and a shifted index put one article's labels on another. Fixes: labels echo the article title and are matched by title (`match_label`); the triage cache key is versioned (`v2`) so old labels are ignored; `limits.min_surprise: 3` keeps institutional news out (fallback included); a per-day-quota 429 stops the run immediately instead of retrying and moving on to more candidates; `gemini.model_budgets` caps the writer model at 10/run (50% of the cap; validated against configured model names); each slot gets an equal share and a candidate is only started if a write and a fact-check still fit. A clean draft costs 2 writer-model calls, so 10 consecutive live drafts take several days of quota. **Open:** the 4 Phase 4 acceptance boxes (live runs from 2026-10-17), and the user to read the drafts for voice. |
| 2026-10-16 | 4 | **Claude stopgap** (user's call): `--llm claude` / `WAYPOINT_LLM=claude` sends text calls through the local `claude -p` CLI (`waypoint/write/claude_cli.py`, calls `claude.exe` directly because cmd.exe mangles the JSON schema); Gemini keeps embeddings; local only, CI stays on Gemini. Live runs found and fixed: fact-check was judging the appended source credit and CTA (it now sees only the writer's caption) and failing accurate-but-loose wording; hooks that overflow 3 lines (writer now gives 2 `alt_hooks`, tried in the renderer before a rewrite); escaped `\n` in captions; same-day kept drafts weren't planned before ranking (two Animals & Nature on 10-17); pending drafts on other dates weren't off-limits (a redo picked a later day's article). New: `state/skipped.json` + `limits.skip_failed_days: 14` so a candidate the writer gave up on isn't retried daily. Result: 10 drafts for 10-17..10-21 across 8 faculties, every one passing fact-check (13-21 claims each); in the batch run 9 of 10 slots passed first time (10-21 PM failed on real overstatements and was redone). Cost: about 4 writer-model calls per passing draft and 10-17 per run with failed candidates, so on Gemini's 10/run budget (5 per slot) a slot with one bad candidate can run dry: the 'fits with headroom' box stays open. Voice notes for the user are on the review page. **Open:** user voice approval; Gemini-side call fit; 10 consecutive live drafts on the production LLM. |
