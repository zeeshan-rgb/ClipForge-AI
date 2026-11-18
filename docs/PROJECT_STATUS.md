# ClipForge AI — Project status

Updated 2026-11-17, after Phase 9. Detail for each phase is in
[ROADMAP.md](ROADMAP.md); feature items are in [TASKS.md](TASKS.md).

## Where it stands

The app takes a video file or a permitted YouTube or Twitch link, analyses
it, has an AI pick and score the best moments, and cuts each one into a clip
on whole sentences. Each clip is framed for 9:16: split screen when the
streamer has a facecam, otherwise full screen following the speaker's face.
It gets word-synced captions kept off faces, a PART label on connected parts,
an on-screen credit for someone else's video, and licensed background music.

Approved clips are rendered as finished 1080×1920 Shorts levelled to
YouTube's loudness. Each one gets AI-written title, description, hashtags and
pinned comment (credit lines added by code), and is checked on the rendered
file with a quality score. It can then be exported to a folder with its text
and rights record, or uploaded to the owner's channel as private and made
public only after an explicit, per-Short confirmation. The upload to a real
channel has not been tried yet: it needs the owner's Google Cloud client.

| Phase | What | State |
| --- | --- | --- |
| 1 | Foundation, dashboard, job queue, launcher | Done |
| 2 | Ingestion and rights record | Done (Kick: file only) |
| 3 | Sound and picture analysis, transcript | Done |
| 4 | AI moment detection and viral score | Built; owner's check open |
| 5 | Clip extraction with Smart Duration and part splitting | Done |
| 6 | Captions | Done |
| 7 | Music | Built; tested on synthetic music only |
| 8 | Vertical framing and rendering | Done (facecam tested on a composite, not a real stream) |
| 9 | Quality checks, titles and descriptions, export | Done |
| 10 | YouTube publishing | Built; refusal gate passed; private upload not done (owner chose to skip it; optional) |

## 2026-11-18 (macOS copy, owner Meeran)

Runs on macOS (Homebrew ffmpeg-full, Python 3.12, Antigravity CLI). Added: the
Permissions page (streamer requests from Gmail), the posting schedule (2 a day,
07:30 and 21:30 IST), the flash-forward hook, the finishing beat and story
checks. YouTube connected to "Peak Stream Moment"; no Short uploaded yet.
535 automated tests pass. See TASKS.md, "Shorts quality and growth".

**Merged into the Windows copy on 2026-11-18** (as one squashed change, so the
commit carries the Windows owner's identity). Reviewed before applying; the
new code has no hard-coded secrets and nothing was run from the other copy.
545 tests in all; the first full run had 5 failures, all in the posting
schedule, because Windows has no time-zone database. Fixed by adding the
`tzdata` package (Windows only in `requirements.txt`). Points the owner should
decide, found in review: the Permissions page sends email on its own by
default (`auto_send`) and fetches pages with a browser-like User-Agent, which
the architecture notes rule out; the posting schedule's automatic mode
publishes at set times on the owner's standing consent. The Gmail, YouTube and
schedule features do nothing until the owner connects those accounts.

## Next

Phase 11 (Creator Studio) is planned in [PHASE11_PLAN.md](PHASE11_PLAN.md)
and was approved on 2026-11-17, with every proposal adopted. Milestone 11.9
(the YouTube Shorts checklist) and 11.1 (smarter moment picking: new
categories, a hook score, measured sound and picture quality, a transcript
context check, flags, moments found from movement) are done. 11.2 (scene
understanding) is next; the rest is not started.

## Open items that need the owner

1. **Phase 4 check.** Watch the top ten picks on three of your own videos and
   judge whether at least seven are worth clipping. Nothing has yet run on
   real stream or gaming footage.
2. **Test videos** you are allowed to use, for that check and for the later
   phases. A real stream with a facecam would show how framing copes with
   overlays that are not clean rectangles.
3. **A few real music tracks** you are allowed to use (for example from the
   YouTube Audio Library), to test beat finding and mood matching on real
   songs. Phase 7 was tested on synthetic music only. Optional: Shorts can be
   made without music.
4. **Optional (skipped for now): the Phase 10 private upload.** Only needed
   if you want ClipForge to upload for you; otherwise export and upload
   yourself. Create a Google Cloud project,
   enable the YouTube Data API v3, set the OAuth consent screen to Testing
   with your account as a test user, create an OAuth client of type "Desktop
   app" and download its JSON. Then in ClipForge, Settings → YouTube channel:
   add the file and connect (you sign in on Google's page; ClipForge never
   sees your password). Then choose which Short to upload as private.

## How it has been verified

- 486 automated tests, using stand-ins for the platforms, the speech model,
  the AI and Google.
- 11.1 moment picking re-run with the real AI on Tears of Steel and the
  composed facecam video: new categories, flags, measured scores and
  moments found from movement all worked; picture quality was 10 everywhere
  on these clean films, so it is unproven on poor footage.
- Caption placement (2026-11-17): every gate Short re-rendered with captions
  at 54–66% down and lines no wider than 704 of the 740 px allowed. Frames
  checked against YouTube's control zones (right-hand buttons, title at the
  bottom): no overlap.
