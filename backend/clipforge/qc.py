"""Quality checks on a finished Short, measured on the file itself.

Every check reads the rendered file (or, for the clip's own sound, compares
the file against the source), so a fault anywhere before it — a bad crop, a
lost sound track, music mixed too loud — is caught here whatever caused it.

Each check ends as ok, warn or fail. A fail stops the Short being exported
unless the owner exports it anyway; warnings are shown but do not stop it.
The quality score is 100, less 30 for each fail and 8 for each warning.
"""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .config import Settings
from .media import MediaError, MixSpec, _audio_graph

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

FAIL_COST, WARN_COST = 30, 8
BAR_SHARE = 0.02  # black edge thicker than this share of the frame counts as a bar
BLACK_FAIL, BLACK_WARN = 1.0, 0.25  # seconds of black picture
FREEZE_FAIL, FREEZE_WARN = 5.0, 2.0  # seconds of a still picture
SILENCE_FAIL, SILENCE_WARN = 4.0, 2.0  # seconds of silence
SILENT_DB = -50.0  # mean level below this: the sound track is effectively empty
PEAK_FAIL, PEAK_WARN = -0.5, -1.0  # dBTP
MUSIC_GAP_MIN = 12.0  # dB the music must sit under the speech
DUPLICATE_SHARE = 0.6
LOW_CONFIDENCE_SHARE = 0.15

LABELS = {
    "format": "1080×1920, H.264 picture and AAC sound",
    "length": "Length matches the clip",
    "black_bars": "No black bars",
    "black_frames": "No black frames",
    "frozen": "No frozen picture",
    "sound_present": "Has a sound track",
    "sound_level": "Sound is not silent",
    "peaks": "No clipping (true peak)",
    "loudness": "Loudness about −14 LUFS",
    "music_under_speech": "Music well under the speech",
    "long_silence": "No long silence",
    "captions": "Caption checks passed",
    "caption_confidence": "Captions heard clearly",
    "cut_words": "Starts and ends on whole sentences",
    "duplicate": "Not a repeat of another clip",
    "opening": "Strong opening (words in the first seconds)",
    "ending": "Ends naturally",
    "dead_air": "Little dead air",
    "original": "Your own narration (original commentary)",
    "rights": "Rights allow publishing",
}


@dataclass
class Check:
    name: str
    status: str  # ok | warn | fail
    detail: str

    @property
    def label(self) -> str:
        return LABELS.get(self.name, self.name)


def ok(name: str, detail: str) -> Check:
    return Check(name, "ok", detail)


def warn(name: str, detail: str) -> Check:
    return Check(name, "warn", detail)


def fail(name: str, detail: str) -> Check:
    return Check(name, "fail", detail)


def summarise(checks: list[Check]) -> dict[str, Any]:
    fails = [c for c in checks if c.status == "fail"]
    warns = [c for c in checks if c.status == "warn"]
    return {
        "score": max(0, 100 - FAIL_COST * len(fails) - WARN_COST * len(warns)),
        "passed": not fails,
        "fails": len(fails),
        "warnings": len(warns),
        "checks": [{**asdict(c), "label": c.label} for c in checks],
    }


# ---- reading the file ---------------------------------------------------------------------


@dataclass
class Scan:
    crop: tuple[int, int, int, int] | None  # w, h, x, y of the area that is not black, in the scanned size
    scanned: tuple[int, int]
    black: list[tuple[float, float]]
    frozen: list[tuple[float, float]]
    silence: list[tuple[float, float]]
    mean_db: float | None
    max_db: float | None


