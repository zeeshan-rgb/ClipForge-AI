"""FFmpeg / ffprobe helpers."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .config import Settings

# Never flash a console window when the app runs without a terminal.
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".flv", ".ts", ".wmv"}


class MediaError(RuntimeError):
    pass


def run(cmd: list[str], *, timeout: float = 120, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=str(cwd) if cwd else None,
            creationflags=_NO_WINDOW,
        )
    except subprocess.TimeoutExpired as exc:
        raise MediaError(f"{Path(cmd[0]).stem} timed out after {timeout:.0f}s") from exc
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-3:]
        raise MediaError(f"{Path(cmd[0]).stem} failed: {' | '.join(tail) or 'no output'}")
    return proc


def tool_version(exe: str | None) -> str | None:
    if not exe:
        return None
    try:
        first = run([exe, "-version"], timeout=15).stdout.splitlines()[0]
    except (MediaError, OSError, IndexError):
        return None
    parts = first.split()
    return parts[2] if len(parts) > 2 else first


def _ratio(value: str | None) -> float | None:
    if not value or value in ("0/0", "N/A"):
        return None
    try:
        if "/" in value:
            num, den = value.split("/", 1)
            return round(float(num) / float(den), 3) if float(den) else None
        return round(float(value), 3)
    except ValueError:
        return None


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def probe_media(settings: Settings, path: Path) -> dict[str, Any]:
    """Read container and stream facts from a media file."""
    if not settings.ffprobe:
        raise MediaError("ffprobe is not installed (expected in tools\\ffmpeg)")
    if not path.is_file():
        raise MediaError(f"File not found: {path}")
    out = run(
        [settings.ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        timeout=120,
    ).stdout
    data = json.loads(out or "{}")
    streams = data.get("streams", [])
    fmt = data.get("format", {})
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and s.get("disposition", {}).get("attached_pic") != 1), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video is None:
        raise MediaError("This file has no video stream")

    duration = _float(fmt.get("duration")) or _float(video.get("duration"))
    return {
        "duration": round(duration, 3) if duration else None,
        "width": video.get("width"),
        "height": video.get("height"),
        "fps": _ratio(video.get("avg_frame_rate")) or _ratio(video.get("r_frame_rate")),
        "video_codec": video.get("codec_name"),
        "audio_codec": audio.get("codec_name") if audio else None,
        "audio_channels": audio.get("channels") if audio else None,
        "has_audio": audio is not None,
        "format": fmt.get("format_long_name") or fmt.get("format_name"),
        "size_bytes": int(fmt["size"]) if str(fmt.get("size", "")).isdigit() else path.stat().st_size,
    }


def run_with_progress(
    cmd: list[str],
    *,
    duration: float | None,
    on_progress: Callable[[float], None],
) -> None:
    """Run FFmpeg and report how far through the input it is.

    `cmd` must contain `-progress pipe:1 -nostats`. `on_progress` may raise (job
    cancelled, app closing); FFmpeg is then stopped and the exception passes on.
    """
    with tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=err, text=True,
            encoding="utf-8", errors="replace", creationflags=_NO_WINDOW,
        )
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                if line.startswith("out_time_us="):
                    value = line.split("=", 1)[1].strip()
                    if value.lstrip("-").isdigit() and duration:
                        on_progress(max(0.0, min(1.0, int(value) / 1e6 / duration)))
                    else:
                        on_progress(0.0)
            proc.wait()
        except BaseException:
            proc.kill()
            proc.wait()
            raise
        if proc.returncode != 0:
            err.seek(0)
            tail = err.read().decode("utf-8", "replace").strip().splitlines()[-3:]
            raise MediaError(f"ffmpeg failed: {' | '.join(tail) or 'no output'}")


def _finish_part(part: Path, dest: Path) -> Path:
    if not part.is_file() or part.stat().st_size == 0:
        raise MediaError(f"FFmpeg produced no output for {dest.name}")
    os.replace(part, dest)  # only a complete file ever has the final name
    return dest


def make_proxy(
    settings: Settings, src: Path, dest: Path, info: dict[str, Any], on_progress: Callable[[float], None]
) -> Path:
    """Small 360p H.264 copy: quick to scrub in the dashboard and cheap to send to AI."""
    if not settings.ffmpeg:
        raise MediaError("ffmpeg is not installed (expected in tools\\ffmpeg)")
    dest.parent.mkdir(parents=True, exist_ok=True)
    fps = min(30.0, float(info.get("fps") or 30.0))
    part = dest.with_name(dest.stem + ".part" + dest.suffix)
    cmd = [
        settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
        "-map", "0:v:0", "-map", "0:a:0?",
        "-vf", f"scale=-2:'min(360,ih)',fps={fps:g}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
        "-g", str(max(1, round(fps * 2))), "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "96k", "-ac", "2",
        "-movflags", "+faststart",
        "-progress", "pipe:1", "-nostats", str(part),
    ]
    run_with_progress(cmd, duration=info.get("duration"), on_progress=on_progress)
    return _finish_part(part, dest)


def extract_audio(
    settings: Settings, src: Path, dest: Path, info: dict[str, Any], on_progress: Callable[[float], None]
) -> Path:
    """16 kHz mono WAV: what speech recognition and audio analysis expect."""
    if not settings.ffmpeg:
        raise MediaError("ffmpeg is not installed (expected in tools\\ffmpeg)")
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.stem + ".part" + dest.suffix)
    cmd = [
        settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
        "-vn", "-map", "0:a:0", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
        "-progress", "pipe:1", "-nostats", str(part),
    ]
    run_with_progress(cmd, duration=info.get("duration"), on_progress=on_progress)
    return _finish_part(part, dest)


def render_ranges(
    settings: Settings, src: Path, dest: Path, ranges: list[tuple[float, float]], has_audio: bool = True
) -> Path:
    """Cut the given time ranges out of `src`, in order, into one small MP4.

    Used for review previews. Frames are re-encoded so the cut lands exactly on
    the requested times, not on the nearest keyframe.
    """
    if not settings.ffmpeg:
        raise MediaError("ffmpeg is not installed (expected in tools\\ffmpeg)")
    ranges = [(float(a), float(b)) for a, b in ranges if b - a > 0.02]
    if not ranges:
        raise MediaError("Nothing to render: the clip has no length")
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.stem + ".part" + dest.suffix)

    chains, labels = [], ""
    for n, (a, b) in enumerate(ranges):
        chains.append(f"[0:v]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS[v{n}]")
        labels += f"[v{n}]"
        if has_audio:
            chains.append(f"[0:a]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS[a{n}]")
            labels += f"[a{n}]"
    chains.append(f"{labels}concat=n={len(ranges)}:v=1:a={1 if has_audio else 0}[v]" + ("[a]" if has_audio else ""))
    cmd = [
        settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
        "-filter_complex", ";".join(chains), "-map", "[v]",
    ]
    if has_audio:
        cmd += ["-map", "[a]", "-c:a", "aac", "-b:a", "96k"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", str(part)]
    run(cmd, timeout=600)
    return _finish_part(part, dest)


def _filter_path(path: Path) -> str:
    """A path written so FFmpeg's filter parser reads it whole (drive colons escaped)."""
    return "'" + path.resolve().as_posix().replace(":", "\\:").replace("'", "\\'") + "'"


