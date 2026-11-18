"""Story mode: the owner's narration, story cards and meme stickers on a Short.

Why: YouTube's reused-content policy accepts other people's footage when it
carries "significant original commentary" or an added storyline, and refuses
clips edited together "with little or no narrative". A streamer's permission
does not change that. Story mode makes each Short the owner's own telling of
the moment:

* the AI drafts 3 or 4 short lines (hook, context, reaction, outro) from what
  is said in the clip and why it was picked; the owner edits them;
* the owner records each line in the dashboard; ClipForge cleans the recording
  (trims silence, levels it) and places it on the clip;
* while the owner speaks, the clip's own sound is lowered and the music ducks;
  their words are shown in a narrator box, apart from the streamer's captions;
* story cards ("10 minutes earlier…") and reaction stickers drawn by ClipForge
  itself as styled text (nothing to license) can be added at chosen moments.

Synthetic voices are not used: the published narration is the owner's voice.
Nothing here decides monetisation; YouTube reviews the channel.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import subprocess
from pathlib import Path
from typing import Any, Callable

import numpy as np

from .config import Settings
from .media import MediaError, VoiceSpec, probe_media, run
from .models import iso, utcnow

log = logging.getLogger("clipforge.story")

ROLES = ("hook", "context", "reaction", "outro")
WORDS_PER_SECOND = 2.6  # a brisk narrator, for placing lines before they are recorded
DUCK_TO = 0.3  # the clip's own sound while the owner speaks (about −10 dB)
DUCK_RAMP = 0.12
LINE_GAP = 0.2
MIN_CONTRIBUTION_SECONDS = 4.0
NARRATION_LUFS = -16.0
MAX_LINES, MAX_CARDS, MAX_STICKERS = 5, 3, 2
SR = 48000

# Stickers drawn by ClipForge: short reaction phrases in bold sticker styles.
STICKERS: dict[str, dict[str, Any]] = {
    "no_way": {"text": "NO WAY", "bg": "#FF2E88", "fg": "#FFFFFF", "moods": ["shocking", "unexpected", "insane"]},
    "wait_for_it": {"text": "WAIT FOR IT…", "bg": "#FFE11A", "fg": "#111111", "moods": ["suspense", "funny"]},
    "plot_twist": {"text": "PLOT TWIST", "bg": "#7B2FF7", "fg": "#FFFFFF", "moods": ["unexpected", "shocking"]},
    "chat_real": {"text": "CHAT, IS THIS REAL?", "bg": "#00C2FF", "fg": "#0B0F2A", "moods": ["insane", "shocking"]},
    "bruh": {"text": "BRUH", "bg": "#FF8A00", "fg": "#FFFFFF", "moods": ["failure", "funny"]},
    "it_happened": {"text": "IT HAPPENED", "bg": "#2BD576", "fg": "#0B0F2A", "moods": ["celebration", "exciting"]},
    "dead": {"text": "I'M DEAD", "bg": "#FFFFFF", "fg": "#FF2E88", "moods": ["funny", "punchline"]},
    "rage": {"text": "RAGE MODE", "bg": "#E5212E", "fg": "#FFFFFF", "moods": ["rage"]},
    "clutch": {"text": "CLUTCH!", "bg": "#FFE11A", "fg": "#E5212E", "moods": ["exciting", "gameplay_outcome"]},
    "mood": {"text": "MOOD.", "bg": "#0B0F2A", "fg": "#FFE11A", "moods": ["emotional", "funny"]},
    "nope": {"text": "NOPE.", "bg": "#111111", "fg": "#FFFFFF", "moods": ["horror", "failure"]},
    "legend": {"text": "LEGENDARY", "bg": "#FFB000", "fg": "#0B0F2A", "moods": ["celebration", "insane"]},
}

# where things sit on the 1080x1920 frame (share of the height)
NARRATOR_Y = 0.30
CARD_Y = 0.15
STICKER_Y = 0.45
STICKER_MAX_H = 0.20
STICKER_MAX_W = 0.62


def empty() -> dict[str, Any]:
    return {"lines": [], "cards": [], "stickers": [], "drafted_at": None}


def normalise(story: dict | None) -> dict[str, Any]:
    out = empty()
    if isinstance(story, dict):
        out.update({k: v for k, v in story.items() if k in out or k in ("updated_at", "note")})
    return out


def _new_id(prefix: str) -> str:
    return f"{prefix}{secrets.token_hex(3)}"


def estimate_seconds(text: str) -> float:
    return round(max(0.8, len(text.split()) / WORDS_PER_SECOND), 2)


# ---- the AI's draft -----------------------------------------------------------------------------


SCHEMA = {
    "type": "object",
    "properties": {
        "lines": {"type": "array", "items": {"type": "object", "properties": {
            "role": {"type": "string", "enum": list(ROLES)},
            "text": {"type": "string"},
        }, "required": ["role", "text"]}},
        "cards": {"type": "array", "items": {"type": "object", "properties": {
            "text": {"type": "string"},
            "when": {"type": "string", "enum": ["start", "before_payoff", "after_payoff"]},
        }, "required": ["text", "when"]}},
        "stickers": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "string", "enum": list(STICKERS)},
            "when": {"type": "string", "enum": ["before_payoff", "at_payoff", "after_payoff"]},
        }, "required": ["id", "when"]}},
    },
    "required": ["lines", "cards", "stickers"],
}


def draft_task(*, creator: str | None, category: str | None, reason: str | None, said: str,
               channel: str | None, length: float, series: str | None) -> str:
    said = said if len(said) <= 2500 else said[:2500].rsplit(" ", 1)[0] + " …"
    sticker_list = ", ".join(f"{k} (“{v['text']}”, for {'/'.join(v['moods'])})" for k, v in STICKERS.items())
    return f"""You help a YouTube Shorts creator narrate a clip from someone else's live stream in their own voice.
