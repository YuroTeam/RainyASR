"""Tests for configuration persistence."""

from __future__ import annotations

import os
import stat

import pytest

from rainyasr import config as config_module
from rainyasr.config import AppConfig, EnvConfig, HotkeyConfig, save_env_api_keys


def test_app_config_save_round_trips(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "config.toml"
    monkeypatch.setattr(config_module, "_config_toml_path", lambda: path)

    config = AppConfig()
    config.audio.sample_rate = 48000
    config.subtitle.font_size = 36

    config.save()

    loaded = AppConfig.load()
    assert loaded.audio.sample_rate == 48000
    assert loaded.subtitle.font_size == 36


def test_app_config_save_sets_private_file_permissions(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "config.toml"
    monkeypatch.setattr(config_module, "_config_toml_path", lambda: path)

    AppConfig().save()

    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_save_env_api_keys_persists_dotenv_and_updates_environment(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / ".env"
    monkeypatch.setattr(config_module, "_env_file_path", lambda: path)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    monkeypatch.setattr(config_module, "_dotenv_loaded", False)

    save_env_api_keys(
        dashscope_api_key="dash-key",
        deepseek_api_key="deep-key",
    )

    assert EnvConfig.dashscope_api_key() == "dash-key"
    assert EnvConfig.deepseek_api_key() == "deep-key"
    saved_text = path.read_text(encoding="utf-8")
    assert "DASHSCOPE_API_KEY='dash-key'" in saved_text
    assert "DEEPSEEK_API_KEY='deep-key'" in saved_text
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_hotkey_config_normalizes_key_sequence() -> None:
    config = HotkeyConfig(toggle_hotkey=" Ctrl + Alt + R ")

    assert config.toggle_hotkey == "ctrl+alt+r"


def test_hotkey_config_rejects_invalid_key_sequence() -> None:
    with pytest.raises(ValueError):
        HotkeyConfig(toggle_hotkey="ctrl shift r")

    with pytest.raises(ValueError):
        HotkeyConfig(toggle_hotkey="ctrl++r")
