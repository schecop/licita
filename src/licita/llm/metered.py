"""An `LLMClient` wrapper that records every call: role, model, tokens, cost, latency, and what
went in and came out (abridged).

The pipeline wraps each role's client (screening, extraction, decision, coach) with one, so a
run knows what it cost and how slow it was; the cost ledger and the Langfuse traces read
the same records. Prices come from config (`usd_per_mtok_in/out`), never from code.
"""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from licita.llm.base import (
    AssistantMessage,
    LLMClient,
    LLMResponse,
    Message,
    ToolResults,
    ToolSpec,
    UserMessage,
)

MTOK = Decimal(1_000_000)
ABRIDGE = 2000  # characters kept of a call's input and output, for traces


@dataclass(frozen=True)
class Call:
    role: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    usd: Decimal
    latency_ms: float
    stop: str
    start_ns: int = 0
    end_ns: int = 0
    input: str = ""  # the last message the model saw (the system prompt is not repeated)
    output: str = ""  # its text, or the tool calls it made


def describe(message: Message) -> str:
    if isinstance(message, UserMessage):
        return message.content
    if isinstance(message, ToolResults):
        return "\n".join(f"{r.name} -> {r.content}" for r in message.results)
    return answer(message)


def answer(message: AssistantMessage) -> str:
    calls = [{"tool": c.name, "arguments": c.arguments} for c in message.tool_calls]
    if not calls:
        return message.text
    return (message.text + "\n" if message.text else "") + json.dumps(calls, ensure_ascii=False)


class Metered:
    def __init__(
        self,
        inner: LLMClient,
        role: str,
        usd_per_mtok_in: Decimal = Decimal(0),
        usd_per_mtok_out: Decimal = Decimal(0),
        calls: list[Call] | None = None,
    ) -> None:
        self.inner = inner
        self.role = role
        self.provider = inner.provider
        self.model = inner.model
        self._in, self._out = usd_per_mtok_in, usd_per_mtok_out
        self.prices = (usd_per_mtok_in, usd_per_mtok_out)
        self.calls: list[Call] = calls if calls is not None else []  # may be shared by roles

    async def complete(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] = (),
        max_tokens: int = 1024,
    ) -> LLMResponse:
        start = time.time_ns()
        response = await self.inner.complete(
            system=system, messages=messages, tools=tools, max_tokens=max_tokens
        )
        usd = (response.input_tokens * self._in + response.output_tokens * self._out) / MTOK
        self.calls.append(
            Call(
                self.role,
                self.provider,
                self.model,
                response.input_tokens,
                response.output_tokens,
                usd,
                response.latency_ms,
                response.stop,
                start_ns=start,
                end_ns=time.time_ns(),
                input=describe(messages[-1])[:ABRIDGE] if messages else "",
                output=answer(response.message)[:ABRIDGE],
            )
        )
        return response

    async def aclose(self) -> None:
        await self.inner.aclose()
