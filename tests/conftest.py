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
