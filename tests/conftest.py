from __future__ import annotations

from pathlib import Path

import pytest

from licita.placsp.feed import FeedPage, parse_page

FIXTURES = Path(__file__).parent / "fixtures" / "placsp"
HEAD = FIXTURES / "head.atom"
PAGE2 = FIXTURES / "licitacionesPerfilesContratanteCompleto3_20261114_200211.atom"


@pytest.fixture
def head() -> FeedPage:
    return parse_page(HEAD.read_bytes())


@pytest.fixture
def page2() -> FeedPage:
    return parse_page(PAGE2.read_bytes())


PLIEGOS = Path(__file__).parent / "fixtures" / "pliegos"
PAGE_BREAK = "--- página ---"


def make_pdf(path: Path, text: str) -> Path:
    """A real PDF from plain text; a line `--- página ---` starts a new page. Built at test
    time, so the synthetic pliego stays a readable .txt in the repo."""
    import pymupdf

    doc = pymupdf.open()
    for page_text in text.split(PAGE_BREAK):
        page = doc.new_page()
        page.insert_textbox(pymupdf.Rect(50, 50, 545, 800), page_text.strip(), fontsize=10)
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def pcap_pdf(tmp_path: Path) -> Path:
    text = (PLIEGOS / "pcap_sintetico.txt").read_text(encoding="utf-8")
    return make_pdf(tmp_path / "PCAP.pdf", text)
