"""Projects: one long source video each."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import func, select

from ..context import AppContext, get_ctx
from ..ingest import detect_platform
from ..media import VIDEO_EXTENSIONS
from ..models import Clip, Job, Moment, Project, Streamer, Track, TranscriptSegment
from ..exports import blocker as export_blocker
from ..exports import export_clip, export_folder
from ..metadata import clean_hashtags
from ..moments import score_parts
from ..pipelines import default_link_title, plan_project_music, reselect, reset_analysis, track_info
from .. import outreach
from ..rights import download_blocker, write_rights_file
from ..captions import CaptionStyle
from ..schemas import (
    AttachFile,
    ClipFraming,
    ClipMusicChoice,
    ClipReview,
    ClipText,
    ExportRequest,
    FinalRender,
    WriteAgain,
    ProjectCreate,
    ProjectOptions,
    RightsUpdate,
    normalise_options,
    project_to_dict,
)

router = APIRouter(prefix="/api/projects", tags=["projects"])

# which changed settings need which work done again
CUT_KEYS = {"categories", "max_clips", "min_score", "target_duration", "duration_mode", "tolerance_pct",
            "split_parts", "max_parts", "context", "trim_silence"}
MUSIC_KEYS = {"music_mode", "music_gap_db"}
FRAME_KEYS = {"layout", "zoom"}


def _clip_counts(s, project_ids: list[int]) -> dict[int, int]:
    if not project_ids:
        return {}
    rows = s.execute(
        select(Clip.project_id, func.count(Clip.id))
        .where(Clip.project_id.in_(project_ids), Clip.valid.is_(True))
        .group_by(Clip.project_id)
    ).all()
    return {pid: n for pid, n in rows}


def _video_path(raw: str | None) -> Path:
    cleaned = (raw or "").strip().strip('"')
    if not cleaned:
        raise HTTPException(422, "Choose a video file")
    path = Path(cleaned)
    if not path.is_file():
        raise HTTPException(422, f"File not found: {cleaned}")
    if path.suffix.lower() not in VIDEO_EXTENSIONS:
        raise HTTPException(
            422, f"'{path.suffix}' is not a supported video type ({', '.join(sorted(VIDEO_EXTENSIONS))})"
        )
    return path.resolve()


def _busy(s, project_id: int) -> bool:
    return bool(
        s.scalar(
            select(func.count(Job.id)).where(
                Job.project_id == project_id, Job.status.in_(("queued", "running"))
            )
        )
    )


def _clean(value: str | None) -> str | None:
    return (value or "").strip() or None


@router.get("")
def list_projects(ctx: AppContext = Depends(get_ctx)) -> list[dict]:
    with ctx.session() as s:
        projects = s.scalars(select(Project).order_by(Project.created_at.desc(), Project.id.desc())).all()
        counts = _clip_counts(s, [p.id for p in projects])
        return [project_to_dict(p, clip_count=counts.get(p.id, 0)) for p in projects]


@router.post("", status_code=201)
def create_project(body: ProjectCreate, ctx: AppContext = Depends(get_ctx)) -> dict:
    common = dict(
        creator=_clean(body.creator),
        rights_status=body.rights_status,
        rights_note=_clean(body.rights_note),
        options=body.options.model_dump(),
    )
    # Permission recorded on the Permissions page: chosen by the owner, or plain from the link itself.
    with ctx.session() as s:
        streamer = None
        if body.permission_id is not None:
            streamer = s.get(Streamer, body.permission_id)
            if streamer is None or streamer.status != "approved":
                raise HTTPException(422, "That streamer has not given permission (see the Permissions page).")
        elif body.source == "url" and body.rights_status == "unverified":
            streamer = outreach.approved_for_link(s, body.url or "")
        if streamer is not None:
            common.update(creator=streamer.name or streamer.handle, rights_status="creator_permission",
                          rights_note=outreach.evidence_note(streamer), permission_id=streamer.id)

    if body.source == "local":
        path = _video_path(body.path)
        project = Project(
            title=_clean(body.title) or path.stem,
            source_kind="local",
            source_path=str(path),
            status="processing",
            **common,
        )
        pipeline = "ingest_local"
    else:
        url = (body.url or "").strip()
        platform = detect_platform(url)
        if platform is None:
            raise HTTPException(
                422, "Paste a YouTube, Twitch or Kick video link. For anything else, use a local file."
            )
        blocker = download_blocker(common["rights_status"], common["creator"], common["rights_note"])
        project = Project(
            title=_clean(body.title) or default_link_title(platform),
            source_kind=platform,
            source_url=url,
            status="needs_rights" if blocker else "processing",
            status_detail=blocker,
            **common,
        )
        pipeline = None if blocker else "ingest_url"

    with ctx.session() as s:
        s.add(project)
        s.commit()
        write_rights_file(ctx.settings, project)
        data = project_to_dict(project)

    if pipeline:
        ctx.worker.enqueue(pipeline, project_id=project.id)
    ctx.bus.publish({"type": "project", "id": project.id})
    return data


@router.get("/{project_id}")
def get_project(project_id: int, ctx: AppContext = Depends(get_ctx)) -> dict:
    with ctx.session() as s:
        project = s.get(Project, project_id)
        if project is None:
            raise HTTPException(404, "Project not found")
        counts = _clip_counts(s, [project_id])
        return project_to_dict(project, clip_count=counts.get(project_id, 0))


@router.patch("/{project_id}/rights")
def update_rights(project_id: int, body: RightsUpdate, ctx: AppContext = Depends(get_ctx)) -> dict:
    """Change the rights record. A link that was waiting on it starts downloading."""
    start = False
    with ctx.session() as s:
        project = s.get(Project, project_id)
        if project is None:
            raise HTTPException(404, "Project not found")
        if body.rights_status != project.rights_status or body.rights_status != "creator_permission":
            project.permission_id = None  # the owner changed the record by hand
        project.rights_status = body.rights_status
        project.creator = _clean(body.creator)
        project.rights_note = _clean(body.rights_note)

        waiting_link = project.source_kind != "local" and not project.source_path
        if waiting_link and not _busy(s, project_id):
            blocker = download_blocker(project.rights_status, project.creator, project.rights_note)
            if blocker:
                project.status, project.status_detail = "needs_rights", blocker
            elif project.status == "needs_rights":
                project.status, project.status_detail = "processing", None
                start = True
        s.commit()
        write_rights_file(ctx.settings, project)
        data = project_to_dict(project, clip_count=_clip_counts(s, [project_id]).get(project_id, 0))

    if start:
        ctx.worker.enqueue("ingest_url", project_id=project_id)
    ctx.bus.publish({"type": "project", "id": project_id})
    return data


@router.post("/{project_id}/attach")
def attach_file(project_id: int, body: AttachFile, ctx: AppContext = Depends(get_ctx)) -> dict:
    """Give a link project a video file the user supplies, instead of downloading."""
    path = _video_path(body.path)
    with ctx.session() as s:
        project = s.get(Project, project_id)
        if project is None:
            raise HTTPException(404, "Project not found")
        if _busy(s, project_id):
            raise HTTPException(409, "This project is still processing. Cancel its job first.")
        reset_analysis(s, ctx.settings, project)
        project.source_path = str(path)
        project.media = None
        project.thumbnail = None
        project.proxy_path = None
        project.audio_path = None
        project.status, project.status_detail = "processing", None
        s.commit()
        data = project_to_dict(project)
    ctx.worker.enqueue("ingest_local", project_id=project_id)
    ctx.bus.publish({"type": "project", "id": project_id})
    return data


def _project_file(ctx: AppContext, project_id: int, attr: str) -> Path:
    with ctx.session() as s:
        project = s.get(Project, project_id)
        value = getattr(project, attr) if project else None
    if not value or not Path(value).is_file():
        raise HTTPException(404, "Not available")
    return Path(value)


@router.get("/{project_id}/thumbnail")
def get_thumbnail(project_id: int, ctx: AppContext = Depends(get_ctx)) -> FileResponse:
    path = _project_file(ctx, project_id, "thumbnail")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "no-cache"})


@router.get("/{project_id}/proxy")
def get_proxy(project_id: int, ctx: AppContext = Depends(get_ctx)) -> FileResponse:
    """The 360p preview copy. Supports byte ranges, so the player can seek."""
    path = _project_file(ctx, project_id, "proxy_path")
    return FileResponse(path, media_type="video/mp4", headers={"Cache-Control": "no-cache"})


def _shrink(series: list[float], points: int) -> tuple[list[float], int]:
    """Fewer points for drawing, keeping each stretch's highest value (peaks stay visible)."""
    if len(series) <= points:
        return series, 1
    step = -(-len(series) // points)
    return [max(series[i:i + step]) for i in range(0, len(series), step)], step


@router.get("/{project_id}/signals")
def get_signals(project_id: int, points: int = 1500, ctx: AppContext = Depends(get_ctx)) -> dict:
    """Loudness and motion curves, silences, scene changes and loud moments."""
    with ctx.session() as s:
        project = s.get(Project, project_id)
        if project is None:
            raise HTTPException(404, "Project not found")
        duration = (project.media or {}).get("duration") or 0
    folder = ctx.settings.project_dir(project_id)
    points = max(100, min(points, 6000))

    def load(name: str) -> dict:
        path = folder / name
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}

    audio, video = load("audio_signals.json"), load("video_signals.json")
    loud, loud_step = _shrink(audio.get("loudness") or [], points)
    motion, motion_step = _shrink(video.get("motion") or [], points)
    return {
        "duration": audio.get("duration") or duration,
        "has_audio": bool(audio),
        "has_video": bool(video),
        "loudness": loud,
        "loudness_hop": (audio.get("hop") or 0.5) * loud_step,
        "loudness_stats": audio.get("stats"),
        "motion": motion,
        "motion_hop": (video.get("hop") or 0.5) * motion_step,
        "silences": audio.get("silences") or [],
        "events": audio.get("events") or [],
        "cuts": video.get("cuts") or [],
    }


