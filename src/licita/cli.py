"""`licita` command line. Subcommands arrive with their slice (ingest, analyse, eval, coach)."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from licita import budget


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="licita",
        description="Match public tenders (PLACSP) against a company profile, with cited go/no-go.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("budget", help="show what the paid API calls have cost so far")
    args = parser.parse_args(argv)
    if args.command == "budget":
        print(f"spent ${budget.spent():.4f} of ${budget.CAP_USD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
