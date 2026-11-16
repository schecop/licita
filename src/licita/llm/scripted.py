"""A scripted `LLMClient` for tests: fixed replies and tool calls, no network, no key.

It drives the real graph and loops in pytest and CI. Each `complete()` call takes the next step
of the script: a fixed `LLMResponse` (build them with `say` and `call`), or a function of the
request, for replies that depend on what the graph sent (e.g. echo a slot from a tool result).
Every request is recorded in `requests`, so a test can assert what the model was shown.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from licita.llm.base import (
    AssistantMessage,
    LLMResponse,
    Message,
    ToolCall,
    ToolSpec,
)


@dataclass(frozen=True)
class Request:
    system: str
    messages: list[Message]
    tools: list[ToolSpec]


Step = LLMResponse | Callable[[Request], LLMResponse]

_ids = itertools.count(1)


def say(text: str) -> LLMResponse:
    return LLMResponse(AssistantMessage(text, provider="scripted"), stop="end")


def call(name: str, text: str = "", **arguments: Any) -> LLMResponse:
    """One tool call (add more with `calls`)."""
    return calls((name, arguments), text=text)


def calls(*tool_calls: tuple[str, dict[str, Any]], text: str = "") -> LLMResponse:
    made = [ToolCall(f"call_{next(_ids)}", name, args) for name, args in tool_calls]
    return LLMResponse(AssistantMessage(text, made, provider="scripted"), stop="tool_use")


class ScriptExhausted(AssertionError):
    """The code under test called the model more times than the script expected."""


class ScriptedLLM:
    provider = "scripted"

    def __init__(self, steps: Iterable[Step], model: str = "scripted") -> None:
        self.model = model
        self._steps = list(steps)
        self.requests: list[Request] = []

    @property
    def remaining(self) -> int:
        return len(self._steps)

    async def complete(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] = (),
        max_tokens: int = 1024,
    ) -> LLMResponse:
        request = Request(system, list(messages), list(tools))
        self.requests.append(request)
        if not self._steps:
            raise ScriptExhausted(f"no scripted reply left for call #{len(self.requests)}")
        step = self._steps.pop(0)
        return step(request) if callable(step) else step

    async def aclose(self) -> None:
        return None
