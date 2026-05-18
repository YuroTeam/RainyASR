"""Thread-safe ring buffer for streaming audio data."""

from __future__ import annotations

import threading

import numpy as np


class AudioRingBuffer:
    """Fixed-capacity ring buffer for float32 audio samples.

    Writes cycle back to the start when full, overwriting the oldest data.
    Reads always return the most recently written samples.
    """

    def __init__(self, sample_rate: int, max_duration: float = 15.0) -> None:
        """Initialize the ring buffer.

        Args:
            sample_rate: Sampling rate in Hz. Must be positive.
            max_duration: Maximum audio duration to keep, in seconds.
                Must be non-negative.
        """
        if sample_rate <= 0:
            raise ValueError("sample_rate must be positive")
        if max_duration < 0:
            raise ValueError("max_duration must be non-negative")

        self.sample_rate = sample_rate
        self.max_duration = max_duration
        self.capacity = int(sample_rate * max_duration)
        self._buffer = np.zeros(self.capacity, dtype=np.float32)
        self._write_index = 0
        self._valid_count = 0
        self._lock = threading.Lock()

    def write(self, samples: np.ndarray) -> None:
        """Append float32 samples into the buffer.

        Args:
            samples: 1-D array of float32 samples in range [-1, 1].
        """
        if samples.size == 0:
            return

        if samples.dtype != np.float32:
            samples = samples.astype(np.float32)

        # Ensure 1-D
        samples = np.ravel(samples)
        n = samples.shape[0]

        with self._lock:
            if self.capacity == 0:
                return

            if n > self.capacity:
                samples = samples[-self.capacity :]
                n = self.capacity

            # Write in at most two contiguous chunks
            first_chunk = min(n, self.capacity - self._write_index)
            self._buffer[self._write_index : self._write_index + first_chunk] = samples[
                :first_chunk
            ]

            if first_chunk < n:
                second_chunk = n - first_chunk
                self._buffer[:second_chunk] = samples[first_chunk:]
                self._write_index = second_chunk
            else:
                self._write_index = (self._write_index + first_chunk) % self.capacity

            self._valid_count = min(self._valid_count + n, self.capacity)

    def read_last_n_seconds(self, n: float) -> np.ndarray:
        """Read the most recent ``n`` seconds of audio.

        Args:
            n: Duration in seconds to read back from the newest sample.

        Returns:
            A 1-D float32 numpy array. May be shorter than requested if the
            buffer has not yet accumulated enough samples.
        """
        n_samples = int(n * self.sample_rate)

        with self._lock:
            available = min(n_samples, self._valid_count)
            if available == 0:
                return np.array([], dtype=np.float32)

            start = (self._write_index - available) % self.capacity

            # Single contiguous slice
            if start + available <= self.capacity:
                return self._buffer[start : start + available].copy()

            # Wrap-around: two slices
            first_len = self.capacity - start
            result = np.empty(available, dtype=np.float32)
            result[:first_len] = self._buffer[start:]
            result[first_len:] = self._buffer[: available - first_len]
            return result
