"""OpenAI-compatible translation providers."""

from __future__ import annotations

import asyncio

from openai import APIError, APITimeoutError, AsyncOpenAI
from openai.types.chat import ChatCompletion

from rainyasr.providers.base import TranslationProvider, TranslationProviderError

_DEFAULT_COMPATIBLE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
_DEFAULT_MODEL = "qwen-mt-flash"
_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
_DEEPSEEK_MODEL = "deepseek-chat"
_MAX_RETRIES = 2  # total attempts = retries + 1
_BACKOFF_BASE = 1.0  # seconds
_TIMEOUT = 20.0  # seconds per attempt

_LANGUAGE_NAMES = {
    "zh": "Chinese",
    "en": "English",
    "ja": "Japanese",
    "ko": "Korean",
    "fr": "French",
    "de": "German",
    "es": "Spanish",
    "ru": "Russian",
}


class OpenAICompatibleTranslationProvider(TranslationProvider):
    """Translate text via an OpenAI-compatible chat completions API.

    Supports context-aware translation by injecting up to 2 previous source
    sentences into the system prompt for general chat models.

    Qwen-MT models are handled as dedicated translation models: the request
    passes DashScope ``translation_options`` via ``extra_body`` instead of
    prompt-engineering the task.
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str | None = None,
        model: str | None = None,
    ) -> None:
        self._model = model or _DEFAULT_MODEL
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url or _DEFAULT_COMPATIBLE_BASE_URL,
        )

    @staticmethod
    def is_qwen_mt_model(model: str | None) -> bool:
        """Return whether *model* is a Qwen machine-translation model."""
        return (model or "").lower().startswith("qwen-mt-")

    @staticmethod
    def is_qwen_model(model: str | None) -> bool:
        """Return whether *model* should default to DashScope compatible mode."""
        return (model or "").lower().startswith("qwen")

    @staticmethod
    def _build_system_prompt(target_lang: str, history: list[str] | None) -> str:
        lines: list[str] = [
            f"You are a professional translator. Translate the user sentence into {target_lang}.",
            "Respond with ONLY the translated text. No explanations, no prefixes, no quotes.",
        ]

        if history:
            lines.append("")
            lines.append("Historical context (for reference only, do not re-translate):")
            for h in history[-2:]:
                lines.append(f"- {h}")

        return "\n".join(lines)

    @staticmethod
    def _target_language_name(target_lang: str) -> str:
        return _LANGUAGE_NAMES.get(target_lang, target_lang)

    @classmethod
    def _build_qwen_mt_extra_body(cls, target_lang: str) -> dict[str, dict[str, str]]:
        return {
            "translation_options": {
                "source_lang": "auto",
                "target_lang": cls._target_language_name(target_lang),
            }
        }

    def _build_request_kwargs(
        self,
        text: str,
        target_lang: str,
        history: list[str] | None,
    ) -> dict[str, object]:
        if self.is_qwen_mt_model(self._model):
            return {
                "model": self._model,
                "messages": [{"role": "user", "content": text.strip()}],
                "extra_body": self._build_qwen_mt_extra_body(target_lang),
                "timeout": _TIMEOUT,
            }

        system_prompt = self._build_system_prompt(target_lang, history)
        return {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text.strip()},
            ],
            "timeout": _TIMEOUT,
        }

    async def translate(
        self,
        text: str,
        target_lang: str = "zh",
        history: list[str] | None = None,
    ) -> str:
        if not text or not text.strip():
            return ""

        request_kwargs = self._build_request_kwargs(text, target_lang, history)

        last_exception: Exception | None = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                response: ChatCompletion = await self._client.chat.completions.create(
                    **request_kwargs,  # type: ignore[arg-type]
                )

                content = response.choices[0].message.content
                if content is None:
                    msg = "Empty translation response from API"
                    raise TranslationProviderError(msg)

                return content.strip()

            except (APIError, APITimeoutError) as exc:
                last_exception = exc
                if attempt < _MAX_RETRIES:
                    backoff = _BACKOFF_BASE * (2**attempt)
                    await asyncio.sleep(backoff)
                continue
            except TranslationProviderError:
                raise
            except Exception as exc:
                msg = f"Translation request failed: {exc}"
                raise TranslationProviderError(msg) from exc

        msg = f"Translation failed after {_MAX_RETRIES + 1} attempts: {last_exception}"
        raise TranslationProviderError(msg) from last_exception


class DeepSeekTranslationProvider(OpenAICompatibleTranslationProvider):
    """Backward-compatible DeepSeek provider wrapper."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str | None = None,
        model: str | None = None,
    ) -> None:
        super().__init__(
            api_key,
            base_url=base_url or _DEEPSEEK_BASE_URL,
            model=model or _DEEPSEEK_MODEL,
        )
