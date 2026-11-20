from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
import yaml

from licita.config import AppConfig
from licita.evals.labels import FieldLabel, Label, Where, load_labels
from licita.evals.score import (
    TenderScore,
    cascade_curve,
    pick_threshold,
    report,
    score_tender,
    value_correct,
    wilson_lower,
)
from licita.graph import analyse, record
from licita.llm.scripted import ScriptedLLM
from licita.placsp.feed import Tender
from tests.test_graph import GOOD, N, _deps, cfg, decider, extractor, policia, with_pliego

__all__ = ["cfg", "policia", "with_pliego"]  # fixtures shared with the graph tests


def _gold(value: Any, page: int | None = 2, waived: bool = False) -> FieldLabel:
    where: Where | str = (
        "absent" if page is None else Where(doc="PCAP", page=page, quote="x", waived=waived)
    )
    return FieldLabel(value=value, where=where, checked="confirmed")


def _solv(*criteria: dict[str, Any], any_of: bool = False) -> dict[str, Any]:
    return {"any_of": any_of, "criteria": list(criteria)}


def _c(
    kind: str, eur: float | None = None, x: float | None = None, base: str | None = None
) -> dict[str, Any]:
    return {"kind": kind, "amount_eur": eur, "multiple": x, "base": base}


def test_value_rules_per_field() -> None:
    v = value_correct
    # amounts: within 0.5 %
    assert v("budget_eur", _gold(240000), 240900, "verified")
    assert not v("budget_eur", _gold(240000), 264000, "verified")
    assert not v(
        "budget_eur", _gold(240000), 240000, "bad_quote"
    )  # an unverified value counts wrong
    # solvency: criteria matched one to one, by amount or by multiple and base
    gold = _gold(_solv(_c("turnover", x=1.5, base="annual_value")))
    assert v(
        "economic_solvency", gold, _solv(_c("turnover", 229079, 1.5, "annual_value")), "verified"
    )
    assert not v(
        "economic_solvency", gold, _solv(_c("insurance", x=1.5, base="annual_value")), "verified"
    )
    alternatives = _gold(_solv(_c("turnover", 90000), _c("insurance", 75000), any_of=True))
    pred = _solv(_c("insurance", 75000), _c("turnover", 90000), any_of=True)
    assert v("economic_solvency", alternatives, pred, "verified")  # order does not matter
    assert not v("economic_solvency", alternatives, {**pred, "any_of": False}, "verified")
    # certifications: by number, ENS by category
    gold_certs = _gold(["ISO/IEC 27001", "ENS categoría MEDIA"])
    assert v(
        "certifications",
        gold_certs,
        ["ISO 27001 en vigor", "Esquema Nacional de Seguridad, media"],
        "verified",
    )
    assert not v("certifications", gold_certs, ["ISO 27001"], "verified")
    # absent or waived: right unless the model reports a cited requirement
    assert v("classification", _gold(None, page=None), None, "not_found")
    assert v("classification", _gold(None, waived=True), None, "explicit_none")
    assert not v("classification", _gold(None, page=None), "Grupo V subgrupo 2", "verified")
    assert v("economic_solvency", _gold(None, waived=True), _solv(), "verified")


def test_a_done_label_must_be_complete(tmp_path: Path) -> None:
    base: dict[str, Any] = {
        "entry_id": "…/1",
        "title": "t",
        "split": "dev",
        "status": "done",
        "fields": {"budget_eur": {"value": 1, "where": "absent", "checked": None}},
    }
    with pytest.raises(ValueError, match="decision"):
        Label.model_validate(base)
    (tmp_path / "1.yaml").write_text(yaml.safe_dump(base | {"status": "draft"}), "utf-8")
    assert load_labels(tmp_path) == []  # drafts are not scored
    assert len(load_labels(tmp_path, drafts=True)) == 1


def _score(conf: float | None, ok: bool, by: str = "model", cited: bool = True) -> TenderScore:
    return TenderScore("1", "dev", "go", "go" if ok else "no_go", "auto", by, conf, cited, [])


def test_the_threshold_is_the_lowest_that_reaches_the_target_precision() -> None:
    scores = [
        _score(0.95, True),
        _score(0.9, True),
        _score(0.8, True),
        _score(0.7, False),  # below 0.8 the model makes mistakes
        _score(0.99, True, cited=False),  # uncited: never auto, whatever its confidence
        _score(1.0, True, by="requirements"),  # by code: not subject to the threshold
    ]
    curve = cascade_curve(scores)
    assert pick_threshold(curve) == 0.8
    at = {p.threshold: p for p in curve}
    assert (at[0.8].auto, at[0.8].correct) == (3, 3)
    assert at[0.8].review_share == pytest.approx(2 / 6)  # the uncited one and the 0.7 one
    assert at[0.7].precision == 0.75
    assert wilson_lower(3, 3) < 0.5  # three out of three is not proof of 95 %


@pytest.mark.usefixtures("with_pliego")
def test_a_perfect_run_scores_perfectly_and_finds_every_labelled_quote(
    cfg: AppConfig, policia: Tender
) -> None:
    """End to end: the graph on the synthetic pliego, its saved record, a label written from the
    pliego, and the score."""
    dec = ScriptedLLM([decider("go", 0.95, "No se exige clasificación empresarial.")])
    a = asyncio.run(analyse(_deps(cfg, ScriptedLLM([extractor(GOOD)] * N), dec), policia))
    fields = {}
    for name in ("budget_eur", "economic_solvency", "technical_solvency", "required_staff"):
        value, quote = GOOD[name]
        page = next(c.page for c in a.state["chunks"].values() if quote and quote in c.text)
        fields[name] = FieldLabel(
            value=value, where=Where(doc="PCAP", page=page, quote=quote), checked="confirmed"
        )
    fields["classification"] = FieldLabel(
        value=None,
        where=Where(doc="PCAP", page=2, quote="No se exige clasificación", waived=True),
        checked="confirmed",
    )
    label = Label(
        entry_id=policia.entry_id,
        title=policia.title,
        split="dev",
        status="done",
        labelled_by="test",
        labelled_on="2026-11-20",
        decision="go",
        deciding_clause=Where(doc="PCAP", page=2, quote="No se exige clasificación"),
        fields=fields,
    )
    s = score_tender(label, record(a), a.state["chunks"])
    assert s.decision_correct and s.unlocatable == []
    assert all(f.correct for f in s.fields), [f for f in s.fields if not f.correct]
    assert all(f.retrieved for f in s.fields if f.retrieved is not None)
    assert report([s]).summary()["decision_accuracy"] == 1.0
