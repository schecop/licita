# Labels

One YAML per tender, `<entry number>.yaml`: the truth the evals score extraction, retrieval and decisions against. Drafted by `scripts/draft_labels.py` from what the model extracted; a person confirms or corrects every field against the pliego and decides go / no-go.

## How to label one tender
1. Open the pliegos in `data/pliegos/<entry number>/`.
2. **Decide first, before reading the fields:** would a tender consultant tell this company (`data/company/profile.yaml`) to bid? Write `decision: go | no_go` and the clause that weighs most in `deciding_clause: {doc, page, quote}`. The model's decision is not in this file on purpose.
3. For each field, go to `where` (or to `look_at` if the model found nothing) and set `checked`:
   - `confirmed`: value and place are right;
   - `corrected`: you changed `value` and/or `where`.
   - A field the pliego does not mention: `value: null`, `where: absent`. One it explicitly waives ("No se exige clasificación"): `value: null` (or `[]`), `where` with the quote and `waived: true`.
4. `quote` must be copied literally from the page (a phrase is enough): the evals find it again in the chunks, so a label survives a change in chunking.
5. Set `labelled_by`, `labelled_on`, `status: done`. Write anything ambiguous in `notes`.

## Rules
- `split` is fixed when the draft is created and never edited. The coach never reads `heldout` labels.
- Amounts in euros without VAT. Solvency as the pliego states it (ADR-0003): criteria with `kind`, `amount_eur` or `multiple` + `base`, and `any_of` for "o bien".
- If almost every field ends up `confirmed`, read the tender again: the draft may be anchoring you.
