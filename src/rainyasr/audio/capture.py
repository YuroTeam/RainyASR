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

        Uses pyaudiowpatch which correctly enumerates WASAPI loopback devices.
        Falls back to the default WASAPI output device as loopback source.
        """
        import pyaudiowpatch  # pyright: ignore[reportMissingImports]

        try:
            p = pyaudiowpatch.PyAudio()
        except Exception as exc:
            msg = f"Failed to initialize PyAudio: {exc}"
            raise NoLoopbackDeviceError(msg) from exc

        try:
            with p:
                wasapi_info = None
                for i in range(p.get_host_api_count()):
                    api = p.get_host_api_info_by_index(i)
                    if "wasapi" in api["name"].lower():
                        wasapi_info = api
                        break

                if wasapi_info is None:
                    msg = "No WASAPI host API found"
                    raise NoLoopbackDeviceError(msg)

                # Find the default output device name, then match its loopback
                default_out_idx = wasapi_info["defaultOutputDevice"]
                default_out_name = ""
                if default_out_idx is not None and default_out_idx >= 0:
                    default_out_name = p.get_device_info_by_index(default_out_idx)["name"]

                # Look for the loopback device matching the default output
                for idx in range(p.get_device_count()):
                    dev = p.get_device_info_by_index(idx)
                    if dev["hostApi"] != wasapi_info["index"]:
                        continue
                    if dev["maxInputChannels"] == 0:
                        continue
                    if "loopback" not in dev["name"].lower():
                        continue
                    if default_out_name and default_out_name in dev["name"]:
                        return AudioDeviceInfo(
                            device_id=idx,
                            name=dev["name"],
                            sample_rate=int(dev["defaultSampleRate"]),
                            channels=min(dev["maxInputChannels"], 2),
                        )

                # Fallback: any loopback device
                for idx in range(p.get_device_count()):
                    dev = p.get_device_info_by_index(idx)
                    if dev["hostApi"] != wasapi_info["index"]:
                        continue
                    if dev["maxInputChannels"] == 0:
                        continue
                    if "loopback" in dev["name"].lower():
                        return AudioDeviceInfo(
                            device_id=idx,
                            name=dev["name"],
                            sample_rate=int(dev["defaultSampleRate"]),
                            channels=min(dev["maxInputChannels"], 2),
                        )

                # Fallback: open the default WASAPI output as a loopback source
                default_out_idx = wasapi_info["defaultOutputDevice"]
                if default_out_idx is not None and default_out_idx >= 0:
                    dev = p.get_device_info_by_index(default_out_idx)
                    return AudioDeviceInfo(
                        device_id=default_out_idx,
                        name=dev["name"],
                        sample_rate=int(dev["defaultSampleRate"]),
                        channels=min(dev["maxOutputChannels"], 2),
                    )
        except NoLoopbackDeviceError:
            raise
        except Exception as exc:
            msg = f"No WASAPI loopback device found: {exc}"
            raise NoLoopbackDeviceError(msg) from exc

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
        """Find PulseAudio/PipeWire monitor source on Linux.
    Uses pactl to find the monitor source for the default output sink.
    Does not fall back to microphones, because that would silently capture
    the wrong audio source.
        """
        import os
        import subprocess   

        def run_pactl(args: list[str]) -> str | None:
            try:
                result = subprocess.run(
                    ["pactl"] + args,
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=2,
                )
            except (FileNotFoundError,subprocess.CalledProcessError,subprocess.TimeoutExpired):
                return None
            return result.stdout.strip()
        
        def source_exists(source_name: str) -> bool:
            sources = run_pactl(["list", "short","sources"])
            if not sources:
                return False    
            for line in sources.splitlines():
                parts = line.split("\t")
                if len(parts) >= 2 and parts[1] == source_name:
                    return True
            return False
        
        monitor_name: str | None = None
        
        default_sink = run_pactl(["get-default-sink"])
        if default_sink:
            candidate = f"{default_sink}.monitor"
            if source_exists(candidate):
                monitor_name = candidate

        devices = sd.query_devices()

        if monitor_name:
            os.environ["PULSE_SOURCE"] = monitor_name
            for idx, dev in enumerate(devices):
                if dev["max_input_channels"] == 0:
                    continue
                name = str(dev["name"]).lower()

                 # First choice: ALSA pulse plugin, because PULSE_SOURCE is for PulseAudio.
                if name == "pulse":
                    return AudioDeviceInfo(
                        device_id=idx,
                        name=monitor_name,
                        sample_rate=48000,
                        channels=min(dev["max_input_channels"], 2),
                    )
        for idx , dev in enumerate(devices):
            if dev["max_input_channels"] == 0:
                continue
            name = str(dev["name"])
            if "monitor" in name.lower():
                return AudioDeviceInfo(
                    device_id=idx,
                    name=name,
                    sample_rate=int(dev.get("default_samplerate", 48000)),
                    channels=min(dev["max_input_channels"], 2),
                )

        msg = (
        "No Linux loopback monitor source found. "
        "PulseAudio/PipeWire monitor may exist in pactl, but sounddevice did not expose "
        "a usable pulse/pipewire input device. Refusing to fall back to microphone."
    )
        raise NoLoopbackDeviceError(msg)


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
