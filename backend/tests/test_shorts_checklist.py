"""Phase 11.9: the YouTube Shorts publishing checklist, and that the upload and publish steps enforce it."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from clipforge import youtube as yt

from .test_qc_export import finished_project, wait_ready
from .test_youtube import client, client_file, connect  # noqa: F401  (fixtures)


@pytest.fixture
def video_file(tmp_path) -> Path:
    path = tmp_path / "final.mp4"
    path.write_bytes(b"\0" * 1024)
    return path


def make_clip(video_file: Path, **over):
    final = {"file": str(video_file), "name": "final.mp4", "width": 1080, "height": 1920, "duration": 45.0,
             "video_codec": "h264", "audio_codec": "aac", "loudness": {"lufs": -14.0, "true_peak": -1.5},
             "qc": {"passed": True, "score": 100, "checks": []},
             "story": {"seconds": 9.0, "lines": 3}}  # the owner's narration (Story mode)
    final.update(over.pop("final", {}))
    meta = {"title": "A good title", "description": "Credit: Someone on YouTube\nOriginal video: x\n\nText.\n\n#a #Shorts",
            "hashtags": ["#a", "#Shorts"]}
    meta.update(over.pop("meta", {}))
    return SimpleNamespace(review=over.pop("review", "approved"), final=final, meta=meta,
                           music=over.pop("music", {"used": False}))


def check(clip, rights="licensed", connected=True, kids=False):
    result = yt.shorts_checklist(rights, clip, connected=connected, channel="Test Channel",
                                 defaults={"made_for_kids": kids})
    return result, {i["name"]: i for i in result["items"]}


def test_a_ready_short_passes_every_item(video_file):
    result, items = check(make_clip(video_file))
    assert result["passed"] and result["fails"] == 0 and result["warnings"] == 0
    assert {"rights", "approved", "rendered", "vertical", "duration", "encoding", "audio", "quality", "title",
            "description", "hashtags", "credit", "kids", "channel"} <= set(items)
    assert items["kids"]["detail"] == "Declared not made for kids."


@pytest.mark.parametrize(("rights", "status"), [("own", "ok"), ("licensed", "ok"), ("creator_permission", "ok"),
                                                 ("unverified", "fail")])
def test_rights(video_file, rights, status):
    _result, items = check(make_clip(video_file), rights=rights)
    assert items["rights"]["status"] == status
    if status == "fail":
        assert yt.publish_blocker(rights, make_clip(video_file)) == yt.RIGHTS_REFUSAL


def test_not_approved_or_not_rendered(video_file, tmp_path):
    _r, items = check(make_clip(video_file, review="pending"))
    assert items["approved"]["status"] == "fail"
    _r, items = check(make_clip(video_file, final={"file": str(tmp_path / "gone.mp4")}))
    assert items["rendered"]["status"] == "fail" and "vertical" not in items


def test_size_and_encoding(video_file):
    _r, items = check(make_clip(video_file, final={"width": 1920, "height": 1080, "video_codec": "hevc"}))
    assert items["vertical"]["status"] == "fail" and items["encoding"]["status"] == "fail"


@pytest.mark.parametrize(("seconds", "rights", "music", "status"), [
    (45, "licensed", False, "ok"),
    (180, "own", False, "ok"),
    (181, "own", False, "fail"),
    (90, "licensed", False, "warn"),  # someone else's footage: a claim would block a Short over a minute
    (90, "own", True, "warn"),  # own footage but music: the same risk
    (90, "own", False, "ok"),
])
def test_duration(video_file, seconds, rights, music, status):
    clip = make_clip(video_file, final={"duration": float(seconds)},
                     music={"used": music, "licence_name": "CC BY 4.0", "title": "t"})
    _r, items = check(clip, rights=rights)
    assert items["duration"]["status"] == status


def test_sound(video_file):
    _r, items = check(make_clip(video_file, final={"loudness": {"lufs": -9.0, "true_peak": 0.2}}))
    assert items["audio"]["status"] == "fail" and "distort" in items["audio"]["detail"]
    _r, items = check(make_clip(video_file, final={"audio_codec": None}))
    assert items["audio"]["status"] == "warn"


def test_a_failed_quality_check_blocks_unless_overridden(video_file):
    clip = make_clip(video_file, final={"qc": {"passed": False, "score": 70, "checks": [
        {"label": "No black bars", "status": "fail"}]}})
    result, items = check(clip)
    assert items["quality"]["status"] == "fail" and items["quality"]["overridable"]
    assert not result["passed"] and result["passed_with_override"]
    assert "No black bars" in yt.checklist_blocker(result, override=False)
    assert yt.checklist_blocker(result, override=True) is None
    result, _ = check(make_clip(video_file, review="pending", final=clip.final))
    assert yt.checklist_blocker(result, override=True) is not None, "an override passes only the quality check"


def test_title_description_and_hashtags(video_file):
    _r, items = check(make_clip(video_file, meta={"title": ""}))
    assert items["title"]["status"] == "fail"
    _r, items = check(make_clip(video_file, meta={"title": "x" * 101}))
    assert items["title"]["status"] == "fail"
    _r, items = check(make_clip(video_file, meta={"title": "a <b>"}))
    assert items["title"]["status"] == "warn"
    _r, items = check(make_clip(video_file, meta={"description": "y" * 5001}))
    assert items["description"]["status"] == "fail"
    many = "Credit: x\n" + " ".join(f"#t{i}" for i in range(61))
    _r, items = check(make_clip(video_file, meta={"description": many}))
    assert items["hashtags"]["status"] == "fail" and "ignores every one" in items["hashtags"]["detail"]
    _r, items = check(make_clip(video_file, meta={"description": "Credit: x\n" + " ".join(f"#t{i}" for i in range(60))}))
    assert items["hashtags"]["status"] == "ok"


def test_credit_and_music_licence(video_file):
    _r, items = check(make_clip(video_file, meta={"description": "No credit here."}))
    assert items["credit"]["status"] == "warn"
    _r, items = check(make_clip(video_file, meta={"description": "Mine."}), rights="own")
    assert "credit" not in items, "the owner's own video needs no credit"
    _r, items = check(make_clip(video_file, music={"used": True, "title": "Song", "licence_name": None}))
    assert items["music"]["status"] == "fail"


def test_channel_and_kids(video_file):
    result, items = check(make_clip(video_file), connected=False, kids=True)
    assert items["channel"]["status"] == "fail" and not result["passed"]
    assert items["kids"]["detail"] == "Declared made for kids."


# ---- enforced by the endpoints ------------------------------------------------------------------------


def uploaded(client, client_file, fake_google, sample_video):  # noqa: F811
    connect(client, client_file, fake_google)
    project, clip = finished_project(client, sample_video)
    client.post(f"/api/projects/{project['id']}/upload", json={"clip_ids": [clip["id"]]})
    wait_ready(client, project["id"])
    [clip] = client.get(f"/api/projects/{project['id']}/clips").json()
    return project, clip


def test_the_checklist_endpoint_reports_what_the_steps_enforce(client, client_file, fake_google, sample_video):  # noqa: F811
    project, clip = finished_project(client, sample_video)
    url = f"/api/projects/{project['id']}/clips/{clip['id']}/publish-checklist"
    result = client.get(url).json()
    by = {i["name"]: i for i in result["items"]}
    assert by["channel"]["status"] == "fail" and not result["passed"]
    r = client.post(f"/api/projects/{project['id']}/upload", json={"clip_ids": [clip["id"]]})
    assert r.status_code == 409
    connect(client, client_file, fake_google)
    assert client.get(url).json()["passed"]


def test_too_many_hashtags_block_publishing_and_sending_text(client, client_file, fake_google, sample_video):  # noqa: F811
    project, clip = uploaded(client, client_file, fake_google, sample_video)
    pid, cid = project["id"], clip["id"]
    bad = {"title": clip["meta"]["title"], "description": " ".join(f"#t{i}" for i in range(61)),
           "hashtags": [], "pinned_comment": ""}
    client.patch(f"/api/projects/{pid}/clips/{cid}/meta", json=bad)
    r = client.post(f"/api/projects/{pid}/clips/{cid}/publish",
                    json={"privacy": "public", "title": clip["youtube"]["title"], "confirm": True})
    assert r.status_code == 409 and "Hashtags" in r.json()["error"]
    assert client.post(f"/api/projects/{pid}/clips/{cid}/youtube-text").status_code == 409
    assert not any("snippet" in u for u in fake_google.updates)


def test_edited_text_is_sent_to_the_uploaded_video_and_then_confirmed_against(client, client_file, fake_google, sample_video):  # noqa: F811
    project, clip = uploaded(client, client_file, fake_google, sample_video)
    pid, cid = project["id"], clip["id"]
    checklist = f"/api/projects/{pid}/clips/{cid}/publish-checklist"
    assert client.get(checklist).json()["text_differs"] is False
    client.patch(f"/api/projects/{pid}/clips/{cid}/meta",
                 json={"title": "A better title", "description": "New words. #new", "hashtags": ["#new"],
                       "pinned_comment": "?"})
    assert client.get(checklist).json()["text_differs"] is True
    r = client.post(f"/api/projects/{pid}/clips/{cid}/youtube-text")
    assert r.status_code == 200 and r.json()["youtube"]["title"] == "A better title"
    [sent] = [u for u in fake_google.updates if "snippet" in u]
    assert sent["id"] == "vid123" and sent["snippet"]["title"] == "A better title"
    assert sent["snippet"]["categoryId"] == "20" and sent["snippet"]["tags"] == ["new"]
    assert client.get(checklist).json()["text_differs"] is False
    url = f"/api/projects/{pid}/clips/{cid}/publish"
    old = client.post(url, json={"privacy": "public", "title": clip["youtube"]["title"], "confirm": True})
    assert old.status_code == 409, "the confirmation must name the title now on YouTube"
    assert client.post(url, json={"privacy": "public", "title": "A better title", "confirm": True}).status_code == 200


def test_youtube_refusing_new_text_is_reported(client, client_file, fake_google, sample_video):  # noqa: F811
    project, clip = uploaded(client, client_file, fake_google, sample_video)
    fake_google.refuse_text = True
    r = client.post(f"/api/projects/{project['id']}/clips/{clip['id']}/youtube-text")
    assert r.status_code == 409 and "YouTube refused the new text" in r.json()["error"]


def test_text_cannot_be_sent_before_upload(client, sample_video):  # noqa: F811
    project, clip = finished_project(client, sample_video)
    r = client.post(f"/api/projects/{project['id']}/clips/{clip['id']}/youtube-text")
    assert r.status_code == 409 and "goes with the upload" in r.json()["error"]


def test_someone_elses_footage_without_narration_gets_a_reused_content_warning(video_file):
    _r, items = check(make_clip(video_file, final={"story": None}))
    assert items["original"]["status"] == "warn" and "reused-content" in items["original"]["detail"]
    _r, items = check(make_clip(video_file, final={"story": None}), rights="own")
    assert "original" not in items, "the owner's own footage needs no commentary"
    _r, items = check(make_clip(video_file))
    assert items["original"]["status"] == "ok"
