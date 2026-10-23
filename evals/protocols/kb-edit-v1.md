# Protocol KB-EDIT-V1: knowledge-base fix round and the chunk title prefix

| Field | Value |
|---|---|
| Protocol id | `kb-edit-v1` |
| Status | committed before results |
| Date | 2026-10-23 |
| Owner choice | option A on the KB audit (`docs/data-audit/kb-v2.md`), 2026-10-23 |
| Implements | SPEC D9, D24, D83, D126, D166 (amended by D168); HARNESS rules 1, 6, 14 |
| Produces | edited articles, `data/gold_alt.jsonl`, `evals/results/retrieval/retrieval_dev.json` (arm B), `retrieval_dev_notitle.json` (arm A), `kb_edit_title.json` (paired comparison) |
| Measured calls | two dev retrieval runs with `google/gemini-embedding-2`, about 0.002 USD each, cap 0.02 USD |

## Question

After fixing the three high and one medium contradiction of the KB audit, does prefixing each chunk with its
article title (audit K09: 33 of 167 chunks never name their subject) improve dense retrieval on dev?

## Changes made before any measurement

1. **Articles (K01 to K04), sentence level only.** No paragraph or heading added or removed, so every `doc#i` id
   stays. Every gold quote, dev and test, stays byte-identical. Where the audit's wording would have changed a
   test quote (K02 `export-only-tools#6`, q051; K04 `accounts-paperwork#2`, q054), the correction is a sentence
   added after the quote instead.
   - K01 `cases/trades-dispatch#3`, `#4`: document-data-entry added as the module the export-only-tool surcharge
     applies to; ticket-creation and staff-assignment use csv-export with a spreadsheet.
   - K02 `implementation/export-only-tools#6`: a sentence after the api-connector line says document-data-entry
     moves to csv-export, not api-connector.
   - K03 `policies/personal-data#2`, `#3`: "Nothing." becomes "No customer records are collected."; an uploaded
     document's text is stored until it is removed.
   - K04 `cases/grooming-bookings#2` (export and import CSV), `cases/accounts-paperwork#2` (a sentence that the
     system could export too).
2. **Chunk title prefix (K09).** `chunk_document` prefixes the front-matter `title` to every chunk that does not
   open with a top-level heading. The prefix is added after splitting, so the 800-character budget and the overlap
   are computed on the body alone. `title_prefix=False` gives the old text.
3. **Accepted alternative passages (K10 to K12).** Ten dev rows get an `alt_evidence` entry in
   `data/source/gold_questions.yaml`, generated to `data/gold_alt.jsonl` (the kit's `gold.jsonl` is unchanged):
   q003, q009, q010, q012, q018, q058, q075, q090, q100, q104. Each is a verbatim quote inside one level-2 section
   that fully answers the question; `build_data.py` checks this. Rows the audit marked case-specific (q053, q086)
   and twins that do not fully answer (volume-tiers#4 for q090) get none. No test row gets one.

Checks already run: the chunk id list and the chunk holding each of the 120 gold quotes are identical before and
after the edits (167 chunks); the longest chunk is 765 characters with the prefix; `taint.matched_phrases` is
empty for all 24 articles; the 14 judge-pool items whose contexts include an edited chunk were read and none of
their labels depends on an edited sentence.

## Metric

As in `e01-rerun.md`: binary relevance, top k 5, cosine, no query prefix, dev rows only, 95 percent bootstrap CI
(2000 resamples, seed 0).

- **Strict** (`summary`): only the gold chunk counts. Comparable with M1 and F2.
- **Accepted** (`summary_accepted`, D168): a hit on an alternative chunk counts like a hit on the gold chunk, and
  the ideal ranking still holds `len(relevant)` answers, so only the first that many hits score. Without that cap
  a second accepted passage would lower nDCG (the ideal would assume two answers) instead of forgiving a twin.

## Arms

| Arm | KB | Title prefix | Output |
|---|---|---|---|
| A | edited | no (`--no-title`) | `retrieval_dev_notitle.json`, index `...-800-100_notitle` |
| B | edited | yes | `retrieval_dev.json`, index `...-800-100` |

Both arms embed the same 60 dev questions. Reference (not an arm): the stored M1 result, commit `0b055c1`,
strict nDCG@5 0.7608, recall@5 0.9167, MRR 0.7164 on the unedited KB.

## Decision rule (D24)

Paired per-question difference B minus A on accepted nDCG@5, bootstrap as above. The title prefix stays
(`TITLE_PREFIX = True`) only when the CI lower bound is above zero. Otherwise it is switched off and the index is
rebuilt without it; the KB edits stay either way, because they are corrections, not an experiment. Strict
nDCG@5, recall@5 and the `min_score` calibration are reported for both arms with no decision attached.

## Voided results

The edit changes chunk text, so every stored result that holds KB contexts describes the old KB: E01 (embedding
comparison), M2 (A/B arms), M7 generation and judge contexts, and F2's retrieval numbers. They stay in the
repository as the record of the old KB and still replay from their rows; they are not re-measured here. M7 and
the discovery runs are re-measured in phase C1.
