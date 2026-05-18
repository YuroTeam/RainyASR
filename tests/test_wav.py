"""Tests for audio encoding utilities (wav.py)."""

from __future__ import annotations

import io

import numpy as np
import soundfile as sf

from rainyasr.audio.wav import encode_wav, float32_to_pcm16


class TestPcm16Encoding:
    def test_zero_returns_silence(self) -> None:
        data = np.zeros(10, dtype=np.float32)
        pcm = float32_to_pcm16(data)
        assert pcm == b"\x00" * 20

    def test_positive_one_maps_to_32767(self) -> None:
        data = np.array([1.0], dtype=np.float32)
        pcm = float32_to_pcm16(data)
        # 32767 in little-endian int16
        assert pcm == b"\xff\x7f"

    def test_negative_one_maps_to_minus_32767(self) -> None:
        data = np.array([-1.0], dtype=np.float32)
        pcm = float32_to_pcm16(data)
        # -32767 in little-endian int16 (two's complement)
        assert pcm == b"\x01\x80"

    def test_zero_sample_maps_to_zero(self) -> None:
        data = np.array([0.0], dtype=np.float32)
        pcm = float32_to_pcm16(data)
        assert pcm == b"\x00\x00"

    def test_half_maps_to_16384(self) -> None:
        data = np.array([0.5], dtype=np.float32)
        pcm = float32_to_pcm16(data)
        # 0.5 * 32767 = 16383.5, np.rint rounds to 16384
        expected = (16384).to_bytes(2, "little", signed=True)
        assert pcm == expected

    def test_clipping_positive_overflow(self) -> None:
        data = np.array([1.5, 2.0], dtype=np.float32)
        pcm = float32_to_pcm16(data)
        # Both should be clipped to 32767
        expected = b"\xff\x7f" * 2
        assert pcm == expected

    def test_clipping_negative_overflow(self) -> None:
        data = np.array([-1.5, -2.0], dtype=np.float32)
        pcm = float32_to_pcm16(data)
        # Both should be clipped to -32767
        expected = b"\x01\x80" * 2
        assert pcm == expected

    def test_output_length_is_twice_input(self) -> None:
        data = np.random.uniform(-1, 1, 16000).astype(np.float32)
        pcm = float32_to_pcm16(data)
        assert len(pcm) == len(data) * 2

    def test_multi_dimensional_input_flattened(self) -> None:
        data = np.array([[-0.5, 0.5], [0.25, -0.25]], dtype=np.float32)
        pcm = float32_to_pcm16(data)
        assert len(pcm) == 4 * 2

    def test_sine_wave_roundtrip(self) -> None:
        t = np.linspace(0, 2 * np.pi, 100, dtype=np.float32)
        data = np.sin(t)
        pcm = float32_to_pcm16(data)

        # Decode back to float32
        decoded = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32767.0
        np.testing.assert_allclose(decoded, data, atol=1e-3)


class TestWavEncoding:
    def test_output_starts_with_riff(self) -> None:
        data = np.zeros(100, dtype=np.float32)
        wav = encode_wav(data, 16000)
        assert wav[:4] == b"RIFF"
        assert wav[8:12] == b"WAVE"

    def test_output_contains_fmt_and_data_chunks(self) -> None:
        data = np.random.uniform(-1, 1, 16000).astype(np.float32)
        wav = encode_wav(data, 16000)
        assert b"fmt " in wav
        assert b"data" in wav

    def test_output_reasonable_size(self) -> None:
        data = np.zeros(16000, dtype=np.float32)
        wav = encode_wav(data, 16000)
        # WAV header (~44 bytes) + 16000 samples * 2 bytes
        assert len(wav) >= 44 + 16000 * 2

    def test_different_sample_rates(self) -> None:
        for sr in [8000, 16000, 44100, 48000]:
            data = np.zeros(100, dtype=np.float32)
            wav = encode_wav(data, sr)
            assert wav[:4] == b"RIFF"

    def test_wav_can_be_decoded_back(self) -> None:
        """Encoding then decoding should produce approximately the same audio."""
        data = np.random.uniform(-1, 1, 16000).astype(np.float32)

        wav = encode_wav(data, 16000)

        # Decode with soundfile from memory
        decoded, sr = sf.read(io.BytesIO(wav), dtype="float32")

        assert sr == 16000
        assert len(decoded) == len(data)
        np.testing.assert_allclose(decoded, data, atol=1e-3)
