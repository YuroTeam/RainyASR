"""Tests for AudioRingBuffer."""

from __future__ import annotations

import threading
import time

import numpy as np

from rainyasr.audio.ring_buffer import AudioRingBuffer

SAMPLE_RATE = 16000


# ---------------------------------------------------------------------------
# Single-threaded consistency
# ---------------------------------------------------------------------------


class TestSingleThreaded:
    def test_empty_buffer_returns_empty_array(self) -> None:
        buf = AudioRingBuffer(SAMPLE_RATE, max_duration=1.0)
        data = buf.read_last_n_seconds(0.5)
        assert data.shape == (0,)
        assert data.dtype == np.float32

    def test_write_and_read_back(self) -> None:
        buf = AudioRingBuffer(SAMPLE_RATE, max_duration=1.0)
        samples = np.linspace(-1, 1, SAMPLE_RATE, dtype=np.float32)
        buf.write(samples)

        result = buf.read_last_n_seconds(1.0)
        np.testing.assert_array_equal(result, samples)

    def test_read_less_than_written(self) -> None:
        buf = AudioRingBuffer(SAMPLE_RATE, max_duration=2.0)
        samples = np.arange(SAMPLE_RATE * 2, dtype=np.float32)
        buf.write(samples)

        result = buf.read_last_n_seconds(0.5)
        expected = samples[-SAMPLE_RATE // 2 :]
        np.testing.assert_array_equal(result, expected)

    def test_read_more_than_written_returns_all(self) -> None:
        buf = AudioRingBuffer(SAMPLE_RATE, max_duration=2.0)
        samples = np.arange(SAMPLE_RATE, dtype=np.float32)
        buf.write(samples)

        result = buf.read_last_n_seconds(2.0)
        assert len(result) == SAMPLE_RATE
        np.testing.assert_array_equal(result, samples)

    def test_overwrite_oldest_data(self) -> None:
        buf = AudioRingBuffer(SAMPLE_RATE, max_duration=1.0)
        first = np.ones(SAMPLE_RATE, dtype=np.float32) * 1.0
        second = np.ones(SAMPLE_RATE, dtype=np.float32) * 2.0
        third = np.ones(SAMPLE_RATE // 2, dtype=np.float32) * 3.0

        buf.write(first)
        buf.write(second)
        buf.write(third)

        # capacity = SAMPLE_RATE (1.0 sec).
        # write(first) fills [0:SAMPLE_RATE] with 1.0, write_index resets to 0.
        # write(second) overwrites [0:SAMPLE_RATE] with 2.0, wiping first entirely.
        # write(third) overwrites second's first half [0:SAMPLE_RATE//2] with 3.0.
        # Buffer now: [3.0 * 8000, 2.0 * 8000].
        # Read last 1.0 sec → 0.5 sec of third + 0.5 sec of second.
        result = buf.read_last_n_seconds(1.0)
        expected = np.concatenate(
            [
                np.ones(SAMPLE_RATE // 2, dtype=np.float32) * 2.0,
                np.ones(SAMPLE_RATE // 2, dtype=np.float32) * 3.0,
            ]
        )
        np.testing.assert_array_equal(result, expected)

    def test_write_multi_dtype_converts_to_float32(self) -> None:
        buf = AudioRingBuffer(SAMPLE_RATE, max_duration=1.0)
        buf.write(np.array([0.0, 0.5, 1.0]))  # default float64
        result = buf.read_last_n_seconds(1.0)
        assert result.dtype == np.float32

    def test_write_empty_does_not_crash(self) -> None:
        buf = AudioRingBuffer(SAMPLE_RATE, max_duration=1.0)
        buf.write(np.array([], dtype=np.float32))
        assert buf.read_last_n_seconds(1.0).shape == (0,)

    def test_write_2d_array_flattens(self) -> None:
        buf = AudioRingBuffer(SAMPLE_RATE, max_duration=1.0)
        arr = np.array([[0.0, 0.1], [0.2, 0.3]], dtype=np.float32)
        buf.write(arr)
        result = buf.read_last_n_seconds(1.0)
        np.testing.assert_array_equal(result, np.array([0.0, 0.1, 0.2, 0.3], dtype=np.float32))


# ---------------------------------------------------------------------------
# Boundary conditions
# ---------------------------------------------------------------------------


class TestBoundaryConditions:
    def test_exact_capacity_write(self) -> None:
        buf = AudioRingBuffer(SAMPLE_RATE, max_duration=1.0)
        samples = np.arange(SAMPLE_RATE, dtype=np.float32)
        buf.write(samples)
        result = buf.read_last_n_seconds(1.0)
        np.testing.assert_array_equal(result, samples)

    def test_wraparound_read(self) -> None:
        """Force a read that wraps around the end of the internal array."""
        buf = AudioRingBuffer(10, max_duration=1.0)  # capacity = 10
        # Write 7 samples → write_index = 7
        buf.write(np.array([0, 1, 2, 3, 4, 5, 6], dtype=np.float32))
        # Write 5 more → wraps: 7,8,9 then 0,1 (second chunk is only 2 samples)
        buf.write(np.array([10, 11, 12, 13, 14], dtype=np.float32))

        # Buffer now contains [13, 14, 2, 3, 4, 5, 6, 10, 11, 12]
        # Read last 4 should give [11, 12, 13, 14]
        result = buf.read_last_n_seconds(0.4)  # 4 samples
        np.testing.assert_array_equal(result, np.array([11, 12, 13, 14], dtype=np.float32))

    def test_zero_capacity(self) -> None:
        buf = AudioRingBuffer(SAMPLE_RATE, max_duration=0.0)
        buf.write(np.array([1.0, 2.0], dtype=np.float32))
        assert buf.read_last_n_seconds(1.0).shape == (0,)


# ---------------------------------------------------------------------------
# Multi-threaded safety
# ---------------------------------------------------------------------------


class TestConcurrency:
    def test_concurrent_write_and_read(self) -> None:
        buf = AudioRingBuffer(SAMPLE_RATE, max_duration=2.0)
        errors: list[Exception] = []
        stop = threading.Event()

        def writer() -> None:
            for i in range(500):
                samples = np.full(SAMPLE_RATE // 10, float(i), dtype=np.float32)
                try:
                    buf.write(samples)
                except Exception as exc:
                    errors.append(exc)
                time.sleep(0.001)
            stop.set()

        def reader() -> None:
            while not stop.is_set():
                try:
                    data = buf.read_last_n_seconds(0.5)
                    assert data.dtype == np.float32
                except Exception as exc:
                    errors.append(exc)
                time.sleep(0.002)

        t_write = threading.Thread(target=writer)
        t_read = threading.Thread(target=reader)
        t_write.start()
        t_read.start()
        t_write.join()
        t_read.join()

        assert not errors, f"Concurrent operations raised: {errors}"

    def test_multiple_writers(self) -> None:
        buf = AudioRingBuffer(SAMPLE_RATE, max_duration=1.0)
        errors: list[Exception] = []
        num_threads = 4
        writes_per_thread = 100

        def writer(thread_id: int) -> None:
            for _ in range(writes_per_thread):
                samples = np.full(100, float(thread_id), dtype=np.float32)
                try:
                    buf.write(samples)
                except Exception as exc:
                    errors.append(exc)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Multiple writers raised: {errors}"
        # Buffer should be full
        result = buf.read_last_n_seconds(1.0)
        assert len(result) == SAMPLE_RATE

    def test_reader_never_sees_corrupted_shape(self) -> None:
        """Ensure reads always return 1-D arrays even under heavy contention."""
        buf = AudioRingBuffer(1000, max_duration=1.0)
        stop = threading.Event()
        shape_errors: list[tuple] = []

        def writer() -> None:
            for _ in range(1000):
                buf.write(np.random.uniform(-1, 1, 50).astype(np.float32))
            stop.set()

        def reader() -> None:
            while not stop.is_set():
                data = buf.read_last_n_seconds(0.1)
                if data.ndim != 1:
                    shape_errors.append(data.shape)

        t_write = threading.Thread(target=writer)
        t_read = threading.Thread(target=reader)
        t_write.start()
        t_read.start()
        t_write.join()
        t_read.join()

        assert not shape_errors, f"Saw non-1D shapes: {shape_errors}"
