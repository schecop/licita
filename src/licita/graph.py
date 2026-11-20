"""The analysis of one tender as a LangGraph graph.

    screen -> load_documents -> retrieve -> extract -> requirements -> decide
       |            |              |                        |
       +------------+--------------+------------------------+--> END (as soon as `outcome` is set)

Each node is an async function that returns only the part of the state it changes. Any node can
end the run by setting `outcome`: a feed rule fails (no-go by code), there are no documents or no
text (review), a solvency requirement plainly fails (no-go by code, citing the clause), or the
solvency could not be read from the pliegos (review). Only what code leaves open reaches the
model's decision.

Why a graph and not one long function: every step is a unit with its own span in the trace and
its own progress event (the front streams them over SSE), the early exits are explicit edges, and
a step can be retried or swapped (e.g. a reranker between retrieve and extract) without touching
the others. Models come in through `LLMClient` only (rule 7): no LangChain chat-model wrappers.
"""

from __future__ import annotations

import asyncio
import itertools
import json
from collections.abc import AsyncIterator, Callable, Coroutine
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from licita.config import AppConfig
from licita.decide.decision import Outcome, model_decision, render_request
from licita.decide.requirements import Requirement, check_requirements, unestablished
from licita.documents.chunk import Chunk, chunk_pages
from licita.documents.pdf import read_pdf
from licita.documents.source import local_documents, tender_folder
from licita.extract.citations import Citation, FieldResult, check_field
from licita.extract.extractor import extract_group
from licita.extract.fields import GROUPS
from licita.llm.base import LLMClient
from licita.llm.metered import Call
from licita.placsp.feed import Tender
from licita.profile import CompanyProfile
from licita.retrieval.embed import Embedder
from licita.retrieval.hybrid import Hit, HybridIndex
from licita.rules import Screening, screen
from licita.tracing import Span, Trace

PROMPTS = Path("prompts")


class TenderState(TypedDict, total=False):
    tender: Tender
    screening: Screening
    documents: list[Path]
    pages: int
    scanned_pages: int
    chunks: dict[str, Chunk]
    hits: dict[str, list[Hit]]  # field group -> retrieved chunks
    fields: dict[str, FieldResult]
    requirements: list[Requirement]
    outcome: Outcome


@dataclass
class Deps:
    cfg: AppConfig
    profile: CompanyProfile
    extraction: LLMClient
    decision: LLMClient
    embedder: Embedder
    now: datetime
    extraction_mode: Literal["tool", "json"] = "tool"
    calls: list[Call] = field(default_factory=list)  # shared with the Metered clients, if any
    extraction_prompt: str = ""
    decision_prompt: str = ""

    def __post_init__(self) -> None:
        self.extraction_prompt = self.extraction_prompt or (PROMPTS / "extraction.md").read_text(
            "utf-8"
        )
        self.decision_prompt = self.decision_prompt or (PROMPTS / "decision.md").read_text("utf-8")


@dataclass(frozen=True)
class Event:
    """One finished node, for progress (CLI, SSE): its name and a short summary."""

    node: str
    summary: dict[str, Any]


Node = Callable[[TenderState], Coroutine[Any, Any, dict[str, Any]]]


