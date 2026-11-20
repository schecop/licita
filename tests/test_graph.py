"""The real graph, end to end, on a synthetic pliego, driven by a scripted model (no network)."""

from __future__ import annotations

import asyncio
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from licita.config import AnalysisConfig, AppConfig, load_config
from licita.extract.fields import GROUPS
from licita.graph import Analysis, Deps, Event, analyse, stream
from licita.llm.base import LLMResponse
from licita.llm.scripted import Request, ScriptedLLM, call, calls
from licita.placsp.feed import MADRID, FeedPage, Tender
from licita.profile import load_profile
from licita.retrieval.embed import HashingEmbedder
from licita.tracing import Trace

NOW = datetime(2026, 11, 16, 9, 0, tzinfo=MADRID)
PROFILE = load_profile(Path("data/company/profile.yaml"))

# what a good extraction model answers for the synthetic PCAP: (value, verbatim quote)
GOOD: dict[str, tuple[Any, str | None]] = {
    "budget_eur": (240000, "El presupuesto base de licitación asciende a 240.000,00 euros"),
    "estimated_value_eur": (480000, "es de 480.000,00 euros"),
    "duration_months": (24, "El plazo de ejecución será de veinticuatro (24) meses"),
    "submission_deadline": (
        "2026-12-15 14:00",
        "hasta las 14:00 horas del día 15 de diciembre de 2026",
    ),
    "min_annual_turnover_eur": (360000, "por importe igual o superior a 360.000,00 euros"),
    "min_similar_services_eur": (168000, "sea igual o superior a 168.000,00 euros"),
    "certifications": (
        ["ISO/IEC 27001", "ENS categoría MEDIA"],
        "deberá disponer de la certificación ISO/IEC 27001 en vigor",
    ),
    "classification": (None, "No se exige clasificación empresarial."),
    "award_criteria": (
        [
            {"criterion": "oferta económica", "weight": 60},
            {"criterion": "memoria técnica", "weight": 40},
        ],
        "oferta económica, hasta 60 puntos",
    ),
    "contract_object": (
        "servicio de desarrollo, evolución y soporte de la plataforma de analítica de datos",
        "El objeto del contrato es el servicio de desarrollo, evolución y soporte",
    ),
    "required_staff": (
        ["jefe de proyecto con cinco años de experiencia", "dos desarrolladores"],
        "un jefe de proyecto con cinco años de experiencia y dos desarrolladores",
    ),
}
N = len(GROUPS)  # one extraction call per field group


def _chunks_in(req: Request) -> dict[str, str]:
    """chunk id -> text, as the graph rendered them in the request."""
    text = req.messages[-1].content  # type: ignore[union-attr]
    parts = re.split(r"^\[([^\]]+)\] \([^)]*\)\n", text, flags=re.M)
    return {parts[i]: parts[i + 1] for i in range(1, len(parts) - 1, 2)}


def _cite(req: Request, quote: str | None) -> str | None:
    if quote is None:
        return None
    flat = " ".join(quote.split()).lower()
    return next(
        (cid for cid, t in _chunks_in(req).items() if flat in " ".join(t.split()).lower()), None
    )


def extractor(answers: dict[str, tuple[Any, str | None]]) -> Any:
    """A scripted extraction step: fills the fields the request asks for, citing the chunk that
    holds each quote (null citation if retrieval did not bring that chunk)."""

    def step(req: Request) -> LLMResponse:
        asked = req.tools[0].input_schema["required"]
        args = {}
        for name in asked:
            value, quote = answers.get(name, (None, None))
            cid = _cite(req, quote)
            args[name] = {"value": value, "chunk_id": cid, "quote": quote if cid else None}
        return calls(("record_fields", args))

    return step


def decider(decision: str, confidence: float, quote: str) -> Any:
    def step(req: Request) -> LLMResponse:
        return call(
            "decide",
            decision=decision,
            confidence=confidence,
            reason="cumple la solvencia y el objeto encaja",
            chunk_id=_cite(req, quote) or "PCAP:p9:9",
            quote=quote,
        )

    return step


@pytest.fixture
def policia(head: FeedPage) -> Tender:
    return next(t for t in head.tenders if "Opentext" in t.title)


@pytest.fixture
def cfg(tmp_path: Path) -> AppConfig:
    base = load_config(Path("config/example.yaml"))
    return base.model_copy(
        update={"analysis": AnalysisConfig(pliegos_dir=tmp_path / "pliegos", chunk_max_chars=400)}
    )


