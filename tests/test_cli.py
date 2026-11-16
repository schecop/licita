from __future__ import annotations

import pytest

from licita.cli import main


def test_budget_reports_the_cap(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["budget"]) == 0
    assert "of $15" in capsys.readouterr().out
