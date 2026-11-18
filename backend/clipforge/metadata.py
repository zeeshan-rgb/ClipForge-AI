"""Titles, descriptions, hashtags and a pinned comment for each finished Short.

The AI writes the words from what was actually said and why the moment was
picked; everything that must be right is added by code afterwards, never left
to the model:

* the creator credit and the original link, first in the description, for
  every clip made from someone else's video;
* the credit line a CC BY music track requires;
* "Part 1/2" in the title of connected parts;
* #Shorts among the hashtags, and lengths within YouTube's limits.

One AI request covers every clip being written, because each request carries
a large fixed overhead. If no AI is available, a plain title and description
are made from the clip itself and the clip says so.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from .ai.base import AIError

TITLE_MAX = 100  # YouTube's limit
TITLE_AIM = 70  # what shows in full on a phone
DESCRIPTION_MAX = 5000
HASHTAGS_MAX = 8
EXCERPT_CHARS = 700

PLATFORM_NAMES = {"youtube": "YouTube", "twitch": "Twitch", "kick": "Kick", "local": None}

SCHEMA = {
    "type": "object",
    "properties": {
        "clips": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "hashtags": {"type": "array", "items": {"type": "string"}},
                    "pinned_comment": {"type": "string"},
                },
                "required": ["id", "title", "description", "hashtags", "pinned_comment"],
            },
        }
    },
    "required": ["clips"],
}


@dataclass
class ClipText:
    """What the writer is told about one clip."""

    id: int
    category: str | None
    reason: str | None
    said: str  # the words spoken in the clip, as transcribed
    duration: float
    part: int = 1
    parts: int = 1


@dataclass
class Source:
    title: str | None
    original_title: str | None
    creator: str | None  # who to credit (the owner's own wording)
    channel: str | None  # the channel name the platform reports
    platform: str | None
    url: str | None
    rights_status: str
    language: str | None = None


@dataclass
class Written:
    title: str
    description: str
    hashtags: list[str]
    pinned_comment: str
    by: str  # the AI's name, or "plain" when no AI wrote it
    notes: list[str] = field(default_factory=list)


def task(clips: list[ClipText], source: Source) -> str:
    lang = source.language or "the language the clip is spoken in"
    blocks = []
    for c in clips:
        part = f" It is part {c.part} of {c.parts} of one story." if c.parts > 1 else ""
        said = c.said if len(c.said) <= EXCERPT_CHARS else c.said[:EXCERPT_CHARS].rsplit(" ", 1)[0] + " …"
        blocks.append(
            f"Clip {c.id}: {c.duration:.0f} seconds, category {c.category or 'unknown'}.{part}\n"
            f"Why it was picked: {c.reason or 'not given'}\n"
            f"What is said (speech-to-text, may contain mistakes): \"{said or '(nobody speaks)'}\""
        )
    return f"""You write the text that goes with YouTube Shorts cut from a longer video.
The long video is called "{source.original_title or source.title or 'untitled'}".

For each clip below write:
- title: at most {TITLE_AIM} characters, in {lang}. Make a viewer curious to see what happens, with the most
  interesting words first (only the start shows in the Shorts feed), but do not give away the punchline.
  Only claim what the words and the reason show: never promise something the clip does not deliver.
  No made-up facts, names or numbers, no ALL CAPS, at most one emoji.
- description: one or two short sentences in {lang} about what happens. Do not add links, credits or hashtags;
  those are added separately.
- hashtags: 3 to 6 relevant hashtags, each one word, without spaces, starting with #. Do not include #Shorts.
- pinned_comment: one short question in {lang} that invites viewers to reply, about this clip.
Do not say the clip is official, licensed, fair use or approved by anyone. Do not mention part numbers;
they are added separately.

{chr(10).join(blocks)}

