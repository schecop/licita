"""The label files (data/labels/<entry number>.yaml): format, validation, loading.

A label is the truth for one tender: the go / no-go a consultant would give the company, the
clause that weighs most, and the value and place of each field that weighs on the decision.
Places are document + page + a literal quote, never a chunk id, so a label survives a change in
chunking: the evals find the quote again in whatever chunks there are. See data/labels/README.md.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, model_validator

Split = Literal["dev", "heldout"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Where(_Strict):
    doc: str
    page: int
    quote: str
    waived: bool = False  # the pliego says explicitly it is not required


class FieldLabel(_Strict):
    value: Any = None
    where: Where | Literal["absent"] | None = None  # None only in a draft
    checked: Literal["confirmed", "corrected"] | None = None
    # left by the draft, for the labeller: ignored by the scoring
    model_check: str | None = None
    look_at: list[str] | None = None

    @property
    def absent(self) -> bool:
        """The pliego does not ask for it: not mentioned, or explicitly waived."""
        return self.where == "absent" or (isinstance(self.where, Where) and self.where.waived)


class Label(_Strict):
    entry_id: str
    title: str
    split: Split
    status: Literal["draft", "done"]
    labelled_by: str | None = None
    labelled_on: date | None = None
    decision: Literal["go", "no_go"] | None = None
    deciding_clause: Where | None = None
    fields: dict[str, FieldLabel]
    notes: str = ""

    @property
    def number(self) -> str:
        return self.entry_id.rstrip("/").rsplit("/", 1)[-1]

    @model_validator(mode="after")
    def _done_is_complete(self) -> Label:
        if self.status != "done":
            return self
        missing = [
            k
            for k in ("decision", "deciding_clause", "labelled_by", "labelled_on")
            if getattr(self, k) is None
        ]
        missing += [f"fields.{n}.checked" for n, f in self.fields.items() if f.checked is None]
        missing += [f"fields.{n}.where" for n, f in self.fields.items() if f.where is None]
        if missing:
            raise ValueError(f"status is done but these are not filled in: {missing}")
        return self


def load_label(path: Path) -> Label:
    try:
        return Label.model_validate(yaml.safe_load(path.read_text("utf-8")))
    except ValueError as e:
        raise ValueError(f"{path}: {e}") from e


def load_labels(
    folder: Path = Path("data/labels"), split: Split | None = None, drafts: bool = False
) -> list[Label]:
    """Labels with status done (and drafts if asked), optionally one split only."""
    labels = [load_label(p) for p in sorted(folder.glob("*.yaml"))]
    return [
        lb
        for lb in labels
        if (drafts or lb.status == "done") and (split is None or lb.split == split)
    ]
