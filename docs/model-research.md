# Model research (rule 7)

Filled on D1 with current, sourced data (never from memory). The choice is made later with evals and recorded in an ADR `model-choice`; `config/example.yaml` holds placeholders until then.

**Budget:** $15 total for paid API calls, no credits. Develop on the scripted fake; free tiers where they exist; batch and prompt caching where they apply. Product metric: **cost per tender analysed**.

## Task spec per role

| Role | Input | Output | Hard filters | Disqualifiers |
|---|---|---|---|---|
| Screening | tender metadata (title, CPV, budget, body) + profile | keep / drop | _tbd_ | _tbd_ |
| Extraction | retrieved chunks of the pliegos (Spanish, long) | structured fields with citations | reliable structured output | _tbd_ |
| Decision | extracted fields + profile | go / no-go + confidence + cited clause | _tbd_ | _tbd_ |
| Coach | failed extractions (visible half) + current prompt | one prompt change with a reason | _tbd_ | _tbd_ |
| Embeddings | chunks + queries in Spanish | vectors | Spanish quality | _tbd_ |
| Reranker | query + candidate chunks | ranking | Spanish quality | _tbd_ |

## Candidates per role

_3-5 per role: price in/out, latency (TTFT, tokens/s), context, structured-output quality, Spanish quality, hosting, batch and caching support, sources._

## Cost estimate per tender and per eval pass

_tbd: correct `PER_TENDER` in `src/licita/budget.py` from it, then from the ledger._