@router.get("/{project_id}/transcript")
def get_transcript(project_id: int, words: bool = False, ctx: AppContext = Depends(get_ctx)) -> dict:
    with ctx.session() as s:
        project = s.get(Project, project_id)
        if project is None:
            raise HTTPException(404, "Project not found")
        state = (project.analysis or {}).get("transcript") or {}
        rows = s.scalars(
            select(TranscriptSegment)
            .where(TranscriptSegment.project_id == project_id)
            .order_by(TranscriptSegment.start, TranscriptSegment.id)
        ).all()
        return {
            "language": state.get("language"),
            "model": state.get("model"),
            "done": bool(state.get("done")),
            "segments": [
                {"id": r.id, "start": r.start, "end": r.end, "text": r.text, **({"words": r.words} if words else {})}
                for r in rows
            ],
        }


@router.post("/{project_id}/analyze")
def analyze(project_id: int, ctx: AppContext = Depends(get_ctx)) -> dict:
    """Run the analysis again from scratch on a video that is already prepared."""
    with ctx.session() as s:
        project = s.get(Project, project_id)
        if project is None:
            raise HTTPException(404, "Project not found")
        if _busy(s, project_id):
            raise HTTPException(409, "This project is still processing. Cancel its job first.")
        if not project.proxy_path or not Path(project.proxy_path).is_file():
            raise HTTPException(409, "This video has not been prepared yet, so there is nothing to analyse.")
        reset_analysis(s, ctx.settings, project)
        project.status, project.status_detail = "processing", None
        s.commit()
        data = project_to_dict(project)
    ctx.worker.enqueue("analyze", project_id=project_id)
    ctx.bus.publish({"type": "project", "id": project_id})
    return data


