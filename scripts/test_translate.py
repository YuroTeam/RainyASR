"""Manual validation script for DeepSeekTranslationProvider.

Usage:
    DEEPSEEK_API_KEY=xxx uv run python scripts/test_translate.py

The script reads English sentences from stdin and prints Chinese translations.
Type "quit" to exit.
"""

from __future__ import annotations

import asyncio
import sys

from rainyasr.config import EnvConfig
from rainyasr.providers import DeepSeekTranslationProvider


async def main() -> None:
    api_key = EnvConfig.deepseek_api_key()
    if not api_key:
        print("Error: DEEPSEEK_API_KEY not set.", file=sys.stderr)
        sys.exit(1)

    provider = DeepSeekTranslationProvider(api_key=api_key)
    history: list[str] = []

    print("DeepSeek Translation Test")
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
