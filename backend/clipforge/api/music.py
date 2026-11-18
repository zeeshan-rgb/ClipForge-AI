"""The music library: tracks the owner is licensed to use, with the licence record that makes them usable."""

from __future__ import annotations

import mimetypes
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from fastapi.responses import FileResponse
from sqlalchemy import select

from ..context import AppContext, get_ctx
from ..media import MediaError
from ..models import Clip, Track, iso
from ..music import (
    AUDIO_EXTENSIONS,
    BEAT_REGULARITY_MIN,
    CATEGORY_MOODS,
    LICENCES,
    MAX_TRACK_BYTES,
    MIN_GAP_DB,
    MOOD_LABEL,
    MOODS,
    YT_AUDIO_LIBRARY,
    analyse_track,
    estimate_moods,
    licence_warnings,
    requires_attribution,
)
from ..pipelines import track_info
from ..schemas import TrackFields, TrackImport

router = APIRouter(prefix="/api/music", tags=["music"])


def _clean(value: str | None) -> str | None:
    return (value or "").strip() or None


def _used_by(s) -> dict[int, int]:
    counts: dict[int, int] = {}
    for music in s.scalars(select(Clip.music).where(Clip.music.is_not(None))).all():
        if isinstance(music, dict) and music.get("used") and music.get("track_id") is not None:
            counts[music["track_id"]] = counts.get(music["track_id"], 0) + 1
    return counts


def track_to_dict(t: Track, used_by: int = 0) -> dict:
    info = track_info(t)
    return {
        "id": t.id,
        "title": t.title,
        "original_name": t.original_name,
        "duration": t.duration,
        "bpm": t.bpm,
        "steady_beat": bool(t.beats) and (t.regularity or 0) >= BEAT_REGULARITY_MIN,
        "loudness_db": t.loudness_db,
        "moods": list(t.moods or []),
        "source": t.source,
        "licence_name": t.licence_name,
        "licence_url": t.licence_url,
        "licence_note": t.licence_note,
        "attribution": t.attribution,
        "requires_attribution": requires_attribution(t.licence_name),
        "usable": info.usable,
        "problems": info.problems,
        "warnings": licence_warnings(t.licence_name, t.licence_note),
        "added_at": iso(t.added_at),
        "used_by": used_by,
    }


@router.get("/meta")
def meta() -> dict:
    return {
        "moods": [{"name": m, "label": MOOD_LABEL[m]} for m in MOODS],
        "licences": [{"name": n, "requires_attribution": r} for n, r in LICENCES.items()],
        "category_moods": CATEGORY_MOODS,
        "audio_extensions": sorted(AUDIO_EXTENSIONS),
        "min_gap_db": MIN_GAP_DB,
    }


@router.get("")
def list_tracks(ctx: AppContext = Depends(get_ctx)) -> list[dict]:
    with ctx.session() as s:
        used = _used_by(s)
        return [track_to_dict(t, used.get(t.id, 0)) for t in s.scalars(select(Track).order_by(Track.id)).all()]


@router.post("/import", status_code=201)
def import_track(body: TrackImport, ctx: AppContext = Depends(get_ctx)) -> dict:
    """Add a track. It is copied into the app's music folder and analysed (length, level, tempo, beats).

    Licence details can be filled in now or later, but the track cannot be used until they are complete.
    """
    raw = body.path.strip().strip('"')
    if not raw:
        raise HTTPException(422, "Choose an audio file")
    path = Path(raw)
    if not path.is_file():
        raise HTTPException(422, f"File not found: {raw}")
    if path.suffix.lower() not in AUDIO_EXTENSIONS:
        raise HTTPException(422, f"'{path.suffix}' is not a supported audio type ({', '.join(sorted(AUDIO_EXTENSIONS))})")
    if path.stat().st_size > MAX_TRACK_BYTES:
        raise HTTPException(422, "That file is larger than 200 MB")

    ctx.settings.music_dir.mkdir(parents=True, exist_ok=True)
    with ctx.session() as s:
        track = Track(
            title=_clean(body.title) or path.stem[:200], file="", original_name=path.name,
            moods=body.moods, source=_clean(body.source), licence_name=_clean(body.licence_name),
            licence_url=_clean(body.licence_url), licence_note=_clean(body.licence_note),
            attribution=_clean(body.attribution),
        )
        s.add(track)
        s.flush()
        dest = ctx.settings.music_dir / f"{track.id}{path.suffix.lower()}"
        try:
            shutil.copyfile(path, dest)
            analysis = analyse_track(ctx.settings, dest)
        except (MediaError, OSError) as exc:
            s.rollback()
            dest.unlink(missing_ok=True)
            raise HTTPException(422, f"Could not read this audio file: {exc}") from exc
        track.file = str(dest)
        track.duration = analysis["duration"]
        track.loudness_db = analysis["loudness_db"]
        track.bpm = analysis["bpm"]
        track.beats = analysis["beats"]
        track.phase = analysis["phase"]
        track.regularity = analysis["regularity"]
        track.channels = analysis["channels"]
        s.commit()
        return track_to_dict(track)


