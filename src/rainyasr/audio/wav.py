"""Audio encoding utilities: float32 to PCM16 and WAV."""

from __future__ import annotations

import io

import numpy as np
import soundfile as sf

DEFAULT_GAIN: float = 8.0
DEFAULT_HEADROOM: float = 0.95


def peak_normalize(samples: np.ndarray, headroom: float = DEFAULT_HEADROOM) -> np.ndarray:
    """Scale samples so that the absolute peak matches ``headroom``.

    This is a peak limiter: if the signal is already below headroom,
    it is returned unchanged.  If it exceeds headroom, the *entire*
    waveform is scaled down uniformly, which avoids the distortion
    caused by hard-clipping.

    Args:
        samples: Float array of any shape.
        headroom: Target peak level (0.0–1.0).  Default 0.95 leaves
            a small safety margin before the PCM16 full-scale limit.

    Returns:
        Scaled array with the same shape and dtype as *samples*.
    """
    if samples.size == 0:
        return samples
    peak = np.max(np.abs(samples))
    if peak > headroom:
        return samples / peak * headroom
    return samples


def float32_to_pcm16(
    audio_array: np.ndarray,
    *,
    gain: float = 1.0,
    headroom: float = DEFAULT_HEADROOM,
) -> bytes:
    """Convert float32 audio to 16-bit little-endian PCM bytes.

    If gain is applied and the amplified signal exceeds *headroom*,
    a peak limiter scales the entire waveform down uniformly before
    the final clip to ``[-1.0, 1.0]``.  This avoids the audible
    distortion of hard-clipping.

    Args:
        audio_array: 1-D array of float32 samples in range [-1, 1].
        gain: Linear gain to apply before conversion.  Default 1.0.
        headroom: Peak limiter threshold (0.0–1.0).  Default 0.95.

    Returns:
        PCM16 bytes, little-endian.
    """
    samples = np.ravel(audio_array)
    if samples.size == 0:
        return b""
    samples = samples * gain
    if gain > 1.0:
        samples = peak_normalize(samples, headroom=headroom)
    clipped = np.clip(samples, -1.0, 1.0)
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
