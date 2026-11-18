"""Background music: the licensed track library, choosing a track for a clip, and where it sits under the speech.

Rules that hold throughout:

* A track can be used only when its licence details are complete (where it
  came from, which licence, a link or note proving it, and a credit line when
  the licence needs one). This is checked here, again when a track is picked,
  and again when a clip is rendered. A track without them is never chosen,
  automatically or by hand.
* The clip's own sound is never processed. Music is added to it, and is lowered
  under every spoken word using the word timings already known.
* Nothing here calls an AI. The mood comes from the moment's category.
"""

from __future__ import annotations

import math
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from .config import Settings
from .media import MediaError, run

AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".opus"}
MAX_TRACK_BYTES = 200 * 1024 * 1024
ANALYSE_SECONDS = 360  # tempo and beats are read from the first six minutes
BEAT_REGULARITY_MIN = 0.9  # a track whose beats wander more than this is not lined up on a beat
BEAT_HOP = 256  # analysis frame, in samples at 22.05 kHz
PULSE_MIN = 1.5  # onsets must be this much stronger on the beats than on average, or there is no beat
SNAP_BEFORE, SNAP_AFTER = 0.07, 0.04  # where around a detected beat to look for the real attack
SNAP_SHARPNESS = 8.0  # peak rise must be this many times the typical rise to count as an attack
TRACKER_LAG = 0.025  # how late the beat tracker's beats arrive (seconds), measured on known tracks

MOODS = ["comedic", "suspense", "dramatic", "energetic", "emotional", "intense"]
MOOD_LABEL = {
    "comedic": "Comedic", "suspense": "Suspense", "dramatic": "Dramatic",
    "energetic": "Energetic", "emotional": "Emotional", "intense": "Intense",
}
# what kind of music suits each kind of moment, best first
CATEGORY_MOODS: dict[str, list[str]] = {
    "funny": ["comedic"],
    "failure": ["comedic"],
    "shocking": ["dramatic"],
    "unexpected": ["dramatic", "comedic"],
    "horror": ["suspense"],
    "rage": ["intense"],
    "exciting": ["energetic"],
    "emotional": ["emotional"],
    "insane": ["energetic", "dramatic"],
    "suspense": ["suspense", "dramatic"],
    "celebration": ["energetic", "emotional"],
    "punchline": ["comedic"],
    "gameplay_outcome": ["energetic", "intense"],
}

# licence name -> does it require crediting the author
LICENCES: dict[str, bool] = {
    "CC0 / Public domain": False,
    "CC BY 4.0": True,
    "CC BY 3.0": True,
    "CC BY-SA 4.0": True,
    "YouTube Audio Library": False,
    "Purchased or royalty-free licence": False,
    "Other": False,
}

MUSIC_MODES = ("auto", "off", "always")

# mix settings
DUCK_DB = 10.0  # how far the music drops under speech, on top of its open level
DUCK_LEAD = 0.12  # start lowering this long before a word
DUCK_ATTACK = 0.10
DUCK_RELEASE = 0.45
DUCK_MERGE = 0.5  # words closer than this are one stretch of speech (no pumping between words)
MIN_GAP_DB = 12.0  # the speech must stay at least this far above the music while it is spoken
ENVELOPE_RATE = 8000
CONVERSATION_COVERAGE = 0.9  # "auto" skips music when speech fills at least this share of a clip
NO_SPEECH_BOOST_DB = 4.0  # with nobody talking the music may sit a little closer to the clip's level


# ---- licence -------------------------------------------------------------------------


def requires_attribution(licence_name: str | None) -> bool:
    name = (licence_name or "").strip()
    if name in LICENCES:
        return LICENCES[name]
    return bool(re.search(r"\bBY\b", name.upper()))


