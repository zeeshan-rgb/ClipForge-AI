"""Story mode: the AI's draft lines, the owner's recordings, narration mixed in, cards and stickers, and the checks."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from clipforge import qc, schedule, story as st
from clipforge.app import create_app

from .test_qc_export import finished_project, wait_ready


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings, configure_logging=False)) as c:
        yield c


def tone(settings, path: Path, seconds: float = 0.8, freq: int = 300, silent: bool = False) -> Path:
    src = "anullsrc=r=48000:cl=mono" if silent else f"sine=f={freq}:r=48000"
    subprocess.run([settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"{src}", "-t", f"{seconds}", str(path)], check=True)
    return path


def url(project, clip, tail=""):
    return f"/api/projects/{project['id']}/clips/{clip['id']}/story{tail}"


def test_the_ai_drafts_lines_cards_and_stickers_from_the_list_only(client, sample_video, fake_ai):
    project, clip = finished_project(client, sample_video, rights="licensed", creator="SomeStreamer")
    r = client.post(url(project, clip, "/draft")).json()
    assert [l["role"] for l in r["lines"]] == ["hook", "context", "reaction", "outro"]
    assert r["lines"][0]["at"] <= 0.15 and all(l["audio"] is None for l in r["lines"])
    assert [x["sticker"] for x in r["stickers"]] == ["no_way"], "an unknown sticker is dropped"
    assert r["cards"][0]["text"] == "Moments earlier"
    assert "SomeStreamer" in fake_ai.story_tasks[-1] and "own voice" in fake_ai.story_tasks[-1]
    assert r["contribution"]["seconds"] == 0


def test_a_recording_is_cleaned_placed_and_dropped_when_its_words_change(client, settings, sample_video, tmp_path):
    project, clip = finished_project(client, sample_video, rights="licensed", creator="SomeStreamer")
    story = client.post(url(project, clip, "/draft")).json()
    hook = story["lines"][0]
    take = tone(settings, tmp_path / "take.wav")
    with take.open("rb") as fh:
        r = client.post(url(project, clip, f"/lines/{hook['id']}/audio"), files={"file": ("take.wav", fh, "audio/wav")})
    assert r.status_code == 200, r.text
    line = r.json()["lines"][0]
    assert 0.6 <= line["seconds"] <= 0.9 and Path(line["audio"]).is_file()
    assert client.get(url(project, clip, f"/lines/{hook['id']}/audio")).status_code == 200
    with tone(settings, tmp_path / "quiet.wav", silent=True).open("rb") as fh:
        bad = client.post(url(project, clip, f"/lines/{hook['id']}/audio"), files={"file": ("q.wav", fh)})
    assert bad.status_code == 422 and "silent" in bad.json()["error"]

    lines = [{"id": l["id"], "role": l["role"], "text": l["text"]} for l in r.json()["lines"]]
    keep = client.put(url(project, clip), json={"lines": lines, "cards": [], "stickers": []}).json()
    assert keep["lines"][0]["audio"], "unchanged words keep their take"
    lines[0]["text"] = "Something else entirely"
    changed = client.put(url(project, clip), json={"lines": lines, "cards": [], "stickers": []}).json()
    assert changed["lines"][0]["audio"] is None and not Path(line["audio"]).exists()
    assert client.put(url(project, clip), json={"lines": lines, "stickers": [{"sticker": "nope_x", "at": 1}]}).status_code == 422


def test_the_final_short_carries_the_narration_and_says_how_much(client, settings, tmp_path):
    video = tmp_path / "longer.mp4"
    subprocess.run([settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=s=640x360:r=30:d=6", "-f", "lavfi", "-i", "sine=f=440:d=6", "-c:v", "libx264",
                    "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(video)], check=True)
    project, clip = finished_project(client, video, rights="licensed", creator="SomeStreamer")
    before = clip["final"]["qc"]
    assert next(c for c in before["checks"] if c["name"] == "original")["status"] == "warn"
    story = client.post(url(project, clip, "/draft")).json()
    hook = story["lines"][0]
    with tone(settings, tmp_path / "take.wav", seconds=0.9, freq=660).open("rb") as fh:
        client.post(url(project, clip, f"/lines/{hook['id']}/audio"), files={"file": ("take.wav", fh)})
    assert client.get(url(project, clip)).json()["render_needed"], "the story changed after the render"
    client.post(f"/api/projects/{project['id']}/render-final", json={})
    wait_ready(client, project["id"])
    [clip] = client.get(f"/api/projects/{project['id']}/clips").json()
    final = clip["final"]
    assert final["story"]["seconds"] == pytest.approx(0.9, abs=0.15) and final["story"]["lines"] == 1
    original = next(c for c in final["qc"]["checks"] if c["name"] == "original")
    assert original["status"] == "warn" and "Only" in original["detail"], "under 4 s is still too little"
    assert not client.get(url(project, clip)).json()["render_needed"]


def test_the_mix_lowers_the_clip_under_the_narration_and_keeps_it_elsewhere(settings, tmp_path):
    from clipforge.media import replace_audio

    video = tmp_path / "v.mp4"
    subprocess.run([settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=s=320x240:r=30:d=6", "-f", "lavfi", "-i", "sine=f=440:d=6", "-c:v", "libx264",
                    "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(video)], check=True)
    take = tmp_path / "take.wav"
    st.clean_recording(settings, tone(settings, tmp_path / "raw.wav", seconds=1.5, freq=660), take)
    story = {"lines": [{"id": "l", "role": "hook", "text": "x", "audio": str(take), "seconds": 1.5, "at": 2.0}],
             "cards": [], "stickers": []}
    spec = st.build_voice(settings, story, 6.0, tmp_path, "c")
    out = tmp_path / "out.mp4"
    out.write_bytes(video.read_bytes())
    replace_audio(settings, out, video, [(0.0, 6.0)], voice=spec)
    wav = tmp_path / "out.wav"
    subprocess.run([settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(out), "-ac", "1",
                    "-ar", "48000", str(wav)], check=True)
    data, sr = sf.read(wav)
    told, rest = data[int(2.4 * sr):int(3.2 * sr)], data[int(4.2 * sr):int(5.0 * sr)]
    band = lambda x, f: np.abs(np.fft.rfft(x))[int(f * len(x) / sr) - 2:int(f * len(x) / sr) + 3].max()
    assert band(told, 660) > 10 * band(rest, 660), "the narration is heard where it was placed"
    ratio = band(told, 440) / band(rest, 440)
    assert 0.2 < ratio < 0.45, f"the clip's own sound is about 10 dB lower under the narration ({ratio:.2f})"


def test_narration_box_cards_and_stickers_go_into_the_caption_file():
    story = {"lines": [{"id": "l1", "role": "hook", "text": "He really thought that would work", "audio": __file__,
                        "seconds": 1.5, "at": 0.2}],
             "cards": [{"id": "c1", "text": "10 minutes earlier", "at": 0.3, "seconds": 2.0}],
             "stickers": [{"id": "s1", "sticker": "plot_twist", "at": 5.0, "seconds": 1.6}]}
    ass = st.add_to_ass("[Script Info]\n\n[V4+ Styles]\nStyle: Caption,x\n\n[Events]\nFormat: x\n", story, 10.0)
    assert "Style: Narrator" in ass and "Style: Card" in ass and "Style: Sticker" in ass
    assert "0:00:00.20,0:00:01.85,Narrator" in ass and "10 MINUTES EARLIER" in ass and "PLOT TWIST" in ass
    assert ass.index("Style: Narrator") < ass.index("[Events]")


def test_the_voice_stem_and_the_ducking_envelope(settings, tmp_path):
    take = tone(settings, tmp_path / "t.wav", seconds=1.0)
    story = {"lines": [{"id": "l", "role": "hook", "text": "x", "audio": str(take), "seconds": 1.0, "at": 2.0}],
             "cards": [], "stickers": []}
    spec = st.build_voice(settings, story, 5.0, tmp_path, "c")
    voice, sr = sf.read(spec.stem)
    duck, _ = sf.read(spec.duck)
    assert len(voice) == 5 * sr and np.abs(voice[int(2.4 * sr):int(2.6 * sr)]).max() > 0.01
    assert np.abs(voice[int(3.5 * sr):]).max() == 0
    assert duck[int(2.5 * sr)][0] == pytest.approx(st.DUCK_TO) and duck[int(4.5 * sr)][0] == 1.0
    assert 0.3 < duck[int((2.0 - 0.06) * sr)][0] < 1.0, "it ramps down instead of jumping"


def test_contribution_check_and_the_schedule_gate():
    assert qc.contribution_check({"seconds": 9, "lines": 3, "cards": 1}).status == "ok"
    weak = qc.contribution_check({"seconds": 0, "lines": 0, "cards": 2, "stickers": 1})
    assert weak.status == "warn" and "not enough" in weak.detail
    clip = SimpleNamespace(final={"story": {"seconds": 8}, "rendered_at": "2026-11-18T10:00:00+00:00"},
                           story={"updated_at": "2026-11-18T09:00:00+00:00"})
    assert schedule.narrated(clip)
    clip.story["updated_at"] = "2026-11-18T11:00:00+00:00"
    assert not schedule.narrated(clip), "edited after the render: render again first"
    assert not schedule.narrated(SimpleNamespace(final={"story": {"seconds": 1}}, story=None))


def test_a_youtube_audio_library_folder_is_imported_with_moods_and_its_licence(client, settings, tmp_path):
    folder = tmp_path / "audio library"
    folder.mkdir()
    # a steady 150 bpm click track, and a slow quiet drone
    subprocess.run([settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "sine=f=1000:r=44100:d=0.05,apad=pad_dur=0.35", "-af", "aloop=loop=-1:size=17640",
                    "-t", "20", str(folder / "Upbeat Party.wav")], check=True)
    subprocess.run([settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "sine=f=110:r=44100:d=20", "-af", "volume=-24dB", str(folder / "Dark Mystery.wav")], check=True)
    (folder / "notes.txt").write_text("not audio")
    r = client.post("/api/music/import-folder", json={"folder": str(folder)}).json()
    tracks = {t["title"]: t for t in r["added"]}
    assert set(tracks) == {"Upbeat Party", "Dark Mystery"}
    assert tracks["Dark Mystery"]["moods"][0] == "suspense"
    assert "energetic" in tracks["Upbeat Party"]["moods"]
    assert all(t["usable"] for t in tracks.values()), "the Audio Library licence record is complete"
    again = client.post("/api/music/import-folder", json={"folder": str(folder)}).json()
    assert again["added"] == [] and len(again["skipped"]) == 2


def test_lines_never_overlap_and_stay_inside_the_clip():
    story = st.from_answer({"lines": [{"role": "hook", "text": "one two three four five six"},
                                      {"role": "reaction", "text": "one two three four five six seven eight nine"},
                                      {"role": "outro", "text": "one two three four five six seven eight nine ten"}],
                            "cards": [], "stickers": []})
    st.place(story, 27.1, 20.5)
    spans = sorted((l["at"], l["at"] + st.estimate_seconds(l["text"])) for l in story["lines"])
    assert all(b1 + st.LINE_GAP - 0.011 <= a2 for (_, b1), (a2, _) in zip(spans, spans[1:])), spans
    assert spans[-1][1] <= 27.1 and spans[0][0] >= 0


def test_required_hashtags_always_come_first_and_survive_the_limit():
    from clipforge import metadata as md

    many = [f"#tag{n}" for n in range(12)]
    tags = md.clean_hashtags(many, "funny", ["#LACY"])
    assert tags[0] == "#LACY" and tags[-1] == "#Shorts" and len(tags) <= md.HASHTAGS_MAX
    assert md.clean_hashtags(["#lacy", "#gta6"], None, ["LACY"]) == ["#LACY", "#gta6", "#Shorts"]
    src = md.Source(title=None, original_title=None, creator="Lacy", channel=None, platform="twitch",
                    url="https://www.twitch.tv/videos/1", rights_status="licensed", required_hashtags=["#LACY"])
    w = md.plain(md.ClipText(id=1, category="funny", reason=None, said="no way", duration=20), src, None, "x")
    assert "#LACY" in w.description and w.hashtags[0] == "#LACY"
