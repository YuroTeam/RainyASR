"""DashScope Qwen real-time ASR provider."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosed, InvalidStatus

from rainyasr.providers.base import (
    ASRProviderError,
    RealtimeASRProvider,
    TranscriptEvent,
)

_DASHSCOPE_WS_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"

# Maximum time to wait for the server to send session.finished after we
# emit session.finish.
_SESSION_FINISH_TIMEOUT = 2.0  # seconds


class QwenRealtimeASRProvider(RealtimeASRProvider):
    """Realtime ASR via DashScope Qwen WebSocket API.

    Uses the OpenAI-compatible realtime protocol:
    - ``session.update`` to configure the session
    - ``input_audio_buffer.append`` to stream audio (base64 PCM16)
    - ``session.finish`` to signal end-of-stream

    Server returns ``conversation.item.input_audio_transcription.*`` events
    containing partial and final transcripts.
    """

    def __init__(
        self,
        api_key: str,
        *,
        model: str = "qwen3-asr-flash-realtime",
        sample_rate: int = 16000,
        language: str = "auto",
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._sample_rate = sample_rate
        self._language = language
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._closing = False
        self._session_finished = asyncio.Event()

    @staticmethod
    def _generate_event_id() -> str:
        return "event_" + uuid.uuid4().hex

    async def start(self) -> None:
        """Establish the WebSocket session and send initial configuration."""
        url = f"{_DASHSCOPE_WS_URL}?model={self._model}"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "OpenAI-Beta": "realtime=v1",
        }
        try:
            self._ws = await websockets.connect(url, additional_headers=headers)
        except InvalidStatus as exc:
            msg = f"ASR connection rejected: {exc}"
            raise ASRProviderError(msg) from exc
        except Exception as exc:
            msg = f"ASR connection failed: {exc}"
            raise ASRProviderError(msg) from exc

        session_update: dict[str, Any] = {
            "event_id": self._generate_event_id(),
            "type": "session.update",
            "session": {
                "modalities": ["text"],
                "input_audio_format": "pcm",
                "sample_rate": self._sample_rate,
                "input_audio_transcription": {
                    "language": self._language,
                },
                "turn_detection": {
                    "type": "server_vad",
                    "threshold": 0.5,
                    "prefix_padding_ms": 300,
                    "silence_duration_ms": 500,
                },
            },
        }
        await self._send_json(session_update)

    async def send_audio(self, pcm_bytes: bytes) -> None:
        """Send a chunk of PCM16 audio data (base64-encoded)."""
        if self._ws is None:
            msg = "WebSocket not connected. Call start() first."
            raise ASRProviderError(msg)

        event = {
            "event_id": self._generate_event_id(),
            "type": "input_audio_buffer.append",
            "audio": base64.b64encode(pcm_bytes).decode("ascii"),
        }
        await self._send_json(event)

    async def events(self) -> AsyncIterator[TranscriptEvent]:
        """Yield transcript events as they arrive from the ASR backend."""
        if self._ws is None:
            msg = "WebSocket not connected. Call start() first."
            raise ASRProviderError(msg)

        try:
            async for raw in self._ws:
                if isinstance(raw, bytes):
                    continue
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                event_type = data.get("type", "")

                if event_type == "conversation.item.input_audio_transcription.text":
                    # Realtime preview: concatenate committed text with draft stash.
                    text = data.get("text", "")
                    stash = data.get("stash", "")
                    combined = text + stash
                    if combined:
                        yield TranscriptEvent(text=combined, is_final=False)

                elif event_type == "conversation.item.input_audio_transcription.completed":
                    text = data.get("transcript", "")
                    if text:
                        yield TranscriptEvent(text=text, is_final=True)

                elif event_type == "error":
                    msg = data.get("message", "Unknown ASR server error")
                    raise ASRProviderError(f"ASR server error: {msg}")

                elif event_type == "conversation.item.input_audio_transcription.failed":
                    msg = data.get("message", "Transcription failed")
                    raise ASRProviderError(f"ASR transcription failed: {msg}")

                elif event_type == "session.finished":
                    self._session_finished.set()

        except ConnectionClosed as exc:
            if not self._closing:
                msg = f"ASR WebSocket closed unexpectedly: {exc}"
                raise ASRProviderError(msg) from exc

    async def stop(self) -> None:
        """Signal end-of-stream and close the connection gracefully.

        This method should be called while :meth:`events` is still running in
        the background so that the ``session.finished`` acknowledgement can be
        consumed.  If ``events()`` is not running the call will time out after
        :data:`_SESSION_FINISH_TIMEOUT` seconds and force-close the socket.
        """
        if self._ws is None:
            return

        ws = self._ws
        self._closing = True

        try:
            try:
                finish_event = {
                    "event_id": self._generate_event_id(),
                    "type": "session.finish",
                }
                await self._send_json(finish_event)
            except ASRProviderError:
                # Connection already gone; nothing to do.
                pass

            with contextlib.suppress(TimeoutError):
                # Wait for the server to acknowledge session completion.
                await asyncio.wait_for(
                    self._session_finished.wait(), timeout=_SESSION_FINISH_TIMEOUT
                )
        finally:
            with contextlib.suppress(Exception):
                await ws.close()
            self._ws = None
            self._closing = False
            self._session_finished.clear()

    async def _send_json(self, data: dict[str, Any]) -> None:
        if self._ws is None:
            msg = "WebSocket not connected"
            raise ASRProviderError(msg)
        try:
            await self._ws.send(json.dumps(data))
        except ConnectionClosed as exc:
            msg = f"ASR WebSocket closed during send: {exc}"
            raise ASRProviderError(msg) from exc
