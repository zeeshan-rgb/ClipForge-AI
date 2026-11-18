"""Posting schedule: approved Shorts go live on YouTube at the owner's chosen times.

A Short is uploaded as **private with a publish time** (YouTube's `publishAt`),
and YouTube itself makes it public at that moment, even when this computer is
off. Approving a Short and having a schedule switched on is the owner's consent
to publish it at its slot; every rule of the confirmed Publish step still
applies (rights confirmed, approved, rendered, quality and Shorts checklist).

YouTube keeps videos from API projects it has not audited private. Then the
publish time may be dropped; ClipForge reads what YouTube kept and says so.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select

from . import youtube as yt
from .models import Clip, Job, Project, Setting, iso, utcnow

log = logging.getLogger("clipforge.schedule")

KEY = "schedule"
LEAD_MINUTES = 30  # a slot must be at least this far ahead (the upload and YouTube's processing need time)
HORIZON_HOURS = 48  # automatic scheduling fills slots this far ahead, which keeps uploads near the posting rate
MAX_PER_DAY = 6  # YouTube's default quota allows about six uploads a day


def local_timezone() -> str:
    """This computer's IANA time zone name (for example Asia/Kolkata), or UTC."""
    tz = os.environ.get("TZ")
    if tz:
        return tz
    try:
        target = os.path.realpath("/etc/localtime")
        if "zoneinfo/" in target:
            return target.split("zoneinfo/", 1)[1]
    except OSError:
        pass
    return "UTC"


DEFAULTS: dict[str, Any] = {"enabled": False, "auto": True, "times": ["12:00", "18:00"], "timezone": "",
                            "require_story": True}  # automatic mode waits for the owner's narration


def _valid_time(t: str) -> bool:
    try:
        h, m = t.split(":")
        return 0 <= int(h) < 24 and 0 <= int(m) < 60 and len(m) == 2
    except ValueError:
        return False


def load(s) -> dict[str, Any]:
    row = s.get(Setting, KEY)
    cfg = dict(DEFAULTS)
    if row and isinstance(row.value, dict):
        cfg.update({k: v for k, v in row.value.items() if k in DEFAULTS})
    cfg["timezone"] = cfg["timezone"] or local_timezone()
    return cfg


def save(s, values: dict[str, Any]) -> dict[str, Any]:
    cfg = load(s)
    if "times" in values and values["times"] is not None:
        times = sorted({t.strip() for t in values["times"] if t and t.strip()})
        bad = [t for t in times if not _valid_time(t)]
        if bad:
            raise ValueError(f"Not a time: {', '.join(bad)}. Use 24-hour HH:MM, for example 18:30.")
        if not times:
            raise ValueError("Add at least one posting time.")
        if len(times) > MAX_PER_DAY:
            raise ValueError(f"At most {MAX_PER_DAY} times a day: YouTube allows about {MAX_PER_DAY} uploads a day.")
        cfg["times"] = times
    if values.get("timezone"):
        try:
            ZoneInfo(values["timezone"])
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"Unknown time zone: {values['timezone']}") from exc
        cfg["timezone"] = values["timezone"]
    for k in ("enabled", "auto", "require_story"):
        if values.get(k) is not None:
            cfg[k] = bool(values[k])
    row = s.get(Setting, KEY)
    if row is None:
        s.add(Setting(key=KEY, value=cfg))
    else:
        row.value = cfg
    s.commit()
    return cfg


def _parse(at: str | None) -> datetime | None:
    if not at:
        return None
    dt = datetime.fromisoformat(at.replace("Z", "+00:00"))
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).replace(tzinfo=None)


def taken_slots(s) -> set[datetime]:
    out = set()
    for (video,) in s.execute(select(Clip.youtube).where(Clip.youtube.is_not(None))).all():
        at = _parse((video or {}).get("publish_at"))
        if at:
            out.add(at)
    return out


def slots(cfg: dict[str, Any], now: datetime, days: int = 30) -> Iterator[datetime]:
    """Every slot from now on (naive UTC), in order."""
    tz = ZoneInfo(cfg["timezone"])
    local_now = now.replace(tzinfo=timezone.utc).astimezone(tz)
    earliest = now + timedelta(minutes=LEAD_MINUTES)
    for d in range(days + 1):
        day = (local_now + timedelta(days=d)).date()
        for t in cfg["times"]:
            h, m = (int(x) for x in t.split(":"))
            at = datetime(day.year, day.month, day.day, h, m, tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None)
            if at >= earliest:
                yield at


