"""Score saved analyses (runs/analyses/) against labels (data/labels/). No model calls.

Per tender:
- each labelled field: is the value right (rules per field, below) and is the citation on the
  labelled page;
- retrieval: was a chunk holding the labelled quote among the chunks the field's group query
  returned (recall@k). Chunks are matched by the quote, not by id, so labels outlive chunking;
- the decision: right or wrong, and who made it (rules, requirements, model, none).

Across tenders, besides accuracies:
- precision of the no-gos made by code (rules and requirements): automatic and not subject to the
  threshold, so they are measured on their own;
- the cascade: for each threshold, the precision of the model decisions it would auto-accept
  (cited, confidence >= threshold) and the share of tenders left for review. The threshold to use
  is the lowest one whose auto-accepted model decisions reach the target precision (CLAUDE.md:
  >= 95%), reported with its sample size and a Wilson lower bound, because with ~10 tenders a
  95% is one decision away from 85%.

Value rules: amounts within 0.5% (or 1 €); solvency criteria matched one to one by kind and
amount (or multiple and base), with the same any_of; certifications as sets of normalised keys
(the 4-5 digit number, or ENS with its category); classification required or not; free text
(object, staff) is scored on presence only: whether the model found it where the label says.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from licita.documents.chunk import Chunk
from licita.evals.labels import FieldLabel, Label, Where
from licita.extract.citations import quote_in
from licita.extract.fields import GROUPS
from licita.retrieval.text import fold

GROUP_OF = {f.name: g.name for g in GROUPS for f in g.fields}
AMOUNTS = {"budget_eur", "estimated_value_eur", "max_duration_months", "duration_months"}
PRESENCE_ONLY = {"contract_object", "required_staff"}
TARGET_PRECISION = 0.95


@dataclass(frozen=True)
class FieldScore:
    name: str
    correct: bool
    located: bool | None  # citation on the labelled page; None when there is nothing to locate
    retrieved: bool | None  # labelled quote among the group's hits; None if absent/unlocatable
    gold: Any
    predicted: Any
    model_check: str


@dataclass(frozen=True)
class TenderScore:
    number: str
    split: str
    gold_decision: str
    decision: str | None
    route: str
    decided_by: str
    confidence: float | None
    cited: bool
    fields: list[FieldScore]
    unlocatable: list[str] = field(default_factory=list)  # labelled quotes found in no chunk

    @property
    def decision_correct(self) -> bool:
        return self.decision == self.gold_decision


def _close(a: Any, b: Any) -> bool:
    try:
        x, y = float(a), float(b)
    except (TypeError, ValueError):
        return False
    return abs(x - y) <= max(1.0, 0.005 * max(abs(x), abs(y)))


def _criteria(v: Any) -> tuple[bool, list[Mapping[str, Any]]]:
    if not isinstance(v, Mapping):
        return False, []
    return bool(v.get("any_of")), [c for c in v.get("criteria") or [] if isinstance(c, Mapping)]


def _same_criterion(g: Mapping[str, Any], p: Mapping[str, Any]) -> bool:
    if g.get("kind") != p.get("kind"):
        return False
    if g.get("amount_eur") is not None:
        return _close(g["amount_eur"], p.get("amount_eur"))
    if g.get("multiple") is not None:
        return _close(g["multiple"], p.get("multiple")) and g.get("base") == p.get("base")
    return p.get("amount_eur") is None and p.get("multiple") is None


def _same_solvency(gold: Any, pred: Any) -> bool:
    g_any, g = _criteria(gold)
    p_any, p = _criteria(pred)
    if len(g) != len(p) or (len(g) > 1 and g_any != p_any):
        return False
    left = list(p)
    for gc in g:
        match = next((pc for pc in left if _same_criterion(gc, pc)), None)
        if match is None:
            return False
        left.remove(match)
    return True


def _cert_keys(certs: Any) -> set[str]:
    keys = set()
    for c in certs or []:
        text = fold(str(c))
        if "esquema nacional de seguridad" in text or re.search(r"\bens\b", text):
            level = re.search(r"\b(basic|medi|alt)", text)
            keys.add(f"ens:{level.group(1) if level else '?'}")
        elif numbers := re.findall(r"\d{4,5}", text):
            keys.add(numbers[0])
        else:
            keys.add(text)
    return keys


def _empty(v: Any) -> bool:
    if isinstance(v, Mapping):
        return not v.get("criteria")
    return v in (None, [], "")


def value_correct(name: str, gold: FieldLabel, pred_value: Any, pred_check: str) -> bool:
    if gold.absent:  # not required: right unless the model reports a cited requirement
        return not (pred_check == "verified" and not _empty(pred_value))
    if pred_check not in ("verified", "explicit_none"):
        return False
    if name in AMOUNTS:
        return _close(gold.value, pred_value)
    if name in ("economic_solvency", "technical_solvency"):
        return _same_solvency(gold.value, pred_value)
    if name == "certifications":
        return _cert_keys(gold.value) == _cert_keys(pred_value)
    if name == "classification":
        return pred_value is not None
    return pred_value not in (None, [], "")  # presence only


def _where(c: Mapping[str, Any] | None) -> tuple[str, int] | None:
    return None if not c else (c["chunk"].split(":", 1)[0], int(c["page"]))


def score_tender(
    label: Label, analysis: Mapping[str, Any], chunks: Mapping[str, Chunk]
) -> TenderScore:
    """`chunks`: every chunk of the tender's pliegos, by id, as the analysis made them."""
    hits = {g: {h["chunk"] for h in hs} for g, hs in analysis.get("hits", {}).items()}
    fields, unlocatable = [], []
    for name, gold in label.fields.items():
        pred = analysis.get("fields", {}).get(name, {"value": None, "check": "missing"})
        located = retrieved = None
        if isinstance(gold.where, Where) and not gold.where.waived:
            w = gold.where
            located = _where(pred.get("citation")) == (w.doc, w.page)
            holding = {
                cid
                for cid, c in chunks.items()
                if c.doc == w.doc and c.page == w.page and quote_in(w.quote, c.text)
            }
            if holding:
                retrieved = bool(holding & hits.get(GROUP_OF.get(name, ""), set()))
            else:
                unlocatable.append(name)
        fields.append(
            FieldScore(
                name,
                value_correct(name, gold, pred.get("value"), pred.get("check", "missing")),
                located,
                retrieved,
                gold.value,
                pred.get("value"),
                pred.get("check", "missing"),
            )
        )
    o = analysis["outcome"]
    assert label.decision is not None
    return TenderScore(
        label.number,
        label.split,
        label.decision,
        o["decision"],
        o["route"],
        o["by"],
        o.get("confidence"),
        o.get("citation") is not None,
        fields,
        unlocatable,
    )


