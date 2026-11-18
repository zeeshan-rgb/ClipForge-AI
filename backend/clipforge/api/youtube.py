"""The owner's YouTube channel: connect, upload as private, publish with confirmation.

The sign-in happens on Google's own page in the owner's browser; this app only
receives a one-time code at /api/youtube/callback and never sees a password.
"""

from __future__ import annotations

import html
import secrets
import threading
import time
from datetime import timedelta
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select

import httpx

from .. import schedule
from .. import youtube as yt
from ..context import AppContext, get_ctx
from ..rights import PUBLISHABLE
from ..models import Clip, Job, Project, Setting, iso, utcnow
from ..pipelines import YOUTUBE_DEFAULTS_KEY, youtube_defaults
from ..schemas import project_to_dict
from .projects import clip_to_dict

router = APIRouter(prefix="/api", tags=["youtube"])

_PENDING: dict[str, tuple[str, str, float]] = {}  # state -> (code verifier, redirect uri, when)
_LOCK = threading.Lock()
PENDING_SECONDS = 15 * 60


class ClientFile(BaseModel):
    path: str


class Defaults(BaseModel):
    category_id: str = Field(default="20", pattern=r"^\d{1,3}$")
    made_for_kids: bool = False


class UploadRequest(BaseModel):
    clip_ids: list[int] = Field(min_length=1)
    override: bool = False  # upload even though a quality check failed


class PublishRequest(BaseModel):
    privacy: Literal["public", "unlisted", "private"] = "public"
    confirm: bool = False  # the owner's explicit yes for this clip
    title: str  # the title the owner saw when confirming; must still match
    override: bool = False  # publish even though a quality check failed


def _status(ctx: AppContext) -> dict:
    client_ok, client_error, project_id = False, None, None
    path = yt.client_file(ctx.settings)
    if path.is_file():
        try:
            project_id = yt.read_client(path).project_id
            client_ok = True
        except yt.Refused as exc:
            client_error = str(exc)
    token = yt.load_token(ctx.settings) or {}
    with ctx.session() as s:
        defaults = youtube_defaults(s)
    return {
        "client": client_ok, "client_error": client_error, "cloud_project": project_id,
        "connected": bool(token.get("refresh_token")), "channel": token.get("channel"),
        "connected_at": token.get("connected_at"), "scopes": yt.SCOPES, "defaults": defaults,
        "categories": yt.CATEGORIES,
    }


@router.get("/youtube")
def status(ctx: AppContext = Depends(get_ctx)) -> dict:
    return _status(ctx)


@router.post("/youtube/client")
def set_client(body: ClientFile, ctx: AppContext = Depends(get_ctx)) -> dict:
    """Use the OAuth client file the owner downloaded from their Google Cloud project."""
    source = Path(body.path.strip().strip('"'))
    if not source.is_file():
        raise HTTPException(422, f"File not found: {source}")
    try:
        yt.save_client(ctx.settings, source)
    except yt.Refused as exc:
        raise HTTPException(422, str(exc)) from exc
    return _status(ctx)


@router.post("/youtube/connect")
def connect(request: Request, ctx: AppContext = Depends(get_ctx)) -> dict:
    """The address of Google's sign-in page, for the dashboard to open in the owner's browser."""
    try:
        client = yt.read_client(yt.client_file(ctx.settings))
    except yt.Refused as exc:
        raise HTTPException(409, "Add the client file from Google Cloud first. " + str(exc)) from exc
    port = request.url.port or ctx.settings.port
    redirect = f"http://127.0.0.1:{port}/api/youtube/callback"
    verifier, challenge = yt.pkce_pair()
    state = secrets.token_urlsafe(24)
    with _LOCK:
        now = time.time()
        for k in [k for k, v in _PENDING.items() if now - v[2] > PENDING_SECONDS]:
            del _PENDING[k]
        _PENDING[state] = (verifier, redirect, now)
    return {"url": yt.auth_url(client, redirect, state, challenge)}


def _page(title: str, text: str, ok: bool) -> HTMLResponse:
    colour = "#2f9e44" if ok else "#c92a2a"
    return HTMLResponse(
        f"<!doctype html><meta charset=utf-8><title>{html.escape(title)}</title>"
        f"<body style='font-family:system-ui;background:#111;color:#eee;display:grid;place-items:center;height:90vh'>"
        f"<div style='max-width:32rem'><h2 style='color:{colour}'>{html.escape(title)}</h2>"
        f"<p>{html.escape(text)}</p><p><a style='color:#8cf' href='/settings'>Back to ClipForge settings</a></p></div>",
        status_code=200 if ok else 400,
    )


