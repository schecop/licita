# ADR-0002: PyMuPDF for pliegos, hybrid retrieval in memory, a LangGraph graph per tender

- **Date:** 2026-11-18
- **Status:** accepted (Pablo chose the parser and the retrieval store)

## Context
The analysis reads the pliegos (PCAP, PPT: long Spanish PDFs) of one tender at a time: a few hundred chunks per tender, ~5-15 candidate tenders a day. It needs text with page numbers (citations), lexical and semantic retrieval, and steps that can exit early, be traced and stream progress.

## Decision
- **PDF:** PyMuPDF, `get_text("text", sort=True)` (reading order for columns). A page under 40 characters is flagged as scanned and kept, never silently dropped.
- **Chunks:** whole paragraphs up to `chunk_max_chars` (1,200, unmeasured), never across a page, one paragraph of overlap. Each chunk carries its section path ("4. SOLVENCIA > 4.2. TÉCNICA"), detected from numbered upper-case headings and annexes and carried across pages; retrieval ranks section + text, so the third chunk of a long clause is still found by the clause's topic.
- **Field groups:** economics, deadline, solvency, award, scope (object, required staff): one query and one model call each (~5 calls per tender).
- **Retrieval:** BM25 written by hand (`licita.retrieval.bm25`) plus dense cosine with numpy, fused by reciprocal rank (k = 60). Everything in memory per tender; no vector database. One query per field group, in the pliegos' own wording.
- **Embeddings:** behind an `Embedder` interface. `HashingEmbedder` (bag of words, deterministic) for tests and until the D4 retrieval comparison picks a real model; it must never be the one measured.
- **Graph:** LangGraph `StateGraph`, nodes `screen → load_documents → retrieve → extract → requirements → decide`, each returning only what it changes; any node ends the run by setting `outcome`. Progress = `astream(stream_mode="updates")`, the same events the front will get over SSE. One Langfuse trace per tender: a span per node, model calls as generations under the node that made them.

## Alternatives considered
- **pdfplumber:** better tables, 10-50× slower. **pypdf:** pure Python, weaker reading order (quotes that do not match what a person sees).
- **sqlite-vec / Chroma / Qdrant:** an extension or a service to install and deploy for a volume that fits in memory.
- **Weighted sum of BM25 and cosine:** the scales differ and the weight would be arbitrary per query; ranks are comparable as they are.
- **One long async function instead of a graph:** fewer imports, but no per-step spans and events, and early exits hidden in `if`s.

## Consequences
- PyMuPDF is AGPL (fine for a public repo; ties any closed use) and its 2.x wheel pulls `onnxruntime` and layout models (~100+ MB): watch the AWS package size on D6.
- Embeddings are paid once per chunk; a cache by chunk text hash arrives with the first real embedder.
- Reranker: only added between `retrieve` and `extract` if the evals show hybrid misses the labelled chunk in the top k often enough to matter.
