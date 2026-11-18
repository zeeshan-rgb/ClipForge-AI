"""Shared fixtures.

Tests never touch the real data folder, never open a browser and never show a
file dialog: every app is built on a temporary folder, and the picker is stubbed.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from clipforge import ingest, pipelines
from clipforge.analysis.transcript import Segment, Word
from clipforge.api import system as system_api
from clipforge.ingest import IngestError, IngestRefused, IngestSource, SourceMeta
from clipforge.ingest.ytdlp_source import YtDlpSource
from clipforge.config import REPO_ROOT, Settings
from clipforge.db import make_engine, make_session_factory
from clipforge.events import EventBus
from clipforge.jobs import engine as engine_module
from clipforge.moments import SUBSCORES


OPENED_FOLDERS: list[Path] = []  # folders the app asked to show in the file manager (never really opened)


def pytest_configure(config):
    """Test files (renders included) go on D: when this machine has one: C: is nearly full.

    Set CLIPFORGE_TEST_TMP to choose another folder; it is emptied at the start of each run.
    """
    import os

    if config.option.basetemp:
        return
    chosen = os.environ.get("CLIPFORGE_TEST_TMP") or (r"D:\tmp\cf_pytest" if Path("D:/").exists() else None)
    if chosen:
        config.option.basetemp = chosen


@pytest.fixture(autouse=True)
def _no_real_side_effects(monkeypatch):
    def _refuse(*_a, **_k):
        raise AssertionError("tests must not open a real file dialog")

    OPENED_FOLDERS.clear()
    monkeypatch.setattr(system_api, "pick_video_file", _refuse)
    monkeypatch.setattr(system_api, "pick_audio_file", _refuse)
    monkeypatch.setattr(system_api, "open_in_explorer", lambda folder: OPENED_FOLDERS.append(folder))
    monkeypatch.setattr("webbrowser.open", lambda *_a, **_k: False)
    monkeypatch.setattr(engine_module, "RETRY_DELAY_SECONDS", 0.01)


class FakeSource(IngestSource):
    """Stands in for a platform: 'downloads' by copying a local file."""

    def __init__(self, kind: str, label: str, hosts: tuple[str, ...]) -> None:
        self.kind, self.label, self.hosts = kind, label, hosts
        self.video: Path | None = None
        self.refuse: IngestRefused | None = None
        self.transient_failures = 0
        self.metadata_calls = 0
        self.download_calls = 0
        self.est_bytes: int | None = 1_000_000

    def fetch_metadata(self, url, settings):
        self.metadata_calls += 1
        if self.refuse:
            raise self.refuse
        if self.transient_failures > 0:
            self.transient_failures -= 1
            raise IngestError("Could not fetch the video: connection reset")
        return SourceMeta(
            platform=self.kind, video_id="abc123", title="Original Stream Title",
            creator="Channel Name", creator_url="https://example.test/channel",
            duration=2.0, upload_date="2026-01-02", licence=None, webpage_url=url,
            est_bytes=self.est_bytes, max_height=360,
        )

    def download(self, url, dest_dir, settings, progress):
        self.download_calls += 1
        assert self.video is not None, "test must set a video for the fake source"
        dest_dir.mkdir(parents=True, exist_ok=True)
        progress(0.5, "halfway")
        out = dest_dir / "source.mp4"
        shutil.copyfile(self.video, out)
        progress(1.0, "done")
        return out


@pytest.fixture(autouse=True)
def fake_sources(monkeypatch) -> dict[str, FakeSource]:
    """Every test gets fake platforms; the real downloader is unreachable."""
    fakes = [
        FakeSource("youtube", "YouTube", ("youtube.com", "youtu.be")),
        FakeSource("twitch", "Twitch", ("twitch.tv",)),
        FakeSource("kick", "Kick", ("kick.com",)),
    ]
    monkeypatch.setattr(ingest, "SOURCES", fakes)

    def _no_network(*_a, **_k):
        raise AssertionError("tests must not reach the network")

    monkeypatch.setattr(YtDlpSource, "_extract", _no_network)
    return {f.kind: f for f in fakes}


class FakeTranscriber:
    """Stands in for Whisper: one line per chunk, saying how long the chunk was."""

    name = "fake-stt"

    def __init__(self) -> None:
        self.calls: list[float] = []
        self.fail_on_call: int | None = None
        self.memory_limit_seconds: float | None = None  # audio longer than this "runs out of memory"
        self.closed = False

    def close(self) -> None:
        self.closed = True

    def transcribe(self, audio, language, on_progress=None):
        seconds = len(audio) / 16000
        self.calls.append(round(seconds, 2))
        if self.memory_limit_seconds is not None and seconds > self.memory_limit_seconds:
            raise MemoryError(f"Unable to allocate {seconds * 1.0:.0f}. MiB for an array")
        if self.fail_on_call is not None and len(self.calls) == self.fail_on_call:
            raise RuntimeError("speech engine crashed")
        if on_progress:
            on_progress(0.5)
        words = [Word(0.2, 0.5, "hello"), Word(0.6, 0.9, "chat.")]  # a finished sentence
        return [Segment(0.2, 0.9, f"hello chat ({seconds:.1f}s chunk)", words)], language or "en"


@pytest.fixture(autouse=True)
def fake_stt(monkeypatch) -> FakeTranscriber:
    """No test loads or downloads the real speech model."""
    fake = FakeTranscriber()
    monkeypatch.setattr(pipelines, "make_transcriber", lambda _settings: fake)
    monkeypatch.setattr(pipelines, "model_is_downloaded", lambda _settings: True)
    return fake


class FakeReasoner:
    """Stands in for the AI model. Answers from the task text alone, so tests are offline and free."""

    name = "fake-ai"

    def __init__(self) -> None:
        self.text_tasks: list[str] = []
        self.visual_tasks: list[str] = []
        self.meta_tasks: list[str] = []
        self.meta_answer = None  # callable(clip_ids) -> dict, or None for the default
        self.fail_meta: Exception | None = None
        self.images_seen: list[list[str]] = []
        self.text_answer = None  # callable(line_numbers) -> dict, or None for the default
        self.visual_answer = None  # callable(moment_ids) -> dict, or None for the default
        self.fail_text: Exception | None = None
        self.fail_visual: Exception | None = None

    def ask_json(self, task, schema, images=None, check=None):
        if check:
            check()
        if "stickers" in schema.get("properties", {}):  # Story mode draft
            self.story_tasks = getattr(self, "story_tasks", []) + [task]
            return {"lines": [{"role": "hook", "text": "You will not believe what happens next"},
                              {"role": "context", "text": "The streamer is about to greet chat"},
                              {"role": "reaction", "text": "Honestly that was the best hello ever"},
                              {"role": "outro", "text": "Follow for tomorrow's peak moment"}],
                    "cards": [{"text": "Moments earlier", "when": "start"}],
                    "stickers": [{"id": "no_way", "when": "at_payoff"}, {"id": "made_up", "when": "at_payoff"}]}
        if "clips" in schema.get("properties", {}):  # titles and descriptions
            self.meta_tasks.append(task)
            if self.fail_meta:
                raise self.fail_meta
            ids = [int(n) for n in re.findall(r"^Clip (\d+):", task, flags=re.M)]
            if self.meta_answer:
                return self.meta_answer(ids)
            return {"clips": [
                {"id": i, "title": "Chat gets a warm hello", "description": "The streamer greets chat.",
                 "hashtags": ["#hello", "chat", "#Shorts"], "pinned_comment": "How do you greet your chat?"}
                for i in ids
            ]}
        if images:
            self.visual_tasks.append(task)
            self.images_seen.append([p.name for p in images])
            assert all(p.is_file() and p.stat().st_size > 0 for p in images), "sheets must exist when sent"
            if self.fail_visual:
                raise self.fail_visual
            ids = [int(n) for n in re.findall(r"^Moment (\d+):", task, flags=re.M)]
            if self.visual_answer:
                return self.visual_answer(ids)
            scores = {k: 6 for k in SUBSCORES}
            return {"moments": [
                {"id": i, "seen": "A person reacts at a desk.", "picture_useful": True, "keep": True,
                 "category": "funny", "scores": scores}
                for i in ids
            ]}
        self.text_tasks.append(task)
        if self.fail_text:
            raise self.fail_text
        numbers = [int(n) for n in re.findall(r"^L(\d+) \[", task, flags=re.M)]
        if self.text_answer:
            return self.text_answer(numbers)
        if not numbers:
            return {"moments": []}
        return {"moments": [{
            "start_line": numbers[0], "end_line": numbers[-1], "peak_line": numbers[-1],
            "category": "funny", "scores": {k: 7 for k in SUBSCORES}, "reason": "They say hello to chat.",
        }]}


class FakeGoogle:
    """Stands in for Google's sign-in and the YouTube Data API. Records every request; nothing leaves this computer."""

    SESSION = "https://upload.fake.test/session/1"

    def __init__(self) -> None:
        self.requests: list[tuple[str, str]] = []
        self.uploads: list[dict] = []  # the metadata sent when an upload starts
        self.received = b""
        self.drop_after: int | None = None  # simulate a dropped connection once this many bytes have arrived
        self.quota = False
        self.locked_private = False  # an unaudited API project: YouTube keeps videos private
        self.revoked = False
        self.granted_scope = "https://www.googleapis.com/auth/youtube"
        self.privacy = "private"
        self.updates: list[dict] = []
        self.expected_size = 0
        self.refuse_text = False
        self.publish_at: str | None = None  # a scheduled publish time YouTube kept

    def handler(self, request):
        import httpx

        url = str(request.url)
        self.requests.append((request.method, url.split("?")[0]))
        if url.startswith("https://oauth2.googleapis.com/token"):
            form = dict(x.split("=", 1) for x in request.content.decode().split("&"))
            if form.get("grant_type") == "refresh_token" and self.revoked:
                return httpx.Response(400, json={"error": "invalid_grant"})
            body = {"access_token": "access-1", "expires_in": 3600, "scope": self.granted_scope}
            if form.get("grant_type") == "authorization_code":
                body["refresh_token"] = "refresh-1"
            return httpx.Response(200, json=body)
        if url.startswith("https://oauth2.googleapis.com/revoke"):
            self.revoked = True
            return httpx.Response(200)
        if url.startswith("https://www.googleapis.com/youtube/v3/channels"):
            return httpx.Response(200, json={"items": [{"id": "UC123", "snippet": {"title": "Test Channel"}}]})
        if url.startswith("https://www.googleapis.com/upload/youtube/v3/videos"):
            if self.quota:
                return httpx.Response(403, json={"error": {"message": "quota", "errors": [{"reason": "quotaExceeded"}]}})
            self.uploads.append(json.loads(request.content))
            self.expected_size = int(request.headers["X-Upload-Content-Length"])
            self.received = b""
            return httpx.Response(200, headers={"Location": self.SESSION})
        if url.startswith(self.SESSION):
            rng = request.headers["Content-Range"]
            if rng.startswith("bytes */"):
                if len(self.received) == self.expected_size:
                    return self._done()
                return httpx.Response(308, headers={"Range": f"bytes=0-{len(self.received) - 1}"} if self.received else {})
            start = int(rng.split(" ")[1].split("-")[0])
            assert start == len(self.received), "chunks must carry on exactly where the server stopped"
            if self.drop_after is not None and len(self.received) >= self.drop_after:
                self.drop_after = None
                raise httpx.ConnectError("connection dropped", request=request)
            self.received += request.content
            if len(self.received) == self.expected_size:
                return self._done()
            return httpx.Response(308, headers={"Range": f"bytes=0-{len(self.received) - 1}"})
        if url.startswith("https://www.googleapis.com/youtube/v3/videos"):
            if request.method == "GET":
                st = {"privacyStatus": self.privacy, "uploadStatus": "processed", "selfDeclaredMadeForKids": False,
                      "license": "youtube", "embeddable": True}
                if self.publish_at:
                    st["publishAt"] = self.publish_at
                return httpx.Response(200, json={"items": [{"id": "vid123", "status": st}]})
            body = json.loads(request.content)
            self.updates.append(body)
            if "snippet" in body:
                if self.refuse_text:
                    return httpx.Response(400, json={"error": {"message": "bad title",
                                                               "errors": [{"reason": "invalidTitle"}]}})
                return httpx.Response(200, json={"id": "vid123", "snippet": body["snippet"]})
            if not self.locked_private:
                self.privacy = body["status"]["privacyStatus"]
                self.publish_at = body["status"].get("publishAt")
            st = {"privacyStatus": self.privacy, **({"publishAt": self.publish_at} if self.publish_at else {})}
            return httpx.Response(200, json={"id": "vid123", "status": st})
        raise AssertionError(f"unexpected request to {url}")

    def _done(self):
        import httpx

        st = {"privacyStatus": "private", "uploadStatus": "uploaded"}
        asked = (self.uploads[-1].get("status") or {}).get("publishAt") if self.uploads else None
        if asked and not self.locked_private:
            self.publish_at = st["publishAt"] = asked
        return httpx.Response(200, json={"id": "vid123", "status": st})