def moment_to_dict(m: Moment) -> dict:
    return {
        "id": m.id,
        "rank": m.rank,
        "start": m.start,
        "end": m.end,
        "peak": m.peak,
        "category": m.category,
        "score": m.score,
        "subscores": m.subscores or {},
        # every score the moment has (AI and measured), with context blended from the AI and the transcript
        "parts": score_parts(m.subscores or {}, m.measured),
        "measured": m.measured or {},
        "flags": m.flags or {},
        "reason": m.reason,
        "excerpt": m.excerpt,
        "visual": m.visual,
        "visual_checked": m.visual_checked,
        "source": m.source,
        "selected": m.selected,
    }


@router.get("/{project_id}/moments")
def get_moments(project_id: int, ctx: AppContext = Depends(get_ctx)) -> list[dict]:
    with ctx.session() as s:
        if s.get(Project, project_id) is None:
            raise HTTPException(404, "Project not found")
        rows = s.scalars(
            select(Moment).where(Moment.project_id == project_id).order_by(Moment.rank, Moment.id)
        ).all()
        return [moment_to_dict(m) for m in rows]


@router.post("/{project_id}/detect")
def detect(project_id: int, ctx: AppContext = Depends(get_ctx)) -> dict:
    """Pick the moments again (asks the AI again), keeping the existing analysis."""
    with ctx.session() as s:
        project = s.get(Project, project_id)
        if project is None:
            raise HTTPException(404, "Project not found")
        if _busy(s, project_id):
            raise HTTPException(409, "This project is still processing. Cancel its job first.")
        if not (project.analysis or {}).get("transcript", {}).get("done"):
            raise HTTPException(409, "This video has not been analysed yet, so there is nothing to pick from.")
        project.status, project.status_detail = "processing", None
        s.commit()
        data = project_to_dict(project)
    ctx.worker.enqueue("detect", project_id=project_id)
    ctx.bus.publish({"type": "project", "id": project_id})
    return data


