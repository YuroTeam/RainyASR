"""Audio encoding utilities: float32 to PCM16 and WAV."""

from __future__ import annotations

import io

import numpy as np
import soundfile as sf


def float32_to_pcm16(audio_array: np.ndarray) -> bytes:
    """Convert float32 audio to 16-bit little-endian PCM bytes.

    Args:
        audio_array: 1-D array of float32 samples in range [-1, 1].
            Values outside this range are clipped.

    Returns:
        PCM16 bytes, little-endian.
    """
    clipped = np.clip(np.ravel(audio_array), -1.0, 1.0)
    pcm = np.rint(clipped * 32767.0).astype("<i2")
    return pcm.tobytes()


def encode_wav(audio_array: np.ndarray, sample_rate: int) -> bytes:
    """Encode float32 audio into a standard WAV file (PCM16).

    Args:
        audio_array: 1-D array of float32 samples in range [-1, 1].
        sample_rate: Sampling rate in Hz.

    Returns:
        WAV file contents as bytes.
    """
    buf = io.BytesIO()
    sf.write(buf, np.ravel(audio_array), sample_rate, format="WAV", subtype="PCM_16")
    buf.seek(0)
    return buf.read()
