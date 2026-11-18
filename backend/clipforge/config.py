"""Application settings. Values come from CLIPFORGE_* environment variables or .env."""

from __future__ import annotations

import shutil
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CLIPFORGE_",
        env_file=str(REPO_ROOT / ".env"),
        extra="ignore",
    )

    data_dir: Path = REPO_ROOT / "data"
    host: str = "127.0.0.1"
    port: int = 8765
    # AI for moment detection. "agy" = the signed-in Antigravity command-line
    # tool (uses the owner's subscription); "api" = a Gemini API key.
    gemini_route: str = "agy"
    agy_path: str = r"D:\Helios\tools\agy\bin\agy.exe"
    # Tried in order; the first that answers is kept for the rest of the run.
    # Tested 2026-11-15: claude-sonnet-4-6 answered reliably through this
    # sign-in; the Gemini tiers returned empty replies or timed out.
    agy_models: str = "claude-sonnet-4-6,gemini-3.8-flash-medium"
    ai_timeout_seconds: int = 300
    gemini_model: str = ""  # required when gemini_route is "api"
    ffmpeg_dir: Path = REPO_ROOT / "tools" / "ffmpeg"
    frontend_dist: Path = REPO_ROOT / "frontend" / "dist"

    # Speech-to-text. "small" is about 10x faster than real time on this CPU;
    # "base" is about 25x and a little less accurate with slang and names.
    whisper_model: str = "small"
    language: str = "auto"  # or a code such as "en"
    # How eagerly stretches are treated as speech before transcribing them.
    # Measured on a film with dialogue under music (share of caption words found):
    #   "normal"    69%, fastest - misses speech under loud music or effects
    #   "sensitive" 77%, about 1.5x the time (default)
    #   "off"       85%, about 6x the time - transcribes everything, music included
    speech_detection: str = "sensitive"
    transcript_chunk_seconds: int = 600

    # Permission requests: how often the background sender looks for work (0 = off).
    outreach_interval_seconds: int = 30

    fonts_dir: Path = REPO_ROOT / "tools" / "fonts"
    export_dir: Path | None = None  # finished Shorts are exported here (default: data\exports)
    # OpenCV's YuNet face model (MIT licence), for framing the shot around faces.
    face_model: Path = REPO_ROOT / "tools" / "models" / "face_detection_yunet_2023mar.onnx"

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "cache" / "models"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "clipforge.db"

    @property
    def exports_dir(self) -> Path:
        """Where exported Shorts go: CLIPFORGE_EXPORT_DIR, or `exports` in the data folder."""
        return self.export_dir or self.data_dir / "exports"

    @property
    def youtube_dir(self) -> Path:
        """The owner's OAuth client file and channel token (never in git)."""
        return self.data_dir / "youtube"

    @property
    def music_dir(self) -> Path:
        return self.data_dir / "music"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def projects_dir(self) -> Path:
        return self.data_dir / "projects"

    @property
    def tmp_dir(self) -> Path:
        return self.data_dir / "tmp"

    def project_dir(self, project_id: int) -> Path:
        return self.projects_dir / str(project_id)

    def _tool(self, name: str) -> str | None:
        for bundled in (self.ffmpeg_dir / f"{name}.exe", self.ffmpeg_dir / name):
            if bundled.is_file():
                return str(bundled)
        return shutil.which(name)

    @property
    def ffmpeg(self) -> str | None:
        return self._tool("ffmpeg")

    @property
    def ffprobe(self) -> str | None:
        return self._tool("ffprobe")

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.logs_dir, self.projects_dir, self.tmp_dir, self.music_dir):
            d.mkdir(parents=True, exist_ok=True)
