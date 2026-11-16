"""Provider quirks normalised in the adapters (no network: parse() on recorded-shape bodies)."""

from __future__ import annotations

import asyncio

from licita.llm.openai_compat import OpenAICompatClient, split_thought

GEMMA = '<thought>*   Role: extractor.\n    *   JSON only? Yes.</thought>{"budget_eur": 120000}'


def test_split_thought() -> None:
    assert split_thought(GEMMA) == (
        '{"budget_eur": 120000}',
        "*   Role: extractor.\n    *   JSON only? Yes.",
    )
    assert split_thought("Hola.") == ("Hola.", "")
    assert split_thought("<thought>cut by max_tok") == ("", "cut by max_tok")


def test_the_adapter_strips_reasoning_from_the_text_and_the_replayed_turn() -> None:
    client = OpenAICompatClient(
        provider="gemini", model="gemma-4-31b-it", api_key="x", base_url="http://unused"
    )
    body = {
        "choices": [{"message": {"role": "assistant", "content": GEMMA}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 30, "completion_tokens": 9},
    }
    r = client.parse(body, 10.0)
    assert r.text == '{"budget_eur": 120000}'
    assert r.message.raw["content"] == r.text
    asyncio.run(client.aclose())
