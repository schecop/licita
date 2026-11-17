"""The company profile (fictional) that tenders are matched against."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Range(_Strict):
    min: Decimal
    max: Decimal


class Solvency(_Strict):
    annual_turnover_eur: Decimal
    staff: int
    similar_contracts_last_3y_eur: Decimal
    certifications: list[str]
    ens_level: str | None
    classification: str | None


class CompanyProfile(_Strict):
    name: str
    city: str
    summary: str
    cpv_prefixes: list[str]
    budget_eur: Range
    regions: list[str]
    solvency: Solvency
    languages: list[str]


def load_profile(path: Path = Path("data/company/profile.yaml")) -> CompanyProfile:
    return CompanyProfile.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
