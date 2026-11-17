# Model research (rule 7)

No default model. Each role gets the model that wins on its own job, on quality, latency and cost. Data from searches on 2026-11-16, building on clinic-desk's research of 2026-11-11 (its prices, free-tier findings and measured behaviour are reused where they still apply; source noted). **Every verdict below is a hypothesis with the test that settles it:** the evals decide (ADR `model-choice`, D4). `config/example.yaml` holds placeholders until then.

**Budget: $15 total in paid API calls, no credits** (Pablo, 2026-11-16). Development runs on the scripted fake; real models only for runs that feed a decision; the budget guard (`licita.budget`) refuses a run that would cross the cap. **Product metric: cost per tender analysed.**

## What is different from clinic-desk

- **Long Spanish documents, not short turns.** A tender has a PCAP (administrative clauses) and a PPT (technical specs); from the feed check (2026-11-16) 17 of 19 IT tenders link both. Typical size is to be measured on D3; assumption until then: **20-60k tokens per tender** (both documents).
- **Nightly batch, not interactive.** Latency does not matter for the pipeline; batch APIs (50% off at Anthropic, OpenAI, Google, Mistral) and prompt caching do. Only the live demo and the MCP server are interactive.
- **Citations are the product.** A go/no-go without a document + page + quote is not auto-accepted. So "how the citation is produced" is a design choice, not a detail (see Open decision below).
- **Spanish only** (pliegos are in Spanish; Catalan tenders exist on the Catalan platform, out of scope). Catalan quality stops being a criterion.

## Decided: where the citations come from (option A, one mechanism for every model; Pablo 2026-11-16, revised the same evening)

**Own RAG, the same chunks and the same citation format for every model.** We parse the PDFs, chunk with page numbers, retrieve hybrid (BM25 + embeddings, reranker if it pays), and send the top chunks with ids. Every model returns, per field, the value plus `chunk_id` + verbatim quote in structured output. **Code checks every quote against the chunk text**; a field whose quote does not match counts as uncited and cannot be auto-accepted.

Why one mechanism: the first version of A used Claude's native `search_result` citations ([search results](https://platform.claude.com/docs/en/build-with-claude/search-results.md), [citations](https://platform.claude.com/docs/en/build-with-claude/citations)) and was decided before the full candidate list. With candidates from 3 families, mixing citation mechanisms would confound the model comparison: a difference could come from the mechanism, not the model.

**Native citations become a separate experiment (D4, one model):** Haiku 5.5 with `search_result` blocks vs the same Haiku with JSON `chunk_id` + quote, on the labelled set: invalid-quote rate and field accuracy. Adopted for Claude only if the number justifies it. ADR when D3 builds extraction.

| Option | How | For | Against |
|---|---|---|---|
| **A. Own RAG, `chunk_id` + quote in JSON for every model** (chosen) | As above | Keeps the week's lead skill (hybrid RAG) real and measurable; provider-agnostic; fair comparison | We own PDF parsing (tables, scanned annexes); invented quotes to catch (code does) |
| A'. Own RAG + Claude `search_result` blocks | Same chunks as content blocks; Claude cites them natively | Possibly fewer invalid quotes | Claude-only: confounds the cross-family comparison. Kept as the D4 experiment |
| B. Whole PDF to Claude with citations on | Each PDF as a document block with `citations: enabled` | Least code; page numbers for free | No retrieval to learn or measure; Claude-only; scanned PDFs not citable; full document on every call |

