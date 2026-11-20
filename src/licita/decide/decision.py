"""The go / no-go: by code when it can, by the model for what code leaves open, then the cascade.

Who decides, in order:
1. Feed rules (licita.rules) fail -> no-go by code, citing the feed field.
2. No documents -> no decision: review.
3. A solvency requirement plainly failed (licita.decide.requirements) -> no-go by code, citing the
   clause.
4. Otherwise the decision model: go / no-go + confidence + the deciding clause.

The cascade then routes the model's decision: auto-accepted only with a verified citation and a
confidence at or above the threshold; everything else goes to the review inbox. The threshold is a
placeholder until the evals measure the value that gives >= 95% precision (CLAUDE.md).
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from licita.decide.requirements import Requirement
from licita.documents.chunk import Chunk
from licita.extract.citations import Citation, FieldResult, check_field
from licita.llm.base import LLMClient, ToolSpec, UserMessage
from licita.profile import CompanyProfile

TOOL = "decide"
SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": ["go", "no_go"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reason": {"type": "string"},
        "chunk_id": {"type": ["string", "null"]},
        "quote": {"type": ["string", "null"]},
    },
    "required": ["decision", "confidence", "reason", "chunk_id", "quote"],
}

DecidedBy = Literal["rules", "requirements", "model", "none"]


@dataclass(frozen=True)
class Outcome:
    decision: Literal["go", "no_go"] | None
    route: Literal["auto", "review"]
    decided_by: DecidedBy
    reason: str
    confidence: float | None = None
    citation: Citation | None = None


def cascade(
    decision: Literal["go", "no_go"],
    confidence: float,
    reason: str,
    citation: Citation | None,
    threshold: float,
) -> Outcome:
    if citation is None:
        return Outcome(decision, "review", "model", f"{reason} [uncited: to review]", confidence)
    route: Literal["auto", "review"] = "auto" if confidence >= threshold else "review"
    return Outcome(decision, route, "model", reason, confidence, citation)


def render_request(
    profile: CompanyProfile,
    title: str,
    fields: Mapping[str, FieldResult],
    requirements: Sequence[Requirement],
    chunks: Sequence[Chunk],
) -> str:
    company = profile.model_dump(mode="json", include={"name", "summary", "solvency"})
    data = {
        name: {
            "value": f.value,
            "check": f.check,
            "chunk_id": f.citation.chunk_id if f.citation else None,
        }
        for name, f in fields.items()
    }
    checks = [{"name": r.name, "verdict": r.verdict, "reason": r.reason} for r in requirements]
    cited = "\n\n".join(c.render() for c in chunks)
    return (
        f"Licitación: {title}\n\n"
        f"Empresa:\n{json.dumps(company, ensure_ascii=False, indent=1)}\n\n"
        f"Datos extraídos:\n{json.dumps(data, ensure_ascii=False, indent=1)}\n\n"
        f"Comprobaciones del código:\n{json.dumps(checks, ensure_ascii=False, indent=1)}\n\n"
        f"Fragmentos citados:\n\n{cited}\n\nLlama a {TOOL}."
    )


async def model_decision(
    llm: LLMClient,
    system: str,
    request: str,
    chunks: Mapping[str, Chunk],
    threshold: float,
) -> Outcome:
    response = await llm.complete(
        system=system,
        messages=[UserMessage(request)],
        tools=[ToolSpec(TOOL, "Registra la decisión go / no_go con su cita.", SCHEMA)],
        max_tokens=1000,
    )
    call = next((c for c in response.tool_calls if c.name == TOOL), None)
    args = call.arguments if call and not call.bad_arguments else {}
    if args.get("decision") not in ("go", "no_go"):
        return Outcome(None, "review", "model", "the decision model returned no decision")
    checked = check_field("decision", {"value": args["decision"], **args}, chunks)
    confidence = min(max(float(args.get("confidence") or 0), 0.0), 1.0)
    return cascade(
        args["decision"], confidence, str(args.get("reason", "")), checked.citation, threshold
    )
