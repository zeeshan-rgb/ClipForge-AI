# ClipForge AI — working notes for Claude

Read this first, then [docs/PROJECT_STATUS.md](docs/PROJECT_STATUS.md) for
where the work stands.

## What this is

A local web app that turns one long video into reviewed, ready-to-publish
YouTube Shorts. Python (FastAPI, SQLite, an in-process job queue) serves a
React dashboard; one command starts it. Built in ten phases, each ending with
a test on real output.

## The documents

| File | Holds | Update when |
| --- | --- | --- |
| [docs/PROJECT_STATUS.md](docs/PROJECT_STATUS.md) | One page: what is done, open, next | End of every phase |
| [docs/ROADMAP.md](docs/ROADMAP.md) | The ten phases, each phase's gate and its recorded results (this is the implementation roadmap) | End of every phase |
| [docs/TASKS.md](docs/TASKS.md) | Feature requests broken into items marked built / partly built / not built | When an item changes state |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Design, and an "as built" note per phase | When a phase changes the design |
| [README.md](README.md) | What the app does today, how to run and set it up | When what the user sees changes |
| [docs/PHASE11_PLAN.md](docs/PHASE11_PLAN.md) | Phase 11 plan (Creator Studio), milestones 11.1–11.10; approved 2026-11-17 with all proposals; progress per milestone in its section 7 | After every milestone |

At the end of a phase, all five are brought up to date before the phase is
reported as done. Record what was tested and how, what failed and was fixed,
and what was not done. Never call a feature done because its screen exists.

## Layout

| Path | What |
| --- | --- |
| `clipforge.cmd` | Launcher: starts the server and opens the dashboard (port 8765) |
| `backend/clipforge/app.py` | App factory, routes, WebSocket progress |
| `backend/clipforge/jobs/engine.py` | SQLite-backed job queue; stages save results so jobs resume after a crash |
| `backend/clipforge/pipelines.py` | The pipelines and every stage function |
| `backend/clipforge/ingest/` | Video sources (yt-dlp based) |
| `backend/clipforge/analysis/` | Sound and picture signals, speech-to-text |
| `backend/clipforge/ai/` | AI provider layer (`Reasoner.ask_json`) |
| `backend/clipforge/moments.py` | AI moment detection and the moment score (8 AI parts + 2 measured, weighted average of the parts a moment has) |
| `backend/clipforge/moment_quality.py` | Measured sound and picture quality of a moment (no AI) |
| `backend/clipforge/duration.py` | Smart Duration engine: exact clip boundaries and part splitting, no AI |
| `backend/clipforge/captions.py` | Word-synced captions: lines, styles, ASS output, timing and placement checks |
| `backend/clipforge/music.py` | Licensed music: licence rules, track analysis, mood matching, beat alignment, ducking envelope |
| `backend/clipforge/framing.py` | Faces, facecam, layouts, camera path, zoom, keeping text off faces; the frame compositor |
| `backend/clipforge/qc.py` | Quality checks on the rendered file and the quality score |
| `backend/clipforge/metadata.py` | Titles, descriptions, hashtags, pinned comment; credit lines by code |
| `backend/clipforge/exports.py` | Export: video, `.txt`, `.json` sidecar and `rights.json` into the export folder |
| `backend/clipforge/youtube.py` | YouTube: owner's OAuth client, PKCE sign-in, resumable private upload, privacy change, `publish_blocker` |
| `backend/clipforge/rights.py` | Rights record and the download gate |
| `frontend/src/` | React + TypeScript + Tailwind dashboard |
| `data/` | Database, project files, speech models (not in git) |
| `tools/ffmpeg/` | FFmpeg and ffprobe (not in git) |
| `tools/fonts/` | Caption fonts, SIL Open Font License (not in git; sources in README) |
| `tools/models/` | YuNet face model, MIT licence (not in git; source in README) |

## Commands

Run from the repository root (`D:\ClipForge-AI`).

- Tests: `backend\.venv\Scripts\python -m pytest -q` from `backend`
  (about 420 tests, five to thirteen minutes). Their temporary files go to
  `D:\tmp\cf_pytest` (or `CLIPFORGE_TEST_TMP`): C: is too full, and a low C:
  once made unrelated tests time out. Do not run the suite while a live
  transcription is running; together they ran out of memory.
- Dashboard build, after any frontend change: `npm run build` in `frontend`.
  The server serves `frontend/dist`; it does not hot-reload.
- Start the app: `clipforge.cmd` (`--port`, `--no-browser`, `--data-dir`).

## Rules that must hold

- **Rights.** A link is downloaded only with a rights record (own, or
  licensed / creator permission with creator and evidence). The check is in
  `rights.py` and is repeated inside the download stages. Credit is never
  treated as permission. Never sign in, use cookies, or work around platform
  access limits.
- **Tests have no real side effects.** `tests/conftest.py` replaces the
  platforms, the speech model, the AI and the file dialog with stand-ins.
  New outward-facing code needs a stand-in there too.
