"""Permission requests to streamers: finding a contact, sending from the owner's Gmail, replies, and what counts as permission."""

from __future__ import annotations

import base64
import email
import json
import urllib.parse
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from clipforge import gmail, outreach
from clipforge import youtube as yt
from clipforge.app import create_app
from clipforge.ingest import SourceMeta
from clipforge.models import Project, Streamer, utcnow
from clipforge.pipelines import _permission_mismatch

REAL_LOOKUP = outreach.lookup  # kept before any test replaces it

from .conftest import wait_for

CLIENT = {"installed": {"client_id": "abc.apps.googleusercontent.com", "client_secret": "s3cret",
                        "project_id": "my-clips", "redirect_uris": ["http://localhost"]}}
OWNER = "owner@gmail.test"


class FakeGmail:
    """Stands in for Google's sign-in and the Gmail API. Nothing leaves this computer."""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.threads: dict[str, list[dict]] = {}
        self.scope = " ".join(gmail.SCOPES)
        self.fail_send = False

    def reply(self, thread_id: str, sender: str, text: str, subject: str = "Re: request") -> None:
        body = base64.urlsafe_b64encode(text.encode()).decode()
        self.threads[thread_id].append({
            "id": f"r{len(self.threads[thread_id])}", "threadId": thread_id, "snippet": text[:80],
            "internalDate": "1", "labelIds": ["INBOX"],
            "payload": {"mimeType": "text/plain", "headers": [{"name": "From", "value": sender},
                                                               {"name": "Subject", "value": subject},
                                                               {"name": "Date", "value": "Wed, 18 Nov 2026 10:00:00 +0000"}],
                        "body": {"data": body}}})

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.startswith("https://oauth2.googleapis.com/token"):
            form = dict(urllib.parse.parse_qsl(request.content.decode()))
            body = {"access_token": "access-g", "expires_in": 3600, "scope": self.scope}
            if form.get("grant_type") == "authorization_code":
                body["refresh_token"] = "refresh-g"
            return httpx.Response(200, json=body)
        if url.startswith("https://oauth2.googleapis.com/revoke"):
            return httpx.Response(200)
        if url.endswith("/users/me/profile"):
            return httpx.Response(200, json={"emailAddress": OWNER})
        if url.endswith("/users/me/messages/send"):
            if self.fail_send:
                return httpx.Response(500, json={"error": {"message": "backend error"}})
            payload = json.loads(request.content)
            msg = email.message_from_bytes(base64.urlsafe_b64decode(payload["raw"]))
            thread = payload.get("threadId") or f"t{len(self.threads) + 1}"
            mid = f"m{len(self.sent) + 1}"
            self.sent.append({"to": msg["To"], "from": msg["From"], "subject": msg["Subject"],
                              "body": msg.get_payload(decode=True).decode(), "thread": thread,
                              "in_reply_to": msg["In-Reply-To"], "message_id": msg["Message-ID"]})
            self.threads.setdefault(thread, []).append({
                "id": mid, "threadId": thread, "snippet": "", "internalDate": "1", "labelIds": ["SENT"],
                "payload": {"mimeType": "text/plain", "headers": [{"name": "From", "value": f"Me <{OWNER}>"},
                                                                   {"name": "Subject", "value": msg["Subject"]}],
                            "body": {"data": ""}}})
            return httpx.Response(200, json={"id": mid, "threadId": thread})
        if "/users/me/threads/" in url:
            tid = url.split("/threads/")[1].split("?")[0]
            return httpx.Response(200, json={"id": tid, "messages": self.threads.get(tid, [])})
        raise AssertionError(f"unexpected request to {url}")


@pytest.fixture
def fake_gmail(monkeypatch) -> FakeGmail:
    fake = FakeGmail()
    monkeypatch.setattr(gmail, "make_http", lambda: httpx.Client(transport=httpx.MockTransport(fake.handler)))
    return fake


@pytest.fixture
def profiles(monkeypatch) -> dict[str, outreach.Profile]:
    """What each streamer's public profile says, by handle."""
    found: dict[str, outreach.Profile] = {}

    def fake_lookup(_settings, url):
        _platform, handle, _cid = outreach.parse_link(url)
        if handle not in found:
            raise outreach.LookupError_(f"No channel called {handle}")
        return found[handle]

    monkeypatch.setattr(outreach, "lookup", fake_lookup)
    return found


