"""Posting schedule: slots in the owner's time zone, uploads with a publish time, and automatic scheduling."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from clipforge import schedule
from clipforge.app import create_app
from clipforge.models import utcnow

from .conftest import wait_for
from .test_qc_export import finished_project, wait_ready
from .test_youtube import client_file, connect  # noqa: F401 (fixture)


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings, configure_logging=False)) as c:
        yield c


def test_slots_follow_the_owners_time_zone_and_skip_ones_too_soon():
    cfg = {"times": ["12:00", "18:00"], "timezone": "Asia/Kolkata"}
    now = datetime(2026, 11, 18, 6, 0)  # 11:30 in Kolkata
    first = list(zip(schedule.slots(cfg, now), range(3)))
    # 12:00 IST is 06:30 UTC: only 30 minutes away, which is just enough
    assert [a for a, _ in first] == [datetime(2026, 11, 18, 6, 30), datetime(2026, 11, 18, 12, 30),
                                     datetime(2026, 11, 19, 6, 30)]
    later = next(schedule.slots(cfg, datetime(2026, 11, 18, 6, 10)))
    assert later == datetime(2026, 11, 18, 12, 30), "a slot under 30 minutes away is skipped"


def test_schedule_settings_are_checked(client):
    assert client.put("/api/schedule", json={"times": ["25:00"]}).status_code == 422
    assert client.put("/api/schedule", json={"times": ["1:2:3"]}).status_code == 422
    assert client.put("/api/schedule", json={"timezone": "Mars/Base"}).status_code == 422
    assert client.put("/api/schedule", json={"times": [f"{h:02d}:00" for h in range(7)]}).status_code == 422
    r = client.put("/api/schedule", json={"times": ["18:00", "09:30", "18:00"], "timezone": "Asia/Kolkata"}).json()
    assert r["settings"]["times"] == ["09:30", "18:00"] and len(r["next_slots"]) == 5


def test_a_short_is_uploaded_private_with_its_publish_time(client, sample_video, client_file, fake_google):
    connect(client, client_file, fake_google)
    project, clip = finished_project(client, sample_video)
    url = f"/api/projects/{project['id']}/clips/{clip['id']}/schedule"
    assert client.post(url, json={}).status_code == 409, "needs the owner's confirmation"
    at = (utcnow() + timedelta(days=1)).replace(microsecond=0)
    r = client.post(url, json={"confirm": True, "at": at.isoformat() + "Z"})
    assert r.status_code == 200, r.text
    wait_ready(client, project["id"])
    sent = fake_google.uploads[-1]["status"]
    assert sent["privacyStatus"] == "private" and sent["publishAt"] == at.isoformat() + "Z"
    video = client.get(f"/api/projects/{project['id']}/clips").json()[0]["youtube"]
    assert video["video_id"] == "vid123" and video["publish_at"].startswith(at.isoformat())
    up = client.get("/api/schedule").json()["upcoming"]
    assert [u["clip_id"] for u in up] == [clip["id"]]

    # moved to another time, then taken off the schedule: it stays private
    later = at + timedelta(hours=6)
    assert client.post(url, json={"confirm": True, "at": later.isoformat() + "Z"}).status_code == 200
    assert fake_google.updates[-1]["status"]["publishAt"] == later.isoformat() + "Z"
    assert client.delete(url).status_code == 200
    assert "publishAt" not in fake_google.updates[-1]["status"]
    assert fake_google.updates[-1]["status"]["privacyStatus"] == "private"
    assert client.get("/api/schedule").json()["upcoming"] == []


def test_an_unaudited_project_keeping_videos_private_is_said_plainly(client, sample_video, client_file, fake_google):
    fake_google.locked_private = True
    connect(client, client_file, fake_google)
    project, clip = finished_project(client, sample_video)
    client.post(f"/api/projects/{project['id']}/clips/{clip['id']}/schedule", json={"confirm": True})
    wait_ready(client, project["id"])
    video = client.get(f"/api/projects/{project['id']}/clips").json()[0]["youtube"]
    assert video["publish_at"] is None and "did not keep the publish time" in video["schedule_note"]


def test_only_approved_shorts_with_confirmed_rights_are_scheduled(client, sample_video, client_file, fake_google):
    connect(client, client_file, fake_google)
    project, clip = finished_project(client, sample_video)
    client.patch(f"/api/projects/{project['id']}/rights", json={"rights_status": "unverified"})
    r = client.post(f"/api/projects/{project['id']}/clips/{clip['id']}/schedule", json={"confirm": True})
    assert r.status_code == 409 and "rights" in r.json()["error"]
    client.patch(f"/api/projects/{project['id']}/rights", json={"rights_status": "own"})
    client.patch(f"/api/projects/{project['id']}/clips/{clip['id']}", json={"review": "rejected"})
    r = client.post(f"/api/projects/{project['id']}/clips/{clip['id']}/schedule", json={"confirm": True})
    assert r.status_code == 409 and "Approve" in r.json()["error"]
    assert fake_google.uploads == []


def test_automatic_mode_fills_the_next_slots_with_approved_shorts(client, settings, sample_video, client_file, fake_google):
    connect(client, client_file, fake_google)
    project, clip = finished_project(client, sample_video)
    ctx = client.app.state.ctx
    assert schedule.tick(settings, ctx.session, ctx.worker) == 0, "off until the owner switches it on"
    client.put("/api/schedule", json={"enabled": True, "auto": True, "times": ["00:00", "06:00", "12:00", "18:00"],
                                      "timezone": "UTC"})
    assert schedule.tick(settings, ctx.session, ctx.worker) == 1
    wait_ready(client, project["id"])
    video = client.get(f"/api/projects/{project['id']}/clips").json()[0]["youtube"]
    assert video["video_id"] and video["publish_at"] and video["scheduled_by"] == "auto"
    assert fake_google.uploads[-1]["status"]["publishAt"]
    assert schedule.tick(settings, ctx.session, ctx.worker) == 0, "never scheduled twice"

    # taken off the schedule by the owner: automatic mode leaves it alone
    client.delete(f"/api/projects/{project['id']}/clips/{clip['id']}/schedule")
    assert schedule.tick(settings, ctx.session, ctx.worker) == 0
