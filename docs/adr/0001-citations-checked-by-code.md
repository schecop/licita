# ADR-0001: Citations as chunk id + quote, checked by code, for every model

- **Date:** 2026-11-18
- **Status:** accepted

## Context
Every go/no-go must cite its clause (document, page, quoted text), and a decision without a valid citation cannot be auto-accepted. The model comparison covers 3 families (Anthropic, OpenAI, Google), so the citation mechanism must be the same for all of them, or a difference in accuracy could come from the mechanism instead of the model (decided 2026-11-16, `docs/model-research.md`).

## Decision
- Our own RAG: PDFs parsed to pages, chunked without crossing a page, ids `<doc>:p<page>:<n>`.
- Every extracted field and the final decision come back as `{value, chunk_id, quote}` in structured output (a tool call, or JSON in the text for models that need it).
- Code checks every quote against the chunk it names (`licita.extract.citations`), on folded text (case, accents, layout whitespace ignored; `...` splits a quote into ordered pieces). The result is one of `verified`, `explicit_none` (the pliego says there is none, cited), `not_found`, `uncited`, `unknown_chunk`, `bad_quote`.
- Only `verified` / `explicit_none` fields can drive a code decision (a plain solvency fail). A model decision whose quote does not verify goes to review whatever its confidence.
- The reverse holds too: if economic or technical solvency is not `verified` / `explicit_none`, nothing was compared, so the tender goes to review by code before any decision model (added 2026-11-20, FAILURES.md).
- A chunk's section heading is shown to the model as context but quotes are checked against the chunk's own text, so a citation never lands on a heading printed pages earlier.

## Alternatives considered
- **Whole PDF to Claude with native citations:** least code and page numbers for free, but Claude-only, no retrieval to learn or measure, and the full document on every call. Kept as a D4 experiment on one model (native `search_result` vs JSON quote: invalid-quote rate).
- **Trust the model's quote:** a plausible clause that is not in the pliego is the failure that destroys trust in the product; the check costs microseconds.

## Consequences
- A lying or sloppy model shows up as a measurable rate (`bad_quote`, `unknown_chunk`), per model, in the evals.
- Quotes must be literal: paraphrasing models are penalised. Revisit if a good model is held back only by quote wording (e.g. allow a fuzzy match with a threshold, measured).
- Tables flattened by the PDF parser may break literal quotes; watch it on the real pliegos.
