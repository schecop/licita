"""Where a tender's documents (pliegos) come from: a local folder per tender, filled by hand.

PLACSP's document servlet rejects scripted clients (D1, `FAILURES.md`), so for now the PDFs are
downloaded in a browser into `data/pliegos/<entry number>/`, where `<entry number>` is the last
part of the ATOM entry id (…/licitacionesPerfilContratante/20913966 -> `20913966`). Every PDF in
that folder is one document of the tender; its file name (stem) is the document name a citation
shows. A downloader can replace this later behind the same function.
"""

from __future__ import annotations

from pathlib import Path


def tender_folder(root: Path, entry_id: str) -> Path:
    return root / entry_id.rstrip("/").rsplit("/", 1)[-1]


def local_documents(root: Path, entry_id: str) -> list[Path]:
    folder = tender_folder(root, entry_id)
    return sorted(folder.glob("*.pdf")) if folder.is_dir() else []
