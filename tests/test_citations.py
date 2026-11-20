from __future__ import annotations

from pathlib import Path

from licita.decide.requirements import check_requirements
from licita.documents.chunk import Chunk
from licita.extract.citations import check_field, quote_in
from licita.placsp.feed import FeedPage
from licita.profile import load_profile

PROFILE = load_profile(Path("data/company/profile.yaml"))
CID = "PCAP:p2:0"
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
    # the id as the request prints it, brackets included (seen on a real run)
    assert (
        check({"value": 360000, "chunk_id": "[PCAP:p2:0]", "quote": "360.000,00 euros"})
        == "verified"
    )


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
        "economic_solvency": check_field("economic_solvency", ok, CHUNKS),
        "technical_solvency": check_field("technical_solvency", lie, CHUNKS),
    }
    assert unestablished(fields) == ["technical_solvency (bad_quote)"]
    assert unestablished({}) == [
        "economic_solvency (missing)",
        "technical_solvency (missing)",
    ]


def _economic(
    head: FeedPage, any_of: bool, *criteria: dict[str, object], months: int | None = None
) -> tuple[str, str]:
    """Verdict and reason for the Policía tender (valor estimado €177,677.78)."""
    tender = next(t for t in head.tenders if "Opentext" in t.title)
    q = "volumen anual de negocios"
    fields = {
        "economic_solvency": check_field(
            "economic_solvency",
            {"value": {"any_of": any_of, "criteria": list(criteria)}, "chunk_id": CID, "quote": q},
            CHUNKS,
        )
    }
    if months is not None:
        raw = {"value": months, "chunk_id": CID, "quote": q}
        fields["max_duration_months"] = check_field("max_duration_months", raw, CHUNKS)
    [req] = check_requirements(fields, PROFILE, tender)
    return req.verdict, req.reason


def _c(
    kind: str, eur: float | None = None, x: float | None = None, base: str | None = None
) -> dict[str, object]:
    return {"kind": kind, "amount_eur": eur, "multiple": x, "base": base}


def test_solvency_as_real_pliegos_state_it(head: FeedPage) -> None:
    """Profile: turnover €1.4M, insurance €600k, net worth €420k."""
    # fixed amount
    assert _economic(head, False, _c("turnover", 90000))[0] == "pass"
    assert _economic(head, False, _c("turnover", 2_000_000))[0] == "fail"
    # relative to the contract, computed from the feed: 1 x valor estimado = €177,677.78
    verdict, reason = _economic(head, False, _c("turnover", x=1, base="estimated_value"))
    assert verdict == "pass" and "€177,678" in reason
    # 1.5 x the annual value: valor estimado over the duration with extensions (24 months)
    verdict, reason = _economic(head, False, _c("turnover", x=1.5, base="annual_value"), months=24)
    assert verdict == "pass" and "€133,258" in reason
    # ...and without that duration it cannot be computed: open, never a guess
    assert _economic(head, False, _c("turnover", x=1.5, base="annual_value"))[0] == "open"
    # other means: insurance and net worth against the profile
    assert _economic(head, False, _c("insurance", 75000))[0] == "pass"
    assert _economic(head, False, _c("insurance", 1_000_000))[0] == "fail"
    assert _economic(head, False, _c("net_worth"))[0] == "pass"  # "patrimonio neto positivo"
    assert _economic(head, False, _c("other"))[0] == "open"


def test_alternatives_and_cumulative_criteria(head: FeedPage) -> None:
    big = _c("turnover", 2_000_000)  # the company fails it
    # "o bien": one met is enough; failing all computable ones is a fail
    assert _economic(head, True, big, _c("insurance", 75000))[0] == "pass"
    assert _economic(head, True, big, _c("insurance", 1_000_000))[0] == "fail"
    assert _economic(head, True, big, _c("other"))[0] == "open"  # the other may be met
    # cumulative: one computable fail fails; anything open keeps a pass open
    assert _economic(head, False, big, _c("other"))[0] == "fail"
    assert _economic(head, False, _c("turnover", 90000), _c("other"))[0] == "open"
