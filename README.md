# Goodframes

From three hundred frames to the ones worth editing. A Mac app that culls a photo shoot:

1. **Your brief**: after the free local analysis, chat with Claude about the shoot: what it is, who
   matters, the look you like. Claude writes a brief, and the cull starts only when you confirm it
   and its cost. A one-click standard cull is also available, and is confirmed the same way.
   After the cull, **Refine** corrects it the same way, rerunning only what the change needs.
   Lasting preferences can be saved to My style when you approve them.
2. **Technical pass** (local, free): rejects only what's unusable and flags the rest (blur, shake,
   exposure, blinks, tilted horizons). It keeps up to four frames across each burst, so the
   best expression still reaches the review, and lets deliberate extremes through with a note.
3. **Moments**: groups frames shot of one subject in one go, so you get one pick per moment
   instead of five variations of the same scene.
4. **Claude review**: each moment is sent to Claude as 1024 px previews, with a full-resolution
   close-up of the main face to check focus. Claude judges like a photo editor (intent, impact,
   moment, then light and composition), names the best frame, and spots **Hidden gems**: frames
   with strong bones that need an edit, each with a recipe. By default the review is **two-tier**:
   Claude Haiku screens every moment, and the model chosen in Settings re-reviews only the close
   calls (a best frame near the line, or near-tied best frames) and makes the final edit. Turn it
   off in Settings, or with `review --single`, to send every moment to the chosen model.
5. **Final edit**: the best frames of the whole shoot are compared together, ranked, and cut down
   to a set without repeats. Selection size in Settings moves the cut for free.
6. **You decide**: browse picks, gems and rejects, override anything, and copy the results to
   `Goodframes/Picks` and `Goodframes/Hidden Gems` next to your originals. Your overrides teach
   later reviews your taste.

Reviews, overrides and analysis are cached per shoot in `<folder>/.photo-cull/cull.db`, so
re-opening a shoot costs nothing. Originals are never moved or modified.

## Run

```bash
python3 -m venv .venv && .venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python desktop.py                        # the app
.venv/bin/python -m photocull --help               # the CLI (used by the Claude Code skill)
.venv/bin/python -m photocull eval FOLDER --keepers keepers.txt   # score picks against your own
.venv/bin/python -m pytest tests                   # tests
.venv/bin/pyinstaller packaging/Goodframes.spec     # build dist/Goodframes.app
```

You need a Claude API key (console.anthropic.com). It's stored in the macOS Keychain.
Default model: Claude Sonnet 5.5 (the app shows the estimated cost before each review); Claude Opus 5.5 is available in
Settings. `PHOTOCULL_DEMO=1` runs the UI with fake reviews and no key, for development.

## Layout

- `photocull/engine/`: ingest, quality metrics, bursts, moments, scoring, brief, Claude review, gems, export, SQLite store
- `photocull/server/app.py`: local FastAPI server (REST + Server-Sent Events), bound to 127.0.0.1
- `photocull/ui/`: HTML/CSS/JS, animations with GSAP
- `desktop.py`: native window via pywebview