- **Cutting, captioning, framing and rendering never call the AI.** Changing
  duration settings re-cuts from stored moments; changing the caption style
  re-captions without re-cutting. The AI is asked only by "Pick moments
  again", and for a Short's text: once per final render for clips without text
  (one request for all of them), and by "Write again".
- **The moment score is an estimate, never a prediction.** Every part shows
  whether the AI estimated it or code measured it; never word it as
  predicting views or virality, and never change weights without the owner.
- **Credit lines are written by code, never by the AI.** The creator credit
  and original link, the music credit, part numbers and #Shorts are added
  after the AI answers (`metadata.py`); links from the AI are removed.
- **Quality checks measure the rendered file** (`qc.py`); a fail blocks export
  unless the owner exports anyway.
- **YouTube Shorts rules live only in `youtube.shorts_checklist()`**; never
  re-implement a rule in the frontend or another endpoint.
- **Phase 11 is YouTube Shorts only**: no Reels, TikTok or multi-platform export.
- **YouTube: private uploads only; public only with the owner's confirmation**
  (`confirm: true` plus the exact title). `publish_blocker` refuses unconfirmed
  rights in the API and again in the upload stage. Never sign in, upload or
  publish on the owner's behalf from a session: the owner signs in on Google's
  page and says which Short goes up. Tests reach Google only through the
  `fake_google` stand-in; `no_real_google` blocks everything else.
- **Captions show what was heard.** Never correct, add or drop a word; flag
  unsure ones.
- **Captions sit at 60% down and inside the centre 740 px** (owner's choice,
  2026-11-17), clear of YouTube's right-hand buttons and the title below 78%.
  These limits live in `captions.py` (`RIGHT_BUTTONS`, `SAFE_WIDTH`,
  `PLATFORM_UI_ZONE`, `DEFAULT_POSITION`); do not move captions back towards
  the bottom.
- **No track without a complete licence record is ever used.** The check is
  in `music.licence_problems` and is applied when importing, when choosing
  (automatic or by hand) and when rendering. Never add a path around it, and
  never put music files in git. Test tracks must be original or properly
  licensed, and are kept outside the repository.
- **The clip's own sound is never processed**, except one owner-approved case
  (2026-11-18): under the owner's own Story-mode narration it is lowered by a
  gain envelope (`story.DUCK_TO`, about −10 dB) with short ramps. Music is added to it and
  lowered under speech; mono sounds are copied to both channels at unity
  (FFmpeg's default mono-to-stereo conversion cuts 3 dB per channel).
- **Loudness levelling is one gain on the finished mix** (then a peak-only
  limiter), measured on the stereo signal as delivered. Never compress, EQ or
  otherwise reshape the clip's sound to hit the target.
- **Framing never calls the AI.** Face scans are kept per video and range in
  the project's `framing\` folder; changing the layout re-plans from them.
- When measuring audio in a test, compare the same channel of both files:
  FFmpeg's stereo-to-mono downmix adds the channels and reads 3 dB louder.
- **Memory is scarce on this PC.** Speech-to-text releases its model in a
  `finally` and does an out-of-memory chunk in halves (`transcribe_span`);
  keep any new heavy step able to do the same. Never keep a model, or a big
  array, alive past the stage that needs it (a failed attempt's traceback holds
  its frames until the `except` block ends).
- **Database changes** must be new nullable columns: `db.add_missing_columns`
  upgrades existing databases in place.
- **Heavy files live on D:.** C: is nearly full.
- **Changes are kept small.** Extend the existing modules; do not restructure
  working code without a reason found in testing.

## Checking work in a browser

- Use the preview configuration `clipforge-test`: port 8799, data in
  `D:\tmp\cf_dev_data`, never the real `data\`.
- To reuse the downloaded speech models there, make a directory junction
  `D:\tmp\cf_dev_data\cache\models` → `D:\ClipForge-AI\data\cache\models`.
  Remove the junction with `cmd /c rmdir` before deleting the test folder,
  or the real models are deleted with it.
- Stopping the preview can leave the Python process running. Afterwards,
  stop whatever still listens on ports 8799 and 8800, or a stale server with
  old code will share the test database.
- Test videos must be ones the owner may use, or openly licensed. Tears of
  Steel (CC BY 3.0, Blender Foundation) has been used for real speech.
- Scratch scripts go outside the repository and must not share a name with a
  Python standard module (a file called `inspect.py` broke `httpx`).
- A job keeps the stage list it was created with. After adding a stage,
  restart the test server and start a new job; a job queued under the old
  code resumes without the new stage.
- When checking rendered frames at a given time, sample well inside the
  window being tested: the frame shown at time t is the first one at or after
  t, so a time within one frame of a change shows what comes next.

## Releasing a phase

The owner pushes to GitHub themselves. At the end of each phase, give them
three commands (`git -C D:\ClipForge-AI add -A`, `commit -m "Phase N: …"`,
`push`) after checking that nothing private, no media and nothing from
`data/`, `tools/`, `.venv/`, `node_modules/` or `.claude/` would be included.
The repository is public. Do not commit or push for them, and do not add a
co-author line to commit messages.