**Baseline zero either way:** the whole pliego in a cached prompt, no retrieval (like clinic-desk's "all policies in the prompt"). If extraction is as accurate that way, retrieval is not earning its place, and the README says so.

## Roles

| Role | What it does | Calls per tender | What matters most |
|---|---|---|---|
| **Screening** | Keep or drop a tender from feed metadata (title, CPV, budget, deadline, region) | 0-1 | Mostly **code** (CPV prefix, budget range, deadline, region); an LLM only for borderline titles. Cheapest model or none |
| **Extraction** | From retrieved chunks: budget, duration, deadline, solvency (economic, technical), required classification/certifications, award criteria with weights, each with a citation | ~4-6 (one per field group) | Structured output that validates; faithful quotes; long Spanish legal text; caching and batch |
| **Decision** | Requirement check vs profile → go / no-go + confidence + the deciding clause | 1 | Reasoning over the extracted fields; calibrated confidence (the cascade's threshold depends on it) |
| **Coach** | Reads failed extractions on the visible half, proposes one change to the extraction prompt | 1-2 per iteration | Diagnosis quality; one change only |
| **Embeddings** | Chunks of the pliegos and field queries, in Spanish | per chunk once (cached) + per query | Spanish retrieval on legal wording |
| **Reranker** | Reorders ~20-50 candidate chunks per field | 1 per field | Spanish; only kept if it beats hybrid without it |

## Shortlist hypothesis (Pablo, 2026-11-16)

Quality-cost favourites going into the evals; the verification calls below can still remove one.

| Role | Favourites | Diagnostic / reserve only |
|---|---|---|
| Extraction | Haiku 5.5, GPT-6 Luna | GPT-6.1 Terra (cheap ceiling); Sonnet 5.5 (diagnostic ceiling, ~5 tenders) |
| Go / no-go | Haiku 5.5, GPT-6 Luna, GPT-6.1 Terra, Gemini 3.8 Flash | — |
| Coach | Sonnet 5.5, GPT-6.1 Terra | — |
| Any role | — | GPT-6.1 Sol, Gemini 4 Argon: only if > ~$5 remains after D4 |

## Extraction

| Model | Role in the test | Price in / out per 1M (standard · batch) | What it must show | What rules it out |
|---|---|---|---|---|
| **Claude Haiku 5.5** | Main | $0.10 / $0.50 (≤100k prompt) · batch $0.05 / $0.25 · cache read $0.01 | Field accuracy close to the ceilings on the labelled set; quotes that match the chunk text; schema valid every time | Accuracy gap to Terra/Sonnet beyond the spread between repeats |
| **GPT-6 Luna** | Main rival, same price | $0.10 / $0.50 · batch $0.05 / $0.25 · cache read $0.01; 1M context, higher rate above 272k (third-party, citing OpenAI's page; released 2026-09-22) | Matches or beats Haiku on field accuracy and quote fidelity at the same price | Structured outputs unconfirmed on its model page: one verification call first; invented quotes |
| **GPT-6.1 Terra** | Cheap ceiling | $1 / $5 (third-party; released 2026-10-26) | How much a 10× dearer model gains on the hardest fields (solvency, award weights) | Tools need the Responses API: check structured output on Chat Completions with one call (our OpenAI-compatible adapter uses Chat Completions) |
| **Claude Sonnet 5.5** | **Diagnostic ceiling only**, not a production candidate | $2 / $10 · batch $1 / $5 · cache read $0.10 | Run on ~5 tenders (~$0.3) to tell model errors from prompt/retrieval errors: if Sonnet also misses a field, the fault is upstream | — |

- **Citations:** one mechanism for every model, see the section above. **Every quote is checked by code against the chunk text, for every model.**
- **Dropped from the shortlist:** *Gemini 3.8 Flash* for extraction (~7× Haiku's price; kept for the decision, where it reads ~3k tokens); *Gemini 3.6 Flash* (older than 3.8 Flash and dearer at standard price); *Gemini 3.5 Flash-Lite* ($0.30 / $2.50: dearer than Luna and Haiku, no reason to expect better extraction); *Mistral Small 4* (EU lane only matters for private data; tenders are public); *GPT-5.4 mini* (superseded for this job by Luna at ~1/7 the price).
- **Reserve ceilings, only if > ~$5 remains after D4:** *GPT-6.1 Sol* ($2 / $10, third-party); *Gemini 4 Argon* ($2 / $10 until 2026-11-30, then $4 / $20, third-party; access unclear: test with one call).

- **Haiku 5.5 above 100k tokens costs 5×** ($0.50 / $2.50). Retrieval keeps prompts well under it; the whole-pliego baseline may cross it on big tenders, which is itself a finding for the cost table ([Anthropic pricing](https://docs.anthropic.com/en/docs/about-claude/pricing), [pricingsaas diff](https://pricingsaas.com/companies/anthropic.api/diffs/2026D281); the 100k break point is from third-party rate cards, older docs say 200k: check the official page before the cost table goes in the README).
- **Batch stacks with caching** at Anthropic ([pricing](https://docs.anthropic.com/en/docs/about-claude/pricing)). Within one tender the PCAP/PPT chunks repeat across the field calls: put them first, the field question last.
- Clinic-desk lesson: Haiku 5.5 defaults to thinking on (effort `medium`); set effort explicitly.

## Decision (go / no-go)

| Model | Role | Price | What it must show |
|---|---|---|---|
| **Claude Haiku 5.5** | Cheap | $0.10 / $0.50 | Correct go/no-go on the labelled set; confidence that separates right from wrong (the cascade needs ≥95% precision above a threshold that still auto-accepts a useful share). The decision only reads ~2-3k tokens of extracted fields |
| **GPT-6 Luna** | Cheap, other family | $0.10 / $0.50 | Calibration vs Haiku at the same price |
| **GPT-6.1 Terra** | Mid | $1 / $5 (third-party) | Whether 10× the price buys a better-calibrated confidence (more auto-accepted at ≥95%) |
| **Gemini 3.8 Flash** | Mid, third family | $0.75 / $3.75 (intro, third-party) | Calibration vs Terra; was the most stable judge between identical runs in clinic-desk, which matters for a threshold |

- **Sonnet 5.5 left the decision shortlist** (Pablo, 2026-11-16): at $2 / $10 it only earns a place if the cheaper four all fail calibration.

- **Most no-gos never reach a model:** deterministic rules decide the plain cases (CPV, budget, deadline, missing certification). The decision model only sees what the rules leave open.
- **Confidence:** self-reported confidence is the cheap option; agreement across 3 samples is the more honest one at 3× the cost. Decide on D4 with the data.

## Coach (2nd pass, rewritten)

**Sonnet 5.5** main ($2 / $10), **GPT-6.1 Terra** as the alternative ($1 / $5). **Opus dropped** (Pablo, 2026-11-16): too expensive for a $15 cap. The cost is in the extraction re-runs each iteration triggers, not in the coach's own calls. **No batch inside the coach loop:** each keep/rollback waits for the scores.

## Embeddings (Spanish)

| Option | Role | Price | What it must show |
|---|---|---|---|
| **BM25** | Lexical baseline, in CI | Free | Pliegos are formal and repetitive ("solvencia económica y financiera", "criterios de adjudicación"): lexical may be strong here, unlike clinic-desk's colloquial questions. Needs a Spanish stemmer |
| **text-embedding-3-small** | Hosted dense | ~$0.02 / 1M tokens (clinic-desk research; not re-verified today) | Beats BM25 on the field queries; won clinic-desk's test (17/20 vs 11/20) |
| **gemini-embedding** | Hosted dense, other family | Price not found in today's search: check the official page | Top of MTEB multilingual by Google's own report (~68.3) ([datallmlab](https://www.datallmlab.com/blog/best-embedding-models.html)) |
| **BGE-M3** | Local dense + sparse | Free (CPU), heavy (torch) | Dense + sparse in one model, 8k-token input, 100+ languages ([zilliz](https://zilliz.com/blog/choose-embedding-model-rag-2026)). Opt-in, never in CI |

- **Hybrid = BM25 + one dense model,** fused by reciprocal rank. Compare BM25 alone, dense alone, hybrid, on the field queries of the labelled tenders: hit@k of the chunk that holds the labelled answer.
- Qwen3-Embedding-8B tops MTEB multilingual on its model card (70.58) but needs a GPU: dropped for this budget.
- Cost is negligible: ~50k tokens per tender embedded once.

## Reranker

| Option | Price | Note |
|---|---|---|
| **Cohere Rerank 4 Fast** | ~$2 per 1,000 searches (third-party; Cohere's page lists no per-search rate for v4) | Per-search billing; ~5 searches per tender = $0.01 |
| **Voyage rerank-2.5-lite** | ~$0.02 / 1M tokens, 200M free tokens (third-party) | Cheapest at our shape; another key and provider |
| **bge-reranker-v2-m3** | Free, local (Apache 2.0) | No Spanish-specific benchmark found |

Sources: [beri.net](https://www.beri.net/article/cohere-vs-voyage-vs-bge-reranker-per-search-vs-per-token-cost-2026), [apicostcalc](https://apicostcalc.com/es/cohere-vs-voyage-vs-jina-rerank-cost-calculator.html), [mixpeek](https://mixpeek.com/curated-lists/best-rerankers). **Stretch inside the must:** only if hybrid misses the labelled chunk in the top 5 often enough to matter; otherwise "no reranker" goes in the decision table with the numbers.

## PDF parsing (not a model, but a cost line)

- **Local text extraction first** (pypdf / pdfplumber / PyMuPDF: library choice is a fork for Pablo on D3). Most PLACSP pliegos are digital PDFs.
- **Scanned annexes:** Mistral OCR at ~$1-2 per 1,000 pages (batch / standard) for OCR 3; a 2026-11-10 article reports OCR 4 at $4 per 1,000 pages. Prices conflict: only if D3 finds scanned pliegos ([activepieces](https://www.activepieces.com/blog/mistral-ocr-pricing-2026-cost-per-1000-pages.md), [docs](https://docs.mistral.ai/models/ocr-3-25-12)).
- Gemini bills a PDF page as an image plus its text (2026 guide, unverified on the official page): another reason to send our own text, not the PDF.

## Cost per tender (estimate, to be replaced by the ledger)

Assumptions: screening by code (free); extraction 5 calls × ~8k retrieved tokens in / ~600 out; decision 1 call × ~3k / ~500; embeddings negligible.

| Setup | Extraction | Decision | **Per tender** | **Per eval pass** (20 tenders × 2 repeats) |
|---|---|---|---|---|
| Haiku + Haiku, standard | $0.0055 | $0.0006 | **~$0.006** | **~$0.25** |
| Haiku + Terra, standard | $0.0055 | $0.0055 | **~$0.011** | **~$0.44** |
| Haiku + Terra, batch (nightly, if Terra has 50% batch) | $0.0028 | $0.0028 | **~$0.006** | n/a (evals are not batched in the coach loop) |
| Sonnet diagnostic (extraction, ~5 tenders once) | ~$0.06 | — | — | ~$0.3 total |
| Whole-pliego baseline, Haiku, 50k tokens × 5 calls, cached | ~$0.006-0.03 (cache hit vs miss) | — | measured on D4 | — |

Daily production for the fictional company: ~5 open IT tenders a day → **~$0.05-0.10 a day**.

## Budget ($15 cap)

| Item | Estimate |
|---|---|
| Smoke runs on 2-3 real tenders, a few times | $0.5 |
| Extraction comparison: Haiku, Luna × 20 tenders × 2 repeats; Terra × 20 × 1; Sonnet diagnostic on ~5 | $1.5-2.5 |
| Native-citation experiment (Haiku, `search_result` vs JSON) | < $0.3 |
| Retrieval comparison (embeddings, optional reranker) | < $0.2 |
| Whole-pliego baseline | $0.5-1 |
| Decision comparison + cascade threshold (4 models, 20 tenders, 3 samples for confidence) | $1-2 |
| Coach run (5 iterations; extraction re-runs on the visible half, held-out at the end) | $2-3 |
| Daily runs for the deployed demo (D6-D7) | $0.5 |
| **Total** | **~$7-10**, leaving ~$5 of margin |

**What the cap costs the experiment:** the held-out half is ~10 tenders, so only a large coach gain will be distinguishable from noise (same lesson as clinic-desk: repeats and the sequential test, z ≥ 2.2). More labelled tenders would help more than more money; labelling time is the real limit.

## Constraints

- **Keys:** new for this project: Anthropic, OpenAI (GPT-6 Luna, GPT-6.1 Terra and embeddings) and Gemini (3.8 Flash; gemini-embedding if it stays). No Mistral key unless OCR is needed.
- **Data:** all tenders are public; the company is fictional. Free tiers that train on data are acceptable.
- **PLACSP access:** the ATOM feed downloads by script; pliego downloads are rejected for scripts but work in Pablo's browser (2026-11-16). The ~20 labelled tenders come from the browser; the daily pipeline's route is open (see PROGRESS).

## Open items

- [x] Citations: option A, one mechanism for every model (Pablo, 2026-11-16, revised). Native `search_result` as a D4 experiment.
- [ ] Confirm GPT-6 Luna on OpenAI's model page: id, price, structured outputs, Chat Completions vs Responses. One verification call.
- [ ] Confirm GPT-6.1 Terra: price, batch, structured output on Chat Completions (tools need Responses). One verification call.
- [ ] Gemini 4 Argon access: one call, only if the reserve is used.
- [ ] Confirm Gemini 3.8 Flash price and batch on the official pricing page (all figures above are third-party).
- [ ] Measure real pliego sizes (tokens per PCAP + PPT) on the first 5 tenders and redo the cost table; update `PER_TENDER` in `src/licita/budget.py`.
- [ ] Confirm on official pages: Haiku 5.5 long-context break point (100k vs 200k), text-embedding-3-small and gemini-embedding prices, Gemini 3.5 Flash-Lite price.
- [ ] Check a Spanish stemmer for BM25.
- [ ] Decide confidence method for the cascade (self-reported vs sample agreement) on D4.
