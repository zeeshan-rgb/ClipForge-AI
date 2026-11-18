# ClipForge AI

Turns one long video into reviewed, ready-to-publish YouTube Shorts: finds the
best moments, cuts them with context, reframes to 9:16, adds word-synced
captions and optional licensed music, and writes titles and hashtags. Nothing
is published without approval.

**Status:** Phases 1 to 10 are built; the upload to a real YouTube channel
is still to be tried with the owner's own Google Cloud client. Phases 1 to 9 are — the app foundation, dashboard,
video ingestion, whole-video analysis, AI moment picking, clip cutting with
Smart Duration, word-synced captions, background music, vertical framing with
the final render, and quality checks with titles, descriptions and export.
From one link or file it produces finished 1080×1920 Shorts, each checked,
with its text written and credit given, and exports them to a folder. It does
not yet publish to YouTube (Phase 10). Everything so far has been checked on
test videos, openly licensed films and synthetic music, not on real stream
footage or real songs. See the
[roadmap](docs/ROADMAP.md) for what each
phase adds and the [architecture](docs/ARCHITECTURE.md) for how it fits
together.

## Run it

```
D:\ClipForge-AI\clipforge.cmd
```

This starts the app and opens the dashboard at http://127.0.0.1:8765. Close it
with Ctrl+C in that window. Running the command again while the app is open
just opens the dashboard.

Options: `--port 9000`, `--no-browser`, `--data-dir <folder>`.

## What works today

- Create a project from a video file on this computer. The app reads its
  length, resolution and codecs, makes a poster frame, a 360p preview copy and
  a 16 kHz mono audio track for analysis.
- Create a project from a YouTube or Twitch link. Public videos are downloaded
  at up to 1080p, but only once the rights record is filled in: you own it, or
  you name the creator and where the permission comes from. Otherwise the link
  is saved and not downloaded.
- Kick links are accepted, but Kick recordings currently cannot be fetched;
  add the video as a file instead. Any link project can take a file.
- The app never signs in or works around access limits. Private,
  members-only, age-restricted, DRM-protected and live videos are refused with
  an explanation.
- Each project folder gets a `rights.json` with the link, channel, credit,
  rights status and permission evidence.
- Every prepared video is analysed automatically: a loudness curve, silences,
  sudden loud moments, scene changes, on-screen motion, and a transcript with
  word timings. The project page shows them on a clickable timeline beside a
  searchable transcript. Transcription runs on this computer; the speech model
  (about 500 MB) downloads on first use. A three-hour stream takes roughly
  20–40 minutes to transcribe, and an interrupted run continues from the last
  finished ten-minute chunk.
- After analysis the AI picks the moments worth clipping. Each gets:
  - a category: funny, shocking, unexpected, horror, rage, exciting,
    emotional, failure, insane, suspense, celebration, punchline or gameplay
    outcome;
  - eight marks out of ten from the AI, including a "hook" for the first
    seconds;
  - two scores measured by the app itself, sound quality and picture
    quality, with the reasons for any points off;
  - a check of the transcript for a complete setup and payoff;
  - flags for no dialogue, a reaction on screen, or gameplay;
  - a score from 0 to 100, a reason in plain words, and a note on what frames
    from the video showed.

  The score is an estimate for ranking moments against each other; it does
  not predict views. Moments with no dialogue can come from sudden loud
  sounds or bursts of on-screen movement, and are kept only if the frames
  show something. The project page lists the moments with filters and a play
  button, and marks them on the timeline. Changing a project's clip settings
  or the score weights (Settings, all ten parts) re-ranks them at once
  without asking the AI again.
- Picking needs an AI provider: the Antigravity command-line tool, signed in,
  or a Gemini API key in `.env`. Without one the video, timeline and
  transcript are still produced and the project says why moments are missing.
- Each selected moment is cut into a clip by the Smart Duration engine. You
  choose a target length (15, 20, 30, 35, 45, 60, 90 seconds or your own) and
  a mode: Strict (never longer), Smart (a little over or under so a sentence
  can finish) or Flexible (as long as the moment needs). A clip always starts
  at the beginning of a sentence and ends after a finished one; if nothing
  complete fits, it says so instead of cutting a sentence. Long moments can
  be split into connected Part 1 / Part 2 clips at finished sentences.
- Every clip has a plain preview, its target against actual length with the
  reason for any difference, a list of checks it passed, and Approve / Reject
  (the same buttons are at the top of the Clip editor's Final Short panel, so
  you can approve a clip while you are editing it).
  Changing the duration settings cuts the clips again at once, with no AI
  call.
- Every clip gets captions timed to the spoken words, with the word being
  said highlighted. Four styles: Viral Highlight (the look of the reference
  Short), Bold Emphasis, Minimal Professional and Gaming. The Clip editor
  changes font, size, colours, outline, shadow, height, words per line and
  animation speed for the whole project, so connected parts match, and
  re-renders the previews. Each clip shows whether its captions passed the
  timing and placement checks, and which words the speech model was unsure
  of; those are shown as heard, never guessed.
