"""Permissions page: the owner's Gmail, the list of streamers asked, and the owner's decisions."""

from __future__ import annotations

import secrets
import threading
import time

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from .. import gmail
from .. import outreach as ox
from .. import youtube as yt
from ..context import AppContext, get_ctx
from ..models import Project, Streamer, iso, utcnow
from .youtube import _page

router = APIRouter(prefix="/api", tags=["outreach"])

_PENDING: dict[str, tuple[str, str, float]] = {}
_LOCK = threading.Lock()
PENDING_SECONDS = 15 * 60
EMAIL = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class AddStreamer(BaseModel):
    url: str = Field(min_length=4, max_length=500)
    email: str | None = Field(default=None, max_length=320)


class BulkStreamers(BaseModel):
    """One streamer per line: a channel link, optionally followed by a comma and an email."""

    text: str = Field(min_length=1, max_length=50000)


class EditStreamer(BaseModel):
    contact_email: str | None = Field(default=None, max_length=320)
    subject: str | None = Field(default=None, max_length=300)
    body: str | None = Field(default=None, max_length=10000)


class Decision(BaseModel):
    evidence: str | None = Field(default=None, max_length=2000)
    conditions: str | None = Field(default=None, max_length=2000)


class OutreachSettings(BaseModel):
    auto_send: bool | None = None
    daily_limit: int | None = None
    spacing_minutes: int | None = None
    followup_days: int | None = None
    close_days: int | None = None
    sender_name: str | None = Field(default=None, max_length=120)
    channel_name: str | None = Field(default=None, max_length=200)
    channel_url: str | None = Field(default=None, max_length=300)
    subject: str | None = Field(default=None, max_length=300)
    body: str | None = Field(default=None, max_length=10000)
    followup_body: str | None = Field(default=None, max_length=10000)


def _status(ctx: AppContext) -> dict:
    token = gmail.load_token(ctx.settings) or {}
    with ctx.session() as s:
        cfg = ox.load_settings(s)
        eff = ox.effective(ctx.settings, cfg)
        state = ox.get_state(s)
        now = utcnow()
        recent = ox.sends_in_last_day(s, now)
        counts: dict[str, int] = {}
        for (st,) in s.execute(select(Streamer.status)).all():
            counts[st] = counts.get(st, 0) + 1
    return {
        "client": yt.client_file(ctx.settings).is_file(),
        "connected": bool(token.get("refresh_token")), "address": token.get("address"),
        "connected_at": token.get("connected_at"), "scopes": gmail.SCOPES,
        "settings": cfg, "channel_name": eff["channel_name"], "channel_url": eff["channel_url"],
        "missing": ox.missing_sender_details(eff),
        "sent_last_day": len(recent), "last_error": state.get("last_error"), "last_tick": state.get("last_tick"),
        "counts": counts,
    }


def _wake(ctx: AppContext) -> None:
    worker = getattr(ctx, "outreach", None)
    if worker is not None:
        worker.wake()


@router.get("/outreach")
def status(ctx: AppContext = Depends(get_ctx)) -> dict:
    return _status(ctx)


@router.put("/outreach/settings")
def put_settings(body: OutreachSettings, ctx: AppContext = Depends(get_ctx)) -> dict:
    with ctx.session() as s:
        ox.save_settings(s, body.model_dump(exclude_none=True))
    _wake(ctx)
    return _status(ctx)


@router.post("/outreach/preview")
def preview(body: OutreachSettings, ctx: AppContext = Depends(get_ctx)) -> dict:
    """The request as a streamer would read it, with an example name."""
    with ctx.session() as s:
        cfg = ox.effective(ctx.settings, {**ox.load_settings(s), **body.model_dump(exclude_none=True)})
    sample = Streamer(url="", platform="twitch", name="ExampleStreamer", handle="examplestreamer")
    subject, text = ox.draft(sample, cfg)
    follow = ox.fill(cfg["followup_body"], ox.template_values(sample, cfg))
    return {"subject": subject, "body": text, "followup": follow}


# ---- Gmail sign-in -----------------------------------------------------------------------------------


