# Protocol E01-RERUN: embedding comparison on the expanded dev split

| Field | Value |
|---|---|
| Protocol id | `e01-rerun` (register row for E01c in `docs/experiments.html`, following E01 and E01b) |
| Status | committed before results |
| Date | 2026-10-22 |
| Owning milestone | M0b (the `embedding_dev_v3.json` rerun named in D78); runs only after the `m0b-gold-expansion.md` audit report is committed |
| Implements | SPEC D5, D9, D24, D56, D60, D69, D78, D83, D126; section 21 pre-S0 scripts; HARNESS rules 1, 6, 14 |
| Produces | the rewritten `evals/retrieval/compare_embeddings.py`, `evals/results/retrieval/embedding_dev_v3.json` |
| Measured calls | about 0.02 USD, cap 0.05 USD |

## Question

With 48 answerable dev questions instead of 16, does `google/gemini-embedding-2` still have the
best retrieval quality among the five candidates, and is any difference now outside the paired
confidence interval, so that the provisional D5 choice can be called confirmed on dev or replaced?

## Data

- Gold: the dev rows with non-empty `gold_evidence`, loaded through `evals/common.py` (48 after
  M0b). The test split is never read; F2 reports the test numbers for the chosen model once.
- Knowledge base: the 24 articles under `data/kb/`, front matter removed, split at headings.
- Models: the five in `evals/results/retrieval/embedding_dev_v2.json`, same order, same listed
  prices, re-read from the catalogue on the run day and recorded:

| Model | Role |
|---|---|
| `google/gemini-embedding-2` | reference (current D5 choice) |
| `openai/text-embedding-3-large` | candidate |
| `mistralai/mistral-embed-2312` | candidate |
| `qwen/qwen3-embedding-8b` | candidate |
| `openai/text-embedding-3-small` | candidate |

- Chunk settings, as in E01b: 800 characters with 100 overlap (the product setting, D9), 400/50,
  1500/150. Heading-aware: a section is cut at level headings, then packed to the size with the
  overlap. The chunk list per setting is hashed into the result file.
- Query prefix: none, for every model (as in E01 and E01b). Top k = 5. Cosine similarity.
- Relevance: a chunk is relevant when it contains the gold quote after whitespace collapse and
  lower-casing, in the gold document. Binary.

## Procedure

Precondition: the M0b audit commit exists on `agent/audit/m0b` with no outstanding verdict, and the
result's `git_commit` descends from it (the audit branch merged, or the M0b branch rebased onto it).
The verifier checks that this protocol's commit precedes the result file (D83).

1. Rewrite `evals/retrieval/compare_embeddings.py` (M0b owns it, HARNESS Appendix A): load rows
   through `evals/common.py` (the file no longer names the gold file), call the embedding endpoint
   through `milo.llm.cloud` (HARNESS rule 1; if M5a has not merged it, M0b files a request and waits;
   the URL is never written into the script), write every D56 field, and add `--replay`. The
   metric code (`per_question_metrics`, `boot_ci`, `paired_diff`) is kept as in E01b so that v2 and
   v3 are comparable.
2. For each chunk setting: build the chunks, map every dev question to its relevant chunk ids, and
   list `queries_without_chunk`. Such a row is scored as a miss for every model (as in E01b) and a
   second summary that excludes those rows is written beside the first. At 800/100 the M0b quote rule
   (inside one heading, at most 150 characters) should give an empty list; a non-empty list at
   800/100 is reported back to M0b as a data finding before the decision is taken.
3. Embed every chunk (batches of 64) and every question with each model; record the usage tokens
   from the response. One pass; no retries beyond the three attempts the script already makes.
4. Rank all chunks per question; compute the per-question metrics; summarise per model and setting
   with the bootstrap CI; compute the paired bootstrap against the reference per metric.
5. Write `embedding_dev_v3.json` with: `git_commit`, `model` (the list of five slugs with prices),
   `seed` 0, `temperature` null (embeddings have none; reason stated), `command`, `timestamp`,
   `inputs` (SHA-256 of the gold file via `common.gold_sha256()`, of each KB file, of the script),
   `split` `dev`, `k`, `bootstrap`, `seed`, `reference`, `query_prefix`, `total_cost_usd`, and
   `rows` per model and setting (per question: `qid`, `type`, `relevant`, `top5`, the five metrics,
   `first_relevant_rank`).
6. `--replay` recomputes every summary and paired interval from `rows` and must match exactly
   (HARNESS 7.6). The live rerun of dense retrieval is compared within 0.005 (7.7).
7. The E01c row and the updated numbers go to `docs/experiments.html` and the embedding report;
   the D5 wording is updated by the integrator with the result file path.

## Metrics

Per question, as in `compare_embeddings.py`: recall@5 (a relevant chunk in the top 5), nDCG@5
(binary gain), MRR (full ranking), P@5, average precision (full ranking). Per model and setting:
mean with a bootstrap 95 percent CI (2000 resamples, seed 0, D24); by question type (identifier,
paraphrase) as well, since the M1 report names the weakest type. Paired comparison: the mean
per-question difference between each candidate and the reference with its paired bootstrap CI, for
all five metrics and all three settings. Cost and tokens per model from the usage field.

## Decision rule

The decision is taken at 800/100 (the product setting, D9) on nDCG@5, the metric D5 used.

- The reference `google/gemini-embedding-2` stays unless a candidate's paired nDCG@5 difference
  interval at 800/100 lies entirely above zero in the candidate's favour.
- If more than one candidate meets that, the one with the largest mean difference is chosen; cost
  is reported but is not a tie-breaker, because the embedding allowance (about 0.10 USD) makes every
  candidate affordable.
- If no candidate meets it, D5 is restated as "confirmed on 48 dev questions, interval still
  includes the runner-up" or "confirmed, runner-up excluded", whichever the interval says.
- The other two settings and the other four metrics are reported as context. A setting change is
  not decided here; D9 stays at 800/100.
- The choice is not revisited on the test split (D5 wording, section 21). F2 reports the test
  numbers for the chosen model once, through `evals/final/`.

## Limits

- 48 questions narrow the intervals from E01b but still cannot separate differences of about 0.05
  nDCG@5; the rule is deliberately conservative towards keeping the current choice.
- Single-quote binary relevance understates recall for questions with several good chunks.
- Dense only; BM25, fusion and reranking are M2 arms under their own protocol.
- Embedding outputs from the provider can change between runs; the stored `rows` are the record,
  and the 0.005 tolerance applies to the live rerun.
- The dependency on `milo.llm.cloud` (M5a, Phase 1a) can delay this rerun past the M0b merge; if so,
  the rerun is the last M0b commit and M1 still starts only after the audit commit, not after this
  result.

## Measured-call budget

About 10,700 tokens per model per setting (the chunks dominate; 48 questions add about 700),
five models, three settings: about 160,000 tokens. At the listed prices (0.2 USD per million for the
reference, less for the others) that is about 0.016 USD; E01b with 16 questions cost 0.015 USD.
Estimate 0.02 USD, cap 0.05 USD, inside the 0.10 USD embedding allowance (D18). Reason recorded in
the result file: "candidate embedding vectors needed for the comparison" (D126). No chat model and
no judge is called.
