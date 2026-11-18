"""Story mode for one Short: the AI's draft lines, the owner's edits and recordings, cards and stickers."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import story as st
from .. import youtube as yt
from .. import pipelines
from ..ai.base import AIError, AIUnavailable
from ..context import AppContext, get_ctx
from ..media import MediaError
from ..models import Clip, Project, TranscriptSegment, iso, utcnow
from ..music import to_clip_time

router = APIRouter(prefix="/api", tags=["story"])

MAX_UPLOAD = 25 * 1024 * 1024


class LineIn(BaseModel):
    id: str | None = None
    role: str = Field(pattern="^(hook|context|reaction|outro)$")
    text: str = Field(min_length=1, max_length=200)
    at: float | None = Field(default=None, ge=0)


class CardIn(BaseModel):
    id: str | None = None
    text: str = Field(min_length=1, max_length=40)
    at: float = Field(ge=0)
    seconds: float = Field(default=2.2, ge=0.8, le=6)


class StickerIn(BaseModel):
    id: str | None = None
    sticker: str
    at: float = Field(ge=0)
    seconds: float = Field(default=1.6, ge=0.6, le=4)


class StoryIn(BaseModel):
    lines: list[LineIn] = Field(default_factory=list, max_length=st.MAX_LINES)
    cards: list[CardIn] = Field(default_factory=list, max_length=st.MAX_CARDS)
    stickers: list[StickerIn] = Field(default_factory=list, max_length=st.MAX_STICKERS)


def _clip(s, project_id: int, clip_id: int) -> tuple[Project, Clip]:
    project, clip = s.get(Project, project_id), s.get(Clip, clip_id)
    if project is None or clip is None or clip.project_id != project_id:
        raise HTTPException(404, "Clip not found")
    return project, clip


def _story_dir(ctx: AppContext, project_id: int, clip_id: int) -> Path:
    return ctx.settings.project_dir(project_id) / "story" / str(clip_id)


def _payoff(s, clip: Clip) -> float | None:
    from ..models import Moment

    hook = (clip.report or {}).get("hook") or {}
    if hook.get("payoff_at") is not None:
        return float(hook["payoff_at"])
    moment = s.get(Moment, clip.moment_id) if clip.moment_id else None
    if moment is None or moment.peak is None:
        return None
    return to_clip_time(float(moment.peak), [tuple(x) for x in (clip.segments or [])])


def _out(clip: Clip) -> dict:
    story = st.normalise(clip.story)
    final = clip.final or {}
    rendered = (final.get("story") or {}) if final else {}
    stale = bool(final) and (story.get("updated_at") or "") > (final.get("rendered_at") or "")
    return {**story, "contribution": st.contribution(story, float(clip.duration or 0)),
            "in_final": rendered or None, "render_needed": stale,
            "stickers_available": {k: {"text": v["text"], "moods": v["moods"]} for k, v in st.STICKERS.items()}}


@router.get("/projects/{project_id}/clips/{clip_id}/story")
def get_story(project_id: int, clip_id: int, ctx: AppContext = Depends(get_ctx)) -> dict:
    with ctx.session() as s:
        _, clip = _clip(s, project_id, clip_id)
        return _out(clip)


@router.post("/projects/{project_id}/clips/{clip_id}/story/draft")
def draft(project_id: int, clip_id: int, ctx: AppContext = Depends(get_ctx)) -> dict:
    """Ask the AI for narration lines, cards and stickers. Recordings already made are kept for unchanged roles."""
    with ctx.session() as s:
        project, clip = _clip(s, project_id, clip_id)
        segs = [tuple(x) for x in (clip.segments or [])]
        lo, hi = (min(a for a, _ in segs), max(b for _, b in segs)) if segs else (0.0, 0.0)
        rows = s.query(TranscriptSegment).filter(TranscriptSegment.project_id == project_id,
                                                  TranscriptSegment.end > lo, TranscriptSegment.start < hi).all()
        said = " ".join(r.text for r in sorted(rows, key=lambda r: r.start) if r.text).strip()
        from ..models import Moment

        moment = s.get(Moment, clip.moment_id) if clip.moment_id else None
        channel = ((yt.load_token(ctx.settings) or {}).get("channel") or {}).get("title")
        task = st.draft_task(creator=project.creator or (project.source_meta or {}).get("creator"),
                             category=clip.category, reason=getattr(moment, "reason", None), said=said,
                             channel=channel, length=float(clip.duration or 0), series=None)
        payoff = _payoff(s, clip)
        old = st.normalise(clip.story)
    try:
        answer = pipelines.make_reasoner(ctx.settings).ask_json(task, st.SCHEMA)
    except AIUnavailable as exc:
        raise HTTPException(409, str(exc)) from exc
    except AIError as exc:
        raise HTTPException(502, f"The AI did not answer: {exc}") from exc
    story = st.from_answer(answer)
    for line in story["lines"]:  # keep a take the owner already recorded for the same role and words
        prev = next((l for l in old["lines"] if l.get("role") == line["role"] and l.get("text") == line["text"]), None)
        if prev and prev.get("audio"):
            line.update(audio=prev["audio"], seconds=prev["seconds"])
    with ctx.session() as s:
        _, clip = _clip(s, project_id, clip_id)
        story = st.place(story, float(clip.duration or 0), payoff)
        story["updated_at"] = iso(utcnow())
        clip.story = story
        s.commit()
        out = _out(clip)
    ctx.bus.publish({"type": "project", "id": project_id})
    return out


@router.put("/projects/{project_id}/clips/{clip_id}/story")
def save(project_id: int, clip_id: int, body: StoryIn, ctx: AppContext = Depends(get_ctx)) -> dict:
    """The owner's edits. A line whose words change loses its recording (it no longer says what is written)."""
    bad = [x.sticker for x in body.stickers if x.sticker not in st.STICKERS]
    if bad:
        raise HTTPException(422, f"Unknown sticker: {bad[0]}")
    with ctx.session() as s:
        _, clip = _clip(s, project_id, clip_id)
        old = {l["id"]: l for l in st.normalise(clip.story)["lines"]}
        length = float(clip.duration or 0)
        lines = []
        for item in body.lines:
            prev = old.get(item.id or "")
            line = {"id": item.id if prev else st._new_id("l"), "role": item.role, "text": " ".join(item.text.split()),
                    "audio": None, "seconds": None, "at": item.at, "auto_at": item.at is None}
            if prev and prev.get("text") == line["text"] and prev.get("audio"):
                line.update(audio=prev["audio"], seconds=prev["seconds"])
            elif prev and prev.get("audio"):
                Path(prev["audio"]).unlink(missing_ok=True)
            lines.append(line)
        for gone in set(old) - {l["id"] for l in lines}:
            if old[gone].get("audio"):
                Path(old[gone]["audio"]).unlink(missing_ok=True)
        story = st.normalise(clip.story)
        story["lines"] = lines
        story["cards"] = [{"id": c.id or st._new_id("c"), "text": c.text, "at": min(c.at, length), "seconds": c.seconds}
                          for c in body.cards]
        story["stickers"] = [{"id": x.id or st._new_id("s"), "sticker": x.sticker, "at": min(x.at, length),
                              "seconds": x.seconds} for x in body.stickers]
        story = st.place(story, length, _payoff(s, clip))
        story["updated_at"] = iso(utcnow())
        clip.story = story
        s.commit()
        return _out(clip)