@router.post("/gmail/connect")
def connect(request: Request, ctx: AppContext = Depends(get_ctx)) -> dict:
    try:
        info = gmail.client(ctx.settings)
    except gmail.Refused as exc:
        raise HTTPException(409, str(exc)) from exc
    port = request.url.port or ctx.settings.port
    redirect = f"http://127.0.0.1:{port}/api/gmail/callback"
    verifier, challenge = yt.pkce_pair()
    state = secrets.token_urlsafe(24)
    with _LOCK:
        now = time.time()
        for k in [k for k, v in _PENDING.items() if now - v[2] > PENDING_SECONDS]:
            del _PENDING[k]
        _PENDING[state] = (verifier, redirect, now)
    return {"url": gmail.auth_url(info, redirect, state, challenge)}


@router.get("/gmail/callback", response_class=HTMLResponse)
def callback(state: str = "", code: str = "", error: str = "", ctx: AppContext = Depends(get_ctx)) -> HTMLResponse:
    with _LOCK:
        pending = _PENDING.pop(state, None)
    if pending is None or time.time() - pending[2] > PENDING_SECONDS:
        return _page("Gmail not connected", "This sign-in link has expired or was not started here. Connect again.", False)
    if error or not code:
        return _page("Gmail not connected", f"Google reported: {error or 'no code was given'}.", False)
    verifier, redirect, _ = pending
    http = gmail.make_http()
    try:
        info = gmail.client(ctx.settings)
        token = gmail.exchange_code(http, info, code, verifier, redirect)
        token["address"] = gmail.fetch_address(http, token["access_token"])
        token["connected_at"] = iso(utcnow())
        gmail.save_token(ctx.settings, token)
    except gmail.GmailError as exc:
        return _page("Gmail not connected", str(exc), False)
    except httpx.HTTPError as exc:
        return _page("Gmail not connected", f"Google could not be reached ({exc}).", False)
    finally:
        http.close()
    ctx.bus.publish({"type": "outreach"})
    _wake(ctx)
    return _page("Gmail connected", f"Permission requests will be sent from {token['address']}, a few a day. "
                 "Replies are noticed automatically; you decide what counts as permission.", True)


@router.post("/gmail/disconnect")
def disconnect(ctx: AppContext = Depends(get_ctx)) -> dict:
    http = gmail.make_http()
    try:
        gmail.revoke(ctx.settings, http)
    finally:
        http.close()
    return _status(ctx)


# ---- streamers ----------------------------------------------------------------------------------------


def _get(s, streamer_id: int) -> Streamer:
    st = s.get(Streamer, streamer_id)
    if st is None:
        raise HTTPException(404, "Streamer not found")
    return st


def _done(ctx: AppContext, st: Streamer) -> dict:
    ctx.bus.publish({"type": "outreach"})
    _wake(ctx)
    return ox.streamer_to_dict(st)


@router.get("/streamers")
def list_streamers(ctx: AppContext = Depends(get_ctx)) -> list[dict]:
    with ctx.session() as s:
        rows = s.scalars(select(Streamer).order_by(Streamer.created_at.desc(), Streamer.id.desc())).all()
        return [ox.streamer_to_dict(st) for st in rows]


@router.post("/streamers", status_code=201)
def add_streamer(body: AddStreamer, ctx: AppContext = Depends(get_ctx)) -> dict:
    """Add a streamer by their channel link. Looking them up and writing to them happen in the background."""
    try:
        platform, handle, channel_id = ox.parse_link(body.url)
    except ox.LookupError_ as exc:
        raise HTTPException(422, str(exc)) from exc
    email = (body.email or "").strip().lower() or None
    if email and not __import__("re").match(EMAIL, email):
        raise HTTPException(422, "That email address does not look right.")
    with ctx.session() as s:
        if handle or channel_id:
            q = select(Streamer).where(Streamer.platform == platform)
            q = q.where(Streamer.handle == handle) if handle else q.where(Streamer.channel_id == channel_id)
            existing = s.scalars(q).first()
            if existing is not None:
                raise HTTPException(409, f"{existing.name or existing.handle} is already on the list.")
        st = Streamer(url=ox.normalise(body.url), platform=platform, handle=handle, channel_id=channel_id,
                      name=handle, contact_email=email, status="looking_up",
                      status_detail="Reading their public profile…")
        s.add(st)
        s.commit()
        return _done(ctx, st)


