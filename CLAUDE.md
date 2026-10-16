# Waypoint Daily

Automated Instagram educational carousels (hook → 3 insights → follow/save CTA), twice a day, free to run.

**Read [PLAN.md](PLAN.md) sections 1–4 before any work, then do only your assigned phase (section 5).**
When done, tick that phase's acceptance boxes and add a line to the Build log (section 7).
Do not re-open the decisions in section 1.

## Run

```
uv sync                            # install (Python 3.12 via uv)
uv run python -m waypoint --help   # CLI
uv run pytest                      # tests
uv run ruff check                  # lint (CI runs both)
```

## Conventions

- Python 3.12, managed with uv. Lint with ruff; tests with pytest. CI must stay green.
- `waypoint/models.py` is the data contract (PLAN.md 3.2/3.3). Change it only together with PLAN.md.
- `dry_run` (config.yaml) must be respected by every network write: log it and skip it.
- Never print, log or commit secrets. They come from env vars / GitHub Actions secrets only.
- No live network in tests: use recorded fixtures and mock the Gemini client.
- `state/posted.json` is append-only. Never rewrite history.
- If any step fails, post nothing for that slot and record why. No partial carousels.
- Keep model names, API versions and limits in config.yaml, not in code.
