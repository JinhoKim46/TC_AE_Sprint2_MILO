# Protocol E02: chat model comparison for development and evaluation

| Field | Value |
|---|---|
| Protocol id | `e02-chat-compare` (register row E02 in `docs/experiments.html`) |
| Status | committed before results |
| Date | 2026-10-22 |
| Owning milestone | not named in SPEC; run by the eval owner after the M0b audit commit and before M5a fixes its model setting (D3 wording, section 21). Paths under `evals/chat/` and `evals/results/e02/` are unowned in HARNESS Appendix A and are requested from the integrator first |
| Implements | SPEC D3, D4, D18, D23b, D24, D56, D60, D69, D83, D126; HARNESS rules 1, 6, 14 |
| Produces | `evals/chat/compare_chat.py`, `evals/chat/prompts/extract.v1.md`, `evals/chat/prompts/reply.v1.md`, `evals/results/e02/chat_dev.json`, `evals/results/e02/chat_test.json` |
| Measured calls | about 0.80 USD, cap 1.20 USD (see the last section) |

## Question

Which OpenRouter chat model is the cheapest that passes the extraction and reply checks on the dev
split, measured against `gpt-4.1-mini` as the quality reference, so that development and the dev
evaluations (M1 to M7) run on it (D3)? The product model is fixed by D4 (`claude-sonnet-5.5`,
pinned version) and is not a candidate; this experiment does not change it.

## Data

### Models

| Role | Model (name as listed in the task) | OpenRouter slug |
|---|---|---|
| candidate | deepseek-v4-flash | to verify at run time |
| candidate | gpt-5-nano | to verify at run time |
| candidate | gemini-2.5-flash-lite | to verify at run time |
| candidate | gpt-4.1-nano | to verify at run time |
| candidate | claude-haiku-5.5 | to verify at run time |
| reference | gpt-4.1-mini | to verify at run time |
| product (not run here) | claude-sonnet-5.5 (D4) | SPEC gives no provider prefix; to verify at run time |

SPEC names no OpenRouter slug for any chat model. Before the run, the eval owner reads the
OpenRouter model catalogue (a free endpoint), records each slug, its listed input and output price
and its context length in the result file, and pins the exact slug string there. A candidate that
is not listed, or whose price is missing, is dropped and the drop is recorded. Temperature 0 where
the model accepts it; a model that rejects the parameter runs at its fixed default, which is
recorded per row and flagged in the header (to verify per candidate at run time). Thinking output
disabled where the model emits it, `<think>` blocks stripped (D23b); one JSON retry on parse
failure, through the same path the product will use.

### Task set A: discovery turns (extraction)

The 102 owner turns of the six demo conversations (`data/demo/conversations/*.jsonl`) with the
expected facts per turn from `<id>.expected.json` (derived from the truth files, never from a model).
SPEC does not split these, so this protocol does: dev = A-1, B-1, E-1 (54 turns); test = A-2, B-2,
E-2 (48 turns). The test conversations are not run until the dev decision is written. E-1 and E-2
stay in the set because an injection or IDOR turn must still yield an honest extraction.