@dataclass(frozen=True)
class MixSpec:
    """Background music to lay under a clip: which file, where in it to start, how loud, and when to duck."""

    path: Path
    offset: float = 0.0  # seconds into the track where this clip's music starts
    loop: bool = False  # the track is shorter than the clip: repeat it
    gain_db: float = 0.0  # applied to the track as it is (open level, before ducking)
    fade_in: float = 0.4
    fade_out: float = 1.0
    envelope: Path | None = None  # stereo WAV of gain 0..1 over time: lowers the music under speech
    channels: int = 2  # channels in the track file


@dataclass(frozen=True)
class VoiceSpec:
    """The owner's narration for a clip, already placed on the clip's clock.

    `stem` is the narration (48 kHz stereo, the clip's length) and `duck` a
    stereo gain envelope (0..1) that lowers the clip's own sound while the owner
    speaks. Both are made by `story.py`.
    """

    stem: Path
    duck: Path


def _voice_inputs(voice: VoiceSpec | None) -> list[str]:
    return [] if voice is None else ["-i", str(voice.stem), "-i", str(voice.duck)]


def _stereo(channels: int) -> str:
    """Bring a sound to 48 kHz stereo without changing how loud it is.

    FFmpeg's own mono-to-stereo conversion lowers each channel by 3 dB. For a
    mono sound this puts the same signal in both channels unchanged, so levels
    measured on the mono sound stay true in the mix.
    """
    if channels == 1:
        return "aresample=48000,pan=stereo|c0=c0|c1=c0,aformat=sample_fmts=fltp"
    return "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"


