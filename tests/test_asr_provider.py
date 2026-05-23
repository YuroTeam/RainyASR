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
        assert data["session"]["input_audio_transcription"] == {}

    @pytest.mark.asyncio
    async def test_omits_language_when_configured_as_auto(self) -> None:
        provider = QwenRealtimeASRProvider(api_key="test-key", language="auto")
        mock_ws = _MockWebSocket()

        with patch("rainyasr.providers.asr.websockets.connect", AsyncMock(return_value=mock_ws)):
            await provider.start()

        data = json.loads(mock_ws.send.call_args_list[0][0][0])
        assert "language" not in data["session"]["input_audio_transcription"]

    @pytest.mark.asyncio
    async def test_sends_explicit_language_when_configured(self) -> None:
        provider = QwenRealtimeASRProvider(api_key="test-key", language="ZH")
        mock_ws = _MockWebSocket()

        with patch("rainyasr.providers.asr.websockets.connect", AsyncMock(return_value=mock_ws)):
            await provider.start()

        data = json.loads(mock_ws.send.call_args_list[0][0][0])
        assert data["session"]["input_audio_transcription"]["language"] == "zh"

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
                        "item_id": "item-hello",
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
        assert events[0] == TranscriptEvent(text="hello", is_final=False, segment_id="item-hello")

    @pytest.mark.asyncio
    async def test_yields_partial_event_with_stash(self, provider: QwenRealtimeASRProvider) -> None:
        mock_ws = _MockWebSocket(
            [
                json.dumps(
                    {
                        "type": "conversation.item.input_audio_transcription.text",
                        "item_id": "item-draft",
                        "text": "",
                        "stash": "draft",
                    }
                ),
                json.dumps(
                    {
                        "type": "conversation.item.input_audio_transcription.text",
                        "item_id": "item-hello-world",
                        "text": "hello",
                        "stash": " world",
                    }
                ),
            ]
        )
        provider._ws = mock_ws

        events = []
        async for evt in provider.events():
            events.append(evt)

        assert len(events) == 2
        assert events[0] == TranscriptEvent(text="draft", is_final=False, segment_id="item-draft")
        assert events[1] == TranscriptEvent(
            text="hello world", is_final=False, segment_id="item-hello-world"
        )

    @pytest.mark.asyncio
    async def test_yields_final_event(self, provider: QwenRealtimeASRProvider) -> None:
        mock_ws = _MockWebSocket(
            [
                json.dumps(
                    {
                        "type": "conversation.item.input_audio_transcription.completed",
                        "item_id": "item-world",
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
        assert events[0] == TranscriptEvent(text="world", is_final=True, segment_id="item-world")

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
    async def test_sets_session_finished(self, provider: QwenRealtimeASRProvider) -> None:
        mock_ws = _MockWebSocket([json.dumps({"type": "session.finished"})])
        provider._ws = mock_ws

        async for _ in provider.events():
            pass

        assert provider._session_finished.is_set()

    @pytest.mark.asyncio
    async def test_raises_on_server_error(self, provider: QwenRealtimeASRProvider) -> None:
        mock_ws = _MockWebSocket(
            [
                json.dumps(
                    {
                        "type": "error",
                        "error": {
                            "code": "rate_limit_exceeded",
                            "message": "rate limit exceeded",
                        },
                    }
                )
            ]
        )
        provider._ws = mock_ws

        with pytest.raises(ASRProviderError, match="rate limit exceeded"):
            async for _ in provider.events():
                pass

    @pytest.mark.asyncio
    async def test_raises_on_transcription_failed(self, provider: QwenRealtimeASRProvider) -> None:
        mock_ws = _MockWebSocket(
            [
                json.dumps(
                    {
                        "type": "conversation.item.input_audio_transcription.failed",
                        "item_id": "item-noisy",
                        "error": {
                            "code": "invalid_audio",
                            "message": "audio too noisy",
                        },
                    }
                ),
            ]
        )
        provider._ws = mock_ws

        with pytest.raises(ASRProviderError, match="audio too noisy"):
            async for _ in provider.events():
                pass

    @pytest.mark.asyncio
    async def test_raises_when_not_connected(self, provider: QwenRealtimeASRProvider) -> None:
        with pytest.raises(ASRProviderError):
            async for _ in provider.events():
                pass


class TestStop:
    @pytest.mark.asyncio
    async def test_sends_finish_and_closes(self, provider: QwenRealtimeASRProvider) -> None:
        # In real usage events() runs concurrently and sets _session_finished
        # when it sees the server's session.finished message.  Here we simulate
        # that the event has already been set so stop() doesn't wait.
        mock_ws = _MockWebSocket()
        provider._ws = mock_ws
        provider._session_finished.set()

        await provider.stop()

        call_args = mock_ws.send.call_args[0][0]
        data = json.loads(call_args)
        assert data["type"] == "session.finish"
        mock_ws.close.assert_awaited_once()
        assert provider._ws is None

    @pytest.mark.asyncio
    async def test_times_out_when_session_finished_never_arrives(
        self, provider: QwenRealtimeASRProvider
    ) -> None:
        mock_ws = _MockWebSocket()
        provider._ws = mock_ws

        await provider.stop()

        mock_ws.close.assert_awaited_once()
        assert provider._ws is None

    @pytest.mark.asyncio
    async def test_noop_when_not_connected(self, provider: QwenRealtimeASRProvider) -> None:
        await provider.stop()