@router.get("/youtube/callback", response_class=HTMLResponse)
def callback(state: str = "", code: str = "", error: str = "", ctx: AppContext = Depends(get_ctx)) -> HTMLResponse:
    """Where Google sends the owner back after they sign in."""
    with _LOCK:
        pending = _PENDING.pop(state, None)
    if pending is None or time.time() - pending[2] > PENDING_SECONDS:
        return _page("Not connected", "This sign-in link has expired or was not started here. Connect again.", False)
    if error or not code:
        return _page("Not connected", f"Google reported: {error or 'no code was given'}.", False)
    verifier, redirect, _ = pending
    http = yt.make_http()
    try:
        client = yt.read_client(yt.client_file(ctx.settings))
        token = yt.exchange_code(http, client, code, verifier, redirect)
        token["channel"] = yt.fetch_channel(http, token["access_token"])
        token["connected_at"] = iso(utcnow())
        yt.save_token(ctx.settings, token)
    except yt.YouTubeError as exc:
        return _page("Not connected", str(exc), False)
    except httpx.HTTPError as exc:
        return _page("Not connected", f"Google could not be reached ({exc}). Check the internet connection.", False)
    finally:
        http.close()
    ctx.bus.publish({"type": "youtube"})
    return _page("YouTube connected", f"ClipForge can now upload to “{token['channel']['title']}”. "
                 "Every Short is uploaded as private; nothing becomes public without your confirmation.", True)


@router.post("/youtube/disconnect")
def disconnect(ctx: AppContext = Depends(get_ctx)) -> dict:
    http = yt.make_http()
    try:
        yt.revoke(ctx.settings, http)
    finally:
        http.close()
    return _status(ctx)


@router.put("/youtube/defaults")
def set_defaults(body: Defaults, ctx: AppContext = Depends(get_ctx)) -> dict:
    if body.category_id not in yt.CATEGORIES:
        raise HTTPException(422, "Unknown category")
    with ctx.session() as s:
        row = s.get(Setting, YOUTUBE_DEFAULTS_KEY)
        if row is None:
            s.add(Setting(key=YOUTUBE_DEFAULTS_KEY, value=body.model_dump()))
        else:
            row.value = body.model_dump()
        s.commit()
    return _status(ctx)


def _busy(s, project_id: int) -> bool:
    return bool(s.scalar(select(func.count(Job.id)).where(Job.project_id == project_id,
                                                         Job.status.in_(("queued", "running")))))


@router.post("/projects/{project_id}/upload")
def upload(project_id: int, body: UploadRequest, ctx: AppContext = Depends(get_ctx)) -> dict:
    """Upload approved, rendered Shorts as private videos. Refused outright for unconfirmed rights."""
    with ctx.session() as s:
        project = s.get(Project, project_id)
        if project is None:
            raise HTTPException(404, "Project not found")
        if _busy(s, project_id):
            raise HTTPException(409, "This project is still processing. Wait for it to finish first.")
        clips = {c.id: c for c in s.scalars(select(Clip).where(Clip.project_id == project_id)).all()}
        if not set(body.clip_ids) <= set(clips):
            raise HTTPException(404, "Clip not found")
        for cid in body.clip_ids:
            why = yt.publish_blocker(project.rights_status, clips[cid], body.override)
            if why:
                raise HTTPException(409, why)
        if not (yt.load_token(ctx.settings) or {}).get("refresh_token"):
            raise HTTPException(409, "No YouTube channel is connected. Connect one in Settings.")
        project.status, project.status_detail = "processing", None
        s.commit()
        data = project_to_dict(project)
    ctx.worker.enqueue("upload", project_id=project_id, params={"clip_ids": body.clip_ids, "override": body.override})
    ctx.bus.publish({"type": "project", "id": project_id})
    return data


