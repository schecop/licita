"""Typed config from YAML. Tunables and the model per role live here, never in code (rule 7)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ModelChoice(_Strict):
    """Provider and model for one role (screening, extraction, decision, coach, embeddings)."""

    provider: str  # a key of licita.llm.factory.PROVIDERS
    model: str
    # provider-specific request fields, passed through as-is (reasoning effort, thinking)
    options: dict[str, Any] = {}
    # how a role that needs structured output (extraction, decision) gets it: a tool call, or
    # JSON in the text (for models that cannot reason and call tools in the same request, e.g.
    # GPT-5.x on Chat Completions)
    structured_output: Literal["tool", "json"] = "tool"
    # list prices, for the cost of every call (cost per tender analysed, per eval pass)
    usd_per_mtok_in: Decimal = Field(ge=0)
    usd_per_mtok_out: Decimal = Field(ge=0)


class AppConfig(_Strict):
    llm: dict[str, ModelChoice]


def load_config(path: Path = Path("config/local.yaml")) -> AppConfig:
    """`config/local.yaml` (gitignored) if present, else the committed example."""
    if not path.exists():
        path = Path("config/example.yaml")
    return AppConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