@router.post("/streamers/bulk", status_code=201)
def add_many(body: BulkStreamers, ctx: AppContext = Depends(get_ctx)) -> dict:
    """Add a whole list at once. Each is looked up in the background; sending keeps the daily limit."""
    import re

    lines = [ln.strip() for ln in body.text.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    if len(lines) > 300:
        raise HTTPException(422, "Add at most 300 streamers at a time.")
    added, skipped = [], []
    with ctx.session() as s:
        seen: set[tuple[str, str]] = set()
        for line in lines:
            parts = [p.strip() for p in re.split(r"[,;\t]| {2,}", line) if p.strip()]
            url = next((p for p in parts if "@" not in p or p.startswith(("http", "www.", "youtube.com"))), parts[0])
            email = next((p.lower() for p in parts if p is not url and re.match(EMAIL, p)), None)
            try:
                platform, handle, channel_id = ox.parse_link(url)
            except ox.LookupError_ as exc:
                skipped.append({"line": line, "reason": str(exc)})
                continue
            key = (platform, handle or channel_id or url)
            if key in seen:
                skipped.append({"line": line, "reason": "Listed twice."})
                continue
            seen.add(key)
            if handle or channel_id:
                q = select(Streamer).where(Streamer.platform == platform)
                q = q.where(Streamer.handle == handle) if handle else q.where(Streamer.channel_id == channel_id)
                existing = s.scalars(q).first()
                if existing is not None:
                    skipped.append({"line": line, "reason": f"Already on the list ({STATUS_WORD.get(existing.status, existing.status)})."})
                    continue
            st = Streamer(url=ox.normalise(url), platform=platform, handle=handle, channel_id=channel_id, name=handle,
                          contact_email=email, status="looking_up", status_detail="Waiting to read their public profile…")
            s.add(st)
            s.flush()
            added.append(ox.streamer_to_dict(st))
        s.commit()
    ctx.bus.publish({"type": "outreach"})
    _wake(ctx)
    return {"added": added, "skipped": skipped}


STATUS_WORD = {"approved": "permission given", "declined": "declined", "sent": "asked", "followed_up": "asked",
               "queued": "queued", "replied": "replied", "no_reply": "no reply", "no_contact": "needs an email"}


@router.patch("/streamers/{streamer_id}")
def edit_streamer(streamer_id: int, body: EditStreamer, ctx: AppContext = Depends(get_ctx)) -> dict:
    import re

    with ctx.session() as s:
        st = _get(s, streamer_id)
        if st.status in ("sent", "followed_up", "replied", "approved", "declined", "no_reply") and \
                (body.subject is not None or body.body is not None):
            raise HTTPException(409, "This request was already sent; its text can no longer change.")
        if body.contact_email is not None:
            email = body.contact_email.strip().lower()
            if email and not re.match(EMAIL, email):
                raise HTTPException(422, "That email address does not look right.")
            if st.status in ("sent", "followed_up") and email != st.contact_email:
                raise HTTPException(409, "A request is already on its way to this streamer.")
            st.contact_email = email or None
            if st.status in ("no_contact", "bounced", "failed", "paused") and st.contact_email:
                st.thread_id, st.sent_at, st.followup_at, st.reply = None, None, None, None
                st.subject = st.body = None
                ox.queue(s, st, ox.effective(ctx.settings, ox.load_settings(s)))
        if body.subject is not None:
            st.subject = body.subject.strip() or None
        if body.body is not None:
            st.body = body.body.strip() + "\n" if body.body.strip() else None
        s.commit()
        return _done(ctx, st)


@router.post("/streamers/{streamer_id}/send")
def send_now(streamer_id: int, ctx: AppContext = Depends(get_ctx)) -> dict:
    with ctx.session() as s:
        st = _get(s, streamer_id)
        try:
            ox.send_now(ctx.settings, s, st)
        except gmail.Refused as exc:
            raise HTTPException(409, str(exc)) from exc
        except gmail.GmailError as exc:
            raise HTTPException(502, str(exc)) from exc
        return _done(ctx, st)


@router.post("/streamers/{streamer_id}/check")
def check_now(streamer_id: int, ctx: AppContext = Depends(get_ctx)) -> dict:
    """Read the thread for a reply right away."""
    with ctx.session() as s:
        st = _get(s, streamer_id)
        if not st.thread_id:
            raise HTTPException(409, "Nothing has been sent to this streamer yet.")
        token = gmail.load_token(ctx.settings) or {}
        http = gmail.make_http()
        try:
            ox.check_reply(ctx.settings, http, s, st, token.get("address") or "", utcnow())
        except gmail.GmailError as exc:
            raise HTTPException(502, str(exc)) from exc
        finally:
            http.close()
        return _done(ctx, st)


@router.post("/streamers/{streamer_id}/lookup")
def lookup_again(streamer_id: int, ctx: AppContext = Depends(get_ctx)) -> dict:
    with ctx.session() as s:
        st = _get(s, streamer_id)
        if st.status in ("sent", "followed_up", "replied", "approved"):
            raise HTTPException(409, "This streamer has already been written to.")
        st.status, st.status_detail = "looking_up", "Reading their public profile…"
        s.commit()
        return _done(ctx, st)


@router.post("/streamers/{streamer_id}/approve")
def approve(streamer_id: int, body: Decision, ctx: AppContext = Depends(get_ctx)) -> dict:
    """The owner records that this streamer gave permission. Needs their reply, or written evidence."""
    with ctx.session() as s:
        st = _get(s, streamer_id)
        evidence = (body.evidence or "").strip()
        if not st.reply and not evidence:
            raise HTTPException(422, "Say where the permission comes from (for example a link to their clipping rules, "
                                     "or the date of their message). Silence is never permission.")
        st.status, st.evidence = "approved", evidence or None
        st.conditions = (body.conditions or "").strip() or None
        st.decided_at = utcnow()
        st.status_detail = "Approved by you. New projects from this streamer get “creator permission” with this record."
        s.commit()
        ox.save_evidence(ctx.settings, st)
        return _done(ctx, st)


@router.post("/streamers/{streamer_id}/decline")
def decline(streamer_id: int, body: Decision, ctx: AppContext = Depends(get_ctx)) -> dict:
    with ctx.session() as s:
        st = _get(s, streamer_id)
        st.status, st.decided_at = "declined", utcnow()
        st.evidence = (body.evidence or "").strip() or None
        st.status_detail = "Declined. ClipForge will not write to them again or use their videos with permission."
        for p in s.scalars(select(Project).where(Project.permission_id == st.id)).all():
            p.rights_status = "unverified"
            p.rights_note = f"Permission withdrawn: {st.name or st.handle} declined on {iso(st.decided_at)}."
        s.commit()
        ox.save_evidence(ctx.settings, st)
        return _done(ctx, st)


@router.delete("/streamers/{streamer_id}", status_code=204)
def delete_streamer(streamer_id: int, ctx: AppContext = Depends(get_ctx)) -> None:
    with ctx.session() as s:
        st = _get(s, streamer_id)
        if st.status in ("approved", "declined"):
            raise HTTPException(409, "Keep the record of an approval or a refusal; it is the evidence.")
        s.delete(st)
        s.commit()
    ctx.bus.publish({"type": "outreach"})


@router.get("/streamers/approved")
def approved(ctx: AppContext = Depends(get_ctx)) -> list[dict]:
    """Streamers who gave permission, for the New project page."""
    with ctx.session() as s:
        rows = s.scalars(select(Streamer).where(Streamer.status == "approved").order_by(Streamer.name)).all()
        return [{"id": st.id, "name": st.name or st.handle, "platform": st.platform, "channel_url": st.channel_url,
                 "conditions": st.conditions, "note": ox.evidence_note(st)} for st in rows]