Each turn is sent with the frozen extraction prompt `evals/chat/prompts/extract.v1.md` (committed
with this protocol's code, hashed into `inputs`), the protocol field list from
`protocols/discovery.v1.yaml`, and the prior owner turns of that conversation as history. The output
mirrors section 5 step 2: JSON `{facts: [{field_id, value, unit, period, evidence_text}],
asks_about}`. The prompt is a stand-in: M5b writes the product prompt later, and this one is
frozen so that every candidate sees the same text.

### Task set B: gold questions (reply, citation, abstention)

The 60 dev gold rows loaded through `evals/common.py` (48 answerable, 12 unanswerable after M0b).
M1 is not built when E02 runs, so the eval script computes retrieval itself: chunks at 800/100 (D9)
with the logic of `evals/retrieval/compare_embeddings.py`, embedded with the chosen embedding model
(`google/gemini-embedding-2`, D5), cosine top 5. The same five chunks go to every model. The frozen
reply prompt `evals/chat/prompts/reply.v1.md` asks for an answer that cites chunk ids in square
brackets, or the exact string `INSUFFICIENT_CONTEXT` when the context does not answer the question.

### Expected-value normalisation (task A)

A predicted fact matches an expected fact when `field_id` is equal and the values match after:
numbers parsed from digits or number words and compared as floats with 1 percent tolerance; units
and periods mapped through the protocol's `conversions`; strings case-folded and whitespace-collapsed;
enum values compared as ids; lists compared as sets. `evidence_text` must be a verbatim substring
of the owner turn (whitespace-collapsed). A fact for a field the expected file does not list for
that turn, or any earlier turn, is a false positive.

## Procedure

1. Request the paths, commit the two prompts and this protocol, then the script. The script loads
   gold through `evals/common.py` only, calls the provider only through `milo.llm.cloud` (HARNESS
   rule 1; if M5a has not merged it yet, E02 waits or files a request; it never embeds the URL).
2. Record slugs and prices from the catalogue (free call) into the result header.
3. Dev run, task A: 54 turns times 6 models, one call each plus at most one JSON retry.
4. Dev run, task B: embed the 167 chunks and the 60 questions once, then 60 prompts times 6 models.
5. Write `chat_dev.json` with the D56 fields: `git_commit`, `model` (the list of pinned slugs),
   `seed` null (no sampling; reason stated), `temperature` 0, `command`, `timestamp`, `inputs`
   (SHA-256 of the gold file, the conversations, the expected files, the protocol YAML, the KB files,
   the two prompts), and `rows` (per turn and per question: request hash, raw reply, parsed output,
   match results, usage tokens, cost from the usage field, latency, retry flag).
6. Compute the metrics and the decision (below). Write the decision line to the result file.
7. Single test-split run, task A only: the chosen model and the reference on the 48 test turns, once;
   `chat_test.json`. Task B on test gold needs `MILO_FINAL=1`, which only `make final-test` sets;
   that confirmation is added to `evals/final/run_final.py` for F2 and is not run here.
8. Blind answer grading (task B, secondary): a session agent grades each dev answer pass or fail
   against `reference_answer`, with model names hidden and rows shuffled; the grades are written
   into `rows` with the grader's rubric. This is session work, not an OpenRouter call (D126).

Replay: `compare_chat.py --replay` recomputes every metric from `rows` and must match exactly.
Live reruns of this model-driven arm are reported, not failed (section 10.3).

## Metrics

Per model, dev, with bootstrap 95 percent CI (2000 resamples, seed 0) and a paired bootstrap of the
difference to the reference on the same turns or questions (D24):

| Metric | Definition |
|---|---|
| extraction precision, recall, F1 | per turn against the expected facts, then averaged over turns |
| JSON validity | share of turns parsed without the retry; share parsed after one retry |
| evidence verbatim rate | share of predicted facts whose `evidence_text` is a substring of the turn |
| citation correctness | answerable rows: at least one cited chunk id is in the context and contains the gold quote; no cited id outside the context |
| abstention correctness | unanswerable rows: reply is exactly `INSUFFICIENT_CONTEXT`; answerable rows whose context holds a relevant chunk: reply is not an abstention |
| answer pass rate | the blind session grading (secondary; reported, not used in the decision) |
| cost per turn | mean USD from the OpenRouter usage field, tasks A and B separately |
| latency | mean and p95 wall-clock seconds per call |

## Decision rule

Thresholds are fixed here, before any run, relative to the reference `gpt-4.1-mini` on the same dev
rows. A candidate passes when all hold:

1. extraction F1 at least the reference F1 minus 0.05, and the paired CI of the difference does not
   lie entirely below minus 0.10;
2. JSON validity after the retry at least 0.98;
3. citation correctness and abstention correctness each at least the reference value minus 0.05;
4. no row with a provider error that persisted after the D23a retries.

The development and evaluation model is the passing candidate with the lowest measured mean cost
per turn (task A). If no candidate passes, the reference is the development model. The test run
(step 7) confirms the choice once: if the chosen model's test F1 falls more than 0.10 below the
reference on test, the result is reported and the reference becomes the development model; no
candidate is re-tuned on test. The product model stays `claude-sonnet-5.5` whatever the outcome (D4).
The chosen slug is written to SPEC D3 by the integrator with the result file path.

## Limits

- The extraction and reply prompts are stand-ins; the M5b prompts will differ, so the ranking is a
  guide for the development model, not a measure of the product.
- 54 dev turns and 60 questions give wide intervals; the thresholds are absolute margins, not
  significance tests, and a cheaper model inside the margin is preferred by design.
- Task B retrieval is the eval's own dense top 5, not the M1 code; numbers are not M1 numbers.
- Candidate availability, slugs and prices come from the catalogue on the run day; a model pulled
  from OpenRouter later would need a rerun under this protocol.
- D3 asks for this result before M5a starts, while D60 asks for it after M0b; this protocol runs
  after the M0b audit commit, and M5a builds against a configurable slug in the meantime.
- Blind grading by a session agent is one grader; its rubric is stored but it is not a kappa-checked
  judge (that is M7).

## Measured-call budget

Dev: task A about 324 calls (plus retries) at about 1,500 input and 150 output tokens; task B about
360 calls at about 1,200 input and 120 output tokens; together about 0.9 million input and 0.07
million output tokens over six models. Candidate prices are to verify; at an average of 0.50 USD
per million input tokens and 2.00 per million output the dev run is about 0.60 USD. Test: 2 models
times 48 turns, about 0.10 USD. Embeddings for task B: about 11,000 tokens with
`google/gemini-embedding-2`, about 0.003 USD. Estimate 0.80 USD, cap 1.20 USD, inside the 2.50 USD
"dev and eval" allowance of D18. Reason recorded per call in the result file: "candidate chat model
output needed for the comparison" (D126). The product model is not run here, so no Sonnet spend.
