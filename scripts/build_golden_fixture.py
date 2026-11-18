"""Copy the golden tenders' entries verbatim out of the cached feed, and write a blank golden.yaml.

Run once: `uv run python scripts/build_golden_fixture.py`. It writes:
- `tests/fixtures/placsp/golden.atom`: the newest cached version of each entry, unedited.
- `tests/fixtures/placsp/golden.yaml` (only if missing): one block per tender with its human PLACSP
  URL and every value as TODO. The values are filled in by reading the PLACSP web page, never the
  XML: the point is a source independent of the one the parser reads.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from licita.placsp.feed import parse_page

CACHE = Path("data/raw/atom")
OUT = Path("tests/fixtures/placsp")
BASE = "https://contrataciondelestado.es/sindicacion/licitacionesPerfilContratante/"

# entry id suffix -> why it is in the set
GOLDEN = {
    "20913966": "IT candidate (Policía, Opentext): the reference case",
    "20913952": "5 lots with different budgets",
    "20913957": "lots that repeat the whole budget (source quirk), adjudicated",
    "20914016": "open tender without CPV",
    "20934361": "open tender without documents",
    "20913951": "patrimonial (type 50)",
    "20913962": "works (type 3)",
    "20913968": "cancelled (ANUL)",
    "20913949": "resolved (RES), no deadline",
    "20913955": "Cataluña (NUTS ES51x)",
    "20913994": "award criteria with decimal weights",
}
FIELDS = (
    "status",
    "buyer",
    "budget_eur",
    "estimated_value_eur",
    "deadline",
    "cpvs",
    "nuts",
    "lot_budgets_eur",
    "award_criteria",
)


def main() -> None:
    newest: dict[str, tuple[str, str]] = {}  # entry id -> (updated, raw entry)
    for page in sorted(CACHE.glob("*.atom")):
        for raw in re.findall(r"<entry[ >].*?</entry>", page.read_text(encoding="utf-8"), re.S):
            m = re.search(r"<id>([^<]+)</id>", raw)
            u = re.search(r"<updated>([^<]+)</updated>", raw)
            if not (m and u and m.group(1).removeprefix(BASE) in GOLDEN):
                continue
            if m.group(1) not in newest or u.group(1) > newest[m.group(1)][0]:
                newest[m.group(1)] = (u.group(1), raw)
    missing = {BASE + k for k in GOLDEN} - newest.keys()
    if missing:
        raise SystemExit(f"not in the cache: {sorted(missing)}")
    head = next(iter(sorted(CACHE.glob("*.atom")))).read_text(encoding="utf-8")
    head = re.sub(
        r"<at:deleted-entry.*?</at:deleted-entry>", "", head[: head.find("<entry")], flags=re.S
    )
    head = re.sub(r'<link href="[^"]*" rel="next"/>', "", head)
    body = "\n".join(newest[BASE + k][1] for k in GOLDEN)
    (OUT / "golden.atom").write_text(head + body + "\n</feed>\n", encoding="utf-8")

    target = OUT / "golden.yaml"
    if target.exists():
        print(f"kept {target} (already filled in?)")
        return
    tenders = {t.entry_id: t for t in parse_page((OUT / "golden.atom").read_bytes()).tenders}
    blocks = [
        {
            "entry_id": BASE + k,
            "why": why,
            "url": tenders[BASE + k].url,
            "checked_on": None,  # date the values were read on the web page; null = skipped
            **dict.fromkeys(FIELDS, "TODO"),
        }
        for k, why in GOLDEN.items()
    ]
    header = (
        "# Golden values read by hand from each tender's PLACSP web page (not from the XML).\n"
        "# A key set to null means the page shows nothing for it. Set checked_on when done.\n"
        "# deadline: 'YYYY-MM-DD HH:MM' Madrid time.\n"
        "# award_criteria: [[start of the description, weight]].\n"
    )
    target.write_text(
        header + yaml.safe_dump(blocks, allow_unicode=True, sort_keys=False, width=100),
        encoding="utf-8",
    )
    print(f"wrote {target} with {len(blocks)} tenders to fill in")


if __name__ == "__main__":
    main()
