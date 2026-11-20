from __future__ import annotations

from pathlib import Path

from licita.decide.requirements import check_requirements
from licita.documents.chunk import Chunk
from licita.extract.citations import check_field, quote_in
from licita.profile import load_profile

TEXT = (
    "4. SOLVENCIA ECONÓMICA\nLos licitadores deberán acreditar un volumen anual de negocios\n"
    "por importe igual o superior a 360.000,00 euros."
)
CHUNKS = {"PCAP:p2:0": Chunk("PCAP:p2:0", "PCAP", 2, TEXT)}


def test_quote_matches_across_case_accents_line_breaks_and_ellipsis() -> None:
    assert quote_in("solvencia economica", TEXT)
    assert quote_in("volumen anual de negocios por importe", TEXT)
    assert quote_in("deberán acreditar ... 360.000,00 euros", TEXT)
    assert not quote_in("360.000,00 euros ... deberán acreditar", TEXT)  # order matters
    assert not quote_in("", TEXT)


def test_field_checks() -> None:
    def check(raw: dict[str, object]) -> str:
        return check_field("f", raw, CHUNKS).check

    assert (
        check({"value": 360000, "chunk_id": "PCAP:p2:0", "quote": "360.000,00 euros"}) == "verified"
    )
    assert check({"value": None, "chunk_id": None, "quote": None}) == "not_found"
    assert check({"value": 360000, "chunk_id": None, "quote": None}) == "uncited"
    assert (
        check({"value": 1, "chunk_id": "PCAP:p9:9", "quote": "360.000,00 euros"}) == "unknown_chunk"
    )
    assert check({"value": 1, "chunk_id": "PCAP:p2:0", "quote": "1.000.000 euros"}) == "bad_quote"


def test_a_certification_the_company_lacks_is_a_plain_fail() -> None:
    raw = {"value": ["ISO 14001"], "chunk_id": "PCAP:p2:0", "quote": "volumen anual de negocios"}
    fields = {"certifications": check_field("certifications", raw, CHUNKS)}
    [req] = check_requirements(fields, load_profile(Path("data/company/profile.yaml")))
    assert (req.verdict, req.name) == ("fail", "certification ISO 14001")


def _certs(*certs: str) -> dict[str, str]:
    raw = {"value": list(certs), "chunk_id": "PCAP:p2:0", "quote": "volumen anual de negocios"}
    fields = {"certifications": check_field("certifications", raw, CHUNKS)}
    profile = load_profile(Path("data/company/profile.yaml"))  # ENS medio
    return {r.name: r.verdict for r in check_requirements(fields, profile)}


def test_ens_is_compared_by_category() -> None:
    assert _certs("ENS categoría MEDIA", "ENS categoría ALTA", "Esquema Nacional de Seguridad") == {
        "certification ENS categoría MEDIA": "pass",
        "certification ENS categoría ALTA": "fail",
        "certification Esquema Nacional de Seguridad": "open",  # no category named
    }
    # the RD's year is not taken for a certification number
    assert _certs("ENS (RD 311/2022) categoría BÁSICA") == {
        "certification ENS (RD 311/2022) categoría BÁSICA": "pass"
    }


def test_solvency_not_read_from_the_pliego_is_unestablished() -> None:
    from licita.decide.requirements import unestablished

    ok = {"value": 360000, "chunk_id": "PCAP:p2:0", "quote": "360.000,00 euros"}
    lie = {"value": 1, "chunk_id": "PCAP:p2:0", "quote": "1.000.000 euros"}
    fields = {
        "min_annual_turnover_eur": check_field("min_annual_turnover_eur", ok, CHUNKS),
        "min_similar_services_eur": check_field("min_similar_services_eur", lie, CHUNKS),
    }
    assert unestablished(fields) == ["min_similar_services_eur (bad_quote)"]
    assert unestablished({}) == [
        "min_annual_turnover_eur (missing)",
        "min_similar_services_eur (missing)",
    ]
