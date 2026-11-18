"""Asking streamers for permission to clip them, from the owner's Gmail.

The owner pastes a streamer's channel link. ClipForge then, on its own:

1. reads the streamer's public profile (YouTube through yt-dlp, Twitch through
   its official API when keys are set, Kick through its public channel page)
   and any link-in-bio page it lists, and collects the contact addresses
   published there;
2. writes a short, personal request from the owner's template;
3. sends it from the owner's Gmail, a few a day and spaced out, so it reads as
   a person writing and not as bulk mail;
4. reads the thread for a reply, sends one polite follow-up if there is none
   after a week, and closes the request a fortnight after that.

What it never does: treat silence as permission, send twice to someone who
said no, or mark a streamer as approved. Approval is the owner's click, with
the reply kept as evidence; only then do projects from that streamer get
"creator permission" filled in.

ClipForge does not get past CAPTCHAs or sign-in walls. YouTube hides many
business addresses behind one ("View email address"); the owner can open it
and paste the address in.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import or_, select

from . import gmail
from . import youtube as yt
from .config import Settings
from .models import Setting, Streamer, iso, utcnow

log = logging.getLogger("clipforge.outreach")

SETTINGS_KEY = "outreach"
STATE_KEY = "outreach_state"

OPEN = ("queued", "sent", "followed_up")  # requests still in progress
CLOSED_NO = ("declined", "bounced")  # never write to this address again

DEFAULT_SUBJECT = "Permission to post Shorts of {streamer}'s streams"
DEFAULT_BODY = """Hi {streamer} team,

I run the YouTube channel {channel_name} ({channel_url}). I'd love to post short clips (under 60 seconds) of the best moments from {streamer}'s streams as YouTube Shorts.

Every Short would:
- credit {streamer} on screen and at the top of the description,
- link to the original stream and to your channel,
- only use public streams, never private or members-only content.

Shorts like these tend to send new viewers to the full streams. I'm happy to follow any rules you have: how you'd like to be credited, anything you'd rather I didn't post, and whether I may monetise the Shorts. If you run a clipping program, I'd be glad to join it. You can ask me to take anything down at any time.

Would that be OK, and under what conditions?

Thanks,
{sender_name}
{channel_name}

(If you'd rather not hear from me again, just reply "no" and I won't write again.)"""

DEFAULT_FOLLOWUP = """Hi {streamer} team,

Just following up on my note from last week about posting credited Shorts of {streamer}'s streams on {channel_name} ({channel_url}).

A quick yes, no, or "yes, with these rules" would be a great help. If I don't hear back, I won't post anything and won't write again.

