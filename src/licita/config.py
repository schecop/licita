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


class PlacspConfig(_Strict):
    """Feed ingestion. PLACSP is a public service: cache everything, throttle the rest."""

    feed_url: str = (
        "https://contrataciondelestado.es/sindicacion/sindicacion_643/"
        "licitacionesPerfilesContratanteCompleto3.atom"
    )
    cache_dir: Path = Path("data/raw/atom")
    db_path: Path = Path("data/licita.db")
    throttle_seconds: float = Field(default=2.0, ge=1.0)
    max_pages: int = Field(default=20, ge=1)
    # largest share of open tenders allowed to miss each field before the audit warns
    # (licita.placsp.audit has the baseline these come from)
    audit_max_missing: dict[str, float] = {
        "title": 0.01,
        "buyer": 0.01,
        "budget_eur": 0.02,
        "estimated_value_eur": 0.02,
        "deadline": 0.02,
        "cpvs": 0.05,
        "nuts": 0.05,
        "documents": 0.10,
        "award_criteria": 0.20,
    }


class RulesConfig(_Strict):
    profile_path: Path = Path("data/company/profile.yaml")
    # fewer days than this to the submission deadline: not enough time to prepare a bid
    min_days_to_deadline: int = Field(default=5, ge=0)


class AppConfig(_Strict):
    llm: dict[str, ModelChoice]
    placsp: PlacspConfig = PlacspConfig()
    rules: RulesConfig = RulesConfig()


def load_config(path: Path = Path("config/local.yaml")) -> AppConfig:
    """`config/local.yaml` (gitignored) if present, else the committed example."""
    if not path.exists():
        path = Path("config/example.yaml")
    return AppConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