_STEREO = _stereo(2)  # for streams already known to be stereo (the ducking envelope)


def _music_inputs(music: MixSpec) -> list[str]:
    args = ["-ss", f"{music.offset:.3f}"]
    if music.loop:
        args += ["-stream_loop", "-1"]
    args += ["-i", str(music.path)]
    if music.envelope is not None:
        args += ["-i", str(music.envelope)]
    return args


def _music_chain(music: MixSpec, total: float, music_index: int, out: str) -> list[str]:
    """Filters that turn the track into the music bus: level, fades, then the speech ducking."""
    fade_out = min(music.fade_out, total / 2)
    fade_in = min(music.fade_in, total / 2)
    chain = [
        f"[{music_index}:a]{_stereo(music.channels)},atrim=duration={total:.3f},asetpts=PTS-STARTPTS,"
        f"volume={music.gain_db:.2f}dB,afade=t=in:st=0:d={fade_in:.2f},"
        f"afade=t=out:st={max(total - fade_out, 0):.3f}:d={fade_out:.2f}[mus]"
    ]
    if music.envelope is not None:
        chain.append(f"[{music_index + 1}:a]{_STEREO},atrim=duration={total:.3f}[env]")
        chain.append(f"[mus][env]amultiply[{out}]")
    else:
        chain.append(f"[mus]anull[{out}]")
    return chain


def render_music_stem(settings: Settings, music: MixSpec, total: float, dest: Path) -> Path:
    """Just the music as it will sound in the clip (level, fades, ducking), for measuring."""
    if not settings.ffmpeg:
        raise MediaError("ffmpeg is not installed (expected in tools\\ffmpeg)")
    dest.parent.mkdir(parents=True, exist_ok=True)
    run(
        [settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *_music_inputs(music),
         "-filter_complex", ";".join(_music_chain(music, total, 0, "md")),
         "-map", "[md]", "-t", f"{total:.3f}", "-c:a", "pcm_f32le", "-ar", "48000", str(dest)],
        timeout=300,
    )
    return dest