Thanks,
{sender_name}"""

DEFAULTS: dict[str, Any] = {
    "auto_send": True,
    "daily_limit": 5,  # emails a day (first requests and follow-ups together)
    "spacing_minutes": 20,
    "followup_days": 7,
    "close_days": 14,  # after the follow-up with no reply, the request is closed as "no reply"
    "sender_name": "",
    "channel_name": "",
    "channel_url": "",
    "subject": DEFAULT_SUBJECT,
    "body": DEFAULT_BODY,
    "followup_body": DEFAULT_FOLLOWUP,
}
LIMITS = {"daily_limit": (1, 20), "spacing_minutes": (5, 24 * 60), "followup_days": (2, 30), "close_days": (3, 60)}
CHECK_MINUTES = 10  # how often an open thread is read for a reply
LOOKUPS_PER_ROUND = 5  # a long list is looked up a few at a time

# Pages people use as a "link in bio"; their contact addresses are often only there.
LINK_HUBS = ("linktr.ee", "beacons.ai", "bio.link", "solo.to", "lnk.bio", "campsite.bio", "carrd.co",
             "linkin.bio", "allmylinks.com", "msha.ke", "taplink.cc", "stan.store", "komi.io", "hoo.be")
BUSINESS_WORDS = ("business", "biz", "partner", "mgmt", "manage", "booking", "inquir", "enquir", "collab",
                  "sponsor", "contact", "media", "press", "talent", "agency", "team", "work")
EMAIL_RE = re.compile(r"(?<![\w.+-])([A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,24})(?![\w-])")
URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")
NOT_EMAIL = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", "example.com", "sentry.io", "wixpress.com",
             "domain.com", "email.com", "yourname")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"


class LookupError_(RuntimeError):
    pass


# ---- settings -------------------------------------------------------------------------------------


def load_settings(s) -> dict[str, Any]:
    row = s.get(Setting, SETTINGS_KEY)
    out = dict(DEFAULTS)
    if row and isinstance(row.value, dict):
        out.update({k: v for k, v in row.value.items() if k in DEFAULTS})
    return out


def save_settings(s, values: dict[str, Any]) -> dict[str, Any]:
    current = load_settings(s)
    for k, v in values.items():
        if k not in DEFAULTS or v is None:
            continue
        if k in LIMITS:
            lo, hi = LIMITS[k]
            v = max(lo, min(hi, int(v)))
        elif isinstance(DEFAULTS[k], bool):
            v = bool(v)
        else:
            v = str(v)
        current[k] = v
    for k in ("subject", "body", "followup_body"):
        if not current[k].strip():
            current[k] = DEFAULTS[k]
    row = s.get(Setting, SETTINGS_KEY)
    if row is None:
        s.add(Setting(key=SETTINGS_KEY, value=current))
    else:
        row.value = current
    s.commit()
    return current


def effective(settings: Settings, values: dict[str, Any]) -> dict[str, Any]:
    """Settings with the channel filled in from the connected YouTube channel when left empty."""
    out = dict(values)
    channel = (yt.load_token(settings) or {}).get("channel") or {}
    out["channel_name"] = out["channel_name"].strip() or channel.get("title") or ""
    out["channel_url"] = out["channel_url"].strip() or channel.get("url") or ""
    return out


def set_state(s, **values) -> None:
    row = s.get(Setting, STATE_KEY)
    value = dict(row.value) if row and isinstance(row.value, dict) else {}
    value.update(values)
    if row is None:
        s.add(Setting(key=STATE_KEY, value=value))
    else:
        row.value = value
    s.commit()


def get_state(s) -> dict[str, Any]:
    row = s.get(Setting, STATE_KEY)
    return dict(row.value) if row and isinstance(row.value, dict) else {}


class _Blank(dict):
    def __missing__(self, key):  # an unknown {field} in a template is left as it was written
        return "{" + key + "}"


def fill(template: str, values: dict[str, Any]) -> str:
    return template.format_map(_Blank({k: ("" if v is None else v) for k, v in values.items()}))


def template_values(streamer: Streamer, cfg: dict[str, Any]) -> dict[str, Any]:
    return {
        "streamer": streamer.name or streamer.handle or "there",
        "handle": streamer.handle or "",
        "platform": {"youtube": "YouTube", "twitch": "Twitch", "kick": "Kick"}.get(streamer.platform, streamer.platform),
        "channel_name": cfg.get("channel_name") or "my channel",
        "channel_url": cfg.get("channel_url") or "",
        "sender_name": cfg.get("sender_name") or cfg.get("channel_name") or "",
    }


def draft(streamer: Streamer, cfg: dict[str, Any]) -> tuple[str, str]:
    values = template_values(streamer, cfg)
    return fill(cfg["subject"], values).strip(), fill(cfg["body"], values).strip() + "\n"


def missing_sender_details(cfg: dict[str, Any]) -> str | None:
    if not cfg.get("sender_name", "").strip():
        return "Add your name (the sender) on the Permissions page before requests can go out."
    if not cfg.get("channel_name") or not cfg.get("channel_url"):
        return "Connect your YouTube channel, or type its name and link on the Permissions page."
    return None


# ---- reading a link ------------------------------------------------------------------------------


def parse_link(url: str) -> tuple[str, str | None, str | None]:
    """(platform, handle, channel id) from a channel or video link, as far as the link itself says."""
    url = (url or "").strip()
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    path = [p for p in parts.path.split("/") if p]
    if host in ("youtube.com", "youtu.be", "music.youtube.com"):
        if path and path[0].startswith("@"):
            return "youtube", path[0][1:].lower(), None
        if len(path) >= 2 and path[0] == "channel":
            return "youtube", None, path[1]
        if len(path) >= 2 and path[0] in ("c", "user"):
            return "youtube", path[1].lower(), None
        return "youtube", None, None  # a video: the channel is found by looking it up
    if host.endswith("twitch.tv"):
        if path and path[0] not in ("videos", "directory", "search", "p", "settings"):
            return "twitch", path[0].lower(), None
        return "twitch", None, None
    if host.endswith("kick.com"):
        if path and path[0] not in ("video", "categories", "search", "browse"):
            return "kick", path[0].lower(), None
        return "kick", None, None
    raise LookupError_("Paste a YouTube, Twitch or Kick channel link, for example https://www.youtube.com/@name.")


def normalise(url: str) -> str:
    url = url.strip()
    return url if re.match(r"^https?://", url, re.I) else "https://" + url


@dataclass
class Profile:
    platform: str
    handle: str | None = None
    name: str | None = None
    followers: int | None = None
    channel_id: str | None = None
    channel_url: str | None = None
    description: str = ""
    links: list[dict[str, str]] = field(default_factory=list)  # {"url", "found"}
    emails: list[dict[str, str]] = field(default_factory=list)  # {"email", "found"}
    notes: list[str] = field(default_factory=list)


def find_emails(text: str) -> list[str]:
    out: list[str] = []
    for m in EMAIL_RE.findall(text or ""):
        e = m.strip(".").lower()
        if any(bad in e for bad in NOT_EMAIL) or e in out:
            continue
        out.append(e)
    return out


def find_links(text: str) -> list[str]:
    out: list[str] = []
    for u in URL_RE.findall(text or ""):
        u = u.rstrip(".,;:!")
        if u not in out:
            out.append(u)
    return out


def _env(settings: Settings, name: str) -> str | None:
    value = os.environ.get(name)
    if value:
        return value.strip() or None
    env_file = settings.model_config.get("env_file")
    if env_file and Path(str(env_file)).is_file():
        for line in Path(str(env_file)).read_text(encoding="utf-8").splitlines():
            key, _, v = line.partition("=")
            if key.strip() == name and v.strip():
                return v.strip().strip('"').strip("'")
    return None


def make_web() -> httpx.Client:
    """The HTTP client for public web pages. Tests replace this."""
    return httpx.Client(timeout=httpx.Timeout(20.0, connect=10.0), follow_redirects=True,
                        headers={"User-Agent": UA, "Accept-Language": "en"})


def ytdlp_info(url: str) -> dict[str, Any]:
    """What yt-dlp can read about a channel or video without downloading anything. Tests replace this."""
    import yt_dlp

    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "extract_flat": True,
            "playlist_items": "0", "noprogress": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False) or {}


def _youtube(url: str, handle: str | None, channel_id: str | None) -> Profile:
    target = url
    if not handle and not channel_id:  # a video link: find its channel first
        info = ytdlp_info(url)
        target = info.get("channel_url") or info.get("uploader_url")
        if not target:
            raise LookupError_("Could not tell which channel this video belongs to. Paste the channel link instead.")
    info = ytdlp_info(target)
    uploader_url = info.get("uploader_url") or ""
    found_handle = handle
    m = re.search(r"/@([^/?#]+)", uploader_url)
    if m:
        found_handle = m.group(1).lower()
    p = Profile(
        platform="youtube", handle=found_handle, name=info.get("channel") or info.get("uploader"),
        followers=info.get("channel_follower_count"), channel_id=info.get("channel_id") or channel_id,
        channel_url=info.get("channel_url") or uploader_url or target, description=info.get("description") or "",
    )
    p.notes.append("YouTube often hides the business email behind “View email address” on the channel's About "
                   "page (it needs a CAPTCHA). If none is found here, open it and paste the address in.")
    return p


def _twitch(settings: Settings, web: httpx.Client, handle: str | None, url: str) -> Profile:
    if not handle:
        info = ytdlp_info(url)
        handle = (info.get("uploader_id") or info.get("channel_id") or "").lower() or None
        if not handle:
            raise LookupError_("Could not tell which channel this video belongs to. Paste the channel link instead.")
    p = Profile(platform="twitch", handle=handle, name=handle, channel_url=f"https://www.twitch.tv/{handle}")
    cid, secret = _env(settings, "TWITCH_CLIENT_ID"), _env(settings, "TWITCH_CLIENT_SECRET")
    if not (cid and secret):
        p.notes.append("Twitch profile text needs TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET in .env (a free app at "
                       "dev.twitch.tv). Without them only the name is known; add their YouTube link too, or the email.")
        return p
    tok = web.post("https://id.twitch.tv/oauth2/token",
                   data={"client_id": cid, "client_secret": secret, "grant_type": "client_credentials"})
    if tok.status_code != 200:
        p.notes.append("Twitch did not accept the client ID and secret in .env.")
        return p
    headers = {"Client-Id": cid, "Authorization": f"Bearer {tok.json()['access_token']}"}
    r = web.get("https://api.twitch.tv/helix/users", params={"login": handle}, headers=headers)
    users = (r.json().get("data") or []) if r.status_code == 200 else []
    if not users:
        raise LookupError_(f"Twitch has no channel called “{handle}”.")
    u = users[0]
    p.name, p.channel_id, p.description = u.get("display_name") or handle, u.get("id"), u.get("description") or ""
    f = web.get("https://api.twitch.tv/helix/channels/followers", params={"broadcaster_id": u["id"]}, headers=headers)
    if f.status_code == 200:
        p.followers = f.json().get("total")
    p.notes.append("Twitch panels under the stream are not available to apps; check them for a business email.")
    return p


def _kick(web: httpx.Client, handle: str | None) -> Profile:
    if not handle:
        raise LookupError_("Paste the Kick channel link (kick.com/name) rather than a video link.")
    p = Profile(platform="kick", handle=handle, name=handle, channel_url=f"https://kick.com/{handle}")
    try:
        r = web.get(f"https://kick.com/api/v2/channels/{handle}", headers={"Accept": "application/json"})
    except httpx.HTTPError:
        r = None
    if r is None or r.status_code != 200:
        p.notes.append("Kick did not let ClipForge read this channel's page; add the email or their YouTube link.")
        return p
    data = r.json()
    user = data.get("user") or {}
    p.name = user.get("username") or handle
    p.channel_id = str(data.get("id") or "") or None
    p.followers = data.get("followers_count") or data.get("followersCount")
    p.description = user.get("bio") or ""
    socials = {"instagram": "https://instagram.com/{}", "twitter": "https://x.com/{}",
               "youtube": "https://www.youtube.com/{}", "discord": "https://discord.gg/{}",
               "tiktok": "https://www.tiktok.com/@{}"}
    for key, pattern in socials.items():
        v = (user.get(key) or "").strip()
        if v:
            p.links.append({"url": v if v.startswith("http") else pattern.format(v.lstrip("@")), "found": "Kick profile"})
    return p


def lookup(settings: Settings, url: str) -> Profile:
    """Read a streamer's public profile and gather the contact addresses it lists."""
    platform, handle, channel_id = parse_link(url)
    web = make_web()
    try:
        if platform == "youtube":
            p = _youtube(normalise(url), handle, channel_id)
        elif platform == "twitch":
            p = _twitch(settings, web, handle, normalise(url))
        else:
            p = _kick(web, handle)

        source = {"youtube": "YouTube channel description", "twitch": "Twitch bio", "kick": "Kick bio"}[platform]
        for e in find_emails(p.description):
            p.emails.append({"email": e, "found": source})
        for u in find_links(p.description):
            if all(link["url"] != u for link in p.links):
                p.links.append({"url": u, "found": source})

        # One step further: a YouTube channel listed on a Twitch/Kick profile, and link-in-bio pages.
        if platform != "youtube":
            yt_link = next((link["url"] for link in p.links if "youtube.com/" in link["url"]), None)
            if not yt_link and p.handle:
                same_name_youtube(web, p)
            if yt_link:
                try:
                    other = _youtube(yt_link, *parse_link(yt_link)[1:])
                    for e in find_emails(other.description):
                        if all(x["email"] != e for x in p.emails):
                            p.emails.append({"email": e, "found": "their YouTube channel description"})
                    p.links += [{"url": u, "found": "their YouTube channel"} for u in find_links(other.description)
                                if all(link["url"] != u for link in p.links)]
                except Exception as exc:  # noqa: BLE001 - a second source is a bonus, never a failure
                    log.info("YouTube lookup for %s failed: %s", yt_link, exc)
        hubs = [link["url"] for link in p.links
                if any((urllib.parse.urlsplit(link["url"]).hostname or "").removeprefix("www.").endswith(h)
                       for h in LINK_HUBS)][:3]
        for hub in hubs:
            try:
                r = web.get(hub)
            except httpx.HTTPError:
                continue
            if r.status_code != 200:
                continue
            text = urllib.parse.unquote(r.text)
            for e in find_emails(text):
                if all(x["email"] != e for x in p.emails):
                    p.emails.append({"email": e, "found": f"link page {urllib.parse.urlsplit(hub).hostname}"})
    finally:
        web.close()
    return p