class FolderImport(BaseModel):
    folder: str = Field(min_length=1, max_length=1000)
    attribution: str | None = Field(default=None, max_length=500)  # only for tracks marked "attribution required"


@router.post("/import-folder", status_code=201)
def import_folder(body: FolderImport, ctx: AppContext = Depends(get_ctx)) -> dict:
    """Add every audio file in a folder of tracks downloaded from the YouTube Audio Library.

    Each is analysed, given a first guess at its moods (tempo, beat strength, level; the owner can correct
    it) and recorded with the Audio Library licence. Files already in the library are skipped. Tracks the
    Audio Library marks "attribution required" need their credit line: import those separately, or add it.
    """
    folder = Path(body.folder.strip().strip('"')).expanduser()
    if not folder.is_dir():
        raise HTTPException(422, f"Folder not found: {folder}")
    files = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS)[:200]
    if not files:
        raise HTTPException(422, "No audio files in that folder (" + ", ".join(sorted(AUDIO_EXTENSIONS)) + ").")
    added, skipped = [], []
    ctx.settings.music_dir.mkdir(parents=True, exist_ok=True)
    with ctx.session() as s:
        have = {t.original_name for t in s.scalars(select(Track)).all()}
    for path in files:
        if path.name in have:
            skipped.append({"file": path.name, "reason": "already in the library"})
            continue
        if path.stat().st_size > MAX_TRACK_BYTES:
            skipped.append({"file": path.name, "reason": "larger than 200 MB"})
            continue
        with ctx.session() as s:
            track = Track(title=path.stem[:200], file="", original_name=path.name, moods=[],
                          source=YT_AUDIO_LIBRARY["source"], licence_name=YT_AUDIO_LIBRARY["licence_name"],
                          licence_url=YT_AUDIO_LIBRARY["licence_url"],
                          licence_note="Free to use in videos on YouTube, including monetised ones.",
                          attribution=_clean(body.attribution))
            s.add(track)
            s.flush()
            dest = ctx.settings.music_dir / f"{track.id}{path.suffix.lower()}"
            try:
                shutil.copyfile(path, dest)
                analysis = analyse_track(ctx.settings, dest)
            except (MediaError, OSError) as exc:
                s.rollback()
                dest.unlink(missing_ok=True)
                skipped.append({"file": path.name, "reason": f"could not be read ({exc})"})
                continue
            track.file = str(dest)
            track.duration, track.loudness_db = analysis["duration"], analysis["loudness_db"]
            track.bpm, track.beats, track.phase = analysis["bpm"], analysis["beats"], analysis["phase"]
            track.regularity, track.channels = analysis["regularity"], analysis["channels"]
            track.moods = estimate_moods(analysis, path.stem)
            s.commit()
            added.append(track_to_dict(track))
    return {"added": added, "skipped": skipped}


@router.patch("/{track_id}")
def update_track(track_id: int, body: TrackFields, ctx: AppContext = Depends(get_ctx)) -> dict:
    """Change a track's title, moods or licence record. Only the fields sent are changed."""
    with ctx.session() as s:
        track = s.get(Track, track_id)
        if track is None:
            raise HTTPException(404, "Track not found")
        for key in body.model_fields_set:
            value = getattr(body, key)
            if key == "moods":
                track.moods = value
            elif key == "title":
                track.title = _clean(value) or track.title
            else:
                setattr(track, key, _clean(value))
        s.commit()
        return track_to_dict(track, _used_by(s).get(track.id, 0))


@router.delete("/{track_id}", status_code=204)
def delete_track(track_id: int, force: bool = False, ctx: AppContext = Depends(get_ctx)) -> None:
    with ctx.session() as s:
        track = s.get(Track, track_id)
        if track is None:
            raise HTTPException(404, "Track not found")
        users = [c for c in s.scalars(select(Clip).where(Clip.music.is_not(None))).all()
                 if isinstance(c.music, dict) and c.music.get("track_id") == track_id]
        if users and not force:
            raise HTTPException(
                409, f"This track is used by {len(users)} clip(s). Remove it anyway and those clips lose their music "
                     "the next time they are rendered.",
            )
        for c in users:
            c.music = {"used": False, "mode": "auto", "reason": "The chosen track was removed from the library."}
        path = Path(track.file) if track.file else None
        s.delete(track)
        s.commit()
    if path is not None:
        path.unlink(missing_ok=True)


@router.get("/{track_id}/audio")
def track_audio(track_id: int, ctx: AppContext = Depends(get_ctx)) -> FileResponse:
    """The track itself, so the dashboard can play it."""
    with ctx.session() as s:
        track = s.get(Track, track_id)
        path = Path(track.file) if track and track.file else None
    if path is None or not path.is_file():
        raise HTTPException(404, "Track file not found")
    media_type = mimetypes.guess_type(path.name)[0] or "audio/mpeg"
    return FileResponse(path, media_type=media_type)