def _audio_graph(
    ranges: list[tuple[float, float]], has_audio: bool, music: MixSpec | None, audio_channels: int,
    src_index: int, music_index: int, total: float, gain_db: float | None = None, peak_limit: float | None = None,
    voice: VoiceSpec | None = None, voice_index: int | None = None,
) -> tuple[list[str], str | None]:
    """The clip's sound (its ranges joined), with the music mixed under it. Returns filters and the output label.

    The clip's own sound is not reshaped: the music is added to it. The one
    exception is the owner's narration (Story mode): while the owner speaks,
    the clip's sound is lowered by the `voice.duck` envelope, and raised back
    after. `gain_db` is the final render's single loudness gain on the finished
    mix, and `peak_limit` the limiter ceiling that follows it.
    """
    chains: list[str] = []
    if has_audio:
        labels = ""
        for n, (a, b) in enumerate(ranges):
            chains.append(f"[{src_index}:a]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS[s{n}]")
            labels += f"[s{n}]"
        chains.append(f"{labels}concat=n={len(ranges)}:v=0:a=1[a0]")
    buses: list[str] = []
    stereo = music is not None or voice is not None
    if has_audio:
        if voice is not None and voice_index is not None:
            chains.append(f"[a0]{_stereo(audio_channels)}[a0s]")
            chains.append(f"[{voice_index + 1}:a]{_STEREO},atrim=duration={total:.3f},asetpts=PTS-STARTPTS[dk]")
            chains.append("[a0s][dk]amultiply[ao]")
            buses.append("ao")
        elif stereo:
            chains.append(f"[a0]{_stereo(audio_channels)}[ao]")
            buses.append("ao")
        else:
            buses.append("a0")
    if music is not None:
        chains += _music_chain(music, total, music_index, "md")
        buses.append("md")
    if voice is not None and voice_index is not None:
        chains.append(f"[{voice_index}:a]{_STEREO},atrim=duration={total:.3f},asetpts=PTS-STARTPTS[vo]")
        buses.append("vo")
    out = None
    if len(buses) > 1:
        # normalize=0: every part keeps exactly the level it was given
        chains.append("".join(f"[{b}]" for b in buses)
                      + f"amix=inputs={len(buses)}:duration=first:dropout_transition=0:normalize=0[mx]")
        chains.append("[mx]alimiter=limit=0.95:level=0[mix]")
        out = "mix"
    elif buses:
        out = buses[0] if (has_audio or not stereo) else None
        if out is None or out in ("md", "vo"):
            chains.append(f"[{buses[0]}]alimiter=limit=0.95:level=0[mix]")
            out = "mix"
    if out is not None and (gain_db is not None or peak_limit is not None):
        level = f"volume={gain_db or 0.0:.2f}dB"
        if peak_limit is not None:
            # limited at four times the sample rate, so peaks that fall between samples are caught too
            level += (f",aresample=192000,alimiter=limit={peak_limit:.4f}:level=0:attack=5:release=60:latency=1,"
                      "aresample=48000")
        chains.append(f"[{out}]{_stereo(2 if stereo else audio_channels)},{level}[lvl]")
        out = "lvl"
    return chains, (f"[{out}]" if out else None)


def read_frames(
    settings: Settings, video: Path, start: float, end: float, fps: float, size: tuple[int, int]
):
    """Frames of `video` from start to end at a fixed rate and size, as BGR numpy arrays (FFmpeg decodes)."""
    import numpy as np

    if not settings.ffmpeg:
        raise MediaError("ffmpeg is not installed (expected in tools\\ffmpeg)")
    w, h = size
    cmd = [settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", f"{start:.3f}", "-i", str(video),
           "-t", f"{max(end - start, 0.01):.3f}", "-an", "-sn",
           "-vf", f"fps={fps:.4f},scale={w}:{h}:flags=area,setsar=1",
           "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=_NO_WINDOW)
    frame_bytes = w * h * 3
    try:
        assert proc.stdout is not None
        while True:
            raw = proc.stdout.read(frame_bytes)
            if len(raw) < frame_bytes:
                break
            yield np.frombuffer(raw, dtype=np.uint8).reshape(h, w, 3)
    finally:
        proc.kill()
        proc.wait()


_QSV_OK: dict[str, bool] = {}


def hardware_encoder(settings: Settings) -> bool:
    """Whether Intel Quick Sync H.264 encoding works on this computer (checked once)."""
    exe = settings.ffmpeg or ""
    if exe not in _QSV_OK:
        try:
            run([exe, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=s=320x240:r=30:d=0.2",
                 "-c:v", "h264_qsv", "-f", "null", "-"], timeout=30)
            _QSV_OK[exe] = True
        except (MediaError, OSError):
            _QSV_OK[exe] = False
    return _QSV_OK[exe]


def _video_codec(settings: Settings, final: bool, fps: float) -> list[str]:
    gop = str(max(1, round(fps * 2)))
    if not final:
        return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", "-g", gop]
    if hardware_encoder(settings):
        return ["-c:v", "h264_qsv", "-preset", "slow", "-b:v", "10M", "-maxrate", "14M", "-bufsize", "20M",
                "-profile:v", "high", "-pix_fmt", "nv12", "-g", gop]
    return ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-profile:v", "high", "-pix_fmt", "yuv420p",
            "-g", gop]