- Captions sit just below the middle of the Short (60% down) by default, in
  the centre of the width. They stay clear of YouTube's own controls: the
  like, comment and share buttons on the right, and the channel name and title
  at the bottom. A word too long for that space is drawn smaller rather than
  running into the buttons. If a face is in the way, captions move to the
  nearest clear height. In tight close-ups a face can go undetected, so a
  caption may then cross it.
- Background music from a library of tracks you are licensed to use
  (Settings → Music library). A track is added with its mood and licence
  details (where it came from, the licence, a link or note, and a credit line
  if the licence needs one); until those are complete it cannot be used,
  automatically or by hand. Each moment gets a track that suits it, or none:
  music is skipped for non-stop talking in Automatic and can be turned off for
  a project or for one clip. The music sits well under the speech (about 24 dB
  while someone is talking), is lowered under every spoken word, fades in and
  out, and starts on a bar line that lines up with the key moment when the
  track has a steady beat. Connected parts keep the same track. The speech
  itself is never altered. A CC BY track's credit line is kept with each clip
  that uses it. The library starts empty and Shorts are made without music
  until a matching licensed track is added.
- Vertical framing. Faces are found in every clip on this computer (no AI).
  A streamer's facecam is recognised once for the whole video, and its box is
  lined up with the overlay's edges. Layouts: full screen following the
  speaker's face, split screen (streamer on top, gameplay below), gameplay
  with the facecam in a corner, or a facecam reaction. Automatic picks split
  screen when there is a facecam and full screen otherwise; each clip can be
  changed in the Clip editor. The camera glides after the face, cuts with the
  scene, and pushes in slowly on the key moment. Captions move up or down if
  they would cover a face, and connected parts get a "PART 1/2" label for
  their first seconds, placed clear of faces and captions. Each clip says
  what was found and why its layout was chosen.
- Final Shorts. Approved clips are rendered at 1080×1920 (H.264 through Intel
  Quick Sync when available, AAC 48 kHz) at the video's own frame rate, with
  one overall volume change to reach YouTube's loudness (−14 LUFS) and a
  limiter on peaks only. Each file is checked (size, codecs, length,
  loudness, peak) and can be downloaded from the Clip editor; they are kept in
  the project's `final` folder.
- Clips from someone else's video carry a small "@Name" credit on screen, in a
  corner clear of faces and captions (it can be turned off per project).
- Quality checks on every final Short, on the rendered file itself: size and
  codecs, length, black bars, black or frozen picture, missing or silent
  sound, clipping, loudness, music too loud under the speech, long silence,
  caption checks, unclear words, whole-sentence cut, repeats and rights. Each
  Short gets a score out of 100 and the reason for every problem. A Short that
  fails a check is not exported unless you export it anyway.
- Titles, descriptions, hashtags and a pinned comment, written by the AI from
  what is said in each clip (one request for all of them). The credit and the
  original link at the top of the description, the music credit, "Part 1/2"
  and #Shorts are added by the app itself, so they are never missing. You can
  edit the text or have it written again. Without an AI, a plain title is made
  from the words said.
- Generated Shorts page: every approved or rendered clip with its video,
  quality report and text; approve, reject, render again, export, download.
  Export writes the MP4, a paste-ready `.txt` and a `.json` with the text,
  rights record, music licence and quality report into
  `data\exports\<project>` (or the folder in `CLIPFORGE_EXPORT_DIR`), with the
  project's `rights.json` beside them. Shorts from a video whose rights are
  not confirmed are exported with a warning.
- Live, stage-by-stage progress for background jobs, with cancel and retry.
  A job interrupted by closing or killing the app continues from the first
  unfinished stage when the app starts again.
- Default clip settings (categories, clip count, score, length).
- Settings → Pipeline self-test: checks FFmpeg, renders a captioned vertical
  test clip, checks storage.