@router.post("/projects/{project_id}/clips/{clip_id}/publish")
def publish(project_id: int, clip_id: int, body: PublishRequest, ctx: AppContext = Depends(get_ctx)) -> dict:
    """Change an uploaded Short's privacy. Only with the owner's explicit confirmation, checked here."""
    if not body.confirm:
        raise HTTPException(409, "Publishing needs your explicit confirmation for this Short.")
    with ctx.session() as s:
        project = s.get(Project, project_id)
        clip = s.get(Clip, clip_id)
        if project is None or clip is None or clip.project_id != project_id:
            raise HTTPException(404, "Clip not found")
        video = clip.youtube or {}
        if not video.get("video_id"):
            raise HTTPException(409, "Upload this Short as private first.")
        if body.privacy != "private":
            if project.rights_status not in PUBLISHABLE:
                raise HTTPException(409, "Refused: the rights to this video are not confirmed, so it cannot be "
                                         "made public or unlisted.")
            with_override = body.override or bool(video.get("uploaded_anyway"))
            why = yt.publish_blocker(project.rights_status, clip, with_override, youtube_defaults(s))
            if why:
                raise HTTPException(409, f"Not published. {why}")
        if body.title.strip() != (video.get("title") or "").strip():
            raise HTTPException(409, "The title you confirmed is not the one on YouTube. Reload and confirm again.")
        video_id = video["video_id"]
    http = yt.make_http()
    try:
        result = yt.set_privacy(ctx.settings, http, video_id, body.privacy)
    except yt.YouTubeError as exc:
        raise HTTPException(409 if isinstance(exc, yt.Refused) else 502, str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"YouTube could not be reached ({exc}).") from exc
    finally:
        http.close()
    actual = result.get("privacyStatus", body.privacy)
    note = None
    if actual != body.privacy:
        note = ("YouTube kept this video private. Videos uploaded through a Google Cloud project that has not "
                "passed YouTube's API audit stay private; publish it yourself in YouTube Studio, or apply for "
                "the audit.")
    with ctx.session() as s:
        clip = s.get(Clip, clip_id)
        clip.youtube = {**(clip.youtube or {}), "privacy": actual, "note": note,
                        "published_at": iso(utcnow()) if actual != "private" else None,
                        "requested_privacy": body.privacy}
        s.commit()
        data = clip_to_dict(clip)
    ctx.bus.publish({"type": "project", "id": project_id})
    return data


@router.get("/projects/{project_id}/clips/{clip_id}/publish-checklist")
def publish_checklist(project_id: int, clip_id: int, ctx: AppContext = Depends(get_ctx)) -> dict:
    """The YouTube Shorts checklist for one Short: the same rules the upload and publish steps enforce."""
    with ctx.session() as s:
        project = s.get(Project, project_id)
        clip = s.get(Clip, clip_id)
        if project is None or clip is None or clip.project_id != project_id:
            raise HTTPException(404, "Clip not found")
        token = yt.load_token(ctx.settings) or {}
        result = yt.shorts_checklist(project.rights_status, clip, connected=bool(token.get("refresh_token")),
                                     channel=(token.get("channel") or {}).get("title"), defaults=youtube_defaults(s))
        video = clip.youtube or {}
        meta = clip.meta or {}
        sent_title = yt.video_body(meta, "20", False)["snippet"]["title"] if meta.get("title") else ""
        result["text_differs"] = bool(video.get("video_id")) and (
            sent_title.strip() != (video.get("title") or "").strip()
            or meta.get("description") != video.get("description_sent", meta.get("description")))
        return result


@router.post("/projects/{project_id}/clips/{clip_id}/youtube-text")
def send_text(project_id: int, clip_id: int, ctx: AppContext = Depends(get_ctx)) -> dict:
    """Send the Short's edited title, description and hashtags to its video on YouTube."""
    with ctx.session() as s:
        project = s.get(Project, project_id)
        clip = s.get(Clip, clip_id)
        if project is None or clip is None or clip.project_id != project_id:
            raise HTTPException(404, "Clip not found")
        video = clip.youtube or {}
        if not video.get("video_id"):
            raise HTTPException(409, "This Short is not on YouTube yet; its text goes with the upload.")
        if project.rights_status not in PUBLISHABLE:
            raise HTTPException(409, yt.RIGHTS_REFUSAL)
        checklist = yt.shorts_checklist(project.rights_status, clip, connected=True, channel=None,
                                        defaults=youtube_defaults(s))
        bad = [i for i in checklist["items"] if i["status"] == "fail" and i["name"] in ("title", "description", "hashtags")]
        if bad:
            raise HTTPException(409, f"{bad[0]['label']}: {bad[0]['detail']}")
        meta = dict(clip.meta)
        category = youtube_defaults(s)["category_id"]
        video_id = video["video_id"]
    http = yt.make_http()
    try:
        snippet = yt.update_text(ctx.settings, http, video_id, meta, category)
    except yt.YouTubeError as exc:
        raise HTTPException(409 if isinstance(exc, yt.Refused) else 502, str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"YouTube could not be reached ({exc}).") from exc
    finally:
        http.close()
    with ctx.session() as s:
        clip = s.get(Clip, clip_id)
        clip.youtube = {**(clip.youtube or {}), "title": snippet.get("title", meta["title"]),
                        "description_sent": meta.get("description"), "text_updated_at": iso(utcnow())}
        s.commit()
        data = clip_to_dict(clip)
    ctx.bus.publish({"type": "project", "id": project_id})
    return data