@router.patch("/{project_id}/options")
def update_options(project_id: int, body: ProjectOptions, ctx: AppContext = Depends(get_ctx)) -> dict:
    """Change what the project asks for.

    Existing moments are re-ranked at once and the clips are cut again with the
    new duration settings. Neither step asks the AI again.
    """
    follow_up = None
    with ctx.session() as s:
        project = s.get(Project, project_id)
        if project is None:
            raise HTTPException(404, "Project not found")
        before = normalise_options(project.options)
        after = body.model_dump()
        changed = {k for k in after if before.get(k) != after[k]}
        project.options = after
        reselect(s, project)
        has_moments = bool(s.scalar(select(func.count(Moment.id)).where(Moment.project_id == project_id)))
        if has_moments and not _busy(s, project_id):
            # do only as much again as the change needs: cuts, then music, then captions
            if changed & CUT_KEYS:
                follow_up = "cut"
            elif changed & MUSIC_KEYS:
                follow_up = "remix"
            elif changed & FRAME_KEYS:
                follow_up = "reframe"
            elif "caption_style" in changed or "part_labels" in changed or "credit_tag" in changed:
                follow_up = "caption"
            if follow_up:
                project.status, project.status_detail = "processing", None
        s.commit()
        data = project_to_dict(project)
    if follow_up:
        ctx.worker.enqueue(follow_up, project_id=project_id)
    ctx.bus.publish({"type": "project", "id": project_id})
    return data


def clip_to_dict(c: Clip) -> dict:
    return {
        "id": c.id,
        "moment_id": c.moment_id,
        "rank": c.rank,
        "part": c.part or 1,
        "parts": c.parts or 1,
        "start": c.start,
        "end": c.end,
        "segments": c.segments or [],
        "duration": c.duration,
        "category": c.category,
        "score": c.score,
        "valid": bool(c.valid),
        "report": c.report or {},
        "has_preview": bool(c.preview),
        "has_vertical": bool(c.vertical),
        "captions": c.captions,
        "music": c.music,
        "framing": _framing_view(c.framing),
        "final": _final_view(c.final),
        "meta": c.meta,
        "story": c.story,
        "exported": c.exported,
        "youtube": {k: v for k, v in (c.youtube or {}).items() if k != "session"} or None,
        "export_blocker": export_blocker(c) if c.final else None,
        "status": c.status,
        "review": c.review or "pending",
        "overridden": bool(c.overridden),
    }