def licence_problems(
    source: str | None, licence_name: str | None, licence_url: str | None,
    licence_note: str | None, attribution: str | None,
) -> list[str]:
    """Everything still missing before this track may be used. Empty means it may."""
    problems: list[str] = []
    if not (source or "").strip():
        problems.append("Say where the track came from.")
    if not (licence_name or "").strip():
        problems.append("Name the licence.")
    url, note = (licence_url or "").strip(), (licence_note or "").strip()
    if not url and not note:
        problems.append("Add the licence link, or a note that shows you may use it (such as a receipt).")
    if url and not re.match(r"^https?://", url, re.I):
        problems.append("The licence link should start with http:// or https://.")
    if (licence_name or "").strip() and requires_attribution(licence_name) and not (attribution or "").strip():
        problems.append("This licence requires credit: add the credit line to use in descriptions.")
    return problems


def licence_warnings(licence_name: str | None, licence_note: str | None) -> list[str]:
    text = f"{licence_name or ''} {licence_note or ''}"
    if re.search(r"\bNC\b|non-?commercial", text, re.I):
        return ["Non-commercial licences usually do not allow use on a monetised channel."]
    return []


# ---- analysing a track ---------------------------------------------------------------


def audio_duration(settings: Settings, path: Path) -> float:
    if not settings.ffprobe:
        raise MediaError("ffprobe is not installed (expected in tools\\ffmpeg)")
    out = run([settings.ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
              timeout=60).stdout.strip()
    try:
        value = float(out)
    except ValueError as exc:
        raise MediaError("This file has no readable audio") from exc
    if value <= 0:
        raise MediaError("This file has no audio")
    return round(value, 3)


def snap_to_attacks(y: np.ndarray, sr: int, beats: np.ndarray) -> np.ndarray:
    """Move each detected beat onto the attack in the sound that it belongs to.

    Beat trackers find beats from spectral changes over a long window, which makes
    them arrive about 25 ms late (measured: +22 to +31 ms on four tracks with known
    beats). Where the sound has a clearly sharp attack near the detected time, the
    beat is moved onto it. Where it does not (a drone, soft pads), the nearest bump
    would be noise, so the beat only has the tracker's usual lag taken off.
    Both rules were checked on synthetic tracks only; real music is unmeasured.
    """
    win = max(1, int(0.004 * sr))
    env = np.convolve(np.abs(y), np.ones(win) / win, mode="same")
    slope = np.diff(env, prepend=env[0])
    out = []
    for b in beats:
        lo, hi = max(0, int((b - SNAP_BEFORE) * sr)), min(len(slope), int((b + SNAP_AFTER) * sr))
        seg = slope[lo:hi]
        if seg.size >= 8:
            k = int(np.argmax(seg))
            if seg[k] > SNAP_SHARPNESS * float(np.median(np.abs(seg))) + 1e-6:
                out.append((lo + k) / sr)
                continue
        out.append(max(0.0, float(b) - TRACKER_LAG))
    return np.array(out)


def audio_channels(settings: Settings, path: Path) -> int:
    out = run([settings.ffprobe, "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=channels",
               "-of", "csv=p=0", str(path)], timeout=60).stdout.strip()
    return int(out) if out.isdigit() and int(out) > 0 else 2


def rms_db(samples: np.ndarray) -> float:
    """RMS level in dBFS, ignoring near-silent stretches so a quiet intro does not skew it."""
    if samples.size == 0:
        return -80.0
    frame = 2048
    n = samples.size // frame
    if n == 0:
        active = samples
    else:
        frames = samples[: n * frame].reshape(n, frame)
        rms = np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1))
        active = frames[rms > 0.003]  # louder than about -50 dBFS
        if active.size == 0:
            return -80.0
    return float(max(-80.0, 20 * math.log10(math.sqrt(float(np.mean(active.astype(np.float64) ** 2))) + 1e-9)))


