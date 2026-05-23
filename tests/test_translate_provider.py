"""Tests for translation providers."""

from __future__ import annotations

from rainyasr.providers.translate import (
    DeepSeekTranslationProvider,
    OpenAICompatibleTranslationProvider,
)


class TestDeepSeekTranslationProvider:
    def test_system_prompt_uses_only_last_two_history_items(self) -> None:
        prompt = DeepSeekTranslationProvider._build_system_prompt(
            "zh",
            ["first sentence", "second sentence", "third sentence"],
        )

        assert "first sentence" not in prompt
        assert "second sentence" in prompt
        assert "third sentence" in prompt

    def test_system_prompt_omits_history_section_when_history_is_empty(self) -> None:
        prompt = DeepSeekTranslationProvider._build_system_prompt("zh", [])

        assert "Historical context" not in prompt


class TestOpenAICompatibleTranslationProvider:
    def test_qwen_mt_request_uses_translation_options_without_prompt(self) -> None:
        provider = OpenAICompatibleTranslationProvider(
            "key",
            model="qwen-mt-flash",
        )

        kwargs = provider._build_request_kwargs(
            "hello",
            target_lang="zh",
            history=["previous sentence"],
        )

        assert kwargs["model"] == "qwen-mt-flash"
        assert kwargs["messages"] == [{"role": "user", "content": "hello"}]
        assert kwargs["extra_body"] == {
            "translation_options": {
                "source_lang": "auto",
                "target_lang": "Chinese",
            }
        }

    def test_general_model_request_uses_context_prompt(self) -> None:
        provider = OpenAICompatibleTranslationProvider(
            "key",
            model="deepseek-chat",
        )

        kwargs = provider._build_request_kwargs(
            "hello",
            target_lang="ja",
            history=["previous sentence"],
        )

        assert kwargs["model"] == "deepseek-chat"
        assert "extra_body" not in kwargs
        assert kwargs["messages"][0]["role"] == "system"
        assert "previous sentence" in kwargs["messages"][0]["content"]
        assert kwargs["messages"][1] == {"role": "user", "content": "hello"}

    def test_qwen_model_detection_includes_mt_and_general_qwen(self) -> None:
        assert OpenAICompatibleTranslationProvider.is_qwen_mt_model("qwen-mt-lite")
        assert OpenAICompatibleTranslationProvider.is_qwen_model("qwen-plus")
        assert not OpenAICompatibleTranslationProvider.is_qwen_model("deepseek-chat")
