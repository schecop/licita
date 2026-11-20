# ADR-0003: Solvency extracted as the pliego states it, compared by code where it can be

- **Date:** 2026-11-20
- **Status:** accepted (Pablo chose option A)

## Context
The first schema had two numbers: minimum annual turnover and minimum similar services, in euros. On the first real run, 5 of 9 tenders went to review with "solvency not established", although retrieval had the solvency clause in the top 8 every time and the model read it right: the pliegos do not state solvency as a fixed amount. Among the first nine: "≥ 90.000 euros" (fixed), "≥ the valor estimado" and "1,5 times the annual value" (relative), a professional liability insurance of 75.000 € and a positive net worth (other means), a list of works with no amount, and "o bien" alternatives.

## Decision
- `economic_solvency` and `technical_solvency` are each `{any_of, criteria: [{kind, amount_eur, multiple, base}]}`, cited. Kinds: turnover / insurance / net_worth / other; similar_services / works_list / staff / other. `null` with a citation when the pliego waives it.
- Code computes relative amounts from the feed (valor estimado and budget, checked by the golden set); the annual value is the valor estimado over `max_duration_months` (with extensions, which the valor estimado includes). Without that duration, the criterion is open, never guessed.
- Code compares turnover, insurance and net worth with the profile (which gains `professional_liability_insurance_eur` and `net_worth_eur`), and similar services only to fail when even the 3-year total is short. Everything else is open, for the decision model.
- Alternatives pass on one computable pass; cumulative criteria fail on one computable fail. Anything unclear is open: a false no-go by code is the costliest error.
- "Solvency not established" (review by code) now means the extraction could not cite what the pliego says about it, not that there was no number.

## Alternatives considered
- **Add the cited text only and let the model compare whenever there is no fixed amount:** less code, but relative amounts are common and arithmetic is where code is certain and models are not.
- **Keep the two numbers and accept the review:** half the tenders to review, and a cascade measured on the wrong thing.

## Consequences
- The extraction asks more of the model (kinds, multiples, bases): the evals must score the solvency fields per criterion, not as one number.
- The profile carries two more invented values; a real client would have to supply them.
- Revisit the kinds after ~20 labelled tenders: a frequent "other" is a missing kind.