@pytest.fixture
def with_pliego(cfg: AppConfig, policia: Tender, pcap_pdf: Path) -> Path:
    folder = cfg.analysis.pliegos_dir / policia.entry_id.rsplit("/", 1)[-1]
    folder.mkdir(parents=True)
    return Path(shutil.copy(pcap_pdf, folder / "PCAP.pdf"))


def _deps(cfg: AppConfig, extraction: ScriptedLLM, decision: ScriptedLLM, **kw: Any) -> Deps:
    return Deps(cfg, kw.pop("profile", PROFILE), extraction, decision, HashingEmbedder(), NOW, **kw)


def _run(d: Deps, t: Tender, trace: Trace | None = None) -> Analysis:
    return asyncio.run(analyse(d, t, trace))


@pytest.mark.usefixtures("with_pliego")
def test_candidate_with_pliego_gets_a_cited_go_auto_accepted(
    cfg: AppConfig, policia: Tender
) -> None:
    ext = ScriptedLLM([extractor(GOOD)] * N)
    dec = ScriptedLLM([decider("go", 0.95, "No se exige clasificación empresarial.")])
    a = _run(_deps(cfg, ext, dec), policia)

    assert [e.node for e in a.events] == [
        "screen",
        "load_documents",
        "retrieve",
        "extract",
        "requirements",
        "decide",
    ]
    f = a.state["fields"]
    assert {n: r.check for n, r in f.items()} == {
        **dict.fromkeys(GOOD, "verified"),
        "classification": "explicit_none",
    }
    assert f["min_annual_turnover_eur"].citation.page == 2  # type: ignore[union-attr]
    assert {r.name: r.verdict for r in a.state["requirements"]} == {
        "turnover": "pass",
        "similar_services": "open",  # 900k over 3 years vs 168k: depends on the wording
        "certification ISO/IEC 27001": "pass",  # matched by its number
        "certification ENS categoría MEDIA": "pass",  # profile: ENS medio
    }
    o = a.outcome
    assert (o.decision, o.route, o.decided_by) == ("go", "auto", "model")
    assert o.citation is not None and o.citation.doc == "PCAP" and o.citation.page == 2
    # the decision model saw the profile, the cited fields and the code's checks
    assert "Dades i Codi" in dec.requests[0].messages[0].content  # type: ignore[union-attr]
    assert '"verdict": "open"' in dec.requests[0].messages[0].content  # type: ignore[union-attr]
    # ...and the object and the team the pliego asks for, with their clause
    request = dec.requests[0].messages[0].content  # type: ignore[union-attr]
    assert '"contract_object"' in request and "dos desarrolladores" in request


@pytest.mark.usefixtures("with_pliego")
def test_low_confidence_goes_to_review(cfg: AppConfig, policia: Tender) -> None:
    dec = ScriptedLLM([decider("go", 0.6, "No se exige clasificación empresarial.")])
    o = _run(_deps(cfg, ScriptedLLM([extractor(GOOD)] * N), dec), policia).outcome
    assert (o.decision, o.route) == ("go", "review")


@pytest.mark.usefixtures("with_pliego")
def test_an_invented_decision_quote_is_uncited_and_goes_to_review(
    cfg: AppConfig, policia: Tender
) -> None:
    dec = ScriptedLLM([decider("go", 0.99, "La empresa cumple todos los requisitos sobradamente.")])
    o = _run(_deps(cfg, ScriptedLLM([extractor(GOOD)] * N), dec), policia).outcome
    assert (o.decision, o.route, o.citation) == ("go", "review", None)
    assert "uncited" in o.reason


@pytest.mark.usefixtures("with_pliego")
def test_an_invented_field_quote_is_caught_and_the_tender_goes_to_review(
    cfg: AppConfig, policia: Tender
) -> None:
    def lying(req: Request) -> LLMResponse:
        response = extractor(GOOD)(req)
        args = response.tool_calls[0].arguments
        if "min_annual_turnover_eur" in args:  # right chunk, invented figure and quote
            cid = _cite(req, GOOD["min_annual_turnover_eur"][1])
            args["min_annual_turnover_eur"] = {
                "value": 5_000_000,
                "chunk_id": cid,
                "quote": "por importe igual o superior a 5.000.000,00 euros",
            }
        return response  # type: ignore[no-any-return]

    a = _run(_deps(cfg, ScriptedLLM([lying] * N), ScriptedLLM([])), policia)
    assert a.state["fields"]["min_annual_turnover_eur"].check == "bad_quote"
    # an unverified 5M requirement must not become a code no-go...
    assert "turnover" not in {r.name for r in a.state["requirements"]}
    # ...nor be skipped over: the turnover was not checked, so no model decides
    o = a.outcome
    assert (o.decision, o.route, o.decided_by) == (None, "review", "requirements")
    assert "min_annual_turnover_eur (bad_quote)" in o.reason


