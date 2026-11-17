# Failures

Every real failure, newest first: symptom, how it was found (trace, eval, test), root cause class, fix, and the regression test or eval case added.

**Classes:** ingestion · PDF parsing · retrieval · extraction · rules · model · labels · front

**Summary by class:** ingestion 1 · rules 1

<!-- Template:
## YYYY-MM-DD · <short title>
- **Symptom:**
- **Found by:** trace / eval / test / manual run
- **Root cause (class):**
- **Fix:**
- **Regression test / eval case:**
-->

## 2026-11-17 · Property auctions in the candidate inbox
- **Symptom:** the first real `licita inbox` listed three TGSS "subasta pública ... enajenación de inmuebles" as candidates for an IT consultancy.
- **Found by:** manual run on the real feed (11 candidates, 3 of them auctions).
- **Root cause (rules):** they carry no CPV, and a missing CPV was "open" (pass on to the documents) by design. Their CODICE contract type is 50, patrimonial: a sale of public property, not a contract to deliver.
- **Fix:** new `type` rule: contract type 50 is a direct no-go. Also, `cpv_prefix` never shortens below the 2-digit division, so a generic `70000000` cannot count as "broader than" the profile's `72x`.
- **Regression test / eval case:** `test_patrimonial_contract_is_a_no_go`, `test_division_70_is_not_broader_than_72` (tests/test_rules.py).

## 2026-11-17 · Feed walk hit the page cap: one page is not two days
- **Symptom:** `licita ingest` stopped at `max_pages=10` (4,546 entries, ~2 min, ~140 MB) without reaching yesterday's midnight; nothing said so.
- **Found by:** manual run on the real feed; dates per cached page.
- **Root cause (ingestion):** the D1 estimate "one page ≈ 2 days" came from a weekend head page (250 entries over 13-15 Nov). On a weekday PLACSP publishes ~3,000 updates a day in pages of ~500 entries (~15 MB): ~7 pages a day.
- **Fix:** incremental ingest (walk back only to the newest update already in the store; `--days` for a full window), `max_pages` 10 → 20, and a warning naming the unread gap when the cap cuts the walk.
- **Regression test / eval case:** `test_second_ingest_is_incremental`, `test_ingest_warns_when_the_page_cap_cuts_the_walk` (tests/test_cli.py), `test_max_pages_stops_the_walk_and_says_so` (tests/test_fetch.py).
