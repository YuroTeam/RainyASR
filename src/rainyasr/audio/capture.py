"""Cross-platform audio loopback device detection and capture."""

from __future__ import annotations

import platform
from dataclasses import dataclass

import sounddevice as sd


class NoLoopbackDeviceError(RuntimeError):
    """Raised when no suitable loopback device is found on the system."""


@dataclass(frozen=True)
class AudioDeviceInfo:
    """Information about a detected audio device."""

    device_id: int
    name: str
    sample_rate: int
    channels: int


class AudioDeviceDetector:
    """Detect system audio loopback devices across platforms."""

    def find_loopback_device(self) -> AudioDeviceInfo:
        """Find the best available loopback device for the current platform.

        Returns:
            AudioDeviceInfo with device details.

        Raises:
            NoLoopbackDeviceError: if no suitable device is found.
        """
        system = platform.system()
        if system == "Windows":
            return self._find_windows_loopback()
        if system == "Darwin":
            return self._find_macos_loopback()
        if system == "Linux":
            return self._find_linux_loopback()
        msg = f"Unsupported platform: {system}"
        raise NoLoopbackDeviceError(msg)

    def _find_windows_loopback(self) -> AudioDeviceInfo:
        """Find WASAPI loopback device on Windows.

        PortAudio with WASAPI exposes loopback devices as input devices
        with 'Loopback' in their name.
        """
        devices = sd.query_devices()
        for idx, dev in enumerate(devices):
            if dev["max_input_channels"] == 0:
                continue
            name = dev["name"]
            if "loopback" in name.lower():
                return AudioDeviceInfo(
                    device_id=idx,
                    name=name,
                    sample_rate=int(dev.get("default_samplerate", 48000)),
                    channels=min(dev["max_input_channels"], 2),
                )
        msg = (
            "No WASAPI loopback device found. "
            "Ensure you are using Windows Vista+ and PortAudio is built with WASAPI support."
        )
        raise NoLoopbackDeviceError(msg)

    def _find_macos_loopback(self) -> AudioDeviceInfo:
        """Find BlackHole virtual audio device on macOS."""
        devices = sd.query_devices()
        for idx, dev in enumerate(devices):
            if dev["max_input_channels"] == 0:
                continue
            name = dev["name"]
            if "blackhole" in name.lower():
                return AudioDeviceInfo(
                    device_id=idx,
                    name=name,
                    sample_rate=int(dev.get("default_samplerate", 48000)),
                    channels=min(dev["max_input_channels"], 2),
                )
        msg = (
            "No BlackHole device found. "
            "Please install BlackHole from https://github.com/ExistentialAudio/BlackHole"
        )
        raise NoLoopbackDeviceError(msg)

    def _find_linux_loopback(self) -> AudioDeviceInfo:
        """Find PulseAudio monitor source on Linux.

        Falls back to the default input device if no monitor is found.
        """
        devices = sd.query_devices()
        for idx, dev in enumerate(devices):
            if dev["max_input_channels"] == 0:
                continue
            name = dev["name"]
            if "monitor" in name.lower():
                return AudioDeviceInfo(
                    device_id=idx,
                    name=name,
                    sample_rate=int(dev.get("default_samplerate", 48000)),
                    channels=min(dev["max_input_channels"], 2),
                )
        try:
            default_input = sd.query_devices(kind="input")
            default_idx = sd.default.device[0]
            if default_idx is None:
                msg = "No default input device available"
                raise NoLoopbackDeviceError(msg)
            return AudioDeviceInfo(
                device_id=default_idx,
                name=default_input["name"],
                sample_rate=int(default_input.get("default_samplerate", 48000)),
                channels=min(default_input["max_input_channels"], 2),
            )
        except sd.PortAudioError as exc:
            msg = f"No suitable audio device found: {exc}"
            raise NoLoopbackDeviceError(msg) from exc


def get_default_sample_rate(device_id: int | None = None) -> int:
    """Return the default sample rate for the given device.

    Args:
        device_id: Device index, or None for default input.

    Returns:
        Sample rate in Hz.
    """
    dev = sd.query_devices(kind="input") if device_id is None else sd.query_devices(device_id)
    sr = dev.get("default_samplerate")
    return int(sr) if sr is not None else 48000
