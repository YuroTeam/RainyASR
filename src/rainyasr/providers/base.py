"""Abstract base classes for ASR and translation providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass


@dataclass(frozen=True)
class TranscriptEvent:
    """A single transcript event from a real-time ASR stream.

    Attributes:
        text: The recognized text.
        is_final: ``True`` if this is a final (committed) transcript.
            ``False`` for partial (interim) results that may change.
    """

    text: str
    is_final: bool


class ProviderError(Exception):
    """Base exception for all provider-related errors."""


class ASRProviderError(ProviderError):
    """Raised when the real-time ASR provider encounters an error."""


class TranslationProviderError(ProviderError):
    """Raised when the translation provider encounters an error."""


class RealtimeASRProvider(ABC):
    """Abstract base class for real-time speech-to-text providers.

    Implementations are expected to manage a persistent WebSocket or
    streaming connection.  Audio frames are pushed via :meth:`send_audio`;
    transcript events are consumed via the async iterator returned by
    :meth:`events`.
    """

    @abstractmethod
    async def start(self) -> None:
        """Establish the real-time ASR session.

        Raises:
            ASRProviderError: on connection or authentication failure.
        """

    @abstractmethod
    async def send_audio(self, pcm_bytes: bytes) -> None:
        """Send a chunk of PCM16 audio data.

        Args:
            pcm_bytes: Raw PCM16 little-endian audio frame.

        Raises:
            ASRProviderError: if the connection is broken.
        """

    @abstractmethod
    async def events(self) -> AsyncIterator[TranscriptEvent]:
        """Yield transcript events as they arrive from the ASR backend.

        Yields:
            TranscriptEvent with partial or final text.
        """

    @abstractmethod
    async def stop(self) -> None:
        """Signal end-of-stream and close the connection gracefully."""


class TranslationProvider(ABC):
    """Abstract base class for text translation providers."""

    @abstractmethod
    async def translate(
        self,
        text: str,
        target_lang: str = "zh",
        history: list[str] | None = None,
    ) -> str:
        """Translate *text* into *target_lang*.

        Args:
            text: The source text to translate.
            target_lang: Target language code (e.g. ``"zh"``, ``"en"``).
            history: Up to 2 previous source sentences for context.

        Returns:
            The translated text.

        Raises:
            TranslationProviderError: on API or network failure.
        """