shorts_router = APIRouter(prefix="/api/shorts", tags=["shorts"])


@shorts_router.get("")
def list_shorts(ctx: AppContext = Depends(get_ctx)) -> list[dict]:
    """Every clip that has been approved or rendered, newest project first, with its project's name and rights."""
    with ctx.session() as s:
        rows = s.execute(
            select(Clip, Project).join(Project, Project.id == Clip.project_id)
            .where((Clip.review == "approved") | Clip.final.is_not(None))
            .order_by(Project.created_at.desc(), Project.id.desc(), Clip.rank, Clip.part)
        ).all()
        return [{**clip_to_dict(c), "project": {"id": p.id, "title": p.title, "rights_status": p.rights_status,
                                                "creator": p.creator}} for c, p in rows]


@router.patch("/{project_id}/clips/{clip_id}/meta")
def edit_clip_text(project_id: int, clip_id: int, body: ClipText, ctx: AppContext = Depends(get_ctx)) -> dict:
    """Save the owner's own title, description, hashtags and pinned comment. No AI call."""
    with ctx.session() as s:
        clip = s.get(Clip, clip_id)
        if clip is None or clip.project_id != project_id:
            raise HTTPException(404, "Clip not found")
        tags = clean_hashtags(body.hashtags, None) if body.hashtags else []
        clip.meta = {**(clip.meta or {}), "title": " ".join(body.title.split()),
                     "description": body.description.strip(), "hashtags": tags,
                     "pinned_comment": " ".join(body.pinned_comment.split()), "edited": True,
                     "by": (clip.meta or {}).get("by", "owner")}
        s.commit()
        data = clip_to_dict(clip)
    ctx.bus.publish({"type": "project", "id": project_id})
    return data


@router.post("/{project_id}/metadata")
def write_again(project_id: int, body: WriteAgain, ctx: AppContext = Depends(get_ctx)) -> dict:
    """Ask the AI to write the named clips' text again (replacing any edits)."""
    with ctx.session() as s:
        project = s.get(Project, project_id)
        if project is None:
            raise HTTPException(404, "Project not found")
        if _busy(s, project_id):
            raise HTTPException(409, "This project is still processing. Wait for it to finish first.")
        ids = {c.id for c in s.scalars(select(Clip).where(Clip.project_id == project_id)).all()}
        if not set(body.clip_ids) <= ids:
            raise HTTPException(404, "Clip not found")
        project.status, project.status_detail = "processing", None
        s.commit()
        data = project_to_dict(project)
    ctx.worker.enqueue("text", project_id=project_id, params={"clip_ids": body.clip_ids, "force": True})
    ctx.bus.publish({"type": "project", "id": project_id})
    return data


@router.post("/{project_id}/export")
def export(project_id: int, body: ExportRequest, ctx: AppContext = Depends(get_ctx)) -> dict:
    """Copy finished Shorts with their text and rights record into the project's export folder.

    Every approved clip with a final render, or the clips named. A clip that failed its quality
    checks is only exported with `override`. Returns what was exported and what was not, and why.
    """
    done, refused = [], []
    with ctx.session() as s:
        project = s.get(Project, project_id)
        if project is None:
            raise HTTPException(404, "Project not found")
        clips = s.scalars(select(Clip).where(Clip.project_id == project_id).order_by(Clip.rank, Clip.part)).all()
        if body.clip_ids:
            chosen = [c for c in clips if c.id in set(body.clip_ids)]
            if len(chosen) != len(set(body.clip_ids)):
                raise HTTPException(404, "Clip not found")
        else:
            chosen = [c for c in clips if c.review == "approved" and c.final]
        if not chosen:
            raise HTTPException(409, "Nothing to export: approve clips and render them as final Shorts first.")
        folder = None
        for c in chosen:
            try:
                result = export_clip(ctx.settings.exports_dir, project, c, body.override)
            except ValueError as exc:
                refused.append({"clip_id": c.id, "rank": c.rank, "part": c.part, "reason": str(exc)})
                continue
            except OSError as exc:
                refused.append({"clip_id": c.id, "rank": c.rank, "part": c.part, "reason": f"Could not write: {exc}"})
                continue
            folder = result["folder"]
            c.exported = {"folder": result["folder"], "video": result["video"], "at": result["exported_at"],
                          "anyway": bool(body.override and not (c.final.get("qc") or {}).get("passed", True))}
            c.status = "exported"
            done.append({"clip_id": c.id, "rank": c.rank, "part": c.part, **result})
        s.commit()
    ctx.bus.publish({"type": "project", "id": project_id})
    return {"exported": done, "refused": refused,
            "folder": folder or str(export_folder(ctx.settings.exports_dir, project))}