def next_free(s, cfg: dict[str, Any], now: datetime, extra_taken: set[datetime] | None = None) -> datetime | None:
    taken = taken_slots(s) | (extra_taken or set())
    return next((at for at in slots(cfg, now) if at not in taken), None)


def to_api(at: datetime) -> str:
    """YouTube wants RFC 3339 in UTC."""
    return at.replace(microsecond=0).isoformat() + "Z"


def blocker(s, project: Project, clip: Clip) -> str | None:
    """Why this Short may not be scheduled: the same rules as publishing."""
    if clip.review != "approved":
        return "Approve this Short first."
    if not clip.final or not clip.final.get("file") or not Path(clip.final["file"]).is_file():
        return "Render the final Short first."
    from .pipelines import youtube_defaults

    override = bool((clip.youtube or {}).get("uploaded_anyway"))
    return yt.publish_blocker(project.rights_status, clip, override, youtube_defaults(s))


def narrated(clip: Clip) -> bool:
    """The final render carries the owner's narration, and nothing in the story changed since."""
    final = clip.final or {}
    told = final.get("story") or {}
    updated = (clip.story or {}).get("updated_at") or ""
    return float(told.get("seconds") or 0) >= 4 and updated <= (final.get("rendered_at") or "")


def project_busy(s, project_id: int) -> bool:
    return bool(s.scalar(select(func.count(Job.id)).where(Job.project_id == project_id,
                                                          Job.status.in_(("queued", "running")))))


def upcoming(s, now: datetime) -> list[dict[str, Any]]:
    rows = []
    for clip, project in s.execute(select(Clip, Project).join(Project, Project.id == Clip.project_id)
                                   .where(Clip.youtube.is_not(None))).all():
        video = clip.youtube or {}
        at = _parse(video.get("publish_at"))
        if not at:
            continue
        rows.append({"clip_id": clip.id, "project_id": project.id, "project": project.title,
                     "title": (clip.meta or {}).get("title") or video.get("title"), "publish_at": iso(at),
                     "past": at <= now, "video_id": video.get("video_id"), "url": video.get("url"),
                     "state": video.get("state"), "note": video.get("schedule_note")})
    return sorted(rows, key=lambda r: r["publish_at"])


def tick(settings, session_factory, worker, now: datetime | None = None) -> int:
    """Automatic mode: give approved, unscheduled Shorts the next slots within the horizon and upload them."""
    now = now or utcnow()
    if not (yt.load_token(settings) or {}).get("refresh_token"):
        return 0
    queued = 0
    with session_factory() as s:
        cfg = load(s)
        if not (cfg["enabled"] and cfg["auto"]):
            return 0
        horizon = now + timedelta(hours=HORIZON_HOURS)
        since = now - timedelta(days=1)
        uploads_today = sum(1 for (v,) in s.execute(select(Clip.youtube).where(Clip.youtube.is_not(None))).all()
                            if (_parse((v or {}).get("uploaded_at")) or datetime.min) > since
                            or (v or {}).get("state") == "scheduled")
        candidates = s.execute(
            select(Clip, Project).join(Project, Project.id == Clip.project_id)
            .where(Clip.review == "approved").order_by(Clip.project_id, Clip.rank, Clip.part)).all()
        by_project: dict[int, list[int]] = {}
        for clip, project in candidates:
            video = clip.youtube or {}
            if video.get("video_id") or video.get("publish_at") or video.get("unscheduled"):
                continue
            if project_busy(s, project.id) or blocker(s, project, clip):
                continue
            if cfg.get("require_story") and project.rights_status != "own" and not narrated(clip):
                continue
            if uploads_today >= MAX_PER_DAY:
                break
            at = next_free(s, cfg, now)
            if at is None or at > horizon:
                break
            clip.youtube = {**video, "publish_at": iso(at), "state": "scheduled", "scheduled_by": "auto"}
            s.commit()
            by_project.setdefault(project.id, []).append(clip.id)
            uploads_today += 1
            queued += 1
        for pid, ids in by_project.items():
            worker.enqueue("upload", project_id=pid, params={"clip_ids": ids, "override": False})
            log.info("Scheduled %d Short(s) of project %d", len(ids), pid)
    return queued
