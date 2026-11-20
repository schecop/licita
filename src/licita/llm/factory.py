"""Build the LLM client for a role from config. Providers differ only in data here.

Each provider: which adapter speaks its format, its official base URL, the `.env` variable that
holds its key, and (Chat Completions only) what the output-token limit field is called.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from licita.config import ModelChoice
from licita.env import require
from licita.llm.anthropic import AnthropicClient
from licita.llm.base import LLMClient, LLMResponse, Message, ToolSpec
from licita.llm.openai_compat import OpenAICompatClient


@dataclass(frozen=True)
class Provider:
    adapter: str  # "openai_compat" | "anthropic"
    base_url: str
    key_env: str
    max_tokens_field: str = "max_completion_tokens"


PROVIDERS: dict[str, Provider] = {
    "openai": Provider("openai_compat", "https://api.openai.com/v1", "OPENAI_API_KEY"),
    # https://ai.google.dev/gemini-api/docs/openai
    "gemini": Provider(
        "openai_compat",
        "https://generativelanguage.googleapis.com/v1beta/openai",
        "GEMINI_API_KEY",
        "max_tokens",
    ),
    "anthropic": Provider("anthropic", "https://api.anthropic.com", "ANTHROPIC_API_KEY"),
    # https://docs.mistral.ai/api (Chat Completions compatible)
    "mistral": Provider(
        "openai_compat", "https://api.mistral.ai/v1", "MISTRAL_API_KEY", "max_tokens"
    ),
}


def build_llm(choice: ModelChoice) -> LLMClient:
    try:
        p = PROVIDERS[choice.provider]
    except KeyError:
        raise ValueError(f"unknown LLM provider {choice.provider!r}: {sorted(PROVIDERS)}") from None
    key = require(p.key_env)
    if p.adapter == "anthropic":
        return AnthropicClient(
            model=choice.model, api_key=key, base_url=p.base_url, options=choice.options
        )
    return OpenAICompatClient(
        provider=choice.provider,
        model=choice.model,
        api_key=key,
        base_url=p.base_url,
        max_tokens_field=p.max_tokens_field,
        options=choice.options,
    )


class LazyLLM:
    """Builds the real client on the first call. A run that never reaches a model (a feed
    no-go, no documents) needs no key and cannot spend."""

    def __init__(self, choice: ModelChoice) -> None:
        self.choice = choice
        self.provider = choice.provider
        self.model = choice.model
        self._inner: LLMClient | None = None

    async def complete(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] = (),
        max_tokens: int = 1024,
    ) -> LLMResponse:
        if self._inner is None:
            self._inner = build_llm(self.choice)
        return await self._inner.complete(
            system=system, messages=messages, tools=tools, max_tokens=max_tokens
        )

    async def aclose(self) -> None:
        if self._inner is not None:
            await self._inner.aclose()


def cost_usd(choice: ModelChoice, input_tokens: int, output_tokens: int) -> Decimal:
    """List price from config; reasoning tokens are billed as output and counted there."""
    return (
        Decimal(input_tokens) * choice.usd_per_mtok_in
        + Decimal(output_tokens) * choice.usd_per_mtok_out
    ) / 1_000_000