- YouTube (Settings → YouTube channel): use an OAuth client of type "Desktop
  app" from your own Google Cloud project with the YouTube Data API v3 on, and
  sign in on Google's own page; ClipForge never sees your password. Approved
  Shorts are uploaded as private only. Making one public or unlisted is a
  separate step with your explicit confirmation. The YouTube Shorts checklist
  covers rights, approval, 1080×1920, length (up to 3 minutes, with a warning
  over one minute for others' footage or music), encoding, sound, quality,
  title, description, hashtags, credit, music licence, kids declaration and
  channel. It is shown on each Short and enforced by both steps. A title or
  description edited after the upload can be sent to the video before
  publishing. Anything from
  a video whose rights are not confirmed is refused. YouTube allows about six
  uploads a day on a new project, and keeps videos from projects it has not
  audited private (publish those in YouTube Studio).

## Set up on a new machine

The repository does not contain the Python environment, the built dashboard or
FFmpeg. After cloning:

1. `python -m venv backend\.venv`
2. `backend\.venv\Scripts\python -m pip install -r backend\requirements.lock.txt`
3. `backend\.venv\Scripts\python -m pip install -e backend`
4. In `frontend`: `npm install`, then `npm run build`
5. Put `ffmpeg.exe` and `ffprobe.exe` in `tools\ffmpeg\`
6. Put the caption fonts in `tools\fonts\`: `Montserrat-ExtraBold.ttf` and
   `Montserrat-Black.ttf` (github.com/JulietaUla/Montserrat), `Anton-Regular.ttf`
   and `Poppins-SemiBold.ttf` (github.com/google/fonts, `ofl/`). All are under
   the SIL Open Font License. Settings shows whether all four are found.
7. Put the face model in `tools\models\`: `face_detection_yunet_2023mar.onnx`
   from github.com/opencv/opencv_zoo (`models/face_detection_yunet`, MIT
   licence). Without it the app still works but keeps the centre of the
   picture instead of following faces.

## Story mode (your narration)

In the Clip editor, **Story mode** suggests 3–4 short lines for the Short (a hook,
context, your reaction, an outro), story cards and reaction stickers. Edit the
lines into your own words and record each one with your microphone right there.
The stream's sound dips while you speak and your words appear in a narrator box.
Then render the final Short. Shorts of someone else's stream are only scheduled
automatically once they carry at least 4 s of your narration. Stickers are drawn
by ClipForge as text; music can come from a YouTube Audio Library folder
(Settings → Music library → "Add a folder"), with moods guessed per track.

## Posting schedule and hooks

- **Schedule** (Generated Shorts page): Shorts are uploaded as private with a
  publish time and YouTube makes them public then, even with this computer off.
  Set daily times in your time zone; "Schedule approved Shorts automatically"
  fills the next 48 hours of slots. Current setting: 07:30 and 21:30 IST:
  07:30 is 9 pm ET / 6 pm PT the evening before and 1 pm in Sydney; 21:30 is
  4 pm in the UK and Indian prime time. YouTube says time of day is not
  something its Shorts feed optimises for, so after about a month use
  YouTube Studio → Analytics → Audience → "When your viewers are on YouTube".
- **Opening hook** (project option): Automatic opens a clip with a 1–3.5 s
  flash-forward of the payoff when it comes late, then plays the story in
  order. The quality report now also checks the opening, dead air and the ending.
- **Monetisation:** YouTube's reused-content policy applies to the whole
  channel, and a streamer's permission does not exempt clips "with little or no
  narrative". Add original commentary or editing.

## Permissions page (asking streamers)

Paste a streamer's YouTube, Twitch or Kick channel link. ClipForge reads their
public profile and any link-in-bio page for a contact address, writes a
personal request from your template, and sends it from your Gmail: by default
at most 5 a day, 20 minutes apart, with one follow-up after 7 days and the
request closed as "no reply" a fortnight later. Replies are noticed
automatically and kept in `data/permissions`. Silence is never permission;
only your "They said yes" click is, and then new projects from that streamer
get "creator permission" with the record, after a check that each video really
is theirs. A "no" withdraws it and that address is never written to again.

Gmail uses the same Google Cloud client as YouTube: enable the Gmail API in
that project, then Connect Gmail on the Permissions page. Twitch profile text
needs `TWITCH_CLIENT_ID` and `TWITCH_CLIENT_SECRET` in `.env`. YouTube hides
many business emails behind a CAPTCHA ("View email address"), which ClipForge
does not get past: paste those in by hand.

## Set up on macOS

1. `brew install python@3.12 python-tk@3.12 ffmpeg-full` (the plain `ffmpeg`
   formula has no libass, so captions fail)
2. `/opt/homebrew/bin/python3.12 -m venv backend/.venv`
3. `backend/.venv/bin/python -m pip install -r backend/requirements.lock.txt`
4. `backend/.venv/bin/python -m pip install -e backend`
5. In `frontend`: `npm install`, then `npm run build`
6. `mkdir -p tools/ffmpeg && ln -sf /opt/homebrew/opt/ffmpeg-full/bin/ffmpeg /opt/homebrew/opt/ffmpeg-full/bin/ffprobe tools/ffmpeg/`
7. Fonts and face model as in steps 6–7 above; `cp .env.example .env`
8. Run `./clipforge.sh`

## Layout

| Path | What |
| --- | --- |
| `clipforge.cmd` | launcher |
| `backend/clipforge` | FastAPI app, job queue, pipelines |
| `backend/tests` | tests (`backend\.venv\Scripts\python -m pytest` from `backend`) |
| `frontend` | React dashboard (`npm run build` after changing it) |
| `tools/ffmpeg` | FFmpeg and ffprobe (not in git) |
| `tools/models` | the YuNet face model (not in git) |
| `data` | database, logs, per-project files (not in git) |

## Rights

Only use footage you own or have permission to reuse. The app records the
rights status of every source and will refuse to publish clips from sources
whose rights are unverified. Crediting a creator is not permission. Editing a
clip does not make it fair use, and monetisation is never guaranteed.
