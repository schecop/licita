"""Provider quirks normalised in the adapters (no network: parse() on recorded-shape bodies)."""

from __future__ import annotations

import asyncio

import pytest

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


def test_lazy_client_needs_no_key_until_the_first_call(monkeypatch: pytest.MonkeyPatch) -> None:
    from licita.config import ModelChoice
    from licita.env import MissingSecretError
    from licita.llm.base import UserMessage
    from licita.llm.factory import LazyLLM

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    lazy = LazyLLM(
        ModelChoice(provider="anthropic", model="m", usd_per_mtok_in=1, usd_per_mtok_out=1)
    )
    assert (lazy.provider, lazy.model) == ("anthropic", "m")  # built without a key
    with pytest.raises(MissingSecretError):
        asyncio.run(lazy.complete(system="", messages=[UserMessage("hola")]))