The narration is what makes the Short the creator's own work: context and an opinion, not a summary.

The clip: {length:.0f} seconds, from {creator or 'a streamer'}'s stream, category {category or 'unknown'}.
Why it was picked: {reason or 'not given'}
What is said in it (speech-to-text, may contain mistakes): "{said or '(nobody speaks)'}"
The creator's channel: {channel or 'their channel'}.{f' Series name: {series}.' if series else ''}

Write, in English, for a viewer in the US, UK, Australia or India:
- lines: 3 or 4 short lines the creator will record, each under 14 words, spoken casually:
  hook (plays over the first seconds: make the viewer need to see what happens, without spoiling it),
  context (who and what is going on, so the moment makes sense without the stream),
  reaction (the creator's own take, said right after the payoff),
  outro (optional, at the end: a one-line sign-off that invites a follow; no "like and subscribe" clichés).
  Only claim what the words and the reason show. No made-up facts, names or numbers. Never insult the streamer.
- cards: 0 to 2 very short on-screen story cards (under 5 words), such as "10 minutes earlier…", placed at the
  start, before or after the payoff. Only where they help the story.
- stickers: 0 to 2 reaction stickers from this list only, placed before, at or after the payoff:
  {sticker_list}.

Return JSON."""


def place(story: dict[str, Any], length: float, payoff_at: float | None) -> dict[str, Any]:
    """Give each line, card and sticker a time on the clip, keeping lines apart and inside the clip."""
    payoff = payoff_at if payoff_at is not None else length * 0.6
    order = {r: n for n, r in enumerate(ROLES)}
    lines = sorted(story["lines"], key=lambda l: order.get(l.get("role"), 9))
    for line in lines:
        secs = float(line.get("seconds") or estimate_seconds(line.get("text") or ""))
        if line.get("at") is None or line.get("auto_at", True):
            want = {"hook": 0.15, "context": min(payoff - secs - 0.5, length * 0.35),
                    "reaction": payoff + 0.8, "outro": length - secs - 0.3}.get(line.get("role"), 0.0)
            line["at"] = round(max(0.0, want), 2)
            line["auto_at"] = True
    def secs_of(line: dict) -> float:
        return float(line.get("seconds") or estimate_seconds(line.get("text") or ""))

    ordered = sorted(lines, key=lambda l: l["at"])
    cursor = 0.0
    for line in ordered:  # forward: never before the previous line has finished
        line["at"] = round(max(line["at"], cursor), 2)
        cursor = line["at"] + secs_of(line) + LINE_GAP
    limit = length - 0.1
    for line in reversed(ordered):  # backward: finish inside the clip and before the next line starts
        line["at"] = round(max(0.0, min(line["at"], limit - secs_of(line))), 2)
        limit = line["at"] - LINE_GAP
    for card in story["cards"]:
        if card.get("at") is None:
            card["at"] = round({"start": 0.3, "before_payoff": max(0.3, payoff - 2.5),
                                "after_payoff": payoff + 1.0}.get(card.get("when"), 0.3), 2)
        card["at"] = round(min(card["at"], max(0.0, length - 1.6)), 2)
        card.setdefault("seconds", 2.2)
    for st in story["stickers"]:
        if st.get("at") is None:
            st["at"] = round({"before_payoff": max(0.2, payoff - 1.6), "at_payoff": payoff,
                              "after_payoff": payoff + 0.6}.get(st.get("when"), payoff), 2)
        st["at"] = round(min(st["at"], max(0.0, length - 1.6)), 2)
        st.setdefault("seconds", 1.6)
    story["lines"] = lines
    return story


def from_answer(answer: dict[str, Any]) -> dict[str, Any]:
    """The AI's answer as a story, keeping only what fits the rules."""
    story = empty()
    seen_roles: set[str] = set()
    for item in (answer.get("lines") or [])[:MAX_LINES]:
        role, text = item.get("role"), " ".join(str(item.get("text") or "").split())
        if role not in ROLES or not text or role in seen_roles:
            continue
        seen_roles.add(role)
        story["lines"].append({"id": _new_id("l"), "role": role, "text": text[:160], "audio": None,
                               "seconds": None, "at": None, "auto_at": True})
    for item in (answer.get("cards") or [])[:MAX_CARDS]:
        text = " ".join(str(item.get("text") or "").split())[:40]
        if text:
            story["cards"].append({"id": _new_id("c"), "text": text, "when": item.get("when"), "at": None})
    for item in (answer.get("stickers") or [])[:MAX_STICKERS]:
        if item.get("id") in STICKERS:
            story["stickers"].append({"id": _new_id("s"), "sticker": item["id"], "when": item.get("when"), "at": None})
    story["drafted_at"] = iso(utcnow())
    return story


# ---- recordings ------------------------------------------------------------------------------------


def clean_recording(settings: Settings, src: Path, dest: Path) -> float:
    """The owner's take as 48 kHz mono WAV: rumble cut, silence trimmed at both ends, levelled. Returns seconds."""
    if not settings.ffmpeg:
        raise MediaError("ffmpeg is not installed")
    trim = "silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.05"
    af = f"highpass=f=80,{trim},areverse,{trim},areverse,loudnorm=I={NARRATION_LUFS}:TP=-2:LRA=11,aresample={SR}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    run([settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(src), "-vn", "-ac", "1",
         "-af", af, "-ar", str(SR), "-c:a", "pcm_s16le", str(dest)], timeout=120)
    import soundfile as sf

    seconds = float(sf.info(str(dest)).duration) if dest.is_file() else 0.0
    if seconds < 0.3:
        dest.unlink(missing_ok=True)
        raise MediaError("The recording is silent or too short. Check the microphone and record again.")
    return round(seconds, 2)


