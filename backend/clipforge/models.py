"""Database tables."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    """Naive UTC timestamp (SQLite does not keep time zones)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def iso(dt: datetime | None) -> str | None:
    return None if dt is None else dt.replace(tzinfo=timezone.utc).isoformat()


class Base(DeclarativeBase):
    pass


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(300))
    # local | youtube | twitch | kick
    source_kind: Mapped[str] = mapped_column(String(20))
    source_url: Mapped[str | None] = mapped_column(Text, default=None)
    source_path: Mapped[str | None] = mapped_column(Text, default=None)

    creator: Mapped[str | None] = mapped_column(String(200), default=None)
    # own | licensed | creator_permission | unverified
    rights_status: Mapped[str] = mapped_column(String(30), default="unverified")
    rights_note: Mapped[str | None] = mapped_column(Text, default=None)
    # the streamer whose recorded permission this project relies on (Permissions page)
    permission_id: Mapped[int | None] = mapped_column(Integer, default=None)

    options: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    media: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)
    thumbnail: Mapped[str | None] = mapped_column(Text, default=None)
    # what the platform says about the video (title, channel, licence, ...)
    source_meta: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)
    # 360p copy for analysis and preview, and 16 kHz mono audio for speech work
    proxy_path: Mapped[str | None] = mapped_column(Text, default=None)
    audio_path: Mapped[str | None] = mapped_column(Text, default=None)
    # analysis bookkeeping: transcript chunk plan and progress, language, model
    analysis: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)

    # created | processing | ready | needs_rights | failed
    status: Mapped[str] = mapped_column(String(30), default="created")
    status_detail: Mapped[str | None] = mapped_column(Text, default=None)

    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    jobs: Mapped[list[Job]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    clips: Mapped[list[Clip]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    transcript: Mapped[list[TranscriptSegment]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    moments: Mapped[list[Moment]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), default=None, index=True
    )
    kind: Mapped[str] = mapped_column(String(40))
    # queued | running | done | failed | cancelled
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    error: Mapped[str | None] = mapped_column(Text, default=None)
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    project: Mapped[Project | None] = relationship(back_populates="jobs")
    stages: Mapped[list[JobStage]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="JobStage.position",
        lazy="selectin",
    )


class JobStage(Base):
    __tablename__ = "job_stages"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(60))
    label: Mapped[str] = mapped_column(String(120))
    # pending | running | done | failed
    status: Mapped[str] = mapped_column(String(20), default="pending")
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    message: Mapped[str | None] = mapped_column(Text, default=None)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)
    started_at: Mapped[datetime | None] = mapped_column(default=None)
    finished_at: Mapped[datetime | None] = mapped_column(default=None)

    job: Mapped[Job] = relationship(back_populates="stages")


class Clip(Base):
    """A generated Short. Filled in from Phase 5 onwards."""

    __tablename__ = "clips"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    # cut | rendered | exported   (how far through production it is)
    status: Mapped[str] = mapped_column(String(20), default="cut")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    # Phase 5: where it is cut, and why. All nullable so older databases upgrade in place.
    moment_id: Mapped[int | None] = mapped_column(Integer, default=None)
    rank: Mapped[int | None] = mapped_column(Integer, default=None)  # the moment's rank
    part: Mapped[int | None] = mapped_column(Integer, default=None)
    parts: Mapped[int | None] = mapped_column(Integer, default=None)
    start: Mapped[float | None] = mapped_column(Float, default=None)
    end: Mapped[float | None] = mapped_column(Float, default=None)
    # kept time ranges in order; dead air between them is left out
    segments: Mapped[list[Any] | None] = mapped_column(JSON, default=None)
    duration: Mapped[float | None] = mapped_column(Float, default=None)
    category: Mapped[str | None] = mapped_column(String(30), default=None)
    score: Mapped[int | None] = mapped_column(Integer, default=None)
    valid: Mapped[bool | None] = mapped_column(default=None)  # passed the context checks
    report: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)
    preview: Mapped[str | None] = mapped_column(Text, default=None)
    # Phase 6: vertical 9:16 preview with captions burned in, and the caption lines + checks
    vertical: Mapped[str | None] = mapped_column(Text, default=None)
    captions: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)
    # Phase 7: the background music chosen for this clip (or why there is none) and how it was mixed
    music: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)
    # Phase 8: how the shot is framed (layout, faces, camera path), and the final 1080x1920 file
    framing: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)
    final: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)
    # Phase 9: title, description, hashtags and pinned comment; and where it was last exported
    meta: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)
    exported: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)
    # Phase 10: the video on the owner's channel (id, privacy, when) and any upload in progress
    youtube: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)
    # pending | approved | rejected   (the user's decision)
    review: Mapped[str | None] = mapped_column(String(20), default="pending")
    overridden: Mapped[bool | None] = mapped_column(default=False)  # approved despite failing a check
    # Story mode: the owner's narration lines (text, recording, time), story cards and stickers
    story: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)

    project: Mapped[Project] = relationship(back_populates="clips")


class TranscriptSegment(Base):
    """One spoken line, timed on the whole video's clock."""

    __tablename__ = "transcript_segments"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    chunk: Mapped[int] = mapped_column(Integer)
    start: Mapped[float] = mapped_column(Float, index=True)
    end: Mapped[float] = mapped_column(Float)
    text: Mapped[str] = mapped_column(Text)
    # [{"s": start, "e": end, "w": word, "p": confidence}]
    words: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)

    project: Mapped[Project] = relationship(back_populates="transcript")