def render_framed(
    settings: Settings,
    src: Path,
    dest: Path,
    ranges: list[tuple[float, float]],
    draw: Callable[[Any, float], Any],
    decode_size: tuple[int, int],
    ass: Path | None,
    *,
    width: int = 540,
    fps: float = 30.0,
    has_audio: bool = True,
    music: MixSpec | None = None,
    audio_channels: int = 2,
    final: bool = False,
    gain_db: float | None = None,
    peak_limit: float | None = None,
    check: Callable[[], None] | None = None,
    on_progress: Callable[[float], None] | None = None,
    voice: VoiceSpec | None = None,
) -> Path:
    """A 9:16 render where every frame is built in Python by `draw(frame, clip_time)` and piped to FFmpeg.

    The source is decoded at `decode_size`, frame by frame across the ranges;
    `draw` returns the composed frame at the output size. Captions are burned on
    top, and the sound is the clip's own with the music under it. `final` uses
    the delivery encoder (Quick Sync when present) at a high bitrate and 48 kHz
    AAC at 192 kb/s.
    """
    if not settings.ffmpeg:
        raise MediaError("ffmpeg is not installed (expected in tools\\ffmpeg)")
    ranges = [(float(a), float(b)) for a, b in ranges if b - a > 0.02]
    if not ranges:
        raise MediaError("Nothing to render: the clip has no length")
    height = width * 16 // 9
    total = sum(b - a for a, b in ranges)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.stem + ".part" + dest.suffix)

    inputs = ["-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{width}x{height}", "-r", f"{fps:.4f}", "-i", "-"]
    chains: list[str] = []
    look = "setsar=1"
    if ass is not None:
        look += f",ass={_filter_path(ass)}:fontsdir={_filter_path(settings.fonts_dir)}"
    chains.append(f"[0:v]{look}[v]")
    if has_audio:
        inputs += ["-i", str(src)]
    music_index = 2 if has_audio else 1
    if music is not None:
        inputs += _music_inputs(music)
    voice_index = music_index + (0 if music is None else (2 if music.envelope is not None else 1))
    inputs += _voice_inputs(voice)
    audio_chains, audio_out = _audio_graph(ranges, has_audio, music, audio_channels, 1, music_index,
                                           total, gain_db, peak_limit, voice, voice_index)
    chains += audio_chains
    cmd = [settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *inputs,
           "-filter_complex", ";".join(chains), "-map", "[v]"]
    if audio_out:
        cmd += ["-map", audio_out, "-c:a", "aac", "-b:a", "192k" if final else "128k", "-ar", "48000"]
    cmd += ["-t", f"{total:.3f}", *_video_codec(settings, final, fps), "-r", f"{fps:.4f}",
            "-movflags", "+faststart", str(part)]

    with tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=err,
                                creationflags=_NO_WINDOW)
        expected = max(1, round(total * fps))
        written = 0
        last = None
        try:
            assert proc.stdin is not None
            offset = 0.0
            for a, b in ranges:
                count = max(1, round((b - a) * fps))
                k = 0
                for frame in read_frames(settings, src, a, b, fps, decode_size):
                    if k >= count:
                        break
                    last = draw(frame, offset + k / fps)
                    proc.stdin.write(last.tobytes())
                    k += 1
                    written += 1
                    if written % 15 == 0:
                        if check:
                            check()
                        if on_progress:
                            on_progress(min(1.0, written / expected))
                while k < count and last is not None:  # the source ran out early: hold the last frame
                    proc.stdin.write(last.tobytes())
                    k += 1
                    written += 1
                offset += b - a
            proc.stdin.close()
            proc.wait()
        except BrokenPipeError:
            proc.wait()
        except BaseException:
            proc.kill()
            proc.wait()
            raise
        if proc.returncode != 0:
            err.seek(0)
            tail = err.read().decode("utf-8", "replace").strip().splitlines()[-3:]
            raise MediaError(f"ffmpeg failed: {' | '.join(tail) or 'no output'}")
    return _finish_part(part, dest)


