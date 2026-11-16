"""The committed example config and the fictional company profile load and stay consistent."""

from __future__ import annotations

from pathlib import Path

import yaml

from licita.config import load_config

ROLES = {"screening", "extraction", "decision", "coach"}


def test_the_example_config_names_a_model_for_every_role() -> None:
    cfg = load_config(Path("config/example.yaml"))
    assert set(cfg.llm) >= ROLES


def test_the_company_profile_targets_it_services() -> None:
    profile = yaml.safe_load(Path("data/company/profile.yaml").read_text("utf-8"))
    assert all(code.startswith("72") for code in profile["cpv_prefixes"])
    assert profile["budget_eur"]["min"] < profile["budget_eur"]["max"]
