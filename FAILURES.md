# Failures

Every real failure, newest first: symptom, how it was found (trace, eval, test), root cause class, fix, and the regression test or eval case added.

**Classes:** ingestion · PDF parsing · retrieval · extraction · rules · model · labels · front

**Summary by class:** ingestion 3 · rules 1

<!-- Template:
## YYYY-MM-DD · <short title>
- **Symptom:**
- **Found by:** trace / eval / test / manual run
- **Root cause (class):**
- **Fix:**
- **Regression test / eval case:**
-->

## 2026-11-18 · A maintenance page would have been cached as the day's feed
- **Symptom:** PLACSP went into maintenance and answered every request, the ATOM feed included, with `200 OK` and a 187-byte HTML error page from its firewall. That page is well-formed XML, so `parse_page` read it as an empty feed page: 0 entries, no `next` link, walk "complete". `licita ingest` would have cached it as `<today>-head.atom`, and every later ingest that day would have read the error page from the cache. The new audit did not flag it either (no open tenders, no shares to compute).
- **Found by:** manual probe of the feed while the web page was down (no ingest had run yet, nothing was cached).
- **Root cause (ingestion):** trusting the HTTP status. A 200 was taken as "this is the feed", and the parser is lenient by design (missing elements are None, not errors).
- **Fix:** `FeedFetcher` checks the root element is an Atom `<feed>` before caching, and raises `NotAFeedError` otherwise. `licita ingest` reports it as a possible maintenance window, stores nothing and exits 1. The audit warns on pages with no entries at all.
- **Regression test / eval case:** `test_a_200_that_is_not_a_feed_raises_and_caches_nothing` (tests/test_fetch.py, with the real WAF page), `test_ingest_during_maintenance_says_so_and_stores_nothing` (tests/test_cli.py), `test_a_page_with_no_entries_is_flagged` (tests/test_audit.py).

## 2026-11-18 · Valor estimado not read: solvency would be checked against the wrong amount
- **Symptom:** the parser read only the budget (`TaxExclusiveAmount`). The feed also carries `EstimatedOverallContractAmount`, the valor estimado (budget plus extensions and modifications), which is what solvency clauses in the pliegos usually refer to. In the Policía tender it is double the budget (€177,678 vs €88,839).
- **Found by:** a field-coverage audit over the 14 cached pages (6,293 entries), while asking how we know the feed is parsed right.
- **Root cause (ingestion):** the parser was written from one reference entry and the fields the rules needed; nothing compared it with what the feed actually offers, and the existing tests take their expected values from the same XML, so a missing or misread field could not fail them.
- **Fix:** `Tender.estimated_value_eur` (present in 1,514/1,514 open tenders). Also `licita audit-feed` and an audit after every ingest: warns when a field goes missing in more open tenders than its baseline allows (the signature of a wrong XPath, which returns None silently) or when an entry does not parse (`FeedPage.skipped`). And a golden set of 11 varied tenders whose expected values are read on the PLACSP web page, not the XML.
- **Regression test / eval case:** `test_estimated_value_is_read_apart_from_the_budget` (tests/test_feed.py), tests/test_audit.py, tests/test_golden.py (pending the manual values).

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