def analyse_track(settings: Settings, path: Path) -> dict[str, Any]:
    """Length, level, tempo and where the beats fall."""
    import librosa  # slow to import: only when a track is added
    import soundfile as sf

    duration = audio_duration(settings, path)
    settings.tmp_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=settings.tmp_dir) as tmp:
        wav = Path(tmp) / "a.wav"
        run([settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(path), "-vn", "-ac", "1",
             "-ar", "22050", "-t", str(ANALYSE_SECONDS), str(wav)], timeout=300)
        y, sr = sf.read(str(wav), dtype="float32")
    if y.size == 0:
        raise MediaError("This file has no audio")

    out: dict[str, Any] = {
        "duration": duration, "loudness_db": round(rms_db(y), 1), "channels": audio_channels(settings, path),
        "bpm": None, "beats": [], "phase": 0, "regularity": 0.0, "pulse": 0.0,
    }
    # a finer frame (11.6 ms) than the default (23 ms) so each beat lands closer to its real onset
    tempo, frames = librosa.beat.beat_track(y=y, sr=sr, hop_length=BEAT_HOP, units="frames")
    beats = librosa.frames_to_time(frames, sr=sr, hop_length=BEAT_HOP)
    env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=BEAT_HOP)
    if len(frames):
        at_beats = env[np.clip(frames, 0, len(env) - 1)]
        out["pulse"] = round(float(at_beats.mean() / (env.mean() + 1e-9)), 2)
    # A beat tracker always returns a beat grid, even for a drone. Only believe it where the
    # onsets really are stronger on the beats than between them.
    if len(beats) >= 8 and out["pulse"] >= PULSE_MIN:
        beats = snap_to_attacks(y, sr, beats)
        gaps = np.diff(beats)
        out["regularity"] = round(max(0.0, 1.0 - float(np.std(gaps) / np.mean(gaps))), 3)
        # the slope of beat number against time is exact; the tempo librosa reports is coarse (117.5 for 120)
        slope = float(np.polyfit(np.arange(len(beats)), beats, 1)[0])
        out["bpm"] = round(60.0 / slope, 1)
        strength = env[np.clip(frames, 0, len(env) - 1)]
        phases = [float(strength[p::4].mean()) if len(strength[p::4]) else 0.0 for p in range(4)]
        out["phase"] = int(np.argmax(phases))
        out["beats"] = [round(float(b), 3) for b in beats[:2000]]
    return out


YT_AUDIO_LIBRARY = {
    "source": "YouTube Audio Library (YouTube Studio → Audio Library), downloaded by the channel owner",
    "licence_name": "YouTube Audio Library licence",
    "licence_url": "https://support.google.com/youtube/answer/3376882",
}


def estimate_moods(analysis: dict[str, Any], name: str = "") -> list[str]:
    """A first guess at a track's moods from its tempo, beat strength and level. The owner can correct it.

    Measured only; no AI. Slow or beatless and quiet reads as suspense or emotional, a bouncy mid tempo as
    comedic, fast as energetic, fast and loud as intense.
    """
    bpm, pulse, level = analysis.get("bpm"), float(analysis.get("pulse") or 0), float(analysis.get("loudness_db") or -20)
    words = name.lower()
    hinted = [m for m, keys in {"comedic": ("funny", "comic", "silly", "quirky", "goofy", "bouncy"),
                                "suspense": ("suspense", "dark", "mystery", "tense", "horror", "creep"),
                                "emotional": ("sad", "piano", "emotional", "tender", "calm"),
                                "energetic": ("happy", "upbeat", "party", "dance", "summer"),
                                "intense": ("rock", "metal", "epic", "battle", "action"),
                                "dramatic": ("drama", "cinematic", "trailer")}.items()
              if any(k in words for k in keys)]
    if bpm is None or pulse < 1.15:
        guess = ["suspense", "emotional"] if level < -22 else ["dramatic", "emotional"]
    elif bpm < 85:
        guess = ["emotional", "suspense"] if level < -20 else ["dramatic", "emotional"]
    elif bpm < 112:
        guess = ["comedic", "dramatic"] if pulse >= 1.4 else ["dramatic", "emotional"]
    elif bpm < 135:
        guess = ["energetic", "comedic"]
    else:
        guess = ["intense", "energetic"] if level > -16 else ["energetic", "intense"]
    out = list(dict.fromkeys(hinted + guess))
    return out[:2]


# ---- choosing a track -----------------------------------------------------------------


@dataclass
class TrackInfo:
    id: int
    title: str
    file: str
    duration: float
    loudness_db: float
    moods: list[str]
    usable: bool
    problems: list[str] = field(default_factory=list)
    bpm: float | None = None
    beats: list[float] = field(default_factory=list)
    phase: int = 0
    regularity: float = 0.0
    licence_name: str | None = None
    source: str | None = None
    attribution: str | None = None
    channels: int = 2


