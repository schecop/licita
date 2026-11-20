"""One field group -> one model call over that group's retrieved chunks -> raw {field: {...}}.

The chunks go first and the field list last, so a provider's prompt cache can reuse the prefix
when the same chunks serve several groups. Structured output comes either as a `record_fields`
tool call or as JSON in the text (`structured_output` in config), for models that cannot do both
reasoning and tool calls in one request; both end in the same dict, and every quote in it is
then checked by code (licita.extract.citations), whatever the model.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any, Literal

from licita.extract.fields import FieldGroup, tool_schema
from licita.llm.base import LLMClient, ToolSpec, UserMessage
from licita.retrieval.hybrid import Hit

TOOL = "record_fields"


def render_chunks(hits: Sequence[Hit]) -> str:
    return "\n\n".join(h.chunk.render() for h in hits)


def render_request(group: FieldGroup, hits: Sequence[Hit], mode: Literal["tool", "json"]) -> str:
    fields = "\n".join(f"- {f.name}: {f.description}" for f in group.fields)
    tail = (
        f"Llama a {TOOL} con estos campos."
        if mode == "tool"
        else "Responde solo con un objeto JSON con estos campos, cada uno "
        '{"value": ..., "chunk_id": ..., "quote": ...}, sin texto alrededor.'
    )
    chunks = render_chunks(hits)
    return f"Fragmentos de los pliegos:\n\n{chunks}\n\nCampos a extraer:\n{fields}\n\n{tail}"


def _json_in(text: str) -> dict[str, Any] | None:
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


async def extract_group(
    llm: LLMClient,
    system: str,
    group: FieldGroup,
    hits: Sequence[Hit],
    mode: Literal["tool", "json"] = "tool",
    max_tokens: int = 1500,
) -> dict[str, Any]:
    """Raw fields as the model returned them; {} if it returned nothing usable."""
    tools = [ToolSpec(TOOL, f"Registra los campos del grupo {group.name}.", tool_schema(group))]
    response = await llm.complete(
        system=system,
        messages=[UserMessage(render_request(group, hits, mode))],
        tools=tools if mode == "tool" else (),
        max_tokens=max_tokens,
    )
    if mode == "tool":
        call = next((c for c in response.tool_calls if c.name == TOOL), None)
        return dict(call.arguments) if call and not call.bad_arguments else {}
    return _json_in(response.text) or {}
