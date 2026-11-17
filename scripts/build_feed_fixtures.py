"""Cut two small feed pages for the tests out of a real cached PLACSP ATOM page.

Run once: `uv run python scripts/build_feed_fixtures.py data/raw/atom/2026-11-16-head.atom`.
Entries are copied verbatim (public data) except two edits to page 2, which exist to test the
store: an older PRE version of the Policía tender (keep-newest) and one entry dated before the
ingest window (pagination stops there).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

OUT = Path("tests/fixtures/placsp")
NEXT = "licitacionesPerfilesContratanteCompleto3_20261114_200211.atom"


def main(source: Path) -> None:
    src = source.read_text(encoding="utf-8")
    head = src[: src.find("<entry")]
    entries = re.findall(r"<entry[ >].*?</entry>", src, re.S)

    def find(*needles: str) -> str:
        for e in entries:
            if all(n in e for n in needles):
                return e
        raise SystemExit(f"not found: {needles}")

    def redate(entry: str, when: str) -> str:
        return re.sub(r"<updated>[^<]+</updated>", f"<updated>{when}</updated>", entry)

    policia = find("Opentext", ">PUB<")
    page1 = [
        policia,  # IT, €89k, Madrid: candidate
        find("Seguridad Social", ">PUB<", "Gerencia de Inform"),  # IT, €54M: over budget
        find("ntabro de Salud", ">PUB<"),  # IT, €1.2M, Cantabria: budget + region
        find("backup", ">PUB<"),  # IT, €176k, Madrid: candidate
        find("buzones", ">RES<"),  # resolved: not open
        find("04101/2026/250-O"),  # works (CPV 45): not IT
    ]  # the page's one tombstone sits in `head`, before the first entry
    page2 = [
        redate(policia, "2026-11-14T09:00:00.000+01:00").replace(">PUB</", ">PRE</", 1),
        redate(find("Canales del Taibilla", ">PUB<"), "2026-11-12T09:00:00.000+01:00"),
    ]
    head2 = re.sub(r"<at:deleted-entry.*?</at:deleted-entry>", "", head, flags=re.S)
    head2 = head2.replace(NEXT, "licitacionesPerfilesContratanteCompleto3_20261112_000000.atom")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "head.atom").write_text(head + "\n".join(page1) + "\n</feed>\n", encoding="utf-8")
    (OUT / NEXT).write_text(head2 + "\n".join(page2) + "\n</feed>\n", encoding="utf-8")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
