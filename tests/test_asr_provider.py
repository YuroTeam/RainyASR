"""Mock tests for QwenRealtimeASRProvider.

End-to-end tests require a real API key and network; these tests verify
JSON protocol formatting and event parsing with a mocked WebSocket.
"""

from __future__ import annotations

import base64
import json
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, patch

import pytest

from rainyasr.providers.asr import QwenRealtimeASRProvider
from rainyasr.providers.base import ASRProviderError, TranscriptEvent


class _MockWebSocket:
    """A mock WebSocket that supports ``async for``, ``send``, and ``close``."""

    def __init__(self, messages: list[str] | None = None) -> None:
        self.messages = messages or []
        self.send = AsyncMock()
        self.close = AsyncMock()

    def __aiter__(self) -> AsyncIterator[str]:
        self._iter = iter(self.messages)
        return self

    async def __anext__(self) -> str:
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration from None


@pytest.fixture
def provider() -> QwenRealtimeASRProvider:
    return QwenRealtimeASRProvider(api_key="test-key")


class TestStart:
    @pytest.mark.asyncio
    async def test_sends_session_update_on_connect(self, provider: QwenRealtimeASRProvider) -> None:
        mock_ws = _MockWebSocket()

        with patch("rainyasr.providers.asr.websockets.connect", AsyncMock(return_value=mock_ws)):
            await provider.start()

        assert provider._ws is mock_ws
        call_args = mock_ws.send.call_args_list[0][0][0]
        data = json.loads(call_args)
        assert data["type"] == "session.update"
        assert data["session"]["modalities"] == ["text"]
        assert data["session"]["input_audio_format"] == "pcm"
        assert data["session"]["sample_rate"] == 16000

    @pytest.mark.asyncio
    async def test_raises_asr_error_on_connection_failure(
        self, provider: QwenRealtimeASRProvider
    ) -> None:
        from websockets import http11
        from websockets.exceptions import InvalidStatus

        headers = http11.Headers([("Content-Type", "text/plain")])
        response = http11.Response(
            status_code=401, reason_phrase="Unauthorized", headers=headers, body=b""
        )

        with (
            patch(
                "rainyasr.providers.asr.websockets.connect",
                AsyncMock(side_effect=InvalidStatus(response)),
            ),
            pytest.raises(ASRProviderError),
        ):
            await provider.start()


class TestSendAudio:
    @pytest.mark.asyncio
    async def test_encodes_pcm_as_base64(self, provider: QwenRealtimeASRProvider) -> None:
        mock_ws = _MockWebSocket()
        provider._ws = mock_ws

        pcm = b"\x01\x02\x03\x04"
        await provider.send_audio(pcm)

        call_args = mock_ws.send.call_args[0][0]
        data = json.loads(call_args)
        assert data["type"] == "input_audio_buffer.append"
        assert base64.b64decode(data["audio"]) == pcm

    @pytest.mark.asyncio
    async def test_raises_when_not_connected(self, provider: QwenRealtimeASRProvider) -> None:
        with pytest.raises(ASRProviderError):
            await provider.send_audio(b"test")


class TestEvents:
    @pytest.mark.asyncio
    async def test_yields_partial_event(self, provider: QwenRealtimeASRProvider) -> None:
        mock_ws = _MockWebSocket(
            [
                json.dumps(
                    {
                        "type": "conversation.item.input_audio_transcription.text",
                        "text": "hello",
                    }
                ),
            ]
        )
        provider._ws = mock_ws

        events = []
        async for evt in provider.events():
            events.append(evt)

        assert len(events) == 1
        assert events[0] == TranscriptEvent(text="hello", is_final=False)

    @pytest.mark.asyncio
    async def test_yields_final_event(self, provider: QwenRealtimeASRProvider) -> None:
        mock_ws = _MockWebSocket(
            [
                json.dumps(
                    {
                        "type": "conversation.item.input_audio_transcription.completed",
                        "transcript": "world",
                    }
                ),
            ]
        )
        provider._ws = mock_ws

        events = []
        async for evt in provider.events():
            events.append(evt)

        assert len(events) == 1
        assert events[0] == TranscriptEvent(text="world", is_final=True)

    @pytest.mark.asyncio
    async def test_ignores_unknown_event_types(self, provider: QwenRealtimeASRProvider) -> None:
        mock_ws = _MockWebSocket(
            [
                json.dumps({"type": "input_audio_buffer.speech_started"}),
                json.dumps(
                    {
                        "type": "conversation.item.input_audio_transcription.completed",
                        "transcript": "only",
                    }
                ),
            ]
        )
        provider._ws = mock_ws

        events = []
        async for evt in provider.events():
            events.append(evt)

        assert len(events) == 1
        assert events[0].text == "only"

    @pytest.mark.asyncio
    async def test_raises_when_not_connected(self, provider: QwenRealtimeASRProvider) -> None:
        with pytest.raises(ASRProviderError):
            async for _ in provider.events():
                pass


class TestStop:
    @pytest.mark.asyncio
    async def test_sends_finish_and_closes(self, provider: QwenRealtimeASRProvider) -> None:
        mock_ws = _MockWebSocket()
        provider._ws = mock_ws

        await provider.stop()

        call_args = mock_ws.send.call_args[0][0]
        data = json.loads(call_args)
        assert data["type"] == "session.finish"
        mock_ws.close.assert_awaited_once()
        assert provider._ws is None

    @pytest.mark.asyncio
    async def test_noop_when_not_connected(self, provider: QwenRealtimeASRProvider) -> None:
        await provider.stop()
