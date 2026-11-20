from __future__ import annotations

import os
from pathlib import Path

import pytest

from licita.cli import main
from licita.env import load_env


def test_dotenv_fills_missing_and_empty_variables_but_not_set_ones(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dotenv = tmp_path / "secrets.env"
    dotenv.write_text("# comment\nLICITA_A='from file'\nLICITA_B=from file\nLICITA_C=x\n", "utf-8")
    monkeypatch.setenv("LICITA_A", "")  # empty in the shell: the file fills it
    monkeypatch.setenv("LICITA_B", "from shell")  # set in the shell: it wins
    monkeypatch.setenv("LICITA_C", "")
    load_env(dotenv)
    assert os.environ["LICITA_A"] == "from file"
    assert os.environ["LICITA_B"] == "from shell"


def test_the_cli_loads_dotenv_before_any_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: load_env existed but nothing called it, so a key set in the file never
    reached the model clients (FAILURES.md, 2026-11-20)."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("LICITA_PROBE_KEY=abc\n", "utf-8")
    monkeypatch.setenv("LICITA_PROBE_KEY", "")
    assert main(["budget"]) == 0
    assert os.environ["LICITA_PROBE_KEY"] == "abc"