def _framing_view(f: dict | None) -> dict | None:
    """What the dashboard shows about the framing (the camera path itself stays on the server)."""
    if not f:
        return None
    plan = f.get("plan") or {}
    return {"requested": f.get("requested"), "layout": f.get("layout"), "layout_label": f.get("layout_label"),
            "reason": f.get("reason"), "subject": f.get("subject"), "samples": f.get("samples"),
            "detector": f.get("detector"), "follow": plan.get("follow"),
            "zoom_at": plan.get("anchor") if plan.get("zoom") else None}


def _final_view(f: dict | None) -> dict | None:
    if not f:
        return None
    return {k: v for k, v in f.items() if k != "file"} | {"exists": bool(f.get("file") and Path(f["file"]).is_file())}


@router.patch("/{project_id}/clips/{clip_id}/framing")
def set_clip_framing(project_id: int, clip_id: int, body: ClipFraming, ctx: AppContext = Depends(get_ctx)) -> dict:
    """Choose this clip's layout by hand ("auto" = the project's choice) and render its preview again.

    Connected parts of one moment get the same layout, so they look alike.
    """
    with ctx.session() as s:
        clip = s.get(Clip, clip_id)
        if clip is None or clip.project_id != project_id:
            raise HTTPException(404, "Clip not found")
        if _busy(s, project_id):
            raise HTTPException(409, "This project is still processing. Wait for it to finish first.")
        siblings = [c for c in s.scalars(select(Clip).where(Clip.project_id == project_id)).all()
                    if c.id == clip.id or (clip.moment_id is not None and c.moment_id == clip.moment_id)]
        requested = None if body.layout == "auto" else body.layout
        for c in siblings:
            c.framing = {**(c.framing or {}), "requested": requested}
        project = s.get(Project, project_id)
        project.status, project.status_detail = "processing", None
        s.commit()
        ids = [c.id for c in siblings]
        data = clip_to_dict(clip)
    ctx.worker.enqueue("reframe", project_id=project_id, params={"clip_ids": ids})
    ctx.bus.publish({"type": "project", "id": project_id})
    return data


@router.post("/{project_id}/render-final")
def render_final(project_id: int, body: FinalRender, ctx: AppContext = Depends(get_ctx)) -> dict:
    """Render finished 1080x1920 Shorts: the clips named, or every approved clip. No AI call."""
    with ctx.session() as s:
        project = s.get(Project, project_id)
        if project is None:
            raise HTTPException(404, "Project not found")
        if _busy(s, project_id):
            raise HTTPException(409, "This project is still processing. Wait for it to finish first.")
        clips = s.scalars(select(Clip).where(Clip.project_id == project_id)).all()
        if body.clip_ids:
            chosen = [c for c in clips if c.id in set(body.clip_ids)]
            if len(chosen) != len(set(body.clip_ids)):
                raise HTTPException(404, "Clip not found")
            not_ok = [c for c in chosen if c.review != "approved"]
            if not_ok:
                raise HTTPException(409, "Only approved clips are rendered as final Shorts. Approve the clip first.")
        else:
            chosen = [c for c in clips if c.review == "approved"]
            if not chosen:
                raise HTTPException(409, "No clip is approved yet. Approve the clips you want, then render them.")
        project.status, project.status_detail = "processing", None
        s.commit()
        ids = [c.id for c in chosen]
        data = project_to_dict(project)
    ctx.worker.enqueue("final", project_id=project_id, params={"clip_ids": ids})
    ctx.bus.publish({"type": "project", "id": project_id})
    return data