def preferred_moods(category: str | None) -> list[str]:
    return CATEGORY_MOODS.get(category or "", [])


def is_plain_conversation(speech_coverage: float, has_loud_moment: bool) -> bool:
    """A clip that is almost wall-to-wall talking with nothing happening on top: music would only crowd it."""
    return speech_coverage >= CONVERSATION_COVERAGE and not has_loud_moment


def choose_track(
    tracks: list[TrackInfo], category: str | None, clip_len: float, uses: dict[int, int] | None = None
) -> tuple[TrackInfo | None, str]:
    """The best licensed track for this kind of moment, or None with the reason there is none."""
    uses = uses or {}
    moods = preferred_moods(category)
    if not moods:
        return None, f"No music style is defined for a “{category}” moment."
    if not tracks:
        return None, "The music library is empty."
    usable = [t for t in tracks if t.usable]
    if not usable:
        return None, "No track in the library has complete licence details, so none may be used."
    scored = []
    for t in usable:
        ranks = [i for i, m in enumerate(moods) if m in t.moods]
        if not ranks:
            continue
        score = 1.0 - 0.2 * min(ranks) + (0.05 if t.duration >= clip_len else 0.0) - 0.1 * uses.get(t.id, 0)
        scored.append((score, -t.id, t))
    if not scored:
        return None, f"No licensed track is tagged {' or '.join(moods)}, the style for a “{category}” moment."
    _, _, best = max(scored, key=lambda x: (x[0], x[1]))
    matched = next(m for m in moods if m in best.moods)
    return best, f"“{best.title}” is tagged {matched}, which suits a “{category}” moment."


# ---- where in the track the clip's music starts ---------------------------------------


def align_start(
    track: TrackInfo, clip_len: float, anchor: float | None
) -> tuple[float, dict[str, Any] | None, str | None]:
    """Offset into the track so that a beat falls exactly on the key moment of the clip.

    Returns (offset, beat info or None, why it could not be lined up or None). Bars (every
    fourth beat) are preferred; the earliest start that fits is used, so the track's own
    intro is kept where possible.
    """
    if anchor is None:
        return 0.0, None, "The key moment is not inside the clip, so the music starts at the beginning."
    if track.regularity < BEAT_REGULARITY_MIN or not track.beats:
        return 0.0, None, "The track has no steady beat to line up, so it starts at the beginning."
    if track.duration < clip_len + 0.05:
        return 0.0, None, "The track is shorter than the clip, so it plays from the beginning and repeats."
    downbeats = track.beats[track.phase::4]
    for label, beats in (("downbeat", downbeats), ("beat", track.beats)):
        for b in beats:
            offset = b - anchor
            if offset >= 0 and offset + clip_len <= track.duration:
                return round(offset, 3), {
                    "anchor": round(anchor, 3), "beat_time": round(b, 3), "kind": label, "error_ms": 0,
                }, None
    return 0.0, None, "No beat falls late enough in the track to line up with the key moment."


def plan_mix(
    track: TrackInfo, clip_len: float, anchor: float | None, speech_db: float | None,
    overall_db: float, gap_db: float, continue_from: float | None = None,
) -> dict[str, Any]:
    """Everything needed to mix this track under this clip."""
    beat = None
    beat_note = None
    loop = track.duration < clip_len + 0.05
    if continue_from is not None:
        # a later part of a connected moment: carry on from where the previous part's music ended
        offset = continue_from
        if offset + clip_len > track.duration + 0.01:
            loop = True
            offset = offset % track.duration
        beat_note = "Continues from the previous part, so no beat is lined up here."
    else:
        offset, beat, beat_note = align_start(track, clip_len, anchor)
    if speech_db is not None:
        open_db = speech_db - gap_db
        level_basis = "the speech"
    else:
        open_db = overall_db - gap_db + NO_SPEECH_BOOST_DB
        level_basis = "the clip's sound (nobody speaks in it)"
    gain_db = max(-40.0, min(30.0, open_db - track.loudness_db))
    return {
        "offset": round(offset, 3), "loop": loop, "gain_db": round(gain_db, 2),
        "fade_in": round(min(0.4, 0.2 * clip_len), 2), "fade_out": round(min(1.0, 0.25 * clip_len), 2),
        "open_db": round(open_db, 1), "level_basis": level_basis, "beat": beat, "beat_note": beat_note,
        "ends_at": round(offset + clip_len, 3),
    }