def replace_audio(
    settings: Settings, video_file: Path, src: Path, ranges: list[tuple[float, float]], *, has_audio: bool = True,
    music: MixSpec | None = None, audio_channels: int = 2, gain_db: float | None = None,
    peak_limit: float | None = None, voice: VoiceSpec | None = None,
) -> Path:
    """Mix and encode a rendered file's sound again (new gain or ceiling); the picture is copied untouched."""
    if not settings.ffmpeg:
        raise MediaError("ffmpeg is not installed (expected in tools\\ffmpeg)")
    ranges = [(float(a), float(b)) for a, b in ranges if b - a > 0.02]
    total = sum(b - a for a, b in ranges)
    inputs = ["-i", str(video_file), "-i", str(src)] + (_music_inputs(music) if music is not None else [])
    voice_index = 2 + (0 if music is None else (2 if music.envelope is not None else 1))
    inputs += _voice_inputs(voice)
    chains, out = _audio_graph(ranges, has_audio, music, audio_channels, 1, 2, total, gain_db, peak_limit,
                               voice, voice_index)
    if out is None:
        return video_file
    part = video_file.with_name(video_file.stem + ".part" + video_file.suffix)
    run([settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *inputs, "-filter_complex", ";".join(chains),
         "-map", "0:v:0", "-map", out, "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
         "-t", f"{total:.3f}", "-movflags", "+faststart", str(part)], timeout=900)
    return _finish_part(part, video_file)


def measure_loudness(
    settings: Settings, src: Path, ranges: list[tuple[float, float]], *, has_audio: bool = True,
    music: MixSpec | None = None, audio_channels: int = 2, whole_file: bool = False,
    voice: VoiceSpec | None = None,
) -> dict[str, float] | None:
    """Integrated loudness (LUFS) and true peak (dBTP) of the clip's finished mix, or of a whole file."""
    if not settings.ffmpeg:
        raise MediaError("ffmpeg is not installed (expected in tools\\ffmpeg)")
    if whole_file:
        cmd = [settings.ffmpeg, "-hide_banner", "-nostats", "-i", str(src), "-vn",
               "-af", "loudnorm=print_format=json", "-f", "null", "-"]
    else:
        ranges = [(float(a), float(b)) for a, b in ranges if b - a > 0.02]
        total = sum(b - a for a, b in ranges)
        inputs = ["-i", str(src)] + (_music_inputs(music) if music is not None else [])
        voice_index = 1 + (0 if music is None else (2 if music.envelope is not None else 1))
        inputs += _voice_inputs(voice)
        chains, out = _audio_graph(ranges, has_audio, music, audio_channels, 0, 1, total, voice=voice,
                                   voice_index=voice_index)
        if out is None:
            return None
        # measured as it will be heard: stereo, the way the render delivers it (a mono sound
        # copied to both channels reads 3 dB louder than the mono sound itself)
        stereo = music is not None or voice is not None
        chains.append(f"{out}{_stereo(2 if stereo else audio_channels)},loudnorm=print_format=json[m]")
        cmd = [settings.ffmpeg, "-hide_banner", "-nostats", *inputs, "-filter_complex", ";".join(chains),
               "-map", "[m]", "-t", f"{total:.3f}", "-f", "null", "-"]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900,
                          creationflags=_NO_WINDOW)
    text = proc.stderr or ""
    start = text.rfind("{")
    end = text.rfind("}")
    if proc.returncode != 0 or start < 0 or end < start:
        tail = text.strip().splitlines()[-3:]
        raise MediaError(f"Loudness could not be measured: {' | '.join(tail) or 'no output'}")
    data = json.loads(text[start:end + 1])
    i, tp = _float(data.get("input_i")), _float(data.get("input_tp"))
    if i is None or i < -70:  # silence
        return {"lufs": None, "true_peak": tp}
    return {"lufs": round(i, 2), "true_peak": None if tp is None else round(tp, 2)}