def recorded(story: dict[str, Any]) -> list[dict[str, Any]]:
    return [l for l in story.get("lines") or [] if l.get("audio") and Path(l["audio"]).is_file() and l.get("seconds")]


def windows(story: dict[str, Any]) -> list[tuple[float, float]]:
    """Where the owner speaks, on the clip's clock."""
    return [(float(l["at"]), float(l["at"]) + float(l["seconds"])) for l in recorded(story)]


def contribution(story: dict[str, Any] | None, length: float) -> dict[str, Any]:
    """How much of the Short is the owner's own narration."""
    story = normalise(story)
    secs = sum(e - s for s, e in windows(story))
    return {"seconds": round(secs, 1), "share": round(secs / length, 3) if length else 0.0,
            "lines": len(recorded(story)), "cards": len(story["cards"]), "stickers": len(story["stickers"])}


def build_voice(settings: Settings, story: dict[str, Any], length: float, out_dir: Path, stem: str) -> VoiceSpec | None:
    """The narration stem and the ducking envelope for the clip's own sound, both `length` seconds long."""
    import soundfile as sf

    lines = recorded(story)
    if not lines:
        return None
    n = int(round(length * SR))
    voice = np.zeros(n, dtype=np.float32)
    duck = np.ones(n, dtype=np.float32)
    ramp = int(DUCK_RAMP * SR)
    for line in lines:
        data, rate = sf.read(line["audio"], dtype="float32", always_2d=True)
        mono = data.mean(axis=1)
        if rate != SR:
            idx = np.linspace(0, len(mono) - 1, int(len(mono) * SR / rate))
            mono = np.interp(idx, np.arange(len(mono)), mono).astype(np.float32)
        a = int(round(float(line["at"]) * SR))
        if a >= n:
            continue
        b = min(n, a + len(mono))
        voice[a:b] += mono[: b - a]
        lo, hi = max(0, a - ramp), min(n, b + ramp)
        duck[lo:hi] = np.minimum(duck[lo:hi], DUCK_TO)
        # ramps so the clip's sound fades down and back up instead of jumping
        if a - lo > 0:
            duck[lo:a] = np.minimum(duck[lo:a], np.linspace(1.0, DUCK_TO, a - lo, dtype=np.float32))
        if hi - b > 0:
            duck[b:hi] = np.minimum(duck[b:hi], np.linspace(DUCK_TO, 1.0, hi - b, dtype=np.float32))
    out_dir.mkdir(parents=True, exist_ok=True)
    stem_path, duck_path = out_dir / f"{stem}_voice.wav", out_dir / f"{stem}_duck.wav"
    sf.write(stem_path, np.stack([voice, voice], axis=1), SR, subtype="FLOAT")
    sf.write(duck_path, np.stack([duck, duck], axis=1), SR, subtype="FLOAT")
    return VoiceSpec(stem_path, duck_path)