@router.get("/{project_id}/clips/{clip_id}/final")
def get_clip_final(project_id: int, clip_id: int, download: bool = False,
                   ctx: AppContext = Depends(get_ctx)) -> FileResponse:
    """The finished 1080x1920 Short. With ?download=1 the browser saves it under its file name."""
    with ctx.session() as s:
        clip = s.get(Clip, clip_id)
        final = (clip.final or {}) if clip and clip.project_id == project_id else {}
    path = final.get("file")
    if not path or not Path(path).is_file():
        raise HTTPException(404, "This clip has not been rendered as a final Short")
    return FileResponse(path, media_type="video/mp4", filename=final.get("name") if download else None,
                        headers={"Cache-Control": "no-cache"})


@router.patch("/{project_id}/clips/{clip_id}/music")
def set_clip_music(project_id: int, clip_id: int, body: ClipMusicChoice, ctx: AppContext = Depends(get_ctx)) -> dict:
    """Choose this clip's music by hand (or none, or back to automatic) and render it again.

    It applies to every part of the same moment, so connected parts keep the same music.
    A track whose licence details are incomplete is refused here, not just hidden.
    """
    with ctx.session() as s:
        clip = s.get(Clip, clip_id)
        if clip is None or clip.project_id != project_id:
            raise HTTPException(404, "Clip not found")
        if _busy(s, project_id):
            raise HTTPException(409, "This project is still processing. Wait for it to finish first.")
        if body.mode == "track":
            if body.track_id is None:
                raise HTTPException(422, "Choose a track")
            track = s.get(Track, body.track_id)
            if track is None:
                raise HTTPException(404, "Track not found")
            info = track_info(track)
            if not info.usable:
                raise HTTPException(409, "This track cannot be used yet: " + " ".join(info.problems))
        key = clip.moment_id if clip.moment_id is not None else -clip.id
        forced = None if body.mode == "auto" else {"mode": body.mode, "track_id": body.track_id}
        plans = plan_project_music(s, ctx.settings, project_id, only_keys={key}, forced={key: forced} if forced else None)
        ids = []
        for c in s.scalars(select(Clip).where(Clip.project_id == project_id)).all():
            if c.id in plans:
                c.music = plans[c.id]
                ids.append(c.id)
        project = s.get(Project, project_id)
        project.status, project.status_detail = "processing", None
        s.commit()
        data = clip_to_dict(clip)
    ctx.worker.enqueue("caption", project_id=project_id, params={"clip_ids": ids})
    ctx.bus.publish({"type": "project", "id": project_id})
    return data


@router.get("/{project_id}/clips/{clip_id}/vertical")
def get_clip_vertical(project_id: int, clip_id: int, ctx: AppContext = Depends(get_ctx)) -> FileResponse:
    """The 9:16 preview with captions burned in."""
    with ctx.session() as s:
        clip = s.get(Clip, clip_id)
        path = clip.vertical if clip and clip.project_id == project_id else None
    if not path or not Path(path).is_file():
        raise HTTPException(404, "No captioned preview for this clip")
    return FileResponse(path, media_type="video/mp4", headers={"Cache-Control": "no-cache"})


