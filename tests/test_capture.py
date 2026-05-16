"""Tests for audio device detection across platforms.

These tests detect the actual system and only run the platform-specific tests.
If no suitable loopback device is found on the current system, the test skips
rather than fails — this allows developers on different machines to run tests
with their own hardware setup.
"""

from __future__ import annotations

import platform

import pytest

from rainyasr.audio.capture import (
    AudioDeviceDetector,
    AudioDeviceInfo,
    NoLoopbackDeviceError,
    get_default_sample_rate,
)

MACOS = platform.system() == "Darwin"
WINDOWS = platform.system() == "Windows"
LINUX = platform.system() == "Linux"


# ---------------------------------------------------------------------------
# macOS
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not MACOS, reason="Requires macOS")
class TestMacOS:
    """Real device tests for macOS loopback detection."""

    def test_finds_blackhole_device(self) -> None:
        detector = AudioDeviceDetector()
        try:
            info = detector.find_loopback_device()
        except NoLoopbackDeviceError:
            pytest.skip("No BlackHole device found on this system")
        assert "blackhole" in info.name.lower()
        assert info.sample_rate > 0
        assert info.channels > 0


# ---------------------------------------------------------------------------
# Windows
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not WINDOWS, reason="Requires Windows")
class TestWindows:
    """Real device tests for Windows loopback detection."""

    def test_finds_wasapi_loopback(self) -> None:
        detector = AudioDeviceDetector()
        try:
            info = detector.find_loopback_device()
        except NoLoopbackDeviceError:
            pytest.skip("No WASAPI loopback device found on this system")
        assert "loopback" in info.name.lower()
        assert info.sample_rate > 0
        assert info.channels > 0


# ---------------------------------------------------------------------------
# Linux
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not LINUX, reason="Requires Linux")
class TestLinux:
    """Real device tests for Linux monitor detection."""

    def test_finds_monitor_or_fallback(self) -> None:
        detector = AudioDeviceDetector()
        try:
            info = detector.find_loopback_device()
        except NoLoopbackDeviceError:
            pytest.skip("No monitor or input device found on this system")
        assert info.sample_rate > 0
        assert info.channels > 0


# ---------------------------------------------------------------------------
# Cross-platform (runs everywhere)
# ---------------------------------------------------------------------------


class TestAudioDeviceInfo:
    """Pure dataclass tests — no hardware required."""

    def test_frozen_dataclass(self) -> None:
        info = AudioDeviceInfo(0, "Test", 48000, 2)
        with pytest.raises(AttributeError):
            info.name = "Changed"  # type: ignore[reportAttributeAccessIssue]

    def test_equality(self) -> None:
        a = AudioDeviceInfo(0, "Test", 48000, 2)
        b = AudioDeviceInfo(0, "Test", 48000, 2)
        c = AudioDeviceInfo(1, "Test", 48000, 2)
        assert a == b
        assert a != c


class TestGetDefaultSampleRate:
    """Query real system device — skips if no input device available."""

    def test_query_returns_positive_int(self) -> None:
        try:
            sr = get_default_sample_rate()
        except Exception as exc:
            pytest.skip(f"No default input device available: {exc}")
        assert isinstance(sr, int)
        assert sr > 0
