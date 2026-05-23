"""Background worker for audio capture, ASR, translation, and subtitle updates."""

from __future__ import annotations

import asyncio
import contextlib
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from math import ceil
from typing import Protocol

import logfire
import numpy as np
import sounddevice as sd
from PySide6.QtCore import QObject, Signal

from rainyasr.audio.capture import AudioDeviceDetector
from rainyasr.audio.ring_buffer import AudioRingBuffer
from rainyasr.audio.wav import DEFAULT_GAIN, float32_to_pcm16
from rainyasr.gui.subtitle_window import SubtitleWindow
from rainyasr.providers.base import RealtimeASRProvider, TranscriptEvent, TranslationProvider


class AudioStream(Protocol):
    """Protocol for the small subset of sounddevice.InputStream we use."""

    def start(self) -> None:
        """Start audio capture."""

    def stop(self) -> None:
        """Stop audio capture."""

    def close(self) -> None:
        """Release audio capture resources."""


AudioStreamFactory = Callable[..., AudioStream]


@dataclass(frozen=True)
class _TranslationRequest:
    text: str
    segment_id: str | None
    history: tuple[str, ...]
    source_version: int
    is_partial: bool = False


@dataclass(frozen=True)
class _AudioQueueItem:
    audio_blocks: tuple[bytes, ...]
    stop_after_send: bool = False


