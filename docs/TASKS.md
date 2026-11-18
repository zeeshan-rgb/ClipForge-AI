# ClipForge AI — Feature tasks

Requirements that were agreed as a feature, with what is built and what is
still to come. The phase plan is in [ROADMAP.md](ROADMAP.md).

## Phase 11 — Creator Studio

Requested 2026-11-17 and approved the same day with every proposal of
[PHASE11_PLAN.md](PHASE11_PLAN.md) section 2 adopted. Platform: YouTube
Shorts only.

`[x]` built and tested · `[~]` built in part · `[ ]` not built

- [x] 11.9 YouTube Shorts optimisation: checks for vertical size, duration (3
      minutes, plus the one-minute Content ID warning), encoding and sound,
      title and description, hashtags, rights, credit, music licence, kids
      declaration and channel. The checklist is enforced on upload and publish,
      metadata stays editable and can be sent to an uploaded video, and
      publishing still needs explicit confirmation. Thumbnail rules come with
      11.4.
- [~] 11.1 Advanced viral intelligence. Built and tested:
      - **Categories:** funny, unexpected, shocking, horror, rage, plus the
        new suspense, celebration (excitement), punchline and
        gameplay_outcome; emotional reactions.
      - **Moments without dialogue:** from loud sounds and movement bursts,
        confirmed by the picture.
      - **Scores:** reaction and gameplay flags from the picture check; a
        transcript context check (setup and payoff, unfinished sentence,
        unanswered question, conversation carrying on); duplicates by time
        and by words; hook, entertainment, emotional impact, context
        completeness, measured sound and picture quality, and an overall
        estimate with editable, transparent weights.

      Partly met:
      - facial expressions are judged on sampled still frames only (scene
        understanding is 11.2);
      - picture quality and the context check are unproven on poor footage;
      - nothing has run on real stream or game footage yet.
- [ ] 11.2 Scene understanding
- [ ] 11.3 Caption intelligence
- [ ] 11.4 Title, description and thumbnail studio
- [ ] 11.5 Manual clip editor
- [ ] 11.6 Creator brand kit
- [ ] 11.7 Batch processing
- [ ] 11.8 Performance feedback loop
- [ ] 11.10 Unified creator studio

## Shorts quality and growth (requested 2026-11-18)

From the owner's "end-to-end video quality" brief (Future RoadMap.txt) and their
request for 2 Shorts a day for US, UK, Australian and Indian viewers.
`[x]` built and tested · `[~]` built in part · `[ ]` not built

- [x] **Opening hook.** Project option `hook`: auto (default), story, cold_open.
      When the payoff comes 6 s or more into a clip, it opens with a 1–3.5 s
      whole-word flash-forward of the payoff line, then plays the story from
      its start in order (no line loses its setup). Single, complete clips only;
      Strict mode never goes over for it. `duration.cold_open`. Live on Tears of
      Steel: 1 of 5 clips got a teaser ("Move your asses!", 1.6 s, payoff 18.9 s
      in); the others kept story order. Frames checked on the rendered preview.
- [x] **Finishing beat.** A loud reaction starting within 0.8 s of the last word
      is kept for up to 1.2 s (never into the next words). `duration.finishing_beat`.
- [x] **Story checks in the quality report:** opening (first words after 1.5 s),
      dead air (over 30% in pauses over 1.5 s) and ending (Smart Duration's
      ending score under 60). Warnings only. `qc.story_checks`.
- [x] **Titles** are asked to lead with the most interesting words without
      giving away the punchline or promising what the clip does not show.
- [x] **Posting schedule** (`schedule.py`): Shorts uploaded private with
      YouTube's publish time; daily times in the owner's time zone; automatic
      mode fills the next 48 h of slots with approved Shorts. Owner's settings:
      07:30 and 21:30 IST (see README for why), category Entertainment.
- [x] **Permissions page** (`outreach.py`, `gmail.py`): streamer permission
      requests from the owner's Gmail, replies tracked, approval by the owner.
