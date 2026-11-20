# Licita — instructions for Claude Code

## What this is
A public-tender scout for small companies. Every morning Licita reads the new tenders on Spain's Plataforma de Contratación del Sector Público (PLACSP), matches them against a company profile, reads the tender documents (pliegos), extracts what matters (budget, deadline, solvency, award criteria) and gives a **go / no-go citing the exact clause**. Small companies miss tenders they could win because checking the requirements in long, messy PDFs takes hours; a single prompt cannot do it reliably, a multi-step graph that screens, retrieves, extracts and checks each requirement against the profile, with citations, can. The citations are what make a company trust the decision. An MCP server lets a consultant ask Claude about any tender.

Part of a 4-PoC sprint ("NNN", November 2026): one week, one working product, one lead skill.

- **Week:** 3 (16-24 Nov 2026, extended to Tue 24) · **Budget:** ~22h · **Type:** portfolio build (deployed, React front, full README, demo video, a measured number)
- **Lead skill:** multi-step agents (LangGraph) + RAG + MCP
- **Core skills repeated here:** LangGraph (3rd pass), APIs (ATOM/XML + messy PDFs), real-time (SSE), evals (3rd), the coach (2nd pass, rewritten), observability (3rd), debugging, hybrid RAG (1st full), MCP (3rd), React/TS (1st), AWS deployment
- **Must scope:** daily PLACSP ingestion; profile matching; LangGraph pipeline (screening → document download → hybrid retrieval → structured extraction → requirement check → go/no-go) with progress streamed over SSE; extraction evals on ~20 hand-labelled tenders; a cascade with a measured threshold and a review inbox; the coach's 2nd pass on the extraction prompt; Langfuse; React + TypeScript front; MCP server; deployed on AWS
- **Stretch:** multi-client mode (a tender consultancy); email digest
- **Done when:** a public URL shows the day's real tenders with a cited go/no-go; the README shows extraction accuracy, the cascade's accuracy (precision of what is auto-accepted at the chosen threshold, and the % sent to review) and the coach's gain on the held-out half; and there is a 2-minute demo video.

Key files: `FAILURES.md` (real failures and fixes), `docs/model-research.md` (model choice), `docs/adr/` (lasting decisions), `config/example.yaml` (tunables and the model per role), `data/company/profile.yaml` (the fictional company).

Operator context (plan, progress, teach-back) lives in `private/`, a separate private repo that is gitignored here:

@private/CLAUDE.md

## Architecture rules (non-negotiable)
- **Every go/no-go cites its clause** (document, page, quoted text). A decision without a citation is not auto-accepted: it goes to the review inbox.
- **Deterministic rules before the model.** CPV, budget range, deadline and solvency the company plainly fails are a direct no-go by code; the model only decides what the rules leave open.
- **The cascade's threshold comes from the evals**, never by eye: the value at which auto-accepted decisions reach ≥95% precision, with the share sent to review measured alongside.
- **The coach never sees the held-out half** of the labelled tenders, and the keep/rollback rule is fixed before each run. Every change is logged: prompt diff, reason, scores before and after, kept or rolled back.
- **Every review-inbox decision is saved as a new label** for the eval set.
- **Public data only.** PLACSP tenders are public; the company is fictional. No real company's private data anywhere.

## How we work: fast mode, demo first
Pablo learns by seeing things work, then reasoning about why, then rebuilding with more understanding. This is a one-week build, so speed matters, but nothing should stay a black box.

1. **Claude builds most of the code; Pablo directs, runs, breaks and fixes.** No tutor mode, no guided exercises (that is Market Desk's job).
2. **Demo first, then why.** When a piece works, show it running (a command, a test, an output), then explain in 5-10 lines how it works and *why it's built this way*: the main alternative and why not. Point to the 2-3 lines that matter most.
3. **Small vertical slices.** Each step ends in something runnable. Prefer a rough end-to-end path over a polished half.
4. **Ask before big forks.** Library, provider, data model or architecture choices (PDF parser, retriever, vector store, SSE server, MCP SDK, AWS shape): give a recommendation with one line of trade-offs and wait for a yes. Everything else: proceed.
5. **Must before stretch.** Track the must scope; propose cutting stretch items when the week's budget is at risk. Cut order: stretch → MCP down to read-only with 2 tools → simpler deployment. Evals, the cascade and the coach are never cut.

## Rule 7: no default model
Latency and cost matter as much as quality. Any model is allowed: open-source, free, cheap or frontier.
- **Day 1:** fill `docs/model-research.md`: per role (screening, extraction, decision, coach, embeddings, reranker) 3-5 candidates with price in/out, latency, context, structured-output quality, Spanish quality, hosting. Search for current data; never answer from memory.
- **Model-agnostic code:** everything goes through `licita.llm.LLMClient`. The model per role lives in `config/*.yaml` (`llm:`), never in code. LangGraph nodes call `LLMClient`; no LangChain chat-model wrappers.
- **Organise for cost (this is the PoC where cost weighs most: tender documents are long):** screening with no LLM where CPV and keywords suffice; structured extraction with a mid model reliable at structured output; the strong model only for the final go/no-go. Nightly batch, so latency does not matter and batch APIs do. Prompt caching over the tender document across steps. Compare embeddings and reranker in Spanish.
- **Decide with evals:** compare 2-3 models per role on quality, latency p50/p95 and cost; record the choice in an ADR `model-choice` with the table.
- **Product cost metric:** cost per tender analysed.

## Records (keep them as you go, not at the end)
- **`FAILURES.md`:** every real failure: symptom, how it was found (trace, eval, test), root cause class (ingestion, PDF parsing, retrieval, extraction, rules, model, labels, front), fix, regression test or eval case added.
- **`docs/adr/`:** one short ADR per lasting decision (copy `0000-template.md`).

## Conventions
- Python 3.12+, uv, ruff, mypy (strict), pytest, `src/` layout. Front in `web/`: Vite + React + TypeScript (strict).
- LLM prompts live in `prompts/`. The coach edits only the extraction prompt.
- Observability from day 1: one Langfuse trace per tender analysed (each node a span, model calls as generations, retrieval with the chunks it returned).
- Tests never call live models or APIs (PLACSP included): a scripted fake `LLMClient` drives the real graph, and recorded feed/PDF fixtures stand in for PLACSP. Real-model evals are separate, opt-in (`-m live`), and run on purpose.
- Budget: every paid call goes through `Metered` and into `runs/cost.jsonl`; `licita.budget.check` refuses a run that would pass the $15 cap.
- Never commit secrets. Keys go in `.env` (gitignored) and are listed in `.env.example`.
- Never commit on your own: show `git status`, propose a conventional commit (`feat:`, `fix:`, `test:`, `docs:`, `chore:`) with a why-body, and commit on Pablo's go-ahead.
- Public repo: docs describe the product and what was learned.

## Safety
- PLACSP is a public service: cache every download locally (`data/raw/`, `data/pliegos/`), throttle requests, never re-download what is cached.
- Claude never runs an eval sweep, a coach run or a batch over tenders against a paid API without stating the expected cost first; free tiers and the fake model need no confirmation.
- Nothing on AWS (accounts, resources, costs) without Pablo's explicit go-ahead.
