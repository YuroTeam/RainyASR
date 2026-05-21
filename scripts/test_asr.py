"""Task 8: Validate DashScope real-time ASR.

Requires DASHSCOPE_API_KEY in environment.
Sends a generated sine-wave PCM stream and prints partial/final transcripts.
"""

from __future__ import annotations

import asyncio
import os

import numpy as np

from rainyasr.audio.wav import float32_to_pcm16
from rainyasr.providers.asr import QwenRealtimeASRProvider

SAMPLE_RATE = 16000
CHUNK_DURATION = 0.1  # seconds
CHUNK_BYTES = int(SAMPLE_RATE * CHUNK_DURATION * 2)  # 16-bit mono
TOTAL_DURATION = 5.0


def generate_test_tone(duration: float, freq: float = 440.0) -> bytes:
    """Generate a PCM16 sine wave for testing."""
    t = np.linspace(0, duration, int(SAMPLE_RATE * duration), endpoint=False)
    wave = 0.3 * np.sin(2 * np.pi * freq * t)
    return float32_to_pcm16(wave.astype(np.float32))


async def main() -> None:
    api_key = os.getenv("DASHSCOPE_API_KEY", "")
    if not api_key:
        print("[ERROR] Set DASHSCOPE_API_KEY environment variable.")
        raise SystemExit(1)

    provider = QwenRealtimeASRProvider(api_key=api_key)

    print("Connecting to DashScope ASR...")
    await provider.start()
    print("Connected. Sending audio...\n")

    pcm = generate_test_tone(TOTAL_DURATION)
    chunks = [pcm[i : i + CHUNK_BYTES] for i in range(0, len(pcm), CHUNK_BYTES)]

    async def sender() -> None:
        for chunk in chunks:
            await provider.send_audio(chunk)
            await asyncio.sleep(CHUNK_DURATION)

    async def receiver() -> None:
        async for event in provider.events():
            tag = "[FINAL]" if event.is_final else "[PARTIAL]"
            print(f"  {tag} {event.text}")

    try:
        await asyncio.gather(sender(), receiver())
    except asyncio.CancelledError:
        pass
    finally:
        print("\nStopping...")
        await provider.stop()
        print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