def wilson_lower(successes: int, n: int, z: float = 1.96) -> float:
    if n == 0:
        return 0.0
    p = successes / n
    centre = p + z * z / (2 * n)
    spread = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (centre - spread) / (1 + z * z / n)


@dataclass(frozen=True)
class CascadePoint:
    threshold: float
    auto: int  # model decisions auto-accepted at this threshold
    correct: int
    review_share: float  # of all tenders: neither a code decision nor auto-accepted

    @property
    def precision(self) -> float | None:
        return self.correct / self.auto if self.auto else None


def cascade_curve(
    scores: Sequence[TenderScore], thresholds: Iterable[float] | None = None
) -> list[CascadePoint]:
    model = [s for s in scores if s.decided_by == "model" and s.decision and s.cited]
    by_code = sum(1 for s in scores if s.decided_by in ("rules", "requirements") and s.decision)
    grid = sorted(
        set(thresholds or [0.5, 0.6, 0.7, 0.8, 0.85, 0.9, 0.95])
        | {s.confidence for s in model if s.confidence is not None}
    )
    points = []
    for t in grid:
        auto = [s for s in model if (s.confidence or 0) >= t]
        review = len(scores) - by_code - len(auto)
        points.append(
            CascadePoint(
                t,
                len(auto),
                sum(s.decision_correct for s in auto),
                review / len(scores) if scores else 0.0,
            )
        )
    return points


def pick_threshold(curve: Sequence[CascadePoint], target: float = TARGET_PRECISION) -> float | None:
    """The lowest threshold whose auto-accepted model decisions reach the target precision."""
    ok = [p for p in curve if p.auto and (p.precision or 0) >= target]
    return min((p.threshold for p in ok), default=None)


@dataclass
class Report:
    scores: list[TenderScore]
    curve: list[CascadePoint]
    threshold: float | None

    def field_table(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for name in dict.fromkeys(f.name for s in self.scores for f in s.fields):
            fs = [f for s in self.scores for f in s.fields if f.name == name]
            loc = [f.located for f in fs if f.located is not None]
            ret = [f.retrieved for f in fs if f.retrieved is not None]
            out[name] = {
                "n": len(fs),
                "value": sum(f.correct for f in fs) / len(fs),
                "located": sum(loc) / len(loc) if loc else None,
                "retrieved": sum(ret) / len(ret) if ret else None,
                "retrieved_n": len(ret),
            }
        return out

    def summary(self) -> dict[str, Any]:
        n = len(self.scores)
        code = [s for s in self.scores if s.decided_by in ("rules", "requirements") and s.decision]
        code_ok = sum(s.decision_correct for s in code)
        chosen = next((p for p in self.curve if p.threshold == self.threshold), None)
        return {
            "tenders": n,
            "decision_accuracy": sum(s.decision_correct for s in self.scores) / n if n else None,
            "code_decisions": len(code),
            "code_precision": code_ok / len(code) if code else None,
            "code_precision_wilson_low": wilson_lower(code_ok, len(code)) if code else None,
            "threshold": self.threshold,
            "auto_model_decisions": chosen.auto if chosen else 0,
            "auto_precision_wilson_low": (
                wilson_lower(chosen.correct, chosen.auto) if chosen else None
            ),
            "review_share": chosen.review_share if chosen else None,
            "unlocatable_quotes": sum(len(s.unlocatable) for s in self.scores),
        }


def report(scores: list[TenderScore]) -> Report:
    curve = cascade_curve(scores)
    return Report(scores, curve, pick_threshold(curve))