# ---- on screen: narrator box and story cards (ASS), stickers (drawn on frames) -----------------------


def _wrap(text: str, width: int) -> list[str]:
    out, cur = [], ""
    for word in text.split():
        if cur and len(cur) + 1 + len(word) > width:
            out.append(cur)
            cur = word
        else:
            cur = f"{cur} {word}".strip()
    if cur:
        out.append(cur)
    return out


def add_to_ass(ass: str, story: dict[str, Any], length: float, frame_w: int = 1080, frame_h: int = 1920) -> str:
    """The narrator box and story cards added to a clip's caption file."""
    from .captions import _ass_time, _escape

    lines, cards = recorded(story), story.get("cards") or []
    stickers = sticker_events(story, length, frame_w, frame_h)
    if not lines and not cards and not stickers:
        return ass
    styles = (
        "Style: Narrator,Montserrat ExtraBold,54,&H00FFFFFF,&H00FFFFFF,&H40A30C3A,&H80000000,0,0,0,0,100,100,0,0,"
        "3,14,0,5,0,0,0,1\n"
        "Style: Card,Montserrat Black,62,&H001AE1FF,&H001AE1FF,&H202A0F0B,&H80000000,0,0,0,0,100,100,1,-3,"
        "3,16,0,5,0,0,0,1\n"
        "Style: Sticker,Montserrat Black,92,&H00FFFFFF,&H00FFFFFF,&H00882EFF,&H90000000,0,0,0,0,100,100,1,0,"
        "3,22,6,5,0,0,0,1\n"
    )
    if "\n\n[Events]" in ass:
        ass = ass.replace("\n\n[Events]", "\n" + styles.rstrip("\n") + "\n\n[Events]", 1)
    events = []
    for line in lines:
        start, end = float(line["at"]), min(length, float(line["at"]) + float(line["seconds"]) + 0.15)
        text = "\\N".join(_escape(t) for t in _wrap(line["text"], 26))
        events.append(f"Dialogue: 2,{_ass_time(start)},{_ass_time(end)},Narrator,,0,0,0,,"
                      f"{{\\pos({frame_w // 2},{round(frame_h * NARRATOR_Y)})\\fad(120,120)}}{text}")
    for card in cards:
        start = float(card["at"])
        end = min(length, start + float(card.get("seconds") or 2.2))
        text = _escape(card["text"].upper())
        events.append(f"Dialogue: 3,{_ass_time(start)},{_ass_time(end)},Card,,0,0,0,,"
                      f"{{\\pos({frame_w // 2},{round(frame_h * CARD_Y)})\\fad(150,150)}}{text}")
    events += stickers
    return ass.rstrip("\n") + "\n" + "\n".join(events) + "\n"


def _ass_bgr(hex_rgb: str) -> str:
    return f"&H00{hex_rgb[5:7]}{hex_rgb[3:5]}{hex_rgb[1:3]}&".upper()


def sticker_events(story: dict[str, Any], length: float, frame_w: int = 1080, frame_h: int = 1920) -> list[str]:
    """Reaction stickers as caption events: a coloured box, a slight tilt and a pop-in. Text only, nothing to license."""
    from .captions import _ass_time, _escape

    out = []
    for st in story.get("stickers") or []:
        spec = STICKERS.get(st.get("sticker"))
        if not spec:
            continue
        start = float(st["at"])
        end = min(length, start + float(st.get("seconds") or 1.6))
        if end - start < 0.3:
            continue
        tags = (f"\\pos({frame_w // 2},{round(frame_h * STICKER_Y)})\\frz4\\1c{_ass_bgr(spec['fg'])}"
                f"\\3c{_ass_bgr(spec['bg'])}\\fscx70\\fscy70\\t(0,140,\\fscx100\\fscy100)\\fad(60,120)")
        out.append(f"Dialogue: 4,{_ass_time(start)},{_ass_time(end)},Sticker,,0,0,0,,{{{tags}}}{_escape(spec['text'])}")
    return out
