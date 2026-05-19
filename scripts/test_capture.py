"""Task 6: Validate real-time audio capture from system loopback.

Detects the loopback device, records for 10 seconds, and verifies:
1. Real-time path: PCM16 frames are produced continuously with volume stats.
2. Debug path: Audio is saved to audio_test.wav for playback verification.
"""

from __future__ import annotations

import queue
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import sounddevice as sd

from rainyasr.audio.capture import AudioDeviceDetector, NoLoopbackDeviceError
from rainyasr.audio.ring_buffer import AudioRingBuffer
from rainyasr.audio.wav import DEFAULT_GAIN, encode_wav, float32_to_pcm16, peak_normalize

RECORD_DURATION = 10.0
REPORT_INTERVAL = 0.5
OUTPUT_WAV = Path("audio_test.wav")


@dataclass(frozen=True)
class CaptureStats:
    frame_count: int
    frames: int
    elapsed: float
    raw_peak: float
    raw_rms: float
    gain_peak: float
    gain_rms: float
    pcm_bytes: int


def put_latest(stats_queue: queue.Queue[CaptureStats], stats: CaptureStats) -> None:
    """Publish the newest stats without blocking the audio callback."""
    try:
        stats_queue.put_nowait(stats)
    except queue.Full:
        with suppress(queue.Empty):
            stats_queue.get_nowait()
        with suppress(queue.Full):
            stats_queue.put_nowait(stats)


def print_stats(stats: CaptureStats) -> None:
    print(
        f"  frame={stats.frame_count:4d}  block={stats.frames:4d}  "
        f"elapsed={stats.elapsed:5.1f}s  "
        f"raw_peak={stats.raw_peak:.4f}  raw_rms={stats.raw_rms:.4f}  "
        f"gain_peak={stats.gain_peak:.4f}  gain_rms={stats.gain_rms:.4f}  "
        f"pcm_bytes={stats.pcm_bytes:5d}",
        flush=True,
    )


def main() -> None:
    print("=" * 50)
    print("Task 6: Real-time Audio Capture Validation")
    print("=" * 50)

    detector = AudioDeviceDetector()
    try:
        device = detector.find_loopback_device()
    except NoLoopbackDeviceError as exc:
        print(f"[ERROR] Device detection failed: {exc}")
        raise SystemExit(1) from exc

    print(
        f"Detected device: {device.name} (id={device.device_id}, sr={device.sample_rate}, ch={device.channels})"
    )

    target_sr = device.sample_rate
    ring = AudioRingBuffer(sample_rate=target_sr, max_duration=RECORD_DURATION + 2.0)

    frame_count = 0
    start_time = None
    stats_queue: queue.Queue[CaptureStats] = queue.Queue(maxsize=1)

    def callback(indata: np.ndarray, frames: int, _timestamp: object, _status: object) -> None:
        nonlocal frame_count, start_time
        if start_time is None:
            start_time = time.monotonic()

        # Convert to mono if stereo
        if indata.shape[1] > 1:
            samples = np.mean(indata, axis=1).astype(np.float32)
        else:
            samples = indata[:, 0].astype(np.float32)

        # Write to ring buffer (debug path)
        ring.write(samples)

        # Real-time path: apply gain + peak limiter before PCM16 conversion
        raw_peak = float(np.max(np.abs(samples)))
        raw_rms = float(np.sqrt(np.mean(samples**2)))

        pcm_bytes = float32_to_pcm16(samples, gain=DEFAULT_GAIN)
        pcm = np.frombuffer(pcm_bytes, dtype=np.int16)

        gain_peak = float(np.max(np.abs(pcm))) / 32767.0
        gain_rms = float(np.sqrt(np.mean((pcm / 32767.0) ** 2)))

        frame_count += 1
        elapsed = time.monotonic() - start_time
        put_latest(
            stats_queue,
            CaptureStats(
                frame_count=frame_count,
                frames=frames,
                elapsed=elapsed,
                raw_peak=raw_peak,
                raw_rms=raw_rms,
                gain_peak=gain_peak,
                gain_rms=gain_rms,
                pcm_bytes=len(pcm_bytes),
            ),
        )

    print(f"\nRecording {RECORD_DURATION} seconds of system audio...")
    print("Make sure something is playing (music, video, etc.) so the loopback has signal.\n")

    stream = sd.InputStream(
        device=device.device_id,
        channels=device.channels,
        samplerate=target_sr,
        dtype=np.float32,
        callback=callback,
    )

    latest_stats = None
    deadline = time.monotonic() + RECORD_DURATION
    with stream:
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            time.sleep(min(REPORT_INTERVAL, remaining))

            while True:
                try:
                    latest_stats = stats_queue.get_nowait()
                except queue.Empty:
                    break

            if latest_stats is not None:
                print_stats(latest_stats)

    print(f"\nRecording complete. Total frames: {frame_count}")

    # Save WAV from ring buffer (apply same gain + limiter as real-time path)
    recorded = ring.read_last_n_seconds(RECORD_DURATION)
    if recorded.size == 0:
        print("[WARNING] No audio captured. Check that system audio is playing.")
    else:
        boosted = peak_normalize(recorded * DEFAULT_GAIN)
        wav_bytes = encode_wav(boosted, target_sr)
        OUTPUT_WAV.write_bytes(wav_bytes)
        print(f"Saved {OUTPUT_WAV} ({len(wav_bytes)} bytes, {recorded.size / target_sr:.1f}s)")
        print(f"\nNext step: Play {OUTPUT_WAV} in an audio player.")
        print("Verify it contains system audio (music/video sound), NOT microphone.")


if __name__ == "__main__":
    main()