def _nodes(d: Deps) -> dict[str, Node]:
    a = d.cfg.analysis

    async def screen_node(s: TenderState) -> dict[str, Any]:
        sc = screen(s["tender"], d.profile, d.now, d.cfg.rules.min_days_to_deadline)
        out: dict[str, Any] = {"screening": sc}
        if sc.no_go:
            first = sc.failed[0]
            cite = Citation("feed", "feed PLACSP", 0, first.reason)
            reason = "; ".join(f"{r.rule}: {r.reason}" for r in sc.failed)
            out["outcome"] = Outcome("no_go", "auto", "rules", reason, 1.0, cite)
        return out

    async def load_documents(s: TenderState) -> dict[str, Any]:
        docs = local_documents(a.pliegos_dir, s["tender"].entry_id)
        out: dict[str, Any] = {"documents": docs}
        if not docs:
            where = tender_folder(a.pliegos_dir, s["tender"].entry_id)
            out["outcome"] = Outcome(None, "review", "none", f"no documents in {where}")
        return out

    async def retrieve(s: TenderState) -> dict[str, Any]:
        chunks: list[Chunk] = []
        pages = scanned = 0
        for path in s["documents"]:
            doc_pages = await asyncio.to_thread(read_pdf, path)
            pages += len(doc_pages)
            scanned += sum(p.scanned for p in doc_pages)
            chunks += chunk_pages(path.stem, doc_pages, a.chunk_max_chars)
        out: dict[str, Any] = {
            "pages": pages,
            "scanned_pages": scanned,
            "chunks": {c.id: c for c in chunks},
        }
        if not chunks:
            out["outcome"] = Outcome(None, "review", "none", f"no text in {pages} pages (scanned?)")
            return out
        index = await HybridIndex.build(chunks, d.embedder)
        out["hits"] = {g.name: await index.search(g.query, a.top_k) for g in GROUPS}
        return out

    async def extract(s: TenderState) -> dict[str, Any]:
        raws = await asyncio.gather(
            *(
                extract_group(
                    d.extraction, d.extraction_prompt, g, s["hits"][g.name], d.extraction_mode
                )
                for g in GROUPS
            )
        )
        fields = {
            f.name: check_field(f.name, raw.get(f.name), s["chunks"])
            for g, raw in zip(GROUPS, raws, strict=True)
            for f in g.fields
        }
        return {"fields": fields}

    async def requirements(s: TenderState) -> dict[str, Any]:
        reqs = check_requirements(s["fields"], d.profile)
        out: dict[str, Any] = {"requirements": reqs}
        failed = [r for r in reqs if r.verdict == "fail"]
        missing = unestablished(s["fields"])
        if failed:  # a cited plain fail stands even if other solvency fields are missing
            reason = "; ".join(f"{r.name}: {r.reason}" for r in failed)
            out["outcome"] = Outcome(
                "no_go", "auto", "requirements", reason, 1.0, failed[0].citation
            )
        elif missing:  # nothing to compare: no model may auto-accept over it
            reason = "solvency not established from the pliegos: " + ", ".join(missing)
            out["outcome"] = Outcome(None, "review", "requirements", reason)
        return out

    async def decide(s: TenderState) -> dict[str, Any]:
        cited_ids = {f.citation.chunk_id for f in s["fields"].values() if f.citation}
        cited = [s["chunks"][i] for i in sorted(cited_ids)]
        request = render_request(
            d.profile, s["tender"].title, s["fields"], s["requirements"], cited
        )
        outcome = await model_decision(
            d.decision, d.decision_prompt, request, {c.id: c for c in cited}, a.cascade_threshold
        )
        return {"outcome": outcome}

    return {
        "screen": screen_node,
        "load_documents": load_documents,
        "retrieve": retrieve,
        "extract": extract,
        "requirements": requirements,
        "decide": decide,
    }


Owners = list[tuple[int, int, Span]]  # (first call index, end index, node span)


def build_graph(d: Deps, trace: Trace | None = None, owners: Owners | None = None) -> Any:
    nodes = _nodes(d)
    graph = StateGraph(TenderState)
    for name, fn in nodes.items():
        graph.add_node(  # type: ignore[call-overload]  # a typed TypedDict node is fine at runtime
            name, _traced(name, fn, trace, d.calls, owners if owners is not None else [])
        )
    order = list(nodes)
    graph.add_edge(START, order[0])
    for here, there in itertools.pairwise(order):
        graph.add_conditional_edges(here, _stop_or(there), {there: there, END: END})
    graph.add_edge(order[-1], END)
    return graph.compile()


def _stop_or(next_node: str) -> Callable[[TenderState], str]:
    return lambda s: END if "outcome" in s else next_node