def _norm(name: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def same_name_youtube(web: httpx.Client, p: Profile) -> None:
    """Look for the streamer's email on a YouTube channel with the same name.

    Most streamers use one name everywhere, but a same-name channel can belong to
    someone else. So an address from it is used only when the channel links back
    to this Twitch/Kick channel; otherwise it is kept as a suggestion the owner
    must confirm, and nothing is sent to it.
    """
    url = f"https://www.youtube.com/@{p.handle}"
    try:
        info = ytdlp_info(url)
    except Exception as exc:  # noqa: BLE001 - no such channel, or YouTube refused: just no extra source
        log.info("No same-name YouTube channel for %s: %s", p.handle, str(exc)[:120])
        return
    title = info.get("channel") or info.get("uploader") or ""
    if _norm(title) not in {_norm(p.handle), _norm(p.name)} or not _norm(title):
        p.notes.append(f"The YouTube channel @{p.handle} is called “{title}”, which does not match; it was not used.")
        return
    description = info.get("description") or ""
    page = ""
    try:
        r = web.get(url + "/about")
        if r.status_code == 200:
            page = urllib.parse.unquote(r.text)
    except httpx.HTTPError:
        pass
    home = {"twitch": "twitch.tv", "kick": "kick.com"}.get(p.platform, "")
    links_back = bool(home) and re.search(rf"{re.escape(home)}/{re.escape(p.handle)}(?![A-Za-z0-9_])",
                                          description + "\n" + page, re.I) is not None
    found = find_emails(description) + [e for e in find_emails(page)
                                        if not re.search(r"(youtube|google|gstatic|ggpht|ytimg)\.", e)]
    channel_link = info.get("channel_url") or url
    if all(link["url"] != channel_link for link in p.links):
        p.links.append({"url": channel_link, "found": "same-name YouTube channel"})
    for e in dict.fromkeys(found):
        if any(x["email"] == e for x in p.emails):
            continue
        if links_back:
            p.emails.append({"email": e, "found": f"YouTube @{p.handle} (links back to their {PLATFORM_NAME[p.platform]})"})
        else:
            p.emails.append({"email": e, "found": f"YouTube @{p.handle}: same name, but it does not link back. "
                                                  "Check it is them before using it", "unconfirmed": True})


PLATFORM_NAME = {"youtube": "YouTube", "twitch": "Twitch", "kick": "Kick"}


def best_email(emails: list[dict[str, str]]) -> str | None:
    """A business-looking address first, then the first one found. Unconfirmed suggestions are never chosen."""
    emails = [e for e in emails if not e.get("unconfirmed")]
    if not emails:
        return None
    for e in emails:
        local = e["email"].split("@", 1)[0]
        if any(w in local for w in BUSINESS_WORDS):
            return e["email"]
    return emails[0]["email"]


# ---- the work, one step at a time ------------------------------------------------------------------


def streamer_to_dict(st: Streamer) -> dict[str, Any]:
    return {
        "id": st.id, "url": st.url, "platform": st.platform, "handle": st.handle, "name": st.name,
        "followers": st.followers, "channel_id": st.channel_id, "channel_url": st.channel_url,
        "links": st.links or [], "emails": st.emails or [], "contact_email": st.contact_email,
        "status": st.status, "status_detail": st.status_detail, "subject": st.subject, "body": st.body,
        "thread_id": st.thread_id, "sent_at": iso(st.sent_at), "followup_at": iso(st.followup_at),
        "checked_at": iso(st.checked_at), "reply": st.reply, "evidence": st.evidence,
        "conditions": st.conditions, "decided_at": iso(st.decided_at),
        "created_at": iso(st.created_at), "updated_at": iso(st.updated_at),
    }


def _blocked_address(s, email: str, own_id: int) -> bool:
    """Someone who said no, or whose address bounced, is never written to again."""
    return bool(s.scalar(select(Streamer.id).where(Streamer.id != own_id, Streamer.contact_email == email,
                                                   Streamer.status.in_(CLOSED_NO))))


def apply_profile(s, st: Streamer, p: Profile, cfg: dict[str, Any]) -> None:
    st.platform = p.platform
    st.handle, st.name, st.followers = p.handle or st.handle, p.name or st.name, p.followers
    st.channel_id, st.channel_url, st.description = p.channel_id, p.channel_url, p.description
    st.links, st.emails = p.links, p.emails
    dup = s.scalar(select(Streamer).where(
        Streamer.id != st.id, Streamer.platform == st.platform,
        or_(*[c for c in ((Streamer.handle == st.handle) if st.handle else None,
                          (Streamer.channel_id == st.channel_id) if st.channel_id else None) if c is not None]
            or [Streamer.id == -1])))
    if dup is not None:
        st.status, st.status_detail = "failed", f"Already on the list as “{dup.name or dup.handle}”."
        return
    if not st.contact_email:
        st.contact_email = best_email(p.emails)
    notes = " ".join(p.notes)
    if not st.contact_email:
        st.status = "no_contact"
        maybe = [e["email"] for e in p.emails if e.get("unconfirmed")]
        if maybe:
            st.status_detail = (f"Possible address from a same-name YouTube channel: {', '.join(maybe)}. It does not "
                                "link back to them, so check it is really them, then press Use this email.")
        else:
            st.status_detail = ("No public contact address was found. " + notes).strip()
        return
    queue(s, st, cfg)


def queue(s, st: Streamer, cfg: dict[str, Any]) -> None:
    if _blocked_address(s, st.contact_email or "", st.id):
        st.status, st.status_detail = "paused", "This address said no or bounced before; ClipForge will not write to it."
        return
    if not st.subject or not st.body:
        st.subject, st.body = draft(st, cfg)
    st.status = "queued"
    st.status_detail = None if cfg["auto_send"] else "Automatic sending is off: press Send now, or turn it on."


def sends_in_last_day(s, now: datetime) -> list[datetime]:
    since = now - timedelta(days=1)
    times = [t for (t,) in s.execute(select(Streamer.sent_at).where(Streamer.sent_at >= since)).all()]
    times += [t for (t,) in s.execute(select(Streamer.followup_at).where(Streamer.followup_at >= since)).all()]
    return sorted(t for t in times if t)


def send_allowed(s, cfg: dict[str, Any], now: datetime, *, manual: bool = False) -> str | None:
    """Why nothing may be sent right now, or None."""
    recent = sends_in_last_day(s, now)
    if len(recent) >= cfg["daily_limit"]:
        nxt = recent[0] + timedelta(days=1)
        return f"Today's limit of {cfg['daily_limit']} emails is reached; the next can go after {iso(nxt)}."
    if not manual and recent and now - recent[-1] < timedelta(minutes=cfg["spacing_minutes"]):
        return "Waiting a little between emails so they don't look like bulk mail."
    return None


def _send_first(settings: Settings, http: httpx.Client, s, st: Streamer, cfg: dict[str, Any], now: datetime) -> None:
    result = gmail.send(settings, http, to=st.contact_email, subject=st.subject, body=st.body,
                        sender_name=cfg["sender_name"])
    st.thread_id, st.first_message_id = result["thread_id"], result["header_id"]
    st.sent_at, st.checked_at = now, now
    st.status, st.status_detail = "sent", f"Sent to {st.contact_email}."
    s.commit()
    log.info("Permission request sent to %s (%s)", st.name or st.handle, st.contact_email)


def _send_followup(settings: Settings, http: httpx.Client, s, st: Streamer, cfg: dict[str, Any], now: datetime) -> None:
    body = fill(cfg["followup_body"], template_values(st, cfg)).strip() + "\n"
    subject = st.subject if (st.subject or "").lower().startswith("re:") else f"Re: {st.subject}"
    gmail.send(settings, http, to=st.contact_email, subject=subject, body=body, sender_name=cfg["sender_name"],
               thread_id=st.thread_id, in_reply_to=st.first_message_id)
    st.followup_at = now
    st.status, st.status_detail = "followed_up", "No reply after the first email; one follow-up was sent."
    s.commit()


def send_now(settings: Settings, s, st: Streamer, now: datetime | None = None) -> None:
    """The owner pressed Send now: the daily limit still applies, the spacing does not."""
    now = now or utcnow()
    cfg = effective(settings, load_settings(s))
    why = missing_sender_details(cfg) or send_allowed(s, cfg, now, manual=True)
    if why:
        raise gmail.Refused(why)
    if st.status not in ("queued", "paused", "failed") or not st.contact_email:
        raise gmail.Refused("Only a queued request with a contact address can be sent.")
    if _blocked_address(s, st.contact_email, st.id):
        raise gmail.Refused("This address said no or bounced before; ClipForge will not write to it.")
    if not st.subject or not st.body:
        st.subject, st.body = draft(st, cfg)
    http = gmail.make_http()
    try:
        _send_first(settings, http, s, st, cfg, now)
    finally:
        http.close()


def _is_bounce(msg: dict[str, Any]) -> bool:
    addr = msg["address"]
    return addr.startswith(("mailer-daemon@", "postmaster@")) or "delivery status notification" in msg["subject"].lower() \
        or "undeliverable" in msg["subject"].lower()


def check_reply(settings: Settings, http: httpx.Client, s, st: Streamer, own: str, now: datetime) -> None:
    messages = gmail.read_thread(settings, http, st.thread_id)
    st.checked_at = now
    theirs = [m for m in messages if m["address"] and m["address"] != own.lower()]
    if not theirs:
        s.commit()
        return
    last = theirs[-1]
    if _is_bounce(last):
        st.status = "bounced"
        st.status_detail = f"The email to {st.contact_email} could not be delivered. Add another address to try again."
    else:
        text = last["text"] or last["snippet"]
        # keep only their words, not the quoted request underneath
        text = re.split(r"\n\s*On .{5,200}wrote:\s*\n|\n-{2,}\s*Original Message", text, maxsplit=1)[0].strip()
        st.reply = {"from": last["from"], "date": last["date"], "snippet": last["snippet"], "text": text[:5000],
                    "message_id": last["id"], "seen_at": iso(now)}
        st.status = "replied"
        st.status_detail = "They replied. Read it, then record Approved (with any conditions) or Declined."
        save_evidence(settings, st)
    s.commit()


def save_evidence(settings: Settings, st: Streamer) -> Path:
    """A copy of the request and the reply, kept with ClipForge's data as the permission record."""
    folder = settings.data_dir / "permissions"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"streamer_{st.id}.json"
    path.write_text(json.dumps({
        "streamer": st.name, "handle": st.handle, "platform": st.platform, "channel_url": st.channel_url,
        "asked": {"to": st.contact_email, "subject": st.subject, "body": st.body, "sent_at": iso(st.sent_at),
                  "followup_at": iso(st.followup_at), "gmail_thread_id": st.thread_id},
        "reply": st.reply, "decision": st.status if st.status in ("approved", "declined") else None,
        "evidence": st.evidence, "conditions": st.conditions, "decided_at": iso(st.decided_at),
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def tick(settings: Settings, session_factory, now: datetime | None = None, bus=None) -> dict[str, Any]:
    """One round of the background work. Returns what happened, for logs and tests."""
    now = now or utcnow()
    done: dict[str, Any] = {"looked_up": 0, "sent": 0, "followups": 0, "replies": 0, "closed": 0, "error": None}
    changed = False

    with session_factory() as s:
        cfg = effective(settings, load_settings(s))
        waiting = select(Streamer).where(Streamer.status == "looking_up").order_by(Streamer.id).limit(LOOKUPS_PER_ROUND)
        for st in s.scalars(waiting).all():
            try:
                apply_profile(s, st, lookup(settings, st.url), cfg)
            except LookupError_ as exc:
                st.status, st.status_detail = "failed", str(exc)
            except Exception as exc:  # noqa: BLE001 - a bad page must not stop the rest
                log.warning("Looking up %s failed: %s", st.url, exc)
                st.status, st.status_detail = "failed", f"Could not read this channel: {str(exc)[:200]}"
            s.commit()
            done["looked_up"] += 1
            changed = True

        token = gmail.load_token(settings) or {}
        if not token.get("refresh_token"):
            if changed and bus:
                bus.publish({"type": "outreach"})
            return done
        own = token.get("address") or ""
        http = gmail.make_http()
        try:
            check_before = now - timedelta(minutes=CHECK_MINUTES)
            for st in s.scalars(select(Streamer).where(Streamer.status.in_(("sent", "followed_up")),
                                                       Streamer.thread_id.is_not(None))).all():
                if st.checked_at and st.checked_at > check_before:
                    continue
                before = st.status
                check_reply(settings, http, s, st, own, now)
                if st.status != before:
                    done["replies"] += 1
                    changed = True

            for st in s.scalars(select(Streamer).where(Streamer.status == "followed_up")).all():
                if st.followup_at and now - st.followup_at > timedelta(days=cfg["close_days"]):
                    st.status = "no_reply"
                    st.status_detail = "No reply to the request or the follow-up. Not permission: nothing is posted."
                    done["closed"] += 1
                    changed = True
            s.commit()

            if missing_sender_details(cfg):
                set_state(s, last_error=missing_sender_details(cfg), last_tick=iso(now))
                return done

            # follow-ups first: they finish what was started
            due = [st for st in s.scalars(select(Streamer).where(Streamer.status == "sent")).all()
                   if st.sent_at and now - st.sent_at > timedelta(days=cfg["followup_days"])]
            for st in due:
                if send_allowed(s, cfg, now):
                    break
                _send_followup(settings, http, s, st, cfg, now)
                done["followups"] += 1
                changed = True

            if cfg["auto_send"] and not send_allowed(s, cfg, now):
                st = s.scalars(select(Streamer).where(Streamer.status == "queued", Streamer.contact_email.is_not(None))
                               .order_by(Streamer.created_at, Streamer.id)).first()
                if st is not None:
                    if _blocked_address(s, st.contact_email, st.id):
                        st.status, st.status_detail = "paused", "This address said no or bounced before."
                        s.commit()
                    else:
                        try:
                            _send_first(settings, http, s, st, cfg, now)
                            done["sent"] += 1
                        except gmail.NotConnected:
                            raise
                        except gmail.Refused as exc:
                            st.status, st.status_detail = "failed", str(exc)
                            s.commit()
                    changed = True
            set_state(s, last_error=None, last_tick=iso(now))
        except gmail.GmailError as exc:
            done["error"] = str(exc)
            log.warning("Outreach: %s", exc)
            set_state(s, last_error=str(exc), last_tick=iso(now))
            changed = True
        finally:
            http.close()
    if changed and bus:
        bus.publish({"type": "outreach"})
    return done


class OutreachWorker:
    """Runs `tick` in the background every so often while the app is open."""

    def __init__(self, settings: Settings, session_factory, bus, interval: float, job_worker=None) -> None:
        self.settings, self.session_factory, self.bus, self.interval = settings, session_factory, bus, interval
        self.job_worker = job_worker  # for the posting schedule's automatic uploads
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self.interval <= 0 or self._thread:
            return
        self._thread = threading.Thread(target=self._run, name="outreach", daemon=True)
        self._thread.start()

    def wake(self) -> None:
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=10)

    def _run(self) -> None:
        time.sleep(min(5.0, self.interval))
        while not self._stop.is_set():
            try:
                tick(self.settings, self.session_factory, bus=self.bus)
            except Exception:  # noqa: BLE001 - keep the loop alive; the error is logged
                log.exception("Outreach round failed")
            if self.job_worker is not None:
                try:
                    from . import schedule

                    if schedule.tick(self.settings, self.session_factory, self.job_worker):
                        self.bus.publish({"type": "schedule"})
                except Exception:  # noqa: BLE001
                    log.exception("Schedule round failed")
            self._wake.wait(self.interval)
            self._wake.clear()


# ---- permission for a project --------------------------------------------------------------------


def approved_for_link(s, url: str) -> Streamer | None:
    """The approved streamer a video link plainly belongs to (by the handle or channel id in the link)."""
    try:
        platform, handle, channel_id = parse_link(url)
    except LookupError_:
        return None
    if not handle and not channel_id:
        return None
    for st in s.scalars(select(Streamer).where(Streamer.status == "approved", Streamer.platform == platform)).all():
        if handle and st.handle and st.handle.lower() == handle.lower():
            return st
        if channel_id and st.channel_id == channel_id:
            return st
    return None


def evidence_note(st: Streamer) -> str:
    parts = [f"Permission from {st.name or st.handle} ({st.channel_url or st.url})"]
    if st.reply:
        parts.append(f"reply from {st.reply.get('from')} on {st.reply.get('date')}")
    if st.evidence:
        parts.append(st.evidence)
    if st.conditions:
        parts.append(f"Conditions: {st.conditions}")
    parts.append(f"Recorded in ClipForge, Permissions #{st.id}")
    return "; ".join(parts)[:2000]


def belongs_to(st: Streamer, creator: str | None, creator_url: str | None) -> bool | None:
    """Does a downloaded video's channel match the streamer? None when the platform did not say."""
    if not creator and not creator_url:
        return None
    url = (creator_url or "").lower()
    names = {x.lower() for x in (st.name, st.handle) if x}
    if st.channel_id and st.channel_id.lower() in url:
        return True
    if st.handle and (f"/@{st.handle.lower()}" in url or url.rstrip("/").endswith("/" + st.handle.lower())):
        return True
    if st.channel_url and url and st.channel_url.lower().rstrip("/") == url.rstrip("/"):
        return True
    return bool(creator and creator.lower() in names)
