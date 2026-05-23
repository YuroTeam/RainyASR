"""Tests for SubtitleWorker."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np
import pytest

from rainyasr.audio.capture import AudioDeviceInfo
from rainyasr.providers.base import RealtimeASRProvider, TranscriptEvent, TranslationProvider
from rainyasr.worker import SubtitleWorker

_END = object()


async def wait_until(predicate: Callable[[], bool], timeout: float = 1.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    assert predicate()


class FakeASRProvider(RealtimeASRProvider):
    def __init__(self) -> None:
        self.started = 0
        self.stopped = 0
        self.sent_audio: list[bytes] = []
        self._events: asyncio.Queue[TranscriptEvent | object] = asyncio.Queue()

    async def start(self) -> None:
        self.started += 1

    async def send_audio(self, pcm_bytes: bytes) -> None:
        self.sent_audio.append(pcm_bytes)

    async def events(self):
        while True:
            item = await self._events.get()
            if item is _END:
                return
            assert isinstance(item, TranscriptEvent)
            yield item

    async def stop(self) -> None:
        self.stopped += 1
        await self._events.put(_END)

    async def emit(self, event: TranscriptEvent) -> None:
        await self._events.put(event)


class BlockingSendASRProvider(FakeASRProvider):
    def __init__(self) -> None:
        super().__init__()
        self.send_started = asyncio.Event()
        self.allow_send = asyncio.Event()

    async def send_audio(self, pcm_bytes: bytes) -> None:
        self.send_started.set()
        await self.allow_send.wait()
        await super().send_audio(pcm_bytes)


class FailingSendASRProvider(FakeASRProvider):
    async def send_audio(self, pcm_bytes: bytes) -> None:
        raise RuntimeError("send failed")


class FailingReconnectASRProvider(FakeASRProvider):
    async def start(self) -> None:
        self.started += 1
        if self.started > 1:
            raise RuntimeError("reconnect failed")


@dataclass(frozen=True)
class TranslationCall:
    text: str
    target_lang: str
    history: tuple[str, ...]


class FakeTranslationProvider(TranslationProvider):
    def __init__(self) -> None:
        self.calls: list[TranslationCall] = []

    async def translate(
        self,
        text: str,
        target_lang: str = "zh",
        history: list[str] | None = None,
    ) -> str:
        self.calls.append(
            TranslationCall(
                text=text,
                target_lang=target_lang,
                history=tuple(history or []),
            )
        )
        return f"{target_lang}:{text}"


class BlockingTranslationProvider(FakeTranslationProvider):
    def __init__(self) -> None:
        super().__init__()
        self.translate_started = asyncio.Event()
        self.allow_translate = asyncio.Event()

    async def translate(
        self,
        text: str,
        target_lang: str = "zh",
        history: list[str] | None = None,
    ) -> str:
        self.calls.append(
            TranslationCall(
                text=text,
                target_lang=target_lang,
                history=tuple(history or []),
            )
        )
        self.translate_started.set()
        await self.allow_translate.wait()
        return f"{target_lang}:{text}"


class FakeAudioDeviceDetector:
    def find_loopback_device(self) -> AudioDeviceInfo:
        return AudioDeviceInfo(
            device_id=7,
            name="Fake Loopback",
            sample_rate=16000,
            channels=2,
        )


class FakeInputStream:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.callback = kwargs["callback"]
        self.started = 0
        self.stopped = 0
        self.closed = 0

    def start(self) -> None:
        self.started += 1

    def stop(self) -> None:
        self.stopped += 1

    def close(self) -> None:
        self.closed += 1


class FakeInputStreamFactory:
    def __init__(self) -> None:
        self.stream: FakeInputStream | None = None

    def __call__(self, **kwargs: Any) -> FakeInputStream:
        self.stream = FakeInputStream(**kwargs)
        return self.stream


def make_worker(
    *,
    asr: FakeASRProvider | None = None,
    translator: FakeTranslationProvider | None = None,
    **kwargs: Any,
) -> tuple[SubtitleWorker, FakeASRProvider, FakeTranslationProvider]:
    fake_asr = asr or FakeASRProvider()
    fake_translator = translator or FakeTranslationProvider()
    worker = SubtitleWorker(
        asr_provider=fake_asr,
        translation_provider=fake_translator,
        **kwargs,
    )
    return worker, fake_asr, fake_translator


def test_shutdown_task_timeout_must_be_positive(qapp) -> None:
    with pytest.raises(ValueError, match="shutdown_task_timeout_s must be positive"):
        make_worker(shutdown_task_timeout_s=0)


@pytest.mark.asyncio
async def test_partial_updates_subtitle_without_translation(qapp) -> None:
    worker, asr, translator = make_worker()
    updates: list[tuple[str, str, bool]] = []
    worker.subtitle_changed.connect(
        lambda original, translated, partial: updates.append((original, translated, partial))
    )

    await worker.start(capture_audio=False)
    await asr.emit(TranscriptEvent(text="hel", is_final=False, segment_id="seg-1"))

    await wait_until(lambda: updates == [("hel", "", True)])
    assert translator.calls == []

    await worker.stop()


@pytest.mark.asyncio
async def test_partial_transcripts_are_translated_before_final(qapp) -> None:
    worker, asr, translator = make_worker(partial_translation_interval_ms=10)
    updates: list[tuple[str, str, bool]] = []
    worker.subtitle_changed.connect(
        lambda original, translated, partial: updates.append((original, translated, partial))
    )

    await worker.start(capture_audio=False)
    await asr.emit(TranscriptEvent(text="hello world", is_final=False, segment_id="seg-1"))

    await wait_until(lambda: len(translator.calls) == 1)
    assert translator.calls[0] == TranslationCall("hello world", "zh", ())
    await wait_until(lambda: ("hello world", "zh:hello world", True) in updates)

    await worker.stop()


@pytest.mark.asyncio
async def test_partial_translation_uses_latest_text_after_throttle(qapp) -> None:
    worker, asr, translator = make_worker(partial_translation_interval_ms=30)

    await worker.start(capture_audio=False)
    await asr.emit(TranscriptEvent(text="hello", is_final=False, segment_id="seg-1"))
    await asr.emit(TranscriptEvent(text="hello world", is_final=False, segment_id="seg-1"))

    await wait_until(lambda: len(translator.calls) == 1)
    assert translator.calls[0].text == "hello world"

    await worker.stop()


@pytest.mark.asyncio
async def test_stale_partial_translation_result_is_not_emitted(qapp) -> None:
    translator = BlockingTranslationProvider()
    worker, asr, translator = make_worker(
        translator=translator,
        partial_translation_interval_ms=10,
    )
    updates: list[tuple[str, str, bool]] = []
    worker.subtitle_changed.connect(
        lambda original, translated, partial: updates.append((original, translated, partial))
    )

    await worker.start(capture_audio=False)
    await asr.emit(TranscriptEvent(text="hello", is_final=False, segment_id="seg-1"))
    await wait_until(lambda: translator.translate_started.is_set())

    await asr.emit(TranscriptEvent(text="hello world", is_final=False, segment_id="seg-1"))
    translator.allow_translate.set()

    await wait_until(lambda: len(translator.calls) == 2)
    await wait_until(lambda: ("hello world", "zh:hello world", True) in updates)
    assert ("hello", "zh:hello", True) not in updates

    await worker.stop()


@pytest.mark.asyncio
async def test_final_transcripts_are_translated_with_previous_history(qapp) -> None:
    worker, asr, translator = make_worker(target_lang="ja")
    updates: list[tuple[str, str, bool]] = []
    worker.subtitle_changed.connect(
        lambda original, translated, partial: updates.append((original, translated, partial))
    )

    await worker.start(capture_audio=False)
    await asr.emit(TranscriptEvent(text="hello", is_final=True, segment_id="seg-1"))
    await asr.emit(TranscriptEvent(text="world", is_final=True, segment_id="seg-2"))

    await wait_until(lambda: len(translator.calls) == 2)
    assert translator.calls[0] == TranslationCall("hello", "ja", ())
    assert translator.calls[1] == TranslationCall("world", "ja", ("hello",))
    assert ("hello", "ja:hello", False) in updates
    assert ("world", "ja:world", False) in updates

    await worker.stop()


@pytest.mark.asyncio
async def test_translation_history_keeps_only_two_previous_sentences(qapp) -> None:
    worker, asr, translator = make_worker(translation_queue_max_items=10)

    await worker.start(capture_audio=False)
    await asr.emit(TranscriptEvent(text="one", is_final=True, segment_id="seg-1"))
    await asr.emit(TranscriptEvent(text="two", is_final=True, segment_id="seg-2"))
    await asr.emit(TranscriptEvent(text="three", is_final=True, segment_id="seg-3"))
    await asr.emit(TranscriptEvent(text="four", is_final=True, segment_id="seg-4"))

    await wait_until(lambda: len(translator.calls) == 4)
    assert translator.calls[2].history == ("one", "two")
    assert translator.calls[3].history == ("two", "three")

    await worker.stop()


@pytest.mark.asyncio
async def test_translation_queue_backpressure_drops_stale_final_transcripts(qapp) -> None:
    translator = BlockingTranslationProvider()
    worker, asr, translator = make_worker(
        translator=translator,
        translation_queue_max_items=1,
    )

    await worker.start(capture_audio=False)
    await asr.emit(TranscriptEvent(text="one", is_final=True, segment_id="seg-1"))
    await wait_until(lambda: translator.translate_started.is_set())

    await asr.emit(TranscriptEvent(text="two", is_final=True, segment_id="seg-2"))
    await asr.emit(TranscriptEvent(text="three", is_final=True, segment_id="seg-3"))

    await wait_until(lambda: worker.dropped_translation_items == 1)
    translator.allow_translate.set()

    await wait_until(lambda: [call.text for call in translator.calls] == ["one", "three"])

    await worker.stop()


@pytest.mark.asyncio
async def test_duplicate_final_segment_id_is_not_translated_twice(qapp) -> None:
    worker, asr, translator = make_worker()

    await worker.start(capture_audio=False)
    await asr.emit(TranscriptEvent(text="hello", is_final=True, segment_id="seg-1"))
    await wait_until(lambda: len(translator.calls) == 1)

    await asr.emit(TranscriptEvent(text="hello again", is_final=True, segment_id="seg-1"))
    await asyncio.sleep(0.05)

    assert len(translator.calls) == 1
    assert translator.calls[0].text == "hello"

    await worker.stop()


@pytest.mark.asyncio
async def test_duplicate_final_without_segment_id_uses_text_dedupe(qapp) -> None:
    worker, asr, translator = make_worker()

    await worker.start(capture_audio=False)
    await asr.emit(TranscriptEvent(text="same text", is_final=True))
    await wait_until(lambda: len(translator.calls) == 1)

    await asr.emit(TranscriptEvent(text="  same   text  ", is_final=True))
    await asyncio.sleep(0.05)

    assert len(translator.calls) == 1

    await worker.stop()


@pytest.mark.asyncio
async def test_audio_stream_callback_sends_pcm16_bytes(qapp) -> None:
    stream_factory = FakeInputStreamFactory()
    worker, asr, _translator = make_worker(
        audio_device_detector=FakeAudioDeviceDetector(),
        audio_stream_factory=stream_factory,
        sample_rate=16000,
        channels=2,
        frame_ms=100,
        enable_silence_gate=False,
    )

    await worker.start(capture_audio=True)
    assert stream_factory.stream is not None
    assert stream_factory.stream.started == 1
    assert stream_factory.stream.kwargs["device"] == 7
    assert stream_factory.stream.kwargs["samplerate"] == 16000
    assert stream_factory.stream.kwargs["blocksize"] == 1600

    frame = np.array([[0.25, 0.25], [0.5, 0.5]], dtype=np.float32)
    stream_factory.stream.callback(frame, 2, None, None)

    await wait_until(lambda: len(asr.sent_audio) == 1)
    assert len(asr.sent_audio[0]) == 4

    await worker.stop()
    assert stream_factory.stream.stopped == 1
    assert stream_factory.stream.closed == 1


def test_audio_queue_backpressure_drops_oldest_frame(qapp) -> None:
    worker, _asr, _translator = make_worker(
        audio_queue_max_frames=1,
        backpressure_reconnect_threshold=0,
    )

    worker._audio_queue.put_nowait(b"old")
    worker._enqueue_audio_bytes(b"new")

    assert worker.dropped_audio_frames == 1
    assert worker._audio_queue.get_nowait().audio_blocks == (b"new",)


@pytest.mark.asyncio
async def test_audio_queue_backpressure_reconnects_active_asr_session(qapp) -> None:
    asr = BlockingSendASRProvider()
    worker, asr, _translator = make_worker(
        asr=asr,
        audio_queue_max_frames=1,
        backpressure_reconnect_threshold=2,
    )

    await worker.start(capture_audio=False)
    assert asr.started == 1

    worker._enqueue_audio_bytes(b"blocked-send")
    await wait_until(lambda: asr.send_started.is_set())

    worker._enqueue_audio_bytes(b"queued-1")
    worker._enqueue_audio_bytes(b"queued-2")
    worker._enqueue_audio_bytes(b"queued-3")

    assert worker.dropped_audio_frames == 2

    asr.allow_send.set()
    await wait_until(lambda: asr.started == 2 and asr.stopped == 1)

    await worker.stop()


@pytest.mark.asyncio
async def test_audio_sender_failure_stops_worker_from_own_task(qapp) -> None:
    asr = FailingSendASRProvider()
    worker, asr, _translator = make_worker(asr=asr)
    errors: list[str] = []
    states: list[str] = []
    worker.error_occurred.connect(errors.append)
    worker.state_changed.connect(states.append)

    await worker.start(capture_audio=False)
    worker._enqueue_audio_bytes(b"bad-frame")

    await wait_until(lambda: not worker.is_running and bool(errors))

    assert errors == ["Audio sender failed: send failed"]
    assert states[-1] == "stopped"
    assert asr.stopped == 1


@pytest.mark.asyncio
async def test_backpressure_reconnect_failure_stops_worker(qapp) -> None:
    asr = FailingReconnectASRProvider()
    worker, asr, _translator = make_worker(
        asr=asr,
        backpressure_reconnect_threshold=1,
    )
    errors: list[str] = []
    worker.error_occurred.connect(errors.append)

    await worker.start(capture_audio=False)
    worker._consecutive_audio_drops = 1
    worker._request_asr_reconnect_if_needed()

    await wait_until(lambda: not worker.is_running and bool(errors))

    assert errors == ["ASR reconnect failed: reconnect failed"]
    assert asr.started == 2
    assert asr.stopped >= 1


@pytest.mark.asyncio
async def test_audio_capture_delays_asr_until_local_speech(qapp) -> None:
    stream_factory = FakeInputStreamFactory()
    worker, asr, _translator = make_worker(
        audio_device_detector=FakeAudioDeviceDetector(),
        audio_stream_factory=stream_factory,
        speech_start_frames=1,
    )

    await worker.start(capture_audio=True)
    assert asr.started == 0
    assert not worker.is_asr_session_active
    assert stream_factory.stream is not None

    silence = np.zeros((1600, 1), dtype=np.float32)
    stream_factory.stream.callback(silence, 1600, None, None)
    await asyncio.sleep(0.05)

    assert asr.started == 0
    assert asr.sent_audio == []

    speech = np.full((1600, 1), 0.1, dtype=np.float32)
    stream_factory.stream.callback(speech, 1600, None, None)

    await wait_until(lambda: asr.started == 1 and len(asr.sent_audio) >= 1)
    assert worker.is_asr_session_active

    await worker.stop()


@pytest.mark.asyncio
async def test_speech_start_sends_preroll_before_current_frame(qapp) -> None:
    worker, asr, _translator = make_worker(
        audio_device_detector=FakeAudioDeviceDetector(),
        audio_stream_factory=FakeInputStreamFactory(),
        frame_ms=100,
        preroll_ms=300,
        speech_start_frames=2,
    )

    await worker.start(capture_audio=True)
    worker._enqueue_audio_frame(b"silence", rms=0.0)
    worker._enqueue_audio_frame(b"active-1", rms=0.1)
    await asyncio.sleep(0.05)

    assert asr.started == 0
    assert asr.sent_audio == []

    worker._enqueue_audio_frame(b"active-2", rms=0.1)

    await wait_until(lambda: asr.sent_audio[:3] == [b"silence", b"active-1", b"active-2"])
    assert asr.started == 1

    await worker.stop()


@pytest.mark.asyncio
async def test_speech_start_frames_are_sent_when_preroll_is_short(qapp) -> None:
    worker, asr, _translator = make_worker(
        audio_device_detector=FakeAudioDeviceDetector(),
        audio_stream_factory=FakeInputStreamFactory(),
        frame_ms=100,
        preroll_ms=0,
        speech_start_frames=3,
    )

    await worker.start(capture_audio=True)
    worker._enqueue_audio_frame(b"active-1", rms=0.1)
    worker._enqueue_audio_frame(b"active-2", rms=0.1)
    await asyncio.sleep(0.05)

    assert asr.sent_audio == []

    worker._enqueue_audio_frame(b"active-3", rms=0.1)

    await wait_until(lambda: asr.sent_audio[:3] == [b"active-1", b"active-2", b"active-3"])

    await worker.stop()


@pytest.mark.asyncio
async def test_local_silence_after_speech_stops_asr_session(qapp) -> None:
    worker, asr, _translator = make_worker(
        audio_device_detector=FakeAudioDeviceDetector(),
        audio_stream_factory=FakeInputStreamFactory(),
        frame_ms=100,
        preroll_ms=0,
        speech_start_frames=1,
        silence_stop_ms=200,
    )

    await worker.start(capture_audio=True)
    worker._enqueue_audio_frame(b"speech", rms=0.1)

    await wait_until(lambda: asr.started == 1 and worker.is_asr_session_active)
    worker._enqueue_audio_frame(b"silence-1", rms=0.0)
    worker._enqueue_audio_frame(b"silence-2", rms=0.0)

    await wait_until(lambda: asr.stopped == 1 and not worker.is_asr_session_active)
    assert asr.sent_audio == [b"speech", b"silence-1", b"silence-2"]

    await worker.stop()


@pytest.mark.asyncio
async def test_disabled_silence_gate_starts_asr_immediately_for_audio_capture(qapp) -> None:
    stream_factory = FakeInputStreamFactory()
    worker, asr, _translator = make_worker(
        audio_device_detector=FakeAudioDeviceDetector(),
        audio_stream_factory=stream_factory,
        enable_silence_gate=False,
    )

    await worker.start(capture_audio=True)

    assert asr.started == 1
    assert worker.is_asr_session_active

    await worker.stop()


@pytest.mark.asyncio
async def test_stop_is_idempotent(qapp) -> None:
    stream_factory = FakeInputStreamFactory()
    worker, asr, _translator = make_worker(
        audio_device_detector=FakeAudioDeviceDetector(),
        audio_stream_factory=stream_factory,
        enable_silence_gate=False,
    )

    await worker.start(capture_audio=True)
    await worker.stop()
    await worker.stop()

    assert asr.stopped == 1
    assert stream_factory.stream is not None
    assert stream_factory.stream.stopped == 1
    assert stream_factory.stream.closed == 1
