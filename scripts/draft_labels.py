"""Draft one label file per analysed tender, for a person to confirm or correct against the pliego.

    uv run python scripts/draft_labels.py 20836815 20957910 ...

Reads `runs/analyses/<n>.json` (what the model extracted, with its citations) and writes
`data/labels/<n>.yaml` with status `draft`. It never overwrites a label file that exists.

Against anchoring on the model:
- the model's go / no-go and its reason are left out: the labeller decides blind;
- every field carries `checked: null`, to be set to `confirmed` or `corrected` after looking at
  the page; a label where nearly everything is `confirmed` deserves a second look;
- a field the model did not find gets the pages retrieval returned for it, as places to look,
  not as an answer.

The split (dev / heldout) is fixed here, balanced as labels are added, and never changed: the
coach never sees heldout (CLAUDE.md).
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import yaml

ANALYSES = Path("runs/analyses")
LABELS = Path("data/labels")
# the fields that weigh on the decision, plus the two amounts the feed also gives (a cross-check)
FIELDS = {
    "economic_solvency": "solvency",
    "technical_solvency": "solvency",
    "certifications": "solvency",
    "classification": "solvency",
    "contract_object": "scope",
    "required_staff": "scope",
    "budget_eur": "economics",
    "estimated_value_eur": "economics",
    "max_duration_months": "economics",
}


def split(entry: str) -> str:
    """To the smaller half so far; a hash of the entry number breaks ties. A hash alone gave 8 of
    the first 9 to heldout."""
    existing = [yaml.safe_load(p.read_text("utf-8")).get("split") for p in LABELS.glob("*.yaml")]
    dev, heldout = existing.count("dev"), existing.count("heldout")
    if dev != heldout:
        return "dev" if dev < heldout else "heldout"
    return "heldout" if int(hashlib.sha256(entry.encode()).hexdigest(), 16) % 2 else "dev"


def _where(citation: dict[str, Any] | None) -> dict[str, Any] | None:
    if not citation:
        return None
    return {
        "doc": citation["chunk"].split(":", 1)[0],
        "page": citation["page"],
        "quote": citation["quote"],
    }


def _look_at(hits: list[dict[str, Any]]) -> list[str]:
    pages: list[str] = []
    for h in hits:
        doc, page = h["chunk"].split(":")[:2]
        if (p := f"{doc} {page}") not in pages:
            pages.append(p)
    return pages[:4]


def draft(number: str) -> dict[str, Any]:
    a = json.loads((ANALYSES / f"{number}.json").read_text("utf-8"))
    fields: dict[str, Any] = {}
    for name, group in FIELDS.items():
        f = a["fields"].get(name, {"value": None, "check": "missing", "citation": None})
        entry: dict[str, Any] = {
            "value": f["value"],
            "where": _where(f["citation"]),
            "model_check": f["check"],
            "checked": None,  # confirmed | corrected, once you have looked at the page
        }
        if f["check"] == "explicit_none":
            entry["where"]["waived"] = True
        if not f["citation"]:
            entry["look_at"] = _look_at(a["hits"].get(group, []))
        fields[name] = entry
    return {
        "entry_id": a["entry_id"],
        "title": a["title"],
        "split": split(number),
        "status": "draft",  # draft -> done
        "labelled_by": None,
        "labelled_on": None,
        "decision": None,  # go | no_go: yours, decided without looking at the model's
        "deciding_clause": None,  # {doc, page, quote}
        "fields": fields,
        "notes": "",
    }


def main(numbers: list[str]) -> None:
    LABELS.mkdir(parents=True, exist_ok=True)
    for n in numbers:
        out = LABELS / f"{n}.yaml"
        if out.exists():
            print(f"{out}: exists, left as it is")
            continue
        label = draft(n)
        text = yaml.safe_dump(label, allow_unicode=True, sort_keys=False, width=100)
        out.write_text(text, "utf-8")
        print(f"{out}: drafted ({label['split']})")


if __name__ == "__main__":
    main(sys.argv[1:])