Return JSON with one entry per clip, using the clip numbers given."""


_HASHTAG = re.compile(r"[^\w]", re.UNICODE)


def clean_hashtags(tags: list[str], category: str | None) -> list[str]:
    out: list[str] = []
    for t in [*tags, category or "", "Shorts"]:
        word = _HASHTAG.sub("", str(t).lstrip("#"))
        if not word or word.isdigit():
            continue
        tag = "#" + word
        if tag.lower() not in {x.lower() for x in out}:
            out.append(tag)
    # #Shorts always, the rest in the writer's order
    shorts = next(x for x in out if x.lower() == "#shorts")
    rest = [x for x in out if x.lower() != "#shorts"][: HASHTAGS_MAX - 1]
    return [*rest, shorts]


def clean_title(text: str, part: int, parts: int) -> str:
    title = " ".join(str(text).replace("\n", " ").split())
    # take away quote marks only when they wrap the whole title, not when they open a quotation inside it
    pairs = {'"': '"', "“": "”", "'": "'", "«": "»"}
    if len(title) > 1 and title[0] in pairs and title[-1] == pairs[title[0]] and title.count(title[-1]) <= 2:
        title = title[1:-1].strip()
    title = re.sub(r"\s#\w+", "", title).strip()  # hashtags belong in the description
    if title.isupper() and len(title) > 4:
        title = title.capitalize()
    suffix = f" (Part {part}/{parts})" if parts > 1 else ""
    room = min(TITLE_AIM, TITLE_MAX - len(suffix))
    if len(title) > room:
        title = title[:room].rsplit(" ", 1)[0].rstrip(",;:-–—") + "…"
    return (title or "Clip") + suffix


def credit_lines(source: Source) -> list[str]:
    """The credit for someone else's video, written by code so it is never missing or wrong."""
    if source.rights_status == "own":
        return []
    name = source.creator or source.channel
    if not name:
        return []
    where = PLATFORM_NAMES.get(source.platform or "")
    line = f"Credit: {name}" + (f" on {where}" if where else "")
    out = [line]
    if source.url:
        out.append(f"Original video: {source.url}")
    return out


def compose_description(body: str, source: Source, music_credit: str | None, hashtags: list[str],
                        part: int, parts: int) -> str:
    lines = credit_lines(source)
    if lines:
        lines.append("")
    body = " ".join(str(body).split())
    body = re.sub(r"https?://\S+", "", body).strip()  # links only come from the credit
    if body:
        lines.append(body)
    if parts > 1:
        lines.append(f"Part {part} of {parts}." + (" More in the next part." if part < parts else ""))
    if music_credit:
        lines += ["", f"Music: {music_credit}"]
    lines += ["", " ".join(hashtags)]
    text = "\n".join(lines).strip()
    return text[:DESCRIPTION_MAX]


def plain(c: ClipText, source: Source, music_credit: str | None, why: str) -> Written:
    """Text made without the AI: the category and the first words said."""
    words = c.said.split()
    title = " ".join(words[:8]) + ("…" if len(words) > 8 else "") if words else (c.category or "Clip").capitalize()
    tags = clean_hashtags([], c.category)
    body = c.reason or ""
    return Written(clean_title(title, c.part, c.parts),
                   compose_description(body, source, music_credit, tags, c.part, c.parts), tags,
                   "What did you think of this moment?", "plain", [why])


def write(
    reasoner, clips: list[ClipText], source: Source, music_credits: dict[int, str | None],
    check: Callable[[], None] | None = None,
) -> dict[int, Written]:
    """Text for every clip, from one AI request. A clip the AI leaves out gets plain text."""
    if not clips:
        return {}
    out: dict[int, Written] = {}
    why = ""
    answers: dict[int, dict[str, Any]] = {}
    if reasoner is not None:
        try:
            reply = reasoner.ask_json(task(clips, source), SCHEMA, check=check)
            answers = {int(a["id"]): a for a in reply.get("clips", []) if isinstance(a, dict) and "id" in a}
        except AIError as exc:
            why = f"Written without the AI ({exc}). Edit it or ask for it again."
    else:
        why = "Written without the AI: no AI provider is set up."
    for c in clips:
        a = answers.get(c.id)
        credit = music_credits.get(c.id)
        if not a or not str(a.get("title", "")).strip():
            out[c.id] = plain(c, source, credit, why or "The AI gave no text for this clip, so a plain one was made.")
            continue
        tags = clean_hashtags(list(a.get("hashtags") or []), c.category)
        notes = []
        title = clean_title(a["title"], c.part, c.parts)
        if len(" ".join(str(a["title"]).split())) > TITLE_AIM:
            notes.append(f"The title was shortened to {TITLE_AIM} characters.")
        out[c.id] = Written(
            title=title,
            description=compose_description(str(a.get("description", "")), source, credit, tags, c.part, c.parts),
            hashtags=tags,
            pinned_comment=" ".join(str(a.get("pinned_comment", "")).split())[:300],
            by=getattr(reasoner, "name", "ai"),
            notes=notes,
        )
    return out