- [ ] Audio re-check of uncertain caption words and hand correction (11.3/11.5).
- [ ] Clip editor: start/end, zoom and framing adjustments, revisions (11.5).
- [ ] Gaming HUD safe regions; scene understanding (11.2).
- [x] **Story mode** (`story.py`, `api/story.py`, Clip editor → Story mode): the AI
      drafts 3–4 narration lines (hook, context, reaction, outro), story cards and up
      to 2 reaction stickers; the owner edits and records each line in the browser;
      recordings are cleaned (rumble cut, silence trimmed, levelled to −16 LUFS) and
      placed without overlapping. In the render the clip's own sound is lowered about
      10 dB under the narration (measured in a test: 0.2–0.45 of its level), the music
      ducks under it, the narration shows in a narrator box, cards and stickers are
      drawn by the caption engine (text only, nothing to license). New check
      "Your own narration" (warning under 4 s) in the quality report and the Shorts
      checklist (other people's footage only); automatic scheduling waits for at
      least 4 s of narration in the final render ("Wait for my narration", on).
      Live: the real AI drafted lines for a Tears of Steel clip in 17 s. Not done:
      a real microphone take rendered end to end (tested with generated tones).
- [x] **YouTube Audio Library folder import** (Settings → Music library): copies,
      analyses and records the Audio Library licence; first-guess moods from tempo,
      beat strength, level and title words (no AI). Tested on generated tracks only.
- [ ] The benchmark Short `Test_Video_Sapnap_08.mp4` was not supplied; nothing
      was measured against it. Word accuracy and ±100 ms timing targets need a
      manually verified transcript, which does not exist yet.

## Smart Duration and context-aware clip splitting

Requested 2026-11-15. **The core was built in Phase 5 (2026-11-15).** The
parts that depend on captions, framing, music and export are still open and
are marked with the phase they belong to. Principle: the chosen duration
guides the editor; it never forces a cut through a sentence, a punchline, a
reaction or an event.

`[x]` built and tested · `[~]` built in part, see the note · `[ ]` not built

### Where each part lands

| Part | Lands in | State |
| --- | --- | --- |
| Target duration, modes, ending detection, part splitting | Phase 5 | Built |
| Smart Duration panel on New Project, project settings, Settings | Phase 5 | Built |
| Caption start/end checks, part labels clear of captions | Phase 6 | Built |
| Part labels placed off faces, consistent layout across parts | Phase 8 | Built |
| Consistent music and audio levels across parts | Phase 7 (music), Phase 8 (levels) | Built (music tested on synthetic tracks only) |
| Per-part titles and hashtags | Phase 9 | Built |
| Smart Duration panel in the Clip Editor, manual start/end adjustment | Phases 6–8 | Not built |

The engine is `backend/clipforge/duration.py`. It takes a picked moment plus
the word timings, silences, scene changes and loud moments, and returns
boundaries with a validation report. It makes no AI call and cuts no video;
it is never a plain time cut-off in FFmpeg.

### Tasks

Duration choice
- [x] Presets: 15, 20, 30, 35, 45, 60 and 90 seconds, plus a custom value
      (10 to 180 seconds).
- [x] The old shortest/longest pair is replaced by a target and a mode;
      projects saved with the old pair still open (the longest becomes the
      target).

Modes
- [x] **Strict:** never longer than the target. Setup is trimmed from the
      start if that makes a complete clip fit; otherwise it reports that no
      valid clip was found, with the shortest complete length.
- [x] **Smart (default):** the target is an aim with a tolerance (default
      10%, so 35 s gives about 32–39 s). Prefers natural endings.
- [x] **Flexible:** the target is a preference; a clip may run to 1.6 times
      the target to keep the whole moment, and states why.

Ending detection, checked before a clip is final
- [x] The speaker finished the sentence. A full stop, question mark or
      exclamation mark ends one; so does two seconds of silence after a word
      with no comma, because speech-to-text sometimes omits the full stop.
- [x] A question got its answer: a clip never ends on a question when a reply
      follows within 2.5 seconds.
- [~] A joke reached its punchline. The AI marks where the payoff is when it
      picks the moment; the engine never ends before that line and never
      splits right before it. The engine itself does not judge humour.
- [~] A reaction finished. The engine carries on to lines that follow the
      payoff without a pause, and will not end while a sudden loud moment is
      under way. It cannot hear laughter or see a face.
- [~] No important gameplay event is still in progress. Judged from sudden
      loud moments only, not from what is on screen.
- [~] Another speaker is not about to respond. Judged from word timings
      (the next words start with no pause, or overlap the last ones). There
      is no speaker identification.
- [x] The conversation reached a natural stopping point: a clip ends early
      instead of padding itself with a line from after a pause.
- [x] If the target ends mid-sentence: extend when the mode allows;
      otherwise move the start; otherwise refuse with a reason. A clip that
      would drop more than half of the moment is refused, not shortened.

Part splitting (Part 1, Part 2, …)
- [x] Option to split a long moment into connected parts, with a target
      duration and a maximum number of parts (2 to 5). The number of parts is
      chosen automatically. With splitting on, the AI is asked for moments
      long enough to be worth splitting.
- [x] Splits fall only where a sentence has finished: never mid-sentence,
      never on a question whose answer follows, never while a loud moment is
      under way, never right before the key line, and never before a short
      afterthought to the previous line.
- [x] A final part too short to stand alone is not produced; the lines join
      the part before.
- [x] Part numbering; no repeated dialogue, no missing dialogue, no overlap:
      parts are consecutive runs of whole sentences.
- [~] Same caption style across parts: the style is set once per project,
      so every clip and every part uses it (Phase 6).
- [x] Same music across parts: parts of one moment share one track and the
      music carries on from where the previous part's ended; the same gap
      under the speech is used for every part (Phase 7).
- [x] Same layout and final audio levels across parts (Phase 8). The facecam
      is found once for the whole video, so every part gets the same split;
      a layout chosen by hand applies to every part of the moment; every
      final part is levelled to the same loudness (−14 LUFS).
- [~] Optional on-screen PART 1/2 label (project setting, on by default),
      shown for the first 2.5 s and placed where it touches no face and no
      caption (Phase 8). It avoids faces, not gameplay as such: in split
      screen it sits over the streamer panel's top edge or the game when
      that is where no face is.
- [x] Titles and hashtags per part (Phase 9): each part gets its own title
      with "(Part n/m)" added by code, its own hashtags and pinned comment,
      and a "Part n of m" line in the description.

Caption and video sync (Phase 6)
- [x] Engine side: the clip always runs past the end of its last word and
      starts before its first, so a caption for either can be shown in full;
      a word timed past the end of the video is flagged.
- [x] First caption starts at the right time (checked per clip, to one frame).
- [x] No caption ends before the speaker or lingers more than 0.3 s after
      the dialogue; a line gives way to the next one instead of overlapping.
- [x] The last caption is never cut off by the end of the clip.
- [x] Captions never overlap the part label: the label is placed only where
      it misses the caption band (Phase 8), and captions stay out of the top
      18% of the frame.

Pre-export validation
- [x] Report per clip: requested duration, actual duration, first spoken
      word, last spoken word, sentence completion, question answered, event
      completion, speakers overlapping at the end, ending quality score, and
      for parts whether each continues cleanly from the one before.
- [x] A clip that fails is marked "did not pass" and cannot be approved
      without an explicit "Approve anyway".
- [x] The final Short is checked again on the rendered file (Phase 9), and a
      clip approved anyway is shown as a warning in its quality report.

Interface
- [x] Smart Duration panel on New Project, in a project's Clip settings and
      in Settings (defaults): duration selector, custom input, Strict / Smart
      / Flexible, tolerance, part splitting, maximum parts, context before
      the moment, dead-air removal.
- [x] Each clip shows target against actual length with the reason for any
      difference, a preview, and Approve / Reject.
- [~] "Adjust the result" is done by changing the settings, which re-cuts at
      once without the AI. Dragging a clip's own start or end is not built.
- [ ] The Smart Duration panel inside the Clip Editor. The Clip Editor holds
      framing, caption style, music and the final render; duration is still
      set in the project's Clip settings.

Test cases (automated, in `backend/tests/test_duration.py`)
- [x] 1. Target 35 s, sentence finishes at 37 s.
- [x] 2. Target 30 s, punchline at 34 s.
- [x] 3. Target 45 s, reaction ends at 42 s.
- [x] 4. Target 60 s, gameplay event continues past 60 s.
- [x] 5. A 105 s moment split into connected parts.
- [x] 6. A speaker pauses before finishing a sentence.
- [x] 7. Several speakers talking at once.
- [x] 8. A caption overlapping the end of the video: the clip never runs
      past the video, the last word is fully inside, and the last caption's
      hold is shortened so it ends with the clip (`test_captions.py`).
- [x] 9. A natural stopping point before the target.
- [x] 10. No valid standalone moment fits Strict mode.

Also tested: a randomised check over many generated transcripts that no valid
clip ever cuts a word or exceeds its mode's limit, and a live run of 87 clips
under 30 setting combinations checked against real word timings.

### Known limits

- No speaker identification, so anything about "who is speaking" is inferred
  from timing.
- Checked on one synthetic spoken video. Not yet run on real stream footage.
- In Smart mode a part can exceed the tolerance when a moment needs more
  parts than the maximum allows. The clip says so in its reasons.