@pytest.mark.usefixtures("with_pliego")
def test_solvency_not_found_goes_to_review_without_the_decision_model(
    cfg: AppConfig, policia: Tender
) -> None:
    missed = GOOD | {"min_similar_services_eur": (None, None)}  # retrieval or the model missed it
    a = _run(_deps(cfg, ScriptedLLM([extractor(missed)] * N), ScriptedLLM([])), policia)
    o = a.outcome
    assert (o.decision, o.route, o.decided_by) == (None, "review", "requirements")
    assert o.reason.endswith("min_similar_services_eur (not_found)")
    assert a.events[-1].node == "requirements"


@pytest.mark.usefixtures("with_pliego")
def test_plainly_failed_solvency_is_a_no_go_by_code_without_the_decision_model(
    cfg: AppConfig, policia: Tender
) -> None:
    small = PROFILE.model_copy(
        update={"solvency": PROFILE.solvency.model_copy(update={"annual_turnover_eur": 300000})}
    )
    dec = ScriptedLLM([])  # any call would raise ScriptExhausted
    a = _run(_deps(cfg, ScriptedLLM([extractor(GOOD)] * N), dec, profile=small), policia)
    o = a.outcome
    assert (o.decision, o.route, o.decided_by) == ("no_go", "auto", "requirements")
    assert "€360,000" in o.reason
    assert o.citation is not None and "360.000,00 euros" in o.citation.quote
    assert a.events[-1].node == "requirements"


def test_feed_rule_no_go_ends_before_any_document_or_model(cfg: AppConfig, head: FeedPage) -> None:
    works = next(t for t in head.tenders if t.folder_id == "04101/2026/250-O")
    a = _run(_deps(cfg, ScriptedLLM([]), ScriptedLLM([])), works)
    assert [e.node for e in a.events] == ["screen"]
    assert (a.outcome.decision, a.outcome.decided_by) == ("no_go", "rules")
    assert a.calls == []


def test_candidate_without_documents_goes_to_review(cfg: AppConfig, policia: Tender) -> None:
    a = _run(_deps(cfg, ScriptedLLM([]), ScriptedLLM([])), policia)
    assert (a.outcome.decision, a.outcome.route) == (None, "review")
    assert "no documents in" in a.outcome.reason


@pytest.mark.usefixtures("with_pliego")
def test_progress_streams_one_event_per_node_then_the_analysis(
    cfg: AppConfig, policia: Tender
) -> None:
    d = _deps(
        cfg,
        ScriptedLLM([extractor(GOOD)] * N),
        ScriptedLLM([decider("go", 0.95, "No se exige clasificación empresarial.")]),
    )

    async def collect() -> list[Event | Analysis]:
        return [item async for item in stream(d, policia)]

    items = asyncio.run(collect())
    assert isinstance(items[-1], Analysis)
    retrieve = next(i for i in items if isinstance(i, Event) and i.node == "retrieve")
    assert retrieve.summary["pages"] == 3 and retrieve.summary["chunks"] > 0


@pytest.mark.usefixtures("with_pliego")
def test_one_trace_per_tender_with_a_span_per_node_and_generations_inside(
    cfg: AppConfig, policia: Tender
) -> None:
    from licita.llm.metered import Call, Metered

    ledger: list[Call] = []
    ext = Metered(ScriptedLLM([extractor(GOOD)] * N), "extraction", calls=ledger)
    dec = Metered(
        ScriptedLLM([decider("go", 0.95, "No se exige clasificación empresarial.")]),
        "decision",
        calls=ledger,
    )
    trace = Trace("tender", tender_id=policia.entry_id)
    a = _run(Deps(cfg, PROFILE, ext, dec, HashingEmbedder(), NOW, calls=ledger), policia, trace)
    names = [s.name for s in trace.spans]
    assert names[:7] == [
        "tender",
        "screen",
        "load_documents",
        "retrieve",
        "extract",
        "requirements",
        "decide",
    ]
    gens = [s for s in trace.spans if s.attributes.get("langfuse.observation.type") == "generation"]
    assert len(gens) == len(a.calls) == N + 1
    extract_span = next(s for s in trace.spans if s.name == "extract")
    assert sum(g.parent_id == extract_span.span_id for g in gens) == N
    assert "retrieval.hits" in next(s for s in trace.spans if s.name == "retrieve").attributes
