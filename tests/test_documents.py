from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

from licita.documents.chunk import Chunk, chunk_pages, paragraphs
from licita.documents.pdf import Page, read_pdf
from licita.documents.source import local_documents
from licita.retrieval.embed import HashingEmbedder
from licita.retrieval.hybrid import HybridIndex
from licita.retrieval.text import fold, tokens


def test_pdf_text_comes_out_per_page(pcap_pdf: Path) -> None:
    pages = read_pdf(pcap_pdf)
    assert [p.number for p in pages] == [1, 2, 3]
    assert "presupuesto base de licitación asciende a 240.000,00 euros" in pages[0].text.replace(
        "\n", " "
    )
    assert "volumen anual de negocios" in pages[1].text.replace("\n", " ")
    assert not any(p.scanned for p in pages)


def test_a_page_without_text_is_flagged_as_scanned_and_not_chunked() -> None:
    pages = [Page(1, "Texto de verdad. " * 10), Page(2, "  \n ")]
    assert pages[1].scanned
    assert {c.page for c in chunk_pages("PCAP", pages)} == {1}


def test_layout_line_breaks_are_rejoined_into_paragraphs() -> None:
    text = (
        "El plazo de ejecución será de veinticuatro\nmeses desde la formalización.\n"
        "4. SOLVENCIA\nLos licitadores"
    )
    assert paragraphs(text) == [
        "El plazo de ejecución será de veinticuatro meses desde la formalización.",
        "4. SOLVENCIA",  # a heading is a paragraph of its own
        "Los licitadores",
    ]


def test_chunks_never_cross_a_page_and_respect_the_size(pcap_pdf: Path) -> None:
    chunks = chunk_pages("PCAP", read_pdf(pcap_pdf), max_chars=300)
    assert len({c.id for c in chunks}) == len(chunks)
    assert all(c.id.startswith(f"PCAP:p{c.page}:") for c in chunks)
    assert all(len(c.text) <= 300 + 300 for c in chunks)  # one paragraph may reach the max alone
    page2 = " ".join(c.text for c in chunks if c.page == 2)
    assert "360.000,00 euros" in page2


def test_fold_removes_case_accents_and_layout_whitespace() -> None:
    assert fold("SOLVENCIA  Económica\ny  Financiera") == "solvencia economica y financiera"
    assert tokens("la solvencia de la empresa") == ["solvencia", "empresa"]


def test_hybrid_search_finds_the_solvency_clause(pcap_pdf: Path) -> None:
    chunks = chunk_pages("PCAP", read_pdf(pcap_pdf), max_chars=400)
    index = asyncio.run(HybridIndex.build(chunks, HashingEmbedder()))
    hits = asyncio.run(index.search("solvencia económica volumen anual de negocios", k=3))
    assert hits[0].chunk.page == 2
    assert "volumen anual de negocios" in hits[0].chunk.text
    assert hits[0].bm25_rank == 1


def test_documents_are_the_pdfs_in_the_tender_folder(tmp_path: Path) -> None:
    folder = tmp_path / "20913966"
    folder.mkdir()
    (folder / "PCAP.pdf").write_bytes(b"%PDF")
    (folder / "notas.txt").write_text("x")
    entry = "https://contrataciondelestado.es/sindicacion/licitacionesPerfilContratante/20913966"
    assert [p.name for p in local_documents(tmp_path, entry)] == ["PCAP.pdf"]
    assert local_documents(tmp_path, entry + "0") == []
    # the same file published twice (as PCAP and as PPT) is read once
    (folder / "PPT.pdf").write_bytes(b"%PDF")
    (folder / "ANEXO.pdf").write_bytes(b"%PDF-other")
    assert [p.name for p in local_documents(tmp_path, entry)] == ["ANEXO.pdf", "PCAP.pdf"]


def test_headings_as_real_pliegos_write_them() -> None:
    from licita.documents.chunk import heading

    # clauses, in any case
    assert heading("4. SOLVENCIA ECONÓMICA Y FINANCIERA") == (
        1,
        "4. SOLVENCIA ECONÓMICA Y FINANCIERA",
    )
    assert heading("11.  Criterios de adjudicación") == (1, "11. Criterios de adjudicación")
    assert heading("CLÁUSULA 4.2 Solvencia técnica") == (2, "4.2. Solvencia técnica")
    assert heading("Cláusula 23. Presentación de proposiciones") == (
        1,
        "23. Presentación de proposiciones",
    )
    assert heading("CLAUSULA SEGUNDA. Órgano de contratación.") == (
        1,
        "Clausula segunda. Órgano de contratación.",
    )
    assert heading(
        "CLAUSULA CUARTA. Presupuesto base de licitación y valor estimado del contrato."
    ) == (1, "Clausula cuarta. Presupuesto base de licitación y valor estimado del contrato.")
    # parts (Roman, capitals) and the cuadro de características (letters)
    assert heading("III. DE LA LICITACIÓN") == (0, "III. DE LA LICITACIÓN")
    assert heading("I. DISPOSICIONES GENERALES") == (0, "I. DISPOSICIONES GENERALES")
    assert heading("I. Criterios de adjudicación") == (1, "I. Criterios de adjudicación")
    assert heading("F.2. Solvencia") == (2, "F.2. Solvencia")
    # annexes: titled in capitals; a mention of one in a clause is not one
    assert heading("ANEXO II.- OFERTA ECONÓMICA") == (0, "ANEXO II - OFERTA ECONÓMICA")
    assert heading("ANEXO VIII - Oferta técnica.") is None
    # table rows, amounts, long lines
    assert heading("10 TOTAL PBL con IVA 369.581,19 €") is None
    assert heading("4 13% Gastos Generales") is None
    assert heading("3. IVA") is None  # too short to be a title
    assert heading("1. " + "Los licitadores deberán presentar la documentación " * 2) is None