@pytest.fixture(autouse=True)
def no_real_google(monkeypatch):
    """No test reaches Google. Tests that need it use `fake_google`."""
    from clipforge import youtube

    def refuse():
        raise AssertionError("tests must not reach Google")

    monkeypatch.setattr(youtube, "make_http", refuse)
    from clipforge import gmail, outreach

    monkeypatch.setattr(gmail, "make_http", refuse)
    monkeypatch.setattr(outreach, "make_web", refuse)
    monkeypatch.setattr(outreach, "ytdlp_info", lambda *_a, **_k: refuse())


@pytest.fixture
def fake_google(monkeypatch) -> FakeGoogle:
    import httpx

    from clipforge import youtube

    fake = FakeGoogle()
    monkeypatch.setattr(youtube, "make_http", lambda: httpx.Client(transport=httpx.MockTransport(fake.handler)))
    return fake


@pytest.fixture(autouse=True)
def fake_ai(monkeypatch) -> FakeReasoner:
    """No test reaches a real AI provider."""
    fake = FakeReasoner()
    monkeypatch.setattr(pipelines, "make_reasoner", lambda _settings: fake)
    return fake


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    s = Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        ffmpeg_dir=REPO_ROOT / "tools" / "ffmpeg",
        fonts_dir=REPO_ROOT / "tools" / "fonts",
        frontend_dist=tmp_path / "no-dist",
        outreach_interval_seconds=0,  # tests run the background sender's rounds themselves
    )
    s.ensure_dirs()
    return s


@pytest.fixture
def db(settings: Settings):
    engine = make_engine(settings.db_path)
    yield make_session_factory(engine)
    engine.dispose()


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


def wait_for(predicate, timeout: float = 20.0, interval: float = 0.02):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError("condition not met in time")


@pytest.fixture
def sample_video(settings: Settings, tmp_path: Path) -> Path:
    """A real two-second 640x360 clip with audio, made by the bundled FFmpeg."""
    if not settings.ffmpeg:
        pytest.skip("FFmpeg is not installed")
    out = tmp_path / "sample clip.mp4"
    subprocess.run(
        [settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "testsrc2=s=640x360:r=30:d=2",
         "-f", "lavfi", "-i", "sine=f=440:d=2",
         "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(out)],
        check=True, capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return out