def render_vertical(
    settings: Settings,
    src: Path,
    dest: Path,
    ranges: list[tuple[float, float]],
    ass: Path | None,
    has_audio: bool = True,
    width: int = 540,
    music: MixSpec | None = None,
    audio_channels: int = 2,
) -> Path:
    """A 9:16 preview of the given ranges with captions burned in, and music under the speech if given.

    Framing is a plain centre crop for now; following faces and the action is
    Phase 8. The captions are laid out for 1080x1920 and scaled with the frame.
    The clip's own sound is never processed: the music is added to it.
    """
    if not settings.ffmpeg:
        raise MediaError("ffmpeg is not installed (expected in tools\\ffmpeg)")
    ranges = [(float(a), float(b)) for a, b in ranges if b - a > 0.02]
    if not ranges:
        raise MediaError("Nothing to render: the clip has no length")
    height = width * 16 // 9
    total = sum(b - a for a, b in ranges)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.stem + ".part" + dest.suffix)

    chains, labels = [], ""
    for n, (a, b) in enumerate(ranges):
        chains.append(f"[0:v]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS[v{n}]")
        labels += f"[v{n}]"
    chains.append(f"{labels}concat=n={len(ranges)}:v=1:a=0[joined]")
    look = (
        "crop=w='min(iw,ih*9/16)':h='min(ih,iw*16/9)',"
        f"scale={width}:{height}:flags=lanczos,setsar=1"
    )
    if ass is not None:
        look += f",ass={_filter_path(ass)}:fontsdir={_filter_path(settings.fonts_dir)}"
    chains.append(f"[joined]{look}[v]")

    inputs = ["-i", str(src)]
    if music is not None:
        inputs += _music_inputs(music)
    audio_chains, audio_out = _audio_graph(ranges, has_audio, music, audio_channels, 0, 1, total)
    chains += audio_chains

    cmd = [settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *inputs,
           "-filter_complex", ";".join(chains), "-map", "[v]"]
    if audio_out:
        cmd += ["-map", audio_out, "-c:a", "aac", "-b:a", "128k"]
        if music is not None:
            cmd += ["-ar", "48000"]
    cmd += ["-t", f"{total:.3f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", str(part)]
    run(cmd, timeout=900)
    return _finish_part(part, dest)


def grab_frame(settings: Settings, video: Path, at: float, dest: Path) -> Path:
    """One frame at an exact time (decoded, not the nearest keyframe)."""
    if not settings.ffmpeg:
        raise MediaError("ffmpeg is not installed (expected in tools\\ffmpeg)")
    dest.parent.mkdir(parents=True, exist_ok=True)
    run([settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(video),
         "-ss", f"{max(0.0, at):.3f}", "-frames:v", "1", "-q:v", "3", str(dest)], timeout=120)
    if not dest.is_file():
        raise MediaError("Could not read a frame")
    return dest


def make_thumbnail(settings: Settings, src: Path, dest: Path, at_seconds: float) -> Path:
    if not settings.ffmpeg:
        raise MediaError("ffmpeg is not installed (expected in tools\\ffmpeg)")
    dest.parent.mkdir(parents=True, exist_ok=True)
    run(
        [settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
         "-ss", f"{max(0.0, at_seconds):.2f}", "-i", str(src),
         "-frames:v", "1", "-vf", "scale=640:-2", "-q:v", "4", str(dest)],
        timeout=120,
    )
    if not dest.is_file() or dest.stat().st_size == 0:
        raise MediaError("Could not read a frame from the video")
    return dest
