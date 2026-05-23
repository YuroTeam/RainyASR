"""Manual validation for SubtitleWorker.

Examples:
    uv run python scripts/test_worker.py --fake
    DASHSCOPE_API_KEY=... uv run python scripts/test_worker.py --real
    uv run python scripts/test_worker.py --real --silence-rms-threshold 0.003
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import AsyncIterator

import qasync
from PySide6.QtWidgets import QApplication

from rainyasr.config import AppConfig, EnvConfig
from rainyasr.gui.subtitle_window import SubtitleWindow, configure_macos_overlay_app
from rainyasr.providers import OpenAICompatibleTranslationProvider, QwenRealtimeASRProvider
from rainyasr.providers.base import RealtimeASRProvider, TranscriptEvent, TranslationProvider
from rainyasr.worker import SubtitleWorker

_END = object()


class FakeASRProvider(RealtimeASRProvider):
    def __init__(self) -> None:
        self._events: asyncio.Queue[TranscriptEvent | object] = asyncio.Queue()

    async def start(self) -> None:
        print("[fake-asr] started")

    async def send_audio(self, pcm_bytes: bytes) -> None:
        _ = pcm_bytes

    async def events(self) -> AsyncIterator[TranscriptEvent]:
        while True:
            item = await self._events.get()
            if item is _END:
                return
            assert isinstance(item, TranscriptEvent)
            yield item

    async def stop(self) -> None:
        await self._events.put(_END)
        print("[fake-asr] stopped")

    async def emit(self, event: TranscriptEvent) -> None:
        await self._events.put(event)


class FakeTranslationProvider(TranslationProvider):
    async def translate(
        self,
        text: str,
        target_lang: str = "zh",
        history: list[str] | None = None,
    ) -> str:
        _ = history
        await asyncio.sleep(0.2)
        return f"{target_lang}: {text}"


def connect_terminal_logging(worker: SubtitleWorker) -> None:
    def print_subtitle(original: str, translated: str, is_partial: bool) -> None:
        tag = "PARTIAL" if is_partial else "FINAL"
        print(f"[{tag}] {original}")
        if translated:
            print(f"[TRANS] {translated}")

    worker.subtitle_changed.connect(print_subtitle)
    worker.error_occurred.connect(lambda message: print(f"[ERROR] {message}", file=sys.stderr))
    worker.state_changed.connect(lambda state: print(f"[state] {state}"))


async def drive_fake_asr(asr: FakeASRProvider) -> None:
    events = [
        TranscriptEvent("Hel", is_final=False, segment_id="seg-1"),
        TranscriptEvent("Hello wor", is_final=False, segment_id="seg-1"),
        TranscriptEvent("Hello world", is_final=True, segment_id="seg-1"),
        TranscriptEvent("This is task twelve", is_final=False, segment_id="seg-2"),
        TranscriptEvent("This is task twelve", is_final=True, segment_id="seg-2"),
    ]
    for event in events:
        await asr.emit(event)
        await asyncio.sleep(0.8)
    await asyncio.sleep(1.5)


async def run_fake(app: QApplication) -> None:
    window = SubtitleWindow()
    asr = FakeASRProvider()
    translator = FakeTranslationProvider()

    worker = SubtitleWorker(
        asr_provider=asr,
        translation_provider=translator,
        subtitle_window=window,
    )
    connect_terminal_logging(worker)

    window.show()
    await worker.start(capture_audio=False)
    try:
        await drive_fake_asr(asr)
    finally:
        await worker.stop()
        window.close()
        app.quit()


async def run_real(args: argparse.Namespace) -> None:
    dashscope_key = EnvConfig.dashscope_api_key()
    if not dashscope_key:
        print("[ERROR] DASHSCOPE_API_KEY is not set.", file=sys.stderr)
        raise SystemExit(1)

    config = AppConfig.load()
    translation_model = EnvConfig.translate_model()
    translation_key = EnvConfig.translate_api_key()
    if not translation_key and OpenAICompatibleTranslationProvider.is_qwen_model(translation_model):
        translation_key = dashscope_key
    if not translation_key:
        translation_key = EnvConfig.deepseek_api_key()
    if not translation_key:
        print("[ERROR] translation API key is not set.", file=sys.stderr)
        raise SystemExit(1)

    translation_base_url = EnvConfig.translate_base_url()
    if not translation_base_url and OpenAICompatibleTranslationProvider.is_qwen_model(
        translation_model
    ):
        translation_base_url = EnvConfig.dashscope_compatible_base_url()
    if not translation_base_url:
        translation_base_url = EnvConfig.deepseek_base_url()

    silence_rms_threshold = (
        args.silence_rms_threshold
        if args.silence_rms_threshold is not None
        else config.audio.silence_rms_threshold
    )

    window = SubtitleWindow(config.subtitle)
    asr = QwenRealtimeASRProvider(
        api_key=dashscope_key,
        model=config.asr.asr_model,
        sample_rate=config.audio.sample_rate,
        language=config.asr.asr_language,
    )
    translator = OpenAICompatibleTranslationProvider(
        api_key=translation_key,
        base_url=translation_base_url,
        model=translation_model,
    )
    worker = SubtitleWorker(
        asr_provider=asr,
        translation_provider=translator,
        subtitle_window=window,
        target_lang=config.language.target_lang,
        sample_rate=config.audio.sample_rate,
        channels=config.audio.channels,
        frame_ms=config.audio.frame_ms,
        audio_queue_max_frames=config.audio.audio_queue_max_frames,
        enable_silence_gate=not args.disable_silence_gate,
        silence_rms_threshold=silence_rms_threshold,
        speech_start_frames=args.speech_start_frames,
        silence_stop_ms=args.silence_stop_ms,
        preroll_ms=args.preroll_ms,
    )
    connect_terminal_logging(worker)

    window.show()
    await worker.start(capture_audio=True)
    print(
        "[real] Worker running. "
        f"silence_gate={not args.disable_silence_gate}, "
        f"threshold={silence_rms_threshold}, "
        f"silence_stop_ms={args.silence_stop_ms}, "
        f"preroll_ms={args.preroll_ms}. "
        "Press Ctrl+C to stop."
    )
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await worker.stop()
        window.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate SubtitleWorker")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--fake",
        action="store_true",
        help="Run without real audio or API keys (default when no mode is provided)",
    )
    mode.add_argument("--real", action="store_true", help="Run real loopback audio + API providers")
    parser.add_argument(
        "--disable-silence-gate",
        action="store_true",
        help="Keep the ASR session open immediately instead of using local silence gating",
    )
    parser.add_argument(
        "--silence-rms-threshold",
        type=float,
        default=None,
        help="Local RMS threshold that opens the ASR gate",
    )
    parser.add_argument(
        "--speech-start-frames",
        type=int,
        default=2,
        help="Consecutive active frames needed before opening the ASR gate",
    )
    parser.add_argument(
        "--silence-stop-ms",
        type=int,
        default=3000,
        help="Continuous local silence before closing the ASR session",
    )
    parser.add_argument(
        "--preroll-ms",
        type=int,
        default=500,
        help="Buffered audio sent before the first active frame",
    )
    args = parser.parse_args()
    if not args.fake and not args.real:
        args.fake = True
    return args


def main() -> None:
    args = parse_args()
    app = QApplication.instance() or QApplication(sys.argv)
    configure_macos_overlay_app()
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    try:
        with loop:
            if args.fake:
                print(
                    "[fake] Running simulated providers. Use --real for live audio/API validation."
                )
            task = run_real(args) if args.real else run_fake(app)
            loop.run_until_complete(task)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        asyncio.set_event_loop(None)


if __name__ == "__main__":
    main()