class SubtitleWorker(QObject):
    """Coordinate live audio, realtime ASR, translation, and Qt subtitle updates.

    The worker intentionally receives provider instances and runtime parameters
    from its caller.  Loading or mutating application config belongs to the app
    shell and settings UI, not to this background pipeline.
    """

    subtitle_changed = Signal(str, str, bool)
    error_occurred = Signal(str)
    state_changed = Signal(str)

    def __init__(
        self,
        *,
        asr_provider: RealtimeASRProvider,
        translation_provider: TranslationProvider,
        subtitle_window: SubtitleWindow | None = None,
        target_lang: str = "zh",
        sample_rate: int = 16000,
        channels: int = 1,
        frame_ms: int = 100,
        audio_queue_max_frames: int = 100,
        translation_queue_max_items: int = 2,
        translate_partials: bool = True,
        partial_translation_interval_ms: int = 300,
        partial_translation_min_chars: int = 3,
        audio_device_detector: AudioDeviceDetector | None = None,
        audio_stream_factory: AudioStreamFactory | None = None,
        audio_ring_buffer: AudioRingBuffer | None = None,
        audio_gain: float = DEFAULT_GAIN,
        backpressure_reconnect_threshold: int = 50,
        enable_silence_gate: bool = True,
        silence_rms_threshold: float = 0.0003,
        speech_start_frames: int = 2,
        silence_stop_ms: int = 3000,
        preroll_ms: int = 500,
    ) -> None:
        super().__init__()
        if sample_rate <= 0:
            raise ValueError("sample_rate must be positive")
        if channels <= 0:
            raise ValueError("channels must be positive")
        if frame_ms <= 0:
            raise ValueError("frame_ms must be positive")
        if audio_queue_max_frames <= 0:
            raise ValueError("audio_queue_max_frames must be positive")
        if translation_queue_max_items <= 0:
            raise ValueError("translation_queue_max_items must be positive")
        if partial_translation_interval_ms <= 0:
            raise ValueError("partial_translation_interval_ms must be positive")
        if partial_translation_min_chars < 0:
            raise ValueError("partial_translation_min_chars must be non-negative")
        if backpressure_reconnect_threshold < 0:
            raise ValueError("backpressure_reconnect_threshold must be non-negative")
        if silence_rms_threshold < 0:
            raise ValueError("silence_rms_threshold must be non-negative")
        if speech_start_frames <= 0:
            raise ValueError("speech_start_frames must be positive")
        if silence_stop_ms <= 0:
            raise ValueError("silence_stop_ms must be positive")
        if preroll_ms < 0:
            raise ValueError("preroll_ms must be non-negative")

        self._asr_provider = asr_provider
        self._translation_provider = translation_provider
        self._target_lang = target_lang
        self._sample_rate = sample_rate
        self._channels = channels
        self._frame_ms = frame_ms
        self._audio_gain = audio_gain
        self._translate_partials = translate_partials
        self._partial_translation_interval = partial_translation_interval_ms / 1000
        self._partial_translation_min_chars = partial_translation_min_chars
        self._backpressure_reconnect_threshold = backpressure_reconnect_threshold
        self._enable_silence_gate = enable_silence_gate
        self._silence_rms_threshold = silence_rms_threshold
        self._speech_start_frames = speech_start_frames
        self._silence_stop_frames = max(1, ceil(silence_stop_ms / frame_ms))
        self._preroll_max_frames = max(0, ceil(preroll_ms / frame_ms))

        self._audio_device_detector = audio_device_detector or AudioDeviceDetector()
        self._audio_stream_factory = audio_stream_factory or sd.InputStream
        self._audio_ring_buffer = audio_ring_buffer

        self._audio_queue: asyncio.Queue[_AudioQueueItem | None] = asyncio.Queue(
            maxsize=audio_queue_max_frames
        )
        self._translation_queue: asyncio.Queue[_TranslationRequest | None] = asyncio.Queue(
            maxsize=translation_queue_max_items
        )
        self._preroll_audio_frames: deque[bytes] = deque(maxlen=self._preroll_max_frames)
        self._audio_stream: AudioStream | None = None

        self._audio_sender_task: asyncio.Task[None] | None = None
        self._asr_event_task: asyncio.Task[None] | None = None
        self._translation_task: asyncio.Task[None] | None = None
        self._partial_translation_task: asyncio.Task[None] | None = None
        self._backpressure_reconnect_task: asyncio.Task[None] | None = None

        self._loop: asyncio.AbstractEventLoop | None = None
        self._reconnect_event: asyncio.Event | None = None
        self._partial_translation_event: asyncio.Event | None = None
        self._translation_semaphore: asyncio.Semaphore | None = None
        self._asr_send_lock: asyncio.Lock | None = None
        self._stop_lock: asyncio.Lock | None = None

        self._running = False
        self._stopping = False
        self._dropped_audio_frames = 0
        self._dropped_translation_items = 0
        self._consecutive_audio_drops = 0
        self._reconnecting = False
        self._asr_session_active = False
        self._speech_run_frames = 0
        self._silence_run_frames = 0
        self._speech_gate_open = False
        self._pending_speech_audio_frames: list[bytes] = []

        self._final_segment_ids: set[str] = set()
        self._final_texts_without_id: set[str] = set()
        self._source_history: list[str] = []
        self._source_version = 0
        self._latest_partial_request: _TranslationRequest | None = None
        self._latest_partial_version = 0
        self._last_partial_translation_text = ""

        if subtitle_window is not None:
            self.subtitle_changed.connect(
                lambda original, translated, is_partial: subtitle_window.update_subtitle(
                    original,
                    translated,
                    is_partial=is_partial,
                )
            )

    @property
    def is_running(self) -> bool:
        """Return whether the worker pipeline is currently running."""
        return self._running

    @property
    def dropped_audio_frames(self) -> int:
        """Number of audio frames dropped because the queue was full."""
        return self._dropped_audio_frames

    @property
    def dropped_translation_items(self) -> int:
        """Number of stale final transcripts dropped before translation."""
        return self._dropped_translation_items

    @property
    def is_asr_session_active(self) -> bool:
        """Return whether a remote ASR session is currently open."""
        return self._asr_session_active

    async def start(self, *, capture_audio: bool = True) -> None:
        """Start ASR, background tasks, and optionally live audio capture."""
        if self._running:
            return

        self._loop = asyncio.get_running_loop()
        self._reconnect_event = asyncio.Event()
        self._partial_translation_event = asyncio.Event()
        self._translation_semaphore = asyncio.Semaphore(2)
        self._asr_send_lock = asyncio.Lock()
        self._stop_lock = asyncio.Lock()
        self._stopping = False
        self._dropped_audio_frames = 0
        self._dropped_translation_items = 0
        self._consecutive_audio_drops = 0
        self._reconnecting = False
        self._asr_session_active = False
        self._reset_silence_gate_state()
        self._clear_runtime_state()
        self._drain_queue(self._audio_queue)
        self._drain_queue(self._translation_queue)

        self.state_changed.emit("starting")
        logfire.info("Starting subtitle worker")

        try:
            self._audio_sender_task = self._loop.create_task(
                self._audio_sender_loop(), name="rainyasr-audio-sender"
            )
            self._translation_task = self._loop.create_task(
                self._translation_loop(), name="rainyasr-translation"
            )
            self._partial_translation_task = self._loop.create_task(
                self._partial_translation_loop(), name="rainyasr-partial-translation"
            )
            self._backpressure_reconnect_task = self._loop.create_task(
                self._backpressure_reconnect_loop(),
                name="rainyasr-backpressure-reconnect",
            )

            if not capture_audio or not self._enable_silence_gate:
                await self._start_asr_session()

            if capture_audio:
                self._start_audio_stream()
        except Exception:
            await self.stop()
            raise

        self._running = True
        self.state_changed.emit("running")
        logfire.info("Subtitle worker started", capture_audio=capture_audio)

    async def stop(self) -> None:
        """Stop audio capture, ASR, translation, and background tasks."""
        if self._stop_lock is None:
            self._stop_audio_stream()
            return

        async with self._stop_lock:
            has_tasks = any(
                task is not None
                for task in (
                    self._audio_sender_task,
                    self._asr_event_task,
                    self._translation_task,
                    self._partial_translation_task,
                    self._backpressure_reconnect_task,
                )
            )
            if not self._running and self._audio_stream is None and not has_tasks:
                return

            self._stopping = True
            self.state_changed.emit("stopping")
            logfire.info("Stopping subtitle worker")

            self._stop_audio_stream()
            self._drain_queue(self._audio_queue)
            await self._put_queue_sentinel(self._audio_queue)
            await self._await_task(self._audio_sender_task)

            await self._stop_asr_session()
            await self._put_queue_sentinel(self._translation_queue)
            await self._cancel_task(self._backpressure_reconnect_task)
            await self._cancel_task(self._partial_translation_task)
            await self._cancel_task(self._translation_task)

            self._audio_sender_task = None
            self._asr_event_task = None
            self._translation_task = None
            self._partial_translation_task = None
            self._backpressure_reconnect_task = None
            self._reconnect_event = None
            self._partial_translation_event = None
            self._translation_semaphore = None
            self._asr_send_lock = None
            self._running = False
            self._stopping = False
            self._reset_silence_gate_state()
            self.state_changed.emit("stopped")
            logfire.info("Subtitle worker stopped")

    def _start_audio_stream(self) -> None:
        device = self._audio_device_detector.find_loopback_device()
        blocksize = max(1, int(self._sample_rate * self._frame_ms / 1000))
        channels = max(1, min(self._channels, device.channels))

        self._audio_stream = self._audio_stream_factory(
            device=device.device_id,
            channels=channels,
            samplerate=self._sample_rate,
            blocksize=blocksize,
            dtype=np.float32,
            callback=self._audio_callback,
        )
        self._audio_stream.start()
        logfire.info(
            "Audio stream started",
            device_id=device.device_id,
            device_name=device.name,
            sample_rate=self._sample_rate,
            channels=channels,
            blocksize=blocksize,
        )

    def _stop_audio_stream(self) -> None:
        if self._audio_stream is None:
            return

        stream = self._audio_stream
        self._audio_stream = None
        with contextlib.suppress(Exception):
            stream.stop()
        with contextlib.suppress(Exception):
            stream.close()
        logfire.info("Audio stream stopped")

    def _audio_callback(
        self,
        indata: np.ndarray,
        frames: int,
        _time_info: object,
        status: object,
    ) -> None:
        if self._stopping:
            return
        if status:
            logfire.warning("Audio callback status", status=str(status))

        try:
            samples = self._to_mono_float32(indata)
            if self._audio_ring_buffer is not None:
                self._audio_ring_buffer.write(samples)

            rms = self._audio_rms(samples)
            pcm_bytes = float32_to_pcm16(samples, gain=self._audio_gain)
        except Exception:
            logfire.exception("Failed to encode audio frame", frames=frames)
            return

        loop = self._loop
        if loop is None or loop.is_closed():
            return
        loop.call_soon_threadsafe(self._enqueue_audio_frame, pcm_bytes, rms)

    @staticmethod
    def _to_mono_float32(indata: np.ndarray) -> np.ndarray:
        frame = np.asarray(indata)
        if frame.ndim == 1:
            return frame.astype(np.float32, copy=True)
        if frame.ndim != 2:
            return np.ravel(frame).astype(np.float32, copy=True)
        if frame.shape[1] == 0:
            return np.array([], dtype=np.float32)
        if frame.shape[1] == 1:
            return frame[:, 0].astype(np.float32, copy=True)
        return np.mean(frame, axis=1).astype(np.float32, copy=False)

    @staticmethod
    def _audio_rms(samples: np.ndarray) -> float:
        if samples.size == 0:
            return 0.0
        return float(np.sqrt(np.mean(samples.astype(np.float32, copy=False) ** 2)))

    def _enqueue_audio_frame(self, pcm_bytes: bytes, rms: float) -> None:
        if self._stopping or not pcm_bytes:
            return

        if not self._enable_silence_gate:
            self._enqueue_audio_bytes(pcm_bytes)
            return

        is_active = rms >= self._silence_rms_threshold

        if is_active:
            self._silence_run_frames = 0
            if not self._speech_gate_open:
                self._speech_run_frames += 1
                self._pending_speech_audio_frames.append(pcm_bytes)
                if self._speech_run_frames < self._speech_start_frames:
                    logfire.debug(
                        "Holding active audio frame until speech gate opens",
                        rms=rms,
                        silence_rms_threshold=self._silence_rms_threshold,
                        speech_run_frames=self._speech_run_frames,
                    )
                    return

                self._speech_gate_open = True
                audio_blocks = tuple(self._preroll_audio_frames) + tuple(
                    self._pending_speech_audio_frames
                )
                logfire.info(
                    "Speech detected; opening ASR gate",
                    rms=rms,
                    silence_rms_threshold=self._silence_rms_threshold,
                    preroll_frames=len(self._preroll_audio_frames),
                    speech_start_frames=len(self._pending_speech_audio_frames),
                )
                self._pending_speech_audio_frames.clear()
                self._enqueue_audio_item(_AudioQueueItem(audio_blocks=audio_blocks))
                return

            self._enqueue_audio_item(_AudioQueueItem(audio_blocks=(pcm_bytes,)))
            return

        self._speech_run_frames = 0
        self._pending_speech_audio_frames.clear()
        if not self._speech_gate_open:
            self._append_preroll(pcm_bytes)
            logfire.debug(
                "Dropping local silence before ASR session starts",
                rms=rms,
                silence_rms_threshold=self._silence_rms_threshold,
            )
            return

        self._silence_run_frames += 1
        should_stop = self._silence_run_frames >= self._silence_stop_frames
        self._enqueue_audio_item(
            _AudioQueueItem(audio_blocks=(pcm_bytes,), stop_after_send=should_stop)
        )

        if should_stop:
            logfire.info(
                "Closing ASR gate after local silence",
                rms=rms,
                silence_rms_threshold=self._silence_rms_threshold,
                silence_frames=self._silence_run_frames,
            )
            self._speech_gate_open = False
            self._speech_run_frames = 0
            self._silence_run_frames = 0
            self._preroll_audio_frames.clear()
            self._pending_speech_audio_frames.clear()

    def _append_preroll(self, pcm_bytes: bytes) -> None:
        if self._preroll_max_frames <= 0:
            return
        self._preroll_audio_frames.append(pcm_bytes)

    def _enqueue_audio_bytes(self, pcm_bytes: bytes) -> None:
        if self._stopping or not pcm_bytes:
            return

        self._enqueue_audio_item(_AudioQueueItem(audio_blocks=(pcm_bytes,)))

    def _enqueue_audio_item(self, item: _AudioQueueItem) -> None:
        if not item.audio_blocks:
            return

        if self._audio_queue.full():
            with contextlib.suppress(asyncio.QueueEmpty):
                self._audio_queue.get_nowait()
            self._dropped_audio_frames += 1
            self._consecutive_audio_drops += 1
            logfire.warning(
                "Audio queue full; dropped oldest frame",
                dropped_audio_frames=self._dropped_audio_frames,
                audio_queue_size=self._audio_queue.qsize(),
            )
            self._request_asr_reconnect_if_needed()
        else:
            self._consecutive_audio_drops = 0

        with contextlib.suppress(asyncio.QueueFull):
            self._audio_queue.put_nowait(item)

    def _request_asr_reconnect_if_needed(self) -> None:
        if self._backpressure_reconnect_threshold == 0:
            return
        if not self._asr_session_active:
            return
        if self._consecutive_audio_drops < self._backpressure_reconnect_threshold:
            return
        if self._reconnect_event is None or self._reconnecting:
            return

        logfire.warning(
            "Requesting ASR reconnect after repeated audio backpressure",
            consecutive_audio_drops=self._consecutive_audio_drops,
        )
        self._reconnect_event.set()

    async def _audio_sender_loop(self) -> None:
        try:
            while True:
                item = await self._audio_queue.get()
                if item is None:
                    return

                await self._send_audio_item(item)

                logfire.debug(
                    "Sent audio frame",
                    audio_blocks=len(item.audio_blocks),
                    pcm_bytes=sum(len(block) for block in item.audio_blocks),
                    audio_queue_size=self._audio_queue.qsize(),
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._publish_error(f"Audio sender failed: {exc}", exc)
            await self.stop()

    async def _send_audio_item(self, item: _AudioQueueItem) -> None:
        lock = self._asr_send_lock
        if lock is None:
            return

        async with lock:
            await self._start_asr_session_locked()
            for audio_block in item.audio_blocks:
                await self._asr_provider.send_audio(audio_block)

        if item.stop_after_send:
            await self._stop_asr_session()

    async def _start_asr_session(self) -> None:
        lock = self._asr_send_lock
        if lock is None:
            return
        async with lock:
            await self._start_asr_session_locked()

    async def _start_asr_session_locked(self) -> None:
        if self._asr_session_active:
            return
        if self._loop is None:
            return

        logfire.info("Starting ASR session")
        await self._asr_provider.start()
        self._asr_session_active = True
        self._asr_event_task = self._loop.create_task(
            self._asr_event_loop(), name="rainyasr-asr-events"
        )
        self.state_changed.emit("asr_running")
        logfire.info("ASR session started")

    async def _stop_asr_session(self) -> None:
        lock = self._asr_send_lock
        if lock is None:
            return

        event_task: asyncio.Task[None] | None = None
        async with lock:
            event_task = self._asr_event_task
            if not self._asr_session_active and event_task is None:
                return

            if self._asr_session_active:
                logfire.info("Stopping ASR session")
                with contextlib.suppress(Exception):
                    await self._asr_provider.stop()
                self._asr_session_active = False

        await self._await_task(event_task)
        await self._cancel_task(event_task)
        if self._asr_event_task is event_task:
            self._asr_event_task = None
        logfire.info("ASR session stopped")

    async def _asr_event_loop(self) -> None:
        try:
            async for event in self._asr_provider.events():
                await self._handle_transcript_event(event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._publish_error(f"ASR event stream failed: {exc}", exc)
            await self.stop()

    async def _translation_loop(self) -> None:
        try:
            while True:
                item = await self._translation_queue.get()
                if item is None:
                    return

                logfire.info(
                    "Translating final transcript",
                    segment_id=item.segment_id,
                    history_size=len(item.history),
                )
                try:
                    translated = await self._translate_request(item)
                except Exception as exc:
                    self._publish_error(f"Translation failed: {exc}", exc)
                    translated = ""

                self.subtitle_changed.emit(item.text, translated, False)
                logfire.info(
                    "Translation completed",
                    segment_id=item.segment_id,
                    translated_chars=len(translated),
                )
        except asyncio.CancelledError:
            raise

    async def _partial_translation_loop(self) -> None:
        assert self._partial_translation_event is not None
        try:
            while True:
                await self._partial_translation_event.wait()
                self._partial_translation_event.clear()
                await asyncio.sleep(self._partial_translation_interval)

                item = self._latest_partial_request
                if item is None:
                    continue
                if item.text == self._last_partial_translation_text:
                    continue
                if len(item.text.strip()) < self._partial_translation_min_chars:
                    continue

                logfire.debug(
                    "Translating partial transcript",
                    segment_id=item.segment_id,
                    text_chars=len(item.text),
                    source_version=item.source_version,
                )
                try:
                    translated = await self._translate_request(item)
                except Exception as exc:
                    self._publish_error(f"Partial translation failed: {exc}", exc)
                    continue

                if item.source_version != self._latest_partial_version:
                    logfire.debug(
                        "Skipped stale partial translation",
                        segment_id=item.segment_id,
                        source_version=item.source_version,
                        latest_partial_version=self._latest_partial_version,
                    )
                    continue

                self._last_partial_translation_text = item.text
                self.subtitle_changed.emit(item.text, translated, True)
                logfire.debug(
                    "Partial translation completed",
                    segment_id=item.segment_id,
                    translated_chars=len(translated),
                )
        except asyncio.CancelledError:
            raise

    async def _translate_request(self, item: _TranslationRequest) -> str:
        semaphore = self._translation_semaphore
        if semaphore is None:
            return ""
        async with semaphore:
            return await self._translation_provider.translate(
                item.text,
                target_lang=self._target_lang,
                history=list(item.history),
            )

    async def _backpressure_reconnect_loop(self) -> None:
        assert self._reconnect_event is not None
        try:
            while True:
                await self._reconnect_event.wait()
                self._reconnect_event.clear()
                await self._reconnect_asr()
        except asyncio.CancelledError:
            raise

    async def _reconnect_asr(self) -> None:
        if self._stopping or self._reconnecting:
            return
        if not self._asr_session_active:
            return
        lock = self._asr_send_lock
        if lock is None:
            return

        self._reconnecting = True
        self.state_changed.emit("reconnecting")
        logfire.warning("Reconnecting ASR provider")
        try:
            async with lock:
                if not self._asr_session_active:
                    return
                with contextlib.suppress(Exception):
                    await self._asr_provider.stop()
                await self._cancel_task(self._asr_event_task)
                self._asr_event_task = None
                self._asr_session_active = False
                await self._asr_provider.start()
                self._asr_session_active = True
                if self._loop is not None:
                    self._asr_event_task = self._loop.create_task(
                        self._asr_event_loop(), name="rainyasr-asr-events"
                    )
            self._consecutive_audio_drops = 0
            self.state_changed.emit("running")
            logfire.info("ASR provider reconnected")
        except Exception as exc:
            self._publish_error(f"ASR reconnect failed: {exc}", exc)
            await self.stop()
        finally:
            self._reconnecting = False

    async def _handle_transcript_event(self, event: TranscriptEvent) -> None:
        text = event.text.strip()
        if not text:
            return

        if event.is_final:
            await self._handle_final_transcript(event, text)
            return

        logfire.debug(
            "Received partial transcript",
            segment_id=event.segment_id,
            text_chars=len(text),
        )
        source_version = self._next_source_version()
        self.subtitle_changed.emit(text, "", True)
        self._schedule_partial_translation(
            _TranslationRequest(
                text=text,
                segment_id=event.segment_id,
                history=tuple(self._source_history[-2:]),
                source_version=source_version,
                is_partial=True,
            )
        )

    async def _handle_final_transcript(self, event: TranscriptEvent, text: str) -> None:
        if self._is_duplicate_final(event.segment_id, text):
            logfire.debug("Skipped duplicate final transcript", segment_id=event.segment_id)
            return

        source_version = self._next_source_version()
        history = tuple(self._source_history[-2:])
        self._source_history.append(text)
        if len(self._source_history) > 2:
            self._source_history = self._source_history[-2:]

        logfire.info(
            "Received final transcript",
            segment_id=event.segment_id,
            text_chars=len(text),
            history_size=len(history),
        )
        self.subtitle_changed.emit(text, "", False)
        self._enqueue_translation_item(
            _TranslationRequest(
                text=text,
                segment_id=event.segment_id,
                history=history,
                source_version=source_version,
            )
        )

    def _schedule_partial_translation(self, item: _TranslationRequest) -> None:
        if not self._translate_partials:
            return
        event = self._partial_translation_event
        if event is None:
            return

        self._latest_partial_request = item
        self._latest_partial_version = item.source_version
        event.set()

    def _enqueue_translation_item(self, item: _TranslationRequest) -> None:
        if self._translation_queue.full():
            with contextlib.suppress(asyncio.QueueEmpty):
                self._translation_queue.get_nowait()
                self._dropped_translation_items += 1
            logfire.warning(
                "Translation queue full; dropped stale transcript",
                dropped_translation_items=self._dropped_translation_items,
                translation_queue_size=self._translation_queue.qsize(),
                segment_id=item.segment_id,
            )

        with contextlib.suppress(asyncio.QueueFull):
            self._translation_queue.put_nowait(item)

    def _is_duplicate_final(self, segment_id: str | None, text: str) -> bool:
        if segment_id:
            if segment_id in self._final_segment_ids:
                return True
            self._final_segment_ids.add(segment_id)
            return False

        normalized = " ".join(text.split())
        if normalized in self._final_texts_without_id:
            return True
        self._final_texts_without_id.add(normalized)
        return False

    def _next_source_version(self) -> int:
        self._source_version += 1
        return self._source_version

    def _clear_runtime_state(self) -> None:
        self._final_segment_ids.clear()
        self._final_texts_without_id.clear()
        self._source_history.clear()
        self._source_version = 0
        self._latest_partial_request = None
        self._latest_partial_version = 0
        self._last_partial_translation_text = ""

    def _reset_silence_gate_state(self) -> None:
        self._speech_run_frames = 0
        self._silence_run_frames = 0
        self._speech_gate_open = False
        self._preroll_audio_frames.clear()
        self._pending_speech_audio_frames.clear()

    def _publish_error(self, message: str, exc: Exception) -> None:
        logfire.exception(message, error_type=type(exc).__name__, error=str(exc))
        self.error_occurred.emit(message)

    @staticmethod
    def _drain_queue(queue: asyncio.Queue[object]) -> None:
        while True:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                return

    @staticmethod
    async def _put_queue_sentinel(queue: asyncio.Queue[object]) -> None:
        try:
            await asyncio.wait_for(queue.put(None), timeout=0.2)
            return
        except TimeoutError:
            dropped_items = 0
            while True:
                try:
                    queue.get_nowait()
                    dropped_items += 1
                except asyncio.QueueEmpty:
                    break
            if dropped_items:
                logfire.warning(
                    "Dropped queued items while enqueueing shutdown sentinel",
                    dropped_items=dropped_items,
                )

        with contextlib.suppress(asyncio.QueueFull):
            queue.put_nowait(None)

    async def _await_task(self, task: asyncio.Task[None] | None) -> None:
        if task is None or task is self._current_task_or_none():
            return
        try:
            await asyncio.wait_for(task, timeout=2.0)
        except asyncio.CancelledError:
            if task.cancelled():
                return
            raise
        except TimeoutError:
            logfire.warning("Task did not finish before shutdown timeout", task=task.get_name())
        except Exception as exc:
            logfire.warning(
                "Task did not shut down cleanly",
                task=task.get_name(),
                error_type=type(exc).__name__,
                error=str(exc),
            )

    async def _cancel_task(self, task: asyncio.Task[None] | None) -> None:
        if task is None or task is self._current_task_or_none():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            if task.cancelled():
                return
            raise
        except Exception as exc:
            logfire.warning(
                "Task raised while being cancelled",
                task=task.get_name(),
                error_type=type(exc).__name__,
                error=str(exc),
            )

    @staticmethod
    def _current_task_or_none() -> asyncio.Task[object] | None:
        with contextlib.suppress(RuntimeError):
            return asyncio.current_task()
        return None
