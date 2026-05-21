"""DashScope Qwen real-time ASR provider."""

from __future__ import annotations

import base64
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
                    text = data.get("text", "")
                    if text:
                        yield TranscriptEvent(text=text, is_final=False)

                elif event_type == "conversation.item.input_audio_transcription.completed":
                    text = data.get("transcript", "")
                    if text:
                        yield TranscriptEvent(text=text, is_final=True)

        except ConnectionClosed as exc:
            msg = f"ASR WebSocket closed: {exc}"
            raise ASRProviderError(msg) from exc

    async def stop(self) -> None:
        """Signal end-of-stream and close the connection."""
        if self._ws is not None:
            try:
                finish_event = {
                    "event_id": self._generate_event_id(),
                    "type": "session.finish",
                }
                await self._send_json(finish_event)
                await self._ws.close()
            except ConnectionClosed:
                pass
            finally:
                self._ws = None

    async def _send_json(self, data: dict[str, Any]) -> None:
        if self._ws is None:
            msg = "WebSocket not connected"
            raise ASRProviderError(msg)
        await self._ws.send(json.dumps(data))
