"""The budget guard: ledger, estimate, refusal; and Metered prices every call it sees."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from licita import budget
from licita.llm.base import UserMessage
from licita.llm.metered import Call, Metered
from licita.llm.scripted import ScriptedLLM, say


def paid(usd: str, role: str = "extraction") -> Call:
    return Call(role, "anthropic", "m", 1000, 100, Decimal(usd), 10.0, "end")


def test_the_ledger_keeps_paid_calls_only_and_sums_them(tmp_path: Path) -> None:
    ledger = tmp_path / "cost.jsonl"
    free = Call("screening", "scripted", "s", 10, 10, Decimal(0), 1.0, "end")
    assert budget.record([paid("0.01"), free, paid("0.02")], "run1", ledger) == Decimal("0.03")
    assert len(ledger.read_text("utf-8").splitlines()) == 2
    assert budget.spent(ledger) == Decimal("0.03")
    assert json.loads(ledger.read_text("utf-8").splitlines()[0])["run"] == "run1"


def test_the_guard_refuses_a_run_that_would_pass_the_cap(tmp_path: Path) -> None:
    ledger = tmp_path / "cost.jsonl"
    budget.record([paid("14.00")], "old", ledger)
    assert "WARNING" in budget.check(Decimal("0.50"), ledger)
    with pytest.raises(budget.BudgetExceeded):
        budget.check(Decimal("1.50"), ledger)


def test_the_estimate_follows_the_assumptions() -> None:
    # 1 x (1500 x 0.10 + 100 x 0.50) / 1e6 = $0.0002 per tender
    one = budget.estimate(1, {"screening": (Decimal("0.10"), Decimal("0.50"))})
    assert one == Decimal("0.0002")
    assert budget.estimate(20, {"screening": (Decimal("0.10"), Decimal("0.50"))}) == 20 * one


def test_metered_prices_each_call_and_scripted_calls_stay_free(tmp_path: Path) -> None:
    reply = replace(say("ok"), input_tokens=2_000_000, output_tokens=1_000_000)
    llm = Metered(ScriptedLLM([reply]), "extraction", Decimal("1"), Decimal("5"))
    asyncio.run(llm.complete(system="s", messages=[UserMessage("hola")]))
    [c] = llm.calls
    assert (c.role, c.usd) == ("extraction", Decimal(7))
    assert "hola" in c.input
