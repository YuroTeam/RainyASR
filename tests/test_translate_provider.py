"""Tests for DeepSeekTranslationProvider."""

from __future__ import annotations

from rainyasr.providers.translate import DeepSeekTranslationProvider


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