@router.post("/projects/{project_id}/clips/{clip_id}/youtube-refresh")
def refresh(project_id: int, clip_id: int, ctx: AppContext = Depends(get_ctx)) -> dict:
    """Read the video's current state on YouTube (processing, privacy, removed)."""
    with ctx.session() as s:
        clip = s.get(Clip, clip_id)
        if clip is None or clip.project_id != project_id or not (clip.youtube or {}).get("video_id"):
            raise HTTPException(404, "This Short is not on YouTube")
        video_id = clip.youtube["video_id"]
    http = yt.make_http()
    try:
        item = yt.video_status(ctx.settings, http, video_id)
    except yt.YouTubeError as exc:
        raise HTTPException(409 if isinstance(exc, yt.Refused) else 502, str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"YouTube could not be reached ({exc}).") from exc
    finally:
        http.close()
    with ctx.session() as s:
        clip = s.get(Clip, clip_id)
        if item is None:
            clip.youtube = {**clip.youtube, "state": "removed", "note": "This video is no longer on your channel."}
        else:
            st = item.get("status") or {}
            clip.youtube = {**clip.youtube, "privacy": st.get("privacyStatus"), "upload_status": st.get("uploadStatus"),
                            "rejection": st.get("rejectionReason"), "failure": st.get("failureReason"),
                            "checked_at": iso(utcnow())}
        s.commit()
        data = clip_to_dict(clip)
    return data


# ---- posting schedule ---------------------------------------------------------------------------------


class ScheduleSettings(BaseModel):
    enabled: bool | None = None
    auto: bool | None = None
    require_story: bool | None = None
    times: list[str] | None = None
    timezone: str | None = None


class ScheduleRequest(BaseModel):
    confirm: bool = False  # the owner's yes: publish this Short at this time
    at: str | None = None  # a chosen time (ISO); default: the next free slot


def _schedule_status(ctx: AppContext) -> dict:
    now = utcnow()
    with ctx.session() as s:
        cfg = schedule.load(s)
        nxt = [iso(at) for at, _ in zip(
            (a for a in schedule.slots(cfg, now) if a not in schedule.taken_slots(s)), range(5))]
        return {"settings": cfg, "upcoming": schedule.upcoming(s, now), "next_slots": nxt,
                "connected": bool((yt.load_token(ctx.settings) or {}).get("refresh_token")),
                "horizon_hours": schedule.HORIZON_HOURS, "max_per_day": schedule.MAX_PER_DAY}


@router.get("/schedule")
def get_schedule(ctx: AppContext = Depends(get_ctx)) -> dict:
    return _schedule_status(ctx)


@router.put("/schedule")
def put_schedule(body: ScheduleSettings, ctx: AppContext = Depends(get_ctx)) -> dict:
    with ctx.session() as s:
        try:
            schedule.save(s, body.model_dump(exclude_none=True))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    if getattr(ctx, "outreach", None) is not None:
        ctx.outreach.wake()
    return _schedule_status(ctx)