def profile(handle: str, *emails: str, name: str | None = None) -> outreach.Profile:
    return outreach.Profile(platform="twitch", handle=handle, name=name or handle.title(), followers=12000,
                            channel_url=f"https://www.twitch.tv/{handle}",
                            emails=[{"email": e, "found": "Twitch bio"} for e in emails])


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings, configure_logging=False)) as c:
        yield c


def connect_gmail(client, settings, tmp_path) -> None:
    path = tmp_path / "client_secret.json"
    path.write_text(json.dumps(CLIENT), encoding="utf-8")
    yt.save_client(settings, path)
    url = client.post("/api/gmail/connect").json()["url"]
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
    page = client.get(f"/api/gmail/callback?state={q['state']}&code=one-time")
    assert page.status_code == 200, page.text


def ready(client, **extra) -> None:
    body = {"sender_name": "Sam", "channel_name": "Peak Stream Moments",
            "channel_url": "https://www.youtube.com/@peakstreammoments", **extra}
    assert client.put("/api/outreach/settings", json=body).status_code == 200


def tick(client, settings, now=None) -> dict:
    return outreach.tick(settings, client.app.state.ctx.session, now=now)


def streamer(client, sid: int) -> dict:
    return next(s for s in client.get("/api/streamers").json() if s["id"] == sid)


# ---- reading a link and finding a contact -------------------------------------------------------------


def test_channel_and_video_links_are_understood():
    assert outreach.parse_link("https://www.youtube.com/@SomeOne/videos") == ("youtube", "someone", None)
    assert outreach.parse_link("youtube.com/channel/UC12345") == ("youtube", None, "UC12345")
    assert outreach.parse_link("https://www.twitch.tv/KaiCenat") == ("twitch", "kaicenat", None)
    assert outreach.parse_link("https://www.twitch.tv/videos/123") == ("twitch", None, None)
    assert outreach.parse_link("https://kick.com/adinross") == ("kick", "adinross", None)
    with pytest.raises(outreach.LookupError_):
        outreach.parse_link("https://example.com/someone")