# ---- ducking ---------------------------------------------------------------------------


def merge_ranges(ranges: list[tuple[float, float]], gap: float) -> list[tuple[float, float]]:
    out: list[list[float]] = []
    for a, b in sorted(ranges):
        if out and a - out[-1][1] <= gap:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def duck_envelope(
    speech: list[tuple[float, float]], length: float, duck_db: float = DUCK_DB, rate: int = ENVELOPE_RATE
) -> np.ndarray:
    """Gain from 1 down to -duck_db over every stretch of speech, with a soft attack and release."""
    n = int(math.ceil((length + 1.0) * rate))
    t = np.arange(n, dtype=np.float32) / rate
    amount = np.zeros(n, dtype=np.float32)
    for a, b in merge_ranges(speech, DUCK_MERGE):
        a, b = a - DUCK_LEAD, b + 0.1
        rise = np.clip((t - (a - DUCK_ATTACK)) / DUCK_ATTACK, 0.0, 1.0)
        fall = np.clip(((b + DUCK_RELEASE) - t) / DUCK_RELEASE, 0.0, 1.0)
        amount = np.maximum(amount, np.minimum(rise, fall))
    gain_db = -duck_db * amount
    return (10.0 ** (gain_db / 20.0)).astype(np.float32)


def silence_gaps(speech: list[tuple[float, float]], length: float, min_len: float = 1.0, trim: float = 0.25) -> list[tuple[float, float]]:
    """Stretches of the clip where nobody speaks, trimmed so the soft edges of the ducking are left out."""
    out: list[tuple[float, float]] = []
    cursor = 0.0
    for a, b in merge_ranges(speech, 0.0):
        if a - cursor >= min_len:
            out.append((cursor + trim, a - trim))
        cursor = max(cursor, b)
    if length - cursor >= min_len:
        out.append((cursor + trim, length - trim))
    return [(a, b) for a, b in out if b - a > 0.2]


def write_envelope(path: Path, speech: list[tuple[float, float]], length: float) -> Path:
    """The ducking gain as a stereo WAV (FFmpeg multiplies the music by it)."""
    import soundfile as sf

    env = duck_envelope(speech, length)
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), np.stack([env, env], axis=1), ENVELOPE_RATE, subtype="FLOAT")
    return path


# ---- levels ----------------------------------------------------------------------------


def level_in_windows(wav: Path, windows: list[tuple[float, float]]) -> float | None:
    """RMS dBFS of a WAV file inside the given time windows, or None when they hold no sound."""
    import soundfile as sf

    chunks = []
    with sf.SoundFile(str(wav)) as f:
        sr = f.samplerate
        for a, b in windows:
            if b - a < 0.02:
                continue
            f.seek(max(0, int(a * sr)))
            data = f.read(int((b - a) * sr), dtype="float32", always_2d=True)
            if data.size:
                chunks.append(data.mean(axis=1))
    if not chunks:
        return None
    joined = np.concatenate(chunks)
    return float(max(-90.0, 20 * math.log10(math.sqrt(float(np.mean(joined.astype(np.float64) ** 2))) + 1e-9)))


def clip_windows(words: list[dict[str, Any]], segments: list[list[float]] | list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Where in the video the words of a clip sit (video time), limited to the clip's kept ranges."""
    out: list[tuple[float, float]] = []
    for a, b in segments:
        for w in words:
            s, e = max(float(w["s"]), a), min(float(w["e"]), b)
            if e > s:
                out.append((s, e))
    return sorted(out)


def to_clip_time(t: float, segments: list[list[float]] | list[tuple[float, float]]) -> float | None:
    """A time in the video as a time in the clip, or None when the clip does not contain it."""
    acc = 0.0
    for a, b in segments:
        if a <= t <= b:
            return round(acc + t - a, 3)
        acc += b - a
    return None