@router.post("/projects/{project_id}/clips/{clip_id}/schedule")
def schedule_clip(project_id: int, clip_id: int, body: ScheduleRequest, ctx: AppContext = Depends(get_ctx)) -> dict:
    """Publish this Short at a set time: uploaded as private with that publish time, made public by YouTube."""
    if not body.confirm:
        raise HTTPException(409, "Scheduling needs your explicit confirmation for this Short.")
    if not (yt.load_token(ctx.settings) or {}).get("refresh_token"):
        raise HTTPException(409, "No YouTube channel is connected. Connect one in Settings.")
    now = utcnow()
    with ctx.session() as s:
        project = s.get(Project, project_id)
        clip = s.get(Clip, clip_id)
        if project is None or clip is None or clip.project_id != project_id:
            raise HTTPException(404, "Clip not found")
        why = schedule.blocker(s, project, clip)
        if why:
            raise HTTPException(409, f"Not scheduled. {why}")
        video = dict(clip.youtube or {})
        if video.get("privacy") and video["privacy"] != "private":
            raise HTTPException(409, "This Short is already public or unlisted.")
        cfg = schedule.load(s)
        if body.at:
            try:
                at = schedule._parse(body.at)
            except ValueError as exc:
                raise HTTPException(422, "That is not a date and time.") from exc
            if at < now + timedelta(minutes=schedule.LEAD_MINUTES):
                raise HTTPException(422, f"Choose a time at least {schedule.LEAD_MINUTES} minutes from now.")
        else:
            at = schedule.next_free(s, cfg, now)
            if at is None:
                raise HTTPException(409, "No free slot in the next 30 days. Add posting times.")
        uploaded = bool(video.get("video_id"))
        if not uploaded and schedule.project_busy(s, project_id):
            raise HTTPException(409, "This project is still processing. Wait for it to finish first.")
        video_id = video.get("video_id")
    if uploaded:
        http = yt.make_http()
        try:
            st = yt.set_publish_at(ctx.settings, http, video_id, schedule.to_api(at))
        except yt.YouTubeError as exc:
            raise HTTPException(409 if isinstance(exc, yt.Refused) else 502, str(exc)) from exc
        except httpx.HTTPError as exc:
            raise HTTPException(502, f"YouTube could not be reached ({exc}).") from exc
        finally:
            http.close()
        kept = bool(st.get("publishAt"))
        note = None if kept else ("YouTube did not keep the publish time, so this Short stays private (an API "
                                  "project YouTube has not audited). Publish it in YouTube Studio.")
    with ctx.session() as s:
        clip = s.get(Clip, clip_id)
        new = {**(clip.youtube or {}), "planned_publish_at": iso(at), "scheduled_by": "owner", "unscheduled": None}
        if uploaded:
            new.update(publish_at=iso(at) if kept else None, schedule_note=note)
        else:
            new.update(publish_at=iso(at), state="scheduled", schedule_note=None)
        clip.youtube = new
        if not uploaded:
            project = s.get(Project, project_id)
            project.status, project.status_detail = "processing", None
        s.commit()
        data = clip_to_dict(clip)
    if not uploaded:
        ctx.worker.enqueue("upload", project_id=project_id, params={"clip_ids": [clip_id], "override": False})
    ctx.bus.publish({"type": "project", "id": project_id})
    return data


@router.delete("/projects/{project_id}/clips/{clip_id}/schedule")
def unschedule_clip(project_id: int, clip_id: int, ctx: AppContext = Depends(get_ctx)) -> dict:
    """Take the publish time away: the Short stays (or will be uploaded) private. Automatic mode leaves it alone."""
    with ctx.session() as s:
        clip = s.get(Clip, clip_id)
        if clip is None or clip.project_id != project_id:
            raise HTTPException(404, "Clip not found")
        video = dict(clip.youtube or {})
        video_id = video.get("video_id")
    if video_id and video.get("publish_at"):
        http = yt.make_http()
        try:
            yt.set_publish_at(ctx.settings, http, video_id, None)
        except yt.YouTubeError as exc:
            raise HTTPException(409 if isinstance(exc, yt.Refused) else 502, str(exc)) from exc
        except httpx.HTTPError as exc:
            raise HTTPException(502, f"YouTube could not be reached ({exc}).") from exc
        finally:
            http.close()
    with ctx.session() as s:
        clip = s.get(Clip, clip_id)
        new = {**(clip.youtube or {}), "publish_at": None, "planned_publish_at": None, "schedule_note": None,
               "unscheduled": iso(utcnow())}
        if not video_id:
            new["state"] = None
        clip.youtube = new
        s.commit()
        data = clip_to_dict(clip)
    ctx.bus.publish({"type": "project", "id": project_id})
    return data
