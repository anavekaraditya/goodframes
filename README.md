# PhotoCull

From three hundred frames to the ones worth editing. A Mac app that culls a photo shoot:

1. **Technical pass** (local, free): blur, camera shake, blown or crushed exposure, blinks,
   tilted horizons; keeps the best frames of each burst.
2. **Moments**: groups frames shot of one subject in one go, so you get one pick per moment
   instead of five variations of the same scene.
3. **Claude review**: each moment is sent (as small 1024 px previews) to Claude, which scores
   composition, light and moment, names the best frame, and spots **Hidden gems**: frames with
   strong bones that need an edit, each with a recipe.
4. **You decide**: browse picks, gems and rejects, override anything, and copy the results to
   `PhotoCull/Picks` and `PhotoCull/Hidden Gems` next to your originals. Your overrides teach
   later reviews your taste.

Reviews, overrides and analysis are cached per shoot in `<folder>/.photo-cull/cull.db`, so
re-opening a shoot costs nothing. Originals are never moved or modified.

## Run

```bash
python3 -m venv .venv && .venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python desktop.py                        # the app
.venv/bin/python -m photocull --help               # the CLI (used by the Claude Code skill)
.venv/bin/python -m pytest tests                   # tests
.venv/bin/pyinstaller packaging/PhotoCull.spec     # build dist/PhotoCull.app
```

You need a Claude API key (console.anthropic.com). It's stored in the macOS Keychain.
Default model: Claude Sonnet 5.5 (~$1 for a 300-photo shoot); Claude Opus 5.5 is available in
Settings. `PHOTOCULL_DEMO=1` runs the UI with fake reviews and no key, for development.

## Layout

- `photocull/engine/`: ingest, quality metrics, bursts, moments, scoring, Claude review, gems, export, SQLite store
- `photocull/server/app.py`: local FastAPI server (REST + Server-Sent Events), bound to 127.0.0.1
- `photocull/ui/`: HTML/CSS/JS, animations with GSAP
- `desktop.py`: native window via pywebview