def _traced(name: str, fn: Node, trace: Trace | None, calls: list[Call], owners: Owners) -> Node:
    """A span per node; the model calls made while it ran are recorded as its own, by index in
    the shared call list, not by timestamp (Windows clocks tick too coarsely to tell nodes
    apart)."""

    async def run(s: TenderState) -> dict[str, Any]:
        if trace is None:
            return await fn(s)
        with trace.span(name) as span:
            first = len(calls)
            update = await fn(s)
            owners.append((first, len(calls), span))
            span.set_many(
                {"langfuse.observation.output": json.dumps(summarise(name, update), default=str)}
            )
            if name == "retrieve" and "hits" in update:  # the chunks each query returned
                span.set_many({"retrieval.hits": json.dumps(_hits(update["hits"]))})
            return update

    return run


def _hits(hits: dict[str, list[Hit]]) -> dict[str, list[dict[str, Any]]]:
    return {
        g: [{"chunk": h.chunk.id, "bm25": h.bm25_rank, "dense": h.dense_rank} for h in hs]
        for g, hs in hits.items()
    }


def summarise(node: str, update: dict[str, Any]) -> dict[str, Any]:
    """What a progress line (CLI, SSE) and a trace span show for one finished node."""
    out: dict[str, Any] = {}
    if "screening" in update:
        out["rules"] = {r.rule: r.verdict for r in update["screening"].results}
    if "documents" in update:
        out["documents"] = [p.name for p in update["documents"]]
    if "chunks" in update:
        out |= {
            "pages": update["pages"],
            "scanned": update["scanned_pages"],
            "chunks": len(update["chunks"]),
        }
    if "fields" in update:
        out["fields"] = {n: f.check for n, f in update["fields"].items()}
    if "requirements" in update:
        out["requirements"] = {r.name: r.verdict for r in update["requirements"]}
    if "outcome" in update:
        o: Outcome = update["outcome"]
        out["outcome"] = {
            "decision": o.decision,
            "route": o.route,
            "by": o.decided_by,
            "reason": o.reason,
        }
    return out


@dataclass
class Analysis:
    state: TenderState
    events: list[Event]
    calls: list[Call]

    @property
    def outcome(self) -> Outcome:
        return self.state["outcome"]


async def stream(
    d: Deps, tender: Tender, trace: Trace | None = None
) -> AsyncIterator[Event | Analysis]:
    """Progress events as nodes finish, then the full Analysis last."""
    first_call = len(d.calls)
    state: dict[str, Any] = {"tender": tender}
    events: list[Event] = []
    owners: Owners = []
    graph = build_graph(d, trace, owners)
    async for chunk in graph.astream({"tender": tender}, stream_mode="updates"):
        for node, update in chunk.items():
            state |= update or {}
            event = Event(node, summarise(node, update or {}))
            events.append(event)
            yield event
    calls = d.calls[first_call:]
    if trace is not None:
        _add_generations(trace, d.calls, first_call, owners)
        o: Outcome = state["outcome"]
        trace.finish(
            langfuse__trace__output=json.dumps(summarise("end", {"outcome": o}), default=str)
        )
    yield Analysis(TenderState(**state), events, calls)  # type: ignore[typeddict-item]


async def analyse(d: Deps, tender: Tender, trace: Trace | None = None) -> Analysis:
    result: Analysis | None = None
    async for item in stream(d, tender, trace):
        if isinstance(item, Analysis):
            result = item
    assert result is not None
    return result


def _add_generations(trace: Trace, calls: list[Call], first: int, owners: Owners) -> None:
    """Model calls as Langfuse generations, under the node span that made them."""
    for i in range(first, len(calls)):
        c = calls[i]
        parent = next((span for lo, hi, span in owners if lo <= i < hi), None)
        trace.add(
            f"{c.role}:{c.model}",
            parent,
            c.start_ns,
            c.end_ns,
            {
                "langfuse.observation.type": "generation",
                "gen_ai.request.model": c.model,
                "gen_ai.usage.input_tokens": c.input_tokens,
                "gen_ai.usage.output_tokens": c.output_tokens,
                "gen_ai.usage.cost": float(c.usd),
                "langfuse.observation.input": c.input,
                "langfuse.observation.output": c.output,
            },
        )