- The service refused to upload a real Short from a video with unconfirmed
  rights (HTTP 409, nothing sent).
- Quality checks proven on deliberately broken copies of a real Short: black
  bars, a missing sound track, a silent one and music louder than the speech
  were all caught; the original scored 100.
- End to end from one YouTube link (Tears of Steel) to an export folder of 7
  approved Shorts with their text, sidecars and rights record, using the real
  speech model and real AI. All 7 passed their checks (scores 92–100).
- Live runs with the real speech model and the real AI on: synthetic spoken
  test videos with known answers, and Tears of Steel (12 minutes of real
  dialogue, CC BY 3.0, Blender Foundation).
- Clip cuts checked by an independent script against the real word timings,
  under every duration preset, mode and splitting setting (204 clips on real
  speech, no problems).
- Captions checked frame by frame on the rendered previews: the highlight was
  on the word being said in 237 of 237 frames, and nothing showed during
  29 of 29 pauses.
- Music checked on the rendered files: 24 dB under the speech on every clip,
  the speech changed by at most 0.15 dB when music was added, and the
  unlicensed track was never used (synthetic tracks, spoken test video).
- Framing checked on a composed facecam "gaming" video and on Tears of Steel,
  by running the face model again on the rendered Shorts: the subject was in
  frame at 470 of 470 face samples, no caption covered a face in 302 frames,
  and no PART label touched one. Seven final Shorts at 1080×1920, −14.1 to
  −15.6 LUFS, peaks at −1.1 dBTP or lower.

## Known limits

- The AI that picks moments is `claude-sonnet-4-6` through the owner's
  Antigravity sign-in, because the Gemini tiers there returned empty replies
  or timed out. The Gemini API-key route is written but has never been run.
- The AI sees still frames, not moving video or sound. Each call carries about
  28,000 tokens of fixed overhead.
- No speaker identification; who is talking is inferred from timing.
- Framing follows faces only: the gameplay panel is a centred crop and does
  not follow the action; with two people too far apart the camera stays with
  one. Captions avoid faces, not the game's HUD. Real facecam overlays
  (rounded, transparent, keyed) are untested.
- The final render takes about 1 to 1.8 times the clip's length here.
- A face bigger than the frame (extreme close-up) is not detected, so captions
  can cover it, unnoticed by the checks.
- Titles and descriptions come from the transcript and the AI's reading of it
  and are not fact-checked; read them before publishing.
- No real music has been tested: beat finding and mood matching are measured
  on synthetic tracks only (beats within 6 ms on average, worst case 35 ms).
- About one caption word in nine on real film dialogue is flagged as one the
  speech model was unsure of; captions cannot yet be corrected by hand.
- Kick recordings cannot be downloaded; add them as files.
- YouTube allows about six uploads a day on a new Google Cloud project, and
  keeps videos uploaded through an unaudited project private; publish those
  yourself in YouTube Studio.
- This PC has no graphics card: speech-to-text runs at about ten times real
  time, so a three-hour stream takes roughly 20–40 minutes.
- This PC is short of memory: Windows' ceiling (RAM plus page file, 26 GB) was
  nearly all in use by other programs and C: had 0.17 GB free, so
  transcription of a 42-minute video failed with out-of-memory errors.
  ClipForge now splits and retries (and says plainly when there is no memory
  left), but closing programs and freeing space on C: still helps. Speech-to-text
  needs about 600 MB for a ten-minute chunk plus the model.
