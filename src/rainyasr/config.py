"""Configuration for RainyASR.

Sensitive values (API keys) are loaded from .env via python-dotenv.
User preferences are stored in project-root config/config.toml.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Literal

import tomli_w
from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv()


def _config_toml_path() -> Path:
    """Return the path to config/config.toml relative to this package."""
    return Path(__file__).parent.parent.parent / "config" / "config.toml"


class AudioConfig(BaseModel):
    """Audio capture and processing settings for real-time streaming."""

    sample_rate: int = Field(default=16000, ge=8000, le=48000)
    channels: int = Field(default=1, ge=1, le=2)
    frame_ms: int = Field(default=100, ge=20, le=500)
    audio_queue_max_frames: int = Field(default=100, ge=10, le=1000)


class ASRConfig(BaseModel):
    """Real-time ASR settings."""

    asr_model: str = Field(default="qwen3-asr-flash-realtime")
    asr_format: str = Field(default="pcm")
    asr_language: str = Field(default="auto")


class SubtitleConfig(BaseModel):
    """Subtitle appearance settings."""

    font_family: str = Field(default="PingFang SC, Microsoft YaHei, sans-serif")
    font_size: int = Field(default=24, ge=8, le=72)
    text_color: str = Field(default="#FFFFFF")
    bg_opacity: int = Field(default=80, ge=0, le=100)
    bilingual_mode: bool = Field(default=True)


class HotkeyConfig(BaseModel):
    """Global hotkey settings."""

    toggle_hotkey: str = Field(default="ctrl+shift+r")


class LanguageConfig(BaseModel):
    """Translation target language."""

    target_lang: Literal["zh", "en", "ja", "ko", "fr", "de", "es", "ru"] = Field(default="zh")


class AppConfig(BaseModel):
    """Root configuration model."""

    audio: AudioConfig = Field(default_factory=AudioConfig)
    asr: ASRConfig = Field(default_factory=ASRConfig)
    subtitle: SubtitleConfig = Field(default_factory=SubtitleConfig)
    hotkey: HotkeyConfig = Field(default_factory=HotkeyConfig)
    language: LanguageConfig = Field(default_factory=LanguageConfig)

    def save(self) -> None:
        """Persist config to project config.toml."""
        path = _config_toml_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            _dump_toml(self.model_dump(mode="json")),
            encoding="utf-8",
        )

    @classmethod
    def load(cls) -> AppConfig:
        """Load config from project config.toml, or return defaults if missing."""
        path = _config_toml_path()
        if not path.exists():
            return cls()
        with path.open("rb") as f:
            data = tomllib.load(f)
        return cls.model_validate(data)


def _dump_toml(data: dict) -> str:
    """Pretty-print TOML with section ordering."""
    # tomli_w only accepts dict, so we manually format for readability
    import io

    buf = io.BytesIO()
    tomli_w.dump(data, buf)
    return buf.getvalue().decode("utf-8")


class EnvConfig:
    """Sensitive configuration loaded from environment / .env file."""

    @staticmethod
    def dashscope_api_key() -> str:
        return os.getenv("DASHSCOPE_API_KEY", "")

    @staticmethod
    def deepseek_api_key() -> str:
        return os.getenv("DEEPSEEK_API_KEY", "")

    @staticmethod
    def dashscope_base_url() -> str:
        return os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/api/v1")

    @staticmethod
    def deepseek_base_url() -> str:
        return os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")

    @staticmethod
    def asr_model() -> str:
        return os.getenv("ASR_MODEL", "qwen3-asr-flash-realtime")

    @staticmethod
    def translate_model() -> str:
        return os.getenv("TRANSLATE_MODEL", "deepseek-chat")

    @staticmethod
    def logfire_token() -> str | None:
        return os.getenv("LOGFIRE_TOKEN")