def test_a_numbered_sentence_that_runs_on_is_not_a_heading() -> None:
    page = Page(1, "1. El objeto del contrato es\nel desarrollo de la plataforma de datos.")
    [chunk] = chunk_pages("PCAP", [page])
    assert chunk.section == ""
    assert chunk.text == "1. El objeto del contrato es el desarrollo de la plataforma de datos."


def test_running_headers_and_contents_pages_are_dropped() -> None:
    stamp = "Docusign Envelope ID: B6FCA6A3-E2D4-81D7-817E-705089714482"
    toc = Page(
        1,
        f"{stamp}\nÍndice\n8.  Solvencia ........................ 22\n"
        "ANEXO II.- OFERTA ECONÓMICA Y RESTO DE CRITERIOS VALORABLES MEDIANTE LA APLICACIÓN DE\n"
        "FÓRMULAS (SOBRE 3) ........................ 56\n"
        "ANEXO IX.- CESIÓN DE DATOS PERSONALES ..................... 9\nPágina 1 de 4",
    )
    body = [
        "8. Solvencia\nVolumen anual de negocios mínimo de 300.000 euros.",
        "Se acreditará con las cuentas anuales depositadas.",
        "8.1. Medios personales\nUn jefe de proyecto con cinco años de experiencia.",
    ]
    pages = [toc] + [Page(n, f"{stamp}\n{t}\nPágina {n} de 4") for n, t in enumerate(body, 2)]
    chunks = chunk_pages("PCAP", pages)
    assert [c.page for c in chunks] == [2, 3, 4]  # the contents page gives no chunk
    assert all("Docusign" not in c.text and "Página" not in c.text for c in chunks)
    # the sections come from the clauses, not from "ANEXO IX" in the contents
    assert [c.section for c in chunks] == [
        "8. Solvencia",
        "8. Solvencia",
        "8. Solvencia > 8.1. Medios personales",
    ]


def test_a_chunk_carries_its_section_across_pages_but_quotes_check_its_text_only() -> None:
    from licita.extract.citations import check_field

    pages = [
        Page(1, "4. SOLVENCIA ECONÓMICA Y FINANCIERA\nLos licitadores deberán acreditar:"),
        Page(2, "Un volumen anual de negocios por importe igual o superior a 300.000 euros."),
    ]
    p1, p2 = chunk_pages("PCAP", pages)
    assert p2.section == "4. SOLVENCIA ECONÓMICA Y FINANCIERA"
    assert p2.render().startswith("[PCAP:p2:0] (PCAP, pág. 2)\nSección: 4. SOLVENCIA")

    # BM25 finds the page-2 chunk for "solvencia", a word its text never says; without the
    # section it would not match at all
    def bm25_rank(chunk: Chunk) -> int | None:
        index = asyncio.run(HybridIndex.build([p1, chunk], HashingEmbedder()))
        hits = asyncio.run(index.search("solvencia", k=2))
        return next(h.bm25_rank for h in hits if h.chunk.id == chunk.id)

    assert bm25_rank(p2) is not None
    assert bm25_rank(replace(p2, section="")) is None
    # quoting the heading against the page-2 chunk does not verify: it is printed on page 1
    raw = {"value": 300000, "chunk_id": "PCAP:p2:0", "quote": "SOLVENCIA ECONÓMICA"}
    assert check_field("f", raw, {p2.id: p2}).check == "bad_quote"


def test_a_sub_heading_replaces_its_siblings_and_keeps_its_parent() -> None:
    text = (
        "4. SOLVENCIA\nIntro.\n\n4.1 ECONÓMICA\nVolumen.\n\n4.2 TÉCNICA\nServicios.\n\n"
        "5. CRITERIOS\nPrecio."
    )
    sections = [c.section for c in chunk_pages("PCAP", [Page(1, text)], max_chars=30, overlap=0)]
    assert sections == [
        "4. SOLVENCIA",
        "4. SOLVENCIA > 4.1. ECONÓMICA",
        "4. SOLVENCIA > 4.2. TÉCNICA",
        "5. CRITERIOS",
    ]