@router.patch("/{project_id}/captions")
def update_captions(project_id: int, body: CaptionStyle, ctx: AppContext = Depends(get_ctx)) -> dict:
    """Change the project's caption style and re-caption every clip. The cuts are not touched; no AI call."""
    recaption = False
    with ctx.session() as s:
        project = s.get(Project, project_id)
        if project is None:
            raise HTTPException(404, "Project not found")
        if _busy(s, project_id):
            raise HTTPException(409, "This project is still processing. Wait for it to finish, then change captions.")
        options = normalise_options(project.options)
        options["caption_style"] = body.model_dump()
        project.options = options
        if s.scalar(select(func.count(Clip.id)).where(Clip.project_id == project_id)):
            project.status, project.status_detail = "processing", None
            recaption = True
        s.commit()
        data = project_to_dict(project)
    if recaption:
        ctx.worker.enqueue("caption", project_id=project_id)
    ctx.bus.publish({"type": "project", "id": project_id})
    return data


@router.get("/{project_id}/clips")
def get_clips(project_id: int, ctx: AppContext = Depends(get_ctx)) -> list[dict]:
    with ctx.session() as s:
        if s.get(Project, project_id) is None:
            raise HTTPException(404, "Project not found")
        rows = s.scalars(
            select(Clip).where(Clip.project_id == project_id).order_by(Clip.rank, Clip.part, Clip.id)
        ).all()
        return [clip_to_dict(c) for c in rows]


@router.get("/{project_id}/clips/{clip_id}/preview")
def get_clip_preview(project_id: int, clip_id: int, ctx: AppContext = Depends(get_ctx)) -> FileResponse:
    with ctx.session() as s:
        clip = s.get(Clip, clip_id)
        path = clip.preview if clip and clip.project_id == project_id else None
    if not path or not Path(path).is_file():
        raise HTTPException(404, "No preview for this clip")
    return FileResponse(path, media_type="video/mp4", headers={"Cache-Control": "no-cache"})


@router.patch("/{project_id}/clips/{clip_id}")
def review_clip(project_id: int, clip_id: int, body: ClipReview, ctx: AppContext = Depends(get_ctx)) -> dict:
    """Approve or reject a clip. A clip that failed its checks can only be approved with an explicit override."""
    with ctx.session() as s:
        clip = s.get(Clip, clip_id)
        if clip is None or clip.project_id != project_id:
            raise HTTPException(404, "Clip not found")
        if body.review == "approved" and not clip.valid and not body.override:
            why = (clip.report or {}).get("no_fit") or "it did not pass the checks for a complete ending"
            raise HTTPException(409, f"This clip is not approved automatically: {why} Approve it anyway to override.")
        clip.review = body.review
        clip.overridden = bool(body.review == "approved" and not clip.valid)
        s.commit()
        data = clip_to_dict(clip)
    ctx.bus.publish({"type": "project", "id": project_id})
    return data


@router.post("/{project_id}/cut")
def cut(project_id: int, ctx: AppContext = Depends(get_ctx)) -> dict:
    """Cut the clips again from the moments already picked (no AI call)."""
    with ctx.session() as s:
        project = s.get(Project, project_id)
        if project is None:
            raise HTTPException(404, "Project not found")
        if _busy(s, project_id):
            raise HTTPException(409, "This project is still processing. Cancel its job first.")
        if not s.scalar(select(func.count(Moment.id)).where(Moment.project_id == project_id)):
            raise HTTPException(409, "No moments have been picked yet, so there is nothing to cut.")
        project.status, project.status_detail = "processing", None
        s.commit()
        data = project_to_dict(project)
    ctx.worker.enqueue("cut", project_id=project_id)
    ctx.bus.publish({"type": "project", "id": project_id})
    return data


@router.delete("/{project_id}", status_code=204)
def delete_project(project_id: int, ctx: AppContext = Depends(get_ctx)) -> None:
    """Remove the project and the app's own files for it (downloads, preview copy, audio).

    A video file the user pointed to on their own disk is never touched.
    """
    with ctx.session() as s:
        project = s.get(Project, project_id)
        if project is None:
            raise HTTPException(404, "Project not found")
        if _busy(s, project_id):
            raise HTTPException(409, "This project is still processing. Cancel its job first.")
        s.delete(project)
        s.commit()
    shutil.rmtree(ctx.settings.project_dir(project_id), ignore_errors=True)
    ctx.bus.publish({"type": "project", "id": project_id})