class Moment(Base):
    """A stretch of the video worth turning into a Short, with its scores."""

    __tablename__ = "moments"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    start: Mapped[float] = mapped_column(Float)
    end: Mapped[float] = mapped_column(Float)
    peak: Mapped[float] = mapped_column(Float)
    # the span the AI itself picked, before it was roughly fitted to a length;
    # the Smart Duration engine cuts from this (None on moments from older runs)
    core_start: Mapped[float | None] = mapped_column(Float, default=None)
    core_end: Mapped[float | None] = mapped_column(Float, default=None)
    # Phase 11.1: measured sound/picture quality and the transcript context check; flags such as no_dialogue
    measured: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)
    flags: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)
    category: Mapped[str] = mapped_column(String(30))
    score: Mapped[int] = mapped_column(Integer, index=True)
    # seven 0-10 sub-scores; the viral score is computed from these in code
    subscores: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    reason: Mapped[str] = mapped_column(Text, default="")
    excerpt: Mapped[str] = mapped_column(Text, default="")
    visual: Mapped[str | None] = mapped_column(Text, default=None)  # what the frames showed
    visual_checked: Mapped[bool] = mapped_column(default=False)
    source: Mapped[str] = mapped_column(String(20), default="ai_text")  # ai_text | signals
    rank: Mapped[int] = mapped_column(Integer, default=0)
    selected: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    project: Mapped[Project] = relationship(back_populates="moments")


class Track(Base):
    """A music track in the library. It can be used only when its licence details are complete."""

    __tablename__ = "tracks"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    file: Mapped[str] = mapped_column(Text)  # a copy inside the data folder
    original_name: Mapped[str | None] = mapped_column(String(300), default=None)
    duration: Mapped[float] = mapped_column(Float, default=0.0)
    bpm: Mapped[float | None] = mapped_column(Float, default=None)
    beats: Mapped[list[float]] = mapped_column(JSON, default=list)
    phase: Mapped[int] = mapped_column(Integer, default=0)  # which of four beats is the bar's first
    regularity: Mapped[float] = mapped_column(Float, default=0.0)  # 1 = perfectly steady beat
    loudness_db: Mapped[float] = mapped_column(Float, default=-20.0)
    channels: Mapped[int | None] = mapped_column(Integer, default=2)
    moods: Mapped[list[str]] = mapped_column(JSON, default=list)
    # licence record: all of these are needed (credit only if the licence asks for it)
    source: Mapped[str | None] = mapped_column(Text, default=None)
    licence_name: Mapped[str | None] = mapped_column(String(120), default=None)
    licence_url: Mapped[str | None] = mapped_column(Text, default=None)
    licence_note: Mapped[str | None] = mapped_column(Text, default=None)
    attribution: Mapped[str | None] = mapped_column(Text, default=None)
    added_at: Mapped[datetime] = mapped_column(default=utcnow)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)


class Streamer(Base):
    """A creator the owner asked (or will ask) for permission to clip, and what came of it."""

    __tablename__ = "streamers"

    id: Mapped[int] = mapped_column(primary_key=True)
    url: Mapped[str] = mapped_column(Text)
    platform: Mapped[str] = mapped_column(String(20))  # youtube | twitch | kick
    handle: Mapped[str | None] = mapped_column(String(120), default=None)
    name: Mapped[str | None] = mapped_column(String(200), default=None)
    followers: Mapped[int | None] = mapped_column(Integer, default=None)
    channel_id: Mapped[str | None] = mapped_column(String(120), default=None)
    channel_url: Mapped[str | None] = mapped_column(Text, default=None)
    description: Mapped[str | None] = mapped_column(Text, default=None)
    # public links and contact addresses found, each with where it was found
    links: Mapped[list[Any] | None] = mapped_column(JSON, default=None)
    emails: Mapped[list[Any] | None] = mapped_column(JSON, default=None)
    contact_email: Mapped[str | None] = mapped_column(String(320), default=None)

    # looking_up | no_contact | queued | sent | followed_up | replied | approved | declined
    # | no_reply | bounced | failed | paused
    status: Mapped[str] = mapped_column(String(30), default="looking_up")
    status_detail: Mapped[str | None] = mapped_column(Text, default=None)
    subject: Mapped[str | None] = mapped_column(Text, default=None)
    body: Mapped[str | None] = mapped_column(Text, default=None)

    thread_id: Mapped[str | None] = mapped_column(String(64), default=None)
    first_message_id: Mapped[str | None] = mapped_column(Text, default=None)  # Message-ID header, for the follow-up
    sent_at: Mapped[datetime | None] = mapped_column(default=None)
    followup_at: Mapped[datetime | None] = mapped_column(default=None)
    checked_at: Mapped[datetime | None] = mapped_column(default=None)
    reply: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)

    evidence: Mapped[str | None] = mapped_column(Text, default=None)  # what the owner recorded when approving
    conditions: Mapped[str | None] = mapped_column(Text, default=None)  # e.g. "no monetisation"
    decided_at: Mapped[datetime | None] = mapped_column(default=None)

    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