def test_contacts_come_from_the_description_and_a_link_in_bio_page(monkeypatch, settings):
    monkeypatch.setattr(outreach, "ytdlp_info", lambda url: {
        "channel": "Big Streamer", "channel_id": "UCbig", "channel_url": "https://www.youtube.com/channel/UCbig",
        "uploader_url": "https://www.youtube.com/@bigstreamer", "channel_follower_count": 2_000_000,
        "description": "Live daily! Fan mail: fans@bigstreamer.test\nAll my links: https://linktr.ee/bigstreamer\n"
                       "logo.png@2x.png"})

    def web(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "linktr.ee"
        return httpx.Response(200, text='<a href="mailto:business@talentco.test">Business</a>')

    monkeypatch.setattr(outreach, "make_web", lambda: httpx.Client(transport=httpx.MockTransport(web)))
    p = outreach.lookup(settings, "https://www.youtube.com/@BigStreamer")
    assert p.name == "Big Streamer" and p.followers == 2_000_000 and p.handle == "bigstreamer"
    assert [e["email"] for e in p.emails] == ["fans@bigstreamer.test", "business@talentco.test"]
    assert p.emails[1]["found"] == "link page linktr.ee"
    assert outreach.best_email(p.emails) == "business@talentco.test", "a business address is preferred"
    assert any("View email address" in n for n in p.notes), "the hidden YouTube address is explained, not bypassed"


def test_a_streamer_is_looked_up_and_a_personal_request_is_queued(client, settings, profiles):
    profiles["goodstreamer"] = profile("goodstreamer", "biz@good.test", name="GoodStreamer")
    ready(client)
    sid = client.post("/api/streamers", json={"url": "https://www.twitch.tv/GoodStreamer"}).json()["id"]
    assert client.post("/api/streamers", json={"url": "twitch.tv/goodstreamer"}).status_code == 409, "no duplicates"
    tick(client, settings)
    st = streamer(client, sid)
    assert st["status"] == "queued" and st["contact_email"] == "biz@good.test"
    assert "GoodStreamer" in st["subject"] and "Peak Stream Moments" in st["body"]
    assert "https://www.youtube.com/@peakstreammoments" in st["body"] and st["body"].rstrip().endswith("won't write again.)")


def test_no_public_contact_waits_for_an_address_from_the_owner(client, settings, profiles, fake_gmail, tmp_path):
    profiles["quiet"] = profile("quiet")
    ready(client)
    sid = client.post("/api/streamers", json={"url": "https://www.twitch.tv/quiet"}).json()["id"]
    tick(client, settings)
    assert streamer(client, sid)["status"] == "no_contact"
    assert client.patch(f"/api/streamers/{sid}", json={"contact_email": "not an email"}).status_code == 422
    st = client.patch(f"/api/streamers/{sid}", json={"contact_email": "Mgmt@Quiet.test"}).json()
    assert st["status"] == "queued" and st["contact_email"] == "mgmt@quiet.test"


# ---- Gmail and sending ---------------------------------------------------------------------------------


def test_gmail_sign_in_needs_both_send_and_read_access(client, settings, tmp_path, fake_gmail):
    fake_gmail.scope = gmail.SCOPES[0]
    path = tmp_path / "client_secret.json"
    path.write_text(json.dumps(CLIENT), encoding="utf-8")
    yt.save_client(settings, path)
    url = client.post("/api/gmail/connect").json()["url"]
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
    assert q["scope"] == " ".join(gmail.SCOPES) and q["code_challenge_method"] == "S256"
    assert q["redirect_uri"].endswith("/api/gmail/callback")
    page = client.get(f"/api/gmail/callback?state={q['state']}&code=x")
    assert page.status_code == 400 and "gmail.readonly" in page.text
    fake_gmail.scope = " ".join(gmail.SCOPES)
    connect_gmail(client, settings, tmp_path)
    status = client.get("/api/outreach").json()
    assert status["connected"] and status["address"] == OWNER


def test_requests_go_out_a_few_at_a_time_and_spaced_apart(client, settings, profiles, fake_gmail, tmp_path):
    for i in range(4):
        profiles[f"s{i}"] = profile(f"s{i}", f"biz@s{i}.test")
        client.post("/api/streamers", json={"url": f"https://www.twitch.tv/s{i}"})
    connect_gmail(client, settings, tmp_path)
    tick(client, settings)
    assert fake_gmail.sent == [], "nothing goes out before the sender's name is set"
    assert "your name" in client.get("/api/outreach").json()["last_error"]

    ready(client, daily_limit=2, spacing_minutes=20)
    now = utcnow()
    assert tick(client, settings, now)["sent"] == 1
    assert tick(client, settings, now + timedelta(minutes=5))["sent"] == 0, "spaced out"
    assert tick(client, settings, now + timedelta(minutes=25))["sent"] == 1
    assert tick(client, settings, now + timedelta(hours=3))["sent"] == 0, "the daily limit holds"
    assert tick(client, settings, now + timedelta(hours=25))["sent"] == 1
    first = fake_gmail.sent[0]
    assert first["to"] == "biz@s0.test" and first["from"] == f"Sam <{OWNER}>"
    assert "S0" in first["subject"]

    sid = next(s["id"] for s in client.get("/api/streamers").json() if s["handle"] == "s3")
    r = client.post(f"/api/streamers/{sid}/send")
    assert r.status_code == 409 and "limit" in r.json()["error"], "Send now still respects the daily limit"


def test_turning_automatic_sending_off_keeps_requests_waiting(client, settings, profiles, fake_gmail, tmp_path):
    profiles["a"] = profile("a", "biz@a.test")
    connect_gmail(client, settings, tmp_path)
    ready(client, auto_send=False)
    sid = client.post("/api/streamers", json={"url": "https://www.twitch.tv/a"}).json()["id"]
    tick(client, settings)
    tick(client, settings)
    assert fake_gmail.sent == [] and "off" in streamer(client, sid)["status_detail"]
    assert client.post(f"/api/streamers/{sid}/send").json()["status"] == "sent"
    assert len(fake_gmail.sent) == 1


def test_one_follow_up_then_closed_and_silence_is_never_permission(client, settings, profiles, fake_gmail, tmp_path):
    profiles["slow"] = profile("slow", "biz@slow.test")
    connect_gmail(client, settings, tmp_path)
    ready(client)
    sid = client.post("/api/streamers", json={"url": "https://www.twitch.tv/slow"}).json()["id"]
    t0 = utcnow()
    tick(client, settings, t0)
    assert tick(client, settings, t0 + timedelta(days=3))["followups"] == 0
    assert tick(client, settings, t0 + timedelta(days=8))["followups"] == 1
    follow = fake_gmail.sent[1]
    assert follow["thread"] == fake_gmail.sent[0]["thread"] and follow["in_reply_to"] == fake_gmail.sent[0]["message_id"]
    assert follow["subject"].startswith("Re: ")
    tick(client, settings, t0 + timedelta(days=30))
    st = streamer(client, sid)
    assert st["status"] == "no_reply" and len(fake_gmail.sent) == 2
    r = client.post("/api/projects", json={"source": "url", "url": "https://www.twitch.tv/slow"})
    assert r.json()["rights_status"] == "unverified", "no reply gives no permission"


def test_a_reply_is_noticed_and_kept_as_evidence(client, settings, profiles, fake_gmail, tmp_path):
    profiles["goodstreamer"] = profile("goodstreamer", "biz@good.test", name="GoodStreamer")
    connect_gmail(client, settings, tmp_path)
    ready(client)
    sid = client.post("/api/streamers", json={"url": "https://www.twitch.tv/goodstreamer"}).json()["id"]
    t0 = utcnow()
    tick(client, settings, t0)
    thread = fake_gmail.sent[0]["thread"]
    fake_gmail.reply(thread, "Manager <biz@good.test>",
                     "Sure, go ahead! Credit us and no monetising please.\n\nOn Tue, Sam wrote:\n> Hi GoodStreamer team")
    tick(client, settings, t0 + timedelta(minutes=30))
    st = streamer(client, sid)
    assert st["status"] == "replied" and st["reply"]["text"] == "Sure, go ahead! Credit us and no monetising please."
    record = json.loads((settings.data_dir / "permissions" / f"streamer_{sid}.json").read_text(encoding="utf-8"))
    assert record["reply"]["from"] == "Manager <biz@good.test>" and record["asked"]["to"] == "biz@good.test"
    assert record["decision"] is None, "a reply is not a decision: the owner reads it and decides"
    assert tick(client, settings, t0 + timedelta(days=9))["followups"] == 0, "no follow-up after a reply"


def test_approval_fills_in_permission_for_that_streamers_videos_only(client, settings, profiles, fake_gmail, tmp_path):
    profiles["goodstreamer"] = profile("goodstreamer", "biz@good.test", name="GoodStreamer")
    connect_gmail(client, settings, tmp_path)
    ready(client)
    sid = client.post("/api/streamers", json={"url": "https://www.twitch.tv/goodstreamer"}).json()["id"]
    t0 = utcnow()
    tick(client, settings, t0)
    fake_gmail.reply(fake_gmail.sent[0]["thread"], "biz@good.test", "Yes, credit us.")
    tick(client, settings, t0 + timedelta(minutes=30))
    st = client.post(f"/api/streamers/{sid}/approve", json={"conditions": "Credit on screen"}).json()
    assert st["status"] == "approved"

    p = client.post("/api/projects", json={"source": "url", "url": "https://www.twitch.tv/goodstreamer/clip/abc"}).json()
    assert p["rights_status"] == "creator_permission" and p["creator"] == "GoodStreamer"
    assert "Permissions #" in p["rights_note"] and "Credit on screen" in p["rights_note"] and p["permission_id"] == sid
    other = client.post("/api/projects", json={"source": "url", "url": "https://www.twitch.tv/someoneelse/clip/x"}).json()
    assert other["rights_status"] == "unverified", "someone else's video is not covered"
    assert client.get("/api/streamers/approved").json()[0]["name"] == "GoodStreamer"

    with client.app.state.ctx.session() as s:
        project = Project(title="t", source_kind="twitch", permission_id=sid)  # as created, before any download
        good = SourceMeta(platform="twitch", video_id="1", title="t", creator="GoodStreamer",
                          creator_url="https://www.twitch.tv/goodstreamer", duration=1.0, upload_date=None,
                          licence=None, webpage_url=None)
        assert _permission_mismatch(s, project, good) is None
        bad = SourceMeta(platform="twitch", video_id="1", title="t", creator="Someone Else",
                         creator_url="https://www.twitch.tv/someoneelse", duration=1.0, upload_date=None,
                         licence=None, webpage_url=None)
        assert "not from GoodStreamer" in _permission_mismatch(s, project, bad)

    client.post(f"/api/streamers/{sid}/decline", json={})
    assert client.get(f"/api/projects/{p['id']}").json()["rights_status"] == "unverified", "a later no withdraws it"


def test_a_video_from_someone_else_is_not_downloaded_under_a_streamers_permission(client, settings, fake_sources):
    with client.app.state.ctx.session() as s:
        s.add(Streamer(url="https://www.twitch.tv/goodstreamer", platform="twitch", handle="goodstreamer",
                       name="GoodStreamer", channel_url="https://www.twitch.tv/goodstreamer", status="approved",
                       evidence="Their Discord clipping rules, 18 Nov 2026"))
        s.commit()
    sid = client.get("/api/streamers").json()[0]["id"]
    p = client.post("/api/projects", json={"source": "url", "url": "https://www.twitch.tv/videos/999",
                                           "permission_id": sid}).json()
    assert p["rights_status"] == "creator_permission"
    # the fake platform says this video is from "Channel Name"
    after = wait_for(lambda: (lambda d: d if d["rights_status"] == "unverified" else None)(
        client.get(f"/api/projects/{p['id']}").json()))
    assert "not from GoodStreamer" in after["rights_note"]
    assert fake_sources["twitch"].download_calls == 0


def test_approving_without_a_reply_needs_written_evidence(client, settings, profiles):
    profiles["quiet"] = profile("quiet")
    sid = client.post("/api/streamers", json={"url": "https://www.twitch.tv/quiet"}).json()["id"]
    tick(client, settings)
    r = client.post(f"/api/streamers/{sid}/approve", json={})
    assert r.status_code == 422 and "Silence is never permission" in r.json()["error"]
    ok = client.post(f"/api/streamers/{sid}/approve", json={"evidence": "Clipping allowed in their Discord #rules"})
    assert ok.json()["status"] == "approved"
    assert client.delete(f"/api/streamers/{sid}").status_code == 409, "the record is kept"


def test_a_bounced_or_refusing_address_is_never_written_to_again(client, settings, profiles, fake_gmail, tmp_path):
    profiles["one"] = profile("one", "shared@agency.test")
    profiles["two"] = profile("two", "shared@agency.test")
    connect_gmail(client, settings, tmp_path)
    ready(client)
    a = client.post("/api/streamers", json={"url": "https://www.twitch.tv/one"}).json()["id"]
    t0 = utcnow()
    tick(client, settings, t0)
    fake_gmail.reply(fake_gmail.sent[0]["thread"], "Mail Delivery Subsystem <mailer-daemon@googlemail.com>",
                     "Address not found", subject="Delivery Status Notification (Failure)")
    tick(client, settings, t0 + timedelta(minutes=30))
    assert streamer(client, a)["status"] == "bounced"
    b = client.post("/api/streamers", json={"url": "https://www.twitch.tv/two"}).json()["id"]
    tick(client, settings, t0 + timedelta(hours=2))
    assert streamer(client, b)["status"] == "paused" and len(fake_gmail.sent) == 1


def test_a_gmail_outage_is_reported_and_the_request_waits(client, settings, profiles, fake_gmail, tmp_path):
    profiles["a"] = profile("a", "biz@a.test")
    connect_gmail(client, settings, tmp_path)
    ready(client)
    sid = client.post("/api/streamers", json={"url": "https://www.twitch.tv/a"}).json()["id"]
    fake_gmail.fail_send = True
    done = tick(client, settings)
    assert done["error"] and streamer(client, sid)["status"] == "queued"
    assert "backend error" in client.get("/api/outreach").json()["last_error"]
    fake_gmail.fail_send = False
    assert tick(client, settings, utcnow() + timedelta(minutes=1))["sent"] == 1


def test_the_template_preview_and_unknown_fields(client):
    r = client.post("/api/outreach/preview", json={"sender_name": "Sam", "channel_name": "Peak Stream Moments",
                                                  "subject": "Hi {streamer} from {channel_name} {oops}"}).json()
    assert r["subject"] == "Hi ExampleStreamer from Peak Stream Moments {oops}"
    assert "ExampleStreamer" in r["followup"]


def test_a_whole_list_is_added_at_once_and_looked_up_a_few_at_a_time(client, settings, profiles):
    for i in range(7):
        profiles[f"chat{i}"] = profile(f"chat{i}", f"biz@chat{i}.test")
    client.post("/api/streamers", json={"url": "https://www.twitch.tv/chat0"})
    text = "\n".join([
        "# my Just Chatting list",
        "https://www.twitch.tv/chat0",                    # already on the list
        *[f"twitch.tv/chat{i}" for i in range(1, 7)],
        "https://www.twitch.tv/chat1",                    # listed twice
        "https://www.youtube.com/@SomeOne, mgmt@someone.test",
        "not a link",
    ])
    r = client.post("/api/streamers/bulk", json={"text": text}).json()
    assert len(r["added"]) == 7 and len(r["skipped"]) == 3
    reasons = " ".join(x["reason"] for x in r["skipped"])
    assert "Already on the list" in reasons and "twice" in reasons and "Paste a YouTube" in reasons
    yt_row = next(a for a in r["added"] if a["platform"] == "youtube")
    assert yt_row["handle"] == "someone" and yt_row["contact_email"] == "mgmt@someone.test"
    assert tick(client, settings)["looked_up"] == outreach.LOOKUPS_PER_ROUND, "a long list does not hold up sending"
    tick(client, settings)
    statuses = [s["status"] for s in client.get("/api/streamers").json()]
    assert "looking_up" not in statuses


def _twitch_with_youtube(monkeypatch, channels: dict, pages: dict):
    """A Twitch profile with an empty bio, and whatever same-name YouTube channels exist."""
    monkeypatch.setattr(outreach, "_twitch", lambda settings, web, handle, url: outreach.Profile(
        platform="twitch", handle=handle, name=handle.title(), channel_url=f"https://www.twitch.tv/{handle}"))

    def info(url):
        handle = url.rstrip("/").rsplit("@", 1)[1]
        if handle not in channels:
            raise RuntimeError("HTTP Error 404")
        return channels[handle]

    monkeypatch.setattr(outreach, "ytdlp_info", info)

    def web(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=pages.get(str(request.url), ""))

    monkeypatch.setattr(outreach, "make_web", lambda: httpx.Client(transport=httpx.MockTransport(web)))


def test_a_same_name_youtube_channel_that_links_back_gives_the_email(monkeypatch, settings):
    _twitch_with_youtube(monkeypatch, {"robcdee": {"channel": "robcdee", "description": "biz: rob@biz.test",
                                                   "channel_url": "https://www.youtube.com/channel/UCrob"}},
                         {"https://www.youtube.com/@robcdee/about": '<a href="https://www.twitch.tv/robcdee">Twitch</a>'})
    p = outreach.lookup(settings, "https://www.twitch.tv/robcdee")
    assert outreach.best_email(p.emails) == "rob@biz.test"
    assert "links back" in p.emails[0]["found"]


def test_a_same_name_channel_that_does_not_link_back_is_only_a_suggestion(monkeypatch, settings, client, profiles):
    _twitch_with_youtube(monkeypatch, {"poet": {"channel": "Poet", "description": "contact poet@mail.test"}}, {})
    p = REAL_LOOKUP(settings, "https://www.twitch.tv/poet")
    assert p.emails[0]["unconfirmed"] and outreach.best_email(p.emails) is None, "never chosen on its own"
    profiles["poet"] = p
    sid = client.post("/api/streamers", json={"url": "https://www.twitch.tv/poet"}).json()["id"]
    tick(client, settings)
    st = streamer(client, sid)
    assert st["status"] == "no_contact" and "poet@mail.test" in st["status_detail"] and st["contact_email"] is None


def test_a_same_name_channel_with_another_name_or_none_is_ignored(monkeypatch, settings):
    _twitch_with_youtube(monkeypatch, {"sam": {"channel": "Samantha Cooks", "description": "sam@cook.test"}}, {})
    p = outreach.lookup(settings, "https://www.twitch.tv/sam")
    assert p.emails == [] and any("does not match" in n for n in p.notes)
    assert outreach.lookup(settings, "https://www.twitch.tv/nobody").emails == []
    # a link to twitch.tv/sam_other is not a link back to twitch.tv/sam
    _twitch_with_youtube(monkeypatch, {"sam": {"channel": "Sam", "description": "x@sam.test https://twitch.tv/sam_other"}}, {})
    assert outreach.lookup(settings, "https://www.twitch.tv/sam").emails[0].get("unconfirmed")