def scan_file(settings: Settings, path: Path, duration: float, has_audio: bool) -> Scan:
    """One pass over the file: black edges, black and frozen stretches, silences and levels."""
    if not settings.ffmpeg:
        raise MediaError("ffmpeg is not installed (expected in tools\\ffmpeg)")
    w, h = 270, 480
    vf = (f"scale={w}:{h},cropdetect=limit=0.08:round=2:reset=0,"
          "blackdetect=d=0.2:pic_th=0.98:pix_th=0.08,freezedetect=n=0.002:d=1.5")
    cmd = [settings.ffmpeg, "-hide_banner", "-nostats", "-i", str(path), "-vf", vf]
    if has_audio:
        cmd += ["-af", "silencedetect=n=-50dB:d=1.5,volumedetect"]
    cmd += ["-f", "null", "-"]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900,
                          creationflags=_NO_WINDOW)
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-3:]
        raise MediaError(f"The file could not be checked: {' | '.join(tail) or 'no output'}")
    text = proc.stderr or ""
    crops = re.findall(r"crop=(\d+):(\d+):(\d+):(\d+)", text)
    black = [(float(a), float(b)) for a, b in re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", text)]
    starts = [float(x) for x in re.findall(r"freeze_start: ([\d.]+)", text)]
    ends = [float(x) for x in re.findall(r"freeze_end: ([\d.]+)", text)]
    frozen = [(a, ends[i] if i < len(ends) else duration) for i, a in enumerate(starts)]
    s_starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", text)]
    s_ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", text)]
    silence = [(max(a, 0.0), s_ends[i] if i < len(s_ends) else duration) for i, a in enumerate(s_starts)]
    mean = re.search(r"mean_volume: (-?[\d.]+|-inf) dB", text)
    peak = re.search(r"max_volume: (-?[\d.]+|-inf) dB", text)

    def db(m) -> float | None:
        if not m:
            return None
        return -120.0 if m.group(1) == "-inf" else float(m.group(1))

    return Scan(tuple(int(v) for v in crops[-1]) if crops else None, (w, h), black, frozen, silence,
                db(mean), db(peak))


def _decode(settings: Settings, cmd_tail: list[str]) -> np.ndarray:
    raw = subprocess.run([settings.ffmpeg, "-hide_banner", "-loglevel", "error", *cmd_tail,
                          "-ar", "48000", "-f", "f32le", "-"],
                         capture_output=True, check=True, creationflags=_NO_WINDOW, timeout=600).stdout
    return np.frombuffer(raw, dtype=np.float32)


def music_gap(
    settings: Settings, final: Path, src: Path, ranges: list[tuple[float, float]], speech: list[tuple[float, float]],
    gain_db: float, audio_channels: int,
) -> dict[str, Any] | None:
    """How far under the speech everything that is not the clip's own sound sits, measured on the file.

    The clip's own sound reaches the Short unchanged apart from one gain, so
    taking it (with that gain) away from the file's sound leaves the music
    and any other change. Its level while someone speaks, against the speech,
    is the gap. It also shows whether the speech itself was altered: then the
    remainder is loud even with no music.
    """
    if not speech:
        return None
    total = sum(b - a for a, b in ranges)
    chains, out = _audio_graph(ranges, True, None, audio_channels, 0, 1, total, gain_db, None)
    chains.append(f"{out}pan=mono|c0=c0[m]")
    ref = _decode(settings, ["-i", str(src), "-filter_complex", ";".join(chains), "-map", "[m]",
                             "-t", f"{total:.3f}"])
    got = _decode(settings, ["-i", str(final), "-vn", "-af", "pan=mono|c0=c0"])
    if got.size == 0 or ref.size == 0:
        return None
    # line the two up (encoders can shift sound by a few samples)
    n = min(len(ref), len(got), 48000 * 6)
    best, lag = -1.0, 0
    a = ref[:n].astype(np.float64)
    for shift in range(-480, 481, 4):
        b = np.roll(got[:n].astype(np.float64), -shift)
        c = float(np.dot(a, b))
        if c > best:
            best, lag = c, shift
    for fine in range(lag - 4, lag + 5):
        b = np.roll(got[:n].astype(np.float64), -fine)
        c = float(np.dot(a, b))
        if c > best:
            best, lag = c, fine
    got = np.roll(got, -lag)
    m = min(len(ref), len(got))
    rest = got[:m] - ref[:m]

    def level(x: np.ndarray, windows: list[tuple[float, float]]) -> float | None:
        parts = [x[int(s * 48000):int(e * 48000)] for s, e in windows if e - s > 0.05]
        parts = [p for p in parts if p.size]
        if not parts:
            return None
        joined = np.concatenate(parts).astype(np.float64)
        return float(20 * np.log10(np.sqrt(np.mean(joined ** 2)) + 1e-9))

    speech_db = level(ref[:m], speech)
    rest_db = level(rest, speech)
    if speech_db is None or rest_db is None:
        return None
    return {"speech_db": round(speech_db, 1), "music_in_speech_db": round(rest_db, 1),
            "gap_db": round(speech_db - rest_db, 1), "lag_samples": lag}


# ---- the checks ---------------------------------------------------------------------------------


def words_of(captions: dict | None) -> list[str]:
    return [w["w"].lower().strip(".,!?\"'") for ln in (captions or {}).get("lines") or [] for w in ln["words"]]


def similarity(a: list[str], b: list[str]) -> float:
    if not a or not b:
        return 0.0
    sa, sb = set(a), set(b)
    return len(sa & sb) / len(sa | sb)


OPENING_LATE = 1.5  # first words later than this into a Short: a weak opening (viewers decide in the first seconds)
ENDING_WEAK = 60  # Smart Duration's ending score under this: the end may feel abrupt
DEAD_AIR_SHARE = 0.3  # more than this share of the Short without speech, in gaps over 1.5 s
DEAD_AIR_GAP = 1.5


def story_checks(report: dict | None, speech: list[tuple[float, float]] | None, length: float) -> list[Check]:
    """How the Short opens and ends, and how much of it is dead air. Warnings only: the owner decides.

    Measured from the word timings inside the Short and Smart Duration's own
    boundary scores; a talking-free moment (no speech at all) is not judged here.
    """
    out: list[Check] = []
    report = report or {}
    words = sorted(speech or [])
    hook = report.get("hook") or {}
    if words:
        first = words[0][0]
        if hook.get("strategy") == "cold_open":
            out.append(ok("opening", f"Opens with a {hook.get('seconds', 0):.1f} s flash-forward of the payoff."))
        elif first > OPENING_LATE:
            out.append(warn("opening", f"Weak opening: the first words come {first:.1f} s in. Viewers decide in the "
                                       "first seconds; consider the cold-open hook or a tighter start."))
        else:
            out.append(ok("opening", f"Speech starts {first:.1f} s in."))
        quiet, cursor = 0.0, 0.0
        for s, e in words:
            if s - cursor > DEAD_AIR_GAP:
                quiet += s - cursor
            cursor = max(cursor, e)
        if length - cursor > DEAD_AIR_GAP + (report.get("finishing_beat") or 0):
            quiet += length - cursor - (report.get("finishing_beat") or 0)
        share = quiet / length if length else 0.0
        if share > DEAD_AIR_SHARE:
            out.append(warn("dead_air", f"{share:.0%} of the Short has no speech in pauses over {DEAD_AIR_GAP:.1f} s."))
        else:
            out.append(ok("dead_air", f"{share:.0%} of the Short is pauses over {DEAD_AIR_GAP:.1f} s."))
    eq = report.get("ending_quality")
    if eq is not None:
        if eq < ENDING_WEAK:
            keys = ("end", "question", "loud", "talking", "follow", "trails", "payoff", "pause")
            problems = "; ".join(p for p in report.get("problems") or [] if any(k in p for k in keys)) \
                or "the last line is not a clean stop"
            out.append(warn("ending", f"The ending may feel abrupt ({eq}/100): {problems}."))
        else:
            beat = report.get("finishing_beat")
            out.append(ok("ending", f"Ends on a finished line ({eq}/100)"
                                    + (f", keeping the {beat:.1f} s reaction after it." if beat else ".")))
    return out


ORIGINAL_MIN_SECONDS = 4.0


def contribution_check(story: dict | None) -> Check:
    """Is there enough of the owner's own narration? YouTube's reused-content policy looks for original commentary.

    A warning, never a fail: the owner may post their own footage without narration. Whether a channel is
    monetised is YouTube's decision; this only reports what the Short contains.
    """
    story = story or {}
    secs, lines = float(story.get("seconds") or 0), int(story.get("lines") or 0)
    extras = [f"{story.get('cards')} card(s)" if story.get("cards") else "",
              f"{story.get('stickers')} sticker(s)" if story.get("stickers") else ""]
    extra = ", ".join(e for e in extras if e)
    if secs >= ORIGINAL_MIN_SECONDS:
        return ok("original", f"{secs:.0f} s of your narration in {lines} line(s)" + (f", plus {extra}" if extra else "")
                  + ".")
    if secs > 0:
        return warn("original", f"Only {secs:.1f} s of your narration. Add a context or reaction line: "
                                "YouTube's reused-content policy looks for original commentary.")
    return warn("original", "Reused-content risk: no narration of yours. Record the Story lines (Clip editor) so the "
                            "Short carries your own commentary" + (f"; cards and stickers alone ({extra}) are not enough."
                                                                   if extra else "."))


def run_checks(
    *, info: dict[str, Any], scan: Scan, clip_duration: float, out_size: tuple[int, int],
    loudness: dict[str, Any] | None, loudness_ok: tuple[bool, str] | None, music_used: bool,
    gap: dict[str, Any] | None, expect_sound: bool, captions: dict | None, report: dict | None, valid: bool,
    overridden: bool, others: list[list[str]], rights_status: str,
    speech: list[tuple[float, float]] | None = None, story: dict | None = None,
) -> dict[str, Any]:
    checks: list[Check] = []
    W, H = out_size

    # picture and container
    good = (info.get("width"), info.get("height")) == (W, H) and info.get("video_codec") == "h264"
    sound_codec = info.get("audio_codec")
    good = good and sound_codec in ("aac", None)
    checks.append((ok if good else fail)("format", f"{info.get('width')}×{info.get('height')}, "
                                                    f"{info.get('video_codec')} / {sound_codec or 'no sound'}"))
    length = float(info.get("duration") or 0)
    checks.append((ok if abs(length - clip_duration) <= 0.15 else fail)(
        "length", f"{length:.2f} s for a {clip_duration:.2f} s clip"))

    if scan.crop:
        cw, ch, cx, cy = scan.crop
        sw, sh = scan.scanned
        sides = {"left": cx / sw, "right": (sw - cx - cw) / sw, "top": cy / sh, "bottom": (sh - cy - ch) / sh}
        bars = {k: v for k, v in sides.items() if v >= BAR_SHARE}
        if bars:
            checks.append(fail("black_bars", "Black edge on the " + ", ".join(
                f"{k} ({v:.0%} of the frame)" for k, v in bars.items()) + " through the whole Short."))
        else:
            checks.append(ok("black_bars", "The picture reaches every edge."))
    else:
        checks.append(warn("black_bars", "The edges could not be measured."))

    black = sum(b - a for a, b in scan.black)
    where = ", ".join(f"{a:.1f}–{b:.1f} s" for a, b in scan.black[:3])
    if black >= BLACK_FAIL:
        checks.append(fail("black_frames", f"{black:.1f} s of black picture ({where})."))
    elif black >= BLACK_WARN:
        checks.append(warn("black_frames", f"{black:.1f} s of black picture ({where})."))
    else:
        checks.append(ok("black_frames", "No black stretch."))

    longest = max((b - a for a, b in scan.frozen), default=0.0)
    if longest >= FREEZE_FAIL:
        checks.append(fail("frozen", f"The picture stands still for {longest:.1f} s."))
    elif longest >= FREEZE_WARN:
        checks.append(warn("frozen", f"The picture stands still for {longest:.1f} s; fine for a menu or a "
                                     "still shot, otherwise look at it."))
    else:
        checks.append(ok("frozen", "The picture keeps moving."))

    # sound
    if not info.get("has_audio"):
        checks.append((fail if expect_sound else warn)(
            "sound_present", "The file has no sound track." + ("" if expect_sound else " The source had none either.")))
    else:
        checks.append(ok("sound_present", f"{sound_codec}, {info.get('audio_channels') or 2} channels."))
        mean = scan.mean_db
        if mean is None or mean < SILENT_DB:
            checks.append(fail("sound_level", f"The sound track is silent (average {mean if mean is not None else '—'} dB)."))
        else:
            checks.append(ok("sound_level", f"Average {mean:.1f} dB."))
        tp = (loudness or {}).get("true_peak")
        if tp is None:
            checks.append(warn("peaks", "The true peak could not be measured."))
        elif tp > PEAK_FAIL:
            checks.append(fail("peaks", f"True peak {tp:.1f} dBTP: the sound will distort."))
        elif tp > PEAK_WARN:
            checks.append(warn("peaks", f"True peak {tp:.1f} dBTP, just over −1."))
        else:
            checks.append(ok("peaks", f"True peak {tp:.1f} dBTP."))
        if loudness_ok is not None:
            checks.append((ok if loudness_ok[0] else warn)("loudness", loudness_ok[1]))
        if music_used:
            if gap is None:
                checks.append(warn("music_under_speech", "Nobody speaks in this clip, so there is nothing to compare."))
            elif gap["gap_db"] < MUSIC_GAP_MIN:
                level = (f"only {gap['gap_db']:.0f} dB under the speech" if gap["gap_db"] >= 0.5
                         else f"{-gap['gap_db']:.0f} dB louder than the speech" if gap["gap_db"] <= -0.5
                         else "as loud as the speech")
                checks.append(fail("music_under_speech", f"The music is {level} "
                                                         f"(at least {MUSIC_GAP_MIN:.0f} dB under it is needed)."))
            else:
                checks.append(ok("music_under_speech", f"{gap['gap_db']:.0f} dB under the speech, measured on the file."))
        elif gap is not None and gap["gap_db"] < MUSIC_GAP_MIN:
            checks.append(fail("music_under_speech", f"No music was added, yet the sound differs from the clip's own "
                                                     f"by more than it should ({gap['gap_db']:.0f} dB)."))
        longest_quiet = max((b - a for a, b in scan.silence), default=0.0)
        if longest_quiet >= SILENCE_FAIL:
            checks.append(fail("long_silence", f"{longest_quiet:.1f} s of silence."))
        elif longest_quiet >= SILENCE_WARN:
            checks.append(warn("long_silence", f"{longest_quiet:.1f} s of silence."))
        else:
            checks.append(ok("long_silence", "No silence longer than 2 s."))

    # captions and cut
    if captions:
        failed = [c["name"] for c in captions.get("checks") or [] if not c.get("ok")]
        if failed:
            checks.append(warn("captions", "Did not pass: " + ", ".join(failed) + "."))
        elif (captions.get("face_share") or 0) > 0:
            checks.append(warn("captions", f"Captions cross a face in {captions['face_share']:.0%} of the clip; "
                                           "no height was fully clear."))
        else:
            checks.append(ok("captions", "Timed to the words, inside the safe area and off faces."))
        count = captions.get("word_count") or 0
        flagged = len(captions.get("flagged_words") or [])
        if count and flagged / count > LOW_CONFIDENCE_SHARE:
            checks.append(warn("caption_confidence", f"{flagged} of {count} words were unclear to the speech model; "
                                                     "listen before publishing."))
        else:
            checks.append(ok("caption_confidence", f"{flagged} of {count} words unclear." if count else "No speech."))
    if valid:
        checks.append(ok("cut_words", "Starts at the beginning of a sentence and ends after a finished one."))
    else:
        why = (report or {}).get("no_fit") or "; ".join((report or {}).get("problems") or []) or "it did not pass its checks"
        checks.append((warn if overridden else fail)("cut_words", ("Approved anyway: " if overridden else "") + why))

    checks += story_checks(report, speech, clip_duration)
    if story is not None:  # only when the render reports it (direct file checks do not know)
        checks.append(contribution_check(story))

    mine = words_of(captions)
    worst = max((similarity(mine, o) for o in others), default=0.0)
    if worst >= DUPLICATE_SHARE:
        checks.append(warn("duplicate", f"{worst:.0%} of its words are shared with another clip of this video."))
    else:
        checks.append(ok("duplicate", "Different from the other clips."))

    if rights_status == "unverified":
        checks.append(warn("rights", "The rights to this video are not confirmed: it can be exported but not "
                                     "published until you record ownership or permission."))
    else:
        checks.append(ok("rights", {"own": "You own this video.", "licensed": "Licensed.",
                                    "creator_permission": "The creator gave permission."}.get(rights_status, rights_status)))
    return summarise(checks)