@router.post("/projects/{project_id}/clips/{clip_id}/story/lines/{line_id}/audio")
async def upload_take(project_id: int, clip_id: int, line_id: str, file: UploadFile = File(...),
                      ctx: AppContext = Depends(get_ctx)) -> dict:
    """The owner's recording of one line (from the dashboard's microphone, or a file)."""
    with ctx.session() as s:
        _, clip = _clip(s, project_id, clip_id)
        if not any(l["id"] == line_id for l in st.normalise(clip.story)["lines"]):
            raise HTTPException(404, "Line not found")
    data = await file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "The recording is too long (over 25 MB).")
    if not data:
        raise HTTPException(422, "The recording is empty.")
    folder = _story_dir(ctx, project_id, clip_id)
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / f"{line_id}_{st.secrets.token_hex(2)}.wav"
    with tempfile.NamedTemporaryFile(suffix=Path(file.filename or "take.webm").suffix or ".webm",
                                     dir=folder, delete=False) as tmp:
        tmp.write(data)
        raw = Path(tmp.name)
    try:
        seconds = st.clean_recording(ctx.settings, raw, dest)
    except MediaError as exc:
        raise HTTPException(422, str(exc)) from exc
    finally:
        raw.unlink(missing_ok=True)
    with ctx.session() as s:
        _, clip = _clip(s, project_id, clip_id)
        story = st.normalise(clip.story)
        for line in story["lines"]:
            if line["id"] == line_id:
                if line.get("audio") and line["audio"] != str(dest):
                    Path(line["audio"]).unlink(missing_ok=True)
                line.update(audio=str(dest), seconds=seconds, recorded_at=iso(utcnow()))
        story = st.place(story, float(clip.duration or 0), _payoff(s, clip))
        story["updated_at"] = iso(utcnow())
        clip.story = story
        s.commit()
        out = _out(clip)
    ctx.bus.publish({"type": "project", "id": project_id})
    return out


@router.get("/projects/{project_id}/clips/{clip_id}/story/lines/{line_id}/audio")
def play_take(project_id: int, clip_id: int, line_id: str, ctx: AppContext = Depends(get_ctx)) -> FileResponse:
    with ctx.session() as s:
        _, clip = _clip(s, project_id, clip_id)
        line = next((l for l in st.normalise(clip.story)["lines"] if l["id"] == line_id), None)
    if not line or not line.get("audio") or not Path(line["audio"]).is_file():
        raise HTTPException(404, "Not recorded yet")
    return FileResponse(line["audio"], media_type="audio/wav")


@router.delete("/projects/{project_id}/clips/{clip_id}/story/lines/{line_id}/audio")
def delete_take(project_id: int, clip_id: int, line_id: str, ctx: AppContext = Depends(get_ctx)) -> dict:
    with ctx.session() as s:
        _, clip = _clip(s, project_id, clip_id)
        story = st.normalise(clip.story)
        for line in story["lines"]:
            if line["id"] == line_id and line.get("audio"):
                Path(line["audio"]).unlink(missing_ok=True)
                line.update(audio=None, seconds=None)
        story["updated_at"] = iso(utcnow())
        clip.story = story
        s.commit()
        return _out(clip)
