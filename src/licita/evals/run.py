"""`licita eval`: score the saved analyses against the done labels and print the numbers.

No model is called: an analysis is made once (`licita analyse`, paid) and scored as often as the
labels change. The pliegos are re-chunked to find each labelled quote; chunking is deterministic,
so the chunk ids match the ones the analysis saw as long as the chunking code and
`analysis.chunk_max_chars` have not changed since (a mismatch shows up as unlocatable quotes).
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from licita.config import AppConfig
from licita.documents.chunk import Chunk, chunk_pages
from licita.documents.pdf import read_pdf
from licita.documents.source import local_documents
from licita.evals.labels import Split, load_labels
from licita.evals.score import Report, TenderScore, report, score_tender

EVALS = Path("runs/evals")


def _chunks(cfg: AppConfig, entry_id: str) -> dict[str, Chunk]:
    out: dict[str, Chunk] = {}
    for pdf in local_documents(cfg.analysis.pliegos_dir, entry_id):
        for c in chunk_pages(pdf.stem, read_pdf(pdf), cfg.analysis.chunk_max_chars):
            out[c.id] = c
    return out


def _pct(v: float | None) -> str:
    return "  —" if v is None else f"{v:4.0%}"


def print_report(r: Report) -> None:
    s = r.summary()
    print(f"\n{s['tenders']} tenders · decision accuracy {_pct(s['decision_accuracy'])}")
    print(
        f"code no-gos: {s['code_decisions']}, precision {_pct(s['code_precision'])} "
        f"(Wilson low {_pct(s['code_precision_wilson_low'])})"
    )
    print("\nfield                  n  value  page  retrieved@k")
    for name, f in r.field_table().items():
        print(
            f"{name:20} {f['n']:3}  {_pct(f['value'])}  {_pct(f['located'])}  "
            f"{_pct(f['retrieved'])} (n={f['retrieved_n']})"
        )
    print("\ncascade (model decisions, cited)  threshold  auto  precision  to review")
    for p in r.curve:
        mark = "  <= chosen" if p.threshold == r.threshold else ""
        print(
            f"{'':34}{p.threshold:9.2f}  {p.auto:4}  {_pct(p.precision):>9}  "
            f"{p.review_share:9.0%}{mark}"
        )
    if r.threshold is None:
        print("no threshold reaches the target precision with these labels")
    if s["unlocatable_quotes"]:
        print(f"\nwarning: {s['unlocatable_quotes']} labelled quotes found in no chunk")


def _tender_lines(scores: list[TenderScore]) -> None:
    print("\ntender     gold   model         by            conf  wrong fields")
    for t in scores:
        wrong = [f.name for f in t.fields if not f.correct]
        mark = " " if t.decision_correct else "✗"
        conf = f"{t.confidence:.2f}" if t.confidence is not None else "  — "
        print(
            f"{mark}{t.number}  {t.gold_decision:6} {t.decision or '—':6} {t.route:6} "
            f"{t.decided_by:13} {conf}  {', '.join(wrong) or '-'}"
        )


def run_eval(
    cfg: AppConfig,
    split: Split | None,
    labels_dir: Path = Path("data/labels"),
    analyses_dir: Path = Path("runs/analyses"),
) -> int:
    labels = load_labels(labels_dir, split)
    drafts = len(load_labels(labels_dir, split, drafts=True)) - len(labels)
    print(f"{len(labels)} labels done ({split or 'all splits'}), {drafts} still drafts")
    checked = [f.checked for lb in labels for f in lb.fields.values()]
    if checked:
        kept = checked.count("confirmed") / len(checked)
        print(f"fields confirmed as drafted: {kept:.0%} (corrected {1 - kept:.0%})")
        if kept > 0.9:
            print("warning: nearly every field kept the model's draft; check for anchoring")
    scores, missing = [], []
    for label in labels:
        path = analyses_dir / f"{label.number}.json"
        if not path.exists():
            missing.append(label.number)
            continue
        analysis = json.loads(path.read_text("utf-8"))
        scores.append(score_tender(label, analysis, _chunks(cfg, label.entry_id)))
    if missing:
        print(f"not analysed yet (run `licita analyse`): {', '.join(missing)}")
    if not scores:
        return 0
    r = report(scores)
    _tender_lines(scores)
    print_report(r)
    EVALS.mkdir(parents=True, exist_ok=True)
    out = EVALS / f"eval-{split or 'all'}-{datetime.now(UTC):%Y%m%dT%H%M%S}.json"
    data: dict[str, Any] = {
        "split": split,
        "summary": r.summary(),
        "fields": r.field_table(),
        "cascade": [asdict(p) | {"precision": p.precision} for p in r.curve],
        "tenders": [asdict(t) for t in scores],
    }
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str), "utf-8")
    print(f"\nsaved {out}")
    return 0
