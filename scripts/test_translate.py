"""Manual validation script for OpenAICompatibleTranslationProvider.

Usage:
    DASHSCOPE_API_KEY=xxx uv run python scripts/test_translate.py

The script reads English sentences from stdin and prints Chinese translations.
Type "quit" to exit.
"""

from __future__ import annotations

import asyncio
import sys

from rainyasr.config import EnvConfig
from rainyasr.providers import OpenAICompatibleTranslationProvider


async def main() -> None:
    model = EnvConfig.translate_model()
    api_key = EnvConfig.translate_api_key()
    if not api_key and OpenAICompatibleTranslationProvider.is_qwen_model(model):
        api_key = EnvConfig.dashscope_api_key()
    if not api_key:
        api_key = EnvConfig.deepseek_api_key()
    if not api_key:
        print("Error: translation API key not set.", file=sys.stderr)
        sys.exit(1)

    base_url = EnvConfig.translate_base_url()
    if not base_url and OpenAICompatibleTranslationProvider.is_qwen_model(model):
        base_url = EnvConfig.dashscope_compatible_base_url()
    if not base_url:
        base_url = EnvConfig.deepseek_base_url()

    provider = OpenAICompatibleTranslationProvider(
        api_key=api_key,
        base_url=base_url,
        model=model,
    )
    history: list[str] = []

    print(f"Translation Test ({model})")
    print("Enter English sentences (type 'quit' to exit):")
    print("-" * 40)

    while True:
        try:
            text = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not text:
            continue
        if text.lower() == "quit":
            break

        try:
            result = await provider.translate(text, target_lang="zh", history=history)
            print(f"  [zh] {result}")

            # Keep last 2 sentences for context
            history.append(text)
            if len(history) > 2:
                history.pop(0)

        except Exception as exc:
            print(f"  [ERROR] {exc}", file=sys.stderr)

    print("Bye.")


if __name__ == "__main__":
    asyncio.run(main())
